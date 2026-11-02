"""Rebuild index replaces rows as it goes, so nothing it does can leave the library without them."""
import sqlite3
from unittest.mock import MagicMock

import pytest
from astropy.io.fits import Header
from astropy.wcs import WCS

from photonfinder.core import ApplicationContext, StatusReporter, compress
from photonfinder.models import File, FileWCS, FitsHeader, Image, ImageStats, LibraryRoot
from photonfinder.ui import BackgroundLoader
from photonfinder.ui.BackgroundLoader import ImageReindexWorker

COUNT = 25


class Reporter(StatusReporter):
    def __init__(self):
        self.messages = []

    def update_status(self, message, bulk=False):
        self.messages.append(message)

    @property
    def errors(self):
        return [m for m in self.messages if m.startswith("Error")]


@pytest.fixture
def context(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(BackgroundLoader, "REINDEX_BATCH_SIZE", 10)
    context = ApplicationContext(str(tmp_path / "library.db"), MagicMock())
    context.set_status_reporter(Reporter())
    with context:
        root = LibraryRoot.create(name="Main", path="/data/")
        for i in range(COUNT):
            file = File.create(root=root, path="", name=f"f{i:03}.fits", size=1, mtime_millis=1)
            header = Header({"IMAGETYP": "LIGHT", "OBJECT": "new", "GAIN": i})
            FitsHeader.create(file=file, header=compress(header.tostring().encode("ascii")))
            Image.create(file=file, image_type="LIGHT", object_name="old")
        yield context


def _rebuild(context):
    assert context.library_write_lock.acquire(holder="Rebuild index")  # as reindex_images does; the task releases it
    ImageReindexWorker(context)._reindex_images_task()


def _wcs_blob(ra=10.68, dec=41.27):
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [50, 50]
    wcs.wcs.cdelt = [-0.001, 0.001]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    header = wcs.to_header()
    header["NAXIS"], header["NAXIS1"], header["NAXIS2"] = 2, 100, 100
    return compress(header.tostring().encode("ascii"))


def _rewritten() -> int:
    return Image.select().where(Image.object_name == "new").count()


def test_a_rebuild_replaces_every_row(context):
    _rebuild(context)

    assert Image.select().count() == COUNT
    assert _rewritten() == COUNT
    assert context.status_reporter.errors == []
    assert Image.get(Image.gain == 7).file.name == "f007.fits"


def test_a_rebuild_survives_a_write_from_another_connection(context, monkeypatch):
    """The failure seen on a real library: every batch after the other write got "database is locked".
    It needs a write while the rows are still being read, so more rows than the old batch of 1000."""
    monkeypatch.setattr(BackgroundLoader, "REINDEX_BATCH_SIZE", 1000)
    root = LibraryRoot.get()
    header = compress(Header({"IMAGETYP": "LIGHT", "OBJECT": "new"}).tostring().encode("ascii"))
    with context.database.atomic():
        for i in range(1100):
            file = File.create(root=root, path="more/", name=f"m{i:04}.fits", size=1, mtime_millis=1)
            FitsHeader.create(file=file, header=header)
    real_normalize = BackgroundLoader.normalize_fits_header
    calls = []

    def another_connection_commits(*args, **kwargs):
        calls.append(1)
        if len(calls) == 5:
            other = sqlite3.connect(context.database_path, timeout=1)
            other.execute("insert into project(name) values ('edited elsewhere')")
            other.commit()
            other.close()
        return real_normalize(*args, **kwargs)
    monkeypatch.setattr(BackgroundLoader, "normalize_fits_header", another_connection_commits)

    _rebuild(context)

    assert context.status_reporter.errors == []
    assert _rewritten() == COUNT + 1100


def test_a_rebuild_stopped_by_an_error_keeps_all_rows_and_stops(context, monkeypatch):
    real_insert = Image.insert
    calls = []

    def fails_after_the_first_batch(*args, **kwargs):
        calls.append(1)
        if len(calls) > 10:
            raise sqlite3.OperationalError("database is locked")
        return real_insert(*args, **kwargs)
    monkeypatch.setattr(Image, "insert", fails_after_the_first_batch)

    _rebuild(context)

    assert Image.select().count() == COUNT
    assert _rewritten() == 10
    assert len(context.status_reporter.errors) == 1
    assert "database is locked" in context.status_reporter.errors[0]
    assert len(calls) == 11


def test_a_failure_partway_through_a_batch_writes_none_of_it(context, monkeypatch):
    real_insert = Image.insert
    calls = []

    def fails_inside_the_second_batch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 15:
            raise sqlite3.OperationalError("disk I/O error")
        return real_insert(*args, **kwargs)
    monkeypatch.setattr(Image, "insert", fails_inside_the_second_batch)

    _rebuild(context)

    assert _rewritten() == 10


def test_a_rebuild_finishes_a_library_an_earlier_one_left_short(context):
    """A library hit by the old failure: the table was emptied and only part of it came back."""
    Image.delete().where(Image.file > 7).execute()

    _rebuild(context)

    assert Image.select().count() == COUNT
    assert _rewritten() == COUNT


def test_an_unusable_solution_keeps_the_row_it_had(context):
    file = File.get(File.name == "f003.fits")
    FileWCS.create(file=file, wcs=compress(b"not a header"))
    Image.update(coord_ra=10.0, coord_dec=20.0).where(Image.file == file).execute()

    _rebuild(context)

    image = Image.get(Image.file == file)
    assert (image.object_name, image.coord_ra, image.coord_dec) == ("old", 10.0, 20.0)
    assert Image.select().count() == COUNT
    assert _rewritten() == COUNT - 1
    assert len(context.status_reporter.errors) == 1


def test_a_solved_file_gets_its_position_from_the_solution(context):
    file = File.get(File.name == "f003.fits")
    FileWCS.create(file=file, wcs=_wcs_blob(10.68, 41.27))

    _rebuild(context)

    image = Image.get(Image.file == file)
    assert image.object_name == "new"
    assert image.coord_ra == pytest.approx(10.68, abs=0.01)
    assert image.coord_radius is not None


def test_a_solution_embedded_in_a_header_is_stored(context):
    file = File.get(File.name == "f003.fits")
    wcs_cards = Header.fromstring(_decompressed(_wcs_blob(30.0, 40.0)))
    wcs_cards["CD1_1"], wcs_cards["CD1_2"], wcs_cards["CD2_1"], wcs_cards["CD2_2"] = -0.001, 0.0, 0.0, 0.001
    wcs_cards["CROTA1"], wcs_cards["CROTA2"] = 0.0, 0.0
    wcs_cards["IMAGETYP"] = "LIGHT"
    FitsHeader.update(header=compress(wcs_cards.tostring().encode("ascii"))).where(FitsHeader.file == file).execute()

    _rebuild(context)

    assert FileWCS.get_or_none(FileWCS.file == file) is not None
    assert Image.get(Image.file == file).coord_ra == pytest.approx(30.0, abs=0.01)


def test_stats_and_other_tables_are_untouched(context):
    file = File.get(File.name == "f003.fits")
    ImageStats.create(file=file, star_count=100)

    _rebuild(context)

    assert ImageStats.get(ImageStats.file == file).star_count == 100
    assert File.select().count() == COUNT


def _decompressed(blob):
    from photonfinder.core import decompress
    return decompress(blob)

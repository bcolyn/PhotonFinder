"""JPEG and PNG pictures: detection, size reading, and their import, opt-in per library root."""
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pytest
from astropy.io import fits

from photonfinder import local_time, pictures
from photonfinder.core import ApplicationContext, StatusReporter, compress
from photonfinder.filesystem import Importer, check_missing_header_cache, update_fits_header_cache
from photonfinder.models import File, FileWCS, FitsHeader, Image, LibraryRoot, Project, ProjectFile

from .conftest import McpSettings

NON_ASCII = "Ñébula ✓"


def _write(path: Path, pixels: np.ndarray, *params) -> Path:
    """cv2.imwrite can't write to a non-ASCII path on Windows; imencode + tofile can."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(path.suffix, pixels, list(params))
    assert ok
    data.tofile(str(path))
    return path


def _jpeg8(path: Path, width=40, height=30) -> Path:
    return _write(path, np.full((height, width, 3), 90, np.uint8), cv2.IMWRITE_JPEG_QUALITY, 90)


def _png8(path: Path, width=41, height=31) -> Path:
    return _write(path, np.full((height, width), 120, np.uint8))


def _png16(path: Path, width=42, height=32) -> Path:
    return _write(path, np.full((height, width, 3), 30000, np.uint16))


def _fits(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = fits.Header()
    header["IMAGETYP"] = "LIGHT"
    header["DATE-OBS"] = "2024-01-01T22:00:00"
    fits.PrimaryHDU(np.zeros((8, 10), np.uint16), header).writeto(path)
    return path


class TestDetection:
    @pytest.mark.parametrize("name", ["a.jpg", "a.JPG", "a.jpeg", "a.JPEG", "a.png", "a.Png", "x.y.png"])
    def test_pictures(self, name):
        assert Importer.is_picture_by_name(name)

    @pytest.mark.parametrize("name", ["a.fits", "a.fit.gz", "a.xisf", "a.tif", "a.tiff", "a.gif", "jpg", "a.jpg.xz",
                                      "a.png.txt"])
    def test_not_pictures(self, name):
        assert not Importer.is_picture_by_name(name)

    def test_one_list_of_extensions(self):
        assert pictures.extensions_text() == ".jpg, .jpeg, .png"


class TestSize:
    @pytest.mark.parametrize("make, name, size", [(_jpeg8, "a.jpg", (40, 30)), (_png8, "b.png", (41, 31)),
                                                  (_png16, "c.png", (42, 32))])
    def test_size_of_each_kind(self, tmp_path, make, name, size):
        assert pictures.picture_size(make(tmp_path / name)) == size

    def test_a_non_ascii_path(self, tmp_path):
        assert pictures.picture_size(_jpeg8(tmp_path / NON_ASCII / f"{NON_ASCII}.jpg")) == (40, 30)

    def test_not_a_picture_raises(self, tmp_path):
        bad = tmp_path / "broken.png"
        bad.write_bytes(b"not a png at all")
        with pytest.raises(ValueError, match="broken.png"):
            pictures.picture_size(bad)


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "lib"
    folder.mkdir()
    context = ApplicationContext(":memory:", McpSettings())
    context.set_status_reporter(StatusReporter())
    with context:
        root = LibraryRoot.create(name="r", path=str(folder).replace("\\", "/") + "/")
        yield context, root, folder


def _scan(context, root):
    """A full rescan of one root, as LibraryScanWorker.import_roots runs it."""
    importer = Importer(context)
    for changes in importer.import_roots([root]):
        changes.apply_all()
        update_fits_header_cache(changes, context.status_reporter, context.settings)
    check_missing_header_cache(context.status_reporter, context.settings)


def _set_pictures(root, on: bool):
    LibraryRoot.update(include_pictures=on).where(LibraryRoot.rowid == root.rowid).execute()


def _names() -> list[str]:
    return sorted(f.name for f in File.select())


@pytest.fixture
def mixed_folder(library):
    _, _, folder = library
    _fits(folder / "light.fits")
    _jpeg8(folder / "shot.jpg")
    _png8(folder / "sub" / "result.PNG")
    _png16(folder / NON_ASCII / f"{NON_ASCII}.png")
    (folder / "notes.txt").write_text("not an image")
    return folder


class TestImport:
    def test_roots_leave_pictures_out_by_default(self, library, mixed_folder):
        context, root, _ = library
        assert not LibraryRoot.get_by_id(root.rowid).include_pictures

        _scan(context, root)

        assert _names() == ["light.fits"]

    def test_a_root_with_pictures_imports_them(self, library, mixed_folder):
        context, root, _ = library
        _set_pictures(root, True)

        _scan(context, root)

        assert _names() == sorted(["light.fits", "shot.jpg", "result.PNG", f"{NON_ASCII}.png"])
        rows = {row["name"]: row for row in Image.select(Image, File.name).join(File)
                .where(Image.image_type == pictures.PICTURE_TYPE).dicts()}
        assert {name: (row["hdu"], row["width"], row["height"]) for name, row in rows.items()} == {
            "shot.jpg": (0, 40, 30), "result.PNG": (0, 41, 31), f"{NON_ASCII}.png": (0, 42, 32)}
        for row in rows.values():
            assert row["date_obs"] is None and row["local_time"] is None and row["local_time_source"] is None
            assert row["object_name"] is None and row["camera"] is None and row["filter"] is None
        picture_files = File.select().where(File.name.in_(list(rows)))
        assert not FitsHeader.select().where(FitsHeader.file.in_(picture_files)).exists()
        fits_image = Image.select().join(File).where(File.name == "light.fits").get()
        assert fits_image.image_type == "LIGHT", "FITS files are imported as before"

    def test_a_scan_sees_the_flag_set_after_the_root_was_loaded(self, library, mixed_folder):
        context, root, _ = library
        stale = LibraryRoot.get_by_id(root.rowid)  # as the library tree holds it
        _set_pictures(root, True)

        _scan(context, stale)

        assert "shot.jpg" in _names()

    def test_a_second_scan_reads_no_picture_again(self, library, mixed_folder, monkeypatch):
        context, root, _ = library
        _set_pictures(root, True)
        _scan(context, root)
        reads = []
        real = pictures.picture_size
        monkeypatch.setattr(pictures, "picture_size", lambda path: reads.append(path) or real(path))

        _scan(context, root)

        assert reads == []

    def test_an_unreadable_picture_is_tried_again(self, library, monkeypatch):
        context, root, folder = library
        _set_pictures(root, True)
        (folder / "broken.png").write_bytes(b"not a png at all")
        _scan(context, root)
        assert _names() == ["broken.png"]
        assert not Image.select().exists()
        reads = []
        real = pictures.picture_size
        monkeypatch.setattr(pictures, "picture_size", lambda path: reads.append(path) or real(path))

        _scan(context, root)

        assert len(reads) == 1

    def test_a_changed_picture_is_measured_again(self, library):
        context, root, folder = library
        _set_pictures(root, True)
        path = _jpeg8(folder / "shot.jpg")
        _scan(context, root)
        _jpeg8(path, width=80, height=60)
        import os
        stat = os.stat(path)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

        _scan(context, root)

        assert [(i.width, i.height) for i in Image.select()] == [(80, 60)]

    def test_switching_pictures_off_removes_them_with_links_and_solutions(self, library, mixed_folder):
        context, root, _ = library
        _set_pictures(root, True)
        _scan(context, root)
        shot = File.get(File.name == "shot.jpg")
        light = File.get(File.name == "light.fits")
        project = Project.create(name="p")
        ProjectFile.create(project=project, file=shot)
        ProjectFile.create(project=project, file=light)
        FileWCS.create(file=shot, wcs=compress(b"wcs"))
        assert LibraryRoot.get_by_id(root.rowid).picture_count() == 3

        _set_pictures(root, False)
        _scan(context, root)

        assert _names() == ["light.fits"]
        assert [pf.file.name for pf in ProjectFile.select()] == ["light.fits"]
        assert not FileWCS.select().exists()
        assert not Image.select().where(Image.image_type == pictures.PICTURE_TYPE).exists()
        assert LibraryRoot.get_by_id(root.rowid).picture_count() == 0

    def test_dropping_a_picture_on_a_root_without_pictures_skips_it(self, library):
        context, root, folder = library
        path = _jpeg8(folder / "shot.jpg")

        changes = Importer(context).import_selection([str(path).replace("\\", "/")])

        assert changes.new_files == []

    def test_dropping_a_picture_on_a_root_with_pictures_imports_it(self, library):
        context, root, folder = library
        _set_pictures(root, True)
        path = _jpeg8(folder / "shot.jpg")

        changes = Importer(context).import_selection([str(path).replace("\\", "/")])
        changes.apply_all()
        update_fits_header_cache(changes, context.status_reporter, context.settings)

        assert [(i.image_type, i.width) for i in Image.select()] == [(pictures.PICTURE_TYPE, 40)]

    def test_the_local_time_fill_leaves_pictures_alone(self, library, mixed_folder):
        context, root, _ = library
        _set_pictures(root, True)
        _scan(context, root)

        assert local_time.update_library(ZoneInfo("Europe/Brussels"))

        for image in Image.select().where(Image.image_type == pictures.PICTURE_TYPE):
            assert image.local_time is None and image.local_time_source is None


class TestMigration:
    def test_an_existing_library_gets_the_column_switched_off(self, tmp_path):
        path = tmp_path / "old.db"
        with ApplicationContext(str(path), McpSettings()) as context:
            LibraryRoot.create(name="r", path="/data/")
            version = context.database.execute_sql("PRAGMA user_version").fetchone()[0]
        with sqlite3.connect(path) as db:  # as before this column existed
            db.execute("ALTER TABLE libraryroot DROP COLUMN include_pictures")
        db.close()

        with ApplicationContext(str(path), McpSettings()) as context:
            assert LibraryRoot.get(LibraryRoot.name == "r").include_pictures is False
            assert context.database.execute_sql("PRAGMA user_version").fetchone()[0] == version
            LibraryRoot.create(name="new", path="/new/")
            assert LibraryRoot.get(LibraryRoot.name == "new").include_pictures is False
        with ApplicationContext(str(path), McpSettings()):  # a second open changes nothing
            assert LibraryRoot.select().count() == 2

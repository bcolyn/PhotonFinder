"""Import, Rebuild index and header repair give every image in a file its own rows."""
import shutil
from pathlib import Path

import pytest
from astropy.io.fits import Header

from photonfinder import filesystem, plate_solution
from photonfinder.core import ApplicationContext, StatusReporter, decompress
from photonfinder.filesystem import ChangeList, reindex_library, update_fits_header_cache
from photonfinder.models import File, FileWCS, FitsHeader, Image, LibraryRoot

from .conftest import McpSettings
from .multi_image_files import write_multi_hdu_fits, write_two_image_xisf

WCS_CARDS = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 10.0, "CRVAL2": 20.0, "CRPIX1": 5.0,
             "CRPIX2": 4.0, "CD1_1": 0.001, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 0.001, "PLTSOLVD": True}


class RecordingSettings(McpSettings):
    def __init__(self):
        super().__init__()
        self.keywords = set()

    def add_known_fits_keywords(self, keywords):
        self.keywords.update(keywords)


@pytest.fixture
def library(tmp_path):
    context = ApplicationContext(":memory:", RecordingSettings())
    context.set_status_reporter(StatusReporter())
    with context:
        root = LibraryRoot.create(name="r", path=str(tmp_path).replace("\\", "/") + "/")
        yield context, root


def _import(context, root, path) -> File:
    name = Path(path).name
    file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
    update_fits_header_cache(ChangeList(new_files=[file]), context.status_reporter, context.settings)
    return file


def _images(file) -> list[dict]:
    """The file's Image rows, without rowid (Rebuild index replaces rows, giving new ones)."""
    rows = list(Image.select().where(Image.file == file).order_by(Image.hdu).dicts())
    for row in rows:
        row.pop("rowid")
    return rows


def _header_hdus(file) -> list[int]:
    return [row.hdu for row in FitsHeader.select().where(FitsHeader.file == file).order_by(FitsHeader.hdu)]


class TestImport:
    def test_each_layer_is_an_image(self, library, tmp_path):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits"))

        images = _images(file)

        assert [(i["hdu"], i["extname"], i["filter"], i["exposure"]) for i in images] == [
            (0, None, "HO", 6000), (1, "SOO", "SO", 11700)]
        for image in images:
            assert image["object_name"] == "C 20"
            assert image["camera"] == "Draco"
            assert image["image_type"] == "LIGHT"
            assert image["date_obs"] is not None
        assert _header_hdus(file) == [0, 1]
        sparse = Header.fromstring(decompress(FitsHeader.get(file=file, hdu=1).header))
        assert "OBJECT" not in sparse, "an extension's header is cached as it is, not merged"

    def test_extension_only_keywords_are_known(self, library, tmp_path):
        context, root = library
        _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits"))

        assert {"EXTNAME", "STACKCNT", "XTENSION"} <= context.settings.keywords

    def test_empty_primary(self, library, tmp_path):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", empty_primary=True))

        assert [(i["hdu"], i["filter"], i["object_name"]) for i in _images(file)] == [(1, "SO", "C 20")]
        assert _header_hdus(file) == [0, 1]

    @pytest.mark.parametrize("compress", [".xz", ".bz2", ".gz"])
    def test_compressed_file_with_extend(self, library, tmp_path, compress):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", compress=compress))

        assert [i["filter"] for i in _images(file)] == ["HO", "SO"]

    def test_compressed_file_without_extend_is_read_no_further_than_its_primary(self, library, tmp_path):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", compress=".xz", extend=False))

        assert [i["filter"] for i in _images(file)] == ["HO"]
        assert _header_hdus(file) == [0]

    def test_plain_file_without_extend_is_read_whole(self, library, tmp_path):
        """Reading past the primary of a plain file costs nothing, so it isn't left to EXTEND."""
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", extend=False))

        assert [i["filter"] for i in _images(file)] == ["HO", "SO"]

    def test_a_changed_file_that_lost_its_extension_loses_its_rows(self, library, tmp_path):
        context, root = library
        path = write_multi_hdu_fits(tmp_path / "draco.fits")
        file = _import(context, root, path)
        Path(path).unlink()
        write_multi_hdu_fits(tmp_path / "plain.fits", table=False)
        from astropy.io import fits
        with fits.open(tmp_path / "plain.fits") as hdul:
            fits.PrimaryHDU(hdul[0].data, hdul[0].header).writeto(path)

        change_list = ChangeList(changed_files=[file])
        change_list.apply_all()
        update_fits_header_cache(change_list, context.status_reporter, context.settings)

        assert [i["hdu"] for i in _images(file)] == [0]
        assert _header_hdus(file) == [0]


class TestPlateSolution:
    def test_embedded_solution_goes_to_every_layer_of_its_size(self, library, tmp_path):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", primary_cards=WCS_CARDS))

        images = _images(file)

        assert FileWCS.select().where(FileWCS.file == file).count() == 1
        assert images[0]["coord_ra"] is not None
        assert (images[1]["coord_ra"], images[1]["coord_dec"], images[1]["coord_radius"]) == (
            images[0]["coord_ra"], images[0]["coord_dec"], images[0]["coord_radius"])

    def test_a_layer_of_another_size_is_left_unsolved(self, library, tmp_path):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", primary_cards=WCS_CARDS,
                                                           extension_shape=(3, 4, 5)))

        images = _images(file)

        assert images[0]["coord_ra"] is not None
        assert images[1]["coord_ra"] is None

    def test_a_stored_solution_goes_to_every_layer_of_its_size(self, library, tmp_path):
        from tests.conftest import fixed_solution_header
        context, root = library
        path = write_multi_hdu_fits(tmp_path / "draco.fits")
        file = File.create(root=root, path="", name="draco.fits", size=1, mtime_millis=1)
        solution = fixed_solution_header()
        solution["NAXIS1"], solution["NAXIS2"] = 10, 8
        plate_solution.store(file, solution, "ASTAP")

        update_fits_header_cache(ChangeList(new_files=[file]), context.status_reporter, context.settings)

        images = _images(file)
        assert images[0]["coord_ra"] is not None
        assert images[1]["coord_ra"] == images[0]["coord_ra"]
        assert Path(path).exists()


class TestRebuildIndex:
    @pytest.mark.parametrize("batch_size", [1, 1000])
    def test_rebuild_gives_the_rows_import_gave(self, library, tmp_path, monkeypatch, batch_size):
        context, root = library
        files = [_import(context, root, write_multi_hdu_fits(tmp_path / "a.fits", primary_cards=WCS_CARDS)),
                 _import(context, root, write_multi_hdu_fits(tmp_path / "b.fits", empty_primary=True)),
                 _import(context, root, write_multi_hdu_fits(tmp_path / "c.fits", extension_shape=(3, 4, 5)))]
        imported = {file.name: _images(file) for file in files}
        Image.update(filter=None, coord_ra=None, extname=None).execute()
        monkeypatch.setattr(filesystem, "REINDEX_BATCH_SIZE", batch_size)

        reindex_library(context.status_reporter, context.settings)

        assert {file.name: _images(file) for file in files} == imported

    def test_xisf_layers_keep_their_names(self, library, tmp_path):
        context, root = library
        write_two_image_xisf(tmp_path / "two.xisf")
        file = _import(context, root, tmp_path / "two.xisf")
        imported = _images(file)

        reindex_library(context.status_reporter, context.settings)

        assert [(i["hdu"], i["extname"], i["filter"]) for i in imported] == [(0, None, "HO"), (1, "SOO", "SO")]
        assert _images(file) == imported


def test_repair_keeps_every_layer(library, tmp_path):
    """A truncated cached primary header is read again; the extension's rows come back with it."""
    from photonfinder.core import compress
    from photonfinder.filesystem import repair_truncated_header_cache
    context, root = library
    file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits"))
    FitsHeader.update(header=compress(b"SIMPLE  =                    T" + b" " * 2850)).where(
        (FitsHeader.file == file) & (FitsHeader.hdu == 0)).execute()

    repair_truncated_header_cache(context.status_reporter, context.settings)

    assert [(i["hdu"], i["filter"]) for i in _images(file)] == [(0, "HO"), (1, "SO")]
    assert _header_hdus(file) == [0, 1]


def test_single_image_test_data_imports_as_one_image_each(library, global_test_data_dir, tmp_path):
    """Every file in tests/data is a single image: one hdu 0 Image and FitsHeader row each, as
    before files could hold several."""
    context, root = library
    files = []
    for path in sorted(global_test_data_dir.iterdir()):
        if filesystem.Importer.is_fits_by_name(path.name) or filesystem.Importer.is_xisf_by_name(path.name):
            shutil.copy(path, tmp_path / path.name)
            files.append(_import(context, root, tmp_path / path.name))

    assert files
    for file in files:
        assert [i["hdu"] for i in _images(file)] == [0], file.name
        assert _header_hdus(file) == [0], file.name

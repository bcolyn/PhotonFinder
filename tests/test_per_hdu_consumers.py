"""Plate solving, solutions and analysis work on one image of a file that holds several."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from photonfinder import library_query, plate_solution
from photonfinder.core import ApplicationContext, StatusReporter
from photonfinder.filesystem import ChangeList, cached_header, update_fits_header_cache
from photonfinder.image_analysis import analyze_file
from photonfinder.image_processing import detect_image_type, image_cache, load_image_data
from photonfinder.models import File, Image, ImageStats, LibraryRoot, SearchCriteria
from photonfinder.platesolver import SolverBase, _create_temp_jpeg

from .conftest import McpSettings, fixed_solution_header
from .multi_image_files import write_multi_hdu_fits, write_two_image_xisf


class _Settings(McpSettings):
    def add_known_fits_keywords(self, keywords):
        pass


@pytest.fixture
def library(tmp_path):
    context = ApplicationContext(str(tmp_path / "library.db"), _Settings())
    context.set_status_reporter(StatusReporter())
    image_cache.clear()
    with context:
        root = LibraryRoot.create(name="r", path=str(tmp_path).replace("\\", "/") + "/")
        yield context, root
    image_cache.clear()


def _import(context, root, path) -> File:
    file = File.create(root=root, path="", name=Path(path).name, size=1, mtime_millis=1)
    update_fits_header_cache(ChangeList(new_files=[file]), context.status_reporter, context.settings)
    return file


def _solution(width=10, height=8):
    solution = fixed_solution_header()
    solution["NAXIS1"], solution["NAXIS2"] = width, height
    return solution


def _coords(file):
    return {hdu: ra for hdu, ra in Image.select(Image.hdu, Image.coord_ra).where(Image.file == file).tuples()}


class TestStoringASolution:
    def test_every_layer_of_the_solved_size_is_solved(self, library, tmp_path):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits"))

        plate_solution.store(file, _solution(), "ASTAP")

        coords = _coords(file)
        assert coords[0] is not None
        assert coords[1] == coords[0]

    def test_a_layer_of_another_size_stays_unsolved(self, library, tmp_path):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", extension_shape=(3, 4, 5)))

        plate_solution.store(file, _solution(), "ASTAP")

        coords = _coords(file)
        assert coords[0] is not None
        assert coords[1] is None

    def test_the_loaded_row_of_another_size_is_left_alone(self, library, tmp_path):
        context, root = library
        _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", extension_shape=(3, 4, 5)))
        so_row = next(f for f in library_query.matching_files(SearchCriteria()) if f.image.hdu == 1)

        plate_solution.store(so_row, _solution(), "ASTAP")

        assert so_row.image.coord_ra is None

    def test_fields_are_sized_by_the_first_image(self, library, tmp_path):
        context, root = library
        file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits", extension_shape=(3, 4, 5)))
        solution = fixed_solution_header()
        for key in ("NAXIS1", "NAXIS2"):  # without them, a field is sized from the Image row
            solution.remove(key, ignore_missing=True)
        plate_solution.store(file, solution, "ASTAP")

        field = plate_solution.fields_of([file.rowid])[file.rowid]

        assert (field.width, field.height) == (10, 8)
        assert (plate_solution.field_of(file).width, plate_solution.field_of(file).height) == (10, 8)


class TestSolverInput:
    @staticmethod
    def _temp_fits(path, tmp_path, hdu=None):
        out = tmp_path / "solver"
        out.mkdir(exist_ok=True)
        temp = SolverBase._create_temp_fits(SimpleNamespace(tmp_dir=str(out)), Path(path), hdu=hdu)
        with fits.open(temp) as hdul:
            return len(hdul), hdul[0].data.copy(), hdul[0].header.copy()

    def test_the_first_image_is_solved(self, tmp_path):
        path = write_multi_hdu_fits(tmp_path / "draco.fits")
        with fits.open(path) as hdul:
            primary = hdul[0].data[0].copy()

        count, data, header = self._temp_fits(path, tmp_path)

        assert count == 1
        np.testing.assert_array_equal(data, primary)
        assert header["FILTER"] == "HO"
        assert header["NAXIS"] == 2

    @pytest.mark.parametrize("compress", [None, ".xz"])
    def test_an_empty_primary_solves_the_extension_with_the_primary_header(self, tmp_path, compress):
        path = write_multi_hdu_fits(tmp_path / "draco.fits", empty_primary=True, compress=compress)
        with fits.open(tmp_path / "draco.fits") as hdul:
            soo = hdul["SOO"].data[0].copy()

        _, data, header = self._temp_fits(path, tmp_path)

        np.testing.assert_array_equal(data, soo)
        assert header["FILTER"] == "SO"
        assert header["DATE-OBS"] == "2026-11-02T20:00:00"
        assert "XTENSION" not in header and "EXTNAME" not in header and "PCOUNT" not in header
        assert header["SIMPLE"] is True

    def test_a_named_image(self, tmp_path):
        path = write_multi_hdu_fits(tmp_path / "draco.fits")
        with fits.open(path) as hdul:
            soo = hdul["SOO"].data[0].copy()

        _, data, header = self._temp_fits(path, tmp_path, hdu=1)

        np.testing.assert_array_equal(data, soo)
        assert header["FILTER"] == "SO"

    def test_an_xisf_image(self, tmp_path):
        path = tmp_path / "two.xisf"
        _, second = write_two_image_xisf(path)

        _, data, header = self._temp_fits(path, tmp_path, hdu=1)

        np.testing.assert_array_equal(data, second[:, :, 0])
        assert header["FILTER"] == "SO"

    def test_jpeg_of_a_named_image(self, tmp_path):
        path = write_multi_hdu_fits(tmp_path / "draco.fits")

        jpeg = _create_temp_jpeg(path, tmp_path / "jpeg", hdu=1)

        assert jpeg.exists() and jpeg.stat().st_size > 0


class TestAnalysis:
    def test_each_layer_gets_its_own_stats(self, library, tmp_path, qapp):
        from photonfinder.ui.BackgroundLoader import ImageAnalysisTask
        context, root = library
        path = write_multi_hdu_fits(tmp_path / "draco.fits")
        _import(context, root, path)

        task = ImageAnalysisTask(context, SearchCriteria(), [])
        assert context.library_write_lock.acquire(holder="Image analysis")  # as start() does
        task._process_files()

        stats = {hdu: (median, rms) for hdu, median, rms in ImageStats
                 .select(ImageStats.hdu, ImageStats.background_median, ImageStats.background_rms).tuples()}
        assert set(stats) == {0, 1}
        for hdu in (0, 1):
            expected = analyze_file(path, detect_sources=True, hdu=hdu)
            assert stats[hdu] == (expected.background_median, expected.background_rms)
        assert stats[0] != stats[1]


def test_cached_header_of_an_extension_is_merged(library, tmp_path):
    context, root = library
    file = _import(context, root, write_multi_hdu_fits(tmp_path / "draco.fits"))

    header = cached_header(file, 1)

    assert header["FILTER"] == "SO"
    assert header["OBJECT"] == "C 20"
    assert cached_header(file, 0)["FILTER"] == "HO"
    assert cached_header(file, 5) is None


def test_a_colour_cube_with_a_stale_bayer_pattern_is_not_debayered(tmp_path):
    """DWARFLAB stacks are 3-plane colour cubes that still carry the camera's BAYERPAT."""
    path = write_multi_hdu_fits(tmp_path / "draco.fits", primary_cards={"BAYERPAT": "BGGR"})
    image_cache.clear()

    for hdu in (0, 1):
        data, header = load_image_data(path, hdu)
        assert header["BAYERPAT"] == "BGGR"
        assert detect_image_type(data, header) == "rgb"
    image_cache.clear()

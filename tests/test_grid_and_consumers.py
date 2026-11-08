"""Search results are images; actions on files take each file once (ticket 06)."""
import string
from pathlib import Path

import pytest

from photonfinder import library_query, mcp_server
from photonfinder.calibration import CalibrationMatcher, load_session_info
from photonfinder.core import ApplicationContext, StatusReporter
from photonfinder.export_plan import ExportPlan
from photonfinder.filesystem import ChangeList, update_fits_header_cache
from photonfinder.library_query import (
    count_distinct_files, count_files, count_text, distinct_files, image_key, matching_files, search_files,
)
from photonfinder.models import File, Image, ImageStats, LibraryRoot, SearchCriteria
from photonfinder.quality_diagnosis import load_scope, save_diagnoses
from photonfinder.ui.ExportDialog import template_filename

from .conftest import DynamicSettings, McpSettings
from .multi_image_files import write_multi_hdu_fits, write_two_image_xisf


class _Settings(McpSettings):
    def add_known_fits_keywords(self, keywords):
        pass

    def get_obs_timezone(self):
        return ""


@pytest.fixture
def library(tmp_path):
    """A two-layer FITS (HO + SOO), a two-image XISF and a single-image FITS, imported."""
    context = ApplicationContext(str(tmp_path / "library.db"), _Settings())
    context.set_status_reporter(StatusReporter())
    folder = tmp_path / "lib"
    folder.mkdir()
    write_multi_hdu_fits(folder / "draco.fits")
    write_two_image_xisf(folder / "two.xisf")
    write_multi_hdu_fits(folder / "single.fits", table=False)
    from astropy.io import fits
    with fits.open(folder / "single.fits") as hdul:  # just the primary of the same shape
        primary = fits.PrimaryHDU(hdul[0].data, hdul[0].header)
    primary.header["FILTER"] = "L"
    primary.writeto(folder / "single.fits", overwrite=True)
    with context:
        root = LibraryRoot.create(name="r", path=str(folder).replace("\\", "/") + "/")
        files = {name: File.create(root=root, path="", name=name, size=1, mtime_millis=1)
                 for name in ("draco.fits", "two.xisf", "single.fits")}
        update_fits_header_cache(ChangeList(new_files=list(files.values())), context.status_reporter,
                                 context.settings)
        yield context, files


def _rows(criteria=None):
    return search_files(criteria or SearchCriteria(), 0, 100).rows


def _layers(rows):
    return sorted(((row.name, row.image.extname, row.image.filter) for row in rows),
                  key=lambda layer: tuple(value or "" for value in layer))


class TestSearch:
    def test_a_filter_finds_the_layer_it_is_on(self, library):
        assert _layers(_rows(SearchCriteria(filter="SO"))) == [("draco.fits", "SOO", "SO"), ("two.xisf", "SOO", "SO")]
        assert _layers(_rows(SearchCriteria(filter="HO"))) == [("draco.fits", None, "HO"), ("two.xisf", None, "HO")]

    def test_counts_are_of_images_and_of_files(self, library):
        criteria = SearchCriteria()
        assert count_files(criteria) == 5
        assert count_distinct_files(criteria) == 3
        assert count_text(5, 3) == "5 images in 3 files"
        assert count_text(3, 3) == "3 files"

    def test_the_layer_column(self, library):
        column = library_query.RESULT_COLUMNS[library_query.COLUMN_INDEX["layer"]]
        assert sorted(library_query.cell(column, column.value(row))[0] for row in _rows()) == [
            "", "", "", "SOO", "SOO"]

    def test_header_text_matches_a_layer_through_the_primary(self, library):
        """The SOO extension's own header has no OBJECT: it is the primary's."""
        assert _layers(_rows(SearchCriteria(header_text="C 20"))) == [
            ("draco.fits", None, "HO"), ("draco.fits", "SOO", "SO"),
            ("single.fits", None, "L")]

    def test_a_header_value_is_the_layer_s_own_else_the_primary_s(self, library):
        assert _layers(_rows(SearchCriteria(header_text="EXPTIME>10000"))) == [("draco.fits", "SOO", "SO")]
        assert _layers([r for r in _rows(SearchCriteria(header_text="EXPTIME<7000")) if r.name == "draco.fits"]) \
            == [("draco.fits", None, "HO")]

    def test_quality_and_diagnosis_filters_are_per_image(self, library):
        _, files = library
        ImageStats.create(file=files["draco.fits"], hdu=1, star_count=500, diagnosis="Bad FWHM")
        assert _layers(_rows(SearchCriteria(star_count_min=100))) == [("draco.fits", "SOO", "SO")]
        hidden = _rows(SearchCriteria(diagnosis_hidden=["Bad FWHM"]))
        assert ("draco.fits", "SOO", "SO") not in _layers(hidden)
        assert ("draco.fits", None, "HO") in _layers(hidden)


class TestFilesOnce:
    def test_distinct_files_keeps_the_first_row_of_each(self, library):
        rows = _rows()
        files = distinct_files(rows)
        assert sorted(f.name for f in files) == ["draco.fits", "single.fits", "two.xisf"]
        assert {image_key(row) for row in rows} == {(f.rowid, h) for f in library[1].values() for h in (0, 1)
                                                    if not (f.name == "single.fits" and h == 1)}

    def test_export_writes_a_file_once_from_its_first_image(self, library):
        rows = [r for r in _rows() if r.name == "draco.fits"]
        so_row = next(r for r in rows if r.image.hdu == 1)
        plan = ExportPlan([], {}, lambda c: [], other_files=rows)
        assert [e.file.name for e in plan.entries()] == ["draco.fits"]
        template = string.Template("$filter/$filename")
        assert Path(template_filename(so_row, template, DynamicSettings())).as_posix() == "HO/draco.fits"

    def test_each_light_layer_is_a_session_of_its_own_filter(self, library):
        context, _ = library
        lights = [r for r in matching_files(SearchCriteria()) if r.name == "draco.fits"]
        sessions = CalibrationMatcher(context).get_sessions(lights)
        assert sorted(key.filter for key in sessions) == ["HO", "SO"]
        plan = ExportPlan(lights, sessions, lambda c: [])
        assert [e.file.name for e in plan.entries()] == ["draco.fits"]

    def test_unsolved_lights_are_each_file_once(self, library):
        # two.xisf has no IMAGETYP, and frames without a type are solved too
        assert sorted(f.name for f in library_query.unsolved_lights(SearchCriteria())) == [
            "draco.fits", "single.fits", "two.xisf"]


class TestDiagnosis:
    def test_session_info_comes_from_the_primary_header(self, library):
        _, files = library
        from astropy.io.fits import Header
        from photonfinder.core import compress
        from photonfinder.models import FitsHeader
        primary = Header({"SIMPLE": True, "DATE-LOC": "2026-11-02T21:00:00", "SITELAT": "50 00 00",
                          "SITELONG": "4 00 00"})
        FitsHeader.update(header=compress(primary.tostring().encode("ascii"))).where(
            (FitsHeader.file == files["draco.fits"]) & (FitsHeader.hdu == 0)).execute()

        info = load_session_info([files["draco.fits"].rowid])

        assert info[files["draco.fits"].rowid][0] is not None

    def test_verdicts_are_stored_per_image(self, library):
        context, files = library
        draco = files["draco.fits"]
        ImageStats.create(file=draco, hdu=0, star_count=1)
        ImageStats.create(file=draco, hdu=1, star_count=2)

        assert save_diagnoses(context, {(draco.rowid, 1): "Bad FWHM"})

        assert dict(ImageStats.select(ImageStats.hdu, ImageStats.diagnosis).where(ImageStats.file == draco)
                    .tuples()) == {0: None, 1: "Bad FWHM"}

    def test_a_selected_layer_scopes_its_own_peers(self, library):
        context, files = library
        draco = files["draco.fits"]
        scope = load_scope(context, SearchCriteria(), {(draco.rowid, 1)})
        assert [[frame.image for frame in group.frames] for group in scope.groups] == [[(draco.rowid, 1)]]


class TestMcp:
    def test_search_results_are_images(self, library):
        context, files = library
        result = mcp_server.query_search(context, {"object_name": "C 20"})
        assert result["total"] == 3 and result["total_files"] == 2
        assert sorted((r["name"], r["hdu"], r["extname"], r["image"]["filter"]) for r in result["results"]) == [
            ("draco.fits", 0, None, "HO"), ("draco.fits", 1, "SOO", "SO"), ("single.fits", 0, None, "L")]

    def test_file_details_list_every_image_with_its_merged_header(self, library):
        context, files = library
        details = mcp_server.query_file_details(context, files["draco.fits"].rowid)
        assert details["image"]["filter"] == "HO"
        assert details["header"]["FILTER"] == "HO"
        assert [(i["hdu"], i["extname"], i["filter"]) for i in details["images"]] == [(0, None, "HO"), (1, "SOO", "SO")]
        assert details["images"][1]["header"]["FILTER"] == "SO"
        assert details["images"][1]["header"]["OBJECT"] == "C 20"


class TestColumnLayouts:
    @pytest.fixture
    def controller(self, qtbot):
        from PySide6.QtGui import QStandardItemModel
        from PySide6.QtWidgets import QTableView
        from photonfinder.ui.common import ColumnVisibilityController
        model = QStandardItemModel(0, 3)
        model.setHorizontalHeaderLabels(["File name", "Size", "Layer"])
        view = QTableView()
        view.setModel(model)
        qtbot.addWidget(view)
        return view, ColumnVisibilityController(view)

    @staticmethod
    def _hidden(view):
        from PySide6.QtCore import Qt
        return [view.model().headerData(c, Qt.Orientation.Horizontal) for c in range(3) if view.isColumnHidden(c)]

    def test_a_layout_saved_before_the_layer_column_keeps_it_hidden(self, controller):
        view, visibility = controller
        visibility.load_visibility("Size")
        assert self._hidden(view) == ["Size", "Layer"]

    def test_showing_it_is_saved(self, controller):
        view, visibility = controller
        visibility.load_visibility("Size")
        view.setColumnHidden(2, False)
        saved = visibility.save_visibility()
        assert saved == "Size,+Layer"
        visibility.load_visibility("")
        visibility.load_visibility(saved)
        assert self._hidden(view) == ["Size"]

    def test_presets(self, controller):
        from photonfinder.ui.common import _BUILTIN_PRESETS
        view, visibility = controller
        visibility.load_visibility(",".join(_BUILTIN_PRESETS["Standard"]))
        assert "Layer" in self._hidden(view)
        visibility.load_visibility(",".join(_BUILTIN_PRESETS["Full"]))
        assert self._hidden(view) == []

"""The toolbar's search filters, on a grid holding all three synthetic libraries.

Each filter is added the way the user does it: the toolbar action opens its dialog, often
prefilled from the selected row. The grid must then show exactly the generated files that
match, and removing the filter's button (right click) brings the rest back.
"""
from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest
from PySide6.QtCore import Qt

from photonfinder.ui.CoordinatesDialog import CoordinatesDialog
from photonfinder.ui.DateRangeDialog import DateRangeDialog
from photonfinder.ui.ExposureDialog import ExposureDialog
from photonfinder.ui.GainDialog import GainDialog
from photonfinder.models import CORE_MODELS, DIAGNOSIS_BAD_VALUES, File, Image, ImageStats, SearchCriteria
from photonfinder.ui.RangeFilterDialogs import DiagnosisFilterDialog, ImageQualityDialog, ImageSizeDialog, PlateScaleDialog
from photonfinder.ui.SearchPanel import AdvancedFilter
from photonfinder.ui.TelescopeDialog import TelescopeDialog
from photonfinder.ui.TemperatureDialog import TemperatureDialog
from tests.e2e.helpers import Grid, add_libraries, choose
from tests.e2e.synthetic_library import Frame, OSC, SyntheticLibraries


@pytest.fixture
def grid(sandbox, app, modals, boundary, libraries) -> Grid:
    add_libraries(sandbox, modals, boundary, libraries.roots, scan=True)
    g = sandbox.grid()
    assert g.total == len(libraries.files)
    return g


def matching(libraries: SyntheticLibraries, keep: Callable[[Frame], bool]) -> list[str]:
    names = sorted(p.name for p, f in libraries.files.items() if keep(f))
    assert names, "the filter under test should match some files"
    return names


def shown(grid: Grid) -> list[str]:
    return sorted(grid.column("File name"))


def add_filter(sandbox, modals, action, dialog_type, fn=lambda d: None):
    with modals.handle(dialog_type, fn):
        action.trigger()
    sandbox.wait_idle()


def filter_button(grid: Grid, kind: AdvancedFilter):
    button = grid.panel.advanced_options.get(kind)
    assert button is not None and button.isVisible(), f"no {kind.name} filter button"
    return button


def remove_filter(sandbox, grid: Grid, kind: AdvancedFilter):
    sandbox.qtbot.mouseClick(filter_button(grid, kind), Qt.MouseButton.RightButton)
    sandbox.wait_idle()
    assert kind not in grid.panel.advanced_options


def name_of(libraries: SyntheticLibraries, keep: Callable[[Frame], bool]) -> str:
    """The earliest file whose frame passes ``keep``."""
    return min((f.date_obs, p.name) for p, f in libraries.files.items() if keep(f))[1]


def test_exposure_from_the_selected_row(sandbox, app, modals, libraries, grid):
    grid.select("File name", name_of(libraries, lambda f: f.object_name == "M 31"))

    def check_prefill(dialog: ExposureDialog):
        assert dialog.get_exposure() == 120.0

    add_filter(sandbox, modals, app.actionExposure, ExposureDialog, check_prefill)

    assert filter_button(grid, AdvancedFilter.EXPOSURE).text() == "Exposure: 120.0 s"
    assert shown(grid) == matching(libraries, lambda f: f.exposure == 120.0)
    remove_filter(sandbox, grid, AdvancedFilter.EXPOSURE)
    assert grid.row_count() == len(libraries.files)


def test_exposure_zero_from_a_bias_row(sandbox, app, modals, libraries, grid):
    grid.select("File name", name_of(libraries, lambda f: f.image_type == "BIAS"))

    def check_prefill(dialog: ExposureDialog):
        assert dialog.get_exposure() == 0.0

    add_filter(sandbox, modals, app.actionExposure, ExposureDialog, check_prefill)

    assert shown(grid) == matching(libraries, lambda f: f.exposure == 0.0)


def test_plate_scale_from_the_selected_row(sandbox, app, modals, libraries, grid):
    name = name_of(libraries, lambda f: f.object_name == "M 31")
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        Image.update(coord_radius=0.0123457).where(Image.file == File.get(File.name == name)).execute()
    grid.panel.update_search_criteria()
    sandbox.wait_idle()
    grid.select("File name", name)

    def check_prefill(dialog: PlateScaleDialog):
        low, high = dialog.get_scale_criteria()
        assert low is not None and low == high

    add_filter(sandbox, modals, app.actionPlateScale, PlateScaleDialog, check_prefill)

    assert shown(grid) == [name]


def _no_filter_frames(libraries) -> list[str]:
    return matching(libraries, lambda f: f.filter is None)


def test_a_saved_search_for_an_empty_value_restores_as_empty(sandbox, app, libraries, grid):
    """filter = None means "files without a filter" (IS NULL), not "any filter"."""
    saved = SearchCriteria.from_json(SearchCriteria(filter=None).to_json())
    assert saved.filter is None

    grid.panel.apply_search_criteria(saved)
    sandbox.wait_idle()

    assert grid.panel.filter_filter_combo.currentText() == "<empty>"
    assert grid.panel.search_criteria.filter is None
    assert shown(grid) == _no_filter_frames(libraries)


def test_duplicating_a_tab_keeps_an_empty_value(sandbox, app, libraries, grid):
    grid.panel.apply_search_criteria(SearchCriteria(filter=None))
    sandbox.wait_idle()

    app.actionDuplicate_Tab.trigger()
    sandbox.wait_idle()

    copy = app.get_current_search_panel()
    assert copy is not grid.panel
    assert copy.search_criteria.filter is None
    assert shown(sandbox.grid(copy)) == _no_filter_frames(libraries)


def test_gain_then_edit_to_gain_and_offset(sandbox, app, modals, libraries, grid):
    add_filter(sandbox, modals, app.actionGain, GainDialog, lambda d: d.set_gain(120))
    assert shown(grid) == matching(libraries, lambda f: f.gain == 120)

    def gain_100_offset_50(dialog: GainDialog):
        assert dialog.get_gain() == 120  # the dialog reopens with the current filter
        dialog.set_gain(100)
        dialog.set_offset(50)

    with modals.handle(GainDialog, gain_100_offset_50):
        filter_button(grid, AdvancedFilter.GAIN).click()  # left click edits
    sandbox.wait_idle()

    assert shown(grid) == matching(libraries, lambda f: f.gain == 100 and f.offset == 50)
    assert len(grid.panel.advanced_options) == 1


def test_temperature(sandbox, app, modals, libraries, grid):
    add_filter(sandbox, modals, app.actionTemperature, TemperatureDialog, lambda d: d.set_temperature(-20.0))
    assert shown(grid) == matching(libraries, lambda f: f.set_temp == -20.0)


def test_telescope_matches_part_of_the_name(sandbox, app, modals, libraries, grid):
    add_filter(sandbox, modals, app.actionTelescope, TelescopeDialog, lambda d: d.set_telescope("RedCat"))
    assert shown(grid) == matching(libraries, lambda f: f.telescope == "RedCat 51")


def test_coordinates_from_the_selected_row(sandbox, app, modals, libraries, grid):
    grid.select("File name", name_of(libraries, lambda f: f.object_name == "M 31"))

    def check_prefill(dialog: CoordinatesDialog):
        ra, dec, radius = dialog.get_coordinates()
        assert ra.startswith("0:42:") and dec.startswith("41:1"), (ra, dec)
        assert radius == 0.5

    add_filter(sandbox, modals, app.actionCoordinates, CoordinatesDialog, check_prefill)
    assert shown(grid) == matching(libraries, lambda f: f.object_name == "M 31")


def test_coordinates_typed_in(sandbox, app, modals, libraries, grid):
    def ngc7000(dialog: CoordinatesDialog):
        dialog.set_coordinates("20:59:00", "+44:31:00", 1.0)

    add_filter(sandbox, modals, app.actionCoordinates, CoordinatesDialog, ngc7000)
    assert shown(grid) == matching(libraries, lambda f: f.object_name == "NGC 7000")


def test_date_range_from_the_selected_row(sandbox, app, modals, libraries, grid):
    """The grid and the date dialog show the computer's local time, while date_obs and the
    search criteria are UTC. Only a computer whose clock is not on UTC can tell the two
    apart, so on a UTC machine (like the CI runner) this test proves less."""
    first_ha = min((f for f in libraries.files.values()
                    if f.object_name == "NGC 7000" and f.filter == "Ha" and f.camera != OSC),
                   key=lambda f: f.date_obs)
    grid.select("File name", name_of(libraries, lambda f: f == first_ha))
    local = first_ha.date_obs.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)

    def half_an_hour(dialog: DateRangeDialog):
        start, _ = dialog.get_datetime_range()
        assert abs(start - local) < timedelta(seconds=1), (start, local)
        dialog.set_start_date(local - timedelta(minutes=1))
        dialog.set_end_date(local + timedelta(minutes=30))

    add_filter(sandbox, modals, app.actionDate, DateRangeDialog, half_an_hour)

    start_utc = first_ha.date_obs - timedelta(minutes=1)
    end_utc = first_ha.date_obs + timedelta(minutes=30)
    expected = matching(libraries, lambda f: start_utc <= f.date_obs <= end_utc)
    assert shown(grid) == expected
    label = filter_button(grid, AdvancedFilter.DATETIME).text()
    assert label == f"Date {local - timedelta(minutes=1):%Y-%m-%d %H:%M:%S} - "                     f"{local + timedelta(minutes=30):%Y-%m-%d %H:%M:%S}"

    def unchanged(dialog: DateRangeDialog):
        start, end = dialog.get_datetime_range()  # reopens in local time, not UTC
        assert abs(start - (local - timedelta(minutes=1))) < timedelta(seconds=1), start
        assert abs(end - (local + timedelta(minutes=30))) < timedelta(seconds=1), end

    with modals.handle(DateRangeDialog, unchanged):
        filter_button(grid, AdvancedFilter.DATETIME).click()
    sandbox.wait_idle()
    assert shown(grid) == expected

    app = sandbox.restart()  # the filter comes back from the session file
    grid = sandbox.grid(app.get_search_panels()[-1])
    assert filter_button(grid, AdvancedFilter.DATETIME).text() == label
    assert shown(grid) == expected


def test_plate_solved_and_unsolved(sandbox, app, modals, boundary, libraries, grid):
    boundary.answer("getItem", ("Solved", True))
    app.actionPlateSolved.trigger()
    sandbox.wait_idle()
    assert shown(grid) == matching(libraries, lambda f: f.solved)
    assert set(grid.column("Solved")) == {"True"}

    boundary.answer("getItem", ("Unsolved", True))
    filter_button(grid, AdvancedFilter.PLATE_SOLVED).click()
    sandbox.wait_idle()
    assert shown(grid) == matching(libraries, lambda f: not f.solved)


def test_image_size(sandbox, app, modals, libraries, grid):
    def at_most_30_wide(dialog: ImageSizeDialog):
        dialog.set_from_criteria(None, 30, None, None)

    add_filter(sandbox, modals, app.actionImageSize, ImageSizeDialog, at_most_30_wide)
    assert shown(grid) == matching(libraries, lambda f: f.width <= 30)


def test_header_text(sandbox, app, boundary, libraries, grid):
    boundary.answer("getText", ("FOCALLEN=250", True))
    app.actionHeader_Text.trigger()
    sandbox.wait_idle()
    assert shown(grid) == matching(libraries, lambda f: f.telescope == "RedCat 51")


def test_filters_combine_with_the_type_box_and_come_off_again(sandbox, app, modals, libraries, grid):
    choose(sandbox, grid.panel.filter_type_combo, "LIGHT")
    add_filter(sandbox, modals, app.actionExposure, ExposureDialog, lambda d: d.set_exposure(300.0))

    expected = matching(libraries, lambda f: f.image_type == "LIGHT" and f.exposure == 300.0)
    assert shown(grid) == expected
    assert grid.panel.get_title().startswith(f"{len(expected)} files [")

    remove_filter(sandbox, grid, AdvancedFilter.EXPOSURE)
    assert shown(grid) == matching(libraries, lambda f: f.image_type == "LIGHT")


ROW_STATS = dict(star_count=480, fwhm_median=3.2547, background_median=-12.53, background_rms=14.75,
                 elongation_median=1.1234)  # more digits than the dialog shows; a bias-subtracted background


def test_image_statistics_from_the_selected_row(sandbox, app, modals, libraries, grid):
    names = shown(grid)
    analysed, other = names[0], names[1]
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        ImageStats.create(file=File.get(File.name == analysed), **ROW_STATS)
        ImageStats.create(file=File.get(File.name == other), star_count=900, fwhm_median=2.0,
                          background_median=800.0, background_rms=10.0, elongation_median=1.0)
    grid.panel.update_search_criteria()
    sandbox.wait_idle()
    grid.select("File name", analysed)

    def check_prefill(dialog: ImageQualityDialog):
        """Each range is as tight as its spinbox shows, and still holds the row's own value."""
        q = dialog.get_quality_criteria()
        assert (q["star_count_min"], q["star_count_max"]) == (480, 480)
        for criterion, stat, decimals in (("fwhm", "fwhm_median", 2), ("background", "background_median", 1),
                                          ("background_rms", "background_rms", 1),
                                          ("elongation", "elongation_median", 2)):
            low, high, value = q[f"{criterion}_min"], q[f"{criterion}_max"], ROW_STATS[stat]
            assert low <= value <= high, (criterion, low, value, high)
            assert high - low <= 10 ** -decimals + 1e-9, (criterion, low, high)

    add_filter(sandbox, modals, app.actionImageQuality, ImageQualityDialog, check_prefill)

    assert shown(grid) == [analysed]
    remove_filter(sandbox, grid, AdvancedFilter.IMAGE_QUALITY)
    assert grid.row_count() == len(libraries.files)


def test_image_statistics_without_stats_on_the_row_shows_the_current_filter(sandbox, app, modals, libraries,
                                                                           grid):
    grid.panel.search_criteria.fwhm_max = 4.0
    grid.select("File name", shown(grid)[0])  # nothing analysed: the row has no stats

    def check_prefill(dialog: ImageQualityDialog):
        q = dialog.get_quality_criteria()
        assert q["fwhm_max"] == 4.0
        assert q["star_count_min"] is None

    add_filter(sandbox, modals, app.actionImageQuality, ImageQualityDialog, check_prefill)


def test_image_statistics_cleared_in_the_dialog_leaves_no_chip(sandbox, app, modals, libraries, grid):
    grid.panel.search_criteria.fwhm_max = 4.0

    def clear_all(dialog: ImageQualityDialog):
        for name in ("stars", "fwhm", "bg", "bgrms", "elong"):
            for bound in ("min", "max"):
                getattr(dialog, f"_{name}_{bound}_cb").setChecked(False)
        assert all(v is None for v in dialog.get_quality_criteria().values())

    add_filter(sandbox, modals, app.actionImageQuality, ImageQualityDialog, clear_all)

    assert AdvancedFilter.IMAGE_QUALITY not in grid.panel.advanced_options
    assert grid.panel.search_criteria.fwhm_max is None


def test_a_restored_image_statistics_filter_keeps_its_chip_and_its_ranges(sandbox, app, libraries, grid):
    saved = SearchCriteria(fwhm_max=4.0, star_count_min=10)

    grid.panel.apply_search_criteria(saved)
    sandbox.wait_idle()
    grid.panel.apply_search_criteria(saved)  # onto a panel already showing the chip
    sandbox.wait_idle()

    assert filter_button(grid, AdvancedFilter.IMAGE_QUALITY).text() == "Quality: Stars, FWHM"
    assert (grid.panel.search_criteria.fwhm_max, grid.panel.search_criteria.star_count_min) == (4.0, 10)
    remove_filter(sandbox, grid, AdvancedFilter.IMAGE_QUALITY)
    assert grid.panel.search_criteria.fwhm_max is None


def test_quality_diagnosis_filter(sandbox, app, modals, libraries, grid):
    names = shown(grid)
    bad, both, good = names[0], names[1], names[2]
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        for name, diagnosis in ((bad, "Bad FWHM"), (both, "Bad FWHM, Bad background"), (good, "OK")):
            ImageStats.create(file=File.get(File.name == name), diagnosis=diagnosis, fwhm_median=3.25, star_count=480)
    grid.panel.update_search_criteria()
    sandbox.wait_idle()
    grid.load_all()
    assert "Diagnosis" in grid.headers()
    row = next(r for r in grid.rows() if r["File name"] == both)
    assert row["Diagnosis"] == "Bad FWHM, Bad background"
    assert (row["FWHM"], row["Stars"]) == ("3.25", "480")  # the numbers sit in their own columns

    def check_default(dialog: DiagnosisFilterDialog):
        assert not dialog.nothing_diagnosed_label.isVisible()
        assert dialog.get_hidden() == list(DIAGNOSIS_BAD_VALUES)  # the default hides bad frames

    add_filter(sandbox, modals, app.actionQualityDiagnosis, DiagnosisFilterDialog, check_default)
    assert filter_button(grid, AdvancedFilter.QUALITY_DIAGNOSIS).text() == "Diagnosis: hide bad"
    assert app.actionQualityDiagnosis.isChecked()
    grid.load_all()
    assert shown(grid) == sorted(set(names) - {bad, both})

    # The filter button edits it, and the dialog reopens with what's hidden now.
    def hide_only_background(dialog: DiagnosisFilterDialog):
        assert dialog.get_hidden() == list(DIAGNOSIS_BAD_VALUES)
        dialog.set_hidden(["Bad background"])

    with modals.handle(DiagnosisFilterDialog, hide_only_background):
        filter_button(grid, AdvancedFilter.QUALITY_DIAGNOSIS).click()
    sandbox.wait_idle()
    grid.load_all()
    assert shown(grid) == sorted(set(names) - {both})  # one Bad value is enough to hide it

    # Restoring a tab's saved criteria brings back the filter and its button.
    saved = SearchCriteria.from_json(grid.panel.search_criteria.to_json())
    remove_filter(sandbox, grid, AdvancedFilter.QUALITY_DIAGNOSIS)
    assert grid.total == len(libraries.files)
    grid.panel.apply_search_criteria(saved)
    sandbox.wait_idle()
    assert filter_button(grid, AdvancedFilter.QUALITY_DIAGNOSIS).text() == "Diagnosis (1 hidden)"
    assert grid.total == len(libraries.files) - 1


def test_quality_diagnosis_filter_says_when_nothing_is_diagnosed(sandbox, app, modals, grid):
    def check(dialog: DiagnosisFilterDialog):
        assert dialog.nothing_diagnosed_label.isVisible()

    add_filter(sandbox, modals, app.actionQualityDiagnosis, DiagnosisFilterDialog, check)

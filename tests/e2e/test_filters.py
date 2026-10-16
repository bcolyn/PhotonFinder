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
from photonfinder.ui.RangeFilterDialogs import ImageSizeDialog
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

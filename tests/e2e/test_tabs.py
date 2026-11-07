"""Tabs opened from the grid, filter buttons, and scans across several tabs."""
import shutil

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from photonfinder.ui.ExposureDialog import ExposureDialog
from photonfinder import filter_catalogue
from photonfinder.ui.SearchPanel import FilterButton
from tests.e2e.helpers import Grid, add_libraries, wait_for_scan
from tests.e2e.test_filters import add_filter, remove_filter


@pytest.fixture
def grid(sandbox, app, modals, boundary, libraries) -> Grid:
    add_libraries(sandbox, modals, boundary, libraries.roots, scan=True)
    return sandbox.grid()


def _first_light(libraries) -> str:
    return min((f.date_obs, p.name) for p, f in libraries.files.items() if f.image_type == "LIGHT")[1]


@pytest.mark.parametrize("action", ["actionFind_matching_darks", "actionFind_matching_flats"])
def test_matching_calibration_tab_is_announced_and_keeps_the_columns(sandbox, app, libraries, grid, action):
    grid.select("File name", _first_light(libraries))
    source = grid.panel
    source.visibility_controller.load_visibility("Path,Size,Exposure")
    hidden = source.visibility_controller.save_visibility()
    announced = []
    app.tabs_changed.connect(announced.append)

    getattr(app, action).trigger()
    sandbox.wait_idle()

    new_panel = app.get_current_search_panel()
    assert new_panel is not source
    assert announced and new_panel in announced[-1]
    assert new_panel.visibility_controller.save_visibility() == hidden


def test_removing_filters_leaves_no_filter_buttons_behind(sandbox, app, modals, grid):
    for _ in range(3):
        add_filter(sandbox, modals, app.actionExposure, ExposureDialog, lambda d: d.set_exposure(120.0))
        remove_filter(sandbox, grid, filter_catalogue.EXPOSURE)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert grid.panel.findChildren(FilterButton) == []


def test_a_scan_refreshes_every_open_tab(sandbox, app, modals, boundary, libraries, tmp_path):
    root = tmp_path / "library"
    shutil.copytree(libraries.roots["simple"], root)
    add_libraries(sandbox, modals, boundary, {"simple": root}, scan=True)
    first = app.get_current_search_panel()
    app.action_New_Tab.trigger()
    sandbox.wait_idle()
    assert app.get_current_search_panel() is not first

    added = next(p for p in root.rglob("*") if p.is_file())
    copy = added.parent.parent / ("COPY_" + added.name)
    shutil.copy(added, copy)
    app.action_Scan_Libraries.trigger()
    wait_for_scan(sandbox)

    for panel in app.get_search_panels():
        assert copy.name in sandbox.grid(panel).column("File name"), panel.title

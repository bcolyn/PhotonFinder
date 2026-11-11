"""Sort By, for the tiles as for the table: chosen from the View menu (also the toolbar's dropdown)
or the tile view's right-click menu, it sorts the shared rows, and the table header shows it."""
import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMenu

from photonfinder.library_query import COLUMN_INDEX
from tests.e2e.helpers import Grid, add_libraries


@pytest.fixture
def grid(sandbox, app, modals, boundary, libraries) -> Grid:
    add_libraries(sandbox, modals, boundary, libraries.roots, scan=True)
    return sandbox.grid()


def _choose(sandbox, menu: QMenu, text: str):
    menu.aboutToShow.emit()  # fills the menu as opening it does
    action = next(a for a in menu.actions() if a.text() == text)
    assert action.isEnabled(), f"{text} is disabled"
    action.trigger()
    sandbox.wait_idle()


def _checked(menu: QMenu) -> list[str]:
    menu.aboutToShow.emit()
    return [a.text() for a in menu.actions() if a.isChecked()]


def _sort_indicator(panel):
    header = panel.dataView.horizontalHeader()
    return header.sortIndicatorSection(), header.sortIndicatorOrder()


def test_sort_by_from_the_view_menu_sorts_the_tiles_and_the_table(sandbox, app, grid):
    panel = grid.panel
    assert panel.thumbnail_mode
    menu = app.menuSortBy
    assert app.actionSortBy.menu() is menu
    assert _checked(menu) == ["None"]

    _choose(sandbox, menu, "File name")
    names = grid.column("File name")
    assert len(names) > 1
    assert names == sorted(names, key=str.lower)
    assert _checked(menu) == ["File name", "Ascending"]
    assert _sort_indicator(panel) == (COLUMN_INDEX["name"], Qt.SortOrder.AscendingOrder)

    _choose(sandbox, menu, "Descending")
    assert grid.column("File name") == sorted(names, key=str.lower, reverse=True)
    assert _sort_indicator(panel) == (COLUMN_INDEX["name"], Qt.SortOrder.DescendingOrder)

    # Another column keeps the direction.
    _choose(sandbox, menu, "Exposure")
    assert _checked(menu) == ["Exposure", "Descending"]
    exposures = [float(e) for e in grid.column("Exposure") if e]
    assert exposures == sorted(exposures, reverse=True)

    _choose(sandbox, menu, "None")
    assert _checked(menu) == ["None"]
    assert all(not a.isEnabled() for a in menu.actions() if a.text() in ("Ascending", "Descending"))
    assert panel.search_criteria.sorting_index is None

    # The table's header and the menu are one sort.
    app.actionThumbnails.trigger()
    panel.dataView.horizontalHeader().setSortIndicator(COLUMN_INDEX["object_name"], Qt.SortOrder.AscendingOrder)
    sandbox.wait_idle()
    assert _checked(menu) == ["Object", "Ascending"]


@pytest.mark.parametrize("label", ["Observation Date", "Night"])
def test_dates_sort_in_date_order(sandbox, app, grid, label):
    """The date columns hold dates, which the in-memory sort used to leave in no order."""
    _choose(sandbox, app.menuSortBy, label)
    shown = grid.column(label)
    assert len(set(shown)) > 2 and all(shown)
    assert shown == sorted(shown)  # their text, "YYYY-MM-DD[ HH:MM:SS]", sorts as the date does
    _choose(sandbox, app.menuSortBy, "Descending")
    assert grid.column(label) == sorted(shown, reverse=True)


def test_frames_and_integration_are_offered_only_when_grouped(sandbox, app, grid):
    def offered():
        app.menuSortBy.aboutToShow.emit()
        return [a.text() for a in app.menuSortBy.actions()]

    assert "Frames" not in offered() and "Integration" not in offered()
    # The table's column order: Night after Observation Date.
    assert offered().index("Night") == offered().index("Observation Date") + 1
    app.actionGroupSimilarFrames.trigger()
    sandbox.wait_idle()
    assert "Frames" in offered() and "Integration" in offered()
    _choose(sandbox, app.menuSortBy, "Frames")
    frames = [int(n) for n in sandbox.grid().column("Frames")]
    assert frames == sorted(frames)


def _right_click(panel, position, choose_sort: str | None = None) -> dict:
    """Right-click the active view at `position` and read the real menu: {item text: its submenu}.
    The menu blocks in its own event loop, so a timer reads it from inside that loop, optionally
    chooses an item of its Sort By submenu, and always closes it: a menu left open would hang
    the test."""
    items = {}

    def read():
        menu = QApplication.activePopupWidget()
        try:
            items.update((a.text(), a.menu()) for a in menu.actions())
            if choose_sort is not None:
                sort_menu = items["Sort By"]
                next(a for a in sort_menu.actions() if a.text() == choose_sort).trigger()
        finally:
            if menu.isVisible():
                menu.close()

    QTimer.singleShot(0, read)
    panel.show_context_menu(position)
    return items


def test_the_tile_view_right_click_menu_has_sort_by(sandbox, app, grid):
    panel = grid.panel
    view = panel.thumbnailView
    on_tile = _right_click(panel, view.visualRect(view.model().index(0, 0)).center())
    assert on_tile["Sort By"] is not None and "View" in on_tile

    empty = view.viewport().rect().bottomRight()
    assert not view.indexAt(empty).isValid()
    on_empty = _right_click(panel, empty, choose_sort="File name")  # on empty space: only Sort By
    assert list(on_empty) == ["Sort By"]
    sandbox.wait_idle()
    names = grid.column("File name")
    assert names == sorted(names, key=str.lower)

    app.actionThumbnails.trigger()  # the table's right-click menu is unchanged
    table = panel.dataView
    assert "Sort By" not in _right_click(panel, table.visualRect(table.model().index(0, 0)).center())


def test_no_tab_open(sandbox, app, grid):
    while app.tabWidget.count():
        app.tabWidget.removeTab(0)
    app.menuSortBy.aboutToShow.emit()
    assert [(a.text(), a.isEnabled()) for a in app.menuSortBy.actions()] == [("No search tab open", False)]

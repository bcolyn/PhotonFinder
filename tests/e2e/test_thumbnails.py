"""The thumbnail view: what a first start and the last tab closed show, toggled from the
toolbar action, over the same rows and selection as the table, a double-click on a tile opens
the viewer, and the mode comes back after a restart."""
import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication

from photonfinder.ui.ThumbnailView import ThumbnailLoader
from tests.e2e.helpers import Grid, add_libraries
from tests.multi_image_files import write_multi_hdu_fits


@pytest.fixture
def grid(sandbox, app, modals, boundary, libraries) -> Grid:
    add_libraries(sandbox, modals, boundary, libraries.roots, scan=True)
    return sandbox.grid()


def _wait_for_thumbnails(app):
    loader = app.thumbnails
    while True:
        loader.pool.waitForDone()
        QApplication.processEvents()
        if loader.pending_count() == 0:
            return


def _tile_center(view, row):
    return view.visualRect(view.model().index(row, 0)).center()


def test_toggle_keeps_the_selection_and_double_click_opens_the_viewer(sandbox, app, grid, qtbot):
    panel = grid.panel
    app.actionThumbnails.trigger()  # to the table
    assert panel.result_views.currentWidget() is panel.dataView
    grid.load_all()
    name = grid.column("File name")[3]
    grid.select("File name", name)

    app.actionThumbnails.trigger()
    sandbox.wait_idle()

    view = panel.thumbnailView
    assert panel.result_views.currentWidget() is view
    assert app.actionThumbnails.isChecked()
    assert [f.name for f in panel.get_selected_files()] == [name]

    # A click on another tile selects that row, whole, as the table does.
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=_tile_center(view, 5))
    other = panel.get_file_at_row(5).name
    assert [f.name for f in panel.get_selected_files()] == [other]
    assert len(panel.dataView.selectionModel().selectedRows()) == 1

    qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=_tile_center(view, 5))
    assert app.image_viewer is not None and app.image_viewer.isVisible()
    assert app.image_viewer._filename.endswith(other)  # pylint: disable=protected-access
    app.image_viewer.close()

    app.actionThumbnails.trigger()  # back to the table, the selection still there
    assert panel.result_views.currentWidget() is panel.dataView
    assert [f.name for f in panel.get_selected_files()] == [other]


def test_visible_tiles_get_thumbnails_cached_in_the_sandbox(sandbox, app, grid):
    panel = grid.panel
    view = panel.thumbnailView
    QApplication.processEvents()  # paint, which asks for the visible tiles
    _wait_for_thumbnails(app)

    visible = [row for row in range(panel.proxy_model.rowCount())
               if view.visualRect(view.model().index(row, 0)).intersects(view.viewport().rect())]
    assert visible
    images = [panel._thumbnail_image(view.model().index(row, 0)) for row in visible]  # pylint: disable=protected-access
    for key, _, _, _ in images:
        pixmap = app.thumbnails.pixmap(key)
        assert pixmap is not None and pixmap is not ThumbnailLoader.FAILED
        assert max(pixmap.width(), pixmap.height()) <= 200
    assert len(list((sandbox.data_dir / "thumbs").rglob("*.jpg"))) == len(visible)
    # Rows below the fold were never asked for.
    assert len(visible) < panel.proxy_model.rowCount()


def test_a_multi_layer_file_has_a_tile_per_image(sandbox, app, modals, boundary, tmp_path):
    folder = tmp_path / "dwarf"
    folder.mkdir()
    write_multi_hdu_fits(folder / "draco.fits")
    add_libraries(sandbox, modals, boundary, {"dwarf": folder}, scan=True)
    panel = app.get_current_search_panel()
    QApplication.processEvents()
    _wait_for_thumbnails(app)

    model = panel.thumbnailView.model()
    images = [panel._thumbnail_image(model.index(row, 0)) for row in range(model.rowCount())]  # pylint: disable=protected-access
    assert sorted(hdu for _, _, hdu, _ in images) == [0, 1]
    assert all(app.thumbnails.pixmap(key) not in (None, ThumbnailLoader.FAILED) for key, _, _, _ in images)

    panel.thumbnailView.selectionModel().select(
        model.index(1, 0), QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
    assert len(panel.get_selected_files()) == 1


def test_a_first_start_and_the_last_tab_closed_show_thumbnails(sandbox, app):
    [panel] = app.get_search_panels()
    assert panel.thumbnail_mode and panel.result_views.currentWidget() is panel.thumbnailView
    assert app.actionThumbnails.isChecked()

    app.actionThumbnails.trigger()  # the table
    app.close_current_search_tab()

    [panel] = app.get_search_panels()
    assert panel.thumbnail_mode


def test_each_tab_comes_back_as_it_was_after_a_restart(sandbox, app, grid):
    app.new_search_tab()  # a new tab shows what the current one does: thumbnails
    app.actionThumbnails.trigger()  # this one the table
    app.new_search_tab()  # and so this one
    assert [panel.thumbnail_mode for panel in app.get_search_panels()] == [True, False, False]

    app = sandbox.restart()

    assert [panel.thumbnail_mode for panel in app.get_search_panels()] == [True, False, False]
    assert not app.actionThumbnails.isChecked()  # the last tab is the current one


def test_a_new_tab_gives_the_results_most_of_the_width(sandbox, app):
    app.resize(1200, 800)
    panel = app.new_search_tab()
    sandbox.wait_idle()
    QApplication.processEvents()

    tree, results = panel.splitter.sizes()
    assert results > 2 * tree, (tree, results)


def test_settings_clears_the_thumbnail_cache(sandbox, app, grid, modals):
    from photonfinder.ui.SettingsDialog import SettingsDialog
    QApplication.processEvents()
    _wait_for_thumbnails(app)
    thumbs = sandbox.data_dir / "thumbs"
    made = len(list(thumbs.rglob("*.jpg")))
    assert made > 0
    seen = {}

    def clear(dialog: SettingsDialog):
        dialog.tab_widget.setCurrentWidget(dialog.thumbnails_tab)
        seen["before"] = dialog.thumbnails_usage_label.text()
        dialog.clear_thumbnails_button.click()
        seen["after"] = dialog.thumbnails_usage_label.text()
        seen["files"] = len(list(thumbs.rglob("*.jpg")))
        seen["enabled"] = dialog.clear_thumbnails_button.isEnabled()

    with modals.handle(SettingsDialog, clear):
        app.action_Settings.trigger()

    assert seen["before"].startswith(f"The cache holds {made} thumbnails")
    assert seen["after"].startswith(f"Removed {made} thumbnails")
    assert seen["files"] == 0 and not seen["enabled"]

    QApplication.processEvents()  # the tiles in view repaint and ask again
    _wait_for_thumbnails(app)
    assert len(list(thumbs.rglob("*.jpg"))) == made

"""Ticket 29: one library writer at a time. The jobs that write to the library hold its write
lock, and what starts them is disabled while another writer holds it, in this window or in
another PhotonFinder window. Starting still asks the lock, as the disabled state can be stale."""
import threading

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from photonfinder.core import OTHER_WINDOW, DatabaseLock, library_busy_message
from photonfinder.models import CORE_MODELS, LibraryRoot
from photonfinder.ui.ImageViewerWindow import ImageViewerWindow
from photonfinder.ui.LibraryRootDialog import LibraryRootDialog
from photonfinder.ui.LibraryRootEditDialog import LibraryRootEditDialog
from photonfinder.ui.PlateSolveDialog import PlateSolveDialog
from tests.e2e.test_cancelling_a_solve import solve  # noqa: F401 (fixture)
from tests.e2e.test_diagnose_quality import GOOD_RESULT, add_frames, lock_is_free, run_analyse_images, search


def _main_writer_actions(app):
    return [app.action_Scan_Libraries, app.actionCompress_files, app.actionPlate_solve_files]


@pytest.fixture
def other_window(sandbox, app):
    """The write lock held by another PhotonFinder window on this library."""
    lock = DatabaseLock(sandbox.context.database_path, ".write.lock")
    assert lock.acquire(blocking=False, holder="Library scan")
    yield lock
    if lock.holder is not None:
        lock.release()


def test_menu_actions_are_disabled_while_another_window_writes(sandbox, app, other_window):
    app.menu_Tools.aboutToShow.emit()  # another window can only be seen by asking, when a menu opens

    for action in _main_writer_actions(app):
        assert not action.isEnabled(), action.text()
        assert action.toolTip() == library_busy_message(OTHER_WINDOW)

    other_window.release()
    app.menu_File.aboutToShow.emit()
    assert all(action.isEnabled() for action in _main_writer_actions(app))
    assert lock_is_free(sandbox)  # asking didn't keep it


def test_menu_actions_follow_a_writer_in_this_window_at_once(sandbox, app):
    lock = sandbox.context.library_write_lock
    assert lock.acquire(blocking=False, holder="Image analysis")
    try:
        sandbox.qtbot.waitUntil(lambda: not app.action_Scan_Libraries.isEnabled())
        for action in _main_writer_actions(app):
            assert not action.isEnabled(), action.text()
            assert action.toolTip() == library_busy_message("Image analysis")
    finally:
        lock.release()
    sandbox.qtbot.waitUntil(lambda: all(action.isEnabled() for action in _main_writer_actions(app)))


def test_grid_context_menu_disables_the_writers(sandbox, app, other_window):
    add_frames(sandbox, "M81", [3.0])
    search(sandbox, app, object_name="M81")
    panel = app.get_current_search_panel()
    view = panel.dataView
    view.selectRow(0)
    shown = []

    def read_and_close():
        """The real menu blocks in its own event loop, so read it from inside that loop."""
        menu = QApplication.activePopupWidget()
        try:
            shown.append({action.text(): (action.isEnabled(), action.toolTip()) for action in menu.actions()})
        finally:
            menu.close()

    QTimer.singleShot(0, read_and_close)
    panel.show_context_menu(view.visualRect(view.model().index(0, 0)).center())

    [items] = shown
    busy = library_busy_message(OTHER_WINDOW)
    for name in ("Compress files", "Plate Solve...", "Analyse Images..."):
        assert items[name] == (False, busy), name
    assert items["Mark as bad"][0]  # takes no lock
    assert items["Export files"][0]


def test_a_scan_is_refused_and_disabled_while_a_solve_runs(sandbox, app, solve):
    assert not app.action_Scan_Libraries.isEnabled()
    assert app.action_Scan_Libraries.toolTip() == library_busy_message("Plate solving")

    app.scan_libraries()  # reached some other way, say the tree's context menu before it caught up
    assert app.scan_worker is None
    assert app.statusBar().currentMessage() == (
        f"Library scan not started: {library_busy_message('Plate solving')}")

    solve.release.set()
    sandbox.wait_idle()
    assert app.action_Scan_Libraries.isEnabled()


def test_a_solve_is_disabled_and_refused_while_a_scan_runs(sandbox, app, boundary, monkeypatch):
    add_frames(sandbox, "M81", [3.0])
    search(sandbox, app, object_name="M81")
    panel = app.get_current_search_panel()
    lock = sandbox.context.library_write_lock
    assert lock.acquire(blocking=False, holder="Library scan")
    try:
        panel.plate_solve_files()
        [dialog] = panel.findChildren(PlateSolveDialog)
        assert not dialog.start_button.isEnabled()
        assert dialog.start_button.toolTip() == library_busy_message("Library scan")

        boundary.answer("warning", None)
        dialog._run_task(dialog.files)
        assert [(kind, args[2]) for kind, args in boundary.calls] == [
            ("warning", f"Plate solving not started: {library_busy_message('Library scan')}")]
        assert dialog._task is None
    finally:
        lock.release()
    sandbox.qtbot.waitUntil(dialog.start_button.isEnabled)
    dialog.close()


def test_the_viewer_plate_solve_button_follows_the_lock(sandbox, app):
    viewer = ImageViewerWindow(context=sandbox.context)
    sandbox.qtbot.addWidget(viewer)
    lock = sandbox.context.library_write_lock
    assert lock.acquire(blocking=False, holder="Compress Files")
    try:
        sandbox.qtbot.waitUntil(lambda: not viewer._plate_solve_btn.isEnabled())
        assert viewer._plate_solve_btn.toolTip() == library_busy_message("Compress Files")
    finally:
        lock.release()
    sandbox.qtbot.waitUntil(viewer._plate_solve_btn.isEnabled)


def test_analyse_images_holds_the_lock_and_scans_wait(sandbox, app, monkeypatch):
    add_frames(sandbox, "M81", [3.0])
    search(sandbox, app, object_name="M81")
    started, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return GOOD_RESULT

    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", held)
    app.get_current_search_panel().analyse_images()
    try:
        assert started.wait(10)
        sandbox.qtbot.waitUntil(lambda: not app.action_Scan_Libraries.isEnabled())
        assert app.action_Scan_Libraries.toolTip() == library_busy_message("Image analysis")
        app.scan_libraries()
        assert app.scan_worker is None
    finally:
        release.set()
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: lock_is_free(sandbox))
    assert app.action_Scan_Libraries.isEnabled()


def test_analyse_images_refuses_to_start_while_another_window_writes(sandbox, app, monkeypatch, other_window):
    add_frames(sandbox, "M81", [3.0])
    search(sandbox, app, object_name="M81")
    called = []
    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", lambda *a, **k: called.append(a))

    dialog = run_analyse_images(sandbox, app)

    assert f"Not started: {library_busy_message(OTHER_WINDOW)}" in dialog._log.toPlainText()
    assert called == []
    dialog.close()


@pytest.fixture
def roots_dialog(sandbox, app):
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        root = LibraryRoot.create(name="Main", path="/data/")
    dialog = LibraryRootDialog(sandbox.context)
    sandbox.qtbot.addWidget(dialog)
    dialog.libraryTable.selectRow(0)
    dialog.root = root
    return dialog


def _root_buttons(dialog):
    return [dialog.editButton, dialog.deleteButton, dialog.reindexButton]


def test_library_root_changes_are_disabled_and_refused_while_another_window_writes(
        sandbox, boundary, roots_dialog, other_window):
    dialog = roots_dialog
    dialog._refresh_library_busy()  # as when the dialog is activated
    for button in _root_buttons(dialog):
        assert not button.isEnabled(), button.text()
        assert button.toolTip() == library_busy_message(OTHER_WINDOW)

    # Each still asks the lock, in case it was reached before the buttons caught up.
    boundary.answer("warning", None)
    dialog.reindex_images()
    assert dialog.reindex_worker is None

    boundary.answer("question", QMessageBox.Yes)
    boundary.answer("warning", None)
    dialog.delete_library()

    edit = LibraryRootEditDialog(library_root=dialog.root, parent=dialog,
                                 write_lock=sandbox.context.library_write_lock)
    edit.pathLineEdit.setText(str(sandbox.data_dir))
    boundary.answer("warning", None)
    edit.accept()

    busy = library_busy_message(OTHER_WINDOW)
    assert [args[2] for kind, args in boundary.calls if kind == "warning"] == [
        f"Rebuild index not started: {busy}", f"Not deleted: {busy}", f"Not saved: {busy}"]
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        assert LibraryRoot.get_by_id(dialog.root.rowid).path == "/data/"

    other_window.release()
    dialog._refresh_library_busy()
    assert all(button.isEnabled() for button in _root_buttons(dialog))


def test_rebuild_index_holds_the_lock_until_it_ends(sandbox, roots_dialog):
    dialog = roots_dialog
    with sandbox.qtbot.waitSignal(sandbox.context.signal_bus.library_write_lock_changed):
        dialog.reindex_images()
    sandbox.wait_idle()
    assert dialog.reindex_worker is None
    assert lock_is_free(sandbox)
    assert all(button.isEnabled() for button in _root_buttons(dialog))


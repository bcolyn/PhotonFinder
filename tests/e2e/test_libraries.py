"""Library management: adding, editing and deleting library roots, and scanning them."""
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from photonfinder.models import CORE_MODELS, LibraryRoot, RootAndPath, SearchCriteria
from photonfinder.ui.LibraryRootDialog import LibraryRootDialog
from photonfinder.ui.LibraryRootEditDialog import LibraryRootEditDialog
from tests.e2e.helpers import YES, NO, add_libraries, fill_in, table_names, wait_for_scan, choose


def names_under(paths, root: Path) -> list[str]:
    return sorted(p.name for p in paths if root in p.parents)


def select_root(dialog: LibraryRootDialog, name: str):
    rows = [r for r in range(dialog.libraryTable.rowCount()) if dialog.libraryTable.item(r, 0).text() == name]
    assert rows, f"no library root named {name!r} in the dialog"
    dialog.libraryTable.selectRow(rows[0])


def tree_roots(panel) -> list[tuple[str, str]]:
    """(name, tooltip) of each library root under "All libraries" in the panel's tree."""
    model = panel.library_tree_model
    all_libraries = model.index(0, 0)
    assert all_libraries.data() == "All libraries"
    return [(model.index(r, 0, all_libraries).data(), model.index(r, 0, all_libraries).data(Qt.ItemDataRole.ToolTipRole))
            for r in range(model.rowCount(all_libraries))]


def test_add_library_by_browsing_and_scan_it(sandbox, app, modals, boundary, libraries):
    root = libraries.roots["rig"]
    boundary.answer("getExistingDirectory", root.as_posix())
    boundary.answer("question", YES)

    def browse(edit: LibraryRootEditDialog):
        edit.browseButton.click()
        assert edit.nameLineEdit.text() == "rig"  # taken from the folder name

    with modals.handle(LibraryRootDialog, lambda d: d.addButton.click()), \
            modals.handle(LibraryRootEditDialog, browse):
        app.action_Manage_Libraries.trigger()
    wait_for_scan(sandbox)

    assert [name for name, _ in tree_roots(app.get_current_search_panel())] == ["rig"]
    shown = sorted(sandbox.grid().column("File name"))
    assert shown == names_under(libraries.files, root)
    assert not set(shown) & {p.name for p in libraries.skipped}  # BAD_* and "bad clouds"


def test_type_and_filter_combos_narrow_the_grid(sandbox, app, modals, boundary, libraries):
    add_libraries(sandbox, modals, boundary, libraries.roots, scan=True)
    panel = app.get_current_search_panel()
    grid = sandbox.grid()
    assert grid.total == len(libraries.files)

    choose(sandbox, panel.filter_type_combo, "DARK")
    assert grid.row_count() == len(libraries.frames(image_type="DARK"))
    assert set(grid.column("Type")) == {"DARK"}

    choose(sandbox, panel.filter_type_combo, "LIGHT")
    choose(sandbox, panel.filter_filter_combo, "Ha")
    assert grid.row_count() == len(libraries.frames(image_type="LIGHT", filter="Ha"))
    assert {(r["Type"], r["Filter"]) for r in grid.rows()} == {("LIGHT", "Ha")}


def test_declined_scan_runs_later_from_the_menu(sandbox, app, modals, boundary, libraries):
    root = libraries.roots["simple"]
    add_libraries(sandbox, modals, boundary, {"simple": root}, scan=False)
    assert sandbox.grid().row_count() == 0

    app.action_Scan_Libraries.trigger()
    wait_for_scan(sandbox)

    assert sorted(sandbox.grid().column("File name")) == names_under(libraries.files, root)


def test_edit_library_renames_it_and_keeps_its_files(sandbox, app, modals, boundary, libraries):
    root = libraries.roots["simple"]
    add_libraries(sandbox, modals, boundary, {"simple": root}, scan=True)
    count = sandbox.grid().row_count()

    def edit_selected(dialog: LibraryRootDialog):
        select_root(dialog, "simple")
        dialog.editButton.click()
        assert table_names(dialog) == ["OSC archive"]

    def rename(edit: LibraryRootEditDialog):
        assert edit.nameLineEdit.text() == "simple"
        assert Path(edit.pathLineEdit.text()) == root
        fill_in("OSC archive", root, "One-shot-colour sessions")(edit)

    boundary.answer("question", NO)  # "Scan libraries now?"
    with modals.handle(LibraryRootDialog, edit_selected), modals.handle(LibraryRootEditDialog, rename):
        app.action_Manage_Libraries.trigger()
    sandbox.wait_idle()

    [(name, tooltip)] = tree_roots(app.get_current_search_panel())
    assert name == "OSC archive"
    assert "One-shot-colour sessions" in tooltip
    app.get_current_search_panel().update_search_criteria()
    sandbox.wait_idle()
    assert sandbox.grid().row_count() == count


def test_delete_library_removes_its_files(sandbox, app, modals, boundary, libraries):
    add_libraries(sandbox, modals, boundary,
                  {"simple": libraries.roots["simple"], "rig": libraries.roots["rig"]}, scan=True)

    def delete_simple(dialog: LibraryRootDialog):
        select_root(dialog, "simple")
        dialog.deleteButton.click()
        assert table_names(dialog) == ["rig"]

    boundary.answer("question", YES)  # "Are you sure you want to delete...?"
    boundary.answer("question", NO)  # "Scan libraries now?"
    with modals.handle(LibraryRootDialog, delete_simple):
        app.action_Manage_Libraries.trigger()
    sandbox.wait_idle()

    assert [name for name, _ in tree_roots(app.get_current_search_panel())] == ["rig"]
    app.get_current_search_panel().update_search_criteria()
    sandbox.wait_idle()
    assert sorted(sandbox.grid().column("File name")) == names_under(libraries.files, libraries.roots["rig"])


def _filter_on_folder(sandbox, app, root_name: str, path: str):
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        root = LibraryRoot.get(LibraryRoot.name == root_name)
    panel = app.get_current_search_panel()
    panel.apply_search_criteria(SearchCriteria(paths=[RootAndPath(root.rowid, root_name, path)]))
    sandbox.wait_idle()
    return panel


def _selected_tree_labels(panel) -> list[str]:
    return [i.data() for i in panel.filesystemTreeView.selectionModel().selectedIndexes()]


def test_closing_manage_libraries_keeps_the_folder_filter_and_its_selection(sandbox, app, modals, boundary,
                                                                           libraries):
    add_libraries(sandbox, modals, boundary,
                  {"simple": libraries.roots["simple"], "rig": libraries.roots["rig"]}, scan=True)
    folder = libraries.roots["simple"] / "2026-08-01"
    panel = _filter_on_folder(sandbox, app, "simple", "2026-08-01")
    assert sorted(sandbox.grid().column("File name")) == names_under(libraries.files, folder)
    assert _selected_tree_labels(panel) == ["2026-08-01"]

    with modals.handle(LibraryRootDialog, lambda dialog: dialog.reject()):
        app.action_Manage_Libraries.trigger()
    sandbox.wait_idle()

    # The reloaded tree shows the filter again, once that library's folders have loaded.
    sandbox.qtbot.waitUntil(lambda: _selected_tree_labels(panel) == ["2026-08-01"], timeout=5000)
    assert [p.path for p in panel.search_criteria.paths] == ["2026-08-01"]
    assert sorted(sandbox.grid().column("File name")) == names_under(libraries.files, folder)


def test_deleting_the_filtered_library_drops_its_folder_filter(sandbox, app, modals, boundary, libraries):
    add_libraries(sandbox, modals, boundary,
                  {"simple": libraries.roots["simple"], "rig": libraries.roots["rig"]}, scan=True)
    panel = _filter_on_folder(sandbox, app, "simple", "2026-08-01")
    assert [p.path for p in panel.search_criteria.paths] == ["2026-08-01"]
    assert sorted(sandbox.grid().column("File name")) == names_under(
        libraries.files, libraries.roots["simple"] / "2026-08-01")

    def delete_simple(dialog: LibraryRootDialog):
        select_root(dialog, "simple")
        dialog.deleteButton.click()

    boundary.answer("question", YES)  # "Are you sure you want to delete...?"
    boundary.answer("question", NO)  # "Scan libraries now?"
    with modals.handle(LibraryRootDialog, delete_simple):
        app.action_Manage_Libraries.trigger()
    sandbox.wait_idle()

    assert panel.search_criteria.paths == []
    assert sorted(sandbox.grid().column("File name")) == names_under(libraries.files, libraries.roots["rig"])


def test_a_reload_without_the_filtered_library_drops_its_folder_filter(sandbox, app, modals, boundary, libraries):
    """The same, without the dialog: whatever reloads the tree, a path into a library root that
    is gone is dropped and the search runs again."""
    add_libraries(sandbox, modals, boundary,
                  {"simple": libraries.roots["simple"], "rig": libraries.roots["rig"]}, scan=True)
    panel = _filter_on_folder(sandbox, app, "simple", "2026-08-01")
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        LibraryRoot.get(LibraryRoot.name == "simple").delete_instance(recursive=True)

    app.reload_library_roots_in_all_panels()
    sandbox.wait_idle()

    assert panel.search_criteria.paths == []
    assert sorted(sandbox.grid().column("File name")) == names_under(libraries.files, libraries.roots["rig"])


def test_duplicate_path_is_refused(sandbox, app, modals, boundary, libraries):
    root = libraries.roots["simple"]
    add_libraries(sandbox, modals, boundary, {"simple": root}, scan=False)

    def try_duplicate(edit: LibraryRootEditDialog):
        fill_in("again", root)(edit)
        edit.accept()
        assert edit.isVisible()
        edit.reject()

    boundary.answer("warning", QMessageBox.StandardButton.Ok)
    with modals.handle(LibraryRootDialog, lambda d: d.addButton.click()), \
            modals.handle(LibraryRootEditDialog, try_duplicate):
        app.action_Manage_Libraries.trigger()  # nothing changed: no scan question
    sandbox.wait_idle()

    [(kind, args)] = [c for c in boundary.calls if c[0] == "warning"]
    assert "already exists" in args[2]
    assert [name for name, _ in tree_roots(app.get_current_search_panel())] == ["simple"]


def test_rescan_picks_up_added_removed_and_bad_marked_files(sandbox, app, modals, boundary, libraries, tmp_path):
    root = tmp_path / "library"
    shutil.copytree(libraries.roots["simple"], root)  # the shared libraries must stay untouched
    add_libraries(sandbox, modals, boundary, {"simple": root}, scan=True)
    before = set(sandbox.grid().column("File name"))

    m27 = sorted((root / "2026-08-01" / "M 27").iterdir())
    m27[0].unlink()
    m27[1].rename(m27[1].with_name("BAD_" + m27[1].name))
    shutil.copy(m27[2], root / "2026-08-05" / m27[2].name)

    app.action_Scan_Libraries.trigger()
    wait_for_scan(sandbox)

    # the copy has the same name as its original, in another folder
    expected = sorted([*(before - {m27[0].name, m27[1].name}), m27[2].name])
    assert sorted(sandbox.grid().column("File name")) == expected


def test_scan_does_not_start_while_compression_holds_the_library(sandbox, app, modals, boundary, libraries):
    """Ticket 28: one library writer at a time. Another window compressing this library holds
    its write lock, so a scan is refused until it is released."""
    from photonfinder.core import DatabaseLock
    root = libraries.roots["simple"]
    add_libraries(sandbox, modals, boundary, {"simple": root}, scan=False)

    other_window = DatabaseLock(sandbox.database_path, ".write.lock")
    assert other_window.acquire(blocking=False)
    try:
        app.action_Scan_Libraries.trigger()
        assert app.scan_worker is None
        sandbox.wait_idle()
        assert sandbox.grid().row_count() == 0
    finally:
        other_window.release()

    app.action_Scan_Libraries.trigger()
    wait_for_scan(sandbox)
    assert sorted(sandbox.grid().column("File name")) == names_under(libraries.files, root)
    # the finished scan released the lock
    assert other_window.acquire(blocking=False)
    other_window.release()

"""Library management: adding, editing and deleting library roots, and scanning them."""
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from photonfinder.ui.LibraryRootDialog import LibraryRootDialog
from photonfinder.ui.LibraryRootEditDialog import LibraryRootEditDialog

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


def names_under(paths, root: Path) -> list[str]:
    return sorted(p.name for p in paths if root in p.parents)


def fill_in(name: str, path: Path, description: str = ""):
    def fill(edit: LibraryRootEditDialog):
        edit.nameLineEdit.setText(name)
        edit.pathLineEdit.setText(str(path))
        edit.descriptionTextEdit.setPlainText(description)
    return fill


def select_root(dialog: LibraryRootDialog, name: str):
    rows = [r for r in range(dialog.libraryTable.rowCount()) if dialog.libraryTable.item(r, 0).text() == name]
    assert rows, f"no library root named {name!r} in the dialog"
    dialog.libraryTable.selectRow(rows[0])


def table_names(dialog: LibraryRootDialog) -> list[str]:
    return [dialog.libraryTable.item(r, 0).text() for r in range(dialog.libraryTable.rowCount())]


def add_libraries(sandbox, modals, boundary, roots: dict[str, Path], scan: bool):
    """Add ``roots`` in one Manage Libraries session, then answer the scan question."""
    boundary.answer("question", YES if scan else NO)
    edits = [modals.handle(LibraryRootEditDialog, fill_in(name, path)) for name, path in roots.items()]

    def add_all(dialog: LibraryRootDialog):
        for _ in roots:
            dialog.addButton.click()
        assert table_names(dialog) == sorted(roots)

    with modals.handle(LibraryRootDialog, add_all):
        for edit in edits:
            edit.__enter__()
        try:
            sandbox.window.action_Manage_Libraries.trigger()
        finally:
            for edit in reversed(edits):
                edit.__exit__(None, None, None)
    wait_for_scan(sandbox)


def wait_for_scan(sandbox):
    sandbox.qtbot.waitUntil(lambda: sandbox.window.scan_worker is None, timeout=30_000)
    sandbox.wait_idle()  # the search started when the scan finished


def tree_roots(panel) -> list[tuple[str, str]]:
    """(name, tooltip) of each library root under "All libraries" in the panel's tree."""
    model = panel.library_tree_model
    all_libraries = model.index(0, 0)
    assert all_libraries.data() == "All libraries"
    return [(model.index(r, 0, all_libraries).data(), model.index(r, 0, all_libraries).data(Qt.ItemDataRole.ToolTipRole))
            for r in range(model.rowCount(all_libraries))]


def choose(sandbox, combo, text: str):
    index = combo.findText(text)
    assert index >= 0, f"{text!r} not offered in {combo.objectName()}: " \
                       f"{[combo.itemText(i) for i in range(combo.count())]}"
    combo.setCurrentIndex(index)
    sandbox.wait_idle()


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

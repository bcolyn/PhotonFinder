"""The e2e helpers themselves: modal handling, the hang watchdog and reading the grid."""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from photonfinder.ui.LibraryRootDialog import LibraryRootDialog
from photonfinder.ui.LibraryRootEditDialog import LibraryRootEditDialog
from tests.e2e.helpers import Modals


class Outer(QDialog):
    pass


class Inner(QDialog):
    pass


@pytest.fixture
def quick_modals(qtbot):
    m = Modals(qtbot, timeout=0.3)
    yield m
    m.stop()


def test_handle_runs_fn_on_nested_dialogs_and_accepts_them(qtbot, quick_modals):
    seen, results = [], []

    def open_inner(outer):
        seen.append(outer)
        results.append(Inner(outer).exec())

    with quick_modals.handle(Outer, open_inner), quick_modals.handle(Inner, seen.append):
        results.append(Outer().exec())

    assert [type(d) for d in seen] == [Outer, Inner]
    assert results == [QDialog.DialogCode.Accepted, QDialog.DialogCode.Accepted]
    assert quick_modals.unhandled == []


def test_handle_reraises_and_rejects_when_fn_fails(qtbot, quick_modals):
    def fail(dialog):
        raise ValueError("boom")

    results = []
    with pytest.raises(ValueError, match="boom"):
        with quick_modals.handle(Outer, fail):
            results.append(Outer().exec())
    assert results == [QDialog.DialogCode.Rejected]


def test_handle_fails_when_no_dialog_opens(qtbot, quick_modals):
    with pytest.raises(AssertionError, match="No Outer was opened"):
        with quick_modals.handle(Outer):
            pass


def test_watchdog_closes_an_unhandled_modal(qtbot, quick_modals):
    box = QMessageBox(QMessageBox.Icon.Question, "Surprise", "Nobody expects this")
    box.exec()  # would block forever without the watchdog

    assert quick_modals.unhandled == ["QMessageBox 'Surprise'"]


def test_grid_reads_every_page_of_a_scanned_library(sandbox, app, boundary, modals, libraries, qtbot):
    root = libraries.roots["simple"]
    expected = sorted(p.name for p in libraries.files if root in p.parents)

    def fill_in(edit: LibraryRootEditDialog):
        edit.nameLineEdit.setText("simple")
        edit.pathLineEdit.setText(str(root))

    boundary.answer("question", QMessageBox.StandardButton.Yes)  # "Scan libraries now?"
    with modals.handle(LibraryRootDialog, lambda d: d.addButton.click()), \
            modals.handle(LibraryRootEditDialog, fill_in):
        app.action_Manage_Libraries.trigger()
    qtbot.waitUntil(lambda: app.scan_worker is None, timeout=30_000)
    sandbox.wait_idle()  # the search started when the scan finished

    panel = app.get_current_search_panel()
    # Small pages in a short window: the panel only fetches more rows as the view scrolls.
    app.resize(app.width(), 400)
    panel.search_results_loader.page_size = 5
    panel.update_search_criteria()
    sandbox.wait_idle()

    grid = sandbox.grid()
    assert grid.model.rowCount() < len(expected)  # the rest is fetched on scroll
    assert grid.total == len(expected)
    assert sorted(grid.column("File name")) == expected
    assert grid.row_count() == len(expected)
    assert {r["Type"] for r in grid.rows()} == {"LIGHT", "FLAT"}

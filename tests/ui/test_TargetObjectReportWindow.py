"""Target report window: sorted reloads keep rows intact, only the latest load shows, errors show."""
import threading

import pytest
from PySide6.QtCore import QObject, Qt, Signal, QThreadPool
from PySide6.QtWidgets import QWidget

from photonfinder import reports
from photonfinder.models import SearchCriteria
from photonfinder.ui.TargetObjectReportWindow import TargetObjectReportWindow, TargetReportLoader


class _MainWindow(QObject):
    tabs_changed = Signal(list)


class _Panel(QWidget):
    """The bits of SearchPanel the report window reads."""
    search_criteria_changed = Signal()

    def __init__(self):
        super().__init__()
        self.mainWindow = _MainWindow()
        self.title = "tab"
        self.search_criteria = SearchCriteria()


def _rows(*names):
    return [(n, f"{n}-filter", f"{n}-scope", f"{n}-cam", float(i), f"{n}-date", f"{n}-path")
            for i, n in enumerate(names)]


@pytest.fixture
def window(qtbot, context, monkeypatch):
    monkeypatch.setattr(reports, "target_report", lambda criteria: [])
    panel = _Panel()
    qtbot.addWidget(panel)
    win = TargetObjectReportWindow(context, parent=panel)
    yield win
    QThreadPool.globalInstance().waitForDone(5000)


def _table_rows(win):
    t = win.tableWidget
    return [tuple(t.item(r, c).text() if t.item(r, c) else None for c in range(t.columnCount()))
            for r in range(t.rowCount())]


def test_a_reload_while_sorted_keeps_each_rows_cells_together(window):
    window.on_complete(_rows("b", "a", "c"))
    window.tableWidget.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    window.on_complete(_rows("z", "y", "x"))

    for row in _table_rows(window):
        name = row[0]
        assert row[1:4] == (f"{name}-filter", f"{name}-scope", f"{name}-cam"), row
    assert [r[0] for r in _table_rows(window)] == ["x", "y", "z"]  # still sorted
    assert window.tableWidget.isSortingEnabled()


def test_only_the_latest_load_is_shown(qtbot, context, monkeypatch):
    release_old = threading.Event()

    def target_report(criteria):
        if criteria.object_name == "old":
            assert release_old.wait(5)
        return [reports.TargetReportRow(criteria.object_name, "", "", "", 1.0, "", [], 1)]

    monkeypatch.setattr(reports, "target_report", target_report)
    loader = TargetReportLoader(context)
    received = []
    loader.on_result.connect(received.append)

    criteria = SearchCriteria(object_name="old")
    loader.start(None, criteria)
    criteria.object_name = "new"  # the panel edits its criteria in place
    loader.start(None, criteria)
    qtbot.waitUntil(lambda: bool(received), timeout=5000)
    release_old.set()
    QThreadPool.globalInstance().waitForDone(5000)
    qtbot.wait(50)

    assert [rows[0][0] for rows in received] == ["new"]


def test_a_query_error_is_shown_and_clears_the_table(qtbot, window, monkeypatch):
    window.on_complete(_rows("a"))

    def broken(criteria):
        raise RuntimeError("no such column")

    monkeypatch.setattr(reports, "target_report", broken)
    window.load_report()
    qtbot.waitUntil(lambda: "no such column" in window.statusbar.currentMessage(), timeout=5000)

    assert window.tableWidget.rowCount() == 0

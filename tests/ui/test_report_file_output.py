"""Metadata report and file list: correct files on Windows, visible failures, one export at a time."""
import csv
import threading

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QMessageBox

from photonfinder.core import ApplicationContext, StatusReporter
from photonfinder.models import Image, SearchCriteria
from photonfinder.ui.BackgroundLoader import FileListTask
from photonfinder.ui.MetadataReportDialog import MetadataReportDialog, MetadataReportTask
from tests.conftest import McpSettings


@pytest.fixture
def context(tmp_path):
    """A library on disk: the tasks query from worker threads, which an in-memory one can't serve."""
    ctx = ApplicationContext(str(tmp_path / "library.db"), McpSettings())
    ctx.set_status_reporter(StatusReporter())
    with ctx:
        yield ctx


@pytest.fixture(autouse=True)
def message_boxes(monkeypatch):
    """Record message boxes instead of showing them: a real one would block the test run."""
    shown = []
    for kind in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, kind,
                            lambda parent, title, text, *a, _kind=kind, **k: shown.append((_kind, text)))
    return shown


def _run(qtbot, task, *args):
    finished, errors = [], []
    task.finished.connect(lambda: finished.append(True))
    task.error.connect(errors.append)
    task.start(*args)
    qtbot.waitUntil(lambda: bool(finished or errors), timeout=5000)
    QThreadPool.globalInstance().waitForDone(5000)
    return finished, errors


FIELDS = [("File.name", "photonfinder"), ("Image.object_name", "photonfinder"), ("Image.set_temp", "photonfinder")]


@pytest.mark.parametrize("export_format, dialect", [("csv", csv.excel), ("tsv", csv.excel_tab)])
def test_a_metadata_report_round_trips_with_one_line_per_row(qtbot, sample, tmp_path, export_format, dialect):
    _, d = sample
    Image.update(object_name="M31 – 10° field").where(Image.file == d["light"]).execute()
    out = tmp_path / f"report.{export_format}"

    finished, errors = _run(qtbot, MetadataReportTask(sample[0], SearchCriteria(), None),
                            str(out), FIELDS, export_format)

    assert finished and not errors
    raw = out.read_bytes()
    assert b"\r\r\n" not in raw
    assert raw.count(b"\r\n") == 3  # header + 2 files, no blank lines
    with open(out, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, dialect=dialect))
    assert rows[0] == ["File.name", "Image.object_name", "Image.set_temp"]
    assert ["m31.fits", "M31 – 10° field", "-10.0"] in rows


def test_a_file_list_holds_one_path_per_line(qtbot, sample, tmp_path):
    out = tmp_path / "files.txt"

    finished, errors = _run(qtbot, FileListTask(sample[0], SearchCriteria(), None), str(out))

    assert finished and not errors
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(line.endswith(".fits") for line in lines)


@pytest.mark.parametrize("make_task", [
    lambda ctx, path: (FileListTask(ctx, SearchCriteria(), None), (path,)),
    lambda ctx, path: (MetadataReportTask(ctx, SearchCriteria(), None), (path, FIELDS, "csv")),
], ids=["file list", "metadata report"])
def test_an_unwritable_output_path_is_reported(qtbot, sample, tmp_path, make_task):
    path = str(tmp_path / "missing-dir" / "out.txt")
    task, args = make_task(sample[0], path)

    finished, errors = _run(qtbot, task, *args)

    assert not finished
    assert len(errors) == 1 and "missing-dir" in errors[0]


def test_ok_stays_disabled_while_an_export_runs(qtbot, sample, tmp_path, monkeypatch):
    context, _ = sample
    out = tmp_path / "report.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    release = threading.Event()
    real = MetadataReportTask._process_file

    def slow(self, file, index):
        assert release.wait(5)
        real(self, file, index)

    monkeypatch.setattr(MetadataReportTask, "_process_file", slow)
    dialog = MetadataReportDialog(context, SearchCriteria(), None)
    qtbot.addWidget(dialog)
    dialog._add_photonfinder_item()
    ok = dialog.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled()
    started = []
    monkeypatch.setattr(MetadataReportTask, "start",
                        lambda self, *a: (started.append(self), real_start(self, *a)))

    dialog.start_export()
    assert not ok.isEnabled()
    dialog.selectedItemsListWidget.setCurrentRow(0)  # selection changes re-evaluate the buttons
    assert not ok.isEnabled()
    dialog.start_export()  # a second click can't start another export
    release.set()
    QThreadPool.globalInstance().waitForDone(5000)

    assert len(started) == 1


real_start = MetadataReportTask.start

"""The Image Analysis progress bar, on a real analysis of generated files (ticket 35)."""
import shutil
import threading

import pytest
from PySide6.QtCore import Qt

from photonfinder.ui import BackgroundLoader
from photonfinder.ui.ImageAnalysisDialog import ImageAnalysisDialog
from tests.e2e.helpers import add_libraries


@pytest.fixture
def library(sandbox, app, modals, boundary, libraries, tmp_path):
    """A copy of the simple library, scanned, so a test may damage a file."""
    root = tmp_path / "library"
    shutil.copytree(libraries.roots["simple"], root)
    add_libraries(sandbox, modals, boundary, {"simple": root}, scan=True)
    return root


def _analyse_all(sandbox, app, until=None):
    """Start an analysis of every file in the grid; record what the bar shows."""
    panel = app.get_current_search_panel()
    panel.analyse_images()
    [dialog] = panel.findChildren(ImageAnalysisDialog)
    values = []
    dialog._progress.valueChanged.connect(values.append)
    if until is not None:
        until(dialog)
    sandbox.qtbot.waitUntil(lambda: dialog._task is None, timeout=60_000)
    sandbox.wait_idle()
    return dialog, values


def test_the_bar_is_full_when_the_analysis_finishes(sandbox, app, library):
    total = sandbox.grid().total

    dialog, values = _analyse_all(sandbox, app)

    assert dialog._progress.maximum() == total
    assert dialog._progress.value() == total
    assert values == list(range(1, total + 1))  # one step per finished file
    dialog.close()


def test_a_run_with_unreadable_files_still_ends_full(sandbox, app, library):
    total = sandbox.grid().total
    damaged = sorted(p for p in library.rglob("*.fits") if p.is_file())[:2]
    for path in damaged:
        path.write_bytes(b"not a FITS file")

    dialog, values = _analyse_all(sandbox, app)

    log = dialog._log.toPlainText()
    assert all(path.name in log for path in damaged), log
    assert dialog._progress.value() == dialog._progress.maximum() == total
    dialog.close()


def test_a_cancelled_run_does_not_claim_to_be_complete(sandbox, app, library, monkeypatch):
    total = sandbox.grid().total
    assert total > 2
    real = BackgroundLoader.analyze_file
    calls, at_second, release = [], threading.Event(), threading.Event()

    def analyse(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            at_second.set()
            assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(BackgroundLoader, "analyze_file", analyse)

    def cancel_on_the_second_file(dialog):
        assert at_second.wait(30)
        sandbox.qapp.processEvents()
        assert dialog._progress.value() == 1  # the first is done, the second is still running
        sandbox.qtbot.mouseClick(dialog._btn, Qt.MouseButton.LeftButton)
        release.set()

    _, values = _analyse_all(sandbox, app, until=cancel_on_the_second_file)

    assert len(calls) == 2
    assert max(values) < total

"""Cancelling a bulk solve: the grid shows the solutions found so far, including the one that
was still running when the dialog closed."""
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from photonfinder.platesolver import SolverCancelled
from photonfinder.ui.PlateSolveDialog import PlateSolveDialog
from tests.e2e.test_diagnose_quality import add_frames, search
from tests.test_solve_service import FakeSolver


def _cancel_button(dialog):
    dialog.close_button.click()


def _escape(dialog, qtbot):
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)


class SolveInProgress:
    """A bulk solve from the search grid, with its second file held in the solver."""

    def __init__(self, sandbox, app, monkeypatch):
        add_frames(sandbox, "M81", [3.0] * 4)
        search(sandbox, app, object_name="M81")
        self.reached_second = threading.Event()
        self.release = threading.Event()
        self.solver = FakeSolver(on_solve=self._hold_the_second_file)
        monkeypatch.setattr(PlateSolveDialog, "_build_solver", lambda dialog, index: self.solver)
        self.panel = app.get_current_search_panel()
        self.panel.plate_solve_files()
        [self.dialog] = self.panel.findChildren(PlateSolveDialog)
        self.dialog.start_button.click()
        assert self.reached_second.wait(10)

    def _hold_the_second_file(self):
        if len(self.solver.solved) == 2:
            self.reached_second.set()
            assert self.release.wait(20)


@pytest.fixture
def solve(sandbox, app, monkeypatch):
    in_progress = SolveInProgress(sandbox, app, monkeypatch)
    yield in_progress
    in_progress.release.set()
    sandbox.wait_idle()


def _solved_column(sandbox) -> list[str]:
    grid = sandbox.grid()
    by_name = dict(zip(grid.column("File name"), grid.column("Solved")))
    return [by_name[name] for name in sorted(by_name)]


@pytest.mark.parametrize("cancel", [
    lambda dialog, qtbot: _cancel_button(dialog),
    _escape,
], ids=["cancel_button", "escape_key"])
def test_cancelling_updates_the_grid_with_the_solutions_found(sandbox, solve, qtbot, cancel):
    context = sandbox.context
    first_row_solved = lambda: _solved_column(sandbox)[0] == "True"
    qtbot.waitUntil(lambda: solve.dialog.progressBar.maximum() == 4)

    cancel(solve.dialog, qtbot)

    # At once, as the file still in the solver can take minutes.
    assert not solve.dialog.isVisible()
    qtbot.waitUntil(first_row_solved, timeout=2000)
    assert _solved_column(sandbox)[1:] == ["False", "False", "False"]

    # The solve is still running, so nothing else may start one until it has stopped.
    assert not context.library_write_lock.acquire(blocking=False)

    destroyed = []
    solve.dialog.destroyed.connect(lambda: destroyed.append(True))
    solve.release.set()
    sandbox.wait_idle()

    # The file that was running got stored, so the grid shows it too. The rest never started.
    qtbot.waitUntil(lambda: bool(destroyed), timeout=5000)
    assert _solved_column(sandbox) == ["True", "True", "False", "False"]
    assert len(solve.solver.solved) == 2
    assert context.writers.running() == []
    assert context.library_write_lock.acquire(blocking=False)
    context.library_write_lock.release()
    assert solve.panel.findChildren(PlateSolveDialog) == []
    assert QApplication.activeModalWidget() is None


def test_a_solve_that_ends_normally_still_shows_its_results(sandbox, solve, qtbot):
    solve.release.set()
    sandbox.wait_idle()

    qtbot.waitUntil(lambda: solve.dialog.close_button.text() == "Close", timeout=5000)
    assert solve.dialog.isVisible()
    assert _solved_column(sandbox) == ["True"] * 4
    solve.dialog.close()


class StopsWhenCancelled(FakeSolver):
    """Solves the first file. On the second it runs until cancelled, then stops, as the real
    solvers now do."""

    def __init__(self):
        super().__init__()
        self.reached_second = threading.Event()

    def solve(self, image_path, image=None, hint=None, output_callback=None, file_wcs=None, cancelled=None):
        if not self.solved:
            return super().solve(image_path, image, hint, output_callback, file_wcs, cancelled)
        self.solved.append(image_path.name)
        self.reached_second.set()
        deadline = time.monotonic() + 20
        while not cancelled():
            assert time.monotonic() < deadline, "never cancelled"
            time.sleep(0.05)
        raise SolverCancelled("stopped")


def test_cancelling_stops_the_running_solver_and_frees_the_library(sandbox, app, qtbot, monkeypatch):
    add_frames(sandbox, "M81", [3.0] * 4)
    search(sandbox, app, object_name="M81")
    solver = StopsWhenCancelled()
    monkeypatch.setattr(PlateSolveDialog, "_build_solver", lambda dialog, index: solver)
    panel = app.get_current_search_panel()
    panel.plate_solve_files()
    [dialog] = panel.findChildren(PlateSolveDialog)
    destroyed = []
    dialog.destroyed.connect(lambda: destroyed.append(True))
    dialog.start_button.click()
    assert solver.reached_second.wait(10)

    dialog.close_button.click()

    # Nothing is released by hand here: the solver stopping is what ends the task.
    qtbot.waitUntil(lambda: bool(destroyed), timeout=5000)
    sandbox.wait_idle()
    assert _solved_column(sandbox) == ["True", "False", "False", "False"]
    assert len(solver.solved) == 2
    assert sandbox.context.writers.running() == []
    assert sandbox.context.library_write_lock.acquire(blocking=False)
    sandbox.context.library_write_lock.release()

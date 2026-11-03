"""Storing a plate solution is all-or-nothing. The solver is the only stand-in here: the WCS
handling and the database are real."""
import logging
import threading
import sqlite3

import pytest
from peewee import JOIN

from photonfinder import solve_service
from photonfinder.core import ApplicationContext, StatusReporter, decompress
from photonfinder.models import File, FileWCS, Image, LibraryRoot, SearchCriteria
from photonfinder.platesolver import SolverBase, SolverCancelled, SolverFailure, SolverType
from photonfinder.ui.BackgroundLoader import PlateSolveTask
from tests.conftest import McpSettings, fixed_solution_header


class FakeSolver(SolverBase):
    """Records each run. ``on_solve`` runs during it; ``fails`` makes every run unsolved."""

    def __init__(self, solution=None, on_solve=None, fails=False):
        super().__init__()
        self.solution = solution if solution is not None else fixed_solution_header()
        self.on_solve = on_solve
        self.fails = fails
        self.solved = []
        self.hints = []

    @property
    def solver_type(self):
        return SolverType.ASTAP

    def solve(self, image_path, image=None, hint=None, output_callback=None, file_wcs=None, cancelled=None):
        self.solved.append(image_path.name)
        self.hints.append(hint)
        if self.on_solve:
            self.on_solve()
        if self.fails:
            raise SolverFailure("no match", None)
        return self.solution.copy()


def _load(file):
    return (File.select(File, Image).join_from(File, Image).where(File.rowid == file.rowid)).get()


def _unusable_solution():
    """A solution the solver accepted, but with no usable WCS: the centre can't be computed."""
    solution = fixed_solution_header()
    del solution["CTYPE1"]
    return solution


def test_a_solution_whose_centre_cannot_be_computed_leaves_no_row(sample):
    _, objs = sample

    outcome = solve_service.solve_file(_load(objs["dark"]), FakeSolver(_unusable_solution()))

    assert not outcome.success
    assert not FileWCS.select().where(FileWCS.file == objs["dark"]).exists()


def test_a_failed_resolve_keeps_the_previous_solution(sample):
    _, objs = sample
    before = FileWCS.get(FileWCS.file == objs["light"]).wcs
    coords_before = Image.get(Image.file == objs["light"]).coord_ra

    outcome = solve_service.solve_file(_load(objs["light"]), FakeSolver(_unusable_solution()))

    assert not outcome.success
    assert FileWCS.get(FileWCS.file == objs["light"]).wcs == before
    assert Image.get(Image.file == objs["light"]).coord_ra == coords_before


def test_a_failure_writing_the_image_rolls_back_the_solution(sample):
    context, objs = sample
    context.database.execute_sql(
        "create trigger no_image_update before update on image begin select raise(abort, 'disk says no'); end")

    outcome = solve_service.solve_file(_load(objs["dark"]), FakeSolver())

    assert not outcome.success
    assert "disk says no" in outcome.error
    assert not FileWCS.select().where(FileWCS.file == objs["dark"]).exists()


def test_a_solution_stores_the_wcs_and_the_coordinates_together(sample):
    _, objs = sample
    file = _load(objs["dark"])

    outcome = solve_service.solve_file(file, FakeSolver())

    assert outcome.success and outcome.warning is None
    assert b"CRVAL1" in decompress(FileWCS.get(FileWCS.file == objs["dark"]).wcs)
    stored = Image.get(Image.file == objs["dark"])
    assert stored.coord_ra == pytest.approx(10.68, abs=0.01)
    assert stored.coord_pix256 is not None
    assert (stored.width, stored.height) == (200, 200)
    assert file.image.coord_ra == stored.coord_ra


def test_a_file_without_an_image_row_keeps_its_solution_and_says_so(sample, caplog):
    _, objs = sample
    bare = File.create(root=objs["root"], path="lights/", name="bare.fits", size=1, mtime_millis=1)
    file = (File.select(File, Image).join_from(File, Image, JOIN.LEFT_OUTER)
            .where(File.rowid == bare.rowid)).get()
    lines = []

    with caplog.at_level(logging.WARNING):
        outcome = solve_service.solve_file(file, FakeSolver(), output_callback=lines.append)

    assert outcome.success
    assert FileWCS.select().where(FileWCS.file == bare).exists()
    assert "bare.fits" in outcome.warning
    assert lines == [f"  ! {outcome.warning}"]
    assert any(r.levelname == "WARNING" and "bare.fits" in r.getMessage() for r in caplog.records)


def test_solve_unsolved_survives_a_write_from_another_connection(tmp_path, qapp):
    """A query iterated lazily holds a read snapshot open, and once anything else commits, each
    write made from behind it fails at once with "database is locked"."""
    context = ApplicationContext(str(tmp_path / "library.db"), McpSettings())
    context.set_status_reporter(StatusReporter())
    with context:
        root = LibraryRoot.create(name="Main", path="/data/")
        names = [f"f{i:03}.fits" for i in range(20)]
        for name in names:
            Image.create(file=File.create(root=root, path="batch/", name=name, size=1, mtime_millis=1),
                         image_type="LIGHT")

        def another_connection_commits():
            if len(solver.solved) == 3:
                other = sqlite3.connect(context.database_path, timeout=1)
                other.execute("insert into project(name) values ('edited elsewhere')")
                other.commit()
                other.close()

        solver = FakeSolver(on_solve=another_connection_commits)
        task = PlateSolveTask(context, SearchCriteria(), [], solver=solver)

        task._process_files()

        assert [error for _, _, error in task.file_results if error] == []
        assert solver.solved == names
        assert task.total == len(names)
        assert FileWCS.select().count() == len(names)


class StoppedSolver(FakeSolver):
    """A solver whose run was stopped by the cancel, as the real ones raise it."""

    def solve(self, image_path, image=None, hint=None, output_callback=None, file_wcs=None, cancelled=None):
        self.solved.append(image_path.name)
        raise SolverCancelled("stopped")


def test_cancelling_while_the_primary_fails_skips_the_backup(sample):
    _, objs = sample
    cancel = threading.Event()
    primary = FakeSolver(on_solve=cancel.set, fails=True)
    backup = FakeSolver()

    outcome = solve_service.solve_file(_load(objs["dark"]), primary, backup, cancelled=cancel.is_set)

    assert (outcome.success, outcome.cancelled, outcome.error) == (False, True, None)
    assert backup.solved == []
    assert not FileWCS.select().where(FileWCS.file == objs["dark"]).exists()


def test_without_a_cancel_a_failing_primary_still_falls_back(sample):
    _, objs = sample
    backup = FakeSolver()

    outcome = solve_service.solve_file(_load(objs["dark"]), FakeSolver(fails=True), backup,
                                       cancelled=lambda: False)

    assert outcome.success and not outcome.cancelled
    assert backup.solved == ["dark.fits"]


def test_a_stopped_solver_is_cancelled_not_failed(sample):
    _, objs = sample
    backup = FakeSolver()

    outcome = solve_service.solve_file(_load(objs["dark"]), StoppedSolver(), backup, cancelled=lambda: True)

    assert (outcome.success, outcome.cancelled, outcome.error) == (False, True, None)
    assert backup.solved == []


def test_nothing_runs_once_cancelled(sample):
    _, objs = sample
    primary = FakeSolver()

    outcome = solve_service.solve_file(_load(objs["dark"]), primary, cancelled=lambda: True)

    assert outcome.cancelled
    assert primary.solved == []


class TestCameraScaleRetries:
    @pytest.fixture
    def light(self, sample):
        _, objs = sample
        FileWCS.delete().execute()
        return _load(objs["light"])

    def _task(self, context, light, solver):
        return PlateSolveTask(context, SearchCriteria(), [light], solver=solver,
                              camera_scale_cache={"ASI2600": [1.0, 2.0]}, use_camera_scales=True)

    def test_every_cached_scale_is_tried_when_not_cancelled(self, context, light):
        solver = FakeSolver(fails=True)
        task = self._task(context, light, solver)

        task._process_files()

        assert [h.scale if h else None for h in solver.hints] == [None, 1.0, 2.0]
        assert len(task.file_results) == 1 and task.file_results[0][1] is False

    def test_a_cancel_stops_the_next_cached_scale(self, context, light):
        solver = FakeSolver(fails=True)
        task = self._task(context, light, solver)
        solver.on_solve = task.cancel

        task._process_files()

        assert len(solver.solved) == 1
        assert task.file_results == []  # cancelled, not reported as failed
        assert task.solved_files == []

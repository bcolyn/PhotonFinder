"""Plate solving one frame per group (solve_service.SolvePlan): the earliest unsolved frame of each
group, groups with a solved frame skipped, a failed group moving on to its next frame up to three,
through the dialog's task and the MCP tool. The solver is the only stand-in."""
from datetime import datetime, timedelta

import pytest

from photonfinder import mcp_server, solve_service, solvers
from photonfinder.core import ApplicationContext, StatusReporter, compress
from photonfinder.library_query import unsolved_lights
from photonfinder.models import File, FileWCS, Image, LibraryRoot, SearchCriteria
from photonfinder.ui.BackgroundLoader import PlateSolveTask
from tests.conftest import McpSettings
from tests.test_solve_service import FakeSolver

EVENING = datetime(2026, 3, 5, 21, 0)  # local time


class FailsFirst(FakeSolver):
    """Fails the first `failures` frames it is given, then solves."""

    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def solve(self, image_path, image=None, hint=None, output_callback=None, file_wcs=None, cancelled=None):
        self.fails = len(self.solved) < self.failures
        return super().solve(image_path, image, hint, output_callback, file_wcs, cancelled)


@pytest.fixture
def context(tmp_path):
    settings = McpSettings()
    ctx = ApplicationContext(str(tmp_path / "library.db"), settings)
    ctx.set_status_reporter(StatusReporter())
    with ctx:
        yield ctx


@pytest.fixture
def lights(context):
    """One folder, two nights of M31 in Ha (10 subs each), one night in OIII (2 subs), named by
    time so the earliest sorts first; the subs' order of creation is scrambled."""
    root = LibraryRoot.create(name="Main", path="/data/")

    def night(start, filter_, count):
        for i in reversed(range(count)):
            local = start + timedelta(minutes=5 * i)
            file = File.create(root=root, path="m31/", name=f"{filter_}_{local:%m%d_%H%M}.fits", size=1,
                               mtime_millis=1)
            Image.create(file=file, image_type="LIGHT", object_name="M31", filter=filter_, exposure=300.0,
                         date_obs=local - timedelta(hours=1), local_time=local, local_time_source="loc")
    night(EVENING, "Ha", 10)
    night(EVENING + timedelta(days=1), "Ha", 10)
    night(EVENING, "OIII", 2)
    return unsolved_lights(SearchCriteria())


def _solve(context, task_files=None, plan=None, solver=None):
    task = PlateSolveTask(context, SearchCriteria(), task_files, solver=solver or FakeSolver(),
                          one_per_group=True, plan=plan)
    task._process_files()
    return task


def test_the_earliest_frame_of_each_group(context, lights):
    solver = FakeSolver()

    task = _solve(context, solver=solver)

    assert sorted(solver.solved) == ["Ha_0305_2100.fits", "Ha_0306_2100.fits", "OIII_0305_2100.fits"]
    assert FileWCS.select().count() == 3
    assert task.plan.total_files == 22


def test_a_group_with_a_solved_frame_is_skipped(context, lights):
    FileWCS.create(file=File.get(File.name == "Ha_0305_2130.fits"), wcs=compress(b"wcs"))
    solver = FakeSolver()

    task = _solve(context, solver=solver)

    assert sorted(solver.solved) == ["Ha_0306_2100.fits", "OIII_0305_2100.fits"]
    assert len(task.plan.skipped) == 1
    assert "skipped, already solved" in task.plan.describe()


def test_retry_moves_on_to_the_next_frame_and_gives_up_after_three(context, lights):
    solver = FakeSolver(fails=True)

    first = _solve(context, solver=solver)
    second = _solve(context, plan=first.plan, solver=solver)
    third = _solve(context, plan=second.plan, solver=solver)

    assert sorted(solver.solved[:3]) == ["Ha_0305_2100.fits", "Ha_0306_2100.fits", "OIII_0305_2100.fits"]
    assert sorted(solver.solved[3:6]) == ["Ha_0305_2105.fits", "Ha_0306_2105.fits", "OIII_0305_2105.fits"]
    # the OIII group has only two frames: it gave up after the second round
    assert sorted(solver.solved[6:]) == ["Ha_0305_2110.fits", "Ha_0306_2110.fits"]
    assert third.plan.next_round() == []
    assert len(third.plan.failed_groups()) == 3


def test_a_retry_that_solves_stops_the_group(context, lights):
    solver = FailsFirst(failures=3)  # the first round fails, the retry solves

    first = _solve(context, solver=solver)
    second = _solve(context, plan=first.plan, solver=solver)

    assert second.plan.next_round() == []
    assert not second.plan.failed_groups()
    assert FileWCS.select().count() == 3


def test_a_selection_chooses_among_its_own_frames(context, lights):
    later = [f for f in lights if f.name >= "Ha_0305_2120.fits" and f.name.startswith("Ha_0305")]
    solver = FakeSolver()

    _solve(context, task_files=later, solver=solver)

    assert solver.solved == ["Ha_0305_2120.fits"]


def test_without_the_option_every_file_is_solved(context, lights):
    solver = FakeSolver()
    task = PlateSolveTask(context, SearchCriteria(), None, solver=solver)

    task._process_files()

    assert len(solver.solved) == 22


def test_mcp_retries_within_the_call(context, lights, monkeypatch):
    context.settings._store["mcp_allow_plate_solve"] = True
    solver = FailsFirst(failures=4)
    monkeypatch.setattr(solvers, "build_solver", lambda settings, index: solver)

    result = mcp_server.query_plate_solve(context, [f.rowid for f in lights], one_per_group=True)

    assert result["groups"] == {"solved": 3, "failed": 0, "skipped_already_solved": 0, "files_given": 22}
    assert result["solved"] == 3 and result["failed"] == 4
    assert len(result["results"]) == 7

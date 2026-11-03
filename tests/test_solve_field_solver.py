"""Stopping a solve-field run: a stand-in process for the loop itself, the real solve-field
through WSL and Cygwin for what is left running afterwards."""
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from photonfinder.platesolver import SolveFieldSolver, SolverCancelled, SolverFailure, _to_wsl_path
from tests.test_platesolver import _CYGWIN_SOLVE_FIELD, wsl_available

_ASPS_SOLVE_FIELD = Path(os.path.expandvars(
    r"%USERPROFILE%\AppData\Local\Astrometry\lib\astrometry\bin\solve-field.exe"))  # All Sky Plate Solver


def _cancel_after(seconds: float):
    started = time.monotonic()
    return lambda: time.monotonic() - started > seconds


def _python(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


def test_a_cancel_stops_the_run_and_keeps_its_output():
    lines = []
    started = time.monotonic()

    with pytest.raises(SolverCancelled):
        SolveFieldSolver()._run_solve_field(
            _python("print('extracting sources'); import time; time.sleep(60)"), None,
            output_callback=lines.append, cancelled=_cancel_after(1))

    assert time.monotonic() - started < 5
    assert lines == ["extracting sources"]


def test_without_a_cancel_the_run_ends_by_itself():
    output = SolveFieldSolver()._run_solve_field(_python("print('done')"), None, cancelled=lambda: False)

    assert output == ["done"]


def test_the_timeout_still_applies():
    solver = SolveFieldSolver(timeout=-14)  # the loop allows the timeout plus 15 s

    with pytest.raises(SolverFailure, match="timed out"):
        solver._run_solve_field(_python("import time; time.sleep(60)"), None)


def _unsolvable_frame(path: Path) -> Path:
    """Star-like spots at random: solve-field searches every index before giving up."""
    rng = np.random.default_rng(3)
    data = rng.normal(1000, 30, (2000, 3000))
    for _ in range(400):
        y, x = rng.integers(10, 1990), rng.integers(10, 2990)
        data[y - 2:y + 3, x - 2:x + 3] += rng.uniform(500, 5000)
    fits.PrimaryHDU(data.astype(np.float32)).writeto(path)
    return path


def _solve_and_cancel(solver: SolveFieldSolver, image: Path, after: float):
    """Solve ``image`` and cancel after ``after`` seconds. Returns the run's temp dir. Raising
    SolverCancelled, not SolverFailure, shows the run was still going when the cancel came."""
    started = time.monotonic()
    with solver:
        tmp_dir = Path(solver.tmp_dir)
        with pytest.raises(SolverCancelled):
            solver.solve(image, cancelled=_cancel_after(after))
    assert time.monotonic() - started < after + 30  # the temp image is written before the run
    return tmp_dir


@pytest.mark.slow
@wsl_available
def test_a_cancelled_wsl_run_leaves_nothing_running_in_the_distro(tmp_path):
    image = _unsolvable_frame(tmp_path / "noise.fit")

    tmp_dir = _solve_and_cancel(SolveFieldSolver(), image, after=12)

    leftover = subprocess.run(["wsl", "--exec", "pgrep", "-af", _to_wsl_path(tmp_dir)],
                              capture_output=True, text=True, check=False).stdout
    assert leftover == ""
    assert not tmp_dir.exists()


def _solve_field_processes() -> int:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False).stdout
    return sum(1 for line in out.splitlines() if "solve-field" in line.lower() or "astrometry-engine" in line.lower())


@pytest.mark.slow
@pytest.mark.parametrize("exe", [
    pytest.param(_CYGWIN_SOLVE_FIELD, marks=pytest.mark.skipif(not _CYGWIN_SOLVE_FIELD.exists(),
                                                                reason="ansvr solve-field not found")),
    pytest.param(_ASPS_SOLVE_FIELD, marks=pytest.mark.skipif(not _ASPS_SOLVE_FIELD.exists(),
                                                              reason="All Sky Plate Solver not found")),
], ids=["ansvr", "all_sky_plate_solver"])
def test_a_cancelled_cygwin_run_leaves_nothing_running(tmp_path, exe):
    image = _unsolvable_frame(tmp_path / "noise.fit")
    before = _solve_field_processes()

    tmp_dir = _solve_and_cancel(SolveFieldSolver(exe_path=str(exe)), image, after=12)

    time.sleep(1)
    assert _solve_field_processes() == before
    assert not tmp_dir.exists()

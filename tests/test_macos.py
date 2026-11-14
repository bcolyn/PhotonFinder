"""What changes on macOS: where the app keeps its data, where it looks for ASTAP, and running
solve-field as an ordinary local program. sys.platform is patched, so these run on any OS."""
import sys
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from photonfinder import core, mcp_stub, platesolver
from photonfinder.platesolver import SolveFieldSolver


@pytest.fixture
def on_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.mark.parametrize("module", [core, mcp_stub], ids=["core", "mcp_stub"])
def test_app_data_is_under_application_support(on_macos, module):
    assert module.user_app_data_dir() == on_macos / "Library" / "Application Support" / "photonfinder"


def test_the_stub_and_the_app_agree_on_the_data_folder(on_macos):
    assert mcp_stub.user_app_data_dir() == core.user_app_data_dir()


def test_astap_is_found_inside_its_app_bundle(on_macos, monkeypatch):
    bundled = "/Applications/ASTAP.app/Contents/MacOS/astap"
    monkeypatch.setattr(core.Path, "exists", lambda self: self.as_posix() == bundled)
    assert core.get_default_astap_path() == bundled


def test_without_the_app_astap_is_expected_on_the_path(on_macos, monkeypatch):
    monkeypatch.setattr(core.Path, "exists", lambda self: False)
    assert core.get_default_astap_path() == "astap"


def _solve_field_command(monkeypatch, tmp_path, exe_path="") -> tuple[list, dict | None]:
    """The command and environment solve() would run solve-field with."""
    image = tmp_path / "light.fits"
    fits.PrimaryHDU(np.zeros((16, 16), dtype=np.uint16)).writeto(image)
    seen = {}

    def record(self, cmd, env, *args):
        seen["cmd"], seen["env"] = cmd, env
        return []

    monkeypatch.setattr(SolveFieldSolver, "_run_solve_field", record)
    with SolveFieldSolver(exe_path=exe_path) as solver:
        with pytest.raises(platesolver.SolverFailure):  # nothing ran, so nothing solved
            solver.solve(image)
    return seen["cmd"], seen["env"]


def test_solve_field_runs_directly_with_paths_as_they_are(on_macos, monkeypatch, tmp_path):
    cmd, env = _solve_field_command(monkeypatch, tmp_path)
    assert cmd[0] == "solve-field"
    assert "wsl" not in cmd
    assert env is None
    # Passed in its native spelling, not converted to a WSL/Cygwin path. Only sys.platform is
    # patched, so on a Windows host the native spelling still has backslashes.
    assert Path(cmd[-1]).is_absolute() and cmd[-1] == str(Path(cmd[-1]))


def test_a_configured_solve_field_is_used_without_cygwin(on_macos, monkeypatch, tmp_path):
    cmd, env = _solve_field_command(monkeypatch, tmp_path, exe_path="/opt/homebrew/bin/solve-field")
    assert cmd[0] == "/opt/homebrew/bin/solve-field"
    assert env is None


def test_windows_still_goes_through_wsl(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(SolveFieldSolver, "_to_unix_path", lambda self, path: path.as_posix())
    cmd, _ = _solve_field_command(monkeypatch, tmp_path)
    assert cmd[:2] == ["wsl", "solve-field"]

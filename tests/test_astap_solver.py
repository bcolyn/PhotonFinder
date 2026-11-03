"""What decides whether an ASTAP run solved a file. The .ini contents are as written by ASTAP."""
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from photonfinder import platesolver
from photonfinder.core import compress, decompress, get_default_astap_path
from photonfinder.models import File, FileWCS, Image, LibraryRoot
from photonfinder.platesolver import ASTAPSolver, SolverCancelled, SolverError, SolverFailure
from photonfinder.solve_service import solve_file
from tests.test_solve_service import FakeSolver

OLD_SOLUTION = dict(CTYPE1="RA---TAN", CTYPE2="DEC--TAN", CRVAL1=10.0, CRVAL2=20.0, CRPIX1=128.0, CRPIX2=128.0,
                    CD1_1=0.001, CD1_2=0.0, CD2_1=0.0, CD2_2=0.001, PLTSOLVD=True)
SOLVED_INI = b"PLTSOLVD=T\r\nCRVAL1= 1.5E+002\r\nCRVAL2= 4.5E+001\r\nCMDLINE=astap\r\nDIMENSIONS=256 x 256\r\n"
FAILED_INI = (b"PLTSOLVD=F\r\nCMDLINE=astap -f a.fit\r\nDIMENSIONS=256 x 256\r\nERROR=Not enough stars.\r\n"
              b"WARNING=Warning, small image dimensions! \r\n")


@pytest.fixture
def image_file(tmp_path) -> Path:
    return _image(tmp_path / "already_solved.fits", OLD_SOLUTION)


def _image(path: Path, cards: dict = None) -> Path:
    data = np.random.default_rng(1).normal(1000, 30, (256, 256)).astype(np.uint16)
    header = fits.Header()
    for key, value in (cards or {}).items():
        header[key] = value
    fits.PrimaryHDU(data, header).writeto(path)
    return path


REAL_POPEN = subprocess.Popen


def _process(code: str, **kwargs):
    """A real child process standing in for ASTAP, so waiting, killing and exit codes are real."""
    return REAL_POPEN([sys.executable, "-c", code], **kwargs)


@pytest.fixture
def fake_astap(monkeypatch):
    """Stands in for the ASTAP process only, writing what the given run would leave behind."""

    def install(ini: bytes = None, wcs: bool = False, log: bytes = None, solve_to: tuple = None, returncode=0):
        def popen(params, **kwargs):
            image = Path(params[params.index("-f") + 1])
            if ini is not None:
                image.with_suffix(".ini").write_bytes(ini)
            if wcs:
                image.with_suffix(".wcs").write_bytes(b"SIMPLE  = T")
            if log is not None:
                image.with_suffix(".log").write_bytes(log)
            if solve_to:
                with fits.open(image, mode="update") as hdul:
                    hdul[0].header["CRVAL1"], hdul[0].header["CRVAL2"] = solve_to
                    hdul[0].header["PLTSOLVD"] = True
            return _process(f"import sys; sys.exit({returncode})", **kwargs)
        monkeypatch.setattr(platesolver.subprocess, "Popen", popen)
    return install


class HungAstap:
    """An ASTAP that writes its .ini next to the image, as the real one does, then never ends."""

    def __init__(self, monkeypatch):
        self.processes = []
        self.images = []
        monkeypatch.setattr(platesolver.subprocess, "Popen", self._popen)

    def _popen(self, params, **kwargs):
        image = Path(params[params.index("-f") + 1])
        image.with_suffix(".ini").write_bytes(b"CMDLINE=astap\r\n")
        self.images.append(image)
        process = _process("import time; time.sleep(60)", **kwargs)
        self.processes.append(process)
        return process


@pytest.fixture
def hung_astap(monkeypatch):
    astap = HungAstap(monkeypatch)
    yield astap
    for process in astap.processes:
        process.kill()


def _solve(image_file):
    with ASTAPSolver(exe="astap") as solver:
        return solver.solve(image_file)


def test_a_solved_run_returns_the_new_solution_not_the_old_one(image_file, fake_astap):
    fake_astap(ini=SOLVED_INI, wcs=True, solve_to=(150.0, 45.0))

    solution = _solve(image_file)

    assert (solution["CRVAL1"], solution["CRVAL2"]) == (150.0, 45.0)


@pytest.mark.parametrize("ini, wcs", [
    (None, False),  # killed or crashed: nothing written
    (None, True),
    (SOLVED_INI, False),
    (b"", True),
    (b"PLTSOLVD=F\r\n", True),
], ids=["nothing", "wcs_only", "ini_only", "empty_ini", "ini_says_no"])
def test_a_solved_file_is_not_passed_off_as_a_new_solve(image_file, fake_astap, ini, wcs):
    fake_astap(ini=ini, wcs=wcs, returncode=1)

    with pytest.raises((SolverFailure, SolverError)):
        _solve(image_file)


def test_no_result_at_all_says_so_with_the_exit_code(image_file, fake_astap):
    fake_astap(returncode=7)

    with pytest.raises(SolverFailure, match="exit code 7"):
        _solve(image_file)


def test_astaps_own_error_is_reported(image_file, fake_astap):
    fake_astap(ini=FAILED_INI, returncode=2)

    with pytest.raises(SolverError, match="^Not enough stars.$"):
        _solve(image_file)


@pytest.mark.parametrize("ini, expected", [
    (b"PLTSOLVD=F\r\nCMDLINE=astap -f C:/100%/a.fit\r\nERROR=Only 5% of stars\r\n", "Only 5% of stars"),
    (b"PLTSOLVD=F\r\nWARNING=one\r\nWARNING=two\r\nERROR=Bad image\r\n", "Bad image"),
    (b"PLTSOLVD=F\r\nCMDLINE=astap -f C:/caf\xe9.fit\r\nERROR=Bad image\r\n", "Bad image"),
    (b"PLTSOLVD=F\r\nnot a key value line\r\nERROR=Bad = image\r\n", "Bad = image"),
    (b"PLTSOLVD=F\r\n[odd]\r\n  indented\r\nERROR=Bad image\r\n", "Bad image"),
], ids=["percent", "duplicate_keys", "non_utf8", "stray_line", "section_like"])
def test_an_awkward_ini_still_gives_astaps_error(image_file, fake_astap, ini, expected):
    fake_astap(ini=ini, returncode=2)

    with pytest.raises(SolverError) as error:
        _solve(image_file)

    assert str(error.value) == expected


def test_a_failure_without_an_error_carries_the_log(image_file, fake_astap):
    fake_astap(ini=b"PLTSOLVD=F\r\n", log=b"tried 100 stars\r\nno match caf\xe9\r\n", returncode=1)

    with pytest.raises(SolverFailure) as failure:
        _solve(image_file)

    assert "tried 100 stars" in "".join(failure.value.log)


def test_a_hung_astap_is_killed_at_the_timeout(image_file, hung_astap):
    started = time.monotonic()

    with ASTAPSolver(exe="astap", timeout=1) as solver:
        with pytest.raises(SolverFailure, match="timed out after 1s"):
            solver.solve(image_file)

    assert time.monotonic() - started < 15
    assert hung_astap.processes[0].poll() is not None


def test_a_cancel_stops_a_running_astap_and_its_files_are_removed(image_file, hung_astap):
    started = time.monotonic()
    cancel_after_a_second = lambda: time.monotonic() - started > 1

    with ASTAPSolver(exe="astap") as solver:
        with pytest.raises(SolverCancelled):
            solver.solve(image_file, cancelled=cancel_after_a_second)
        tmp_dir = Path(solver.tmp_dir)

    assert time.monotonic() - started < 5
    assert hung_astap.processes[0].poll() is not None
    assert hung_astap.images[0].parent == tmp_dir
    assert not tmp_dir.exists()  # the temp image and the .ini ASTAP wrote next to it


def test_without_a_cancel_astap_runs_to_its_result(image_file, fake_astap):
    fake_astap(ini=SOLVED_INI, wcs=True, solve_to=(150.0, 45.0))

    with ASTAPSolver(exe="astap") as solver:
        solution = solver.solve(image_file, cancelled=lambda: False)

    assert solution["CRVAL1"] == 150.0


def test_astap_has_a_timeout_by_default():
    assert ASTAPSolver(exe="astap")._timeout == platesolver.ASTAP_TIMEOUT_SECONDS > 0


class TestThroughTheSolveService:
    @pytest.fixture
    def file(self, context, image_file):
        root = LibraryRoot.create(name="r", path=str(image_file.parent).replace("\\", "/") + "/")
        file = File.create(root=root, path="", name=image_file.name, size=1, mtime_millis=1)
        file.image = Image.create(file=file, image_type="LIGHT", coord_ra=10.0, coord_dec=20.0)
        FileWCS.create(file=file, wcs=compress(b"the stored solution"))
        return file

    def test_a_timeout_reaches_the_backup_solver(self, file, hung_astap):
        backup = FakeSolver()

        outcome = solve_file(file, ASTAPSolver(exe="astap", timeout=1), backup_solver=backup)

        assert outcome.success
        assert outcome.used_solver == type(backup).__name__

    def test_a_timeout_without_a_backup_keeps_the_stored_solution(self, file, hung_astap):
        outcome = solve_file(file, ASTAPSolver(exe="astap", timeout=1))

        assert not outcome.success
        assert "timed out" in outcome.error
        assert decompress(FileWCS.get(FileWCS.file == file).wcs) == b"the stored solution"

    def test_a_silent_failure_keeps_the_stored_solution(self, file, fake_astap):
        fake_astap(returncode=1)

        outcome = solve_file(file, ASTAPSolver(exe="astap"))

        assert not outcome.success
        assert decompress(FileWCS.get(FileWCS.file == file).wcs) == b"the stored solution"
        assert Image.get(Image.file == file).coord_ra == 10.0

    def test_a_cancelled_astap_is_cancelled_and_skips_the_backup(self, file, hung_astap):
        started = time.monotonic()
        backup = FakeSolver()

        outcome = solve_file(file, ASTAPSolver(exe="astap"), backup_solver=backup,
                             cancelled=lambda: time.monotonic() - started > 1)

        assert (outcome.success, outcome.cancelled, outcome.error) == (False, True, None)
        assert backup.solved == []
        assert time.monotonic() - started < 5
        assert decompress(FileWCS.get(FileWCS.file == file).wcs) == b"the stored solution"


@pytest.mark.slow
@pytest.mark.skipif(not Path(get_default_astap_path()).is_file(), reason="ASTAP not installed")
def test_real_astap_reports_its_error_for_an_unsolvable_solved_file(image_file):
    with pytest.raises(SolverError, match="Not enough stars"):
        _solve_with_real_astap(image_file)


def _solve_with_real_astap(image_file):
    with ASTAPSolver() as solver:
        return solver.solve(image_file)

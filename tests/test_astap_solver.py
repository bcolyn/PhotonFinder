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
from photonfinder.platesolver import ASTAPSolver, SolverError, SolverFailure
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


@pytest.fixture
def fake_astap(monkeypatch):
    """Stands in for the ASTAP process only, writing what the given run would leave behind."""

    def install(ini: bytes = None, wcs: bool = False, log: bytes = None, solve_to: tuple = None, returncode=0):
        def run(params, **kwargs):
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
            return subprocess.CompletedProcess(params, returncode, "", "")
        monkeypatch.setattr(platesolver.subprocess, "run", run)
    return install


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


def test_a_hung_astap_is_killed_at_the_timeout(image_file, monkeypatch):
    real_run = subprocess.run
    timeouts = []

    def run_something_that_hangs(params, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return real_run([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
    monkeypatch.setattr(platesolver.subprocess, "run", run_something_that_hangs)
    started = time.monotonic()

    with ASTAPSolver(exe="astap", timeout=1) as solver:
        with pytest.raises(SolverFailure, match="timed out after 1s"):
            solver.solve(image_file)

    assert timeouts == [1]
    assert time.monotonic() - started < 15


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

    def _hang(self, monkeypatch):
        def run(params, **kwargs):
            raise subprocess.TimeoutExpired(params, kwargs["timeout"])
        monkeypatch.setattr(platesolver.subprocess, "run", run)

    def test_a_timeout_reaches_the_backup_solver(self, file, monkeypatch):
        self._hang(monkeypatch)
        backup = FakeSolver()

        outcome = solve_file(file, ASTAPSolver(exe="astap"), backup_solver=backup)

        assert outcome.success
        assert outcome.used_solver == type(backup).__name__

    def test_a_timeout_without_a_backup_keeps_the_stored_solution(self, file, monkeypatch):
        self._hang(monkeypatch)

        outcome = solve_file(file, ASTAPSolver(exe="astap"))

        assert not outcome.success
        assert "timed out" in outcome.error
        assert decompress(FileWCS.get(FileWCS.file == file).wcs) == b"the stored solution"

    def test_a_silent_failure_keeps_the_stored_solution(self, file, fake_astap):
        fake_astap(returncode=1)

        outcome = solve_file(file, ASTAPSolver(exe="astap"))

        assert not outcome.success
        assert decompress(FileWCS.get(FileWCS.file == file).wcs) == b"the stored solution"
        assert Image.get(Image.file == file).coord_ra == 10.0


@pytest.mark.slow
@pytest.mark.skipif(not Path(get_default_astap_path()).is_file(), reason="ASTAP not installed")
def test_real_astap_reports_its_error_for_an_unsolvable_solved_file(image_file):
    with pytest.raises(SolverError, match="Not enough stars"):
        _solve_with_real_astap(image_file)


def _solve_with_real_astap(image_file):
    with ASTAPSolver() as solver:
        return solver.solve(image_file)

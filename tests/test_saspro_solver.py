"""Running Seti Astro Suite Pro's Gaia solver: a stand-in process for its command line and
output, and the real SASpro, when installed, for a solve checked against a known solution."""
import json
import lzma
import sys
import textwrap
import time
from pathlib import Path

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS

from photonfinder.core import get_default_saspro_path
from photonfinder.platesolver import (
    SASproSolver, SolverCancelled, SolverError, SolverFailure, SolverHint, _parse_saspro_result,
)

STAND_IN = textwrap.dedent('''
    """Plays SetiAstroSuitePro.exe solve: follows plan.json, one step per run, and records each run."""
    import json, pathlib, sys, time
    from astropy.io import fits

    here = pathlib.Path(__file__).parent
    plan = json.loads((here / "plan.json").read_text())
    calls = here / "calls.json"
    runs = json.loads(calls.read_text()) if calls.exists() else []
    args = sys.argv[1:]
    given = fits.getheader(args[args.index("-i") + 1])
    runs.append({"args": args, "keywords": [k for k in given if k]})
    calls.write_text(json.dumps(runs))
    step = plan[min(len(runs) - 1, len(plan) - 1)]

    print("Loading FITS file: " + args[args.index("-i") + 1])
    for line in step.get("print", []):
        print(line)
    sys.stdout.flush()
    time.sleep(step.get("sleep", 0))
    if "wcs" in step:
        fits.PrimaryHDU(header=fits.Header(step["wcs"])).writeto(args[args.index("--wcs-path") + 1], overwrite=True)
    print(json.dumps(step["json"], indent=2))
    sys.exit(step.get("exit", 0))
''')

SOLVED_WCS = {"CTYPE1": "RA---TAN-SIP", "CTYPE2": "DEC--TAN-SIP", "CRVAL1": 36.85, "CRVAL2": 33.58,
              "CRPIX1": 15.5, "CRPIX2": 10.5, "CD1_1": -0.000344, "CD1_2": 0.0, "CD2_1": 0.0,
              "CD2_2": 0.000344, "A_ORDER": 2, "A_0_2": 1e-7, "IMAGEW": 30, "IMAGEH": 20}
SOLVED = {"wcs": SOLVED_WCS, "json": {"ok": True, "ra_deg": 36.85, "dec_deg": 33.58, "n_stars": 423,
                                      "rms_arcsec": 0.36, "distortion": "SIP-2", "wcs": "x.wcs"}}


def _failed(error, exit_code=1):
    return {"json": {"ok": False, "error": error, "log": f"Status: {error}\n"}, "exit": exit_code}


class StandInSASpro(SASproSolver):
    def __init__(self, folder: Path, plan: list[dict], **kwargs):
        super().__init__(exe="SetiAstroSuitePro.exe", **kwargs)
        self.folder = folder
        (folder / "stand_in.py").write_text(STAND_IN)
        (folder / "plan.json").write_text(json.dumps(plan))

    def _command(self):
        return [sys.executable, str(self.folder / "stand_in.py")]

    @property
    def runs(self):
        calls = self.folder / "calls.json"
        return json.loads(calls.read_text()) if calls.exists() else []


def _frame(tmp_path, **cards) -> Path:
    header = fits.Header({"OBJECT": "NGC 925", "BAYERPAT": "RGGB", "FLIPPED": True, **cards})
    path = tmp_path / "light.fits"
    fits.PrimaryHDU(np.arange(600, dtype=np.uint16).reshape(20, 30), header).writeto(path)
    return path


def _solve(solver, image, **kwargs):
    with solver:
        return solver.solve(image, **kwargs)


def _option(args, name):
    return args[args.index(name) + 1] if name in args else None


def test_a_frame_without_a_position_is_solved_blind(tmp_path):
    solver = StandInSASpro(tmp_path, [SOLVED])

    solution = _solve(solver, _frame(tmp_path))

    (run,) = solver.runs
    assert run["args"][:2] == ["solve", "-i"]
    assert _option(run["args"], "--solver") == "gaia"
    assert "--json" in run["args"] and "--ra" not in run["args"] and "--scale" not in run["args"]
    assert (solution["CRVAL1"], solution["CRVAL2"], solution["CD2_2"]) == (36.85, 33.58, 0.000344)
    assert (solution["NAXIS1"], solution["NAXIS2"]) == (30, 20)


def test_saspro_is_given_only_the_pixels(tmp_path):
    """Some header contents make SASpro read the image upside down."""
    solver = StandInSASpro(tmp_path, [SOLVED])

    _solve(solver, _frame(tmp_path, RA=36.8, DEC=33.6))

    assert set(solver.runs[0]["keywords"]) <= {"SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "EXTEND",
                                               "BZERO", "BSCALE"}


def test_the_header_position_and_scale_seed_the_solve(tmp_path):
    solver = StandInSASpro(tmp_path, [SOLVED])

    _solve(solver, _frame(tmp_path, RA=36.8, DEC=33.6, FOCALLEN=400.0, YPIXSZ=2.4))

    args = solver.runs[0]["args"]
    assert (float(_option(args, "--ra")), float(_option(args, "--dec"))) == (36.8, 33.6)
    assert float(_option(args, "--scale")) == pytest.approx(1.2376, abs=1e-4)


def test_an_override_hint_replaces_the_header_position(tmp_path):
    solver = StandInSASpro(tmp_path, [SOLVED])

    _solve(solver, _frame(tmp_path, RA=36.8, DEC=33.6), hint=SolverHint(ra=10.0, dec=20.0, scale=2.0, mode="override"))

    args = solver.runs[0]["args"]
    assert (_option(args, "--ra"), _option(args, "--dec"), _option(args, "--scale")) == ("10.0", "20.0", "2.0")


def test_a_fallback_hint_only_fills_what_the_header_lacks(tmp_path):
    solver = StandInSASpro(tmp_path, [SOLVED])

    _solve(solver, _frame(tmp_path, RA=36.8, DEC=33.6), hint=SolverHint(ra=10.0, dec=20.0, scale=2.0))

    args = solver.runs[0]["args"]
    assert (_option(args, "--ra"), _option(args, "--scale")) == ("36.8", "2.0")


def test_a_failed_seeded_solve_is_retried_blind(tmp_path):
    """A wrong seed fails in SASpro rather than searching on."""
    solver = StandInSASpro(tmp_path, [_failed("match quality too low"), SOLVED])
    lines = []

    solution = _solve(solver, _frame(tmp_path, RA=100.0, DEC=10.0), output_callback=lines.append)

    first, second = (run["args"] for run in solver.runs)
    assert "--ra" in first and "--ra" not in second and "--scale" not in second
    assert solution["CRVAL1"] == 36.85
    assert any("seeded solve failed (match quality too low), solving blind" in line for line in lines)


def test_saspro_reads_an_ascii_name_whatever_the_original_is_called(tmp_path):
    """SASpro can't read a path with a character outside cp1252 ('charmap' codec error)."""
    solver = StandInSASpro(tmp_path, [_failed("blind solve found no match")])
    frame = _frame(tmp_path).rename(tmp_path / "M31 ✓.fits")

    with pytest.raises(SolverFailure, match="could not solve M31 ✓.fits"):
        _solve(solver, frame)

    image = _option(solver.runs[0]["args"], "-i")
    assert image.isascii() and _option(solver.runs[0]["args"], "--wcs-path").isascii()


def test_a_blind_failure_reports_saspros_reason_and_log(tmp_path):
    solver = StandInSASpro(tmp_path, [_failed("blind solve found no match")])

    with pytest.raises(SolverFailure, match="could not solve light.fits: blind solve found no match") as failure:
        _solve(solver, _frame(tmp_path))

    assert failure.value.log == ["Status: blind solve found no match"]
    assert len(solver.runs) == 1


def test_a_usage_or_io_error_is_not_retried_blind(tmp_path):
    solver = StandInSASpro(tmp_path, [_failed("unsupported or unreadable image", exit_code=2)])

    with pytest.raises(SolverFailure, match="unsupported or unreadable image"):
        _solve(solver, _frame(tmp_path, RA=36.8, DEC=33.6))

    assert len(solver.runs) == 1


def test_a_misread_image_is_never_a_solution(tmp_path):
    fallback = dict(SOLVED, print=["Error reading image x: 'charmap' codec can't encode characters",
                                   "Recovered x via generic fallback reader (bit_depth=16-bit, mono=True)."])
    solver = StandInSASpro(tmp_path, [fallback])

    with pytest.raises(SolverFailure, match="fallback image reader"):
        _solve(solver, _frame(tmp_path, RA=36.8, DEC=33.6))

    assert len(solver.runs) == 1


def test_an_unreadable_wcs_claim_without_a_file_is_a_failure(tmp_path):
    solver = StandInSASpro(tmp_path, [{"json": SOLVED["json"]}])  # says ok, writes no .wcs

    with pytest.raises(SolverFailure):
        _solve(solver, _frame(tmp_path))


def test_a_cancel_stops_saspro(tmp_path):
    solver = StandInSASpro(tmp_path, [dict(SOLVED, sleep=60)])
    started = time.monotonic()

    with pytest.raises(SolverCancelled):
        _solve(solver, _frame(tmp_path), cancelled=lambda: time.monotonic() - started > 1)

    assert time.monotonic() - started < 10


def test_the_timeout_applies(tmp_path):
    solver = StandInSASpro(tmp_path, [dict(SOLVED, sleep=60)], timeout=1)

    with pytest.raises(SolverFailure, match="timed out after 1s"):
        _solve(solver, _frame(tmp_path))


def test_a_missing_executable_is_a_configuration_error(tmp_path):
    solver = SASproSolver(exe=str(tmp_path / "no-saspro.exe"))

    with pytest.raises(SolverError, match="Settings > Plate Solving"):
        _solve(solver, _frame(tmp_path))


def test_the_result_is_found_after_the_loader_lines():
    output = 'Loading FITS file: {odd} name\nWARNING: x\n{\n  "ok": true,\n  "ra_deg": 1.5\n}\n'

    assert _parse_saspro_result(output) == {"ok": True, "ra_deg": 1.5}
    assert _parse_saspro_result("Plate solve failed\n") == {}


# --- the real SASpro --------------------------------------------------------------------------

_M106 = Path(__file__).parent / "data" / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
_M106_SOLUTION = Path(__file__).parent / "data-small" / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.wcs.fits"


@pytest.mark.slow
@pytest.mark.skipif(not get_default_saspro_path(), reason="Seti Astro Suite Pro not installed")
@pytest.mark.skipif(not _M106.exists(), reason="test dataset not downloaded")
@pytest.mark.parametrize("hint", [None, SolverHint(ra=184.74, dec=47.31, scale=1.24, mode="override")],
                         ids=["blind", "seeded"])
def test_saspro_solves_the_m106_frame_like_astap_did(tmp_path, hint):
    """This frame's header makes SASpro's FITS reader fall back and read it upside down, which
    passing only the pixels avoids: every corner matches the stored ASTAP solution. Its name has
    a character outside cp1252, which SASpro can't read in a path."""
    frame = tmp_path / "m106 ✓.fits"
    frame.write_bytes(lzma.decompress(_M106.read_bytes()))

    solution = _solve(SASproSolver(), frame, hint=hint)

    reference, solved = WCS(fits.getheader(_M106_SOLUTION)), WCS(solution)
    for pixel in [(0, 0), (5495, 0), (0, 3671), (5495, 3671), (2747.5, 1835.5)]:
        expected = SkyCoord(*reference.pixel_to_world_values(*pixel), unit="deg")
        actual = SkyCoord(*solved.pixel_to_world_values(*pixel), unit="deg")
        assert expected.separation(actual).arcsec < 10, pixel

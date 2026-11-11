"""Plate solving JPEG and PNG pictures: each solver reads the picture itself, a solution is stored
in the decoded array's row order (ASTAP's is flipped to it), every picture is a group of its own,
and nothing is written next to the user's file."""
import shutil
import subprocess
import warnings
from pathlib import Path

import cv2
import numpy as np
import pytest
from astropy.io import fits
from astropy.io.fits import Header
from astropy.wcs import WCS, FITSFixedWarning

from photonfinder import pictures, plate_solution, solve_service
from photonfinder.core import get_default_astap_path, get_default_saspro_path
from photonfinder.models import File, FileWCS, Image, LibraryRoot, SearchCriteria, group_condition, group_of
from photonfinder.platesolver import (ASTAPSolver, SASproSolver, SolveFieldSolver, flip_solution_rows,
                                      read_astap_wcs)

SMALL = Path(__file__).parent / "data-small"
ASTAP_SAMPLE = SMALL / "astap_picture.wcs"  # ASTAP's own output for a 5457 x 3638 JPEG, paths neutralised
M106 = "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"

warnings.simplefilter("ignore", FITSFixedWarning)


def _sky(header: Header, xy: np.ndarray):
    """Sky positions of 1-based pixels under a solution, SIP included."""
    header = header.copy()
    header["NAXIS"] = 2
    return WCS(header, relax=True).all_pix2world(xy[:, 0], xy[:, 1], 1)


def _sip_solution() -> Header:
    """A solution with every kind of term the flip has to handle."""
    h = Header()
    h.update({"CTYPE1": "RA---TAN-SIP", "CTYPE2": "DEC--TAN-SIP", "CRVAL1": 150.0, "CRVAL2": 30.0,
              "CRPIX1": 210.5, "CRPIX2": 120.25, "CD1_1": -2.1e-4, "CD1_2": 3.0e-5, "CD2_1": -2.5e-5, "CD2_2": 2.05e-4,
              "A_ORDER": 2, "A_0_2": 3e-6, "A_1_1": -2e-6, "A_2_0": 1e-6,
              "B_ORDER": 2, "B_0_2": -1.5e-6, "B_1_1": 2.5e-6, "B_2_0": 4e-6,
              "AP_ORDER": 2, "AP_0_1": 1e-7, "AP_0_2": -3e-6, "AP_1_1": 2e-6, "AP_2_0": -1e-6,
              "BP_ORDER": 2, "BP_0_1": -2e-7, "BP_0_2": 1.5e-6, "BP_1_1": -2.5e-6, "BP_2_0": -4e-6})
    return h


class TestFlip:
    W, H = 400, 300
    POINTS = np.array([[1, 1], [400, 1], [1, 300], [400, 300], [123.4, 56.7], [350, 250]], float)

    def _same_sky_with_rows_flipped(self, original: Header):
        flipped = flip_solution_rows(original, self.H)
        mirror = self.POINTS.copy()
        mirror[:, 1] = self.H + 1 - mirror[:, 1]
        ra0, dec0 = _sky(original, self.POINTS)
        ra1, dec1 = _sky(flipped, mirror)
        np.testing.assert_allclose(ra1, ra0, atol=1e-9)
        np.testing.assert_allclose(dec1, dec0, atol=1e-9)
        return flipped

    def test_cd_and_sip(self):
        original = _sip_solution()
        flipped = self._same_sky_with_rows_flipped(original)

        # and the inverse polynomials too: sky -> pixel lands on the mirrored row
        header = flipped.copy()
        header["NAXIS"] = 2
        x, y = WCS(header, relax=True).all_world2pix(*_sky(original, self.POINTS), 1)
        np.testing.assert_allclose(x, self.POINTS[:, 0], atol=1e-3)
        np.testing.assert_allclose(y, self.H + 1 - self.POINTS[:, 1], atol=1e-3)

    def test_flipping_twice_gives_the_original(self):
        original = _sip_solution()
        twice = flip_solution_rows(flip_solution_rows(original, self.H), self.H)
        assert {k: twice[k] for k in original} == pytest.approx({k: original[k] for k in original})

    def test_pc_and_cdelt(self):
        h = Header({"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 10.0, "CRVAL2": -40.0,
                    "CRPIX1": 100.0, "CRPIX2": 80.0, "CDELT1": -3e-4, "CDELT2": 3e-4,
                    "PC1_1": 0.9, "PC1_2": 0.1, "PC2_1": -0.1, "PC2_2": 0.9})
        self._same_sky_with_rows_flipped(h)

    def test_cdelt_and_crota(self):
        h = Header({"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 10.0, "CRVAL2": -40.0,
                    "CRPIX1": 100.0, "CRPIX2": 80.0, "CDELT1": -3e-4, "CDELT2": 3e-4, "CROTA2": 25.0})
        self._same_sky_with_rows_flipped(h)


class TestAstapWcs:
    def test_the_text_style_file_is_read(self):
        with pytest.raises(Exception):  # why the reader exists: astropy's own refuses this file
            Header.fromtextfile(str(ASTAP_SAMPLE)).tostring()

        header = read_astap_wcs(ASTAP_SAMPLE)

        assert header["CRVAL1"] == pytest.approx(188.034028, abs=1e-6)
        assert header["CRVAL2"] == pytest.approx(7.952266, abs=1e-6)
        assert (header["CRPIX1"], header["CRPIX2"]) == (2729.0, 1819.5)
        assert header["A_ORDER"] == 3 and "B_3_0" in header
        assert "COMMENT" not in header
        _sky(header, np.array([[1.0, 1.0]]))  # a usable WCS


# --- grouping --------------------------------------------------------------------------------

@pytest.fixture
def library(context, tmp_path):
    root = LibraryRoot.create(name="r", path=str(tmp_path).replace("\\", "/") + "/", include_pictures=True)
    return root


def _file(root, name, image_type, **image):
    file = File.create(root=root, path="results/", name=name, size=1, mtime_millis=1)
    Image.create(file=file, hdu=0, image_type=image_type, **image)
    return file


def _with_image(files):
    rows = []
    for file in files:
        row = File.get_by_id(file.rowid)
        row.image = Image.get(Image.file == file)
        rows.append(row)
    return rows


class TestGrouping:
    def test_every_picture_is_a_group_of_its_own(self, library):
        a, b, c = (_file(library, f"{n}.jpg", pictures.PICTURE_TYPE, width=40, height=30) for n in "abc")
        lights = [_file(library, f"l{n}.fits", "LIGHT", object_name="M 31", filter="L", exposure=60.0) for n in "12"]

        plan = solve_service.SolvePlan(_with_image([a, b, c, *lights]))

        assert sorted(len(group.candidates) for group in plan.groups) == [1, 1, 1, 2]
        assert sorted(group.describe() for group in plan.groups if len(group.candidates) == 1) == \
            ["a.jpg", "b.jpg", "c.jpg"]

    def test_a_solved_picture_does_not_skip_the_others(self, library):
        a, b = (_file(library, f"{n}.png", pictures.PICTURE_TYPE, width=40, height=30) for n in "ab")
        FileWCS.create(file=a, wcs=b"x")

        plan = solve_service.SolvePlan(_with_image([a, b]))

        assert [group.candidates[0].name for group in plan.groups] == ["b.png"]
        assert [group.candidates[0].name for group in plan.skipped] == ["a.png"]

    def test_the_grid_groups_and_drills_into_one_picture(self, library):
        from photonfinder.library_query import matching_files, search_files
        _file(library, "a.jpg", pictures.PICTURE_TYPE, width=40, height=30)
        _file(library, "b.jpg", pictures.PICTURE_TYPE, width=40, height=30)
        for n in "12":
            _file(library, f"l{n}.fits", "LIGHT", object_name="M 31", filter="L", exposure=60.0)

        rows = search_files(SearchCriteria(one_per_group=True)).rows

        assert len(rows) == 3
        for row in rows:
            members = matching_files(SearchCriteria(groups=[group_of(row)]), require_image=True)
            expected = 1 if row.name.endswith(".jpg") else 2
            assert len(members) == expected, row.name

    def test_other_frames_group_as_before(self, library):
        f = _file(library, "l1.fits", "LIGHT", object_name="M 31")
        row = _with_image([f])[0]
        assert group_of(row)["picture"] is None
        assert File.select().join(Image).where(group_condition(group_of(row))).count() == 1


# --- solvers with the external program stood in for -------------------------------------------

def _picture(folder: Path, name="shot.jpg", width=5457, height=3638) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    ok, data = cv2.imencode(path.suffix, np.full((height, width), 60, np.uint8))
    assert ok
    data.tofile(str(path))
    return path


class _FinishedProcess:
    returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def test_astap_reads_the_picture_in_place_and_its_solution_is_flipped(tmp_path, monkeypatch):
    picture = _picture(tmp_path / "user")
    calls = []

    def popen(params, **_kwargs):
        calls.append(params)
        base = Path(params[params.index("-o") + 1])
        base.with_suffix(".ini").write_text("PLTSOLVD=T\n")
        shutil.copy(ASTAP_SAMPLE, base.with_suffix(".wcs"))
        return _FinishedProcess()

    monkeypatch.setattr(subprocess, "Popen", popen)
    with ASTAPSolver(exe="astap.exe") as solver:
        solution = solver.solve(picture)

    [params] = calls
    assert params[params.index("-f") + 1] == str(picture)
    assert "-update" not in params and "-o" in params
    assert Path(params[params.index("-o") + 1]).parent == Path(solver.tmp_dir)
    sample = read_astap_wcs(ASTAP_SAMPLE)
    assert solution["CRPIX2"] == pytest.approx(3638 + 1 - sample["CRPIX2"])
    assert solution["CD2_2"] == pytest.approx(-sample["CD2_2"])
    assert (solution["NAXIS1"], solution["NAXIS2"]) == (5457, 3638)
    assert sorted(p.name for p in picture.parent.iterdir()) == ["shot.jpg"]


def test_saspro_gets_an_ascii_named_copy_and_its_solution_is_kept(tmp_path, monkeypatch):
    picture = _picture(tmp_path / "Ñébula ✓", name="M 27 ✓.png", width=400, height=300)
    sample = _sip_solution()
    calls = []

    def popen(cmd, stdout=None, **_kwargs):
        calls.append(cmd)
        fits.PrimaryHDU(header=sample).writeto(cmd[cmd.index("--wcs-path") + 1])
        stdout.write(b'{"ok": true, "n_stars": 42, "rms_arcsec": 0.5, "distortion": "sip"}\n')
        return _FinishedProcess()

    monkeypatch.setattr(subprocess, "Popen", popen)
    with SASproSolver(exe="saspro.exe") as solver:
        solution = solver.solve(picture)

    [cmd] = calls
    copy = Path(cmd[cmd.index("-i") + 1])
    assert copy.parent == Path(solver.tmp_dir) and copy.name.isascii()
    assert "--ra" not in cmd and "--scale" not in cmd  # blind
    assert solution["CRPIX2"] == sample["CRPIX2"] and solution["CD2_2"] == sample["CD2_2"]  # not flipped
    assert (solution["NAXIS1"], solution["NAXIS2"]) == (400, 300)
    assert sorted(p.name for p in picture.parent.iterdir()) == ["M 27 ✓.png"]


def test_astrometry_net_uploads_the_picture_itself(tmp_path, monkeypatch):
    from photonfinder.platesolver import AstrometryNetSolver, _CancellableAstrometryNet
    picture = _picture(tmp_path / "Ñébula ✓", name="M 27 ✓.jpg", width=400, height=300)
    sample = _sip_solution()
    uploads = []

    def solve_by_upload(self, image_path, *, solve_timeout, **settings):
        uploads.append((Path(image_path), Path(image_path).read_bytes(), settings))
        return sample.copy()

    monkeypatch.setattr(_CancellableAstrometryNet, "solve_by_upload", solve_by_upload)
    with AstrometryNetSolver(api_key="key") as solver:
        solution = solver.solve(picture)

    [(uploaded, content, settings)] = uploads
    assert uploaded.name.isascii() and content == picture.read_bytes()  # the picture itself, byte for byte
    assert not {"center_ra", "center_dec", "scale_est", "scale_lower"} & set(settings)  # blind
    assert solution["CRPIX2"] == sample["CRPIX2"] and solution["CD2_2"] == sample["CD2_2"]  # not flipped
    assert (solution["NAXIS1"], solution["NAXIS2"]) == (400, 300)


def test_the_temp_fits_fallback_writes_the_decoded_rows_as_they_are(tmp_path):
    picture = tmp_path / "card.png"
    pixels = np.arange(30 * 40, dtype=np.uint16).reshape(30, 40)
    cv2.imencode(".png", pixels)[1].tofile(str(picture))

    with ASTAPSolver(exe="astap.exe") as solver:
        temp = solver._create_temp_fits(picture)  # pylint: disable=protected-access
        np.testing.assert_array_equal(fits.getdata(temp), pixels)


# --- real solves (slow): a picture made from the M106 test frame --------------------------------

def _solve_field_available() -> bool:
    try:
        return subprocess.run(["wsl", "which", "solve-field"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


SOLVERS = {
    "astap": (lambda: ASTAPSolver(exe=get_default_astap_path()), lambda: Path(get_default_astap_path()).is_file()),
    "saspro": (lambda: SASproSolver(exe=get_default_saspro_path()), lambda: Path(get_default_saspro_path()).is_file()),
    "solve-field": (lambda: SolveFieldSolver(timeout=300), _solve_field_available),
}


@pytest.fixture(scope="module")
def m106_pictures(global_test_data_dir, tmp_path_factory):
    """The M106 frame as a stretched 8-bit mono JPEG and PNG under a non-ASCII folder, and the same
    pixels as a FITS (rows as decoded) to solve for reference."""
    from photonfinder.image_hdus import load_hdu
    from photonfinder.image_processing import (debayer_opencv, detect_bayer_pattern, normalize_to_uint16,
                                               stretch_uint16_to_uint8)
    data, header = load_hdu(str(global_test_data_dir / M106), None)
    rgb = debayer_opencv(normalize_to_uint16(data, header), detect_bayer_pattern(header))
    mono = stretch_uint16_to_uint8(rgb.mean(axis=0).astype(np.uint16), 0.25, 2.8, True)
    folder = tmp_path_factory.mktemp("pics") / "Ñébula ✓"
    folder.mkdir()
    for name, params in (("m106.jpg", [cv2.IMWRITE_JPEG_QUALITY, 92]), ("m106.png", [])):
        ok, encoded = cv2.imencode(Path(name).suffix, mono, params)
        assert ok
        encoded.tofile(str(folder / name))
    reference = folder.parent / "m106_reference.fits"
    fits.PrimaryHDU(mono).writeto(reference)
    return folder, reference, mono.shape


def _corner_offsets_arcsec(solution: Header, reference: Header, width: int, height: int) -> float:
    from astropy.coordinates import SkyCoord
    corners = np.array([[1, 1], [width, 1], [1, height], [width, height]], float)
    a = SkyCoord(*_sky(solution, corners), unit="deg")
    b = SkyCoord(*_sky(reference, corners), unit="deg")
    return float(a.separation(b).arcsec.max())


@pytest.mark.slow
@pytest.mark.parametrize("solver_name", list(SOLVERS))
@pytest.mark.parametrize("picture_name", ["m106.png", "m106.jpg"])
def test_a_real_solve_is_stored_in_the_decoded_row_order(solver_name, picture_name, m106_pictures, context):
    make, available = SOLVERS[solver_name]
    if not available():
        pytest.skip(f"{solver_name} is not installed")
    folder, reference_fits, (height, width) = m106_pictures
    with make() as solver:
        reference = solver.solve(reference_fits)
    root = LibraryRoot.create(name="r", path=str(folder).replace("\\", "/") + "/", include_pictures=True)
    file = File.create(root=root, path="", name=picture_name, size=1, mtime_millis=1)
    Image.create(file=file, hdu=0, image_type=pictures.PICTURE_TYPE, width=width, height=height)
    before = sorted(p.name for p in folder.iterdir())

    outcome = solve_service.solve_file(file, make())

    assert outcome.success, outcome.error
    stored = plate_solution.load(file)
    assert (stored["NAXIS1"], stored["NAXIS2"]) == (width, height)
    assert _corner_offsets_arcsec(stored, reference, width, height) < 10
    assert Image.get(Image.file == file).coord_ra is not None
    assert sorted(p.name for p in folder.iterdir()) == before, "nothing written next to the picture"

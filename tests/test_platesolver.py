import os
import subprocess

import pytest
from astropy import units as u
from astropy_healpix import HEALPix

from photonfinder.platesolver import *
from photonfinder.platesolver import _create_temp_jpeg
from tests.sample_headers import *


_CYGWIN_SOLVE_FIELD = Path(os.path.expandvars(
    r"%USERPROFILE%\AppData\Local\cygwin_ansvr\lib\astrometry\bin\solve-field.exe" #Ansvr
    #"%USERPROFILE%\AppData\Local\Astrometry\lib\astrometry\bin\solve-field.exe" #All Sky Plate Solver
))

def _wsl_available() -> bool:
    try:
        result = subprocess.run(["wsl", "echo", "ok"], capture_output=True, text=True, timeout=30)
        return result.returncode == 0 and "ok" in result.stdout
    except Exception:
        return False


def _cygwin_available() -> bool:
    return _CYGWIN_SOLVE_FIELD.exists()


wsl_available = pytest.mark.skipif(not _wsl_available(), reason="WSL not available")
cygwin_available = pytest.mark.skipif(not _cygwin_available(), reason="CYGWIN solve-field not found")

# Center (RA, hms):	12h 18m 56.659s
# Center (Dec, dms):+47° 18' 49.201"
reference_center = SkyCoord("12h18m56.659s", "+47d18m49.201s", frame='icrs')
_hp = HEALPix(nside=256, order='nested', frame='icrs')
reference_cone_pixels = set(_hp.cone_search_skycoord(reference_center, radius=10 * u.arcsec))

@pytest.mark.slow
def test_solve_image_astap(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_astap(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.footprint_contains(reference_center)

    ra, dec, healpix, radius = get_image_center_coords(Header.fromstring(wcs_str))
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels
    assert 1.13 < radius < 1.14
    assert -178.8 < angle_to_north(wcs) < -178.6

@pytest.mark.internet
def test_solve_image_astrometry(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_astrometry_net(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.footprint_contains(reference_center)
    ra, dec, healpix, radius = get_image_center_coords(Header.fromstring(wcs_str))
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels
    assert radius is not None and 0.3 < radius < 3.0
    assert -178.8 < angle_to_north(wcs) < -178.6

def test_solve_image_astrometry_offline():
    # file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    # astrometry.net is sometimes offline, it's result for the input file is stored in data-small/
    output_file = Path(__file__).parent / "data-small/M106_2020-03-17T024357_60sec_LP__-15C_frame11.wcs.fits"
    assert output_file.exists()
    header = Header.fromfile(output_file)
    header['NAXIS1'] = 5496 # the WCS file does not contain image dimensions
    header['NAXIS2'] = 3672
    wcs = WCS(header)
    assert wcs.has_celestial
    assert wcs.footprint_contains(reference_center)

    ra, dec, healpix, radius = get_image_center_coords(header)
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels
    assert radius is not None
    assert 1.13 < radius < 1.14

@pytest.mark.slow
@cygwin_available
def test_solve_image_cygwin(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_cygwin(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.footprint_contains(reference_center)

    ra, dec, healpix, radius = get_image_center_coords(Header.fromstring(wcs_str))
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels
    assert 1.13 < radius < 1.14
    assert -178.8 < angle_to_north(wcs) < -178.6

@pytest.mark.slow
@wsl_available
def test_solve_image_wsl(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_wsl(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.footprint_contains(reference_center)

    ra, dec, healpix, radius = get_image_center_coords(Header.fromstring(wcs_str))
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels
    assert 1.13 < radius < 1.14
    assert -178.8 < angle_to_north(wcs) < -178.6

@pytest.mark.slow
def test_solve_image_xisf(global_test_data_dir):
    file_path = global_test_data_dir / "masterLight_BIN-1_1080x1920_EXPOSURE-10.00s_FILTER-LP_RGB.xisf"
    wcs_str = solve_image_astap(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.array_shape == (1920, 1080)
    assert wcs.footprint_contains(SkyCoord(6.1617 * 15 * u.deg, 20.5000 * u.deg, frame='icrs'))
    ra, dec, healpix, radius = get_image_center_coords(Header.fromstring(wcs_str))
    assert 0.70 < radius < 0.75


def test_flip_wcs_vertical():
    """Verify flip_wcs_vertical using the sample values from its docstring."""
    h = Header()
    h['NAXIS'] = 2
    h['NAXIS1'] = 4656
    h['NAXIS2'] = 3520
    h['CTYPE1'] = 'RA---TAN'
    h['CTYPE2'] = 'DEC--TAN'
    h['CRVAL1'] = 83.82
    h['CRVAL2'] = -5.39
    h['CRPIX1'] = 2328.0
    h['CRPIX2'] = 1760.0
    h['CD1_1'] = -0.000168
    h['CD1_2'] = 0.0
    h['CD2_1'] = 0.0
    h['CD2_2'] = +0.000168

    naxis2 = 3520
    original_crpix2 = 1760.0
    original_wcs = WCS(h)
    flipped = flip_wcs_vertical(original_wcs, naxis2)

    # CD2_2 must be negative after flipping column 1 of the CD matrix
    assert flipped.wcs.cd[1, 1] < 0

    # CRPIX2 before and after must sum to naxis2 + 1
    assert abs(original_crpix2 + flipped.wcs.crpix[1] - (naxis2 + 1)) < 1e-10

    # Round-trip: pixel → world → pixel must recover the original coordinates
    test_pixel = (1000.5, 2000.5)
    sky = flipped.pixel_to_world(*test_pixel)
    recovered = flipped.world_to_pixel(sky)
    assert abs(recovered[0] - test_pixel[0]) < 1e-6
    assert abs(recovered[1] - test_pixel[1]) < 1e-6


def test_has_been_plate_solved():
    header = Header.fromstring(sgp_header, sep='\n')
    assert not has_been_plate_solved(header)
    assert not ASTAPSolver.is_pre_solved(header)

    header = Header.fromstring(header_seestar, sep='\n')
    assert has_been_plate_solved(header)


def test_create_temp_jpeg(global_test_data_dir, tmpdir):
    file_path = global_test_data_dir / "masterLight_BIN-1_1080x1920_EXPOSURE-10.00s_FILTER-LP_RGB.xisf"
    _create_temp_jpeg(file_path, tmpdir)

    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    _create_temp_jpeg(file_path, tmpdir)


def solve_image_astrometry_net(image_path, api_key=None) -> str:
    if not api_key:
        api_key = os.environ.get("ASTROMETRY_NET_KEY")
    with AstrometryNetSolver(api_key) as solver:
        header = solver.solve(image_path)
    return header.tostring()


def solve_image_astap(image_path) -> str:
    with ASTAPSolver() as solver:
        header = solver.solve(image_path)
    return header.tostring()


def solve_image_wsl(image_path) -> str:
    # Test image: M106, 400mm FL, pixel scale ~1.24 arcsec/px
    hint = SolverHint(
        ra=reference_center.ra.deg,
        dec=reference_center.dec.deg,
        scale=1.24,
        mode='fallback',
    )
    with SolveFieldSolver() as solver:
        header = solver.solve(image_path, hint=hint)
    return header.tostring()


def solve_image_cygwin(image_path) -> str:
    # Test image: M106, 400mm FL, pixel scale ~1.24 arcsec/px
    hint = SolverHint(
        ra=reference_center.ra.deg,
        dec=reference_center.dec.deg,
        scale=1.24,
        mode='fallback',
    )
    with SolveFieldSolver(exe_path=str(_CYGWIN_SOLVE_FIELD)) as solver:
        header = solver.solve(image_path, hint=hint)
    return header.tostring()


def angle_to_north(wcs: WCS) -> float:
    crpix = wcs.wcs.crpix  # reference pixel, 1-indexed
    x, y = crpix[0] - 1, crpix[1] - 1  # convert to 0-indexed
    sky: SkyCoord = wcs.pixel_to_world(x, y)
    sky_north: SkyCoord = sky.directional_offset_by(0 * u.deg, 1 * u.arcsec)
    x2, y2 = wcs.world_to_pixel(sky_north)
    return float(np.degrees(np.arctan2(x2 - x, y2 - y)))
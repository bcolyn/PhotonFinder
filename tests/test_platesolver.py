import os

import pytest
from astropy import units as u
from astropy_healpix import HEALPix

from photonfinder.platesolver import *
from photonfinder.platesolver import _create_temp_jpeg
from tests.sample_headers import *

# Center (RA, hms):	12h 18m 56.659s
# Center (Dec, dms):+47° 18' 49.201"
reference_center = SkyCoord("12h18m56.659s", "+47d18m49.201s", frame='icrs')
_hp = HEALPix(nside=256, order='nested', frame='icrs')
reference_cone_pixels = set(_hp.cone_search_skycoord(reference_center, radius=10 * u.arcsec))

@pytest.mark.slow
def test_solve_image_wcs(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_astap(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.footprint_contains(reference_center)

    ra, dec, healpix = get_image_center_coords(Header.fromstring(wcs_str))
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels

@pytest.mark.internet
def test_solve_image_astrometry(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_astrometry_net(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.footprint_contains(reference_center)
    ra, dec, healpix = get_image_center_coords(Header.fromstring(wcs_str))
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels

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

    ra, dec, healpix = get_image_center_coords(header)
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels

@pytest.mark.slow
def test_solve_image_wsl(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_wsl(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.footprint_contains(reference_center)

    ra, dec, healpix = get_image_center_coords(Header.fromstring(wcs_str))
    assert reference_center.separation(SkyCoord(ra * u.deg, dec * u.deg, frame='icrs')) < 2 * u.arcsec
    assert healpix in reference_cone_pixels

@pytest.mark.slow
def test_solve_image_xisf(global_test_data_dir):
    file_path = global_test_data_dir / "masterLight_BIN-1_1080x1920_EXPOSURE-10.00s_FILTER-LP_RGB.xisf"
    wcs_str = solve_image_astap(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.array_shape == (1920, 1080)
    assert wcs.footprint_contains(SkyCoord(6.1617 * 15 * u.deg, 20.5000 * u.deg, frame='icrs'))


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
    # Test image (M106, 400mm FL) has pixel scale ~1.24 arcsec/px
    with WSLSolveFieldSolver() as solver:
        header = solver.solve(image_path)
    return header.tostring()

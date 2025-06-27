from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io.fits import Header
from astropy.wcs import WCS

from photonfinder.platesolver import solve_image_astap, has_been_plate_solved, ASTAPSolver, get_image_center_coords
from tests.sample_headers import *


def test_solve_image_wcs(global_test_data_dir):
    sky_coord = SkyCoord(12.3160 * 15 * u.deg, 47.3037 * u.deg, frame='icrs')
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_astap(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.array_shape == (3672, 5496)
    assert wcs.footprint_contains(sky_coord)

    ra, dec, healpix = get_image_center_coords(Header.fromstring(wcs_str))
    assert healpix == 175647


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
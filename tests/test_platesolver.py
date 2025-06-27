import os

from astropy import units as u
from astropy.wcs import WCS

from photonfinder.platesolver import *
from photonfinder.platesolver import _create_temp_jpeg
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


def test_solve_image_astrometry(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    wcs_str = solve_image_astrometry_net(file_path)
    wcs = WCS(Header.fromstring(wcs_str))
    assert wcs.has_celestial
    assert wcs.array_shape == (3672, 5496)
    assert wcs.footprint_contains(SkyCoord(12.3160 * 15 * u.deg, 47.3037 * u.deg, frame='icrs'))


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

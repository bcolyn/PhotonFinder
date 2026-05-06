"""Tests for photonfinder.annotation_math — pure CD-matrix geometry.

The M106 integration tests use the pre-solved WCS in tests/data-small/, which is
committed to the repository and always available (not a Hugging Face asset).
"""
from pathlib import Path

import numpy as np
import pytest
from astropy.io.fits import Header
from astropy.wcs import WCS

from photonfinder.annotation_math import annotation_rotation, cd_matrix, north_angle_scene

_DATA_SMALL = Path(__file__).parent / "data-small"
_M106_WCS_FILE = _DATA_SMALL / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.wcs.fits"

# Original image dimensions (not stored in the standalone WCS file)
_NAXIS1, _NAXIS2 = 5496, 3672

_S = 3.45e-4  # representative plate scale in deg/px (~1.24 arcsec/px)


@pytest.fixture(scope="module")
def m106_wcs() -> WCS:
    h = Header.fromfile(_M106_WCS_FILE)
    h["NAXIS1"] = _NAXIS1
    h["NAXIS2"] = _NAXIS2
    return WCS(h)


# ---------------------------------------------------------------------------
# north_angle_scene — synthetic CD matrices
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cd, expected_deg, label", [
    # N-up, E-right  (det < 0): M106-like orientation
    (np.array([[_S,  0.0], [0.0, -_S]]), -90.0, "N-up E-right"),
    # N-up, E-left   (det > 0): standard image-convention EQ mount
    (np.array([[-_S, 0.0], [0.0, -_S]]), -90.0, "N-up E-left"),
    # N-down, E-left (det < 0): standard FITS storage without flip
    (np.array([[-_S, 0.0], [0.0,  _S]]),  90.0, "N-down E-left"),
    # N to the right (det > 0): camera rotated 90° CW from N-up E-left
    (np.array([[0.0, -_S], [_S,  0.0]]),   0.0, "N-right"),
    # N upper-right at -45°: camera rotated 45° CCW from N-up E-right
    (np.array([[_S * 0.707, _S * 0.707],
               [_S * 0.707, -_S * 0.707]]), -45.0, "N-upper-right"),
])
def test_north_angle_scene(cd, expected_deg, label):
    result = north_angle_scene(cd)
    assert abs(result - expected_deg) < 0.5, (
        f"{label}: expected {expected_deg}°, got {result:.1f}°"
    )


# ---------------------------------------------------------------------------
# annotation_rotation — PA → Qt scene angle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cd, pa, expected_deg, label", [
    # N-up E-right (det < 0, s = -1): PA clockwise on screen
    (np.array([[_S, 0.0], [0.0, -_S]]),   0,  -90.0, "E-right PA=0  → north (up)"),
    (np.array([[_S, 0.0], [0.0, -_S]]),  90,    0.0, "E-right PA=90 → east (right)"),
    (np.array([[_S, 0.0], [0.0, -_S]]), 180,   90.0, "E-right PA=180 → south (down)"),
    # N-up E-left (det > 0, s = +1): PA counterclockwise on screen
    (np.array([[-_S, 0.0], [0.0, -_S]]),   0,  -90.0, "E-left PA=0  → north (up)"),
    (np.array([[-_S, 0.0], [0.0, -_S]]),  90, -180.0, "E-left PA=90 → east (left)"),
    (np.array([[-_S, 0.0], [0.0, -_S]]), 180,  -270.0, "E-left PA=180 → south (down)"),
])
def test_annotation_rotation(cd, pa, expected_deg, label):
    north = north_angle_scene(cd)
    result = annotation_rotation(north, pa, cd)
    # Ellipses are 180°-symmetric; normalise difference to (−180, 180]
    diff = (result - expected_deg + 180) % 360 - 180
    assert abs(diff) < 0.5, (
        f"{label}: expected {expected_deg}°, got {result:.1f}° (diff {diff:.1f}°)"
    )


# ---------------------------------------------------------------------------
# M106 WCS — integration tests with real astrometric solution
# ---------------------------------------------------------------------------

def test_m106_cd_matrix(m106_wcs):
    """cd_matrix() returns the 2×2 CD matrix for the M106 WCS."""
    cd = cd_matrix(m106_wcs)
    assert cd.shape == (2, 2)
    det = cd[0, 0] * cd[1, 1] - cd[0, 1] * cd[1, 0]
    # M106 WCS has det < 0 (E-right, N-up orientation)
    assert det < 0


def test_m106_north_angle(m106_wcs):
    """M106 is N-up E-right: north should point near Qt −90° (upward)."""
    cd = cd_matrix(m106_wcs)
    angle = north_angle_scene(cd)
    assert abs(angle - (-90.0)) < 2.0, f"Expected ≈−90°, got {angle:.2f}°"


def test_m106_nucleus_near_center(m106_wcs):
    """M106 nucleus should land near the image centre."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    nucleus = SkyCoord(184.7397, 47.3034, unit=u.deg, frame="icrs")
    x, y = m106_wcs.world_to_pixel(nucleus)

    assert 0 < x < _NAXIS1, f"M106 x={x:.0f} outside image"
    assert 0 < y < _NAXIS2, f"M106 y={y:.0f} outside image"
    # Within 5% of the image centre
    assert abs(x - _NAXIS1 / 2) < 0.05 * _NAXIS1
    assert abs(y - _NAXIS2 / 2) < 0.05 * _NAXIS2


def test_m106_ngc4217_in_frame(m106_wcs):
    """NGC 4217 (edge-on companion at RA=184.28°, Dec=47.09°) is inside the frame."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    ngc4217 = SkyCoord(184.2833, 47.0919, unit=u.deg, frame="icrs")
    x, y = m106_wcs.world_to_pixel(ngc4217)
    assert 0 < x < _NAXIS1, f"NGC 4217 x={x:.0f} outside image"
    assert 0 < y < _NAXIS2, f"NGC 4217 y={y:.0f} outside image"


def test_m106_east_direction(m106_wcs):
    """For the M106 E-right orientation PA=90 (east) should give Qt rotation ≈ 0° (right)."""
    cd = cd_matrix(m106_wcs)
    north = north_angle_scene(cd)
    rot = annotation_rotation(north, 90.0, cd)
    # Normalise and allow 2° for small field-rotation offset from true N
    rot_norm = (rot + 180) % 360 - 180
    assert abs(rot_norm) < 2.0, (
        f"PA=90 rotation expected ≈0° for E-right image, got {rot:.2f}°"
    )


def test_m106_pa0_equals_north(m106_wcs):
    """PA=0 rotation must always equal north_angle_scene regardless of orientation."""
    cd = cd_matrix(m106_wcs)
    north = north_angle_scene(cd)
    rot = annotation_rotation(north, 0.0, cd)
    assert abs(rot - north) < 1e-9

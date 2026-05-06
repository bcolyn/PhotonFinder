"""Pure geometry helpers for catalog annotation overlays.

All functions are Qt-free and database-free so they can be unit-tested without
any UI or ORM setup.  The display model assumed throughout is:

    scene_x = fits_x,  scene_y = fits_y   (no vertical flip)

where fits_x / fits_y are the 0-indexed pixel coordinates returned by
``wcs.world_to_pixel()``.  Qt scene y increases downward.
"""
from __future__ import annotations

import numpy as np
from astropy.wcs import WCS


def cd_matrix(wcs: WCS) -> np.ndarray:
    """Return the 2×2 CD matrix for *wcs* (falls back to pixel_scale_matrix)."""
    return wcs.wcs.cd if wcs.wcs.has_cd() else wcs.pixel_scale_matrix


def north_angle_scene(cd: np.ndarray) -> float:
    """Qt clockwise angle (degrees) of north in scene pixel coords.

    Correct for any CD-matrix orientation and sign convention.
    det(CD) > 0: image-convention FITS (top-down storage, N-up without flip).
    det(CD) < 0: standard FITS or E-right mirror (det encodes handedness).
    """
    det = cd[0, 0] * cd[1, 1] - cd[0, 1] * cd[1, 0]
    s = np.sign(det)
    return float(np.degrees(np.arctan2(s * cd[0, 0], -s * cd[0, 1])))


def annotation_rotation(north_scene: float, position_angle_deg: float, cd: np.ndarray) -> float:
    """Qt clockwise rotation (degrees) for an ellipse at the given astronomical PA.

    *position_angle_deg* follows the standard astronomical convention: measured
    counterclockwise from north toward east on the sky.
    """
    det = cd[0, 0] * cd[1, 1] - cd[0, 1] * cd[1, 0]
    s = float(np.sign(det))
    return north_scene - s * position_angle_deg

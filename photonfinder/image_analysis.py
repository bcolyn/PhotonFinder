"""Image quality analysis using sep (SExtractor bindings)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import sep

from photonfinder.image_processing import debayer_opencv, detect_bayer_pattern, load_image_data
from photonfinder.platesolver import select_first_channel

logger = logging.getLogger(__name__)


@dataclass
class ImageAnalysisResult:
    background_median: Optional[float] = None
    background_rms: Optional[float] = None
    star_count: Optional[int] = None
    fwhm_median: Optional[float] = None
    elongation_median: Optional[float] = None
    error: Optional[str] = None


def _to_float32(data: np.ndarray) -> np.ndarray:
    """Convert to writeable C-contiguous float32 as required by sep."""
    if data.dtype == np.float32 and data.flags['C_CONTIGUOUS'] and data.flags['WRITEABLE']:
        return data
    return np.ascontiguousarray(data.astype(np.float32))


CALIBRATION_TYPES = {"DARK", "FLAT", "BIAS", "MASTER DARK", "MASTER FLAT", "MASTER BIAS"}


def bayer_luminance(data: np.ndarray, pattern: str) -> np.ndarray:
    """Debayer a 2D Bayer mosaic and return the mean of its R, G and B planes (float32, same size).

    The raw mosaic's colour checkerboard skews source detection and shape moments; a 2x2
    super-pixel bin avoids it but undersamples typical stars, which biases FWHM upward
    by ~20 %. Interpolating keeps full resolution. Values stay in the input's units: integer
    data is debayered as is, float data is mapped onto 16 bits and back.
    """
    if data.dtype.kind in 'ui':
        return debayer_opencv(data, pattern).mean(axis=0, dtype=np.float32)
    f = np.nan_to_num(data.astype(np.float32))
    lo, hi = float(f.min()), float(f.max())
    span = hi - lo if hi > lo else 1.0
    lum = debayer_opencv((f - lo) / span, pattern).mean(axis=0, dtype=np.float32)
    return lum * np.float32(span / 65535.0) + np.float32(lo)


def analyze_image(
    data: np.ndarray,
    detection_threshold_sigma: float = 5.0,
    min_area: int = 5,
    max_sources: int = 5000,
    detect_sources: bool = True,
    bayer_pattern: Optional[str] = None,
) -> ImageAnalysisResult:
    """
    Compute background and star-shape statistics for an image array.

    Parameters
    ----------
    data : np.ndarray
        2D (H, W) or 3D channels-first (C, H, W), any dtype.
    detection_threshold_sigma : float
        Detection threshold in units of background RMS.
    min_area : int
        Minimum source area in pixels.
    max_sources : int
        Cap on sources used for shape statistics (brightest-first).
    detect_sources : bool
        When False only background stats are computed (use for calibration frames).
    bayer_pattern : str, optional
        For a 2D one-shot-colour mosaic, its Bayer pattern (e.g. 'RGGB'). Sources and
        background are then measured on the debayered luminance. Background-only analysis
        (detect_sources=False) still uses the raw pixels.
    """
    try:
        arr = select_first_channel(data)
        if bayer_pattern and detect_sources and arr.ndim == 2:
            arr = bayer_luminance(arr, bayer_pattern)
        arr = _to_float32(arr)

        bkg = sep.Background(arr)
        background_median = float(bkg.globalback)
        background_rms = float(bkg.globalrms)

        if not detect_sources:
            return ImageAnalysisResult(
                background_median=background_median,
                background_rms=background_rms,
            )

        data_sub = np.ascontiguousarray(arr - bkg)
        thresh = detection_threshold_sigma * background_rms
        objects = sep.extract(data_sub, thresh=thresh, minarea=min_area)

        star_count = len(objects)
        if star_count == 0:
            return ImageAnalysisResult(
                background_median=background_median,
                background_rms=background_rms,
                star_count=0,
            )

        # Use brightest sources for shape statistics
        order = np.argsort(objects['flux'])[::-1]
        top = objects[order[:max_sources]]

        a, b = top['a'], top['b']
        # FWHM = 2.3548 * sqrt((a² + b²) / 2),  Elongation = a / b
        fwhm = 2.3548 * np.sqrt((a ** 2 + b ** 2) / 2.0)
        with np.errstate(divide='ignore', invalid='ignore'):
            elongation = np.where(b > 0, a / b, np.nan)

        # Remove degenerate detections (cosmic rays, satellites)
        valid = (fwhm > 0.5) & (fwhm < 50.0) & (elongation >= 1.0) & (elongation < 10.0)
        fwhm, elongation = fwhm[valid], elongation[valid]

        return ImageAnalysisResult(
            background_median=background_median,
            background_rms=background_rms,
            star_count=star_count,
            fwhm_median=float(np.median(fwhm)) if len(fwhm) > 0 else None,
            elongation_median=float(np.median(elongation)) if len(elongation) > 0 else None,
        )

    except Exception as exc:
        logger.error("analyze_image failed: %s", exc, exc_info=True)
        return ImageAnalysisResult(error=str(exc))


def analyze_file(filename: str | Path, detect_sources: bool = True) -> ImageAnalysisResult:
    """Load file via load_image_data (LRU-cached) and run analyze_image."""
    try:
        data, header = load_image_data(filename)
        pattern = detect_bayer_pattern(header) if data.ndim == 2 else None
        return analyze_image(data, detect_sources=detect_sources, bayer_pattern=pattern)
    except Exception as exc:
        logger.error("analyze_file(%s) failed: %s", filename, exc, exc_info=True)
        return ImageAnalysisResult(error=str(exc))

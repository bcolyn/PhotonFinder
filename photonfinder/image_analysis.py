"""Image quality analysis using sep (SExtractor bindings)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import sep

from photonfinder.image_processing import load_image_data
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
    """Convert to C-contiguous float32 as required by sep."""
    if data.dtype == np.float32 and data.flags['C_CONTIGUOUS']:
        return data
    return np.ascontiguousarray(data.astype(np.float32))


CALIBRATION_TYPES = {"DARK", "FLAT", "BIAS", "MASTER DARK", "MASTER FLAT", "MASTER BIAS"}


def analyze_image(
    data: np.ndarray,
    detection_threshold_sigma: float = 5.0,
    min_area: int = 5,
    max_sources: int = 5000,
    detect_sources: bool = True,
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
    """
    try:
        arr = _to_float32(select_first_channel(data))

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
        data, _header = load_image_data(filename)
        return analyze_image(data, detect_sources=detect_sources)
    except Exception as exc:
        logger.error("analyze_file(%s) failed: %s", filename, exc, exc_info=True)
        return ImageAnalysisResult(error=str(exc))

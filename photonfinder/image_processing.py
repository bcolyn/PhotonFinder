"""
Astronomical image processing: loading, normalisation, debayering, and MTF stretch.
No Qt dependencies — pure numpy/OpenCV computation.

All internal pixel data is represented as uint16 [0, 65535].  Float32 is used only
for the statistics samples (sub-sampled, so small) and the LUT build step.
"""
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_BAYER_PATTERNS = {'RGGB', 'BGGR', 'GRBG', 'GBRG'}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_image_data(filename: str | Path) -> tuple[np.ndarray, dict]:
    """Load pixel data and header dict from a FITS, compressed FITS, or XISF file."""
    from photonfinder.filesystem import Importer
    filename = str(filename)
    if Importer.is_xisf_by_name(filename):
        return _load_xisf(filename)
    return _load_fits(filename)


def _load_fits(filename: str) -> tuple[np.ndarray, dict]:
    import time
    from astropy.io import fits

    suffix = Path(filename).suffix.lower()
    t0 = time.perf_counter()

    # astropy handles .gz and .fz natively with mmap; only bz2/xz need fopen()
    if suffix in ('.bz2', '.xz'):
        from photonfinder.filesystem import fopen
        with fopen(filename) as f:
            t1 = time.perf_counter()
            with fits.open(f, memmap=False) as hdul:
                t2 = time.perf_counter()
                hdu = hdul[0]
                data = hdu.data  # memmap=False → already in RAM, no copy needed
                header = dict(hdu.header)
        t3 = time.perf_counter()
        logger.debug(
            "FITS load (%s): decompress %.0fms  fits.open %.0fms  data.copy %.0fms  total %.0fms",
            suffix, (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000, (t3 - t0) * 1000,
        )
    else:
        with fits.open(filename) as hdul:  # memmap=True by default
            t1 = time.perf_counter()
            hdu = hdul[0]
            data = hdu.data.copy()
            header = dict(hdu.header)
        t2 = time.perf_counter()
        logger.debug(
            "FITS load (%s): fits.open %.0fms  data.copy %.0fms  total %.0fms",
            suffix, (t1 - t0) * 1000, (t2 - t1) * 1000, (t2 - t0) * 1000,
        )

    if data is None:
        raise ValueError(f"No image data in {filename}")
    return data, header


def _load_xisf(filename: str) -> tuple[np.ndarray, dict]:
    from xisf import XISF
    xf = XISF(filename)
    metadata_list = xf.get_images_metadata()
    if not metadata_list:
        raise ValueError(f"No images in {filename}")
    data = xf.read_image(0, 'channels_first')
    if data.ndim == 3 and data.shape[0] == 1:
        data = data.squeeze(0)  # (1, H, W) monochrome → (H, W)
    fits_keywords = metadata_list[0].get('FITSKeywords', {})
    header = {}
    for key, entries in fits_keywords.items():
        if entries:
            header[key] = entries[0].get('value', '')
    return data, header


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize_to_uint16(data: np.ndarray, header: dict) -> np.ndarray:
    """Convert raw pixel data to uint16 [0, 65535].

    Fast path: uint16 input with no PEDESTAL is returned as-is (zero-copy).
    All other cases go through a float32 intermediate for correct scaling.
    """
    pedestal = float(header.get('PEDESTAL', 0))

    if data.dtype == np.uint16 and pedestal == 0.0:
        return data

    f = data.astype(np.float32)
    if pedestal != 0.0:
        f -= pedestal

    if float(f.max()) <= 1.0 and float(f.min()) >= 0.0:
        return (np.clip(f, 0.0, 1.0) * 65535.0).astype(np.uint16)

    bitpix = abs(int(header.get('BITPIX', 16)))
    bzero  = float(header.get('BZERO', 0))

    if data.dtype.kind == 'f' and bitpix in (32, 64):
        max_val = float(f.max())
    elif bzero == 32768.0 and bitpix == 16:
        max_val = 65535.0
    else:
        max_val = float(2 ** bitpix - 1)

    if max_val > 0:
        f *= (65535.0 / max_val)

    return np.clip(f, 0.0, 65535.0).astype(np.uint16)


# ---------------------------------------------------------------------------
# Image-type detection
# ---------------------------------------------------------------------------

def detect_image_type(data: np.ndarray, header: dict) -> str:
    """Returns 'rgb', 'bayer', or 'mono'."""
    if data.ndim == 3:
        return 'rgb'
    if detect_bayer_pattern(header) is not None:
        return 'bayer'
    return 'mono'


def detect_bayer_pattern(header: dict) -> str | None:
    """Returns 'RGGB', 'BGGR', 'GRBG', 'GBRG', or None."""
    for key in ('BAYERPAT', 'COLORTYP'):
        val = str(header.get(key, '')).strip().upper()
        if val in _BAYER_PATTERNS:
            return val
    return None


def is_linear(data: np.ndarray) -> bool:
    """Heuristic linearity check: low median implies unprocessed linear data."""
    flat = data.ravel()
    step = max(1, len(flat) // 50_000)
    median = float(np.median(flat[::step]))
    if data.dtype.kind in ('u', 'i'):
        return median < 0.1 * 65535.0
    return median < 0.1


# ---------------------------------------------------------------------------
# Debayering
# ---------------------------------------------------------------------------

def debayer_opencv(data: np.ndarray, pattern: str) -> np.ndarray:
    """Demosaic a Bayer-pattern image using OpenCV edge-aware interpolation.

    Input:  2D array — uint16, signed int (FITS BZERO convention), or float32 [0, 1].
    Output: (3, H, W) uint16.

    Integer inputs are passed directly to OpenCV (no float round-trip).
    """
    import cv2

    pattern = pattern.upper()
    code_map = {
        'RGGB': cv2.COLOR_BAYER_RG2RGB_EA,
        'BGGR': cv2.COLOR_BAYER_BG2RGB_EA,
        'GRBG': cv2.COLOR_BAYER_GR2RGB_EA,
        'GBRG': cv2.COLOR_BAYER_GB2RGB_EA,
    }
    if pattern not in code_map:
        raise ValueError(f"Unsupported Bayer pattern: {pattern}")

    if data.dtype == np.uint16:
        data_u16 = data
    elif data.dtype.kind == 'i':
        data_u16 = np.clip(data, 0, 65535).astype(np.uint16)
    else:
        data_u16 = (np.clip(data, 0.0, 1.0) * 65535.0).astype(np.uint16)

    rgb = cv2.cvtColor(data_u16, code_map[pattern])
    return np.transpose(rgb, (2, 0, 1))  # (3, H, W) uint16


# ---------------------------------------------------------------------------
# MTF stretch — LUT-based, operates entirely in uint16 space
# ---------------------------------------------------------------------------

def compute_mtf_params(channel: np.ndarray, target_bg: float = 0.25,
                       clip_sigma: float = 2.8) -> tuple[float, float]:
    """PixInsight AutoSTF — accepts uint16 or float32 [0, 1] channel.

    Returns (black_point, midtone) in float [0, 1] space.
    """
    flat = channel.ravel()
    step = max(1, len(flat) // 500_000)
    sample = flat[::step]

    if channel.dtype.kind in ('u', 'i'):
        sample = sample.astype(np.float32) / 65535.0
    else:
        sample = sample.astype(np.float32)

    median = float(np.median(sample))
    mad    = float(np.median(np.abs(sample - median)))
    black  = max(0.0, median - clip_sigma * mad)
    scale  = 1.0 - black
    if scale < 1e-10:
        return black, 0.5

    clipped = np.clip((sample - black) / scale, 0.0, 1.0)
    m_prime = float(np.median(clipped))
    if m_prime <= 0.0 or m_prime >= 1.0:
        return black, 0.5

    denom = m_prime * (2.0 * target_bg - 1.0) - target_bg
    if abs(denom) < 1e-10:
        return black, 0.5

    return black, float(np.clip(m_prime * (target_bg - 1.0) / denom, 0.0, 1.0))


def build_stretch_lut(black: float, midtone: float) -> np.ndarray:
    """Build a 65536-entry uint16 → uint8 LUT for the MTF transfer function.

    Computing the LUT over 65536 float values is cheap (~0.5 ms).
    Applying it to an entire image is a pure memory-lookup — much faster than
    per-pixel float arithmetic.
    """
    x = np.arange(65536, dtype=np.float32) / 65535.0
    scale = 1.0 - black
    if scale < 1e-10:
        return np.zeros(65536, dtype=np.uint8)

    x = np.clip((x - black) / scale, 0.0, 1.0)
    denom  = x * (2.0 * midtone - 1.0) - midtone
    result = np.where(np.abs(denom) > 1e-10, x * (midtone - 1.0) / denom, 0.0)
    return (np.clip(result, 0.0, 1.0) * 255.0).astype(np.uint8)


def stretch_uint16_to_uint8(data: np.ndarray, target_bg: float,
                             clip_sigma: float, linked: bool) -> np.ndarray:
    """Stretch a uint16 image and produce a uint8 array suitable for QImage.

    Input:  (H, W) or (3, H, W) uint16.
    Output: (H, W) uint8 mono, or (H, W, 3) uint8 RGB (channels-last for QImage).
    """
    if data.ndim == 2:
        lut = build_stretch_lut(*compute_mtf_params(data, target_bg, clip_sigma))
        return lut[data]

    # (3, H, W)
    if linked:
        lut = build_stretch_lut(*compute_mtf_params(data[1], target_bg, clip_sigma))
        out = np.stack([lut[data[i]] for i in range(3)], axis=0)
    else:
        out = np.stack(
            [build_stretch_lut(*compute_mtf_params(data[i], target_bg, clip_sigma))[data[i]]
             for i in range(3)],
            axis=0,
        )

    return np.ascontiguousarray(out.transpose(1, 2, 0))  # (H, W, 3)


def uint16_to_uint8(data: np.ndarray) -> np.ndarray:
    """Scale uint16 to uint8 without stretch (shift right by 8 bits).

    Input:  (H, W) or (3, H, W) uint16.
    Output: (H, W) uint8 mono, or (H, W, 3) uint8 RGB (channels-last for QImage).
    """
    out = (data >> 8).astype(np.uint8)
    if out.ndim == 3:
        return np.ascontiguousarray(out.transpose(1, 2, 0))
    return np.ascontiguousarray(out)

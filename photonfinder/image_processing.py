"""
Astronomical image processing: loading, normalisation, debayering, and MTF stretch.
No Qt dependencies — pure numpy/OpenCV computation.

All internal pixel data is represented as uint16 [0, 65535].  Float32 is used only
for the statistics samples (sub-sampled, so small) and the LUT build step.
"""
import logging
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_BAYER_PATTERNS = {'RGGB', 'BGGR', 'GRBG', 'GBRG'}


class StretchParams:
    """MTF (black_point, midtone) parameters for one or three channels."""

    def __init__(self, channels: list[tuple[float, float]]):
        self.channels = channels

    def __getitem__(self, i: int) -> tuple[float, float]:
        return self.channels[i]

    def __len__(self) -> int:
        return len(self.channels)


# ---------------------------------------------------------------------------
# Image cache
# ---------------------------------------------------------------------------

def _ram_budget() -> int:
    """25 % of total system RAM, used as the image cache byte budget."""
    try:
        import psutil
        return int(psutil.virtual_memory().total * 0.25)
    except Exception as e:
        print(e)
        return 2 * 1024 ** 3  # 2 GB fallback


class _ImageCache:
    """Thread-safe LRU cache bounded by RAM (uncompressed bytes).

    Pixel arrays are stored LZ4-compressed to maximise the number of images
    that fit within the budget.  The budget is checked against uncompressed
    sizes so eviction decisions are free of extra decompression work.
    """

    # Store layout per entry: (compressed_bytes, dtype, shape, header, uncompressed_size)
    _store: OrderedDict[str, tuple[bytes, np.dtype, tuple, dict, int]]

    def __init__(self) -> None:
        self._store = OrderedDict()
        self._max_bytes: int = _ram_budget()
        self._total_bytes: int = 0
        self._lock = threading.Lock()

    def get(self, filename: str) -> tuple[np.ndarray, dict] | None:
        import zstd
        with self._lock:
            entry = self._store.get(filename)
            if entry is None:
                return None
            self._store.move_to_end(filename)
            compressed, dtype, shape, header, _ = entry
        raw = zstd.decompress(compressed)
        return np.frombuffer(raw, dtype=dtype).reshape(shape), header

    def put(self, filename: str, data: np.ndarray, header: dict) -> None:
        import zstd
        raw = np.ascontiguousarray(data).tobytes()
        compressed = zstd.compress(raw, 1)
        compressed_size = len(compressed)
        with self._lock:
            if filename in self._store:
                self._total_bytes -= self._store[filename][4]
                del self._store[filename]
            self._store[filename] = (compressed, data.dtype, data.shape, header, compressed_size)
            self._total_bytes += compressed_size
            logger.debug(
                "Cache put: %s (%.1f MB compressed) — total %.1f / %.1f MB",
                filename, compressed_size / 1024**2,
                self._total_bytes / 1024**2, self._max_bytes / 1024**2,
            )
            while self._total_bytes > self._max_bytes and self._store:
                evicted_key, evicted = self._store.popitem(last=False)
                self._total_bytes -= evicted[4]
                logger.debug(
                    "Cache evict: %s (%.1f MB) — total now %.1f MB",
                    evicted_key, evicted[4] / 1024**2, self._total_bytes / 1024**2,
                )

    def clear(self) -> None:
        with self._lock:
            count = len(self._store)
            total_mb = self._total_bytes / 1024**2
            self._store.clear()
            self._total_bytes = 0
        logger.debug("Cache cleared: %d entries, %.1f MB freed", count, total_mb)


image_cache = _ImageCache()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_image_data(filename: str | Path) -> tuple[np.ndarray, dict]:
    """Load pixel data and header dict from a FITS, compressed FITS, or XISF file.

    Results are cached; repeated loads of the same path are free.
    """
    from photonfinder.filesystem import Importer
    filename = str(filename)
    cached = image_cache.get(filename)
    if cached is not None:
        logger.debug("Cache hit: %s", filename)
        return cached
    if Importer.is_xisf_by_name(filename):
        result = _load_xisf(filename)
    else:
        result = _load_fits(filename)
    image_cache.put(filename, *result)
    return result


def prefetch_image(filename: str) -> None:
    """Load and cache an image in a background daemon thread if not already cached."""
    if image_cache.get(filename) is None:
        t = threading.Thread(target=load_image_data, args=(filename,), daemon=True)
        t.start()


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


_BRIGHT_GAP      = 0.05   # normalised offset above background to be "extended content"
_BRIGHT_FRACTION = 0.01   # if more than this fraction exceeds the threshold → not linear


def is_linear(data: np.ndarray) -> bool:
    """Heuristic: fraction of pixels significantly above background.

    Linear images have a sharp background peak with very few pixels beyond it
    (just stars, typically < 0.5 %).  Processed images have the same peak but
    substantially more pixels in the extended wings (galaxies, nebulae, general
    lifted sky).  This metric is unaffected by noise reduction, which collapses
    MAD without changing the tail fraction.

    Counts the fraction of pixels more than _BRIGHT_GAP (in normalised [0, 1]
    units) above the median background.  If that fraction exceeds _BRIGHT_FRACTION
    the image is considered already stretched.
    """
    flat = data.ravel()
    step = max(1, len(flat) // 50_000)
    sample = flat[::step].astype(np.float64)

    if data.dtype.kind in ('u', 'i'):
        sample /= 65535.0

    bg = float(np.median(sample))
    bright_fraction = float(np.mean(sample > bg + _BRIGHT_GAP))
    return bright_fraction < _BRIGHT_FRACTION


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


def compute_stretch_params(data: np.ndarray, target_bg: float, clip_sigma: float,
                            linked: bool) -> StretchParams:
    """Compute MTF (black, midtone) params without applying them.

    Returns a StretchParams with one channel for mono, or three for RGB.
    Pass the result to ``stretch_uint16_to_uint8`` as ``locked_params`` to
    reproduce the same stretch on a different image.
    """
    if data.ndim == 2:
        return StretchParams([compute_mtf_params(data, target_bg, clip_sigma)])
    if linked:
        p = compute_mtf_params(data[1], target_bg, clip_sigma)
        return StretchParams([p, p, p])
    return StretchParams([compute_mtf_params(data[i], target_bg, clip_sigma) for i in range(3)])


def stretch_uint16_to_uint8(data: np.ndarray, target_bg: float,
                             clip_sigma: float, linked: bool,
                             locked_params: StretchParams | None = None,
                             ) -> np.ndarray:
    """Stretch a uint16 image and produce a uint8 array suitable for QImage.

    Input:  (H, W) or (3, H, W) uint16.
    Output: (H, W) uint8 mono, or (H, W, 3) uint8 RGB (channels-last for QImage).

    If ``locked_params`` is supplied (from a previous ``compute_stretch_params``
    call) those params are used as-is instead of recomputing from ``data``.
    """
    if data.ndim == 2:
        p = locked_params[0] if locked_params else compute_mtf_params(data, target_bg, clip_sigma)
        return build_stretch_lut(*p)[data]

    # (3, H, W)
    if locked_params:
        if len(locked_params) >= 3:
            out = np.stack([build_stretch_lut(*locked_params[i])[data[i]] for i in range(3)], axis=0)
        else:
            # Mono params locked but image is now RGB — apply single param to all channels
            lut = build_stretch_lut(*locked_params[0])
            out = np.stack([lut[data[i]] for i in range(3)], axis=0)
    elif linked:
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

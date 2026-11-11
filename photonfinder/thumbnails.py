"""
Thumbnails of the images in the library, for the tile view of the search results.

A thumbnail goes through the image viewer's pipeline (`image_processing`): load, normalise to
uint16, debayer a Bayer-pattern image, auto-stretch it when it looks linear or is a flat
(`image_processing.should_stretch`), then shrink it. It uses fixed defaults rather than the viewer's
per-image controls: dust donuts and amp glow are worth seeing when browsing.

Thumbnails are cached as small JPEG files in a folder of the app-data directory (so a
``--data-dir`` sandbox keeps its own), named after a hash of the file's path, image (hdu),
modification time and size: a file that changes gets a new entry, and an old one is never
shown for it. The cache is not part of the library database and can always be regenerated.
No Qt dependencies.
"""
import hashlib
import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = 200
"""The longest side of a thumbnail, in pixels."""
JPEG_QUALITY = 85
RENDER_VERSION = 4
"""Part of the cache key: raise it when thumbnails come out differently, so cached ones are
made again. 2: the stretch is measured on the full image, as in the viewer. 3: flats are
always stretched. 4: the full image is stretched, then shrunk (a mono CFA mosaic came out black)."""

# The viewer's AutoSTF defaults, unlinked as the viewer starts.
_TARGET_BG = 0.25
_CLIP_SIGMA = 2.8
_LINKED = False


def file_stamp(path: str | Path) -> tuple[int, int]:
    """(mtime in ns, size) of the file; raises OSError when it can't be read."""
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size


def cache_key(path: str | Path, hdu: int | None, mtime_ns: int, size: int, image_type: str | None = None) -> str:
    """The cache file name (without extension) of one image of a file as it is now. The
    library's image type is part of it, since it decides whether the thumbnail is stretched."""
    text = (f"{os.path.normcase(os.path.abspath(path))}\0{hdu if hdu is not None else ''}\0{mtime_ns}\0{size}"
            f"\0{RENDER_VERSION}\0{image_type or ''}")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def render_thumbnail(path: str | Path, hdu: int | None = None, size: int = THUMBNAIL_SIZE,
                     image_type: str | None = None) -> np.ndarray:
    """The thumbnail of one image of a file: (H, W) uint8 mono or (H, W, 3) uint8 RGB, its
    longest side at most *size*, rows in the order the viewer shows them. *image_type* is the
    library's (`Image.image_type`): a flat is always stretched, as in the viewer.

    Raises whatever reading the file raises (a missing, unreadable or corrupt file)."""
    import cv2
    from photonfinder.image_hdus import load_hdu
    from photonfinder.image_processing import (
        image_cache, normalize_to_uint16, detect_image_type, detect_bayer_pattern, debayer_opencv,
        should_stretch, stretch_uint16_to_uint8, uint16_to_uint8,
    )

    path = str(path)
    # Not load_image_data: that would push every browsed image into the viewer's RAM cache.
    cached = image_cache.get(path, hdu)
    data, header = cached if cached is not None else load_hdu(path, hdu)
    if data.ndim == 3 and data.shape[0] == 1:
        data = data.squeeze(0)
    if data.ndim not in (2, 3) or data.size == 0:
        raise ValueError(f"Not an image: shape {data.shape}")

    data = normalize_to_uint16(data, header)
    if detect_image_type(data, header) == 'bayer':
        data = debayer_opencv(data, detect_bayer_pattern(header))
    elif data.ndim == 3 and data.shape[0] != 3:
        data = data[0]  # neither mono nor RGB: show the first plane

    # As in the viewer: only a flat, or what is_linear takes for linear data, is stretched, so a
    # processed image isn't stretched again. The full image is stretched, as the viewer shows it,
    # and the 8-bit result shrunk. Shrinking first would change the pixel values the stretch is
    # measured on or applied to: it averages the noise away, which narrows the background spread
    # the black point is clipped by, and it averages a mosaic (a CFA image with no Bayer pattern
    # in its header, shown as mono) to below the black point its green pixels set.
    if should_stretch(data, image_type):
        pixels = stretch_uint16_to_uint8(data, _TARGET_BG, _CLIP_SIGMA, _LINKED)
    else:
        pixels = uint16_to_uint8(data)
    return _shrink(pixels, size, cv2)


def _shrink(pixels: np.ndarray, size: int, cv2) -> np.ndarray:
    """Area-average (H, W) or (H, W, 3) uint8 pixels down so the longest side is at most *size*."""
    h, w = pixels.shape[:2]
    scale = size / max(h, w)
    if scale >= 1.0:
        return pixels
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return cv2.resize(pixels, new_size, interpolation=cv2.INTER_AREA)


def encode_jpeg(pixels: np.ndarray) -> bytes:
    """JPEG bytes of a render_thumbnail result."""
    import cv2
    if pixels.ndim == 3:
        pixels = cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
    ok, buffer = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buffer.tobytes()


class ThumbnailCache:
    """JPEG thumbnails on disk, one per image of a file as it is now; see the module docstring.

    With no folder nothing is cached and every thumbnail is rendered again."""

    def __init__(self, folder: str | Path | None):
        self.folder = Path(folder) if folder else None

    def file_for(self, key: str) -> Path | None:
        if self.folder is None:
            return None
        return self.folder / key[:2] / f"{key}.jpg"

    def get(self, key: str) -> bytes | None:
        cached = self.file_for(key)
        if cached is None:
            return None
        try:
            return cached.read_bytes()
        except OSError:
            return None

    def put(self, key: str, jpeg: bytes) -> None:
        target = self.file_for(key)
        if target is None:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Written aside and swapped in, so a reader never sees half a file.
            tmp = target.with_name(f"{target.stem}.{os.getpid()}.{id(jpeg)}.tmp")
            tmp.write_bytes(jpeg)
            os.replace(tmp, target)
        except OSError as e:
            logger.warning("Could not cache the thumbnail %s: %s", target, e)

    def _entries(self):
        """The cache's files: thumbnails and any half-written leftovers."""
        if self.folder is None or not self.folder.is_dir():
            return []
        return [f for pattern in ("*/*.jpg", "*/*.tmp") for f in self.folder.glob(pattern) if f.is_file()]

    def usage(self) -> tuple[int, int]:
        """(number of thumbnails, bytes on disk)."""
        count = size = 0
        for entry in self._entries():
            try:
                size += entry.stat().st_size
            except OSError:
                continue
            count += entry.suffix == ".jpg"
        return count, size

    def clear(self) -> tuple[int, int]:
        """Delete every cached thumbnail; returns (thumbnails deleted, bytes freed). Only the
        cache's own files go, and those in use elsewhere are skipped."""
        count = size = 0
        for entry in self._entries():
            try:
                entry_size = entry.stat().st_size
                entry.unlink()
            except OSError as e:
                logger.warning("Could not delete the thumbnail %s: %s", entry, e)
                continue
            size += entry_size
            count += entry.suffix == ".jpg"
        for sub in self.folder.iterdir() if self.folder is not None and self.folder.is_dir() else ():
            if sub.is_dir():
                try:
                    sub.rmdir()  # only when empty
                except OSError:
                    pass
        logger.info("Cleared the thumbnail cache: %d thumbnails, %.1f MB", count, size / 1024 ** 2)
        return count, size

    def thumbnail(self, path: str | Path, hdu: int | None = None, image_type: str | None = None) -> bytes:
        """JPEG bytes of the thumbnail of one image of a file, from the cache or rendered and
        cached. Raises when the file can't be read; nothing is cached then."""
        stamp = file_stamp(path)  # before reading: a file that changes meanwhile isn't cached as the old one
        key = cache_key(path, hdu, *stamp, image_type=image_type)
        cached = self.get(key)
        if cached is not None:
            return cached
        jpeg = encode_jpeg(render_thumbnail(path, hdu, image_type=image_type))
        self.put(key, jpeg)
        return jpeg

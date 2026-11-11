"""JPEG and PNG pictures: reference images and final results kept next to the data (processed
exports, DSLR or phone shots), not measurement data.

A library root imports them only when its ``include_pictures`` flag is set. A picture gets one
``Image`` row (hdu 0) with its size and the type ``PICTURE``, and nothing else: no header is
read or cached, no EXIF, no date. The size is read through Qt, from the file's header only.

Its pixels are decoded by `load_picture`, the one decode used to show and solve pictures:
exactly as stored, top row first. A plate solution of a picture refers to that array
(decoded row 0 is FITS y 1), and so does the viewer's annotation overlay.
"""
from pathlib import Path

import numpy as np

PICTURE_EXTENSIONS = (".jpg", ".jpeg", ".png")
"""The file name extensions of pictures, lower case. Adding a format is a change here (plus
checking that Qt and every solver read it)."""

PICTURE_TYPE = "PICTURE"
"""The ``Image.image_type`` of a picture."""


def is_picture_name(name: str) -> bool:
    """Whether a file name is a picture's, by its extension, in any case."""
    return name.lower().endswith(PICTURE_EXTENSIONS)


def extensions_text() -> str:
    """The extensions for the user to read, e.g. ".jpg, .jpeg, .png"."""
    return ", ".join(PICTURE_EXTENSIONS)


def picture_size(path: str | Path) -> tuple[int, int]:
    """(width, height) of a picture as stored, read from its header without decoding the pixels.
    Any EXIF orientation is ignored, as everywhere else for pictures. Works off the GUI thread and
    without a Qt application, and with non-ASCII paths.

    Raises ValueError when Qt can't read the file."""
    from PySide6.QtGui import QImageReader
    reader = QImageReader(str(path))
    reader.setAutoTransform(False)
    size = reader.size()
    if not size.isValid():
        raise ValueError(f"Can't read the size of {Path(path).name}: {reader.errorString()}")
    return size.width(), size.height()


def load_picture(path: str | Path) -> np.ndarray:
    """A picture's pixels in the layout `image_hdus.load_hdu` gives an image: (H, W) mono or
    (3, H, W) RGB, uint8 or uint16 as stored, top row first. Alpha is dropped and any EXIF
    orientation ignored, so the array is the file's own pixel grid: the one a plate solution of
    it refers to. Reads non-ASCII paths (cv2.imread can't on Windows).

    Raises ValueError when the file can't be decoded."""
    import cv2
    pixels = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
    if pixels is None:
        raise ValueError(f"Can't decode the picture {Path(path).name}")
    if pixels.ndim == 3:
        channels = pixels.shape[2]
        if channels == 4:
            pixels = cv2.cvtColor(pixels, cv2.COLOR_BGRA2RGB)
        elif channels == 3:
            pixels = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
        else:  # grey with alpha
            pixels = pixels[..., 0]
    if pixels.ndim == 3:
        pixels = np.ascontiguousarray(pixels.transpose(2, 0, 1))
    return pixels

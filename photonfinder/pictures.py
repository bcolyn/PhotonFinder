"""JPEG and PNG pictures: reference images and final results kept next to the data (processed
exports, DSLR or phone shots), not measurement data.

A library root imports them only when its ``include_pictures`` flag is set. A picture gets one
``Image`` row (hdu 0) with its size and the type ``PICTURE``, and nothing else: no header is
read or cached, no EXIF, no date. The size is read through Qt, from the file's header only.
"""
from pathlib import Path

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

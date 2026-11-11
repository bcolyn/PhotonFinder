"""
The images in a file: which HDUs of a FITS file (or which <Image>s of an XISF file) hold an
image, their headers, and their pixels.

A file can hold more than one image, for example a DWARFLAB dual-band stack, which has its SO
layer in an IMAGE extension next to the HO layer in the primary HDU. Every reader goes through
this module, so they agree on which image is which:

- `scan_hdus` / `list_image_hdus` read headers only. For plain FITS, the data units are skipped
  with a seek. For .gz/.bz2/.xz they have to be decompressed to get past them.
- `merged_header` combines an extension's sparse header with the primary's (FITS only).
- `load_hdu` loads one image's pixels with its header.

An image is addressed by its index: the HDU number in a FITS file, or the position in
`XISF.get_images_metadata()` in an XISF file. `None` means the first image.
XISF goes through the upstream `xisf` package only.
"""
import copy
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from astropy.io.fits import Card, Header

from photonfinder import pictures

logger = logging.getLogger(__name__)

FITS_BLOCK = 2880
_CARD = 80

# Cards that describe an HDU's own data layout or integrity. An extension never takes them from
# the primary header.
_STRUCTURAL = {'SIMPLE', 'EXTEND', 'BITPIX', 'NAXIS', 'BZERO', 'BSCALE', 'BLANK', 'PCOUNT', 'GCOUNT',
               'GROUPS', 'XTENSION', 'EXTNAME', 'EXTVER', 'EXTLEVEL', 'INHERIT', 'CHECKSUM', 'DATASUM',
               'END'}
_NAXIS_N = re.compile(r'^NAXIS\d+$')


@dataclass(frozen=True)
class HduInfo:
    """One image in a file."""
    index: int
    """HDU number (FITS) or position in get_images_metadata() (XISF)."""
    extname: Optional[str]
    """EXTNAME of a FITS extension, or the <Image id> of an XISF image other than the first."""
    shape: tuple
    """Numpy axis order of the data as read, before `load_image_data` squeezes a single plane:
    (rows, columns) or (planes, rows, columns). XISF always has the channel axis."""
    header: bytes
    """This image's own header: FITS header blocks (an extension's header is not merged), or
    the XISF image's FITS keywords as JSON (see `read_xisf_header`)."""
    keywords: Optional[dict] = field(default=None, compare=False)
    """XISF only: the FITS keywords as get_images_metadata() returns them."""


@dataclass(frozen=True)
class HduScan:
    primary_header: Optional[bytes]
    """FITS: the primary header's blocks, image or not. XISF: None."""
    images: list
    """The HduInfo of every image in the file, in file order."""


def has_end_card(block: bytes) -> bool:
    """Whether a card in this block is END. ENDTIME and the like are ordinary keywords."""
    return any(block[start:start + 8].rstrip(b" \0") == b"END" for start in range(0, len(block), _CARD))


def is_structural(keyword: str) -> bool:
    return keyword in _STRUCTURAL or bool(_NAXIS_N.match(keyword))


def merged_header(primary: Header, ext: Header) -> Header:
    """An extension's header completed with the primary's.

    Extension headers often hold only what differs from the primary (FILTER, EXPTIME), and rely
    on it for DATE-OBS, OBJECT, INSTRUME... with or without INHERIT = T. The result has the
    extension's structural cards, then the primary's other cards, overlaid with the extension's.
    For the primary itself it returns the primary unchanged.
    """
    if ext is primary:
        return primary
    # Copies: a Header holds the Card objects it is given, and overlaying a value on a shared
    # card would change the primary header too.
    result = Header()
    for card in ext.cards:
        if is_structural(card.keyword):
            result.append(copy.copy(card))
    for card in primary.cards:
        if not is_structural(card.keyword):
            result.append(copy.copy(card))
    for card in ext.cards:
        if is_structural(card.keyword):
            continue
        if card.keyword in Card._commentary_keywords:  # pylint: disable=protected-access
            result.append(copy.copy(card))
        elif card.keyword in result:
            result[card.keyword] = (card.value, card.comment)
        else:
            result.append(card)
    return result


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_hdus(path: str | Path) -> Optional[HduScan]:
    """The images in a FITS or XISF file, from the headers only. None if it can't be read."""
    from photonfinder.filesystem import Importer
    if Importer.is_xisf_by_name(str(path)):
        return scan_xisf(path)
    return scan_fits(path)


def list_image_hdus(path: str | Path) -> list[HduInfo]:
    scan = scan_hdus(path)
    return scan.images if scan else []


def first_image(scan: HduScan) -> Optional[HduInfo]:
    return scan.images[0] if scan.images else None


def _card_value(card: bytes) -> Optional[str]:
    """The value of a `KEY     = value / comment` card as text, strings unquoted."""
    if card[8:10] != b'= ':
        return None
    text = card[10:].decode('ascii', errors='replace').strip()
    if text.startswith("'"):
        # '' is an escaped quote inside a string
        match = re.match(r"'((?:[^']|'')*)'", text)
        return match.group(1).replace("''", "'").rstrip() if match else None
    return text.split('/', 1)[0].strip()


def _parse_layout(header: bytes) -> dict:
    """The few cards needed to find the data unit and decide whether it is an image."""
    wanted = {'SIMPLE', 'XTENSION', 'BITPIX', 'NAXIS', 'PCOUNT', 'GCOUNT', 'GROUPS', 'EXTNAME', 'EXTEND'}
    cards = {}
    for start in range(0, len(header), _CARD):
        card = header[start:start + _CARD]
        key = card[:8].decode('ascii', errors='replace').strip()
        if key == 'END':
            break
        if (key in wanted or _NAXIS_N.match(key)) and key not in cards:
            value = _card_value(card)
            if value is not None:
                cards[key] = value
    return cards


def _int(cards: dict, key: str, default: int = 0) -> int:
    try:
        return int(float(cards.get(key, default)))
    except ValueError:
        return default


def _axes(cards: dict) -> list[int]:
    """NAXIS1..NAXISn, in FITS order."""
    return [_int(cards, f'NAXIS{n}') for n in range(1, _int(cards, 'NAXIS') + 1)]


def _data_size(cards: dict) -> int:
    """Bytes in the data unit, padding included (FITS standard 4.4.1)."""
    axes = _axes(cards)
    if not axes:
        return 0
    if 'XTENSION' not in cards and axes[0] == 0 and cards.get('GROUPS') == 'T':
        axes = axes[1:]  # random groups: NAXIS1 = 0 doesn't count
    bits = abs(_int(cards, 'BITPIX', 8)) * _int(cards, 'GCOUNT', 1) * (_int(cards, 'PCOUNT') + math.prod(axes))
    size = bits // 8
    return (size + FITS_BLOCK - 1) // FITS_BLOCK * FITS_BLOCK


def _is_image(cards: dict) -> bool:
    """A primary or IMAGE extension with at least two axes longer than 1. Tables, empty
    primaries and random groups aren't images."""
    xtension = cards.get('XTENSION')
    if xtension is not None and xtension.strip() != 'IMAGE':
        return False
    if xtension is None and cards.get('GROUPS') == 'T':
        return False
    return sum(1 for n in _axes(cards) if n > 1) >= 2


def is_image_header(header: Header) -> bool:
    """Whether a parsed FITS header describes an image HDU, by the same rule as the scan."""
    cards = {key: ('T' if value is True else 'F' if value is False else str(value))
             for key, value in header.items()
             if key in ('XTENSION', 'GROUPS', 'NAXIS') or _NAXIS_N.match(key)}
    return _is_image(cards)


def _read_header_blocks(f, first_card: bytes) -> Optional[bytes]:
    """Header blocks up to and including the one with END; None at EOF or if the first card
    doesn't start with *first_card*."""
    header = bytearray()
    while True:
        block = f.read(FITS_BLOCK)
        if not block:
            return None
        if not header and not block.startswith(first_card):
            return None
        header += block
        if has_end_card(block):
            return bytes(header)


def scan_fits(path, first_only: bool = False, require_extend: bool = False) -> Optional[HduScan]:
    """The image HDUs of a FITS file. With *first_only*, the scan stops at the first image HDU,
    so a file whose primary holds the image is read no further than its primary header. With
    *require_extend*, it doesn't look past the primary unless that has EXTEND = T: by convention
    only such a file has extensions, and for a compressed file looking further means
    decompressing all of it."""
    from photonfinder.filesystem import fopen
    images = []
    try:
        with fopen(path) as f:
            primary = _read_header_blocks(f, b'SIMPLE  =')
            if primary is None:
                logger.error("Cannot read a FITS header from %s", path)
                return None
            header, index = primary, 0
            while header is not None:
                cards = _parse_layout(header)
                if _is_image(cards):
                    extname = cards.get('EXTNAME') if index > 0 else None
                    images.append(HduInfo(index, extname or None, tuple(reversed(_axes(cards))), header))
                    if first_only:
                        break
                if index == 0 and require_extend and cards.get('EXTEND') != 'T':
                    break
                # Seeking a gz/bz2/xz stream forward decompresses through the data. Past the end
                # of a plain file, the next read returns nothing, which ends the scan.
                f.seek(_data_size(cards), 1)
                try:
                    header = _read_header_blocks(f, b'XTENSION=')
                except EOFError:  # a truncated compressed stream
                    header = None
                index += 1
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("Error scanning FITS HDUs of %s: %s", path, e)
        return None
    return HduScan(primary, images)


def _xisf_keywords(meta: dict) -> dict:
    """The image's FITS keywords, with NAXISn added from its geometry when they're missing."""
    keywords = meta.setdefault("FITSKeywords", {})
    geometry = meta.get("geometry", ())
    # geometry is dim1:...:dimN:channel-count, e.g. (8288, 5644, 1)
    channels = geometry[-1]
    if 'NAXIS' not in keywords:
        keywords['NAXIS'] = [{'value': '3' if channels > 1 else '2', 'comment': ''}]
    if 'NAXIS1' not in keywords:
        keywords['NAXIS1'] = [{'value': str(geometry[0]), 'comment': ''}]
    if 'NAXIS2' not in keywords:
        keywords['NAXIS2'] = [{'value': str(geometry[1]), 'comment': ''}]
    if channels > 1 and 'NAXIS3' not in keywords:
        keywords['NAXIS3'] = [{'value': str(channels), 'comment': ''}]
    return keywords


def _xisf_image_indexes(metas: list[dict]) -> list[int]:
    """The <Image>s that count as images (spec D7): at least two spatial dimensions and FITS
    keywords. If none has keywords, the first image alone, so a keyword-less file is still
    indexed and viewed as it was before. The xisf package gives every image a FITSKeywords
    dict, empty when the image has none."""
    images = [i for i, meta in enumerate(metas) if len(meta.get("geometry", ())) >= 3]
    with_keywords = [i for i in images if metas[i].get("FITSKeywords")]
    return with_keywords or images[:1]


def scan_xisf(path) -> Optional[HduScan]:
    from xisf import XISF
    try:
        metas = XISF(str(path)).get_images_metadata()
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Error reading XISF header from %s: %s", path, e)
        return None
    images = []
    for index in _xisf_image_indexes(metas):
        meta = metas[index]
        keywords = _xisf_keywords(meta)
        *dims, channels = meta["geometry"]
        shape = (channels, *reversed(dims))  # as read_image(n, 'channels_first') returns it
        extname = meta.get("id") if images else None
        images.append(HduInfo(index, extname, shape, json.dumps(keywords).encode(), keywords))
    return HduScan(None, images)


# ---------------------------------------------------------------------------
# Loading pixels
# ---------------------------------------------------------------------------

def load_hdu(path: str | Path, hdu: Optional[int] = None) -> tuple[np.ndarray, dict]:
    """Pixels and header of one image; *hdu* None is the first image. Not cached: callers go
    through `image_processing.load_image_data`. A FITS extension's header is merged with the
    primary's; an XISF header has each keyword's first value, as text. A JPEG or PNG picture
    is its decoded pixels (`pictures.load_picture`) with an empty header."""
    from photonfinder.filesystem import Importer
    if pictures.is_picture_name(str(path)):
        return _load_picture(path, hdu), {}
    if Importer.is_xisf_by_name(str(path)):
        data, keywords = _read_xisf(str(path), hdu)
        return data, {key: entries[0].get('value', '') for key, entries in keywords.items() if entries}
    data, header = _read_fits(str(path), hdu)
    return data, dict(header)


def read_hdu(path: str | Path, hdu: Optional[int] = None) -> tuple[np.ndarray, Header]:
    """Pixels and header of one image as `load_hdu` gives them, the header as an astropy Header,
    for writing the image out again (plate solver input). Not cached."""
    from photonfinder.filesystem import Importer, header_from_xisf_dict
    if pictures.is_picture_name(str(path)):
        return _load_picture(path, hdu), Header()
    if Importer.is_xisf_by_name(str(path)):
        data, keywords = _read_xisf(str(path), hdu)
        return data, header_from_xisf_dict(keywords)
    return _read_fits(str(path), hdu)


def _load_picture(path, hdu: Optional[int]) -> np.ndarray:
    if hdu not in (None, 0):
        raise ValueError(f"A picture holds one image; there is no image {hdu} in {Path(path).name}")
    return pictures.load_picture(path)


def _is_image_hdu(hdu) -> bool:
    from astropy.io import fits
    if not isinstance(hdu, (fits.PrimaryHDU, fits.ImageHDU)):
        return False
    return sum(1 for n in hdu.shape if n > 1) >= 2


def _pick_fits_hdu(hdul, hdu: Optional[int]):
    if hdu is not None:
        return hdul[hdu]
    return next((h for h in hdul if _is_image_hdu(h)), hdul[0])


def _fits_header(hdul, chosen) -> Header:
    from photonfinder.filesystem import repair_header
    repair_header(chosen.header)
    if chosen is hdul[0]:
        return chosen.header
    repair_header(hdul[0].header)
    return merged_header(hdul[0].header, chosen.header)


def _read_fits(filename: str, hdu: Optional[int]) -> tuple[np.ndarray, Header]:
    import time
    from astropy.io import fits

    suffix = Path(filename).suffix.lower()
    t0 = time.perf_counter()

    # astropy handles .gz natively; only bz2/xz need fopen()
    if suffix in ('.bz2', '.xz'):
        from photonfinder.filesystem import fopen
        with fopen(filename) as f:
            with fits.open(f, memmap=False) as hdul:
                chosen = _pick_fits_hdu(hdul, hdu)
                data = chosen.data  # memmap=False: already in RAM, no copy needed
                header = _fits_header(hdul, chosen)
    else:
        with fits.open(filename) as hdul:  # memmap=True by default
            chosen = _pick_fits_hdu(hdul, hdu)
            data = None if chosen.data is None else chosen.data.copy()
            header = _fits_header(hdul, chosen)
    logger.debug("FITS load (%s, hdu %s): %.0fms", suffix, hdu, (time.perf_counter() - t0) * 1000)

    if data is None:
        raise ValueError(f"No image data in {filename}")
    return data, header


def _read_xisf(filename: str, hdu: Optional[int]) -> tuple[np.ndarray, dict]:
    """Pixels (channels first) and FITS keywords of one XISF image."""
    from xisf import XISF
    xf = XISF(filename)
    metas = xf.get_images_metadata()
    if not metas:
        raise ValueError(f"No images in {filename}")
    if hdu is None:
        # The same first image read_xisf_header takes the header from
        hdu = next(iter(_xisf_image_indexes(metas)), 0)
    return xf.read_image(hdu, 'channels_first'), metas[hdu].get('FITSKeywords', {})

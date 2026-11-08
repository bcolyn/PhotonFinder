"""Synthetic files with more than one image, for the multi-image tests."""
import struct

import numpy as np
from astropy.io import fits

PRIMARY_SHAPE = (3, 8, 10)


def write_multi_hdu_fits(path, empty_primary=False, extension_shape=PRIMARY_SHAPE, compress=None,
                         table=True, extend=True, primary_cards=None) -> str:
    """The DWARFLAB dual-band shape: a primary with a full header (FILTER HO) and an IMAGE
    extension "SOO" with a sparse header (FILTER SO), followed by a binary table "STARS".

    *compress* is None, or '.gz', '.bz2' or '.xz' to append to the name and compress with.
    *extend* False writes EXTEND = F in the primary (astropy always writes T), as some writers
    do. *primary_cards* are added to the primary header. Returns the path written."""
    rng = np.random.default_rng(1)
    primary_data = None if empty_primary else rng.integers(0, 65535, PRIMARY_SHAPE, dtype=np.uint16)
    primary = fits.PrimaryHDU(data=primary_data)
    primary.header.update({"FILTER": "HO", "EXPTIME": 6000, "OBJECT": "C 20", "DATE-OBS": "2026-11-02T20:00:00",
                           "IMAGETYP": "LIGHT", "INSTRUME": "Draco"})
    primary.header.update(primary_cards or {})
    soo = fits.ImageHDU(data=rng.integers(0, 65535, extension_shape, dtype=np.uint16), name="SOO")
    soo.header.update({"FILTER": "SO", "EXPTIME": 11700, "STACKCNT": 39})
    hdus = [primary, soo]
    if table:
        hdus.append(fits.BinTableHDU.from_columns([fits.Column(name="x", format="E", array=np.arange(3.0))],
                                                  name="STARS"))
    plain = str(path)
    fits.HDUList(hdus).writeto(plain)
    if not extend:
        raw = open(plain, "rb").read()
        card = b"EXTEND  =                    T"
        assert raw.count(card) == 1
        with open(plain, "wb") as f:
            f.write(raw.replace(card, b"EXTEND  =                    F"))
    if compress is None:
        return plain
    import bz2
    import gzip
    import lzma
    opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[compress]
    with open(plain, "rb") as src, opener(plain + compress, "wb") as dst:
        dst.write(src.read())
    return plain + compress


def write_two_image_xisf(path, first_keywords=True):
    """XISF with two images: 'image' (FILTER HO, or no keywords at all) and 'SOO' (FILTER SO),
    with different pixels. Returns both pixel arrays as written (height, width, 1).

    Test-only: the xisf package writes one image per file, so the second <Image> element is
    spliced into its header by hand. Replace this with the package once it writes several
    images (.scratch/multi-image-fits/issues/07); production code must never do this."""
    import xisf
    first = np.arange(12, dtype=np.uint16).reshape(3, 4, 1)
    second = (np.arange(12, dtype=np.uint16) + 100).reshape(3, 4, 1)
    metadata = {"FITSKeywords": {"FILTER": [{"value": "HO", "comment": ""}]}} if first_keywords else {}
    xisf.XISF.write(str(path), first, image_metadata=metadata)
    raw = bytearray(path.read_bytes())
    header_len = struct.unpack("<I", raw[8:12])[0]
    xml = raw[16:16 + header_len].decode("utf-8")
    offset = len(raw)
    second_image = (f'<Image id="SOO" geometry="4:3:1" colorSpace="Gray" pixelStorage="Planar" '
                    f'sampleFormat="UInt16" location="attachment:{offset}:{second.nbytes}">'
                    f'<FITSKeyword name="FILTER" value="SO" comment="" /></Image>')
    xml = xml.replace("<Metadata>", second_image + "<Metadata>", 1)
    encoded = xml.encode("utf-8")
    assert 16 + len(encoded) <= 4096, "header must still fit before the first attachment"
    raw[8:12] = struct.pack("<I", len(encoded))
    raw[16:16 + len(encoded)] = encoded
    raw += second.tobytes()
    path.write_bytes(bytes(raw))
    return first, second

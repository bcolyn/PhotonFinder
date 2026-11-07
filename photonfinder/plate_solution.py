"""Plate solution: a file's stored plate solution (FileWCS) together with the Image columns
derived from it.

While a solution is stored, the Image centre (coord_ra, coord_dec, coord_pix256) is the solved
centre and coord_radius is the half-diagonal of the solved field, so coord_scale (generated from
the radius and the image dimensions) is known. Without a solution the centre is the pointing
position from the file's header, if it has one, and radius and scale are NULL. This module is
the one place that changes these together.
"""
from typing import Optional

from photonfinder.fits_handlers import normalize_fits_header
from photonfinder.models import File, FileWCS, FitsHeader, Image


def clear(file: File) -> tuple[Optional[float], Optional[float]]:
    """Remove the file's plate solution. Its Image centre goes back to the header's pointing
    position (NULL when the header has none) and its radius, and so its scale, to NULL.
    Returns the centre the Image has now, as (ra, dec) in degrees."""
    ra, dec, healpix = _pointing(file)
    with FileWCS._meta.database.atomic():
        FileWCS.delete().where(FileWCS.file == file).execute()
        Image.update(coord_ra=ra, coord_dec=dec, coord_pix256=healpix, coord_radius=None) \
            .where(Image.file == file).execute()
    return ra, dec


def _pointing(file: File) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """The centre the file's header gives: where the mount pointed, not where it solved."""
    record = FitsHeader.get_or_none(FitsHeader.file == file)
    if record is None:
        return None, None, None
    from photonfinder.filesystem import decode_header_blob  # filesystem stores solutions through this module
    image = normalize_fits_header(file, decode_header_blob(record.header))
    if image is None:
        return None, None, None
    return image.coord_ra, image.coord_dec, image.coord_pix256

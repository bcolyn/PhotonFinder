"""Plate solution: a file's stored plate solution (FileWCS) together with the Image columns
derived from it.

While a solution is stored, the Image centre (coord_ra, coord_dec, coord_pix256) is the solved
centre and coord_radius is the half-diagonal of the solved field, so coord_scale (generated from
the radius and the image dimensions) is known. Without a solution the centre is the pointing
position from the file's header, if it has one, and radius and scale are NULL. This module is
the one place that changes these together.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from astropy.coordinates import SkyCoord
from astropy.io.fits import Header
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from photonfinder.core import compress, decompress, hp
from photonfinder.fits_handlers import normalize_fits_header
from photonfinder.models import File, FileWCS, FitsHeader, Image

logger = logging.getLogger(__name__)

_ORIGIN_PREFIX = "WCS_ORIGIN="


@dataclass(frozen=True)
class StoredSolution:
    """What storing a solution gave the file's Image. `image_updated` is False when the file
    has no Image row: the solution is stored, but there is nowhere to put its centre."""
    ra: float
    dec: float
    radius: Optional[float]
    scale_arcsec: float
    width: Optional[int]
    height: Optional[int]
    image_updated: bool


@dataclass
class CopyResult:
    copied: list = field(default_factory=list)
    already_solved: list = field(default_factory=list)  # kept their own solution: no overwrite asked
    other_dimensions: list = field(default_factory=list)  # a solution for another frame size won't fit


def stamp_origin(solution: Header, origin: str) -> None:
    """Add a COMMENT card recording how the solution was obtained."""
    solution.add_comment(f"{_ORIGIN_PREFIX}{origin}")


def origin_of(solution: Header) -> Optional[str]:
    """How the solution was obtained, as stamped; the last stamp when there are several."""
    stamps = [str(c) for c in solution.get('COMMENT', []) if str(c).startswith(_ORIGIN_PREFIX)]
    return stamps[-1][len(_ORIGIN_PREFIX):] if stamps else None


class SolvedField:
    """A stored solution ready to use: its WCS, with the image dimensions resolved (the
    solution's NAXIS1/NAXIS2, else the ones given), so the field's footprint is known. Without
    dimensions only the reference point and the pixel scale are."""

    def __init__(self, solution: Header, width: Optional[int] = None, height: Optional[int] = None):
        missing = [k for k in ('CTYPE1', 'CTYPE2', 'CRVAL1', 'CRVAL2', 'CRPIX1', 'CRPIX2') if k not in solution]
        if missing:
            raise ValueError(f"Not a usable plate solution, it has no {', '.join(missing)}")
        self.header = solution
        self.width = int(solution.get('NAXIS1') or width or 0) or None
        self.height = int(solution.get('NAXIS2') or height or 0) or None
        self.wcs = WCS(solution)
        if self.has_size:
            self.wcs.array_shape = (self.height, self.width)

    @property
    def has_size(self) -> bool:
        return bool(self.width and self.height)

    def centre(self) -> SkyCoord:
        """The centre of the image; the reference point when its size isn't known."""
        if not self.has_size:
            return SkyCoord(self.header['CRVAL1'], self.header['CRVAL2'], unit='deg', frame='icrs')
        return self.wcs.pixel_to_world((self.width - 1) / 2.0, (self.height - 1) / 2.0)

    def radius(self) -> Optional[float]:
        """Degrees from the centre to the furthest corner; None when the size isn't known."""
        if not self.has_size:
            return None
        w, h = self.width, self.height
        corners = self.wcs.pixel_to_world([0, w - 1, 0, w - 1], [0, 0, h - 1, h - 1])
        return float(self.centre().separation(corners).max().deg)

    def pixel_scale_arcsec(self) -> float:
        return float(proj_plane_pixel_scales(self.wcs)[0]) * 3600

    def contains(self, ra, dec):
        """For each (ra, dec) in degrees (scalars or arrays), whether it falls on the image.
        Needs the size: raises ValueError without it."""
        import numpy as np
        if not self.has_size:
            raise ValueError("The image size isn't known, so neither is its footprint")
        ra, dec = np.atleast_1d(ra).astype(float), np.atleast_1d(dec).astype(float)
        with np.errstate(invalid='ignore'):
            x, y = self.wcs.all_world2pix(ra, dec, 0, quiet=True)
            # Pixel centres run from 0 to size - 1; the pixels' edges reach half a pixel further.
            return (x >= -0.5) & (x < self.width - 0.5) & (y >= -0.5) & (y < self.height - 0.5)


def field_of(file: File, width: Optional[int] = None, height: Optional[int] = None) -> Optional[SolvedField]:
    """The file's stored solution as a SolvedField, or None when it has none. The image size
    comes from the solution, else from `width`/`height`, else from the file's Image row."""
    solution = load(file)
    if solution is None:
        return None
    if not (width and height):
        image = Image.get_or_none(Image.file == file)
        width, height = (image.width, image.height) if image else (None, None)
    return SolvedField(solution, width, height)


def fields_of(file_ids) -> dict:
    """{file id: SolvedField} for each of the files that has a solution, sized as `field_of`
    sizes it; None for one whose solution can't be used. Read in a few queries, for reports
    that look at many files."""
    from peewee import JOIN, chunked
    result = {}
    for chunk in chunked(list(file_ids), 500):
        rows = list(FileWCS.select(FileWCS.file, FileWCS.wcs, Image.width, Image.height)
                    .join(Image, JOIN.LEFT_OUTER, on=(Image.file == FileWCS.file))
                    .where(FileWCS.file.in_(chunk)).tuples())
        for file_id, blob, width, height in rows:
            try:
                result[file_id] = SolvedField(decode(blob), width, height)
            except Exception as e:  # one bad solution must not take the report down
                logger.warning("Unusable plate solution for file %s: %s", file_id, e)
                result[file_id] = None
    return result


def image_centre(solution: Header, width: Optional[int] = None, height: Optional[int] = None):
    """(ra, dec, healpix, radius) of the solved field, in degrees. The dimensions come from the
    solution's NAXIS1/NAXIS2, else from `width`/`height`; without either the centre is the
    reference point and the radius is None."""
    solved = SolvedField(solution, width, height)
    centre = solved.centre()
    return float(centre.ra.deg), float(centre.dec.deg), int(hp.skycoord_to_healpix(centre)), solved.radius()


def store(file: File, solution: Header, origin: str) -> StoredSolution:
    """Store `solution` as the file's plate solution, replacing any it had, stamped with
    `origin`. The Image gets the solved centre and radius, and the solution's dimensions when
    it has them. Nothing is written when the solution can't be used."""
    image = Image.get_or_none(Image.file == file)
    solution = solution.copy()
    stamp_origin(solution, origin)
    # Everything that can fail on the solution comes before the first write, so that a failure
    # keeps whatever solution the file already had.
    values = _derived_values(solution, image and image.width, image and image.height)
    scale_arcsec = round(SolvedField(solution).pixel_scale_arcsec(), 3)
    with FileWCS._meta.database.atomic():
        FileWCS.insert(file=file, wcs=_encode(solution)).on_conflict_replace().execute()
        updated = Image.update(**values).where(Image.file == file).execute() > 0
    _update_loaded(file, values)
    return StoredSolution(ra=values['coord_ra'], dec=values['coord_dec'], radius=values['coord_radius'],
                          scale_arcsec=scale_arcsec,
                          width=values.get('width', image and image.width),
                          height=values.get('height', image and image.height),
                          image_updated=updated)


def place(image: Image, solution: Header) -> None:
    """Give an Image that is not saved yet the centre, radius and dimensions of `solution`, as
    storing it would. Raises ValueError when the solution can't be used."""
    for name, value in _derived_values(solution, image.width, image.height).items():
        setattr(image, name, value)


def save_indexed(file: File, images: list, embedded: Optional[Header]) -> None:
    """Write the Image rows an index built from the file's headers, each replacing the row of
    its (file, hdu), and the solution a header carried when the file had none stored (stamped as
    imported). The images have had the solution placed on them, by `place`, so they agree."""
    with FileWCS._meta.database.atomic():
        if embedded is not None:
            embedded = embedded.copy()
            stamp_origin(embedded, "IMPORT")
            FileWCS.insert(file=file, wcs=_encode(embedded)).on_conflict_ignore().execute()
        for image in images:
            Image.insert(image.__data__).on_conflict_replace().execute()


def decode(blob: bytes) -> Header:
    """A stored solution, from its FileWCS.wcs blob. The one place a stored solution is read."""
    return Header.fromstring(decompress(blob))


def _encode(solution: Header) -> bytes:
    return compress(solution.tostring().encode())


def _derived_values(solution: Header, width: Optional[int], height: Optional[int]) -> dict:
    ra, dec, healpix, radius = image_centre(solution, width, height)
    values = dict(coord_ra=ra, coord_dec=dec, coord_pix256=healpix, coord_radius=radius)
    naxis1, naxis2 = solution.get('NAXIS1'), solution.get('NAXIS2')
    if naxis1 and naxis2:
        values.update(width=int(naxis1), height=int(naxis2))
    return values


def load(file: File) -> Optional[Header]:
    """The file's stored solution, or None when it has none."""
    record = FileWCS.get_or_none(FileWCS.file == file)
    return decode(record.wcs) if record else None


def copy(source: File, targets: list, overwrite: bool) -> CopyResult:
    """Give each target the source's solution, as a solution of its own: centre, radius and
    scale are worked out for it the way a solve would. A target that has a solution keeps it
    unless `overwrite`; one whose known dimensions differ from the source's is left alone."""
    result = CopyResult()
    solution = load(source)
    if solution is None:
        return result
    source_image = Image.get_or_none(Image.file == source)
    source_size = (solution.get('NAXIS1') or (source_image and source_image.width),
                   solution.get('NAXIS2') or (source_image and source_image.height))
    if not solution.get('NAXIS1') and all(source_size):
        solution['NAXIS1'], solution['NAXIS2'] = source_size
    source_origin = origin_of(solution)
    origin = f"COPY:{source.name}" + (f"/{source_origin}" if source_origin else "")
    solution = Header([c for c in solution.cards
                       if not (c.keyword == 'COMMENT' and str(c.value).startswith(_ORIGIN_PREFIX))])
    solved = {row.file_id for row in FileWCS.select(FileWCS.file).where(  # pylint: disable=not-an-iterable  # Peewee query
        FileWCS.file.in_([t.rowid for t in targets]))}
    with FileWCS._meta.database.atomic():
        for target in targets:
            if target.rowid in solved and not overwrite:
                result.already_solved.append(target)
                continue
            image = Image.get_or_none(Image.file == target)
            if image and image.width and image.height and all(source_size) \
                    and (image.width, image.height) != source_size:
                result.other_dimensions.append(target)
                continue
            store(target, solution, origin)
            result.copied.append(target)
    return result


def clear(file: File) -> tuple[Optional[float], Optional[float]]:
    """Remove the file's plate solution. Its Image centre goes back to the header's pointing
    position (NULL when the header has none) and its radius, and so its scale, to NULL.
    Returns the centre the Image has now, as (ra, dec) in degrees."""
    ra, dec, healpix = _pointing(file)
    values = dict(coord_ra=ra, coord_dec=dec, coord_pix256=healpix, coord_radius=None)
    with FileWCS._meta.database.atomic():
        FileWCS.delete().where(FileWCS.file == file).execute()
        Image.update(**values).where(Image.file == file).execute()
    _update_loaded(file, values, solved=False)
    return ra, dec


def _update_loaded(file: File, values: dict, solved: bool = True) -> None:
    """Keep a File loaded with its Image (as the search grid holds them) in step with the rows."""
    file.has_wcs = solved
    image = file.__dict__.get('image')
    if isinstance(image, Image):
        for name, value in values.items():
            setattr(image, name, value)
        radius, width, height = image.coord_radius, image.width, image.height
        image.coord_scale = round(radius * 2.0 * 3600.0 / (width * width + height * height) ** 0.5, 2)             if radius is not None and width and height else None  # as the generated column has it


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

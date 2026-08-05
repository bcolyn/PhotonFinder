"""Report queries shared by the Qt report windows and the MCP server.

Free of the UI layer by design: ``photonfinder/ui/`` and ``mcp_server.py`` both import
this module, never the reverse. Nothing here creates a widget, touches a QObject or emits
a signal, so every function is callable from a plain test or an MCP worker thread.
(``photonfinder.core`` does pull in PySide6 for QSettings, so this is about the widget
layer, not about the import graph being literally Qt-free.)

Callers are responsible for binding the models. The Qt loaders already run inside
``BackgroundLoaderBase``'s ``bind_ctx(CORE_MODELS)`` and the MCP query layer opens its
own, so nothing here opens one.

Progress is reported through a plain callable rather than a Qt signal; the report windows
pass their bound ``Signal.emit``, so no Qt type crosses the boundary.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from astropy.io.fits import Header
from peewee import JOIN, fn

from photonfinder.core import decompress
from photonfinder.filesystem import decode_header_blob
from photonfinder.models import (
    CatalogEntry, File, FileWCS, FitsHeader, Image, LibraryRoot, SearchCriteria,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


def _noop_progress(done: int, total: int) -> None:
    pass


def iso_datetime(value) -> Optional[str]:
    """Normalize a date_obs value to ISO-8601.

    Aggregates read through ``.tuples()`` bypass peewee's field converter, so a
    ``MAX(date_obs)`` comes back as raw SQLite text rather than a ``datetime``.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return datetime.fromisoformat(str(value)).isoformat()
    except ValueError:
        return str(value)


# --- Target report ------------------------------------------------------------------

@dataclass(frozen=True)
class TargetReportRow:
    object_name: str
    filter: Optional[str]
    telescope: Optional[str]
    camera: Optional[str]
    total_exposure: Optional[float]
    last_date_obs: Optional[str]   # raw SQLite text; run through iso_datetime to normalize
    paths: list
    file_count: int


def target_report(criteria: SearchCriteria) -> list:
    """Total integration per (object, filter, telescope, camera).

    SELECT
      image.object_name, image.filter, image.telescope, image.camera,
      SUM(image.exposure) as total_exposure,
      max(image.date_obs) as last_date,
      rtrim(replace(group_concat(DISTINCT file.path||':'), ':,', char(10)), ':') AS paths,
      count(file.rowid) as file_count
    FROM file
    JOIN image ON file.rowid = image.file_id
    WHERE image.object_name IS NOT NULL
      AND image.object_name <> ""
    GROUP BY
      image.object_name, image.filter, image.telescope, image.camera
    ORDER BY
      LOWER(image.object_name), image.filter;
    """
    query = (File.select(Image.object_name,
                         Image.filter,
                         Image.telescope,
                         Image.camera,
                         fn.SUM(Image.exposure),
                         fn.MAX(Image.date_obs),
                         fn.RTRIM(fn.REPLACE(fn.GROUP_CONCAT(fn.DISTINCT(File.path + ":")), ":,", "\n"), ":"),
                         fn.COUNT(File.rowid))
             .join_from(File, Image))
    query = Image.apply_search_criteria(query, criteria)
    query = (query
             .where(Image.object_name.is_null(False))
             .where(Image.object_name != "")
             .group_by(Image.object_name,
                       Image.filter,
                       Image.telescope,
                       Image.camera)
             .order_by(fn.LOWER(Image.object_name).asc(), Image.filter.asc()))

    rows = []
    for (object_name, filter_, telescope, camera,
         total_exposure, last_date_obs, paths, file_count) in query.tuples():
        rows.append(TargetReportRow(
            object_name=object_name,
            filter=filter_,
            telescope=telescope,
            camera=camera,
            total_exposure=total_exposure,
            last_date_obs=last_date_obs,
            # GROUP_CONCAT hands back one newline-joined string; the ":" suffix per path
            # is the delimiter trick that lets the SQL survive paths containing commas.
            paths=[p for p in (paths or "").split("\n") if p] if paths is not None else [],
            file_count=file_count or 0,
        ))
    return rows


# --- Catalog coverage report --------------------------------------------------------

class MatchedFile(NamedTuple):
    object_name: str
    full_path: str
    root_id: int
    root_name: str
    file_dir: str
    file_name: str
    file_id: int


@dataclass(frozen=True)
class CatalogReportEntry:
    rowid: int
    catalog_id: str
    magnitude: Optional[float]
    size: Optional[float]


@dataclass(frozen=True)
class CatalogReportResult:
    entries: list
    matches: dict            # catalog entry rowid -> list[MatchedFile]
    only_matching: bool
    total_entries: int       # ignoring limit/offset
    images_considered: int   # plate-solved images the criteria matched


def _catalog_entries(catalog: str, limit: Optional[int], offset: int,
                     rowids: Optional[list] = None) -> list:
    query = (CatalogEntry.select(CatalogEntry.rowid, CatalogEntry.catalog_id,
                                 CatalogEntry.magnitude, CatalogEntry.size)
             .order_by(CatalogEntry.catalog_id.cast('INTEGER'), CatalogEntry.catalog_id))
    query = query.where(CatalogEntry.rowid.in_(rowids)) if rowids is not None \
        else query.where(CatalogEntry.catalog == catalog)
    if limit is not None:
        query = query.limit(limit).offset(offset)
    return [CatalogReportEntry(rowid=r.rowid, catalog_id=r.catalog_id,
                               magnitude=r.magnitude, size=r.size)
            for r in query.namedtuples()]


def catalog_report(context, catalog: str, criteria: SearchCriteria,
                   only_matching: bool = False,
                   progress: ProgressCallback = _noop_progress,
                   limit: Optional[int] = None, offset: int = 0) -> CatalogReportResult:
    """Which entries of `catalog` fall inside the footprint of a plate-solved image.

    Only plate-solved images are considered -- the image query inner-joins FileWCS.

    With `only_matching` the full catalog is never scanned: entries are fetched by rowid
    after the footprint test, which is the difference between reading a few hundred rows
    and reading all 785k of NGC.
    """
    # Imported here rather than at module scope: numpy/astropy.wcs are expensive and this
    # is the only report that needs them.
    import json
    import time
    import numpy as np
    from astropy.wcs import WCS
    import astropy.units as u
    from photonfinder.core import hp

    db = context.database
    if db is None:
        return CatalogReportResult([], {}, only_matching, 0, 0)

    t0 = time.monotonic()
    total_entries = 0

    if not only_matching:
        # Phase 0: how many entries the catalog has at all (the display includes unmatched
        # ones, so an empty catalog means an empty report regardless of the image side).
        total_entries = CatalogEntry.select().where(CatalogEntry.catalog == catalog).count()
        logger.debug("Phase 0: %d catalog entries in %.2fs", total_entries, time.monotonic() - t0)
        if not total_entries:
            return CatalogReportResult([], {}, only_matching, 0, 0)

    # Phase 1: fetch criteria-filtered images with WCS coords from the main DB.
    # No catalog interaction yet — just the image side of the M×N problem.
    t1 = time.monotonic()
    img_q = (
        Image.select(
            File.rowid.alias('file_id'),
            LibraryRoot.rowid.alias('root_id'),
            LibraryRoot.name.alias('root_name'),
            LibraryRoot.path.alias('root_path'),
            File.path.alias('file_path'),
            File.name.alias('file_name'),
            Image.object_name,
            Image.coord_ra,
            Image.coord_dec,
            Image.coord_pix256,
            Image.coord_radius,
        )
        .join_from(Image, File)
        .join_from(File, LibraryRoot)
        .join_from(File, FileWCS)   # inner join: plate-solved images only
        .where(Image.coord_ra.is_null(False))
    )
    img_q = Image.apply_search_criteria(img_q, criteria)
    image_rows = list(img_q.tuples())
    images_considered = len(image_rows)
    logger.debug("Phase 1: %d candidate images in %.2fs", images_considered, time.monotonic() - t1)

    def _no_matches() -> CatalogReportResult:
        entries = [] if only_matching else _catalog_entries(catalog, limit, offset)
        return CatalogReportResult(entries, {}, only_matching, total_entries, images_considered)

    if not image_rows:
        return _no_matches()

    # Phase 1b: group images by FoV signature — dithered subs share one WCS decode.
    # Key: (center healpix pixel, radius bucket, root_id, directory).
    t1b = time.monotonic()
    fov_groups = {}   # fov_key -> {rep_file_id, coord_ra, coord_dec, coord_radius, file_data[]}

    for row in image_rows:
        (file_id, root_id, root_name, root_path,
         file_path, file_name, object_name,
         coord_ra, coord_dec, coord_pix256, coord_radius) = row

        fov_key = (coord_pix256, round((coord_radius or 0) * 1000), root_id, file_path)
        if fov_key not in fov_groups:
            fov_groups[fov_key] = {
                'rep_file_id': file_id,
                'coord_ra': coord_ra,
                'coord_dec': coord_dec,
                'coord_radius': coord_radius or 0,
                'file_data': [],
            }
        full_path = os.path.join(str(root_path), str(file_path), str(file_name))
        fov_groups[fov_key]['file_data'].append(
            MatchedFile(object_name or '', full_path, root_id, root_name,
                        file_path, file_name, file_id)
        )

    logger.debug("Phase 1b: %d FoV groups from %d images in %.2fs",
                 len(fov_groups), len(image_rows), time.monotonic() - t1b)

    # Phase 1c: compute healpix pixel union across all FoV group representatives
    # in Python (fast numpy), then query the catalog once with that pixel set.
    # This avoids per-row Python UDF calls from inside SQLite.
    t1c = time.monotonic()
    pixel_to_fovkeys = {}   # healpix pixel -> [fov_key, …]
    for fov_key, g in fov_groups.items():
        pixels = hp.cone_search_lonlat(
            g['coord_ra'] * u.deg, g['coord_dec'] * u.deg, g['coord_radius'] * u.deg
        )
        g['pixels'] = set(int(p) for p in pixels)
        for px in g['pixels']:
            pixel_to_fovkeys.setdefault(px, []).append(fov_key)

    all_pixels = list(pixel_to_fovkeys)
    logger.debug("Phase 1c: %d unique healpix pixels across %d groups in %.2fs",
                 len(all_pixels), len(fov_groups), time.monotonic() - t1c)

    # Phase 2: single catalog query using the pixel union via json_each (no per-row UDF).
    t2 = time.monotonic()
    catalog_rows = list(db.execute_sql(
        "SELECT rowid, ra, dec, healpix FROM catalog.catalog_entry"
        " WHERE catalog = ? AND healpix IN (SELECT value FROM json_each(?))",
        [catalog, json.dumps(all_pixels)]
    ))
    logger.debug("Phase 2: %d catalog candidates in %.2fs", len(catalog_rows), time.monotonic() - t2)

    if not catalog_rows:
        return _no_matches()

    # Assign each catalog candidate to the FoV groups whose pixel cone contains it.
    for ce_rowid, ce_ra, ce_dec, ce_healpix in catalog_rows:
        for fov_key in pixel_to_fovkeys.get(ce_healpix, []):
            fov_groups[fov_key].setdefault('ce_candidates', {})[ce_rowid] = (ce_ra, ce_dec)

    # Phase 3: decode WCS once per FoV group; check precise rectangular footprint.
    t3 = time.monotonic()
    wcs_cache = {}      # rep_file_id -> WCS | None
    matches_map = {}    # ce_rowid -> list of MatchedFile

    groups_with_candidates = [g for g in fov_groups.values() if g.get('ce_candidates')]
    total_groups = len(groups_with_candidates)
    logger.debug("Phase 3: %d groups have catalog candidates", total_groups)

    # Batch-load all representative WCS blobs in one query (avoids N round-trips).
    rep_ids = [g['rep_file_id'] for g in groups_with_candidates]
    wcs_blobs = {r.file_id: r.wcs for r in FileWCS.select().where(FileWCS.file.in_(rep_ids))}
    logger.debug("Phase 3: fetched %d WCS blobs in %.2fs", len(wcs_blobs), time.monotonic() - t3)

    for i, group in enumerate(groups_with_candidates):
        progress(i + 1, total_groups)

        rep_id = group['rep_file_id']
        if rep_id not in wcs_cache:
            raw_wcs = wcs_blobs.get(rep_id)
            if raw_wcs is None:
                logger.warning("catalog_report: no WCS record for rep %s", rep_id)
                wcs_cache[rep_id] = None
            else:
                try:
                    wcs_header = Header.fromstring(decompress(raw_wcs).decode())
                    naxis1 = wcs_header.get('NAXIS1', 0)
                    naxis2 = wcs_header.get('NAXIS2', 0)
                    if naxis1 and naxis2:
                        wcs_obj = WCS(wcs_header)
                        wcs_obj.array_shape = (naxis2, naxis1)
                        wcs_cache[rep_id] = wcs_obj
                    else:
                        logger.warning("catalog_report: no NAXIS in WCS for rep %s", rep_id)
                        wcs_cache[rep_id] = None
                except Exception as e:
                    logger.warning("catalog_report: bad WCS for rep %s: %s", rep_id, e)
                    wcs_cache[rep_id] = None

        wcs_obj = wcs_cache[rep_id]
        if wcs_obj is None:
            continue

        # Vectorized: project all candidate coords to pixel space in one call.
        candidates = list(group['ce_candidates'].items())
        world = np.array([(ce_ra, ce_dec) for _, (ce_ra, ce_dec) in candidates])
        try:
            pix = wcs_obj.all_world2pix(world, 0)
        except Exception:
            continue
        ny, nx = wcs_obj.array_shape
        inside = (pix[:, 0] >= 0) & (pix[:, 0] < nx) & (pix[:, 1] >= 0) & (pix[:, 1] < ny)
        file_data = group['file_data']
        for j, (ce_rowid, _) in enumerate(candidates):
            if inside[j]:
                matches_map.setdefault(ce_rowid, []).extend(file_data)

    logger.debug("Phase 3: footprint checks done in %.2fs, %d catalog entries matching",
                 time.monotonic() - t3, len(matches_map))

    if only_matching:
        # Fetch only the matched entries — avoids the full 785k Phase 0 scan.
        total_entries = len(matches_map)
        catalog_entries = _catalog_entries(catalog, limit, offset, rowids=list(matches_map.keys()))
        logger.debug("Phase 3b: fetched %d matching entries in %.2fs",
                     len(catalog_entries), time.monotonic() - t3)
    else:
        catalog_entries = _catalog_entries(catalog, limit, offset)

    logger.debug("Total catalog_report time: %.2fs", time.monotonic() - t0)
    return CatalogReportResult(catalog_entries, matches_map, only_matching,
                               total_entries, images_considered)


# --- Metadata report field extraction -----------------------------------------------
# Moved out of MetadataReportTask so the MCP server can project the same fields without
# Qt. `file` is a File instance with FitsHeader / Image / FileWCS eagerly joined.

WCS_FIELD_PREFIX = "WCS:"


def parse_field_spec(spec: str) -> tuple:
    """Split a prefixed field name into ``(field_name, source_type)``.

    ``"File.size"``/``"Image.exposure"`` -> photonfinder, ``"WCS:CRVAL1"`` -> platesolving,
    anything else -> a FITS header keyword. The ``WCS:`` prefix is needed because keywords
    like ``NAXIS1`` and ``CRVAL1`` appear in both the original header and the solved one.
    """
    if spec.startswith(WCS_FIELD_PREFIX):
        return spec[len(WCS_FIELD_PREFIX):], "platesolving"
    if spec.startswith("File.") or spec.startswith("Image."):
        return spec, "photonfinder"
    return spec, "fits"


def header_values_query(criteria: SearchCriteria, sources) -> tuple:
    """Build the File query for a set of field sources, joining only what is needed."""
    tables = [File, Image, LibraryRoot]
    if "fits" in sources:
        tables.append(FitsHeader)
    if "platesolving" in sources:
        tables.append(FileWCS)

    query = (File
             .select(*tables)
             .join_from(File, LibraryRoot)
             .join_from(File, Image, JOIN.LEFT_OUTER))
    if "fits" in sources:
        query = query.join_from(File, FitsHeader, JOIN.LEFT_OUTER)
    if "platesolving" in sources:
        query = query.join_from(File, FileWCS, JOIN.LEFT_OUTER)
    query = Image.apply_search_criteria(query, criteria)
    return query.order_by(File.root, File.path, File.name)


def extract_field_value(file: File, field_name: str, source_type: str):
    """Extract a single field value from the file based on source type."""
    try:
        if source_type == "photonfinder":
            return extract_photonfinder_field(file, field_name)
        elif source_type == "fits":
            return extract_fits_field(file, field_name)
        elif source_type == "platesolving":
            return extract_platesolving_field(file, field_name)
        else:
            return None
    except Exception as e:
        # Log error but don't fail the entire process
        logging.warning(f"Error extracting field {field_name} from {source_type}: {e}")
        return None


def extract_photonfinder_field(file: File, field_name: str):
    """Extract field from File or Image model."""
    if field_name.startswith("File."):
        attr_name = field_name[5:]  # Remove "File." prefix
        if attr_name == "full_filename":
            return str(Path(file.full_filename()))
        else:
            return getattr(file, attr_name, None)
    elif field_name.startswith("Image."):
        attr_name = field_name[6:]  # Remove "Image." prefix
        if hasattr(file, 'image') and file.image:
            return getattr(file.image, attr_name, None)
        else:
            return None
    else:
        # Handle legacy format without prefix
        if hasattr(file, field_name):
            return getattr(file, field_name, None)
        elif hasattr(file, 'image') and file.image and hasattr(file.image, field_name):
            return getattr(file.image, field_name, None)
        else:
            return None


def extract_fits_field(file: File, field_name: str):
    """Extract field from FITS header."""
    try:
        if hasattr(file, 'header_obj') and file.header_obj:
            header = file.header_obj
        else:
            if hasattr(file, 'fitsheader') and file.fitsheader:
                header = decode_header_blob(file.fitsheader.header)
                file.header_obj = header
            else:
                header = None
        return header.get(field_name, None) if header else None
    except Exception as e:
        logging.warning(f"Error parsing FITS header for field {field_name}: {e}", exc_info=True)
        return None


def extract_platesolving_field(file: File, field_name: str):
    """Extract field from WCS data."""
    try:
        if hasattr(file, 'filewcs_obj') and file.filewcs_obj:
            header = file.filewcs_obj
        else:
            if hasattr(file, 'filewcs') and file.filewcs:
                # Decompress the WCS data and parse it as FITS header
                wcs_data = decompress(file.filewcs.wcs)
                header = Header.fromstring(wcs_data.decode('utf-8'))
                file.filewcs_obj = header
            else:
                header = None
        return header.get(field_name, None) if header else None
    except Exception as e:
        logging.warning(f"Error parsing WCS data for field {field_name}: {e}")
        return None

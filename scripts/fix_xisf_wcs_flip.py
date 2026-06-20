"""
One-off migration: re-apply the vertical WCS flip for XISF files.

PixInsight stores WCS with an inverted Y axis.  flip_wcs_vertical() was
added to correct this at import time, but existing FileWCS rows were stored
without the flip.  This script finds every XISF file that has a cached
header with plate-solving information and rewrites its FileWCS row with the
corrected WCS.

Usage:
    uv run python fix_xisf_wcs_flip.py [path/to/astroFileManager.db]

If no path is given the script looks for the database in the default app-data
location (%LOCALAPPDATA%\photonfinder\astroFileManager.db).
"""

import logging
import sys
from pathlib import Path

from astropy.io.fits import Header
from astropy.wcs import WCS
from peewee import SqliteDatabase

from photonfinder.core import compress, register_udfs
from photonfinder.filesystem import Importer, decode_header_blob
from photonfinder.models import CORE_MODELS, File, FitsHeader, FileWCS
from photonfinder.platesolver import extract_wcs_cards, flip_wcs_vertical, has_been_plate_solved, stamp_wcs_origin

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _default_db_path() -> Path:
    local_app_data = Path.home() / "AppData" / "Local"
    return local_app_data / "photonfinder" / "astroFileManager.db"


def open_db(path: Path) -> SqliteDatabase:
    db = SqliteDatabase(str(path), pragmas={
        "journal_mode": "wal",
        "cache_size": -1 * 64000,
        "foreign_keys": 1,
    })
    register_udfs(db)
    db.bind(CORE_MODELS)
    db.connect()
    return db


def deserialize_header(header_record) -> Header | None:
    return decode_header_blob(header_record.header)


def fix_file(header_record, db: SqliteDatabase) -> str:
    """Return a short status string for logging."""
    header = deserialize_header(header_record)
    if header is None:
        return "skipped (unrecognised format)"

    if not Importer.is_xisf_by_name(header_record.file.name):
        return "skipped (not XISF)"

    if not has_been_plate_solved(header):
        return "skipped (no plate-solve)"

    solution = extract_wcs_cards(header)
    naxis2 = solution.get("NAXIS2")
    wcs_obj = flip_wcs_vertical(WCS(solution), naxis2)
    flipped = wcs_obj.to_header(relax=True)
    for k in ("NAXIS", "NAXIS1", "NAXIS2"):
        if k in solution:
            flipped[k] = solution[k]
    stamp_wcs_origin(flipped, "IMPORT")

    wcs_bytes = compress(flipped.tostring().encode())

    with db.atomic():
        updated = (
            FileWCS.update(wcs=wcs_bytes)
            .where(FileWCS.file == header_record.file)
            .execute()
        )
        if updated == 0:
            FileWCS.insert(file=header_record.file, wcs=wcs_bytes).execute()
            return "inserted"
        return "updated"


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_db_path()

    if not db_path.exists():
        log.error("Database not found: %s", db_path)
        sys.exit(1)

    log.info("Opening database: %s", db_path)
    db = open_db(db_path)

    query = (
        FitsHeader.select(FitsHeader, File)
        .join(File)
        .where(
            File.name.contains(".xisf")
        )
    )

    total = query.count()
    log.info("Found %d XISF header records", total)

    counts = {"updated": 0, "inserted": 0, "skipped (no plate-solve)": 0,
              "skipped (not XISF)": 0, "skipped (unrecognised format)": 0}

    for i, record in enumerate(query, 1):
        status = fix_file(record, db)
        counts[status] = counts.get(status, 0) + 1
        if status in ("updated", "inserted"):
            log.info("  [%s] %s", status, record.file.full_filename())
        if i % 100 == 0 or i == total:
            log.info("  %d / %d processed", i, total)

    db.close()

    log.info("Done.")
    for status, n in counts.items():
        if n:
            log.info("  %-40s %d", status, n)


if __name__ == "__main__":
    main()

"""Catalogs the user imports from CSV, stored in the library database.

Free of the UI layer, like ``reports.py``. Imported entries go into ``custom_catalog_entry``
in the library (the bundled catalog.db is read-only); readers see them next to the built-in
catalogs through the ``catalog_entry_all`` view, with negative rowids.

CSV format: a header row, comma or semicolon separated, column names matched ignoring case.
Required: ``id`` (or ``name``), ``ra``, ``dec``. Optional: ``magnitude``, ``size`` (arcmin),
``axis_ratio``, ``angle``, ``canonical_id``. RA/Dec are decimal degrees or sexagesimal; a
sexagesimal RA is in hours.
"""
import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import astropy.units as u
from astropy.coordinates import Angle
from peewee import chunked

from photonfinder.core import ApplicationContext, hp
from photonfinder.models import CORE_MODELS, CustomCatalogEntry

IMPORTING_CATALOG = "Importing a custom catalog"
REMOVING_CATALOG = "Removing a custom catalog"
_BATCH_SIZE = 500   # rows per INSERT, well under SQLite's bound-variable limit

_COLUMN_ALIASES = {
    "id": "catalog_id", "name": "catalog_id", "catalog_id": "catalog_id",
    "ra": "ra", "dec": "dec",
    "magnitude": "magnitude", "mag": "magnitude",
    "size": "size", "axis_ratio": "axis_ratio", "angle": "angle",
    "canonical_id": "canonical_id",
}
_REQUIRED = ("catalog_id", "ra", "dec")
_OPTIONAL_FLOATS = ("magnitude", "size", "axis_ratio", "angle")


class CatalogImportError(Exception):
    """The file as a whole can't be imported: no header, a missing column, or no usable row."""


@dataclass(frozen=True)
class SkippedRow:
    line: int
    reason: str


@dataclass
class ParsedCatalog:
    entries: list = field(default_factory=list)   # dicts with CustomCatalogEntry's columns
    skipped: list = field(default_factory=list)   # SkippedRow

    def summary(self, max_lines: int = 10) -> str:
        text = f"{len(self.entries)} imported, {len(self.skipped)} skipped"
        if not self.skipped:
            return text + "."
        shown = [f"line {s.line}: {s.reason}" for s in self.skipped[:max_lines]]
        if len(self.skipped) > max_lines:
            shown.append(f"... and {len(self.skipped) - max_lines} more")
        return text + ":\n" + "\n".join(shown)


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def parse_ra(value: str) -> float:
    """RA in degrees from decimal degrees or sexagesimal hours. Raises ValueError."""
    value = value.strip()
    degrees = float(value) if _is_number(value) else Angle(value, unit=u.hourangle).degree
    if not 0.0 <= degrees < 360.0:
        raise ValueError(f"RA {value!r} out of range")
    return float(degrees)


def parse_dec(value: str) -> float:
    """Dec in degrees from decimal or sexagesimal degrees. Raises ValueError."""
    value = value.strip()
    degrees = float(value) if _is_number(value) else Angle(value, unit=u.deg).degree
    if not -90.0 <= degrees <= 90.0:
        raise ValueError(f"Dec {value!r} out of range")
    return float(degrees)


def _optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or not value.strip():
        return None
    return float(value.strip())


def parse_csv(text: str) -> ParsedCatalog:
    """Parse a custom catalog. Bad rows are skipped and listed; raises CatalogImportError
    when a required column is missing or no row is usable."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or not lines[0].strip():
        raise CatalogImportError("The file is empty or has no header row.")
    delimiter = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    header = next(reader)

    columns = {}   # canonical name -> index; the first column with a given meaning wins
    for index, name in enumerate(header):
        canonical = _COLUMN_ALIASES.get(name.strip().lower())
        if canonical and canonical not in columns:
            columns[canonical] = index
    missing = [name if name != "catalog_id" else "id" for name in _REQUIRED if name not in columns]
    if missing:
        raise CatalogImportError(f"Missing required column(s): {', '.join(missing)}. "
                                 f"The header row needs id (or name), ra and dec.")

    result = ParsedCatalog()
    seen_ids = set()
    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue

        def cell(name, row=row):
            index = columns.get(name)
            return row[index] if index is not None and index < len(row) else None

        catalog_id = (cell("catalog_id") or "").strip()
        if not catalog_id:
            result.skipped.append(SkippedRow(line_no, "empty id"))
            continue
        if catalog_id in seen_ids:
            result.skipped.append(SkippedRow(line_no, f"duplicate id {catalog_id!r}"))
            continue
        try:
            ra = parse_ra(cell("ra") or "")
        except ValueError:
            result.skipped.append(SkippedRow(line_no, f"bad RA {cell('ra')!r}"))
            continue
        try:
            dec = parse_dec(cell("dec") or "")
        except ValueError:
            result.skipped.append(SkippedRow(line_no, f"bad Dec {cell('dec')!r}"))
            continue
        try:
            optional = {name: _optional_float(cell(name)) for name in _OPTIONAL_FLOATS}
        except ValueError:
            result.skipped.append(SkippedRow(line_no, "a magnitude, size, axis_ratio or angle is not a number"))
            continue

        seen_ids.add(catalog_id)
        canonical_id = (cell("canonical_id") or "").strip() or None
        result.entries.append({
            "ra": ra, "dec": dec, "catalog_id": catalog_id, "canonical_id": canonical_id,
            "magnitude": optional["magnitude"], "size": optional["size"] or 0.0,
            "axis_ratio": optional["axis_ratio"], "angle": optional["angle"],
        })

    if not result.entries:
        raise CatalogImportError("No usable rows.\n" + result.summary())

    pixels = hp.lonlat_to_healpix([e["ra"] for e in result.entries] * u.deg,
                                  [e["dec"] for e in result.entries] * u.deg)
    for entry, pixel in zip(result.entries, pixels):
        entry["healpix"] = int(pixel)
    return result


def parse_file(path: str | Path) -> ParsedCatalog:
    # utf-8-sig drops an Excel BOM; latin-1 is the fallback for legacy exports.
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return parse_csv(text)


# --- Library side --------------------------------------------------------------------
# Readers query CatalogEntry (the view); these functions are the only writers of
# custom_catalog_entry and the only readers that need to tell the two kinds apart.

def custom_catalog_names() -> list[str]:
    return [name for (name,) in CustomCatalogEntry.select(CustomCatalogEntry.catalog)
            .distinct().order_by(CustomCatalogEntry.catalog).tuples()]


def is_builtin_name(context: ApplicationContext, name: str) -> bool:
    row = context.database.execute_sql(
        "SELECT 1 FROM catalog.catalog_entry WHERE catalog = ? COLLATE NOCASE LIMIT 1", [name]
    ).fetchone()
    return row is not None


def is_custom_catalog(name: str) -> bool:
    return CustomCatalogEntry.select().where(CustomCatalogEntry.catalog == name).exists()


def import_catalog(context: ApplicationContext, name: str, parsed: ParsedCatalog) -> bool:
    """Store `parsed` as custom catalog `name`, replacing a custom catalog of that name in
    the same transaction. Returns False, writing nothing, if another writer holds the library.
    Raises CatalogImportError for an empty name or a built-in catalog's name."""
    name = name.strip()
    if not name:
        raise CatalogImportError("The catalog needs a name.")
    if is_builtin_name(context, name):
        raise CatalogImportError(f"'{name}' is the name of a built-in catalog. Choose another name.")
    if not context.library_write_lock.acquire(blocking=False, holder=IMPORTING_CATALOG):
        return False
    try:
        with context.database.bind_ctx(CORE_MODELS), context.database.atomic():
            CustomCatalogEntry.delete().where(CustomCatalogEntry.catalog == name).execute()
            rows = [{**entry, "catalog": name} for entry in parsed.entries]
            for batch in chunked(rows, _BATCH_SIZE):
                CustomCatalogEntry.insert_many(batch).execute()
        return True
    finally:
        context.library_write_lock.release()


def remove_catalog(context: ApplicationContext, name: str) -> bool:
    """Delete custom catalog `name`. Returns False if another writer holds the library."""
    if not context.library_write_lock.acquire(blocking=False, holder=REMOVING_CATALOG):
        return False
    try:
        with context.database.bind_ctx(CORE_MODELS), context.database.atomic():
            CustomCatalogEntry.delete().where(CustomCatalogEntry.catalog == name).execute()
        return True
    finally:
        context.library_write_lock.release()

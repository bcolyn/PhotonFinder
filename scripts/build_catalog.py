"""
Build script: reads source files from data/catalog/ and produces data/catalog.db.

Sources (see CATALOG_SOURCES below for the definitive list)
-----------------------------------------------------------
data/catalog/openngc/*.csv      — OpenNGC (semicolon-delimited, mattiaverga/OpenNGC)
  Objects with Messier/Caldwell cross-refs emit multiple rows sharing a canonical_id.
  Types in _OPENNGC_SKIP_TYPES are dropped; catalogs in _OPENNGC_CATALOG_BLACKLIST
  are suppressed to avoid duplicates with dedicated sources below.

data/catalog/vizier/*.csv       — Generic comma-delimited CSVs fetched from VizieR
  Required columns (case-insensitive): ra, dec, catalog, catalog_id, size, magnitude
  Optional: canonical_id, axis_ratio, angle

data/catalog/hyperleda/*.txt.bz2 — HyperLEDA galaxy catalogue (bzip2, tab-delimited)
  Columns: pgc, objname, hl_names(pgc), objtype, al2000, de2000, bt, vt, logd25, logr25, pa
  Filtered to objtype='G' in the query; magnitude limit bt≤18 or vt≤19 applied here.
  Visual magnitude stored as vt, or bt−0.8 when vt is absent.

Run:
  uv run python scripts/build_catalog.py
"""

import bz2
import csv
import logging
import re
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy_healpix import HEALPix

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

REPO_ROOT = Path(__file__).parent.parent
CATALOG_DIR = REPO_ROOT / "data" / "catalog"
OUTPUT_DB = REPO_ROOT / "data" / "catalog.db"

HP = HEALPix(nside=256, order="nested", frame="icrs")

CATALOG_APPLICATION_ID = 0x43415453  # "CATS"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS catalog_entry (
    ra           REAL    NOT NULL,
    dec          REAL    NOT NULL,
    catalog      TEXT    NOT NULL,
    catalog_id   TEXT    NOT NULL,
    canonical_id TEXT    NOT NULL,
    size         REAL    NOT NULL,
    axis_ratio   REAL,
    angle        REAL,
    magnitude    REAL,
    healpix      INTEGER NOT NULL
)
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_healpix      ON catalog_entry (healpix)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_id ON catalog_entry (catalog, catalog_id)",
    "CREATE INDEX IF NOT EXISTS idx_canonical_id ON catalog_entry (canonical_id)",
]

# OpenNGC types to skip — stellar objects and invalid/duplicate entries
_OPENNGC_SKIP_TYPES = {"*", "**", "*Ass", "Nova", "Dup", "NonEx"}

# Catalogs sourced from dedicated VizieR CSVs; suppress their OpenNGC entries to avoid duplicates
_OPENNGC_CATALOG_BLACKLIST = {"Barnard", "ESO", "PGC", "UGC", "H", "HCG", "MWSC", "Melotte"}


def _healpix(ra_deg: float, dec_deg: float) -> int:
    coord = SkyCoord(ra_deg, dec_deg, unit=u.deg, frame="icrs")
    return int(HP.skycoord_to_healpix(coord))


def _float_or_none(value: str) -> float | None:
    s = value.strip()
    if not s or s.lower() in ("null", "none", "nan"):
        return None
    return float(s)


# ---------------------------------------------------------------------------
# OpenNGC loader
# ---------------------------------------------------------------------------

def _parse_hms(ra_str: str, dec_str: str) -> tuple[float, float]:
    """Convert OpenNGC HH:MM:SS.ss / ±DD:MM:SS.s strings to decimal degrees."""
    rh, rm, rs = ra_str.strip().split(":")
    ra = (float(rh) + float(rm) / 60 + float(rs) / 3600) * 15.0

    sign = -1 if dec_str.strip().startswith("-") else 1
    dd, dm, ds = dec_str.strip().lstrip("+-").split(":")
    dec = sign * (float(dd) + float(dm) / 60 + float(ds) / 3600)

    return ra, dec


# Maps OpenNGC name prefixes to human-readable catalog names
_PREFIX_TO_CATALOG = {
    "NGC":  "NGC",
    "IC":   "IC",
    "C":    "Caldwell",
    "B":    "Barnard",
    "Mel":  "Melotte",
    "PGC":  "PGC",
    "UGC":  "UGC",
    "HCG":  "HCG",
    "MWSC": "MWSC",
    "ESO":  "ESO",
    "H":    "H",
    "Cl":   "Cl",
    "M":    "Messier",
}

# Matches prefix + numeric ID (with optional hyphen segment for ESO) + optional suffix
_OPENNGC_NAME_RE = re.compile(r"^([A-Za-z]+)(\d+(?:-\d+)?)(.*)")


def _parse_openngc_name(name: str) -> tuple[str, str, str]:
    """
    Parse an OpenNGC Name into (catalog, catalog_id, canonical_id).

    Examples:
      NGC0224       → ('NGC',      '224',        'NGC_224')
      IC0080 NED01  → ('IC',       '80 NED01',   'IC_80')
      C009          → ('Caldwell', '9',           'Caldwell_9')
      ESO056-115    → ('ESO',      '56-115',      'ESO_56-115')
      PGC000143     → ('PGC',      '143',         'PGC_143')
      Mel022        → ('Melotte',  '22',           'Melotte_22')

    catalog_id preserves any sub-component suffix so components are distinct;
    canonical_id is based on the numeric part only so they share it.
    """
    m = _OPENNGC_NAME_RE.match(name.strip())
    if not m:
        return "OpenNGC", name, name

    prefix, raw_id, suffix = m.group(1), m.group(2), m.group(3).strip()
    catalog = _PREFIX_TO_CATALOG.get(prefix, prefix)

    # Strip leading zeros from the first numeric segment only
    if "-" in raw_id:
        parts = raw_id.split("-", 1)
        normalized_id = f"{int(parts[0])}-{parts[1]}"
    else:
        normalized_id = str(int(raw_id))

    catalog_id = normalized_id + (f" {suffix}" if suffix else "")
    canonical_id = f"{catalog}_{normalized_id}"
    return catalog, catalog_id, canonical_id


_CALDWELL_RE = re.compile(r"\bC (\d{1,3})\b")


def load_openngc_csv(path: Path) -> list[tuple]:
    rows: list[tuple] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for line_no, row in enumerate(reader, start=2):
            obj_type = row["Type"].strip()
            skip_primary = obj_type in _OPENNGC_SKIP_TYPES

            ra_str = row["RA"].strip()
            dec_str = row["Dec"].strip()
            if not ra_str or not dec_str:
                continue

            try:
                ra, dec = _parse_hms(ra_str, dec_str)
            except ValueError:
                logging.warning(f"{path.name}:{line_no}: bad coordinates, skipping")
                continue

            name = row["Name"].strip()
            primary_catalog, primary_id, canonical_id = _parse_openngc_name(name)

            maj_ax = _float_or_none(row.get("MajAx", ""))
            min_ax = _float_or_none(row.get("MinAx", ""))
            size = maj_ax if maj_ax is not None else 0.0
            axis_ratio = (min_ax / maj_ax) if (maj_ax and min_ax) else None
            angle = _float_or_none(row.get("PosAng", ""))

            v_mag = _float_or_none(row.get("V-Mag", ""))
            b_mag = _float_or_none(row.get("B-Mag", ""))
            magnitude = v_mag if v_mag is not None else b_mag

            pix = _healpix(ra, dec)

            def make_row(catalog, catalog_id, cid=canonical_id):
                return (ra, dec, catalog, catalog_id, cid,
                        size, axis_ratio, angle, magnitude, pix)

            if primary_catalog in _OPENNGC_CATALOG_BLACKLIST:
                continue

            # Emit primary row unless it's a generic skip type.
            # Named-catalog entries (Messier/Caldwell by Name) always emit —
            # e.g. M040 (**) and C014 (*Ass) should appear in their own catalogs.
            if not skip_primary or primary_catalog in ("Messier", "Caldwell"):
                rows.append(make_row(primary_catalog, primary_id))

            # Messier cross-ref: always emit when M column is set and the
            # primary isn't already Messier (covers IC4715/M24, etc.)
            messier_raw = row.get("M", "").strip()
            if messier_raw and primary_catalog != "Messier":
                rows.append(make_row("Messier", str(int(messier_raw))))

            # Caldwell cross-ref: parse from Identifiers column.
            # Emit whenever found; skip only if primary is already Caldwell
            # (the C-named addendum entries are handled via primary_catalog above).
            if primary_catalog != "Caldwell":
                cm = _CALDWELL_RE.search(row.get("Identifiers", ""))
                if cm:
                    rows.append(make_row("Caldwell", str(int(cm.group(1)))))

    return rows


# ---------------------------------------------------------------------------
# Generic CSV loader
# ---------------------------------------------------------------------------

def load_generic_csv(path: Path) -> list[tuple]:
    rows: list[tuple] = []

    with path.open(newline="", encoding="utf-8") as f:
        # Skip leading # comment lines before handing the stream to DictReader
        while True:
            pos = f.tell()
            line = f.readline()
            if not line.startswith("#"):
                f.seek(pos)
                break
        reader = csv.DictReader(f)
        for line_no, raw in enumerate(reader, start=2):
            row = {k.strip().lower(): v.strip() for k, v in raw.items()}
            try:
                ra = _float_or_none(row["ra"])
                dec = _float_or_none(row["dec"])
                catalog = row["catalog"]
                catalog_id = row["catalog_id"]
                canonical_id = row.get("canonical_id") or f"{catalog}_{catalog_id}"
                size = float(row["size"])
                axis_ratio = _float_or_none(row.get("axis_ratio", ""))
                angle = _float_or_none(row.get("angle", ""))
                magnitude = _float_or_none(row.get("magnitude", "")) if "magnitude" in row else None
            except (KeyError, ValueError) as exc:
                logging.warning(f"{path.name}:{line_no}: skipping — {exc}")
                continue
            if ra is None or dec is None:
                logging.warning(f"{path.name}:{line_no}: skipping — missing coordinates")
                continue

            rows.append((ra, dec, catalog, catalog_id, canonical_id,
                         size, axis_ratio, angle, magnitude, _healpix(ra, dec)))

    return rows


# ---------------------------------------------------------------------------
# HyperLEDA loader
# ---------------------------------------------------------------------------

# columns: pgc, objname, hl_names(pgc), objtype, al2000, de2000, bt, vt, logd25, logr25, pa
_HL_BT_LIMIT = 18.0
_HL_VT_LIMIT = 19.0


def load_hyperleda_bz2(path: Path) -> list[tuple]:
    rows: list[tuple] = []
    skipped_mag = skipped_coord = 0

    with bz2.open(path, 'rt', encoding='utf-8') as f:
        in_data = False
        for line in f:
            if line.startswith('#'):
                continue
            if not in_data:
                in_data = True  # first non-# line is the space-delimited column header
                continue

            parts = line.rstrip('\n').split('\t')
            if len(parts) < 6:
                continue

            pgc_raw  = parts[0].strip()
            objname  = parts[1].strip()
            # parts[2] = hl_names(pgc) — reserved for future dedup, not stored
            # parts[3] = objtype — already filtered to 'G' in query

            al2000_h = _float_or_none(parts[4]) if len(parts) > 4 else None  # decimal hours
            de2000   = _float_or_none(parts[5]) if len(parts) > 5 else None  # decimal degrees
            if al2000_h is None or de2000 is None:
                skipped_coord += 1
                continue
            al2000 = al2000_h * 15.0  # convert hours → degrees

            bt = _float_or_none(parts[6]) if len(parts) > 6 else None
            vt = _float_or_none(parts[7]) if len(parts) > 7 else None

            # Include only if at least one band is within its magnitude limit
            bt_ok = bt is not None and bt <= _HL_BT_LIMIT
            vt_ok = vt is not None and vt <= _HL_VT_LIMIT
            if not bt_ok and not vt_ok:
                skipped_mag += 1
                continue

            # Visual magnitude: prefer vt, fall back to bt − 0.8
            visual_mag: float = vt if vt is not None else (bt - 0.8)  # type: ignore[operator]

            logd25 = _float_or_none(parts[8]) if len(parts) > 8 else None
            size = 10 ** logd25 * 0.1 if logd25 is not None else 0.0  # point source if absent

            logr25 = _float_or_none(parts[9]) if len(parts) > 9 else None
            axis_ratio = 10 ** (-logr25) if logr25 is not None else 1.0  # circular if absent

            pa = _float_or_none(parts[10]) if len(parts) > 10 else None  # None → no rotation

            try:
                pgc_id = str(int(pgc_raw))
            except (ValueError, TypeError):
                continue

            catalog_id = pgc_id
            if objname:
                _, _, canonical_id = _parse_openngc_name(objname)
            else:
                canonical_id = f"PGC_{pgc_id}"

            rows.append((al2000, de2000, 'PGC', catalog_id, canonical_id,
                         size, axis_ratio, pa, visual_mag, _healpix(al2000, de2000)))

    logging.info(f"  skipped {skipped_coord} (no coords), {skipped_mag} (magnitude limit)")
    return rows


# ---------------------------------------------------------------------------
# Catalog source registry — edit here to add/remove/reorder sources
# ---------------------------------------------------------------------------

CATALOG_SOURCES: list[tuple[str, Callable[[Path], list[tuple]]]] = [
    ("openngc/*.csv",          load_openngc_csv),
    ("vizier/*.csv",           load_generic_csv),
    ("hyperleda/*.txt.bz2",    load_hyperleda_bz2),
]


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build(output: Path = OUTPUT_DB, source_dir: Path = CATALOG_DIR) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        try:
            output.unlink()
        except PermissionError:
            logging.error(f"Cannot overwrite {output} — close any application that has it open and retry.")
            sys.exit(1)

    con = sqlite3.connect(output)
    try:
        con.execute(f"PRAGMA application_id = {CATALOG_APPLICATION_ID}")
        con.execute(CREATE_TABLE)

        total = 0
        for pattern, loader in CATALOG_SOURCES:
            files = sorted((source_dir / pattern.split("/")[0]).glob(pattern.split("/")[1]))
            for path in files:
                logging.info(f"Loading {path.name} ({loader.__name__}) ...")
                rows = loader(path)
                con.executemany(
                    "INSERT INTO catalog_entry VALUES (?,?,?,?,?,?,?,?,?,?)", rows
                )
                logging.info(f"  {len(rows):,} rows inserted")
                total += len(rows)

        if total == 0:
            logging.warning("No rows inserted — check that source files exist")

        for stmt in CREATE_INDEXES:
            con.execute(stmt)

        con.commit()
        con.execute("VACUUM")
        logging.info(f"Done — {total:,} total rows written to {output}")
    finally:
        con.close()


if __name__ == "__main__":
    build()
    sys.exit(0)

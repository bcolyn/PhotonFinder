"""
Download a useful subset of the Principal Galaxy Catalogue from VizieR and write
data/catalog/vizier/pgc.csv for use by build_catalog.py.

Source: VII/155 — Third Reference Catalogue of Bright Galaxies (RC3, de Vaucouleurs+ 1991)
~23 000 galaxies, all with PGC cross-references and measured diameters.
Coordinates are J2000. size is the major-axis diameter D25 in arcminutes,
derived from logD25 (log10 of D25 in units of 0.1 arcmin): size = 10^logD25 * 0.1.

PGC entries are already excluded from the OpenNGC import in build_catalog.py
("PGC" is in the openngc blacklist), so no further changes are needed there.

Run:
  uv run python scripts/fetch_pgc.py
"""

import csv
import re
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "pgc.csv"


def main() -> None:
    print("Fetching VII/155 (RC3) from VizieR ...")
    v = Vizier(columns=["**"], row_limit=-1)
    cats = v.get_catalogs("VII/155")
    # RC3 may have multiple tables; the main one is usually the first
    t = cats[0]
    print(f"  {len(t)} rows received")
    print(f"  Columns: {t.colnames}")

    # RC3 PGC column is like "PGC    2"; coordinates are sexagesimal strings
    _pgc_re = re.compile(r"PGC\s+(\d+)", re.I)

    rows, skipped, no_pgc = [], 0, 0
    for row in t:
        pgc_raw = str(row["PGC"]).strip()
        m = _pgc_re.match(pgc_raw)
        if not m:
            no_pgc += 1
            continue
        pgc_id = m.group(1)

        ra_str  = str(row["RA2000"]).strip()
        dec_str = str(row["DE2000"]).strip()
        if not ra_str or not dec_str or ra_str == "--" or dec_str == "--":
            skipped += 1
            continue
        try:
            coord = SkyCoord(ra_str, dec_str, unit=(u.hourangle, u.deg), frame="icrs")
        except Exception as e:
            print(f"  Skipping PGC {pgc_id}: {e}", file=sys.stderr)
            skipped += 1
            continue

        try:
            logd25 = float(row["D25"])   # log10(D25 / 0.1 arcmin)
            size   = 10 ** logd25 * 0.1 if not np.isnan(logd25) else 0.0
        except (ValueError, TypeError, KeyError):
            size = 0.0

        try:
            mag = float(row["BT"])
            if np.isnan(mag):
                mag = None
        except (ValueError, TypeError, KeyError):
            mag = None

        entry = {
            "ra":         f"{coord.ra.deg:.6f}",
            "dec":        f"{coord.dec.deg:.6f}",
            "catalog":    "PGC",
            "catalog_id": pgc_id,
            "size":       f"{size:.2f}",
        }
        if mag is not None:
            entry["magnitude"] = f"{mag:.2f}"
        rows.append(entry)

    fieldnames = ["ra", "dec", "catalog", "catalog_id", "size", "magnitude"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# Principal Galaxy Catalogue — RC3 subset (de Vaucouleurs et al. 1991)\n")
        f.write("# Source: VizieR catalog VII/155 — https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/155\n")
        f.write("# Coordinates are ICRS (J2000). size is D25 (major axis at 25 mag/arcsec²) in arcminutes,\n")
        f.write("# converted from logD25 (log10 of D25 in 0.1 arcmin): size = 10^D25 * 0.1.\n")
        f.write("# magnitude is total B magnitude (BT).\n")
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}  (skipped {skipped}, no PGC number {no_pgc})")


if __name__ == "__main__":
    main()

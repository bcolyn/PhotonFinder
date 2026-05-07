"""
Download Lynds' Catalogue of Bright Nebulae from VizieR and write
data/catalog/vizier/lbn.csv for use by build_catalog.py.

Source: VII/9 (Lynds 1965)
Coordinates in the source are B1950; VizieR supplies _RA.icrs / _DE.icrs in ICRS.
size is the largest angular dimension (LMax) in arcminutes.

Run:
  uv run python scripts/fetch_lbn.py
"""

import csv
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "lbn.csv"


def main() -> None:
    print("Fetching VII/9 from VizieR ...")
    v = Vizier(columns=["**"], row_limit=-1)
    t = v.get_catalogs("VII/9")[0]
    print(f"  {len(t)} rows received")
    print(f"  Columns: {t.colnames}")

    rows, skipped = [], 0
    for row in t:
        # VII/9: sequential number = LBN number; Diam1/Diam2 are in degrees
        lbn_id  = str(int(row["Seq"]))
        ra_str  = str(row["_RA.icrs"]).strip()
        dec_str = str(row["_DE.icrs"]).strip()
        if not ra_str or not dec_str or ra_str == "--" or dec_str == "--":
            skipped += 1
            continue
        try:
            coord = SkyCoord(ra_str, dec_str, unit=(u.hourangle, u.deg), frame="icrs")
        except Exception as e:
            print(f"  Skipping LBN {lbn_id}: {e}", file=sys.stderr)
            skipped += 1
            continue
        try:
            size = float(row["Diam1"])   # already in arcminutes
            if np.isnan(size):
                size = 0.0
        except (ValueError, TypeError, KeyError):
            size = 0.0
        rows.append({
            "ra":         f"{coord.ra.deg:.6f}",
            "dec":        f"{coord.dec.deg:.6f}",
            "catalog":    "LBN",
            "catalog_id": lbn_id,
            "size":       f"{size:.2f}",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# Lynds' Catalogue of Bright Nebulae (Lynds 1965)\n")
        f.write("# Source: VizieR catalog VII/9 — https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/9\n")
        f.write("# Coordinates are ICRS (J2000). size is the largest angular dimension (Diam1) in arcminutes.\n")
        writer = csv.DictWriter(f, fieldnames=["ra", "dec", "catalog", "catalog_id", "size"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}  (skipped {skipped})")


if __name__ == "__main__":
    main()

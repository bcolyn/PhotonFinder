"""
Download Lynds' Catalogue of Dark Nebulae from VizieR and write
data/catalog/vizier/ldn.csv for use by build_catalog.py.

Source: VII/7A (Lynds 1962)
Coordinates in the source are B1950; VizieR supplies _RA.icrs / _DE.icrs in ICRS.
The catalogue gives object area in square degrees; size is the diameter of the
equivalent circle in arcminutes: 2 * sqrt(area / pi) * 60.

Run:
  uv run python scripts/fetch_ldn.py
"""

import csv
import math
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "ldn.csv"


def main() -> None:
    print("Fetching VII/7A from VizieR ...")
    v = Vizier(columns=["**"], row_limit=-1)
    t = v.get_catalogs("VII/7A")[0]
    print(f"  {len(t)} rows received")
    print(f"  Columns: {t.colnames}")

    rows, skipped = [], 0
    for row in t:
        import numpy.ma as ma
        if ma.is_masked(row["LDN"]):
            skipped += 1
            continue
        ldn_id  = str(int(row["LDN"]))
        ra_str  = str(row["_RA.icrs"]).strip()
        dec_str = str(row["_DE.icrs"]).strip()
        if not ra_str or not dec_str or ra_str == "--" or dec_str == "--":
            skipped += 1
            continue
        try:
            coord = SkyCoord(ra_str, dec_str, unit=(u.hourangle, u.deg), frame="icrs")
        except Exception as e:
            print(f"  Skipping LDN {ldn_id}: {e}", file=sys.stderr)
            skipped += 1
            continue
        try:
            area_sq_deg = float(row["Area"])
            size = 2.0 * math.sqrt(area_sq_deg / math.pi) * 60.0 if area_sq_deg > 0 else 0.0
            if np.isnan(size):
                size = 0.0
        except (ValueError, TypeError, KeyError):
            size = 0.0
        rows.append({
            "ra":         f"{coord.ra.deg:.6f}",
            "dec":        f"{coord.dec.deg:.6f}",
            "catalog":    "LDN",
            "catalog_id": ldn_id,
            "size":       f"{size:.2f}",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# Lynds' Catalogue of Dark Nebulae (Lynds 1962)\n")
        f.write("# Source: VizieR catalog VII/7A — https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/7A\n")
        f.write("# Coordinates are ICRS (J2000). size is the diameter of the equivalent circle in arcminutes,\n")
        f.write("# derived from the catalogue Area (sq deg): 2*sqrt(Area/pi)*60.\n")
        writer = csv.DictWriter(f, fieldnames=["ra", "dec", "catalog", "catalog_id", "size"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}  (skipped {skipped})")


if __name__ == "__main__":
    main()

"""
Download Barnard's Catalogue of Dark Nebulae from VizieR and write
data/catalog/vizier/barnard.csv for use by build_catalog.py.

Source: VII/220A (Barnard 1927)

Run:
  uv run python scripts/fetch_barnard.py
"""

import csv
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "barnard.csv"


def main() -> None:
    print("Fetching VII/220A from VizieR ...")
    v = Vizier(columns=["**"], row_limit=-1)
    t = v.get_catalogs("VII/220A")[0]
    print(f"  {len(t)} rows received")

    rows, skipped = [], 0
    for row in t:
        barn_id = str(row["Barn"]).strip()
        ra_str = str(row["_RA.icrs"]).strip()
        dec_str = str(row["_DE.icrs"]).strip()
        if not ra_str or not dec_str:
            skipped += 1
            continue
        try:
            coord = SkyCoord(ra_str, dec_str, unit=(u.hourangle, u.deg), frame="icrs")
        except Exception as e:
            print(f"  Skipping B{barn_id}: {e}", file=sys.stderr)
            skipped += 1
            continue
        diam = row["Diam"]
        try:
            size = float(diam) if str(diam) != "--" and not np.isnan(float(diam)) else 0.0
        except (ValueError, TypeError):
            size = 0.0
        rows.append({
            "ra":         f"{coord.ra.deg:.6f}",
            "dec":        f"{coord.dec.deg:.6f}",
            "catalog":    "Barnard",
            "catalog_id": barn_id,
            "size":       f"{size:.2f}",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# Barnard Catalogue of 349 Dark Objects in the Sky (Barnard 1927)\n")
        f.write("# Source: VizieR catalog VII/220A — https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/220A\n")
        f.write("# Coordinates are ICRS (J2000), converted from the original B1875 positions.\n")
        f.write("# size is the object diameter in arcminutes (0 = not recorded).\n")
        writer = csv.DictWriter(f, fieldnames=["ra", "dec", "catalog", "catalog_id", "size"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}  (skipped {skipped})")


if __name__ == "__main__":
    main()

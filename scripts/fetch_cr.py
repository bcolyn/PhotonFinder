"""
Extract Collinder open clusters from the Dias+ open cluster catalogue on VizieR
and write data/catalog/vizier/cr.csv for use by build_catalog.py.

Source: B/ocl (Dias et al. 2002, continuously updated)
Only rows whose cluster name begins with "Collinder" are kept.
Coordinates are J2000. size is the angular diameter in arcminutes.

NOTE: if build_catalog.py imports an OpenNGC addendum that contains "Collinder"
entries, add "Collinder" to the openngc blacklist in build_catalog.py to prevent
duplicates.

Run:
  uv run python scripts/fetch_cr.py
"""

import csv
import re
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "cr.csv"

_CR_RE = re.compile(r"^Collinder\s+(\d+)$", re.I)


def main() -> None:
    print("Fetching B/ocl from VizieR ...")
    v = Vizier(columns=["**"], row_limit=-1)
    cats = v.get_catalogs("B/ocl")
    t = cats[0]
    print(f"  {len(t)} rows received")
    print(f"  Columns: {t.colnames}")

    rows, skipped = [], 0
    for row in t:
        name = str(row["Cluster"]).strip()
        m = _CR_RE.match(name)
        if not m:
            continue
        cr_id = m.group(1)
        try:
            coord = SkyCoord(str(row["RAJ2000"]), str(row["DEJ2000"]),
                             unit=(u.hourangle, u.deg), frame="icrs")
        except Exception as e:
            print(f"  Skipping Cr {cr_id}: {e}", file=sys.stderr)
            skipped += 1
            continue
        try:
            size = float(row["Diam"])
            if np.isnan(size):
                size = 0.0
        except (ValueError, TypeError, KeyError):
            size = 0.0
        rows.append({
            "ra":         f"{coord.ra.deg:.6f}",
            "dec":        f"{coord.dec.deg:.6f}",
            "catalog":    "Cr",
            "catalog_id": cr_id,
            "size":       f"{size:.2f}",
        })

    rows.sort(key=lambda r: int(r["catalog_id"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# Collinder Catalogue of Open Clusters (Collinder 1931)\n")
        f.write("# Source: VizieR catalog B/ocl (Dias et al. 2002) — https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=B/ocl\n")
        f.write("# Coordinates are ICRS (J2000). size is the angular diameter in arcminutes.\n")
        writer = csv.DictWriter(f, fieldnames=["ra", "dec", "catalog", "catalog_id", "size"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}  (skipped {skipped})")


if __name__ == "__main__":
    main()

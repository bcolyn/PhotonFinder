"""
Download van den Bergh's Catalogue of Reflection Nebulae from VizieR and write
data/catalog/vizier/vdb.csv for use by build_catalog.py.

Source: VII/21 (van den Bergh 1966)
Coordinates are ICRS decimal degrees (_RA / _DE columns).
size is twice the larger of BRadMax / RRadMax (blue/red max radii in arcminutes),
giving an approximate nebula diameter.

Run:
  uv run python scripts/fetch_vdb.py
"""

import csv
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "vdb.csv"


def main() -> None:
    print("Fetching VII/21 from VizieR ...")
    v = Vizier(columns=["**"], row_limit=-1)
    t = v.get_catalogs("VII/21")[0]
    print(f"  {len(t)} rows received")
    print(f"  Columns: {t.colnames}")

    rows, skipped = [], 0
    for row in t:
        vdb_id = str(int(row["VdB"]))
        try:
            # _RA / _DE are ICRS decimal degrees computed by VizieR
            coord = SkyCoord(float(row["_RA"]), float(row["_DE"]), unit=u.deg, frame="icrs")
        except Exception as e:
            print(f"  Skipping VdB {vdb_id}: {e}", file=sys.stderr)
            skipped += 1
            continue
        try:
            brad = float(row["BRadMax"]) if not np.ma.is_masked(row["BRadMax"]) else 0.0
            rrad = float(row["RRadMax"]) if not np.ma.is_masked(row["RRadMax"]) else 0.0
            radius = max(brad, rrad)
            size = radius * 2.0 if radius > 0 else 0.0
        except (ValueError, TypeError, KeyError):
            size = 0.0
        try:
            vmag = float(row["Vmag"]) if not np.ma.is_masked(row["Vmag"]) else None
        except (ValueError, TypeError, KeyError):
            vmag = None
        entry = {
            "ra":         f"{coord.ra.deg:.6f}",
            "dec":        f"{coord.dec.deg:.6f}",
            "catalog":    "VdB",
            "catalog_id": vdb_id,
            "size":       f"{size:.2f}",
        }
        if vmag is not None:
            entry["magnitude"] = f"{vmag:.2f}"
        rows.append(entry)

    fieldnames = ["ra", "dec", "catalog", "catalog_id", "size", "magnitude"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# van den Bergh's Catalogue of Reflection Nebulae (van den Bergh 1966)\n")
        f.write("# Source: VizieR catalog VII/21 — https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/21\n")
        f.write("# Coordinates are ICRS (J2000). size = 2 * max(BRadMax, RRadMax) in arcminutes.\n")
        f.write("# magnitude is the V magnitude of the illuminating star.\n")
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}  (skipped {skipped})")


if __name__ == "__main__":
    main()

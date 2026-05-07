"""
Extract Melotte open clusters from Simbad and write
data/catalog/vizier/mel.csv for use by build_catalog.py.

Strategy:
  - Simbad query_catalog('Cl Melotte') returns all objects with a Melotte
    designation; we keep only rows whose catalog_id is exactly "Cl Melotte NNN"
    (the cluster itself, not member stars).
  - Sizes (arcmin diameters) come from the Dias+ B/ocl open cluster catalogue
    on VizieR, matched by primary cluster name against Simbad's main_id.

Melotte objects are already excluded from the OpenNGC import in build_catalog.py
("Melotte" is in the openngc blacklist), so no further changes are needed there.

Run:
  uv run python scripts/fetch_mel.py
"""

import csv
import re
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astroquery.simbad import Simbad
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "mel.csv"

_MEL_ID_RE = re.compile(r"^Cl\s+Melotte\s+(\d+)$", re.I)


def _build_bocl_size_index() -> dict[str, float]:
    """Return {cluster_primary_name -> diameter_arcmin} from B/ocl."""
    print("  Fetching B/ocl for size data ...")
    v = Vizier(columns=["Cluster", "Diam"], row_limit=-1)
    t = v.get_catalogs("B/ocl")[0]
    index: dict[str, float] = {}
    for row in t:
        name = str(row["Cluster"]).strip()
        try:
            diam = float(row["Diam"])
            if not np.isnan(diam):
                index[name.upper()] = diam
        except (ValueError, TypeError):
            pass
    return index


def main() -> None:
    print("Querying Simbad for Cl Melotte catalog ...")
    s = Simbad()
    t = s.query_catalog("Cl Melotte")
    print(f"  {len(t)} rows received")

    size_index = _build_bocl_size_index()

    rows, skipped = [], 0
    for row in t:
        cat_id = str(row.get("catalog_id", "") or "").strip()
        m = _MEL_ID_RE.match(cat_id)
        if not m:
            continue                      # member star or malformed row
        mel_num = m.group(1)
        try:
            coord = SkyCoord(float(row["ra"]), float(row["dec"]), unit=u.deg, frame="icrs")
        except Exception as e:
            print(f"  Skipping Mel {mel_num}: {e}", file=sys.stderr)
            skipped += 1
            continue

        main_id = str(row["main_id"]).strip()
        size = size_index.get(main_id.upper(), 0.0)

        rows.append({
            "ra":         f"{coord.ra.deg:.6f}",
            "dec":        f"{coord.dec.deg:.6f}",
            "catalog":    "Mel",
            "catalog_id": mel_num,
            "size":       f"{size:.2f}",
        })

    rows.sort(key=lambda r: int(r["catalog_id"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# Melotte Catalogue of Star Clusters (Melotte 1915)\n")
        f.write("# Source: Simbad (positions) + VizieR B/ocl (sizes)\n")
        f.write("# Coordinates are ICRS (J2000). size is the angular diameter in arcminutes (0 = not in B/ocl).\n")
        writer = csv.DictWriter(f, fieldnames=["ra", "dec", "catalog", "catalog_id", "size"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}  (skipped {skipped})")


if __name__ == "__main__":
    main()

"""
Download the Sharpless Catalogue of HII Regions from VizieR and write
data/catalog/vizier/sharpless2.csv for use by build_catalog.py.

Source: VII/20 (Sharpless 1959)
Coordinates are provided by VizieR as B1950 decimal degrees and converted to ICRS.

Run:
  uv run python scripts/fetch_sharpless2.py
"""

import csv
from pathlib import Path

import astropy.units as u
from astropy.coordinates import FK4, SkyCoord
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "sharpless2.csv"


def main() -> None:
    print("Fetching VII/20 from VizieR ...")
    v = Vizier(columns=["**", "_RAB1950", "_DEB1950"], row_limit=-1)
    t = v.get_catalogs("VII/20")[0]
    print(f"  {len(t)} rows received")

    rows = []
    for row in t:
        sh2_id    = str(int(row["Sh2"]))
        ra_b1950  = float(row["_RAB1950"])
        dec_b1950 = float(row["_DEB1950"])
        size      = float(row["Diam"])
        coord = SkyCoord(ra_b1950, dec_b1950, unit=u.deg, frame=FK4(equinox="B1950")).icrs
        rows.append({
            "ra":         f"{coord.ra.deg:.6f}",
            "dec":        f"{coord.dec.deg:.6f}",
            "catalog":    "Sh2",
            "catalog_id": sh2_id,
            "size":       f"{size:.2f}",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# Catalogue of HII Regions (Sharpless 1959)\n")
        f.write("# Source: VizieR catalog VII/20 — https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/20\n")
        f.write("# Coordinates converted from B1950 to ICRS. size is the object diameter in arcminutes.\n")
        writer = csv.DictWriter(f, fieldnames=["ra", "dec", "catalog", "catalog_id", "size"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()

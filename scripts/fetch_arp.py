"""
Download the Arp Atlas of Peculiar Galaxies from VizieR and write
data/catalog/vizier/arp.csv for use by build_catalog.py.

Source: VII/192 (Webb 1996), which extends Arp 1966 with J2000 coords and sizes.
Positions and sizes come from the arpord table (one row per Arp number).
Galaxy counts come from the arplist table; canonical_id is only mapped to an
NGC/IC/Messier entry when the Arp object is a single galaxy.

Run:
  uv run python scripts/fetch_arp.py
"""

import csv
import re
import sys
from collections import Counter
from pathlib import Path

import astropy.units as u
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

OUT = Path(__file__).parent.parent / "data" / "catalog" / "vizier" / "arp.csv"

_NGC_RE  = re.compile(r"^NGC\s+(\d+)", re.I)
_IC_RE   = re.compile(r"^IC\s+(\d+)", re.I)
_MESS_RE = re.compile(r"^MESSIER\s+(\d+)$", re.I)


def _canonical(name: str) -> str | None:
    for pat, prefix in [(_NGC_RE, "NGC"), (_IC_RE, "IC"), (_MESS_RE, "Messier")]:
        m = pat.match(name)
        if m:
            return f"{prefix}_{int(m.group(1))}"
    return None


def main() -> None:
    print("Fetching VII/192 from VizieR ...")
    v = Vizier(columns=["**"], row_limit=-1)
    cats = v.get_catalogs("VII/192")
    arpord  = cats["VII/192/arpord"]
    arplist = cats["VII/192/arplist"]
    print(f"  arpord: {len(arpord)} rows,  arplist: {len(arplist)} rows")

    galaxy_count = Counter(int(r["Arp"]) for r in arplist)

    rows, skipped, mapped = [], 0, 0
    for row in arpord:
        arp_id = int(row["Arp"])
        ra_str  = str(row["RAJ2000"]).strip()
        dec_str = str(row["DEJ2000"]).strip()
        size    = float(row["Size"])
        name    = str(row["Name"]).strip()
        try:
            coord = SkyCoord(ra_str, dec_str, unit=(u.hourangle, u.deg))
        except Exception as e:
            print(f"  Skipping Arp {arp_id}: {e}", file=sys.stderr)
            skipped += 1
            continue

        cid = _canonical(name) if galaxy_count[arp_id] == 1 else None
        if cid:
            mapped += 1
        rows.append({
            "ra":           f"{coord.ra.deg:.6f}",
            "dec":          f"{coord.dec.deg:.6f}",
            "catalog":      "Arp",
            "catalog_id":   str(arp_id),
            "canonical_id": cid or f"Arp_{arp_id}",
            "size":         f"{size:.2f}",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write("# Arp Atlas of Peculiar Galaxies (Arp 1966)\n")
        f.write("# Source: VizieR catalog VII/192 (Webb 1996) — https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VII/192\n")
        f.write("# Coordinates are J2000. size is the object diameter in arcminutes.\n")
        f.write("# canonical_id links to NGC/IC/Messier only for single-galaxy entries.\n")
        writer = csv.DictWriter(f, fieldnames=["ra", "dec", "catalog", "catalog_id", "canonical_id", "size"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT}  ({mapped} with canonical link, skipped {skipped})")


if __name__ == "__main__":
    main()

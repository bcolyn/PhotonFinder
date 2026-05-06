#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "astroquery",
#   "astropy",
#   "pandas",
# ]
# ///
"""
Explore VizieR catalog structure.
Usage:
    uv run explore_vizier.py              # defaults to HCG (VII/213)
    uv run explore_vizier.py VII/20       # Sharpless 2
    uv run explore_vizier.py VII/192      # Arp
    uv run explore_vizier.py VII/110A     # Abell clusters
"""

import sys
from astroquery.vizier import Vizier
from astropy import units as u

KNOWN = {
    "VII/213":  "Hickson Compact Groups (HCG)",
    "VII/20":   "Sharpless 2 HII regions",
    "VII/192":  "Arp Peculiar Galaxies",
    "VII/110A": "Abell Galaxy Clusters",
    "VII/220A": "Barnard Dark Nebulae",
    "VII/237": "Hyperleda",
}

catalog_id = sys.argv[1] if len(sys.argv) > 1 else "VII/213"
label = KNOWN.get(catalog_id, catalog_id)

print(f"\n{'='*60}")
print(f"Catalog : {catalog_id}  —  {label}")
print(f"{'='*60}\n")

Vizier.ROW_LIMIT = -1
tables = Vizier.get_catalogs(catalog_id)

print(f"Tables in this catalog: {len(tables)}\n")

for i, table in enumerate(tables):
    df = table.to_pandas()
    print(f"--- Table {i}: {table.meta.get('name', '?')} ---")
    print(f"  Rows    : {len(df)}")
    print(f"  Columns : {list(df.columns)}\n")

    print("  Column details:")
    for col in table.columns.values():
        unit = str(col.unit) if col.unit else "—"
        print(f"    {col.name:<20} {str(col.dtype):<10} unit={unit:<12} {col.description}")

    print(f"\n  First 3 rows:")
    print(df.head(3).to_string(index=False))
    print()
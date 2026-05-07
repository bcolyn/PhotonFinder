"""
One-off script: backfill Image.coord_radius from existing FileWCS records.
Run from the project root:  uv run python backfill_coord_radius.py
"""
import json
import sys
import warnings
import zstd
from astropy.utils.exceptions import AstropyWarning
from peewee import SqliteDatabase
from astropy.io.fits import Header

DB_PATH = r"C:\Users\benny\Nextcloud\miniserv3.db"


def decompress(data: bytes) -> bytes:
    return zstd.decompress(data)


def main():
    warnings.filterwarnings('ignore', category=AstropyWarning)

    db = SqliteDatabase(DB_PATH, pragmas={
        'journal_mode': 'wal',
        'cache_size': -1 * 64000,
        'foreign_keys': 1,
    })

    from photonfinder.models import CORE_MODELS, Image, FileWCS, FitsHeader
    db.bind(CORE_MODELS, bind_refs=False, bind_backrefs=False)
    db.connect()

    try:
        db.execute_sql("ALTER TABLE image ADD COLUMN coord_radius REAL")
        print("Column coord_radius added.")
    except Exception:
        print("Column coord_radius already exists, skipping ALTER TABLE.")

    from photonfinder.platesolver import get_image_center_coords

    rows = list(
        FileWCS.select(FileWCS, Image)
        .join(Image, on=(FileWCS.file == Image.file))
        .where(Image.coord_radius.is_null())
        .where(Image.coord_ra.is_null(False))
    )
    total = len(rows)
    print(f"Found {total} images to backfill.")

    updated = 0
    skipped = 0
    for i, row in enumerate(rows, 1):
        try:
            header = Header.fromstring(decompress(row.wcs).decode())
            _, _, _, radius = get_image_center_coords(header)
            if radius is None:
                # WCS blob lacks NAXIS1/NAXIS2 — try to recover from the cached header.
                # FITS files store a FITS header string; XISF files store a JSON dict.
                fits_rec = FitsHeader.get_or_none(FitsHeader.file == row.file_id)
                if fits_rec:
                    raw = decompress(fits_rec.header).decode()
                    if raw.startswith('{'):
                        from photonfinder.filesystem import header_from_xisf_dict
                        fits_header = header_from_xisf_dict(json.loads(raw))
                    else:
                        fits_header = Header.fromstring(raw)
                    naxis1 = fits_header.get('NAXIS1')
                    naxis2 = fits_header.get('NAXIS2')
                    if naxis1 and naxis2:
                        header['NAXIS1'] = naxis1
                        header['NAXIS2'] = naxis2
                        _, _, _, radius = get_image_center_coords(header)


            if radius is not None:
                Image.update(coord_radius=radius).where(Image.file == row.file_id).execute()
                updated += 1
            else:
                print(f"  [{i}/{total}] SKIP file_id={row.file_id}: no NAXIS in WCS or FITS header", file=sys.stderr)
                skipped += 1
        except Exception as e:
            print(f"  [{i}/{total}] SKIP file_id={row.file_id}: {e}", file=sys.stderr)
            skipped += 1

        if i % 100 == 0 or i == total:
            print(f"  {i}/{total} processed, {updated} updated, {skipped} skipped")

    db.close()
    print("Done.")


if __name__ == "__main__":
    main()

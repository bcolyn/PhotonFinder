import bz2
import fnmatch
import gzip
import json
import logging
import lzma
import os
import shutil
import typing
from logging import log, INFO, DEBUG, ERROR, WARN
from pathlib import Path

import fs.path
from astropy.io.fits import Header, Card
from fs.base import FS
from fs.info import Info
from peewee import JOIN

from photonfinder import image_hdus, local_time, plate_solution
from photonfinder.core import StatusReporter, compress, decompress
from photonfinder.fits_handlers import normalize_fits_header
from photonfinder.models import File, LibraryRoot, FitsHeader, Image, norm_db_path, FileWCS, ImageStats

compressed_exts = {
    ".xz": lzma.open,
    ".gz": gzip.open,
    ".bz2": bz2.open
}


def fopen(filename: Path | str):
    """
    Open a file handle for reading, handling compressed files transparently.

    Returns:
        A file-like object opened in binary mode
    """
    file_ext = compression_ext(filename)
    if file_ext is not None:
        fn = compressed_exts[file_ext]
        return fn(filename, mode='rb')
    else:
        return open(filename, mode='rb')


def compression_ext(filename) -> typing.Optional[str]:
    """The key in ``compressed_exts`` for *filename*'s extension, matched case-insensitively
    (``IMG.FITS.GZ`` -> ``.gz``), or None if it isn't a compressed file."""
    ext = os.path.splitext(filename)[1].lower()
    return ext if ext in compressed_exts else None


def is_compressed(filename):
    return compression_ext(filename) is not None


def read_fits_header(file: str | Path, status_reporter: StatusReporter = None) -> bytes | None:
    """
    The FITS header of the file's first image: the header blocks (2880 bytes each, up to and
    including the one with END) of the primary HDU, or, when the primary holds no image, the
    first image extension's header merged with the primary's (see `image_hdus.merged_header`).
    A file with no image HDU at all gets its primary header.

    Returns:
        The header bytes, or None if the file is not a valid FITS file
    """
    if status_reporter:
        status_reporter.update_status(f"Reading FITS header for {file}...", bulk=True)
    scan = image_hdus.scan_fits(file, first_only=True)
    if scan is None:
        return None
    image = image_hdus.first_image(scan)
    if image is None or image.index == 0:
        return scan.primary_header
    try:
        merged = image_hdus.merged_header(Header.fromstring(scan.primary_header), Header.fromstring(image.header))
        return merged.tostring().encode("ascii")
    except Exception as e:  # pylint: disable=broad-exception-caught
        log(WARN, f"Cannot merge the headers of HDU {image.index} in {file}, using the primary: {e}")
        return scan.primary_header


def read_fits_headers(file: str | Path, status_reporter: StatusReporter = None) -> image_hdus.HduScan | None:
    """The headers the index needs from a FITS file: the primary's, and each image extension's.
    A compressed file is read past its primary only when that has EXTEND = T, as finding its
    extensions means decompressing all of it. None if it isn't a readable FITS file."""
    if status_reporter:
        status_reporter.update_status(f"Reading FITS header for {file}...", bulk=True)
    return image_hdus.scan_fits(file, require_extend=is_compressed(file))


def read_xisf_headers(file: str | Path, status_reporter: StatusReporter = None) -> image_hdus.HduScan | None:
    """The FITS keywords of every image in an XISF file (see image_hdus). None if it has none."""
    if status_reporter:
        status_reporter.update_status(f"Reading XISF header for {file}...", bulk=True)
    scan = image_hdus.scan_xisf(file)
    return scan if scan and scan.images else None


def read_xisf_header(file: str | Path, status_reporter: StatusReporter = None) -> (tuple[bytes, dict[str, list]] |
                                                                                   tuple[None, None]):
    """The FITS keywords of the file's first image that has them, as JSON bytes and as the dict
    get_images_metadata() returns, with NAXISn filled in from the geometry."""
    if status_reporter:
        status_reporter.update_status(f"Reading XISF header for {file}...", bulk=True)
    scan = image_hdus.scan_xisf(file)
    image = image_hdus.first_image(scan) if scan else None
    if image is None:
        return None, None
    return image.header, image.keywords


class ScanCancelled(Exception):
    """The scan was stopped. Raised from anywhere in it, so that a partial result isn't used."""


def update_fits_header_cache(change_list, status_reporter, settings, cancelled=lambda: False):
    """
    Update the FITS header cache based on the changes in the change_list.

    Args:
        change_list: A ChangeList object with new_files, changed_files, and removed_files
        status_reporter: StatusReporter to update status
        settings:  Settings object to update known keywords
        cancelled: Checked between files. Stopping early is safe: the next scan picks up the
            files still without a header, through check_missing_header_cache.
    """
    if status_reporter:
        status_reporter.update_status("Updating FITS header cache...")

    # Process new files
    for file in [*change_list.new_files, *change_list.changed_files]:
        if cancelled():
            raise ScanCancelled()
        _handle_file_metadata_safely(file, status_reporter, settings)

    # Process removed files
    for file in change_list.removed_files:
        try:
            # Delete any Image objects for this file
            Image.delete().where(Image.file == file).execute()
            # Delete the FitsHeader
            FitsHeader.delete().where(FitsHeader.file == file).execute()
        except FitsHeader.DoesNotExist:
            pass

    if status_reporter:
        status_reporter.update_status("FITS header cache updated.")


def _handle_file_metadata_safely(file, status_reporter, settings):
    """One unreadable file must not abort header processing for the rest of the scan."""
    try:
        _handle_file_metadata(file, status_reporter, settings)
    except Exception as e:
        log(WARN, f"Error processing header of {file.name}: {e}", exc_info=True)
        if status_reporter:
            status_reporter.update_status(f"Error processing header of {file.name}: {e}")


def _read_file_headers(file: File, status_reporter) -> image_hdus.HduScan | None:
    if Importer.is_fits_by_name(file.name):
        return read_fits_headers(file.full_filename(), status_reporter)
    if Importer.is_xisf_by_name(file.name):
        return read_xisf_headers(file.full_filename(), status_reporter)
    return None


def _header_rows(scan: image_hdus.HduScan) -> list[tuple[int, bytes]]:
    """The FitsHeader rows for a file, as (hdu, raw header). A FITS file always has its primary
    header as hdu 0, image or not, so an extension's header can be merged with it later; then
    one row per image extension, its own header unmerged. An XISF file has one row per image."""
    if scan.primary_header is None:
        return [(image.index, image.header) for image in scan.images]
    return [(0, scan.primary_header)] + [(image.index, image.header) for image in scan.images if image.index]


def _headers_to_index(scan: image_hdus.HduScan) -> list[tuple[int, str | None, Header]]:
    """(hdu, extname, header to normalise) per image of a freshly read file. A FITS extension's
    header is merged with the primary's. A file without any image HDU is indexed from its
    primary header, as before files could hold several images."""
    if scan.primary_header is None:
        return [(image.index, image.extname, header_from_xisf_dict(image.keywords)) for image in scan.images]
    primary = parse_FITS_header(scan.primary_header)
    return [(image.index, image.extname,
             primary if image.index == 0 else image_hdus.merged_header(primary, parse_FITS_header(image.header)))
            for image in scan.images] or [(0, None, primary)]


def _store_file_index(file: File, scan: image_hdus.HduScan, status_reporter, settings) -> None:
    """Write a file's header rows, then its Image rows from them. The header rows are written
    first, so a header that can't be indexed is still cached and not read again every scan."""
    for hdu, raw in _header_rows(scan):
        FitsHeader(file=file, hdu=hdu, header=compress(raw)).save()
    headers = _headers_to_index(scan)
    images, embedded = index_images(file, headers, plate_solution.load(file), status_reporter,
                                    local_time.obs_zone(settings))
    plate_solution.save_indexed(file, images, embedded)
    for _, _, header in headers:
        settings.add_known_fits_keywords(header.keys())


def _handle_file_metadata(file, status_reporter, settings):
    scan = _read_file_headers(file, status_reporter)
    if scan is not None:
        _store_file_index(file, scan, status_reporter, settings)


def repair_header(header: Header) -> Header:
    """Fix unparsable cards in-place (astropy's Header has no verify(); only Card does).

    Some files contain values with embedded tab/control characters that astropy's own
    ``verify('fix')`` cannot repair (it refuses to "fix" a value into something still
    non-ASCII). For those, fall back to stripping the offending characters from the raw
    card image and reparsing. As a last resort, blank the value rather than let a single
    bad card raise a VerifyError whenever anything later touches the header (``dict()``,
    ``.get()``, ``.keys()``, etc).
    """
    for i, card in enumerate(header.cards):
        try:
            card.verify('fix')
            _ = card.value  # force lazy parsing now so failures surface here, not later
        except Exception:
            # card.image (the public property) itself calls verify() and re-raises the
            # same error, so read the raw stored image string directly to sanitize it.
            image = (card._image or '').replace('\t', ' ')
            try:
                fixed = Card.fromstring(image)
                fixed.verify('fix')
                header[i] = (fixed.value, fixed.comment)
            except Exception:
                log(WARN, f"Dropping unparsable header card {card.keyword!r} in position {i}")
                header[i] = ('', card.comment)
    return header


def header_from_xisf_dict(header_dict: dict[str, list]) -> Header:
    result = Header()

    def try_parse(value):
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value

    for key, values in header_dict.items():
        for value_dict in values:
            # Cards for COMMENT or HISTORY must have their comment in 'value', not comment. Go figure.
            if key in Card._commentary_keywords:
                card = Card(key, value_dict['comment'])
            else:
                card = Card(key, try_parse(value_dict['value']), value_dict['comment'])
            result.append(card)
    return repair_header(result)


def parse_FITS_header(header_bytes: bytes) -> Header:
    if b'\x09' in header_bytes:
        # log(WARN, f"FITS header contains tab characters: {header_bytes}")
        header_bytes = header_bytes.replace(b'\x09', b' ')
    return repair_header(Header.fromstring(header_bytes))


def embedded_solution(file: File, header: Header) -> Header | None:
    """The plate solution a header carries (e.g. written by N.I.N.A. or PixInsight), or None.

    For XISF files the solution is flipped vertically (FITS row order), matching how solutions
    are recorded elsewhere.
    """
    from astropy.wcs import WCS
    from photonfinder.platesolver import has_been_plate_solved, extract_wcs_cards, flip_wcs_vertical
    if not has_been_plate_solved(header):
        return None
    solution = extract_wcs_cards(header)
    if Importer.is_xisf_by_name(file.name):
        naxis2 = solution.get('NAXIS2')
        wcs_obj = flip_wcs_vertical(WCS(solution), naxis2)
        flipped = wcs_obj.to_header(relax=True)
        for k in ('NAXIS', 'NAXIS1', 'NAXIS2'):
            if k in solution:
                flipped[k] = solution[k]
        solution = flipped
    return solution


def index_images(file: File, headers: list[tuple[int, str | None, Header]], stored: Header | None,
                 status_reporter=None, zone=None) -> tuple[list[Image], Header | None]:
    """What a file's headers give the index: an Image row per image, and the plate solution
    embedded in a header when none is `stored` for the file. Import, header repair and Rebuild
    index all come through here, so a file is indexed the same way by each.

    `headers` is (hdu, extname, header) per image, an extension's header merged with the
    primary's. The embedded solution is the first usable one, in file order. A solution, stored
    or embedded, gives an image its centre, radius and size. In a file with several images it
    goes only to those the size of the solved image (the solution's NAXIS1/2, else the size of
    the image it came from or, when stored, of the first image): layers of one stack share its
    geometry, a layer of another size is left unsolved. A stored solution that can't be used
    raises; an unusable embedded one is ignored. Nothing is written.

    Each image gets its local time from where and when the first header says, with its own
    date_obs (local_time.file_local_times), falling back to `zone`, the obs_timezone setting.
    The background fill (local_time.update_library) reads the primary header, whose cards
    every merged header carries, so both give the same.
    """
    indexed = []
    for hdu, extname, header in headers:
        image = normalize_fits_header(file, header, status_reporter)
        if image is not None:
            image.hdu, image.extname = hdu, extname
        indexed.append((image, header))
    images = [image for image, _ in indexed if image is not None]
    if images:
        info = local_time.session_info_from_header(headers[0][2])
        times = local_time.file_local_times([image.date_obs for image in images], info, zone)
        for image, (local, source) in zip(images, times):
            image.local_time, image.local_time_source = local, source

    embedded, solved = None, indexed[0][0] if indexed else None
    if stored is None:
        for image, header in indexed:
            try:
                found = embedded_solution(file, header)
                if found is not None:  # checked even without an Image, so only a usable one is stored
                    plate_solution.place(image if image is not None else Image(), found)
                    embedded, solved = found, image
                    break
            except Exception as e:
                log(WARN, f"Ignoring unusable WCS in header of {file.name}: {e}")
    solution = stored if stored is not None else embedded
    if solution is None:
        return images, None

    size = (solution.get('NAXIS1'), solution.get('NAXIS2'))
    if not all(size):
        size = (solved.width, solved.height) if solved is not None else (None, None)
    for image in images:
        if len(indexed) == 1 or (all(size) and (image.width, image.height) == size):
            plate_solution.place(image, solution)
    return images, embedded


REINDEX_BATCH_SIZE = 1000


def _headers_from_cache(rows: list) -> list[tuple[int, str | None, Header]]:
    """(hdu, extname, header to normalise) per image, from a file's cached FitsHeader rows
    (hdu order), as `_headers_to_index` gives them for a freshly read file."""
    decoded = [(row.hdu, decode_header_blob(row.header), decompress(row.header).startswith(b'{')) for row in rows]
    if any(is_xisf for _, _, is_xisf in decoded):
        return [(hdu, None, header) for hdu, header, _ in decoded]
    primary = next((header for hdu, header, _ in decoded if hdu == 0), None)
    images = [(hdu, header.get('EXTNAME') if hdu else None,
               header if hdu == 0 or primary is None else image_hdus.merged_header(primary, header))
              for hdu, header, _ in decoded if image_hdus.is_image_header(header)]
    if images or primary is None:
        return images
    return [(0, None, primary)]


def reindex_library(status_reporter, settings) -> None:
    """Rebuild index: replace each file's Image rows with ones built from its cached headers, as
    import builds them, and store a solution embedded in a header when the file has none.

    Rows are replaced a batch of files at a time, not cleared first, so stopping at any point
    leaves complete rows for every file. A batch holds all of a file's header rows, so an
    extension's header can be merged with the primary's. Each batch is read in full before it is
    written: a write made behind an open query fails at once with "database is locked" if
    anything else has written to the library, and keeps failing. A file whose headers can't be
    indexed keeps the rows it had and is reported; an error writing a batch stops the rebuild
    and is raised.
    """
    total_headers = FitsHeader.select().count()
    zone = local_time.obs_zone(settings)
    status_reporter.update_status(f"Processing {total_headers} FITS headers...")
    processed = 0
    last_file = 0
    while file_ids := [row[0] for row in FitsHeader
                       .select(FitsHeader.file).distinct()
                       .where(FitsHeader.file > last_file)
                       .order_by(FitsHeader.file)
                       .limit(REINDEX_BATCH_SIZE).tuples()]:
        last_file = file_ids[-1]
        by_file = {}
        for header_record in (FitsHeader
                              .select(FitsHeader, File, FileWCS)
                              .join(File)
                              .join(FileWCS, JOIN.LEFT_OUTER)
                              .where(FitsHeader.file.in_(file_ids))
                              .order_by(FitsHeader.file, FitsHeader.hdu)):
            by_file.setdefault(header_record.file.rowid, []).append(header_record)
        # The XISF image ids aren't in the cached keywords, so the Image rows keep theirs.
        extnames = {(file_id, hdu): extname for file_id, hdu, extname
                    in Image.select(Image.file, Image.hdu, Image.extname)
                    .where(Image.file.in_(file_ids) & Image.extname.is_null(False)).tuples()}
        rows = []
        for header_records in by_file.values():
            file = header_records[0].file
            try:
                stored_record = getattr(file, 'filewcs', None)
                stored = plate_solution.decode(stored_record.wcs) if stored_record else None
                headers = [(hdu, extname or extnames.get((file.rowid, hdu)), header)
                           for hdu, extname, header in _headers_from_cache(header_records)]
                for _, _, header in headers:
                    settings.add_known_fits_keywords(header.keys())
                rows.append((file, *index_images(file, headers, stored, status_reporter, zone)))
            except Exception as e:
                # Its current rows stay, as nothing better came out of its headers.
                log(ERROR, f"Error processing header of {file.name}: {e}", exc_info=True)
                status_reporter.update_status(f"Error processing header of {file.name}: {e}")

        with FitsHeader._meta.database.atomic():
            for file, images, embedded in rows:
                plate_solution.save_indexed(file, images, embedded)

        processed += sum(len(records) for records in by_file.values())
        status_reporter.update_status(f"Processed {processed}/{total_headers} headers...", True)
    status_reporter.update_status("Image metadata reindexing complete!")


def cached_header(file: File, hdu: int = 0) -> Header | None:
    """The cached header of one of the file's images, as it was indexed: a FITS extension's
    header merged with the primary's. None when the file has no cached header for it."""
    query = (FitsHeader.select(FitsHeader.hdu, FitsHeader.header)
             .where((FitsHeader.file == file) & FitsHeader.hdu.in_({0, hdu})))
    rows = {row.hdu: row.header for row in query}  # pylint: disable=not-an-iterable  # Peewee query
    if hdu not in rows:
        return None
    header = decode_header_blob(rows[hdu])
    if hdu == 0 or 0 not in rows or decompress(rows[hdu]).startswith(b'{'):
        return header
    return image_hdus.merged_header(decode_header_blob(rows[0]), header)


def decode_header_blob(blob: bytes) -> Header:
    """Decompress a stored header blob and parse it into an astropy ``Header``.

    Headers are stored compressed; XISF headers as a JSON keyword dict, FITS headers
    as the raw header bytes. The blob's first byte distinguishes the two. This is the
    single decode path used wherever a cached header is read back from the database.
    """
    raw = decompress(blob)
    if raw.startswith(b'{'):
        return header_from_xisf_dict(json.loads(raw))
    return parse_FITS_header(raw)


def check_missing_header_cache(status_reporter, settings, cancelled=lambda: False):
    """
    Process any FITS files that don't have a corresponding header entry.
    Also creates Image objects from the FITS headers using the appropriate handler.
    """
    if status_reporter:
        status_reporter.update_status("Checking for FITS files without header cache entries...")

    # Find all files that don't have a corresponding FitsHeader entry
    # Use a LEFT OUTER JOIN to find files without headers
    missing_header_files = (File
                            .select(File)
                            .join(FitsHeader, JOIN.LEFT_OUTER, on=(File.rowid == FitsHeader.file))
                            .where(FitsHeader.rowid.is_null()))

    # Process these files as new files
    for file in missing_header_files:
        if cancelled():
            raise ScanCancelled()
        _handle_file_metadata_safely(file, status_reporter, settings)

    if status_reporter:
        status_reporter.update_status("FITS header cache updated.")


def _is_truncated_header(raw: bytes) -> bool:
    """A cached FITS header without an END card. An older version stopped reading at any card
    starting with END, such as ENDTIME, and lost the blocks after it."""
    return not raw.startswith(b'{') and not image_hdus.has_end_card(raw[-image_hdus.FITS_BLOCK:])


def _repair_truncated_header(file: File, status_reporter, settings):
    """Read the header from disk again. The file itself is unchanged, so what was derived from
    it stays: the plate solution with the position it gave the image, and the stats."""
    scan = read_fits_headers(file.full_filename(), status_reporter)
    if scan is None:
        log(WARN, f"Could not read the full header of {file.name} again, keeping the cached part")
        return
    with FitsHeader._meta.database.atomic():
        FitsHeader.delete().where(FitsHeader.file == file).execute()
        _store_file_index(file, scan, status_reporter, settings)


def repair_truncated_header_cache(status_reporter, settings, cancelled=lambda: False):
    """Find cached headers cut short by an older version and read them from disk again, as a scan
    sees these files as unchanged. Checking is cheap: about 0.5s for 50,000 headers."""
    if status_reporter:
        status_reporter.update_status("Checking for incomplete FITS headers in the cache...")

    # Finished before anything is written: a write can't follow an open read on this connection.
    file_ids = list(dict.fromkeys(file_id for file_id, blob
                                  in FitsHeader.select(FitsHeader.file, FitsHeader.header).tuples().iterator()
                                  if _is_truncated_header(decompress(blob))))

    for file_id in file_ids:
        if cancelled():
            raise ScanCancelled()
        file = File.get_by_id(file_id)
        try:
            _repair_truncated_header(file, status_reporter, settings)
        except Exception as e:
            log(WARN, f"Error repairing header of {file.name}: {e}", exc_info=True)
            if status_reporter:
                status_reporter.update_status(f"Error repairing header of {file.name}: {e}")

    if status_reporter:
        status_reporter.update_status("FITS header cache updated.")


class ChangeList:
    def __init__(self, new_files=None, removed_files=None, changed_ids=None, changed_files=None,
                 recompressed_files=None):
        if recompressed_files is None:
            recompressed_files = list()
        if changed_files is None:
            changed_files = list()
        if changed_ids is None:
            changed_ids = list()
        if removed_files is None:
            removed_files = list()
        if new_files is None:
            new_files = list()
        self.new_files = new_files
        self.removed_files = removed_files
        self.changed_ids = changed_ids
        self.changed_files = changed_files
        # Files only renamed to another compression variant (same mtime): content is
        # unchanged, so their header, image, plate solution and stats are kept.
        self.recompressed_files = recompressed_files

    def merge(self, other: 'ChangeList'):
        self.new_files += other.new_files
        self.changed_files += other.changed_files
        self.changed_ids += other.changed_ids
        self.removed_files += other.removed_files
        self.recompressed_files += other.recompressed_files

    def apply_all(self):
        with File._meta.database.atomic():
            # note that bulk_create does not assign the rowid, and we need this later on, hence the loop.
            for file in self.new_files:
                file.save(force_insert=True)
            for file in self.removed_files:
                file.delete_instance()
            for file in self.changed_files:
                file.save()
                # If the file is changed, we want to re-examine its contents but don't disconnect it from any projects
                Image.delete().where(Image.file == file).execute()
                FitsHeader.delete().where(FitsHeader.file == file).execute()
                FileWCS.delete().where(FileWCS.file == file).execute()
                ImageStats.delete().where(ImageStats.file == file).execute()
            for file in self.recompressed_files:
                file.save()


def possible_compressed_variants(filename: str):
    variants = set()
    variants.add(filename)
    basename = os.path.splitext(filename)[0] if is_compressed(filename) else filename
    variants.add(basename)
    for ext in compressed_exts.keys():
        variants.add(basename + ext)
        variants.add(basename + ext.upper())
    return list(variants)


def is_compressible(name: str) -> bool:
    """True for uncompressed FITS files that can be externally compressed."""
    return Importer.is_fits_by_name(name) and not is_compressed(name)


def compress_file(src_path: str, ext: str, verify: bool, level: int = 9,
                  keep_source: bool = False) -> tuple[str, int]:
    """Compress *src_path* in-place using the compression format identified by *ext*.

    Writes to a temporary <src_path>+ext+'.part', optionally verifies the round-trip
    byte count, preserves mtime and only then renames it to <src_path>+ext, so a
    failure never leaves a partial archive that a rescan would pick up.
    Deletes the original unless *keep_source* is set, and returns (dest_path, compressed_size).
    Raises FileExistsError if <src_path>+ext already exists, ValueError if *src_path* is already
    compressed or on verification failure.
    The original is untouched whenever an exception is raised.
    """
    if is_compressed(src_path):
        raise ValueError(f"{src_path} is already compressed")
    open_fn = compressed_exts[ext]
    dest_path = src_path + ext
    if os.path.exists(dest_path):
        raise FileExistsError(f"{dest_path} already exists")
    tmp_path = dest_path + '.part'
    write_kwargs = {'preset': level} if ext == '.xz' else {'compresslevel': level}

    try:
        with open(src_path, 'rb') as src, open_fn(tmp_path, 'wb', **write_kwargs) as dst:
            shutil.copyfileobj(src, dst)

        if verify:
            original_size = os.path.getsize(src_path)
            read_back = 0
            with open_fn(tmp_path, 'rb') as f:
                while chunk := f.read(65536):
                    read_back += len(chunk)
            if read_back != original_size:
                raise ValueError(
                    f"Verification failed: decompressed {read_back} bytes, "
                    f"expected {original_size}")

        shutil.copystat(src_path, tmp_path)
        os.rename(tmp_path, dest_path)  # fails rather than overwrites if dest appeared meanwhile
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    new_size = os.path.getsize(dest_path)
    if not keep_source:
        os.unlink(src_path)
    return dest_path, new_size


class Importer:
    status: StatusReporter

    def __init__(self, context, bad_file_patterns: str = "bad*", bad_dir_patterns: str = "bad*"):
        super().__init__()
        self.status = context.status_reporter
        self.bad_file_patterns = bad_file_patterns.split("|")
        self.bad_dir_patterns = bad_dir_patterns.split("|")
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def marked_bad(self, f: Info) -> bool:
        """" skips over files that are marked bad """
        lc_filename = f.name.lower()
        if f.is_file:
            return any(fnmatch.fnmatch(lc_filename, pattern) for pattern in self.bad_file_patterns)
        elif f.is_dir:
            return any(fnmatch.fnmatch(lc_filename, pattern) for pattern in self.bad_dir_patterns)
        else:
            return False

    @staticmethod
    def is_fits_by_name(filename: str) -> bool:
        # Handle compressed files
        if filename.lower().endswith(tuple(compressed_exts.keys())):
            filename = os.path.splitext(filename)[0]
        return filename.lower().endswith(".fit") or filename.lower().endswith(".fits")

    @staticmethod
    def is_xisf_by_name(filename: str) -> bool:
        # we don't support externally compressed xisf
        return filename.lower().endswith(".xisf")

    @staticmethod
    def is_fits(f: Info) -> bool:
        return Importer.is_fits_by_name(f.name)

    @staticmethod
    def is_xisf(f: Info) -> bool:
        return Importer.is_xisf_by_name(f.name)

    @staticmethod
    def is_compressed(f: Info) -> bool:
        filename = f.name
        return is_compressed(filename)

    def _file_filter(self, x: Info):
        return (Importer.is_fits(x) or Importer.is_xisf(x)) and not self.marked_bad(x)

    def _dir_filter(self, x: Info):
        return not self.marked_bad(x)

    def import_roots(self, roots: typing.Sequence[LibraryRoot] = None) -> typing.Iterable[ChangeList]:
        if not roots:
            roots = list(LibraryRoot.select().execute())
        log(INFO, "Scanning for new/changed files in libraries...")
        for root in roots:
            self.status.update_status(f"importing library root: {root.name}")
            try:
                open_fs = fs.open_fs(root.path, writeable=False)
                ls = open_fs.listdir(".")
                if len(ls) == 0:
                    self.status.update_status(f"Skipping empty library: {root.name}")
                    continue
                change_list = self.import_files_from(open_fs, root)
                yield change_list
            except ScanCancelled:
                raise
            except Exception as err:
                self.status.update_status(f"Error importing library: {root.name} - {str(err)}")
        self.status.update_status("done.")

    def import_files_from(self, root_fs: FS, root: LibraryRoot, start_dir='.') -> ChangeList:
        dir_queue: typing.List[str] = [start_dir]
        all_dirs = set(map(norm_db_path, dir_queue))
        result = ChangeList()
        while len(dir_queue) > 0:
            current_dir: str = dir_queue.pop()
            self.status.update_status(f"Scanning directory: {root.name}/{current_dir}", bulk=True)
            filtered_files = set()
            entries = list(root_fs.scandir(current_dir, namespaces=['details']))
            names_on_disk = {e.name for e in entries if e.is_file}
            entry: Info
            for entry in entries:
                if self.cancelled:
                    # Not a partial result: folders not reached yet would count as deleted.
                    raise ScanCancelled()
                if entry.is_dir:
                    if self._dir_filter(entry):
                        dir_path = fs.path.join(current_dir, entry.name)
                        dir_queue.append(dir_path)
                        all_dirs.add(norm_db_path(dir_path))
                    else:
                        self.status.update_status(f"Skipping directory: {root.name}/{current_dir}/{entry.name}",
                                                  bulk=False)
                if entry.is_file:
                    if self._file_filter(entry):
                        name = self._import_file(entry, current_dir, root, result, names_on_disk)
                        filtered_files.add(name)
                    else:
                        # only log this if it was a file that the user could expect us to handle anyway
                        if self.is_fits(entry) or self.is_xisf(entry):
                            self.status.update_status(f"Skipping file: {root.name}/{current_dir}/{entry.name}",
                                                      bulk=False)

            # evict deleted files
            query = File.select(File.rowid, File.name).where(File.root == root, File.path == norm_db_path(current_dir))
            for file in query.execute():
                if file.name not in filtered_files:
                    result.removed_files.append(file)

        # clean up deleted dirs
        if start_dir == ".":  # only if we saw the whole filesystem
            query = File.select(File.path).distinct().where(File.root == root)
            for (old_path,) in query.tuples().iterator():
                if old_path not in all_dirs:
                    files = File.select().where(File.root == root, File.path == old_path).execute()
                    for file in files:
                        result.removed_files.append(file)

        return result

    def _import_file(self, file: Info, rel_path, root, changelist,
                     names_on_disk: typing.Collection[str]) -> str:
        """Record *file* in *changelist*. *names_on_disk* are the names of all files in its
        directory, used to tell a recompressed file from a compressed copy beside it."""
        log(DEBUG, "[root %s] record file stats: %s/%s", root.name, rel_path, file.name)

        rel_path = norm_db_path(rel_path)

        mtime_millis = int(file.modified.timestamp() * 1000)

        possible_file_names = possible_compressed_variants(file.name)
        query = (File.select().where(
            (File.root == root) & (File.path == rel_path) & (File.name.in_(possible_file_names, ))))

        results = list(query.execute())
        db_file = next((r for r in results if r.name == file.name), None)
        if db_file is None:
            # A row under another compressed variant of this name is this file renamed by
            # (de)compression -- unless that other copy is still on disk, in which case the
            # two are separate files.
            db_file = next((r for r in results if r.name not in names_on_disk), None)

        if db_file is None:
            model = File(name=file.name, path=rel_path, root=root, size=file.size, mtime_millis=mtime_millis)
            changelist.new_files.append(model)
            return file.name
        else:
            oldname = db_file.name
            if db_file.name != file.name and db_file.mtime_millis == mtime_millis:
                # Compressed or decompressed with mtime preserved: same content, new name.
                db_file.name = file.name
                db_file.size = file.size
                changelist.recompressed_files.append(db_file)
            elif db_file.mtime_millis != mtime_millis or db_file.size != file.size:
                db_file.name = file.name
                db_file.size = file.size
                db_file.mtime_millis = mtime_millis
                changelist.changed_ids.append(db_file.rowid)
                changelist.changed_files.append(db_file)
            return oldname

    def import_selection(self, files: typing.List[str]) -> ChangeList:
        changes = ChangeList()
        for file in files:
            root = LibraryRoot.find_for_file(file)
            if root is None:
                logging.warning(f"Skipping {file}: not in a library")
                self.status.update_status(f"Skipping {file}: not in a library")
                continue
            root_fs = fs.open_fs(root.path, writeable=False)
            rel_path = fs.path.relativefrom(root.path, file)

            file_info = root_fs.getinfo(rel_path, ["details"])
            if file_info.is_dir:
                root_changes = self.import_files_from(root_fs, root, rel_path)
                changes.merge(root_changes)
            elif file_info.is_file:
                rel_path_parent = str(Path(rel_path).parent)
                names_on_disk = {e.name for e in root_fs.scandir(rel_path_parent) if e.is_file}
                self._import_file(file_info, rel_path_parent, root, changes, names_on_disk)
            else:
                logging.error(f"Unknow type {file} of type {file_info.type}")
                self.status.update_status(f"Unknow type {file} of type {file_info.type}")
        return changes

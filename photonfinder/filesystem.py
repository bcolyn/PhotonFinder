import bz2
import fnmatch
import gzip
import json
import logging
import lzma
import os
import typing
from logging import log, INFO, DEBUG, ERROR, WARN
from pathlib import Path

import fs.path
from astropy.io.fits import Header, Card
from fs.base import FS
from fs.info import Info
from peewee import JOIN
from xisf import XISF

from photonfinder.core import StatusReporter, compress
from photonfinder.fits_handlers import normalize_fits_header
from photonfinder.models import File, LibraryRoot, FitsHeader, Image, norm_db_path, FileWCS

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
    file_ext = os.path.splitext(filename)[1]
    if file_ext in compressed_exts.keys():
        fn = compressed_exts[file_ext]
        return fn(filename, mode='rb')
    else:
        return open(filename, mode='rb')


def is_compressed(filename):
    last_ext = os.path.splitext(filename)[1]
    return last_ext in compressed_exts.keys()


def read_fits_header(file: str | Path, status_reporter: StatusReporter = None) -> bytes | None:
    """
    Read the FITS header from a file.

    A FITS header consists of one or more blocks of 2880 bytes.
    Each block contains 36 lines of 80 characters.
    The header ends with a line that starts with 'END'.

    Args:
        file: filename or Path object

    Returns:
        The FITS header as a string, or None if the file is not a valid FITS file
    """
    if status_reporter:
        status_reporter.update_status(f"Reading FITS header for {file}...", bulk=True)
    try:
        with fopen(file) as f:
            header = bytes()
            block_size = 2880
            line_size = 80
            lines_per_block = block_size // line_size

            while True:
                block = f.read(block_size)
                if not block:
                    # End of file without finding END
                    log(ERROR, f"End block not found in FITS file: {file}")
                    return None

                if len(header) == 0:  # first block
                    if not block[:80].decode('ascii').startswith('SIMPLE  ='):
                        log(ERROR, f"Cannot decode as FITS file: {file}")
                        return None

                header += block

                # search for END in the block
                for i in range(lines_per_block):
                    start = i * line_size
                    end = start + line_size
                    if start >= len(block):
                        break

                    line = block[start:end].decode('ascii', errors='replace').rstrip()

                    # Check if this line starts with 'END'
                    if line.startswith('END'):
                        return header

    except Exception as e:
        log(DEBUG, f"Error reading FITS header from {file}: {str(e)}")
        return None


def read_xisf_header(file: str | Path, status_reporter: StatusReporter = None) -> (tuple[bytes, dict[str, list]] |
                                                                                   tuple[None, None]):
    try:
        if status_reporter:
            status_reporter.update_status(f"Reading XISF header for {file}...", bulk=True)
        xisf = XISF(file)
        metas = xisf.get_images_metadata()
        for meta in metas:
            if "FITSKeywords" in meta:
                fits_keywords = meta["FITSKeywords"]
                return json.dumps(fits_keywords).encode(), fits_keywords
    except Exception as e:
        log(WARN, f"Error reading XISF header from {file}: {str(e)}")
        return None, None


def update_fits_header_cache(change_list, status_reporter, settings):
    """
    Update the FITS header cache based on the changes in the change_list.

    Args:
        change_list: A ChangeList object with new_files, changed_files, and removed_files
        status_reporter: StatusReporter to update status
        settings:  Settings object to update known keywords
    """
    if status_reporter:
        status_reporter.update_status("Updating FITS header cache...")

    # Process new files
    for file in [*change_list.new_files, *change_list.changed_files]:
        _handle_file_metadata(file, status_reporter, settings)

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


def _handle_file_metadata(file, status_reporter, settings):
    header = None
    if Importer.is_fits_by_name(file.name):
        header_bytes = read_fits_header(file.full_filename(), status_reporter)
        if header_bytes:
            FitsHeader(file=file, header=compress(header_bytes)).save()
            # Normalize the header and create an Image object if possible
            header = parse_FITS_header(header_bytes)
    elif Importer.is_xisf_by_name(file.name):
        header_bytes, header_dict = read_xisf_header(file.full_filename(), status_reporter)
        if header_bytes:
            FitsHeader(file=file, header=compress(header_bytes)).save()
            header = header_from_xisf_dict(header_dict)
    if header is not None:
        settings.add_known_fits_keywords(header.keys())
        image = normalize_fits_header(file, header, status_reporter)
        if image is not None:
            Image.insert(image.__data__).on_conflict_replace().execute()


def header_from_xisf_dict(header_dict: dict[str, list]):
    result = Header()
    for key, values in header_dict.items():
        for value_dict in values:
            # Cards for COMMENT or HISTORY must have their comment in 'value', not comment. Go figure.
            if key in Card._commentary_keywords:
                card = Card(key, value_dict['comment'])
            else:
                card = Card(key, value_dict['value'], value_dict['comment'])
            card.verify('fix')
            result.append(card)
    return result


def parse_FITS_header(header_bytes: bytes) -> Header:
    if b'\x09' in header_bytes:
        # log(WARN, f"FITS header contains tab characters: {header_bytes}")
        header_bytes = header_bytes.replace(b'\x09', b' ')
    return Header.fromstring(header_bytes)


def check_missing_header_cache(status_reporter, settings):
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
        _handle_file_metadata(file, status_reporter, settings)

    if status_reporter:
        status_reporter.update_status("FITS header cache updated.")


class ChangeList:
    def __init__(self, new_files=None, removed_files=None, changed_ids=None, changed_files=None):
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

    def merge(self, other: 'ChangeList'):
        self.new_files += other.new_files
        self.changed_files += other.changed_files
        self.changed_ids += other.changed_ids
        self.removed_files += other.removed_files

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


def possible_compressed_variants(filename: str):
    variants = set()
    variants.add(filename)
    basename = os.path.splitext(filename)[0] if is_compressed(filename) else filename
    variants.add(basename)
    for ext in compressed_exts.keys():
        variants.add(basename + ext)
    return list(variants)


class Importer:
    status: StatusReporter

    def __init__(self, context, bad_file_patterns: str = "bad*", bad_dir_patterns: str = "bad*"):
        super().__init__()
        self.status = context.status_reporter
        self.bad_file_patterns = bad_file_patterns.split("|")
        self.bad_dir_patterns = bad_dir_patterns.split("|")

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

    def import_all(self) -> typing.Iterable[ChangeList]:
        roots: typing.Sequence[LibraryRoot] = list(LibraryRoot.select().execute())
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
            entry: Info
            for entry in root_fs.scandir(current_dir, namespaces=['details']):
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
                        name = self._import_file(entry, current_dir, root, result)
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

    def _import_file(self, file: Info, rel_path, root, changelist) -> str:
        log(DEBUG, "[root %s] record file stats: %s/%s", root.name, rel_path, file.name)

        rel_path = norm_db_path(rel_path)

        mtime_millis = int(file.modified.timestamp() * 1000)

        possible_file_names = possible_compressed_variants(file.name)
        query = (File.select().where(
            (File.root == root) & (File.path == rel_path) & (File.name.in_(possible_file_names, ))))

        results = list(query.execute())
        db_file = None
        if len(results) == 1:
            db_file = results[0]
        elif len(results) > 1:
            exact_match = list(filter(lambda x: x.name == file.name, results))
            db_file = (exact_match + results)[0]

        if db_file is None:
            model = File(name=file.name, path=rel_path, root=root, size=file.size, mtime_millis=mtime_millis)
            changelist.new_files.append(model)
            return file.name
        else:
            oldname = db_file.name
            if db_file.mtime_millis != mtime_millis or db_file.size != file.size:
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
            root_fs = fs.open_fs(root.path, writeable=False)
            rel_path = fs.path.relativefrom(root.path, file)

            file_info = root_fs.getinfo(rel_path, ["details"])
            if file_info.is_dir:
                root_changes = self.import_files_from(root_fs, root, rel_path)
                changes.merge(root_changes)
            elif file_info.is_file:
                rel_path_parent = str(Path(rel_path).parent)
                self._import_file(file_info, rel_path_parent, root, changes)
            else:
                logging.error(f"Unknow type {file} of type {file_info.type}");
                self.status.update_status(f"Unknow type {file} of type {file_info.type}")
        return changes

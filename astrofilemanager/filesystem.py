import bz2
import gzip
import lzma
import os
import typing
from logging import log, INFO, DEBUG
import fs.path
from fs.base import FS
from fs.info import Info
from peewee import JOIN

from astrofilemanager.core import StatusReporter
from astrofilemanager.models import File, LibraryRoot, FitsHeader

compressed_exts = [".xz", ".gz", ".bz2"]


def fopen(file):
    """
    Open a file handle for reading, handling compressed files automatically.

    Args:
        file: A File object with a get_file_exts() method and full_filename() method

    Returns:
        A file-like object opened in binary mode
    """
    file_exts = file.get_file_exts()
    if len(file_exts) and file_exts[-1] == "xz":
        return lzma.open(file.full_filename(), mode='rb')
    elif len(file_exts) and file_exts[-1] == "gz":
        return gzip.open(file.full_filename(), mode='rb')
    elif len(file_exts) and file_exts[-1] == "bz2":
        return bz2.open(file.full_filename(), mode='rb')
    else:
        return open(file.full_filename(), mode='rb')


def read_fits_header(file, status_reporter: StatusReporter = None):
    """
    Read the FITS header from a file.

    A FITS header consists of one or more blocks of 2880 bytes.
    Each block contains 36 lines of 80 characters.
    The header ends with a line that starts with 'END'.

    Args:
        file: A File object with a get_file_exts() method and full_filename() method

    Returns:
        The FITS header as a string, or None if the file is not a valid FITS file
    """
    if status_reporter:
        status_reporter.update_status(f"Reading FITS header for {file.name}...", bulk=True)
    try:
        with fopen(file) as f:
            header = ""
            block_size = 2880
            line_size = 80
            lines_per_block = block_size // line_size

            while True:
                block = f.read(block_size)
                if not block:
                    # End of file without finding END
                    return None

                # Process each line in the block
                for i in range(lines_per_block):
                    start = i * line_size
                    end = start + line_size
                    if start >= len(block):
                        break

                    line = block[start:end].decode('ascii', errors='replace').rstrip()
                    header += line + "\n"

                    # Check if this line starts with 'END'
                    if line.startswith('END'):
                        return header

        return None
    except Exception as e:
        log(DEBUG, f"Error reading FITS header: {str(e)}")
        return None


def update_fits_header_cache(change_list, status_reporter=None):
    """
    Update the FITS header cache based on the changes in the change_list.

    Args:
        change_list: A ChangeList object with new_files, changed_files, and removed_files
        status_reporter: Optional StatusReporter to update status
    """
    if status_reporter:
        status_reporter.update_status("Updating FITS header cache...")

    # Process new files
    for file in change_list.new_files:
        if Importer.is_fits_by_name(file.name):
            header = read_fits_header(file, status_reporter)
            if header:
                FitsHeader.create(file=file, header=header)

    # Process changed files
    for file in change_list.changed_files:
        if Importer.is_fits_by_name(file.name):
            header = read_fits_header(file, status_reporter)
            if header:
                # Try to update existing header, create if it doesn't exist
                try:
                    FitsHeader.update(header=header).where(FitsHeader.file == file)
                except FitsHeader.DoesNotExist:
                    FitsHeader.create(file=file, header=header)

    # Process removed files
    for file in change_list.removed_files:
        try:
            FitsHeader.delete_by_id(file.rowid)
        except FitsHeader.DoesNotExist:
            pass

    if status_reporter:
        status_reporter.update_status("FITS header cache updated.")


def check_missing_header_cache(status_reporter=None):
    # Process any FITS files that don't have a corresponding header entry
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
        if Importer.is_fits_by_name(file.name):
            header = read_fits_header(file)
            if header:
                FitsHeader.create(file=file, header=header)

    if status_reporter:
        status_reporter.update_status("FITS header cache updated.")


class ChangeList:
    def __init__(self):
        self.new_files: typing.List[File] = list()
        self.removed_files: typing.List[File] = list()
        self.changed_ids: typing.List[int] = list()
        self.changed_files: typing.List[File] = list()

    def apply_all(self):
        with File._meta.database.atomic():
            # note that bulk_create does not assign the rowid, and we need this later on, hence the loop.
            for file in self.new_files:
                file.save(force_insert=True)
            for file in self.removed_files:
                File.delete_by_id(file.rowid)
            for fileId in self.changed_ids:  # delete and re-create
                File.delete_by_id(fileId)
            for file in self.changed_files:
                file.save(force_insert=True)


class Importer:
    status: StatusReporter

    def __init__(self, context):
        super().__init__()
        self.status = context.status_reporter

    @staticmethod
    def marked_bad(f: Info) -> bool:
        """" skips over files that are marked bad """
        filename = f.name
        return filename.lower().startswith("bad")

    @staticmethod
    def is_fits_by_name(filename: str) -> bool:
        """Check if a filename is a FITS file based on its extension."""
        # Handle compressed files
        if filename.lower().endswith(tuple(compressed_exts)):
            filename = os.path.splitext(filename)[0]
        return filename.lower().endswith(".fit") or filename.lower().endswith(".fits")

    @staticmethod
    def is_fits(f: Info) -> bool:
        """Check if a file is a FITS file based on its Info object."""
        return Importer.is_fits_by_name(f.name)

    @staticmethod
    def is_compressed(f: Info) -> bool:
        filename = f.name
        last_ext = os.path.splitext(filename)[1]
        return last_ext in compressed_exts

    @staticmethod
    def _file_filter(x: Info):
        return Importer.is_fits(x) and not Importer.marked_bad(x)

    @staticmethod
    def _dir_filter(x: Info):
        return not Importer.marked_bad(x)

    def import_files(self) -> typing.Iterable[ChangeList]:
        roots: typing.Sequence[LibraryRoot] = list(LibraryRoot.select().execute())
        log(INFO, "Scanning for new/changed files in libraries...")
        for root in roots:
            self.status.update_status(f"importing library root: {root.name}")
            try:
                open_fs = fs.open_fs(root.path, writeable=False)
                change_list = self.import_files_from(open_fs, root)
                yield change_list
            except IOError as err:
                self.status.update_status(f"Error importing library: {root.name} - {str(err)}")
        self.status.update_status("done.")

    # TODO: check root exists and is non-empty
    def import_files_from(self, root_fs: FS, root: LibraryRoot) -> ChangeList:
        dir_queue: typing.List[str] = ['.']
        all_dirs = set(dir_queue)
        result = ChangeList()
        while len(dir_queue) > 0:
            current_dir: str = dir_queue.pop()
            filtered_files = set()
            entry: Info
            for entry in root_fs.scandir(current_dir, namespaces=['details']):
                if entry.is_dir:
                    if self._dir_filter(entry):
                        dir_path = fs.path.join(current_dir, entry.name)
                        dir_queue.append(dir_path)
                        all_dirs.add(dir_path)
                    else:
                        pass  # TODO: log skipping
                if entry.is_file:
                    if self._file_filter(entry):
                        self._import_file(entry, current_dir, root, result)
                        filtered_files.add(entry.name)
                    else:
                        pass  # TODO: log skipping

            # evict deleted files
            query = File.select(File.rowid, File.name).where(File.root == root, File.path == current_dir)
            for file in query.execute():
                if file.name not in filtered_files:
                    result.removed_files.append(file)

        # clean up deleted dirs
        query = File.select(File.path).distinct().where(File.root == root)
        for (old_path,) in query.tuples().iterator():
            if old_path not in all_dirs:
                files = File.select().where(File.root == root, File.path == old_path).execute()
                for file in files:
                    result.removed_files.append(file)

        return result

    def _import_file(self, file: Info, rel_path, root, changelist):
        log(DEBUG, "[root %s] record file stats: %s/%s", root.name, rel_path, file.name)

        mtime_millis = int(file.modified.timestamp() * 1000)

        db_file = File.select(File.rowid, File.size, File.mtime_millis) \
            .where(File.root == root, File.path == rel_path, File.name == file.name).get_or_none()
        if db_file is None:
            model = File(name=file.name, path=rel_path, root=root, size=file.size, mtime_millis=mtime_millis)
            changelist.new_files.append(model)
        else:
            if db_file.mtime_millis != mtime_millis or db_file.size != file.size:
                model = File(name=file.name, path=rel_path, root=root, size=file.size, mtime_millis=mtime_millis)
                changelist.changed_ids.append(db_file.rowid)
                changelist.changed_files.append(model)

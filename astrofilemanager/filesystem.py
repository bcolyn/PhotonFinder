import bz2
import gzip
import lzma
import os
import typing
from logging import log, INFO, DEBUG
import fs.path
from fs.base import FS
from fs.info import Info

from astrofilemanager.core import StatusReporter
from astrofilemanager.models import File, LibraryRoot


compressed_exts = [".xz", ".gz", ".bz2"]

def fopen(self):
    file_exts = self.get_file_exts()
    if len(file_exts) and file_exts[-1] == "xz":
        return lzma.open(self.full_filename(), mode='rb')
    elif len(file_exts) and file_exts[-1] == "gz":
        return gzip.open(self.full_filename(), mode='rb')
    elif len(file_exts) and file_exts[-1] == "bz2":
        return bz2.open(self.full_filename(), mode='rb')
    else:
        return open(self.full_filename(), mode='rb')


class ChangeList:
    def __init__(self):
        self.new_files = list()
        self.removed_files = list()
        self.changed_ids = list()
        self.changed_files = list()

    def apply_all(self):
        File.bulk_create(self.new_files, batch_size=100)
        for file in self.removed_files:
            File.delete_by_id(file.rowid)
        for id in self.changed_ids:  # delete and re-create
            File.delete_by_id(id)
        File.bulk_create(self.changed_files, batch_size=100)


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
    def is_fits(f: Info) -> bool:
        filename = f.name
        if Importer.is_compressed(f):
            filename = os.path.splitext(filename)[0]
        return filename.lower().endswith(".fit") or filename.lower().endswith(".fits")

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

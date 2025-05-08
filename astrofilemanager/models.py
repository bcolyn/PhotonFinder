import os
import typing
from pathlib import Path

from peewee import *
from playhouse.sqlite_ext import RowIDField


def auto_str(cls):
    def __str__(self):
        return '%s(%s)' % (
            type(self).__name__,
            ', '.join('%s=%s' % item for item in _get_data_dict(self).items())
        )

    cls.__str__ = __str__
    cls.__repr__ = __str__
    return cls


def _get_data_dict(obj):
    if isinstance(obj, Model):
        return obj.__data__
    else:
        return vars(obj)


@auto_str
class LibraryRoot(Model):
    """
    Model representing a library root directory.
    A library root is a directory that contains files to be managed by the application.
    """
    name = CharField(unique=True)
    path = CharField(unique=True)

    class Meta:
        # This will be set dynamically when the database connection is provided
        database = None

    @classmethod
    def initialize(cls, database: Database):
        """
        Initialize the model with a database connection.
        """
        cls._meta.database = database

        # Set the database for the model
        cls.bind(database, bind_refs=False, bind_backrefs=False)

        # Create the table if it doesn't exist
        if not cls.table_exists():
            cls.create_table()

    @staticmethod
    def is_valid_path(path_str: str) -> bool:
        """
        Check if the given path is a valid directory.
        
        Args:
            path_str: Path string to validate
            
        Returns:
            bool: True if the path is a valid directory, False otherwise
        """
        path = Path(path_str)
        return path.exists() and path.is_dir()

    def __eq__(self, other):
        return self.name == other.name and self.path == other.path


compressed_exts = ["xz", "gz", "bz2"]


@auto_str
class File(Model):
    rowid = RowIDField()
    root = ForeignKeyField(LibraryRoot, on_delete='CASCADE')
    path = CharField()
    name = CharField()
    size = IntegerField()
    # type = CharField(index=True, null=True)  # FITS
    # compression = CharField(null=True)  # xz, gz, lz4
    mtime_millis = IntegerField()

    # sha1 = BlobField(index=True, null=True)  # or CharField?

    class Meta:
        indexes = (
            (('root', 'path', 'name'), True),  # Note the trailing comma!
        )

    def get_file_exts(self) -> typing.List[str]:
        parts = str(self.name).lower().rsplit('.', maxsplit=2)
        if len(parts) and parts[0] == '':  # hidden file that starts with a '.'
            parts = parts[1:]
        if len(parts) == 1:  # no ext
            return []
        ext = parts[-1]
        if ext in compressed_exts:  # is compressed?
            return parts[-2:]
        else:
            return parts[-1:]

    def full_filename(self) -> str:
        return os.path.join(str(self.root.path), str(self.path), str(self.name))


CORE_MODELS = [LibraryRoot, File]

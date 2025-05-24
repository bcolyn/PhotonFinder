import os
import typing
from dataclasses import dataclass, field
from pathlib import Path

from peewee import *
from playhouse.sqlite_ext import RowIDField


@dataclass
class RootAndPath:
    root_id: int
    path: str


@dataclass
class SearchCriteria:
    paths: list[RootAndPath] = field(default_factory=list)
    paths_as_prefix: bool = True
    filter: str = ""
    type: str = ""


def auto_str(cls):
    def _get_data_dict(obj):
        if isinstance(obj, Model):
            return obj.__data__
        else:
            return vars(obj)

    def __str__(self):
        return '%s(%s)' % (
            type(self).__name__,
            ', '.join('%s=%s' % item for item in _get_data_dict(self).items())
        )

    cls.__str__ = __str__
    cls.__repr__ = __str__
    return cls


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


@auto_str
class File(Model):
    rowid = RowIDField()
    root = ForeignKeyField(LibraryRoot, on_delete='CASCADE')
    path = CharField()
    name = CharField()
    size = IntegerField()
    mtime_millis = IntegerField()

    class Meta:
        database = None
        indexes = (
            (('root', 'path', 'name'), True),  # Note the trailing comma!
        )

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

    def full_filename(self) -> str:
        return os.path.join(str(self.root.path), str(self.path), str(self.name))


class Image(Model):
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', index=True, unique=True)
    imageType = CharField(null=True, index=True)
    filter = CharField(null=True, index=True)
    exposure = DoubleField(null=True, index=True)
    gain = IntegerField(null=True, index=True)
    binning = IntegerField(null=True)
    setTemp = DoubleField(null=True)

    class Meta:
        database = None


class FitsHeader(Model):
    """
    Model representing a FITS header.
    This is a cache of the header information from FITS files.
    """
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', unique=True)
    header = BlobField()  # Caches the raw header as bytes

    class Meta:
        database = None
        indexes = (
            (('file',), True),
        )

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


CORE_MODELS = [LibraryRoot, File, Image, FitsHeader]

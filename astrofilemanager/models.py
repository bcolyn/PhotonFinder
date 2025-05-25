import os
import typing
from dataclasses import dataclass, field
from pathlib import Path

from peewee import *
from playhouse.sqlite_ext import RowIDField


@dataclass
class RootAndPath:
    root_id: typing.Optional[int]
    path: typing.Optional[str]


@dataclass
class SearchCriteria:
    paths: list[RootAndPath] = field(default_factory=list)
    paths_as_prefix: bool = True
    filter: str = ""
    type: str = ""
    camera: str = ""
    name: str = ""
    exposure: str = ""
    use_coordinates: bool = False
    use_date: bool = False


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
    rowid = RowIDField()
    name = CharField(unique=True)
    path = CharField(unique=True)

    class Meta:
        # This will be set dynamically when the database connection is provided
        database = None

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

    @staticmethod
    def _apply_search_criteria(query, criteria):
        """Apply search criteria to the query."""
        conditions = []

        # Filter by paths
        if criteria.paths:
            path_conditions = []
            if criteria.paths_as_prefix:
                for full_path in criteria.paths:
                    if full_path.root_id is None and full_path.path is None:  # all libraries is included
                        path_conditions = []  # short-circuit the case where all libraries are included
                        break
                    elif full_path.path is None:  # a root library is included, anything below that is good
                        path_conditions.append(File.root == full_path.root_id)
                    else:  # normal path
                        path_conditions.append(
                            (File.root == full_path.root_id) & (File.path.startswith(full_path.path)))
            else:  # only in exact directory
                for full_path in criteria.paths:
                    if full_path.root_id is None and full_path.path is None:  # all libraries
                        continue  # nothing can be in the 'all libraries' path, so skip it'
                    elif full_path.path is None:  # a root library is included
                        path_conditions.append((File.root == full_path.root_id) & (File.path == ""))
                    else:
                        # Match files exactly in this path
                        path_conditions.append((File.root == full_path.root_id) & (File.path == full_path.path))
            if path_conditions:
                # Combine the path conditions with OR (__or__) if there are multiple conditions
                # if len(path_conditions) == 1:
                #     conditions.append(path_conditions[0])
                # elif len(path_conditions) > 1:
                combined = path_conditions[0]
                for condition in path_conditions[1:]:
                    combined = combined | condition
                conditions.append(combined)

        # Filter by file type
        if criteria.type:
            conditions.append(Image.imageType == criteria.type)

        # Filter by filter
        if criteria.filter:
            conditions.append(Image.filter == criteria.filter)

        # Apply additional criteria if available
        if hasattr(criteria, 'camera') and criteria.camera:
            # This would need to be mapped to the appropriate field in the database
            pass

        if hasattr(criteria, 'name') and criteria.name:
            conditions.append(File.name.contains(criteria.name))

        if hasattr(criteria, 'exposure') and criteria.exposure:
            try:
                exp = float(criteria.exposure)
                conditions.append(Image.exposure == exp)
            except (ValueError, TypeError):
                pass

        # Apply all conditions to the query
        for condition in conditions:
            query = query.where(condition)

        return query

    @staticmethod
    def get_filters(search_criteria: SearchCriteria):
        filters = set()
        query = Image.select(fn.Distinct(Image.filter)).join(File, JOIN.INNER, on=(File.rowid == Image.file))
        query = Image._apply_search_criteria(query, search_criteria)
        for row in query.execute():
            filters.add(row.filter)
        return filters


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


CORE_MODELS = [LibraryRoot, File, Image, FitsHeader]

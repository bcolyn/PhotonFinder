import os
import typing
from dataclasses import dataclass, field
from datetime import datetime
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
    filter: str | None = ""
    type: str | None = ""
    camera: str | None = ""

    object_name: str = ""
    exposure: str = ""
    telescope: str = ""
    binning: str = ""
    gain: str = ""
    temperature: str = ""
    use_coordinates: bool = False
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None


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
    image_type = CharField(null=True, index=True)
    camera = CharField(null=True, index=True)
    filter = CharField(null=True, index=True)
    exposure = DoubleField(null=True, index=True)
    gain = IntegerField(null=True, index=True)
    binning = IntegerField(null=True)
    set_temp = DoubleField(null=True)
    telescope = CharField(null=True, index=True)
    object_name = CharField(null=True, index=True)
    date_obs = DateTimeField(null=True, index=True)
    coord_ra = FloatField(null=True, index=True)  # Right Ascension as floating point value
    coord_dec = FloatField(null=True, index=True)  # Declination as floating point value
    coord_pix256 = IntegerField(null=True, index=True)  # HEALPix value (nside=256)

    class Meta:
        database = None

    @staticmethod
    def apply_search_criteria(query, criteria, exclude_ref=None):
        """Apply search criteria to the query."""
        conditions = []

        # Filter by paths
        if criteria.paths and exclude_ref is not File.path:
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
                combined = path_conditions[0]
                for condition in path_conditions[1:]:
                    combined = combined | condition
                conditions.append(combined)

        # Filter by file type
        if criteria.type != "" and exclude_ref is not Image.image_type:
            conditions.append(Image.image_type == criteria.type)

        # Filter by filter
        if criteria.filter != "" and exclude_ref is not Image.filter:
            conditions.append(Image.filter == criteria.filter)

        # Apply additional criteria if available
        if criteria.camera != "" and exclude_ref is not Image.camera:
            conditions.append(Image.camera == criteria.camera)

        if criteria.object_name and exclude_ref is not Image.object_name:
            conditions.append(Image.object_name.contains(criteria.object_name))

        if criteria.exposure and exclude_ref is not Image.exposure:
            try:
                exp = float(criteria.exposure)
                conditions.append(Image.exposure == exp)
            except (ValueError, TypeError):
                pass

        if criteria.telescope and exclude_ref is not Image.telescope:
            conditions.append(Image.telescope.contains(criteria.telescope))

        if criteria.binning and exclude_ref is not Image.binning:
            try:
                bin_val = int(criteria.binning)
                conditions.append(Image.binning == bin_val)
            except (ValueError, TypeError):
                pass

        if criteria.gain and exclude_ref is not Image.gain:
            try:
                gain_val = int(criteria.gain)
                conditions.append(Image.gain == gain_val)
            except (ValueError, TypeError):
                pass

        if criteria.temperature and exclude_ref is not Image.set_temp:
            try:
                temp_val = float(criteria.temperature)
                conditions.append(Image.set_temp == temp_val)
            except (ValueError, TypeError):
                pass

        if criteria.start_datetime and exclude_ref is not Image.date_obs:
            conditions.append(Image.date_obs >= criteria.start_datetime)

        if criteria.end_datetime and exclude_ref is not Image.date_obs:
            conditions.append(Image.date_obs <= criteria.end_datetime)

        # Apply all conditions to the query
        for condition in conditions:
            query = query.where(condition)

        return query

    @staticmethod
    def get_distinct_values_available(search_criteria: SearchCriteria, field_ref) -> list[str | None]:
        query = Image.select(fn.Distinct(field_ref)).join(File, JOIN.INNER, on=(File.rowid == Image.file)).order_by(
            field_ref)
        query = Image.apply_search_criteria(query, search_criteria, field_ref)
        return list(map(lambda x: x[0], query.tuples()))

    @staticmethod
    def load_filters(search_criteria: SearchCriteria):
        return Image.get_distinct_values_available(search_criteria, Image.filter)

    @staticmethod
    def load_types(search_criteria: SearchCriteria):
        return Image.get_distinct_values_available(search_criteria, Image.image_type)

    @staticmethod
    def load_cameras(search_criteria: SearchCriteria):
        return Image.get_distinct_values_available(search_criteria, Image.camera)


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

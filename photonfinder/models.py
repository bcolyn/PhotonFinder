import os
import typing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from peewee import *
from playhouse.sqlite_ext import RowIDField


@dataclass
class RootAndPath:
    root_id: int
    root_label: str
    path: typing.Optional[str]

    def __str__(self):
        return f"{self.root_label}/{self.path}" if self.path else str(self.root_label)


@dataclass
class SearchCriteria:
    paths: list[RootAndPath] = field(default_factory=list)
    paths_as_prefix: bool = True
    filter: str | None = ""
    type: str | None = ""
    camera: str | None = ""
    file_name: str | None = None
    object_name: str = None
    exposure: str = ""
    telescope: str = ""
    binning: str = ""
    offset: int | None = None
    gain: str = ""
    temperature: str = ""
    coord_ra: str = ""  # Right Ascension in hours (can be in various formats)
    coord_dec: str = ""  # Declination in degrees (can be in various formats)
    coord_radius: float = 0.5  # Search radius in decimal degrees
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    reference_file: Optional['File'] = None

    def is_empty(self):
        return self == SearchCriteria()

    def __str__(self):
        result = []
        if len(self.paths) > 0:
            result += list(map(str, self.paths))

        for text in [self.type, self.filter, self.camera, self.file_name, self.object_name, self.exposure,
                     self.telescope, self.binning, self.gain, self.temperature]:
            if text:
                result.append(text)
        if self.coord_ra and self.coord_dec:
            result.append(f"({self.coord_ra}{self.coord_dec})+-{self.coord_radius}d")
        if self.start_datetime and self.end_datetime:
            result.append(f"{self.start_datetime.isoformat()} to {self.end_datetime.isoformat()}")
        return ', '.join(result)

    @classmethod
    def find_dark(cls, reference_frame: 'Image') -> 'SearchCriteria':
        criteria = cls()
        if reference_frame.exposure:
            criteria.exposure = str(reference_frame.exposure)
        if reference_frame.camera:
            criteria.camera = str(reference_frame.camera)
        if reference_frame.set_temp:
            criteria.temperature = str(reference_frame.set_temp)
        if reference_frame.gain:
            criteria.gain = str(reference_frame.gain)
        if reference_frame.binning:
            criteria.binning = str(reference_frame.binning)
        if reference_frame.offset:
            criteria.offset = int(reference_frame.offset)

        if reference_frame.image_type == "FLAT":
            criteria.type = "DARKFLAT"
        else:
            criteria.type = "DARK"

        reference_file = reference_frame.file
        reference_file.image = reference_frame
        criteria.reference_file = reference_file
        return criteria

    @classmethod
    def find_flat(cls, reference_frame: 'Image') -> 'SearchCriteria':
        criteria = cls()
        if reference_frame.camera:
            criteria.camera = str(reference_frame.camera)
        if reference_frame.filter:
            criteria.filter = str(reference_frame.filter)
        if reference_frame.binning:
            criteria.binning = str(reference_frame.binning)
        if reference_frame.date_obs:
            criteria.start_datetime = reference_frame.date_obs - timedelta(days=1)
            criteria.end_datetime = reference_frame.date_obs + timedelta(days=1)
        criteria.type = "FLAT"

        reference_file = reference_frame.file
        reference_file.image = reference_frame
        criteria.reference_file = reference_file
        return criteria


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
    offset = IntegerField(null=True, index=True)
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

        if criteria.file_name and exclude_ref is not File.name:
            conditions.append(File.name.contains(criteria.file_name))

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

        if criteria.offset and exclude_ref is not Image.offset:
            try:
                offset_val = int(criteria.offset)
                conditions.append(Image.offset == offset_val)
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

        # Filter by coordinates
        if criteria.coord_ra and criteria.coord_dec and exclude_ref is not Image.coord_pix256:
            try:
                from astropy.coordinates import SkyCoord
                import astropy.units as u
                from astropy_healpix import HEALPix

                # Parse RA and DEC from strings to SkyCoord
                coords = SkyCoord(criteria.coord_ra, criteria.coord_dec, unit=(u.hourangle, u.deg), frame='icrs')

                # Create HEALPix object with the same parameters as used in fits_handlers.py
                hp = HEALPix(nside=256, order='nested', frame='icrs')

                # Get pixels in the cone
                radius = criteria.coord_radius * u.deg
                pixels = hp.cone_search_skycoord(coords, radius)

                # Filter images where coord_pix256 is in the list of pixels
                if len(pixels) > 0:
                    conditions.append(Image.coord_pix256.in_(pixels.tolist()))
            except Exception as e:
                print(f"Error applying coordinates filter: {str(e)}")

        # Apply all conditions to the query
        for condition in conditions:
            query = query.where(condition)
        query.order_by(File.mtime_millis.desc())
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

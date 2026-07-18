import json
import logging
import os
import typing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cmp_to_key
from pathlib import Path
from typing import Optional

import astropy.units as u
from astropy.coordinates import SkyCoord
from peewee import *
from playhouse.sqlite_ext import RowIDField

from photonfinder.core import hp



@dataclass(frozen=True)
class RootAndPath:
    root_id: int
    root_label: str = ""  # display-only (see __str__); not used for filtering
    path: typing.Optional[str] = None

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
    header_text: str = ""
    project: 'Project' = None
    sorting_index: int | None = None
    sorting_desc: bool = True
    exposure_tolerance: float | None = None   # ±seconds; None = exact match
    temperature_tolerance: float | None = None  # ±°C; None = exact match
    plate_solved: bool | None = None  # True = solved only, False = unsolved only, None = any
    # Image dimensions (pixels)
    width_min: int | None = None
    width_max: int | None = None
    height_min: int | None = None
    height_max: int | None = None
    # Plate scale (arcsec/pixel, virtual generated column)
    scale_min: float | None = None
    scale_max: float | None = None
    # Image quality stats (from ImageStats table)
    star_count_min: int | None = None
    star_count_max: int | None = None
    fwhm_min: float | None = None
    fwhm_max: float | None = None
    background_min: float | None = None
    background_max: float | None = None
    background_rms_min: float | None = None
    background_rms_max: float | None = None
    elongation_min: float | None = None
    elongation_max: float | None = None

    def is_empty(self):
        return self == SearchCriteria()

    def __str__(self):
        result = []
        if self.paths:
            if len(self.paths) > 1:
                result.append(str(self.paths[0]) + "+" + str(len(self.paths) - 1))
            elif len(self.paths) > 0:
                result.append(str(self.paths[0]))

        for text in [self.type, self.filter, self.camera, self.file_name, self.object_name, self.exposure,
                     self.telescope, self.binning, self.gain, self.temperature, self.header_text]:
            if text:
                result.append(text)
        if self.plate_solved is True:
            result.append("Solved")
        elif self.plate_solved is False:
            result.append("Unsolved")
        if self.project:
            result.append(self.project.name)
        if self.coord_ra and self.coord_dec:
            result.append(f"({self.coord_ra}{self.coord_dec})+-{self.coord_radius}d")
        if self.start_datetime and self.end_datetime:
            result.append(f"{self.start_datetime.isoformat()} to {self.end_datetime.isoformat()}")
        if self.width_min is not None or self.width_max is not None or self.height_min is not None or self.height_max is not None:
            result.append(f"Size: {self.width_min or ''}–{self.width_max or ''}×{self.height_min or ''}–{self.height_max or ''}")
        if self.scale_min is not None or self.scale_max is not None:
            result.append(f"Scale: {self.scale_min or ''}–{self.scale_max or ''} arcsec/px")
        if any(v is not None for v in [self.star_count_min, self.star_count_max, self.fwhm_min, self.fwhm_max,
                                        self.background_min, self.background_max, self.background_rms_min,
                                        self.background_rms_max, self.elongation_min, self.elongation_max]):
            result.append("Image Statistics filter")
        return ', '.join(result)

    @staticmethod
    def _with_reference(criteria: 'SearchCriteria', reference_frame: 'Image') -> 'SearchCriteria':
        """Attach the reference frame's File (with its Image) to the criteria."""
        if reference_file := reference_frame.file:
            reference_file.image = reference_frame
            criteria.reference_file = reference_file
        return criteria

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

        criteria.type = "DARK"
        return cls._with_reference(criteria, reference_frame)

    @classmethod
    def find_flat(cls, reference_frame: 'Image', timedelta_days=7) -> 'SearchCriteria':
        criteria = cls()
        if reference_frame.camera:
            criteria.camera = str(reference_frame.camera)
        if reference_frame.filter:
            criteria.filter = str(reference_frame.filter)
        if reference_frame.binning:
            criteria.binning = str(reference_frame.binning)
        if reference_frame.date_obs:
            criteria.start_datetime = reference_frame.date_obs - timedelta(days=timedelta_days)
            criteria.end_datetime = reference_frame.date_obs + timedelta(days=timedelta_days)
        criteria.type = "FLAT"
        return cls._with_reference(criteria, reference_frame)

    @classmethod
    def find_bias(cls, reference_frame: 'Image') -> 'SearchCriteria':
        criteria = cls()
        if reference_frame.camera:
            criteria.camera = str(reference_frame.camera)
        if reference_frame.gain:
            criteria.gain = str(reference_frame.gain)
        if reference_frame.binning:
            criteria.binning = str(reference_frame.binning)
        if reference_frame.offset:
            criteria.offset = int(reference_frame.offset)
        # note: bias should be temperature-independent, so don't match thatfor
        criteria.type = "BIAS"
        return cls._with_reference(criteria, reference_frame)

    @classmethod
    def find_dark_flat(cls, flat_frame: 'Image') -> 'SearchCriteria':
        """Find DarkFlat frames matching a given FLAT frame."""
        return SearchCriteria.find_dark(flat_frame)

    @classmethod
    def find_light(cls, reference_frame: 'Image') -> 'SearchCriteria':
        """Find LIGHT frames matching camera, filter, binning, and object."""
        criteria = cls()
        if reference_frame.camera:
            criteria.camera = str(reference_frame.camera)
        if reference_frame.filter:
            criteria.filter = str(reference_frame.filter)
        if reference_frame.binning:
            criteria.binning = str(reference_frame.binning)
        if reference_frame.object_name:
            criteria.object_name = str(reference_frame.object_name)
        criteria.type = "LIGHT"
        return cls._with_reference(criteria, reference_frame)

    @classmethod
    def find_master(cls, subs: list['Image'],
                    margin: timedelta = timedelta(minutes=5)) -> 'SearchCriteria':
        """Find master calibration frames for a set of sub calibration frames. Relies on the stacking software
        propagating some header values from the subs to the integrated master.
        """
        if not subs:
            return cls()
        ref = subs[0]
        itype = (ref.image_type or "").upper()
        _finders = {
            "DARK": cls.find_dark,
            "FLAT": cls.find_flat,
            "BIAS": cls.find_bias,
            "LIGHT": cls.find_light,
        }
        finder = _finders.get(itype)
        if finder is None:
            return cls()
        criteria = finder(ref)
        criteria.type = f"MASTER {itype}"

        # Stacking software does not propagate these to the master frames
        criteria.temperature = None
        criteria.temperature_tolerance = None
        criteria.offset = None
        criteria.gain = None # For MASTER DARK, it seems these are missing sometimes

        # date_obs is assumed to be preserved, and the main link on how to find a master from its subs
        # date_obs point to the start of the "observation" so for an integration it is the date_obs of the
        # earliest sub, give or take a bit of rounding.
        dates = [s.date_obs for s in subs if s.date_obs is not None]
        if dates:
            criteria.start_datetime = min(dates) - margin
            criteria.end_datetime = min(dates) + margin
        else:
            criteria.start_datetime = None
            criteria.end_datetime = None
        return criteria

    @staticmethod
    def _serialize(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, File):
            return value.rowid
        if isinstance(value, Project):
            return value.rowid
        else:
            return value.__dict__

    def to_json(self):
        return SearchCriteria._to_json(self)

    @staticmethod
    def _to_json(value: 'SearchCriteria | typing.Iterable[SearchCriteria]') -> str:
        return json.dumps(value, default=SearchCriteria._serialize, sort_keys=True, indent=4)

    list_to_json = _to_json

    @classmethod
    def from_json(cls, json_str: str):
        value = json.loads(json_str)
        return SearchCriteria._inflate(value)

    @staticmethod
    def _inflate(value):
        if isinstance(value, list):
            return [SearchCriteria._inflate(x) for x in value]
        elif isinstance(value, dict):
            return SearchCriteria._inflate_dict(value)
        else:
            raise ValueError(f"Can't inflate value {value}")

    @staticmethod
    def _inflate_dict(data_dict):
        if data_dict.get('reference_file'): # re-inflate the reference file
            file_id =  data_dict['reference_file']
            data_dict['reference_file'] = (File.select(File, Image, LibraryRoot)
                       .join_from(File, LibraryRoot)
                       .join_from(File, Image, JOIN.LEFT_OUTER)
                       .where(File.rowid==file_id)).get()
        if data_dict.get('project'):  # re-inflate the project
            project_id = int(data_dict.get('project'))
            if project_id > 0:
                data_dict['project'] = Project.get(Project.rowid == data_dict['project'])
            else:
                data_dict['project'] = NO_PROJECT
        if data_dict.get('paths', None):
            data_dict['paths'] = [RootAndPath(**x) for x in data_dict['paths']]
        if data_dict.get('start_datetime', None):
            data_dict['start_datetime'] = datetime.fromisoformat(data_dict['start_datetime'])
        if data_dict.get('end_datetime', None):
            data_dict['end_datetime'] = datetime.fromisoformat(data_dict['end_datetime'])
        return SearchCriteria(**data_dict)


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


def norm_db_path(rel_path: str | None):
    if rel_path is None:
        return None
    if rel_path == "" or rel_path == ".":
        return ""
    rel_path = norm_db_path_sep(rel_path)
    rel_path = rel_path + "/" if rel_path[-1] != "/" else rel_path
    return rel_path


def norm_db_path_sep(rel_path):
    return rel_path.replace("\\", "/")


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

    @staticmethod
    def find_for_file(fs_item: str) -> Optional['LibraryRoot']:
        return LibraryRoot.select().where(fn.LIKE(LibraryRoot.path + "%", fs_item)).get_or_none()

    def __eq__(self, other):
        return self.name == other.name and self.path == other.path


@auto_str
class File(Model):
    rowid = RowIDField()
    root = ForeignKeyField(LibraryRoot, on_delete='CASCADE')
    path = CharField()
    name = CharField(index=True, null=False)
    size = IntegerField()
    mtime_millis = IntegerField()

    class Meta:
        database = None
        indexes = (
            (('root', 'path', 'name'), True),  # Note the trailing comma!
        )

    def full_filename(self) -> str:
        return os.path.join(str(self.root.path), str(self.path), str(self.name))

    @classmethod
    def find_by_filename(cls, full_path: str) -> Optional['File']:
        normalized_path = norm_db_path_sep(full_path)
        filename = str(Path(normalized_path).name)
        query = (File.select(File, LibraryRoot)
                 .join(LibraryRoot)
                 .where(File.name == filename)
                 .where(fn.LOWER(LibraryRoot.path + File.path + File.name) == fn.LOWER(normalized_path)))
        return query.first()

    @staticmethod
    def remove_already_mapped(project: 'Project', selected_files: typing.List['File']) -> typing.List['File']:
        selected_file_ids = [file.rowid for file in selected_files]
        already_linked_file_ids = (
            ProjectFile
            .select(ProjectFile.file_id)
            .where(
                (ProjectFile.project == project) &
                (ProjectFile.file_id.in_(selected_file_ids))
            )
            .distinct()
        )
        already_linked_ids_set = set(row.file_id for row in already_linked_file_ids)
        return [file for file in selected_files if file.rowid not in already_linked_ids_set]


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
    coord_radius = FloatField(null=True)  # Half-diagonal of image FOV in degrees
    width = IntegerField(null=True)   # Image width in pixels (NAXIS1)
    height = IntegerField(null=True)  # Image height in pixels (NAXIS2)
    coord_scale = FloatField(null=True, constraints=[SQL(
        'GENERATED ALWAYS AS ('
        'ROUND((coord_radius * 2.0 * 3600.0) /'
        ' SQRT(CAST(width AS REAL) * width + CAST(height AS REAL) * height), 2)'
        ') VIRTUAL'
    )])

    class Meta:
        database = None

    def get_sky_coord(self) -> SkyCoord | None:
        return SkyCoord(self.coord_ra, self.coord_dec, unit=u.deg,
                        frame='icrs') if self.coord_ra and self.coord_dec else None

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
                            (File.root == full_path.root_id) & (File.path.startswith(norm_db_path(full_path.path))))
            else:  # only in exact directory
                for full_path in criteria.paths:
                    if full_path.root_id is None and full_path.path is None:  # all libraries
                        continue  # nothing can be in the 'all libraries' path, so skip it'
                    elif full_path.path is None:  # a root library is included
                        path_conditions.append((File.root == full_path.root_id) & (File.path == norm_db_path(".")))
                    else:
                        # Match files exactly in this path
                        path_conditions.append(
                            (File.root == full_path.root_id) & (File.path == norm_db_path(full_path.path)))
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
                if criteria.exposure_tolerance is not None:
                    tol = criteria.exposure_tolerance
                    conditions.append(Image.exposure.between(exp - tol, exp + tol))
                else:
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
                if criteria.temperature_tolerance is not None:
                    tol = criteria.temperature_tolerance
                    conditions.append(Image.set_temp.between(temp_val - tol, temp_val + tol))
                else:
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
                # Parse RA and DEC from strings to SkyCoord
                coords = SkyCoord(criteria.coord_ra, criteria.coord_dec, unit=(u.hourangle, u.deg), frame='icrs')
                # Get pixels in the cone
                radius = criteria.coord_radius * u.deg
                pixels = hp.cone_search_skycoord(coords, radius)

                # Filter images where coord_pix256 is in the list of pixels
                if len(pixels) > 0:
                    conditions.append(Image.coord_pix256.in_(pixels.tolist()))
            except Exception as e:
                logging.error(f"Error applying coordinates filter: {str(e)}")

        if criteria.header_text:
            query = query.join_from(File, FitsHeader, JOIN.LEFT_OUTER)
            try:
                if '=' in criteria.header_text:
                    key, value = criteria.header_text.split('=')
                    conditions.append(
                        fn.decompress_header_value(FitsHeader.header, key.strip()) == float(value.strip()))
                elif '<' in criteria.header_text:
                    key, value = criteria.header_text.split('<')
                    conditions.append(fn.decompress_header_value(FitsHeader.header, key.strip()) < float(value.strip()))
                elif '>' in criteria.header_text:
                    key, value = criteria.header_text.split('>')
                    conditions.append(fn.decompress_header_value(FitsHeader.header, key.strip()) > float(value.strip()))
                else:
                    conditions.append(fn.decompress(FitsHeader.header).contains(criteria.header_text))
            except ValueError:
                conditions.append(fn.decompress(FitsHeader.header).contains(criteria.header_text))

        if criteria.width_min is not None:
            conditions.append(Image.width >= criteria.width_min)
        if criteria.width_max is not None:
            conditions.append(Image.width <= criteria.width_max)
        if criteria.height_min is not None:
            conditions.append(Image.height >= criteria.height_min)
        if criteria.height_max is not None:
            conditions.append(Image.height <= criteria.height_max)

        if criteria.scale_min is not None:
            conditions.append(Image.coord_scale >= criteria.scale_min)
        if criteria.scale_max is not None:
            conditions.append(Image.coord_scale <= criteria.scale_max)

        # ImageStats filter: use a subquery to avoid conflicting with any pre-existing
        # LEFT OUTER JOIN on ImageStats that callers (e.g. BackgroundLoader) may have added.
        _stats_subq_conditions = []
        if criteria.star_count_min is not None:
            _stats_subq_conditions.append(ImageStats.star_count >= criteria.star_count_min)
        if criteria.star_count_max is not None:
            _stats_subq_conditions.append(ImageStats.star_count <= criteria.star_count_max)
        if criteria.fwhm_min is not None:
            _stats_subq_conditions.append(ImageStats.fwhm_median >= criteria.fwhm_min)
        if criteria.fwhm_max is not None:
            _stats_subq_conditions.append(ImageStats.fwhm_median <= criteria.fwhm_max)
        if criteria.background_min is not None:
            _stats_subq_conditions.append(ImageStats.background_median >= criteria.background_min)
        if criteria.background_max is not None:
            _stats_subq_conditions.append(ImageStats.background_median <= criteria.background_max)
        if criteria.background_rms_min is not None:
            _stats_subq_conditions.append(ImageStats.background_rms >= criteria.background_rms_min)
        if criteria.background_rms_max is not None:
            _stats_subq_conditions.append(ImageStats.background_rms <= criteria.background_rms_max)
        if criteria.elongation_min is not None:
            _stats_subq_conditions.append(ImageStats.elongation_median >= criteria.elongation_min)
        if criteria.elongation_max is not None:
            _stats_subq_conditions.append(ImageStats.elongation_median <= criteria.elongation_max)
        if _stats_subq_conditions:
            stats_subq = ImageStats.select(ImageStats.file)
            for cond in _stats_subq_conditions:
                stats_subq = stats_subq.where(cond)
            conditions.append(File.rowid.in_(stats_subq))

        if criteria.plate_solved is True:
            conditions.append(File.rowid.in_(FileWCS.select(FileWCS.file)))
        elif criteria.plate_solved is False:
            conditions.append(File.rowid.not_in(FileWCS.select(FileWCS.file)))

        if criteria.project:
            if criteria.project.rowid > 0:
                query = query.join_from(File, ProjectFile)
                conditions.append(ProjectFile.project == criteria.project)
            else:
                query = query.join_from(File, ProjectFile, JOIN.LEFT_OUTER)
                conditions.append(ProjectFile.project.is_null(True))

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


# Remove coord_scale from Peewee's write-path field registry so it is never
# included in INSERT / UPDATE / bulk_create (SQLite rejects writes to GENERATED columns).
# The class attribute (FieldAccessor) is intentionally kept for use in WHERE/ORDER BY.
_cs = Image._meta.fields.pop('coord_scale', None)
if _cs:
    Image._meta.sorted_field_names.remove('coord_scale')
    Image._meta.columns.pop(_cs.column_name, None)
del _cs


class Project(Model):
    rowid = RowIDField()
    name = CharField(unique=True)
    last_change = DateTimeField(index=True, null=True)

    @staticmethod
    def find_recent() -> typing.List['Project']:
        return list(Project.select().order_by(Project.last_change.desc()).limit(10))

    @staticmethod
    def find_nearby(coord: SkyCoord) -> typing.List['Project']:
        radius = 1 * u.deg
        pixels = list(hp.cone_search_skycoord(coord, radius))

        rn = fn.ROW_NUMBER().over(
            partition_by=[ProjectFile.project],
            order_by=[Image.date_obs.desc()]
        )

        image_coord = (
            Image.select(Image.coord_ra, Image.coord_dec, Image.coord_pix256, ProjectFile.project.alias("project_id"),
                         rn.alias('rn'))
            .join(File)
            .join(ProjectFile)
            .where(Image.coord_pix256.in_(pixels))
            .order_by(Image.date_obs))

        raw_list = list(Project.select(Project,
                                       image_coord.c.coord_ra.alias("coord_ra"),
                                       image_coord.c.coord_dec.alias("coord_dec")
                                       ).join_from(Project, image_coord,
                                                   on=((Project.rowid == image_coord.c.project_id) & (
                                                           image_coord.c.rn == 1))))

        def dist(project: Project):
            return coord.separation(project.image.get_sky_coord())

        raw_list.sort(key=cmp_to_key(lambda p1, p2: dist(p1) - dist(p2)))
        return raw_list[:9]

    @staticmethod
    def list_projects_with_image_data() -> typing.List['Project']:
        rn = fn.ROW_NUMBER().over(
            partition_by=[ProjectFile.project],
            order_by=[Image.date_obs.desc()]
        )

        image_coord = (
            Image.select(Image.coord_ra, Image.coord_dec, ProjectFile.project.alias("project_id"), rn.alias('rn'))
            .join(File)
            .join(ProjectFile)
            .where(Image.coord_ra.is_null(False))
            .order_by(Image.date_obs))

        date_obs = (Image
                    .select(Image.date_obs)
                    .join_from(Image, File)
                    .join_from(File, ProjectFile)
                    .where(ProjectFile.project == Project.rowid)
                    .where(Image.date_obs.is_null(False))
                    .order_by(Image.date_obs.desc())
                    .limit(1))

        file_counts = (ProjectFile.select(fn.COUNT(ProjectFile.rowid))
                       .where(ProjectFile.project == Project.rowid))
        query = (Project.select(Project,
                                date_obs.alias("date_obs"),
                                file_counts.alias("file_counts"),
                                image_coord.c.coord_ra.alias("coord_ra"),
                                image_coord.c.coord_dec.alias("coord_dec"))
                 .join_from(Project, image_coord, JOIN.LEFT_OUTER,
                            on=((Project.rowid == image_coord.c.project_id) & (image_coord.c.rn <= 1)))
                 .order_by(Project.name))

        return list(query)


NO_PROJECT = Project(rowid=-1, name="No Project")


class ProjectFile(Model):
    rowid = RowIDField()
    project = ForeignKeyField(Project, on_delete='CASCADE', index=True)
    file = ForeignKeyField(File, on_delete='CASCADE', index=True)

    class Meta:
        indexes = (
            (('project', 'file'), True),  # Note the trailing comma!
        )

    def __eq__(self, other):
        if self.rowid is not None or other.rowid is not None:
            return super().__eq__(other)
        else:
            return self.project == other.project and self.file == other.file

    def __hash__(self):
        if self.rowid is not None:
            return super().__hash__()
        else:
            return hash((self.project, self.file))

    @classmethod
    def find_by_filename(cls, full_path: str, project: Project) -> Optional['ProjectFile']:
        normalized_path = norm_db_path_sep(full_path)
        filename = str(Path(normalized_path).name)
        query = (File.select(File, LibraryRoot, ProjectFile)
                 .join(LibraryRoot)
                 .join_from(File, ProjectFile, JOIN.LEFT_OUTER,
                            on=((File.rowid == ProjectFile.file) & (ProjectFile.project == project)))
                 .where(File.name == filename)
                 .where(fn.LOWER(LibraryRoot.path + File.path + File.name) == fn.LOWER(normalized_path)))
        file = query.first()
        if not file:
            return None
        if hasattr(file, 'projectfile'):
            return file.projectfile
        return ProjectFile(project=project, file=file)


class FitsHeader(Model):
    """
    Model representing a FITS header.
    This is a cache of the header information from FITS files.
    """
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', unique=True, backref='header')
    header = BlobField(null=False)  # Caches the raw header as bytes


class FileWCS(Model):
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', unique=True, backref='wcs')
    wcs = BlobField(null=False)


class ImageStats(Model):
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', unique=True, backref='imagestats')
    background_median = FloatField(null=True)
    background_rms = FloatField(null=True)
    star_count = IntegerField(null=True)
    fwhm_median = FloatField(null=True)
    elongation_median = FloatField(null=True)

    class Meta:
        database = None


CORE_MODELS = [LibraryRoot, File, Image, FitsHeader, FileWCS, Project, ProjectFile, ImageStats]


class CatalogEntry(Model):
    rowid = RowIDField()
    ra = FloatField()
    dec = FloatField()
    catalog = TextField()
    catalog_id = TextField()
    canonical_id = TextField(null=True)
    size = FloatField()
    axis_ratio = FloatField(null=True)
    angle = FloatField(null=True)
    magnitude = FloatField(null=True)
    healpix = IntegerField()

    class Meta:
        schema = 'catalog'
        table_name = 'catalog_entry'


CATALOG_MODELS = [CatalogEntry]


# Ordered list of selected columns. The position in this list is what
# SearchCriteria.sorting_index refers to (it maps to result-table columns), so the
# order must stay in sync with the SearchPanel result columns.
def _build_search_query(search_criteria: SearchCriteria):
    """Build the paginated search query shared by the GUI and the MCP server.

    Returns a tuple of (query, fields) where `fields` is the ordered list of selected
    columns used both for the SELECT and for resolving SearchCriteria.sorting_index.
    """
    project_names_subq = (
        ProjectFile
        .select(
            ProjectFile.file.alias('file_id'),
            fn.GROUP_CONCAT(Project.name).alias('project_names')
        )
        .join(Project)
        .group_by(ProjectFile.file)
    )

    fields = [File.name, Image.image_type, Image.filter, Image.exposure, Image.gain, Image.offset,
              Image.binning, Image.set_temp, Image.camera, Image.telescope, Image.object_name,
              Image.date_obs, File.path, File.size, File.mtime_millis, Image.coord_ra, Image.coord_dec,
              FileWCS.wcs.is_null(False).alias('has_wcs'),
              project_names_subq.c.project_names.alias('project_names'),
              ImageStats.background_median.alias('stats_background_median'),
              ImageStats.background_rms.alias('stats_background_rms'),
              ImageStats.star_count.alias('stats_star_count'),
              ImageStats.fwhm_median.alias('stats_fwhm_median'),
              ImageStats.elongation_median.alias('stats_elongation_median')]
    query = (File
             .select(*(fields + [File, Image, LibraryRoot]))
             .join_from(File, LibraryRoot)
             .join_from(File, Image, JOIN.LEFT_OUTER)
             .join_from(File, FileWCS, JOIN.LEFT_OUTER)
             .join_from(File, ImageStats, JOIN.LEFT_OUTER)
             .join_from(File, project_names_subq, JOIN.LEFT_OUTER,
                        on=(File.rowid == project_names_subq.c.file_id))
             )
    query = Image.apply_search_criteria(query, search_criteria)
    return query, fields


def search_files(search_criteria: SearchCriteria, page: int = 0, page_size: int = 100):
    """Run a paginated file search for the given criteria.

    Returns (rows, total, has_more) where `rows` are File model instances with joined
    Image / LibraryRoot data and aliased columns (has_wcs, project_names, stats_*).
    `page` is zero-based. Must be called with the models bound to a database
    (e.g. inside `context.database.bind_ctx(CORE_MODELS)`).
    """
    query, fields = _build_search_query(search_criteria)

    if search_criteria.sorting_index is None:
        query = query.order_by(File.root, File.path, File.name)
    else:
        field = fields[search_criteria.sorting_index]
        if field == File.name or field == File.path:
            field = field.collate("NOCASE")
        query = query.order_by(field.desc()) if search_criteria.sorting_desc else query.order_by(field.asc())

    total = query.count()
    rows = list(query.paginate(page + 1, page_size))
    has_more = (page + 1) * page_size < total
    return rows, total, has_more


_SERIALIZED_IMAGE_FIELDS = (
    "image_type", "camera", "filter", "exposure", "gain", "offset", "binning",
    "set_temp", "telescope", "object_name", "coord_ra", "coord_dec",
    "coord_radius", "width", "height",
)


def serialize_search_row(row: 'File') -> dict:
    """Flatten one search result row into a JSON-safe dict for the MCP server."""
    result = {
        "rowid": row.rowid,
        "name": row.name,
        "path": row.path,
        "size": row.size,
        "mtime_millis": row.mtime_millis,
        "full_filename": row.full_filename(),
        "root": {"rowid": row.root.rowid, "name": row.root.name, "path": row.root.path},
        "has_wcs": bool(getattr(row, "has_wcs", False)),
    }

    image = getattr(row, "image", None)
    if image is not None and image.rowid is not None:
        image_data = {name: getattr(image, name) for name in _SERIALIZED_IMAGE_FIELDS}
        date_obs = image.date_obs
        image_data["date_obs"] = date_obs.isoformat() if isinstance(date_obs, datetime) else date_obs
        result["image"] = image_data
    else:
        result["image"] = None

    if hasattr(row, "projectfile") and getattr(row.projectfile, "project_names", None):
        result["projects"] = row.projectfile.project_names.split(",")
    else:
        result["projects"] = []

    data = row.__data__
    result["stats"] = {
        "background_median": data.get("stats_background_median"),
        "background_rms": data.get("stats_background_rms"),
        "star_count": data.get("stats_star_count"),
        "fwhm_median": data.get("stats_fwhm_median"),
        "elongation_median": data.get("stats_elongation_median"),
    }
    return result

import json
import operator
import os
import typing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cmp_to_key, reduce
from pathlib import Path
from typing import Optional

import astropy.units as u
from astropy.coordinates import SkyCoord
from peewee import (JOIN, SQL, BlobField, BooleanField, Case, CharField, DateTimeField, DoubleField, FloatField,
                    ForeignKeyField, IntegerField, Model, TextField, fn)
from playhouse.sqlite_ext import RowIDField

from photonfinder.core import hp



@dataclass(frozen=True)
class RootAndPath:
    root_id: int
    root_label: str = ""  # display-only (see __str__); not used for filtering
    path: typing.Optional[str] = None

    def __str__(self):
        return f"{self.root_label}/{self.path}" if self.path else str(self.root_label)


# ImageStats.diagnosis values, written by quality_diagnosis. A frame failing several checks
# stores them joined by DIAGNOSIS_SEPARATOR. NULL is shown as DIAGNOSIS_NOT_ANALYSED.
DIAGNOSIS_OK = "OK"
DIAGNOSIS_BAD_FWHM = "Bad FWHM"
DIAGNOSIS_BAD_ELONGATION = "Bad elongation"
DIAGNOSIS_BAD_BACKGROUND = "Bad background"
DIAGNOSIS_BAD_STAR_COUNT = "Bad star count"
DIAGNOSIS_MISSING_METRICS = "Bad: missing metrics"
DIAGNOSIS_NOT_ANALYSED = "Not analysed"
DIAGNOSIS_SEPARATOR = ", "
DIAGNOSIS_BAD_VALUES = (DIAGNOSIS_BAD_FWHM, DIAGNOSIS_BAD_ELONGATION, DIAGNOSIS_BAD_BACKGROUND,
                        DIAGNOSIS_BAD_STAR_COUNT, DIAGNOSIS_MISSING_METRICS)
DIAGNOSIS_VALUES = (DIAGNOSIS_OK, *DIAGNOSIS_BAD_VALUES, DIAGNOSIS_NOT_ANALYSED)


class InvalidCriteria(ValueError):
    """A search criterion whose value can't be used. The GUI search skips such a value; MCP
    tools report it, so an agent doesn't take an unfiltered result for a filtered one."""

    def __init__(self, field_name: str, value, expected: str):
        super().__init__(f"Invalid value {value!r} for `{field_name}`: expected {expected}.")
        self.field_name = field_name


# GROUP_CONCAT separator for project names: a comma can be part of a name (merged projects are
# named "A,B"), the ASCII unit separator can't be typed into one.
PROJECT_NAME_SEP = "\x1f"


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
    coord_query: str = ""  # Object name/catalog ID the coordinates were looked up from, if any
    start_datetime: datetime | None = None  # UTC, compared with Image.date_obs
    end_datetime: datetime | None = None
    local_start: datetime | None = None  # local wall-clock time, compared with Image.local_time
    local_end: datetime | None = None
    session_night: date | None = None  # one observing night (local time - 12 h)
    # One row per group (group_key_sql) instead of one per image: in the grid, MCP search_files
    # and plate solving only. Every other consumer ignores it and works on all matching images.
    one_per_group: bool = False
    # Only the images of these groups: each a dict of GROUP_KEY values (see group_of), as a
    # grouped row's drill-in or its expansion into its members sets them.
    groups: list[dict] = field(default_factory=list)
    reference_file: Optional['File'] = None
    header_text: str = ""
    project: 'Project' = None
    sorting_index: int | None = None
    sorting_desc: bool = True
    # Named alternative to sorting_index for API/MCP callers, who can't know the
    # GUI's result-table column order. See library_query.SORTABLE_FIELDS for valid names. Takes
    # precedence over sorting_index when set; both use sorting_desc for direction.
    sorting_field: str | None = None
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
    # Quality diagnosis values to hide; a frame with any of them is hidden. Empty = no filter.
    diagnosis_hidden: list[str] = field(default_factory=list)

    def is_empty(self):
        return self == SearchCriteria()

    def validate(self) -> None:
        """Raise InvalidCriteria for the first value the library query would skip as
        unparsable. The GUI search never calls this: it ignores such values instead."""
        for name, parse, expected in (("exposure", float, "a number of seconds"),
                                      ("binning", int, 'a whole number, e.g. "2"'),
                                      ("gain", int, "a whole number"),
                                      ("temperature", float, "a number of degrees Celsius")):
            value = getattr(self, name)
            if value:
                try:
                    parse(value)
                except (ValueError, TypeError):
                    raise InvalidCriteria(name, value, expected) from None
        if bool(self.coord_ra) != bool(self.coord_dec):
            missing = "coord_dec" if self.coord_ra else "coord_ra"
            raise InvalidCriteria(missing, getattr(self, missing),
                                  "a value, as coord_ra and coord_dec only work together")
        if self.coord_ra:
            try:
                self.cone_center()
            except Exception:  # astropy raises several types for unparsable input
                raise InvalidCriteria("coord_ra/coord_dec", f"{self.coord_ra} {self.coord_dec}",
                                      'RA in hours and Dec in degrees, e.g. "00 42 44" and '
                                      '"+41 16 09", or "10.68h" and "41.27"') from None
        if self.coord_radius is not None and not self.coord_radius > 0:
            raise InvalidCriteria("coord_radius", self.coord_radius, "a radius in degrees above 0")

    def cone_center(self) -> SkyCoord:
        """The coordinates of a cone search: coord_ra in hours, coord_dec in degrees."""
        return SkyCoord(self.coord_ra, self.coord_dec, unit=(u.hourangle, u.deg), frame='icrs')

    def __str__(self):
        from photonfinder import filter_catalogue  # it builds on these models
        return filter_catalogue.summary(self)

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
        if reference_frame.set_temp is not None:
            criteria.temperature = str(reference_frame.set_temp)
        if reference_frame.gain is not None:
            criteria.gain = str(reference_frame.gain)
        if reference_frame.binning:
            criteria.binning = str(reference_frame.binning)
        if reference_frame.offset is not None:
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
        if reference_frame.gain is not None:
            criteria.gain = str(reference_frame.gain)
        if reference_frame.binning:
            criteria.binning = str(reference_frame.binning)
        if reference_frame.offset is not None:
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
        if isinstance(value, (datetime, date)):
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
        for name in ('local_start', 'local_end'):
            if data_dict.get(name):
                data_dict[name] = datetime.fromisoformat(data_dict[name])
        if data_dict.get('session_night'):
            data_dict['session_night'] = date.fromisoformat(data_dict['session_night'])
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
    # Free-form note describing what lives in this root. Surfaced in the GUI and to MCP
    # clients, which use it to pick the right root without scanning it.
    description = TextField(null=True)
    # Whether a scan imports the JPEG and PNG pictures in this root (see photonfinder.pictures).
    # Off by default; switching it off removes the root's pictures at the next scan.
    include_pictures = BooleanField(default=False, constraints=[SQL('DEFAULT 0')])

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

    def picture_count(self) -> int:
        """How many picture files (by name) the library has under this root."""
        from photonfinder.pictures import PICTURE_EXTENSIONS
        is_picture = reduce(operator.or_, (fn.LOWER(File.name).endswith(ext) for ext in PICTURE_EXTENSIONS))
        return File.select().where((File.root == self) & is_picture).count()

    @staticmethod
    def find_for_file(fs_item: str) -> Optional['LibraryRoot']:
        """The root this path is in, the innermost one when roots are nested."""
        item = norm_db_path_sep(fs_item).casefold() + "/"
        matches = [root for root in LibraryRoot.select() if item.startswith(norm_db_path(root.path).casefold())]
        return max(matches, key=lambda root: len(norm_db_path(root.path)), default=None)

    def __eq__(self, other):
        if not isinstance(other, LibraryRoot):
            return NotImplemented
        return self.name == other.name and self.path == other.path

    def __hash__(self):
        return hash((self.name, self.path))


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


def _hdu_field():
    """Which image in the file a row is about: the FITS HDU number, or the image's position in
    an XISF file (see image_hdus). 0 for the primary HDU / first image. The SQL default lets rows
    written without it, as by the schema migration, mean the first image."""
    return IntegerField(default=0, constraints=[SQL('DEFAULT 0')])


class Image(Model):
    """One image in a file. A file holds one or more: one per image HDU (FITS) or <Image> (XISF)."""
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', index=False)  # indexed by (file, hdu)
    hdu = _hdu_field()
    extname = CharField(null=True)  # EXTNAME, or the XISF image id; NULL for the first image
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
    # When it was taken in local wall-clock time, and where that came from: 'loc' (DATE-LOC),
    # 'site' (date_obs in the observatory's zone) or 'fallback' (in obs_timezone). See local_time.
    # The session night derives from it (session_night_sql), with an expression index.
    local_time = DateTimeField(null=True)
    local_time_source = CharField(null=True)
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
        indexes = ((('file', 'hdu'), True),)

    @staticmethod
    def first_of(file) -> Optional['Image']:
        """The file's first image: the one a plate solve solves (spec D3), and the one shown or
        analysed when no image is named."""
        return Image.select().where(Image.file == file).order_by(Image.hdu).first()

    def get_sky_coord(self) -> SkyCoord | None:
        return SkyCoord(self.coord_ra, self.coord_dec, unit=u.deg,
                        frame='icrs') if self.coord_ra is not None and self.coord_dec is not None else None


# Remove coord_scale from Peewee's write-path field registry so it is never
# included in INSERT / UPDATE / bulk_create (SQLite rejects writes to GENERATED columns).
# The class attribute (FieldAccessor) is intentionally kept for use in WHERE/ORDER BY.
_cs = Image._meta.fields.pop('coord_scale', None)
if _cs:
    Image._meta.sorted_field_names.remove('coord_scale')
    Image._meta.columns.pop(_cs.column_name, None)
del _cs


# The session night: the local time minus 12 hours, as a date (local_time.session_night is the
# Python twin). SQLite uses the index on it only for this exact expression, with the offset
# written in, not bound as a parameter, so every query takes it from session_night_sql.
SESSION_NIGHT_INDEX_SQL = "CREATE INDEX IF NOT EXISTS image_session_night ON image(date(local_time, '-12 hours'))"


def _night_value(value):
    return date.fromisoformat(value) if value else None


def session_night_sql():
    """The session night of an Image row in SQL: 'YYYY-MM-DD' text in the database, a date when
    selected, NULL without a local time. Compare it with ``night.isoformat()``."""
    return fn.date(Image.local_time, SQL("'-12 hours'")).python_value(_night_value)


# A group: the frames of one target on one night, as the grid's grouped mode shows them, plate
# solving picks one frame of (one per group) and the catalog report counts by. Defined here once
# so they agree. Type, library root and folder keep calibration frames and copies elsewhere
# apart; filter and exposure (to 0.01 s) split a night as a stacker would. NULLs group together.
# A picture (JPEG/PNG) is a group of its own: 'picture' is its file's rowid, NULL for any other
# image. Pictures share no metadata to group by, and one plate solution fits one picture only.
GROUP_KEY = ('image_type', 'root', 'path', 'object_name', 'filter', 'exposure', 'session_night', 'picture')


def _picture_key_sql():
    from photonfinder.pictures import PICTURE_TYPE
    return Case(None, [(Image.image_type == PICTURE_TYPE, File.rowid)], None)


def group_key_sql() -> list:
    """The group key over File and Image, in GROUP_KEY order."""
    return [Image.image_type, File.root, File.path, Image.object_name, Image.filter,
            fn.ROUND(Image.exposure, 2), session_night_sql(), _picture_key_sql()]


def group_order_sql() -> list:
    """The order of a group's frames, its representative first: the earliest date_obs (frames
    without one last), then the file and the layer."""
    return [Image.date_obs.is_null(), Image.date_obs, File.rowid, Image.hdu]


def group_of(file: 'File') -> Optional[dict]:
    """The GROUP_KEY values of a search row (a File with its Image), for SearchCriteria.groups.
    The exposure is kept as stored: the condition rounds both sides in SQL alike."""
    image = file.__dict__.get('image')
    if image is None or image.rowid is None:
        return None
    # the night as session_night_sql (and local_time.session_night) has it
    night = (image.local_time - timedelta(hours=12)).date().isoformat() if image.local_time else None
    from photonfinder.pictures import PICTURE_TYPE
    return {'image_type': image.image_type, 'root': file.root_id, 'path': file.path,
            'object_name': image.object_name, 'filter': image.filter, 'exposure': image.exposure,
            'session_night': night, 'picture': file.rowid if image.image_type == PICTURE_TYPE else None}


def group_condition(group: dict):
    """The images of one group: every key part equal, a NULL one only to NULL."""
    parts = []
    for name, column in zip(GROUP_KEY, group_key_sql()):
        value = group.get(name)
        if value is None:
            parts.append(column.is_null())
        elif name == 'exposure':
            parts.append(column == fn.ROUND(value, 2))
        else:
            parts.append(column == value)
    return reduce(operator.and_, parts)


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


    @staticmethod
    def unused_name(name: str, ignore: typing.Collection[int] = ()) -> str:
        """*name*, or *name* with " (2)", " (3)", ... appended if another project has it.
        Projects whose rowid is in *ignore* don't count."""
        def taken(candidate):
            return Project.select().where((Project.name == candidate)
                                          & Project.rowid.not_in(list(ignore))).exists()
        candidate, n = name, 1
        while taken(candidate):
            n += 1
            candidate = f"{name} ({n})"
        return candidate

    @staticmethod
    def merge(projects: typing.Sequence['Project']) -> 'Project':
        """Merge *projects* into the first one, which is named after all of them; the others are
        deleted. A file linked to several of them ends up linked once. Run it in a transaction."""
        leader, others = projects[0], projects[1:]
        other_ids = [p.rowid for p in others]
        # move each file's links over, but never one the leader (or an earlier link) already has
        keep = (ProjectFile.select(fn.MIN(ProjectFile.rowid))
                .where(ProjectFile.project.in_(other_ids))
                .where(ProjectFile.file.not_in(
                    ProjectFile.select(ProjectFile.file).where(ProjectFile.project == leader.rowid)))
                .group_by(ProjectFile.file))
        (ProjectFile.update(project=leader.rowid)
         .where(ProjectFile.rowid.in_(keep)).execute())
        ProjectFile.delete().where(ProjectFile.project.in_(other_ids)).execute()  # the duplicates
        Project.delete().where(Project.rowid.in_(other_ids)).execute()
        leader.name = Project.unused_name(",".join(p.name for p in projects), ignore=[leader.rowid])
        leader.last_change = datetime.now()
        leader.save()
        return leader


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
        if getattr(file, 'projectfile', None):
            return file.projectfile
        return ProjectFile(project=project, file=file)


class FitsHeader(Model):
    """
    Model representing a FITS header.
    This is a cache of the header information from FITS files.
    """
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', index=False, backref='header')
    hdu = _hdu_field()
    header = BlobField(null=False)  # Caches the raw header as bytes; an extension's own, not merged

    class Meta:
        indexes = ((('file', 'hdu'), True),)


def first_image_of_file():
    """Join condition for Image in a query over File that wants one row per file: the file's
    first image (its lowest hdu). For file-level lists, such as a project's files."""
    other = Image.alias()
    return (Image.file == File.rowid) & (Image.hdu == other.select(fn.MIN(other.hdu)).where(other.file == File.rowid))


def same_image(model):
    """Join condition for a per-image table (FitsHeader, ImageStats) in a query over File that
    outer-joins Image: the row for the same (file, hdu). A file without an Image row matches
    its hdu 0 row."""
    return (model.file == File.rowid) & (model.hdu == fn.COALESCE(Image.hdu, 0))


class FileWCS(Model):
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', unique=True, backref='wcs')
    wcs = BlobField(null=False)


class ImageStats(Model):
    rowid = RowIDField()
    file = ForeignKeyField(File, on_delete='CASCADE', index=False, backref='imagestats')
    hdu = _hdu_field()
    background_median = FloatField(null=True)
    background_rms = FloatField(null=True)
    star_count = IntegerField(null=True)
    fwhm_median = FloatField(null=True)
    elongation_median = FloatField(null=True)
    # Verdict against peer frames, written by quality_diagnosis. NULL: not analysed.
    diagnosis = TextField(null=True)

    class Meta:
        database = None
        indexes = ((('file', 'hdu'), True),)


class CustomCatalogEntry(Model):
    """A catalog the user imported from CSV (see custom_catalog.py). Lives in the library
    database because the bundled catalog.db is read-only. Same columns as CatalogEntry;
    readers see both through the catalog_entry_all view, never this table directly."""
    rowid = RowIDField()
    ra = FloatField()
    dec = FloatField()
    catalog = TextField()
    catalog_id = TextField()
    canonical_id = TextField(null=True)
    size = FloatField(default=0.0)
    axis_ratio = FloatField(null=True)
    angle = FloatField(null=True)
    magnitude = FloatField(null=True)
    healpix = IntegerField()

    class Meta:
        database = None
        table_name = 'custom_catalog_entry'
        indexes = (
            (('catalog', 'catalog_id'), True),
            (('healpix',), False),
        )


class LibraryProperty(Model):
    """A setting that belongs to the library, not the user: e.g. the zone its fallback local
    times were computed in, so a change is noticed whichever copy of the app opens it."""
    name = CharField(primary_key=True)
    value = TextField(null=True)

    class Meta:
        table_name = 'library_property'


CORE_MODELS = [LibraryRoot, File, Image, FitsHeader, FileWCS, Project, ProjectFile, ImageStats,
               CustomCatalogEntry, LibraryProperty]


class CatalogEntry(Model):
    """Built-in and custom catalogs together. Reads the per-connection TEMP view
    catalog_entry_all (see LibraryDatabase); custom entries have negative rowids."""
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
        table_name = 'catalog_entry_all'


CATALOG_MODELS = [CatalogEntry]

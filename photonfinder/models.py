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
from astropy_healpix import HEALPix
from peewee import *
from playhouse.sqlite_ext import RowIDField

# Create HEALPix object with the same parameters as used in fits_handlers.py
hp = HEALPix(nside=256, order='nested', frame='icrs')


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
    header_text: str = ""
    project: 'Project' = None
    sorting_index: int | None = None
    sorting_desc: bool = True

    def is_empty(self):
        return self == SearchCriteria()

    def __str__(self):
        result = []
        if len(self.paths) > 0:
            result += list(map(str, self.paths))

        for text in [self.type, self.filter, self.camera, self.file_name, self.object_name, self.exposure,
                     self.telescope, self.binning, self.gain, self.temperature, self.header_text]:
            if text:
                result.append(text)
        if self.project:
            result.append(self.project.name)
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
    def _to_json(value: 'SearchCriteria' | typing.Iterable['SearchCriteria']) -> str:
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
            raise f"Can't inflate value {value}"

    @staticmethod
    def _inflate_dict(data_dict):
        if data_dict.get('reference_file'):  # re-inflate the reference file
            data_dict['reference_file'] = File.get(File.rowid == data_dict['reference_file'])
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

    def __eq__(self, other):
        return self.name == other.name and self.path == other.path


@auto_str
class File(Model):
    rowid = RowIDField()
    root = ForeignKeyField(LibraryRoot, on_delete='CASCADE')
    path = CharField()
    name = CharField(index=True)
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


CORE_MODELS = [LibraryRoot, File, Image, FitsHeader, FileWCS, Project, ProjectFile]

"""Library query: which files match a SearchCriteria.

Owns the join topology (File, LibraryRoot, Image and the optional FITS header, plate
solution, image stats and project data), the translation of criteria into conditions, the
default ordering, sorting, paging and counting. Callers pass criteria and get files back;
they never build the joins themselves, so the search grid and the MCP server return the
same files for the same criteria.

Criteria conventions: an empty string means "no constraint", `None` means "IS NULL" (for
`type`, `filter` and `camera`, the fields the GUI can set to an empty value). Numeric
text that doesn't parse is skipped; `SearchCriteria.validate` reports it instead.

The models must be bound to a database (e.g. inside `context.database.bind_ctx(CORE_MODELS)`).
"""
import dataclasses
from datetime import datetime
from typing import Callable, NamedTuple

from peewee import JOIN, chunked, fn

from photonfinder import filter_catalogue
from photonfinder.formatting import _format_date, _format_dec, _format_file_size, _format_ra, _format_timestamp, _utc_to_local
from photonfinder.models import (
    DIAGNOSIS_NOT_ANALYSED, DIAGNOSIS_SEPARATOR, DIAGNOSIS_VALUES, PROJECT_NAME_SEP, File, FileWCS, FitsHeader, Image, ImageStats,
    LibraryRoot, Project, ProjectFile, SearchCriteria,
)

# The order of files when the criteria ask for no sort.
DEFAULT_ORDER = (File.root, File.path, File.name)

# The search result columns, in grid order. Each is selected by the search query, sorted on by
# its key (SearchCriteria.sorting_field, which MCP callers use) or by its position
# (SearchCriteria.sorting_index, which the grid uses and saved searches store), and shown in the
# grid with its label. Positions and labels must stay as they are: saved searches store the
# sort position, and column visibility and presets are stored by label.

# Project names per file, joined into the search query once per search.
_PROJECT_NAMES = (
    ProjectFile
    .select(ProjectFile.file.alias('file_id'),
            fn.GROUP_CONCAT(Project.name, PROJECT_NAME_SEP).alias('project_names'))
    .join(Project)
    .group_by(ProjectFile.file)
)


def search_row_stat(row: File, alias: str):
    """An aliased ImageStats column of a search row. Peewee keeps it on the joined model."""
    return getattr(getattr(row, 'imagestats', None), alias, None)


def _image_value(name):
    def value(row):
        image = row.__dict__.get('image')
        return getattr(image, name, None) if image is not None else None
    return value


def _project_names(row) -> list:
    names = getattr(getattr(row, 'projectfile', None), 'project_names', None)
    return names.split(PROJECT_NAME_SEP) if names else []


def _date_obs_local(row):
    date_obs = _image_value('date_obs')(row)
    return _utc_to_local(date_obs) if date_obs is not None else None


def _number_text(digits):
    return lambda value: f"{value:.{digits}f}"


class ResultColumn(NamedTuple):
    key: str                       # the sort name (SearchCriteria.sorting_field)
    label: str                     # the grid header
    field: object                  # what the search query selects, and sorts on
    value: Callable                # a result row -> the cell's value (None: empty cell)
    text: Callable = str           # a non-None value -> the cell's text
    sort_value: Callable = None    # a value -> what the grid sorts loaded rows by (default: the value)


RESULT_COLUMNS = (
    ResultColumn("name", "File name", File.name, lambda row: row.name, sort_value=str.lower),
    ResultColumn("type", "Type", Image.image_type, _image_value('image_type')),
    ResultColumn("filter", "Filter", Image.filter, _image_value('filter')),
    ResultColumn("exposure", "Exposure", Image.exposure, _image_value('exposure')),
    ResultColumn("gain", "Gain", Image.gain, _image_value('gain')),
    ResultColumn("offset", "Offset", Image.offset, _image_value('offset')),
    ResultColumn("binning", "Binning", Image.binning, _image_value('binning')),
    ResultColumn("temperature", "Set Temp", Image.set_temp, _image_value('set_temp')),
    ResultColumn("camera", "Camera", Image.camera, _image_value('camera')),
    ResultColumn("telescope", "Telescope", Image.telescope, _image_value('telescope')),
    ResultColumn("object_name", "Object", Image.object_name, _image_value('object_name')),
    ResultColumn("date_obs", "Observation Date", Image.date_obs, _date_obs_local, text=_format_date),
    ResultColumn("path", "Path", File.path, lambda row: row.path, sort_value=str.lower),
    ResultColumn("size", "Size", File.size, lambda row: row.size, text=_format_file_size),
    ResultColumn("mtime", "Modified", File.mtime_millis, lambda row: row.mtime_millis, text=_format_timestamp),
    ResultColumn("coord_ra", "RA", Image.coord_ra, _image_value('coord_ra'), text=_format_ra),
    ResultColumn("coord_dec", "DEC", Image.coord_dec, _image_value('coord_dec'), text=_format_dec),
    ResultColumn("solved", "Solved", FileWCS.wcs.is_null(False).alias('has_wcs'),
                 lambda row: "True" if getattr(row, 'has_wcs', False) else "False"),
    ResultColumn("projects", "Projects", _PROJECT_NAMES.c.project_names.alias('project_names'),
                 lambda row: ", ".join(_project_names(row))),
    ResultColumn("background", "Bg Median", ImageStats.background_median.alias('stats_background_median'),
                 lambda row: search_row_stat(row, 'stats_background_median'), text=_number_text(1)),
    ResultColumn("background_rms", "Bg RMS", ImageStats.background_rms.alias('stats_background_rms'),
                 lambda row: search_row_stat(row, 'stats_background_rms'), text=_number_text(2)),
    ResultColumn("star_count", "Stars", ImageStats.star_count.alias('stats_star_count'),
                 lambda row: search_row_stat(row, 'stats_star_count')),
    ResultColumn("fwhm", "FWHM", ImageStats.fwhm_median.alias('stats_fwhm_median'),
                 lambda row: search_row_stat(row, 'stats_fwhm_median'), text=_number_text(2)),
    ResultColumn("elongation", "Elongation", ImageStats.elongation_median.alias('stats_elongation_median'),
                 lambda row: search_row_stat(row, 'stats_elongation_median'), text=_number_text(3)),
    ResultColumn("diagnosis", "Diagnosis", ImageStats.diagnosis.alias('stats_diagnosis'),
                 lambda row: search_row_stat(row, 'stats_diagnosis') or ""),
)
COLUMN_INDEX = {column.key: i for i, column in enumerate(RESULT_COLUMNS)}

# Named sort targets for SearchCriteria.sorting_field: every result column, by key.
SORTABLE_FIELDS = {column.key: column.field for column in RESULT_COLUMNS}


def cell(column: ResultColumn, value) -> tuple:
    """(text, sort value) of a cell holding `value`; ("", None) for an empty cell."""
    if value is None:
        return "", None
    return column.text(value), (column.sort_value or (lambda v: v))(value)


class SearchPage(NamedTuple):
    """One page of a search. `rows` are File instances with their Image and LibraryRoot
    joined, plus the aliased columns read through search_row_stat, `has_wcs` and
    `projectfile.project_names`."""
    rows: list
    total: int
    has_more: bool


def search_files(criteria: SearchCriteria, page: int = 0, page_size: int = 100) -> SearchPage:
    """The files matching the criteria, sorted as they ask, one zero-based page at a time.

    Raises ValueError for an unknown `sorting_field`.
    """
    query, fields = _result_query(criteria)
    query = query.order_by(*_ordering(criteria, fields))
    total = count_files(criteria)
    rows = list(query.paginate(page + 1, page_size))
    return SearchPage(rows, total, (page + 1) * page_size < total)


def count_files(criteria: SearchCriteria) -> int:
    """How many files match the criteria."""
    return _files_query(criteria).count()


def matching_files(criteria: SearchCriteria, *, require_image: bool = False, with_header: bool = False,
                   with_solution: bool = False, page: int | None = None, page_size: int = 100) -> list[File]:
    """Every file matching the criteria, in DEFAULT_ORDER, with its LibraryRoot and Image
    loaded. A file without an Image row (its header couldn't be read) is included, its `image`
    None, unless `require_image`. `with_header` and `with_solution` also load its FitsHeader
    and FileWCS, None when it has none. With `page` (zero-based), only that page; count_files
    gives the total.

    A list rather than a query: iterating a query lazily holds a read snapshot open, and once
    anything else commits, every write made from behind it fails with "database is locked".
    """
    query = _files_query(criteria, require_image, with_header, with_solution).order_by(*DEFAULT_ORDER)
    if page is not None:
        query = query.paginate(page + 1, page_size)
    return list(query)


def files_in_cone(criteria: SearchCriteria) -> list[File]:
    """The matching files whose image centre lies strictly within `coord_radius` of the
    criteria's cone centre, as matching_files(require_image=True) loads them.

    The cone criterion itself only selects the HEALPix pixels the cone touches, which takes in
    files somewhat beyond the radius; this measures the actual distance. Raises ValueError
    when the criteria have no cone, and what SearchCriteria.cone_center raises when it can't
    be parsed.
    """
    if not (criteria.coord_ra and criteria.coord_dec):
        raise ValueError("files_in_cone needs criteria with coord_ra and coord_dec")
    center = criteria.cone_center()
    return [file for file in matching_files(criteria, require_image=True)
            if (coord := file.image.get_sky_coord()) is not None
            and coord.separation(center).deg < criteria.coord_radius]


def distinct_values(criteria: SearchCriteria, column) -> list:
    """The values `column` (an Image column) takes in the files matching the criteria, sorted,
    leaving out the criterion on `column` itself: the choices a filter combo box still offers.
    NULL (None) is one of them when a matching image has no value."""
    query = select_matching(criteria, fn.DISTINCT(column), require_image=True, exclude=column)
    return [value for value, in query.order_by(column).tuples()]


def diagnosis_counts(criteria: SearchCriteria) -> dict[str, int]:
    """Files per diagnosis value within a search, ignoring its diagnosis filter.

    A file with several Bad values counts under each of them.
    """
    criteria = dataclasses.replace(criteria, diagnosis_hidden=[])
    query = select_matching(criteria, ImageStats.diagnosis, fn.COUNT(File.rowid), with_stats=True)
    counts = dict.fromkeys(DIAGNOSIS_VALUES, 0)
    for diagnosis, count in query.group_by(ImageStats.diagnosis).tuples():
        for value in diagnosis.split(DIAGNOSIS_SEPARATOR) if diagnosis else [DIAGNOSIS_NOT_ANALYSED]:
            counts[value] = counts.get(value, 0) + count
    return counts


def select_matching(criteria: SearchCriteria, *columns, require_image: bool = False, with_header: bool = False,
                    with_solution: bool = False, with_stats: bool = False, solved_only: bool = False,
                    exclude=None):
    """An unordered query of `columns` over the files matching the criteria, for callers that
    project or aggregate (they add their own WHERE, GROUP BY, ORDER BY or LIMIT).

    Columns may come from File, LibraryRoot and Image, and from FitsHeader / FileWCS /
    ImageStats with `with_header` / `with_solution` / `with_stats` (outer-joined: NULL when a
    file has none). Image is outer-joined too, NULL for a file without an Image row, unless
    `require_image`, which leaves such files out. `solved_only` keeps only plate-solved files,
    whatever the criteria's `plate_solved` says (so plate_solved=False leaves nothing).
    `exclude` is a column whose criterion is ignored (see distinct_values).
    """
    query = (File.select(*columns)
             .join_from(File, LibraryRoot)
             .join_from(File, Image, JOIN.INNER if require_image else JOIN.LEFT_OUTER))
    for model, wanted in ((FitsHeader, with_header), (FileWCS, with_solution), (ImageStats, with_stats)):
        if wanted:
            query = query.join_from(File, model, JOIN.LEFT_OUTER)
    if solved_only:
        query = query.where(File.rowid.in_(FileWCS.select(FileWCS.file)))
    return _where_criteria(query, criteria, exclude)


def unsolved_lights(criteria: SearchCriteria) -> list[File]:
    """The files a plate-solve run works through: the matching LIGHT and MASTER LIGHT frames,
    and frames without a type, that have no stored solution. A file without an Image row (its
    header couldn't be read) is left out. In DEFAULT_ORDER, as matching_files."""
    query = (_files_query(criteria, require_image=True)
             .where((Image.image_type == "LIGHT") | (Image.image_type == "MASTER LIGHT") |
                    Image.image_type.is_null())
             .where(File.rowid.not_in(FileWCS.select(FileWCS.file))))
    return list(query.order_by(*DEFAULT_ORDER))


def files_by_id(file_ids, *, with_header: bool = False, with_solution: bool = False) -> list[File]:
    """The files with these ids, loaded as matching_files loads them. Unknown ids are skipped."""
    files = []
    for batch in chunked(file_ids, 500):  # SQLite caps the parameters of one statement
        files.extend(_files_query(SearchCriteria(), with_header=with_header, with_solution=with_solution)
                     .where(File.rowid.in_(batch)))
    return files


def _files_query(criteria: SearchCriteria, require_image: bool = False, with_header: bool = False,
                 with_solution: bool = False):
    """File with LibraryRoot, Image and, when asked, FitsHeader / FileWCS loaded, filtered."""
    models = [File, Image, LibraryRoot,
              *(model for model, wanted in ((FitsHeader, with_header), (FileWCS, with_solution)) if wanted)]
    return select_matching(criteria, *models, require_image=require_image, with_header=with_header,
                           with_solution=with_solution)


_SERIALIZED_IMAGE_FIELDS = (
    "image_type", "camera", "filter", "exposure", "gain", "offset", "binning",
    "set_temp", "telescope", "object_name", "coord_ra", "coord_dec",
    "coord_radius", "width", "height",
)


def serialize_search_row(row: File) -> dict:
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
        result["projects"] = row.projectfile.project_names.split(PROJECT_NAME_SEP)
    else:
        result["projects"] = []

    result["stats"] = {
        "background_median": search_row_stat(row, "stats_background_median"),
        "background_rms": search_row_stat(row, "stats_background_rms"),
        "star_count": search_row_stat(row, "stats_star_count"),
        "fwhm_median": search_row_stat(row, "stats_fwhm_median"),
        "elongation_median": search_row_stat(row, "stats_elongation_median"),
        "diagnosis": search_row_stat(row, "stats_diagnosis"),
    }
    return result


def _result_query(criteria: SearchCriteria):
    """The unordered search-result query, and its selected fields in RESULT_COLUMNS order
    (what SearchCriteria.sorting_index refers to)."""
    fields = [column.field for column in RESULT_COLUMNS]
    query = (File
             .select(*(fields + [File, Image, LibraryRoot]))
             .join_from(File, LibraryRoot)
             .join_from(File, Image, JOIN.LEFT_OUTER)
             .join_from(File, FileWCS, JOIN.LEFT_OUTER)
             .join_from(File, ImageStats, JOIN.LEFT_OUTER)
             .join_from(File, _PROJECT_NAMES, JOIN.LEFT_OUTER,
                        on=(File.rowid == _PROJECT_NAMES.c.file_id))
             )
    return _where_criteria(query, criteria), fields


def _ordering(criteria: SearchCriteria, fields: list) -> list:
    """ORDER BY terms: the named sorting_field, else the sorting_index column, else DEFAULT_ORDER."""
    if criteria.sorting_field is not None:
        sort_field = SORTABLE_FIELDS.get(criteria.sorting_field)
        if sort_field is None:
            raise ValueError(
                f"Unknown sorting_field '{criteria.sorting_field}'. "
                f"Valid fields: {', '.join(sorted(SORTABLE_FIELDS))}.")
    elif criteria.sorting_index is not None:
        sort_field = fields[criteria.sorting_index]
    else:
        return list(DEFAULT_ORDER)
    # Text sorts ignore case; on a number column the collation has no effect.
    sort_field = sort_field.collate("NOCASE")
    return [sort_field.desc() if criteria.sorting_desc else sort_field.asc()]


def _where_criteria(query, criteria: SearchCriteria, exclude=None):
    """Add the criteria's conditions to a query that selects from File joined to Image.

    `exclude` is a field whose criterion is left out (see distinct_values).
    """
    for condition in _criteria_conditions(criteria, exclude):
        query = query.where(condition)
    return query


def _criteria_conditions(criteria: SearchCriteria, exclude=None) -> list:
    """The WHERE conditions of the criteria, over File and Image columns, from the filter
    catalogue. Everything else (headers, plate solutions, stats, projects) is a subquery, so a
    query that joins one of those tables to select its columns never has it joined twice."""
    return filter_catalogue.conditions(criteria, exclude)

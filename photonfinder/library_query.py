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

from peewee import JOIN, SQL, Select, Value, Window, chunked, fn

from photonfinder import filter_catalogue
from photonfinder.formatting import _format_date, _format_dec, _format_file_size, _format_ra, _format_timestamp
from photonfinder.local_time import session_night
from photonfinder.models import (
    DIAGNOSIS_NOT_ANALYSED, DIAGNOSIS_SEPARATOR, DIAGNOSIS_VALUES, PROJECT_NAME_SEP, File, FileWCS, FitsHeader, Image, ImageStats,
    same_image,
    LibraryRoot, Project, ProjectFile, SearchCriteria, group_key_sql, group_order_sql, session_night_sql,
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


def image_key(row: File) -> tuple[int, int]:
    """(file id, hdu) of a search row's image: what tells the rows of one file apart. A row
    without an Image (its header couldn't be read) is its file's hdu 0."""
    image = row.__dict__.get('image')
    return row.rowid, image.hdu if isinstance(image, Image) and image.hdu is not None else 0


def distinct_files(rows) -> list[File]:
    """The files of these search rows, each once, in the order first seen: what an action on
    files (export, move, delete, add to a project) works on, whichever of a file's images were
    selected."""
    seen = set()
    files = []
    for row in rows:
        if row.rowid not in seen:
            seen.add(row.rowid)
            files.append(row)
    return files


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


class _UtcTime(datetime):
    """A date_obs shown as it is, in UTC: the image has no local time yet (the background fill
    hasn't reached it, once, after the upgrade to library version 3)."""


def _observation_time(row):
    """The image's local time, as stored; its UTC date_obs, marked, until it has one."""
    image = row.__dict__.get('image')
    if image is None:
        return None
    if image.local_time is not None:
        return image.local_time
    date_obs = image.date_obs
    return _UtcTime(*date_obs.timetuple()[:6], date_obs.microsecond) if date_obs is not None else None


def _format_observation(value) -> str:
    return f"{_format_date(value)} UTC" if isinstance(value, _UtcTime) else _format_date(value)


def _observation_tooltip(row) -> str | None:
    date_obs = _image_value('date_obs')(row)
    return f"UTC: {_format_date(date_obs)}" if date_obs is not None else None


def _night(row):
    image = row.__dict__.get('image')
    return session_night(image.local_time) if image is not None else None


def _format_duration(seconds: float) -> str:
    seconds = round(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def group_value(row, name: str):
    """What a grouped row stands for ('group_frames', 'group_integration', 'group_solved');
    None otherwise.
    Peewee keeps the grouping CTE's columns on the join's `grouping`, a dict."""
    grouping = getattr(row, 'grouping', None)
    return grouping.get(name) if isinstance(grouping, dict) else getattr(grouping, name, None)


# In grouped mode (SearchCriteria.one_per_group) a row is a group's representative, and these
# say what it stands for; otherwise they are NULL. _result_query puts the real columns in.
_GROUP_FRAMES = Value(None).alias('group_frames')
_GROUP_INTEGRATION = Value(None).alias('group_integration')
_HAS_WCS = FileWCS.wcs.is_null(False).alias('has_wcs')


def _solved(row):
    """A grouped row is solved when any of its frames is (one-per-group plate solving skips
    such a group); otherwise the file itself."""
    solved_frames = group_value(row, 'group_solved')
    if solved_frames is not None:
        return "True" if solved_frames else "False"
    return "True" if getattr(row, 'has_wcs', False) else "False"


def _solved_tooltip(row):
    solved_frames = group_value(row, 'group_solved')
    if solved_frames is None:
        return None
    return f"{solved_frames} of {group_value(row, 'group_frames')} frames solved"


def _number_text(digits):
    return lambda value: f"{value:.{digits}f}"


class ResultColumn(NamedTuple):
    key: str                       # the sort name (SearchCriteria.sorting_field)
    label: str                     # the grid header
    field: object                  # what the search query selects, and sorts on
    value: Callable                # a result row -> the cell's value (None: empty cell)
    text: Callable = str           # a non-None value -> the cell's text
    sort_value: Callable = None    # a value -> what the grid sorts loaded rows by (default: the value)
    tooltip: Callable = None       # a result row -> the cell's tooltip (None: none)


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
    # The image's local time, as shown and sorted (by the query and in the grid alike).
    ResultColumn("date_obs", "Observation Date", Image.local_time, _observation_time, text=_format_observation,
                 tooltip=_observation_tooltip),
    ResultColumn("path", "Path", File.path, lambda row: row.path, sort_value=str.lower),
    ResultColumn("size", "Size", File.size, lambda row: row.size, text=_format_file_size),
    ResultColumn("mtime", "Modified", File.mtime_millis, lambda row: row.mtime_millis, text=_format_timestamp),
    ResultColumn("coord_ra", "RA", Image.coord_ra, _image_value('coord_ra'), text=_format_ra),
    ResultColumn("coord_dec", "DEC", Image.coord_dec, _image_value('coord_dec'), text=_format_dec),
    ResultColumn("solved", "Solved", _HAS_WCS, _solved, tooltip=_solved_tooltip),
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
    # Last, so that saved sort positions (SearchCriteria.sorting_index) keep their column. The
    # grid shows Night after Observation Date (SearchPanel moves it there).
    ResultColumn("layer", "Layer", Image.extname, _image_value('extname')),
    ResultColumn("session_night", "Night", session_night_sql().alias('session_night'), _night),
    ResultColumn("frames", "Frames", _GROUP_FRAMES, lambda row: group_value(row, 'group_frames')),
    ResultColumn("integration", "Integration", _GROUP_INTEGRATION,
                 lambda row: group_value(row, 'group_integration'), text=_format_duration),
)
# Shown only in grouped mode.
GROUP_COLUMNS = ("frames", "integration")
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
    `projectfile.project_names`. A row is an image: a file holding several images (a
    multi-layer FITS or XISF) has a row for each, the same File with another Image. `total`
    counts rows; `count_distinct_files` counts the files they are in."""
    rows: list
    total: int
    has_more: bool


def search_files(criteria: SearchCriteria, page: int = 0, page_size: int = 100) -> SearchPage:
    """The images matching the criteria, sorted as they ask, one zero-based page at a time.

    Raises ValueError for an unknown `sorting_field`.
    """
    query, fields = _result_query(criteria)
    query = query.order_by(*_ordering(criteria, fields))
    total = count_files(criteria)
    rows = list(query.paginate(page + 1, page_size))
    return SearchPage(rows, total, (page + 1) * page_size < total)


def count_files(criteria: SearchCriteria) -> int:
    """How many search rows match the criteria: one per image, a file without an Image row
    counting once; in grouped mode, one per group. See count_distinct_files for the files."""
    if criteria.one_per_group:
        return _result_query(criteria)[0].count()
    return _files_query(criteria).count()


def count_distinct_files(criteria: SearchCriteria) -> int:
    """How many files the matching search rows are in."""
    return select_matching(criteria, fn.COUNT(File.rowid.distinct())).scalar() or 0


def count_text(images: int, files: int) -> str:
    """"N files", or "N images in M files" when some files hold several images."""
    return f"{files} files" if images == files else f"{images} images in {files} files"


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
    if with_header:
        query = query.join_from(File, FitsHeader, JOIN.LEFT_OUTER, on=same_image(FitsHeader), attr='fitsheader')
    if with_solution:
        query = query.join_from(File, FileWCS, JOIN.LEFT_OUTER)
    if with_stats:
        query = query.join_from(File, ImageStats, JOIN.LEFT_OUTER, on=same_image(ImageStats), attr='imagestats')
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
    # A solution is the file's: each file once, however many of its layers matched.
    return distinct_files(query.order_by(*DEFAULT_ORDER))


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
    if group_value(row, "group_frames") is not None:  # grouped mode: what the row stands for
        result["frames"] = group_value(row, "group_frames")
        result["integration_seconds"] = group_value(row, "group_integration")
        result["solved_frames"] = group_value(row, "group_solved")

    image = getattr(row, "image", None)
    result["hdu"] = image.hdu if image is not None and image.rowid is not None else None
    result["extname"] = image.extname if image is not None and image.rowid is not None else None
    if image is not None and image.rowid is not None:
        image_data = {name: getattr(image, name) for name in _SERIALIZED_IMAGE_FIELDS}
        date_obs = image.date_obs
        image_data["date_obs"] = date_obs.isoformat() if isinstance(date_obs, datetime) else date_obs
        image_data["local_time"] = image.local_time.isoformat() if image.local_time else None
        night = session_night(image.local_time)
        image_data["session_night"] = night.isoformat() if night else None
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
    groups = _representatives(criteria) if criteria.one_per_group else None
    if groups is not None:
        fields[COLUMN_INDEX["frames"]] = groups.c.frames.alias('group_frames')
        fields[COLUMN_INDEX["integration"]] = groups.c.integration.alias('group_integration')
        # Shown and sorted by the group's solved frames; the row keeps its own has_wcs.
        fields[COLUMN_INDEX["solved"]] = groups.c.solved.alias('group_solved')
    extra = [] if groups is None else [_HAS_WCS]
    query = (File
             .select(*(fields + extra + [File, Image, LibraryRoot]))
             .join_from(File, LibraryRoot)
             .join_from(File, Image, JOIN.LEFT_OUTER)
             .join_from(File, FileWCS, JOIN.LEFT_OUTER)
             .join_from(File, ImageStats, JOIN.LEFT_OUTER, on=same_image(ImageStats), attr='imagestats')
             .join_from(File, _PROJECT_NAMES, JOIN.LEFT_OUTER,
                        on=(File.rowid == _PROJECT_NAMES.c.file_id))
             )
    if groups is not None:
        # Each group's representative; a file without an Image row has no group, so no row.
        query = (query.with_cte(groups)
                 .join_from(File, groups, JOIN.INNER, on=(Image.rowid == groups.c.image_id), attr='grouping'))
    return _where_criteria(query, criteria), fields


def _representatives(criteria: SearchCriteria):
    """Each group's representative (the first by group_order_sql), with the group's frame count,
    summed exposure and plate-solved frames. A window over the matching images, so a group counts only the frames
    the criteria let through. One sort: ROW_NUMBER ranks the frames, and COUNT and SUM take the
    whole group through an explicit frame (with ORDER BY, their default frame would make them
    running totals)."""
    ranked = Window(partition_by=group_key_sql(), order_by=group_order_sql(), alias='ranked')
    whole = Window(partition_by=group_key_sql(), order_by=group_order_sql(), alias='whole',
                   start=Window.preceding(), end=Window.following())
    framed = (select_matching(criteria, Image.rowid.alias('image_id'),
                              fn.ROW_NUMBER().over(ranked).alias('rank'),
                              fn.COUNT(SQL('*')).over(whole).alias('frames'),
                              fn.SUM(Image.exposure).over(whole).alias('integration'),
                              fn.COUNT(FileWCS.rowid).over(whole).alias('solved'),
                              require_image=True, with_solution=True)
              .window(ranked, whole)
              .alias('framed'))
    # Materialized: computed once, as SQLite could otherwise run it again per joined row.
    return (Select([framed], [framed.c.image_id, framed.c.frames, framed.c.integration, framed.c.solved])
            .where(framed.c.rank == 1)
            .cte('groups', materialized=True))


def _ordering(criteria: SearchCriteria, fields: list) -> list:
    """ORDER BY terms: the named sorting_field, else the sorting_index column, else DEFAULT_ORDER."""
    if criteria.sorting_field is not None:
        if criteria.sorting_field not in SORTABLE_FIELDS:
            raise ValueError(
                f"Unknown sorting_field '{criteria.sorting_field}'. "
                f"Valid fields: {', '.join(sorted(SORTABLE_FIELDS))}.")
        sort_field = fields[COLUMN_INDEX[criteria.sorting_field]]  # the query's own (grouped) column
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

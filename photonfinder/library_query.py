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
import logging
import operator
from datetime import datetime
from functools import reduce
from typing import NamedTuple

import astropy.units as u
from peewee import JOIN, SQL, chunked, fn

from photonfinder.core import hp
from photonfinder.models import (
    DIAGNOSIS_NOT_ANALYSED, DIAGNOSIS_OK, DIAGNOSIS_SEPARATOR, DIAGNOSIS_VALUES, PROJECT_NAME_SEP, File, FileWCS, FitsHeader, Image, ImageStats,
    LibraryRoot, Project, ProjectFile, SearchCriteria, norm_db_path,
)

# The order of files when the criteria ask for no sort.
DEFAULT_ORDER = (File.root, File.path, File.name)

# Named sort targets for SearchCriteria.sorting_field -- independent of the GUI's
# result-table column order (see SearchCriteria.sorting_index), so external callers
# (e.g. the MCP server) can request a sort without knowing that layout.
SORTABLE_FIELDS = {
    "name": File.name,
    "path": File.path,
    "size": File.size,
    "mtime": File.mtime_millis,
    "type": Image.image_type,
    "filter": Image.filter,
    "exposure": Image.exposure,
    "gain": Image.gain,
    "offset": Image.offset,
    "binning": Image.binning,
    "temperature": Image.set_temp,
    "camera": Image.camera,
    "telescope": Image.telescope,
    "object_name": Image.object_name,
    "date_obs": Image.date_obs,
    "coord_ra": Image.coord_ra,
    "coord_dec": Image.coord_dec,
    "star_count": ImageStats.star_count,
    "fwhm": ImageStats.fwhm_median,
    "elongation": ImageStats.elongation_median,
    "background": ImageStats.background_median,
    "background_rms": ImageStats.background_rms,
}


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
    return where_criteria(query, criteria, exclude)


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


def search_row_stat(row: File, alias: str):
    """An aliased ImageStats column of a search row. Peewee keeps it on the joined model."""
    return getattr(getattr(row, 'imagestats', None), alias, None)


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
    """The unordered search-result query, and its selected fields in result-table column
    order (what SearchCriteria.sorting_index refers to; keep in sync with SearchPanel)."""
    project_names_subq = (
        ProjectFile
        .select(
            ProjectFile.file.alias('file_id'),
            fn.GROUP_CONCAT(Project.name, PROJECT_NAME_SEP).alias('project_names')
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
              ImageStats.elongation_median.alias('stats_elongation_median'),
              ImageStats.diagnosis.alias('stats_diagnosis')]
    query = (File
             .select(*(fields + [File, Image, LibraryRoot]))
             .join_from(File, LibraryRoot)
             .join_from(File, Image, JOIN.LEFT_OUTER)
             .join_from(File, FileWCS, JOIN.LEFT_OUTER)
             .join_from(File, ImageStats, JOIN.LEFT_OUTER)
             .join_from(File, project_names_subq, JOIN.LEFT_OUTER,
                        on=(File.rowid == project_names_subq.c.file_id))
             )
    return where_criteria(query, criteria), fields


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


def where_criteria(query, criteria: SearchCriteria, exclude=None):
    """Add the criteria's conditions to a query that selects from File joined to Image.

    `exclude` is a field whose criterion is left out (the filter combo boxes list the values
    still available for their own field). Only for the callers that still build their own
    joins; it goes private once they have moved onto this module.
    """
    for condition in criteria_conditions(criteria, exclude):
        query = query.where(condition)
    return query


def criteria_conditions(criteria: SearchCriteria, exclude=None) -> list:
    """The WHERE conditions of the criteria, over File and Image columns. Everything else
    (headers, plate solutions, stats, projects) is a subquery, so callers that join those
    tables themselves never end up with them joined twice."""
    conditions = []
    _path_conditions(criteria, exclude, conditions)

    # Exact matches: "" is no constraint, None is IS NULL (peewee turns == None into IS NULL)
    for value, column in ((criteria.type, Image.image_type),
                          (criteria.filter, Image.filter),
                          (criteria.camera, Image.camera)):
        if value != "" and exclude is not column:
            conditions.append(column == value)

    for value, column in ((criteria.object_name, Image.object_name),
                          (criteria.file_name, File.name),
                          (criteria.telescope, Image.telescope)):
        if value and exclude is not column:
            conditions.append(column.contains(value))

    # The text fields constrain only when non-empty; offset is a number, where 0 is a value.
    for value, present, parse, column, tolerance in (
            (criteria.exposure, bool(criteria.exposure), float, Image.exposure, criteria.exposure_tolerance),
            (criteria.binning, bool(criteria.binning), int, Image.binning, None),
            (criteria.gain, bool(criteria.gain), int, Image.gain, None),
            (criteria.offset, criteria.offset is not None, int, Image.offset, None),
            (criteria.temperature, bool(criteria.temperature), float, Image.set_temp,
             criteria.temperature_tolerance)):
        if present and exclude is not column:
            _number_condition(value, parse, column, tolerance, conditions)

    if criteria.start_datetime and exclude is not Image.date_obs:
        conditions.append(Image.date_obs >= criteria.start_datetime)
    if criteria.end_datetime and exclude is not Image.date_obs:
        conditions.append(Image.date_obs <= criteria.end_datetime)

    if criteria.coord_ra and criteria.coord_dec and exclude is not Image.coord_pix256:
        try:
            pixels = hp.cone_search_skycoord(criteria.cone_center(), criteria.coord_radius * u.deg)
            if len(pixels) > 0:
                conditions.append(Image.coord_pix256.in_(pixels.tolist()))
        except Exception as e:  # astropy raises several types for unparsable input
            logging.error(f"Error applying coordinates filter: {str(e)}")

    if criteria.header_text:
        conditions.append(_header_condition(criteria.header_text))

    for value, condition in ((criteria.width_min, lambda v: Image.width >= v),
                             (criteria.width_max, lambda v: Image.width <= v),
                             (criteria.height_min, lambda v: Image.height >= v),
                             (criteria.height_max, lambda v: Image.height <= v),
                             (criteria.scale_min, lambda v: Image.coord_scale >= v),
                             (criteria.scale_max, lambda v: Image.coord_scale <= v)):
        if value is not None:
            conditions.append(condition(value))

    stats_conditions = [condition(value) for value, condition in (
        (criteria.star_count_min, lambda v: ImageStats.star_count >= v),
        (criteria.star_count_max, lambda v: ImageStats.star_count <= v),
        (criteria.fwhm_min, lambda v: ImageStats.fwhm_median >= v),
        (criteria.fwhm_max, lambda v: ImageStats.fwhm_median <= v),
        (criteria.background_min, lambda v: ImageStats.background_median >= v),
        (criteria.background_max, lambda v: ImageStats.background_median <= v),
        (criteria.background_rms_min, lambda v: ImageStats.background_rms >= v),
        (criteria.background_rms_max, lambda v: ImageStats.background_rms <= v),
        (criteria.elongation_min, lambda v: ImageStats.elongation_median >= v),
        (criteria.elongation_max, lambda v: ImageStats.elongation_median <= v),
    ) if value is not None]
    if stats_conditions:
        conditions.append(File.rowid.in_(ImageStats.select(ImageStats.file).where(*stats_conditions)))

    if criteria.diagnosis_hidden:
        conditions.extend(_diagnosis_conditions(criteria.diagnosis_hidden))

    if criteria.plate_solved is True:
        conditions.append(File.rowid.in_(FileWCS.select(FileWCS.file)))
    elif criteria.plate_solved is False:
        conditions.append(File.rowid.not_in(FileWCS.select(FileWCS.file)))

    if criteria.project:
        if criteria.project.rowid > 0:
            conditions.append(File.rowid.in_(
                ProjectFile.select(ProjectFile.file).where(ProjectFile.project == criteria.project)))
        else:  # NO_PROJECT
            conditions.append(File.rowid.not_in(ProjectFile.select(ProjectFile.file)))
    return conditions


def _path_conditions(criteria: SearchCriteria, exclude, conditions: list) -> None:
    if not criteria.paths or exclude is File.path:
        return
    path_conditions = []
    for full_path in criteria.paths:
        if full_path.root_id is None and full_path.path is None:  # all libraries
            if criteria.paths_as_prefix:
                return  # everything is below it, so no constraint
            continue  # no file is directly in 'all libraries'
        if criteria.paths_as_prefix:
            if full_path.path is None:  # a library root: anything below it
                path_conditions.append(File.root == full_path.root_id)
            else:
                path_conditions.append(
                    (File.root == full_path.root_id) & (File.path.startswith(norm_db_path(full_path.path))))
        else:  # only directly in the directory
            path = "." if full_path.path is None else full_path.path
            path_conditions.append((File.root == full_path.root_id) & (File.path == norm_db_path(path)))
    if path_conditions:
        conditions.append(reduce(operator.or_, path_conditions))


def _number_condition(text, parse, column, tolerance, conditions: list) -> None:
    """Match a number typed as text, within ±tolerance when given. Skipped when it doesn't parse."""
    try:
        value = parse(text)
    except (ValueError, TypeError):
        return
    if tolerance is not None:
        conditions.append(column.between(value - tolerance, value + tolerance))
    else:
        conditions.append(column == value)


def _header_condition(header_text: str):
    """KEY=n, KEY<n or KEY>n compare a header value; anything else searches the header text."""
    header_condition = None
    for symbol, compare in (('=', operator.eq), ('<', operator.lt), ('>', operator.gt)):
        if symbol in header_text:
            try:
                key, value = header_text.split(symbol)
                header_condition = compare(fn.decompress_header_value(FitsHeader.header, key.strip()),
                                           float(value.strip()))
            except ValueError:
                pass
            break
    if header_condition is None:
        header_condition = fn.decompress(FitsHeader.header).contains(header_text)
    # A correlated subquery rather than a join, so callers that already join
    # FitsHeader (e.g. to read header values) don't end up with it joined twice.
    return fn.EXISTS(FitsHeader.select(SQL('1')).where((FitsHeader.file == File.rowid) & header_condition))


def _diagnosis_conditions(hidden: list[str]) -> list:
    """Conditions hiding every file with any of the given diagnosis values."""
    conditions = []
    if DIAGNOSIS_NOT_ANALYSED in hidden:
        conditions.append(File.rowid.in_(ImageStats.select(ImageStats.file).where(ImageStats.diagnosis.is_null(False))))
    matches = [ImageStats.diagnosis == DIAGNOSIS_OK if value == DIAGNOSIS_OK
               else ImageStats.diagnosis.contains(value)
               for value in hidden if value != DIAGNOSIS_NOT_ANALYSED]
    if matches:
        hidden_files = ImageStats.select(ImageStats.file).where(reduce(operator.or_, matches))
        conditions.append(File.rowid.not_in(hidden_files))
    return conditions

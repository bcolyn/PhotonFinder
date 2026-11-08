"""Filter catalogue: each search filter declared once.

An entry owns the SearchCriteria fields it controls and everything derived from them: whether
it is active, resetting it, its filter-chip label (shown when the filter is added and when a
saved search or session is restored), its fragment of the criteria's text summary (tab
titles), its SQL conditions (used by library_query) and the MCP descriptions of its fields.
Entries without a chip (type, filter, camera, names, paths) have their own controls in the
search panel. No Qt here; the search panel opens each filter's dialog and shows its chip.
"""
import dataclasses
import logging
import operator
from functools import reduce
from typing import Callable, Optional

import astropy.units as u
from peewee import SQL, fn

from photonfinder.core import hp
from photonfinder.formatting import _format_date
from photonfinder.models import (
    DIAGNOSIS_BAD_VALUES, DIAGNOSIS_NOT_ANALYSED, DIAGNOSIS_OK, File, FileWCS, FitsHeader, Image, ImageStats,
    ProjectFile, SearchCriteria, norm_db_path, session_night_sql,
)

_DEFAULTS = {}
for _f in dataclasses.fields(SearchCriteria):
    _DEFAULTS[_f.name] = _f.default_factory if _f.default_factory is not dataclasses.MISSING else _f.default


def default_of(field_name: str):
    """A SearchCriteria field's default value, a fresh one for list fields."""
    default = _DEFAULTS[field_name]
    return default() if callable(default) else default


@dataclasses.dataclass(frozen=True, eq=False)  # one object per filter: compared and hashed by identity
class FilterEntry:
    key: str
    fields: tuple                                    # the SearchCriteria fields it controls
    summary: Callable[[SearchCriteria], Optional[str]]
    conditions: Callable[[SearchCriteria], list]     # peewee conditions over File/Image
    chip_label: Optional[Callable[[SearchCriteria], str]] = None  # None: no chip, a control of its own
    column: object = None                            # the column it constrains, for distinct_values
    active: Optional[Callable[[SearchCriteria], bool]] = None     # default: any field not at its default
    mcp_descriptions: dict = dataclasses.field(default_factory=dict)  # field -> description

    @property
    def has_chip(self) -> bool:
        return self.chip_label is not None

    def is_active(self, criteria: SearchCriteria) -> bool:
        if self.active is not None:
            return self.active(criteria)
        return any(getattr(criteria, name) != default_of(name) for name in self.fields)

    def reset(self, criteria: SearchCriteria) -> None:
        for name in self.fields:
            setattr(criteria, name, default_of(name))


def _number(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def _range(low, high) -> Optional[str]:
    """"2–3.5", "≥2", "≤3.5", or None without either bound."""
    if low is not None and high is not None:
        return _number(low) if low == high else f"{_number(low)}–{_number(high)}"
    if low is not None:
        return f"≥{_number(low)}"
    if high is not None:
        return f"≤{_number(high)}"
    return None


def _tolerance(value, unit: str) -> str:
    return f" ±{_number(value)}{unit}" if value is not None else ""


# --- conditions shared by several entries ----------------------------------------------------

def _number_condition(text, parse, column, tolerance) -> list:
    """Match a number typed as text, within ±tolerance when given. Skipped when it doesn't parse."""
    try:
        value = parse(text)
    except (ValueError, TypeError):
        return []
    if tolerance is not None:
        return [column.between(value - tolerance, value + tolerance)]
    return [column == value]


def _exact(name: str, column) -> Callable:
    """"" is no constraint, None is IS NULL (peewee turns == None into IS NULL)."""
    return lambda c: [column == getattr(c, name)] if getattr(c, name) != "" else []


def _contains(name: str, column) -> Callable:
    return lambda c: [column.contains(getattr(c, name))] if getattr(c, name) else []


def _ranges(*bounds) -> Callable:
    """Conditions for (criteria field, column, operator) bounds that are set."""
    return lambda c: [compare(column, getattr(c, name)) for name, column, compare in bounds
                      if getattr(c, name) is not None]


def _value(name: str) -> Callable:
    """The field's value as the summary shows it; None when unset."""
    return lambda c: str(getattr(c, name)) if getattr(c, name) else None


# --- paths --------------------------------------------------------------------------------------

def _path_conditions(criteria: SearchCriteria) -> list:
    if not criteria.paths:
        return []
    path_conditions = []
    for full_path in criteria.paths:
        if full_path.root_id is None and full_path.path is None:  # all libraries
            if criteria.paths_as_prefix:
                return []  # everything is below it, so no constraint
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
    return [reduce(operator.or_, path_conditions)] if path_conditions else []


def _paths_summary(criteria: SearchCriteria) -> Optional[str]:
    paths = criteria.paths
    if not paths:
        return None
    return str(paths[0]) + (f"+{len(paths) - 1}" if len(paths) > 1 else "")


PATHS = FilterEntry(
    key="paths", fields=("paths", "paths_as_prefix"), column=File.path,
    active=lambda c: bool(c.paths), summary=_paths_summary, conditions=_path_conditions,
    mcp_descriptions={"paths": "Restrict to one or more library roots (optionally a "
                               "subdirectory of each), e.g. [{\"root_id\": 2}]. Get "
                               "`root_id`s from `list_library_roots`."},
)

# --- the search panel's own controls ------------------------------------------------------------

TYPE = FilterEntry(
    key="type", fields=("type",), column=Image.image_type,
    summary=_value("type"), conditions=_exact("type", Image.image_type),
    mcp_descriptions={"type": "Exact image type, e.g. LIGHT, DARK, FLAT, BIAS, "
                              "MASTER DARK. See `list_distinct_values('type')`."},
)
FILTER = FilterEntry(
    key="filter", fields=("filter",), column=Image.filter,
    summary=_value("filter"), conditions=_exact("filter", Image.filter),
    mcp_descriptions={"filter": "Exact filter name as recorded in the header, e.g. Ha. "
                                "See `list_distinct_values('filter')`."},
)
CAMERA = FilterEntry(
    key="camera", fields=("camera",), column=Image.camera,
    summary=_value("camera"), conditions=_exact("camera", Image.camera),
    mcp_descriptions={"camera": "Exact camera name. See `list_distinct_values('camera')`."},
)
FILE_NAME = FilterEntry(
    key="file_name", fields=("file_name",), column=File.name,
    summary=_value("file_name"), conditions=_contains("file_name", File.name),
    mcp_descriptions={"file_name": "Substring match on the file name (not the directory)."},
)
OBJECT_NAME = FilterEntry(
    key="object_name", fields=("object_name",), column=Image.object_name,
    summary=_value("object_name"), conditions=_contains("object_name", Image.object_name),
    mcp_descriptions={"object_name": "Substring match on the OBJECT name recorded in the "
                                     "header, e.g. \"M 31\". Spelling varies per capture "
                                     "program; check `list_distinct_values('object_name')`, or "
                                     "use a coordinate search to catch every spelling."},
)

# --- chips --------------------------------------------------------------------------------------

EXPOSURE = FilterEntry(
    key="exposure", fields=("exposure", "exposure_tolerance"), column=Image.exposure,
    active=lambda c: bool(c.exposure),
    chip_label=lambda c: f"Exposure: {c.exposure} s{_tolerance(c.exposure_tolerance, ' s')}",
    summary=lambda c: f"{c.exposure}{_tolerance(c.exposure_tolerance, 's')}",
    conditions=lambda c: _number_condition(c.exposure, float, Image.exposure, c.exposure_tolerance)
    if c.exposure else [],
    mcp_descriptions={"exposure": 'Exposure time in seconds, e.g. "300".',
                      "exposure_tolerance": "± seconds tolerance for `exposure`; omit for an exact match."},
)
TELESCOPE = FilterEntry(
    key="telescope", fields=("telescope",), column=Image.telescope,
    chip_label=lambda c: f"Telescope: {c.telescope}",
    summary=_value("telescope"), conditions=_contains("telescope", Image.telescope),
    mcp_descriptions={"telescope": "Substring match on the telescope name. See "
                                   "`list_distinct_values('telescope')`."},
)
BINNING = FilterEntry(
    key="binning", fields=("binning",), column=Image.binning,
    chip_label=lambda c: f"Binning: {c.binning}",
    summary=_value("binning"),
    conditions=lambda c: _number_condition(c.binning, int, Image.binning, None) if c.binning else [],
    mcp_descriptions={"binning": 'Binning factor, e.g. "2" for 2x2.'},
)


def _gain_parts(c: SearchCriteria) -> list:
    return [*([f"Gain: {c.gain}"] if c.gain else []), *([f"Offset: {c.offset}"] if c.offset is not None else [])]


GAIN = FilterEntry(
    key="gain", fields=("gain", "offset"), column=Image.gain,
    chip_label=lambda c: ", ".join(_gain_parts(c)),
    summary=lambda c: ", ".join([*([c.gain] if c.gain else []),
                                 *([f"offset {c.offset}"] if c.offset is not None else [])]),
    conditions=lambda c: [*(_number_condition(c.gain, int, Image.gain, None) if c.gain else []),
                          *(_number_condition(c.offset, int, Image.offset, None) if c.offset is not None else [])],
    mcp_descriptions={"gain": 'Camera gain, e.g. "100".', "offset": "Camera offset."},
)
TEMPERATURE = FilterEntry(
    key="temperature", fields=("temperature", "temperature_tolerance"), column=Image.set_temp,
    active=lambda c: bool(c.temperature),
    chip_label=lambda c: f"Temperature: {c.temperature} °C{_tolerance(c.temperature_tolerance, ' °C')}",
    summary=lambda c: f"{c.temperature}{_tolerance(c.temperature_tolerance, '°C')}",
    conditions=lambda c: _number_condition(c.temperature, float, Image.set_temp, c.temperature_tolerance)
    if c.temperature else [],
    mcp_descriptions={"temperature": 'Sensor cooling set-point in °C, e.g. "-10".',
                      "temperature_tolerance": "± °C tolerance for `temperature`; omit for an exact match."},
)


def _row_hdu():
    """The hdu of the search row's image (the query joins Image); 0 for a file without one."""
    return fn.COALESCE(Image.hdu, 0)


def _header_value(key: str, hdu):
    """The value of `key` in one cached header of the row's file, as a correlated subquery."""
    header = FitsHeader.alias()
    return (header.select(fn.decompress_header_value(header.header, key))
            .where((header.file == File.rowid) & (header.hdu == hdu)))


def _header_conditions(criteria: SearchCriteria) -> list:
    """KEY=n, KEY<n or KEY>n compare a header value; anything else searches the header text.

    A row is matched on its image's header as it was indexed: an extension's own header, completed
    with the primary's (hdu 0). A value comes from the extension when it has the keyword, else
    from the primary; text is searched in both.
    """
    header_text = criteria.header_text
    if not header_text:
        return []
    for symbol, compare in (('=', operator.eq), ('<', operator.lt), ('>', operator.gt)):
        if symbol in header_text:
            try:
                key, value = header_text.split(symbol)
                merged = fn.COALESCE(_header_value(key.strip(), _row_hdu()), _header_value(key.strip(), 0))
                return [compare(merged, float(value.strip()))]
            except ValueError:
                pass
            break
    # A correlated subquery rather than a join, so callers that already join
    # FitsHeader (e.g. to read header values) don't end up with it joined twice.
    return [fn.EXISTS(FitsHeader.select(SQL('1')).where(
        (FitsHeader.file == File.rowid) & FitsHeader.hdu.in_([0, _row_hdu()])
        & fn.decompress(FitsHeader.header).contains(header_text)))]


HEADER_TEXT = FilterEntry(
    key="header_text", fields=("header_text",),
    chip_label=lambda c: f"Header: {c.header_text}",
    summary=_value("header_text"), conditions=_header_conditions,
    mcp_descriptions={"header_text": 'FITS header match: "KEYWORD=value", "KEYWORD<value" or '
                                     '"KEYWORD>value" compares one keyword numerically, e.g. '
                                     '"FOCTEMP<0"; anything else (including a non-numeric '
                                     'value) is a substring search over the whole header text.'},
)


def _solved_conditions(criteria: SearchCriteria) -> list:
    if criteria.plate_solved is True:
        return [File.rowid.in_(FileWCS.select(FileWCS.file))]
    if criteria.plate_solved is False:
        return [File.rowid.not_in(FileWCS.select(FileWCS.file))]
    return []


PLATE_SOLVED = FilterEntry(
    key="plate_solved", fields=("plate_solved",),
    chip_label=lambda c: f"Plate Solved: {'Solved' if c.plate_solved else 'Unsolved'}",
    summary=lambda c: "Solved" if c.plate_solved else "Unsolved",
    conditions=_solved_conditions,
    mcp_descriptions={"plate_solved": "True = solved only, False = unsolved only, omit = either."},
)


def _project_conditions(criteria: SearchCriteria) -> list:
    if not criteria.project:
        return []
    if criteria.project.rowid > 0:
        return [File.rowid.in_(ProjectFile.select(ProjectFile.file).where(ProjectFile.project == criteria.project))]
    return [File.rowid.not_in(ProjectFile.select(ProjectFile.file))]  # NO_PROJECT


PROJECT = FilterEntry(
    key="project", fields=("project",),
    chip_label=lambda c: f"Project: {c.project.name}",
    summary=lambda c: c.project.name, conditions=_project_conditions,
    mcp_descriptions={"project": "A project rowid from `list_projects`, or -1 to match "
                                 "files not assigned to any project."},
)


def _coordinates_conditions(criteria: SearchCriteria) -> list:
    if not (criteria.coord_ra and criteria.coord_dec):
        return []
    try:
        pixels = hp.cone_search_skycoord(criteria.cone_center(), criteria.coord_radius * u.deg)
        return [Image.coord_pix256.in_(pixels.tolist())] if len(pixels) > 0 else []
    except Exception as e:  # astropy raises several types for unparsable input
        logging.error(f"Error applying coordinates filter: {str(e)}")
        return []


def _coordinates_summary(c: SearchCriteria) -> str:
    if c.coord_query:
        where = c.coord_query
    else:
        where = f"({c.coord_ra.split('.')[0]}{c.coord_dec.split('.')[0]})"
    return f"{where} ±{_number(c.coord_radius)}°"


def _coordinates_chip(c: SearchCriteria) -> str:
    where = c.coord_query or f"RA={c.coord_ra}, DEC={c.coord_dec}"
    return f"Coordinates: {where}, r={_number(c.coord_radius)}°"


COORDINATES = FilterEntry(
    key="coordinates", fields=("coord_ra", "coord_dec", "coord_radius", "coord_query"),
    column=Image.coord_pix256, active=lambda c: bool(c.coord_ra and c.coord_dec),
    chip_label=_coordinates_chip, summary=_coordinates_summary, conditions=_coordinates_conditions,
    mcp_descriptions={
        "coord_ra": 'Right Ascension in hours, decimal ("0.712") or sexagesimal '
                    '("00:42:44"). Requires `coord_dec`. Use `lookup_object` to '
                    "get an object's coordinates (note it returns RA in "
                    "degrees: divide by 15).",
        "coord_dec": 'Declination in degrees, decimal ("41.27") or sexagesimal '
                     '("+41:16:09"). Requires `coord_ra`.',
        "coord_radius": "Cone search radius in decimal degrees (used with "
                        "`coord_ra`/`coord_dec`); defaults to 0.5.",
    },
)


def _span(start, end, joiner: str) -> str:
    if start and end:
        return f"{_format_date(start)}{joiner}{_format_date(end)}"
    if start:
        return f"from {_format_date(start)}"
    return f"until {_format_date(end)}"


def _is_local(c: SearchCriteria) -> bool:
    return c.local_start is not None or c.local_end is not None


def _dates(c: SearchCriteria, joiner: str) -> str:
    """The range as it was typed: in UTC or in local time, which the label says."""
    if _is_local(c):
        return f"(local) {_span(c.local_start, c.local_end, joiner)}"
    return f"(UTC) {_span(c.start_datetime, c.end_datetime, joiner)}"


_UTC_NOTE = ("Compared with DATE-OBS in UTC: a value with an offset is converted to UTC, one "
             "without is taken as UTC.")
_LOCAL_NOTE = ("Compared with the image's local time (DATE-LOC, else DATE-OBS in the observatory's "
               "time zone, else in the observer timezone setting), as wall-clock time without an "
               "offset. Use either these or `start_datetime`/`end_datetime`, not both.")

# One filter, two ways to give the range: in UTC (date_obs) or in local time (local_time). The
# dialog sets one pair and clears the other. Separate fields, not a flag, so code that sets the
# UTC pair (master and reference-frame matching) is never read as local.
DATES = FilterEntry(
    key="dates", fields=("start_datetime", "end_datetime", "local_start", "local_end"), column=Image.date_obs,
    chip_label=lambda c: f"Date {_dates(c, ' - ')}",
    summary=lambda c: _dates(c, " to "),
    conditions=_ranges(("start_datetime", Image.date_obs, operator.ge),
                       ("end_datetime", Image.date_obs, operator.le),
                       ("local_start", Image.local_time, operator.ge),
                       ("local_end", Image.local_time, operator.le)),
    mcp_descriptions={"start_datetime": f"Only images observed at or after this time (ISO 8601). {_UTC_NOTE}",
                      "end_datetime": f"Only images observed at or before this time (ISO 8601). {_UTC_NOTE}",
                      "local_start": f"Only images observed at or after this local time (ISO 8601). {_LOCAL_NOTE}",
                      "local_end": f"Only images observed at or before this local time (ISO 8601). {_LOCAL_NOTE}"},
)

# The observing night: local time minus 12 hours, as a date, through the indexed expression.
NIGHT = FilterEntry(
    key="night", fields=("session_night",), column=None,
    chip_label=lambda c: f"Night {c.session_night.isoformat()}",
    summary=lambda c: f"night {c.session_night.isoformat()}",
    conditions=lambda c: [session_night_sql() == c.session_night.isoformat()] if c.session_night else [],
    mcp_descriptions={"session_night": "Only images of this observing night (YYYY-MM-DD): the local date on "
                                       "which the night began. A frame belongs to the night of its local "
                                       "time minus 12 hours, so frames before and after midnight share it. "
                                       "Use this for questions like \"last night\"."},
)


def _size(c: SearchCriteria) -> str:
    parts = [f"{axis} {r}" for axis, r in (("W", _range(c.width_min, c.width_max)),
                                            ("H", _range(c.height_min, c.height_max))) if r]
    return f"Size: {', '.join(parts)}"


IMAGE_SIZE = FilterEntry(
    key="image_size", fields=("width_min", "width_max", "height_min", "height_max"),
    chip_label=_size, summary=_size,
    conditions=_ranges(("width_min", Image.width, operator.ge), ("width_max", Image.width, operator.le),
                       ("height_min", Image.height, operator.ge), ("height_max", Image.height, operator.le)),
    mcp_descriptions={"width_min": "Image width in pixels, inclusive.",
                      "height_min": "Image height in pixels, inclusive."},
)


def _scale(c: SearchCriteria) -> str:
    return f"Scale: {_range(c.scale_min, c.scale_max)} arcsec/px"


PLATE_SCALE = FilterEntry(
    key="plate_scale", fields=("scale_min", "scale_max"),
    chip_label=_scale, summary=_scale,
    conditions=_ranges(("scale_min", Image.coord_scale, operator.ge), ("scale_max", Image.coord_scale, operator.le)),
    mcp_descriptions={"scale_min": "Plate scale in arcsec/pixel, inclusive. Only "
                                   "plate-solved images have a scale."},
)

# --- image quality -----------------------------------------------------------------------------

# (short name, criteria prefix, ImageStats column), in the order the dialog shows them.
_QUALITY_METRICS = (
    ("Stars", "star_count", ImageStats.star_count),
    ("FWHM", "fwhm", ImageStats.fwhm_median),
    ("Bg", "background", ImageStats.background_median),
    ("Bg RMS", "background_rms", ImageStats.background_rms),
    ("Elong.", "elongation", ImageStats.elongation_median),
)


def _quality_metrics(criteria: SearchCriteria) -> list:
    """The short names of the metrics the criteria constrain."""
    return [name for name, prefix, _ in _QUALITY_METRICS
            if _range(getattr(criteria, f"{prefix}_min"), getattr(criteria, f"{prefix}_max"))]


def _quality_conditions(criteria: SearchCriteria) -> list:
    """Analysed files whose statistics fall in every range given; unanalysed files never match."""
    stats = []
    for _, prefix, column in _QUALITY_METRICS:
        low, high = getattr(criteria, f"{prefix}_min"), getattr(criteria, f"{prefix}_max")
        if low is not None:
            stats.append(column >= low)
        if high is not None:
            stats.append(column <= high)
    return [fn.EXISTS(_row_stats().where(*stats))] if stats else []


IMAGE_QUALITY = FilterEntry(
    key="image_quality",
    fields=tuple(f"{prefix}_{bound}" for _, prefix, _ in _QUALITY_METRICS for bound in ("min", "max")),
    chip_label=lambda c: f"Quality: {', '.join(_quality_metrics(c))}",
    summary=lambda c: f"Quality: {', '.join(_quality_metrics(c))}",
    conditions=_quality_conditions,
    mcp_descriptions={
        "star_count_min": "Detected star count, inclusive. Like all the image-quality "
                          "filters (star_count, fwhm, background, background_rms, "
                          "elongation), this only matches images that have been "
                          "analysed in PhotonFinder -- unanalysed images are excluded.",
        "fwhm_min": "Median star FWHM in pixels, inclusive. Lower is sharper.",
        "background_min": "Median sky background level, inclusive, in the image's "
                          "own pixel units (ADU for integer data, 0-1 for normalised "
                          "float data) -- so only comparable across similar files.",
        "background_rms_min": "Sky background noise (RMS), inclusive, in the same pixel "
                              "units as `background_min`.",
        "elongation_min": "Median star elongation (major/minor axis ratio), inclusive; "
                          "1.0 is perfectly round, higher suggests trailing.",
    },
)


def _row_stats():
    """The search row's image's ImageStats row, as a correlated subquery (the query joins Image)."""
    return ImageStats.select(SQL('1')).where((ImageStats.file == File.rowid) & (ImageStats.hdu == _row_hdu()))


def diagnosis_conditions(hidden: list) -> list:
    """Conditions hiding every image with any of the given diagnosis values."""
    hiding = []
    if DIAGNOSIS_NOT_ANALYSED in hidden:
        hiding.append(fn.EXISTS(_row_stats().where(ImageStats.diagnosis.is_null(False))))
    matches = [ImageStats.diagnosis == DIAGNOSIS_OK if value == DIAGNOSIS_OK
               else ImageStats.diagnosis.contains(value)
               for value in hidden if value != DIAGNOSIS_NOT_ANALYSED]
    if matches:
        hiding.append(~fn.EXISTS(_row_stats().where(reduce(operator.or_, matches))))
    return hiding


def _diagnosis_label(c: SearchCriteria) -> str:
    hidden = c.diagnosis_hidden
    return "Diagnosis: hide bad" if set(hidden) == set(DIAGNOSIS_BAD_VALUES) else f"Diagnosis ({len(hidden)} hidden)"


QUALITY_DIAGNOSIS = FilterEntry(
    key="quality_diagnosis", fields=("diagnosis_hidden",),
    chip_label=_diagnosis_label, summary=_diagnosis_label,
    conditions=lambda c: diagnosis_conditions(c.diagnosis_hidden) if c.diagnosis_hidden else [],
)

# Every entry, in the order of the criteria's text summary and of the chips on restore.
ENTRIES = (PATHS, TYPE, FILTER, CAMERA, FILE_NAME, OBJECT_NAME, EXPOSURE, TELESCOPE, BINNING, GAIN,
           TEMPERATURE, HEADER_TEXT, PLATE_SOLVED, PROJECT, COORDINATES, DATES, NIGHT, IMAGE_SIZE, PLATE_SCALE,
           IMAGE_QUALITY, QUALITY_DIAGNOSIS)
BY_KEY = {entry.key: entry for entry in ENTRIES}


def conditions(criteria: SearchCriteria, exclude=None) -> list:
    """Every entry's conditions; `exclude` leaves out the entry constraining that column."""
    return [condition for entry in ENTRIES if exclude is None or entry.column is not exclude
            for condition in entry.conditions(criteria)]


def summary(criteria: SearchCriteria) -> str:
    """The criteria in a few words, for tab titles and labels."""
    parts = (entry.summary(criteria) for entry in ENTRIES if entry.is_active(criteria))
    return ', '.join(part for part in parts if part)


def mcp_description(field_name: str) -> Optional[str]:
    """The MCP description of a criteria field, from the entry that owns it."""
    for entry in ENTRIES:
        if field_name in entry.mcp_descriptions:
            return entry.mcp_descriptions[field_name]
    return None

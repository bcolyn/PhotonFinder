"""MCP (Model Context Protocol) server for PhotonFinder.

Exposes read-only access to the file library so that AI agents can query the database
using the same ``SearchCriteria`` JSON the GUI uses.

The server is hosted *inside* the application by ``McpServerController`` (bottom of this
module) on a loopback port. Agents reach it through ``photonfinder/mcp_stub.py``, a thin
stdio program their client spawns; a closed application therefore never surfaces as a
connection error, and the stub can offer to start it. See ``docs/mcp.md`` for why the work
has to happen in the application's own process rather than in the stub.

The tool set here is generated into ``mcp_manifest.py`` so the stub can list it without
the application running -- regenerate with ``scripts/build_mcp_manifest.py`` after adding
or re-describing a tool. It must therefore not vary with settings, which is why
``plate_solve_files`` is always registered and checks its setting when called.

Plate solving holds ``ApplicationContext.library_write_lock``, so a solve triggered by an
agent cannot overlap one started from the GUI, or any other library writer such as a scan.

Blocking database work is offloaded to worker threads with ``anyio.to_thread.run_sync``
and wrapped in ``context.database.bind_ctx(CORE_MODELS)`` -- the same pattern the Qt
``BackgroundLoader`` workers use, so every worker thread gets its own peewee connection
and the event loop stays responsive.

The report tools (``report_targets``, ``report_catalog_coverage``, ``get_header_values``)
are thin wrappers over ``photonfinder/reports.py``, which the Qt report windows also call --
so an agent and the application answer "what have I imaged" from one implementation.

``build_mcp`` is transport-agnostic; the transport is chosen by the caller.
"""
import csv
import dataclasses
import functools
import io
import json
import logging
import threading
import time
import typing
from datetime import datetime, timezone
from typing import Optional

import anyio
from peewee import JOIN
from pydantic import BaseModel, Field

from photonfinder import reports, solve_service, solvers
from photonfinder.filter_catalogue import mcp_description
from photonfinder.core import ApplicationContext, LibraryBusyError, mcp_port_file
from photonfinder.models import (
    CORE_MODELS, CATALOG_MODELS, SearchCriteria, InvalidCriteria, File, Image, LibraryRoot,
    Project, ProjectFile, FitsHeader, FileWCS, CatalogEntry,
)
from photonfinder.library_query import (
    SORTABLE_FIELDS, count_files, search_files as run_search_files, serialize_search_row,
    _SERIALIZED_IMAGE_FIELDS,
)
from photonfinder.platesolver import SolverHint

logger = logging.getLogger(__name__)
# Search logging is DEBUG-level diagnostic detail; enable it for this module specifically
# rather than dropping the whole app to DEBUG (root logger stays at main.py's LEVEL).
logger.setLevel(logging.DEBUG)

MAX_PAGE_SIZE = 500
MAX_SOLVE_BATCH = 10

# Caps on the lists nested inside a report row. The GUI reports are unbounded because a
# table widget and a CSV file do not care how many rows they hold; a context window does.
MAX_REPORT_PATHS = 20
MAX_FILES_PER_OBJECT = 10
MAX_HEADER_KEYWORDS = 20

# Solver name -> settings index, matching PlateSolveDialog's combo box order.

# Fields the agent may request distinct values for, mapped to their Image column.
DISTINCT_FIELDS = {
    "filter": Image.filter,
    "type": Image.image_type,
    "camera": Image.camera,
    "telescope": Image.telescope,
    "object_name": Image.object_name,
}

# SearchCriteria fields the MCP search input leaves out on purpose, and why. Every other field
# is in SearchCriteriaInput (a test holds the two together, so a new field needs a decision).
NOT_FOR_MCP = {
    "diagnosis_hidden": "the quality diagnosis filter is GUI-only for now",
    "paths_as_prefix": "MCP path filters always include subfolders",
    "coord_query": "only the display name of a coordinates search",
    "reference_file": "the GUI's calibration matching sets it",
    "sorting_index": "a GUI column position; MCP callers sort by `sorting_field`",
}

# The quality diagnosis filter is GUI-only for now.
_SEARCH_CRITERIA_FIELDS = {f.name for f in dataclasses.fields(SearchCriteria)} - {"diagnosis_hidden"}


def _coerce_header_value(value):
    """Make a FITS header value JSON-serializable."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _header_to_dict(blob: bytes) -> dict:
    """Decompress a stored FITS header blob into a flat keyword dict."""
    from photonfinder.filesystem import decode_header_blob
    header = decode_header_blob(blob)
    out = {}
    for key in header:
        if key in ("COMMENT", "HISTORY", ""):
            continue
        try:
            out[key] = _coerce_header_value(header[key])
        except Exception:
            pass
    return out


# --- Synchronous query layer (DB-bound; safe to call directly in tests) -------------
# Each function binds the models to the context's database and performs blocking work.
# The async MCP tools wrap these via anyio.to_thread.run_sync so the event loop is not
# blocked; tests call them directly on the connection's own thread.

def _clamp_page(page: int, page_size: int) -> tuple:
    return max(0, page), max(1, min(page_size, MAX_PAGE_SIZE))


def _to_text(result) -> str:
    """Serialize a tool result as compact JSON.

    Left to itself FastMCP pretty-prints results with `indent=2`, which costs the agent
    about a quarter of every response in whitespace. `default=str` mirrors its own
    fallback for values JSON cannot represent.
    """
    return json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)


MAX_LOGGED_ARGS = 500


def _log_args(kwargs: dict) -> str:
    """A tool call's arguments as one short log-friendly string.

    Pydantic inputs (`criteria`) are logged with unset fields dropped, which is what the
    agent actually sent; very long argument lists are clipped to keep the log readable.
    """
    def default(o):
        if isinstance(o, BaseModel):
            return o.model_dump(exclude_none=True, mode="json")
        return str(o)
    text = json.dumps(kwargs, separators=(",", ":"), ensure_ascii=False, default=default)
    text = text[1:-1]  # drop the enclosing braces; the call's parentheses frame it
    if len(text) > MAX_LOGGED_ARGS:
        text = text[:MAX_LOGGED_ARGS] + "..."
    return text


def _rows_to_csv(rows: list[dict], columns: list[str]) -> str:
    """Render flat result rows as CSV text; keys outside `columns` are ignored.

    CSV names each column once instead of repeating every key on every row, so a large
    report costs a fraction of the tokens of its JSON form. Nulls become empty cells.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


OUTPUT_FORMATS = ("json", "csv")
_TARGET_CSV_COLUMNS = ["object_name", "filter", "telescope", "camera",
                       "total_exposure_seconds", "file_count", "last_date_obs"]
_CATALOG_CSV_COLUMNS = ["catalog_id", "magnitude", "size", "file_count"]


def _format_error(output_format: str) -> Optional[dict]:
    if output_format not in OUTPUT_FORMATS:
        return {"error": f"Unknown format '{output_format}'. Valid formats: "
                          f"{', '.join(OUTPUT_FORMATS)}."}
    return None


def _criteria_from_dict(criteria: Optional[dict], tool: str = "search") -> SearchCriteria:
    """Turn an agent-supplied criteria dict into a SearchCriteria.

    Unknown keys are dropped rather than raising, so an over-eager agent inventing a
    filter name gets a broader search instead of a TypeError.
    """
    criteria = criteria or {}
    clean = {k: v for k, v in criteria.items() if k in _SEARCH_CRITERIA_FIELDS}
    dropped = criteria.keys() - clean.keys()
    if dropped:
        logger.debug("%s: dropping unknown criteria fields: %s", tool, sorted(dropped))
    logger.debug("%s: criteria=%s", tool, clean)
    try:
        sc = SearchCriteria.from_json(json.dumps(clean))
    except Project.DoesNotExist:
        raise InvalidCriteria("project", clean.get("project"),
                              "the rowid of an existing project (see list_projects)") from None
    sc.start_datetime = _naive_utc(sc.start_datetime)
    sc.end_datetime = _naive_utc(sc.end_datetime)
    sc.validate()
    return sc


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """DATE-OBS is stored as naive UTC: convert a value with an offset to that, and take one
    without an offset as UTC already."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class NoLibraryOpen(Exception):
    """Between closing one library and opening the next, or while the application closes."""

    def __init__(self):
        super().__init__("No library is open right now, as the application is switching library "
                         "or closing. Try again shortly.")


def _bound(context: ApplicationContext, models):
    database = context.database
    if database is None:
        raise NoLibraryOpen()
    return database.bind_ctx(models)


def query_search(context: ApplicationContext, criteria: Optional[dict] = None,
                 page: int = 0, page_size: int = 100) -> dict:
    page, page_size = _clamp_page(page, page_size)
    logger.debug("search_files: page=%d page_size=%d", page, page_size)
    with _bound(context, CORE_MODELS):
        sc = _criteria_from_dict(criteria, "search_files")
        rows, total, has_more = run_search_files(sc, page, page_size)
        logger.debug("search_files: returned %d rows (total=%d, has_more=%s)",
                      len(rows), total, has_more)
        return {
            "results": [serialize_search_row(r) for r in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": has_more,
        }


def query_library_roots(context: ApplicationContext) -> list[dict]:
    with _bound(context, CORE_MODELS):
        return [{"rowid": r.rowid, "name": r.name, "path": r.path,
                 "description": r.description}
                for r in LibraryRoot.select().order_by(LibraryRoot.name)]  # pylint: disable=not-an-iterable  # Peewee query


def _serialize_project(p: Project) -> dict:
    date_obs = getattr(p, "date_obs", None)
    if isinstance(date_obs, str):
        date_obs = datetime.fromisoformat(date_obs)
    if isinstance(date_obs, datetime):
        date_obs = date_obs.isoformat()
    # `coord_ra`/`coord_dec` come back nested under a synthetic `image` sub-object --
    # peewee attributes aliased columns to the model whose fields they name-match.
    image = getattr(p, "image", None)
    return {
        "rowid": p.rowid,
        "name": p.name,
        "file_count": getattr(p, "file_counts", None) or 0,
        "last_date_obs": date_obs,
        "coord_ra": getattr(image, "coord_ra", None),
        "coord_dec": getattr(image, "coord_dec", None),
    }


def query_projects(context: ApplicationContext) -> list[dict]:
    with _bound(context, CORE_MODELS):
        return [_serialize_project(p) for p in Project.list_projects_with_image_data()]


def query_project_details(context: ApplicationContext, rowid: int) -> dict:
    with _bound(context, CORE_MODELS):
        for p in Project.list_projects_with_image_data():
            if p.rowid == rowid:
                return _serialize_project(p)
        return {"error": f"No project with rowid {rowid}"}


def query_distinct_values(context: ApplicationContext, field: str) -> list:
    column = DISTINCT_FIELDS.get(field)
    if column is None:
        raise ValueError(
            f"Unknown field '{field}'. Valid fields: {', '.join(sorted(DISTINCT_FIELDS))}.")
    with _bound(context, CORE_MODELS):
        query = (Image.select(column)
                 .where(column.is_null(False))
                 .distinct()
                 .order_by(column))
        return [row[0] for row in query.tuples()]


def query_file_details(context: ApplicationContext, rowid: int) -> dict:
    with _bound(context, CORE_MODELS):
        file = (File.select(File, Image, LibraryRoot)
                .join_from(File, LibraryRoot)
                .join_from(File, Image, JOIN.LEFT_OUTER)
                .where(File.rowid == rowid)).get_or_none()
        if file is None:
            return {"error": f"No file with rowid {rowid}"}

        has_wcs = FileWCS.select().where(FileWCS.file == rowid).exists()
        details = {
            "rowid": file.rowid,
            "name": file.name,
            "path": file.path,
            "size": file.size,
            "mtime_millis": file.mtime_millis,
            "full_filename": file.full_filename(),
            "root": {"rowid": file.root.rowid, "name": file.root.name, "path": file.root.path,
                     "description": file.root.description},
            "has_wcs": has_wcs,
        }

        image = getattr(file, "image", None)
        if image is not None and image.rowid is not None:
            image_data = {name: getattr(image, name) for name in _SERIALIZED_IMAGE_FIELDS}
            date_obs = image.date_obs
            image_data["date_obs"] = date_obs.isoformat() if isinstance(date_obs, datetime) else date_obs
            details["image"] = image_data
        else:
            details["image"] = None

        details["projects"] = [
            pf.project.name for pf in
            ProjectFile.select(ProjectFile, Project).join(Project).where(ProjectFile.file == rowid)
        ]

        header_row = FitsHeader.select().where(FitsHeader.file == rowid).get_or_none()
        if header_row is not None:
            try:
                details["header"] = _header_to_dict(header_row.header)
            except Exception as e:
                logger.warning("Failed to decode header for file %s: %s", rowid, e)
                details["header"] = None
        else:
            details["header"] = None
        return details


# The MCP server can't import catalogs (it is read-only), but the user can: tell the model
# to point them there instead of giving up on a catalog PhotonFinder doesn't ship.
CUSTOM_CATALOG_HINT = (
    "If the user wants a catalog that is not listed, tell them they can add it themselves: "
    "in PhotonFinder's Catalog Report (Report menu), the Custom button > Import CSV... "
    "imports a CSV file with a header row and the columns id (or name), ra and dec "
    "(optional: magnitude, size, axis_ratio, angle, canonical_id)."
)


def _unknown_catalog_error(catalog: str, known: list[str]) -> dict:
    return {"error": f"Unknown catalog '{catalog}'. Valid catalogs: {', '.join(known)}. "
                     + CUSTOM_CATALOG_HINT}


def query_list_catalogs(context: ApplicationContext) -> list[str]:
    with _bound(context, CATALOG_MODELS):
        return [row[0] for row in
                CatalogEntry.select(CatalogEntry.catalog).distinct()
                .order_by(CatalogEntry.catalog).tuples()]


def query_lookup_object(context: ApplicationContext, catalog: str, catalog_id: str) -> dict:
    known = query_list_catalogs(context)
    if catalog not in known:
        return _unknown_catalog_error(catalog, known)
    with _bound(context, CATALOG_MODELS):
        entry = (CatalogEntry
                 .select()
                 .where(
                     (CatalogEntry.catalog == catalog) &
                     ((CatalogEntry.catalog_id == catalog_id) |
                      (CatalogEntry.canonical_id == catalog_id))
                 )
                 .first())
        if entry is None:
            return {"error": f"'{catalog_id}' not found in catalog '{catalog}'."}
        return {
            "catalog": entry.catalog,
            "catalog_id": entry.catalog_id,
            "canonical_id": entry.canonical_id,
            "ra": entry.ra,
            "dec": entry.dec,
            "size": entry.size,
            "axis_ratio": entry.axis_ratio,
            "angle": entry.angle,
            "magnitude": entry.magnitude,
        }


def query_target_report(context: ApplicationContext, criteria: Optional[dict] = None,
                        page: int = 0, page_size: int = 100, include_paths: bool = True,
                        max_paths: int = MAX_REPORT_PATHS, output_format: str = "json") -> dict:
    if error := _format_error(output_format):
        return error
    page, page_size = _clamp_page(page, page_size)
    max_paths = max(0, max_paths)
    # A list of paths has no place in a CSV cell; CSV is for compact summaries.
    include_paths = include_paths and output_format == "json"
    with _bound(context, CORE_MODELS):
        sc = _criteria_from_dict(criteria, "report_targets")
        rows = reports.target_report(sc)

    total = len(rows)
    # The total across every group, not just this page -- "how much integration do I have
    # on X" is the question these reports exist for, and paging to sum it is absurd.
    total_exposure_all = sum(r.total_exposure or 0 for r in rows)
    window = rows[page * page_size:(page + 1) * page_size]

    results = []
    for r in window:
        row = {
            "object_name": r.object_name,
            "filter": r.filter,
            "telescope": r.telescope,
            "camera": r.camera,
            "total_exposure_seconds": r.total_exposure,
            "file_count": r.file_count,
            "last_date_obs": reports.iso_datetime(r.last_date_obs),
        }
        if include_paths:
            row["paths"] = r.paths[:max_paths]
            row["paths_truncated"] = len(r.paths) > max_paths
        results.append(row)

    logger.debug("report_targets: %d groups (page %d of %d)", total, page, page_size)
    return {
        "format": output_format,
        "results": (_rows_to_csv(results, _TARGET_CSV_COLUMNS) if output_format == "csv"
                    else results),
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": (page + 1) * page_size < total,
        "total_exposure_seconds_all": total_exposure_all,
    }


def query_catalog_report(context: ApplicationContext, catalog: str,
                         criteria: Optional[dict] = None, only_matching: bool = True,
                         page: int = 0, page_size: int = 100, include_files: bool = True,
                         max_files: int = MAX_FILES_PER_OBJECT,
                         output_format: str = "json") -> dict:
    if error := _format_error(output_format):
        return error
    page, page_size = _clamp_page(page, page_size)
    max_files = max(0, max_files)
    include_files = include_files and output_format == "json"

    known = query_list_catalogs(context)
    if catalog not in known:
        return _unknown_catalog_error(catalog, known)

    with _bound(context, CORE_MODELS):
        sc = _criteria_from_dict(criteria, "report_catalog_coverage")
        result = reports.catalog_report(context, catalog, sc, only_matching,
                                        limit=page_size, offset=page * page_size)

    results = []
    for entry in result.entries:
        matches = result.matches.get(entry.rowid, [])
        row = {
            "catalog_id": entry.catalog_id,
            "magnitude": entry.magnitude,
            "size": entry.size,
            "file_count": len(matches),
        }
        if include_files:
            row["files"] = [{"rowid": m.file_id, "full_path": m.full_path}
                            for m in matches[:max_files]]
            row["files_truncated"] = len(matches) > max_files
        results.append(row)

    return {
        "catalog": catalog,
        "only_matching": only_matching,
        "format": output_format,
        "results": (_rows_to_csv(results, _CATALOG_CSV_COLUMNS) if output_format == "csv"
                    else results),
        "page": page,
        "page_size": page_size,
        "total": result.total_entries,
        "has_more": (page + 1) * page_size < result.total_entries,
        "images_considered": result.images_considered,
        "objects_matched": len(result.matches),
    }


def query_header_values(context: ApplicationContext, fields: Optional[list] = None,
                        criteria: Optional[dict] = None, page: int = 0,
                        page_size: int = 100, output_format: str = "json") -> dict:
    if error := _format_error(output_format):
        return error
    if not fields:
        return {"error": "`fields` must name at least one field. Call `get_file_details` on "
                          "one file to see which FITS keywords its header carries."}
    if len(fields) > MAX_HEADER_KEYWORDS:
        return {"error": f"Too many fields ({len(fields)}); at most {MAX_HEADER_KEYWORDS} "
                          "may be requested at once."}

    page, page_size = _clamp_page(page, page_size)
    specs = [(spec, *reports.parse_field_spec(spec)) for spec in fields]
    sources = {source for _, _, source in specs}

    with _bound(context, CORE_MODELS):
        sc = _criteria_from_dict(criteria, "get_header_values")
        total = count_files(sc)
        results = []
        for file in reports.header_value_files(sc, sources, page, page_size):
            values = {}
            for spec, field_name, source in specs:
                value = reports.extract_field_value(file, field_name, source)
                values[spec] = _coerce_header_value(value)
            results.append({
                "rowid": file.rowid,
                "full_filename": file.full_filename(),
                "values": values,
            })

    if output_format == "csv":
        # One column per requested field, named exactly as the caller spelled it.
        columns = ["rowid", "full_filename", *(spec for spec, _, _ in specs)]
        results = _rows_to_csv([{"rowid": r["rowid"], "full_filename": r["full_filename"],
                                 **r["values"]} for r in results], columns)
    return {
        "format": output_format,
        "results": results,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": (page + 1) * page_size < total,
    }


def _solver_index(name: Optional[str], default_index: int, allow_none: bool = False) -> int:
    return default_index if name is None else solvers.index_for(name, allow_none=allow_none)


MCP_SOLVE_WRITER = "Plate solving (MCP)"


def query_plate_solve(context: ApplicationContext, rowids: list[int], solver: Optional[str] = None,
                       backup_solver: Optional[str] = None, hint_ra: Optional[float] = None,
                       hint_dec: Optional[float] = None, hint_scale: Optional[float] = None,
                       hint_mode: Optional[str] = None, use_camera_scales: bool = False) -> dict:
    # Checked here rather than by leaving the tool unregistered, so that the advertised
    # tool set is the same for every run and can be generated ahead of time. The gate is
    # unchanged: without the setting, no solver runs.
    if not context.settings.get_mcp_allow_plate_solve():
        return {"error": "Plate solving from an AI agent is disabled. The user can enable "
                         "it in Settings -> MCP Server -> 'Allow AI agents to trigger "
                         "plate solving'."}
    if not rowids:
        return {"error": "No rowids given."}
    if len(rowids) > MAX_SOLVE_BATCH:
        return {"error": f"Too many files: {len(rowids)} (max {MAX_SOLVE_BATCH} per call)."}

    s = context.settings
    try:
        primary_index = _solver_index(solver, s.get_plate_solve_primary_solver())
        backup_index = _solver_index(backup_solver, s.get_plate_solve_backup_solver(), allow_none=True)
    except solvers.SolverConfigError as e:
        return {"error": str(e)}

    if context.database is None:
        raise NoLibraryOpen()
    try:
        with solve_service.holding_library(context, MCP_SOLVE_WRITER):
            return _solve_files(context, rowids, primary_index, backup_index, hint_ra, hint_dec,
                                hint_scale, hint_mode, use_camera_scales)
    except LibraryBusyError as e:
        return {"error": f"{e} Try again shortly."}
    except solve_service.SolveLockBusy as e:
        return {"error": str(e)}


def _solve_files(context, rowids, primary_index, backup_index, hint_ra, hint_dec, hint_scale,
                 hint_mode, use_camera_scales) -> dict:
    """The MCP side of a solve run: the given files in order, each outcome as a dict."""
    s = context.settings
    try:
        primary, backup = solvers.build_solvers(s, primary_index, backup_index)
    except solvers.SolverConfigError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Could not build solver: {e}"}

    hint = SolverHint(
        ra=hint_ra if hint_ra is not None else (float(s.get_plate_solve_hint_ra()) if s.get_plate_solve_hint_ra() else None),
        dec=hint_dec if hint_dec is not None else (float(s.get_plate_solve_hint_dec()) if s.get_plate_solve_hint_dec() else None),
        scale=hint_scale if hint_scale is not None else (s.get_plate_solve_hint_scale() or None),
        mode=hint_mode or s.get_plate_solve_hint_mode(),
    )

    results = []
    solved = failed = 0
    with _bound(context, CORE_MODELS):
        camera_scales = solve_service.CameraScales.load() if use_camera_scales else None
        run = solve_service.SolveRun(primary, backup, hint, camera_scales)
        files = {f.rowid: f for f in (File.select(File, Image)
                                      .join_from(File, Image, JOIN.LEFT_OUTER)
                                      .where(File.rowid.in_(rowids)))}
        missing = [rowid for rowid in rowids if rowid not in files]
        outcomes = dict((file.rowid, outcome) for file, outcome
                        in run.run(files[rowid] for rowid in rowids if rowid in files))
        for rowid in rowids:
            if rowid in missing:
                results.append({"rowid": rowid, "success": False, "error": f"No file with rowid {rowid}"})
                failed += 1
                continue
            file, outcome = files[rowid], outcomes[rowid]
            if outcome.success:
                solved += 1
                result = {
                    "rowid": rowid, "path": file.full_filename(), "success": True,
                    "ra": outcome.ra, "dec": outcome.dec, "scale_arcsec": outcome.scale_arcsec,
                    "solver": outcome.used_solver,
                }
                if outcome.warning:
                    result["warning"] = outcome.warning
                results.append(result)
            else:
                failed += 1
                results.append({
                    "rowid": rowid, "path": file.full_filename(), "success": False,
                    "error": outcome.error,
                })
    return {"results": results, "solved": solved, "failed": failed}


class _RootAndPathInput(BaseModel):
    root_id: int = Field(description="A library root's rowid, from `list_library_roots`.")
    path: Optional[str] = Field(
        default=None,
        description="Subdirectory prefix within the root to narrow the search. "
                     "Omit to match the whole root.")
    root_label: Optional[str] = Field(
        default=None,
        description="Display-only; accepted for symmetry with `list_library_roots` output "
                     "but not used for filtering. May be omitted.")


class SearchCriteriaInput(BaseModel):
    """Search filters, shared by `search_files` and the report tools. Every field is
    optional; omit any field to leave it unconstrained. All given fields are combined
    with AND."""

    type: Optional[str] = Field(default=None, description=mcp_description("type"))
    filter: Optional[str] = Field(default=None, description=mcp_description("filter"))
    camera: Optional[str] = Field(default=None, description=mcp_description("camera"))
    telescope: Optional[str] = Field(default=None, description=mcp_description("telescope"))
    object_name: Optional[str] = Field(default=None, description=mcp_description("object_name"))
    file_name: Optional[str] = Field(default=None, description=mcp_description("file_name"))
    exposure: Optional[str] = Field(default=None, description=mcp_description("exposure"))
    exposure_tolerance: Optional[float] = Field(default=None, description=mcp_description("exposure_tolerance"))
    binning: Optional[str] = Field(default=None, description=mcp_description("binning"))
    gain: Optional[str] = Field(default=None, description=mcp_description("gain"))
    offset: Optional[int] = Field(default=None, description=mcp_description("offset"))
    temperature: Optional[str] = Field(default=None, description=mcp_description("temperature"))
    temperature_tolerance: Optional[float] = Field(default=None, description=mcp_description("temperature_tolerance"))
    coord_ra: Optional[str] = Field(default=None, description=mcp_description("coord_ra"))
    coord_dec: Optional[str] = Field(default=None, description=mcp_description("coord_dec"))
    coord_radius: Optional[float] = Field(default=None, description=mcp_description("coord_radius"))
    start_datetime: Optional[datetime] = Field(default=None, description=mcp_description("start_datetime"))
    end_datetime: Optional[datetime] = Field(default=None, description=mcp_description("end_datetime"))
    header_text: Optional[str] = Field(default=None, description=mcp_description("header_text"))
    plate_solved: Optional[bool] = Field(default=None, description=mcp_description("plate_solved"))
    project: Optional[int] = Field(default=None, description=mcp_description("project"))
    paths: Optional[list[_RootAndPathInput]] = Field(default=None, description=mcp_description("paths"))
    width_min: Optional[int] = Field(default=None, description=mcp_description("width_min"))
    width_max: Optional[int] = None
    height_min: Optional[int] = Field(default=None, description=mcp_description("height_min"))
    height_max: Optional[int] = None
    scale_min: Optional[float] = Field(default=None, description=mcp_description("scale_min"))
    scale_max: Optional[float] = None
    star_count_min: Optional[int] = Field(default=None, description=mcp_description("star_count_min"))
    star_count_max: Optional[int] = None
    fwhm_min: Optional[float] = Field(default=None, description=mcp_description("fwhm_min"))
    fwhm_max: Optional[float] = None
    background_min: Optional[float] = Field(default=None, description=mcp_description("background_min"))
    background_max: Optional[float] = None
    background_rms_min: Optional[float] = Field(default=None, description=mcp_description("background_rms_min"))
    background_rms_max: Optional[float] = None
    elongation_min: Optional[float] = Field(default=None, description=mcp_description("elongation_min"))
    elongation_max: Optional[float] = None
    sorting_field: Optional[typing.Literal[tuple(SORTABLE_FIELDS)]] = Field(
        default=None, description="Field to sort results by. Omit for the default "
                                   "root/path/name order.")
    sorting_desc: Optional[bool] = Field(
        default=None, description="Sort direction for `sorting_field`; defaults to True "
                                   "(descending) if omitted.")


def _criteria_param(what: str, same_as_search: bool = True):
    """The `criteria` parameter type, described for the tool that takes it.

    Without a description of its own the parameter is advertised as a bare `$ref` to
    `SearchCriteriaInput`, and weaker agents do not realise that the report tools accept
    the same filters as `search_files` -- they call them unfiltered and page through the
    whole library instead.
    """
    shared = (" The same SearchCriteria object `search_files` takes -- every field works "
              "here too." if same_as_search else "")
    return typing.Annotated[Optional[SearchCriteriaInput], Field(
        description=(
            f"Optional filters restricting which files {what}.{shared} Omit a field to "
            "leave it unconstrained; omit `criteria` for the whole library. Example: "
            '{"object_name": "M 31", "filter": "Ha", "type": "LIGHT", '
            '"start_datetime": "2025-01-01T00:00:00"}.'
        ))]


FormatParam = typing.Annotated[typing.Literal[OUTPUT_FORMATS], Field(
    description='"json" (default) returns `results` as a list of objects. "csv" returns '
                "`results` as one CSV string with a header row -- the same rows at a "
                "fraction of the size, so prefer it for large summaries. Nested lists "
                "(`paths`, `files`) are omitted in CSV; empty cells are nulls. Totals and "
                "paging fields stay in the JSON wrapper either way.")]


def read_only(title: str):
    """Annotations for a tool that only reads the local library.

    Clients decide whether to prompt from these rather than from the description, so
    marking the harmless majority lets a user approve searching once and still be asked
    about `plate_solve_files`. `openWorldHint=False` because every one of these is
    answered from the local database -- no online service is contacted.
    """
    from mcp.types import ToolAnnotations
    return ToolAnnotations(title=title, readOnlyHint=True, openWorldHint=False)


def build_mcp(context: ApplicationContext):
    """Construct the FastMCP server with PhotonFinder's read-only tools."""
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    # The tool set is deliberately identical for every run, so it can be generated ahead
    # of time into `mcp_manifest.py` and served by the stub without the application being
    # started. `plate_solve_files` is therefore always registered and checks its setting
    # when called -- a tool that disappeared with a setting could not be baked, and an
    # agent that can see it can tell the user which setting to turn on.
    instructions = (
        "PhotonFinder manages an astrophotography file library (FITS/XISF images and "
        "calibration frames). Use `search_files` with a SearchCriteria JSON object to find "
        "files; use `list_library_roots`, `list_projects` and `list_distinct_values` to "
        "discover valid filter values. Start with `list_library_roots`: each root carries a "
        "user-written description of what it contains, which is the quickest way to orient "
        "yourself in an unfamiliar library. Use `get_project_details` to inspect a single project, "
        "`get_file_details` to inspect one file's full metadata and FITS header, and "
        "`lookup_object`/`list_catalogs` to resolve an object's RA/Dec from PhotonFinder's "
        "local catalogs -- the built-in ones plus any the user imported -- (no online lookups "
        "such as Simbad or Telescopius are performed). When the user asks about a catalog "
        "`list_catalogs` does not show, don't substitute another one: tell them they can "
        "import it as a CSV through the Custom button in the Catalog Report. "
        "`report_targets` and `report_catalog_coverage` give the same aggregates "
        "as the application's Report menu -- total integration per target (matched by object "
        "name; no plate solving required), and which catalog objects the library covers "
        "(only considering plate-solved images) -- and `get_header_values` reads a few named "
        "FITS/model/WCS fields across many files at once instead of one whole header at a "
        "time. `report_targets`, `report_catalog_coverage` and `get_header_values` all take "
        "the same optional `criteria` object as `search_files`, so scope them to the files "
        "you care about (an object, a filter, a date range, a library root...) instead of "
        "running them over the whole library. Those three also accept `format=\"csv\"`, "
        "which returns the same rows far more compactly. Everything is answered from a "
        "local, indexed database, so you can assume most calls are fast: prefer several "
        "narrow, targeted calls (discover values first, then filter) over one broad call "
        "you have to page through. The exceptions are `report_catalog_coverage`, which "
        "can take tens of seconds on a large library, and `plate_solve_files`, which runs "
        "an external solver per file. Every one of these is read-only, and PhotonFinder never writes files on your "
        "behalf: report content is returned to you to save as you see fit. "
        "`plate_solve_files` is the sole "
        "exception and the user must opt into it in Settings; it plate-solves up to "
        f"{MAX_SOLVE_BATCH} files at once and writes the resulting WCS/coordinates to the "
        "library."
    )
    # stateless_http: the stub answers `initialize` itself so that opening an agent
    # session does not start the application. Each request it forwards therefore arrives
    # without a prior handshake, which only a stateless server will accept.
    mcp = FastMCP("PhotonFinder", instructions=instructions, stateless_http=True)

    # The mcp library logs routine per-request chatter at INFO (e.g. "Processing request
    # of type ListToolsRequest"), which drowns out our own search logging. Quiet it down;
    # our tools still log through `logger` above.
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)
    # Stateless mode tears down a throwaway session after every request and logs
    # "Terminating session: None" for each at INFO -- pure noise; `tool` logs the calls.
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)

    def tool(**kwargs):
        """`mcp.tool`, but the result goes back as compact JSON text (see `_to_text`), and
        every call is logged with its arguments, duration and response size.

        `structured_output=False` because a structured result would be sent a second time
        as `structuredContent`, and clients that show the model both pay for it twice.
        """
        register = mcp.tool(structured_output=False, **kwargs)

        def decorate(fn):
            @functools.wraps(fn)
            async def wrapper(**kw):
                args = _log_args(kw)
                started = time.monotonic()
                try:
                    text = _to_text(await fn(**kw))
                except (NoLibraryOpen, InvalidCriteria) as e:
                    logger.info("MCP %s(%s): %s", fn.__name__, args, e)
                    return _to_text({"error": str(e)})
                except Exception:
                    logger.exception("MCP %s(%s) failed after %.2fs", fn.__name__, args,
                                     time.monotonic() - started)
                    raise
                logger.info("MCP %s(%s) -> %d chars in %.2fs", fn.__name__, args,
                            len(text), time.monotonic() - started)
                return text
            return register(wrapper)
        return decorate

    @tool(annotations=read_only("Search files"))
    async def search_files(criteria: _criteria_param("are returned", same_as_search=False) = None, page: int = 0,
                           page_size: int = 100) -> dict:
        """Search the file library.

        Returns `{results, page, page_size, total, has_more}`. `page` is zero-based.
        Discover valid values for `filter`/`type`/`camera`/etc. via `list_distinct_values`,
        and valid `paths` roots via `list_library_roots`.

        Each result's `stats` holds the stored image analysis, all null if never analysed.
        `stats.diagnosis` is the verdict against peer frames: null if not diagnosed, `OK`, or
        one or more `Bad ...` reasons joined by `, `.
        """
        clean = criteria.model_dump(exclude_none=True, mode="json") if criteria else None
        return await anyio.to_thread.run_sync(query_search, context, clean, page, page_size)

    @tool(annotations=read_only("Target report"))
    async def report_targets(criteria: _criteria_param("are summed into the report") = None,
                             page: int = 0, page_size: int = 100, include_paths: bool = True,
                             max_paths: int = MAX_REPORT_PATHS,
                             format: FormatParam = "json") -> dict:  # pylint: disable=redefined-builtin  # tool parameter name seen by clients
        """Total integration time per target, grouped by object / filter / telescope / camera.

        Accepts the same `criteria` filters as `search_files` to narrow which files are
        counted -- e.g. `criteria={"object_name": "M 31"}` for one target, or a
        `start_datetime`/`end_datetime` range for one season.

        The same aggregate as the application's Target Report. Each row carries the summed exposure in seconds, the file count, the
        most recent observation, and the library directories the files sit in. Files with no
        object name are excluded. Grouping is purely by the `object_name` recorded in each
        file's header (plus filter/telescope/camera) -- plate solving is not required.

        Returns `{format, results, page, page_size, total, has_more,
        total_exposure_seconds_all}`, where `total_exposure_seconds_all` sums every group,
        not just this page. Set `include_paths=false` when you only need integration totals;
        `paths` is capped at `max_paths` per row and `paths_truncated` says when it hit the
        cap. `format="csv"` is the most compact way to get totals for many targets. Narrow
        `criteria` rather than paging through thousands of groups.
        """
        clean = criteria.model_dump(exclude_none=True, mode="json") if criteria else None
        return await anyio.to_thread.run_sync(query_target_report, context, clean, page,
                                              page_size, include_paths, max_paths, format)

    @tool(annotations=read_only("Catalog coverage report"))
    async def report_catalog_coverage(catalog: str,
                                      criteria: _criteria_param(
                                          "are tested against the catalog") = None,
                                      only_matching: bool = True, page: int = 0,
                                      page_size: int = 100, include_files: bool = True,
                                      max_files: int = MAX_FILES_PER_OBJECT,
                                      format: FormatParam = "json") -> dict:  # pylint: disable=redefined-builtin  # tool parameter name seen by clients
        """Which objects of a catalog the library's images actually cover.

        Accepts the same `criteria` filters as `search_files` to narrow which images are
        considered -- e.g. `criteria={"telescope": "RedCat 51"}` for one rig, or `paths`
        for one library root.

        The same coverage report as the application's Catalog Report: each catalog entry is
        tested against the precise footprint of every matching image, not just its centre.
        `catalog` is one of the names from `list_catalogs`, which includes any custom
        catalog the user imported (e.g. their own target list).

        Only **plate-solved** images are considered -- an unsolved library yields an empty
        report, so check `images_considered` before concluding you have not imaged something.
        This can take tens of seconds on a large library.

        `only_matching` defaults to true and returns just the covered objects. Setting it
        false also lists objects you have *not* imaged, which for a catalog like NGC means
        paging through ~785k rows; you almost never want it.

        Returns `{catalog, only_matching, format, results, page, page_size, total, has_more,
        images_considered, objects_matched}`. Each result's `files` entries carry a `rowid`
        usable with `get_file_details`, capped at `max_files` with a `files_truncated` flag;
        set `include_files=false`, or `format="csv"`, for a pure coverage list.
        """
        clean = criteria.model_dump(exclude_none=True, mode="json") if criteria else None
        return await anyio.to_thread.run_sync(query_catalog_report, context, catalog, clean,
                                              only_matching, page, page_size, include_files,
                                              max_files, format)

    @tool(
        # Description passed explicitly rather than as a docstring because it has to name
        # MAX_HEADER_KEYWORDS -- same reason as plate_solve_files below.
        description=(
            "Read the same few fields from many files at once.\n\n"
            "The bulk form of `get_file_details`: instead of one file's entire header, this "
            "returns only the `fields` you name, across every file matching `criteria`. Use "
            'it for questions like "what was FOCTEMP across last night\'s subs".\n\n'
            "`criteria` takes the same filters as `search_files` and should almost always be "
            'given -- e.g. `criteria={"object_name": "M 31", "start_datetime": '
            '"2025-03-01T18:00:00", "end_datetime": "2025-03-02T06:00:00"}`.\n\n'
            "Each entry of `fields` names its source by prefix:\n"
            '- `"File.size"`, `"Image.exposure"` -- a PhotonFinder model field\n'
            '- `"WCS:CRVAL1"` -- a keyword from the plate-solve solution\n'
            '- anything else, e.g. `"FOCTEMP"` -- a FITS header keyword\n\n'
            "The `WCS:` prefix matters because keywords like `NAXIS1` and `CRVAL1` exist in "
            "both the original header and the solved one. To discover what a file carries, "
            "call `get_file_details` on one representative file and read its `header`.\n\n"
            f"At most {MAX_HEADER_KEYWORDS} fields per call. A field a file does not have "
            "comes back as null rather than an error. Returns "
            "{format, results, page, page_size, total, has_more} with one "
            "{rowid, full_filename, values} entry per file; with `format=\"csv\"` the "
            "columns are rowid, full_filename, then one per requested field, named as you "
            "spelled it."
        ),
        annotations=read_only("Header values"))
    async def get_header_values(fields: list[str],
                                criteria: _criteria_param("are read") = None,
                                page: int = 0, page_size: int = 100,
                                format: FormatParam = "json") -> dict:  # pylint: disable=redefined-builtin  # tool parameter name seen by clients
        clean = criteria.model_dump(exclude_none=True, mode="json") if criteria else None
        return await anyio.to_thread.run_sync(query_header_values, context, fields, clean,
                                              page, page_size, format)

    @tool(annotations=read_only("List library roots"))
    async def list_library_roots() -> list[dict]:
        """List the configured library roots (top-level scanned directories).

        Each root has a `rowid` (use it in `search_files` via `paths`), a `name`, its
        `path` on disk, and a user-written `description` of what it holds (may be null).
        Read the descriptions before searching -- they could describe the contents, so you
        can scope a search instead of scanning the whole library."""
        return await anyio.to_thread.run_sync(query_library_roots, context)

    @tool(annotations=read_only("List projects"))
    async def list_projects() -> list[dict]:
        """List projects defined in the library, with summary info per project:
        `file_count`, `last_date_obs` (most recent image's observation time), and the
        `coord_ra`/`coord_dec` (degrees) of its most recent image. Use the `rowid` with
        `search_files` (`criteria={"project": rowid}`) to list a project's files, or with
        `get_project_details` for the same summary for a single project."""
        return await anyio.to_thread.run_sync(query_projects, context)

    @tool(annotations=read_only("Project details"))
    async def get_project_details(rowid: int) -> dict:
        """Get summary details for a single project by its rowid: `file_count`,
        `last_date_obs`, and `coord_ra`/`coord_dec` of its most recent image."""
        return await anyio.to_thread.run_sync(query_project_details, context, rowid)

    @tool(annotations=read_only("List distinct values"))
    async def list_distinct_values(field: str) -> list:
        """List the distinct values present for a field, to help build search criteria.

        `field` must be one of: filter, type, camera, telescope, object_name.
        """
        return await anyio.to_thread.run_sync(query_distinct_values, context, field)

    @tool(annotations=read_only("File details"))
    async def get_file_details(rowid: int) -> dict:
        """Get full metadata for a single file by its rowid, including the decompressed
        FITS header keywords and plate-solve status. Use the `rowid` from `search_files`
        results."""
        return await anyio.to_thread.run_sync(query_file_details, context, rowid)

    @tool(annotations=read_only("List catalogs"))
    async def list_catalogs() -> list[str]:
        """List the local catalog names available for `lookup_object` and
        `report_catalog_coverage`: the built-in catalogs (e.g. "NGC", "IC", "Messier") plus
        any custom catalogs the user imported from CSV. Backed entirely by PhotonFinder's
        local data; performs no online lookups.

        If the catalog the user asks about is missing, tell them they can import it: in
        PhotonFinder's Catalog Report, Custom > Import CSV... takes a CSV with a header row
        and the columns id (or name), ra and dec (optional: magnitude, size, axis_ratio,
        angle, canonical_id)."""
        return await anyio.to_thread.run_sync(query_list_catalogs, context)

    @tool(annotations=read_only("Look up object"))
    async def lookup_object(catalog: str, catalog_id: str) -> dict:
        """Resolve an object's RA/Dec (degrees) from PhotonFinder's local catalogs only (the
        built-in ones plus any the user imported) -- no online services (Simbad,
        Telescopius, etc.) are contacted.

        `catalog` must be one of the names returned by `list_catalogs`. `catalog_id` is
        matched against either the catalog's own identifier or its canonical identifier
        (e.g. catalog="NGC", catalog_id="7000").

        Returns object fields (ra, dec, size, axis_ratio, angle, magnitude) or
        `{"error": ...}` if no match is found.
        """
        return await anyio.to_thread.run_sync(query_lookup_object, context, catalog, catalog_id)

    @tool(description=(
            f"Plate-solve up to {MAX_SOLVE_BATCH} files at once by rowid (from "
            "`search_files`/`get_file_details`), writing the resulting WCS solution and "
            "coordinates to the library. "
            "`solver`/`backup_solver` are one of " + ", ".join(f'"{key}"' for key in solvers.KEYS) + ", "
            'and `backup_solver` may also be "none" for no backup; omit to use the '
            "primary/backup solver configured in Settings (no backup if unset). `hint_ra`/`hint_dec` (degrees) and `hint_scale` (arcsec/pixel) seed the "
            'solver; `hint_mode` is "fallback" (only used if the file has no usable '
            'coordinates/scale) or "override" (always used). Omitted hint fields fall back '
            "to the values configured in Settings. "
            "With `use_camera_scales` true, a file that fails is retried with each pixel scale "
            "earlier solves found for its camera; off by default, as every retry is another "
            "solve and a failing batch then takes much longer. "
            "Returns {results, solved, failed}. `results` is a list of per-file dicts "
            "with `rowid`, `path`, `success`, and on success `ra`/`dec`/`scale_arcsec`/`solver`, "
            "or on failure `error`. One file's failure does not abort the rest of the batch. "
            'Returns {"error": ...} if the batch is empty, too large, if another job is '
            "writing to the library (a plate-solve, scan, compression or analysis, here or in "
            "another PhotonFinder window), or if the user "
            "has not enabled agent-triggered plate solving in Settings."
        ),
        # The one tool that writes, runs an external program, and can upload frames to
        # astrometry.net -- so it gets the opposite hints to the read-only eight and a
        # client has grounds to ask before every call. Not destructive: it adds a
        # solution rather than removing anything, and re-solving the same files lands on
        # the same answer.
        annotations=ToolAnnotations(
            title="Plate-solve files",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ))
    async def plate_solve_files(rowids: list[int], solver: Optional[str] = None,
                                backup_solver: Optional[str] = None,
                                hint_ra: Optional[float] = None, hint_dec: Optional[float] = None,
                                hint_scale: Optional[float] = None,
                                hint_mode: Optional[str] = None,
                                use_camera_scales: bool = False) -> dict:
        return await anyio.to_thread.run_sync(
            query_plate_solve, context, rowids, solver, backup_solver,
            hint_ra, hint_dec, hint_scale, hint_mode, use_camera_scales)

    return mcp


class McpServerController:
    """Hosts the MCP server inside the application, on a loopback port.

    Agents reach it through ``photonfinder-mcp``, a stdio stub their client spawns. The
    work has to happen *here*, in the application's own process, because a sandboxed
    client (an MSIX-packaged Claude Desktop, say) gives everything it launches a
    virtualized registry and redirected app data -- so a server running inside that
    sandbox would read default settings instead of the user's, and would hand the same
    broken environment to ASTAP and solve-field. The application runs outside it.

    Clients that speak HTTP themselves can skip the stub and connect to
    ``http://127.0.0.1:<port>/mcp`` directly, which is what ``requested_port`` (the
    ``mcp_port`` setting) is for: an address they can be configured with once. The stub
    never relies on it -- the port actually bound is published to
    ``core.mcp_port_file()`` -- so when the requested port is taken (a second copy of the
    application, say) the server falls back to one chosen by the OS and the stub keeps
    working. ``requested_port=0`` asks for an OS-chosen port outright.
    """

    def __init__(self, context: ApplicationContext, host: str = "127.0.0.1",
                 requested_port: int = 0):
        self.context = context
        self.host = host
        self.requested_port = requested_port
        self.port: Optional[int] = None
        self._published_port: Optional[int] = None  # what this server wrote to the port file
        self._thread: Optional[threading.Thread] = None
        self._server = None

    STARTUP_TIMEOUT = 15  # seconds for uvicorn to start accepting

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        import socket
        import uvicorn

        # Bind first so the port is known before uvicorn starts; handing the ready socket
        # over avoids the race of picking a free port and hoping it is still free.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                sock.bind((self.host, self.requested_port))
            except OSError as e:
                if not self.requested_port:
                    raise
                # Better a server on the wrong port than none: the stub finds it through
                # the port file either way, and only direct HTTP clients are affected.
                logger.warning("MCP port %d is unavailable (%s); using an automatically "
                               "chosen port instead. Clients configured with the fixed "
                               "HTTP address will not connect.", self.requested_port, e)
                sock.bind((self.host, 0))
        except OSError:
            sock.close()
            raise
        self.port = sock.getsockname()[1]

        app = build_mcp(self.context).streamable_http_app()
        # log_config=None: uvicorn's default config builds a formatter that probes
        # sys.stdout.isatty(), and the windowed build has no stdout at all -- configuring
        # it there fails outright. Our own logging is already set up by main.py.
        self._server = uvicorn.Server(
            uvicorn.Config(app, log_config=None, log_level="warning"))

        def serve():
            try:
                self._server.run(sockets=[sock])
            except Exception:
                logger.exception("MCP server crashed")

        self._thread = threading.Thread(target=serve, name="mcp-server", daemon=True)
        self._thread.start()

        # Only advertise the port once uvicorn is actually accepting, or a stub that
        # launched us could connect to a socket nothing is serving yet.
        deadline = time.monotonic() + self.STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if getattr(self._server, "started", False):
                break
            if not self._thread.is_alive():
                logger.error("MCP server thread died during startup")
                self._abandon_start()
                return
            time.sleep(0.05)
        else:
            logger.error("MCP server did not start within %ds; stopping it", self.STARTUP_TIMEOUT)
            self._abandon_start()
            return

        self._write_port_file()
        logger.info("MCP server listening on http://%s:%d/mcp", self.host, self.port)

    def _abandon_start(self) -> None:
        """Stop a server that didn't come up, so `running` is False and `start()` can retry."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("MCP server thread did not stop after a failed start")
        self._thread = None
        self._server = None
        self.port = None

    def _write_port_file(self) -> None:
        try:
            path = mcp_port_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(self.port), encoding="utf-8")
            self._published_port = self.port
        except OSError:
            logger.warning("Could not publish the MCP port to %s", mcp_port_file(),
                           exc_info=True)

    def _remove_port_file(self) -> None:
        """Remove the port file, but only if it still holds the port this server published:
        another running instance may have published its own since, and removing that would
        make it look down. A stale file is not fatal -- the stub probes before trusting it."""
        if self._published_port is None:
            return
        path = mcp_port_file()
        try:
            if path.read_text(encoding="utf-8").strip() == str(self._published_port):
                path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove %s", path, exc_info=True)
        self._published_port = None

    def stop(self) -> None:
        self._remove_port_file()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("MCP server thread did not stop within timeout")
        self._thread = None
        self._server = None
        self.port = None
        logger.info("MCP server stopped")

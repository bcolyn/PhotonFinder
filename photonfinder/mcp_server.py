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

Plate solving is guarded by ``ApplicationContext.solve_lock``, so a solve triggered by an
agent cannot overlap one started from the GUI.

Blocking database work is offloaded to worker threads with ``anyio.to_thread.run_sync``
and wrapped in ``context.database.bind_ctx(CORE_MODELS)`` -- the same pattern the Qt
``BackgroundLoader`` workers use, so every worker thread gets its own peewee connection
and the event loop stays responsive.

``build_mcp`` is transport-agnostic; the transport is chosen by the caller.
"""
import dataclasses
import json
import logging
import threading
import time
import typing
from datetime import datetime
from typing import Optional

import anyio
from peewee import JOIN
from pydantic import BaseModel, Field

from photonfinder import solve_service
from photonfinder.core import ApplicationContext, mcp_port_file
from photonfinder.models import (
    CORE_MODELS, CATALOG_MODELS, SearchCriteria, SORTABLE_FIELDS, File, Image, LibraryRoot,
    Project, ProjectFile, FitsHeader, FileWCS, CatalogEntry,
    search_files as run_search_files, serialize_search_row, _SERIALIZED_IMAGE_FIELDS,
)
from photonfinder.platesolver import (
    ASTAPSolver, AstrometryNetSolver, SolveFieldSolver, SolverHint,
)

logger = logging.getLogger(__name__)
# Search logging is DEBUG-level diagnostic detail; enable it for this module specifically
# rather than dropping the whole app to DEBUG (root logger stays at main.py's LEVEL).
logger.setLevel(logging.DEBUG)

MAX_PAGE_SIZE = 500
MAX_SOLVE_BATCH = 10

# Solver name -> settings index, matching PlateSolveDialog's combo box order.
SOLVER_NAMES = {"astap": 0, "astrometry_net": 1, "solve_field": 2}

# Fields the agent may request distinct values for, mapped to their Image column.
DISTINCT_FIELDS = {
    "filter": Image.filter,
    "type": Image.image_type,
    "camera": Image.camera,
    "telescope": Image.telescope,
    "object_name": Image.object_name,
}

_SEARCH_CRITERIA_FIELDS = {f.name for f in dataclasses.fields(SearchCriteria)}


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

def query_search(context: ApplicationContext, criteria: Optional[dict] = None,
                 page: int = 0, page_size: int = 100) -> dict:
    criteria = criteria or {}
    page = max(0, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    # Drop unknown keys so an over-eager agent can't trigger a TypeError.
    clean = {k: v for k, v in criteria.items() if k in _SEARCH_CRITERIA_FIELDS}
    dropped = criteria.keys() - clean.keys()
    if dropped:
        logger.debug("search_files: dropping unknown criteria fields: %s", sorted(dropped))
    logger.debug("search_files: criteria=%s page=%d page_size=%d", clean, page, page_size)
    with context.database.bind_ctx(CORE_MODELS):
        sc = SearchCriteria.from_json(json.dumps(clean))
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
    with context.database.bind_ctx(CORE_MODELS):
        return [{"rowid": r.rowid, "name": r.name, "path": r.path}
                for r in LibraryRoot.select().order_by(LibraryRoot.name)]


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
    with context.database.bind_ctx(CORE_MODELS):
        return [_serialize_project(p) for p in Project.list_projects_with_image_data()]


def query_project_details(context: ApplicationContext, rowid: int) -> dict:
    with context.database.bind_ctx(CORE_MODELS):
        for p in Project.list_projects_with_image_data():
            if p.rowid == rowid:
                return _serialize_project(p)
        return {"error": f"No project with rowid {rowid}"}


def query_distinct_values(context: ApplicationContext, field: str) -> list:
    column = DISTINCT_FIELDS.get(field)
    if column is None:
        raise ValueError(
            f"Unknown field '{field}'. Valid fields: {', '.join(sorted(DISTINCT_FIELDS))}.")
    with context.database.bind_ctx(CORE_MODELS):
        query = (Image.select(column)
                 .where(column.is_null(False))
                 .distinct()
                 .order_by(column))
        return [row[0] for row in query.tuples()]


def query_file_details(context: ApplicationContext, rowid: int) -> dict:
    with context.database.bind_ctx(CORE_MODELS):
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
            "root": {"rowid": file.root.rowid, "name": file.root.name, "path": file.root.path},
            "has_wcs": has_wcs,
        }

        image = getattr(file, "image", None)
        if image is not None and image.rowid is not None:
            image_data = {name: getattr(image, name) for name in _SERIALIZED_IMAGE_FIELDS}
            date_obs = image.date_obs
            image_data["date_obs"] = date_obs.isoformat() if date_obs else None
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


def query_list_catalogs(context: ApplicationContext) -> list[str]:
    with context.database.bind_ctx(CATALOG_MODELS):
        return [row[0] for row in
                CatalogEntry.select(CatalogEntry.catalog).distinct()
                .order_by(CatalogEntry.catalog).tuples()]


def query_lookup_object(context: ApplicationContext, catalog: str, catalog_id: str) -> dict:
    with context.database.bind_ctx(CATALOG_MODELS):
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


def _build_solver(context: ApplicationContext, solver_index: int):
    s = context.settings
    if solver_index == 0:
        return ASTAPSolver(exe=s.get_astap_path())
    elif solver_index == 1:
        return AstrometryNetSolver(
            api_key=s.get_astrometry_net_api_key(),
            force_image_upload=s.get_astrometry_net_force_image_upload(),
        )
    elif solver_index == 2:
        return SolveFieldSolver(
            exe_path=s.get_solve_field_path(),
            timeout=s.get_solve_field_timeout(),
            wsl_distro=s.get_solve_field_wsl_distro(),
        )
    return None


def _resolve_solver_index(name: Optional[str], default_index: int) -> int:
    if name is None:
        return default_index
    key = name.strip().lower()
    if key not in SOLVER_NAMES:
        raise ValueError(f"Unknown solver '{name}'. Valid solvers: {', '.join(sorted(SOLVER_NAMES))}.")
    return SOLVER_NAMES[key]


def query_plate_solve(context: ApplicationContext, rowids: list[int], solver: Optional[str] = None,
                       backup_solver: Optional[str] = None, hint_ra: Optional[float] = None,
                       hint_dec: Optional[float] = None, hint_scale: Optional[float] = None,
                       hint_mode: Optional[str] = None) -> dict:
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
        primary_index = _resolve_solver_index(solver, s.get_plate_solve_primary_solver())
        backup_index = _resolve_solver_index(backup_solver, s.get_plate_solve_backup_solver())
    except ValueError as e:
        return {"error": str(e)}

    if not context.solve_lock.acquire(blocking=False):
        return {"error": "A plate-solve is already running (from this application or "
                          "another MCP request); try again shortly."}
    try:
        try:
            primary = _build_solver(context, primary_index)
            backup = _build_solver(context, backup_index) if backup_index >= 0 else None
        except Exception as e:
            return {"error": f"Could not build solver: {e}"}
        if primary is None:
            return {"error": f"No primary solver configured (index {primary_index})."}

        hint = SolverHint(
            ra=hint_ra if hint_ra is not None else (float(s.get_plate_solve_hint_ra()) if s.get_plate_solve_hint_ra() else None),
            dec=hint_dec if hint_dec is not None else (float(s.get_plate_solve_hint_dec()) if s.get_plate_solve_hint_dec() else None),
            scale=hint_scale if hint_scale is not None else (s.get_plate_solve_hint_scale() or None),
            mode=hint_mode or s.get_plate_solve_hint_mode(),
        )

        results = []
        solved = failed = 0
        with context.database.bind_ctx(CORE_MODELS):
            for rowid in rowids:
                file = (File.select(File, Image)
                        .join_from(File, Image, JOIN.LEFT_OUTER)
                        .where(File.rowid == rowid)).get_or_none()
                if file is None:
                    results.append({"rowid": rowid, "success": False, "error": f"No file with rowid {rowid}"})
                    failed += 1
                    continue
                file_wcs = FileWCS.get_or_none(FileWCS.file == file)
                outcome = solve_service.solve_file(file, primary, backup, hint=hint, file_wcs=file_wcs)
                if outcome.success:
                    solved += 1
                    results.append({
                        "rowid": rowid, "path": file.full_filename(), "success": True,
                        "ra": outcome.ra, "dec": outcome.dec, "scale_arcsec": outcome.scale_arcsec,
                        "solver": outcome.used_solver,
                    })
                else:
                    failed += 1
                    results.append({
                        "rowid": rowid, "path": file.full_filename(), "success": False,
                        "error": outcome.error,
                    })
        return {"results": results, "solved": solved, "failed": failed}
    finally:
        context.solve_lock.release()


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
    """Search filters. Omit any field to leave it unconstrained."""

    type: Optional[str] = Field(default=None, description="LIGHT/DARK/FLAT/BIAS/MASTER ...")
    filter: Optional[str] = None
    camera: Optional[str] = None
    telescope: Optional[str] = None
    object_name: Optional[str] = None
    file_name: Optional[str] = None
    exposure: Optional[str] = Field(default=None, description="Exposure time in seconds.")
    exposure_tolerance: Optional[float] = Field(
        default=None, description="± seconds tolerance for `exposure`; omit for an exact match.")
    binning: Optional[str] = None
    gain: Optional[str] = None
    offset: Optional[int] = None
    temperature: Optional[str] = None
    temperature_tolerance: Optional[float] = Field(
        default=None, description="± °C tolerance for `temperature`; omit for an exact match.")
    coord_ra: Optional[str] = Field(default=None, description="Right Ascension in hours.")
    coord_dec: Optional[str] = Field(default=None, description="Declination in degrees.")
    coord_radius: Optional[float] = Field(
        default=None, description="Cone search radius in decimal degrees (used with "
                                   "`coord_ra`/`coord_dec`).")
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    header_text: Optional[str] = Field(
        default=None, description='Free text or a "KEYWORD=value"/"KEYWORD<value" style '
                                   'FITS header match, e.g. "GAIN=100", "FOCTEMP<0".')
    plate_solved: Optional[bool] = Field(
        default=None, description="True = solved only, False = unsolved only, "
                                   "omit = either.")
    project: Optional[int] = Field(
        default=None, description="A project rowid from `list_projects`, or -1 to match "
                                   "files not assigned to any project.")
    paths: Optional[list[_RootAndPathInput]] = Field(
        default=None, description="Restrict the search to one or more library roots.")
    width_min: Optional[int] = None
    width_max: Optional[int] = None
    height_min: Optional[int] = None
    height_max: Optional[int] = None
    scale_min: Optional[float] = Field(default=None, description="Plate scale, arcsec/pixel.")
    scale_max: Optional[float] = None
    star_count_min: Optional[int] = None
    star_count_max: Optional[int] = None
    fwhm_min: Optional[float] = None
    fwhm_max: Optional[float] = None
    background_min: Optional[float] = None
    background_max: Optional[float] = None
    background_rms_min: Optional[float] = None
    background_rms_max: Optional[float] = None
    elongation_min: Optional[float] = None
    elongation_max: Optional[float] = None
    sorting_field: Optional[typing.Literal[tuple(SORTABLE_FIELDS)]] = Field(
        default=None, description="Field to sort results by. Omit for the default "
                                   "root/path/name order.")
    sorting_desc: Optional[bool] = Field(
        default=None, description="Sort direction for `sorting_field`; defaults to True "
                                   "(descending) if omitted.")


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
        "discover valid filter values, `get_project_details` to inspect a single project, "
        "`get_file_details` to inspect one file's full metadata and FITS header, and "
        "`lookup_object`/`list_catalogs` to resolve an object's RA/Dec from PhotonFinder's "
        "local catalog database (no online lookups such as Simbad or Telescopius are "
        "performed). Every one of these is read-only. `plate_solve_files` is the sole "
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

    @mcp.tool(annotations=read_only("Search files"))
    async def search_files(criteria: Optional[SearchCriteriaInput] = None, page: int = 0,
                           page_size: int = 100) -> dict:
        """Search the file library.

        Returns `{results, page, page_size, total, has_more}`. `page` is zero-based.
        Discover valid values for `filter`/`type`/`camera`/etc. via `list_distinct_values`,
        and valid `paths` roots via `list_library_roots`.
        """
        clean = criteria.model_dump(exclude_none=True, mode="json") if criteria else None
        return await anyio.to_thread.run_sync(query_search, context, clean, page, page_size)

    @mcp.tool(annotations=read_only("List library roots"))
    async def list_library_roots() -> list[dict]:
        """List the configured library roots (top-level scanned directories)."""
        return await anyio.to_thread.run_sync(query_library_roots, context)

    @mcp.tool(annotations=read_only("List projects"))
    async def list_projects() -> list[dict]:
        """List projects defined in the library, with summary info per project:
        `file_count`, `last_date_obs` (most recent image's observation time), and the
        `coord_ra`/`coord_dec` (degrees) of its most recent image. Use the `rowid` with
        `search_files` (`criteria={"project": rowid}`) to list a project's files, or with
        `get_project_details` for the same summary for a single project."""
        return await anyio.to_thread.run_sync(query_projects, context)

    @mcp.tool(annotations=read_only("Project details"))
    async def get_project_details(rowid: int) -> dict:
        """Get summary details for a single project by its rowid: `file_count`,
        `last_date_obs`, and `coord_ra`/`coord_dec` of its most recent image."""
        return await anyio.to_thread.run_sync(query_project_details, context, rowid)

    @mcp.tool(annotations=read_only("List distinct values"))
    async def list_distinct_values(field: str) -> list:
        """List the distinct values present for a field, to help build search criteria.

        `field` must be one of: filter, type, camera, telescope, object_name.
        """
        return await anyio.to_thread.run_sync(query_distinct_values, context, field)

    @mcp.tool(annotations=read_only("File details"))
    async def get_file_details(rowid: int) -> dict:
        """Get full metadata for a single file by its rowid, including the decompressed
        FITS header keywords and plate-solve status. Use the `rowid` from `search_files`
        results."""
        return await anyio.to_thread.run_sync(query_file_details, context, rowid)

    @mcp.tool(annotations=read_only("List catalogs"))
    async def list_catalogs() -> list[str]:
        """List the local catalog names available for `lookup_object` (e.g. "NGC", "IC",
        "M"). Backed entirely by PhotonFinder's local catalog database; performs no
        online lookups."""
        return await anyio.to_thread.run_sync(query_list_catalogs, context)

    @mcp.tool(annotations=read_only("Look up object"))
    async def lookup_object(catalog: str, catalog_id: str) -> dict:
        """Resolve an object's RA/Dec (degrees) from PhotonFinder's local catalog
        database only -- no online services (Simbad, Telescopius, etc.) are contacted.

        `catalog` must be one of the names returned by `list_catalogs`. `catalog_id` is
        matched against either the catalog's own identifier or its canonical identifier
        (e.g. catalog="NGC", catalog_id="7000").

        Returns object fields (ra, dec, size, axis_ratio, angle, magnitude) or
        `{"error": ...}` if no match is found.
        """
        return await anyio.to_thread.run_sync(query_lookup_object, context, catalog, catalog_id)

    @mcp.tool(description=(
            f"Plate-solve up to {MAX_SOLVE_BATCH} files at once by rowid (from "
            "`search_files`/`get_file_details`), writing the resulting WCS solution and "
            "coordinates to the library. "
            '`solver`/`backup_solver` are one of "astap", "astrometry_net", "solve_field"; '
            "omit to use the primary/backup solver configured in Settings (no backup if "
            "unset). `hint_ra`/`hint_dec` (degrees) and `hint_scale` (arcsec/pixel) seed the "
            'solver; `hint_mode` is "fallback" (only used if the file has no usable '
            'coordinates/scale) or "override" (always used). Omitted hint fields fall back '
            "to the values configured in Settings. "
            "Returns {results, solved, failed}. `results` is a list of per-file dicts "
            "with `rowid`, `path`, `success`, and on success `ra`/`dec`/`scale_arcsec`/`solver`, "
            "or on failure `error`. One file's failure does not abort the rest of the batch. "
            'Returns {"error": ...} if the batch is empty, too large, if a plate-solve is '
            "already running (from this application or another request), or if the user "
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
                                hint_mode: Optional[str] = None) -> dict:
        return await anyio.to_thread.run_sync(
            query_plate_solve, context, rowids, solver, backup_solver,
            hint_ra, hint_dec, hint_scale, hint_mode)

    return mcp


class McpServerController:
    """Hosts the MCP server inside the application, on a loopback port.

    Agents reach it through ``photonfinder-mcp``, a stdio stub their client spawns. The
    work has to happen *here*, in the application's own process, because a sandboxed
    client (an MSIX-packaged Claude Desktop, say) gives everything it launches a
    virtualized registry and redirected app data -- so a server running inside that
    sandbox would read default settings instead of the user's, and would hand the same
    broken environment to ASTAP and solve-field. The application runs outside it.

    The port is chosen by the OS and published to ``core.mcp_port_file()`` for the stub
    to find; a fixed port would need configuring in two places and could be taken.
    """

    def __init__(self, context: ApplicationContext, host: str = "127.0.0.1"):
        self.context = context
        self.host = host
        self.port: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._server = None

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
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if getattr(self._server, "started", False):
                break
            if not self._thread.is_alive():
                logger.error("MCP server thread died during startup")
                return
            time.sleep(0.05)
        else:
            logger.error("MCP server did not start within 15s; not publishing its port")
            return

        self._write_port_file()
        logger.info("MCP server listening on http://%s:%d/mcp", self.host, self.port)

    def _write_port_file(self) -> None:
        try:
            path = mcp_port_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(self.port), encoding="utf-8")
        except OSError:
            logger.warning("Could not publish the MCP port to %s", mcp_port_file(),
                           exc_info=True)

    def _remove_port_file(self) -> None:
        # A stale file is not fatal -- the stub falls back to starting the application
        # when nothing answers -- but removing it keeps the common case honest.
        try:
            mcp_port_file().unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove %s", mcp_port_file(), exc_info=True)

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

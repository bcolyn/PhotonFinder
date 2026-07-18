"""Embedded MCP (Model Context Protocol) server for PhotonFinder.

Exposes read-only access to the file library over a local HTTP transport so that AI
agents can query the database using the same ``SearchCriteria`` JSON the GUI uses.

The server runs *inside* the GUI process (one process owns the SQLite database), served
over loopback HTTP via FastMCP/uvicorn on a dedicated daemon thread. Blocking database
work is offloaded to worker threads with ``anyio.to_thread.run_sync`` and wrapped in
``context.database.bind_ctx(CORE_MODELS)`` -- the same pattern the Qt ``BackgroundLoader``
workers use, so every worker thread gets its own peewee connection and the event loop
stays responsive.

The tool/serialization layer (``build_mcp``) is transport-agnostic; only
``McpServerController`` is tied to the embedded HTTP transport.
"""
import dataclasses
import json
import logging
import threading
from datetime import datetime
from typing import Optional

import anyio
from peewee import JOIN

from photonfinder.core import ApplicationContext
from photonfinder.models import (
    CORE_MODELS, CATALOG_MODELS, SearchCriteria, File, Image, LibraryRoot, Project,
    ProjectFile, FitsHeader, FileWCS, CatalogEntry,
    search_files as run_search_files, serialize_search_row, _SERIALIZED_IMAGE_FIELDS,
)

logger = logging.getLogger(__name__)
# Search logging is DEBUG-level diagnostic detail; enable it for this module specifically
# rather than dropping the whole app to DEBUG (root logger stays at main.py's LEVEL).
logger.setLevel(logging.DEBUG)

MAX_PAGE_SIZE = 500

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


def build_mcp(context: ApplicationContext, host: str = "127.0.0.1", port: int = 8765):
    """Construct the FastMCP server with PhotonFinder's read-only tools."""
    from mcp.server.fastmcp import FastMCP

    instructions = (
        "PhotonFinder manages an astrophotography file library (FITS/XISF images and "
        "calibration frames). Use `search_files` with a SearchCriteria JSON object to find "
        "files; use `list_library_roots`, `list_projects` and `list_distinct_values` to "
        "discover valid filter values, `get_project_details` to inspect a single project, "
        "`get_file_details` to inspect one file's full metadata and FITS header, and "
        "`lookup_object`/`list_catalogs` to resolve an object's RA/Dec from PhotonFinder's "
        "local catalog database (no online lookups such as Simbad or Telescopius are "
        "performed). All tools are read-only."
    )
    mcp = FastMCP(
        "PhotonFinder",
        instructions=instructions,
        host=host,
        port=port,
        stateless_http=True,
    )

    # The mcp library logs routine per-request/session chatter at INFO (e.g. "Terminating
    # session: None", "Processing request of type ListToolsRequest"), which drowns out our
    # own search logging. Quiet it down; our tools still log through `logger` above.
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)

    @mcp.tool()
    async def search_files(criteria: Optional[dict] = None, page: int = 0,
                           page_size: int = 100) -> dict:
        """Search the file library.

        `criteria` is a SearchCriteria-shaped object. Useful fields include: `type`
        (LIGHT/DARK/FLAT/BIAS/MASTER ...), `filter`, `camera`, `telescope`, `object_name`,
        `file_name`, `exposure` (seconds, with optional `exposure_tolerance`), `binning`,
        `gain`, `offset`, `temperature` (with optional `temperature_tolerance`),
        `coord_ra`/`coord_dec` (RA in hours, Dec in degrees) plus `coord_radius` (degrees)
        for a cone search, `start_datetime`/`end_datetime` (ISO 8601), `plate_solved`
        (true/false), pixel-dimension and image-quality ranges (`width_min`, `fwhm_max`, ...),
        `project` (a project rowid from `list_projects`, or `-1` to match files that are
        NOT assigned to any project), `header_text` (e.g. "GAIN=100", "FOCTEMP<0", or free
        text), and `paths` to restrict the search to one or more library roots -- a list of
        `{"root_id": ...}` objects, where `root_id` comes from `list_library_roots`.
        Optionally add `path`, a subdirectory prefix within that root to narrow further
        (omit or use `null` to match the whole root); `root_label` is accepted for
        symmetry with `list_library_roots` output but is display-only and may be omitted.
        Omit a field to leave it unconstrained.

        Returns `{results, page, page_size, total, has_more}`. `page` is zero-based.
        Discover valid values for `filter`/`type`/`camera`/etc. via `list_distinct_values`,
        and valid `paths` roots via `list_library_roots`.
        """
        return await anyio.to_thread.run_sync(query_search, context, criteria, page, page_size)

    @mcp.tool()
    async def list_library_roots() -> list[dict]:
        """List the configured library roots (top-level scanned directories)."""
        return await anyio.to_thread.run_sync(query_library_roots, context)

    @mcp.tool()
    async def list_projects() -> list[dict]:
        """List projects defined in the library, with summary info per project:
        `file_count`, `last_date_obs` (most recent image's observation time), and the
        `coord_ra`/`coord_dec` (degrees) of its most recent image. Use the `rowid` with
        `search_files` (`criteria={"project": rowid}`) to list a project's files, or with
        `get_project_details` for the same summary for a single project."""
        return await anyio.to_thread.run_sync(query_projects, context)

    @mcp.tool()
    async def get_project_details(rowid: int) -> dict:
        """Get summary details for a single project by its rowid: `file_count`,
        `last_date_obs`, and `coord_ra`/`coord_dec` of its most recent image."""
        return await anyio.to_thread.run_sync(query_project_details, context, rowid)

    @mcp.tool()
    async def list_distinct_values(field: str) -> list:
        """List the distinct values present for a field, to help build search criteria.

        `field` must be one of: filter, type, camera, telescope, object_name.
        """
        return await anyio.to_thread.run_sync(query_distinct_values, context, field)

    @mcp.tool()
    async def get_file_details(rowid: int) -> dict:
        """Get full metadata for a single file by its rowid, including the decompressed
        FITS header keywords and plate-solve status. Use the `rowid` from `search_files`
        results."""
        return await anyio.to_thread.run_sync(query_file_details, context, rowid)

    @mcp.tool()
    async def list_catalogs() -> list[str]:
        """List the local catalog names available for `lookup_object` (e.g. "NGC", "IC",
        "M"). Backed entirely by PhotonFinder's local catalog database; performs no
        online lookups."""
        return await anyio.to_thread.run_sync(query_list_catalogs, context)

    @mcp.tool()
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

    return mcp


class McpServerController:
    """Runs the embedded MCP HTTP server on a dedicated daemon thread.

    The server is bound to loopback only. Start it from the GUI thread once the
    ApplicationContext's database is open; stop it on application shutdown.
    """

    def __init__(self, context: ApplicationContext, host: str = "127.0.0.1",
                 port: int = 8765):
        self.context = context
        self.host = host
        self.port = port
        self._thread: Optional[threading.Thread] = None
        self._server = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        import uvicorn

        mcp = build_mcp(self.context, self.host, self.port)
        app = mcp.streamable_http_app()
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)

        def serve():
            try:
                self._server.run()
            except Exception:
                logger.exception("MCP server crashed")

        self._thread = threading.Thread(target=serve, name="mcp-server", daemon=True)
        self._thread.start()
        logger.info("MCP server started on http://%s:%d/mcp", self.host, self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("MCP server thread did not stop within timeout")
        self._thread = None
        self._server = None
        logger.info("MCP server stopped")

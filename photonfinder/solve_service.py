"""Shared plate-solve-and-persist logic used by both the Qt UI (``PlateSolveTask`` in
``ui/BackgroundLoader.py``) and the MCP server (``mcp_server.py``), which is hosted in
this same process -- the two are kept apart by ``ApplicationContext.solve_lock``.

Handles primary/backup solver fallback for a single file and, on success, writes the
resulting WCS solution to the database (``FileWCS`` + ``Image`` coordinate columns).
Callers that need per-file progress reporting or camera-scale-cache retries (the GUI's
``PlateSolveTask``) still wrap this function with that extra logic themselves.
"""
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from astropy.io.fits import Header
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from photonfinder.core import compress
from photonfinder.models import File, Image, FileWCS
from photonfinder.platesolver import (
    SolverBase, SolverHint, SolverError, SolverFailure, get_image_center_coords,
    stamp_wcs_origin,
)

logger = logging.getLogger(__name__)


@dataclass
class SolveOutcome:
    success: bool
    error: Optional[str] = None
    ra: Optional[float] = None
    dec: Optional[float] = None
    radius: Optional[float] = None
    scale_arcsec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    used_solver: Optional[str] = None


def _solver_name(solver: SolverBase) -> str:
    from photonfinder.platesolver import ASTAPSolver, AstrometryNetSolver, SolveFieldSolver
    if isinstance(solver, ASTAPSolver):
        return "ASTAP"
    if isinstance(solver, AstrometryNetSolver):
        return "Astrometry.net"
    if isinstance(solver, SolveFieldSolver):
        return "solve-field"
    return type(solver).__name__


def solve_file(file: File, solver: SolverBase, backup_solver: SolverBase = None,
                hint: SolverHint = None, file_wcs: FileWCS = None,
                output_callback: Callable[[str], None] = None) -> SolveOutcome:
    """Solve a single file with the given solver (falling back to ``backup_solver`` on
    failure) and, on success, persist the WCS solution to the database.

    Returns a :class:`SolveOutcome` describing the result; never raises for solver
    failures (``SolverError``/``SolverFailure``) or unexpected errors -- those are
    captured in ``SolveOutcome.error``.
    """
    primary_name = _solver_name(solver)
    backup_name = _solver_name(backup_solver) if backup_solver else None
    try:
        try:
            with solver:
                solution: Header = solver.solve(
                    Path(file.full_filename()), file.image, hint,
                    output_callback=output_callback, file_wcs=file_wcs,
                )
            used_solver = solver
        except (SolverFailure, SolverError) as primary_error:
            if not backup_solver:
                raise
            if output_callback:
                output_callback(f"  → {primary_name} failed ({primary_error}), trying {backup_name}…")
            with backup_solver:
                solution = backup_solver.solve(
                    Path(file.full_filename()), file.image, hint,
                    output_callback=output_callback, file_wcs=file_wcs,
                )
            used_solver = backup_solver

        stamp_wcs_origin(solution, used_solver.wcs_origin)
        new_file_wcs = FileWCS(file=file, wcs=compress(solution.tostring().encode()))
        FileWCS.insert(new_file_wcs.__data__).on_conflict_replace().execute()
        ra, dec, healpix, radius = get_image_center_coords(solution)
        Image.update(coord_ra=ra, coord_dec=dec, coord_pix256=healpix, coord_radius=radius
                     ).where(Image.file == file).execute()
        file.has_wcs = True
        if file.image:
            file.image.coord_ra = ra
            file.image.coord_dec = dec
            file.image.coord_pix256 = healpix
            file.image.coord_radius = radius

        width = height = None
        naxis1 = solution.get('NAXIS1')
        naxis2 = solution.get('NAXIS2')
        if naxis1 and naxis2:
            width, height = int(naxis1), int(naxis2)
            Image.update(width=width, height=height).where(Image.file == file).execute()
            if file.image:
                file.image.width = width
                file.image.height = height

        pixel_scales = proj_plane_pixel_scales(WCS(solution))  # degrees/pixel
        scale_arcsec = round(float(pixel_scales[0]) * 3600, 3)
        logger.info("Solved %s: RA=%.4f°  Dec=%.4f°  scale=%.2f\"/px", file.name, ra, dec, scale_arcsec)
        return SolveOutcome(success=True, ra=ra, dec=dec, radius=radius, scale_arcsec=scale_arcsec,
                             width=width, height=height, used_solver=_solver_name(used_solver))
    except SolverError as e:
        logger.warning("Cannot solve %s: %s", file.name, e)
        return SolveOutcome(success=False, error=str(e))
    except SolverFailure as failure:
        logger.warning("Could not solve %s: %s", file.name, failure)
        error_msg = str(failure)
        if failure.log:
            is_line_list = isinstance(failure.log, Iterable) and not isinstance(failure.log, str)
            log_lines = [str(l).strip() for l in failure.log] if is_line_list else [str(failure.log)]
            if output_callback:
                for line in log_lines:
                    output_callback(line)
            error_msg = f"{error_msg}\n\n" + "\n".join(log_lines)
        return SolveOutcome(success=False, error=error_msg)
    except Exception as e:
        logger.error("Error solving file %s: %s", file.full_filename(), e, exc_info=True)
        return SolveOutcome(success=False, error=str(e))

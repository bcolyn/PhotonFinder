"""Shared plate-solve-and-persist logic used by both the Qt UI (``PlateSolveTask`` in
``ui/BackgroundLoader.py``) and the MCP server (``mcp_server.py``), which is hosted in
this same process -- the two are kept apart by ``ApplicationContext.library_write_lock``,
which both take for the whole run.

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
    SolverBase, SolverHint, SolverError, SolverFailure, SolverCancelled, get_image_center_coords,
    stamp_wcs_origin,
)

logger = logging.getLogger(__name__)


@dataclass
class SolveOutcome:
    success: bool
    error: Optional[str] = None
    cancelled: bool = False  # stopped by the caller: not solved, but not an error either
    ra: Optional[float] = None
    dec: Optional[float] = None
    radius: Optional[float] = None
    scale_arcsec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    used_solver: Optional[str] = None
    warning: Optional[str] = None  # solved and stored, but something about it needs saying


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
                output_callback: Callable[[str], None] = None,
                cancelled: Callable[[], bool] = None) -> SolveOutcome:
    """Solve a single file with the given solver (falling back to ``backup_solver`` on
    failure) and, on success, persist the WCS solution to the database.

    ``cancelled`` is asked while solving. Once it returns True the running solver is stopped,
    no backup is started, and the outcome has ``cancelled`` set. A solver that returns a
    solution despite the cancel (it finished before noticing) has that solution stored: it was
    expensive to get. A solution not yet fetched when the cancel is noticed is dropped, e.g. an
    Astrometry.net job that finished during the wait before the next poll.

    Returns a :class:`SolveOutcome` describing the result; never raises for solver
    failures (``SolverError``/``SolverFailure``) or unexpected errors -- those are
    captured in ``SolveOutcome.error``.
    """
    cancelled = cancelled or (lambda: False)
    primary_name = _solver_name(solver)
    backup_name = _solver_name(backup_solver) if backup_solver else None
    try:
        if cancelled():
            raise SolverCancelled("cancelled before starting")
        try:
            with solver:
                solution: Header = solver.solve(
                    Path(file.full_filename()), file.image, hint,
                    output_callback=output_callback, file_wcs=file_wcs, cancelled=cancelled,
                )
            used_solver = solver
        except (SolverFailure, SolverError) as primary_error:
            if not backup_solver:
                raise
            if cancelled():
                raise SolverCancelled(f"{primary_name} failed, backup not started") from primary_error
            if output_callback:
                output_callback(f"  → {primary_name} failed ({primary_error}), trying {backup_name}…")
            with backup_solver:
                solution = backup_solver.solve(
                    Path(file.full_filename()), file.image, hint,
                    output_callback=output_callback, file_wcs=file_wcs, cancelled=cancelled,
                )
            used_solver = backup_solver

        stamp_wcs_origin(solution, used_solver.wcs_origin)

        # Everything that can fail on the solution comes before the first write, so that a
        # failure keeps whatever solution the file already had.
        ra, dec, healpix, radius = get_image_center_coords(solution)
        pixel_scales = proj_plane_pixel_scales(WCS(solution))  # degrees/pixel
        scale_arcsec = round(float(pixel_scales[0]) * 3600, 3)
        image_values = dict(coord_ra=ra, coord_dec=dec, coord_pix256=healpix, coord_radius=radius)
        width = height = None
        naxis1 = solution.get('NAXIS1')
        naxis2 = solution.get('NAXIS2')
        if naxis1 and naxis2:
            width, height = int(naxis1), int(naxis2)
            image_values.update(width=width, height=height)

        new_file_wcs = FileWCS(file=file, wcs=compress(solution.tostring().encode()))
        with FileWCS._meta.database.atomic():
            FileWCS.insert(new_file_wcs.__data__).on_conflict_replace().execute()
            images_updated = Image.update(**image_values).where(Image.file == file).execute()

        file.has_wcs = True
        if file.image:
            for name, value in image_values.items():
                setattr(file.image, name, value)

        warning = None
        if not images_updated:
            # The solution is still worth keeping, it was expensive to get.
            warning = (f"{file.name} has no image record, so its coordinates were not stored "
                       f"and sky searches won't find it.")
            logger.warning("Solved %s without an image record, coordinates not stored", file.name)
            if output_callback:
                output_callback(f"  ! {warning}")

        logger.info("Solved %s: RA=%.4f°  Dec=%.4f°  scale=%.2f\"/px", file.name, ra, dec, scale_arcsec)
        return SolveOutcome(success=True, ra=ra, dec=dec, radius=radius, scale_arcsec=scale_arcsec,
                             width=width, height=height, used_solver=_solver_name(used_solver),
                             warning=warning)
    except SolverCancelled as stopped:
        logger.info("Solving %s cancelled: %s", file.name, stopped)
        return SolveOutcome(success=False, cancelled=True)
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

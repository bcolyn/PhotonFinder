"""Shared plate-solve-and-persist logic used by both the Qt UI (``PlateSolveTask`` in
``ui/BackgroundLoader.py``) and the MCP server (``mcp_server.py``), which is hosted in
this same process -- the two are kept apart by ``ApplicationContext.library_write_lock``,
which both take for the whole run.

Handles primary/backup solver fallback for a single file and, on success, writes the
resulting WCS solution through ``plate_solution.store``.
Callers that need per-file progress reporting or camera-scale-cache retries (the GUI's
``PlateSolveTask``) still wrap this function with that extra logic themselves.
"""
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from astropy.io.fits import Header

from photonfinder import plate_solution, solvers
from photonfinder.models import File, FileWCS
from photonfinder.platesolver import SolverBase, SolverHint, SolverError, SolverFailure, SolverCancelled

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
    primary_name = solvers.label_of(solver)
    backup_name = solvers.label_of(backup_solver) if backup_solver else None
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

        stored = plate_solution.store(file, solution, used_solver.wcs_origin)

        warning = None
        if not stored.image_updated:
            # The solution is still worth keeping, it was expensive to get.
            warning = (f"{file.name} has no image record, so its coordinates were not stored "
                       f"and sky searches won't find it.")
            logger.warning("Solved %s without an image record, coordinates not stored", file.name)
            if output_callback:
                output_callback(f"  ! {warning}")

        logger.info("Solved %s: RA=%.4f°  Dec=%.4f°  scale=%.2f\"/px",
                    file.name, stored.ra, stored.dec, stored.scale_arcsec)
        return SolveOutcome(success=True, ra=stored.ra, dec=stored.dec, radius=stored.radius,
                             scale_arcsec=stored.scale_arcsec, width=stored.width, height=stored.height,
                             used_solver=solvers.label_of(used_solver), warning=warning)
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

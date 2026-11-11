"""Solve run: plate-solving a batch of files, for both the Qt UI (``PlateSolveTask`` in
``ui/BackgroundLoader.py``) and the MCP server (``mcp_server.py``), which is hosted in this
same process -- the two are kept apart by ``ApplicationContext.library_write_lock``, which
both take through ``try_hold_library`` for the whole run.

``SolveRun`` solves each file with the primary solver, falling back to the backup, and when
asked retries a failed file with the pixel scales earlier solves found for its camera
(``CameraScales``), learning the scales of new solves as it goes. Solutions are stored through
``plate_solution.store``. No Qt here: the GUI task and the MCP tool adapt the per-file
outcomes to signals and to dicts.
"""
import logging
import math
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from astropy.io.fits import Header

from photonfinder import plate_solution, solvers
from photonfinder.core import library_busy_message
from peewee import chunked

from photonfinder.models import File, FileWCS, Image, LibraryRoot, group_key_sql
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
    image = image_of(file)
    primary_name = solvers.label_of(solver)
    backup_name = solvers.label_of(backup_solver) if backup_solver else None
    try:
        if cancelled():
            raise SolverCancelled("cancelled before starting")
        try:
            with solver:
                solution: Header = solver.solve(
                    Path(file.full_filename()), image, hint,
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
                    Path(file.full_filename()), image, hint,
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


def image_of(file: File) -> Optional[Image]:
    """The Image a search row carries (as `file.image`), else the file's first image -- the
    one a plate solve solves. Files picked outside a search, e.g. the image viewer's, carry
    none."""
    image = file.__dict__.get('image')
    return image if isinstance(image, Image) else Image.first_of(file)


# --- the library lock --------------------------------------------------------------------------

class SolveLockBusy(Exception):
    """Another job holds the library; the message says which."""


def try_hold_library(context, holder: str) -> Optional[str]:
    """Take the library write lock for a solve run. None when taken; otherwise why not."""
    lock = context.library_write_lock
    if lock.acquire(blocking=False, holder=holder):
        return None
    return f"Plate solving not started: {library_busy_message(lock.busy_with() or 'Another job')}"


@contextmanager
def holding_library(context, holder: str):
    """The library, as a writer and with its write lock, for the length of a solve run that
    starts and ends on one thread (the MCP tool). Raises core.LibraryBusyError when a library
    switch is under way and SolveLockBusy when another job holds the lock."""
    context.writers.begin(holder)
    try:
        refused = try_hold_library(context, holder)
        if refused:
            raise SolveLockBusy(refused)
        try:
            yield
        finally:
            context.library_write_lock.release()
    finally:
        context.writers.end(holder)


# --- camera scales -----------------------------------------------------------------------------

def otsu_threshold(counts: list[int]) -> float:
    """Return the Otsu optimal split threshold for a list of counts.

    Tries every midpoint between adjacent unique count values, picks the one
    that maximises between-class variance.  Returns 0 (keep all) when there
    are fewer than two distinct values or the two classes are too similar
    (lower-class mean >= 25 % of upper-class mean: both are legitimate setups).
    """
    unique = sorted(set(counts))
    if len(unique) < 2:
        return 0
    best_var, best_t = -1.0, 0.0
    n = len(counts)
    for i in range(1, len(unique)):
        t = (unique[i - 1] + unique[i]) / 2.0
        below = [c for c in counts if c <= t]
        above = [c for c in counts if c > t]
        if not below or not above:
            continue
        w0, w1 = len(below) / n, len(above) / n
        m0, m1 = sum(below) / len(below), sum(above) / len(above)
        if m0 >= 0.25 * m1:         # classes too similar — not a real outlier (< 4:1 ratio)
            continue
        var = w0 * w1 * (m0 - m1) ** 2
        if var > best_var:
            best_var, best_t = var, t
    return best_t


def dedupe_scales(scale_counts: dict[float, int]) -> list[float]:
    """Return representative scales, removing near-duplicates and rare outliers.

    Steps:
    1. Sort scales and merge those within a 30% ratio band, accumulating their
       solve counts and keeping the value with the highest individual count.
    2. Apply an Otsu threshold on the per-band totals to drop bands whose
       accumulated count is clearly in the noise tier.
    """
    if not scale_counts:
        return []

    # Step 1 — 30% band merge
    bands: list[tuple[float, int, int]] = []   # (representative_scale, rep_count, band_total)
    for scale in sorted(scale_counts):
        cnt = scale_counts[scale]
        if not bands or scale / bands[-1][0] >= 1.30:
            bands.append((scale, cnt, cnt))
        else:
            rep_s, rep_c, total = bands[-1]
            winner, winner_c = (scale, cnt) if cnt > rep_c else (rep_s, rep_c)
            bands[-1] = (winner, winner_c, total + cnt)

    # Step 2 — Otsu on band totals, drop noise tier
    if len(bands) >= 2:
        totals = [total for _, _, total in bands]
        threshold = otsu_threshold(totals)
        if threshold > 0:
            filtered = [(s, rc, t) for s, rc, t in bands if t > threshold]
            if filtered:
                bands = filtered

    return [s for s, _, _ in bands]


class CameraScales:
    """Pixel scales (arcsec/px) that earlier solves found, per camera: the scales to retry a
    failed solve with. Scales within 30% of each other are one optical setup and rare ones are
    dropped (dedupe_scales)."""

    def __init__(self, by_camera: Optional[dict] = None):
        self.by_camera: dict[str, list[float]] = by_camera if by_camera is not None else {}

    @classmethod
    def load(cls) -> 'CameraScales':
        """The scales of the solved images in the library (one query); empty if that fails."""
        from peewee import fn
        counts: dict[str, dict[float, int]] = {}  # camera -> {scale: count}
        try:
            rows = (Image
                    .select(Image.camera, Image.coord_scale, fn.COUNT(Image.rowid).alias('cnt'))
                    .where(Image.camera.is_null(False) & Image.coord_scale.is_null(False))
                    .group_by(Image.camera, Image.coord_scale))
            for img in rows:
                counts.setdefault(img.camera, {})[img.coord_scale] = img.cnt
        except Exception:
            logger.exception("Failed to load camera scale cache")
            return cls()
        return cls({camera: dedupe_scales(c) for camera, c in counts.items()})

    def for_camera(self, camera: Optional[str]) -> list[float]:
        return list(self.by_camera.get(camera, [])) if camera else []

    def learn(self, camera: Optional[str], outcome: SolveOutcome) -> None:
        """Remember the scale of a new solve, worked out as the library's scale column is."""
        if camera and outcome.radius and outcome.width and outcome.height:
            scale = round((outcome.radius * 2 * 3600) / math.hypot(outcome.width, outcome.height), 2)
            scales = self.by_camera.setdefault(camera, [])
            if scale not in scales:
                scales.append(scale)


# --- the run -----------------------------------------------------------------------------------

class SolveRun:
    """Solves files one at a time: primary solver, then backup; then, with `camera_scales`,
    each scale earlier solves found for the file's camera in turn, until one solves. Solving a
    file teaches `camera_scales` its scale. `output` gets the progress lines, `cancelled` is
    asked between and during solves."""

    def __init__(self, solver: SolverBase, backup_solver: Optional[SolverBase] = None,
                 hint: Optional[SolverHint] = None, camera_scales: Optional[CameraScales] = None,
                 output: Optional[Callable[[str], None]] = None,
                 cancelled: Optional[Callable[[], bool]] = None):
        self.solver = solver
        self.backup_solver = backup_solver
        self.hint = hint
        self.camera_scales = camera_scales
        self.output = output or (lambda line: None)
        self.cancelled = cancelled or (lambda: False)

    def describe(self) -> str:
        """One line naming the solvers and hints, to head the run's log."""
        line = f"Solver: {solvers.label_of(self.solver)}"
        if self.backup_solver:
            line += f"  |  Backup: {solvers.label_of(self.backup_solver)}"
        if self.hint:
            parts = []
            if self.hint.ra is not None:
                parts.append(f"RA {self.hint.ra:.3f}°")
            if self.hint.dec is not None:
                parts.append(f"Dec {self.hint.dec:.3f}°")
            if self.hint.scale is not None:
                parts.append(f"scale {self.hint.scale:.3f} arcsec/px")
            if parts:
                line += f"  |  Hints ({self.hint.mode}): {', '.join(parts)}"
        return line

    def run(self, files: Iterable[File]) -> Iterator[tuple[File, SolveOutcome]]:
        """Each file with its outcome, in order; stops before the next file once cancelled.
        A file without its Image loaded (as `file.image`) uses its first image."""
        remaining = iter(files)
        while not self.cancelled():  # asked before the next file is even taken
            file = next(remaining, None)
            if file is None:
                return
            yield file, self.solve(file)

    def solve(self, file: File) -> SolveOutcome:
        file_wcs = FileWCS.get_or_none(FileWCS.file == file)
        image = image_of(file)
        camera = image.camera if image is not None else None
        hints = self._hints_for(camera)
        outcome = None
        for i, hint in enumerate(hints):
            outcome = solve_file(file, self.solver, self.backup_solver, hint=hint, file_wcs=file_wcs,
                                 output_callback=lambda line: self.output(f"    {line}"),
                                 cancelled=self.cancelled)
            if outcome.success or outcome.cancelled or i == len(hints) - 1:
                break
            scale = hint.scale if hint else None
            scale_str = f"{scale:.2f}\"/px" if scale else "no scale"
            self.output(f"  → {scale_str} failed, trying next cached scale…")
        if outcome.success and self.camera_scales is not None:
            self.camera_scales.learn(camera, outcome)
        return outcome

    def _hints_for(self, camera: Optional[str]) -> list:
        """The hint asked for, then one per cached scale for the camera not already tried."""
        hints = [self.hint]
        if self.camera_scales is None:
            return hints
        tried = self.hint.scale if self.hint else None
        for scale in self.camera_scales.for_camera(camera):
            if scale != tried:
                hints.append(SolverHint(ra=self.hint.ra if self.hint else None,
                                        dec=self.hint.dec if self.hint else None,
                                        scale=scale, mode='fallback'))
        return hints


# --- one frame per group ---------------------------------------------------------------------

MAX_GROUP_TRIES = 3  # frames of a group tried before it is reported as failed


@dataclass
class SolveGroup:
    """The unsolved frames of one group (models.group_key_sql), earliest first, and how far
    solving it has got."""
    key: tuple
    candidates: list
    failed: list = field(default_factory=list)  # frames tried that didn't solve
    solved: bool = False

    def next_frame(self) -> Optional[File]:
        """The frame to try next; None once solved, out of frames or tried MAX_GROUP_TRIES."""
        if self.solved or len(self.failed) >= min(MAX_GROUP_TRIES, len(self.candidates)):
            return None
        return self.candidates[len(self.failed)]

    @property
    def gave_up(self) -> bool:
        return not self.solved and bool(self.failed) and self.next_frame() is None

    def describe(self) -> str:
        image = self.candidates[0].image
        if self.key[-1] is not None:  # a picture, a group of its own (models.GROUP_KEY)
            return self.candidates[0].name
        night = self.key[-2]
        parts = [image.object_name or image.image_type or "no object", image.filter,
                 f"{image.exposure:g} s" if image.exposure is not None else None,
                 f"night {night.isoformat()}" if night else None]
        return ", ".join(part for part in parts if part)


class SolvePlan:
    """Plate solving one frame per group instead of every frame: dithered subs of one target on
    one night differ by arcseconds, and the catalog report counts the rest of a group by the
    solved one. Each group's earliest unsolved frame is solved; a failed group moves on to its
    next frame (Retry Failed), up to MAX_GROUP_TRIES. A group with a frame solved already, in
    the whole library, is skipped. The other frames stay unsolved.

    `files` are the frames to choose from (a plate-solve run's own: a selection, or the search's
    unsolved lights), each with its `image` loaded. Queries the library, so build it where the
    database may be used.
    """

    def __init__(self, files: list[File]):
        self.total_files = len(files)
        keys = _group_keys(files)
        solved = _solved_group_keys()
        by_key: dict[tuple, list] = {}
        for file in sorted(files, key=_group_order):
            key = keys.get((file.rowid, file.image.hdu))
            if key is not None:
                by_key.setdefault(key, []).append(file)
        self.groups = [SolveGroup(key, frames) for key, frames in by_key.items() if key not in solved]
        self.skipped = [SolveGroup(key, frames) for key, frames in by_key.items() if key in solved]
        self._group_of = {file.rowid: group for group in self.groups for file in group.candidates}

    def next_round(self) -> list[File]:
        """The next frame of every group not solved yet: its first, or after a failure (or a
        cancel before it was reached) the next one."""
        return [frame for group in self.groups if (frame := group.next_frame()) is not None]

    def record(self, file: File, success: bool):
        group = self._group_of.get(file.rowid)
        if group is None:
            return
        if success:
            group.solved = True
        elif file not in group.failed:
            group.failed.append(file)

    def failed_groups(self) -> list[SolveGroup]:
        """Groups that tried MAX_GROUP_TRIES frames (or all they had) without a solve."""
        return [group for group in self.groups if group.gave_up]

    def describe(self) -> str:
        line = (f"One frame per group of similar frames: {len(self.groups)} groups to solve "
                f"from {self.total_files} files")
        if self.skipped:
            line += f"; {len(self.skipped)} skipped, already solved: " +                     "; ".join(group.describe() for group in self.skipped[:5]) +                     (" ..." if len(self.skipped) > 5 else "")
        return line


def _group_order(file: File):
    """As models.group_order_sql: the earliest date_obs first, frames without one last."""
    image = file.image
    return image.date_obs is None, image.date_obs or 0, file.rowid, image.hdu


def _group_keys(files: list[File]) -> dict[tuple, tuple]:
    """(file rowid, hdu) -> the group key of that image, as SQL computes it."""
    keys = {}
    for batch in chunked([f.rowid for f in files], 500):
        query = (File.select(File.rowid, Image.hdu, *group_key_sql())
                 .join_from(File, Image).join_from(File, LibraryRoot)
                 .where(File.rowid.in_(batch)))
        for rowid, hdu, *key in query.tuples():
            keys[(rowid, hdu)] = tuple(key)
    return keys


def _solved_group_keys() -> set[tuple]:
    """The groups with a plate-solved frame, anywhere in the library."""
    query = (File.select(*group_key_sql())
             .join_from(File, Image).join_from(File, FileWCS)
             .distinct())
    return {tuple(row) for row in query.tuples()}

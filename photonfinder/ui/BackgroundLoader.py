import copy
import logging
import math
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Signal, QObject, QThreadPool, QRunnable, Slot
from PySide6.QtWidgets import QWidget

from photonfinder.core import ApplicationContext, library_busy_message
from photonfinder.models import File, Image, LibraryRoot, SearchCriteria, FileWCS, \
    Project, ImageStats
from photonfinder import library_query
from photonfinder.library_query import search_files
from photonfinder.image_analysis import analyze_file, ImageAnalysisResult, CALIBRATION_TYPES
from photonfinder.filesystem import reindex_library
from photonfinder import solve_service, solvers
from photonfinder.platesolver import SolverBase, SolverHint

logger = logging.getLogger(__name__)


class BackgroundLoaderBase(QObject):
    """Base class for asynchronous loading of data in background threads."""

    def __init__(self, context: ApplicationContext):
        super().__init__()
        self.thread_pool = QThreadPool.globalInstance()
        self.context = context

    def run_in_thread(self, fn, *args, **kwargs):
        """Run a function in a background thread."""
        runnable = self._create_runnable(fn, *args, **kwargs)
        self.thread_pool.start(runnable)

    def run_writer_in_thread(self, name: str, fn, *args, **kwargs):
        """Run a job that writes to the library, which can't be switched until the job ends."""
        self.context.writers.begin(name)

        def run():
            try:
                fn(*args, **kwargs)
            finally:
                self.context.writers.end(name)

        self.run_in_thread(run)

    def _create_runnable(self, fn, *args, **kwargs):
        """Create a QRunnable that will execute the given function."""

        class WorkerRunnable(QRunnable):
            @Slot()
            def run(self_runnable):  # pylint: disable=no-self-argument  # self is the enclosing BackgroundLoaderBase
                try:
                    if self.context.database is None:  # mid-switch or shutting down; nothing wants the result
                        logging.info("Skipping a background job: no library is open")
                        return
                    fn(*args, **kwargs)
                except Exception as e:
                    logging.error(f"Error in worker thread: {e}")

        return WorkerRunnable()


class GenericControlLoader(BackgroundLoaderBase):
    """Generic background loader for arbitrary functions.

    Each run_tasks call supersedes the previous one: results of an older run that complete
    late are dropped, so a slow refresh can't overwrite the options of a newer one."""
    data_ready = Signal(QWidget, list)
    _task_done = Signal(QWidget, list, int)  # widget, result, generation; worker thread -> GUI thread

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self._generation = 0
        self._task_done.connect(self._on_task_done)

    def run_tasks(self, tasks: List[tuple[QWidget, Callable]], search_criteria):
        self._generation += 1
        self.run_in_thread(self._run_tasks, tasks, copy.deepcopy(search_criteria), self._generation)

    def _run_tasks(self, tasks: List[tuple[QWidget, Callable]], search_criteria: SearchCriteria, generation: int):
        for widget, task in tasks:
            if generation != self._generation:
                return  # superseded; the newer run will load these
            try:
                result = task(search_criteria)
                self._task_done.emit(widget, result, generation)
            except Exception as e:
                logging.error(f"Error loading data for control {widget.objectName()}: {e}", exc_info=True)

    @Slot(QWidget, list, int)
    def _on_task_done(self, widget, result, generation):
        if generation == self._generation:
            self.data_ready.emit(widget, result)


class ProjectsLoader(BackgroundLoaderBase):
    """Helper class for asynchronous loading of projects with image data."""

    projects_loaded = Signal(list)

    def reload_projects(self):
        self.run_in_thread(self._reload_projects_task)

    def _reload_projects_task(self):
        try:
            projects = Project.list_projects_with_image_data()

            projects_with_coords = [p for p in projects
                                    if hasattr(p, 'image') and p.image
                                    and p.image.coord_ra is not None and p.image.coord_dec is not None]
            if projects_with_coords:
                from astropy.coordinates import SkyCoord, get_constellation
                import astropy.units as u
                coords = SkyCoord(
                    [p.image.coord_ra for p in projects_with_coords],
                    [p.image.coord_dec for p in projects_with_coords],
                    unit=u.deg, frame='icrs',
                )
                constellations = get_constellation(coords)
                for p, c in zip(projects_with_coords, constellations):
                    p._constellation = str(c)
            for p in projects:
                if not hasattr(p, '_constellation'):
                    p._constellation = ""

            self.projects_loaded.emit(projects)
        except Exception as e:
            logging.error(f"Error loading projects: {e}")


class LibraryRootsLoader(BackgroundLoaderBase):
    """Helper class for asynchronous loading of library roots from the database."""

    # Signal emitted when library roots are loaded
    library_roots_loaded = Signal(list)

    def reload_library_roots(self):
        """Load all library roots from the database."""
        self.run_in_thread(self._reload_library_roots_task)

    def _reload_library_roots_task(self):
        """Background task to load library roots."""
        try:
            # Fetch library roots from database
            library_roots = list(LibraryRoot.select().order_by(LibraryRoot.name))

            # Emit signal with the results
            self.library_roots_loaded.emit(library_roots)
        except Exception as e:
            logging.error(f"Error loading library roots: {e}")


class FilePathsLoader(BackgroundLoaderBase):
    """Helper class for asynchronous loading of file paths for a library root."""

    # Signal emitted when paths for a library root are loaded
    paths_loaded = Signal(object, list)  # library_root, paths

    def load_paths_for_library(self, library_root: LibraryRoot):
        """Load all paths for a given library root from the database."""
        self.run_in_thread(self._load_paths_for_library_task, library_root)

    def _load_paths_for_library_task(self, library_root: LibraryRoot):
        """Background task to load paths for a library."""
        try:
            # Fetch distinct paths for this library root
            query = (File
                     .select(File.path)
                     .where(File.root == library_root)
                     .distinct())

            paths = list(map(lambda file: file.path, query))

            # Emit signal with the results
            self.paths_loaded.emit(library_root, paths)
        except Exception as e:
            logging.error(f"Error loading paths for library {library_root.name}: {e}")


class SearchResultsLoader(BackgroundLoaderBase):
    """Helper class for asynchronous loading of search results from the database.

    Every search works on its own snapshot of the criteria, so the UI can keep editing its
    criteria object while a query runs. A new search supersedes the previous one: pages that
    arrive late from a superseded search are dropped instead of being shown.
    """

    # Signal emitted when search results are loaded
    results_loaded = Signal(list, int, int, bool)  # results, page, total, has_more
    # worker thread -> GUI thread: results, page, total, has_more, request id
    _page_ready = Signal(list, int, int, bool, int)

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self.page_size = 100
        self.current_page = 0
        self.total_results = 0
        self.last_criteria: Optional[SearchCriteria] = None  # snapshot the current search runs on
        self._request_id = 0
        self._in_flight: set[tuple[int, int]] = set()  # (request id, page) not yet delivered
        self._page_ready.connect(self._on_page_ready)

    def search(self, search_criteria, page=0):
        """Start a search with the given criteria. Page 0 starts a new search, superseding any
        running one; other pages continue the current search."""
        if page == 0:
            if (search_criteria == self.last_criteria
                    and (self._request_id, 0) in self._in_flight):
                return  # the same query is already running
            self._request_id += 1
            self.last_criteria = copy.deepcopy(search_criteria)
        elif (self._request_id, page) in self._in_flight:
            return
        self.current_page = page
        self._in_flight.add((self._request_id, page))
        self.run_in_thread(self._search_task, self.last_criteria, page, self._request_id)

    def load_more(self):
        """Load the next page of results using the last search criteria."""
        if self.last_criteria is not None:
            self.search(self.last_criteria, self.current_page + 1)

    def is_running(self) -> bool:
        return bool(self._in_flight)

    def _search_task(self, search_criteria, page, request_id):
        """Background task to search for files matching the criteria."""
        try:
            results, total, has_more = search_files(search_criteria, page, self.page_size)
        except Exception as e:
            logging.error(f"Error searching files: {e}", exc_info=True)
            results, total, has_more = [], 0, False
        self._page_ready.emit(results, page, total, has_more, request_id)

    @Slot(list, int, int, bool, int)
    def _on_page_ready(self, results, page, total, has_more, request_id):
        self._in_flight.discard((request_id, page))
        if request_id != self._request_id:
            return  # a newer search has started since; its own pages will follow
        if page == 0 or total:
            self.total_results = total
        self.results_loaded.emit(results, page, self.total_results, has_more)


class ImageReindexWorker(BackgroundLoaderBase):
    """Worker class for reindexing image metadata."""
    WRITER_NAME = "Rebuild index"
    finished = Signal()

    def reindex_images(self) -> str | None:
        """Start the reindexing process in a background thread, holding the library write lock
        until it ends. Returns why it didn't start, or None if it did."""
        lock = self.context.library_write_lock
        if not lock.acquire(blocking=False, holder=self.WRITER_NAME):
            return library_busy_message(lock.busy_with() or "Another job")
        try:
            self.run_writer_in_thread(self.WRITER_NAME, self._reindex_images_task)
        except Exception:
            lock.release()
            raise
        return None

    def _reindex_images_task(self):
        """Run Rebuild index (filesystem.reindex_library), then release the library write lock."""
        status = self.context.status_reporter
        try:
            reindex_library(status, self.context.settings)
        except Exception as e:
            logging.error(f"Error during reindexing: {e}", exc_info=True)
            status.update_status(f"Error during reindexing: {e}")
        finally:
            self.context.library_write_lock.release()

        # Signal that we're done
        self.finished.emit()


class ProgressBackgroundTask(BackgroundLoaderBase):
    progress = Signal(int)
    finished = Signal()
    error = Signal(str)
    message = Signal(str)
    total_found = Signal(int)

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self.total = 0
        self.context = context
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FileProcessingTask(ProgressBackgroundTask):
    writer_name: str | None = None  # set by tasks that write to the library

    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File]):
        super().__init__(context)
        self.search_criteria = search_criteria
        self.files = files

    def start(self):
        if self.writer_name:
            self.run_writer_in_thread(self.writer_name, self._process_files)
        else:
            self.run_in_thread(self._process_files)

    def _process_files(self):
        try:
            if self.files is not None and len(self.files) > 0:
                self.total = len(self.files)
                self.total_found.emit(self.total)
                for i, file in enumerate(self.files):
                    if self.cancelled:
                        break
                    self._process_file(file, i)
                    self.progress.emit(i + 1)  # files done, so the last one fills the bar
            else:
                with self.context.database.bind_ctx([File, Image]):
                    files = self.load_files()
                    self.total = len(files)
                    self.total_found.emit(self.total)
                    for i, file in enumerate(files):
                        if self.cancelled:
                            break
                        self._process_file(file, i)
                        self.progress.emit(i + 1)  # files done, so the last one fills the bar

            self.finished.emit()
        except Exception as e:
            logging.error(f"Error processing files: {e}", exc_info=True)
            self.error.emit(str(e))

    def load_files(self) -> List[File]:
        """The files to process when none were selected: those matching the search criteria."""
        return library_query.matching_files(self.search_criteria)

    def _process_file(self, file, index):
        """Process one file. The loop reports progress once it returns."""

    def _open_output(self, path: str, encoding: str, newline: str | None = None):
        """Open the task's output file for writing, or emit error and return None. Tasks open it
        before the processing loop's own error handling, so a failure must be reported here."""
        try:
            return open(path, 'w', encoding=encoding, newline=newline)  # pylint: disable=consider-using-with  # caller closes it
        except OSError as e:
            logging.error(f"Could not open {path} for writing: {e}")
            self.error.emit(f"Could not write {path}:\n{e}")
            return None


class PlateSolveTask(FileProcessingTask):
    writer_name = "Plate solving"

    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File],
                 solver: SolverBase, backup_solver: SolverBase = None, hint: SolverHint = None,
                 camera_scale_cache: dict = None, use_camera_scales: bool = False):
        super().__init__(context, search_criteria, files)
        self.solved_files = list()
        self.file_results = list()  # list of (file, success: bool, error_msg: str | None)
        self.first_solution: tuple | None = None  # (ra, dec, scale_arcsec) of first successful solve
        self.solver = solver
        self.backup_solver = backup_solver
        self.hint = hint
        self.camera_scale_cache = camera_scale_cache if camera_scale_cache is not None else {}
        self.use_camera_scales = use_camera_scales

    def _process_files(self):
        primary_name = solvers.label_of(self.solver)
        backup_name = solvers.label_of(self.backup_solver) if self.backup_solver else None
        header = f"Solver: {primary_name}"
        if backup_name:
            header += f"  |  Backup: {backup_name}"
        if self.hint:
            parts = []
            if self.hint.ra is not None:
                parts.append(f"RA {self.hint.ra:.3f}°")
            if self.hint.dec is not None:
                parts.append(f"Dec {self.hint.dec:.3f}°")
            if self.hint.scale is not None:
                parts.append(f"scale {self.hint.scale:.3f} arcsec/px")
            if parts:
                header += f"  |  Hints ({self.hint.mode}): {', '.join(parts)}"
        self.message.emit(header)
        self.message.emit("")
        try:
            if self.files is not None and len(self.files) > 0:
                self.total = len(self.files)
                self.total_found.emit(self.total)
                for i, file in enumerate(self.files):
                    if self.cancelled:
                        break
                    self._process_file(file, i)
                    self.progress.emit(i + 1)  # files done, so the last one fills the bar
            else:
                with self.context.database.bind_ctx([File, Image]):
                    files = self.load_files()
                    self.total = len(files)
                    self.total_found.emit(self.total)
                    for i, file in enumerate(files):
                        if self.cancelled:
                            break
                        self._process_file(file, i)
                        self.progress.emit(i + 1)  # files done, so the last one fills the bar
            solved = len(self.solved_files)
            self.message.emit("")
            if self.total == 0:
                self.message.emit("No files to solve.")
            elif solved == self.total:
                self.message.emit(f"Done: all {solved} file(s) solved.")
            else:
                self.message.emit(f"Done: {solved}/{self.total} file(s) solved.")
            self.finished.emit()
        except Exception as e:
            logging.error(f"Error processing files: {e}", exc_info=True)
            self.error.emit(str(e))

    def load_files(self) -> List[File]:
        return library_query.unsolved_lights(self.search_criteria)

    def _process_file(self, file, index):
        super()._process_file(file, index)
        self.message.emit(f"Processing file {index + 1}/{self.total}:\n {file.full_filename()}")

        file_wcs = FileWCS.get_or_none(FileWCS.file == file)

        # Build list of hints to try: original hint first, then cached scales for this camera
        hints_to_try = [self.hint]
        if self.use_camera_scales and file.image:
            camera = getattr(file.image, 'camera', None)
            if camera:
                tried_scale = self.hint.scale if self.hint else None
                for s in self.camera_scale_cache.get(camera, []):
                    if s != tried_scale:
                        hints_to_try.append(SolverHint(
                            ra=self.hint.ra if self.hint else None,
                            dec=self.hint.dec if self.hint else None,
                            scale=s,
                            mode='fallback',
                        ))

        outcome = None
        for i, attempt_hint in enumerate(hints_to_try):
            outcome = solve_service.solve_file(
                file, self.solver, self.backup_solver, hint=attempt_hint, file_wcs=file_wcs,
                output_callback=lambda line: self.message.emit(f"    {line}"),
                cancelled=lambda: self.cancelled,
            )
            if outcome.success or outcome.cancelled or i == len(hints_to_try) - 1:
                break
            cached_scale = attempt_hint.scale if attempt_hint else None
            scale_str = f"{cached_scale:.2f}\"/px" if cached_scale else "no scale"
            self.message.emit(f"  → {scale_str} failed, trying next cached scale…")

        if outcome.success:
            self.context.status_reporter.update_status(f"Solved file {file.full_filename()}")
            self.solved_files.append(file)
            self.file_results.append((file, True, None))

            camera = getattr(file.image, 'camera', None) if file.image else None
            if self.use_camera_scales and camera and outcome.radius and outcome.width and outcome.height:
                diag_px = math.hypot(outcome.width, outcome.height)
                new_scale = round((outcome.radius * 2 * 3600) / diag_px, 2)
                scales = self.camera_scale_cache.setdefault(camera, [])
                if new_scale not in scales:
                    scales.append(new_scale)

            if self.first_solution is None:
                self.first_solution = (outcome.ra, outcome.dec, outcome.scale_arcsec)
            self.message.emit(f"  ✓ Solved: RA {outcome.ra:.4f}°  Dec {outcome.dec:.4f}°")
        elif outcome.cancelled:
            self.message.emit("  – Cancelled")
        else:
            self.file_results.append((file, False, outcome.error))
            self.message.emit(f"  ✗ {outcome.error}")



class FileListTask(FileProcessingTask):
    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File]):
        super().__init__(context, search_criteria, files)
        self.output_filename = None
        self.fd = None

    def start(self, output_filename: str = None):
        self.output_filename = output_filename
        super().start()

    def _process_files(self):
        if self.output_filename is None:
            return
        fd = self._open_output(self.output_filename, encoding='utf-8')
        if fd is None:
            return
        with fd:
            self.fd = fd
            super()._process_files()

    def _process_file(self, file, index):
        self.message.emit(f"Processing file {index + 1}/{self.total}:\n {file.full_filename()}")
        self.fd.write(f"{str(Path(file.full_filename()))}\n")


class ImageAnalysisTask(FileProcessingTask):
    """Per-file image quality analysis. Stores results in ImageStats (upsert).

    Holds the library write lock from start until the work has really stopped, which only
    the task knows: cancelling closes the progress dialog while the current file is still
    being analysed and written.
    """
    writer_name = "Image analysis"
    ended = Signal()  # the lock has been released, whether finished, cancelled, failed or refused

    def __init__(self, context: ApplicationContext,
                 search_criteria: SearchCriteria, files: List[File]):
        super().__init__(context, search_criteria, files)
        self.analyzed_files: list[tuple[File, ImageAnalysisResult]] = []

    def start(self) -> bool:
        """Start unless another writer holds the library. When refused, emits error with the
        reason and then ended, and returns False."""
        lock = self.context.library_write_lock
        if not lock.acquire(blocking=False, holder=self.writer_name):
            self.error.emit(f"Not started: {library_busy_message(lock.busy_with() or 'Another job')}")
            self.ended.emit()
            return False
        try:
            super().start()
        except Exception:
            lock.release()
            self.ended.emit()
            raise
        return True

    def _process_files(self):
        try:
            self._analyse()
        finally:
            self.context.library_write_lock.release()
            self.ended.emit()

    def _analyse(self):
        super()._process_files()

    def _process_file(self, file: File, index: int):
        super()._process_file(file, index)
        self.message.emit(f"Analysing {index + 1}/{self.total}: {file.name}")
        try:
            image_type = file.image.image_type if hasattr(file, 'image') and file.image else None
            detect_sources = image_type not in CALIBRATION_TYPES
            result = analyze_file(file.full_filename(), detect_sources=detect_sources)
            if result.error:
                self.message.emit(f"  Warning: {result.error}")
                # Stored stats survive a failed read. A frame with none gets an empty row, which
                # the quality diagnosis counts as "could not be read".
                ImageStats.insert(file=file).on_conflict_ignore().execute()
                return
            (ImageStats
             .insert(
                 file=file,
                 background_median=result.background_median,
                 background_rms=result.background_rms,
                 star_count=result.star_count,
                 fwhm_median=result.fwhm_median,
                 elongation_median=result.elongation_median,
             )
             .on_conflict_replace()
             .execute())
            self.analyzed_files.append((file, result))
        except Exception as exc:
            self.message.emit(f"  Error: {file.name}: {exc}")
            logger.error("ImageAnalysisTask error on %s", file.name, exc_info=True)


class MissingFramesAnalysisTask(ImageAnalysisTask):
    """Analyses the given files, which it loads by id, while holding the library write lock."""

    def __init__(self, context: ApplicationContext, file_ids: List[int]):
        super().__init__(context, SearchCriteria(), [])
        self.file_ids = file_ids

    def _analyse(self):
        try:
            self.files = library_query.files_by_id(self.file_ids)
        except Exception as e:
            logging.error(f"Error loading files to analyse: {e}", exc_info=True)
            self.error.emit(str(e))
            return
        if self.files:
            super()._analyse()
        else:  # an empty list would make the base class process its whole search
            self.finished.emit()

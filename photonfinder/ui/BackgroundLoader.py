import json
import logging
from pathlib import Path
from typing import Callable, List

from PySide6.QtCore import Signal, QObject, QThreadPool, QRunnable, Slot
from PySide6.QtWidgets import QWidget
from peewee import JOIN, fn

from photonfinder.core import ApplicationContext, compress, decompress
from photonfinder.fits_handlers import normalize_fits_header
from photonfinder.models import CORE_MODELS, File, Image, LibraryRoot, FitsHeader, SearchCriteria, FileWCS, ProjectFile, \
    Project
from photonfinder.filesystem import parse_FITS_header, Importer, header_from_xisf_dict
from photonfinder.platesolver import ASTAPSolver, get_image_center_coords, SolverType, AstrometryNetSolver, \
    has_been_plate_solved, extract_wcs_cards


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

    def _create_runnable(self, fn, *args, **kwargs):
        """Create a QRunnable that will execute the given function."""

        class WorkerRunnable(QRunnable):
            @Slot()
            def run(self_runnable):
                try:
                    with self.context.database.bind_ctx(CORE_MODELS):
                        fn(*args, **kwargs)
                except Exception as e:
                    logging.error(f"Error in worker thread: {e}")

        return WorkerRunnable()


class GenericControlLoader(BackgroundLoaderBase):
    """Generic background loader for arbitrary functions."""
    data_ready = Signal(QWidget, list)

    def run_tasks(self, tasks: List[tuple[QWidget, Callable]], search_criteria):
        self.run_in_thread(self._run_tasks, tasks, search_criteria)

    def _run_tasks(self, tasks: List[tuple[QWidget, Callable]], search_criteria: SearchCriteria):
        for widget, task in tasks:
            try:
                with self.context.database.bind_ctx(CORE_MODELS):
                    result = task(search_criteria)
                self.data_ready.emit(widget, result)
            except Exception as e:
                logging.error(f"Error loading data for control {widget.objectName()}: {e}", exc_info=True)


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
    """Helper class for asynchronous loading of search results from the database."""

    # Signal emitted when search results are loaded
    results_loaded = Signal(list, int, int, bool)  # results, page, total, has_more

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self.page_size = 100
        self.current_page = 0
        self.total_results = 0
        self.last_criteria = None
        self.running = False

    def search(self, search_criteria, page=0):
        if self.running and search_criteria == self.last_criteria and page == self.current_page:
            return # don't start if we're running the same query already

        """Start a search with the given criteria."""
        self.current_page = page
        self.last_criteria = search_criteria
        self.running = True
        self.run_in_thread(self._search_task, search_criteria, page)

    def load_more(self):
        """Load the next page of results using the last search criteria."""
        if self.last_criteria:
            self.search(self.last_criteria, self.current_page + 1)

    def _search_task(self, search_criteria, page):
        """Background task to search for files matching the criteria."""
        try:
            project_names_subq = (
                ProjectFile
                .select(
                    ProjectFile.file.alias('file_id'),
                    fn.GROUP_CONCAT(Project.name).alias('project_names')
                )
                .join(Project)
                .group_by(ProjectFile.file)
            )

            # Start building the query
            fields = [File.name, Image.image_type, Image.filter, Image.exposure, Image.gain, Image.offset,
                      Image.binning, Image.set_temp, Image.camera, Image.telescope, Image.object_name,
                      Image.date_obs, File.path, File.size, File.mtime_millis, Image.coord_ra, Image.coord_dec,
                      FileWCS.wcs.is_null(False).alias('has_wcs'), project_names_subq.c.project_names.alias('project_names')]
            query = (File
                     .select(*(fields + [File, Image, LibraryRoot]))
                     .join_from(File, LibraryRoot)
                     .join_from(File, Image, JOIN.LEFT_OUTER)
                     .join_from(File, FileWCS, JOIN.LEFT_OUTER)
                     .join_from(File, project_names_subq, JOIN.LEFT_OUTER, on=(File.rowid == project_names_subq.c.file_id))
                     )


            # Apply search criteria to the query
            query = Image.apply_search_criteria(query, search_criteria)

            # Apply sorting
            if search_criteria.sorting_index is None:
                query = query.order_by(File.root, File.path, File.name)
            else:
                field = fields[search_criteria.sorting_index]
                if field == File.name or field == File.path:
                    field = field.collate("NOCASE")
                query = query.order_by(field.desc()) if search_criteria.sorting_desc else query.order_by(field.asc())

            # Get total count for pagination
            self.total_results = query.count()

            # Apply pagination
            query = query.paginate(page + 1, self.page_size)

            # Execute the query and get results
            results = list(query)

            # Check if there are more results
            has_more = (page + 1) * self.page_size < self.total_results

            # Emit signal with the results
            self.results_loaded.emit(results, page, self.total_results, has_more)
        except Exception as e:
            logging.error(f"Error searching files: {e}", exc_info=True)
            self.results_loaded.emit([], False)
        self.running = False


class ImageReindexWorker(BackgroundLoaderBase):
    """Worker class for reindexing image metadata."""
    finished = Signal()

    def reindex_images(self):
        """Start the reindexing process in a background thread."""
        self.run_in_thread(self._reindex_images_task)

    def _reindex_images_task(self):
        """Background task to reindex image metadata."""
        try:
            # Report starting
            self.context.status_reporter.update_status("Starting image metadata reindexing...")

            # Drop and recreate the Image table
            self.context.status_reporter.update_status("Dropping Image table...")
            with self.context.database.bind_ctx([Image]):
                Image.drop_table()
                Image.create_table()

            # Get count of headers for progress reporting
            with self.context.database.bind_ctx([FitsHeader]):
                total_headers = FitsHeader.select().count()

            self.context.status_reporter.update_status(f"Processing {total_headers} FITS headers...")

            # Process headers in batches
            batch_size = 1000
            processed = 0
            new_images = []

            with self.context.database.bind_ctx([FitsHeader, File, Image]):
                # Query all headers with their associated files
                query = (FitsHeader
                         .select(FitsHeader, File, FileWCS)
                         .join(File)
                         .join(FileWCS, JOIN.LEFT_OUTER))

                # Process each header
                for header_record in query:
                    try:
                        # Deserialize the header
                        from astropy.io.fits import Header
                        header = None
                        if Importer.is_fits_by_name(header_record.file.name):
                            header = parse_FITS_header(decompress(header_record.header))
                        elif Importer.is_xisf_by_name(header_record.file.name):
                            header = header_from_xisf_dict(json.loads(decompress(header_record.header)))

                        if header is None:
                            continue

                        if not hasattr(header_record.file, 'filewcs') and has_been_plate_solved(header):
                            solution = extract_wcs_cards(header)
                            wcs = FileWCS(file=header_record.file, wcs=compress(solution.tostring().encode()))
                            FileWCS.insert(wcs.__data__).on_conflict_ignore().execute()
                            setattr(header_record.file, 'filewcs', wcs)

                        self.context.settings.add_known_fits_keywords(header.keys())
                        # Process the header
                        image = normalize_fits_header(header_record.file, header, self.context.status_reporter)
                        if image:
                            if hasattr(header_record.file, 'filewcs'):
                                wcs_str = decompress(header_record.file.filewcs.wcs)
                                wcs_header = Header.fromstring(wcs_str)
                                ra, dec, healpix = get_image_center_coords(wcs_header)
                                image.coord_ra = ra
                                image.coord_dec = dec
                                image.coord_pix256 = healpix
                            new_images.append(image)

                        # Update progress periodically
                        processed += 1
                        if processed % 100 == 0 or processed == total_headers:
                            self.context.status_reporter.update_status(
                                f"Processed {processed}/{total_headers} headers...", True)

                        # Bulk save images in batches
                        if len(new_images) >= batch_size:
                            with self.context.database.atomic():
                                Image.bulk_create(new_images)
                            new_images = []

                    except Exception as e:
                        logging.error(f"Error processing header: {e}", exc_info=True)
                        self.context.status_reporter.update_status(f"Error processing header: {str(e)}")

                # Save any remaining images
                if new_images:
                    with self.context.database.atomic():
                        Image.bulk_create(new_images)
                    self.context.status_reporter.update_status(f"Saved {len(new_images)} images to database", True)

            self.context.status_reporter.update_status("Image metadata reindexing complete!")

        except Exception as e:
            self.context.status_reporter.update_status(f"Error during reindexing: {str(e)}")

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
    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File]):
        super().__init__(context)
        self.search_criteria = search_criteria
        self.files = files

    def start(self):
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
            else:
                with self.context.database.bind_ctx([File, Image]):
                    query = self.create_query()
                    self.total = query.count()
                    self.total_found.emit(self.total)
                    for i, file in enumerate(query):
                        if self.cancelled:
                            break
                        self._process_file(file, i)

            self.finished.emit()
        except Exception as e:
            logging.error(f"Error processing files: {e}", exc_info=True)
            self.error.emit(str(e))

    def create_query(self):
        query = (File
                 .select(* self.get_tables())
                 .join_from(File, LibraryRoot)
                 .join_from(File, Image, JOIN.LEFT_OUTER)
                 .order_by(File.root, File.path, File.name))
        query = Image.apply_search_criteria(query, self.search_criteria)
        return query

    def _process_file(self, file, index):
        self.progress.emit(index)

    def get_tables(self) -> List:
        return [File, Image, LibraryRoot]


class PlateSolveTask(FileProcessingTask):

    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File],
                 solver_type: SolverType):
        super().__init__(context, search_criteria, files)
        settings = self.context.settings
        self.solved_files = list()
        match solver_type:
            case SolverType.ASTAP:
                self.solver = ASTAPSolver(exe=settings.get_astap_path(), fallback_fov=settings.get_astap_fallback_fov())
            case SolverType.ASTROMETRY_NET:
                self.solver = AstrometryNetSolver(api_key=settings.get_astrometry_net_api_key(),
                                                  force_image_upload=settings.get_astrometry_net_force_image_upload())

    def get_tables(self) -> List:
        tables = super().get_tables()
        tables.append(FileWCS)
        return tables

    def create_query(self):
        query = super().create_query()
        query = (query
                 .where((Image.image_type == "LIGHT") | (Image.image_type == "MASTER LIGHT") |
                        (Image.image_type.is_null()))
                 .join_from(File, FileWCS, JOIN.LEFT_OUTER)
                 .where(FileWCS.wcs.is_null()))
        return query

    def _process_file(self, file, index):
        super()._process_file(file, index)
        self.message.emit(f"Processing file {index + 1}/{self.total}:\n {file.full_filename()}")

        try:
            with (self.solver):
                solution = self.solver.solve(Path(file.full_filename()), file.image)
                if solution:
                    self.context.status_reporter.update_status(f"Solved file {file.full_filename()}")
                    FileWCS(file=file, wcs=compress(solution.tostring().encode())).save()
                    ra, dec, healpix = get_image_center_coords(solution)
                    Image.update(coord_ra=ra, coord_dec=dec, coord_pix256=healpix
                                 ).where(Image.file == file).execute()
                    file.has_wcs = True
                    file.image.coord_ra = ra
                    file.image.coord_dec = dec
                    file.image.coord_pix256 = healpix
                    self.solved_files.append(file)
        except Exception as e:
            logging.error(f"Error solving file {file.full_filename()}: {e}", exc_info=True)
            if self.total <= 1:
                raise e


class FileListTask(FileProcessingTask):
    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File]):
        super().__init__(context, search_criteria, files)
        self.output_filename = None

    def start(self, output_filename: str = None):
        self.output_filename = output_filename
        super().start()

    def create_query(self):
        query = (File
                 .select(File, LibraryRoot)
                 .join_from(File, LibraryRoot)
                 .order_by(File.root, File.path, File.name))
        query = Image.apply_search_criteria(query, self.search_criteria)
        return query

    def _process_files(self):
        if self.output_filename is None:
            return
        with open(self.output_filename, 'w') as fd:
            self.fd = fd
            super()._process_files()

    def _process_file(self, file, index):
        self.message.emit(f"Processing file {index + 1}/{self.total}:\n {file.full_filename()}")
        self.fd.write(f"{str(Path(file.full_filename()))}\n")
        self.progress.emit(index)

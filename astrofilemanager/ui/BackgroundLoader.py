import logging
from typing import Callable, List

from PySide6.QtCore import Signal, QObject, QThreadPool, QRunnable, Slot
from PySide6.QtWidgets import QWidget
from peewee import JOIN

from astrofilemanager.core import ApplicationContext
from astrofilemanager.fits_handlers import normalize_fits_header
from astrofilemanager.models import CORE_MODELS, File, Image, LibraryRoot, FitsHeader, SearchCriteria


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
            library_roots = list(LibraryRoot.select())

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

    def search(self, search_criteria, page=0):
        """Start a search with the given criteria."""
        self.current_page = page
        self.last_criteria = search_criteria
        self.run_in_thread(self._search_task, search_criteria, page)

    def load_more(self):
        """Load the next page of results using the last search criteria."""
        if self.last_criteria:
            self.current_page += 1
            self.search(self.last_criteria, self.current_page)

    def _search_task(self, search_criteria, page):
        """Background task to search for files matching the criteria."""
        try:
            # Start building the query
            query = (File
                     .select(File, Image)
                     .join(Image, JOIN.LEFT_OUTER)
                     .order_by(File.root, File.path, File.name))

            # Apply search criteria to the query
            query = Image.apply_search_criteria(query, search_criteria)

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


class ImageReindexWorker(BackgroundLoaderBase):
    """Worker class for reindexing image metadata."""
    finished = Signal()
    progress = Signal(str)

    def reindex_images(self):
        """Start the reindexing process in a background thread."""
        self.run_in_thread(self._reindex_images_task)

    def _reindex_images_task(self):
        """Background task to reindex image metadata."""
        try:
            # Report starting
            self.progress.emit("Starting image metadata reindexing...")

            # Drop and recreate the Image table
            self.progress.emit("Dropping Image table...")
            with self.context.database.bind_ctx([Image]):
                Image.drop_table()
                Image.create_table()

            # Get count of headers for progress reporting
            with self.context.database.bind_ctx([FitsHeader]):
                total_headers = FitsHeader.select().count()

            self.progress.emit(f"Processing {total_headers} FITS headers...")

            # Process headers in batches
            batch_size = 100
            processed = 0
            new_images = []

            with self.context.database.bind_ctx([FitsHeader, File, Image]):
                # Query all headers with their associated files
                query = (FitsHeader
                         .select(FitsHeader, File)
                         .join(File))

                # Process each header
                for header_record in query:
                    try:
                        # Deserialize the header
                        from astropy.io.fits import Header
                        header = Header.fromstring(header_record.header.decode('utf-8'))

                        # Process the header
                        image = normalize_fits_header(header_record.file, header)
                        if image:
                            new_images.append(image)

                        # Update progress periodically
                        processed += 1
                        if processed % 10 == 0 or processed == total_headers:
                            self.progress.emit(f"Processed {processed}/{total_headers} headers...")

                        # Bulk save images in batches
                        if len(new_images) >= batch_size:
                            with self.context.database.atomic():
                                Image.bulk_create(new_images)
                            self.progress.emit(f"Saved {len(new_images)} images to database")
                            new_images = []

                    except Exception as e:
                        self.progress.emit(f"Error processing header: {str(e)}")

                # Save any remaining images
                if new_images:
                    with self.context.database.atomic():
                        Image.bulk_create(new_images)
                    self.progress.emit(f"Saved {len(new_images)} images to database")

            self.progress.emit("Image metadata reindexing complete!")

        except Exception as e:
            self.progress.emit(f"Error during reindexing: {str(e)}")

        # Signal that we're done
        self.finished.emit()

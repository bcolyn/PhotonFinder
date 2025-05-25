import logging

from PySide6.QtCore import Signal, QObject, QThreadPool, QRunnable, Slot
from peewee import JOIN

from core import ApplicationContext
from models import CORE_MODELS, File, Image, LibraryRoot


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

            paths = []
            for file in query:
                if file.path:  # Skip empty paths
                    # Split the path into segments
                    path_segments = file.path.split('/')

                    # Add each segment and its parent path
                    current_path = ""
                    for segment in path_segments:
                        if segment:  # Skip empty segments
                            if current_path:
                                current_path += f"/{segment}"
                            else:
                                current_path = segment

                            if current_path not in paths:
                                paths.append(current_path)

            # Emit signal with the results
            self.paths_loaded.emit(library_root, paths)
        except Exception as e:
            logging.error(f"Error loading paths for library {library_root.name}: {e}")


class SearchResultsLoader(BackgroundLoaderBase):
    """Helper class for asynchronous loading of search results from the database."""

    # Signal emitted when search results are loaded
    results_loaded = Signal(list, bool)  # results, has_more

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
            query = Image._apply_search_criteria(query, search_criteria)

            # Get total count for pagination
            self.total_results = query.count()

            # Apply pagination
            query = query.paginate(page + 1, self.page_size)

            # Execute the query and get results
            results = list(query)

            # Check if there are more results
            has_more = (page + 1) * self.page_size < self.total_results

            # Emit signal with the results
            self.results_loaded.emit(results, has_more)
        except Exception as e:
            logging.error(f"Error searching files: {e}", exc_info=True)
            self.results_loaded.emit([], False)

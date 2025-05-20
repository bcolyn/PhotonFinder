import logging
from PySide6.QtWidgets import *
from PySide6.QtCore import QThread, Signal

from ..core import ApplicationContext, StatusReporter
from .LibraryRootDialog import LibraryRootDialog
from .SearchPanel import SearchPanel
from .SettingsDialog import SettingsDialog
from .generated.MainWindow_ui import Ui_MainWindow
from ..filesystem import Importer, update_fits_header_cache


class UIStatusReporter(StatusReporter):
    def __init__(self, main_window: 'MainWindow'):
        super().__init__()
        self.main_window = main_window

    def update_status(self, message: str) -> None:
        self.main_window.statusBar().showMessage(message)


class LibraryScanWorker(QThread):
    """Worker thread for scanning libraries."""
    finished = Signal()
    change_list_ready = Signal(object)  # Signal emitted when a change list is ready

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.importer = Importer(context)

    def run(self):
        """Run the import process in a background thread."""
        for changes in self.importer.import_files():
            # Emit the change list so it can be processed in the main thread
            self.change_list_ready.emit(changes)

        # Signal that we're done
        self.finished.emit()


class MainWindow(QMainWindow, Ui_MainWindow):

    def __init__(self, app: QApplication, context: ApplicationContext, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.app = app
        self.scan_worker = None  # Initialize scan_worker attribute
        from models import LibraryRoot
        LibraryRoot.initialize(context.database)
        self.new_search_tab()

        context.set_status_reporter(UIStatusReporter(self))

    def new_search_tab(self):
        panel = SearchPanel(self.context, self.tabWidget)
        self.tabWidget.addTab(panel, "All data")

    def close_current_search_tab(self):
        self.close_search_tab(self.tabWidget.currentIndex())

    def close_search_tab(self, index):
        widget = self.tabWidget.widget(index)
        assert isinstance(widget, SearchPanel)
        self.tabWidget.removeTab(index)
        widget.destroy()

    def manage_library_roots(self):
        """
        Open the dialog for managing library roots.
        """
        if self.context.database:
            dialog = LibraryRootDialog(self.context.database, parent=self)
            result = dialog.exec()

            # Reload library roots in all search panels when the dialog is closed
            # This ensures the tree view is updated when library roots are changed
            self.reload_library_roots_in_all_panels()

    def open_settings_dialog(self):
        """
        Open the settings dialog.
        """
        dialog = SettingsDialog(self.context, parent=self)
        dialog.exec()

    def scan_libraries(self):
        """
        Scan libraries for new, changed, and deleted files.
        This runs in a background thread to avoid blocking the UI.
        After the scan is complete, the FITS header cache is updated.
        """
        # Create a worker thread
        self.scan_worker = LibraryScanWorker(self.context)

        # Connect signals
        self.scan_worker.change_list_ready.connect(self._process_change_list)
        self.scan_worker.finished.connect(self._scan_finished)

        # Start the worker thread
        self.scan_worker.start()

    def _process_change_list(self, changes):
        """Process a change list from the worker thread."""
        # Apply changes to the databases
        changes.apply_all()

        # Update the FITS header cache
        update_fits_header_cache(changes, self.context.status_reporter)

    def _scan_finished(self):
        """Called when the scan is finished."""
        self.context.status_reporter.update_status("Library scan complete.")

        # Clean up the worker thread
        self.scan_worker.deleteLater()
        self.scan_worker = None


    def reload_library_roots_in_all_panels(self):
        """
        Reload library roots in all search panels.
        This should be called when library roots are changed.
        """
        logging.debug("Reloading library roots in all search panels")
        for i in range(self.tabWidget.count()):
            widget = self.tabWidget.widget(i)
            if isinstance(widget, SearchPanel):
                widget.load_library_roots()

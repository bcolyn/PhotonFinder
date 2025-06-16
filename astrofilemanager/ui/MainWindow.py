import logging
import time
from PySide6.QtWidgets import *
from PySide6.QtCore import QThread, Signal, QObject

from astrofilemanager.core import ApplicationContext, StatusReporter
from astrofilemanager.filesystem import Importer, update_fits_header_cache, check_missing_header_cache
from .LibraryRootDialog import LibraryRootDialog
from .LogWindow import LogWindow
from .SearchPanel import SearchPanel
from .SettingsDialog import SettingsDialog
from .generated.MainWindow_ui import Ui_MainWindow


class UIStatusReporter(StatusReporter,QObject):
    on_message = Signal(str)

    def __init__(self):
        super().__init__()
        self.last_update_time = 0
        self.log_messages = []

    def update_status(self, message: str, bulk=False) -> None:
        current_time = time.time()
        if bulk and (current_time - self.last_update_time) < 1:
            return
        self.last_update_time = current_time
        self.on_message.emit(message)

        # Store non-bulk messages for the log window
        if not bulk:
            self.log_messages.append(message)

    def get_log_messages(self):
        """Return the list of log messages."""
        return self.log_messages


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
        for changes_per_library in self.importer.import_files():
            changes_per_library.apply_all()
            update_fits_header_cache(changes_per_library, self.context.status_reporter)

        check_missing_header_cache(self.context.status_reporter)

        # Signal that we're done
        self.finished.emit()


class MainWindow(QMainWindow, Ui_MainWindow):

    def __init__(self, app: QApplication, context: ApplicationContext, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.app = app
        self.scan_worker = None  # Initialize scan_worker attribute
        self.new_search_tab()

        self.reporter = UIStatusReporter()
        self.reporter.on_message.connect(self.statusBar().showMessage)
        context.set_status_reporter(self.reporter)

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
            dialog = LibraryRootDialog(self.context, parent=self)
            dialog.exec()
            self.reload_library_roots_in_all_panels()
            if dialog.has_changes:
                response = QMessageBox.question(self, "Scan Libraries",
                                                "Library roots have been modified. Would you like to scan libraries now?",
                                                QMessageBox.Yes | QMessageBox.No)
                if response == QMessageBox.Yes:
                    self.scan_libraries()

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
        if self.scan_worker is not None:
            logging.warning("Scan already in progress, skipping.")
            return

        # Create a worker thread
        self.scan_worker = LibraryScanWorker(self.context)

        # Connect signals
        self.scan_worker.finished.connect(self._scan_finished)

        # Start the worker thread
        self.scan_worker.start()

    def _scan_finished(self):
        """Called when the scan is finished."""
        self.context.status_reporter.update_status("Library scan complete.")

        # Clean up the worker thread
        if self.scan_worker:
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
                widget.library_tree_model.reload_library_roots()

    def add_exposure_filter(self):
        self.tabWidget.currentWidget().add_exposure_filter()

    def add_telescope_filter(self):
        self.tabWidget.currentWidget().add_telescope_filter()

    def add_binning_filter(self):
        self.tabWidget.currentWidget().add_binning_filter()

    def add_gain_filter(self):
        self.tabWidget.currentWidget().add_gain_filter()

    def add_temperature_filter(self):
        self.tabWidget.currentWidget().add_temperature_filter()

    def add_datetime_filter(self):
        self.tabWidget.currentWidget().add_datetime_filter()

    def view_log(self):
        """
        Open the log window to display log messages.
        """
        log_window = LogWindow(self)

        # Add all log messages to the log window
        for message in self.reporter.get_log_messages():
            log_window.add_message(message)

        # Show the log window
        log_window.exec()

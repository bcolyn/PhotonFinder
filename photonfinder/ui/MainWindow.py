import logging
import time
from typing import List

from PySide6.QtCore import QThread, Signal, QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import *

from photonfinder.core import ApplicationContext, StatusReporter, backup_database
from photonfinder.filesystem import Importer, update_fits_header_cache, check_missing_header_cache
from photonfinder.models import SearchCriteria
from .AboutDialog import AboutDialog
from .DataReportDialog import DataUsageReportDialog
from .LibraryRootDialog import LibraryRootDialog
from .LogWindow import LogWindow
from .SearchPanel import SearchPanel
from .SettingsDialog import SettingsDialog
from .generated.MainWindow_ui import Ui_MainWindow
import photonfinder.ui.generated.resources_rc
from ..platesolver import SolverType


class UIStatusReporter(StatusReporter, QObject):
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
            self.context.status_reporter.update_status(
                f" {changes_per_library.root.name}: Files removed {len(changes_per_library.removed_files)} " +
                f"added {len(changes_per_library.new_files)} " +
                f"changed {len(changes_per_library.changed_files)}")
            changes_per_library.apply_all()
            update_fits_header_cache(changes_per_library, self.context.status_reporter, self.context.settings)

        check_missing_header_cache(self.context.status_reporter, self.context.settings)

        # Signal that we're done
        self.finished.emit()


class MainWindow(QMainWindow, Ui_MainWindow):
    tabs_changed = Signal(list)

    def __init__(self, app: QApplication, context: ApplicationContext, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.app = app
        self.scan_worker = None  # Initialize scan_worker attribute
        self.new_search_tab()

        # Set the window icon from the resource file
        icon = QIcon(":/icon.png")
        self.setWindowIcon(icon)

        self.reporter = UIStatusReporter()
        self.reporter.on_message.connect(self.statusBar().showMessage)
        context.set_status_reporter(self.reporter)

    def new_search_tab(self):
        panel = SearchPanel(self.context, parent=self.tabWidget, mainWindow=self)
        tab = self.tabWidget.addTab(panel, "Loading")
        self.tabWidget.setCurrentIndex(tab)
        self.tabs_changed.emit(self.get_search_panels())

    def dup_search_tab(self):
        current_index = self.tabWidget.currentIndex()
        if current_index == -1:
            return
        current_widget = self.tabWidget.widget(current_index)
        assert isinstance(current_widget, SearchPanel)
        current_criteria = current_widget.search_criteria
        panel = SearchPanel(self.context, parent=self.tabWidget, mainWindow=self)
        tab = self.tabWidget.addTab(panel, "Loading")
        panel.apply_search_criteria(current_criteria)
        self.tabWidget.setCurrentIndex(tab)
        self.tabs_changed.emit(self.get_search_panels())

    def close_current_search_tab(self):
        self.close_search_tab(self.tabWidget.currentIndex())

    def close_search_tab(self, index):
        widget = self.tabWidget.widget(index)
        assert isinstance(widget, SearchPanel)
        self.tabWidget.removeTab(index)
        widget.destroy()
        self.tabs_changed.emit(self.get_search_panels())

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

    def get_search_panels(self) -> list[SearchPanel]:
        return [self.tabWidget.widget(i) for i in range(self.tabWidget.count())]

    def set_tab_title(self, tab, title: str):
        tabs: QTabWidget = self.tabWidget
        my_index = tabs.indexOf(tab)
        tabs.setTabText(my_index, title)
        self.tabs_changed.emit(self.get_search_panels())

    def add_exposure_filter(self):
        self.get_current_search_panel().add_exposure_filter()

    def get_current_search_panel(self) -> SearchPanel:
        return self.tabWidget.currentWidget()

    def add_telescope_filter(self):
        self.get_current_search_panel().add_telescope_filter()

    def add_binning_filter(self):
        self.get_current_search_panel().add_binning_filter()

    def add_gain_filter(self):
        self.get_current_search_panel().add_gain_filter()

    def add_temperature_filter(self):
        self.get_current_search_panel().add_temperature_filter()

    def add_datetime_filter(self):
        self.get_current_search_panel().add_datetime_filter()

    def add_coordinates_filter(self):
        self.get_current_search_panel().add_coordinates_filter()

    def add_header_text_filter(self):
        self.get_current_search_panel().add_header_text_filter()

    def report_metadata(self):
        self.get_current_search_panel().report_metadata()

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

    def show_about_dialog(self):
        """
        Show the about dialog with project information.
        """
        dialog = AboutDialog(self)
        dialog.exec()

    def export_data(self):
        self.get_current_search_panel().export_data()

    def create_backup(self):
        if not self.context.database:
            QMessageBox.warning(self, "Backup Failed", "Database is not open.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Save Database Backup", "",
                                                   "SQLite Database (*.db);;All Files (*)")

        if not file_path:
            return  # User cancelled

        try:
            backup_database(self.context.database, file_path)
            self.context.status_reporter.update_status(f"Database backup created at {file_path}")
            QMessageBox.information(self, "Backup Complete", f"Database backup created at {file_path}")
        except Exception as e:
            error_msg = f"Failed to create backup: {str(e)}"
            logging.error(error_msg)
            self.context.status_reporter.update_status(error_msg)
            QMessageBox.critical(self, "Backup Failed", error_msg)

    def create_database(self):

        file_path, _ = QFileDialog.getSaveFileName(self, "Create Database",
                                                   "", "SQLite Database (*.db);;All Files (*)")

        if not file_path:
            return  # User cancelled

        try:
            # close all tabs
            for i in range(self.tabWidget.count()):
                self.close_search_tab(i)
            self.context.switch_database(file_path)
            # open new tab
            self.new_search_tab()
            self.context.status_reporter.update_status(f"Database created and opened at {file_path}")
            self.reload_library_roots_in_all_panels()

        except Exception as e:
            error_msg = f"Failed to create database: {str(e)}"
            logging.error(error_msg)
            self.context.status_reporter.update_status(error_msg)
            QMessageBox.critical(self, "Database Creation Failed", error_msg)

    def open_database(self):
        """
        Open an existing database file.
        Prompts the user for a file and calls ApplicationContext.switch_database
        to open the selected database.
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Database",
            "",
            "SQLite Database (*.db);;All Files (*)"
        )

        if not file_path:
            return  # User cancelled

        try:
            # close all tabs
            for i in range(self.tabWidget.count()):
                self.close_search_tab(i)
            self.context.switch_database(file_path)
            # open new tab
            self.new_search_tab()

            self.context.status_reporter.update_status(f"Database opened at {file_path}")
            self.reload_library_roots_in_all_panels()
        except Exception as e:
            error_msg = f"Failed to open database: {str(e)}"
            logging.error(error_msg)
            self.context.status_reporter.update_status(error_msg)
            QMessageBox.critical(self, "Database Open Failed", error_msg)

    def find_matching_darks(self):
        current_panel = self.get_current_search_panel()
        selected_image = current_panel.get_selected_image()
        if not selected_image:
            return
        dark_criteria = SearchCriteria.find_dark(selected_image)
        panel = SearchPanel(self.context, parent=self.tabWidget, mainWindow=self)
        tab = self.tabWidget.addTab(panel, "Loading")
        panel.apply_search_criteria(dark_criteria)
        self.tabWidget.setCurrentIndex(tab)

    def find_matching_flats(self):
        current_panel = self.get_current_search_panel()
        selected_image = current_panel.get_selected_image()
        if not selected_image:
            return
        flat_criteria = SearchCriteria.find_flat(selected_image)
        panel = SearchPanel(self.context, parent=self.tabWidget, mainWindow=self)
        tab = self.tabWidget.addTab(panel, "Loading")
        panel.apply_search_criteria(flat_criteria)
        self.tabWidget.setCurrentIndex(tab)

    def on_tab_switch(self):
        self.enable_actions_for_current_tab()

    def open_selected_file(self):
        """Open the selected file using the associated application."""
        current_panel = self.get_current_search_panel()
        if not current_panel:
            return
        # Get the selected row
        selected_rows = current_panel.dataView.selectionModel().selectedRows()
        if not selected_rows:
            return
        # Open the file at the selected row
        current_panel.open_file(selected_rows[0])

    def show_file_location(self):
        """Open the file explorer showing the directory containing the selected file."""
        current_panel = self.get_current_search_panel()
        if not current_panel:
            return
        # Get the selected row
        selected_rows = current_panel.dataView.selectionModel().selectedRows()
        if not selected_rows:
            return
        # Show the file location
        current_panel.show_file_location(selected_rows[0])

    def select_path_in_tree(self):
        """Select the path of the selected file in the tree view."""
        current_panel = self.get_current_search_panel()
        if not current_panel:
            return
        # Get the selected row
        selected_rows = current_panel.dataView.selectionModel().selectedRows()
        if not selected_rows:
            return
        # Select the path in the tree
        current_panel.select_path_in_tree(selected_rows[0])

    def plate_solve_files(self):
        self.get_current_search_panel().plate_solve_files()

    def plate_solve_files_astrometry(self):
        self.get_current_search_panel().plate_solve_files(SolverType.ASTROMETRY_NET)

    def report_list_files(self):
        """
        Show a file save dialog to select an output filename (.txt|.lst),
        then create a FileListTask to generate a list of files matching the current search criteria.
        """
        self.get_current_search_panel().report_list_files()

    def report_telescopius_list(self):
        """
        Show the Telescopius Compare dialog for comparing files with Telescopius data.
        """
        self.get_current_search_panel().report_telescopius_list()

    def report_data_usage(self):
        dialog = DataUsageReportDialog(parent=self)
        dialog.show()

    def enable_actions_for_current_tab(self):
        current_panel = self.get_current_search_panel()
        if not current_panel:
            return
        selected_image = current_panel.get_selected_image()
        has_selection = selected_image is not None
        self.actionOpen_File.setEnabled(has_selection)
        self.actionShow_location.setEnabled(has_selection)
        self.actionSelect_path.setEnabled(has_selection)
        if selected_image:
            current_type = selected_image.image_type
            self.actionFind_matching_darks.setEnabled(current_type == "LIGHT" or current_type == "FLAT")
            self.actionFind_matching_flats.setEnabled(current_type == "LIGHT")

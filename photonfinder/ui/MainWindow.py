import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import List

from PySide6.QtCore import QThread, Signal, QObject, QSize
from PySide6.QtGui import QIcon, QPalette, QAction, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import *

from photonfinder.core import ApplicationContext, StatusReporter, backup_database
from photonfinder.filesystem import Importer, update_fits_header_cache, check_missing_header_cache
from photonfinder.models import SearchCriteria, Project, File, ProjectFile, RootAndPath, Image, LibraryRoot
from .AboutDialog import AboutDialog
from .LibraryRootDialog import LibraryRootDialog
from .LibraryTreeModel import AllLibrariesNode, LibraryRootNode
from .LogWindow import LogWindow
from .ProjectEditDialog import ProjectEditDialog
from .ProjectsWindow import ProjectsWindow
from .SearchPanel import SearchPanel, AdvancedFilter
from .SettingsDialog import SettingsDialog
from .common import create_colored_svg_icon
from .generated.MainWindow_ui import Ui_MainWindow
import photonfinder.ui.generated.resources_rc
from .session import SessionManager, Session
from ..platesolver import SolverType


class MainWindow(QMainWindow, Ui_MainWindow):
    tabs_changed = Signal(list)
    projects_window: ProjectsWindow | None

    def __init__(self, app: QApplication, context: ApplicationContext, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.app = app
        self.session_manager = SessionManager(context.get_session_file())
        self.scan_worker = None  # Initialize scan_worker attribute
        self.projects_window = None

        self.menuProject_Details.removeAction(self.actionLoading_2)
        self.menuSearch_Details.removeAction(self.actionLoading)

        # Set the window icon from the resource file
        self.setWindowIcon(QIcon(":/icon.png"))

        text_color = self.palette().color(QPalette.WindowText)
        size = QSize(24, 24)
        self.actionManage_Projects.setIcon(create_colored_svg_icon(":/res/stack.svg", size, text_color))
        self.action_New_Tab.setIcon(create_colored_svg_icon(":/res/window-plus.svg", size, text_color))
        self.action_Manage_Libraries.setIcon(create_colored_svg_icon(":/res/hdd.svg", size, text_color))
        self.actionOpen_File.setIcon(create_colored_svg_icon(":/res/card-image.svg", size, text_color))
        self.action_Open_Database.setIcon(create_colored_svg_icon(":/res/database.svg", size, text_color))
        self.action_Export_Data.setIcon(create_colored_svg_icon(":/res/send-plus.svg", size, text_color))

        self.actionExposure.setIcon(create_colored_svg_icon(":/res/clock.svg", size, text_color))
        self.actionCoordinates.setIcon(create_colored_svg_icon(":/res/rulers.svg", size, text_color))
        self.actionDate.setIcon(create_colored_svg_icon(":/res/calendar3.svg", size, text_color))
        self.actionTelescope.setIcon(create_colored_svg_icon(":/res/telescope-icon-original.svg", size, text_color))
        self.actionBinning.setIcon(create_colored_svg_icon(":/res/border-outer.svg", size, text_color))
        self.actionGain.setIcon(create_colored_svg_icon(":/res/exposure.svg", size, text_color))
        self.actionTemperature.setIcon(create_colored_svg_icon(":/res/thermometer-half.svg", size, text_color))

        # hide the dock initially
        self.dockWidget.hide()

        self.reporter = UIStatusReporter()
        self.reporter.on_message.connect(self.statusBar().showMessage)
        context.set_status_reporter(self.reporter)
        self.connect_signals()
        self.restore_session()
        self.setAcceptDrops(True)

    def connect_signals(self):
        self.tabWidget.tabCloseRequested.connect(self.close_search_tab)
        self.action_Export_Data.triggered.connect(self.export_data)
        self.action_New_Tab.triggered.connect(self.new_search_tab)
        self.action_Close_Tab.triggered.connect(self.close_current_search_tab)
        self.action_Exit.triggered.connect(self.close)
        self.action_Manage_Libraries.triggered.connect(self.manage_library_roots)
        self.action_Settings.triggered.connect(self.open_settings_dialog)
        self.action_Scan_Libraries.triggered.connect(self.scan_libraries)
        self.actionExposure.triggered.connect(self.add_exposure_filter)
        self.actionDate.triggered.connect(self.add_datetime_filter)
        self.action_View_Log.triggered.connect(self.view_log)
        self.actionCoordinates.triggered.connect(self.add_coordinates_filter)
        self.actionTelescope.triggered.connect(self.add_telescope_filter)
        self.actionBinning.triggered.connect(self.add_binning_filter)
        self.actionGain.triggered.connect(self.add_gain_filter)
        self.actionTemperature.triggered.connect(self.add_temperature_filter)
        self.action_Create_Backup.triggered.connect(self.create_backup)
        self.action_Create_Database.triggered.connect(self.create_database)
        self.action_Open_Database.triggered.connect(self.open_database)
        self.actionDuplicate_Tab.triggered.connect(self.dup_search_tab)
        self.actionFind_matching_darks.triggered.connect(self.find_matching_darks)
        self.actionFind_matching_flats.triggered.connect(self.find_matching_flats)
        self.action_About.triggered.connect(self.show_about_dialog)
        self.tabWidget.currentChanged.connect(self.on_tab_switch)
        self.actionOpen_File.triggered.connect(self.open_selected_file)
        self.actionShow_location.triggered.connect(self.show_file_location)
        self.actionShow_Details.triggered.connect(self.show_file_details)
        self.actionSelect_path.triggered.connect(self.select_path_in_tree)
        self.actionPlate_solve_files.triggered.connect(self.plate_solve_files)
        self.actionPlate_Solve_Astrometry_net.triggered.connect(self.plate_solve_files_astrometry)
        self.actionList_Files.triggered.connect(self.report_list_files)
        self.actionHeader_Text.triggered.connect(self.add_header_text_filter)
        self.actionMetadata_Report.triggered.connect(self.report_metadata)
        self.actionTelescopius_List.triggered.connect(self.report_telescopius_list)
        self.actionTarget_List_Report.triggered.connect(self.report_targets)
        self.actionManage_Projects.triggered.connect(self.show_projects_window)
        self.menuAddToNearbyProject.aboutToShow.connect(self.populate_nearby_projects)
        self.menuAddToRecentProject.aboutToShow.connect(self.populate_recent_projects)
        self.menuProject.aboutToShow.connect(self.on_show_project_menu)
        self.actionAddToNewProject.triggered.connect(self.on_add_to_project_action)
        self.dockWidget.visibilityChanged.connect(self.show_projects_window)
        self.action_filter_no_project.triggered.connect(self.add_no_project_filter)
        self.menuSearch_Details.aboutToShow.connect(self.populate_search_details)
        self.menuProject_Details.aboutToShow.connect(self.populate_project_details)

    def closeEvent(self, event):
        try:
            self.save_session()
        finally:
            event.accept()  # Accept the event to actually close the window

    def dragEnterEvent(self, event: QDragEnterEvent):
        # Check if dragged data has URLs (files)
        if not self.scan_worker and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            file_paths = [url.toLocalFile() for url in urls]
            if self.scan_worker is not None:
                logging.warning("Filesystem Scanner is busy, aborting.")
                return

            self.scan_worker = LibraryScanWorker(self.context, file_paths)
            self.scan_worker.finished.connect(self._scan_finished)
            self.scan_worker.start()
        else:
            event.ignore()

    def restore_session(self):
        try:
            sessions = self.session_manager.load_sessions()
            for session in sessions:
                panel = self.new_search_tab(session.criteria)
                panel.set_title(session.title)
                panel.visibility_controller.load_visibility(session.hidden_columns)
        except Exception as e:
            logging.error(e, exc_info=True)
        finally:
            if len(self.get_search_panels()) == 0:
                self.new_search_tab()

    def save_session(self):
        sessions = [Session(
            criteria=panel.search_criteria,
            hidden_columns=panel.visibility_controller.save_visibility(),
            title=panel.title
        ) for panel in self.get_search_panels()]
        self.session_manager.save_sessions(sessions)

    def new_search_tab(self, search_criteria=None) -> SearchPanel:
        panel = SearchPanel(self.context, parent=self.tabWidget, mainWindow=self)
        tab = self.tabWidget.addTab(panel, "Loading")
        if search_criteria:
            panel.apply_search_criteria(search_criteria)
        self.tabWidget.setCurrentIndex(tab)
        self.tabs_changed.emit(self.get_search_panels())
        return panel

    def dup_search_tab(self):
        current_index = self.tabWidget.currentIndex()
        if current_index == -1:
            return
        current_widget = self.tabWidget.widget(current_index)
        assert isinstance(current_widget, SearchPanel)
        current_criteria = current_widget.search_criteria
        self.new_search_tab(current_criteria)

    def close_current_search_tab(self):
        self.close_search_tab(self.tabWidget.currentIndex())

    def close_search_tab(self, index):
        widget = self.tabWidget.widget(index)
        assert isinstance(widget, SearchPanel)
        self.tabWidget.removeTab(index)
        widget.destroy()
        if self.tabWidget.count() == 0:
            self.new_search_tab()
        else:
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

    def show_projects_window(self, checked):
        if checked:
            if not self.projects_window:
                widget = ProjectsWindow(context=self.context, main_window=self, parent=self)
                self.projects_window = widget
                self.menuProject_Details.setEnabled(True)
                self.dockWidget.setWidget(widget)
            self.actionManage_Projects.setChecked(True)
            self.dockWidget.show()
        else:
            if self.projects_window:
                self.clear_projects_window()
                self.dockWidget.setWidget(self.dockWidgetContents)
            self.actionManage_Projects.setChecked(False)
            self.dockWidget.hide()

    def clear_projects_window(self):
        self.projects_window.save_cols()
        self.projects_window = None
        self.menuProject_Details.setEnabled(False)

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

        search_panel = self.get_current_search_panel()
        tree_nodes = search_panel.get_selected_treenodes()
        roots = []
        for tree_node in tree_nodes:
            if isinstance(tree_node, AllLibrariesNode):
                roots = None
                break
            elif isinstance(tree_node, LibraryRootNode):
                roots.append(tree_node.library_root)

        # Create a worker thread
        self.scan_worker = LibraryScanWorker(self.context, roots=roots)

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

        self.get_current_search_panel().update_search_criteria()

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

    def get_current_search_panel(self) -> SearchPanel:
        return self.tabWidget.currentWidget()

    def add_exposure_filter(self):
        self.get_current_search_panel().add_exposure_filter()
        self.enable_actions_for_current_tab()

    def add_telescope_filter(self):
        self.get_current_search_panel().add_telescope_filter()
        self.enable_actions_for_current_tab()

    def add_binning_filter(self):
        self.get_current_search_panel().add_binning_filter()
        self.enable_actions_for_current_tab()

    def add_gain_filter(self):
        self.get_current_search_panel().add_gain_filter()
        self.enable_actions_for_current_tab()

    def add_temperature_filter(self):
        self.get_current_search_panel().add_temperature_filter()
        self.enable_actions_for_current_tab()

    def add_datetime_filter(self):
        self.get_current_search_panel().add_datetime_filter()
        self.enable_actions_for_current_tab()

    def add_coordinates_filter(self):
        self.get_current_search_panel().add_coordinates_filter()
        self.enable_actions_for_current_tab()

    def add_header_text_filter(self):
        self.get_current_search_panel().add_header_text_filter()

    def add_no_project_filter(self):
        self.get_current_search_panel().add_no_project_filter()

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

    def show_file_details(self):
        current_panel = self.get_current_search_panel()
        if not current_panel:
            return
        selected_rows = current_panel.dataView.selectionModel().selectedRows()
        if not selected_rows:
            return
        current_panel.show_file_details(selected_rows[0])

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

    def report_targets(self):
        self.get_current_search_panel().report_targets()

    def enable_actions_for_current_tab(self):
        current_panel = self.get_current_search_panel()
        if not current_panel:
            return
        file = current_panel.get_selected_file()
        selected_image = file.image if file and hasattr(file, 'image') and file.image else None
        has_selection = selected_image is not None
        self.actionOpen_File.setEnabled(has_selection)
        self.actionShow_location.setEnabled(has_selection)
        self.actionSelect_path.setEnabled(has_selection)
        self.actionShow_Details.setEnabled(has_selection)

        if selected_image:
            current_type = selected_image.image_type
            self.actionFind_matching_darks.setEnabled(current_type == "LIGHT" or current_type == "FLAT")
            self.actionFind_matching_flats.setEnabled(current_type == "LIGHT")
            has_wcs = hasattr(file, 'has_wcs') and file.has_wcs
            is_light = not current_type or "LIGHT" in current_type
            self.actionPlate_solve_files.setEnabled(is_light and not has_wcs)
            self.actionPlate_Solve_Astrometry_net.setEnabled(is_light and not has_wcs)

        self.actionExposure.setChecked(AdvancedFilter.EXPOSURE in current_panel.advanced_options)
        self.actionCoordinates.setChecked(AdvancedFilter.COORDINATES in current_panel.advanced_options)
        self.actionDate.setChecked(AdvancedFilter.DATETIME in current_panel.advanced_options)
        self.actionTelescope.setChecked(AdvancedFilter.TELESCOPE in current_panel.advanced_options)
        self.actionBinning.setChecked(AdvancedFilter.BINNING in current_panel.advanced_options)
        self.actionGain.setChecked(AdvancedFilter.GAIN in current_panel.advanced_options)
        self.actionTemperature.setChecked(AdvancedFilter.TEMPERATURE in current_panel.advanced_options)

    def add_selection_to_project(self, project: Project):
        edit_dialog = ProjectEditDialog(self.context, project=project, parent=self)
        selection = self.get_current_search_panel().get_selected_files()
        files_to_add = File.remove_already_mapped(project, selection)
        for file in files_to_add:
            edit_dialog.add_file(ProjectFile(project=project, file=file))
        edit_dialog.refresh_table()
        result = edit_dialog.exec()
        if result == QDialog.Accepted and self.projects_window:
            self.projects_window.populate_table()

    def create_project_for_folder(self, root_and_paths: List[RootAndPath]):
        if not root_and_paths:
            return
        root_and_path = root_and_paths[0]
        project = Project(name=root_and_path.path)
        edit_dialog = ProjectEditDialog(self.context, project=project, parent=self)
        temp_criteria = deepcopy(self.get_current_search_panel().search_criteria)
        temp_criteria.paths = [root_and_path]
        query = (File.select(File, LibraryRoot, Image)
                 .join_from(File, LibraryRoot)
                 .join_from(File, Image))
        query = Image.apply_search_criteria(query, temp_criteria)
        for file in list(query.execute()):
            edit_dialog.add_file(ProjectFile(project=project, file=file))
        edit_dialog.refresh_table()
        result = edit_dialog.exec()
        if result == QDialog.Accepted and self.projects_window:
            self.projects_window.populate_table()

    def populate_recent_projects(self):
        self.menuAddToRecentProject.clear()
        projects = Project.find_recent()
        if projects:
            for project in projects:
                project_action = self.menuAddToRecentProject.addAction(project.name)
                project_action.setData(project)
                project_action.triggered.connect(self.on_add_to_project_action)
        else:
            self.menuAddToRecentProject.addAction(self.placeholderNoRecentProject)

    def populate_nearby_projects(self):
        self.menuAddToNearbyProject.clear()
        files = self.get_current_search_panel().get_selected_files()
        coord = next((coord for f in files if hasattr(f, 'image') and
                      f.image and (coord := f.image.get_sky_coord()) is not None), None)
        projects = Project.find_nearby(coord)
        if projects:
            for project in projects:
                project_action = self.menuAddToNearbyProject.addAction(project.name)
                project_action.setData(project)
                project_action.triggered.connect(self.on_add_to_project_action)
        else:
            self.menuAddToNearbyProject.addAction(self.placeholderNoNearbyProject)

    def on_add_to_project_action(self):
        action: QAction = self.sender()
        project = action.data() if action.data() else Project()
        self.add_selection_to_project(project)

    def on_show_project_menu(self):
        current_panel = self.get_current_search_panel()
        files = current_panel.get_selected_files()
        has_selection = files is not None and len(files) > 0
        self.menuAddToRecentProject.setEnabled(has_selection)
        self.actionAddToNewProject.setEnabled(has_selection)
        selection_coord = next((coord for f in files if hasattr(f, 'image') and
                                f.image and (coord := f.image.get_sky_coord()) is not None), None)
        self.menuAddToNearbyProject.setEnabled(has_selection and selection_coord is not None)

    def populate_search_details(self):
        self.menuSearch_Details.clear()
        current_tab = self.get_current_search_panel()
        current_tab.visibility_controller.build_menu(self.menuSearch_Details)

    def populate_project_details(self):
        self.menuProject_Details.clear()
        if self.projects_window:
            controller = self.projects_window.visibility_controller
            controller.build_menu(self.menuProject_Details)


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

    def __init__(self, context, files: List[str] = None, roots: List[LibraryRoot] = None):
        super().__init__()
        self.context = context
        self.files = files
        self.roots = roots
        self.importer = Importer(context,
                                 context.settings.get_bad_file_patterns(),
                                 context.settings.get_bad_dir_patterns())

    def run(self):
        if self.files:
            self.import_files()
        else:
            self.import_roots()

        # Signal that we're done
        self.finished.emit()

    def import_roots(self):
        for changes_per_library in self.importer.import_roots(self.roots):
            self.context.status_reporter.update_status(
                f"Files removed {len(changes_per_library.removed_files)} " +
                f"added {len(changes_per_library.new_files)} " +
                f"changed {len(changes_per_library.changed_files)}")
            changes_per_library.apply_all()
            update_fits_header_cache(changes_per_library, self.context.status_reporter, self.context.settings)
        check_missing_header_cache(self.context.status_reporter, self.context.settings)

    def import_files(self):
        changes = self.importer.import_selection(self.files)
        changes.apply_all()
        update_fits_header_cache(changes, self.context.status_reporter, self.context.settings)

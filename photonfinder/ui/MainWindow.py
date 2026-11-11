import logging
import time
from copy import deepcopy
from functools import partial
from typing import List

from PySide6.QtCore import Qt, QByteArray, QThread, Signal, QObject, QSize, QEvent
from PySide6.QtGui import QIcon, QPalette, QAction, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QMainWindow, QMessageBox, QTabWidget,
                               QToolButton, QWidget)

from photonfinder import filter_catalogue, library_query
from photonfinder.core import ApplicationContext, LibraryBusyError, StatusReporter, backup_database, \
    library_busy_message
from photonfinder.filesystem import Importer, ScanCancelled, update_fits_header_cache, check_missing_header_cache, \
    repair_truncated_header_cache
from photonfinder.models import NO_PROJECT, SearchCriteria, Project, File, ProjectFile, RootAndPath, LibraryRoot
from photonfinder.pictures import PICTURE_TYPE
from .AboutDialog import AboutDialog
from .BackgroundLoader import LocalTimeWorker
from .ImageViewerWindow import ImageViewerWindow
from .LibraryRootDialog import LibraryRootDialog
from .LibraryTreeModel import AllLibrariesNode, LibraryRootNode
from .LogWindow import LogWindow
from .ProjectEditDialog import ProjectEditDialog
from .ProjectsWindow import ProjectsWindow
from .SearchPanel import SearchPanel
from .SettingsDialog import SettingsDialog
from .ThumbnailView import ThumbnailLoader
from .common import create_colored_svg_icon, _BUILTIN_PRESETS
from .generated.MainWindow_ui import Ui_MainWindow
import photonfinder.ui.generated.resources_rc  # pylint: disable=unused-import  # registers the Qt resources
from .session import SessionManager, Session


class MainWindow(QMainWindow, Ui_MainWindow):
    SCAN_WRITER = "Library scan"
    SCAN_STOP_TIMEOUT_MS = 30_000
    tabs_changed = Signal(list)
    projects_window: ProjectsWindow | None

    def __init__(self, app: QApplication, context: ApplicationContext, parent=None,
                 enable_mcp: bool = True):
        super(MainWindow, self).__init__(parent)
        self.setupUi(self)
        self._diagnose_dialog = None
        self.context = context
        self.app = app
        self.session_manager = SessionManager(context.get_session_file())
        self.scan_worker = None  # Initialize scan_worker attribute
        self.projects_window = None
        self.image_viewer: ImageViewerWindow | None = None
        self.thumbnails = ThumbnailLoader(context.thumbnail_dir, self)  # shared by the tabs' tile views

        self.menuProject_Details.removeAction(self.actionLoading_2)
        self.menuSearch_Details.removeAction(self.actionLoading)

        # Set the window icon from the resource file
        self.setWindowIcon(QIcon(":/icon.png"))
        self.setWindowTitle(f"PhotonFinder - {context.database_path}")

        text_color = self.palette().color(QPalette.WindowText)
        size = QSize(24, 24)
        self.actionManage_Projects.setIcon(create_colored_svg_icon(":/res/stack.svg", size, text_color))
        self.action_New_Tab.setIcon(create_colored_svg_icon(":/res/window-plus.svg", size, text_color))
        self.action_Manage_Libraries.setIcon(create_colored_svg_icon(":/res/hdd.svg", size, text_color))
        self.actionOpen_File.setIcon(create_colored_svg_icon(":/res/card-image.svg", size, text_color))
        self.action_Open_Database.setIcon(create_colored_svg_icon(":/res/database.svg", size, text_color))
        self.action_Export_Data.setIcon(create_colored_svg_icon(":/res/send-plus.svg", size, text_color))
        self.actionCatalog_Report.setIcon(create_colored_svg_icon(":/res/table.svg", size, text_color))

        self.actionExposure.setIcon(create_colored_svg_icon(":/res/clock.svg", size, text_color))
        self.actionCoordinates.setIcon(create_colored_svg_icon(":/res/crosshair.svg", size, text_color))
        self.actionDate.setIcon(create_colored_svg_icon(":/res/calendar3.svg", size, text_color))
        self.actionNight.setIcon(create_colored_svg_icon(":/res/moon.svg", size, text_color))
        self.actionGroupSimilarFrames.setIcon(create_colored_svg_icon(":/res/box-seam.svg", size, text_color))
        self.actionThumbnails.setIcon(create_colored_svg_icon(":/res/grid.svg", size, text_color))
        self.actionTelescope.setIcon(create_colored_svg_icon(":/res/telescope-icon-original.svg", size, text_color))
        self.actionBinning.setIcon(create_colored_svg_icon(":/res/border-outer.svg", size, text_color))
        self.actionGain.setIcon(create_colored_svg_icon(":/res/exposure.svg", size, text_color))
        self.actionTemperature.setIcon(create_colored_svg_icon(":/res/thermometer-half.svg", size, text_color))
        self.actionPlateSolved.setIcon(create_colored_svg_icon(":/res/stars.svg", size, text_color))
        self.actionPlate_solve_files.setIcon(create_colored_svg_icon(":/res/stars.svg", size, text_color))
        self.actionImageSize.setIcon(create_colored_svg_icon(":/res/crop.svg", size, text_color))
        self.actionPlateScale.setIcon(create_colored_svg_icon(":/res/rulers.svg", size, text_color))
        self.actionImageQuality.setIcon(create_colored_svg_icon(":/res/bar-chart-line-fill.svg", size, text_color))
        self.action_filter_no_project.setIcon(create_colored_svg_icon(":/res/stack.svg", size, text_color, add_slash=True))

        # hide the dock initially
        self.dockWidget.hide()
        self._plate_solve_fits_selection = True  # the selected frame is one that can be solved
        # The actions that start a library writer, with the tooltips they show when it's free.
        self._writer_action_tips = {action: action.toolTip() for action in (
            self.action_Scan_Libraries, self.actionCompress_files, self.actionPlate_solve_files)}

        self.reporter = UIStatusReporter()
        self.reporter.on_message.connect(self.statusBar().showMessage)
        context.set_status_reporter(self.reporter)
        btn = QToolButton(self)
        btn.setDefaultAction(self.action_View_Log)
        self.statusBar().addPermanentWidget(btn)
        self.local_times = LocalTimeWorker(context)
        self._local_times_wanted = False  # started while the library was busy: retried when it's free
        self.connect_signals()
        self.restore_window_layout()
        self.restore_session()
        self.setAcceptDrops(True)
        self.update_local_times()

        self.mcp_controller = None
        if enable_mcp:
            self.start_mcp_server()

    def start_mcp_server(self):
        """Host the MCP server so agents can reach this library.

        Loopback-only and always on: the `photonfinder-mcp` stub an agent's client spawns
        has nothing to talk to otherwise, and starting it is what lets that stub avoid
        launching a second copy of the application. The configured port only matters to
        clients that connect over HTTP directly; the stub finds whichever port was bound.
        Only a sandboxed instance (``--data-dir``, tests) runs without it.
        """
        try:
            from photonfinder.mcp_server import McpServerController
            self.mcp_controller = McpServerController(
                self.context, requested_port=self.context.settings.get_mcp_port())
            self.mcp_controller.start()
        except Exception as e:
            logging.error(f"Failed to start MCP server: {e}", exc_info=True)
            self.mcp_controller = None

    def connect_signals(self):
        self.tabWidget.tabCloseRequested.connect(self.close_search_tab)
        self.action_Export_Data.triggered.connect(self.export_data)
        self.action_New_Tab.triggered.connect(self.new_search_tab)
        self.action_Close_Tab.triggered.connect(self.close_current_search_tab)
        self.action_Exit.triggered.connect(self.close)
        self.action_Manage_Libraries.triggered.connect(self.manage_library_roots)
        self.action_Settings.triggered.connect(self.open_settings_dialog)
        self.action_Scan_Libraries.triggered.connect(self.scan_libraries)
        self.action_View_Log.triggered.connect(self.view_log)
        self.actionDiagnose_Quality.triggered.connect(self.open_diagnose_quality)
        self.action_Create_Backup.triggered.connect(self.create_backup)
        self.action_Create_Database.triggered.connect(self.create_database)
        self.action_Open_Database.triggered.connect(self.open_database)
        self.actionDuplicate_Tab.triggered.connect(self.dup_search_tab)
        self.action_Rename_Tab.triggered.connect(self.rename_current_search_tab)
        self.tabWidget.tabBar().tabBarDoubleClicked.connect(self.rename_search_tab)
        self.actionFind_matching_darks.triggered.connect(self.find_matching_darks)
        self.actionFind_matching_flats.triggered.connect(self.find_matching_flats)
        self.action_About.triggered.connect(self.show_about_dialog)
        self.tabWidget.currentChanged.connect(self.on_tab_switch)
        self.actionOpen_File.triggered.connect(self.open_selected_file)
        self.actionShow_location.triggered.connect(self.show_file_location)
        self.actionShow_Details.triggered.connect(self.show_file_details)
        self.actionSelect_path.triggered.connect(self.select_path_in_tree)
        self.actionPlate_solve_files.triggered.connect(self.plate_solve_files)
        self.actionCompress_files.triggered.connect(self.compress_files)
        self.actionList_Files.triggered.connect(self.report_list_files)
        self.actionMetadata_Report.triggered.connect(self.report_metadata)
        self.actionTelescopius_List.triggered.connect(self.report_telescopius_list)
        self.actionTarget_List_Report.triggered.connect(self.report_targets)
        self.actionCatalog_Report.triggered.connect(self.report_catalog)
        self.actionManage_Projects.triggered.connect(self.show_projects_window)
        self.menuAddToNearbyProject.aboutToShow.connect(self.populate_nearby_projects)
        self.menuAddToRecentProject.aboutToShow.connect(self.populate_recent_projects)
        self.menuProject.aboutToShow.connect(self.on_show_project_menu)
        self.actionAddToNewProject.triggered.connect(self.on_add_to_project_action)
        self.dockWidget.visibilityChanged.connect(self.show_projects_window)
        self.action_filter_no_project.triggered.connect(
            lambda: self.get_current_search_panel().set_project_filter(NO_PROJECT))
        for action, entry in self._filter_actions().items():
            action.triggered.connect(partial(self._edit_filter, entry))
        self.actionGroupSimilarFrames.triggered.connect(lambda checked: self.get_current_search_panel().set_grouped(checked))
        self.actionThumbnails.triggered.connect(lambda checked: self.get_current_search_panel().set_thumbnail_mode(checked))
        self.menuSearch_Details.aboutToShow.connect(self.populate_search_details)
        self.menuProject_Details.aboutToShow.connect(self.populate_project_details)
        self.menu_File.setToolTipsVisible(True)
        self.menu_File.aboutToShow.connect(self._update_database_actions)
        self.menu_File.aboutToShow.connect(self.update_writer_actions)
        self.menu_Tools.setToolTipsVisible(True)
        self.menu_Tools.aboutToShow.connect(self.update_writer_actions)
        self.context.signal_bus.library_write_lock_changed.connect(self._on_library_write_lock_changed)

    def closeEvent(self, event):
        """The database closes after this, so everything using it has to stop here."""
        try:
            try:
                self.save_session()
            except Exception as e:
                logging.error(f"Could not save the session: {e}", exc_info=True)
            try:
                self.save_window_layout()
            except Exception as e:
                logging.error(f"Could not save the window layout: {e}", exc_info=True)
            self._stop_scan()
            if not self.local_times.cancel_and_wait():
                logging.error("Computing local times did not stop in time")
            if self.mcp_controller is not None:
                self.mcp_controller.stop()
            self.thumbnails.shutdown()
        finally:
            event.accept()  # Accept the event to actually close the window

    def _stop_scan(self):
        """Cancel a running scan and wait for it, as the window closes."""
        worker = self.scan_worker
        if worker is None:
            return
        self.context.status_reporter.update_status("Stopping the library scan...")
        worker.finished.disconnect(self._scan_finished)
        worker.cancel()
        if not worker.wait(self.SCAN_STOP_TIMEOUT_MS):  # stuck in the filesystem, say on a dead network share
            logging.error("The library scan did not stop in time")
            return
        self.context.writers.end(self.SCAN_WRITER)
        self.context.library_write_lock.release()
        worker.deleteLater()
        self.scan_worker = None

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
            self._start_scan(lambda: LibraryScanWorker(self.context, file_paths))
        else:
            event.ignore()

    def save_window_layout(self):
        """Remember the window's size, position and maximized state, and the toolbar and dock."""
        settings = self.context.settings
        settings.set_main_window_geometry(self.saveGeometry().toBase64().data().decode("ascii"))
        settings.set_main_window_state(self.saveState().toBase64().data().decode("ascii"))

    def restore_window_layout(self):
        """Put the window back as it was closed. Qt keeps a restored geometry on a visible screen."""
        settings = self.context.settings
        geometry = settings.get_main_window_geometry()
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        state = settings.get_main_window_state()
        if state:
            self.restoreState(QByteArray.fromBase64(state.encode("ascii")))

    def restore_session(self):
        try:
            sessions = self.session_manager.load_sessions()
        except Exception as e:
            logging.error(e, exc_info=True)
            sessions = []
        for session in sessions:
            try:
                panel = self.new_search_tab(session.criteria, hidden_columns=session.hidden_columns,
                                            thumbnails=session.thumbnails)
                if session.title_is_custom:
                    panel.set_custom_title(session.title)
                else:
                    panel.set_title(session.title)
            except Exception as e:
                logging.error(f"Could not restore tab {session.title!r}: {e}", exc_info=True)
        if len(self.get_search_panels()) == 0:
            self.new_search_tab()

    def save_session(self):
        sessions = [Session(
            criteria=panel.search_criteria,
            hidden_columns=panel.visibility_controller.save_visibility(),
            title=panel.title,
            title_is_custom=panel.title_is_custom,
            thumbnails=panel.thumbnail_mode,
        ) for panel in self.get_search_panels()]
        self.session_manager.save_sessions(sessions)

    def new_search_tab(self, search_criteria=None, hidden_columns=None, thumbnails=None) -> SearchPanel:
        """A new tab; what it doesn't say (columns, table or thumbnails) it takes from the current tab.
        With no tab open (the first start, the last tab closed, another library opened) it shows
        thumbnails."""
        current = self.get_current_search_panel()
        if hidden_columns is None:
            if current is not None:
                hidden_columns = current.visibility_controller.save_visibility()
            else:
                hidden_columns = ",".join(_BUILTIN_PRESETS["Standard"])
        if thumbnails is None:
            thumbnails = current.thumbnail_mode if current is not None else True
        panel = SearchPanel(self.context, parent=self.tabWidget, mainWindow=self)
        tab = self.tabWidget.addTab(panel, "Loading")
        if search_criteria:
            panel.apply_search_criteria(search_criteria)
        if hidden_columns:
            panel.visibility_controller.load_visibility(hidden_columns)
        panel.set_thumbnail_mode(thumbnails)
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

    def rename_current_search_tab(self):
        self.rename_search_tab(self.tabWidget.currentIndex())

    def rename_search_tab(self, index):
        if index < 0:
            return
        panel = self.tabWidget.widget(index)
        assert isinstance(panel, SearchPanel)
        new_title, ok = QInputDialog.getText(self, "Rename Tab", "Tab name:", text=panel.get_title())
        if ok and new_title.strip():
            panel.set_custom_title(new_title.strip())

    def close_current_search_tab(self):
        self.close_search_tab(self.tabWidget.currentIndex())

    def close_search_tab(self, index):
        self._remove_search_tab(index)
        if self.tabWidget.count() == 0:
            self.new_search_tab()
        else:
            self.tabs_changed.emit(self.get_search_panels())

    def _remove_search_tab(self, index):
        widget = self.tabWidget.widget(index)
        assert isinstance(widget, SearchPanel)
        self.tabWidget.removeTab(index)
        widget.destroy()
        widget.deleteLater()

    def _switch_database(self, file_path):
        """Close every tab, switch to *file_path* and open one fresh tab on it.

        Tabs are removed without close_search_tab's "never leave zero tabs" rule, which
        would otherwise start a new panel (and its queries) on the database being closed.
        If the new database can't be opened, the previous one is reopened before re-raising.
        Refused with LibraryBusyError while something could write to the current library,
        since a job still running would carry on writing its rows to the new one.
        """
        self.local_times.cancel_and_wait()  # background upkeep: picked up again on the new library
        blockers = self._database_switch_blockers()
        if blockers:
            self.update_local_times()
            raise LibraryBusyError(self._database_busy_message(blockers))
        previous_path = self.context.database_path
        with self.context.writers.switching():
            while self.tabWidget.count() > 0:
                self._remove_search_tab(0)
            try:
                self.context.switch_database(file_path)
            except Exception:
                logging.error(f"Could not open {file_path}, reopening {previous_path}", exc_info=True)
                self.context.switch_database(previous_path)
                raise
            finally:
                self.setWindowTitle(f"PhotonFinder - {self.context.database_path}")
                self.new_search_tab()
                self.reload_library_roots_in_all_panels()
                self.update_local_times()

    def _database_switch_blockers(self) -> list[str]:
        """What stops the library being switched: writers running in this process, and open
        windows that can start one, which declare it with a writer_window_label.

        Modal dialogs don't need the label, as the menu can't be reached while one is open.
        Nor do the grid and the Projects panel: their writes are finished before the event
        loop runs again, so one can't be in progress when a switch is requested.
        """
        windows = [widget.writer_window_label for widget in self.findChildren(QWidget)
                   if getattr(widget, 'writer_window_label', None) and widget.isVisible()]
        return list(dict.fromkeys(self.context.writers.running() + windows))

    @staticmethod
    def _database_busy_message(blockers: list[str]) -> str:
        return f"Wait for or close these first: {', '.join(blockers)}."

    def _update_database_actions(self):
        blockers = self._database_switch_blockers()
        tip = self._database_busy_message(blockers) if blockers else ""
        for action in (self.action_Create_Database, self.action_Open_Database):
            action.setEnabled(not blockers)
            action.setToolTip(tip)
            action.setStatusTip(tip)

    def _on_library_write_lock_changed(self):
        """Taken or released in this process. Windows that start a writer listen for this too."""
        self.update_writer_actions()
        if self._local_times_wanted and not self.context.library_write_lock.busy_with():
            self.update_local_times()

    def update_local_times(self):
        """Bring the library's local times up to date in the background, if anything needs it.
        When the library is busy, it's tried again once the write lock is released here."""
        self._local_times_wanted = False
        try:
            self._local_times_wanted = self.local_times.start() is not None
        except Exception as e:  # pylint: disable=broad-exception-caught  # upkeep: never in the way
            logging.error(f"Could not start computing local times: {e}", exc_info=True)

    def update_writer_actions(self) -> str | None:
        """Disable the actions that start a library writer while one holds the write lock,
        here or in another window, and return what holds it.

        Another window's writer can't be seen without asking the lock, so this runs when a
        menu opens or the window is activated, and when this process takes or releases it.
        Starting still asks the lock, as the state shown here can be out of date.
        """
        busy = self.context.library_write_lock.busy_with()
        tip = library_busy_message(busy) if busy else ""
        self.action_Scan_Libraries.setEnabled(not busy)
        self.actionCompress_files.setEnabled(not busy)
        self.actionPlate_solve_files.setEnabled(not busy and self._plate_solve_fits_selection)
        for action, own_tip in self._writer_action_tips.items():
            action.setToolTip(tip or own_tip)
            action.setStatusTip(tip)
        return busy

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.update_writer_actions()

    def _refuse_database_switch(self, title: str) -> bool:
        """The disabled menu items only update when the menu opens, so check again here."""
        blockers = self._database_switch_blockers()
        if blockers:
            QMessageBox.warning(self, title, self._database_busy_message(blockers))
        return bool(blockers)

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

    def select_project_in_window(self, project):
        if not self.projects_window:
            self.show_projects_window(True)
            self.projects_window.select_project_after_load(project)
        else:
            self.show_projects_window(True)
            self.projects_window.select_project(project)

    def clear_projects_window(self):
        projects_window = self.projects_window
        projects_window.save_cols()
        self.projects_window = None
        projects_window.deleteLater()
        self.menuProject_Details.setEnabled(False)

    def open_settings_dialog(self):
        """
        Open the settings dialog.
        """
        zone = self.context.settings.get_obs_timezone()
        dialog = SettingsDialog(self.context, parent=self, thumbnails=self.thumbnails)
        if dialog.exec() and self.context.settings.get_obs_timezone() != zone:
            self.local_times.cancel_and_wait()  # restarted in the new zone
            self.update_local_times()

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

        self._start_scan(lambda: LibraryScanWorker(self.context, roots=roots))

    def _start_scan(self, create_worker) -> bool:
        """Start a library scan unless another library writer (such as Compress Files or a
        plate solve, here or in another PhotonFinder window) holds the write lock. The lock is
        held until the scan finishes."""
        if not self.context.library_write_lock.acquire(blocking=False, holder=self.SCAN_WRITER):
            busy = self.context.library_write_lock.busy_with() or "Another job"
            self.context.status_reporter.update_status(f"Library scan not started: {library_busy_message(busy)}")
            return False
        self.context.writers.begin(self.SCAN_WRITER)
        try:
            self.scan_worker = create_worker()
        except Exception:
            self.context.writers.end(self.SCAN_WRITER)
            self.context.library_write_lock.release()
            raise
        self.scan_worker.finished.connect(self._scan_finished)
        self.scan_worker.start()
        return True

    def _scan_finished(self):
        """Called when the scan is finished."""
        self.context.writers.end(self.SCAN_WRITER)
        self.context.library_write_lock.release()
        self.context.status_reporter.update_status("Library scan complete.")

        # Clean up the worker thread
        if self.scan_worker:
            self.scan_worker.deleteLater()
            self.scan_worker = None

        for panel in self.get_search_panels():
            panel.library_tree_model.refresh_loaded_paths()
            panel.update_search_criteria()

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

    def _filter_actions(self) -> dict:
        """The Search menu's filter actions, each with the catalogue entry it edits."""
        return {
            self.actionExposure: filter_catalogue.EXPOSURE, self.actionDate: filter_catalogue.DATES,
            self.actionNight: filter_catalogue.NIGHT,
            self.actionCoordinates: filter_catalogue.COORDINATES, self.actionTelescope: filter_catalogue.TELESCOPE,
            self.actionBinning: filter_catalogue.BINNING, self.actionGain: filter_catalogue.GAIN,
            self.actionTemperature: filter_catalogue.TEMPERATURE, self.actionPlateSolved: filter_catalogue.PLATE_SOLVED,
            self.actionImageSize: filter_catalogue.IMAGE_SIZE, self.actionPlateScale: filter_catalogue.PLATE_SCALE,
            self.actionImageQuality: filter_catalogue.IMAGE_QUALITY,
            self.actionQualityDiagnosis: filter_catalogue.QUALITY_DIAGNOSIS,
            self.actionHeader_Text: filter_catalogue.HEADER_TEXT,
        }

    def _edit_filter(self, entry):
        self.get_current_search_panel().edit_filter(entry)

    def open_diagnose_quality(self):
        if self._diagnose_dialog is not None:
            self._diagnose_dialog.raise_()
            self._diagnose_dialog.activateWindow()
            return
        from .DiagnoseQualityDialog import DiagnoseQualityDialog
        dialog = DiagnoseQualityDialog(self.context, self, parent=self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.finished.connect(self._on_diagnose_quality_closed)
        self._diagnose_dialog = dialog
        dialog.show()

    def _on_diagnose_quality_closed(self):
        self._diagnose_dialog = None

    def refresh_current_search(self):
        panel = self.get_current_search_panel()
        if panel is not None:
            panel.refresh_data_grid()

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
        if self._refuse_database_switch("Create Database"):
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Create Database",
                                                   "", "SQLite Database (*.db);;All Files (*)")

        if not file_path:
            return  # User cancelled

        try:
            self._switch_database(file_path)
            self.context.status_reporter.update_status(f"Database created and opened at {file_path}")

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
        if self._refuse_database_switch("Open Database"):
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Database",
            "",
            "SQLite Database (*.db);;All Files (*)"
        )

        if not file_path:
            return  # User cancelled

        try:
            self._switch_database(file_path)
            self.context.status_reporter.update_status(f"Database opened at {file_path}")
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
        self.new_search_tab(SearchCriteria.find_dark(selected_image))

    def find_matching_flats(self):
        current_panel = self.get_current_search_panel()
        selected_image = current_panel.get_selected_image()
        if not selected_image:
            return
        self.new_search_tab(SearchCriteria.find_flat(selected_image))

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

    def view_image(self, file, panel=None, row: int = -1, rows: list[int] | None = None,
                   annotate: bool = False, annotation_catalog: str | None = None,
                   annotation_catalog_id: str | None = None):
        """Open the file in the internal image viewer (single non-modal window)."""
        if self.image_viewer is None or not self.image_viewer.isVisible():
            self.image_viewer = ImageViewerWindow(context=self.context, parent=self)
            self.image_viewer.destroyed.connect(
                lambda: setattr(self, 'image_viewer', None)
            )
            self.image_viewer.show()
        self.image_viewer.set_nav_context(panel, row, rows)
        if annotate:
            self.image_viewer.request_annotations(annotation_catalog, annotation_catalog_id)
        self.image_viewer.load_file(file)
        self.image_viewer.raise_()
        self.image_viewer.activateWindow()

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

    def compress_files(self):
        self.get_current_search_panel().compress_files()

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

    def report_catalog(self):
        self.get_current_search_panel().report_catalog()

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
        # A picture has no header to show.
        self.actionShow_Details.setEnabled(has_selection and selected_image.image_type != PICTURE_TYPE)

        if selected_image:
            current_type = selected_image.image_type
            self.actionFind_matching_darks.setEnabled(current_type == "LIGHT" or current_type == "FLAT")
            self.actionFind_matching_flats.setEnabled(current_type == "LIGHT")
            solvable = not current_type or "LIGHT" in current_type or current_type == PICTURE_TYPE
            self._plate_solve_fits_selection = solvable
            self.actionPlate_solve_files.setEnabled(solvable and self.context.library_write_lock.holder is None)

        for action, entry in self._filter_actions().items():
            action.setChecked(entry in current_panel.advanced_options)  # no effect on the uncheckable ones
        self.actionGroupSimilarFrames.setChecked(current_panel.search_criteria.one_per_group)
        self.actionThumbnails.setChecked(current_panel.thumbnail_mode)

    def add_selection_to_project(self, project: Project):
        edit_dialog = ProjectEditDialog(self.context, project=project, parent=self)
        # A project collects files: a file whose layers are both selected is added once.
        selection = self.get_current_search_panel().get_selected_distinct_files()
        files_to_add = File.remove_already_mapped(project, selection)
        for file in files_to_add:
            edit_dialog.add_file(ProjectFile(project=project, file=file))
        edit_dialog.show()
        edit_dialog.refresh_table()

    def create_project_for_folder(self, root_and_paths: List[RootAndPath]):
        if not root_and_paths:
            return
        root_and_path = root_and_paths[0]
        project = Project(name=root_and_path.path)
        edit_dialog = ProjectEditDialog(self.context, project=project, parent=self)
        temp_criteria = deepcopy(self.get_current_search_panel().search_criteria)
        temp_criteria.paths = [root_and_path]
        for file in library_query.distinct_files(library_query.matching_files(temp_criteria, require_image=True)):
            edit_dialog.add_file(ProjectFile(project=project, file=file))
        edit_dialog.show()
        edit_dialog.refresh_table()

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
        if not bulk and message:
            self.log_messages.append(message)

    def get_log_messages(self):
        """Return the list of log messages."""
        return self.log_messages


class LibraryScanWorker(QThread):
    """Worker thread for scanning libraries."""
    change_list_ready = Signal(object)  # Signal emitted when a change list is ready

    def __init__(self, context, files: List[str] = None, roots: List[LibraryRoot] = None):
        super().__init__()
        self.context = context
        self.files = files
        self.roots = roots
        self.importer = Importer(context,
                                 context.settings.get_bad_file_patterns(),
                                 context.settings.get_bad_dir_patterns())

    def cancel(self):
        self.importer.cancel()

    def _cancelled(self) -> bool:
        return self.importer.cancelled

    def run(self):
        try:
            if self.files:
                self.import_files()
            else:
                self.import_roots()
        except ScanCancelled:
            logging.info("Library scan cancelled")

    def import_roots(self):
        for changes_per_library in self.importer.import_roots(self.roots):
            self.context.status_reporter.update_status(
                f"Files removed {len(changes_per_library.removed_files)} " +
                f"added {len(changes_per_library.new_files)} " +
                f"changed {len(changes_per_library.changed_files)}")
            changes_per_library.apply_all()
            update_fits_header_cache(changes_per_library, self.context.status_reporter, self.context.settings,
                                     self._cancelled)
        check_missing_header_cache(self.context.status_reporter, self.context.settings, self._cancelled)
        repair_truncated_header_cache(self.context.status_reporter, self.context.settings, self._cancelled)

    def import_files(self):
        changes = self.importer.import_selection(self.files)
        changes.apply_all()
        update_fits_header_cache(changes, self.context.status_reporter, self.context.settings, self._cancelled)

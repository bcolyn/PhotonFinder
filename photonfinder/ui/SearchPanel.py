import json
import logging
import typing
from datetime import datetime, timezone
from enum import Enum
from logging import DEBUG

import astropy.units as u
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtWidgets import *
from astropy.coordinates import SkyCoord

from photonfinder.core import ApplicationContext, decompress
from photonfinder.filesystem import Importer, header_from_xisf_dict
from photonfinder.models import SearchCriteria, CORE_MODELS, Image, RootAndPath, File, FitsHeader, Project, NO_PROJECT
from photonfinder.platesolver import SolverType
from .BackgroundLoader import SearchResultsLoader, GenericControlLoader, PlateSolveTask, FileListTask
from .DateRangeDialog import DateRangeDialog
from .HeaderDialog import HeaderDialog
from .LibraryTreeModel import LibraryTreeModel, LibraryRootNode, PathNode
from .MetadataReportDialog import MetadataReportDialog
from .ProgressDialog import ProgressDialog
from .common import _format_ra, _format_dec, _format_date, _format_file_size, _format_timestamp, ensure_header_widths
from .generated.SearchPanel_ui import Ui_SearchPanel

EMPTY_LABEL = "<empty>"
RESET_LABEL = "<any>"

ROWID_ROLE = Qt.UserRole
SORT_ROLE = Qt.UserRole + 1


# Using the new database-backed tree model for filesystemTreeView
def _not_empty(current_text):
    return current_text != EMPTY_LABEL and current_text and current_text != RESET_LABEL


class SearchPanel(QFrame, Ui_SearchPanel):
    search_criteria_changed = Signal()

    def __init__(self, context: ApplicationContext, mainWindow: 'MainWindow', parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

        self.context = context
        self.update_in_progress = False
        self.title = "Loading"
        self.mainWindow = mainWindow
        self.search_criteria = SearchCriteria()
        self.advanced_options = dict()
        self.total_files = 0  # Track total number of files in search results
        self.pending_selections = list()  # Store pending path selections

        # Initialize the search results loader
        self.search_results_loader = SearchResultsLoader(context)
        self.search_results_loader.results_loaded.connect(self.on_search_results_loaded)
        self.combo_loader = GenericControlLoader(context)
        self.combo_loader.data_ready.connect(self.on_combo_options_loaded)
        self.refresh_combo_options()

        # Initialize the data view model
        self.data_model = QStandardItemModel(self)
        self.proxy_model = QSortFilterProxyModel()
        self.proxy_model.setSortRole(SORT_ROLE)
        self.proxy_model.setSourceModel(self.data_model)
        self.dataView.setModel(self.proxy_model)
        self.dataView.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self.dataView.doubleClicked.connect(self.on_item_double_clicked)
        self.dataView.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dataView.setContextMenuPolicy(Qt.CustomContextMenu)
        self.dataView.customContextMenuRequested.connect(self.show_context_menu)
        # Connect selection changes
        self.dataView.selectionModel().selectionChanged.connect(self.on_data_selection_changed)
        self.has_more_results = False
        self.loading_more = False

        # Initialize the library tree model
        self.library_tree_model = LibraryTreeModel(context, self)
        self.library_tree_model.reload_library_roots()
        self.library_tree_model.ready_for_display.connect(self.on_library_tree_ready)

        # Connect to the paths_loaded signal to handle pending selections
        self.library_tree_model.file_paths_loader.paths_loaded.connect(self.on_paths_loaded)

        # Set up the tree view
        self.filesystemTreeView.setModel(self.library_tree_model)
        self.filesystemTreeView.setHeaderHidden(True)
        self.filesystemTreeView.setItemsExpandable(True)
        self.filesystemTreeView.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

        # Connect UI elements to update search criteria
        self.filter_type_combo.currentTextChanged.connect(self.update_search_criteria)
        self.filter_filter_combo.currentTextChanged.connect(self.update_search_criteria)
        self.filter_cam_combo.currentTextChanged.connect(self.update_search_criteria)
        self.filter_name_text.textChanged.connect(self.update_search_criteria)
        self.filter_fname_text.textChanged.connect(self.update_search_criteria)
        self.checkBox.toggled.connect(self.update_search_criteria)
        self.dataView.horizontalHeader().sortIndicatorChanged.connect(self.on_sort_requested)

        # insert the toolbar
        self.toolbar = QToolBar()
        self.filter_layout.insertWidget(1, self.toolbar)
        self.toolbar.addAction(mainWindow.actionExposure)
        self.toolbar.addAction(mainWindow.actionCoordinates)
        self.toolbar.addAction(mainWindow.actionDate)
        self.toolbar.addAction(mainWindow.actionTelescope)
        self.toolbar.addAction(mainWindow.actionBinning)
        self.toolbar.addAction(mainWindow.actionGain)
        self.toolbar.addAction(mainWindow.actionTemperature)

    def showEvent(self, event, /):
        super().showEvent(event)
        if self.data_model.rowCount() == 0:
            self.refresh_data_grid()

    def on_library_tree_ready(self):
        all_libraries_index = self.library_tree_model.index(0, 0, QModelIndex())
        self.filesystemTreeView.expand(all_libraries_index)

    def on_paths_loaded(self, library_root, paths):
        """Handle the paths_loaded signal from the FilePathsLoader."""
        if self.pending_selections:
            pending_roots = set(map(lambda p: p.root_id, self.pending_selections))
            missing_roots = set(
                filter(lambda root_id: root_id not in self.library_tree_model.loaded_library_roots, pending_roots))
            if len(missing_roots) > 0:
                return  # wait until we have all roots necessary

            # now apply pending selections
            self.filesystemTreeView.selectionModel().clearSelection()
            for pending_selection in self.pending_selections:
                self._find_and_select_node(pending_selection)
            self.pending_selections.clear()

    def on_tree_selection_changed(self, selected, deselected):
        # Get the current selection
        indexes = self.filesystemTreeView.selectionModel().selectedIndexes()
        if self.update_in_progress:
            return
        if not indexes:
            return
        self.search_criteria.paths = self.library_tree_model.get_roots_and_paths(indexes)
        self.update_search_criteria()

    def set_title(self, text: str):
        self.title = text
        self.mainWindow.set_tab_title(self, text)

    def get_title(self) -> str:
        return self.title

    def get_search_criteria(self) -> SearchCriteria:
        return self.search_criteria

    def add_filter_button_control(self, button: 'FilterButton'):
        current = self.advanced_options.get(button.filter_type, None)
        if current:
            self.remove_filter_button_control(current)
        self.advanced_options[button.filter_type] = button
        self.filter_layout.insertWidget(2, button)
        button.clicked.connect(self.remove_filter_button)

    def remove_filter_button(self):
        sender: FilterButton = self.sender()
        self.remove_filter_button_control(sender)
        self.update_search_criteria()

    def remove_filter_button_control(self, button: 'FilterButton'):
        del self.advanced_options[button.filter_type]
        self.filter_layout.removeWidget(button)
        self.mainWindow.enable_actions_for_current_tab()
        button.on_remove_filter.emit()
        button.hide()
        button.destroy()

    def get_selected_files(self) -> typing.List[File]:
        """Get the currently selected files in the data grid."""
        selected_indexes = self.dataView.selectionModel().selectedRows()
        selected_files = []

        for index in selected_indexes:
            # Get the Image object from the model
            file = self.get_file_at_row(index.row())
            if file:
                selected_files.append(file)

        return selected_files

    def get_file_at_row(self, row) -> File | None:
        """Get the Image object at the specified row."""
        if row < 0 or row >= self.data_model.rowCount():
            return None

        name_index = self.data_model.index(row, 0)
        return self.data_model.data(name_index, ROWID_ROLE)

    def refresh_data_grid(self):
        """Trigger a search with the current search criteria."""
        if self.update_in_progress:
            return

        self.context.status_reporter.update_status("Searching...")

        self.search_results_loader.search(self.search_criteria)

    def refresh_combo_options(self):
        tasks = [
            (self.filter_filter_combo, Image.load_filters),
            (self.filter_type_combo, Image.load_types),
            (self.filter_cam_combo, Image.load_cameras)
        ]
        self.combo_loader.run_tasks(tasks, self.search_criteria)

    def on_combo_options_loaded(self, target: QComboBox, data: typing.List[str]):
        logging.log(DEBUG, f"data for {target.objectName()}: {data}")
        self.update_in_progress = True
        current_text = target.currentText()
        target.clear()
        target.addItem(RESET_LABEL)
        for datum in data:
            if datum == "" or datum is None:
                target.addItem(EMPTY_LABEL)
            else:
                target.addItem(datum)

        # Calculate appropriate width based on the longest item
        fm = target.fontMetrics()

        # Get width of the longest item
        max_width = max([fm.horizontalAdvance(item) for item in data]) if data else 0
        # Add some padding to the width
        padding = 30  # Add padding for scroll bar and margins

        # Set the minimum width for the popup view
        target.view().setMinimumWidth(max_width + padding)

        # Set the minimum width for the combo box itself
        # We add extra padding for the dropdown arrow button
        abs_min = 120
        target.setMinimumWidth(max(abs_min, max_width + padding + 30))

        if _not_empty(current_text) and not current_text in data:
            target.addItem(current_text)

        target.setCurrentText(current_text)
        self.update_in_progress = False

    def on_data_selection_changed(self, selected, deselected):
        """Handle selection changes in the data grid."""
        # Get the number of selected rows
        selected_count = len(self.dataView.selectionModel().selectedRows())

        # Update the status bar with the selection information
        if selected_count > 0:
            self.context.status_reporter.update_status(f"{selected_count} files out of {self.total_files} selected")

        self.mainWindow.enable_actions_for_current_tab()

    def on_sort_requested(self, logical_index: int, order: Qt.SortOrder):
        if logical_index < 0:
            # step 0: reset
            self.search_criteria.sorting_index = None
            self.search_criteria.sorting_desc = False
        elif self.search_criteria.sorting_index != logical_index:
            # step 1: if we're sorting a new column, start with asc order
            self.search_criteria.sorting_index = logical_index
            self.search_criteria.sorting_desc = False
        elif not self.search_criteria.sorting_desc:
            # step 2: if we're sorting the same colum, switch from asc to desc
            self.search_criteria.sorting_index = logical_index
            self.search_criteria.sorting_desc = True
        else:
            # step 3: if we're sorting the same column and we're already desc reset sorting
            self.search_criteria.sorting_index = None
            self.search_criteria.sorting_desc = False

        if self.has_more_results:  # not all data is loaded, we need to let the database do the sorting
            self.refresh_data_grid()
        else:
            self.update_sort_indicator()

    def update_sort_indicator(self):
        self.dataView.horizontalHeader().sortIndicatorChanged.disconnect(self.on_sort_requested)
        if self.search_criteria.sorting_index is None:
            self.dataView.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        else:
            order = Qt.SortOrder.DescendingOrder if self.search_criteria.sorting_desc else Qt.SortOrder.AscendingOrder
            self.dataView.horizontalHeader().setSortIndicator(self.search_criteria.sorting_index, order)
        self.dataView.horizontalHeader().sortIndicatorChanged.connect(self.on_sort_requested)

    def on_search_results_loaded(self, results, page, total_files, has_more):
        """Handle search results loaded from the database."""
        self.has_more_results = has_more

        # If this is a new search (data model is empty), reset the total files counter
        if page == 0:
            self.data_model.clear()
            self.data_model.setHorizontalHeaderLabels([
                "File name", "Type", "Filter", "Exposure", "Gain", "Offset", "Binning", "Set Temp",
                "Camera", "Telescope", "Object", "Observation Date", "Path", "Size", "Modified", "RA", "DEC", "Solved"
            ])

            # Reset total files counter when starting a new search
            self.total_files = total_files
            # Update our tab title
            if self.search_criteria.is_empty():
                self.set_title(f"{self.total_files} files (All)")
            else:
                self.set_title(f"{self.total_files} files [{str(self.search_criteria)}]")
            # clear the status bar
            self.context.status_reporter.update_status("")

        # Reset loading_more flag after processing
        self.loading_more = False

        # Add results to the data model

        for file in results:
            # Create row items
            name_item = QStandardItem(file.name)
            name_item.setData(file.name.lower(), SORT_ROLE)
            path_item = QStandardItem(file.path)
            path_item.setData(file.path.lower(), SORT_ROLE)

            # Format size (convert bytes to KB, MB, etc.)
            size_str = _format_file_size(file.size)
            size_item = QStandardItem(size_str)
            size_item.setData(file.size, SORT_ROLE)

            # Get image data if available
            type_item = QStandardItem("")
            filter_item = QStandardItem("")
            exposure_item = QStandardItem("")
            gain_item = QStandardItem("")
            offset_item = QStandardItem("")
            binning_item = QStandardItem("")
            set_temp_item = QStandardItem("")
            camera_item = QStandardItem("")
            telescope_item = QStandardItem("")
            object_item = QStandardItem("")
            date_obs_item = QStandardItem("")
            ra_item = QStandardItem("")
            dec_item = QStandardItem("")
            solved_item = QStandardItem("True" if hasattr(file, 'has_wcs') and file.has_wcs else "False")
            solved_item.setData(solved_item.text(), SORT_ROLE)

            localtime: datetime
            try:
                if hasattr(file, 'image') and file.image:
                    if file.image.image_type is not None:
                        type_item.setText(file.image.image_type)
                        type_item.setData(file.image.image_type, SORT_ROLE)
                    if file.image.filter is not None:
                        filter_item.setText(file.image.filter)
                        filter_item.setData(file.image.filter, SORT_ROLE)
                    if file.image.exposure is not None:
                        exposure_item.setText(str(file.image.exposure))
                        exposure_item.setData(file.image.exposure, SORT_ROLE)
                    if file.image.gain is not None:
                        gain_item.setText(str(file.image.gain))
                        gain_item.setData(file.image.gain, SORT_ROLE)
                    if file.image.offset is not None:
                        offset_item.setText(str(file.image.offset))
                        offset_item.setData(file.image.offset, SORT_ROLE)
                    if file.image.binning is not None:
                        binning_item.setText(str(file.image.binning))
                        binning_item.setData(file.image.binning, SORT_ROLE)
                    if file.image.set_temp is not None:
                        set_temp_item.setText(str(file.image.set_temp))
                        set_temp_item.setData(file.image.set_temp, SORT_ROLE)
                    if file.image.camera is not None:
                        camera_item.setText(file.image.camera)
                        camera_item.setData(file.image.camera, SORT_ROLE)
                    if file.image.telescope is not None:
                        telescope_item.setText(file.image.telescope)
                        telescope_item.setData(file.image.telescope, SORT_ROLE)
                    if file.image.object_name is not None:
                        object_item.setText(file.image.object_name)
                        object_item.setData(file.image.object_name, SORT_ROLE)
                    if file.image.date_obs is not None:
                        from zoneinfo import ZoneInfo
                        utctime = file.image.date_obs.replace(tzinfo=timezone.utc)
                        localtime = utctime.astimezone(tz=None)
                        date_obs_item.setText(_format_date(localtime))
                        date_obs_item.setData(localtime, SORT_ROLE)
                    if file.image.coord_ra is not None:
                        ra_item.setText(_format_ra(file.image.coord_ra))
                        ra_item.setData(file.image.coord_ra, SORT_ROLE)
                    if file.image.coord_dec is not None:
                        dec_item.setText(_format_dec(file.image.coord_dec))
                        dec_item.setData(file.image.coord_dec, SORT_ROLE)
            except Exception as e:
                logging.error(f"Error getting image data: {e}")

            # Format date from mtime_millis
            date_item = QStandardItem(_format_timestamp(file.mtime_millis))
            date_item.setData(file.mtime_millis, SORT_ROLE)

            # Store the full filename in the name_item's data
            name_item.setData(file, ROWID_ROLE)

            # Add row to model
            self.data_model.appendRow([
                name_item, type_item, filter_item, exposure_item, gain_item, offset_item,
                binning_item, set_temp_item, camera_item, telescope_item,
                object_item, date_obs_item, path_item, size_item, date_item, ra_item, dec_item, solved_item
            ])

        # Resize columns to content
        self.resize_columns()
        self.update_sort_indicator()

    def resize_columns(self):
        self.dataView.resizeColumnsToContents()
        ensure_header_widths(self.dataView)

    def on_scroll(self, value):
        """Handle scrolling in the data view for infinite scroll."""
        # Check if we're at the bottom of the scroll area
        scrollbar = self.dataView.verticalScrollBar()
        if (value >= scrollbar.maximum() - 10 and
                self.has_more_results and
                not self.loading_more):
            self.loading_more = True
            self.search_results_loader.load_more()

    def update_search_criteria(self):
        """Update search criteria from UI elements."""
        if self.update_in_progress:
            return

        self.search_criteria.type = _get_combo_value(self.filter_type_combo)
        self.search_criteria.filter = _get_combo_value(self.filter_filter_combo)
        self.search_criteria.camera = _get_combo_value(self.filter_cam_combo)

        if self.filter_name_text.text():
            self.search_criteria.object_name = self.filter_name_text.text()
        else:
            self.search_criteria.object_name = None

        if self.filter_fname_text.text():
            self.search_criteria.file_name = self.filter_fname_text.text()
        else:
            self.search_criteria.file_name = None

        self.search_criteria.paths_as_prefix = self.checkBox.isChecked()
        # Refresh the data grid with the updated search criteria
        self.refresh_data_grid()
        # if not self.update_in_progress:
        self.refresh_combo_options()
        self.search_criteria_changed.emit()

    def show_context_menu(self, position):
        """Show context menu for the data view."""
        # Get the index at the position
        index = self.dataView.indexAt(position)
        if not index.isValid():
            return

        # Create context menu
        menu = QMenu(self)
        open_action = menu.addAction("Open File")
        show_location_action = menu.addAction("Show location")
        select_path_action = menu.addAction("Select path")
        show_header_action = menu.addAction("Show cached header")
        menu.addSeparator()
        export_action = menu.addAction("Export files")
        menu.addSeparator()
        find_darks_action = menu.addAction("Find matching darks")
        find_flats_action = menu.addAction("Find matching flats")
        menu.addSeparator()
        menu.addAction(self.mainWindow.actionPlate_solve_files)
        menu.addAction(self.mainWindow.actionPlate_Solve_Astrometry_net)
        menu.addSeparator()
        new_project_action = menu.addAction("Add to New Project")
        new_project_action.setData(Project())

        add_to_recent_project_menu = QMenu("Add to Recent Project", self)
        menu.addMenu(add_to_recent_project_menu)
        recent_projects = Project.find_recent()
        project_actions = list([new_project_action])

        if recent_projects:
            add_to_recent_project_menu.addSeparator()
            for recent_project in recent_projects:
                recent_project_action = add_to_recent_project_menu.addAction(recent_project.name)
                recent_project_action.setData(recent_project)
                project_actions.append(recent_project_action)

        # enable or disable based on current selection
        selected_file = self.get_file_at_row(index.row())
        if selected_file and hasattr(selected_file, 'image'):
            selected_image = selected_file.image
            current_type = selected_image.image_type
            find_darks_action.setEnabled(current_type == "LIGHT" or current_type == "FLAT")
            find_flats_action.setEnabled(current_type == "LIGHT")
            if selected_image.coord_pix256:
                coord = selected_image.get_sky_coord()
                nearby_projects = Project.find_nearby(coord)
                if nearby_projects:
                    add_to_nearby_project_menu = QMenu("Add to Nearby Project", self)
                    menu.addMenu(add_to_nearby_project_menu)
                    for nearby_project in nearby_projects:
                        nearby_project_action = add_to_nearby_project_menu.addAction(nearby_project.name)
                        nearby_project_action.setData(nearby_project)
                        project_actions.append(nearby_project_action)
        else:
            find_darks_action.setEnabled(False)
            find_flats_action.setEnabled(False)

        # Show the menu and get the selected action
        action = menu.exec(self.dataView.viewport().mapToGlobal(position))

        if action == open_action:
            self.open_file(index)
        elif action == show_location_action:
            self.show_file_location(index)
        elif action == select_path_action:
            self.select_path_in_tree(index)
        elif action == show_header_action:
            self.show_cached_header(index)
        elif action == export_action:
            self.export_data()
        elif action == find_darks_action:
            if self.mainWindow:
                self.mainWindow.find_matching_darks()
        elif action == find_flats_action:
            if self.mainWindow:
                self.mainWindow.find_matching_flats()
        elif action in project_actions:
            project = action.data()
            self.mainWindow.add_selection_to_project(project)

    def open_file(self, index):
        """Open the file at the given index."""
        # Get the name item from the first column
        name_index = self.data_model.index(index.row(), 0)

        # Get the full filename from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.data_model.data(name_index, ROWID_ROLE)
            filename = file.full_filename()

        if filename:
            # Open the file with the associated application
            QDesktopServices.openUrl(QUrl.fromLocalFile(filename))

    def show_file_location(self, index):
        """Open the file explorer showing the directory containing the file."""
        # Get the name item from the first column
        name_index = self.data_model.index(index.row(), 0)

        # Get the full filename from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.data_model.data(name_index, ROWID_ROLE)
            filename = file.full_filename()

        if filename:
            import os
            # Get the directory containing the file
            directory = os.path.dirname(filename)
            # Open the directory in the file explorer
            QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def on_item_double_clicked(self, index):
        """Handle double-click on an item in the data view."""
        self.open_file(index)

    def select_path_in_tree(self, index):
        """Select the path in the tree view."""
        # Get the name item from the first column
        name_index = self.data_model.index(index.row(), 0)

        # Get the file object from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.data_model.data(name_index, ROWID_ROLE)
            if not file:
                return

            # Get the library root and path
            root_id = file.root.rowid
            path = file.path

            # Create a RootAndPath object
            root_and_path = RootAndPath(root_id=root_id, root_label=file.root.name, path=path)
            self.filesystemTreeView.selectionModel().clearSelection()
            # Find and select the node in the tree
            self._find_and_select_node(root_and_path)
            self.update_search_criteria()

    def show_cached_header(self, index):
        """Show the cached FITS header for the selected file."""
        # Get the name item from the first column
        name_index = self.data_model.index(index.row(), 0)

        # Get the file object from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.data_model.data(name_index, ROWID_ROLE)
            if not file:
                QMessageBox.warning(self, "Warning", "No file selected.")
                return

            try:
                # Try to get the FitsHeader for this file
                fits_header = FitsHeader.get(FitsHeader.file == file)

                # Decompress the header data
                header_bytes = decompress(fits_header.header)
                if Importer.is_fits_by_name(file.name):
                    header_text = header_bytes.decode('utf-8')
                elif Importer.is_xisf_by_name(file.name):
                    header_dict = json.loads(header_bytes.decode('utf-8'))
                    header_text = header_from_xisf_dict(header_dict).tostring(sep="\n")

                # Show the header dialog
                dialog = HeaderDialog(header_text, self)
                dialog.exec()

            except FitsHeader.DoesNotExist:
                QMessageBox.information(self, "No Cached Header",
                                        f"No cached header found for file: {file.name}")
            except Exception as e:
                QMessageBox.critical(self, "Error",
                                     f"Error reading cached header: {str(e)}")

    def _find_and_select_node(self, root_and_path):
        """Find and select a node in the tree view based on RootAndPath."""
        # Start with the "All libraries" node
        all_libraries_index = self.library_tree_model.index(0, 0, QModelIndex())
        self.filesystemTreeView.expand(all_libraries_index)

        # If no specific root or path, select "All libraries"
        if root_and_path.root_id is None:
            self.filesystemTreeView.selectionModel().select(all_libraries_index, QItemSelectionModel.SelectCurrent)
            return

        # Find the library root node
        for i in range(self.library_tree_model.rowCount(all_libraries_index)):
            library_index = self.library_tree_model.index(i, 0, all_libraries_index)
            library_node = self.library_tree_model.getItem(library_index)

            if isinstance(library_node, LibraryRootNode) and library_node.library_root.rowid == root_and_path.root_id:
                # If no specific path, select the library root
                if not root_and_path.path:
                    self.filesystemTreeView.selectionModel().select(library_index, QItemSelectionModel.Select)
                    return

                # Check if the library root's paths have been loaded
                if library_node.library_root.rowid not in self.library_tree_model.loaded_library_roots:
                    # Store the selection for later and expand the node to trigger loading
                    self.pending_selections.append(root_and_path)
                    self.filesystemTreeView.expand(library_index)
                    return

                # Expand the library root to ensure its children are visible
                self.filesystemTreeView.expand(library_index)

                # Find the path node
                path_segments = root_and_path.path.split('/')
                current_index = library_index

                for segment in path_segments:
                    if not segment:  # Skip empty segments
                        continue

                    found = False
                    for j in range(self.library_tree_model.rowCount(current_index)):
                        child_index = self.library_tree_model.index(j, 0, current_index)
                        child_node = self.library_tree_model.getItem(child_index)

                        if isinstance(child_node, PathNode) and child_node.path_segment == segment:
                            current_index = child_index
                            self.filesystemTreeView.expand(current_index)
                            found = True
                            break

                    if not found:
                        # If we can't find the exact path, select the closest parent
                        break

                # Select the found node
                self.filesystemTreeView.selectionModel().select(current_index, QItemSelectionModel.Select)
                self.filesystemTreeView.scrollTo(current_index)
                return

        # If we couldn't find the library root, select "All libraries"
        self.filesystemTreeView.selectionModel().select(all_libraries_index, QItemSelectionModel.SelectCurrent)

    def reset_date_criteria(self):
        self.search_criteria.start_datetime = None
        self.search_criteria.end_datetime = None

    def add_exposure_filter(self):
        from .ExposureDialog import ExposureDialog
        dialog = ExposureDialog(self.context)

        # Check if there's a selected image with an exposure value
        selected_image = self.get_selected_image()
        if selected_image and selected_image.exposure is not None:
            # Use the selected image's exposure as the default
            dialog.set_exposure(selected_image.exposure)
        elif self.search_criteria.exposure:
            try:
                dialog.set_exposure(float(self.search_criteria.exposure))
            except (ValueError, TypeError):
                pass

        if dialog.exec():
            exposure = dialog.get_exposure()
            text = f"Exposure: {exposure} s"
            filter_button = FilterButton(self, text, AdvancedFilter.EXPOSURE)
            filter_button.on_remove_filter.connect(self.reset_exposure_criteria)
            self.add_filter_button_control(filter_button)
            self.search_criteria.exposure = str(exposure)
            self.update_search_criteria()

    def reset_exposure_criteria(self):
        self.search_criteria.exposure = ""

    def add_telescope_filter(self):
        from .TelescopeDialog import TelescopeDialog
        dialog = TelescopeDialog(self.context)

        # Check if there's a selected image with a telescope value
        selected_image = self.get_selected_image()
        if selected_image and selected_image.telescope is not None:
            # Use the selected image's telescope as the default
            dialog.set_telescope(selected_image.telescope)
        elif self.search_criteria.telescope:
            dialog.set_telescope(self.search_criteria.telescope)

        if dialog.exec():
            telescope = dialog.get_telescope()
            text = f"Telescope: {telescope}"
            filter_button = FilterButton(self, text, AdvancedFilter.TELESCOPE)
            filter_button.on_remove_filter.connect(self.reset_telescope_criteria)
            self.add_filter_button_control(filter_button)
            self.search_criteria.telescope = telescope
            self.update_search_criteria()

    def reset_telescope_criteria(self):
        self.search_criteria.telescope = ""

    def add_binning_filter(self):
        from .BinningDialog import BinningDialog
        dialog = BinningDialog(self.context)

        # Check if there's a selected image with a binning value
        selected_image = self.get_selected_image()
        if selected_image and selected_image.binning is not None:
            # Use the selected image's binning as the default
            dialog.set_binning(selected_image.binning)
        elif self.search_criteria.binning:
            try:
                dialog.set_binning(int(self.search_criteria.binning))
            except (ValueError, TypeError):
                pass

        if dialog.exec():
            binning = dialog.get_binning()
            text = f"Binning: {binning}"
            filter_button = FilterButton(self, text, AdvancedFilter.BINNING)
            filter_button.on_remove_filter.connect(self.reset_binning_criteria)
            self.add_filter_button_control(filter_button)
            self.search_criteria.binning = str(binning)
            self.update_search_criteria()

    def reset_binning_criteria(self):
        self.search_criteria.binning = ""

    def add_gain_filter(self):
        from .GainDialog import GainDialog
        dialog = GainDialog(self.context)

        # Check if there's a selected image with a gain value
        selected_image: Image = self.get_selected_image()
        if selected_image and selected_image.gain is not None:
            # Use the selected image's gain as the default
            dialog.set_gain(selected_image.gain)
        elif self.search_criteria.gain:
            try:
                dialog.set_gain(int(self.search_criteria.gain))
            except (ValueError, TypeError):
                pass

        if selected_image and selected_image.offset is not None:
            # Use the selected image's gain as the default
            dialog.set_offset(selected_image.offset)
        elif self.search_criteria.offset:
            try:
                dialog.set_offset(int(self.search_criteria.offset))
            except (ValueError, TypeError):
                pass

        if dialog.exec():
            gain = dialog.get_gain()
            offset = dialog.get_offset()
            text = f"Gain: {gain}"
            filter_button = FilterButton(self, text, AdvancedFilter.GAIN)
            filter_button.on_remove_filter.connect(self.reset_gain_criteria)
            self.add_filter_button_control(filter_button)
            self.search_criteria.gain = str(gain)
            self.search_criteria.offset = offset
            self.update_search_criteria()

    def reset_gain_criteria(self):
        self.search_criteria.gain = ""
        self.search_criteria.offset = None

    def add_header_text_filter(self):
        from textwrap import dedent
        header_text, ok = QInputDialog.getText(self, "Add generic header filter",
                                               dedent("""\
                                               Search the header cache for any value. 
                                               Supports finding by value (KEY=VALUE,KEY<VALUE,...) or just free text.
                                               Beware that this involves decompressing all headers and does not use an index. 
                                               If there are no other filters to limit results the search will be slow.
                                               """), text=self.search_criteria.header_text)

        if ok and header_text:
            text = f"Header: {header_text}"
            filter_button = FilterButton(self, text, AdvancedFilter.HEADER_TEXT)
            filter_button.on_remove_filter.connect(self.reset_header_text_criteria)
            self.add_filter_button_control(filter_button)
            self.search_criteria.header_text = header_text
            self.update_search_criteria()

    def reset_header_text_criteria(self):
        self.search_criteria.header_text = ""

    def add_temperature_filter(self):
        from .TemperatureDialog import TemperatureDialog
        dialog = TemperatureDialog(self.context)

        # Check if there's a selected image with a set_temp value
        selected_image = self.get_selected_image()
        if selected_image and selected_image.set_temp is not None:
            # Use the selected image's set_temp as the default
            dialog.set_temperature(selected_image.set_temp)
        elif self.search_criteria.temperature:
            try:
                dialog.set_temperature(float(self.search_criteria.temperature))
            except (ValueError, TypeError):
                pass

        if dialog.exec():
            temperature = dialog.get_temperature()
            text = f"Temperature: {temperature} °C"
            filter_button = FilterButton(self, text, AdvancedFilter.TEMPERATURE)
            filter_button.on_remove_filter.connect(self.reset_temperature_criteria)
            self.add_filter_button_control(filter_button)
            self.search_criteria.temperature = str(temperature)
            self.update_search_criteria()

    def reset_temperature_criteria(self):
        self.search_criteria.temperature = ""

    def add_coordinates_filter(self):
        from .CoordinatesDialog import CoordinatesDialog
        dialog = CoordinatesDialog(self.context)

        # Check if there's a selected image with coordinate values
        selected_image = self.get_selected_image()
        if selected_image and selected_image.coord_ra is not None and selected_image.coord_dec is not None:
            # Use the selected image's coordinates as the default
            # Convert from decimal degrees to string format
            from astropy.coordinates import SkyCoord
            import astropy.units as u

            try:
                # Create SkyCoord from the image's coordinates
                coords = selected_image.get_sky_coord()

                # Format RA as hours and DEC as degrees
                ra_str = coords.ra.to_string(unit=u.hour, sep=':', precision=2)
                dec_str = coords.dec.to_string(unit=u.deg, sep=':', precision=2)

                dialog.set_coordinates(ra_str, dec_str, self.search_criteria.coord_radius)
            except Exception as e:
                logging.error(f"Error setting coordinates from selected image: {str(e)}")
        elif self.search_criteria.coord_ra and self.search_criteria.coord_dec:
            # Use the existing search criteria
            dialog.set_coordinates(
                self.search_criteria.coord_ra,
                self.search_criteria.coord_dec,
                self.search_criteria.coord_radius
            )

        if dialog.exec():
            ra, dec, radius = dialog.get_coordinates()
            text = f"Coordinates: RA={ra}, DEC={dec}, r={radius}°"
            filter_button = FilterButton(self, text, AdvancedFilter.COORDINATES)
            filter_button.on_remove_filter.connect(self.reset_coordinates_criteria)
            self.add_filter_button_control(filter_button)
            self.search_criteria.coord_ra = ra
            self.search_criteria.coord_dec = dec
            self.search_criteria.coord_radius = radius
            self.update_search_criteria()

    def reset_coordinates_criteria(self):
        self.search_criteria.coord_ra = ""
        self.search_criteria.coord_dec = ""
        self.search_criteria.coord_radius = 0.5

    def get_selected_file(self) -> File | None:
        """Get the image data of the first selected file, if any."""
        selected_rows = self.dataView.selectionModel().selectedRows()
        if not selected_rows:
            return None

        # Get the first selected row
        first_row = selected_rows[0].row()

        # Get the name item from the first column
        name_index = self.data_model.index(first_row, 0)

        # Get the file object from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.data_model.data(name_index, ROWID_ROLE)
            return file


    def get_selected_image(self) -> Image | None:
        file = self.get_selected_file()
        if file and hasattr(file, 'image') and file.image:
            return file.image
        else:
            return None

    def reset_project_criteria(self):
        self.search_criteria.project = None

    def add_datetime_filter(self):
        dialog = DateRangeDialog(self.context)

        # Check if there's a selected image with a date_obs value
        selected_image = self.get_selected_image()
        if selected_image and selected_image.date_obs is not None:
            # Use the selected image's date_obs as the default
            utctime = selected_image.date_obs.replace(tzinfo=timezone.utc)
            localtime = utctime.astimezone(tz=None)
            dialog.set_start_date(localtime)
            dialog.set_end_date(localtime)
        elif self.search_criteria.start_datetime is not None:
            dialog.set_start_date(self.search_criteria.start_datetime)
            if self.search_criteria.end_datetime is not None:
                dialog.set_end_date(self.search_criteria.end_datetime)

        if dialog.exec():
            (start_datetime, end_datetime) = dialog.get_datetime_range()
            text = f"Date {_format_date(start_datetime)} - {_format_date(end_datetime)}"
            filter_button = FilterButton(self, text, AdvancedFilter.DATETIME)
            filter_button.on_remove_filter.connect(self.reset_date_criteria)
            self.add_filter_button_control(filter_button)
            self.search_criteria.start_datetime = start_datetime
            self.search_criteria.end_datetime = end_datetime
            self.update_search_criteria()

    def export_data(self):
        # Get the selected files
        selected_files = self.get_selected_files()

        # If no files are selected, use the search criteria
        if not selected_files:
            # Get the total number of files matching the current filters
            # If there are too many files, ask for confirmation
            if self.total_files > 100:
                response = QMessageBox.question(
                    self,
                    "Export Confirmation",
                    f"No files are selected. Do you want to export all {self.total_files} files matching the current filters?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if response == QMessageBox.No:
                    return

        # Pass the search criteria instead of loading all files
        from .ExportDialog import ExportDialog
        dialog = ExportDialog(self.context, self.search_criteria, selected_files, parent=self)
        dialog.exec()

    def _set_combo_value(self, combo: QComboBox, value: str | None):
        """Set the value of a combo box based on the search criteria."""
        if value == "":
            combo.setCurrentText(RESET_LABEL)
        elif value is None:
            combo.setCurrentText(EMPTY_LABEL)
        else:
            # Try to find the exact value
            index = combo.findText(value)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                # If the value is not in the combo box, add it
                combo.addItem(value)
                combo.setCurrentText(value)

    def _apply_pending_path_criteria(self):
        self.library_tree_model.library_roots_loader.library_roots_loaded.disconnect(self._apply_pending_path_criteria)
        # update path selections
        if len(self.search_criteria.paths) > 0:
            self.filesystemTreeView.selectionModel().clearSelection()
            for path in self.search_criteria.paths:
                self._find_and_select_node(path)
        else:
            self.filesystemTreeView.selectionModel().clearSelection()

    def apply_search_criteria(self, criteria: SearchCriteria):
        import copy
        self.search_criteria = copy.deepcopy(criteria)
        if len(self.library_tree_model.loaded_library_roots) == 0:
            self.library_tree_model.library_roots_loader.library_roots_loaded.connect(self._apply_pending_path_criteria)
        else:
            for path in self.search_criteria.paths:
                self._find_and_select_node(path)

        self.checkBox.setChecked(criteria.paths_as_prefix)

        # Update combo boxes
        self.update_in_progress = True
        if criteria.type is not None:
            self._set_combo_value(self.filter_type_combo, criteria.type)
        if criteria.filter is not None:
            self._set_combo_value(self.filter_filter_combo, criteria.filter)
        if criteria.camera is not None:
            self._set_combo_value(self.filter_cam_combo, criteria.camera)

        # Update text edits
        if criteria.file_name is not None:
            self.filter_fname_text.setText(criteria.file_name)
        else:
            self.filter_fname_text.clear()

        if criteria.object_name is not None:
            self.filter_name_text.setText(criteria.object_name)
        else:
            self.filter_name_text.clear()

        self.update_in_progress = False

        # Clear existing filter buttons
        for button in list(self.advanced_options.values()):
            self.remove_filter_button_control(button)
        self.advanced_options.clear()

        # Add filter buttons for other filters
        if criteria.exposure:
            text = f"Exposure: {criteria.exposure} s"
            filter_button = FilterButton(self, text, AdvancedFilter.EXPOSURE)
            filter_button.on_remove_filter.connect(self.reset_exposure_criteria)
            self.add_filter_button_control(filter_button)

        if criteria.telescope:
            text = f"Telescope: {criteria.telescope}"
            filter_button = FilterButton(self, text, AdvancedFilter.TELESCOPE)
            filter_button.on_remove_filter.connect(self.reset_telescope_criteria)
            self.add_filter_button_control(filter_button)

        if criteria.binning:
            text = f"Binning: {criteria.binning}"
            filter_button = FilterButton(self, text, AdvancedFilter.BINNING)
            filter_button.on_remove_filter.connect(self.reset_binning_criteria)
            self.add_filter_button_control(filter_button)

        if criteria.gain:
            text = f"Gain: {criteria.gain}"
            filter_button = FilterButton(self, text, AdvancedFilter.GAIN)
            filter_button.on_remove_filter.connect(self.reset_gain_criteria)
            self.add_filter_button_control(filter_button)

        if criteria.temperature:
            text = f"Temperature: {criteria.temperature} °C"
            filter_button = FilterButton(self, text, AdvancedFilter.TEMPERATURE)
            filter_button.on_remove_filter.connect(self.reset_temperature_criteria)
            self.add_filter_button_control(filter_button)

        if criteria.coord_ra and criteria.coord_dec:
            text = f"Coordinates: RA={criteria.coord_ra}, DEC={criteria.coord_dec}, r={criteria.coord_radius}°"
            filter_button = FilterButton(self, text, AdvancedFilter.COORDINATES)
            filter_button.on_remove_filter.connect(self.reset_coordinates_criteria)
            self.add_filter_button_control(filter_button)

        if criteria.start_datetime and criteria.end_datetime:
            text = f"Date {_format_date(criteria.start_datetime)} - {_format_date(criteria.end_datetime)}"
            filter_button = FilterButton(self, text, AdvancedFilter.DATETIME)
            filter_button.on_remove_filter.connect(self.reset_date_criteria)
            self.add_filter_button_control(filter_button)

        if criteria.header_text:
            text = f"Header: {criteria.header_text}"
            filter_button = FilterButton(self, text, AdvancedFilter.HEADER_TEXT)
            filter_button.on_remove_filter.connect(self.reset_header_text_criteria)
            self.add_filter_button_control(filter_button)

        if criteria.project:
            text = f"Project: {criteria.project.name}"
            filter_button = FilterButton(self, text, AdvancedFilter.PROJECT)
            filter_button.on_remove_filter.connect(self.reset_project_criteria)
            self.add_filter_button_control(filter_button)

    def plate_solve_files(self, solver_type: SolverType = SolverType.ASTAP):
        selected_files = self.get_selected_files()
        # If no files are selected, use the search criteria
        if not selected_files:
            # Get the total number of files matching the current filters
            # If there are too many files, ask for confirmation
            if self.total_files > 100:
                response = QMessageBox.question(
                    self,
                    "Plate solving confirmation",
                    f"No files are selected. Are you sure you want to plate solve all files matching the current filters?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if response == QMessageBox.No:
                    return

        # Pass the search criteria instead of loading all files
        task = PlateSolveTask(context=self.context, search_criteria=self.search_criteria,
                              files=selected_files if selected_files else None, solver_type=solver_type)
        dialog = ProgressDialog("Loading", "Plate solving", task, parent=self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        task.finished.connect(self.refresh_data_grid)
        task.finished.connect(self.print_task_complete)
        task.start()

    def report_list_files(self):
        selected_files = self.get_selected_files()
        # Show file save dialog
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save File List",
            "",
            "Text Files (*.txt);;List Files (*.lst);;All Files (*)"
        )

        if not file_path:
            return  # User cancelled

        try:
            task = FileListTask(context=self.context, search_criteria=self.search_criteria,
                                files=selected_files if selected_files else None)
            dialog = ProgressDialog("Creating file list", "File List", task, parent=self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.show()
            task.start(file_path)

        except Exception as e:
            error_msg = f"Failed to create file list: {str(e)}"
            logging.error(error_msg)
            self.context.status_reporter.update_status(error_msg)
            QMessageBox.critical(self, "File List Creation Failed", error_msg)

    def report_metadata(self):
        selected_files = self.get_selected_files()
        report_dialog = MetadataReportDialog(context=self.context, search_criteria=self.search_criteria,
                                             files=selected_files if selected_files else None, parent=self)
        report_dialog.setAttribute(Qt.WA_DeleteOnClose)
        report_dialog.show()

    def report_telescopius_list(self):
        selected_files = self.get_selected_files()
        from .TelescopiusCompareDialog import TelescopiusCompareDialog
        report_dialog = TelescopiusCompareDialog(context=self.context, search_criteria=self.search_criteria,
                                                 files=selected_files if selected_files else None, parent=self)
        report_dialog.setAttribute(Qt.WA_DeleteOnClose)
        report_dialog.show()

    def report_targets(self):
        from .TargetObjectReportWindow import TargetObjectReportWindow
        target_report_window = TargetObjectReportWindow(context=self.context, parent=self)
        target_report_window.setAttribute(Qt.WA_DeleteOnClose)
        target_report_window.show()

    def add_no_project_filter(self):
        project = NO_PROJECT
        text = f"Project: {project.name}"
        filter_button = FilterButton(self, text, AdvancedFilter.PROJECT)
        filter_button.on_remove_filter.connect(self.reset_project_criteria)
        self.add_filter_button_control(filter_button)
        self.search_criteria.project = project
        self.update_search_criteria()

    def print_task_complete(self):
        self.context.status_reporter.update_status("Task complete")


def _get_combo_value(combo: QComboBox) -> str | None:
    if combo.currentText() == RESET_LABEL:
        return ""
    elif combo.currentText() == EMPTY_LABEL:
        return None
    else:
        return combo.currentText()


class FilterButton(QPushButton):
    on_remove_filter = Signal()

    def __init__(self, parent, text: str, filter_type) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setMinimumHeight(20)
        self.setMinimumWidth(20)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        palette: QPalette = self.palette()
        brush: QBrush = palette.windowText()
        self.setStyleSheet(
            f"border-radius : 10px; border : 1px solid {brush.color().name()}; padding-left:10px; padding-right:10px")
        self.filter_type = filter_type


class AdvancedFilter(Enum):
    EXPOSURE = 1
    DATETIME = 2
    TELESCOPE = 3
    BINNING = 4
    GAIN = 5
    TEMPERATURE = 6
    COORDINATES = 7
    HEADER_TEXT = 8
    PROJECT = 9

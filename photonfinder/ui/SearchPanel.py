import logging
import typing
from datetime import datetime, timezone
from enum import Enum
from logging import DEBUG

from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtWidgets import *

from photonfinder.core import ApplicationContext
from photonfinder.models import SearchCriteria, CORE_MODELS, Image, RootAndPath
from .BackgroundLoader import SearchResultsLoader, GenericControlLoader
from .DateRangeDialog import DateRangeDialog
from .LibraryTreeModel import LibraryTreeModel, LibraryRootNode, PathNode
from .generated.SearchPanel_ui import Ui_SearchPanel

EMPTY_LABEL = "<empty>"
RESET_LABEL = "<any>"


# Using the new database-backed tree model for filesystemTreeView
def _not_empty(current_text):
    return current_text != EMPTY_LABEL and current_text and current_text != RESET_LABEL


class SearchPanel(QFrame, Ui_SearchPanel):
    def __init__(self, context: ApplicationContext, mainWindow: 'MainWindow', parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

        self.context = context
        self.update_in_progress = False
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
        self.dataView.setModel(self.data_model)
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

            self.update_in_progress = True
            # now apply pending selections
            self.filesystemTreeView.selectionModel().clearSelection()
            for pending_selection in self.pending_selections:
                self._find_and_select_node(pending_selection)
            self.pending_selections.clear()
            self.update_in_progress = False

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
        pages: QStackedWidget = self.parent()
        tabs: QTabWidget = pages.parent()
        my_index = pages.indexOf(self)
        tabs.setTabText(my_index, text)

    def add_filter_button_control(self, button: 'FilterButton'):
        current = self.advanced_options.get(button.filter_type, None)
        if current:
            self.remove_filter_button_control(current)
        self.advanced_options[button.filter_type] = button
        self.filter_layout.insertWidget(0, button)
        button.clicked.connect(self.remove_filter_button)

    def remove_filter_button(self):
        sender: FilterButton = self.sender()
        self.remove_filter_button_control(sender)
        self.update_search_criteria()

    def remove_filter_button_control(self, button: 'FilterButton'):
        self.filter_layout.removeWidget(button)
        button.on_remove_filter.emit()
        button.hide()
        button.destroy()

    def get_selected_files(self):
        """Get the currently selected files in the data grid."""
        selected_indexes = self.dataView.selectionModel().selectedRows()
        selected_files = []

        for index in selected_indexes:
            # Get the Image object from the model
            image = self.get_image_at_row(index.row())
            if image:
                selected_files.append(image)

        return selected_files

    def get_image_at_row(self, row):
        """Get the Image object at the specified row."""
        if row < 0 or row >= self.data_model.rowCount():
            return None

        name_index = self.data_model.index(row, 0)
        return self.data_model.data(name_index, Qt.UserRole)

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

        self.refresh_data_grid()

    def on_data_selection_changed(self, selected, deselected):
        """Handle selection changes in the data grid."""
        # Get the number of selected rows
        selected_count = len(self.dataView.selectionModel().selectedRows())

        # Update the status bar with the selection information
        if selected_count > 0:
            self.context.status_reporter.update_status(f"{selected_count} files out of {self.total_files} selected")
        else:
            self.context.status_reporter.update_status(f"{self.total_files} files")

        self.mainWindow.enable_actions_for_current_tab()

    def on_search_results_loaded(self, results, page, total_files, has_more):
        """Handle search results loaded from the database."""
        self.has_more_results = has_more

        # If this is a new search (data model is empty), reset the total files counter
        if page == 0:
            self.data_model.clear()
            self.data_model.setHorizontalHeaderLabels([
                "File name", "Type", "Filter", "Exposure", "Gain", "Offset", "Binning", "Set Temp",
                "Camera", "Telescope", "Object", "Observation Date", "Path", "Size", "Modified"
            ])

            # Reset total files counter when starting a new search
            self.total_files = total_files
            # Update our tab title
            if self.search_criteria.is_empty():
                self.set_title("All files")
            else:
                self.set_title(f"{self.total_files} files [{str(self.search_criteria)}]")
            # Update the status bar with the total number of files
            self.context.status_reporter.update_status(f"{self.total_files} files")

        # Reset loading_more flag after processing
        self.loading_more = False

        # Add results to the data model

        for file in results:
            # Create row items
            name_item = QStandardItem(file.name)
            path_item = QStandardItem(file.path)

            # Format size (convert bytes to KB, MB, etc.)
            size_str = _format_file_size(file.size)
            size_item = QStandardItem(size_str)

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
            localtime: datetime
            try:
                if hasattr(file, 'image') and file.image:
                    if file.image.image_type is not None:
                        type_item.setText(file.image.image_type)
                    if file.image.filter is not None:
                        filter_item.setText(file.image.filter)
                    if file.image.exposure is not None:
                        exposure_item.setText(str(file.image.exposure))
                    if file.image.gain is not None:
                        gain_item.setText(str(file.image.gain))
                    if file.image.offset is not None:
                        offset_item.setText(str(file.image.offset))
                    if file.image.binning is not None:
                        binning_item.setText(str(file.image.binning))
                    if file.image.set_temp is not None:
                        set_temp_item.setText(str(file.image.set_temp))
                    if file.image.camera is not None:
                        camera_item.setText(file.image.camera)
                    if file.image.telescope is not None:
                        telescope_item.setText(file.image.telescope)
                    if file.image.object_name is not None:
                        object_item.setText(file.image.object_name)
                    if file.image.date_obs is not None:
                        from zoneinfo import ZoneInfo
                        utctime = file.image.date_obs.replace(tzinfo=timezone.utc)
                        localtime = utctime.astimezone(tz=None)
                        date_obs_item.setText(_format_date(localtime))
            except Exception as e:
                logging.error(f"Error getting image data: {e}")

            # Format date from mtime_millis
            dt = datetime.fromtimestamp(file.mtime_millis / 1000)
            date_str = _format_date(dt)
            date_item = QStandardItem(date_str)

            # Store the full filename in the name_item's data
            name_item.setData(file, Qt.UserRole)

            # Add row to model
            self.data_model.appendRow([
                name_item, type_item, filter_item, exposure_item, gain_item, offset_item,
                binning_item, set_temp_item, camera_item, telescope_item,
                object_item, date_obs_item, path_item, size_item, date_item
            ])

        # Resize columns to content
        self.dataView.resizeColumnsToContents()

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

    def show_context_menu(self, position):
        """Show context menu for the data view."""
        # Get the index at the position
        index = self.dataView.indexAt(position)
        if not index.isValid():
            return

        # Create context menu
        menu = QMenu(self)
        open_action = menu.addAction("Open")
        show_location_action = menu.addAction("Show location")
        select_path_action = menu.addAction("Select path")

        # Show the menu and get the selected action
        action = menu.exec(self.dataView.viewport().mapToGlobal(position))

        if action == open_action:
            # Reuse the double-click functionality
            self.open_file(index)
        elif action == show_location_action:
            # Show the file location in explorer
            self.show_file_location(index)
        elif action == select_path_action:
            # Select the path in the tree view
            self.select_path_in_tree(index)

    def open_file(self, index):
        """Open the file at the given index."""
        # Get the name item from the first column
        name_index = self.data_model.index(index.row(), 0)

        # Get the full filename from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.data_model.data(name_index, Qt.UserRole)
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
            file = self.data_model.data(name_index, Qt.UserRole)
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
            file = self.data_model.data(name_index, Qt.UserRole)
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
                coords = SkyCoord(selected_image.coord_ra, selected_image.coord_dec, unit=u.deg, frame='icrs')

                # Format RA as hours and DEC as degrees
                ra_str = coords.ra.to_string(unit=u.hour, sep=':', precision=2)
                dec_str = coords.dec.to_string(unit=u.deg, sep=':', precision=2)

                dialog.set_coordinates(ra_str, dec_str, self.search_criteria.coord_radius)
            except Exception as e:
                print(f"Error setting coordinates from selected image: {str(e)}")
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

    def get_selected_image(self) -> Image | None:
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
            file = self.data_model.data(name_index, Qt.UserRole)
            if hasattr(file, 'image') and file.image:
                return file.image

        return None

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
                    f"No files are selected. Do you want to export all files matching the current filters?",
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


def _get_combo_value(combo: QComboBox) -> str | None:
    if combo.currentText() == RESET_LABEL:
        return ""
    elif combo.currentText() == EMPTY_LABEL:
        return None
    else:
        return combo.currentText()


def _format_file_size(size_bytes):
    """Format file size from bytes to human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _format_date(value: datetime):
    try:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as ex:
        logging.exception(f"Error formatting date {ex}", exc_info=ex)
        return None


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

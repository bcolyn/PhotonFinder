import logging
import typing
from datetime import datetime, timezone
from enum import Enum
from logging import DEBUG

from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtWidgets import *

from astrofilemanager.core import ApplicationContext
from astrofilemanager.models import SearchCriteria, CORE_MODELS, Image
from .BackgroundLoader import SearchResultsLoader, GenericControlLoader
from .DateRangeDialog import DateRangeDialog
from .LibraryTreeModel import LibraryTreeModel
from .generated.SearchPanel_ui import Ui_SearchPanel

EMPTY_LABEL = "<empty>"
RESET_LABEL = "<any>"


# Using the new database-backed tree model for filesystemTreeView
class SearchPanel(QFrame, Ui_SearchPanel):
    def __init__(self, context: ApplicationContext, parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

        self.context = context
        self.update_in_progress = False
        self.search_criteria = SearchCriteria()
        self.advanced_options = dict()

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
        self.has_more_results = False
        self.loading_more = False

        # Initialize the library tree model
        self.library_tree_model = LibraryTreeModel(context, self)
        self.library_tree_model.reload_library_roots()
        self.library_tree_model.ready_for_display.connect(self.on_library_tree_ready)

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
        self.checkBox.toggled.connect(self.update_search_criteria)

    def on_library_tree_ready(self):
        all_libraries_index = self.library_tree_model.index(0, 0, QModelIndex())
        self.filesystemTreeView.expand(all_libraries_index)

    def on_tree_selection_changed(self, selected, deselected):
        # Get the current selection
        indexes = self.filesystemTreeView.selectionModel().selectedIndexes()
        if not indexes:
            return
        self.search_criteria.paths = self.library_tree_model.get_roots_and_paths(indexes)
        self.update_search_criteria()

    def set_title(self, text: str):
        my_index = self.parent().indexOf(self)
        self.parent().setTabText(my_index, text)

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

    def refresh_data_grid(self):
        """Trigger a search with the current search criteria."""
        if self.update_in_progress:
            return

        self.data_model.clear()
        self.data_model.setHorizontalHeaderLabels([
            "File name", "Type", "Filter", "Exposure", "Gain", "Binning", "Set Temp",
            "Camera", "Telescope", "Object", "Observation Date", "Path", "Size", "Modified"
        ])
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

        if current_text in data or current_text == EMPTY_LABEL:
            target.setCurrentText(current_text)
            self.update_in_progress = False
        else:
            self.update_in_progress = False
            target.setCurrentText(RESET_LABEL)
            self.refresh_data_grid()

    def on_search_results_loaded(self, results, has_more):
        """Handle search results loaded from the database."""
        self.has_more_results = has_more
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
                name_item, type_item, filter_item, exposure_item, gain_item,
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

        self.search_criteria.type = _get_combo_value(self.filter_type_combo)
        self.search_criteria.filter = _get_combo_value(self.filter_filter_combo)
        self.search_criteria.camera = _get_combo_value(self.filter_cam_combo)

        if self.filter_name_text.text():
            self.search_criteria.object_name = self.filter_name_text.text()
        else:
            self.search_criteria.object_name = None

        self.search_criteria.paths_as_prefix = self.checkBox.isChecked()
        # Refresh the data grid with the updated search criteria
        self.refresh_data_grid()
        if not self.update_in_progress:
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

        # Show the menu and get the selected action
        action = menu.exec(self.dataView.viewport().mapToGlobal(position))

        if action == open_action:
            # Reuse the double-click functionality
            self.open_file(index)
        elif action == show_location_action:
            # Show the file location in explorer
            self.show_file_location(index)

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

    def reset_date_criteria(self):
        self.search_criteria.start_datetime = None
        self.search_criteria.end_datetime = None

    def add_exposure_filter(self):
        pass

    def add_datetime_filter(self):
        dialog = DateRangeDialog(self.context)
        if self.search_criteria.start_datetime is not None:
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

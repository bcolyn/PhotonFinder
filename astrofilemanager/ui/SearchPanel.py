import logging
import time
import typing
from datetime import datetime, timezone
from logging import DEBUG

from PySide6.QtCore import *
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import *

from core import ApplicationContext
from models import SearchCriteria, CORE_MODELS, Image
from .BackgroundLoader import SearchResultsLoader, GenericControlLoader
from .LibraryTreeModel import LibraryTreeModel
from .generated.SearchPanel_ui import Ui_SearchPanel


# Using the new database-backed tree model for filesystemTreeView
class SearchPanel(QFrame, Ui_SearchPanel):
    def __init__(self, context: ApplicationContext, parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

        self.context = context
        self.update_in_progress = False
        self.search_criteria = SearchCriteria()

        # Initialize the search results loader
        self.search_results_loader = SearchResultsLoader(context)
        self.search_results_loader.results_loaded.connect(self.on_search_results_loaded)
        self.combo_loader = GenericControlLoader(context)
        self.combo_loader.data_ready.connect(self.on_combo_options_loaded)

        # Initialize the data view model
        self.data_model = QStandardItemModel(self)
        self.dataView.setModel(self.data_model)
        self.dataView.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self.dataView.doubleClicked.connect(self.on_item_double_clicked)
        self.dataView.setEditTriggers(QAbstractItemView.NoEditTriggers)
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
        self.filter_exp_text.textChanged.connect(self.update_search_criteria)
        self.filter_coord_button.toggled.connect(self.update_search_criteria)
        self.filter_date_button.toggled.connect(self.update_search_criteria)
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

    def add_filter(self):
        self.add_filter_button()

    def set_title(self, text: str):
        my_index = self.parent().indexOf(self)
        self.parent().setTabText(my_index, text)

    def add_filter_button(self):
        button = FilterButton(self)
        self.filter_layout.insertWidget(1, button)
        button.clicked.connect(self.remove_filter_button)

    def remove_filter_button(self):
        sender = self.sender()
        self.filter_layout.removeWidget(sender)
        sender.hide()
        sender.destroy()

    def refresh_data_grid(self):
        """Trigger a search with the current search criteria."""
        if self.update_in_progress:
            return

        self.data_model.clear()
        self.data_model.setHorizontalHeaderLabels([
            "Name", "Type", "Filter", "Exposure", "Gain", "Binning", "Set Temp",
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
        target.addItem("<clear>")
        target.addItems(data)
        
        # Calculate appropriate width based on the longest item
        fm = target.fontMetrics()
        max_width = max([fm.horizontalAdvance(item) for item in data]) if data else 0
        # Add some padding to the width
        popup_width = max_width + 30  # Add padding for scroll bar and margins
        
        # Set the minimum width for the popup view
        target.view().setMinimumWidth(popup_width)
        
        if current_text in data:
            target.setCurrentText(current_text)
        self.update_in_progress = False

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
            size_str = self._format_file_size(file.size)
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
                        date_obs_item.setText(self._format_date(localtime))
            except Exception as e:
                logging.error(f"Error getting image data: {e}")

            # Format date from mtime_millis
            dt = datetime.fromtimestamp(file.mtime_millis / 1000)
            date_str = self._format_date(dt)
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
        if self.filter_type_combo.currentText() == "<clear>":
            self.search_criteria.type = ""
        else:
            self.search_criteria.type = self.filter_type_combo.currentText()

        if self.filter_filter_combo.currentText() == "<clear>":
            self.search_criteria.filter = ""
        else:
            self.search_criteria.filter = self.filter_filter_combo.currentText()

        if self.filter_cam_combo.currentText() == "<clear>":
            self.search_criteria.camera = ""
        else:
            self.search_criteria.camera = self.filter_cam_combo.currentText()
        if self.filter_name_text.text():
            self.search_criteria.name = self.filter_name_text.text()
        if self.filter_exp_text.text():
            self.search_criteria.exposure = self.filter_exp_text.text()

        self.search_criteria.use_coordinates = self.filter_coord_button.isChecked()
        self.search_criteria.use_date = self.filter_date_button.isChecked()
        self.search_criteria.paths_as_prefix = self.checkBox.isChecked()
        # Refresh the data grid with the updated search criteria
        self.refresh_data_grid()
        if not self.update_in_progress:
            self.refresh_combo_options()

    def _format_file_size(self, size_bytes):
        """Format file size from bytes to human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    @staticmethod
    def _format_date(mtime: datetime):
        try:
            return mtime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as ex:
            logging.exception(f"Error formatting date {ex}", exc_info=ex)
            return None

    def on_item_double_clicked(self, index):
        """Handle double-click on an item in the data view."""
        # Get the name item from the first column
        name_index = self.data_model.index(index.row(), 0)

        # Get the full filename from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.data_model.data(name_index, Qt.UserRole)
            filename = file.full_filename()

        if filename:
            # Open the file with the associated application
            QDesktopServices.openUrl(QUrl.fromLocalFile(filename))


class FilterButton(QPushButton):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setText("FilterTest" + str(int(time.time())))
        self.setMinimumHeight(20)
        self.setStyleSheet("border-radius : 10px; border : 2px solid black; padding-left:10px; padding-right:10px")
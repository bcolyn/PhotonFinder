import logging
import time
from datetime import datetime

from PySide6.QtCore import *
from PySide6.QtWidgets import *
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from core import ApplicationContext
from models import SearchCriteria, CORE_MODELS
from .LibraryTreeModel import LibraryTreeModel
from .generated.SearchPanel_ui import Ui_SearchPanel
from .loaders import SearchResultsLoader


# Using the new database-backed tree model for filesystemTreeView
class SearchPanel(QFrame, Ui_SearchPanel):
    def __init__(self, context: ApplicationContext, parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

        self.context = context
        self.search_criteria = SearchCriteria()

        # Initialize the search results loader
        self.search_results_loader = SearchResultsLoader(context)
        self.search_results_loader.results_loaded.connect(self.on_search_results_loaded)

        # Initialize the data view model
        self.data_model = QStandardItemModel(self)
        self.dataView.setModel(self.data_model)
        self.dataView.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self.dataView.doubleClicked.connect(self.on_item_double_clicked)
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
        self.filter_cam_text.textChanged.connect(self.update_search_criteria)
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
        self.refresh_data_grid()

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
        # Clear the current data model
        self.data_model.clear()

        # Set up headers
        self.data_model.setHorizontalHeaderLabels([
            "Name", "Type", "Filter", "Exposure", "Gain", "Binning", "Set Temp", 
            "Camera", "Telescope", "Object", "Observation Date", "Path", "Size", "Modified"
        ])

        # Make the datagrid readonly
        self.dataView.setEditTriggers(QAbstractItemView.NoEditTriggers)

        # Start the search
        self.search_results_loader.search(self.search_criteria)

    def on_search_results_loaded(self, results, has_more):
        """Handle search results loaded from the database."""
        self.has_more_results = has_more
        self.loading_more = False

        # Add results to the data model
        with self.context.database.bind_ctx(CORE_MODELS):
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
                            date_obs_item.setText(self._format_date(file.image.date_obs))
                except Exception as e:
                    logging.error(f"Error getting image data: {e}")

                # Format date from mtime_millis
                date_str = self._format_date(file.mtime_millis)
                date_item = QStandardItem(date_str)

                # Store the full filename in the name_item's data
                name_item.setData(file.full_filename(), Qt.UserRole)

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
        # Update search criteria from UI elements
        self.search_criteria.type = self.filter_type_combo.currentText()
        self.search_criteria.filter = self.filter_filter_combo.currentText()
        self.search_criteria.camera = self.filter_cam_text.text()
        self.search_criteria.name = self.filter_name_text.text()
        self.search_criteria.exposure = self.filter_exp_text.text()
        self.search_criteria.use_coordinates = self.filter_coord_button.isChecked()
        self.search_criteria.use_date = self.filter_date_button.isChecked()
        self.search_criteria.paths_as_prefix = self.checkBox.isChecked()

        # Refresh the data grid with the updated search criteria
        self.refresh_data_grid()

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

    def _format_date(self, mtime_millis):
        """Format date from milliseconds since epoch."""
        try:
            dt = datetime.fromtimestamp(mtime_millis / 1000)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    def on_item_double_clicked(self, index):
        """Handle double-click on an item in the data view."""
        # Get the name item from the first column
        name_index = self.data_model.index(index.row(), 0)

        # Get the full filename from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            filename = self.data_model.data(name_index, Qt.UserRole)

        if filename:
            # Open the file with the associated application
            QDesktopServices.openUrl(QUrl.fromLocalFile(filename))


class FilterButton(QPushButton):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setText("FilterTest" + str(int(time.time())))
        self.setMinimumHeight(20)
        self.setStyleSheet("border-radius : 10px; border : 2px solid black; padding-left:10px; padding-right:10px")

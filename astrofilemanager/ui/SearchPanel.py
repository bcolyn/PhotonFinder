import logging
import time
import os
from datetime import datetime

from PySide6.QtCore import *
from PySide6.QtWidgets import *
from PySide6.QtGui import QStandardItemModel, QStandardItem
from peewee import JOIN

from core import ApplicationContext
from models import LibraryRoot, SearchCriteria, File, Image, RootAndPath, CORE_MODELS
from .LibraryTreeModel import LibraryTreeModel, BackgroundLoaderBase
from .generated.SearchPanel_ui import Ui_SearchPanel


class SearchResultsLoader(BackgroundLoaderBase):
    """Helper class for asynchronous loading of search results from the database."""

    # Signal emitted when search results are loaded
    results_loaded = Signal(list, bool)  # results, has_more

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self.page_size = 100
        self.current_page = 0
        self.total_results = 0
        self.last_criteria = None

    def search(self, search_criteria, page=0):
        """Start a search with the given criteria."""
        self.current_page = page
        self.last_criteria = search_criteria
        self.run_in_thread(self._search_task, search_criteria, page)

    def load_more(self):
        """Load the next page of results using the last search criteria."""
        if self.last_criteria:
            self.current_page += 1
            self.search(self.last_criteria, self.current_page)

    def _search_task(self, search_criteria, page):
        """Background task to search for files matching the criteria."""
        with self.context.database.bind_ctx(CORE_MODELS):
            try:
                # Start building the query
                query = (File
                         .select(File, Image)
                         .join(Image, JOIN.LEFT_OUTER)
                         .order_by(File.name))

                # Apply search criteria to the query
                query = self._apply_search_criteria(query, search_criteria)

                # Get total count for pagination
                self.total_results = query.count()

                # Apply pagination
                query = query.paginate(page + 1, self.page_size)

                # Execute the query and get results
                results = list(query)

                # Check if there are more results
                has_more = (page + 1) * self.page_size < self.total_results

                # Emit signal with the results
                self.results_loaded.emit(results, has_more)
            except Exception as e:
                logging.error(f"Error searching files: {e}")
                self.results_loaded.emit([], False)

    def _apply_search_criteria(self, query, criteria):
        """Apply search criteria to the query."""
        conditions = []

        # Filter by paths
        if criteria.paths:
            path_conditions = []
            for root_and_path in criteria.paths:
                if criteria.paths_as_prefix:
                    # Match files in this path or any subdirectory
                    path_conditions.append(
                        (File.root == root_and_path.root_id) &
                        (File.path.startswith(root_and_path.path))
                    )
                else:
                    # Match files exactly in this path
                    path_conditions.append(
                        (File.root == root_and_path.root_id) &
                        (File.path == root_and_path.path)
                    )
            if path_conditions:
                conditions.append(path_conditions[0] if len(path_conditions) == 1
                                  else path_conditions[0].orwhere(*path_conditions[1:]))

        # Filter by file type
        if criteria.type:
            conditions.append(Image.imageType == criteria.type)

        # Filter by filter
        if criteria.filter:
            conditions.append(Image.filter == criteria.filter)

        # Apply additional criteria if available
        if hasattr(criteria, 'camera') and criteria.camera:
            # This would need to be mapped to the appropriate field in the database
            pass

        if hasattr(criteria, 'name') and criteria.name:
            conditions.append(File.name.contains(criteria.name))

        if hasattr(criteria, 'exposure') and criteria.exposure:
            try:
                exp = float(criteria.exposure)
                conditions.append(Image.exposure == exp)
            except (ValueError, TypeError):
                pass

        # Apply all conditions to the query
        for condition in conditions:
            query = query.where(condition)

        return query


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
        # Don't expand all at once - just expand the first level
        # to show library roots
        all_libraries_index = self.library_tree_model.index(0, 0, QModelIndex())
        self.filesystemTreeView.expand(all_libraries_index)
        # self.filesystemTreeView.setRootIndex(QModelIndex())

    def on_tree_selection_changed(self, selected, deselected):
        """
        Handle selection changes in the tree view.

        Args:
            selected: Selected indexes
            deselected: Deselected indexes
        """
        # Get the current selection
        indexes = self.filesystemTreeView.selectionModel().selectedIndexes()
        if not indexes:
            return

        # Get the selected index
        index = indexes[0]

        # Clear existing paths in search criteria
        self.search_criteria.paths.clear()

        # Get the file system model for the selected index
        file_system_model = self.library_tree_model.get_file_system_model_for_index(index)
        if file_system_model:
            # If a library root is selected, add it to search criteria
            root_path = self.library_tree_model.get_root_path_for_index(index)
            if root_path:
                logging.debug(f"Selected library root with path: {root_path}")
                # Find the library root ID
                for library_root in LibraryRoot.select():
                    if library_root.path == root_path:
                        # Add to search criteria
                        self.search_criteria.paths.append(RootAndPath(
                            root_id=library_root.id,
                            path=""  # Empty path means search the entire library root
                        ))
                        break
        else:
            # If "All locations" is selected, include all library roots
            item = self.library_tree_model.getItem(index)
            if item and item.data() == "All locations":
                logging.debug("Selected All locations")
                for library_root in LibraryRoot.select():
                    self.search_criteria.paths.append(RootAndPath(
                        root_id=library_root.id,
                        path=""
                    ))

        # Refresh the data grid with the updated search criteria
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
            "Name", "Path", "Size", "Type", "Filter", "Exposure", "Date"
        ])

        # Start the search
        self.search_results_loader.search(self.search_criteria)

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

            try:
                if hasattr(file, 'image') and file.image:
                    if file.image.imageType:
                        type_item.setText(file.image.imageType)
                    if file.image.filter:
                        filter_item.setText(file.image.filter)
                    if file.image.exposure:
                        exposure_item.setText(str(file.image.exposure))
            except Exception as e:
                logging.error(f"Error getting image data: {e}")

            # Format date from mtime_millis
            date_str = self._format_date(file.mtime_millis)
            date_item = QStandardItem(date_str)

            # Add row to model
            self.data_model.appendRow([
                name_item, path_item, size_item, type_item,
                filter_item, exposure_item, date_item
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


class FilterButton(QPushButton):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setText("FilterTest" + str(int(time.time())))
        self.setMinimumHeight(20)
        self.setStyleSheet("border-radius : 10px; border : 2px solid black; padding-left:10px; padding-right:10px")

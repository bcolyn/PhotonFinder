import copy
import datetime
import logging
import os
import subprocess
import sys
from functools import partial
from logging import DEBUG
from types import SimpleNamespace
from typing import Collection, List, Optional

from PySide6.QtCore import (QByteArray, QEvent, QItemSelectionModel, QMimeData, QModelIndex,
                            QSortFilterProxyModel, QUrl, Qt, Signal)
from PySide6.QtGui import QBrush, QDesktopServices, QKeySequence, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QInputDialog, QMenu,
                               QMessageBox, QPushButton, QSizePolicy, QToolBar, QWidget)

from photonfinder import filter_catalogue, plate_solution
from photonfinder.core import ApplicationContext, Change, library_busy_message
from photonfinder.filesystem import is_compressible, cached_header
from photonfinder.local_time import obs_zone, session_night
from photonfinder.library_query import (
    COLUMN_INDEX, GROUP_COLUMNS, RESULT_COLUMNS, cell, count_text, diagnosis_counts, distinct_files,
    distinct_values, group_value, image_key, matching_files, search_row_stat,
)
from photonfinder.models import SearchCriteria, CORE_MODELS, Image, RootAndPath, File, FitsHeader, Project, \
    ProjectFile, LibraryRoot, group_of
from .BackgroundLoader import SearchResultsLoader, GenericControlLoader, PlateSolveTask, FileListTask, ImageAnalysisTask
from .DateRangeDialog import DateRangeDialog
from .HeaderDialog import HeaderDialog
from .ImageAnalysisDialog import ImageAnalysisDialog
from .LibraryTreeModel import LibraryTreeModel, LibraryRootNode, PathNode
from .MetadataReportDialog import MetadataReportDialog
from .PlateSolveDialog import PlateSolveDialog
from .ProgressDialog import ProgressDialog
from .common import ensure_header_widths, ColumnVisibilityController
from .generated.SearchPanel_ui import Ui_SearchPanel

EMPTY_LABEL = "<empty>"


def _last_night(context) -> datetime.date:
    """The night that is under way or, during the day, the one that ended this morning: from
    18:00 local time (obs_timezone) on, tonight; before, last night."""
    now = datetime.datetime.now(obs_zone(context.settings))
    return now.date() if now.hour >= 18 else now.date() - datetime.timedelta(days=1)

RESET_LABEL = "<any>"

ROWID_ROLE = Qt.UserRole
SORT_ROLE = Qt.UserRole + 1

FILE_DRAG_MIME = "application/x-photonfinder-file-ids"


class _FileDragProxyModel(QSortFilterProxyModel):
    def mimeTypes(self):
        return [FILE_DRAG_MIME]

    def mimeData(self, indexes):
        mime = QMimeData()
        rows = sorted({idx.row() for idx in indexes})
        rowids = []
        for row in rows:
            source_idx = self.mapToSource(self.index(row, 0))
            item = self.sourceModel().itemFromIndex(source_idx)
            if item:
                file = item.data(ROWID_ROLE)
                # A file's layers are rows of their own; a project takes the file once.
                if file is not None and str(file.rowid) not in rowids:
                    rowids.append(str(file.rowid))
        mime.setData(FILE_DRAG_MIME, QByteArray(",".join(rowids).encode()))
        return mime


# Using the new database-backed tree model for filesystemTreeView
def _not_empty(current_text):
    return current_text != EMPTY_LABEL and current_text and current_text != RESET_LABEL

class SearchPanel(QFrame, Ui_SearchPanel):
    search_criteria_changed = Signal()
    data_fully_loaded = Signal()  # emitted when the last batch arrives (has_more → False)

    def __init__(self, context: ApplicationContext, mainWindow: 'MainWindow', parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

        self.context = context
        self.update_in_progress = False
        self.select_all_pending = False
        self.title = "Loading"
        self.title_is_custom = False
        self.mainWindow = mainWindow
        self.search_criteria = SearchCriteria() #TODO: previous / next?
        self.advanced_options = dict()
        self.total_files = 0  # Search result rows: one per image
        self.total_result_files = 0  # The files those rows are in
        self.pending_selections = list()  # Store pending path selections

        # Initialize the search results loader
        self.search_results_loader = SearchResultsLoader(context)
        self.search_results_loader.results_loaded.connect(self.on_search_results_loaded)
        self.combo_loader = GenericControlLoader(context)
        self.combo_loader.data_ready.connect(self.on_combo_options_loaded)
        # self.refresh_combo_options()

        # Initialize the data view model
        self.data_model = QStandardItemModel(self)
        self.data_model.setHorizontalHeaderLabels([column.label for column in RESULT_COLUMNS])
        self.proxy_model = _FileDragProxyModel()
        self.proxy_model.setSortRole(SORT_ROLE)
        self.proxy_model.setSourceModel(self.data_model)
        self.dataView.setModel(self.proxy_model)
        # Night comes last in RESULT_COLUMNS (saved sort positions), and is shown after the date.
        header = self.dataView.horizontalHeader()
        header.moveSection(header.visualIndex(COLUMN_INDEX["session_night"]),
                           header.visualIndex(COLUMN_INDEX["date_obs"]) + 1)
        self.dataView.setDragEnabled(True)
        self.dataView.setDragDropMode(QAbstractItemView.DragOnly)
        self.dataView.verticalScrollBar().valueChanged.connect(self.on_scroll)
        self.dataView.doubleClicked.connect(self.on_item_double_clicked)
        self.dataView.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dataView.setContextMenuPolicy(Qt.CustomContextMenu)
        self.dataView.customContextMenuRequested.connect(self.show_context_menu)
        # Connect selection changes
        self.dataView.selectionModel().selectionChanged.connect(self.on_data_selection_changed)
        self.dataView.installEventFilter(self)
        self.visibility_controller = ColumnVisibilityController(self.dataView, context=context.settings)
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
        self.filesystemTreeView.customContextMenuRequested.connect(self.on_tree_context_menu)

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
        self.toolbar.addAction(mainWindow.actionGroupByNight)
        self.toolbar.addSeparator()
        self.toolbar.addAction(mainWindow.actionExposure)
        self.toolbar.addAction(mainWindow.actionCoordinates)
        self.toolbar.addAction(mainWindow.actionDate)
        self.toolbar.addAction(mainWindow.actionNight)
        self.toolbar.addAction(mainWindow.actionTelescope)
        self.toolbar.addAction(mainWindow.actionBinning)
        self.toolbar.addAction(mainWindow.actionGain)
        self.toolbar.addAction(mainWindow.actionTemperature)
        self.toolbar.addAction(mainWindow.actionPlateSolved)
        self.toolbar.addAction(mainWindow.actionImageSize)
        self.toolbar.addAction(mainWindow.actionPlateScale)
        self.toolbar.addAction(mainWindow.actionImageQuality)
        self.toolbar.addAction(mainWindow.action_filter_no_project)

        # connect to signal bus
        self.context.signal_bus.projects_changed.connect(self.on_projects_changed)
        self.context.signal_bus.project_links_changed.connect(self.on_project_links_changed)

    def showEvent(self, event, /):
        super().showEvent(event)
        if self.data_model.rowCount() == 0:
            self.update_search_criteria()

    def eventFilter(self, obj, event):
        """Handle CTRL+A to select all rows, loading all data if needed."""
        if obj == self.dataView and event.type() == QEvent.KeyPress:
            if event.matches(QKeySequence.SelectAll):
                self.handle_select_all()
                return True  # Event handled
        return super().eventFilter(obj, event)

    def handle_select_all(self):
        """Handle CTRL+A - load all data if needed, then select all."""
        if self.has_more_results:
            # There's more data to load, so load it all first
            self.select_all_pending = True
            self.load_all_remaining_data()
        else:
            # All data is already loaded, just select all
            self.dataView.selectAll()

    def load_all_remaining_data(self):
        """Load all remaining data in batches."""
        if not self.loading_more and self.has_more_results:
            self.loading_more = True
            self.context.status_reporter.update_status("Loading all data for selection...", bulk=True)
            self.search_results_loader.load_more()

    def on_library_tree_ready(self):
        all_libraries_index = self.library_tree_model.index(0, 0, QModelIndex())
        self.filesystemTreeView.expand(all_libraries_index)
        self._revalidate_tree_paths()

    def _revalidate_tree_paths(self):
        """After the library tree is (re)loaded, e.g. once Manage Libraries closes: its reset
        cleared the tree selection but not the search's folder paths. Drop the paths of library
        roots that are gone, and show the others selected again."""
        if not self.search_criteria.paths:
            return
        all_libraries = self.library_tree_model.root_node.child(0)
        root_ids = {n.library_root.rowid for n in all_libraries.children if isinstance(n, LibraryRootNode)}
        kept = [p for p in self.search_criteria.paths if p.root_id is None or p.root_id in root_ids]
        dropped = len(kept) != len(self.search_criteria.paths)
        self.search_criteria.paths = kept
        was_updating = self.update_in_progress
        self.update_in_progress = True  # selecting them again must not search once per path
        try:
            self.filesystemTreeView.selectionModel().clearSelection()
            for path in kept:
                self._find_and_select_node(path)
        finally:
            self.update_in_progress = was_updating
        if dropped:
            self.update_search_criteria()

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
        """Set an auto-generated tab title; ignored once the user has set a custom title."""
        if self.title_is_custom:
            return
        self.title = text
        self.mainWindow.set_tab_title(self, text)

    def set_custom_title(self, text: str):
        """Set a user-defined tab title, which takes precedence over auto-generated titles."""
        self.title = text
        self.title_is_custom = True
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
        button.clicked.connect(self.edit_filter_button)
        button.right_clicked.connect(self.remove_filter_button)

    def edit_filter_button(self):
        sender: FilterButton = self.sender()
        self.edit_filter(sender.filter_type)

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
        # deleteLater, not destroy(): this can run inside the button's own mouse event, and
        # destroy() only drops the native window, leaving the widget and its children alive.
        button.deleteLater()

    def get_selected_files(self) -> List[File]:
        """The selected rows of the data grid: one File per image, so a file whose layers are
        both selected comes twice, each with its own Image. Actions on files take
        get_selected_distinct_files. In grouped mode a row stands for its group, so this is
        every image of the selected groups (that the search lets through)."""
        rows = self.get_selected_rows()
        if not self.search_criteria.one_per_group or not rows:
            return rows
        return self._group_members(rows)

    def get_selected_rows(self) -> List[File]:
        """The selected rows as shown: in grouped mode, the groups' representatives."""
        selected_files = []
        for index in self.dataView.selectionModel().selectedRows():
            file = self.get_file_at_row(index.row())
            if file:
                selected_files.append(file)
        return selected_files

    def _group_criteria(self, rows: List[File]) -> SearchCriteria:
        """The current search narrowed to the groups of these grouped rows, one row per image."""
        criteria = copy.deepcopy(self.search_criteria)
        criteria.one_per_group = False
        criteria.groups = [group for group in map(group_of, rows) if group is not None]
        return criteria

    def _group_members(self, rows: List[File]) -> List[File]:
        with self.context.database.bind_ctx(CORE_MODELS):
            return matching_files(self._group_criteria(rows), require_image=True)

    def set_grouped(self, grouped: bool):
        """One row per group (target, night, filter, exposure, folder) or one per image."""
        if self.search_criteria.one_per_group == grouped:
            return
        self.search_criteria.one_per_group = grouped
        self.update_search_criteria()

    def open_group(self, row: File):
        """Drill into a grouped row: its frames in a new tab, which keeps the group as a filter."""
        self.mainWindow.new_search_tab(self._group_criteria([row]))

    def _show_group_columns(self):
        grouped = self.search_criteria.one_per_group
        for key in GROUP_COLUMNS:
            self.dataView.setColumnHidden(COLUMN_INDEX[key], not grouped)

    def get_selected_distinct_files(self) -> List[File]:
        """The files of the selected rows, each once: what export, move, compress, plate solving
        and adding to a project work on."""
        return distinct_files(self.get_selected_files())

    def _counts_text(self) -> str:
        if self.search_criteria.one_per_group:
            return f"{self.total_result_files} files in {self.total_files} groups"
        return count_text(self.total_files, self.total_result_files)

    def _update_title(self):
        if self.search_criteria.is_empty():
            self.set_title(f"{self._counts_text()} (All)")
        else:
            self.set_title(f"{self._counts_text()} [{str(self.search_criteria)}]")

    def refresh_data_grid(self):
        """Trigger a search with the current search criteria."""
        if self.update_in_progress:
            return

        self.context.status_reporter.update_status("Searching...", bulk=True)
        self._show_group_columns()
        self.search_results_loader.search(self.search_criteria)

    def refresh_combo_options(self):
        tasks = [
            (self.filter_filter_combo, partial(distinct_values, column=Image.filter)),
            (self.filter_type_combo, partial(distinct_values, column=Image.image_type)),
            (self.filter_cam_combo, partial(distinct_values, column=Image.camera))
        ]
        self.combo_loader.run_tasks(tasks, self.search_criteria)

    def on_combo_options_loaded(self, target: QComboBox, data: List[str]):
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
        elif current_text == EMPTY_LABEL and target.findText(EMPTY_LABEL) < 0:
            target.addItem(EMPTY_LABEL)  # keep a chosen "<empty>" even when no file lacks the value

        target.setCurrentText(current_text)
        self.update_in_progress = False

    def on_data_selection_changed(self, selected, deselected):
        """Handle selection changes in the data grid."""
        if self.search_criteria.one_per_group:
            rows = self.get_selected_rows()
            if rows:
                frames = sum(group_value(row, 'group_frames') or 1 for row in rows)
                groups = f"{len(rows)} group" + ("s" if len(rows) != 1 else "")
                self.context.status_reporter.update_status(
                    f"{frames} frames in {groups} out of {self._counts_text()} selected", bulk=True)
            self.mainWindow.enable_actions_for_current_tab()
            return
        selected = self.get_selected_files()
        if selected:
            selection = count_text(len(selected), len(distinct_files(selected)))
            self.context.status_reporter.update_status(f"{selection} out of {self._counts_text()} selected",
                                                       bulk=True)

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
            self.data_model.removeRows(0, self.data_model.rowCount())

            # Reset total files counter when starting a new search
            self.total_files = total_files
            self.total_result_files = self.search_results_loader.total_result_files
            self._update_title()
            # clear the status bar
            self.context.status_reporter.update_status("")

        # Reset loading_more flag after processing
        self.loading_more = False

        # Add results to the data model

        for file in results:
            items = []
            for column in RESULT_COLUMNS:
                text, sort_value = cell(column, column.value(file))
                item = QStandardItem(text)
                if sort_value is not None:
                    item.setData(sort_value, SORT_ROLE)
                if column.tooltip is not None:
                    item.setToolTip(column.tooltip(file))
                items.append(item)
            items[0].setData(file, ROWID_ROLE)
            self.data_model.appendRow(items)

        # Resize columns to content
        if page == 0:
            self.resize_columns()
        self.update_sort_indicator()
        self.mainWindow.enable_actions_for_current_tab()

        # If we're loading all data for select all, continue loading or select all when done
        if self.select_all_pending:
            if self.has_more_results:
                # Continue loading more data
                self.load_all_remaining_data()
            else:
                # All data loaded, now select all
                self.select_all_pending = False
                self.dataView.selectAll()
                self.context.status_reporter.update_status(f"All {self._counts_text()} selected", bulk=False)

        if not self.has_more_results:
            self.data_fully_loaded.emit()

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

    def get_selected_roots_and_paths(self) -> List[RootAndPath]:
        indexes = self.filesystemTreeView.selectionModel().selectedIndexes()
        return self.library_tree_model.get_roots_and_paths(indexes)

    def get_selected_treenodes(self):
        indexes = self.filesystemTreeView.selectionModel().selectedIndexes()
        return [self.library_tree_model.getItem(idx) for idx in indexes]

    def on_tree_context_menu(self, position):
        index = self.filesystemTreeView.indexAt(position)
        if not index.isValid():
            return

        root_and_paths = self.get_selected_roots_and_paths()
        roots = [rp for rp in root_and_paths if rp.path is None]
        only_paths = len(root_and_paths) > 0 and len(roots) == 0
        only_roots = len(roots) == len(root_and_paths)

        menu = QMenu(self)
        create_project_action = menu.addAction("Create Project") if only_paths else None
        show_location_action = menu.addAction("Show location") if only_paths and len(root_and_paths) == 1 else None

        if only_roots:
            self.mainWindow.update_writer_actions()
            menu.setToolTipsVisible(True)
            menu.addAction(self.mainWindow.action_Scan_Libraries)

        action = menu.exec(self.filesystemTreeView.viewport().mapToGlobal(position))
        if action is None:
            return
        if create_project_action is not None and action == create_project_action:
            self.mainWindow.create_project_for_folder(root_and_paths)
        if show_location_action is not None and action == show_location_action:
            self.show_folder_location(root_and_paths[0])

    def show_context_menu(self, position):
        """Show context menu for the data view."""
        # TODO: copy-paste?
        # TODO: open associated project?
        # Get the index at the position
        index = self.dataView.indexAt(position)
        if not index.isValid():
            return

        selected_file = self.get_file_at_row(index.row())

        # Create context menu
        menu = QMenu(self)
        if self.search_criteria.one_per_group and selected_file is not None and group_of(selected_file):
            frames = group_value(selected_file, 'group_frames')
            menu.addAction(f"Show the {frames} frames of this group").triggered.connect(
                partial(self.open_group, selected_file))
            menu.addSeparator()
        view_action = menu.addAction("View")
        open_action = menu.addAction("Open File")
        show_location_action = menu.addAction("Show location")
        select_path_action = menu.addAction("Select path")
        show_header_action = menu.addAction("Show metadata")
        menu.addSeparator()
        export_action = menu.addAction("Export files")
        compress_action = menu.addAction("Compress files")
        menu.addSeparator()
        find_darks_action = menu.addAction("Find matching darks")
        find_flats_action = menu.addAction("Find matching flats")
        menu.addSeparator()
        is_solved = selected_file is not None and getattr(selected_file, 'has_wcs', False)
        plate_solve_action = menu.addAction("Force Re-Solve..." if is_solved else "Plate Solve...")
        plate_solve_action.triggered.connect(self.plate_solve_files)
        clear_solution_action = menu.addAction("Clear Plate Solution")
        clear_solution_action.setEnabled(is_solved)
        analyse_action = menu.addAction("Analyse Images...")
        menu.addAction(self.mainWindow.actionDiagnose_Quality)
        menu.addSeparator()
        mark_bad_action = menu.addAction("Mark as bad")
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
        if selected_file and hasattr(selected_file, 'image') and selected_file.image:
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

        compress_action.setEnabled(
            selected_file is not None and is_compressible(selected_file.name)
        )
        # Starting any of these needs the library write lock.
        busy = self.mainWindow.update_writer_actions()
        if busy:
            menu.setToolTipsVisible(True)
            for writer_action in (compress_action, plate_solve_action, analyse_action):
                writer_action.setEnabled(False)
                writer_action.setToolTip(library_busy_message(busy))

        select_project_actions = []
        if selected_file:
            with self.context.database.bind_ctx(CORE_MODELS):
                file_projects = list(
                    Project.select().join(ProjectFile).where(ProjectFile.file == selected_file.rowid)
                )
            if file_projects:
                menu.addSeparator()
                if len(file_projects) == 1:
                    a = menu.addAction(f"Select Project: {file_projects[0].name}")
                    a.setData(file_projects[0])
                    select_project_actions.append(a)
                else:
                    select_project_submenu = QMenu("Select Project", self)
                    menu.addMenu(select_project_submenu)
                    for proj in file_projects:
                        a = select_project_submenu.addAction(proj.name)
                        a.setData(proj)
                        select_project_actions.append(a)

        # Show the menu and get the selected action
        action = menu.exec(self.dataView.viewport().mapToGlobal(position))

        if action == view_action:
            self.view_file(index)
        elif action == open_action:
            self.open_file(index)
        elif action == show_location_action:
            self.show_file_location(index)
        elif action == select_path_action:
            self.select_path_in_tree(index)
        elif action == show_header_action:
            self.show_file_details(index)
        elif action == export_action:
            self.export_data()
        elif action == compress_action:
            self.compress_files()
        elif action == find_darks_action:
            if self.mainWindow:
                self.mainWindow.find_matching_darks()
        elif action == find_flats_action:
            if self.mainWindow:
                self.mainWindow.find_matching_flats()
        elif action == clear_solution_action:
            file = self.get_file_at_row(index.row())
            if file:
                self.clear_plate_solution(file, index.row())
        elif action == analyse_action:
            self.analyse_images()
        elif action == mark_bad_action:
            file = self.get_file_at_row(index.row())
            if file and self.search_criteria.one_per_group and group_of(file) is not None:
                self.mark_group_as_bad(file)
            elif file:
                self.mark_file_as_bad(file, index.row())
        elif action in project_actions:
            project = action.data()
            self.mainWindow.add_selection_to_project(project)
        elif action in select_project_actions:
            self.mainWindow.select_project_in_window(action.data())

    def open_file(self, index):
        """Open the file at the given index."""
        # Get the name item from the first column
        name_index = self.dataView.model().index(index.row(), 0)

        # Get the full filename from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.dataView.model().data(name_index, ROWID_ROLE)
            filename = file.full_filename()

        if filename:
            # clean up the environment for PixInsight/QT - can't find it's plugins otherwise
            old_env = os.environ.copy()
            for var in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
                os.environ.pop(var, None)
            try:
                # Open the file with the associated application
                QDesktopServices.openUrl(QUrl.fromLocalFile(filename))
            finally:
                os.environ.clear()
                os.environ.update(old_env)

    def show_folder_location(self, root_and_path: RootAndPath):
        root = LibraryRoot.get_by_id(root_and_path.root_id)
        if root:
            filename = os.path.join(root.path, root_and_path.path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(filename))


    def show_file_location(self, index):
        """Reveal the file in the system file manager."""
        name_index = self.dataView.model().index(index.row(), 0)
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.dataView.model().data(name_index, ROWID_ROLE)
            filename = file.full_filename()

        if not filename:
            return

        if sys.platform == 'win32':
            norm = os.path.normpath(filename)
            try:
                subprocess.Popen(["explorer", "/select,", norm])
            except OSError as e:
                logging.getLogger(__name__).warning("Could not open Explorer for %r: %s", norm, e)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(filename)))

    def view_file(self, index):
        """Open the file at the given index in the internal image viewer."""
        name_index = self.dataView.model().index(index.row(), 0)
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.dataView.model().data(name_index, ROWID_ROLE)
        if file and self.mainWindow:
            selected = self.dataView.selectionModel().selectedRows()
            rows = sorted(i.row() for i in selected) if len(selected) > 1 else None
            self.mainWindow.view_image(file, panel=self, row=index.row(), rows=rows)

    def get_file_at_row(self, row: int):
        """Return the File object at the given proxy-model row, or None."""
        if row < 0 or row >= self.dataView.model().rowCount():
            return None
        name_index = self.dataView.model().index(row, 0)
        with self.context.database.bind_ctx(CORE_MODELS):
            return self.dataView.model().data(name_index, ROWID_ROLE)

    def mark_file_as_bad(self, file: File, row: int, dialog_parent: QWidget | None = None) -> bool:
        """After confirmation, rename the file with BAD_ prefix and remove it from the database and grid.

        *row* is a row of the (sorted) view, not of the underlying data model.
        *dialog_parent* lets the image viewer show the prompt over itself.
        Returns False if the user declined or the rename failed."""
        with self.context.database.bind_ctx(CORE_MODELS):
            layers = Image.select().where(Image.file == file).count()
        whole_file = f"\n\nThis removes the whole file, with all {layers} of its image layers." if layers > 1 else ""
        answer = QMessageBox.question(
            dialog_parent or self, "Mark as Bad",
            f"Rename {file.name} to BAD_{file.name} and remove it from the library?{whole_file}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)  # Del, Enter keeps culling in the viewer quick
        if answer != QMessageBox.StandardButton.Yes:
            return False
        try:
            self._rename_as_bad(file)
        except OSError as e:
            QMessageBox.warning(self, "Mark as Bad", f"Could not rename file:\n{e}")
            return False
        self._warn_if_bad_files_would_return("BAD_" + file.name)
        # Every row of the file goes: one per image layer.
        rows = self._source_rows_of(file)
        for source_row in reversed(rows):
            self.data_model.removeRow(source_row)
        self.total_files -= len(rows)
        self.total_result_files -= 1
        self._update_title()
        return True

    def mark_group_as_bad(self, row: File) -> bool:
        """After confirmation, mark every file of a grouped row's group as bad (see
        mark_file_as_bad), then search again."""
        files = distinct_files(self._group_members([row]))
        answer = QMessageBox.question(
            self, "Mark as Bad",
            f"Rename the {len(files)} files of this group to BAD_<name> and remove them from the library?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        failed = []
        for file in files:
            try:
                self._rename_as_bad(file)
            except OSError as e:
                failed.append(f"{file.name}: {e}")
        if failed:
            QMessageBox.warning(self, "Mark as Bad", "Could not rename:\n" + "\n".join(failed[:10]))
        if len(failed) < len(files):
            self._warn_if_bad_files_would_return("BAD_" + files[0].name)
        self.refresh_data_grid()
        return not failed

    def _rename_as_bad(self, file: File):
        """Rename the file to BAD_<name> and remove it from the library. Raises OSError when the
        rename fails, leaving it as it was."""
        old_path = file.full_filename()
        os.rename(old_path, os.path.join(os.path.dirname(old_path), "BAD_" + file.name))
        with self.context.database.bind_ctx(CORE_MODELS):
            file.delete_instance()

    def _warn_if_bad_files_would_return(self, new_name: str):
        """A renamed file the bad-file patterns don't match is imported again by the next scan."""
        import fnmatch
        patterns = self.context.settings.get_bad_file_patterns().split("|")
        if not any(fnmatch.fnmatch(new_name.lower(), p) for p in patterns):
            QMessageBox.warning(
                self, "Mark as Bad",
                f"The file was renamed to:\n{new_name}\n\n"
                f"However, your current bad-file filter pattern ({self.context.settings.get_bad_file_patterns()!r}) "
                f"does not match this name. The file will be re-imported on the next library scan.\n\n"
                f"Go to Settings and add a pattern that matches 'bad*' to prevent this."
            )

    def _source_rows_of(self, file: File) -> list[int]:
        """The source-model rows of a file, one per image of it in the results."""
        return [row for row in range(self.data_model.rowCount())
                if (other := self.data_model.index(row, 0).data(ROWID_ROLE)) is not None
                and other.rowid == file.rowid]

    def clear_plate_solution(self, file: File, model_row: int):
        """Remove the file's plate solution and update the grid row: RA/Dec show the header's
        pointing position again, if it has one."""
        with self.context.database.bind_ctx(CORE_MODELS):
            ra, dec = plate_solution.clear(file)
        # The solution is the file's: every row of it (one per image layer) loses it. Source-model
        # rows, as a sorted proxy moves a row as soon as a sort value changes.
        for source_row in self._source_rows_of(file):
            _set_cells(self.data_model, source_row, coord_ra=ra, coord_dec=dec, solved="False")

    def on_item_double_clicked(self, index):
        """Handle double-click on an item in the data view: a grouped row opens its frames."""
        if self.search_criteria.one_per_group:
            row = self.get_file_at_row(index.row())
            if row is not None and group_of(row) is not None:
                self.open_group(row)
                return
        if self.context.settings.get_use_internal_viewer():
            self.view_file(index)
        else:
            self.open_file(index)

    def select_path_in_tree(self, index):
        """Select the path in the tree view."""
        # Get the name item from the first column
        name_index = self.dataView.model().index(index.row(), 0)

        # Get the file object from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.dataView.model().data(name_index, ROWID_ROLE)
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

    def show_file_details(self, index):
        """Show the cached FITS header for the selected file."""
        # Get the name item from the first column
        name_index = self.dataView.model().index(index.row(), 0)

        # Get the file object from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.dataView.model().data(name_index, ROWID_ROLE)
            if not file:
                QMessageBox.warning(self, "Warning", "No file selected.")
                return

            try:
                # The header of the row's image; an extension's merged with the primary's
                header = cached_header(file, image_key(file)[1])
                if header is None:
                    raise FitsHeader.DoesNotExist()
                wcs_header = plate_solution.load(file)

                # Show the header dialog
                dialog = HeaderDialog(header, wcs_header, parent=self)
                dialog.setAttribute(Qt.WA_DeleteOnClose)
                dialog.setWindowFlags(Qt.Window)
                dialog.show()

            except FitsHeader.DoesNotExist:
                QMessageBox.information(self, "No Cached Header",
                                        f"No cached header found for file: {file.name}")
            except Exception as e:
                logging.error(e, exc_info=True)
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

    # --- filters: each has a catalogue entry (photonfinder.filter_catalogue) and, for the ones
    # with a chip, an editor below that opens its dialog and writes the entry's fields.

    def edit_filter(self, entry: filter_catalogue.FilterEntry):
        """Open the filter's dialog (prefilled from the selected row or the current criteria)
        and apply what it returns."""
        editor = getattr(self, f"_edit_{entry.key}", None)
        if editor is not None:
            editor()
        self.mainWindow.enable_actions_for_current_tab()

    def _apply_filter(self, entry: filter_catalogue.FilterEntry):
        """The entry's fields were just set: show its chip (or none, if they leave it inactive)
        and search again."""
        self._show_catalogue_chip(entry)
        self.update_search_criteria()

    def set_project_filter(self, project: Project):
        self.search_criteria.project = project
        self._apply_filter(filter_catalogue.PROJECT)

    def set_diagnosis_filter(self, hidden: list[str]):
        self.search_criteria.diagnosis_hidden = hidden
        self._apply_filter(filter_catalogue.QUALITY_DIAGNOSIS)

    def _edit_exposure(self):
        from .ExposureDialog import ExposureDialog
        dialog = ExposureDialog(self.context)
        selected_image = self.get_selected_image()
        if selected_image and selected_image.exposure is not None:
            dialog.set_exposure(selected_image.exposure)
        elif self.search_criteria.exposure:
            try:
                dialog.set_exposure(float(self.search_criteria.exposure))
            except (ValueError, TypeError):
                pass
        if dialog.exec():
            self.search_criteria.exposure = str(dialog.get_exposure())
            self._apply_filter(filter_catalogue.EXPOSURE)

    def _edit_telescope(self):
        from .TelescopeDialog import TelescopeDialog
        dialog = TelescopeDialog(self.context)
        selected_image = self.get_selected_image()
        if selected_image and selected_image.telescope is not None:
            dialog.set_telescope(selected_image.telescope)
        elif self.search_criteria.telescope:
            dialog.set_telescope(self.search_criteria.telescope)
        if dialog.exec():
            self.search_criteria.telescope = dialog.get_telescope()
            self._apply_filter(filter_catalogue.TELESCOPE)

    def _edit_binning(self):
        from .BinningDialog import BinningDialog
        dialog = BinningDialog(self.context)
        selected_image = self.get_selected_image()
        if selected_image and selected_image.binning is not None:
            dialog.set_binning(selected_image.binning)
        elif self.search_criteria.binning:
            try:
                dialog.set_binning(int(self.search_criteria.binning))
            except (ValueError, TypeError):
                pass
        if dialog.exec():
            self.search_criteria.binning = str(dialog.get_binning())
            self._apply_filter(filter_catalogue.BINNING)

    def _edit_gain(self):
        from .GainDialog import GainDialog
        dialog = GainDialog(self.context)
        selected_image: Image = self.get_selected_image()
        if selected_image and selected_image.gain is not None:
            dialog.set_gain(selected_image.gain)
        elif self.search_criteria.gain:
            try:
                dialog.set_gain(int(self.search_criteria.gain))
            except (ValueError, TypeError):
                pass
        if selected_image and selected_image.offset is not None:
            dialog.set_offset(selected_image.offset)
        elif self.search_criteria.offset is not None:
            try:
                dialog.set_offset(int(self.search_criteria.offset))
            except (ValueError, TypeError):
                pass
        if dialog.exec():
            self.search_criteria.gain = str(dialog.get_gain())
            self.search_criteria.offset = dialog.get_offset()
            self._apply_filter(filter_catalogue.GAIN)

    def _edit_temperature(self):
        from .TemperatureDialog import TemperatureDialog
        dialog = TemperatureDialog(self.context)
        selected_image = self.get_selected_image()
        if selected_image and selected_image.set_temp is not None:
            dialog.set_temperature(selected_image.set_temp)
        elif self.search_criteria.temperature:
            try:
                dialog.set_temperature(float(self.search_criteria.temperature))
            except (ValueError, TypeError):
                pass
        if dialog.exec():
            self.search_criteria.temperature = str(dialog.get_temperature())
            self._apply_filter(filter_catalogue.TEMPERATURE)

    def _edit_header_text(self):
        from textwrap import dedent
        header_text, ok = QInputDialog.getText(self, "Add generic header filter",
                                               dedent("""\
                                               Search the header cache for any value.
                                               Supports finding by value (KEY=VALUE,KEY<VALUE,...) or just free text.
                                               Beware that this involves decompressing all headers and does not use an index.
                                               If there are no other filters to limit results the search will be slow.
                                               """), text=self.search_criteria.header_text)
        if ok and header_text:
            self.search_criteria.header_text = header_text
            self._apply_filter(filter_catalogue.HEADER_TEXT)

    def _edit_plate_solved(self):
        options = ["Solved", "Unsolved"]
        current = "Solved" if self.search_criteria.plate_solved else "Unsolved"
        choice, ok = QInputDialog.getItem(self, "Plate Solved Filter", "Show:", options, options.index(current), False)
        if ok:
            self.search_criteria.plate_solved = choice == "Solved"
            self._apply_filter(filter_catalogue.PLATE_SOLVED)

    def _edit_coordinates(self):
        from .CoordinatesDialog import CoordinatesDialog
        dialog = CoordinatesDialog(self.context)
        selected_image = self.get_selected_image()
        if selected_image and selected_image.coord_ra is not None and selected_image.coord_dec is not None:
            import astropy.units as u
            try:
                coords = selected_image.get_sky_coord()
                ra_str = coords.ra.to_string(unit=u.hour, sep=':', precision=2)
                dec_str = coords.dec.to_string(unit=u.deg, sep=':', precision=2)
                dialog.set_coordinates(ra_str, dec_str, self.search_criteria.coord_radius)
            except Exception as e:
                logging.error(f"Error setting coordinates from selected image: {str(e)}")
        elif self.search_criteria.coord_ra and self.search_criteria.coord_dec:
            dialog.set_coordinates(self.search_criteria.coord_ra, self.search_criteria.coord_dec,
                                   self.search_criteria.coord_radius, self.search_criteria.coord_query)
        if dialog.exec():
            ra, dec, radius = dialog.get_coordinates()
            self.search_criteria.coord_ra = ra
            self.search_criteria.coord_dec = dec
            self.search_criteria.coord_radius = radius
            self.search_criteria.coord_query = dialog.get_query_label() or ""
            self._apply_filter(filter_catalogue.COORDINATES)

    def _edit_dates(self):
        """A range in local time (the default, as the grid shows) or in UTC. Nothing is
        converted: each basis has its own criteria fields, and applying one clears the other."""
        dialog = DateRangeDialog(self.context)
        criteria = self.search_criteria
        selected_image = self.get_selected_image()
        if selected_image is not None:
            dialog.set_range(True, selected_image.local_time, None)
            dialog.set_range(False, selected_image.date_obs, None)
        dialog.set_range(True, criteria.local_start, criteria.local_end)
        dialog.set_range(False, criteria.start_datetime, criteria.end_datetime)
        dialog.set_local(criteria.start_datetime is None and criteria.end_datetime is None)
        if dialog.exec():
            start, end = dialog.get_datetime_range()
            local = dialog.is_local()
            criteria.local_start, criteria.local_end = (start, end) if local else (None, None)
            criteria.start_datetime, criteria.end_datetime = (None, None) if local else (start, end)
            self._apply_filter(filter_catalogue.DATES)

    def _edit_night(self):
        """One observing night: the selected image's, else the filter's, else last night."""
        from .NightDialog import NightDialog
        dialog = NightDialog(self)
        selected_image = self.get_selected_image()
        night = session_night(selected_image.local_time) if selected_image is not None else None
        dialog.set_night(night or self.search_criteria.session_night or _last_night(self.context))
        if dialog.exec():
            self.search_criteria.session_night = dialog.get_night()
            self._apply_filter(filter_catalogue.NIGHT)

    def _edit_image_size(self):
        from .RangeFilterDialogs import ImageSizeDialog
        dialog = ImageSizeDialog(self)
        selected_image = self.get_selected_image()
        if selected_image and (selected_image.width is not None or selected_image.height is not None):
            dialog.set_from_image(selected_image)
        else:
            dialog.set_from_criteria(self.search_criteria.width_min, self.search_criteria.width_max,
                                     self.search_criteria.height_min, self.search_criteria.height_max)
        if dialog.exec():
            (self.search_criteria.width_min, self.search_criteria.width_max,
             self.search_criteria.height_min, self.search_criteria.height_max) = dialog.get_size_criteria()
            self._apply_filter(filter_catalogue.IMAGE_SIZE)

    def _edit_plate_scale(self):
        from .RangeFilterDialogs import PlateScaleDialog
        dialog = PlateScaleDialog(self)
        selected_image = self.get_selected_image()
        if selected_image and selected_image.coord_scale is not None:
            dialog.set_from_image(selected_image)
        else:
            dialog.set_from_criteria(self.search_criteria.scale_min, self.search_criteria.scale_max)
        if dialog.exec():
            self.search_criteria.scale_min, self.search_criteria.scale_max = dialog.get_scale_criteria()
            self._apply_filter(filter_catalogue.PLATE_SCALE)

    def _edit_image_quality(self):
        from .RangeFilterDialogs import ImageQualityDialog
        dialog = ImageQualityDialog(self)
        stats = self._selected_row_stats()
        if stats is not None:
            dialog.set_from_stats(stats)
        else:
            dialog.set_from_criteria(self.search_criteria)
        if dialog.exec():
            for key, value in dialog.get_quality_criteria().items():
                setattr(self.search_criteria, key, value)
            self._apply_filter(filter_catalogue.IMAGE_QUALITY)

    def _edit_quality_diagnosis(self):
        from .RangeFilterDialogs import DiagnosisFilterDialog
        dialog = DiagnosisFilterDialog(diagnosis_counts(self.search_criteria), self)
        dialog.set_hidden(self.search_criteria.diagnosis_hidden)
        if dialog.exec():
            self.set_diagnosis_filter(dialog.get_hidden())
        elif dialog.diagnose_requested:
            self.mainWindow.open_diagnose_quality()

    def _selected_row_stats(self) -> Optional[SimpleNamespace]:
        """The selected row's image statistics, or None when there is no row or it has none.
        A search row carries them as the query's stats_* columns (see search_row_stat), not as
        ImageStats fields."""
        selected_file = self.get_selected_file()
        if selected_file is None:
            return None
        stats = SimpleNamespace(**{field: search_row_stat(selected_file, f"stats_{field}") for field in (
            "star_count", "fwhm_median", "background_median", "background_rms", "elongation_median")})
        return stats if any(v is not None for v in vars(stats).values()) else None

    def _drop_chip(self, button: 'FilterButton'):
        """Remove a chip without resetting its filter's criteria."""
        try:
            button.on_remove_filter.disconnect()
        except (RuntimeError, TypeError):  # nothing connected
            pass
        self.remove_filter_button_control(button)

    def _show_catalogue_chip(self, entry: filter_catalogue.FilterEntry):
        """Show the entry's chip with its label for the current criteria, or none when the
        criteria leave the filter inactive. Removing the chip resets the entry's fields."""
        current = self.advanced_options.get(entry)
        if current is not None:
            self._drop_chip(current)  # the criteria already hold the new values
        if not entry.is_active(self.search_criteria):
            return
        filter_button = FilterButton(self, entry.chip_label(self.search_criteria), entry)
        filter_button.on_remove_filter.connect(lambda: entry.reset(self.search_criteria))
        self.add_filter_button_control(filter_button)

    def get_selected_file(self) -> File | None:
        """Get the image data of the first selected file, if any."""
        selected_rows = self.dataView.selectionModel().selectedRows()
        if not selected_rows:
            return None

        # Get the first selected row
        first_row = selected_rows[0].row()

        # Get the name item from the first column
        name_index = self.dataView.model().index(first_row, 0)

        # Get the file object from the name item's data
        with self.context.database.bind_ctx(CORE_MODELS):
            file = self.dataView.model().data(name_index, ROWID_ROLE)
            return file

    def get_selected_image(self) -> Image | None:
        file = self.get_selected_file()
        if file and hasattr(file, 'image') and file.image:
            return file.image
        else:
            return None


    def export_data(self):
        # Get the selected files
        selected_files = self.get_selected_files()

        # If no files are selected, use the search criteria
        if not selected_files:
            # Get the total number of files matching the current filters
            # If there are too many files, ask for confirmation
            if self.total_files > 400:
                response = QMessageBox.question(
                    self,
                    "Export Confirmation",
                    f"No files are selected. Do you want to export all {self._counts_text()} matching the current filters?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if response == QMessageBox.No:
                    return

        # Pass the search criteria instead of loading all files
        from .ExportDialog import ExportDialog
        dialog = ExportDialog(self.context, self.search_criteria, selected_files, parent=self)
        dialog.show()

    def compress_files(self):
        from .CompressFilesDialog import CompressFilesDialog
        selected = self.get_selected_distinct_files()
        if selected:
            dialog = CompressFilesDialog(
                context=self.context, files=selected, search_criteria=None, parent=self)
        else:
            dialog = CompressFilesDialog(
                context=self.context, files=None, search_criteria=self.search_criteria, parent=self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.compression_complete.connect(self.refresh_data_grid)
        dialog.exec()

    def _set_combo_value(self, combo: QComboBox, value: str | None):
        """Set the value of a combo box based on the search criteria."""
        if value == "":
            combo.setCurrentText(RESET_LABEL)
        elif value is None:
            if combo.findText(EMPTY_LABEL) < 0:  # the options may not list it (yet)
                combo.addItem(EMPTY_LABEL)
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
        self.search_criteria = copy.deepcopy(criteria)
        self.update_in_progress = True
        # Wait for the list of library roots, not for any root's folders: those load per root
        # as the tree expands, and _find_and_select_node already defers a path until then.
        if not self.library_tree_model.root_node.child(0).loaded:
            self.library_tree_model.library_roots_loader.library_roots_loaded.connect(self._apply_pending_path_criteria)
        else:
            self.filesystemTreeView.selectionModel().clearSelection()
            for path in self.search_criteria.paths:
                self._find_and_select_node(path)

        self.checkBox.setChecked(criteria.paths_as_prefix)

        # Update combo boxes. None is a value here: "<empty>", the files that have none.
        self._set_combo_value(self.filter_type_combo, criteria.type)
        self._set_combo_value(self.filter_filter_combo, criteria.filter)
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

        # Clear existing filter buttons, without resetting the criteria just restored
        for button in list(self.advanced_options.values()):
            self._drop_chip(button)
        self.advanced_options.clear()

        for entry in filter_catalogue.ENTRIES:
            if entry.has_chip:
                self._show_catalogue_chip(entry)

        self.update_search_criteria()

    def plate_solve_files(self):
        # A solution is the file's (of its first image), so each file is solved once.
        selected_files = self.get_selected_distinct_files()
        if not selected_files and self.total_files > 100:
            response = QMessageBox.question(
                self,
                "Plate solving confirmation",
                "No files are selected. Are you sure you want to plate solve all files matching the current filters?",
                QMessageBox.Yes | QMessageBox.No
            )
            if response == QMessageBox.No:
                return

        dialog = PlateSolveDialog(
            context=self.context,
            files=selected_files if selected_files else None,
            search_criteria=self.search_criteria,
            parent=self
        )
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.solving_complete.connect(self.on_files_solved)
        dialog.wcs_copied.connect(self.on_files_solved)
        dialog.show()

    def analyse_images(self):
        selected_files = self.get_selected_files()
        if not selected_files and self.total_files > 100:
            if QMessageBox.question(
                self, "Image Analysis",
                f"No files selected. Analyse all {self._counts_text()} matching the current filters?",
                QMessageBox.Yes | QMessageBox.No,
            ) == QMessageBox.No:
                return
        dialog = ImageAnalysisDialog(
            context=self.context,
            files=selected_files if selected_files else None,
            search_criteria=self.search_criteria,
            parent=self,
            on_diagnose=self.mainWindow.open_diagnose_quality,
        )
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.analysis_complete.connect(self.on_files_analysed)
        dialog.show()

    def on_files_analysed(self, task: ImageAnalysisTask):
        if not task.analyzed_files:
            return
        # By image: a file's layers are analysed, and are rows, of their own.
        result_map = {image_key(f): r for f, r in task.analyzed_files}
        model = self.data_model  # not the sorted proxy, which moves rows as sort values change
        for row in range(model.rowCount()):
            file = model.index(row, 0).data(ROWID_ROLE)
            if file is None or image_key(file) not in result_map:
                continue
            r = result_map[image_key(file)]

            measured = dict(background=r.background_median, background_rms=r.background_rms,
                            star_count=r.star_count, fwhm=r.fwhm_median, elongation=r.elongation_median)
            # Re-analysing clears the stored diagnosis; a value not measured keeps its cell.
            _set_cells(model, row, diagnosis="", **{k: v for k, v in measured.items() if v is not None})

    def on_files_solved(self, task: PlateSolveTask):
        solved_ids = {f.rowid for f in task.solved_files}
        # Each row shows its own image's centre: the solved row's from its loaded Image (storing
        # the solution updated it), another layer's as stored, as a solution reaches only the
        # layers of its size.
        centres = {image_key(f): (f.image.coord_ra, f.image.coord_dec)
                   for f in task.solved_files if isinstance(f.__dict__.get('image'), Image)}
        model = self.data_model  # not the sorted proxy, which moves rows as sort values change
        other_layers = {image_key(file) for row in range(model.rowCount())
                        if (file := model.index(row, 0).data(ROWID_ROLE)) is not None
                        and file.rowid in solved_ids and image_key(file) not in centres
                        and getattr(file.__dict__.get('image'), 'rowid', None) is not None}
        if other_layers:
            with self.context.database.bind_ctx(CORE_MODELS):
                centres.update({(file_id, hdu): (ra, dec) for file_id, hdu, ra, dec in
                                Image.select(Image.file, Image.hdu, Image.coord_ra, Image.coord_dec)
                                .where(Image.file.in_(list({file_id for file_id, _ in other_layers}))).tuples()
                                if (file_id, hdu) in other_layers})
        for row in range(model.rowCount()):
            file = model.index(row, 0).data(ROWID_ROLE)
            if file is None or file.rowid not in solved_ids:
                continue
            if image_key(file) in centres:
                ra, dec = centres[image_key(file)]
                _set_cells(model, row, coord_ra=ra, coord_dec=dec)
            _set_cells(model, row, solved="True")

        if solved_ids and self.search_criteria.plate_solved is not None:
            self.refresh_data_grid()

    def report_list_files(self):
        selected_files = self.get_selected_distinct_files()
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
        if not self.context.settings.get_telescopius_api_key():
            QMessageBox.information(
                self, "Telescopius API Key Required",
                "Comparing with a Telescopius list uses the Telescopius API, which requires a personal API key.\n\n"
                "Request a key at https://telescopius.com/settings/api-keys and enter it under Settings > Telescopius.")
            return
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

    def report_catalog(self):
        from .CatalogReportWindow import CatalogReportWindow
        window = CatalogReportWindow(context=self.context, search_panel=self, parent=self.mainWindow)
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.show()

    def print_task_complete(self):
        self.context.status_reporter.update_status("Task complete")

    def on_projects_changed(self, projects: Collection[Project], change: Change):
        # TODO partial update
        self.update_search_criteria()

    def on_project_links_changed(self, project_files: Collection[ProjectFile], change: Change):
        #TODO partial update
        self.update_search_criteria()


def _get_combo_value(combo: QComboBox) -> str | None:
    if combo.currentText() == RESET_LABEL:
        return ""
    elif combo.currentText() == EMPTY_LABEL:
        return None
    else:
        return combo.currentText()


class FilterButton(QPushButton):
    on_remove_filter = Signal()
    right_clicked = Signal()

    def __init__(self, parent, text: str, filter_type) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setMinimumHeight(20)
        self.setMinimumWidth(20)
        self.setToolTip("Click to edit, right click to remove filter")
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        palette: QPalette = self.palette()
        brush: QBrush = palette.windowText()
        self.setStyleSheet(
            f"border-radius : 10px; border : 1px solid {brush.color().name()}; padding-left:10px; padding-right:10px")
        self.filter_type = filter_type

    def mouseReleaseEvent(self, e, /):
        if e.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
        else:
            super().mouseReleaseEvent(e)


def _set_cells(model, source_row: int, **values):
    """Set cells of a row of the source model (not the sorted proxy, which moves rows as sort
    values change) by column key, formatted as the grid formats them."""
    for key, value in values.items():
        text, sort_value = cell(RESULT_COLUMNS[COLUMN_INDEX[key]], value)
        index = model.index(source_row, COLUMN_INDEX[key])
        model.setData(index, text, Qt.DisplayRole)
        model.setData(index, sort_value, SORT_ROLE)

import logging
import time

from PySide6.QtCore import *
from PySide6.QtWidgets import *

from core import ApplicationContext
from models import LibraryRoot, SearchCriteria
from .LibraryTreeModel import LibraryTreeModel
from .generated.SearchPanel_ui import Ui_SearchPanel

# Using the new database-backed tree model for filesystemTreeView
class SearchPanel(QFrame, Ui_SearchPanel):
    def __init__(self, context: ApplicationContext, parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

        self.context = context
        self.search_criteria = SearchCriteria()
        # Initialize the library tree model
        self.library_tree_model = LibraryTreeModel(self)

        # Set up the tree view
        self.filesystemTreeView.setModel(self.library_tree_model)
        self.filesystemTreeView.setHeaderHidden(True)
        self.filesystemTreeView.setItemsExpandable(True)
        self.filesystemTreeView.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

        # Store library roots for later use
        self.library_roots = []
        QTimer.singleShot(0, self.load_library_roots)

    def load_library_roots(self):
        """
        Load library roots and update the tree model.
        """
        self.library_roots = list(LibraryRoot.select())

        logging.debug(f"Found {len(self.library_roots)} library roots")
        # Update the tree model with the results
        self.library_tree_model.load_library_roots(self.library_roots)

        # Don't expand all at once - just expand the first level
        # to show library roots
        all_libraries_index = self.library_tree_model.index(0, 0, QModelIndex())
        self.filesystemTreeView.expand(all_libraries_index)

        logging.debug("Expanded first level of tree view")
        logging.debug("Library tree model updated")
        self.filesystemTreeView.setRootIndex(QModelIndex())
        logging.debug("Tree view root index set")

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

        # Get the file system model for the selected index
        file_system_model = self.library_tree_model.get_file_system_model_for_index(index)
        if file_system_model:
            # If a library root is selected, log it
            root_path = self.library_tree_model.get_root_path_for_index(index)
            if root_path:
                logging.debug(f"Selected library root with path: {root_path}")
        else:
            # If "All locations" is selected, log it
            item = self.library_tree_model.getItem(index)
            if item and item.data(0) == "All locations":
                logging.debug("Selected All locations")

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


class FilterButton(QPushButton):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setText("FilterTest" + str(int(time.time())))
        self.setMinimumHeight(20)
        self.setStyleSheet("border-radius : 10px; border : 2px solid black; padding-left:10px; padding-right:10px")

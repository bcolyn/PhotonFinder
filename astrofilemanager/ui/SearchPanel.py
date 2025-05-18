import asyncio
import logging
import time

from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtMultimedia import *
from PySide6.QtWidgets import *

from astrofilemanager.models import LibraryRoot
from .generated.SearchPanel_ui import Ui_SearchPanel


class SearchPanel(QFrame, Ui_SearchPanel):
    def __init__(self, parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

        # Store library roots for later use
        self.library_roots = []

        # Initialize file system model to None
        self.file_system_model = None

        # Connect combobox signal
        self.libraryComboBox.currentIndexChanged.connect(self.on_library_changed)

        # Schedule async query for library roots after the event loop is running
        logging.debug("Scheduling async query for library roots")
        QTimer.singleShot(0, self.start_load_library_roots)

    def start_load_library_roots(self):
        """
        Start loading library roots asynchronously.
        This method is called after the event loop is running.
        """
        try:
            logging.debug("Starting async query for library roots")
            asyncio.create_task(self.load_library_roots())
        except Exception as e:
            logging.error(f"Error starting library roots query: {e}")

    async def load_library_roots(self):
        """
        Asynchronously load library roots and populate the combobox.
        """
        try:
            logging.debug("Querying library roots asynchronously")
            # Run database query in a separate thread to avoid blocking the UI
            self.library_roots = await asyncio.to_thread(self._query_library_roots)

            logging.debug(f"Found {len(self.library_roots)} library roots")
            # Update the UI with the results
            self.libraryComboBox.clear()
            for root in self.library_roots:
                self.libraryComboBox.addItem(root.name)
            logging.debug("Library combobox populated")
        except Exception as e:
            # Log the error but don't crash the application
            logging.error(f"Error loading library roots: {e}")

    def _query_library_roots(self):
        """
        Query all library roots from the database.
        This method runs in a separate thread.
        """
        return list(LibraryRoot.select())

    def get_selected_library_root(self):
        """
        Get the currently selected library root.

        Returns:
            LibraryRoot or None: The selected library root, or None if no library root is selected.
        """
        index = self.libraryComboBox.currentIndex()
        if index >= 0 and index < len(self.library_roots):
            return self.library_roots[index]
        return None

    def on_library_changed(self, index):
        """
        Handle selection changes in the library combobox.

        Args:
            index (int): The index of the newly selected item.
        """
        library_root = self.get_selected_library_root()
        if library_root:
            logging.debug(f"Library root changed to: {library_root.name}")

            # Create a QFileSystemModel if it doesn't exist
            if self.file_system_model is None:
                self.file_system_model = QFileSystemModel()
                self.file_system_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
                self.filesystemTreeView.setModel(self.file_system_model)
                self.filesystemTreeView.hideColumn(1)
                self.filesystemTreeView.hideColumn(2)
                self.filesystemTreeView.hideColumn(3)

            # Set the library root's path as the model's rootPath
            root_path = library_root.path
            logging.debug(f"Setting root path to: {root_path}")

            # Set the root path and update the tree view
            root_index = self.file_system_model.setRootPath(root_path)
            self.filesystemTreeView.setRootIndex(root_index)
        else:
            logging.debug("No library root selected")

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

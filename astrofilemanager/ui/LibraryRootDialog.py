import logging
from typing import Optional

from PySide6.QtCore import Slot, Qt
from PySide6.QtWidgets import QDialog, QTableWidgetItem, QMessageBox
from peewee import Database

from astrofilemanager.models import LibraryRoot
from astrofilemanager.ui.LibraryRootEditDialog import LibraryRootEditDialog
from astrofilemanager.ui.generated.LibraryRootDialog_ui import Ui_LibraryRootDialog


class LibraryRootDialog(QDialog, Ui_LibraryRootDialog):
    """
    Dialog for managing LibraryRoot entities.
    Shows a list of configured libraries with add, edit, and delete buttons.
    """
    
    def __init__(self, connection: Database, parent=None):
        """
        Initialize the dialog.
        
        Args:
            connection: Database connection object
            parent: Parent widget
        """
        super(LibraryRootDialog, self).__init__(parent)
        self.setupUi(self)
        
        # Initialize the LibraryRoot model with the connection
        LibraryRoot.initialize(connection)
        self.connection = connection
        
        # Set up the table
        self.libraryTable.setColumnWidth(0, 200)  # Name column
        self.libraryTable.setColumnWidth(1, 350)  # Path column
        
        # Load the library roots
        self.load_library_roots()
        
        # Enable/disable edit and delete buttons based on selection
        self.libraryTable.itemSelectionChanged.connect(self.update_button_states)
        self.update_button_states()
    
    def load_library_roots(self):
        """
        Load all library roots from the database and display them in the table.
        """
        self.libraryTable.setRowCount(0)  # Clear the table
        
        try:
            library_roots = LibraryRoot.select().order_by(LibraryRoot.name)
            
            for i, library_root in enumerate(library_roots):
                self.libraryTable.insertRow(i)
                
                # Create and set the name item
                name_item = QTableWidgetItem(library_root.name)
                name_item.setData(Qt.UserRole, library_root.id)  # Store the ID for later use
                self.libraryTable.setItem(i, 0, name_item)
                
                # Create and set the path item
                path_item = QTableWidgetItem(library_root.path)
                self.libraryTable.setItem(i, 1, path_item)
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while loading library roots: {str(e)}")
            logging.exception("Error loading library roots")
    
    def update_button_states(self):
        """
        Enable or disable the edit and delete buttons based on whether a row is selected.
        """
        has_selection = len(self.libraryTable.selectedItems()) > 0
        self.editButton.setEnabled(has_selection)
        self.deleteButton.setEnabled(has_selection)
    
    def get_selected_library_root(self) -> Optional[LibraryRoot]:
        """
        Get the currently selected library root.
        
        Returns:
            The selected LibraryRoot object, or None if no row is selected.
        """
        selected_items = self.libraryTable.selectedItems()
        if not selected_items:
            return None
        
        # Get the ID from the first column of the selected row
        row = selected_items[0].row()
        library_id = self.libraryTable.item(row, 0).data(Qt.UserRole)
        
        try:
            return LibraryRoot.get_by_id(library_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while retrieving the library root: {str(e)}")
            logging.exception("Error retrieving library root")
            return None
    
    @Slot()
    def add_library(self):
        """
        Open the dialog to add a new library root.
        """
        dialog = LibraryRootEditDialog(self.connection, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.load_library_roots()
    
    @Slot()
    def edit_library(self):
        """
        Open the dialog to edit the selected library root.
        """
        library_root = self.get_selected_library_root()
        if not library_root:
            return
        
        dialog = LibraryRootEditDialog(self.connection, library_root, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.load_library_roots()
    
    @Slot()
    def delete_library(self):
        """
        Delete the selected library root after confirmation.
        """
        library_root = self.get_selected_library_root()
        if not library_root:
            return
        
        # Confirm deletion
        result = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete the library root '{library_root.name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if result == QMessageBox.Yes:
            try:
                library_root.delete_instance()
                logging.info(f"Deleted library root: {library_root.name}")
                self.load_library_roots()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"An error occurred while deleting: {str(e)}")
                logging.exception("Error deleting library root")
import logging
from typing import Optional

from PySide6.QtCore import QEvent, Slot, Qt
from PySide6.QtWidgets import QDialog, QTableWidgetItem, QMessageBox

from photonfinder.models import LibraryRoot
from photonfinder.ui.LibraryRootEditDialog import LibraryRootEditDialog
from photonfinder.ui.generated.LibraryRootDialog_ui import Ui_LibraryRootDialog
from photonfinder.core import ApplicationContext, library_busy_message
from photonfinder.ui.BackgroundLoader import ImageReindexWorker


ROOT_CHANGE_WRITER = "Library root change"


class LibraryRootDialog(QDialog, Ui_LibraryRootDialog):
    """
    Dialog for managing LibraryRoot entities.
    Shows a list of configured libraries with add, edit, and delete buttons.
    """

    def __init__(self, context: ApplicationContext, parent=None):
        super(LibraryRootDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.has_changes = False
        # Set up the table
        self.libraryTable.setColumnWidth(0, 150)  # Name column
        self.libraryTable.setColumnWidth(1, 250)  # Path column
        self.libraryTable.setColumnWidth(2, 200)  # Description column

        # Initialize reindex worker
        self.reindex_worker = None
        # What holds the library write lock, if anything. Deleting, editing and rebuilding
        # the index all need it.
        self._library_busy: str | None = None

        # Load the library roots
        self.load_library_roots()

        # Enable/disable edit and delete buttons based on selection
        self.libraryTable.itemSelectionChanged.connect(self.update_button_states)
        # The table is not editable in place (the cells are a view of the database, and
        # inline edits were silently discarded), so double-click opens the edit dialog.
        self.libraryTable.itemDoubleClicked.connect(lambda _item: self.edit_library())
        context.signal_bus.library_write_lock_changed.connect(self._refresh_library_busy)
        self._refresh_library_busy()

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
                name_item.setData(Qt.UserRole, library_root.rowid)  # Store the ID for later use
                self.libraryTable.setItem(i, 0, name_item)

                # Create and set the path item
                path_item = QTableWidgetItem(library_root.path)
                self.libraryTable.setItem(i, 1, path_item)

                # Create and set the description item; the full text goes in the tooltip
                # since a long description will not fit the column.
                description = library_root.description or ""
                description_item = QTableWidgetItem(description)
                description_item.setToolTip(description)
                self.libraryTable.setItem(i, 2, description_item)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while loading library roots: {str(e)}")
            logging.exception("Error loading library roots")

    def update_button_states(self):
        """
        Enable or disable the edit and delete buttons based on whether a row is selected.
        """
        has_selection = len(self.libraryTable.selectedItems()) > 0
        busy = self._library_busy
        tip = library_busy_message(busy) if busy else ""
        self.editButton.setEnabled(has_selection and not busy)
        self.deleteButton.setEnabled(has_selection and not busy)
        self.reindexButton.setEnabled(self.reindex_worker is None and not busy)
        for button in (self.editButton, self.deleteButton, self.reindexButton):
            button.setToolTip(tip)

    def _refresh_library_busy(self):
        """Ask the lock when it changes here or the dialog is activated: for another window's
        writer that means taking it for a moment."""
        self._library_busy = self.context.library_write_lock.busy_with()
        self.update_button_states()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._refresh_library_busy()

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
        dialog = LibraryRootEditDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.load_library_roots()
            self.has_changes = True

    @Slot()
    def edit_library(self):
        """
        Open the dialog to edit the selected library root.
        """
        library_root = self.get_selected_library_root()
        if not library_root:
            return

        dialog = LibraryRootEditDialog(parent=self, library_root=library_root,
                                       write_lock=self.context.library_write_lock)
        if dialog.exec() == QDialog.Accepted:
            self.load_library_roots()
            self.has_changes = True

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
            # Deleting a root deletes all its files' rows, so not under a running writer.
            lock = self.context.library_write_lock
            if not lock.acquire(blocking=False, holder=ROOT_CHANGE_WRITER):
                QMessageBox.warning(self, "Library Busy",
                                    f"Not deleted: {library_busy_message(lock.busy_with() or 'Another job')}")
                return
            try:
                library_root.delete_instance()
                logging.info(f"Deleted library root: {library_root.name}")
                self.load_library_roots()
                self.has_changes = True
            except Exception as e:
                QMessageBox.critical(self, "Error", f"An error occurred while deleting: {str(e)}")
                logging.exception("Error deleting library root")
            finally:
                lock.release()

    @Slot()
    def reindex_images(self):
        """
        Reindex all image metadata by replacing each row of the Image table,
        with one built by normalize_fits_header from the cached FITS header.
        """
        # Check if a reindexing is already in progress
        if self.reindex_worker is not None:
            logging.warning("Reindexing already in progress, skipping.")
            return

        worker = ImageReindexWorker(self.context)
        worker.finished.connect(self._reindex_finished)
        refused = worker.reindex_images()  # holds the library write lock until it ends
        if refused:
            QMessageBox.warning(self, "Library Busy", f"Rebuild index not started: {refused}")
            return
        self.reindex_worker = worker
        self.update_button_states()

    def _reindex_finished(self):
        """Called when reindexing is finished."""
        self.reindex_worker = None
        self.update_button_states()

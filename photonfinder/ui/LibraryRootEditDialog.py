import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from photonfinder.core import library_busy_message
from photonfinder.models import LibraryRoot, norm_db_path
from photonfinder.ui.generated.LibraryRootEditDialog_ui import Ui_LibraryRootEditDialog


class LibraryRootEditDialog(QDialog, Ui_LibraryRootEditDialog):
    """
    Dialog for creating or updating a LibraryRoot entity.
    """

    def __init__(self, library_root: Optional[LibraryRoot] = None, parent=None, write_lock=None):
        """*write_lock* is the library write lock, taken to change an existing root: a writer
        running under a changed path would work on the wrong files."""
        super(LibraryRootEditDialog, self).__init__(parent)
        self.setupUi(self)

        self.library_root = library_root
        self.write_lock = write_lock
        self.is_new = library_root is None

        # Set up the dialog based on whether we're creating or editing
        if self.is_new:
            self.setWindowTitle("Create Library Root")
        else:
            self.setWindowTitle("Edit Library Root")
            self.nameLineEdit.setText(library_root.name)
            self.pathLineEdit.setText(library_root.path)
            self.descriptionTextEdit.setPlainText(library_root.description or "")

    @Slot()
    def browse_directory(self):
        """
        Open a directory picker dialog and set the selected directory in the path field.
        """
        # Use native file dialog for a more native look and feel
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Directory",
            self.pathLineEdit.text() if self.pathLineEdit.text() else str(Path.home()),
            options=QFileDialog.Option.ShowDirsOnly  # Explicitly avoid DontUseNativeDialog
        )

        if directory:
            self.pathLineEdit.setText(directory)
            if not self.nameLineEdit.text():
                self.nameLineEdit.setText(Path(directory).name)

    @Slot()
    def accept(self):
        """
        Handle the OK button click.
        Validate the input and save the library root.
        """
        name = self.nameLineEdit.text().strip()
        path = norm_db_path(self.pathLineEdit.text().strip())
        description = self.descriptionTextEdit.toPlainText().strip() or None

        # Validate input
        if not name:
            QMessageBox.warning(self, "Validation Error", "Name cannot be empty.")
            return

        if not path:
            QMessageBox.warning(self, "Validation Error", "Path cannot be empty.")
            return

        if not LibraryRoot.is_valid_path(path):
            QMessageBox.warning(self, "Validation Error", "Path must be a valid directory.")
            return

        try:
            # Check for duplicate name or path
            query = LibraryRoot.select().where(
                (LibraryRoot.name == name) | (LibraryRoot.path == path)
            )

            if self.is_new:
                # For new library roots, any match is a duplicate
                if query.exists():
                    existing = query.get()
                    if existing.name == name:
                        QMessageBox.warning(self, "Validation Error", f"A library root with the name '{name}' already exists.")
                    else:
                        QMessageBox.warning(self, "Validation Error", f"A library root with the path '{path}' already exists.")
                    return

                # Create new library root
                LibraryRoot.create(name=name, path=path, description=description)
                logging.info(f"Created library root: {name} at {path}")
            else:
                # For existing library roots, check if the duplicate is a different record
                for existing in query:
                    if existing.rowid != self.library_root.rowid:
                        if existing.name == name:
                            QMessageBox.warning(self, "Validation Error", f"A library root with the name '{name}' already exists.")
                            return
                        if existing.path == path:
                            QMessageBox.warning(self, "Validation Error", f"A library root with the path '{path}' already exists.")
                            return

                # Update existing library root
                if self.write_lock is not None and not self.write_lock.acquire(blocking=False,
                                                                                holder="Library root change"):
                    busy = self.write_lock.busy_with() or "Another job"
                    QMessageBox.warning(self, "Library Busy", f"Not saved: {library_busy_message(busy)}")
                    return
                try:
                    self.library_root.name = name
                    self.library_root.path = path
                    self.library_root.description = description
                    self.library_root.save()
                finally:
                    if self.write_lock is not None:
                        self.write_lock.release()
                logging.info(f"Updated library root: {name} at {path}")

            # Close the dialog
            super(LibraryRootEditDialog, self).accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred: {str(e)}")
            logging.exception("Error saving library root")

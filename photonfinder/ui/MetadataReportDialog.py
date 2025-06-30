import csv
import json
import sys
from pathlib import Path
from typing import List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QListWidgetItem, QMessageBox, QDialogButtonBox, QFileDialog
from astropy.io.fits import Header
from peewee import JOIN

from photonfinder.core import ApplicationContext, decompress
from photonfinder.filesystem import header_from_xisf_dict, Importer
from photonfinder.models import SearchCriteria, File, Image, FitsHeader, FileWCS
from photonfinder.platesolver import SolverBase
from photonfinder.ui.BackgroundLoader import FileProcessingTask
from photonfinder.ui.generated.MetadataReportDialog_ui import Ui_MetadataReportDialog


class MetadataReportTask(FileProcessingTask):
    export_format: str
    output_filename: str
    field_list: List[Tuple[str, str]]

    def start(self, output_filename: str = None, field_list: List[Tuple[str, str]] = None, export_format: str = 'csv'):
        self.export_format = export_format
        self.field_list = field_list
        self.output_filename = output_filename
        super().start()

    def get_tables(self) -> List:
        tables = super().get_tables()
        if any(value == "fits" for _, value in self.field_list):
            tables.append(FitsHeader)
        if any(value == "platesolving" for _, value in self.field_list):
            tables.append(FileWCS)
        return tables

    def create_query(self):
        query = super().create_query()
        if any(value == "fits" for _, value in self.field_list):
            query = query.join_from(File, FitsHeader, JOIN.LEFT_OUTER)
        if any(value == "platesolving" for _, value in self.field_list):
            query = query.join_from(File, FileWCS, JOIN.LEFT_OUTER)
        return query

    def _process_files(self):
        with open(self.output_filename, 'w') as f:
            self.writer = csv.writer(f, dialect=csv.excel_tab if self.export_format == 'tsv' else csv.excel)
            self.writer.writerow(map(lambda tup: tup[0], self.field_list))
            super()._process_files()

    def _process_file(self, file, index):
        super()._process_file(file, index)
        list_of_values = self.file_to_list_of_values(file, self.field_list)
        self.writer.writerow(list_of_values)

    def file_to_list_of_values(self, file: File, field_list: List[Tuple[str, str]]) -> List[str]:
        """
        Process a file and extract metadata values based on the field list.

        Args:
            file: File model object with eagerly loaded FitsHeader, Image and FileWCS tables
            field_list: List of tuples (field_name, source_type) where source_type is 
                       'photonfinder', 'fits', or 'platesolving'

        Returns:
            List of string values corresponding to the requested fields
        """
        result = []

        for field_name, source_type in field_list:
            value = self._extract_field_value(file, field_name, source_type)
            result.append(str(value) if value is not None else "")

        return result

    def _extract_field_value(self, file: File, field_name: str, source_type: str):
        """Extract a single field value from the file based on source type."""
        try:
            if source_type == "photonfinder":
                return self._extract_photonfinder_field(file, field_name)
            elif source_type == "fits":
                return self._extract_fits_field(file, field_name)
            elif source_type == "platesolving":
                return self._extract_platesolving_field(file, field_name)
            else:
                return None
        except Exception as e:
            # Log error but don't fail the entire process
            import logging
            logging.warning(f"Error extracting field {field_name} from {source_type}: {e}")
            return None

    def _extract_photonfinder_field(self, file: File, field_name: str):
        """Extract field from File or Image model."""
        if field_name.startswith("File."):
            attr_name = field_name[5:]  # Remove "File." prefix
            if attr_name == "full_filename":
                return str(Path(file.full_filename()))
            else:
                return getattr(file, attr_name, None)
        elif field_name.startswith("Image."):
            attr_name = field_name[6:]  # Remove "Image." prefix
            if hasattr(file, 'image') and file.image:
                return getattr(file.image, attr_name, None)
            else:
                return None
        else:
            # Handle legacy format without prefix
            if hasattr(file, field_name):
                return getattr(file, field_name, None)
            elif hasattr(file, 'image') and file.image and hasattr(file.image, field_name):
                return getattr(file.image, field_name, None)
            else:
                return None

    def _extract_fits_field(self, file: File, field_name: str):
        """Extract field from FITS header."""
        try:
            if hasattr(file, 'header_obj') and file.header_obj:
                header = file.header_obj
            else:
                if hasattr(file, 'fitsheader') and file.fitsheader:
                    if Importer.is_xisf_by_name(file.name):
                        header_dict = json.loads(decompress(file.fitsheader.header))
                        header = header_from_xisf_dict(header_dict)
                        file.header_obj = header
                    else:
                        header_data = decompress(file.fitsheader.header)
                        header = Header.fromstring(header_data.decode('utf-8'))
                        file.header_obj = header
                else:
                    header = None
            return header.get(field_name, None) if header else None
        except Exception as e:
            import logging
            logging.warning(f"Error parsing FITS header for field {field_name}: {e}", exc_info=True)
            return None

    def _extract_platesolving_field(self, file: File, field_name: str):
        """Extract field from WCS data."""
        try:
            if hasattr(file, 'filewcs_obj') and file.filewcs_obj:
                header = file.filewcs_obj
            else:
                if hasattr(file, 'filewcs') and file.filewcs:
                    # Decompress the WCS data and parse it as FITS header
                    wcs_data = decompress(file.filewcs.wcs)
                    header = Header.fromstring(wcs_data.decode('utf-8'))
                    file.filewcs_obj = header
                else:
                    header = None
            return header.get(field_name, None) if header else None
        except Exception as e:
            import logging
            logging.warning(f"Error parsing WCS data for field {field_name}: {e}")
            return None


class MetadataReportDialog(QDialog, Ui_MetadataReportDialog):
    """
    Dialog for configuring and generating metadata reports.
    Allows users to select metadata fields from different sources (photonfinder, FITS, Plate Solving)
    and configure export options.
    """
    worker: MetadataReportTask

    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File], parent=None):
        super(MetadataReportDialog, self).__init__(parent)
        self.setupUi(self)

        # Store references
        self.context = context
        self.search_criteria = search_criteria
        self.files = files

        # Connect signals to slots
        self._connect_signals()

        # Initialize the dialog state
        self._initialize_dialog()

    def _connect_signals(self):
        """Connect UI signals to their respective slots."""
        # Add buttons
        self.addPhotonFinderButton.clicked.connect(self._add_photonfinder_item)
        self.addFitsButton.clicked.connect(self._add_fits_item)
        self.addPlateSolvingButton.clicked.connect(self._add_platesolving_item)

        # Remove and reorder buttons
        self.removeButton.clicked.connect(self._remove_selected_items)
        self.upButton.clicked.connect(self._move_items_up)
        self.downButton.clicked.connect(self._move_items_down)

        # List widget selection changes
        self.selectedItemsListWidget.itemSelectionChanged.connect(self._update_button_states)
        self.selectedItemsListWidget.model().rowsInserted.connect(self._update_button_states)
        self.selectedItemsListWidget.model().rowsRemoved.connect(self._update_button_states)
        self.selectedItemsListWidget.model().modelReset.connect(self._update_button_states)

        # accept and cancel
        self.buttonBox.accepted.connect(self.start_export)
        self.buttonBox.rejected.connect(self.cancel_export)

    def _initialize_dialog(self):
        """Initialize the dialog with default values and populate combo boxes."""
        # Populate photonfinder combo box with File and Image model fields
        self._populate_photonfinder_combo()

        # Populate FITS combo box with known FITS keywords (editable)
        self._populate_fits_combo()

        # Populate Plate Solving combo box with WCS headers (editable)
        self._populate_platesolving_combo()

        # Set initial button states
        self._update_button_states()

        # Set default export format
        self.exportFormatComboBox.setCurrentIndex(0)  # Default to CSV

        # disable OK button unless the user selects something
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        # Initialize progress bar
        self.progressBar.setVisible(False)  # Hide until needed
        self.worker = None

    def _populate_photonfinder_combo(self):
        """Populate photonfinder combo box with File and Image model fields."""
        # Get File model fields (excluding internal fields)
        file_fields = ["File.full_filename"]
        for field_name in File._meta.fields:
            if not field_name.startswith('_') and field_name != 'rowid' and field_name != "root":
                file_fields.append(f"File.{field_name}")

        # Get Image model fields (excluding internal fields)
        image_fields = []
        exclude_fields = {"file", "rowid", "coord_pix256"}
        for field_name in Image._meta.fields:
            if not field_name.startswith('_') and field_name not in exclude_fields:
                image_fields.append(f"Image.{field_name}")

        # Combine and sort fields
        all_fields = sorted(file_fields + image_fields)

        # Add fields to combo box
        self.photonFinderComboBox.addItems(all_fields)

    def _populate_fits_combo(self):
        """Populate FITS combo box with known FITS keywords (editable)."""
        # Make combo box editable
        self.fitsComboBox.setEditable(True)

        # Add known FITS keywords
        fits_keywords = self.context.get_known_fits_keywords()
        self.fitsComboBox.addItems(fits_keywords)

    def _populate_platesolving_combo(self):
        """Populate Plate Solving combo box with WCS headers (editable)."""
        # Make combo box editable
        self.plateSolvingComboBox.setEditable(True)

        # Add WCS headers from SolverBase.keep_headers
        wcs_headers = sorted(list(SolverBase.keep_headers))
        self.plateSolvingComboBox.addItems(wcs_headers)

    def _add_photonfinder_item(self):
        """Add selected photonfinder item to the list."""
        current_text = self.photonFinderComboBox.currentText()
        if current_text and not self._is_item_already_added(current_text, "photonfinder"):
            # Create list item (no prefix needed for photonfinder items)
            item = QListWidgetItem(current_text)
            item.setData(Qt.UserRole, ("photonfinder", current_text))  # Store source type and original field name
            self.selectedItemsListWidget.addItem(item)

            # Remove from combo box to prevent duplicate selection
            current_index = self.photonFinderComboBox.currentIndex()
            if current_index >= 0:
                self.photonFinderComboBox.removeItem(current_index)

    def _add_fits_item(self):
        """Add selected FITS item to the list."""
        current_text = self.fitsComboBox.currentText()
        if current_text and not self._is_item_already_added(current_text, "fits"):
            # Create list item with source prefix for visual distinction
            display_text = f"FITS: {current_text}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, ("fits", current_text))  # Store source type and original field name
            self.selectedItemsListWidget.addItem(item)

            # For editable combo, clear the text but keep the items
            self.fitsComboBox.setCurrentText("")

    def _add_platesolving_item(self):
        """Add selected Plate Solving item to the list."""
        current_text = self.plateSolvingComboBox.currentText()
        if current_text and not self._is_item_already_added(current_text, "platesolving"):
            # Create list item with source prefix for visual distinction
            display_text = f"WCS: {current_text}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, ("platesolving", current_text))  # Store source type and original field name
            self.selectedItemsListWidget.addItem(item)

            # For editable combo, clear the text but keep the items
            self.plateSolvingComboBox.setCurrentText("")

    def _is_item_already_added(self, field_name: str, source_type: str) -> bool:
        """Check if an item with the same field name and source type is already in the list."""
        for i in range(self.selectedItemsListWidget.count()):
            item = self.selectedItemsListWidget.item(i)
            item_data = item.data(Qt.UserRole)
            if isinstance(item_data, tuple) and len(item_data) == 2:
                existing_source, existing_field = item_data
                if existing_source == source_type and existing_field == field_name:
                    return True
        return False

    def _get_list_items(self):
        """Get all items currently in the selected items list."""
        items = []
        for i in range(self.selectedItemsListWidget.count()):
            items.append(self.selectedItemsListWidget.item(i).text())
        return items

    def _remove_selected_items(self):
        """Remove selected items from the list and return them to combo boxes."""
        selected_items = self.selectedItemsListWidget.selectedItems()
        for item in selected_items:
            item_data = item.data(Qt.UserRole)
            source_type, original_field_name = item_data

            # Return item to appropriate combo box based on source type
            if source_type == "photonfinder":
                # Add back to photonfinder combo box in sorted order
                self._add_item_to_combo_sorted(self.photonFinderComboBox, original_field_name)
            elif source_type == "fits":
                # FITS combo is editable, so we don't need to add it back
                pass
            elif source_type == "platesolving":
                # Plate solving combo is editable, so we don't need to add it back
                pass

            # Remove from list widget
            row = self.selectedItemsListWidget.row(item)
            self.selectedItemsListWidget.takeItem(row)

    def _add_item_to_combo_sorted(self, combo_box, item_text):
        """Add an item to a combo box in sorted order."""
        # Get all current items
        items = [combo_box.itemText(i) for i in range(combo_box.count())]
        items.append(item_text)
        items.sort()

        # Clear and re-add all items
        combo_box.clear()
        combo_box.addItems(items)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(len(items) > 0)

    def _move_items_up(self):
        """Move selected items up in the list."""
        selected_items = self.selectedItemsListWidget.selectedItems()
        if not selected_items:
            return

        # Get the rows of selected items
        rows = [self.selectedItemsListWidget.row(item) for item in selected_items]
        rows.sort()

        # Can't move up if the first selected item is already at the top
        if rows[0] == 0:
            return

        # Move each selected item up one position
        for row in rows:
            item = self.selectedItemsListWidget.takeItem(row)
            self.selectedItemsListWidget.insertItem(row - 1, item)
            item.setSelected(True)

    def _move_items_down(self):
        """Move selected items down in the list."""
        selected_items = self.selectedItemsListWidget.selectedItems()
        if not selected_items:
            return

        # Get the rows of selected items
        rows = [self.selectedItemsListWidget.row(item) for item in selected_items]
        rows.sort(reverse=True)  # Sort in reverse order for moving down

        # Can't move down if the last selected item is already at the bottom
        if rows[0] == self.selectedItemsListWidget.count() - 1:
            return

        # Move each selected item down one position
        for row in rows:
            item = self.selectedItemsListWidget.takeItem(row)
            self.selectedItemsListWidget.insertItem(row + 1, item)
            item.setSelected(True)

    def _update_button_states(self):
        """Update the enabled/disabled state of buttons based on current selection."""
        has_selection = len(self.selectedItemsListWidget.selectedItems()) > 0

        # Enable/disable buttons based on selection
        self.removeButton.setEnabled(has_selection)
        self.upButton.setEnabled(has_selection)
        self.downButton.setEnabled(has_selection)

        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.selectedItemsListWidget.count() > 0)

    def get_selected_fields(self) -> list:
        """
        Get the list of selected metadata fields in order.

        Returns:
            list: List of tuples (field_name, source_type) for report generation
        """
        fields = []
        for i in range(self.selectedItemsListWidget.count()):
            item = self.selectedItemsListWidget.item(i)
            item_data = item.data(Qt.UserRole)
            source_type, original_field_name = item_data
            fields.append((original_field_name, source_type))
        return fields

    def get_export_format(self) -> str:
        """
        Get the selected export format.

        Returns:
            str: 'csv' for comma separated values, 'tsv' for tab separated values
        """
        if self.exportFormatComboBox.currentIndex() == 0:
            return 'csv'
        else:
            return 'tsv'

    def cancel_export(self):
        """Cancel the export process."""
        if self.worker:
            self.worker.cancel()
        self.reject()

    def on_error(self, error_message):
        QMessageBox.critical(self, "Error", error_message)
        self.cancel_export()

    def start_export(self):
        format = self.get_export_format()
        if format == 'csv':
            filter = "Comma Separated Values (*.csv);;All Files (*)"
        else:
            filter = "Tab Separated Values (*.tsv);;All Files (*)"

        file_path, _ = QFileDialog.getSaveFileName(self, f"Save {format.upper()} Report", "", filter)

        if not file_path:
            return  # User cancelled

        self.worker = MetadataReportTask(self.context, self.search_criteria, self.files)
        self.worker.finished.connect(self.accept)
        self.worker.total_found.connect(self.progressBar.setMaximum)
        self.worker.progress.connect(self.progressBar.setValue)
        self.worker.error.connect(self.on_error)
        self.worker.start(file_path, self.get_selected_fields(), self.get_export_format())

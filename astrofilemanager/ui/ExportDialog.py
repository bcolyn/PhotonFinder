import copy
import logging
import os
import shutil
import string
from datetime import timedelta
from pathlib import Path
from typing import List, Optional, Union

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QDialogButtonBox
from peewee import JOIN

from astrofilemanager.core import ApplicationContext, Settings
from astrofilemanager.models import Image, File, SearchCriteria
from astrofilemanager.ui.BackgroundLoader import BackgroundLoaderBase
from astrofilemanager.ui.generated.ExportDialog_ui import Ui_ExportDialog
from filesystem import is_compressed, fopen


def template_filename_with_ref(file: File, ref: File, template: string.Template, settings: Settings,
                               decompress=False) -> str:
    regular_filename = template_filename(file, template, settings, decompress=decompress)
    if ref is None:
        return regular_filename
    else:
        ref_filename = template_filename(ref, template, settings, decompress=decompress)
        ref_path = Path(ref_filename).parent
        regular_name = Path(regular_filename).name
        return os.path.join(ref_path, regular_name)


def template_filename(file: File, template: string.Template, settings: Settings, decompress=False) -> str:
    image = file.image if hasattr(file, 'image') and file.image else None
    file_name = file.name

    # drop the last extension for the decompressed file name
    if is_compressed(file_name) and decompress:
        file_name = os.path.splitext(file_name)[0]

    mapping = {
        'filename': file_name,
        'lib_path': file.path,
        'image_type': image.image_type if image else None,
        'camera': image.camera if image else None,
        'filter': image.filter if image else None,
        'exposure': image.exposure if image else None,
        'gain': image.gain if image else None,
        'binning': image.binning if image else None,
        'set_temp': image.set_temp if image else None,
        'telescope': image.telescope if image else None,
        'object_name': image.object_name if image else None,
        'date_obs': image.date_obs.isoformat() if image else None
    }
    if image and image.date_obs:
        datetime_minus12 = image.date_obs - timedelta(hours=12)
        mapping['date_minus12'] = datetime_minus12.date().isoformat()
        mapping['date'] = image.date_obs.date().isoformat()
    else:
        mapping['date_minus12'] = None
        mapping['date'] = None

    mapping['last_light_path'] = settings.get_last_light_path()
    mapping['filename_no_ext'] = os.path.splitext(file_name)[0]
    mapping['ext'] = os.path.splitext(file_name)[1].lstrip('.')
    # mapping['root'] = file.root.name

    result = template.safe_substitute(mapping)
    if not result:
        result = file_name
    if image and image.image_type == 'LIGHT':
        settings.set_last_light_path(Path(result).parent)
    return result


class ExportWorker(BackgroundLoaderBase):
    """Worker class for exporting files in a background thread."""
    progress = Signal(int)
    finished = Signal()
    error = Signal(str)

    pattern: string.Template | None = None

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self.total_files = 0
        self.files = None
        self.search_criteria = None
        self.output_path = ""
        self.decompress = False
        self.pattern = None
        self.cancelled = False

    def export_files(self, search_criteria: SearchCriteria,
                     files: Optional[List[File]], output_path: str, decompress: bool,
                     pattern: str, total_files: int):
        """Start the export process in a background thread."""
        self.search_criteria = search_criteria
        self.files = files
        self.output_path = output_path
        self.decompress = decompress
        self.pattern = string.Template(pattern)
        self.cancelled = False
        self.total_files = total_files
        self.run_in_thread(self._export_files_task)

    def _export_files_task(self):
        """Background task to export files."""
        try:
            if self.files is not None:
                for i, file in enumerate(self.files):
                    if self.cancelled:
                        break
                    self._process_file(file, i)
            else:
                with self.context.database.bind_ctx([File, Image]):
                    query = (File
                             .select(File, Image)
                             .join(Image, JOIN.LEFT_OUTER)
                             .order_by(File.root, File.path, File.name))
                    query = Image.apply_search_criteria(query, self.search_criteria)
                    for i, file in enumerate(query):
                        if self.cancelled:
                            break
                        self._process_file(file, i)

            self.finished.emit()
        except Exception as e:
            logging.error(f"Error exporting files: {e}", exc_info=True)
            self.error.emit(str(e))

    def _process_file(self, file, index):
        """Process a single file during export."""
        # Get the source file path
        source_path = file.full_filename()

        # Create the output filename using the pattern
        output_filename = template_filename_with_ref(file, self.search_criteria.reference_file, self.pattern,
                                                     self.context.settings)

        # Create the full output path
        output_file_path = os.path.join(self.output_path, output_filename)

        # Ensure the output directory exists
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)

        # Copy the file
        if Path(output_file_path).exists():
            logging.info(f"File {output_file_path} already exists, skipping")
        else:
            logging.info(f"Copying {source_path} to {output_file_path}")
            self.copy_file(source_path, output_file_path)

        # Update progress
        self.progress.emit(int((index + 1) / self.total_files * 100))

    def copy_file(self, source_path, output_file_path):
        if is_compressed(source_path) and self.decompress:
            with fopen(source_path) as source_file:
                with open(output_file_path, "wb") as destination_file:
                    shutil.copyfileobj(source_file, destination_file)
            shutil.copystat(source_path, output_file_path)
        else:
            shutil.copy2(source_path, output_file_path)

    def cancel(self):
        """Cancel the export process."""
        self.cancelled = True


class ExportDialog(QDialog, Ui_ExportDialog):
    """Dialog for exporting files."""

    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria,
                 files: Optional[List[File]] = None, parent=None):
        super(ExportDialog, self).__init__(parent)
        self.setupUi(self)
        self.setModal(True)
        self.context = context
        self.search_criteria = copy.deepcopy(search_criteria)  # make a copy since we may modify it.
        self.files = files if files else None

        if files: # user has a selection made
            self.total_files = len(self.files)
            self.first_file = self.files[0]
        else:
            # Count the total number of files matching the criteria
            with self.context.database.bind_ctx([File, Image]):
                query = (File
                         .select(File, Image)
                         .join(Image, JOIN.LEFT_OUTER)
                         .order_by(File.root, File.path, File.name))
                query = Image.apply_search_criteria(query, self.search_criteria)
                self.total_files = query.count()
                self.first_file = query.first()

        if self.search_criteria.reference_file:
            self.useRefCheckBox.setEnabled(True)
            self.useRefCheckBox.setText(self.search_criteria.reference_file.name)
        self.setWindowTitle(f"Export {self.total_files} images")

        # Load settings
        self.load_settings()

        # Initialize the export worker
        self.export_worker = ExportWorker(context)

        # Connect signals
        self.export_worker.progress.connect(self.progressBar.setValue)
        self.export_worker.finished.connect(self.on_export_finished)
        self.export_worker.error.connect(self.on_export_error)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText("Export")

        # Connect the accepted signal to our export_files method
        self.buttonBox.accepted.connect(self.export_files)
        self.patternComboBox.editTextChanged.connect(self.update_preview)
        self.useRefCheckBox.stateChanged.connect(self.update_preview)
        self.update_preview(self.patternComboBox.currentText())

    def load_settings(self):
        """Load settings from the application context."""
        settings = self.context.settings

        # Load the last output path
        last_output_path = settings.get_last_export_path()
        if last_output_path:
            self.outputPathEdit.setText(last_output_path)

        # Load the last decompress option
        decompress = settings.get_last_export_decompress()
        self.decompressCheckBox.setChecked(decompress)

        # Load the last output patterns
        patterns = settings.get_last_export_patterns()
        self.patternComboBox.clear()
        if patterns:
            self.patternComboBox.addItems(patterns)
            if patterns:
                self.patternComboBox.setCurrentText(patterns[0])

    def save_settings(self):
        """Save settings to the application context."""
        settings = self.context.settings

        # Save the output path
        output_path = self.outputPathEdit.text()
        settings.set_last_export_path(output_path)

        # Save the decompress option
        decompress = self.decompressCheckBox.isChecked()
        settings.set_last_export_decompress(decompress)

        # Save the output pattern
        pattern = self.patternComboBox.currentText()
        patterns = settings.get_last_export_patterns() or []

        # Add the current pattern to the beginning of the list if it's not already there
        if pattern in patterns:
            patterns.remove(pattern)
        patterns.insert(0, pattern)

        # Keep only the last 10 patterns
        patterns = patterns[:10]

        settings.set_last_export_patterns(patterns)

    def export_files(self):
        # Save settings
        self.save_settings()
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        if not self.useRefCheckBox.isChecked():
            self.search_criteria.reference_file = None

        # Start the export process with either files or search criteria
        self.export_worker.export_files(
            self.search_criteria,
            self.files,
            self.outputPathEdit.text(),
            self.decompressCheckBox.isChecked(),
            self.patternComboBox.currentText(),
            self.total_files
        )

        # Connect the Cancel button to cancel the export
        self.buttonBox.rejected.connect(self.cancel_export)

    def browse_output_path(self):
        """Open a file dialog to select the output path."""
        current_path = self.outputPathEdit.text()
        directory = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", current_path or str(Path.home())
        )
        if directory:
            self.outputPathEdit.setText(directory)

    def cancel_export(self):
        """Cancel the export process."""
        if self.export_worker:
            self.export_worker.cancel()
        self.reject()

    def on_export_finished(self):
        """Called when the export is finished."""
        self.accept()

    def on_export_error(self, error_message):
        """Called when an error occurs during export."""
        QMessageBox.critical(self, "Export Error", error_message)
        self.reject()

    def update_preview(self, ignored):
        text = self.patternComboBox.currentText()
        tpl = string.Template(template=text)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(tpl.is_valid())
        ref = self.search_criteria.reference_file if self.useRefCheckBox.isChecked() else None
        filename = template_filename_with_ref(self.first_file, ref, tpl, self.context.settings,
                                              decompress=self.decompressCheckBox.isChecked())
        self.outputPreview.setText(filename)

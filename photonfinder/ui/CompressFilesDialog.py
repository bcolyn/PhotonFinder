import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtWidgets import QDialog, QListWidgetItem, QMessageBox
from peewee import JOIN

from photonfinder.core import ApplicationContext
from photonfinder.filesystem import compress_file, is_compressible
from photonfinder.models import File, Image, LibraryRoot, CORE_MODELS, SearchCriteria
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase
from photonfinder.ui.generated.CompressFilesDialog_ui import Ui_CompressFilesDialog

logger = logging.getLogger(__name__)

COMPRESSION_ALGORITHMS = {
    'bzip2': '.bz2',
    'gzip':  '.gz',
    'xz':    '.xz',
}


class CompressFilesWorker(BackgroundLoaderBase):
    progress = Signal(int)   # files processed
    total    = Signal(int)   # total files to process
    message  = Signal(str)   # per-file status line
    finished = Signal()
    error    = Signal(str)

    def compress(self, files: list, ext: str, verify: bool, level: int, parallelism: int):
        self.cancelled = False
        self.run_in_thread(self._run, files, ext, verify, level, parallelism)

    def cancel(self):
        self.cancelled = True

    def _run(self, files: list, ext: str, verify: bool, level: int, parallelism: int):
        self.total.emit(len(files))
        completed = 0
        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            futures = [pool.submit(self._process, file, ext, verify, level)
                       for file in files]
            for future in as_completed(futures):
                completed += 1
                self.progress.emit(completed)
                if self.cancelled:
                    for f in futures:
                        f.cancel()
                    self.message.emit("Cancelled.")
                    break
        self.finished.emit()

    def _process(self, file: File, ext: str, verify: bool, level: int):
        try:
            dest_path, new_size = compress_file(file.full_filename(), ext, verify, level)
            new_name = os.path.basename(dest_path)
            with self.context.database.bind_ctx(CORE_MODELS):
                File.update(name=new_name, size=new_size).where(File.rowid == file.rowid).execute()
            self.message.emit(f"OK   {file.name}  →  {new_name}")
        except Exception as e:
            logger.warning("Compression failed for %s: %s", file.name, e)
            self.message.emit(f"ERR  {file.name}: {e}")


class CompressFilesDialog(QDialog, Ui_CompressFilesDialog):
    compression_complete = Signal()

    def __init__(self, context: ApplicationContext,
                 files: list | None, search_criteria: SearchCriteria | None,
                 parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.context = context

        if files is not None:
            compressible = [f for f in files if is_compressible(f.name)]
        else:
            with self.context.database.bind_ctx(CORE_MODELS):
                query = (File.select(File, Image, LibraryRoot)
                         .join_from(File, LibraryRoot)
                         .join_from(File, Image, JOIN.LEFT_OUTER)
                         .order_by(File.root, File.path, File.name))
                query = Image.apply_search_criteria(query, search_criteria)
                compressible = [f for f in query if is_compressible(f.name)]

        for f in compressible:
            label = '/'.join(filter(None, [f.root.name, f.path, f.name]))
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.file_list.addItem(item)

        self.remove_btn.clicked.connect(self._remove_selected)
        self.file_list.installEventFilter(self)

        self.progress_group.setVisible(False)
        self.start_button.clicked.connect(self._start)
        self.close_button.clicked.connect(self._on_close_clicked)

        self._worker = CompressFilesWorker(context)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.total.connect(self.progress_bar.setMaximum)
        self._worker.message.connect(self._append_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self.level_spin.setValue(context.settings.get_compress_level())
        self.parallel_spin.setValue(context.settings.get_compress_parallelism())
        self._update_summary()

    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.file_list and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Delete:
                self._remove_selected()
                return True
        return super().eventFilter(obj, event)

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self._update_summary()

    def _update_summary(self):
        count = self.file_list.count()
        total_mb = sum(
            self.file_list.item(i).data(Qt.ItemDataRole.UserRole).size
            for i in range(count)
        ) / 1_048_576
        self.summary_label.setText(
            f"{count} uncompressed FITS file(s) queued ({total_mb:.1f} MB)."
        )
        self.start_button.setEnabled(count > 0)

    # ------------------------------------------------------------------

    def _start(self):
        files = [self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.file_list.count())]
        ext         = COMPRESSION_ALGORITHMS[self.algorithm_combo.currentText()]
        verify      = self.verify_check.isChecked()
        level       = self.level_spin.value()
        parallelism = self.parallel_spin.value()
        self.context.settings.set_compress_level(level)
        self.context.settings.set_compress_parallelism(parallelism)
        self.file_list.setEnabled(False)
        self.remove_btn.setEnabled(False)
        self.progress_group.setVisible(True)
        w = self.width()
        self.adjustSize()
        self.resize(w, self.height())
        self.start_button.setEnabled(False)
        self.close_button.setText("Cancel")
        self._worker.compress(files, ext, verify, level, parallelism)

    def _on_finished(self):
        self.close_button.setText("Close")
        self.compression_complete.emit()

    def _on_close_clicked(self):
        if self.close_button.text() == "Cancel":
            self._worker.cancel()
        else:
            self.accept()

    def _on_error(self, msg: str):
        self._append_log(f"Error: {msg}")
        QMessageBox.critical(self, "Compression error", msg)

    def _append_log(self, text: str):
        self.log_edit.appendPlainText(text)
        self.log_edit.ensureCursorVisible()

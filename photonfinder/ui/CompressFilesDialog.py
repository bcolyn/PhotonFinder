import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtWidgets import QDialog, QListWidgetItem, QMessageBox

from photonfinder import library_query
from photonfinder.core import ApplicationContext, library_busy_message
from photonfinder.filesystem import compress_file, is_compressible
from photonfinder.models import File, CORE_MODELS, SearchCriteria
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase
from photonfinder.ui.generated.CompressFilesDialog_ui import Ui_CompressFilesDialog

logger = logging.getLogger(__name__)

COMPRESS_WRITER = "Compress Files"

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
    cancelled = False

    def compress(self, files: list, ext: str, verify: bool, level: int, parallelism: int):
        self.cancelled = False
        self.run_writer_in_thread(COMPRESS_WRITER, self._run, files, ext, verify, level, parallelism)

    def cancel(self):
        self.cancelled = True

    def _run(self, files: list, ext: str, verify: bool, level: int, parallelism: int):
        try:
            self.total.emit(len(files))
            completed = 0
            with ThreadPoolExecutor(max_workers=parallelism) as pool:
                futures = [pool.submit(self._process, file, ext, verify, level)
                           for file in files]
                for _ in as_completed(futures):
                    completed += 1
                    self.progress.emit(completed)
                    if self.cancelled:
                        for f in futures:
                            f.cancel()
                        self.message.emit("Cancelled.")
                        break
        except Exception as e:
            logger.exception("Compression run failed")
            self.error.emit(str(e))
        finally:
            self.finished.emit()

    def _process(self, file: File, ext: str, verify: bool, level: int):
        src_path = file.full_filename()
        try:
            dest_path, new_size = compress_file(src_path, ext, verify, level, keep_source=True)
        except Exception as e:
            logger.warning("Compression failed for %s: %s", file.name, e)
            self.message.emit(f"ERR  {file.name}: {e}")
            return
        new_name = os.path.basename(dest_path)
        try:
            db = self.context.database
            with db.bind_ctx(CORE_MODELS), db.atomic():
                File.update(name=new_name, size=new_size).where(File.rowid == file.rowid).execute()
                # Delete the original inside the transaction: if that fails (e.g. the file is
                # open elsewhere), the rename is rolled back and the archive discarded below.
                os.unlink(src_path)
        except Exception as e:
            if os.path.exists(src_path):
                # Original intact: drop the archive so disk and DB stay as they were.
                # (If only the commit failed, the original is gone and the next rescan
                # re-links the row to the archive via possible_compressed_variants.)
                os.unlink(dest_path)
            logger.warning("Compression failed for %s: %s", file.name, e)
            self.message.emit(f"ERR  {file.name}: {e}")
            return
        self.message.emit(f"OK   {file.name}  →  {new_name}")


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
                compressible = [f for f in library_query.matching_files(search_criteria)
                                if is_compressible(f.name)]

        for f in compressible:
            label = '/'.join(filter(None, [f.root.name, f.path, f.name]))
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.file_list.addItem(item)

        self.remove_btn.clicked.connect(self._remove_selected)
        self.file_list.installEventFilter(self)

        self._running = False
        self._started = False  # Start is one-shot
        self._holds_write_lock = False
        self._library_busy: str | None = None  # what else holds the library write lock, if anything
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
        context.signal_bus.library_write_lock_changed.connect(self._refresh_library_busy)
        self._refresh_library_busy()

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
        busy = None if self._holds_write_lock else self._library_busy
        self.start_button.setEnabled(count > 0 and not self._started and not busy)
        self.start_button.setToolTip(library_busy_message(busy) if busy else "")

    def _refresh_library_busy(self):
        """Ask the lock when it changes here or the dialog is activated: for another window's
        writer that means taking it for a moment."""
        self._library_busy = None if self._holds_write_lock else self.context.library_write_lock.busy_with()
        self._update_summary()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._refresh_library_busy()

    # ------------------------------------------------------------------

    def _start(self):
        # One library writer at a time: a scan running alongside would see both the archive
        # and the original and register them as two files.
        if not self.context.library_write_lock.acquire(blocking=False, holder=COMPRESS_WRITER):
            busy = self.context.library_write_lock.busy_with() or "Another job"
            QMessageBox.information(self, "Library Busy", f"Compression not started: {library_busy_message(busy)}")
            return
        self._holds_write_lock = True
        self._started = True
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
        self._running = True
        self._worker.compress(files, ext, verify, level, parallelism)

    def _on_finished(self):
        self._running = False
        if self._holds_write_lock:
            self._holds_write_lock = False
            self.context.library_write_lock.release()
        self.close_button.setText("Close")
        self.compression_complete.emit()

    def _on_close_clicked(self):
        if self._running:
            self._worker.cancel()
        else:
            self.accept()

    def reject(self):
        # Esc and the window close button land here. Closing mid-run would destroy the
        # dialog while workers are still deleting originals, so treat it as Cancel instead.
        if self._running:
            self._worker.cancel()
            return
        super().reject()

    def _on_error(self, msg: str):
        self._append_log(f"Error: {msg}")
        QMessageBox.critical(self, "Compression error", msg)

    def _append_log(self, text: str):
        self.log_edit.appendPlainText(text)
        self.log_edit.ensureCursorVisible()

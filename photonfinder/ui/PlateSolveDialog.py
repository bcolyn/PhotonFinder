import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QMessageBox

from photonfinder.core import ApplicationContext, decompress
from photonfinder.filesystem import parse_FITS_header
from photonfinder.models import File, FitsHeader, Image, SearchCriteria
from photonfinder.platesolver import (
    ASTAPSolver, AstrometryNetSolver, WSLSolveFieldSolver, SolverHint
)
from photonfinder.ui.BackgroundLoader import PlateSolveTask
from photonfinder.ui.generated.PlateSolveDialog_ui import Ui_PlateSolveDialog

# Maps combo index → (display name, solver index 0/1/2)
_SOLVERS = [
    ("ASTAP", 0),
    ("Astrometry.net", 1),
    ("WSL solve-field", 2),
]


class PlateSolveDialog(QDialog, Ui_PlateSolveDialog):
    solving_complete = Signal(object)  # emits PlateSolveTask when done

    def __init__(self, context: ApplicationContext, files, search_criteria: SearchCriteria, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.context = context
        self.files = files
        self.search_criteria = search_criteria
        self._task = None

        self._populate_combos()
        self._load_settings()
        self._infer_hints()

        self.start_button.clicked.connect(self._start_solving)
        self.close_button.clicked.connect(self._on_close_clicked)

    def _populate_combos(self):
        for name, _ in _SOLVERS:
            self.primary_solver_combo.addItem(name)
        self.backup_solver_combo.addItem("None")
        for name, _ in _SOLVERS:
            self.backup_solver_combo.addItem(name)

    def _load_settings(self):
        s = self.context.settings
        self.primary_solver_combo.setCurrentIndex(s.get_plate_solve_primary_solver())
        # backup: stored as -1=None, 0/1/2=solver index → combo index = stored + 1
        self.backup_solver_combo.setCurrentIndex(s.get_plate_solve_backup_solver() + 1)

        ra = s.get_plate_solve_hint_ra()
        dec = s.get_plate_solve_hint_dec()
        scale = s.get_plate_solve_hint_scale()
        mode = s.get_plate_solve_hint_mode()

        self.hint_ra_edit.setText(ra)
        self.hint_dec_edit.setText(dec)
        self.hint_scale_spin.setValue(scale)
        if mode == 'override':
            self.hint_override_radio.setChecked(True)
        else:
            self.hint_fallback_radio.setChecked(True)

    def _save_settings(self):
        s = self.context.settings
        s.set_plate_solve_primary_solver(self.primary_solver_combo.currentIndex())
        s.set_plate_solve_backup_solver(self.backup_solver_combo.currentIndex() - 1)
        s.set_plate_solve_hint_ra(self.hint_ra_edit.text().strip())
        s.set_plate_solve_hint_dec(self.hint_dec_edit.text().strip())
        s.set_plate_solve_hint_scale(self.hint_scale_spin.value())
        s.set_plate_solve_hint_mode('override' if self.hint_override_radio.isChecked() else 'fallback')

    def _infer_hints(self):
        """Try to pre-fill hint fields from the input files' database records."""
        sample_files = self._get_sample_files(5)
        if not sample_files:
            return

        # RA/Dec: use first file that has coords in the Image table
        for file in sample_files:
            if hasattr(file, 'image') and file.image and file.image.coord_ra is not None:
                self.hint_ra_edit.setText(f"{file.image.coord_ra:.4f}")
                self.hint_dec_edit.setText(f"{file.image.coord_dec:.4f}")
                break

        # Scale: look in FitsHeader for SCALE/PIXSCALE or compute from FOCALLEN/YPIXSZ
        if self.hint_scale_spin.value() == 0.0:
            for file in sample_files:
                scale = self._infer_scale_from_header(file)
                if scale is not None:
                    self.hint_scale_spin.setValue(round(scale, 3))
                    break

    def _get_sample_files(self, n: int):
        try:
            if self.files:
                return self.files[:n]
            query = (File
                     .select(File, Image)
                     .join_from(File, Image)
                     .limit(n))
            query = Image.apply_search_criteria(query, self.search_criteria)
            return list(query)
        except Exception:
            return []

    def _infer_scale_from_header(self, file: File):
        try:
            fh = FitsHeader.get(FitsHeader.file == file)
            header = parse_FITS_header(decompress(fh.header))
            scale = header.get("SCALE") or header.get("PIXSCALE")
            if scale is not None:
                return float(scale)
            focal_len = header.get("FOCALLEN")
            pix_size = header.get("YPIXSZ")
            if focal_len and pix_size:
                return 206.265 * float(pix_size) / float(focal_len)
        except Exception:
            pass
        return None

    def _build_solver(self, solver_index: int):
        s = self.context.settings
        if solver_index == 0:
            return ASTAPSolver(exe=s.get_astap_path())
        elif solver_index == 1:
            return AstrometryNetSolver(
                api_key=s.get_astrometry_net_api_key(),
                force_image_upload=s.get_astrometry_net_force_image_upload()
            )
        elif solver_index == 2:
            return WSLSolveFieldSolver(timeout=s.get_wsl_solver_timeout())
        return None

    def _build_hint(self):
        ra_text = self.hint_ra_edit.text().strip()
        dec_text = self.hint_dec_edit.text().strip()
        scale_val = self.hint_scale_spin.value()
        mode = 'override' if self.hint_override_radio.isChecked() else 'fallback'

        ra = None
        dec = None
        if ra_text and dec_text:
            try:
                ra = float(ra_text)
                dec = float(dec_text)
            except ValueError:
                pass

        scale = scale_val if scale_val > 0 else None
        return SolverHint(ra=ra, dec=dec, scale=scale, mode=mode)

    def _start_solving(self):
        self._save_settings()

        primary_index = self.primary_solver_combo.currentIndex()
        backup_index = self.backup_solver_combo.currentIndex() - 1  # -1 = None

        try:
            primary_solver = self._build_solver(primary_index)
            backup_solver = self._build_solver(backup_index) if backup_index >= 0 else None
        except Exception as e:
            QMessageBox.critical(self, "Configuration error", str(e))
            return

        hint = self._build_hint()

        self._task = PlateSolveTask(
            context=self.context,
            search_criteria=self.search_criteria,
            files=self.files,
            solver=primary_solver,
            backup_solver=backup_solver,
            hint=hint,
        )
        self._task.progress.connect(self.progressBar.setValue)
        self._task.total_found.connect(self.progressBar.setMaximum)
        self._task.message.connect(self._append_log)
        self._task.finished.connect(self._on_finished)
        self._task.error.connect(self._on_error)

        self.progressBar.setValue(0)
        self.log_edit.clear()
        self.start_button.setEnabled(False)
        self.close_button.setText("Cancel")

        self._task.start()

    def _append_log(self, message: str):
        self.log_edit.appendPlainText(message)

    def _on_finished(self):
        task = self._task
        self._task = None
        self.solving_complete.emit(task)
        self.accept()

    def _on_error(self, error_message: str):
        self._append_log(f"Error: {error_message}")
        QMessageBox.critical(self, "Plate solving error", error_message)
        self.start_button.setEnabled(True)
        self.close_button.setText("Close")
        self._task = None

    def _on_close_clicked(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.close()

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from photonfinder.core import ApplicationContext, decompress
from photonfinder.filesystem import parse_FITS_header, header_from_xisf_dict
from peewee import JOIN
from photonfinder.models import File, FileWCS, FitsHeader, Image, SearchCriteria
from photonfinder.platesolver import (
    ASTAPSolver, AstrometryNetSolver, SolveFieldSolver, SolverHint
)
from photonfinder.ui.BackgroundLoader import PlateSolveTask
from photonfinder.ui.generated.PlateSolveDialog_ui import Ui_PlateSolveDialog

class CopyWCSDialog(QDialog):
    def __init__(self, wcs_files, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Copy Plate Solution")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select solution source:"))

        self.combo = QComboBox()
        for f, image in wcs_files:
            ra = f"{image.coord_ra:.4f}" if image.coord_ra is not None else "?"
            dec = f"{image.coord_dec:.4f}" if image.coord_dec is not None else "?"
            self.combo.addItem(f"{f.name}  RA {ra}°  Dec {dec}°", userData=f)
        layout.addWidget(self.combo)

        self.overwrite_check = QCheckBox("Overwrite current solution, if present")
        self.overwrite_check.setChecked(False)
        layout.addWidget(self.overwrite_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_file(self):
        return self.combo.currentData()

    def should_overwrite(self):
        return self.overwrite_check.isChecked()


# Maps combo index → (display name, solver index 0/1/2)
_SOLVERS = [
    ("ASTAP", 0),
    ("Astrometry.net", 1),
    ("solve-field", 2),
]


def _infer_scale_from_header(file: File):
    try:
        import json
        fh = FitsHeader.get(FitsHeader.file == file)
        raw = decompress(fh.header)
        if file.name.lower().endswith('.xisf'):
            header = header_from_xisf_dict(json.loads(raw))
        else:
            header = parse_FITS_header(raw)
        scale = header.get("SCALE") or header.get("PIXSCALE")
        if scale is not None:
            return float(scale)
        focal_len = header.get("FOCALLEN")
        pix_size = header.get("YPIXSZ")
        if focal_len and pix_size:
            scale = 206.265 * float(pix_size) / float(focal_len)
            scale = round(scale, 3)
            return scale
    except Exception:
        pass
    return None


def _infer_scale_from_wcs(file: File):
    try:
        import math
        from astropy.io.fits import Header as AstroHeader
        wcs_rec = FileWCS.get_or_none(FileWCS.file == file)
        if wcs_rec is None:
            return None
        raw = decompress(wcs_rec.wcs)
        header = AstroHeader.fromstring(raw.decode())
        wcs = WCS(header)
        pixel_scales = proj_plane_pixel_scales(wcs)  # in degrees/pixel
        scale = float(pixel_scales[0]) * 3600  # convert to arcsec/pixel
        scale = round(scale, 3)
        return scale if scale > 0 else None
    except Exception as e:
        print(e)
        return None


class PlateSolveDialog(QDialog, Ui_PlateSolveDialog):
    solving_complete = Signal(object)  # emits PlateSolveTask when done
    wcs_copied = Signal(object)        # emits object with .solved_files after Direct Copy

    def __init__(self, context: ApplicationContext, files, search_criteria: SearchCriteria, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.context = context
        self.files = files
        self.search_criteria = search_criteria
        self._task: PlateSolveTask | None = None
        self._pending_retry_files = None

        self._populate_combos()
        self._load_settings()
        self._infer_hints()
        self._update_title()
        self._camera_scale_cache = self._load_camera_scale_cache()
        if not self._any_file_has_camera():
            self.use_camera_scales_check.setChecked(False)
            self.use_camera_scales_check.setEnabled(False)

        self.start_button.clicked.connect(self._start_solving)
        self.close_button.clicked.connect(self._on_close_clicked)
        self.direct_copy_button.clicked.connect(self._on_direct_copy)

        self.hint_ra_edit.textChanged.connect(self._update_hms_dms)
        self.hint_dec_edit.textChanged.connect(self._update_hms_dms)
        self.hint_ra_edit.editingFinished.connect(self._try_convert_ra)
        self.hint_dec_edit.editingFinished.connect(self._try_convert_dec)
        self.lookup_hint_button.clicked.connect(self._on_lookup_hint)
        self._update_hms_dms()

        if self.files:
            self.log_edit.appendPlainText("Files to solve:")
            for f in self.files:
                self.log_edit.appendPlainText(f.name)

    def _update_title(self):
        if self.files:
            n = len(self.files)
            self.setWindowTitle(f"Plate Solve — {n} file{'s' if n != 1 else ''}")
        elif hasattr(self.parent(), 'get_title'):
            self.setWindowTitle(f"Plate Solve — {self.parent().get_title()}")

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

        # Scale: try WCS then header; fall back to whatever settings restored
        for file in sample_files:
            scale = _infer_scale_from_wcs(file) or _infer_scale_from_header(file)
            if scale is not None:
                assert scale <= self.hint_scale_spin.maximum(), (
                    f"Inferred scale {scale:.4f} arcsec/px exceeds spinner max "
                    f"({self.hint_scale_spin.maximum()}) for file {file.name!r}"
                )
                self.hint_scale_spin.setValue(round(scale, 3))
                break

    def _get_sample_files(self, n: int):
        try:
            if self.files:
                with_wcs = [f for f in self.files if getattr(f, 'has_wcs', False)]
                without_wcs = [f for f in self.files if not getattr(f, 'has_wcs', False)]
                return (with_wcs + without_wcs)[:n]
            query = (File
                     .select(File, Image, FileWCS)
                     .join_from(File, Image)
                     .join_from(File, FileWCS, JOIN.LEFT_OUTER)
                     .order_by(FileWCS.wcs.is_null())
                     .limit(n))
            query = Image.apply_search_criteria(query, self.search_criteria)
            return list(query)
        except Exception:
            return []

    def _load_camera_scale_cache(self) -> dict:
        """Returns {camera: [scale, ...]} from solved Image records (one DB query).

        Only distinct (camera, coord_scale) pairs are fetched. Scales within 30% of
        each other are collapsed to the most common one — they represent the same
        optical setup and would not give the solver useful new information.
        """
        from peewee import fn
        cache: dict[str, dict[float, int]] = {}  # camera → {scale: count}
        try:
            rows = (Image
                    .select(Image.camera, Image.coord_scale,
                            fn.COUNT(Image.rowid).alias('cnt'))
                    .where(Image.camera.is_null(False)
                           & Image.coord_scale.is_null(False))
                    .group_by(Image.camera, Image.coord_scale))
            for img in rows:
                cache.setdefault(img.camera, {})[img.coord_scale] = img.cnt
        except Exception:
            logging.exception("Failed to load camera scale cache")
            return {}
        return {cam: self._dedupe_scales(counts) for cam, counts in cache.items()}

    @staticmethod
    def _otsu_threshold(counts: list[int]) -> float:
        """Return the Otsu optimal split threshold for a list of counts.

        Tries every midpoint between adjacent unique count values, picks the one
        that maximises between-class variance.  Returns 0 (keep all) when there
        are fewer than two distinct values or the two classes are too similar
        (lower-class mean ≥ 20 % of upper-class mean — both are legitimate setups).
        """
        unique = sorted(set(counts))
        if len(unique) < 2:
            return 0
        best_var, best_t = -1.0, 0.0
        n = len(counts)
        for i in range(1, len(unique)):
            t = (unique[i - 1] + unique[i]) / 2.0
            below = [c for c in counts if c <= t]
            above = [c for c in counts if c > t]
            if not below or not above:
                continue
            w0, w1 = len(below) / n, len(above) / n
            m0, m1 = sum(below) / len(below), sum(above) / len(above)
            if m0 >= 0.25 * m1:         # classes too similar — not a real outlier (< 4:1 ratio)
                continue
            var = w0 * w1 * (m0 - m1) ** 2
            if var > best_var:
                best_var, best_t = var, t
        return best_t

    @staticmethod
    def _dedupe_scales(scale_counts: dict[float, int]) -> list[float]:
        """Return representative scales, removing near-duplicates and rare outliers.

        Steps:
        1. Sort scales and merge those within a 30% ratio band, accumulating their
           solve counts and keeping the value with the highest individual count.
        2. Apply an Otsu threshold on the per-band totals to drop bands whose
           accumulated count is clearly in the noise tier (only triggers when one
           class is less than 20% as common as the other).
        """
        if not scale_counts:
            return []

        # Step 1 — 30% band merge
        bands: list[tuple[float, int, int]] = []   # (representative_scale, rep_count, band_total)
        for scale in sorted(scale_counts):
            cnt = scale_counts[scale]
            if not bands or scale / bands[-1][0] >= 1.30:
                bands.append((scale, cnt, cnt))
            else:
                rep_s, rep_c, total = bands[-1]
                winner, winner_c = (scale, cnt) if cnt > rep_c else (rep_s, rep_c)
                bands[-1] = (winner, winner_c, total + cnt)

        # Step 2 — Otsu on band totals, drop noise tier
        if len(bands) >= 2:
            totals = [total for _, _, total in bands]
            threshold = PlateSolveDialog._otsu_threshold(totals)
            if threshold > 0:
                filtered = [(s, rc, t) for s, rc, t in bands if t > threshold]
                if filtered:
                    bands = filtered

        return [s for s, _, _ in bands]

    def _any_file_has_camera(self) -> bool:
        """Return True if at least one file to be solved has a camera field set."""
        try:
            if self.files:
                return (Image.select(Image.camera)
                        .where(Image.file.in_(self.files) & Image.camera.is_null(False))
                        .exists())
            query = (File.select(Image.camera)
                     .join(Image)
                     .where(Image.camera.is_null(False)))
            query = Image.apply_search_criteria(query, self.search_criteria)
            return query.exists()
        except Exception:
            return True  # safe default — don't disable if check fails

    def _build_solver(self, solver_index: int):
        s = self.context.settings
        if solver_index == 0:
            return ASTAPSolver(exe=s.get_astap_path())
        elif solver_index == 1:
            return AstrometryNetSolver(
                api_key=s.get_astrometry_net_api_key(),
                force_image_upload=s.get_astrometry_net_force_image_upload(),
            )
        elif solver_index == 2:
            return SolveFieldSolver(
                exe_path=s.get_solve_field_path(),
                timeout=s.get_solve_field_timeout(),
                wsl_distro=s.get_solve_field_wsl_distro(),
            )
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
        if hasattr(self, '_results_scroll'):
            self._cleanup_results()
        files = self._pending_retry_files if self._pending_retry_files is not None else self.files
        self._pending_retry_files = None
        self._run_task(files)

    def _run_task(self, files):
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
            files=files,
            solver=primary_solver,
            backup_solver=backup_solver,
            hint=hint,
            camera_scale_cache=self._camera_scale_cache,
            use_camera_scales=self.use_camera_scales_check.isChecked(),
        )
        assert self._task is not None
        self._task.progress.connect(self.progressBar.setValue)
        self._task.total_found.connect(self.progressBar.setMaximum)
        self._task.message.connect(self._append_log)
        self._task.finished.connect(self._on_finished)
        self._task.error.connect(self._on_error)

        self.progressBar.setValue(0)
        self.log_edit.clear()
        self.start_button.setEnabled(False)
        self.direct_copy_button.setEnabled(False)
        self.start_button.setText("Start")
        self.close_button.setText("Cancel")

        self._task.start()

    def _cleanup_results(self):
        self.layout().removeWidget(self._results_scroll)
        self._results_scroll.deleteLater()
        del self._results_scroll
        self.log_edit.show()

    def _append_log(self, message: str):
        self.log_edit.appendPlainText(message)

    def _on_finished(self):
        task = self._task
        assert task is not None
        self._task = None
        self.solving_complete.emit(task)
        self._show_results(task)
        if task.first_solution is not None:
            ra, dec, scale_arcsec = task.first_solution
            s = self.context.settings
            s.set_plate_solve_hint_ra(f"{ra:.6f}")
            s.set_plate_solve_hint_dec(f"{dec:.6f}")
            s.set_plate_solve_hint_scale(round(scale_arcsec, 4))

    def _show_results(self, task: PlateSolveTask):
        self.log_edit.hide()
        self.close_button.setText("Close")
        self.progressBar.setValue(self.progressBar.maximum())
        self.direct_copy_button.setEnabled(True)

        failed_files = [f for f, success, _ in task.file_results if not success]
        if failed_files:
            self._pending_retry_files = failed_files
            self.start_button.setText("Retry Failed")
            self.start_button.setEnabled(True)

        results_widget = QWidget()
        grid = QGridLayout(results_widget)
        grid.setColumnStretch(1, 1)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(8)

        if not task.file_results:
            none_label = QLabel("No files to solve.")
            grid.addWidget(none_label, 0, 0, 1, 3)
            n_rows = 1
        else:
            for row, (file, success, error_msg) in enumerate(task.file_results):
                icon = QLabel("✓" if success else "✗")
                icon.setStyleSheet("color: green; font-weight: bold;" if success else "color: red; font-weight: bold;")
                grid.addWidget(icon, row, 0)

                name_label = QLabel(file.name)
                grid.addWidget(name_label, row, 1)

                if not success and error_msg:
                    btn = QPushButton("Show Error")
                    btn.clicked.connect(lambda checked=False, msg=error_msg: self._show_error_popup(msg))
                    grid.addWidget(btn, row, 2)
            n_rows = len(task.file_results)

        grid.setRowStretch(n_rows, 1)

        scroll = QScrollArea()
        scroll.setWidget(results_widget)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(120)

        main_layout = self.layout()
        log_index = main_layout.indexOf(self.log_edit)
        main_layout.insertWidget(log_index + 1, scroll)
        self._results_scroll = scroll

    def _show_error_popup(self, error_msg: str):
        dlg = QDialog(self)
        dlg.setWindowTitle("Solve Error")
        dlg.resize(600, 400)
        layout = QVBoxLayout(dlg)
        text = QPlainTextEdit(error_msg)
        text.setReadOnly(True)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def _on_error(self, error_message: str):
        self._append_log(f"Error: {error_message}")
        QMessageBox.critical(self, "Plate solving error", error_message)
        self.start_button.setEnabled(True)
        self.direct_copy_button.setEnabled(True)
        self.close_button.setText("Close")
        self._task = None

    def closeEvent(self, event):
        if self._task is not None:
            self._task.cancel()
            self._task = None
        super().closeEvent(event)

    def _on_close_clicked(self):
        self.close()

    def _on_lookup_hint(self):
        from photonfinder.ui.ObjectLookupDialog import ObjectLookupDialog
        dialog = ObjectLookupDialog(self.context, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        result = dialog.result_ra_dec
        if result is None:
            return
        ra_deg, dec_deg = result
        self.hint_ra_edit.setText(f"{ra_deg:.4f}")
        self.hint_dec_edit.setText(f"{dec_deg:.4f}")

    def _update_hms_dms(self):
        from astropy.coordinates import Angle
        import astropy.units as u
        ra_text = self.hint_ra_edit.text().strip()
        dec_text = self.hint_dec_edit.text().strip()
        try:
            hms = Angle(float(ra_text), unit=u.deg).to_string(unit=u.hour, sep=('h', 'm', 's'), precision=1, pad=True)
            self.hint_ra_hms_label.setText(hms)
        except Exception:
            self.hint_ra_hms_label.setText("")
        try:
            dms = Angle(float(dec_text), unit=u.deg).to_string(unit=u.deg, sep=':', precision=1, alwayssign=True)
            self.hint_dec_dms_label.setText(dms)
        except Exception:
            self.hint_dec_dms_label.setText("")

    def _try_convert_ra(self):
        text = self.hint_ra_edit.text().strip()
        if not text:
            return
        try:
            float(text)
            return  # already decimal
        except ValueError:
            pass
        try:
            from astropy.coordinates import Angle
            import astropy.units as u
            self.hint_ra_edit.setText(f"{Angle(text, unit=u.hour).deg:.4f}")
        except Exception:
            pass

    def _try_convert_dec(self):
        text = self.hint_dec_edit.text().strip()
        if not text:
            return
        try:
            float(text)
            return  # already decimal
        except ValueError:
            pass
        try:
            from astropy.coordinates import Angle
            import astropy.units as u
            self.hint_dec_edit.setText(f"{Angle(text, unit=u.deg).deg:.4f}")
        except Exception:
            pass

    def _on_direct_copy(self):
        if not self.files:
            QMessageBox.information(self, "Direct Copy", "Direct Copy requires specific files to be selected.")
            return

        dirs = {(f.root_id, f.path) for f in self.files}
        exclude_ids = {f.rowid for f in self.files}
        wcs_files = self._find_wcs_files_in_dirs(dirs, exclude_ids)
        if not wcs_files:
            QMessageBox.information(self, "Direct Copy", "No solved files found in the same director(ies).")
            return

        dialog = CopyWCSDialog(wcs_files, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        copied = self._apply_wcs_copy(dialog.selected_file(), dialog.should_overwrite())
        if copied:
            import types
            result = types.SimpleNamespace(solved_files=copied)
            self.wcs_copied.emit(result)
        self._infer_hints()

        reply = QMessageBox.question(
            self, "Refine with plate solver?",
            "WCS data was copied. Do you want to start the plate solver to refine or correct it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_solving()

    def _find_wcs_files_in_dirs(self, dirs, exclude_ids=None):
        import operator
        from functools import reduce
        conditions = [(File.root == root_id) & (File.path == path) for root_id, path in dirs]
        condition = reduce(operator.or_, conditions)
        if exclude_ids:
            condition = condition & File.rowid.not_in(exclude_ids)
        query = (File.select(File, Image, FileWCS)
                 .join(FileWCS)
                 .switch(File)
                 .join(Image)
                 .where(condition)
                 .order_by(File.name))
        return [(f, f.image) for f in query]

    def _apply_wcs_copy(self, source_file, overwrite):
        try:
            source_wcs = FileWCS.get(FileWCS.file == source_file)
        except FileWCS.DoesNotExist:
            return []
        source_image = source_file.image

        existing_wcs = {
            fw.file_id
            for fw in FileWCS.select(FileWCS.file).where(
                FileWCS.file.in_([f.rowid for f in self.files])
            )
        }

        copied = []
        for f in self.files:
            if f.rowid in existing_wcs and not overwrite:
                continue
            FileWCS.insert({
                FileWCS.file: f,
                FileWCS.wcs: source_wcs.wcs,
            }).on_conflict_replace().execute()
            Image.update(
                coord_ra=source_image.coord_ra,
                coord_dec=source_image.coord_dec,
                coord_pix256=source_image.coord_pix256,
                coord_radius=source_image.coord_radius,
            ).where(Image.file == f).execute()
            f.has_wcs = True
            img = f.image
            if img is not None:
                img.coord_ra = source_image.coord_ra
                img.coord_dec = source_image.coord_dec
                img.coord_pix256 = source_image.coord_pix256
                img.coord_radius = source_image.coord_radius
            copied.append(f)
        return copied

import copy
import datetime
import functools
import logging
import os
import re
import shutil
import string

from pathlib import Path
from typing import List, Optional

import numpy as np
from PySide6.QtCore import Signal, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QFileDialog, QMessageBox, QDialogButtonBox,
                                QHeaderView, QLabel, QComboBox, QPushButton, QVBoxLayout,
                                QPlainTextEdit, QAbstractItemView, QTableWidgetItem)
from astropy.io import fits
from peewee import JOIN

from photonfinder.calibration import CalibrationMatcher, CalibrationCandidate, SessionKey, session_date_for
from photonfinder.core import ApplicationContext, Settings, decompress
from photonfinder.filesystem import is_compressed, fopen, Importer, header_from_xisf_dict
from photonfinder.models import Image, File, SearchCriteria, FileWCS, Project, ProjectFile
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase
from photonfinder.ui.common import coerce_value
from photonfinder.ui.generated.ExportDialog_ui import Ui_ExportDialog


_SESS_DATE_RE = re.compile(r'\$\{sess_date\}|\$sess_date\b')


def _make_shared_template_str(template_str: str) -> str:
    """Replace path elements containing ${sess_date} with 'shared', preserving separators."""
    # Capturing group keeps separators in the token list at odd indices
    tokens = re.split(r'([/\\])', template_str)
    return ''.join(
        'shared' if (i % 2 == 0 and _SESS_DATE_RE.search(tok)) else tok
        for i, tok in enumerate(tokens)
    )


def template_filename_with_ref(file: File, ref: File, template: string.Template, settings: Settings,
                               decompress=False, export_xisf_as_fits=False,
                               sess_date=None) -> str:
    regular_filename = template_filename(file, template, settings, decompress, export_xisf_as_fits,
                                         sess_date=sess_date)
    if ref is None:
        return regular_filename
    else:
        ref_filename = template_filename(ref, template, settings, decompress, export_xisf_as_fits)
        ref_path = Path(ref_filename).parent
        regular_name = Path(regular_filename).name
        return os.path.join(ref_path, regular_name)


def template_filename(file: File, template: string.Template, settings: Settings,
                      decompress=False, export_xisf_as_fits=False,
                      sess_date=None) -> str:
    image = file.image if hasattr(file, 'image') and file.image else None
    file_name = file.name

    # drop the last extension for the decompressed file name
    if is_compressed(file_name) and decompress:
        file_name = os.path.splitext(file_name)[0]

    if Importer.is_xisf_by_name(file_name) and export_xisf_as_fits:
        file_name = str(Path(file_name).with_suffix(".fit"))

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
        'date_obs': image.date_obs.isoformat() if image and image.date_obs else None
    }
    if image and image.date_obs:
        mapping['date_minus12'] = session_date_for(image.date_obs).isoformat()
        mapping['date'] = image.date_obs.date().isoformat()
    else:
        mapping['date_minus12'] = None
        mapping['date'] = None

    # sess_date: the date of the light-frame session this file belongs to.
    # For light frames this equals date_minus12; for calibration frames it is
    # the session date of the lights they were matched to, which may differ.
    # Falls back to date_minus12 when no session context is available.
    mapping['sess_date'] = sess_date.isoformat() if sess_date else mapping['date_minus12']

    mapping['last_light_path'] = settings.get_last_light_path()
    mapping['filename_no_ext'] = os.path.splitext(file_name)[0]
    mapping['ext'] = os.path.splitext(file_name)[1].lstrip('.')

    result = template.safe_substitute(mapping)
    if not result:
        result = file_name
    if image and image.image_type == 'LIGHT':
        settings.set_last_light_path(Path(result).parent)
    return result


def build_file_session_dates(
    session_keys: list[SessionKey],
    sessions: dict[SessionKey, list[File]],
    calib_selections: dict[int, dict[str, Optional[CalibrationCandidate]]],
    use_master: bool,
) -> dict[int, datetime.date]:
    """Map file rowid → session date. Calibration files get the date of the session they matched."""
    file_session_dates = {}
    for row, key in enumerate(session_keys):
        d = key.session_date
        if d is None:
            continue
        for f in sessions[key]:
            if f.rowid is not None:
                file_session_dates[f.rowid] = d
        for candidate in calib_selections[row].values():
            if candidate:
                files = [candidate.master] if use_master and candidate.master else candidate.files
                for f in files:
                    if f.rowid is not None and f.rowid not in file_session_dates:
                        file_session_dates[f.rowid] = d
    return file_session_dates


def collect_calibration_files(
    calib_selections: dict[int, dict[str, Optional[CalibrationCandidate]]],
    use_master: bool,
) -> list[File]:
    """Return all unique calibration files selected in the grid, respecting the Use Master flag."""
    seen_ids: set[int] = set()
    result: list[File] = []
    for row_sel in calib_selections.values():
        for candidate in row_sel.values():
            if not candidate:
                continue
            files_to_add = [candidate.master] if use_master and candidate.master else candidate.files
            for f in files_to_add:
                if f.rowid not in seen_ids:
                    seen_ids.add(f.rowid)
                    result.append(f)
    return result


def build_file_headers_map(
    session_keys: list[SessionKey],
    sessions: dict[SessionKey, list[File]],
    calib_selections: dict[int, dict[str, Optional[CalibrationCandidate]]],
    calib_headers: dict[int, str],
) -> dict[int, dict]:
    """Map file rowid → parsed custom FITS headers for each session that has headers set.

    Headers from a session are applied to its light files and selected calibration files
    (candidate.files only — master is not used here). First-assignment wins for shared files.
    """
    file_headers = {}
    for row, key in enumerate(session_keys):
        raw = calib_headers.get(row, "")
        if not raw.strip():
            continue
        parsed = {}
        for line in raw.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            if '=' not in line:
                logging.warning(f"Ignoring malformed custom header line (expected KEY=VALUE): {line!r}")
                continue
            k, v = line.split('=', 1)
            parsed[k.strip()] = coerce_value(v.strip())
        if not parsed:
            continue
        for f in sessions[key]:
            if f.rowid is not None:
                file_headers[f.rowid] = parsed
        for candidate in calib_selections[row].values():
            if candidate:
                for f in candidate.files:
                    if f.rowid is not None and f.rowid not in file_headers:
                        file_headers[f.rowid] = parsed
    return file_headers


_CALIB_LABEL_FLAGS = {
    "DARK":     dict(show_filter=False, show_exposure=True,  show_temperature=True),
    "FLAT":     dict(show_filter=True,  show_exposure=True,  show_temperature=False),
    "BIAS":     dict(show_filter=False, show_exposure=False, show_temperature=False),
    "DARKFLAT": dict(show_filter=False, show_exposure=True,  show_temperature=False),
}


def _candidate_label(candidate: CalibrationCandidate, calib_type: str) -> str:
    flags = _CALIB_LABEL_FLAGS.get(calib_type, {})
    show_filter = flags.get("show_filter", True)
    show_exposure = flags.get("show_exposure", True)
    show_temperature = flags.get("show_temperature", False)
    img = candidate.files[0].image if candidate.files and hasattr(candidate.files[0], 'image') else None
    parts = [str(candidate.session_date) if candidate.session_date else "Unknown date"]
    if show_filter and img and img.filter:
        parts.append(img.filter)
    if show_exposure and img and img.exposure is not None:
        parts.append(f"{img.exposure:g}s")
    if show_temperature and img and img.set_temp is not None:
        parts.append(f"{img.set_temp:g}°C")
    parts.append(f"({candidate.count} frames)")
    return "  ".join(parts)


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
        self.export_xisf_as_fits = False
        self.override_platesolve = False
        self.file_headers = {}        # file rowid → {KEY: value}
        self.file_session_dates = {}  # file rowid → datetime.date (session date)
        self.shared_file_ids = set()  # file rowids exported to "shared" path
        self.shared_pattern = None
        self.project = None
        self.project_file_ids = set()  # file rowids to add to the project (lights only)

    def export_files(self, search_criteria: SearchCriteria,
                     files: Optional[List[File]], output_path: str, decompress: bool,
                     pattern: str, total_files: int, export_xisf_as_fits: bool = False,
                     override_platesolve: bool = False, file_headers: dict = None,
                     file_session_dates: dict = None, project: Project = None,
                     shared_file_ids: set = None, project_file_ids: set = None):
        """Start the export process in a background thread."""
        self.search_criteria = search_criteria
        self.files = files
        self.output_path = output_path
        self.decompress = decompress
        self.pattern = string.Template(pattern)
        self.cancelled = False
        self.total_files = total_files
        self.export_xisf_as_fits = export_xisf_as_fits
        self.override_platesolve = override_platesolve
        self.file_headers = file_headers or {}
        self.file_session_dates = file_session_dates or {}
        self.shared_file_ids = shared_file_ids or set()
        self.shared_pattern = string.Template(_make_shared_template_str(pattern)) if self.shared_file_ids else None
        self.project = project
        self.project_file_ids = project_file_ids or set()
        self.run_in_thread(self._export_files_task)

    def _export_files_task(self):
        """Background task to export files."""
        try:
            for i, file in enumerate(self.files):
                if self.cancelled:
                    break
                self._process_file(file, i)
            self.finished.emit()
        except Exception as e:
            logging.error(f"Error exporting files: {e}", exc_info=True)
            self.error.emit(str(e))

    def _process_file(self, file: File, index: int):
        """Process a single file during export."""
        source_path = file.full_filename()
        custom_headers = self.file_headers.get(file.rowid, {})
        sess_date = self.file_session_dates.get(file.rowid)

        is_shared = file.rowid in self.shared_file_ids
        active_pattern = self.shared_pattern if (is_shared and self.shared_pattern) else self.pattern

        ref_file = self.search_criteria.reference_file if self.search_criteria else None
        output_filename = template_filename_with_ref(file, ref_file, active_pattern,
                                                     self.context.settings, self.decompress, self.export_xisf_as_fits,
                                                     sess_date=sess_date)
        output_file_path = os.path.join(self.output_path, output_filename)

        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        if Path(output_file_path).exists():
            logging.info(f"File {output_file_path} already exists, skipping")
        else:
            logging.info(f"Copying {source_path} to {output_file_path}")
            self.copy_file(source_path, output_file_path, file, custom_headers)

        if self.project and file.rowid in self.project_file_ids:
            link = ProjectFile(project=self.project, file=file)
            link.save()

        self.progress.emit(int((index + 1) / self.total_files * 100))

    def copy_file(self, source_path: str, output_file_path: str, file: File, custom_headers: dict = None):
        custom_headers = custom_headers or {}
        # XISF
        if Importer.is_xisf_by_name(source_path):
            if self.export_xisf_as_fits:
                self.copy_xisf_as_fits(source_path, output_file_path, file, custom_headers)
                shutil.copystat(source_path, output_file_path)
            else:
                shutil.copy2(source_path, output_file_path)
        # FITS
        elif Importer.is_fits_by_name(source_path):
            if self.customize_fits_headers(custom_headers) or (is_compressed(source_path) and self.decompress):
                with fopen(source_path) as source_file:
                    self.copy_fits_data(source_file, output_file_path, file, custom_headers)
                shutil.copystat(source_path, output_file_path)
            else:
                shutil.copy2(source_path, output_file_path)

    def copy_fits_data(self, source_fd, output_file_path: str, file: File, custom_headers: dict = None):
        custom_headers = custom_headers or {}
        if self.customize_fits_headers(custom_headers):
            with fits.open(source_fd) as hdul:
                header = hdul[0].header
                data = hdul[0].data

                self._copy_wcs(file, header)

                for key, value in custom_headers.items():
                    header[key] = value

                hdu = fits.PrimaryHDU(data=data, header=header)
                hdu.writeto(output_file_path, overwrite=True, output_verify='silentfix')
        else:
            with open(output_file_path, "wb") as destination_file:
                shutil.copyfileobj(source_fd, destination_file)

    def _copy_wcs(self, file: File, header):
        if self.override_platesolve and hasattr(file, 'filewcs'):
            wcs_str = decompress(file.filewcs.wcs)
            wcs_header = fits.Header.fromstring(wcs_str)
            for key in wcs_header:
                if not key.startswith('NAXIS'):
                    header[key] = wcs_header[key]

    def copy_xisf_as_fits(self, source_path: str, output_file_path: str, file: File, custom_headers: dict = None):
        custom_headers = custom_headers or {}
        from xisf import XISF
        xisf = XISF(source_path)
        metas = xisf.get_images_metadata()

        for i, meta in enumerate(metas):
            if "FITSKeywords" in meta:
                header = header_from_xisf_dict(meta["FITSKeywords"])
                image_data = xisf.read_image(i, 'channels_first')

                self._copy_wcs(file, header)

                for key, value in custom_headers.items():
                    header[key] = value

                image_data = np.squeeze(image_data)

                hdu = fits.PrimaryHDU(data=image_data, header=header)
                hdu.writeto(output_file_path, overwrite=True, output_verify='silentfix')
                return

        raise Exception(f"No suitable image with FITS keywords found in XISF file: {source_path}")

    def cancel(self):
        """Cancel the export process."""
        self.cancelled = True

    def customize_fits_headers(self, custom_headers: dict = None):
        return self.override_platesolve or custom_headers


class CalibrationGridLoader(BackgroundLoaderBase):
    """Loads calibration candidates for each session row in a background thread."""
    row_ready = Signal(int, dict)  # row index → {calib_type: [CalibrationCandidate]}
    all_done = Signal()

    def load(self, session_keys: list, sessions: dict, matcher):
        self.run_in_thread(self._load_task, session_keys, sessions, matcher)

    def _load_task(self, session_keys, sessions, matcher):
        for row, key in enumerate(session_keys):
            lights = sessions[key]
            candidates = {
                "DARK": matcher.dark_candidates(lights),
                "FLAT": matcher.flat_candidates(lights),
                "BIAS": matcher.bias_candidates(lights),
                "DARKFLAT": [],  # populated reactively when a flat is selected
            }
            for cands in candidates.values():
                for cand in cands:
                    matcher.add_best_master(cand)
            self.row_ready.emit(row, candidates)
        self.all_done.emit()


# Column layout: 0=Session, 1-4=calibration types, 5=Headers button
_CALIB_COL_MAP = {"FLAT": 1, "DARK": 2, "BIAS": 3, "DARKFLAT": 4}
_HEADERS_COL = 5
_CALIB_COL_LABELS = ["Session", "Flat", "Dark", "Bias", "DarkFlat", "Headers"]


class DryRunResultDialog(QDialog):
    """Shows the theoretical file copy operations without performing them."""

    def __init__(self, lines: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dry Run — File Copy Preview")
        self.setMinimumSize(1200, 500)
        layout = QVBoxLayout(self)
        text_edit = QPlainTextEdit(self)
        text_edit.setReadOnly(True)
        text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        text_edit.setPlainText("\n".join(lines))
        layout.addWidget(text_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class HeadersDialog(QDialog):
    """Small dialog for entering per-session custom FITS headers."""

    def __init__(self, initial_text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom FITS Headers")
        self.setMinimumSize(420, 220)
        layout = QVBoxLayout(self)
        self.textEdit = QPlainTextEdit(self)
        self.textEdit.setPlaceholderText("KEY=VALUE, one per line")
        self.textEdit.setPlainText(initial_text)
        layout.addWidget(self.textEdit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_text(self) -> str:
        return self.textEdit.toPlainText()


class ExportDialog(QDialog, Ui_ExportDialog):
    """Dialog for exporting files."""

    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria,
                 files: Optional[List[File]] = None, parent=None):
        super(ExportDialog, self).__init__(parent)
        self.setupUi(self)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.context = context
        self.search_criteria = copy.deepcopy(search_criteria)

        # Materialize all files and split into lights vs. calibration preselect
        all_files = self._materialize_files(files)
        self.light_files, self.calib_preselect_files = self._partition_lights(all_files)
        self.first_file = self.light_files[0] if self.light_files else (all_files[0] if all_files else None)

        # Build session grid
        self.matcher = CalibrationMatcher(context)
        self.sessions = self.matcher.get_sessions(self.light_files)
        self._session_keys: List[SessionKey] = list(self.sessions.keys())
        self._calib_candidates: dict[int, dict[str, list]] = {}
        self._calib_selections: dict[int, dict[str, Optional[CalibrationCandidate]]] = {}
        self._calib_headers: dict[int, str] = {}  # row → raw KEY=VALUE text
        self._preselections = self.matcher.preselect_from_existing(self.sessions, self.calib_preselect_files)

        self._setup_calibration_table()
        self._setup_calibration_rows()

        self._grid_loader = CalibrationGridLoader(context)
        self._grid_loader.row_ready.connect(self._on_row_candidates_ready)
        self._grid_loader.all_done.connect(self.calibrationTable.resizeColumnsToContents)
        self._grid_loader.load(self._session_keys, self.sessions, self.matcher)

        if not self.light_files:
            self.calibrationTable.hide()

        if self.search_criteria.reference_file:
            self.useRefCheckBox.setEnabled(True)
            self.useRefCheckBox.setText(self.search_criteria.reference_file.name)
        n = len(self.light_files) if self.light_files else len(self.calib_preselect_files)
        kind = "light frames" if self.light_files else "calibration frames"
        self.setWindowTitle(f"Export {n} {kind}")

        self.load_settings()

        self.export_worker = ExportWorker(context)
        self.export_worker.progress.connect(self.progressBar.setValue)
        self.export_worker.finished.connect(self.on_export_finished)
        self.export_worker.error.connect(self.on_export_error)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText("Export")

        self.buttonBox.accepted.connect(self.export_files)
        self.dryRunButton.clicked.connect(self.dry_run)
        self.patternComboBox.editTextChanged.connect(self.update_preview)
        self.useRefCheckBox.stateChanged.connect(self.update_preview)
        self.useMasterCheckBox.stateChanged.connect(self._refresh_all_calib_labels)
        self.sharedSessionCheckBox.stateChanged.connect(self._refresh_all_calib_labels)
        self.variablesButton.clicked.connect(self._open_variables_docs)

        if self.first_file:
            self.update_preview(self.patternComboBox.currentText())

    def _materialize_files(self, files: Optional[List[File]]) -> List[File]:
        if files:
            return files
        with self.context.database.bind_ctx([File, Image]):
            query = (File
                     .select(File, Image, FileWCS)
                     .join_from(File, Image, JOIN.LEFT_OUTER)
                     .join_from(File, FileWCS, JOIN.LEFT_OUTER)
                     .order_by(File.root, File.path, File.name))
            query = Image.apply_search_criteria(query, self.search_criteria)
            return list(query)

    def _partition_lights(self, files: List[File]) -> tuple:
        lights = []
        others = []
        for f in files:
            image = f.image if hasattr(f, 'image') else None
            itype = (image.image_type or "").upper() if image else ""
            if itype in ("DARK", "FLAT", "BIAS"):
                others.append(f)
            else:
                lights.append(f)
        return lights, others

    def _setup_calibration_table(self):
        self.calibrationTable.setColumnCount(len(_CALIB_COL_LABELS))
        self.calibrationTable.setHorizontalHeaderLabels(_CALIB_COL_LABELS)
        self.calibrationTable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.calibrationTable.verticalHeader().setVisible(False)
        self.calibrationTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hdr = self.calibrationTable.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        self.calibrationTable.setColumnWidth(0, 280)
        for col in range(1, 5):
            self.calibrationTable.setColumnWidth(col, 160)
        self.calibrationTable.setColumnWidth(_HEADERS_COL, 50)

    def _setup_calibration_rows(self):
        """Populate session labels and placeholder cells; candidates are loaded asynchronously."""
        self.calibrationTable.setRowCount(len(self._session_keys))
        for row, key in enumerate(self._session_keys):
            lights = self.sessions[key]
            date_str = str(key.session_date) if key.session_date else "?"
            filter_str = key.filter or "?"
            exp_str = f"{key.exposure:g}s" if key.exposure else "?"
            first_img = lights[0].image if lights and hasattr(lights[0], 'image') else None
            temp_str = f"  {first_img.set_temp:g}°C" if first_img and first_img.set_temp is not None else ""
            session_text = f"{date_str}  {filter_str}  {exp_str}{temp_str}  ({len(lights)} frames)"
            self.calibrationTable.setItem(row, 0, QTableWidgetItem(session_text))

            self._calib_candidates[row] = {}
            self._calib_selections[row] = {k: None for k in _CALIB_COL_MAP}

            for col in range(1, 5):
                placeholder = QLabel("…")
                placeholder.setEnabled(False)
                self.calibrationTable.setCellWidget(row, col, placeholder)

            btn = QPushButton("…")
            btn.setToolTip("Custom FITS headers for this session")
            btn.setMaximumWidth(40)
            btn.clicked.connect(functools.partial(self._open_headers_dialog, row))
            self.calibrationTable.setCellWidget(row, _HEADERS_COL, btn)

    def _on_row_candidates_ready(self, row: int, candidates: dict):
        """Slot: fills in calibration combo boxes for one row when background queries complete."""
        presel = self._preselections.get(self._session_keys[row], {})
        for calib_type, col in _CALIB_COL_MAP.items():
            cands = candidates.get(calib_type, [])
            self._calib_candidates[row][calib_type] = cands
            self._set_calib_cell(row, col, calib_type, cands, presel.get(calib_type))

        # Connect signals after all cells are populated to avoid spurious DarkFlat updates
        for calib_type, col in _CALIB_COL_MAP.items():
            widget = self.calibrationTable.cellWidget(row, col)
            if isinstance(widget, QComboBox):
                if calib_type == "FLAT":
                    widget.currentIndexChanged.connect(
                        functools.partial(self._on_flat_changed, row))
                else:
                    widget.currentIndexChanged.connect(
                        functools.partial(self._on_calib_combo_changed, row, calib_type))

        # Seed the DarkFlat column based on the initially selected flat
        self._on_flat_changed(row, 0)

    def _set_calib_cell(self, row: int, col: int, calib_type: str,
                        candidates: list, preselection: Optional[CalibrationCandidate] = None):
        if not candidates:
            widget = QLabel("—")
            widget.setEnabled(False)
            self.calibrationTable.setCellWidget(row, col, widget)
            self._calib_selections[row][calib_type] = None
            return

        combo = QComboBox()
        combo.blockSignals(True)
        combo.addItem("(none)", None)
        for c in candidates:
            combo.addItem(_candidate_label(c, calib_type), c)
            first_file = c.files[0] if c.files else None
            if first_file:
                combo.setItemData(combo.count() - 1, first_file.full_filename(), Qt.ItemDataRole.ToolTipRole)

        # Pre-select: use preselection date match or default to most-recent (index 1)
        selected_idx = 1
        if preselection:
            for i, c in enumerate(candidates):
                if c.session_date == preselection.session_date:
                    selected_idx = i + 1
                    break
        combo.setCurrentIndex(selected_idx)
        combo.blockSignals(False)

        self._calib_selections[row][calib_type] = combo.currentData()
        self.calibrationTable.setCellWidget(row, col, combo)

    def _update_calib_cell_label(self, row: int, calib_type: str,
                                  shared_cand: Optional[CalibrationCandidate] = None):
        """Update combo items' text and color to reflect master/shared status."""
        col = _CALIB_COL_MAP[calib_type]
        widget = self.calibrationTable.cellWidget(row, col)
        if not isinstance(widget, QComboBox):
            return
        use_master = self.useMasterCheckBox.isChecked()
        show_shared = self.sharedSessionCheckBox.isChecked()

        shared_file_ids = (frozenset(f.rowid for f in shared_cand.files if f.rowid is not None)
                           if (shared_cand and show_shared) else frozenset())

        current_is_shared = False
        for i in range(widget.count()):
            candidate = widget.itemData(i)
            if candidate is None:
                continue
            label = _candidate_label(candidate, calib_type)
            is_this_shared = False
            if shared_file_ids:
                cand_ids = frozenset(f.rowid for f in candidate.files if f.rowid is not None)
                is_this_shared = bool(cand_ids & shared_file_ids)
            prefix = ("☁ " if is_this_shared else "") + ("→ master: " if use_master and candidate.master else "")
            widget.setItemText(i, prefix + label)
            if i == widget.currentIndex() and is_this_shared:
                current_is_shared = True

        widget.setStyleSheet("QComboBox { color: #1565C0; }" if current_is_shared else "")

        selected = widget.currentData()
        if selected is not None:
            effective_file = selected.master if (use_master and selected.master) else (selected.files[0] if selected.files else None)
            widget.setToolTip(effective_file.full_filename() if effective_file else "")
        else:
            widget.setToolTip("")

    def _refresh_all_calib_labels(self):
        shared = self._compute_shared_candidates()
        for row in range(len(self._session_keys)):
            for calib_type in _CALIB_COL_MAP:
                self._update_calib_cell_label(row, calib_type, shared.get(calib_type))

    def _on_calib_combo_changed(self, row: int, calib_type: str, index: int):
        col = _CALIB_COL_MAP[calib_type]
        widget = self.calibrationTable.cellWidget(row, col)
        if isinstance(widget, QComboBox):
            self._calib_selections[row][calib_type] = widget.currentData()
        self._refresh_all_calib_labels()

    def _on_flat_changed(self, row: int, index: int):
        flat_widget = self.calibrationTable.cellWidget(row, _CALIB_COL_MAP["FLAT"])
        flat_candidate = flat_widget.currentData() if isinstance(flat_widget, QComboBox) else \
            self._calib_selections[row].get("FLAT")
        self._calib_selections[row]["FLAT"] = flat_candidate

        df_cands = self.matcher.dark_flat_candidates(flat_candidate)
        self._calib_candidates[row]["DARKFLAT"] = df_cands
        self._set_calib_cell(row, 4, "DARKFLAT", df_cands, None)

        for cand in df_cands:
            self.matcher.add_best_master(cand)

        df_widget = self.calibrationTable.cellWidget(row, 4)
        if isinstance(df_widget, QComboBox):
            df_widget.currentIndexChanged.connect(
                functools.partial(self._on_calib_combo_changed, row, "DARKFLAT"))
        self._refresh_all_calib_labels()

    def _compute_shared_candidates(self) -> dict:
        """Returns {calib_type: CalibrationCandidate} for candidates used by 2+ sessions."""
        if not self.sharedSessionCheckBox.isChecked():
            return {}
        type_cand_map = {}  # calib_type → {frozenset_of_file_ids: (candidate, session_count)}
        for row_sel in self._calib_selections.values():
            for calib_type, candidate in row_sel.items():
                if candidate is None:
                    continue
                cid = frozenset(f.rowid for f in candidate.files if f.rowid is not None)
                tc = type_cand_map.setdefault(calib_type, {})
                if cid in tc:
                    existing, n = tc[cid]
                    tc[cid] = (existing, n + 1)
                else:
                    tc[cid] = (candidate, 1)
        result = {}
        for calib_type, cand_map in type_cand_map.items():
            multi = [(c, n) for c, n in cand_map.values() if n >= 2]
            if multi:
                result[calib_type] = max(multi, key=lambda x: x[1])[0]
        return result

    def _build_shared_file_ids(self) -> set:
        """Returns set of file rowids that should use the shared export path."""
        shared_cands = self._compute_shared_candidates()
        if not shared_cands:
            return set()
        use_master = self.useMasterCheckBox.isChecked()
        shared_ids = set()
        for candidate in shared_cands.values():
            files_to_add = [candidate.master] if use_master and candidate.master else candidate.files
            for f in files_to_add:
                if f.rowid is not None:
                    shared_ids.add(f.rowid)
        return shared_ids

    def _collect_calibration_files(self) -> List[File]:
        return collect_calibration_files(self._calib_selections, self.useMasterCheckBox.isChecked())

    def _open_headers_dialog(self, row: int):
        dlg = HeadersDialog(self._calib_headers.get(row, ""), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            text = dlg.get_text().strip()
            bad_lines = [l.strip() for l in text.splitlines() if l.strip() and '=' not in l]
            if bad_lines:
                QMessageBox.warning(
                    self, "Custom FITS Headers",
                    "The following lines were ignored (expected KEY=VALUE format):\n\n"
                    + "\n".join(bad_lines)
                )
            self._calib_headers[row] = text
            btn = self.calibrationTable.cellWidget(row, _HEADERS_COL)
            if isinstance(btn, QPushButton):
                btn.setText("✎" if text else "…")

    def _build_file_session_dates(self) -> dict:
        return build_file_session_dates(
            self._session_keys, self.sessions, self._calib_selections,
            self.useMasterCheckBox.isChecked()
        )

    def _build_file_headers_map(self) -> dict:
        return build_file_headers_map(
            self._session_keys, self.sessions, self._calib_selections, self._calib_headers
        )

    def _open_variables_docs(self):
        docs_path = Path(__file__).parent.parent.parent / "docs" / "export-templates.md"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(docs_path)))

    def load_settings(self):
        settings = self.context.settings

        last_output_path = settings.get_last_export_path()
        if last_output_path:
            self.outputPathEdit.setText(last_output_path)

        self.decompressCheckBox.setChecked(settings.get_last_export_decompress())
        self.exportXisfAsFitsCheckBox.setChecked(settings.get_last_export_xisf_as_fits())
        self.overridePlatesolveCheckBox.setChecked(settings.get_last_export_override_platesolve())
        self.useMasterCheckBox.setChecked(settings.get_last_export_use_master())
        self.sharedSessionCheckBox.setChecked(settings.get_last_export_shared_session())

        patterns = settings.get_last_export_patterns()
        self.patternComboBox.clear()
        if patterns:
            self.patternComboBox.addItems(patterns)
            self.patternComboBox.setCurrentText(patterns[0])

    def save_settings(self):
        settings = self.context.settings

        settings.set_last_export_path(self.outputPathEdit.text())
        settings.set_last_export_decompress(self.decompressCheckBox.isChecked())
        settings.set_last_export_xisf_as_fits(self.exportXisfAsFitsCheckBox.isChecked())
        settings.set_last_export_override_platesolve(self.overridePlatesolveCheckBox.isChecked())
        settings.set_last_export_use_master(self.useMasterCheckBox.isChecked())
        settings.set_last_export_shared_session(self.sharedSessionCheckBox.isChecked())

        pattern = self.patternComboBox.currentText()
        patterns = settings.get_last_export_patterns() or []
        if pattern in patterns:
            patterns.remove(pattern)
        patterns.insert(0, pattern)
        settings.set_last_export_patterns(patterns[:10])

    def export_files(self):
        self.save_settings()
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        if not self.useRefCheckBox.isChecked():
            self.search_criteria.reference_file = None

        project = None
        if self.createGroupCheckBox.isChecked():
            path = Path(self.outputPathEdit.text())
            project = Project(name=f"Export {path.name} {datetime.datetime.now().isoformat()}",
                              last_change=datetime.datetime.now())
            project.save()

        calib_files = self.calib_preselect_files if not self.light_files else self._collect_calibration_files()
        all_files = self.light_files + calib_files

        self.export_worker.export_files(
            self.search_criteria,
            all_files,
            self.outputPathEdit.text(),
            self.decompressCheckBox.isChecked(),
            self.patternComboBox.currentText(),
            len(all_files),
            self.exportXisfAsFitsCheckBox.isChecked(),
            self.overridePlatesolveCheckBox.isChecked(),
            self._build_file_headers_map(),
            self._build_file_session_dates(),
            project,
            self._build_shared_file_ids(),
            {f.rowid for f in self.light_files}
        )

        self.buttonBox.rejected.connect(self.cancel_export)

    def dry_run(self):
        output_path = self.outputPathEdit.text()
        pattern_str = self.patternComboBox.currentText()
        tpl = string.Template(pattern_str)
        shared_tpl = string.Template(_make_shared_template_str(pattern_str))

        ref_file = self.search_criteria.reference_file if self.useRefCheckBox.isChecked() else None
        decompress = self.decompressCheckBox.isChecked()
        export_xisf_as_fits = self.exportXisfAsFitsCheckBox.isChecked()

        calib_files = self.calib_preselect_files if not self.light_files else self._collect_calibration_files()
        all_files = self.light_files + calib_files

        shared_file_ids = self._build_shared_file_ids()
        file_session_dates = self._build_file_session_dates()

        lines = []
        for file in all_files:
            source_path = file.full_filename()
            sess_date = file_session_dates.get(file.rowid)
            is_shared = file.rowid in shared_file_ids
            active_tpl = shared_tpl if is_shared else tpl
            output_filename = template_filename_with_ref(
                file, ref_file, active_tpl, self.context.settings,
                decompress, export_xisf_as_fits, sess_date=sess_date)
            dest_path = os.path.join(output_path, output_filename)
            lines.append(f"{source_path}  →  {dest_path}")

        if not lines:
            lines = ["(no files to export)"]

        dlg = DryRunResultDialog(lines, parent=self)
        dlg.exec()

    def browse_output_path(self):
        current_path = self.outputPathEdit.text()
        directory = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", current_path or str(Path.home())
        )
        if directory:
            self.outputPathEdit.setText(directory)

    def cancel_export(self):
        if self.export_worker:
            self.export_worker.cancel()
        self.reject()

    def on_export_finished(self):
        self.accept()

    def on_export_error(self, error_message):
        QMessageBox.critical(self, "Export Error", error_message)
        self.reject()

    def update_preview(self, ignored):
        text = self.patternComboBox.currentText()
        tpl = string.Template(template=text)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(tpl.is_valid())
        if not self.first_file:
            return
        ref = self.search_criteria.reference_file if self.useRefCheckBox.isChecked() else None
        filename = template_filename_with_ref(self.first_file, ref, tpl, self.context.settings,
                                              self.decompressCheckBox.isChecked(),
                                              self.exportXisfAsFitsCheckBox.isChecked())
        self.outputPreview.setText(filename)

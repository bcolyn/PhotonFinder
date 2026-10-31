"""Non-modal dialog for tuning and saving the quality diagnosis of the current search."""
import copy
import dataclasses
import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout,
)

from photonfinder.core import ApplicationContext
from photonfinder.models import DIAGNOSIS_BAD_VALUES, DIAGNOSIS_MISSING_METRICS, SearchCriteria
from photonfinder.quality_diagnosis import (
    DiagnosisResult, DiagnosisScope, DiagnosisSettings, Metric, MetricRule, RuleMode, diagnose,
    frames_needing_analysis, load_scope, save_diagnoses,
)
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase, MissingFramesAnalysisTask
from photonfinder.ui.ImageAnalysisDialog import ImageAnalysisDialog

_METRIC_NAMES = {Metric.FWHM: "FWHM", Metric.ELONGATION: "Elongation", Metric.BACKGROUND: "Background",
                 Metric.STAR_COUNT: "Star count"}
_MODE_NAMES = {RuleMode.OFF: "Off", RuleMode.MAD: "× MAD", RuleMode.PERCENT: "% from median"}
_DEFAULT_LIMITS = {RuleMode.MAD: 3.0, RuleMode.PERCENT: 20.0}
# Recalculating a very large scope takes most of a second, too slow for every keystroke.
_PREVIEW_DELAY_MS = 150
_LIBRARY_BUSY = "another job (a scan, compression or analysis) is writing to this library. " \
                "Try again when it has finished."


def scope_criteria(search_criteria: SearchCriteria) -> SearchCriteria:
    """What a search means as a diagnosis scope: frames hidden by a diagnosis filter still
    belong to their groups, and the sort order doesn't matter."""
    return dataclasses.replace(copy.deepcopy(search_criteria), diagnosis_hidden=[], sorting_index=None,
                               sorting_field=None, sorting_desc=True)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class _DiagnosisLoader(BackgroundLoaderBase):
    scope_loaded = Signal(object, object)  # (DiagnosisScope, the criteria it was loaded for)
    load_failed = Signal(str, object)  # (message, the criteria it was loading)
    saved = Signal(bool)  # False: another writer holds the library
    save_failed = Signal(str)

    def load(self, criteria: SearchCriteria, selected_file_ids: set[int] | None):
        self.run_in_thread(self._load, criteria, selected_file_ids)

    def _load(self, criteria: SearchCriteria, selected_file_ids: set[int] | None):
        try:
            self.scope_loaded.emit(load_scope(self.context, criteria, selected_file_ids), criteria)
        except Exception as exc:
            logging.error("Could not load the diagnosis scope", exc_info=True)
            self.load_failed.emit(str(exc), criteria)

    def save(self, verdicts: dict):
        self.run_writer_in_thread("Saving diagnosis", self._save, verdicts)

    def _save(self, verdicts: dict):
        try:
            self.saved.emit(save_diagnoses(self.context, verdicts))
        except Exception as exc:
            logging.error("Could not save the diagnosis", exc_info=True)
            self.save_failed.emit(str(exc))


class _MetricRow:
    def __init__(self, metric: Metric, grid: QGridLayout, row: int, on_change):
        self.count_label = QLabel()
        self.mode_combo = QComboBox()
        for mode, name in _MODE_NAMES.items():
            self.mode_combo.addItem(name, mode)
        self.limit_spin = QDoubleSpinBox()
        self.limit_spin.setRange(0.1, 1000.0)
        self.limit_spin.setDecimals(1)
        self.limit_spin.setSingleStep(0.5)

        grid.addWidget(QLabel(f"{_METRIC_NAMES[metric]}:"), row, 0)
        grid.addWidget(self.limit_spin, row, 1)
        grid.addWidget(self.mode_combo, row, 2)
        grid.addWidget(self.count_label, row, 3)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.currentIndexChanged.connect(on_change)
        self.limit_spin.valueChanged.connect(on_change)

    def _on_mode_changed(self):
        mode = self.mode_combo.currentData()
        self.limit_spin.setEnabled(mode != RuleMode.OFF)
        if mode in _DEFAULT_LIMITS:  # a limit means something different in each mode
            self.limit_spin.setValue(_DEFAULT_LIMITS[mode])

    def set_rule(self, rule: MetricRule):
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(rule.mode))
        self.limit_spin.setEnabled(rule.mode != RuleMode.OFF)
        self.limit_spin.setValue(rule.limit)

    def rule(self) -> MetricRule:
        return MetricRule(self.mode_combo.currentData(), self.limit_spin.value())


class DiagnoseQualityDialog(QDialog):
    """Judges the peer groups of the selection or else the search, previewing in memory until
    the user saves.

    The scope stays fixed until the user asks for the current one, since saving overwrites
    stored results and must not change target because the grid was filtered meanwhile.
    """
    writer_window_label = "Diagnose Quality"  # see MainWindow._database_switch_blockers

    def __init__(self, context: ApplicationContext, main_window, parent=None):
        super().__init__(parent)
        self.context = context
        self.main_window = main_window
        self.setWindowTitle("Diagnose Quality")
        self._scope: DiagnosisScope | None = None
        self._scope_criteria: SearchCriteria | None = None
        self._scope_selection: set[int] = set()
        self._watched_panel = None
        self._hide_after_save = False
        self._settings_being_saved: DiagnosisSettings | None = None
        self._saving = False
        self._analysing = False
        self.analysis_dialog: ImageAnalysisDialog | None = None

        self._loader = _DiagnosisLoader(context)
        self._loader.scope_loaded.connect(self._on_scope_loaded)
        self._loader.load_failed.connect(self._on_load_failed)
        self._loader.saved.connect(self._on_saved)
        self._loader.save_failed.connect(self._on_save_failed)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._update_preview)

        self._build_ui()
        self._set_settings(DiagnosisSettings.from_json(context.settings.get_quality_diagnosis_settings()))

        main_window.tabWidget.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed()
        self.use_current_search()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.scope_label = QLabel()
        layout.addWidget(self.scope_label)

        stale_row = QHBoxLayout()
        self.stale_label = QLabel("The search or selection has changed. Saving still applies to the scope above.")
        self.use_current_button = QPushButton("Use current search")
        self.use_current_button.clicked.connect(self.use_current_search)
        stale_row.addWidget(self.stale_label, 1)
        stale_row.addWidget(self.use_current_button)
        layout.addLayout(stale_row)

        counts_row = QHBoxLayout()
        self.counts_label = QLabel()
        self.analyse_button = QPushButton("Analyse missing")
        self.analyse_button.setToolTip("Analyse the frames in this scope that have no statistics, "
                                       "skipping groups too small to judge")
        self.analyse_button.clicked.connect(self._analyse_missing)
        counts_row.addWidget(self.counts_label, 1)
        counts_row.addWidget(self.analyse_button)
        layout.addLayout(counts_row)

        grid = QGridLayout()
        self.metric_rows = {metric: _MetricRow(metric, grid, row, self._schedule_preview)
                            for row, metric in enumerate(Metric)}
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        group_row = QHBoxLayout()
        group_row.addWidget(QLabel("Minimum group size"))
        self.min_group_spin = QSpinBox()
        self.min_group_spin.setRange(2, 100000)
        self.min_group_spin.valueChanged.connect(self._schedule_preview)
        group_row.addWidget(self.min_group_spin, 1)
        layout.addLayout(group_row)

        self.rejected_label = QLabel()
        self.details_label = QLabel()
        layout.addWidget(self.rejected_label)
        layout.addWidget(self.details_label)

        buttons = QHBoxLayout()
        self.status_label = QLabel()
        self.save_button = QPushButton("Save")
        self.save_and_hide_button = QPushButton("Save and hide bad frames")
        close_button = QPushButton("Close")
        self.save_button.clicked.connect(lambda: self._save(hide_bad_frames=False))
        self.save_and_hide_button.clicked.connect(lambda: self._save(hide_bad_frames=True))
        close_button.clicked.connect(self.close)
        buttons.addWidget(self.status_label, 1)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.save_and_hide_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    # --- Scope ---------------------------------------------------------------------------

    def use_current_search(self):
        panel = self.main_window.get_current_search_panel()
        self._scope = None
        self._scope_criteria = scope_criteria(panel.search_criteria)
        self._scope_selection = self._selected_file_ids(panel)
        self._update_stale_note()
        self._load_scope()

    def _load_scope(self):
        self._scope = None
        self.scope_label.setText("Loading…")
        self.counts_label.clear()
        self.rejected_label.clear()
        self.details_label.clear()
        self.status_label.clear()
        self._set_actions_enabled()
        self._loader.load(self._scope_criteria, self._scope_selection)

    def _on_scope_loaded(self, scope: DiagnosisScope, criteria: SearchCriteria):
        if criteria is not self._scope_criteria:
            return  # superseded by a later Use current search
        self._scope = scope
        lights = scope.frame_count
        sessions = len({group.key.session_night for group in scope.groups})
        peers_of = f" (peers of {_plural(scope.selected_lights, 'selected light')})" if scope.selected_lights else ""
        self.scope_label.setText(f"Scope: {_plural(lights, 'light')} in {_plural(sessions, 'session')}{peers_of}"
                                 if lights else "Scope: no lights in this "
                                                f"{'selection' if self._scope_selection else 'search'}")
        self._set_actions_enabled()
        self._update_preview()

    def _on_load_failed(self, message: str, criteria: SearchCriteria):
        if criteria is not self._scope_criteria:
            return  # superseded by a later Use current search
        self.scope_label.setText(f"Could not load the scope: {message}")

    def _on_tab_changed(self):
        if self._watched_panel is not None:
            try:
                self._watched_panel.search_criteria_changed.disconnect(self._update_stale_note)
                self._watched_panel.dataView.selectionModel().selectionChanged.disconnect(self._update_stale_note)
            except RuntimeError:
                pass  # the tab has been closed
        self._watched_panel = self.main_window.get_current_search_panel()
        if self._watched_panel is not None:
            self._watched_panel.search_criteria_changed.connect(self._update_stale_note)
            self._watched_panel.dataView.selectionModel().selectionChanged.connect(self._update_stale_note)
        self._update_stale_note()

    @staticmethod
    def _selected_file_ids(panel) -> set[int]:
        return {file.rowid for file in panel.get_selected_files()}

    def _update_stale_note(self):
        panel = self._watched_panel
        stale = False
        if panel is not None and self._scope_criteria is not None:
            selection = self._selected_file_ids(panel)
            # An emptied selection alone is no new scope: refreshing the grid after a save clears it.
            stale = (scope_criteria(panel.search_criteria) != self._scope_criteria
                     or (bool(selection) and selection != self._scope_selection))
            self.use_current_button.setText("Use current selection" if selection else "Use current search")
        self.stale_label.setVisible(stale)
        self.use_current_button.setVisible(stale)

    # --- Settings and preview ------------------------------------------------------------

    def _set_settings(self, settings: DiagnosisSettings):
        for metric, row in self.metric_rows.items():
            row.set_rule(settings.rules[metric])
        self.min_group_spin.setValue(settings.min_group_size)

    def current_settings(self) -> DiagnosisSettings:
        return DiagnosisSettings(rules={metric: row.rule() for metric, row in self.metric_rows.items()},
                                 min_group_size=self.min_group_spin.value())

    def _schedule_preview(self):
        self._preview_timer.start()

    def _update_preview(self):
        if self._scope is None:
            return
        self._show_result(diagnose(self._scope, self.current_settings()))

    def _show_result(self, result: DiagnosisResult):
        total = self._scope.frame_count
        analysed = total - result.not_analysed - result.read_failed
        self.counts_label.setText(f"Analysed: {analysed}    Not analysed: {result.not_analysed}    "
                                  f"Could not be read: {result.read_failed}")
        self.rejected_label.setText(f"Rejected {result.rejected} of {_plural(total, 'light')}")
        self.details_label.setText(f"{DIAGNOSIS_MISSING_METRICS}: {result.by_label[DIAGNOSIS_MISSING_METRICS]}    "
                                   f"Too few peers: {result.too_few_peers}")
        for metric, row in self.metric_rows.items():
            row.count_label.setText(f"{result.by_label[metric.label]} rejected")
        self._set_actions_enabled()

    def _set_actions_enabled(self):
        ready = self._scope is not None and self._scope.frame_count > 0 and not self._analysing
        self.save_button.setEnabled(ready and not self._saving)
        self.save_and_hide_button.setEnabled(ready and not self._saving)
        missing = frames_needing_analysis(self._scope, self.current_settings()) if self._scope else []
        self.analyse_button.setText(f"Analyse missing ({len(missing)})")
        self.analyse_button.setEnabled(ready and not self._saving and bool(missing))

    # --- Analysing missing frames --------------------------------------------------------

    def _analyse_missing(self):
        file_ids = frames_needing_analysis(self._scope, self.current_settings())
        if not file_ids:
            return
        if not self.context.library_write_lock.acquire(blocking=False):
            QMessageBox.warning(self, "Diagnose Quality", f"Nothing was analysed: {_LIBRARY_BUSY}")
            return
        try:
            task = MissingFramesAnalysisTask(self.context, file_ids)
            task.ended.connect(self._on_analysis_ended)
            # On the window, as this dialog may be gone by the time the run ends.
            task.ended.connect(self.main_window.refresh_current_search)
            # Not a child of this dialog: analysis carries on if this one is closed.
            progress = ImageAnalysisDialog(self.context, None, SearchCriteria(), parent=self.main_window,
                                           task=task, label=f"Analysing {len(file_ids)} file(s)...")
            progress.setAttribute(Qt.WA_DeleteOnClose)
        except Exception as exc:
            logging.error("Could not start the analysis", exc_info=True)
            self.context.library_write_lock.release()  # no task to release it
            QMessageBox.warning(self, "Diagnose Quality", f"Could not start the analysis: {exc}")
            return
        self._analysing = True
        self._set_actions_enabled()
        self.analysis_dialog = progress
        progress.show()  # starts the task, which releases the lock when it ends

    def _on_analysis_ended(self):
        self._analysing = False
        self.analysis_dialog = None
        self._load_scope()

    # --- Saving --------------------------------------------------------------------------

    def _save(self, hide_bad_frames: bool):
        if self._scope is None:
            return
        self._hide_after_save = hide_bad_frames
        self._settings_being_saved = self.current_settings()
        self._saving = True
        self._set_actions_enabled()
        self.status_label.setText("Saving…")
        self._loader.save(diagnose(self._scope, self._settings_being_saved).verdicts)

    def _on_saved(self, stored: bool):
        self._saving = False
        self._set_actions_enabled()
        if not stored:
            self.status_label.clear()
            QMessageBox.warning(self, "Diagnose Quality", f"Nothing was saved: {_LIBRARY_BUSY}")
            return
        self.context.settings.set_quality_diagnosis_settings(self._settings_being_saved.to_json())
        self.status_label.setText("Saved.")
        panel = self.main_window.get_current_search_panel()
        if self._hide_after_save:
            panel.set_diagnosis_filter(list(DIAGNOSIS_BAD_VALUES))
            self.main_window.enable_actions_for_current_tab()
        else:
            panel.refresh_data_grid()

    def _on_save_failed(self, message: str):
        self._saving = False
        self._set_actions_enabled()
        self.status_label.clear()
        QMessageBox.warning(self, "Diagnose Quality", f"Could not save the diagnosis: {message}")

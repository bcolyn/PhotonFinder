"""The Diagnose Quality dialog on the real main window: tuning, saving and its scope."""
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from photonfinder import quality_diagnosis
from photonfinder.core import LibraryBusyError
from photonfinder.image_analysis import ImageAnalysisResult
from photonfinder.models import (
    CORE_MODELS, DIAGNOSIS_NOT_ANALYSED, File, Image, ImageStats, LibraryRoot, SearchCriteria,
)
from photonfinder.quality_diagnosis import DiagnosisSettings, Metric, MetricRule, RuleMode
from photonfinder.ui.common import _format_dec
from photonfinder.ui.ImageAnalysisDialog import ImageAnalysisDialog
from photonfinder.ui.RangeFilterDialogs import DiagnosisFilterDialog
from photonfinder.ui.SearchPanel import AdvancedFilter

GOOD_FWHMS = [2.6, 2.7, 2.8, 2.9, 3.0, 3.1, 3.2, 3.3]
BAD_NAME = "M81_bad.fits"


def add_frames(sandbox, target: str, fwhms: list[float], names: list[str] | None = None):
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        root = LibraryRoot.get_or_create(name="Main", path="/data/")[0]
        for i, fwhm in enumerate(fwhms):
            name = names[i] if names else f"{target}_{i}.fits"
            file = File.create(root=root, path=f"{target}/", name=name, size=1, mtime_millis=1)
            Image.create(file=file, image_type="LIGHT", object_name=target, filter="Ha", exposure=300.0, gain=100,
                         binning=1, camera="C", telescope="T", date_obs=datetime(2024, 1, 1, 22) + timedelta(minutes=i))
            ImageStats.create(file=file, fwhm_median=fwhm, elongation_median=1.2, background_median=100.0,
                              background_rms=5.0, star_count=500)


def diagnosis_of(sandbox, name: str):
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        return ImageStats.select().join(File).where(File.name == name).get().diagnosis


def search(sandbox, app, **criteria):
    app.get_current_search_panel().apply_search_criteria(SearchCriteria(**criteria))
    sandbox.wait_idle()


def open_dialog(sandbox, app):
    app.actionDiagnose_Quality.trigger()
    sandbox.wait_idle()
    return app._diagnose_dialog


@pytest.fixture
def m81(sandbox, app):
    add_frames(sandbox, "M81", [*GOOD_FWHMS, 9.0], [*(f"M81_{i}.fits" for i in range(8)), BAD_NAME])
    search(sandbox, app, object_name="M81")


def wait_for_grid(sandbox, check):
    """A refresh can finish before wait_idle looks, with its results still queued."""
    def ready():
        sandbox.wait_idle()
        return check(sandbox.grid())
    sandbox.qtbot.waitUntil(ready)


def wait_for_preview(sandbox, dialog, text):
    sandbox.qtbot.waitUntil(lambda: dialog.rejected_label.text() == text)


def test_opens_on_the_current_search_with_a_preview(sandbox, app, m81):
    add_frames(sandbox, "M33", GOOD_FWHMS)
    dialog = open_dialog(sandbox, app)
    assert dialog.scope_label.text() == "Scope: 9 lights in 1 session"
    assert dialog.counts_label.text().startswith("Analysed: 9    Not analysed: 0")
    assert dialog.rejected_label.text() == "Rejected 1 of 9 lights"
    assert dialog.metric_rows[Metric.FWHM].count_label.text() == "1 rejected"
    assert diagnosis_of(sandbox, BAD_NAME) is None
    assert not dialog.isModal()


def test_tuning_previews_without_writing(sandbox, app, m81):
    dialog = open_dialog(sandbox, app)
    row = dialog.metric_rows[Metric.FWHM]

    row.mode_combo.setCurrentIndex(row.mode_combo.findData(RuleMode.OFF))
    wait_for_preview(sandbox, dialog, "Rejected 0 of 9 lights")
    assert not row.limit_spin.isEnabled()

    row.mode_combo.setCurrentIndex(row.mode_combo.findData(RuleMode.PERCENT))
    assert row.limit_spin.value() == 20.0
    wait_for_preview(sandbox, dialog, "Rejected 1 of 9 lights")

    dialog.min_group_spin.setValue(50)
    wait_for_preview(sandbox, dialog, "Rejected 0 of 9 lights")
    assert "Too few peers: 9" in dialog.details_label.text()

    dialog.close()
    sandbox.wait_idle()
    assert diagnosis_of(sandbox, BAD_NAME) is None
    assert app._diagnose_dialog is None


def test_save_stores_results_and_refreshes_the_grid(sandbox, app, m81):
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()

    assert diagnosis_of(sandbox, BAD_NAME) == "Bad FWHM"
    assert diagnosis_of(sandbox, "M81_0.fits") == "OK"
    assert dialog.status_label.text() == "Saved."
    wait_for_grid(sandbox, lambda g: {r["File name"]: r["Diagnosis"] for r in g.rows()}[BAD_NAME] == "Bad FWHM")
    assert AdvancedFilter.QUALITY_DIAGNOSIS not in sandbox.grid().panel.advanced_options


def test_save_and_hide_adds_the_filter_and_leaves_the_scope_alone(sandbox, app, m81):
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.save_and_hide_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()

    wait_for_grid(sandbox, lambda g: g.total == 8)
    grid = sandbox.grid()
    assert grid.panel.advanced_options[AdvancedFilter.QUALITY_DIAGNOSIS].text() == "Diagnosis: hide bad"
    assert app.actionQualityDiagnosis.isChecked()
    assert BAD_NAME not in grid.column("File name")
    assert not dialog.use_current_button.isVisible()  # hiding frames isn't a new scope


def test_a_search_hiding_every_frame_still_scopes_their_groups(sandbox, app, m81):
    # Nothing is diagnosed yet, so hiding Not analysed leaves an empty grid.
    search(sandbox, app, object_name="M81", diagnosis_hidden=[DIAGNOSIS_NOT_ANALYSED])
    assert sandbox.grid().total == 0

    assert open_dialog(sandbox, app).scope_label.text() == "Scope: 9 lights in 1 session"


def test_closing_while_still_loading_is_harmless(sandbox, app, m81):
    app.actionDiagnose_Quality.trigger()
    app._diagnose_dialog.close()
    sandbox.wait_idle()
    assert app._diagnose_dialog is None
    assert open_dialog(sandbox, app).scope_label.text() == "Scope: 9 lights in 1 session"


def test_starts_from_the_last_saved_settings(sandbox, app, m81):
    dialog = open_dialog(sandbox, app)
    dialog.metric_rows[Metric.ELONGATION].set_rule(MetricRule(RuleMode.OFF))
    dialog.metric_rows[Metric.FWHM].set_rule(MetricRule(RuleMode.PERCENT, 35.0))
    dialog.min_group_spin.setValue(7)
    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    dialog.close()
    sandbox.wait_idle()

    settings = open_dialog(sandbox, app).current_settings()
    assert settings.rules[Metric.FWHM] == MetricRule(RuleMode.PERCENT, 35.0)
    assert settings.rules[Metric.ELONGATION].mode == RuleMode.OFF
    assert settings.min_group_size == 7


def test_unsaved_settings_are_not_remembered(sandbox, app, m81):
    dialog = open_dialog(sandbox, app)
    dialog.min_group_spin.setValue(7)
    dialog.close()
    sandbox.wait_idle()
    assert open_dialog(sandbox, app).current_settings() == DiagnosisSettings()


def test_save_is_refused_while_a_writer_holds_the_library_and_tuning_still_works(sandbox, app, boundary, m81):
    dialog = open_dialog(sandbox, app)
    assert sandbox.context.library_write_lock.acquire(blocking=False)
    try:
        boundary.answer("warning", None)
        sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        sandbox.wait_idle()

        assert [kind for kind, _ in boundary.calls] == ["warning"]
        assert diagnosis_of(sandbox, BAD_NAME) is None
        assert not sandbox.context.settings.get_quality_diagnosis_settings()  # nothing was saved
        assert dialog.save_button.isEnabled()
        dialog.min_group_spin.setValue(50)
        wait_for_preview(sandbox, dialog, "Rejected 0 of 9 lights")
    finally:
        sandbox.context.library_write_lock.release()


def test_only_one_dialog_at_a_time(sandbox, app, m81):
    assert open_dialog(sandbox, app) is open_dialog(sandbox, app)


def test_scope_stays_fixed_until_asked(sandbox, app, m81):
    add_frames(sandbox, "M33", [*GOOD_FWHMS, 9.0], [*(f"M33_{i}.fits" for i in range(8)), "M33_bad.fits"])
    dialog = open_dialog(sandbox, app)
    assert not dialog.use_current_button.isVisible()

    search(sandbox, app, object_name="M33")
    assert dialog.use_current_button.isVisible()
    assert dialog.scope_label.text() == "Scope: 9 lights in 1 session"

    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)  # still M81
    sandbox.wait_idle()
    assert diagnosis_of(sandbox, BAD_NAME) == "Bad FWHM"
    assert diagnosis_of(sandbox, "M33_bad.fits") is None

    sandbox.qtbot.mouseClick(dialog.use_current_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    assert not dialog.use_current_button.isVisible()
    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    assert diagnosis_of(sandbox, "M33_bad.fits") == "Bad FWHM"


def test_switching_tab_counts_as_a_changed_search(sandbox, app, m81):
    dialog = open_dialog(sandbox, app)
    app.action_New_Tab.trigger()
    sandbox.wait_idle()
    assert dialog.use_current_button.isVisible()
    assert dialog.scope_label.text() == "Scope: 9 lights in 1 session"


def test_sorting_the_grid_is_not_a_changed_search(sandbox, app, m81):
    dialog = open_dialog(sandbox, app)
    panel = app.get_current_search_panel()
    panel.search_criteria.sorting_index = 3
    panel.search_criteria_changed.emit()
    assert not dialog.use_current_button.isVisible()


def test_filter_dialog_leads_to_the_diagnose_dialog(sandbox, app, modals, m81):
    with modals.handle(DiagnosisFilterDialog, lambda d: d.diagnose_button.click()):
        app.actionQualityDiagnosis.trigger()
    sandbox.wait_idle()

    assert app._diagnose_dialog is not None and app._diagnose_dialog.isVisible()
    assert AdvancedFilter.QUALITY_DIAGNOSIS not in app.get_current_search_panel().advanced_options
    assert not app.actionQualityDiagnosis.isChecked()


def test_closing_the_dialog_allows_switching_database(sandbox, app, m81, tmp_path):
    dialog = open_dialog(sandbox, app)
    with pytest.raises(LibraryBusyError):
        app._switch_database(str(tmp_path / "other.db"))
    dialog.close()
    sandbox.wait_idle()
    assert app._diagnose_dialog is None
    app._switch_database(str(tmp_path / "other.db"))
    sandbox.wait_idle()
    assert sandbox.context.database_path == str(tmp_path / "other.db")


# --- Analyse missing -------------------------------------------------------------------


def add_unanalysed(sandbox, target: str, count: int):
    """Frames with no stats, on paths that don't exist unless analyze_file is patched."""
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        root = LibraryRoot.get_or_create(name="Main", path="/data/")[0]
        for i in range(count):
            file = File.create(root=root, path=f"{target}/", name=f"{target}_new{i}.fits", size=1, mtime_millis=1)
            Image.create(file=file, image_type="LIGHT", object_name=target, filter="Ha", exposure=300.0, gain=100,
                         binning=1, camera="C", telescope="T", date_obs=datetime(2024, 1, 1, 23) + timedelta(minutes=i))


def stats_of(sandbox, name: str):
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        return ImageStats.select().join(File).where(File.name == name).first()


GOOD_RESULT = ImageAnalysisResult(background_median=100.0, background_rms=5.0, star_count=500, fwhm_median=3.0,
                                  elongation_median=1.2)


@pytest.fixture
def analyze_ok(monkeypatch):
    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", lambda *a, **k: GOOD_RESULT)


def lock_is_free(sandbox) -> bool:
    lock = sandbox.context.library_write_lock
    if lock.acquire(blocking=False):
        lock.release()
        return True
    return False


def test_analyse_button_counts_the_frames_worth_analysing(sandbox, app, m81):
    add_unanalysed(sandbox, "M81", 3)
    add_unanalysed(sandbox, "NGC1", 2)  # a group too small to be judged
    search(sandbox, app)

    dialog = open_dialog(sandbox, app)
    assert dialog.analyse_button.text() == "Analyse missing (3)"
    assert dialog.analyse_button.isEnabled()

    dialog.min_group_spin.setValue(2)
    sandbox.qtbot.waitUntil(lambda: dialog.analyse_button.text() == "Analyse missing (5)")

    dialog.min_group_spin.setValue(50)
    sandbox.qtbot.waitUntil(lambda: dialog.analyse_button.text() == "Analyse missing (0)")
    assert not dialog.analyse_button.isEnabled()


def test_analyse_covers_only_the_scope_and_reloads_the_preview(sandbox, app, m81, analyze_ok):
    add_unanalysed(sandbox, "M81", 3)
    add_unanalysed(sandbox, "M33", 6)
    search(sandbox, app, object_name="M81")
    dialog = open_dialog(sandbox, app)
    assert dialog.counts_label.text().startswith("Analysed: 9    Not analysed: 3")

    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    assert dialog.analyse_button.isEnabled() is False
    assert dialog.save_button.isEnabled() is False
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: dialog.counts_label.text().startswith("Analysed: 12    Not analysed: 0"))

    assert stats_of(sandbox, "M81_new0.fits").star_count == 500
    assert stats_of(sandbox, "M33_new0.fits") is None
    assert dialog.analyse_button.text() == "Analyse missing (0)"
    assert dialog.save_button.isEnabled()
    assert lock_is_free(sandbox)


def test_analysis_holds_the_write_lock_while_running(sandbox, app, m81, monkeypatch):
    add_unanalysed(sandbox, "M81", 1)
    search(sandbox, app, object_name="M81")
    started, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return GOOD_RESULT

    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", held)
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    try:
        assert started.wait(10)
        assert not lock_is_free(sandbox)
    finally:
        release.set()
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: lock_is_free(sandbox))


def test_analyse_is_refused_while_a_writer_holds_the_library(sandbox, app, boundary, m81, analyze_ok):
    add_unanalysed(sandbox, "M81", 2)
    search(sandbox, app, object_name="M81")
    dialog = open_dialog(sandbox, app)
    assert sandbox.context.library_write_lock.acquire(blocking=False)
    try:
        boundary.answer("warning", None)
        sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
        sandbox.wait_idle()
    finally:
        sandbox.context.library_write_lock.release()

    assert [kind for kind, _ in boundary.calls] == ["warning"]
    assert dialog.analysis_dialog is None
    assert stats_of(sandbox, "M81_new0.fits") is None
    assert dialog.analyse_button.isEnabled()


def test_cancelling_keeps_finished_results_and_reloads(sandbox, app, m81, monkeypatch):
    add_unanalysed(sandbox, "M81", 3)
    search(sandbox, app, object_name="M81")
    second_started, release = threading.Event(), threading.Event()
    calls = []

    def analyze(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            second_started.set()
            assert release.wait(10)
        return GOOD_RESULT

    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", analyze)
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    assert second_started.wait(10)

    dialog.analysis_dialog._btn.click()  # Cancel: the file in progress still has to finish
    assert not lock_is_free(sandbox)
    release.set()
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: dialog.analyse_button.text() == "Analyse missing (1)")

    assert len(calls) == 2
    assert dialog.counts_label.text().startswith("Analysed: 11    Not analysed: 1")
    assert lock_is_free(sandbox)


def test_closing_the_dialog_mid_analysis_still_releases_the_lock(sandbox, app, m81, monkeypatch):
    add_unanalysed(sandbox, "M81", 1)
    search(sandbox, app, object_name="M81")
    started, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return GOOD_RESULT

    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", held)
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    assert started.wait(10)

    dialog.close()
    release.set()
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: lock_is_free(sandbox))
    assert stats_of(sandbox, "M81_new0.fits").star_count == 500
    wait_for_grid(sandbox, lambda g: {r["File name"]: r["Stars"] for r in g.rows()}["M81_new0.fits"] == "500")


def test_showing_the_progress_dialog_again_does_not_restart_the_analysis(sandbox, app, m81, monkeypatch):
    add_unanalysed(sandbox, "M81", 1)
    search(sandbox, app, object_name="M81")
    started, release = threading.Event(), threading.Event()
    calls = []

    def held(*args, **kwargs):
        calls.append(args)
        started.set()
        assert release.wait(10)
        return GOOD_RESULT

    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", held)
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    assert started.wait(10)

    dialog.analysis_dialog.hide()
    dialog.analysis_dialog.show()
    release.set()
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: lock_is_free(sandbox))
    assert len(calls) == 1


def test_failing_to_start_the_analysis_releases_the_lock(sandbox, app, boundary, m81, monkeypatch):
    add_unanalysed(sandbox, "M81", 1)
    search(sandbox, app, object_name="M81")
    dialog = open_dialog(sandbox, app)

    def fail(*args, **kwargs):
        raise RuntimeError("no progress window")

    monkeypatch.setattr("photonfinder.ui.DiagnoseQualityDialog.ImageAnalysisDialog", fail)
    boundary.answer("warning", None)
    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()

    assert [kind for kind, _ in boundary.calls] == ["warning"]
    assert lock_is_free(sandbox)
    assert dialog.analyse_button.isEnabled()
    assert dialog.save_button.isEnabled()


def test_a_late_failure_of_a_replaced_load_leaves_the_newer_scope_alone(sandbox, app, m81, monkeypatch):
    add_frames(sandbox, "M33", GOOD_FWHMS)
    entered, release = threading.Event(), threading.Event()
    calls = []
    real_load = quality_diagnosis.load_scope

    def load(context, criteria, selection):
        calls.append(criteria)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
            raise OSError("the first load failed")
        return real_load(context, criteria, selection)

    monkeypatch.setattr("photonfinder.ui.DiagnoseQualityDialog.load_scope", load)
    app.actionDiagnose_Quality.trigger()
    dialog = app._diagnose_dialog
    assert entered.wait(10)

    panel = app.get_current_search_panel()
    panel.search_criteria.object_name = "M33"
    dialog.use_current_search()
    sandbox.qtbot.waitUntil(lambda: dialog.scope_label.text() == "Scope: 8 lights in 1 session")

    release.set()
    sandbox.wait_idle()
    assert dialog.scope_label.text() == "Scope: 8 lights in 1 session"


def test_a_failing_load_says_so(sandbox, app, m81, monkeypatch):
    def fail(context, criteria, selection):
        raise OSError("disk gone")

    monkeypatch.setattr("photonfinder.ui.DiagnoseQualityDialog.load_scope", fail)
    dialog = open_dialog(sandbox, app)
    assert dialog.scope_label.text() == "Could not load the scope: disk gone"
    assert not dialog.save_button.isEnabled()


def test_unreadable_frame_stays_not_analysed_and_is_retried(sandbox, app, m81):
    add_unanalysed(sandbox, "M81", 1)  # the real analyze_file: this path doesn't exist
    search(sandbox, app, object_name="M81")
    dialog = open_dialog(sandbox, app)

    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: "Could not be read: 1" in dialog.counts_label.text())

    assert "Not analysed: 0" in dialog.counts_label.text()
    assert dialog.analyse_button.text() == "Analyse missing (1)"  # offered again, as a read failure
    stats = stats_of(sandbox, "M81_new0.fits")
    assert stats is not None and stats.star_count is None

    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    assert stats_of(sandbox, "M81_new0.fits").diagnosis is None
    assert diagnosis_of(sandbox, BAD_NAME) == "Bad FWHM"


def test_analysis_results_reach_the_grid(sandbox, app, m81, analyze_ok):
    add_unanalysed(sandbox, "M81", 1)
    search(sandbox, app, object_name="M81")
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    wait_for_grid(sandbox, lambda g: {r["File name"]: r["Stars"] for r in g.rows()}["M81_new0.fits"] == "500")


# --- Other ways in ---------------------------------------------------------------------


def run_analyse_images(sandbox, app) -> ImageAnalysisDialog:
    panel = app.get_current_search_panel()
    panel.analyse_images()
    dialog = panel.findChild(ImageAnalysisDialog)
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: dialog._btn.text() == "Close")
    return dialog


def test_analyse_images_offers_the_diagnosis_when_done(sandbox, app, m81, analyze_ok):
    add_unanalysed(sandbox, "M81", 1)
    search(sandbox, app, object_name="M81")
    progress = run_analyse_images(sandbox, app)
    assert progress.diagnose_button.isVisible()
    assert app._diagnose_dialog is None  # only an offer

    sandbox.qtbot.mouseClick(progress.diagnose_button, Qt.MouseButton.LeftButton)
    assert not progress.isVisible()
    sandbox.wait_idle()
    assert app._diagnose_dialog.isVisible()
    assert app._diagnose_dialog.scope_label.text() == "Scope: 10 lights in 1 session"


def test_ignoring_the_offer_changes_nothing(sandbox, app, m81, analyze_ok):
    search(sandbox, app, object_name="M81")
    progress = run_analyse_images(sandbox, app)
    progress._btn.click()
    sandbox.wait_idle()
    assert app._diagnose_dialog is None


def test_no_offer_without_a_light(sandbox, app, analyze_ok):
    add_frames(sandbox, "M81", [3.0])
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        Image.update(image_type="DARK").execute()
    search(sandbox, app)
    assert not run_analyse_images(sandbox, app).diagnose_button.isVisible()


def test_analyse_missing_does_not_offer_what_is_already_open(sandbox, app, m81, analyze_ok):
    add_unanalysed(sandbox, "M81", 1)
    search(sandbox, app, object_name="M81")
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    progress = dialog.analysis_dialog
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: progress._btn.text() == "Close")
    assert not progress.diagnose_button.isVisible()


def test_cancelled_analysis_does_not_offer(sandbox, app, m81, monkeypatch):
    search(sandbox, app, object_name="M81")
    started, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return GOOD_RESULT

    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", held)
    panel = app.get_current_search_panel()
    panel.analyse_images()
    progress = panel.findChild(ImageAnalysisDialog)
    assert started.wait(10)
    progress._btn.click()
    release.set()
    sandbox.wait_idle()
    assert app._diagnose_dialog is None


def context_menu_item(app, text: str):
    """Right-click the first grid row and choose an item from the real menu.

    The menu blocks in its own event loop, so a timer works it from inside that loop, and
    always closes it: a menu left open would hang the test.
    """
    panel = app.get_current_search_panel()
    view = panel.dataView
    position = view.visualRect(view.model().index(0, 0)).center()
    view.selectRow(0)
    found = []

    def choose():
        menu = QApplication.activePopupWidget()
        try:
            action = next((a for a in menu.actions() if a.text() == text), None)
            found.append(action)
            if action is not None:
                menu.setActiveAction(action)
                QTest.keyClick(menu, Qt.Key.Key_Return)
        finally:
            if menu.isVisible():
                menu.close()

    QTimer.singleShot(0, choose)
    panel.show_context_menu(position)
    assert found and found[0] is not None, f"no {text!r} item in the menu"


def test_right_click_menu_opens_the_dialog_and_brings_it_forward(sandbox, app, m81):
    context_menu_item(app, "Diagnose Quality…")
    sandbox.wait_idle()
    dialog = app._diagnose_dialog
    assert dialog is not None and dialog.isVisible()

    context_menu_item(app, "Diagnose Quality…")
    sandbox.wait_idle()
    assert app._diagnose_dialog is dialog


# --- Selection -------------------------------------------------------------------------


@pytest.fixture
def two_targets(sandbox, app, m81):
    """All of M81 and M33 in the grid, each with one bad frame, and nothing selected."""
    add_frames(sandbox, "M33", [*GOOD_FWHMS, 9.0], [*(f"M33_{i}.fits" for i in range(8)), "M33_bad.fits"])
    search(sandbox, app)
    return sandbox.grid()


def test_no_selection_covers_the_whole_search(sandbox, app, two_targets):
    assert open_dialog(sandbox, app).scope_label.text() == "Scope: 18 lights in 1 session"


@pytest.mark.parametrize("entry", ["tools", "right_click"])
def test_a_selection_narrows_the_scope_to_its_peer_groups(sandbox, app, two_targets, entry):
    two_targets.select("File name", "M33_0.fits")
    if entry == "tools":
        app.actionDiagnose_Quality.trigger()
    else:
        panel = app.get_current_search_panel()
        row = two_targets.column("File name").index("M33_0.fits")
        position = panel.dataView.visualRect(panel.dataView.model().index(row, 0)).center()

        def choose():
            menu = QApplication.activePopupWidget()
            menu.setActiveAction(next(a for a in menu.actions() if a.text() == "Diagnose Quality…"))
            QTest.keyClick(menu, Qt.Key.Key_Return)

        QTimer.singleShot(0, choose)
        panel.show_context_menu(position)
    sandbox.wait_idle()
    dialog = app._diagnose_dialog
    assert dialog.scope_label.text() == "Scope: 9 lights in 1 session (peers of 1 selected light)"
    assert dialog.rejected_label.text() == "Rejected 1 of 9 lights"

    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    assert diagnosis_of(sandbox, "M33_bad.fits") == "Bad FWHM"
    assert diagnosis_of(sandbox, "M33_0.fits") == "OK"  # judged with its whole group
    assert diagnosis_of(sandbox, BAD_NAME) is None


def test_selecting_two_groups_covers_both(sandbox, app, two_targets):
    view = two_targets.panel.dataView
    names = two_targets.column("File name")
    view.selectionModel().clearSelection()
    model = view.model()
    for name in ("M33_0.fits", "M81_0.fits"):
        view.selectionModel().select(model.index(names.index(name), 0),
                                     QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    assert open_dialog(sandbox, app).scope_label.text() == "Scope: 18 lights in 1 session (peers of 2 selected lights)"


def test_selection_without_lights_has_an_empty_scope(sandbox, app, two_targets):
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        Image.update(image_type="DARK").where(Image.file.in_(File.select().where(File.name == BAD_NAME))).execute()
    search(sandbox, app)
    sandbox.grid().select("File name", BAD_NAME)
    dialog = open_dialog(sandbox, app)
    assert dialog.scope_label.text() == "Scope: no lights in this selection"
    assert not dialog.save_button.isEnabled()


def test_changing_the_selection_offers_it_without_following_it(sandbox, app, two_targets):
    two_targets.select("File name", "M33_0.fits")
    dialog = open_dialog(sandbox, app)
    assert not dialog.use_current_button.isVisible()

    two_targets.select("File name", "M81_0.fits")
    assert dialog.use_current_button.isVisible()
    assert dialog.use_current_button.text() == "Use current selection"
    assert dialog.scope_label.text() == "Scope: 9 lights in 1 session (peers of 1 selected light)"

    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)  # still M33
    sandbox.wait_idle()
    assert diagnosis_of(sandbox, "M33_bad.fits") == "Bad FWHM"
    assert diagnosis_of(sandbox, BAD_NAME) is None


def test_using_the_current_selection_moves_the_scope(sandbox, app, two_targets):
    two_targets.select("File name", "M33_0.fits")
    dialog = open_dialog(sandbox, app)
    two_targets.select("File name", "M81_0.fits")
    sandbox.qtbot.mouseClick(dialog.use_current_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    assert not dialog.use_current_button.isVisible()

    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    assert diagnosis_of(sandbox, BAD_NAME) == "Bad FWHM"
    assert diagnosis_of(sandbox, "M33_bad.fits") is None


def test_clearing_the_selection_is_no_new_scope(sandbox, app, two_targets):
    two_targets.select("File name", "M33_0.fits")
    dialog = open_dialog(sandbox, app)
    two_targets.panel.dataView.clearSelection()
    assert not dialog.use_current_button.isVisible()


def test_saving_with_a_selection_does_not_look_like_a_changed_scope(sandbox, app, two_targets):
    two_targets.select("File name", "M33_0.fits")
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    wait_for_grid(sandbox, lambda g: {r["File name"]: r["Diagnosis"] for r in g.rows()}["M33_bad.fits"] == "Bad FWHM")
    assert not dialog.use_current_button.isVisible()


def test_selection_scope_survives_saving_again(sandbox, app, two_targets):
    two_targets.select("File name", "M33_0.fits")
    dialog = open_dialog(sandbox, app)
    sandbox.qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    sandbox.qtbot.mouseClick(dialog.save_and_hide_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    assert dialog.scope_label.text() == "Scope: 9 lights in 1 session (peers of 1 selected light)"
    assert diagnosis_of(sandbox, BAD_NAME) is None


def test_analyse_missing_follows_the_selection(sandbox, app, two_targets, analyze_ok):
    add_unanalysed(sandbox, "M81", 2)
    add_unanalysed(sandbox, "M33", 3)
    search(sandbox, app)
    sandbox.grid().select("File name", "M33_0.fits")
    dialog = open_dialog(sandbox, app)
    assert dialog.analyse_button.text() == "Analyse missing (3)"

    sandbox.qtbot.mouseClick(dialog.analyse_button, Qt.MouseButton.LeftButton)
    sandbox.wait_idle()
    sandbox.qtbot.waitUntil(lambda: dialog.analyse_button.text() == "Analyse missing (0)")
    assert stats_of(sandbox, "M33_new0.fits").star_count == 500
    assert stats_of(sandbox, "M81_new0.fits") is None


# --- Updating a sorted grid ------------------------------------------------------------


STATS_COLUMNS = ["Bg Median", "Bg RMS", "Stars", "FWHM", "Elongation"]
# A different order for each column, so a value on the wrong row shows up.
SHUFFLE = [3, 0, 5, 1, 7, 2, 6, 4]


def analysed_values(number: int) -> dict[str, float]:
    return {"Bg Median": 100.0 + SHUFFLE[number], "Bg RMS": 5.0 + SHUFFLE[(number + 1) % 8],
            "Stars": 500 + SHUFFLE[(number + 2) % 8], "FWHM": 2.0 + SHUFFLE[(number + 3) % 8] / 10,
            "Elongation": 1.1 + SHUFFLE[(number + 4) % 8] / 100}


def analysed_text(number: int) -> dict[str, str]:
    values = analysed_values(number)
    return {"Bg Median": f"{values['Bg Median']:.1f}", "Bg RMS": f"{values['Bg RMS']:.2f}",
            "Stars": str(values["Stars"]), "FWHM": f"{values['FWHM']:.2f}", "Elongation": f"{values['Elongation']:.3f}"}


def analyse_by_name(name, **_) -> ImageAnalysisResult:
    values = analysed_values(int(Path(name).stem.split("_")[1]))
    return ImageAnalysisResult(background_median=values["Bg Median"], background_rms=values["Bg RMS"],
                               star_count=values["Stars"], fwhm_median=values["FWHM"],
                               elongation_median=values["Elongation"])


def sort_grid(sandbox, panel, column: str, descending: bool):
    """Click the header as the user does: the first click on a column is always ascending."""
    col = sandbox.grid().headers().index(column)
    panel.dataView.horizontalHeader().setSortIndicator(col, Qt.SortOrder.AscendingOrder)
    if descending:
        panel.dataView.horizontalHeader().setSortIndicator(col, Qt.SortOrder.DescendingOrder)
    sandbox.wait_idle()
    assert panel.proxy_model.sortColumn() == col
    assert panel.proxy_model.sortOrder() == (Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)


@pytest.mark.parametrize("descending", [False, True], ids=["ascending", "descending"])
@pytest.mark.parametrize("column", [*STATS_COLUMNS, "Diagnosis", "File name"])
def test_analysis_results_land_on_their_own_rows_in_a_sorted_grid(sandbox, app, monkeypatch, column, descending):
    add_frames(sandbox, "M81", [3.0] * 8)
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        for i, file in enumerate(File.select().order_by(File.rowid)):
            ImageStats.update(diagnosis="Bad FWHM" if i % 2 else "OK").where(ImageStats.file == file).execute()
    search(sandbox, app, object_name="M81")
    panel = app.get_current_search_panel()
    sort_grid(sandbox, panel, column, descending)
    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", analyse_by_name)

    run_analyse_images(sandbox, app)

    grid = sandbox.grid()
    names = grid.column("File name")
    assert sorted(names) == [f"M81_{i}.fits" for i in range(8)]
    for name_on_row, *cells in zip(names, *(grid.column(c) for c in STATS_COLUMNS)):
        assert dict(zip(STATS_COLUMNS, cells)) == analysed_text(int(name_on_row[4])), name_on_row
    assert grid.column("Diagnosis") == [""] * 8


def test_solve_results_land_on_their_own_rows_in_a_sorted_grid(sandbox, app):
    add_frames(sandbox, "M81", [3.0] * 8)
    search(sandbox, app, object_name="M81")
    panel = app.get_current_search_panel()
    sort_grid(sandbox, panel, "RA", True)  # setting the RA moves the row, ahead of its Dec and Solved cells
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        solved = list(File.select(File, Image).join(Image).order_by(File.rowid))
        for i, file in enumerate(solved):
            file.image.coord_ra = 10.0 * (SHUFFLE[i] + 1)
            file.image.coord_dec = float(i + 1)

    panel.on_files_solved(SimpleNamespace(solved_files=solved))

    grid = sandbox.grid()
    decs = {name: dec for name, dec in zip(grid.column("File name"), grid.column("DEC"))}
    assert decs == {f"M81_{i}.fits": _format_dec(float(i + 1)) for i in range(8)}
    assert grid.column("Solved") == ["True"] * 8


def test_clearing_a_plate_solution_updates_its_own_row_in_a_sorted_grid(sandbox, app):
    add_frames(sandbox, "M81", [3.0] * 3)
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        for i, file in enumerate(File.select().order_by(File.rowid)):
            Image.update(coord_ra=10.0 * (i + 1), coord_dec=float(i + 1)).where(Image.file == file).execute()
    search(sandbox, app, object_name="M81")
    panel = app.get_current_search_panel()
    sort_grid(sandbox, panel, "RA", True)  # blanking the RA moves the row, ahead of its Dec and Solved cells
    grid = sandbox.grid()
    names = grid.column("File name")
    row = names.index("M81_1.fits")

    panel.clear_plate_solution(panel.proxy_model.index(row, 0).data(Qt.ItemDataRole.UserRole), row)

    by_name = dict(zip(grid.column("File name"), zip(grid.column("RA"), grid.column("DEC"), grid.column("Solved"))))
    assert by_name["M81_1.fits"] == ("", "", "False")
    assert all(by_name[n][0] and by_name[n][1] for n in ("M81_0.fits", "M81_2.fits"))

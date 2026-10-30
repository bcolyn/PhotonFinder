"""The Diagnose Quality dialog on the real main window: tuning, saving and its scope."""
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import Qt

from photonfinder.models import (
    CORE_MODELS, DIAGNOSIS_NOT_ANALYSED, File, Image, ImageStats, LibraryRoot, SearchCriteria,
)
from photonfinder.quality_diagnosis import DiagnosisSettings, Metric, MetricRule, RuleMode
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


def test_switching_database_closes_the_dialog(sandbox, app, m81, tmp_path):
    open_dialog(sandbox, app)
    app._switch_database(str(tmp_path / "other.db"))
    sandbox.wait_idle()
    assert app._diagnose_dialog is None

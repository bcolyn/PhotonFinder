"""Telescopius compare, catalog report and header dialog: bad input and late loads."""
import threading

import pytest
from astropy.io.fits import Header
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QMessageBox

from photonfinder import reports
from photonfinder.models import SearchCriteria
from photonfinder.ui.CatalogReportWindow import CatalogReportLoader
from photonfinder.ui.HeaderDialog import HeaderDialog, get_shape_from_header
from photonfinder.ui.TelescopiusCompareDialog import (
    TelescopiusCompareDialog, TelescopiusListsTask, TelescopiusTarget, enrich_telescopius_data)


@pytest.fixture(autouse=True)
def message_boxes(monkeypatch):
    """Record message boxes instead of showing them: a real one would block the test run."""
    shown = []
    for kind in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, kind,
                            lambda parent, title, text, *a, _kind=kind, **k: shown.append((_kind, text)))
    return shown


@pytest.fixture
def compare_dialog(qtbot, context, mocker):
    mocker.patch.object(TelescopiusListsTask, "start")  # no network
    context.settings.get_telescopius_api_key = lambda: "key"
    dialog = TelescopiusCompareDialog(context, None, None)
    qtbot.addWidget(dialog)
    dialog.list_combo.addItem("My list", "list-1")
    dialog.list_combo.setCurrentIndex(0)
    dialog.fetch_button.setEnabled(True)
    return dialog


@pytest.mark.parametrize("text", ["", "0"])
def test_a_missing_or_zero_tolerance_is_refused_and_fetch_stays_usable(compare_dialog, mocker, message_boxes, text):
    start = mocker.patch.object(compare_dialog.task, "start")
    compare_dialog.tolerance_edit.setText(text)

    compare_dialog.on_start()

    start.assert_not_called()
    assert compare_dialog.fetch_button.isEnabled()
    assert [kind for kind, _ in message_boxes] == ["warning"]


def test_a_valid_tolerance_starts_the_fetch_in_degrees(compare_dialog, mocker):
    start = mocker.patch.object(compare_dialog.task, "start")
    compare_dialog.tolerance_edit.setText("30")

    compare_dialog.on_start()

    assert start.call_args.args[3] == pytest.approx(0.5)


class _BrokenTarget(TelescopiusTarget):
    def coord(self):
        raise ValueError("bad coordinates")


def test_a_target_that_fails_is_reported_in_the_results(compare_dialog, sample):
    targets = [TelescopiusTarget("M 31", 0.712, 41.27), _BrokenTarget("Broken", 0.0, 0.0)]

    results = enrich_telescopius_data(targets, SearchCriteria(), 0.5)

    assert [r.target.name for r in results] == ["M 31", "Broken"]
    assert not results[0].error
    assert results[1].error == "bad coordinates"
    compare_dialog.task.results = results
    compare_dialog.on_complete()
    cells = [compare_dialog.tableWidget.item(r, compare_dialog.PATHS_COLUMN).text()
             for r in range(compare_dialog.tableWidget.rowCount())]
    assert "Could not check: bad coordinates" in cells


def test_switching_catalogs_mid_load_shows_only_the_latest(qtbot, context, monkeypatch):
    release_old = threading.Event()

    def catalog_report(ctx, catalog, criteria, only_matching, progress=None):
        if catalog == "Messier":
            progress(1, 2)
            assert release_old.wait(5)
        return reports.CatalogReportResult(entries=[catalog], matches={}, only_matching=only_matching,
                                           total_entries=1, images_considered=0)

    monkeypatch.setattr(reports, "catalog_report", catalog_report)
    loader = CatalogReportLoader(context)
    results, progress = [], []
    loader.on_result.connect(results.append)
    loader.on_progress.connect(lambda done, total: progress.append(done))

    loader.start("Messier", SearchCriteria())
    loader.start("NGC", SearchCriteria())
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    release_old.set()
    QThreadPool.globalInstance().waitForDone(5000)
    qtbot.wait(50)

    assert [r[0] for r in results] == [["NGC"]]
    assert progress == []


def test_a_failing_catalog_load_is_reported(qtbot, context, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("no such table")

    monkeypatch.setattr(reports, "catalog_report", broken)
    loader = CatalogReportLoader(context)
    errors = []
    loader.on_error.connect(errors.append)

    loader.start("Messier", SearchCriteria())
    qtbot.waitUntil(lambda: bool(errors), timeout=5000)

    assert "no such table" in errors[0]


def _wcs_without_crpix():
    wcs = Header()
    wcs["CTYPE1"], wcs["CTYPE2"] = "RA---TAN", "DEC--TAN"
    wcs["CRVAL1"], wcs["CRVAL2"] = 10.68, 41.27
    wcs["CDELT1"], wcs["CDELT2"] = -0.0003, 0.0003
    return wcs


def _image_header():
    header = Header()
    header["NAXIS"], header["NAXIS1"], header["NAXIS2"] = 2, 6000, 4000
    return header


def test_the_header_dialog_opens_for_a_wcs_without_crpix(qtbot):
    dialog = HeaderDialog(_image_header(), _wcs_without_crpix())
    qtbot.addWidget(dialog)

    assert dialog.wcsSummary.rowCount() == 1
    assert get_shape_from_header(_image_header(), _wcs_without_crpix()) == (4000, 6000)


def test_the_size_falls_back_to_the_reference_pixel(qtbot):
    wcs = _wcs_without_crpix()
    wcs["CRPIX1"], wcs["CRPIX2"] = 3000.5, 2000.5

    assert get_shape_from_header(Header(), wcs) == (4001, 6001)


def test_without_any_size_the_dialog_opens_without_the_summary(qtbot):
    dialog = HeaderDialog(Header(), _wcs_without_crpix())
    qtbot.addWidget(dialog)

    assert dialog.wcsSummary.rowCount() == 0

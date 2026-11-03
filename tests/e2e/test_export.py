"""Exporting light frames with their calibration, through the real dialog on a scanned library.

The library has 300 s darks that suit three light sessions (shared-session), flats per filter
and night, darks at the flat exposures (dark-flats), and masters for one dark, flat and bias
series (use-master, and a master flat clearing the dark-flat). The expected output is what
the export wrote before the Export plan refactor (architecture-deepening 16)."""
import dataclasses
import json
import re
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QComboBox

from photonfinder.models import SearchCriteria
from photonfinder.ui.ExportDialog import ExportDialog
from tests.e2e.helpers import add_libraries
from tests.e2e.synthetic_library import MONO, PLAIN, file_name, write_frame
from tests.e2e.test_diagnose_quality import search

PATTERN = "${sess_date}/${image_type}/${filter}/${filename}"
EXPECTED = Path(__file__).parent / "data" / "export_expected.json"
FLAT_COL, DARKFLAT_COL = 1, 4


def _write_masters(libraries, folder: Path) -> dict[str, str]:
    """A master for one dark, flat and bias series, stamped with its earliest sub's DATE-OBS as
    stacking software does. Returns {type: file name}."""
    rng = np.random.default_rng(7)
    series = {
        "MASTER DARK": libraries.frames(image_type="DARK", camera=MONO, exposure=300.0, set_temp=-10.0),
        "MASTER FLAT": libraries.frames(image_type="FLAT", camera=MONO, filter="Ha"),
        "MASTER BIAS": libraries.frames(image_type="BIAS", camera=MONO, set_temp=-10.0, gain=100),
    }
    names = {}
    for master_type, subs in series.items():
        first = min(subs, key=lambda f: f.date_obs)
        master = dataclasses.replace(first, image_type=master_type)
        name = "master_" + file_name(master, PLAIN)
        write_frame(folder / name, master, rng)
        names[master_type] = name
    return names


@pytest.fixture
def export_dialog(sandbox, app, modals, boundary, libraries, tmp_path, qtbot):
    masters = tmp_path / "masters"
    masters.mkdir()
    _write_masters(libraries, masters)
    add_libraries(sandbox, modals, boundary, {**libraries.roots, "masters": masters}, scan=True)
    search(sandbox, app, type="LIGHT")
    panel = app.get_current_search_panel()
    panel.export_data()
    [dialog] = panel.findChildren(ExportDialog)
    qtbot.waitUntil(lambda: dialog._grid_loaded, timeout=10_000)
    yield dialog
    dialog.close()


def _export(dialog, qtbot, out: Path, use_master: bool, shared: bool) -> list[str]:
    dialog.useMasterCheckBox.setChecked(use_master)
    dialog.sharedSessionCheckBox.setChecked(shared)
    dialog.outputPathEdit.setText(str(out))
    dialog.patternComboBox.setCurrentText(PATTERN)
    finished = []
    dialog.export_worker.finished.connect(lambda: finished.append(True))
    dialog.export_files()
    qtbot.waitUntil(lambda: bool(finished), timeout=30_000)
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def _session_rows(dialog) -> list[str]:
    return [dialog.calibrationTable.item(row, 0).text() for row in range(dialog.calibrationTable.rowCount())]


def _combo(dialog, row, col) -> QComboBox:
    widget = dialog.calibrationTable.cellWidget(row, col)
    assert isinstance(widget, QComboBox), (row, col, widget)
    return widget


def test_unchecking_use_master_brings_the_dark_flat_back(export_dialog):
    dialog = export_dialog
    dialog.useMasterCheckBox.setChecked(False)
    rows = _session_rows(dialog)
    row = next(i for i, text in enumerate(rows) if text.startswith("2026-10-14  Ha"))
    darkflat = _combo(dialog, row, DARKFLAT_COL)
    default = darkflat.currentData()
    assert default is not None and _combo(dialog, row, FLAT_COL).currentData().master is not None

    dialog.useMasterCheckBox.setChecked(True)
    assert darkflat.currentData() is None  # the master flat is already dark-flat corrected

    dialog.useMasterCheckBox.setChecked(False)
    assert darkflat.currentData() is default


def test_a_mixed_export_goes_out_as_it_is(sandbox, app, modals, boundary, libraries, qtbot, tmp_path):
    """Exporting a whole camera's files, lights and calibration: no calibration matching, every
    file once, at the night it was taken."""
    add_libraries(sandbox, modals, boundary, libraries.roots, scan=True)
    search(sandbox, app, camera=MONO)
    panel = app.get_current_search_panel()
    panel.export_data()
    [dialog] = panel.findChildren(ExportDialog)
    qtbot.waitUntil(lambda: dialog._grid_loaded, timeout=10_000)

    assert not dialog.calibrationTable.isVisibleTo(dialog)
    mono = sorted(p.name for p, f in libraries.files.items() if f.camera == MONO)
    decompressed = sorted(re.sub(r"\.(gz|bz2|xz)$", "", name) for name in mono)
    assert dialog.decompressCheckBox.isChecked()
    assert dialog.windowTitle() == f"Export {len(mono)} files"
    paths = _export(dialog, qtbot, tmp_path / "out", use_master=False, shared=False)
    dialog.close()

    assert sorted(p.rsplit("/", 1)[1] for p in paths) == decompressed
    nights = {f.session_date.isoformat() for f in libraries.files.values() if f.camera == MONO}
    assert {p.split("/", 1)[0] for p in paths} == nights


def test_lights_alone_get_their_calibration_matched(export_dialog):
    assert export_dialog.calibrationTable.isVisibleTo(export_dialog)
    assert export_dialog.calibrationTable.rowCount() > 0


def _expected(name: str) -> list[str]:
    return json.loads(EXPECTED.read_text(encoding="utf-8"))[name]


@pytest.mark.parametrize("use_master, shared, name", [
    (False, True, "subs_shared"),
    (True, True, "masters_shared"),
    (False, False, "subs_per_session"),
])
def test_export_output(export_dialog, qtbot, tmp_path, use_master, shared, name):
    paths = _export(export_dialog, qtbot, tmp_path / "out", use_master=use_master, shared=shared)

    assert paths == _expected(name)


def test_choosing_no_flat_clears_its_dark_flats(export_dialog, qtbot, tmp_path):
    rows = _session_rows(export_dialog)
    row = next(i for i, text in enumerate(rows) if text.startswith("2026-10-14  Ha"))
    _combo(export_dialog, row, FLAT_COL).setCurrentIndex(0)  # (none)

    darkflat = export_dialog.calibrationTable.cellWidget(row, DARKFLAT_COL)
    assert not isinstance(darkflat, QComboBox) or darkflat.currentData() is None
    paths = _export(export_dialog, qtbot, tmp_path / "out", use_master=False, shared=True)

    assert paths == _expected("no_ha_flat")

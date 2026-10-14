from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QDialogButtonBox

from photonfinder.models import SearchCriteria
from photonfinder.ui.ExportDialog import ExportDialog, CalibrationGridLoader
from tests.conftest import DynamicSettings


@pytest.fixture
def dialog(sample, qtbot):
    context, objs = sample
    context.settings = DynamicSettings()
    for flag in ("decompress", "xisf_as_fits", "override_platesolve", "use_master", "shared_session"):
        getattr(context.settings, f"set_last_export_{flag}")(False)
    # Keep the calibration grid "loading" until the test says otherwise.
    with patch.object(CalibrationGridLoader, "load"):
        dlg = ExportDialog(context, SearchCriteria(), files=[objs["light"]])
    qtbot.addWidget(dlg)
    dlg.patternComboBox.setEditText("$filename")
    return dlg


def _ok_enabled(dlg):
    return dlg.buttonBox.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_export_disabled_until_calibration_grid_loaded(dialog):
    assert not _ok_enabled(dialog)
    dialog._on_grid_loaded()
    assert _ok_enabled(dialog)


@pytest.mark.parametrize("path", ["", "relative/dir"])
def test_export_refuses_non_absolute_output_path(dialog, path):
    dialog._on_grid_loaded()
    dialog.outputPathEdit.setText(path)
    with patch("photonfinder.ui.ExportDialog.QMessageBox.warning") as warning, \
            patch.object(dialog.export_worker, "export_files") as start:
        dialog.export_files()
    warning.assert_called_once()
    start.assert_not_called()


def test_closing_during_export_cancels_worker_and_announces_project(dialog, tmp_path):
    dialog._on_grid_loaded()
    dialog.outputPathEdit.setText(str(tmp_path))
    dialog.createGroupCheckBox.setChecked(True)
    announced = []
    dialog.context.signal_bus.projects_changed.connect(lambda projects, change: announced.append(projects))
    with patch.object(dialog.export_worker, "export_files"):
        dialog.export_files()

    dialog.reject()

    assert dialog.export_worker.cancelled
    assert announced == [[dialog.created_project]]
    dialog.on_export_finished()  # the worker's late "finished" must not announce again
    assert len(announced) == 1

import plistlib
import sys

import pytest

from photonfinder.core import ApplicationContext, Settings, StatusReporter
from photonfinder.ui.SettingsDialog import SettingsDialog


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_plate_solving_tab_matches_platform(qtbot, tmp_path, monkeypatch, platform):
    context = ApplicationContext(":memory:", Settings(path=tmp_path / "settings.ini"))
    context.set_status_reporter(StatusReporter())
    monkeypatch.setattr(sys, "platform", platform)
    dialog = SettingsDialog(context)
    qtbot.addWidget(dialog)

    windows = platform == "win32"
    assert dialog.wsl_distro_edit.isHidden() != windows
    assert dialog.wsl_distro_label.isHidden() != windows
    texts = [dialog.wsl_hint_label.text(), dialog.solve_field_path_edit.placeholderText(),
             dialog.solve_field_path_edit.toolTip(), dialog.saspro_path_edit.placeholderText(),
             dialog.saspro_path_edit.toolTip()]
    windows_only = ("WSL", "ANSVR", "Cygwin", "Program Files", ".exe")
    assert any(word in text for text in texts for word in windows_only) == windows


def test_app_bundle_resolves_to_its_executable(tmp_path):
    macos = tmp_path / "ASTAP.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "astap").write_bytes(b"")
    (macos / "helper").write_bytes(b"")
    with open(tmp_path / "ASTAP.app" / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleExecutable": "astap"}, f)

    assert SettingsDialog._app_bundle_executable(str(tmp_path / "ASTAP.app")) == str(macos / "astap")
    assert SettingsDialog._app_bundle_executable(str(macos / "helper")) == str(macos / "helper")


def test_app_bundle_without_plist_uses_its_only_program(tmp_path):
    macos = tmp_path / "SetiAstroSuitePro.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "SetiAstroSuitePro").write_bytes(b"")

    assert SettingsDialog._app_bundle_executable(str(tmp_path / "SetiAstroSuitePro.app")) == str(macos / "SetiAstroSuitePro")

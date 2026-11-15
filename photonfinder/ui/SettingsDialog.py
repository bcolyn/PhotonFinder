import os
import plistlib
import sys
import zoneinfo
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCompleter, QDialog, QFileDialog, QMessageBox

from photonfinder.core import ApplicationContext, get_default_saspro_path
from photonfinder.local_time import is_valid_timezone
from photonfinder.thumbnails import ThumbnailCache
from photonfinder.ui.generated.SettingsDialog_ui import Ui_SettingsDialog


class SettingsDialog(QDialog, Ui_SettingsDialog):
    def __init__(self, context: ApplicationContext, parent=None, thumbnails=None):
        """*thumbnails* is the main window's ThumbnailLoader, so clearing also forgets the
        thumbnails in memory; without it only the disk cache is cleared."""
        super(SettingsDialog, self).__init__(parent)
        self.setupUi(self)
        self.adjustSize()
        self.context = context
        self.thumbnails = thumbnails
        self.clear_thumbnails_button.clicked.connect(self.clear_thumbnail_cache)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        self.astap_path_edit.setText(self.context.settings.get_astap_path())
        self.astrometry_api_key_edit.setText(self.context.settings.get_astrometry_net_api_key())
        self.astrometry_image_upload_check.setChecked(self.context.settings.get_astrometry_net_force_image_upload())
        self.solve_field_path_edit.setText(self.context.settings.get_solve_field_path())
        self.wsl_distro_edit.setText(self.context.settings.get_solve_field_wsl_distro())
        self.wsl_timeout_spin.setValue(self.context.settings.get_solve_field_timeout())
        self.saspro_path_edit.setText(self.context.settings.get_saspro_path())

        self.file_ignore_edit.setText(self.context.settings.get_bad_file_patterns())
        self.folder_ignore_edit.setText(self.context.settings.get_bad_dir_patterns())

        self.internal_viewer_check.setChecked(self.context.settings.get_use_internal_viewer())

        self.obs_timezone_edit.setText(self.context.settings.get_obs_timezone())
        completer = QCompleter(sorted(zoneinfo.available_timezones()), self.obs_timezone_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.obs_timezone_edit.setCompleter(completer)

        self.telescopius_api_key_edit.setText(self.context.settings.get_telescopius_api_key())

        self.mcp_port_spin.setValue(self.context.settings.get_mcp_port())
        self.mcp_allow_plate_solve_check.setChecked(self.context.settings.get_mcp_allow_plate_solve())

        self.astap_browse_button.clicked.connect(self.browse_astap_executable)
        self.solve_field_browse_button.clicked.connect(self.browse_solve_field_executable)
        self.saspro_browse_button.clicked.connect(self.browse_saspro_executable)
        self._adapt_plate_solving_tab()

    def _adapt_plate_solving_tab(self):
        """The .ui holds the Windows wording (ANSVR, Cygwin, WSL, Program Files). Elsewhere
        solve-field is an ordinary local program, so the WSL distro setting does not apply."""
        if sys.platform == 'win32':
            return
        self.wsl_distro_label.hide()
        self.wsl_distro_edit.hide()
        installer = "Homebrew (brew install astrometry-net)" if sys.platform == 'darwin' \
            else "your package manager (e.g. apt install astrometry.net)"
        self.wsl_hint_label.setText(
            f"solve-field is astrometry.net's local solver; install it with {installer}, "
            "together with index files for your field of view.")
        self.solve_field_path_edit.setToolTip(
            "Path to the solve-field executable. Leave empty to use solve-field from the PATH.")
        self.solve_field_path_edit.setPlaceholderText("Leave empty to use solve-field from the PATH")
        self.saspro_path_edit.setToolTip(
            "Path to the Seti Astro Suite Pro executable or app. "
            "Leave empty to use the standard install location.")
        if sys.platform == 'darwin':
            self.saspro_path_edit.setPlaceholderText("Leave empty for the app in /Applications")
        else:
            self.saspro_path_edit.setPlaceholderText("Path to the Seti Astro Suite Pro executable")

    @staticmethod
    def _file_filter(windows_name: str) -> str:
        """Windows filters on the .exe name; elsewhere executables have no extension to match."""
        if sys.platform == 'win32':
            return f"{windows_name};;All Files (*.*)"
        return "All Files (*)"

    @staticmethod
    def _app_bundle_executable(path: str) -> str:
        """The program inside a macOS .app bundle, which a file dialog returns whole; any other
        path unchanged. The bundle's Info.plist names its executable."""
        bundle = Path(path)
        if bundle.suffix.lower() != ".app" or not bundle.is_dir():
            return path
        macos = bundle / "Contents" / "MacOS"
        try:
            with open(bundle / "Contents" / "Info.plist", "rb") as f:
                name = plistlib.load(f).get("CFBundleExecutable")
            if name and (macos / name).is_file():
                return str(macos / name)
        except (OSError, plistlib.InvalidFileException):
            pass
        programs = sorted(p for p in macos.glob("*") if p.is_file()) if macos.is_dir() else []
        return str(programs[0]) if len(programs) == 1 else path

    def _browse_executable(self, edit, title: str, windows_name: str, default: str = ""):
        current_path = edit.text() or default
        start_dir = os.path.dirname(current_path) if current_path else os.path.expanduser("~")
        file_path, _ = QFileDialog.getOpenFileName(self, title, start_dir, self._file_filter(windows_name))
        if file_path:
            edit.setText(SettingsDialog._app_bundle_executable(file_path))

    def _thumbnail_cache(self) -> ThumbnailCache:
        return self.thumbnails.cache if self.thumbnails is not None else ThumbnailCache(self.context.thumbnail_dir)

    def _on_tab_changed(self, index):
        if self.tab_widget.widget(index) is self.thumbnails_tab:
            self.show_thumbnail_usage()

    def show_thumbnail_usage(self):
        """Counted when the tab is opened, not with the dialog: a big cache takes a moment."""
        count, size = self._thumbnail_cache().usage()
        self.thumbnails_usage_label.setText(f"The cache holds {count} thumbnails ({size / 1024 ** 2:.1f} MB).")
        self.clear_thumbnails_button.setEnabled(count > 0 or size > 0)

    def clear_thumbnail_cache(self):
        """Takes effect at once, not with OK: the cache is no setting."""
        if self.thumbnails is not None:
            count, size = self.thumbnails.clear()
        else:
            count, size = self._thumbnail_cache().clear()
        self.thumbnails_usage_label.setText(f"Removed {count} thumbnails ({size / 1024 ** 2:.1f} MB).")
        self.clear_thumbnails_button.setEnabled(False)

    def browse_astap_executable(self):
        self._browse_executable(self.astap_path_edit, "Select ASTAP Executable", "astap.exe")

    def browse_solve_field_executable(self):
        self._browse_executable(self.solve_field_path_edit, "Select solve-field Executable", "solve-field.exe")

    def browse_saspro_executable(self):
        self._browse_executable(self.saspro_path_edit, "Select Seti Astro Suite Pro Executable",
                                "SetiAstroSuitePro.exe", default=get_default_saspro_path())

    def accept(self):
        zone = self.obs_timezone_edit.text().strip()
        if not is_valid_timezone(zone):
            # Never empty: stored local times depend on it (local_time.obs_zone).
            QMessageBox.warning(self, "Timezone", f"\"{zone}\" is not a known timezone. "
                                                  "Pick one from the list, e.g. Europe/Brussels.")
            self.obs_timezone_edit.setFocus()
            return
        self.context.settings.set_astap_path(self.astap_path_edit.text())
        self.context.settings.set_astrometry_net_api_key(self.astrometry_api_key_edit.text())
        self.context.settings.set_astrometry_net_force_image_upload(self.astrometry_image_upload_check.isChecked())
        self.context.settings.set_solve_field_path(self.solve_field_path_edit.text())
        self.context.settings.set_solve_field_wsl_distro(self.wsl_distro_edit.text().strip())
        self.context.settings.set_solve_field_timeout(self.wsl_timeout_spin.value())
        self.context.settings.set_saspro_path(self.saspro_path_edit.text().strip())

        self.context.settings.set_bad_file_patterns(self.file_ignore_edit.text())
        self.context.settings.set_bad_dir_patterns(self.folder_ignore_edit.text())

        self.context.settings.set_use_internal_viewer(self.internal_viewer_check.isChecked())

        self.context.settings.set_obs_timezone(zone)

        self.context.settings.set_telescopius_api_key(self.telescopius_api_key_edit.text().strip())

        self.context.settings.set_mcp_port(self.mcp_port_spin.value())
        self.context.settings.set_mcp_allow_plate_solve(self.mcp_allow_plate_solve_check.isChecked())

        self.context.settings.sync()
        super(SettingsDialog, self).accept()

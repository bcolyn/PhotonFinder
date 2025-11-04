import os
import platform
from PySide6.QtWidgets import QDialog, QFileDialog

from photonfinder.core import ApplicationContext
from photonfinder.ui.generated.SettingsDialog_ui import Ui_SettingsDialog


class SettingsDialog(QDialog, Ui_SettingsDialog):
    """
    Dialog for managing application settings.
    """

    def __init__(self, context: ApplicationContext, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context

        # Initialize plate solving settings
        self.astap_path_edit.setText(self.context.settings.get_astap_path())
        self.astap_fov_edit.setText(self.context.settings.get_astap_fallback_fov())
        self.astrometry_api_key_edit.setText(self.context.settings.get_astrometry_net_api_key())
        self.astrometry_image_upload_check.setChecked(self.context.settings.get_astrometry_net_force_image_upload())

        # Initialize file settings
        self.file_ignore_edit.setText(self.context.settings.get_bad_file_patterns())
        self.folder_ignore_edit.setText(self.context.settings.get_bad_dir_patterns())

        # Connect signals
        self.astap_browse_button.clicked.connect(self.browse_astap_executable)

    def browse_astap_executable(self):
        """Open a file dialog to select the ASTAP executable."""
        file_filter = ""
        if platform.system() == "Windows":
            file_filter = "Executable Files (*.exe);;All Files (*.*)"
            default_name = "astap.exe"
        else:
            file_filter = "All Files (*)"
            default_name = "astap"

        current_path = self.astap_path_edit.text()
        start_dir = os.path.dirname(current_path) if current_path else os.path.expanduser("~")

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select ASTAP Executable",
            start_dir,
            file_filter
        )

        if file_path:
            self.astap_path_edit.setText(file_path)

    def accept(self):
        """Save settings when OK is clicked."""

        # Save plate solving settings
        self.context.settings.set_astap_path(self.astap_path_edit.text())
        self.context.settings.set_astap_fallback_fov(self.astap_fov_edit.text())
        self.context.settings.set_astrometry_net_api_key(self.astrometry_api_key_edit.text())
        self.context.settings.set_astrometry_net_force_image_upload(self.astrometry_image_upload_check.isChecked())

        # Save file settings
        self.context.settings.set_bad_file_patterns(self.file_ignore_edit.text())
        self.context.settings.set_bad_dir_patterns(self.folder_ignore_edit.text())

        # Sync settings to ensure they are saved
        self.context.settings.sync()

        super(SettingsDialog, self).accept()

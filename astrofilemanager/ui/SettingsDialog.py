from PySide6.QtWidgets import QDialog

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.generated.SettingsDialog_ui import Ui_SettingsDialog


class SettingsDialog(QDialog, Ui_SettingsDialog):
    """
    Dialog for managing application settings.
    """
    def __init__(self, context: ApplicationContext, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context

        # Initialize settings
        self.cache_headers_checkbox.setChecked(self.context.settings.get_cache_compressed_headers())

    def accept(self):
        """Save settings when OK is clicked."""
        # Save the "cache compressed headers" setting
        self.context.settings.set_cache_compressed_headers(self.cache_headers_checkbox.isChecked())

        # Sync settings to ensure they are saved
        self.context.settings.sync()

        super(SettingsDialog, self).accept()

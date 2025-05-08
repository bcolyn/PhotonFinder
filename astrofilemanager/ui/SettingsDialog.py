from PySide6.QtWidgets import QDialog, QVBoxLayout, QCheckBox, QDialogButtonBox, QGroupBox

from astrofilemanager.core import ApplicationContext


class SettingsDialog(QDialog):
    """
    Dialog for managing application settings.
    """
    def __init__(self, context: ApplicationContext, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.context = context
        self.setWindowTitle("Settings")
        self.resize(400, 300)
        
        # Main layout
        layout = QVBoxLayout(self)
        
        # Create a group box for header settings
        header_group = QGroupBox("Header Settings")
        header_layout = QVBoxLayout(header_group)
        
        # Add the "cache compressed headers" checkbox
        self.cache_headers_checkbox = QCheckBox("Cache compressed headers")
        self.cache_headers_checkbox.setChecked(self.context.settings.get_cache_compressed_headers())
        self.cache_headers_checkbox.setToolTip("When enabled, compressed headers will be cached for faster access")
        header_layout.addWidget(self.cache_headers_checkbox)
        
        # Add the group box to the main layout
        layout.addWidget(header_group)
        
        # Add some space
        layout.addStretch(1)
        
        # Add standard buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
    def accept(self):
        """Save settings when OK is clicked."""
        # Save the "cache compressed headers" setting
        self.context.settings.set_cache_compressed_headers(self.cache_headers_checkbox.isChecked())
        
        # Sync settings to ensure they are saved
        self.context.settings.sync()
        
        super(SettingsDialog, self).accept()
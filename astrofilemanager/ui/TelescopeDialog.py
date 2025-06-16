from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QDialogButtonBox

from astrofilemanager.core import ApplicationContext


class TelescopeDialog(QDialog):
    """Dialog for entering telescope name (string)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(TelescopeDialog, self).__init__(parent)
        self.context = context
        self.setWindowTitle("Enter Telescope")
        self.setModal(True)
        
        # Create layout
        layout = QVBoxLayout(self)
        
        # Add label
        label = QLabel("Enter telescope name:")
        layout.addWidget(label)
        
        # Add line edit for telescope name
        self.telescope_edit = QLineEdit(self)
        layout.addWidget(self.telescope_edit)
        
        # Add buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
    
    def get_telescope(self) -> str:
        """Get the telescope name entered by the user."""
        return self.telescope_edit.text()
    
    def set_telescope(self, telescope: str):
        """Set the initial telescope name."""
        if telescope:
            self.telescope_edit.setText(telescope)
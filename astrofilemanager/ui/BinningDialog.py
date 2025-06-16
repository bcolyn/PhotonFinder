from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QDialogButtonBox

from astrofilemanager.core import ApplicationContext


class BinningDialog(QDialog):
    """Dialog for entering binning value (integer between 1 and 4)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(BinningDialog, self).__init__(parent)
        self.context = context
        self.setWindowTitle("Enter Binning")
        self.setModal(True)
        
        # Create layout
        layout = QVBoxLayout(self)
        
        # Add label
        label = QLabel("Enter binning value (1-4):")
        layout.addWidget(label)
        
        # Add spin box for binning value
        self.binning_spin = QSpinBox(self)
        self.binning_spin.setRange(1, 4)  # Binning is typically 1, 2, 3, or 4
        self.binning_spin.setValue(1)  # Default value
        layout.addWidget(self.binning_spin)
        
        # Add buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
    
    def get_binning(self) -> int:
        """Get the binning value entered by the user."""
        return self.binning_spin.value()
    
    def set_binning(self, binning: int):
        """Set the initial binning value."""
        if binning is not None:
            try:
                bin_val = int(binning)
                if 1 <= bin_val <= 4:
                    self.binning_spin.setValue(bin_val)
            except (ValueError, TypeError):
                pass
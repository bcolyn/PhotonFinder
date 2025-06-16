from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QDialogButtonBox

from astrofilemanager.core import ApplicationContext


class GainDialog(QDialog):
    """Dialog for entering gain value (integer)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(GainDialog, self).__init__(parent)
        self.context = context
        self.setWindowTitle("Enter Gain")
        self.setModal(True)
        
        # Create layout
        layout = QVBoxLayout(self)
        
        # Add label
        label = QLabel("Enter gain value:")
        layout.addWidget(label)
        
        # Add spin box for gain value
        self.gain_spin = QSpinBox(self)
        self.gain_spin.setRange(0, 10000)  # Allow a wide range of gain values
        self.gain_spin.setValue(0)  # Default value
        layout.addWidget(self.gain_spin)
        
        # Add buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
    
    def get_gain(self) -> int:
        """Get the gain value entered by the user."""
        return self.gain_spin.value()
    
    def set_gain(self, gain: int):
        """Set the initial gain value."""
        if gain is not None:
            try:
                gain_val = int(gain)
                if gain_val >= 0:
                    self.gain_spin.setValue(gain_val)
            except (ValueError, TypeError):
                pass
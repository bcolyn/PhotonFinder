from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDoubleSpinBox, QDialogButtonBox

from astrofilemanager.core import ApplicationContext


class ExposureDialog(QDialog):
    """Dialog for entering exposure value (floating point)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(ExposureDialog, self).__init__(parent)
        self.context = context
        self.setWindowTitle("Enter Exposure")
        self.setModal(True)
        
        # Create layout
        layout = QVBoxLayout(self)
        
        # Add label
        label = QLabel("Enter exposure time (seconds):")
        layout.addWidget(label)
        
        # Add spin box for exposure value
        self.exposure_spin = QDoubleSpinBox(self)
        self.exposure_spin.setRange(0.001, 10000.0)  # Allow a wide range of exposure times
        self.exposure_spin.setDecimals(3)  # Allow millisecond precision
        self.exposure_spin.setSingleStep(0.1)
        self.exposure_spin.setValue(1.0)  # Default value
        layout.addWidget(self.exposure_spin)
        
        # Add buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
    
    def get_exposure(self) -> float:
        """Get the exposure value entered by the user."""
        return self.exposure_spin.value()
    
    def set_exposure(self, exposure: float):
        """Set the initial exposure value."""
        if exposure is not None:
            try:
                self.exposure_spin.setValue(float(exposure))
            except (ValueError, TypeError):
                pass
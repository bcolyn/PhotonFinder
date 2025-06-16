from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDoubleSpinBox, QDialogButtonBox

from astrofilemanager.core import ApplicationContext


class TemperatureDialog(QDialog):
    """Dialog for entering temperature value (floating point)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(TemperatureDialog, self).__init__(parent)
        self.context = context
        self.setWindowTitle("Enter Temperature")
        self.setModal(True)
        
        # Create layout
        layout = QVBoxLayout(self)
        
        # Add label
        label = QLabel("Enter temperature (°C):")
        layout.addWidget(label)
        
        # Add spin box for temperature value
        self.temperature_spin = QDoubleSpinBox(self)
        self.temperature_spin.setRange(-100.0, 50.0)  # Allow a wide range of temperatures
        self.temperature_spin.setDecimals(1)  # One decimal place is sufficient for temperature
        self.temperature_spin.setSingleStep(0.5)
        self.temperature_spin.setValue(-20.0)  # Default value, common for astronomy cameras
        layout.addWidget(self.temperature_spin)
        
        # Add buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
    
    def get_temperature(self) -> float:
        """Get the temperature value entered by the user."""
        return self.temperature_spin.value()
    
    def set_temperature(self, temperature: float):
        """Set the initial temperature value."""
        if temperature is not None:
            try:
                temp_val = float(temperature)
                if -100.0 <= temp_val <= 50.0:
                    self.temperature_spin.setValue(temp_val)
            except (ValueError, TypeError):
                pass
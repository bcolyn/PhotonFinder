from PySide6.QtWidgets import QDialog

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.generated.TemperatureDialog_ui import Ui_TemperatureDialog


class TemperatureDialog(QDialog, Ui_TemperatureDialog):
    """Dialog for entering temperature value (floating point)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(TemperatureDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context

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

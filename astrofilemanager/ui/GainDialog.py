from PySide6.QtWidgets import QDialog

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.generated.GainDialog_ui import Ui_GainDialog


class GainDialog(QDialog, Ui_GainDialog):
    """Dialog for entering gain value (integer)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(GainDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context

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

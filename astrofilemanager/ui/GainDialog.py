from PySide6.QtWidgets import QDialog

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.generated.GainDialog_ui import Ui_GainDialog


class GainDialog(QDialog, Ui_GainDialog):
    """Dialog for entering gain value (integer)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(GainDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.offset_check.toggled.connect(self.offset_spin.setEnabled)

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

    def get_offset(self) -> int | None:
        if self.offset_check.isChecked():
            return self.offset_spin.value()
        else:
            return None

    def set_offset(self, value):
        if value is not None:
            try:
                self.offset_spin.setValue(int(value))
                self.offset_check.setChecked(True)
            except (ValueError, TypeError):
                pass

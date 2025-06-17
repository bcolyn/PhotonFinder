from PySide6.QtWidgets import QDialog

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.generated.BinningDialog_ui import Ui_BinningDialog


class BinningDialog(QDialog, Ui_BinningDialog):
    """Dialog for entering binning value (integer between 1 and 4)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(BinningDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context

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

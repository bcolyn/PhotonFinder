from PySide6.QtWidgets import QDialog

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.generated.ExposureDialog_ui import Ui_ExposureDialog


class ExposureDialog(QDialog, Ui_ExposureDialog):
    """Dialog for entering exposure value (floating point)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(ExposureDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context

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

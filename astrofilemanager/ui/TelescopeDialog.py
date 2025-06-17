from PySide6.QtWidgets import QDialog

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.generated.TelescopeDialog_ui import Ui_TelescopeDialog


class TelescopeDialog(QDialog, Ui_TelescopeDialog):
    """Dialog for entering telescope name (string)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(TelescopeDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context

    def get_telescope(self) -> str:
        """Get the telescope name entered by the user."""
        return self.telescope_edit.text()

    def set_telescope(self, telescope: str):
        """Set the initial telescope name."""
        if telescope:
            self.telescope_edit.setText(telescope)

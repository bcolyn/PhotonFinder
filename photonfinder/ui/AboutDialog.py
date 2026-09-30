from PySide6.QtWidgets import QDialog

from photonfinder.ui.generated.AboutDialog_ui import Ui_AboutDialog
from photonfinder.version import get_build_date, get_version


class AboutDialog(QDialog, Ui_AboutDialog):
    """
    Dialog for displaying information about the application.
    """
    def __init__(self, parent=None):
        super(AboutDialog, self).__init__(parent)
        self.setupUi(self)
        build_date = get_build_date() or "development"
        self.versionLabel.setText(f"Version: {get_version()} (build {build_date})")

from PySide6.QtWidgets import QDialog

from astrofilemanager.ui.generated.AboutDialog_ui import Ui_AboutDialog


class AboutDialog(QDialog, Ui_AboutDialog):
    """
    Dialog for displaying information about the application.
    """
    def __init__(self, parent=None):
        super(AboutDialog, self).__init__(parent)
        self.setupUi(self)
        
        # The UI file already contains all the necessary information from pyproject.toml:
        # - Project name: "PhotonFinder"
        # - Version: "1.0.0"
        # - Description: "Desktop application for managing astronomical files"
        # - License: "MIT"
        # - Authors: "benny" with email "benny.colyn@gmail.com"
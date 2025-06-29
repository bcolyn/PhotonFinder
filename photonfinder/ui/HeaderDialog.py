from PySide6.QtWidgets import QDialog

from photonfinder.ui.generated.HeaderDialog_ui import Ui_HeaderDialog


class HeaderDialog(QDialog, Ui_HeaderDialog):
    """
    Dialog for displaying cached FITS header content.
    """
    def __init__(self, header_content: str, parent=None):
        super(HeaderDialog, self).__init__(parent)
        self.setupUi(self)
        
        # Set the header content in the text edit
        self.headerTextEdit.setPlainText(header_content)
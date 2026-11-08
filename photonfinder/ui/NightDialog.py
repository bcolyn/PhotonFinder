import datetime

from PySide6.QtWidgets import QDialog

from photonfinder.ui.generated.NightDialog_ui import Ui_NightDialog


class NightDialog(QDialog, Ui_NightDialog):
    """Pick one observing night: the local date it began on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.setModal(True)

    def set_night(self, night: datetime.date):
        self.nightEdit.setDate(night)

    def get_night(self) -> datetime.date:
        return self.nightEdit.date().toPython()

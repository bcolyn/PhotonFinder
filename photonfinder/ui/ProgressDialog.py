from PySide6.QtWidgets import QDialog, QMessageBox

from photonfinder.ui.BackgroundLoader import ProgressBackgroundTask
from photonfinder.ui.generated.ProgressDialog_ui import Ui_ProgressDialog


class ProgressDialog(QDialog, Ui_ProgressDialog):
    def __init__(self, label: str, title: str, task: ProgressBackgroundTask, parent=None):
        super(ProgressDialog, self).__init__(parent)
        self.setupUi(self)
        self.setWindowTitle(title)
        self.label.setText(label)
        self.task = task

        self.task.progress.connect(self.progressBar.setValue)
        self.task.total_found.connect(self.progressBar.setMaximum)
        self.task.finished.connect(self.on_finished)
        self.task.message.connect(self.label.setText)
        self.task.error.connect(self.on_error)
        self.buttonBox.rejected.connect(self.on_cancel)

    def on_cancel(self):
        if self.task:
            self.task.cancel()
        self.reject()

    def on_finished(self):
        self.accept()

    def on_error(self, error_message):
        QMessageBox.critical(self, "Error", error_message)
        self.reject()

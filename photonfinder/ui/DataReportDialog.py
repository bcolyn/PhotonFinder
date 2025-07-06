from PySide6.QtWidgets import QDialog, QDialogButtonBox

from photonfinder.models import UsageReport
from photonfinder.ui.BackgroundLoader import ProgressBackgroundTask
from photonfinder.ui.CreateDataReportDialog import CreateDataReportDialog
from photonfinder.ui.generated.DataReportDialog_ui import Ui_DataUsageReportDialog


class DataUsageReportTask(ProgressBackgroundTask):
    pass


class DataUsageReportDialog(QDialog, Ui_DataUsageReportDialog):

    def __init__(self, parent=None):
        super(DataUsageReportDialog, self).__init__(parent)
        self.setupUi(self)
        self.progressBar.setVisible(False)
        self.connect_signals()
        self.disable_enable_controls()
        self.refresh_report_list()
        self.reports = list()
        self.update_generated_at()

    def connect_signals(self):
        self.comboBox.currentIndexChanged.connect(self.disable_enable_controls)
        self.comboBox.currentIndexChanged.connect(self.update_generated_at)
        self.create_button.clicked.connect(self.create_new_report)
        self.delete_button.clicked.connect(self.delete_current)

    def disable_enable_controls(self):
        index = self.comboBox.currentIndex()
        self.delete_button.setEnabled(index >= 0)
        self.regenerate_button.setEnabled(index >= 0)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).setEnabled(self.tableWidget.rowCount() > 0)

    def create_new_report(self):
        main_window = self.parent()
        dialog = CreateDataReportDialog(main_window=main_window, parent=self)
        main_window.tabs_changed.connect(dialog.refresh_tab_lists)
        dialog.accepted.connect(self.refresh_report_list)
        dialog.show()

    def refresh_report_list(self):
        reports = UsageReport.select().execute()
        self.comboBox.clear()
        for report in reports:
            self.comboBox.addItem(report.name, report)
        self.disable_enable_controls()

    def update_generated_at(self):
        index = self.comboBox.currentIndex()
        if index >= 0:
            report = self.comboBox.itemData(index)
            if report and report.generated_at:
                self.generated_label.setText(
                    "Report generated at: " + report.generated_at.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                self.generated_label.setText("Report generated at: (never generated)")
        else:
            self.generated_label.setText("")

    def delete_current(self):
        index = self.comboBox.currentIndex()
        if index >= 0:
            report = self.comboBox.itemData(index)
            report.delete_instance()
            self.comboBox.removeItem(index)

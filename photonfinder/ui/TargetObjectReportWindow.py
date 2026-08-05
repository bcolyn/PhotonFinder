from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMainWindow, QTableWidget, QTableWidgetItem, QDialogButtonBox


class _NumericItem(QTableWidgetItem):
    def __lt__(self, other):
        try:
            return float(self.text()) < float(other.text())
        except ValueError:
            return super().__lt__(other)

from photonfinder import reports
from photonfinder.core import ApplicationContext
from photonfinder.models import SearchCriteria
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase
from photonfinder.ui.TelescopiusCompareDialog import TableWidgetMixin
from photonfinder.ui.generated.TargetObjectReportWindow_ui import Ui_TargetObjectReportWindow


class TargetObjectReportWindow(QMainWindow, Ui_TargetObjectReportWindow, TableWidgetMixin):

    def __init__(self, context: ApplicationContext, parent=None):
        super(TargetObjectReportWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.loader = TargetReportLoader(self.context)
        self.headers = ["Object Name", "Filter", "Telescope", "Camera", "Total Exposure", "Latest data", "Paths"]
        self.loader.on_result.connect(self.on_complete)
        from .SearchPanel import SearchPanel
        self.search_panel: SearchPanel = self.parent()
        self.search_panel.search_criteria_changed.connect(self.load_report)
        self.search_panel.mainWindow.tabs_changed.connect(self.on_tabs_changed)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save_data)
        self.on_tabs_changed()
        self.load_report()

    def on_tabs_changed(self):
        self.tabname_label.setText(self.search_panel.title)

    def load_report(self):
        self.loader.start(self.tableWidget, self.search_panel.search_criteria)

    def on_complete(self, result):
        self.tableWidget.clearContents()
        self.tableWidget.setRowCount(len(result))
        self.tableWidget.setColumnCount(len(self.headers))
        self.tableWidget.setHorizontalHeaderLabels(self.headers)

        for row, data in enumerate(result):
            for col, value in enumerate(data):
                if value:
                    item = _NumericItem(str(value)) if col == 4 else QTableWidgetItem(str(value))
                    item.setFlags(item.flags() & ~ Qt.ItemFlag.ItemIsEditable)
                    item.setTextAlignment(Qt.AlignTop)
                    self.tableWidget.setItem(row, col, item)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.resizeRowsToContents()


class TargetReportLoader(BackgroundLoaderBase):
    table: QTableWidget
    criteria: SearchCriteria
    on_result = Signal(object)

    def __init__(self, context: ApplicationContext):
        super().__init__(context)

    def start(self, table_widget: QTableWidget, criteria: SearchCriteria):
        self.table = table_widget
        self.criteria = criteria
        self.run_in_thread(self._query_data)

    def _query_data(self):
        self.on_result.emit(rows_to_table_data(reports.target_report(self.criteria)))


def rows_to_table_data(rows) -> list:
    """Flatten TargetReportRows into the 7 columns the table widget expects.

    `file_count` is deliberately dropped: the table has seven headers, so an eighth
    value would be discarded by `setColumnCount` anyway.
    """
    return [(r.object_name, r.filter, r.telescope, r.camera,
             r.total_exposure, r.last_date_obs, "\n".join(r.paths))
            for r in rows]

import copy
import logging

from PySide6.QtCore import Qt, Signal, Slot
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
        self.headers = ["Object Name", "Filter", "Telescope", "Camera", "Total Exposure", "Nights", "Latest data", "Paths"]
        self.loader.on_result.connect(self.on_complete)
        self.loader.on_error.connect(self.on_error)
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
        self.statusbar.clearMessage()
        # sorting must be off while filling, otherwise rows move around as items are inserted
        # and the remaining cells of a row land on other rows
        self.tableWidget.setSortingEnabled(False)
        self.tableWidget.clearContents()
        self.tableWidget.setRowCount(len(result))
        self.tableWidget.setColumnCount(len(self.headers))
        self.tableWidget.setHorizontalHeaderLabels(self.headers)

        for row, data in enumerate(result):
            for col, value in enumerate(data):
                if value:
                    item = _NumericItem(str(value)) if col in (4, 5) else QTableWidgetItem(str(value))
                    item.setFlags(item.flags() & ~ Qt.ItemFlag.ItemIsEditable)
                    item.setTextAlignment(Qt.AlignTop)
                    self.tableWidget.setItem(row, col, item)
        self.tableWidget.setSortingEnabled(True)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.resizeRowsToContents()

    def on_error(self, message: str):
        # not a message box: the report reloads on every criteria edit, so a failing query
        # would pop one up per keystroke
        self.tableWidget.clearContents()
        self.tableWidget.setRowCount(0)
        self.statusbar.showMessage(message)


class TargetReportLoader(BackgroundLoaderBase):
    """Loads the target report. Each start supersedes the previous one: only the result
    (or error) of the latest load is delivered."""
    table: QTableWidget
    on_result = Signal(object)
    on_error = Signal(str)
    _done = Signal(object, str, int)  # rows or None, error message, generation; worker -> GUI thread

    def __init__(self, context):
        super().__init__(context)
        self._generation = 0
        self._done.connect(self._on_done)

    def start(self, table_widget: QTableWidget, criteria: SearchCriteria):
        self.table = table_widget
        self._generation += 1
        self.run_in_thread(self._query_data, copy.deepcopy(criteria), self._generation)

    def _query_data(self, criteria: SearchCriteria, generation: int):
        try:
            rows = rows_to_table_data(reports.target_report(criteria))
        except Exception as e:
            logging.error(f"Error loading the target report: {e}", exc_info=True)
            self._done.emit(None, f"Could not load the target report: {e}", generation)
            return
        self._done.emit(rows, "", generation)

    @Slot(object, str, int)
    def _on_done(self, rows, error, generation):
        if generation != self._generation:
            return  # a newer load has started since
        if rows is None:
            self.on_error.emit(error)
        else:
            self.on_result.emit(rows)


def rows_to_table_data(rows) -> list:
    """Flatten TargetReportRows into the 7 columns the table widget expects.

    `file_count` is deliberately dropped: the table has seven headers, so an eighth
    value would be discarded by `setColumnCount` anyway.
    """
    return [(r.object_name, r.filter, r.telescope, r.camera,
             r.total_exposure, r.last_date_obs, "\n".join(r.paths))
            for r in rows]

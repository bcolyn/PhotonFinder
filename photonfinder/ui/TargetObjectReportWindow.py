from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMainWindow, QTableWidget, QTableWidgetItem, QDialogButtonBox

from photonfinder.core import ApplicationContext
from photonfinder.models import File, Image, SearchCriteria
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
                    item = QTableWidgetItem(str(value))
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
        from peewee import fn
        query = (File.select(Image.object_name,
                             Image.filter,
                             Image.telescope,
                             Image.camera,
                             fn.SUM(Image.exposure),
                             fn.MAX(Image.date_obs),
                             fn.RTRIM(fn.REPLACE(fn.GROUP_CONCAT(fn.DISTINCT(File.path + ":")), ":,", "\n"), ":"))
                 .join_from(File, Image))
        query = Image.apply_search_criteria(query, self.criteria)
        query = (query
                 .where(Image.object_name.is_null(False))
                 .where(Image.object_name != "")
                 .group_by(Image.object_name,
                           Image.filter,
                           Image.telescope,
                           Image.camera)
                 .order_by(fn.LOWER(Image.object_name).asc(), Image.filter.asc()))
        result = list(query.tuples())
        self.on_result.emit(result)

# SELECT
#   image.object_name,
#   image.filter,
#   image.telescope,
#   image.camera,
#   SUM(image.exposure) as total_exposure,
#   max(image.date_obs) as last_date,
#   rtrim(replace(group_concat(DISTINCT file.path||':'), ':,', char(10)), ':') AS paths
# FROM file
# JOIN image ON file.rowid = image.file_id
# WHERE image.object_name IS NOT NULL
#   AND image.object_name <> ""
# GROUP BY
#   image.object_name,
#   image.filter,
#   image.telescope,
#   image.camera
# ORDER BY
#   LOWER(image.object_name), image.filter;

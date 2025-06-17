from datetime import datetime, timedelta

from PySide6.QtWidgets import QDialog

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.generated.DateRangeDialog_ui import Ui_DateRangeDialog


class DateRangeDialog(QDialog, Ui_DateRangeDialog):

    def __init__(self, context: ApplicationContext, parent=None):
        super(DateRangeDialog, self).__init__(parent)
        self.setupUi(self)
        self.setModal(True)
        self.context = context
        self.initial_value = datetime.now()
        self.initial_from = self.initial_value - timedelta(days=1)
        self.dateTimeEditFrom.setDateTime(self.initial_from)
        self.dateTimeEditTo.setDateTime(self.initial_value)
        self.dateTimeEditFrom.dateTimeChanged.connect(self.on_from_date_time_changed)

    def on_from_date_time_changed(self):
        if self.dateTimeEditTo.dateTime() == self.initial_value:
            date_plus24h = self.dateTimeEditFrom.dateTime().addDays(1)
            self.dateTimeEditTo.setDateTime(date_plus24h)

    def get_datetime_range(self) -> (datetime, datetime):
        dt_from = self.dateTimeEditFrom.dateTime().toPython()
        dt_to = self.dateTimeEditTo.dateTime().toPython()
        return dt_from, dt_to

    def set_start_date(self, start_datetime):
        self.dateTimeEditFrom.setDateTime(start_datetime)

    def set_end_date(self, end_datetime):
        self.dateTimeEditTo.setDateTime(end_datetime)

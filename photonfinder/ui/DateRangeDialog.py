from datetime import datetime, timedelta

from PySide6.QtWidgets import QDialog

from photonfinder.core import ApplicationContext
from photonfinder.ui.generated.DateRangeDialog_ui import Ui_DateRangeDialog


class DateRangeDialog(QDialog, Ui_DateRangeDialog):
    """A from/to range in local time (Image.local_time) or in UTC (Image.date_obs). Nothing is
    converted: the range is typed in the basis chosen, and the dialog can be given a range for
    each, of which it shows the one for the current basis."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(DateRangeDialog, self).__init__(parent)
        self.setupUi(self)
        self.setModal(True)
        self.context = context
        self.initial_value = datetime.now()
        self.initial_from = self.initial_value - timedelta(days=1)
        self.dateTimeEditFrom.setDateTime(self.initial_from)
        self.dateTimeEditTo.setDateTime(self.initial_value)
        self._ranges = {}  # local (bool) -> (start, end) to show for that basis
        self.dateTimeEditFrom.dateTimeChanged.connect(self.on_from_date_time_changed)
        self.localRadio.toggled.connect(self._show_range)

    def on_from_date_time_changed(self):
        if self.dateTimeEditTo.dateTime() == self.initial_value:
            date_plus24h = self.dateTimeEditFrom.dateTime().addDays(1)
            self.dateTimeEditTo.setDateTime(date_plus24h)

    def set_range(self, local: bool, start: datetime | None, end: datetime | None):
        """The range to show when the basis is `local` (True) or UTC (False). A missing end is
        the start."""
        if start is None and end is None:
            return
        self._ranges[local] = (start or end, end or start)
        if self.is_local() == local:
            self._show_range()

    def set_local(self, local: bool):
        (self.localRadio if local else self.utcRadio).setChecked(True)
        self._show_range()

    def is_local(self) -> bool:
        return self.localRadio.isChecked()

    def _show_range(self):
        start_end = self._ranges.get(self.is_local())
        if start_end:
            self.dateTimeEditFrom.setDateTime(start_end[0])
            self.dateTimeEditTo.setDateTime(start_end[1])

    def get_datetime_range(self) -> (datetime, datetime):
        dt_from = self.dateTimeEditFrom.dateTime().toPython()
        dt_to = self.dateTimeEditTo.dateTime().toPython()
        return dt_from, dt_to

import csv
import html
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import List

import astropy.units as u
import requests
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QIntValidator, QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QMessageBox, QFileDialog, QDialogButtonBox, QTableWidgetItem, QApplication
from astropy.coordinates import SkyCoord, Angle

from photonfinder.core import ApplicationContext
from photonfinder.models import SearchCriteria, File, Image, LibraryRoot
from photonfinder.ui.BackgroundLoader import ProgressBackgroundTask
from photonfinder.ui.generated.TelescopiusCompareDialog_ui import Ui_TelescopiusCompareDialog


@dataclass
class TelescopiusTarget:
    name: str
    ra_hr: float
    dec: float

    def coord(self) -> SkyCoord:
        return SkyCoord(self.ra_hr, self.dec, unit=(u.hourangle, u.deg), frame='icrs')


@dataclass
class TelescopiusMatch:
    target: TelescopiusTarget
    paths: List[str]

    def display_values(self) -> List[str]:
        return [self.target.name,
                Angle(self.target.ra_hr * u.hourangle).to_string(unit=u.hourangle, sep=':', pad=True, precision=0),
                Angle(self.target.dec * u.deg).to_string(unit=u.deg, sep=':', pad=True, precision=0,
                                                         alwayssign=True),
                "\n".join(self.paths)]

    def sort_keys(self) -> list:
        """Per-column sort keys: sexagesimal RA/Dec strings don't sort correctly as text (e.g. negative Dec)."""
        return [self.target.name.lower(), self.target.ra_hr, self.target.dec, len(self.paths)]


class SortableTableWidgetItem(QTableWidgetItem):
    """Table item that sorts on the value stored under SORT_ROLE instead of its display text."""
    SORT_ROLE = Qt.ItemDataRole.UserRole

    def __lt__(self, other):
        mine, theirs = self.data(self.SORT_ROLE), other.data(self.SORT_ROLE)
        if mine is not None and theirs is not None:
            return mine < theirs
        return super().__lt__(other)


TELESCOPIUS_API_URL = "https://api.telescopius.com/v2.2"


@dataclass
class TelescopiusList:
    id: str
    name: str
    targets_count: int

    def display_name(self) -> str:
        return f"{self.name} ({self.targets_count} targets)"


class TelescopiusApiError(Exception):
    pass


def _telescopius_get(api_key: str, path: str) -> dict:
    """GET a Telescopius API endpoint, authenticated with the user's API key."""
    headers = {'Accept': 'application/json', 'Authorization': f'Key {api_key}'}
    response = requests.get(f"{TELESCOPIUS_API_URL}{path}", headers=headers, timeout=30)
    if response.status_code in (401, 403):
        raise TelescopiusApiError("Telescopius rejected the API key. Please check the key in Settings.")
    if response.status_code == 429:
        raise TelescopiusApiError("Too many requests to the Telescopius API. Please try again later.")
    response.raise_for_status()
    return response.json()


def parse_telescopius_lists(json_data: dict) -> List[TelescopiusList]:
    """Parse the response of /target-lists into a list of TelescopiusList."""
    return [TelescopiusList(str(item.get('id', '')), item.get('name', ''), int(item.get('targets_count') or 0))
            for item in json_data.get('lists', [])]


def parse_telescopius_json(json_data: dict) -> List[TelescopiusTarget]:
    """Parse the response of /target-lists/{id} into a list of TelescopiusTarget."""
    return [TelescopiusTarget(target.get('name', ''), float(target['ra_hr']), float(target['dec']))
            for target in json_data.get('targets', [])]


def fetch_telescopius_lists(api_key: str) -> List[TelescopiusList]:
    return parse_telescopius_lists(_telescopius_get(api_key, "/target-lists"))


def fetch_telescopius_targets(api_key: str, list_id: str) -> List[TelescopiusTarget]:
    return parse_telescopius_json(_telescopius_get(api_key, f"/target-lists/{list_id}"))


def enrich_telescopius_data(targets: List[TelescopiusTarget],
                            search_criteria: SearchCriteria,
                            tolerance: float) -> List['TelescopiusMatch']:
    results = []
    for target in targets:
        try:
            query = (File.select(File, Image, LibraryRoot)
                     .join_from(File, Image)
                     .join_from(File, LibraryRoot))
            full_criteria = deepcopy(search_criteria)
            full_criteria.coord_ra = str(target.coord().ra.hourangle)
            full_criteria.coord_dec = str(target.coord().dec.deg)
            full_criteria.coord_radius = tolerance
            query = Image.apply_search_criteria(query, full_criteria, None)
            files = query.execute()
            paths = set()
            for file in files:
                image = file.image
                img_coord = image.get_sky_coord()
                # check distance to target
                if img_coord.separation(target.coord()).deg < tolerance:
                    paths.add(file.root.name + ":" + file.path)

            results.append(TelescopiusMatch(target, sorted(paths)))
        except Exception as e:
            logging.error(f"Error processing target {target.name}: {e}", exc_info=True)
    return results


class TelescopiusListsTask(ProgressBackgroundTask):
    lists: List[TelescopiusList]

    def start(self, api_key: str):
        self.run_in_thread(self._load_lists, api_key)

    def _load_lists(self, api_key: str):
        try:
            self.lists = fetch_telescopius_lists(api_key)
        except Exception as e:
            logging.error(f"Error fetching Telescopius lists: {e}", exc_info=True)
            self.error.emit(f"Could not fetch your Telescopius lists:\n{e}")
            return
        self.finished.emit()


class TelescopiusCompareTask(ProgressBackgroundTask):
    results: List[TelescopiusMatch]

    def start(self, api_key: str, list_id: str, search_criteria: SearchCriteria, tolerance: float = 0.5):
        self.run_in_thread(self._fill_datagrid, api_key, list_id, search_criteria, tolerance)

    def _fill_datagrid(self, api_key: str, list_id: str, search_criteria: SearchCriteria, tolerance: float):
        try:
            targets = fetch_telescopius_targets(api_key, list_id)
        except Exception as e:
            logging.error(f"Error fetching Telescopius list {list_id}: {e}", exc_info=True)
            self.error.emit(f"Could not fetch the Telescopius list:\n{e}")
            return
        self.results = enrich_telescopius_data(targets, search_criteria, tolerance)
        self.finished.emit()

class TableWidgetMixin:
    def save_data(self):
        """Save the data from the tableWidget to a CSV or TSV file."""
        # Check if there is any data in the tableWidget
        if self.tableWidget.rowCount() == 0:
            QMessageBox.information(self, "No Data", "There is no data to save.")
            return

        # Show file save dialog with format options
        file_dialog = QFileDialog(self)
        file_dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        file_dialog.setDefaultSuffix("csv")
        file_dialog.setNameFilters([
            "Comma Separated Values (*.csv)",
            "Tab Separated Values (*.tsv)",
            "All Files (*)"
        ])
        file_dialog.setWindowTitle("Save Comparison Data")

        if file_dialog.exec() != QFileDialog.DialogCode.Accepted:
            return  # User cancelled

        file_path = file_dialog.selectedFiles()[0]
        selected_filter = file_dialog.selectedNameFilter()

        # Determine format based on selected filter or file extension
        if "Tab Separated" in selected_filter or file_path.lower().endswith('.tsv'):
            export_format = 'tsv'
        else:
            export_format = 'csv'

        try:
            self._export_table_data(file_path, export_format)
            QMessageBox.information(self, "Export Complete", f"Data successfully saved to {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to save data: {str(e)}")

    def _export_table_data(self, file_path: str, export_format: str):
        """Export the tableWidget data to a CSV or TSV file."""
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            if export_format == 'tsv':
                writer = csv.writer(f, dialect=csv.excel_tab)
            else:
                writer = csv.writer(f, dialect=csv.excel)

            # Write header row
            writer.writerow(self.headers)

            # Write data rows (rows hidden by a filter are not exported)
            for row in range(self.tableWidget.rowCount()):
                if self.tableWidget.isRowHidden(row):
                    continue
                row_data = []
                for col in range(self.tableWidget.columnCount()):
                    item = self.tableWidget.item(row, col)
                    text = item.text() if item else ""
                    text = text.replace("\n", "|")
                    row_data.append(text)
                writer.writerow(row_data)


class TelescopiusCompareDialog(QDialog, Ui_TelescopiusCompareDialog, TableWidgetMixin):
    """
    Dialog for comparing files with Telescopius data.
    """
    NAME_COLUMN = 0
    PATHS_COLUMN = 3

    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File], parent=None):
        super(TelescopiusCompareDialog, self).__init__(parent)
        self.setupUi(self)

        # Store references
        self.context = context
        self.search_criteria = search_criteria
        self.files = files
        self.api_key = self.context.settings.get_telescopius_api_key()
        self.task = TelescopiusCompareTask(self.context)
        self.lists_task = TelescopiusListsTask(self.context)
        self.headers = ["Name", "RA", "Dec", "Paths with matches"]
        self.progressBar.setVisible(False)
        # Connect signals to slots
        self._connect_signals()

        # Initialize the dialog state
        self._initialize_dialog()

    def _connect_signals(self):
        """Connect UI signals to their respective slots."""
        self.task.progress.connect(self.progressBar.setValue)
        self.task.finished.connect(self.on_complete)
        self.task.total_found.connect(self.progressBar.setMaximum)
        self.task.error.connect(self.on_error)
        self.lists_task.finished.connect(self.on_lists_loaded)
        self.lists_task.error.connect(self.on_lists_error)
        self.fetch_button.clicked.connect(self.on_start)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save_data)
        self.copy_names_button.clicked.connect(self.copy_names)
        # right-click "Copy"; the shortcut is only shown as a hint in the menu: QTableView claims Ctrl+C
        # itself (copying just the current cell), so that key is redirected to copy_selection in eventFilter
        copy_action = QAction("Copy", self.tableWidget)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_action.triggered.connect(self.copy_selection)
        self.tableWidget.addAction(copy_action)
        self.tableWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.tableWidget.installEventFilter(self)
        for radio in (self.show_both_radio, self.show_matching_radio, self.show_missing_radio):
            radio.toggled.connect(self.apply_show_filter)

    def _initialize_dialog(self):
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.copy_names_button.setEnabled(False)
        self.tolerance_edit.setValidator(QIntValidator(0, 180, self))
        self._show_applied_filters()
        self.fetch_button.setEnabled(False)
        self.lists_task.start(self.api_key)

    def _show_applied_filters(self):
        """Show the same filter summary as the originating tab's title."""
        if self.search_criteria is None or self.search_criteria.is_empty():
            text = "all files (no filters)"
        else:
            text = str(self.search_criteria)
        self.filters_label.setText(f"Searched for matches in: <b>{html.escape(text)}</b>")
        self.filters_label.setToolTip(text)

    def on_lists_loaded(self):
        self.list_combo.clear()
        for telescopius_list in self.lists_task.lists:
            self.list_combo.addItem(telescopius_list.display_name(), telescopius_list.id)
        if self.list_combo.count() == 0:
            self.list_combo.setPlaceholderText("No lists found in your Telescopius account")
        else:
            # with a placeholder set, QComboBox leaves the index at -1 instead of selecting the first item
            self.list_combo.setCurrentIndex(0)
        self.fetch_button.setEnabled(self.list_combo.count() > 0)

    def on_lists_error(self, error_message):
        QMessageBox.critical(self, "Error", error_message)
        self.reject()

    def on_error(self, error_message):
        QMessageBox.critical(self, "Error", error_message)
        self.progressBar.setVisible(False)
        self.fetch_button.setEnabled(True)

    def on_start(self):
        list_id = self.list_combo.currentData()
        if not list_id:
            return
        self.fetch_button.setEnabled(False)
        self.task.start(self.api_key, list_id, self.search_criteria, float(self.tolerance_edit.text()) / 60.0)

    def on_complete(self):
        results = self.task.results
        # sorting must be off while filling, otherwise rows move around as items are inserted
        self.tableWidget.setSortingEnabled(False)
        self.tableWidget.setRowCount(len(results))
        # set table column headers
        self.tableWidget.setHorizontalHeaderLabels(self.headers)

        for row, result in enumerate(results):
            for col, (value, sort_key) in enumerate(zip(result.display_values(), result.sort_keys())):
                item = SortableTableWidgetItem(value)
                item.setData(SortableTableWidgetItem.SORT_ROLE, sort_key)
                item.setFlags(item.flags() & ~ Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignTop)
                self.tableWidget.setItem(row, col, item)
        self.tableWidget.setSortingEnabled(True)
        self.apply_show_filter()
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        self.copy_names_button.setEnabled(True)
        self.fetch_button.setEnabled(True)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.resizeRowsToContents()

    def _row_has_matches(self, row: int) -> bool:
        item = self.tableWidget.item(row, self.PATHS_COLUMN)
        return bool(item and item.data(SortableTableWidgetItem.SORT_ROLE))

    def apply_show_filter(self):
        """Hide rows according to the Show both/matching/missing radio buttons."""
        for row in range(self.tableWidget.rowCount()):
            if self.show_matching_radio.isChecked():
                hidden = not self._row_has_matches(row)
            elif self.show_missing_radio.isChecked():
                hidden = self._row_has_matches(row)
            else:
                hidden = False
            self.tableWidget.setRowHidden(row, hidden)

    def visible_names(self) -> List[str]:
        return [self.tableWidget.item(row, self.NAME_COLUMN).text()
                for row in range(self.tableWidget.rowCount())
                if not self.tableWidget.isRowHidden(row) and self.tableWidget.item(row, self.NAME_COLUMN)]

    def selected_text(self) -> str:
        """Selected cells as text: a single cell as-is, several as tab-separated rows in display order."""
        indexes = [index for index in self.tableWidget.selectedIndexes()
                   if not self.tableWidget.isRowHidden(index.row())]
        if len(indexes) == 1:
            return indexes[0].data() or ""
        rows = {}
        for index in indexes:
            rows.setdefault(index.row(), {})[index.column()] = (index.data() or "").replace("\n", "|")
        columns = sorted({index.column() for index in indexes})
        return "\n".join("\t".join(rows[row].get(col, "") for col in columns) for row in sorted(rows))

    def eventFilter(self, watched, event):
        if (watched is self.tableWidget and event.type() == QEvent.Type.KeyPress
                and event.matches(QKeySequence.StandardKey.Copy)):
            self.copy_selection()
            return True
        return super().eventFilter(watched, event)

    def copy_selection(self):
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)

    def copy_names(self):
        QApplication.clipboard().setText("\n".join(self.visible_names()))

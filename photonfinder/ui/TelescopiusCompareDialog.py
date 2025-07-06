import csv
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import List, Tuple

import astropy.units as u
import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QDialog, QMessageBox, QFileDialog, QDialogButtonBox, QTableWidgetItem
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


def parse_telescopius_json(json_data: dict) -> List[TelescopiusTarget]:
    """
    Parse and transform the Telescopius JSON into a list of tuples (name, ra, dec).

    Args:
        json_data: The JSON data from Telescopius API

    Returns:
        List[Tuple[str, str, str]]: List of tuples containing (name, ra, dec)
    """
    targets = []

    if 'data' in json_data and 'targets' in json_data['data']:
        for target in json_data['data']['targets']:
            name = target.get('name', '')
            ra = target.get('ra_hr', '')
            dec = target.get('dec', '')
            targets.append(TelescopiusTarget(name, float(ra), float(dec)))

    return targets


def get_telescopius_json(url: str) -> dict:
    """
    Makes an HTTP(s) call to the user-provided URL and returns the JSON.
    Transforms the URL by adding '/api/' before the path.

    Args:
        url: The Telescopius URL (e.g., "https://telescopius.com/observing-lists/xxxxxxxx")

    Returns:
        dict: The JSON response from the API
    """
    # Transform URL by adding '/api/' before the path
    if '//telescopius.com/observing-lists/' in url:
        api_url = url.replace('/observing-lists/', '/api/observing-lists/')
    else:
        api_url = url

    # Make the HTTP request with the required header
    headers = {'Accept': 'application/json'}
    response = requests.get(api_url, headers=headers)
    response.raise_for_status()
    return response.json()


def enrich_telescopius_data(targets: List[TelescopiusTarget],
                            search_criteria: SearchCriteria,
                            tolerance: float) -> List[Tuple[str, str, str, str]]:
    results = []
    for target in targets:
        try:
            query = (File.select(File, Image)
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
                img_coord = SkyCoord(image.coord_ra, image.coord_dec, unit=(u.deg, u.deg), frame='icrs')
                # check distance to target
                if img_coord.separation(target.coord()).deg < tolerance:
                    paths.add(file.root.name + ":" + file.path)

            results.append((target.name,
                            Angle(target.ra_hr * u.hourangle).to_string(unit=u.hourangle, sep=':', pad=True,
                                                                        precision=0),
                            Angle(target.dec * u.deg).to_string(unit=u.deg, sep=':', pad=True, precision=0),
                            "\n".join(paths)))
        except Exception as e:
            logging.error(f"Error processing target {target.name}: {e}", exc_info=True)
    return results


class TelescopiusCompareTask(ProgressBackgroundTask):
    results: List[Tuple[str, str, str, str]]
    tolerance: float
    url: str
    search_criteria: SearchCriteria

    def start(self, url: str, search_criteria: SearchCriteria, tolerance: float = 0.5):
        self.url = url
        self.search_criteria = search_criteria
        self.tolerance = tolerance
        self.run_in_thread(self._fill_datagrid)

    def _fill_datagrid(self):
        json = get_telescopius_json(self.url)
        targets = parse_telescopius_json(json)
        self.results = enrich_telescopius_data(targets, self.search_criteria, self.tolerance)
        self.finished.emit()


class TelescopiusCompareDialog(QDialog, Ui_TelescopiusCompareDialog):
    """
    Dialog for comparing files with Telescopius data.
    """

    def __init__(self, context: ApplicationContext, search_criteria: SearchCriteria, files: List[File], parent=None):
        super(TelescopiusCompareDialog, self).__init__(parent)
        self.setupUi(self)

        # Store references
        self.context = context
        self.search_criteria = search_criteria
        self.files = files
        self.task = TelescopiusCompareTask(self.context)
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
        self.fetch_button.clicked.connect(self.on_start)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save_data)

    def _initialize_dialog(self):
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.tolerance_edit.setValidator(QIntValidator(0, 180, self))

    def on_error(self, error_message):
        QMessageBox.critical(self, "Error", error_message)
        self.reject()

    def on_start(self):
        self.fetch_button.setEnabled(False)
        self.task.start(self.url_edit.text(), self.search_criteria, float(self.tolerance_edit.text()) / 60.0)

    def on_complete(self):
        results = self.task.results
        # load the results into the datagrid
        self.tableWidget.setRowCount(len(results))
        # set table column headers
        self.tableWidget.setHorizontalHeaderLabels(self.headers)

        for row, result in enumerate(results):
            for col, value in enumerate(result):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~ Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignTop)
                self.tableWidget.setItem(row, col, item)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        self.fetch_button.setEnabled(True)
        self.tableWidget.resizeColumnsToContents()
        self.tableWidget.resizeRowsToContents()

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

            # Write data rows
            for row in range(self.tableWidget.rowCount()):
                row_data = []
                for col in range(self.tableWidget.columnCount()):
                    item = self.tableWidget.item(row, col)
                    text = item.text() if item else ""
                    text = text.replace("\n", "|")
                    row_data.append(text)
                writer.writerow(row_data)

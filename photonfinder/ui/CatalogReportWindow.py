import csv
import logging
from copy import deepcopy

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QMainWindow, QTreeWidgetItem, QMenu, QHeaderView, QFileDialog, QMessageBox

from photonfinder.core import ApplicationContext, decompress
from photonfinder.models import SearchCriteria, CatalogEntry, RootAndPath
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase
from photonfinder.ui.generated.CatalogReportWindow_ui import Ui_CatalogReportWindow

logger = logging.getLogger(__name__)

_GREEN = QBrush(QColor(0, 160, 0))
_GRAY = QBrush(QColor(128, 128, 128))

HEADERS = ["ID / Path / File", "Mag", "Size (')", "Images"]


class CatalogReportLoader(BackgroundLoaderBase):
    on_result = Signal(object)   # (List[CatalogEntry], Dict[int, List[(str, str)]])
    on_progress = Signal(int, int)

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self._catalog = ""
        self._criteria = None

    def start(self, catalog: str, criteria: SearchCriteria):
        self._catalog = catalog
        self._criteria = criteria
        self.run_in_thread(self._query_data)

    def _query_data(self):
        from astropy.io.fits import Header
        from astropy.wcs import WCS
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        from peewee import JOIN
        from photonfinder.models import File, Image, LibraryRoot, FileWCS, hp

        catalog = self._catalog
        criteria = self._criteria

        catalog_entries = list(
            CatalogEntry.select()
            .where(CatalogEntry.catalog == catalog)
            .order_by(CatalogEntry.catalog_id.cast('INTEGER'), CatalogEntry.catalog_id)
        )

        # Pass 1: fetch image center + radius without loading any WCS blob.
        meta_query = (
            Image.select(Image.coord_ra, Image.coord_dec, Image.coord_radius, Image.file)
            .join_from(Image, File)
            .join_from(File, FileWCS)
            .join_from(File, LibraryRoot)
        )
        meta_query = Image.apply_search_criteria(meta_query, criteria)

        catalog_coords = SkyCoord(
            [e.ra for e in catalog_entries], [e.dec for e in catalog_entries],
            unit=u.deg, frame='icrs',
        )
        candidate_file_ids = [
            row.file for row in meta_query.namedtuples()
            if row.coord_ra is not None
            and (
                row.coord_radius is None  # radius unknown: include and let WCS decode decide
                or float(SkyCoord(row.coord_ra, row.coord_dec, unit=u.deg).separation(catalog_coords).min().deg)
                   <= row.coord_radius
            )
        ]

        if not candidate_file_ids:
            self.on_result.emit((catalog_entries, {}))
            return

        # Pass 2: load WCS blobs only for the surviving candidates.
        wcs_query = (
            FileWCS.select(FileWCS, File, Image, LibraryRoot)
            .join_from(FileWCS, File)
            .join_from(File, LibraryRoot)
            .join_from(File, Image, JOIN.LEFT_OUTER)
            .where(File.rowid.in_(candidate_file_ids))
        )
        image_list = list(wcs_query)
        total = len(image_list)

        coverage: dict[int, list] = {}

        for i, wcs_record in enumerate(image_list):
            self.on_progress.emit(i + 1, total)
            try:
                file = wcs_record.file
                wcs_header = Header.fromstring(decompress(wcs_record.wcs).decode())
                naxis1 = wcs_header.get('NAXIS1', 0)
                naxis2 = wcs_header.get('NAXIS2', 0)
                if not naxis1 or not naxis2:
                    continue

                wcs = WCS(wcs_header)
                wcs.array_shape = (naxis2, naxis1)

                cx, cy = (naxis1 - 1) / 2.0, (naxis2 - 1) / 2.0
                center = wcs.pixel_to_world(cx, cy)
                corner_coords = wcs.pixel_to_world(
                    [0, naxis1 - 1, 0, naxis1 - 1],
                    [0, 0, naxis2 - 1, naxis2 - 1],
                )
                radius_deg = float(center.separation(corner_coords).max().deg) * 1.05

                pixels = hp.cone_search_skycoord(center, radius_deg * u.deg)
                candidates = list(
                    CatalogEntry.select()
                    .where(CatalogEntry.catalog == catalog)
                    .where(CatalogEntry.healpix.in_(pixels.tolist()))
                )

                obj_name = ""
                try:
                    img = file.image
                    if img and img.object_name:
                        obj_name = img.object_name
                except Exception:
                    pass
                filepath = file.full_filename()

                for obj in candidates:
                    coord = SkyCoord(obj.ra, obj.dec, unit=u.deg, frame='icrs')
                    if wcs.footprint_contains(coord):
                        coverage.setdefault(obj.rowid, []).append(
                            (obj_name, filepath, file.root.rowid, file.root.name, file.path, file.name)
                        )

            except Exception as e:
                logger.warning("CatalogReportLoader: skipping wcs record %s: %s", wcs_record.rowid, e)

        self.on_result.emit((catalog_entries, coverage))


_FILE_DATA_ROLE = Qt.ItemDataRole.UserRole


class CatalogReportWindow(QMainWindow, Ui_CatalogReportWindow):

    def __init__(self, context: ApplicationContext, search_panel, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.context = context
        self._catalog_entries = []
        self._coverage = {}
        self.loader = CatalogReportLoader(context)
        self.loader.on_result.connect(self.on_load_complete)
        self.loader.on_progress.connect(self.on_progress)

        from .SearchPanel import SearchPanel
        self.search_panel: SearchPanel = search_panel
        self.search_panel.search_criteria_changed.connect(self.load_report)
        self.search_panel.mainWindow.tabs_changed.connect(self.on_tabs_changed)

        self.catalogCombo.currentTextChanged.connect(self._on_catalog_changed)
        self.showCoveredCheckBox.toggled.connect(self.apply_filter)
        self.treeWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeWidget.customContextMenuRequested.connect(self._on_context_menu)
        self.saveButton.clicked.connect(self._save_report)

        self.on_tabs_changed()
        self._populate_catalog_combo()

    def on_tabs_changed(self):
        self.tabname_label.setText(self.search_panel.title)

    def _populate_catalog_combo(self):
        catalogs = list(
            CatalogEntry.select(CatalogEntry.catalog)
            .distinct()
            .order_by(CatalogEntry.catalog)
            .tuples()
        )
        self.catalogCombo.blockSignals(True)
        for (name,) in catalogs:
            self.catalogCombo.addItem(name)
        last = self.context.settings.get_last_catalog()
        idx = self.catalogCombo.findText(last)
        if idx >= 0:
            self.catalogCombo.setCurrentIndex(idx)
        self.catalogCombo.blockSignals(False)
        if self.catalogCombo.count() > 0:
            self.load_report()

    def _on_catalog_changed(self, text: str):
        if text:
            self.context.settings.set_last_catalog(text)
        self.load_report()

    def load_report(self):
        catalog = self.catalogCombo.currentText()
        if not catalog:
            return
        self.treeWidget.clear()
        self.statusbar.showMessage("Loading…")
        self.loader.start(catalog, self.search_panel.search_criteria)

    def on_progress(self, done: int, total: int):
        self.statusbar.showMessage(f"Processing images: {done} / {total}…")

    def on_load_complete(self, result):
        catalog_entries, coverage = result
        self._catalog_entries = catalog_entries
        self._coverage = coverage
        covered_count = sum(1 for rowid in coverage if coverage[rowid])
        self.statusbar.showMessage(
            f"{covered_count} of {len(catalog_entries)} objects covered by plate-solved images."
        )
        self.apply_filter()

    def apply_filter(self):
        show_only_covered = self.showCoveredCheckBox.isChecked()
        self.treeWidget.clear()
        self.treeWidget.setHeaderLabels(HEADERS)

        for entry in self._catalog_entries:
            matches = self._coverage.get(entry.rowid, [])
            covered = bool(matches)
            if show_only_covered and not covered:
                continue

            parent = QTreeWidgetItem(self.treeWidget)
            parent.setText(0, entry.catalog_id)
            parent.setText(1, f"{entry.magnitude:.1f}" if entry.magnitude else "")
            parent.setText(2, f"{entry.size:.1f}" if entry.size else "")
            count_text = str(len(matches)) if covered else ""
            parent.setText(3, count_text)
            if covered:
                parent.setForeground(3, _GREEN)
            else:
                parent.setForeground(0, _GRAY)
                parent.setForeground(1, _GRAY)
                parent.setForeground(2, _GRAY)

            folders: dict[tuple, list] = {}
            for match in matches:
                key = (match[2], match[3], match[4])  # root_id, root_label, file_dir
                folders.setdefault(key, []).append(match)

            for (root_id, root_label, file_dir), folder_matches in folders.items():
                folder_label = f"{root_label}\\{file_dir}" if file_dir else root_label
                folder_item = QTreeWidgetItem(parent)
                folder_item.setText(0, folder_label)
                folder_item.setData(0, _FILE_DATA_ROLE, (root_id, root_label, file_dir, None, None))
                for obj_name, filepath, froot_id, froot_label, ffile_dir, file_name in folder_matches:
                    file_item = QTreeWidgetItem(folder_item)
                    file_item.setText(0, file_name)
                    file_item.setData(0, _FILE_DATA_ROLE, (froot_id, froot_label, ffile_dir, file_name, obj_name))

        header = self.treeWidget.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, self.treeWidget.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

    def _on_context_menu(self, pos):
        item = self.treeWidget.itemAt(pos)
        if item is None:
            return
        data = item.data(0, _FILE_DATA_ROLE)
        if data is None:
            return
        menu = QMenu(self)
        action = menu.addAction("Open in new tab")
        if menu.exec(self.treeWidget.viewport().mapToGlobal(pos)) == action:
            root_id, root_label, file_dir, file_name, obj_name = data
            self._open_in_new_tab(root_id, root_label, file_dir, file_name, obj_name)

    def _save_report(self):
        if not self._coverage:
            QMessageBox.information(self, "No Data", "There is no coverage data to save.")
            return

        catalog = self.catalogCombo.currentText()
        file_dialog = QFileDialog(self)
        file_dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        file_dialog.setDefaultSuffix("csv")
        file_dialog.setNameFilters([
            "Comma Separated Values (*.csv)",
            "Tab Separated Values (*.tsv)",
            "All Files (*)",
        ])
        file_dialog.setWindowTitle("Save Catalog Report")
        file_dialog.selectFile(catalog)
        if file_dialog.exec() != QFileDialog.DialogCode.Accepted:
            return

        file_path = file_dialog.selectedFiles()[0]
        selected_filter = file_dialog.selectedNameFilter()
        use_tsv = "Tab Separated" in selected_filter or file_path.lower().endswith('.tsv')
        dialect = csv.excel_tab if use_tsv else csv.excel

        try:
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, dialect=dialect)
                writer.writerow(["CatalogName", "CatalogId", "FilePath"])
                for entry in self._catalog_entries:
                    for _obj_name, filepath, *_ in self._coverage.get(entry.rowid, []):
                        writer.writerow([catalog, entry.catalog_id, filepath])
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to save report: {e}")

    def _open_in_new_tab(self, root_id, root_label, file_dir, file_name, obj_name):
        criteria = deepcopy(self.search_panel.search_criteria)
        criteria.paths = [RootAndPath(root_id=root_id, root_label=root_label, path=file_dir)]
        #criteria.paths_as_prefix = False #Inherit this from the origin panel
        if file_name:
            criteria.file_name = file_name
        if obj_name:
            criteria.object_name = obj_name
        self.search_panel.mainWindow.new_search_tab(criteria)

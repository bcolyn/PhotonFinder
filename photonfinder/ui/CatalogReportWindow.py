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
    on_result = Signal(object)   # (List[CatalogEntry], Dict[int, List[(str, str)]], bool only_matching)
    on_progress = Signal(int, int)

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self._catalog = ""
        self._criteria = None
        self._only_matching = False

    def start(self, catalog: str, criteria: SearchCriteria, only_matching: bool = False):
        self._catalog = catalog
        self._criteria = criteria
        self._only_matching = only_matching
        self.run_in_thread(self._query_data)

    def _query_data(self):
        import json
        import os
        import time
        import numpy as np
        from astropy.io.fits import Header
        from astropy.wcs import WCS
        import astropy.units as u
        from photonfinder.core import hp
        from photonfinder.models import File, Image, LibraryRoot, FileWCS

        catalog = self._catalog
        criteria = self._criteria
        context = self.context
        db = context.database
        if db is None:
            return

        t0 = time.monotonic()
        only_matching = self._only_matching
        catalog_entries = []

        if not only_matching:
            # Phase 0: fetch all catalog entries for display (including unmatched ones).
            catalog_entries = list(
                CatalogEntry.select(CatalogEntry.rowid, CatalogEntry.catalog_id,
                                    CatalogEntry.magnitude, CatalogEntry.size)
                .where(CatalogEntry.catalog == catalog)
                .order_by(CatalogEntry.catalog_id.cast('INTEGER'), CatalogEntry.catalog_id)
                .namedtuples()
            )
            logger.debug("Phase 0: %d catalog entries in %.2fs", len(catalog_entries), time.monotonic() - t0)
            if not catalog_entries:
                self.on_result.emit(([], {}, False))
                return

        # Phase 1: fetch criteria-filtered images with WCS coords from the main DB.
        # No catalog interaction yet — just the image side of the M×N problem.
        t1 = time.monotonic()
        img_q = (
            Image.select(
                File.rowid.alias('file_id'),
                LibraryRoot.rowid.alias('root_id'),
                LibraryRoot.name.alias('root_name'),
                LibraryRoot.path.alias('root_path'),
                File.path.alias('file_path'),
                File.name.alias('file_name'),
                Image.object_name,
                Image.coord_ra,
                Image.coord_dec,
                Image.coord_pix256,
                Image.coord_radius,
            )
            .join_from(Image, File)
            .join_from(File, LibraryRoot)
            .join_from(File, FileWCS)   # inner join: plate-solved images only
            .where(Image.coord_ra.is_null(False))
        )
        img_q = Image.apply_search_criteria(img_q, criteria)
        image_rows = list(img_q.tuples())
        logger.debug("Phase 1: %d candidate images in %.2fs", len(image_rows), time.monotonic() - t1)

        if not image_rows:
            self.on_result.emit((catalog_entries if not only_matching else [], {}, only_matching))
            return

        # Phase 1b: group images by FoV signature — dithered subs share one WCS decode.
        # Key: (center healpix pixel, radius bucket, root_id, directory).
        t1b = time.monotonic()
        fov_groups = {}   # fov_key -> {rep_file_id, coord_ra, coord_dec, coord_radius, file_data[]}

        for row in image_rows:
            (file_id, root_id, root_name, root_path,
             file_path, file_name, object_name,
             coord_ra, coord_dec, coord_pix256, coord_radius) = row

            fov_key = (coord_pix256, round((coord_radius or 0) * 1000), root_id, file_path)
            if fov_key not in fov_groups:
                fov_groups[fov_key] = {
                    'rep_file_id': file_id,
                    'coord_ra': coord_ra,
                    'coord_dec': coord_dec,
                    'coord_radius': coord_radius or 0,
                    'file_data': [],
                }
            full_path = os.path.join(str(root_path), str(file_path), str(file_name))
            fov_groups[fov_key]['file_data'].append(
                (object_name or '', full_path, root_id, root_name, file_path, file_name)
            )

        logger.debug("Phase 1b: %d FoV groups from %d images in %.2fs",
                     len(fov_groups), len(image_rows), time.monotonic() - t1b)

        # Phase 1c: compute healpix pixel union across all FoV group representatives
        # in Python (fast numpy), then query the catalog once with that pixel set.
        # This avoids per-row Python UDF calls from inside SQLite.
        t1c = time.monotonic()
        pixel_to_fovkeys = {}   # healpix pixel -> [fov_key, …]
        for fov_key, g in fov_groups.items():
            pixels = hp.cone_search_lonlat(
                g['coord_ra'] * u.deg, g['coord_dec'] * u.deg, g['coord_radius'] * u.deg
            )
            g['pixels'] = set(int(p) for p in pixels)
            for px in g['pixels']:
                pixel_to_fovkeys.setdefault(px, []).append(fov_key)

        all_pixels = list(pixel_to_fovkeys)
        logger.debug("Phase 1c: %d unique healpix pixels across %d groups in %.2fs",
                     len(all_pixels), len(fov_groups), time.monotonic() - t1c)

        # Phase 2: single catalog query using the pixel union via json_each (no per-row UDF).
        t2 = time.monotonic()
        catalog_rows = list(db.execute_sql(
            "SELECT rowid, ra, dec, healpix FROM catalog.catalog_entry"
            " WHERE catalog = ? AND healpix IN (SELECT value FROM json_each(?))",
            [catalog, json.dumps(all_pixels)]
        ))
        logger.debug("Phase 2: %d catalog candidates in %.2fs", len(catalog_rows), time.monotonic() - t2)

        if not catalog_rows:
            self.on_result.emit((catalog_entries if not only_matching else [], {}, only_matching))
            return

        # Assign each catalog candidate to the FoV groups whose pixel cone contains it.
        for ce_rowid, ce_ra, ce_dec, ce_healpix in catalog_rows:
            for fov_key in pixel_to_fovkeys.get(ce_healpix, []):
                fov_groups[fov_key].setdefault('ce_candidates', {})[ce_rowid] = (ce_ra, ce_dec)

        # Phase 3: decode WCS once per FoV group; check precise rectangular footprint.
        t3 = time.monotonic()
        wcs_cache = {}   # rep_file_id -> WCS | None
        matches_map = {}    # ce_rowid -> list of match tuples

        groups_with_candidates = [g for g in fov_groups.values() if g.get('ce_candidates')]
        total_groups = len(groups_with_candidates)
        logger.debug("Phase 3: %d groups have catalog candidates", total_groups)

        # Batch-load all representative WCS blobs in one query (avoids N round-trips).
        rep_ids = [g['rep_file_id'] for g in groups_with_candidates]
        wcs_blobs = {r.file_id: r.wcs for r in FileWCS.select().where(FileWCS.file.in_(rep_ids))}
        logger.debug("Phase 3: fetched %d WCS blobs in %.2fs", len(wcs_blobs), time.monotonic() - t3)

        for i, group in enumerate(groups_with_candidates):
            self.on_progress.emit(i + 1, total_groups)

            rep_id = group['rep_file_id']
            if rep_id not in wcs_cache:
                raw_wcs = wcs_blobs.get(rep_id)
                if raw_wcs is None:
                    logger.warning("CatalogReportLoader: no WCS record for rep %s", rep_id)
                    wcs_cache[rep_id] = None
                else:
                    try:
                        wcs_header = Header.fromstring(decompress(raw_wcs).decode())
                        naxis1 = wcs_header.get('NAXIS1', 0)
                        naxis2 = wcs_header.get('NAXIS2', 0)
                        if naxis1 and naxis2:
                            wcs_obj = WCS(wcs_header)
                            wcs_obj.array_shape = (naxis2, naxis1)
                            wcs_cache[rep_id] = wcs_obj
                        else:
                            logger.warning("CatalogReportLoader: no NAXIS in WCS for rep %s", rep_id)
                            wcs_cache[rep_id] = None
                    except Exception as e:
                        logger.warning("CatalogReportLoader: bad WCS for rep %s: %s", rep_id, e)
                        wcs_cache[rep_id] = None

            wcs_obj = wcs_cache[rep_id]
            if wcs_obj is None:
                continue

            # Vectorized: project all candidate coords to pixel space in one call.
            candidates = list(group['ce_candidates'].items())
            world = np.array([(ce_ra, ce_dec) for _, (ce_ra, ce_dec) in candidates])
            try:
                pix = wcs_obj.all_world2pix(world, 0)
            except Exception:
                continue
            ny, nx = wcs_obj.array_shape
            inside = (pix[:, 0] >= 0) & (pix[:, 0] < nx) & (pix[:, 1] >= 0) & (pix[:, 1] < ny)
            file_data = group['file_data']
            for j, (ce_rowid, _) in enumerate(candidates):
                if inside[j]:
                    matches_map.setdefault(ce_rowid, []).extend(file_data)

        logger.debug("Phase 3: footprint checks done in %.2fs, %d catalog entries matching",
                     time.monotonic() - t3, len(matches_map))

        if only_matching:
            # Fetch only the matched entries — avoids the full 785k Phase 0 scan.
            matched_rowids = list(matches_map.keys())
            catalog_entries = list(
                CatalogEntry.select(CatalogEntry.rowid, CatalogEntry.catalog_id,
                                    CatalogEntry.magnitude, CatalogEntry.size)
                .where(CatalogEntry.rowid.in_(matched_rowids))
                .order_by(CatalogEntry.catalog_id.cast('INTEGER'), CatalogEntry.catalog_id)
                .namedtuples()
            )
            logger.debug("Phase 3b: fetched %d matching entries in %.2fs",
                         len(catalog_entries), time.monotonic() - t3)

        logger.debug("Total _query_data time: %.2fs", time.monotonic() - t0)
        self.on_result.emit((catalog_entries, matches_map, only_matching))


_FILE_DATA_ROLE = Qt.ItemDataRole.UserRole


class CatalogReportWindow(QMainWindow, Ui_CatalogReportWindow):

    def __init__(self, context: ApplicationContext, search_panel, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.context = context
        self._catalog_entries = []
        self._matches_map = {}
        self.loader = CatalogReportLoader(context)
        self.loader.on_result.connect(self.on_load_complete)
        self.loader.on_progress.connect(self.on_progress)

        from .SearchPanel import SearchPanel
        self.search_panel: SearchPanel = search_panel
        self.search_panel.search_criteria_changed.connect(self.load_report)
        self.search_panel.mainWindow.tabs_changed.connect(self.on_tabs_changed)

        self._loaded_only_matching = False
        self.catalogCombo.currentTextChanged.connect(self._on_catalog_changed)
        self.showMatchingCheckBox.toggled.connect(self._on_show_matching_toggled)
        self.treeWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeWidget.customContextMenuRequested.connect(self._on_context_menu)
        self.treeWidget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.filterEdit.textChanged.connect(self._apply_text_filter)
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
        only_matching = self.showMatchingCheckBox.isChecked()
        self.loader.start(catalog, self.search_panel.search_criteria, only_matching)

    def _on_show_matching_toggled(self, checked: bool):
        if not checked and self._loaded_only_matching:
            # We only have matching entries in memory — need a full reload to show all.
            self.load_report()
        else:
            self.apply_filter()

    def on_progress(self, done: int, total: int):
        self.statusbar.showMessage(f"Processing images: {done} / {total}…")

    def on_load_complete(self, result):
        catalog_entries, matches_map, only_matching = result
        self._catalog_entries = catalog_entries
        self._matches_map = matches_map
        self._loaded_only_matching = only_matching
        match_count = len(matches_map)
        if only_matching:
            self.statusbar.showMessage(f"{match_count} objects matching plate-solved images.")
        else:
            self.statusbar.showMessage(
                f"{match_count} of {len(catalog_entries)} objects matching plate-solved images."
            )
        self.apply_filter()

    def apply_filter(self):
        show_only_matching = self.showMatchingCheckBox.isChecked()
        self.treeWidget.clear()
        self.treeWidget.setHeaderLabels(HEADERS)

        for entry in self._catalog_entries:
            matches = self._matches_map.get(entry.rowid, [])
            has_matches = bool(matches)
            if show_only_matching and not has_matches:
                continue

            parent = QTreeWidgetItem(self.treeWidget)
            parent.setText(0, entry.catalog_id)
            parent.setText(1, f"{entry.magnitude:.1f}" if entry.magnitude else "")
            parent.setText(2, f"{entry.size:.1f}" if entry.size else "")
            count_text = str(len(matches)) if has_matches else ""
            parent.setText(3, count_text)
            if has_matches:
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
                    file_item.setFirstColumnSpanned(True)
                    file_item.setText(0, file_name)
                    file_item.setData(0, _FILE_DATA_ROLE, (froot_id, froot_label, ffile_dir, file_name, obj_name))

        header = self.treeWidget.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, self.treeWidget.columnCount()):
            header.resizeSectionToContents(col)
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)

        self._apply_text_filter(self.filterEdit.text())

    def _apply_text_filter(self, text: str):
        needle = text.strip().lower()
        root = self.treeWidget.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            item.setHidden(bool(needle) and needle not in item.text(0).lower())

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
        if not self._matches_map:
            QMessageBox.information(self, "No Data", "There is no match data to save.")
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
                    for _obj_name, filepath, *_ in self._matches_map.get(entry.rowid, []):
                        writer.writerow([catalog, entry.catalog_id, filepath])
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to save report: {e}")

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, _FILE_DATA_ROLE)
        if data is None:
            return
        root_id, root_label, file_dir, file_name, obj_name = data
        if file_name is None:
            return
        from photonfinder.models import File, FileWCS
        file = File.get_or_none((File.root == root_id) & (File.path == file_dir) & (File.name == file_name))
        if file is None:
            return
        file.has_wcs = FileWCS.select().where(FileWCS.file == file).exists()
        catalog = self.catalogCombo.currentText()
        catalog_entry_node = item.parent().parent() if item.parent() else None
        catalog_entry_id = catalog_entry_node.text(0) if catalog_entry_node else None
        self.search_panel.mainWindow.view_image(
            file, annotate=True,
            annotation_catalog=catalog, annotation_catalog_id=catalog_entry_id,
        )

    def _open_in_new_tab(self, root_id, root_label, file_dir, file_name, obj_name):
        criteria = deepcopy(self.search_panel.search_criteria)
        criteria.paths = [RootAndPath(root_id=root_id, root_label=root_label, path=file_dir)]
        #criteria.paths_as_prefix = False #Inherit this from the origin panel
        if file_name:
            criteria.file_name = file_name
        if obj_name:
            criteria.object_name = obj_name
        self.search_panel.mainWindow.new_search_tab(criteria)

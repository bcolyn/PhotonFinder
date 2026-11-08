import csv
import logging
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Signal, Qt, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QMainWindow, QTreeWidgetItem, QMenu, QHeaderView, QFileDialog, QMessageBox,
                               QInputDialog, QLineEdit)

from photonfinder import custom_catalog, reports
from photonfinder.core import ApplicationContext, library_busy_message
from photonfinder.custom_catalog import CatalogImportError
from photonfinder.models import SearchCriteria, CatalogEntry, RootAndPath
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase
from photonfinder.ui.generated.CatalogReportWindow_ui import Ui_CatalogReportWindow

_GREEN = QBrush(QColor(0, 160, 0))
_GRAY = QBrush(QColor(128, 128, 128))

HEADERS = ["ID / Path / File", "Mag", "Size (')", "Images"]


class CatalogReportLoader(BackgroundLoaderBase):
    """Loads a catalog report. Each start supersedes the previous one: progress, results and
    errors of an older load are dropped, so switching catalogs mid-load can't show the old one."""
    on_result = Signal(object)   # (list[CatalogReportEntry], dict[int, list[MatchedFile]], bool only_matching)
    on_progress = Signal(int, int)
    on_error = Signal(str)
    _done = Signal(object, str, int)   # result or None, error, generation; worker -> GUI thread
    _progress = Signal(int, int, int)  # done, total, generation

    def __init__(self, context: ApplicationContext):
        super().__init__(context)
        self._generation = 0
        self._done.connect(self._on_done)
        self._progress.connect(self._on_progress)

    def start(self, catalog: str, criteria: SearchCriteria, only_matching: bool = False):
        self._generation += 1
        self.run_in_thread(self._query_data, catalog, deepcopy(criteria), only_matching, self._generation)

    def _query_data(self, catalog, criteria, only_matching, generation):
        try:
            result = reports.catalog_report(
                self.context, catalog, criteria, only_matching,
                progress=lambda done, total: self._progress.emit(done, total, generation))
        except Exception as e:
            logging.error(f"Error loading the {catalog} catalog report: {e}", exc_info=True)
            self._done.emit(None, f"Could not load the {catalog} report: {e}", generation)
            return
        self._done.emit((result.entries, result.matches, result.only_matching), "", generation)

    @Slot(int, int, int)
    def _on_progress(self, done, total, generation):
        if generation == self._generation:
            self.on_progress.emit(done, total)

    @Slot(object, str, int)
    def _on_done(self, result, error, generation):
        if generation != self._generation:
            return  # a newer load has started since
        if result is None:
            self.on_error.emit(error)
        else:
            self.on_result.emit(result)


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
        self.loader.on_error.connect(self.statusbar.showMessage)

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

        custom_menu = QMenu(self.customButton)
        custom_menu.addAction("Import CSV…", self._import_custom_catalog)
        self._remove_action = custom_menu.addAction("Remove", self._remove_custom_catalog)
        custom_menu.aboutToShow.connect(self._update_custom_menu)
        self.customButton.setMenu(custom_menu)

        self.on_tabs_changed()
        self._populate_catalog_combo()

    def on_tabs_changed(self):
        self.tabname_label.setText(self.search_panel.title)

    def _populate_catalog_combo(self, select: str | None = None):
        catalogs = list(
            CatalogEntry.select(CatalogEntry.catalog)
            .distinct()
            .order_by(CatalogEntry.catalog)
            .tuples()
        )
        self.catalogCombo.blockSignals(True)
        self.catalogCombo.clear()
        for (name,) in catalogs:
            self.catalogCombo.addItem(name)
        if select:
            self.context.settings.set_last_catalog(select)
        last = self.context.settings.get_last_catalog()
        idx = self.catalogCombo.findText(last)
        if idx >= 0:
            self.catalogCombo.setCurrentIndex(idx)
        self.catalogCombo.blockSignals(False)
        if self.catalogCombo.count() > 0:
            self.load_report()

    def _update_custom_menu(self):
        current = self.catalogCombo.currentText()
        is_custom = bool(current) and custom_catalog.is_custom_catalog(current)
        self._remove_action.setText(f"Remove \"{current}\"…" if is_custom else "Remove")
        self._remove_action.setEnabled(is_custom)

    def _import_custom_catalog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Custom Catalog", "",
                                              "CSV files (*.csv *.txt);;All files (*)")
        if not path:
            return
        try:
            parsed = custom_catalog.parse_file(path)
        except (CatalogImportError, OSError) as e:
            QMessageBox.warning(self, "Import Custom Catalog", f"Could not import {path}:\n{e}")
            return

        name = Path(path).stem
        while True:
            name, ok = QInputDialog.getText(self, "Import Custom Catalog", "Catalog name:",
                                            QLineEdit.EchoMode.Normal, name)
            name = name.strip()
            if not ok:
                return
            if not name:
                continue
            if custom_catalog.is_builtin_name(self.context, name):
                QMessageBox.warning(self, "Import Custom Catalog",
                                    f"'{name}' is the name of a built-in catalog. Choose another name.")
                continue
            if custom_catalog.is_custom_catalog(name):
                answer = QMessageBox.question(self, "Import Custom Catalog", f"Replace '{name}'?")
                if answer != QMessageBox.StandardButton.Yes:
                    continue
            break

        try:
            stored = custom_catalog.import_catalog(self.context, name, parsed)
        except CatalogImportError as e:
            QMessageBox.warning(self, "Import Custom Catalog", str(e))
            return
        if not stored:
            QMessageBox.warning(self, "Import Custom Catalog", self._busy_message())
            return
        QMessageBox.information(self, "Import Custom Catalog", f"'{name}': {parsed.summary()}")
        self._populate_catalog_combo(select=name)

    def _remove_custom_catalog(self):
        name = self.catalogCombo.currentText()
        if not name or not custom_catalog.is_custom_catalog(name):
            return
        answer = QMessageBox.question(self, "Remove Custom Catalog",
                                      f"Remove the custom catalog '{name}' from this library?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not custom_catalog.remove_catalog(self.context, name):
            QMessageBox.warning(self, "Remove Custom Catalog", self._busy_message())
            return
        self._populate_catalog_combo()

    def _busy_message(self) -> str:
        return library_busy_message(self.context.library_write_lock.busy_with() or "Another job")

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
            self.statusbar.showMessage(f"{match_count} objects matching plate-solved images and their groups.")
        else:
            self.statusbar.showMessage(
                f"{match_count} of {len(catalog_entries)} objects matching plate-solved images and their groups."
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
                key = (match.root_id, match.root_name, match.file_dir)
                folders.setdefault(key, []).append(match)

            for (root_id, root_label, file_dir), folder_matches in folders.items():
                folder_label = f"{root_label}\\{file_dir}" if file_dir else root_label
                folder_item = QTreeWidgetItem(parent)
                folder_item.setText(0, folder_label)
                folder_item.setData(0, _FILE_DATA_ROLE, (root_id, root_label, file_dir, None, None))
                for m in folder_matches:
                    file_item = QTreeWidgetItem(folder_item)
                    file_item.setFirstColumnSpanned(True)
                    file_item.setText(0, m.file_name)
                    file_item.setData(0, _FILE_DATA_ROLE,
                                      (m.root_id, m.root_name, m.file_dir, m.file_name, m.object_name))
                    if not m.solved:
                        # Counted through its group's solved frame; it has no solution of its own.
                        font = file_item.font(0)
                        font.setItalic(True)
                        file_item.setFont(0, font)
                        file_item.setForeground(0, _GRAY)
                        file_item.setToolTip(0, f"Not plate-solved itself: covered by the solution of {m.solution_file}")

        header = self.treeWidget.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, self.treeWidget.columnCount()):
            self.treeWidget.resizeColumnToContents(col)
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
        root_id, _root_label, file_dir, file_name, _obj_name = data
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

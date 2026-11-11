from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QPixmap, Qt, QPainter, QPen, QAction, QColor
from PySide6.QtSvg import QSvgRenderer
from photonfinder.formatting import (  # pylint: disable=unused-import  # re-exported for the GUI
    _format_date, _format_dec, _format_file_size, _format_ra, _format_timestamp,
)
from photonfinder.library_query import RESULT_COLUMNS
from PySide6.QtWidgets import QStyle, QTableView, QMenu, QInputDialog, QMessageBox, QDialog, QVBoxLayout, QListWidget, QPushButton, QDialogButtonBox, QHBoxLayout


def create_colored_svg_icon(svg_path: str, size: QSize, color, add_slash=False) -> QIcon:
    renderer = QSvgRenderer(svg_path)
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color)

    if add_slash:
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        pen = QPen(QColor(128, 128, 128), max(1, size.width() // 8))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(pixmap.rect().topLeft(), pixmap.rect().bottomRight())

    painter.end()

    return QIcon(pixmap)


def ensure_header_widths(table_view, extra_padding=12):
    from PySide6.QtGui import QFontMetrics

    header = table_view.horizontalHeader()
    font = header.font()
    fm = QFontMetrics(font)

    for col in range(table_view.model().columnCount()):
        # Get header text
        text = table_view.model().headerData(col, Qt.Horizontal, Qt.DisplayRole)
        text_width = fm.horizontalAdvance(str(text))

        # Add extra space for sort indicator
        sort_space = header.style().pixelMetric(QStyle.PixelMetric.PM_HeaderMargin) + 20

        total_needed = text_width + sort_space + extra_padding

        current_width = header.sectionSize(col)
        if current_width < total_needed:
            header.resizeSection(col, total_needed)


# Columns added after layouts were first saved, which a layout shows only when it says so with
# a "+<label>" token: a layout saved before such a column existed lists only the columns it
# hid, and must not start showing the new one.
_OPT_IN_COLUMNS = {"Layer"}

# Columns shown by the search's mode, not by the user's layout: what a row of the grouped grid
# stands for. Presets and saved layouts leave them alone, and the column menu doesn't list them.
_MODE_COLUMNS = {"Frames", "Integration"}

# The labels a layout can show or hide: every column but the mode columns.
_LAYOUT_LABELS = tuple(column.label for column in RESULT_COLUMNS if column.label not in _MODE_COLUMNS)


def _showing_only(*labels: str) -> set[str]:
    """The hidden-column tokens of a layout that shows `labels` (and the mode columns) only."""
    unknown = set(labels) - set(_LAYOUT_LABELS)
    assert not unknown, f"unknown column labels: {unknown}"
    return ({label for label in _LAYOUT_LABELS if label not in labels}
            | {f"+{label}" for label in labels if label in _OPT_IN_COLUMNS})


# Hidden columns per preset, each given by the columns it shows; the opt-in columns are hidden
# unless listed as "+<label>". Standard (the default of a new tab) and Full are sized by how much
# they show; the others ("perspectives") show the columns for one task.
_BUILTIN_PRESETS = {
    "Standard": _showing_only(
        "File name", "Type", "Filter", "Exposure", "Gain", "Offset", "Binning", "Set Temp",
        "Camera", "Telescope", "Object", "Observation Date", "Path", "Projects"),
    "Full": _showing_only(*_LAYOUT_LABELS),
    "File management": _showing_only(
        "File name", "Path", "Size", "Modified", "Type", "Night",
        "Projects", "Layer"),
    "Quality assessment": _showing_only(
        "File name", "Object", "Filter", "Exposure", "Observation Date",
        "Bg Median", "Bg RMS", "Stars", "FWHM", "Elongation", "Diagnosis"),
    "Astrometry": _showing_only(
        "File name", "Object", "RA", "DEC", "Solved", "Telescope", "Camera"),
    "Acquisition": _showing_only(
        "File name", "Type", "Filter", "Exposure", "Gain", "Offset", "Binning", "Set Temp",
        "Camera", "Telescope", "Object", "Observation Date", "Night"),
}


class ColumnVisibilityController:
    def __init__(self, table_view: QTableView, context=None):
        self.table_view = table_view
        self.model = table_view.model()
        self.header = table_view.horizontalHeader()
        self.context = context  # optional; enables preset support when provided

        self.header.setContextMenuPolicy(Qt.CustomContextMenu)
        self.header.customContextMenuRequested.connect(
            lambda pos: self.show_menu(self.header.mapToGlobal(pos))
        )

    def show_menu(self, global_pos):
        menu = QMenu("Select Columns")
        self.build_menu(menu)
        menu.exec(global_pos)

    def build_menu(self, menu: QMenu) -> QMenu:
        if not self.model:
            return

        if self.context is not None:
            self._build_presets_section(menu)
            menu.addSeparator()

        for col in range(self.model.columnCount()):
            header = self.model.headerData(col, Qt.Horizontal)
            if header in _MODE_COLUMNS:
                continue
            action = QAction(header, menu)
            action.setCheckable(True)
            action.setChecked(not self.table_view.isColumnHidden(col))
            action.toggled.connect(lambda checked, c=col: self.table_view.setColumnHidden(c, not checked))
            menu.addAction(action)

        return menu

    def _build_presets_section(self, menu: QMenu):
        user_presets = self.context.get_column_presets()

        for name, hidden_set in _BUILTIN_PRESETS.items():
            action = QAction(name, menu)
            action.triggered.connect(lambda _, h=hidden_set: self.load_visibility(",".join(h)))
            menu.addAction(action)

        if user_presets:
            menu.addSeparator()
            for name, hidden_csv in user_presets.items():
                action = QAction(name, menu)
                action.triggered.connect(lambda _, csv=hidden_csv: self.load_visibility(csv))
                menu.addAction(action)

        menu.addSeparator()
        save_action = QAction("Save as Preset...", menu)
        save_action.triggered.connect(self._save_preset_dialog)
        menu.addAction(save_action)

        manage_action = QAction("Manage Presets...", menu)
        manage_action.triggered.connect(self._manage_presets_dialog)
        manage_action.setEnabled(bool(user_presets))
        menu.addAction(manage_action)

    def _save_preset_dialog(self):
        name, ok = QInputDialog.getText(
            self.table_view, "Save Preset", "Preset name:"
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in _BUILTIN_PRESETS:
            QMessageBox.warning(self.table_view, "Save Preset",
                                f'"{name}" is a built-in preset and cannot be overwritten.')
            return
        presets = self.context.get_column_presets()
        presets[name] = self.save_visibility()
        self.context.set_column_presets(presets)

    def _manage_presets_dialog(self):
        presets = self.context.get_column_presets()
        if not presets:
            return

        dlg = QDialog(self.table_view)
        dlg.setWindowTitle("Manage Presets")
        layout = QVBoxLayout(dlg)

        list_widget = QListWidget()
        list_widget.addItems(presets.keys())
        layout.addWidget(list_widget)

        btn_layout = QHBoxLayout()
        delete_btn = QPushButton("Delete")
        delete_btn.setEnabled(False)
        btn_layout.addWidget(delete_btn)
        btn_layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        btn_layout.addWidget(buttons)
        layout.addLayout(btn_layout)

        list_widget.itemSelectionChanged.connect(
            lambda: delete_btn.setEnabled(bool(list_widget.selectedItems()))
        )

        def delete_selected():
            item = list_widget.currentItem()
            if not item:
                return
            name = item.text()
            presets.pop(name, None)
            self.context.set_column_presets(presets)
            list_widget.takeItem(list_widget.row(item))
            delete_btn.setEnabled(False)

        delete_btn.clicked.connect(delete_selected)
        buttons.rejected.connect(dlg.accept)
        dlg.exec()

    def save_visibility(self) -> str:
        """The hidden columns' labels, and "+<label>" for each opt-in column shown."""
        tokens = []
        for col in range(self.model.columnCount()):
            header = str(self.model.headerData(col, Qt.Horizontal))
            if header in _MODE_COLUMNS:
                continue
            if self.table_view.isColumnHidden(col):
                tokens.append(header)
            elif header in _OPT_IN_COLUMNS:
                tokens.append(f"+{header}")
        return ",".join(tokens)

    def load_visibility(self, csv_string: str):
        tokens = set(h.strip() for h in csv_string.split(",") if h.strip())
        for col in range(self.model.columnCount()):
            header = str(self.model.headerData(col, Qt.Horizontal))
            if header in _MODE_COLUMNS:
                continue
            hidden = header in tokens or (header in _OPT_IN_COLUMNS and f"+{header}" not in tokens)
            self.table_view.setColumnHidden(col, hidden)


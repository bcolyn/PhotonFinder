import logging
from datetime import datetime

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QPixmap, Qt, QPainter, QAction
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QStyle, QTableView, QMenu, QInputDialog, QMessageBox, QDialog, QVBoxLayout, QListWidget, QPushButton, QDialogButtonBox, QHBoxLayout


def _format_file_size(size_bytes):
    """Format file size from bytes to human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _format_date(value: datetime):
    if not value:
        return ""
    try:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as ex:
        logging.exception(f"Error formatting date {ex}", exc_info=ex)
        return None


def _format_ra(ra_deg: float):
    if not ra_deg:
        return ""
    """Format RA from decimal degrees to string format."""
    total_hours = ra_deg / 15.0
    hours = int(total_hours)
    minutes = int((total_hours - hours) * 60)
    seconds = int(((total_hours - hours) * 60 - minutes) * 60)
    return f"{hours:02d}h{minutes:02d}'{seconds:02d}\""


def _format_dec(dec_deg: float):
    if not dec_deg:
        return ""
    """Format DEC from decimal degrees to string format."""
    sign = "+" if dec_deg >= 0 else "-"
    abs_deg = abs(dec_deg)
    degrees = int(abs_deg)
    minutes = int((abs_deg - degrees) * 60)
    seconds = int(((abs_deg - degrees) * 60 - minutes) * 60)

    return f"{sign}{degrees:02d}:{minutes:02d}:{seconds:02d}"


def _format_timestamp(timestamp_ms: int):
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    date_str = _format_date(dt)
    return date_str

def coerce_value(value: str):
    """
    Parse a string value and return it as int, float, or str.

    Attempts to convert the value to int first, then float,
    otherwise returns the original string.

    Args:
        value: String value to parse

    Returns:
        The value as int, float, or str depending on what conversion succeeds
    """
    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        pass

    return value


def create_colored_svg_icon(svg_path: str, size: QSize, color) -> QIcon:
    renderer = QSvgRenderer(svg_path)
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color)
    painter.end()

    return QIcon(pixmap)


def ensure_header_widths(table_view, extra_padding=12):
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtCore import Qt

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


_BUILTIN_PRESETS = {
    "Essentials": {"Gain", "Offset", "Binning", "Set Temp", "Camera", "Telescope",
                   "Size", "Modified", "RA", "DEC", "Solved", "Paths"},
    "Standard": {"Size", "Modified", "RA", "DEC", "Solved"},
    "Full": set(),
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
        hidden_columns = []
        for col in range(self.model.columnCount()):
            if self.table_view.isColumnHidden(col):
                header = str(self.model.headerData(col, Qt.Horizontal))
                hidden_columns.append(header)
        return ",".join(hidden_columns)

    def load_visibility(self, csv_string: str):
        hidden_headers = set(h.strip() for h in csv_string.split(",") if h.strip())
        for col in range(self.model.columnCount()):
            header = str(self.model.headerData(col, Qt.Horizontal))
            self.table_view.setColumnHidden(col, header in hidden_headers)


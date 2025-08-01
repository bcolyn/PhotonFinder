import logging
from datetime import datetime

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QPixmap, Qt, QPainter, QAction
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QStyle, QTableView, QMenu


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


class ColumnVisibilityController:
    def __init__(self, table_view: QTableView):
        self.table_view = table_view
        self.model = table_view.model()
        self.header = table_view.horizontalHeader()

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

        for col in range(self.model.columnCount()):
            header = self.model.headerData(col, Qt.Horizontal)
            action = QAction(header, menu)
            action.setCheckable(True)
            action.setChecked(not self.table_view.isColumnHidden(col))
            action.toggled.connect(lambda checked, c=col: self.table_view.setColumnHidden(c, not checked))
            menu.addAction(action)

        return menu

    def save_visibility(self) -> str:
        """Return a comma-separated string of hidden column headers."""
        hidden_columns = []
        for col in range(self.model.columnCount()):
            if self.table_view.isColumnHidden(col):
                header = str(self.model.headerData(col, Qt.Horizontal))
                hidden_columns.append(header)
        return ",".join(hidden_columns)

    def load_visibility(self, csv_string: str):
        """Hide columns listed in the comma-separated string; show all others."""
        hidden_headers = set(h.strip() for h in csv_string.split(",") if h.strip())
        for col in range(self.model.columnCount()):
            header = str(self.model.headerData(col, Qt.Horizontal))
            self.table_view.setColumnHidden(col, header in hidden_headers)


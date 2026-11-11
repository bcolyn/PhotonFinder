"""The tile view of the search results: a thumbnail per search row (one per image), over the
same model and selection as the table, so selecting, opening and acting on files work alike.

Thumbnails (see `photonfinder.thumbnails`) are made in the background, only for tiles that get
painted; a tile scrolled out of view before its turn is dropped from the queue. A tile shows a
placeholder until its thumbnail is ready, and an error placeholder for a file that can't be
read, which is not tried again this session.
"""
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable

from PySide6.QtCore import (QEvent, QObject, QPersistentModelIndex, QRect, QRunnable, QSize, Qt, QThreadPool,
                            QTimer, Signal, Slot)
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QToolTip)

from photonfinder.thumbnails import THUMBNAIL_SIZE, ThumbnailCache

logger = logging.getLogger(__name__)

WORKERS = 3
MEMORY_ENTRIES = 1500  # thumbnails kept as pixmaps: ~100 KB each


class ThumbnailLoader(QObject):
    """Makes thumbnails in a small thread pool of its own and keeps the latest as pixmaps.

    A thumbnail is asked for by a key that tells the image and its version apart (the path,
    the hdu, and the file's modification time and size as the library knows them), so a file
    that changes is asked for anew. Shared by all tabs; only the current one is visible."""

    thumbnail_ready = Signal(object)  # key
    _finished = Signal(object, object)  # key, QImage or None; worker thread -> GUI thread

    FAILED = object()
    """What `pixmap` returns for an image that could not be read."""

    def __init__(self, cache_folder, parent=None):
        super().__init__(parent)
        self.cache = ThumbnailCache(cache_folder)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(WORKERS)
        self._lock = threading.Lock()
        self._pending: OrderedDict[Hashable, tuple[str, int | None, str | None, object]] = OrderedDict()
        self._in_flight: set = set()
        self._workers = 0
        self._pixmaps: OrderedDict[Hashable, QPixmap] = OrderedDict()
        self._failed: set = set()
        self._finished.connect(self._on_finished)

    def pixmap(self, key):
        """The thumbnail, FAILED, or None when it isn't made yet (see `request`)."""
        if key in self._failed:
            return self.FAILED
        pixmap = self._pixmaps.get(key)
        if pixmap is not None:
            self._pixmaps.move_to_end(key)
        return pixmap

    def request(self, key, path: str, hdu: int | None, token=None, image_type: str | None = None):
        """Queue the thumbnail of one image. *token* is handed to `retain`'s test; *image_type*
        is the library's, which decides the stretch (see `image_processing.should_stretch`)."""
        if key in self._failed or key in self._pixmaps:
            return
        with self._lock:
            if key in self._in_flight or key in self._pending:
                return
            self._pending[key] = (path, hdu, image_type, token)
            start = self._workers < WORKERS
            if start:
                self._workers += 1
        if start:
            self.pool.start(_Worker(self))

    def retain(self, keep: Callable[[object], bool]):
        """Drop the queued requests whose token *keep* rejects: tiles no longer in view."""
        with self._lock:
            for key in [key for key, (_, _, _, token) in self._pending.items() if not keep(token)]:
                del self._pending[key]

    def clear(self) -> tuple[int, int]:
        """Forget every thumbnail, on disk and in memory, failures too: the tiles in view are
        made again. Returns (thumbnails deleted, bytes freed) from the disk cache."""
        with self._lock:
            self._pending.clear()
        self._pixmaps.clear()
        self._failed.clear()
        cleared = self.cache.clear()
        self.thumbnail_ready.emit(None)  # repaint the tiles in view
        return cleared

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending) + len(self._in_flight)

    def shutdown(self, timeout_ms: int = 5000):
        """Drop the queue and wait for the thumbnails being made."""
        with self._lock:
            self._pending.clear()
        if not self.pool.waitForDone(timeout_ms):
            logger.warning("Thumbnail workers did not stop in time")

    def _next(self):
        """A worker's next request, or None, and then that worker ends."""
        with self._lock:
            if not self._pending:
                self._workers -= 1
                return None
            key, (path, hdu, image_type, _) = self._pending.popitem(last=False)
            self._in_flight.add(key)
            return key, path, hdu, image_type

    def _make(self, key, path, hdu, image_type=None):
        image = None
        try:
            jpeg = self.cache.thumbnail(path, hdu, image_type)
            image = QImage.fromData(jpeg)
            if image.isNull():
                image = None
                logger.warning("Thumbnail of %s (hdu %s) could not be decoded", path, hdu)
        except Exception as e:
            logger.info("No thumbnail for %s (hdu %s): %s", path, hdu, e)
        self._finished.emit(key, image)

    @Slot(object, object)
    def _on_finished(self, key, image):
        with self._lock:
            self._in_flight.discard(key)
        if image is None:
            self._failed.add(key)
        else:
            self._pixmaps[key] = QPixmap.fromImage(image)
            while len(self._pixmaps) > MEMORY_ENTRIES:
                self._pixmaps.popitem(last=False)
        self.thumbnail_ready.emit(key)


class _Worker(QRunnable):
    def __init__(self, loader: ThumbnailLoader):
        super().__init__()
        self.loader = loader

    def run(self):
        while (job := self.loader._next()) is not None:  # pylint: disable=protected-access
            self.loader._make(*job)  # pylint: disable=protected-access


def _placeholder(error: bool) -> QPixmap:
    pixmap = QPixmap(THUMBNAIL_SIZE, THUMBNAIL_SIZE * 2 // 3)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setPen(QPen(QColor(128, 128, 128, 160), 1, Qt.PenStyle.DashLine))
        painter.drawRect(pixmap.rect().adjusted(0, 0, -1, -1))
        if error:
            from PySide6.QtWidgets import QApplication
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
            side = 48
            icon.paint(painter, QRect((pixmap.width() - side) // 2, (pixmap.height() - side) // 2, side, side))
    finally:
        painter.end()
    return pixmap


class ThumbnailDelegate(QStyledItemDelegate):
    """Draws a tile: the thumbnail and the file name, elided in the middle (names usually look
    like Object-Timestamp-Filter-Frame, so both ends matter)."""

    PADDING = 8

    def __init__(self, view: 'ThumbnailView'):
        super().__init__(view)
        self.view = view
        self._loading_icon = QIcon(_placeholder(error=False))
        self._error_icon = QIcon(_placeholder(error=True))

    def tile_size(self) -> QSize:
        text_height = self.view.fontMetrics().height()
        return QSize(THUMBNAIL_SIZE + 2 * self.PADDING, THUMBNAIL_SIZE + text_height + 3 * self.PADDING)

    def sizeHint(self, option, index):
        return self.tile_size()

    def caption(self, index) -> str:
        """The file name, elided in the middle to the tile's width."""
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        return self.view.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, THUMBNAIL_SIZE)

    def initStyleOption(self, option: QStyleOptionViewItem, index):
        super().initStyleOption(option, index)
        pixmap = self.view.thumbnail_for(index)
        if pixmap is ThumbnailLoader.FAILED:
            option.icon = self._error_icon
        elif pixmap is None:
            option.icon = self._loading_icon
        else:
            option.icon = QIcon(pixmap)
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
        option.decorationSize = QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        option.decorationPosition = QStyleOptionViewItem.Position.Top
        option.decorationAlignment = Qt.AlignmentFlag.AlignCenter
        option.displayAlignment = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        option.textElideMode = Qt.TextElideMode.ElideMiddle
        option.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText
        option.text = self.caption(index)

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.Type.ToolTip and index.isValid():
            QToolTip.showText(event.globalPos(), self.view.tooltip_for(index), view)
            return True
        return super().helpEvent(event, view, option, index)


class ThumbnailView(QListView):
    """Tiles over a search panel's model. *image_of(index)* gives a tile's
    (key, path, hdu, image_type), or None; *tooltip_of(index)* its tooltip text."""

    def __init__(self, loader: ThumbnailLoader, image_of: Callable, tooltip_of: Callable, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.image_of = image_of
        self.tooltip_of = tooltip_of
        self.setObjectName("thumbnailView")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSpacing(2)
        self.delegate = ThumbnailDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setGridSize(self.delegate.tile_size() + QSize(4, 4))
        self.verticalScrollBar().setSingleStep(THUMBNAIL_SIZE // 4)

        self._keys_shown: set = set()
        loader.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._prune_timer = QTimer(self)
        self._prune_timer.setSingleShot(True)
        self._prune_timer.setInterval(100)
        self._prune_timer.timeout.connect(self.drop_hidden_requests)
        self.verticalScrollBar().valueChanged.connect(lambda _: self._prune_timer.start())

    def thumbnail_for(self, index):
        """The tile's thumbnail, FAILED, or None, in which case it is asked for."""
        image = self.image_of(index)
        if image is None:
            return ThumbnailLoader.FAILED
        key, path, hdu, image_type = image
        self._keys_shown.add(key)
        pixmap = self.loader.pixmap(key)
        if pixmap is None:
            self.loader.request(key, path, hdu, QPersistentModelIndex(index), image_type)
        return pixmap

    def tooltip_for(self, index) -> str:
        return self.tooltip_of(index)

    def is_in_view(self, index) -> bool:
        return (self.isVisible() and isinstance(index, QPersistentModelIndex) and index.isValid()
                and index.model() is self.model()
                and self.visualRect(self.model().index(index.row(), index.column())).intersects(self.viewport().rect()))

    @Slot()
    def drop_hidden_requests(self):
        """Forget the queued thumbnails of tiles that have been scrolled out of view."""
        self.loader.retain(self.is_in_view)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.loader.retain(lambda token: False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._prune_timer.start()

    @Slot(object)
    def _on_thumbnail_ready(self, key):
        if (key is None or key in self._keys_shown) and self.isVisible():
            self.viewport().update()

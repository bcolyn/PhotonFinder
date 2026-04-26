"""Non-modal image viewer for FITS/XISF astronomical images."""
import logging
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap, QPainter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QToolBar, QLabel, QCheckBox, QDoubleSpinBox, QComboBox, QPushButton,
    QStatusBar,
)

from photonfinder.models import File

logger = logging.getLogger(__name__)

_BLINK_INTERVALS = {'Off': 0, '0.25s': 250, '0.5s': 500, '1s': 1000, '2s': 2000, '5s': 5000}


class PreloadWorker(QThread):
    """Loads all given filenames into the image cache in parallel."""
    progress = Signal(int, int)  # (done, total)

    def __init__(self, filenames: list[str]):
        super().__init__()
        self.filenames = filenames
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from photonfinder.image_processing import load_image_data
        total = len(self.filenames)
        done = 0
        n_workers = min(4, os.cpu_count() or 1)
        logger.debug("Using %d parallel workers for preloading.", n_workers)
        executor = ThreadPoolExecutor(max_workers=n_workers)
        futures = {executor.submit(load_image_data, fn): fn for fn in self.filenames}
        for future in as_completed(futures):
            fn = futures[future]
            try:
                future.result()
            except Exception as exc:
                logger.warning("Preload failed for %s: %s", fn, exc)
            done += 1
            self.progress.emit(done, total)
            if self._cancel:
                for f in futures:
                    f.cancel()
                break
        # cancel_futures=True drops queued work immediately so this QThread exits fast.
        # Any already-running load_image_data calls finish on daemon threads in the background.
        executor.shutdown(wait=False, cancel_futures=True)


class ImageLoadWorker(QThread):
    """Loads raw pixel data and header dict in a background thread."""
    image_loaded = Signal(object, dict, bool)  # (ndarray, header, is_fits)
    error = Signal(str)

    def __init__(self, filename: str, is_fits: bool):
        super().__init__()
        self.filename = filename
        self.is_fits = is_fits

    def run(self):
        try:
            from photonfinder.image_processing import load_image_data
            data, header = load_image_data(self.filename)
            self.image_loaded.emit(data, header, self.is_fits)
        except Exception as exc:
            logger.error("Error loading image %s: %s", self.filename, exc, exc_info=True)
            self.error.emit(str(exc))


class ProcessWorker(QThread):
    """Applies debayer + stretch to uint16 image data in a background thread."""
    result = Signal(object, object, object)  # (display_uint8, src_uint16, StretchParams | None)
    error = Signal(str)

    def __init__(self, raw_uint16: np.ndarray, img_type: str, debayer_on: bool,
                 pattern: str | None, stretch_on: bool, target_bg: float,
                 clip_sigma: float, linked: bool, is_fits: bool = False,
                 locked_params=None):
        super().__init__()
        self.raw_uint16 = raw_uint16
        self.img_type = img_type
        self.debayer_on = debayer_on
        self.pattern = pattern
        self.stretch_on = stretch_on
        self.target_bg = target_bg
        self.clip_sigma = clip_sigma
        self.linked = linked
        self.is_fits = is_fits
        self.locked_params = locked_params

    def run(self):
        try:
            from photonfinder.image_processing import (
                debayer_opencv, stretch_uint16_to_uint8, uint16_to_uint8,
                compute_stretch_params,
            )
            t0 = time.perf_counter()

            data = self.raw_uint16
            img_type = self.img_type

            if img_type in ('mono', 'bayer') and self.debayer_on and self.pattern:
                data = debayer_opencv(data, self.pattern)
                img_type = 'rgb'
            t1 = time.perf_counter()

            # FITS is stored bottom-up; flip after debayering so BAYERPAT is applied correctly
            if self.is_fits:
                data = np.flip(data, axis=-2).copy()
            t2 = time.perf_counter()

            stretch_params = None
            if self.stretch_on:
                if self.locked_params is not None:
                    stretch_params = self.locked_params
                else:
                    stretch_params = compute_stretch_params(
                        data, self.target_bg, self.clip_sigma, self.linked,
                    )
                display = stretch_uint16_to_uint8(
                    data, self.target_bg, self.clip_sigma, self.linked,
                    locked_params=stretch_params,
                )
            else:
                display = uint16_to_uint8(data)
            t3 = time.perf_counter()

            logger.debug(
                "debayer %.0fms  flip %.0fms  stretch %.0fms  total %.0fms",
                (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000, (t3 - t0) * 1000,
            )

            self.result.emit(display, data, stretch_params)
        except Exception as exc:
            logger.error("Error processing image: %s", exc, exc_info=True)
            self.error.emit(str(exc))


class ImageCanvas(QGraphicsView):
    """Pan/zoom image display widget."""
    pixel_hovered = Signal(int, int)  # image x, y

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._zoom = 1.0
        self._image_w = 0
        self._image_h = 0

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(30, 30, 30))
        self.setMouseTracking(True)
        self._fit_mode = True  # re-fit on resize until user manually zooms

    def set_pixmap(self, pixmap: QPixmap, image_w: int, image_h: int):
        if self._pixmap_item:
            self._scene.removeItem(self._pixmap_item)
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self._image_w = image_w
        self._image_h = image_h

    def fit_in_view(self):
        if not self._pixmap_item:
            return
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        vp = self.viewport().size()
        sr = self._pixmap_item.boundingRect()
        if sr.width() > 0 and sr.height() > 0:
            self._zoom = min(vp.width() / sr.width(), vp.height() / sr.height())
        self._fit_mode = True

    def set_zoom(self, zoom: float):
        self.resetTransform()
        self.scale(zoom, zoom)
        self._zoom = zoom
        self._fit_mode = False

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)
        self._zoom *= factor
        self._fit_mode = False
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode and self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def mouseMoveEvent(self, event):
        if self._pixmap_item:
            scene_pos = self.mapToScene(event.pos())
            x = int(scene_pos.x())
            y = int(scene_pos.y())
            if 0 <= x < self._image_w and 0 <= y < self._image_h:
                self.pixel_hovered.emit(x, y)
        super().mouseMoveEvent(event)


class ImageViewerWindow(QMainWindow):
    """Single non-modal viewer window; MainWindow holds one reference."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Viewer")
        self.resize(1080, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._filename: str | None = None
        self._raw_uint16: np.ndarray | None = None
        self._header: dict = {}
        self._image_type: str = 'mono'
        self._is_fits: bool = True
        self._display_src: np.ndarray | None = None  # post-debayer/flip uint16, for hover
        self._display_buffer: np.ndarray | None = None  # keeps buffer alive for QImage
        self._fitted = False
        self._pending_process = False
        self._load_worker: ImageLoadWorker | None = None
        self._process_worker: ProcessWorker | None = None
        self._nav_tb: QToolBar | None = None
        self._view_tb: QToolBar | None = None
        self._toolbar_broken: bool = False

        # Navigation state
        self._nav_panel = None         # SearchPanel | None
        self._nav_row: int = -1        # index into _nav_rows (or model row when _nav_rows is None)
        self._nav_rows: list[int] | None = None  # model rows to navigate; None = all rows
        self._pending_nav_row: int | None = None  # target row deferred until async load completes
        self._preserve_view: bool = False

        # Blink state
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._nav_next)
        self._blink_due_at: float = 0.0

        # Stretch lock
        self._locked_stretch = None   # StretchParams | None — applied to every subsequent image
        self._last_stretch = None     # StretchParams | None — params from last completed image

        # User overrides for debayer controls (None = follow auto-detection)
        self._debayer_override: bool | None = None
        self._pattern_override: str | None = None

        # Loading / nav state
        self._is_loading: bool = False
        self._cursor_overridden: bool = False
        self._first_btn: QPushButton | None = None
        self._prev_btn: QPushButton | None = None
        self._next_btn: QPushButton | None = None
        self._last_btn: QPushButton | None = None
        self._preload_btn: QPushButton | None = None
        self._preload_worker: PreloadWorker | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self._canvas = ImageCanvas(self)
        self._canvas.pixel_hovered.connect(self._on_pixel_hover)
        self.setCentralWidget(self._canvas)

        # --- Navigation toolbar ---
        nav_tb = QToolBar("Navigation", self)
        nav_tb.setObjectName("nav_toolbar")
        self._nav_tb = nav_tb
        self.addToolBar(nav_tb)

        self._first_btn = QPushButton("|←")
        self._first_btn.setFixedWidth(36)
        self._first_btn.setToolTip("First image (Home)")
        self._first_btn.clicked.connect(self._nav_first)
        nav_tb.addWidget(self._first_btn)

        self._prev_btn = QPushButton("←")
        self._prev_btn.setFixedWidth(32)
        self._prev_btn.setToolTip("Previous image (←)")
        self._prev_btn.clicked.connect(self._nav_prev)
        nav_tb.addWidget(self._prev_btn)

        self._next_btn = QPushButton("→")
        self._next_btn.setFixedWidth(32)
        self._next_btn.setToolTip("Next image (→)")
        self._next_btn.clicked.connect(self._nav_next)
        nav_tb.addWidget(self._next_btn)

        self._last_btn = QPushButton("→|")
        self._last_btn.setFixedWidth(36)
        self._last_btn.setToolTip("Last image (End)")
        self._last_btn.clicked.connect(self._nav_last)
        nav_tb.addWidget(self._last_btn)

        nav_tb.addSeparator()

        nav_tb.addWidget(QLabel("Blink:"))
        self._blink_combo = QComboBox()
        self._blink_combo.addItems(list(_BLINK_INTERVALS.keys()))
        self._blink_combo.setFixedWidth(60)
        self._blink_combo.setToolTip("Auto-advance interval for blinking between images")
        nav_tb.addWidget(self._blink_combo)

        nav_tb.addSeparator()

        self._preload_btn = QPushButton("Preload")
        self._preload_btn.setToolTip("Load all images in the current result set into the cache")
        self._preload_btn.setEnabled(False)
        self._preload_btn.clicked.connect(self._on_preload_clicked)
        nav_tb.addWidget(self._preload_btn)

        nav_tb.addSeparator()

        self._lock_stretch_check = QCheckBox("Lock stretch")
        self._lock_stretch_check.setChecked(False)
        self._lock_stretch_check.setToolTip(
            "Freeze the current stretch and apply it to subsequent images"
        )
        nav_tb.addWidget(self._lock_stretch_check)

        # --- View toolbar (shares the top row when there is room) ---
        view_tb = QToolBar("View", self)
        view_tb.setObjectName("view_toolbar")
        self._view_tb = view_tb
        self.addToolBar(view_tb)

        # --- Stretch ---
        self._stretch_check = QCheckBox("Stretch")
        self._stretch_check.setChecked(True)
        self._stretch_check.setToolTip("Apply auto-stretch")
        view_tb.addWidget(self._stretch_check)

        view_tb.addWidget(QLabel(" BG%:"))
        self._bg_spin = QDoubleSpinBox()
        self._bg_spin.setRange(0.01, 0.99)
        self._bg_spin.setValue(0.25)
        self._bg_spin.setSingleStep(0.05)
        self._bg_spin.setDecimals(2)
        self._bg_spin.setFixedWidth(90)
        self._bg_spin.setToolTip("Target background level after stretch (0–1)")
        view_tb.addWidget(self._bg_spin)

        view_tb.addWidget(QLabel(" σ:"))
        self._sigma_spin = QDoubleSpinBox()
        self._sigma_spin.setRange(0.5, 10.0)
        self._sigma_spin.setValue(2.8)
        self._sigma_spin.setSingleStep(0.1)
        self._sigma_spin.setDecimals(1)
        self._sigma_spin.setFixedWidth(80)
        self._sigma_spin.setToolTip("Clipping sigma for black point estimation")
        view_tb.addWidget(self._sigma_spin)

        self._linked_check = QCheckBox(" Linked")
        self._linked_check.setChecked(False)
        self._linked_check.setToolTip("Linked: derive stretch from green channel and apply to all")
        view_tb.addWidget(self._linked_check)

        view_tb.addSeparator()

        # --- Debayer ---
        self._debayer_check = QCheckBox("Debayer")
        self._debayer_check.setChecked(False)
        self._debayer_check.setToolTip("Apply Bayer demosaicing to single-channel image")
        view_tb.addWidget(self._debayer_check)

        view_tb.addWidget(QLabel(" Pattern:"))
        self._pattern_combo = QComboBox()
        self._pattern_combo.addItems(['RGGB', 'BGGR', 'GRBG', 'GBRG'])
        self._pattern_combo.setFixedWidth(75)
        self._pattern_combo.setToolTip("Bayer matrix pattern (override auto-detected value)")
        view_tb.addWidget(self._pattern_combo)

        view_tb.addSeparator()

        # --- Zoom ---
        for label, zoom in [('50%', 0.5), ('100%', 1.0), ('200%', 2.0)]:
            btn = QPushButton(label)
            btn.setFixedWidth(42)
            btn.clicked.connect(lambda _checked, z=zoom: self._canvas.set_zoom(z))
            view_tb.addWidget(btn)

        fit_btn = QPushButton("Fit")
        fit_btn.setFixedWidth(36)
        fit_btn.clicked.connect(self._canvas.fit_in_view)
        view_tb.addWidget(fit_btn)

        # --- Status bar ---
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._nav_label = QLabel()
        sb.addPermanentWidget(self._nav_label)
        self._pixel_label = QLabel()
        sb.addPermanentWidget(self._pixel_label)

        # --- Connect controls ---
        self._stretch_check.toggled.connect(self._on_controls_changed)
        self._bg_spin.valueChanged.connect(self._on_controls_changed)
        self._sigma_spin.valueChanged.connect(self._on_controls_changed)
        self._linked_check.toggled.connect(self._on_controls_changed)
        self._debayer_check.toggled.connect(self._on_controls_changed)
        self._pattern_combo.currentTextChanged.connect(self._on_controls_changed)
        self._blink_combo.currentTextChanged.connect(self._on_blink_changed)
        self._lock_stretch_check.toggled.connect(self._on_lock_stretch_toggled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_nav_context(self, panel, row: int, rows: list[int] | None = None):
        """Set the search panel and row index used for prev/next navigation.

        rows: if given, navigation is restricted to those model row indices.
              row must be the model row of the initially displayed file.
        """
        if self._nav_panel is not None and self._nav_panel is not panel:
            try:
                self._nav_panel.data_fully_loaded.disconnect(self._on_nav_data_loaded)
            except RuntimeError:
                pass
        self._nav_panel = panel
        self._nav_rows = rows if rows and len(rows) > 1 else None
        self._nav_row = self._nav_rows.index(row) if self._nav_rows and row in self._nav_rows else row
        self._pending_nav_row = None
        panel.data_fully_loaded.connect(self._on_nav_data_loaded)
        self._update_nav_label()
        if self._preload_btn:
            self._preload_btn.setEnabled(True)

    def load_file(self, file: File, preserve_view: bool = False):
        """Load and display the image associated with a File model instance."""
        from photonfinder.filesystem import Importer
        filename = file.full_filename()
        if not filename:
            return
        self._filename = filename
        self._is_fits = Importer.is_fits_by_name(filename)
        self._preserve_view = preserve_view
        if not preserve_view:
            self._fitted = False

        self._is_loading = True
        self._update_nav_state()
        self.statusBar().showMessage(f"Loading {Path(filename).name}…")
        self.setWindowTitle(f"Image Viewer — {Path(filename).name}")

        # Check cache first — on a hit we can skip the worker entirely
        from photonfinder.image_processing import image_cache
        cached = image_cache.get(filename)
        if cached is not None:
            logger.debug("Cache hit — loading inline: %s", filename)
            self._on_raw_loaded(cached[0], cached[1], self._is_fits)
            return

        # Discard any in-flight load worker
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.image_loaded.disconnect()
            self._load_worker.error.disconnect()

        self._load_worker = ImageLoadWorker(filename, self._is_fits)
        self._load_worker.image_loaded.connect(self._on_raw_loaded)
        self._load_worker.error.connect(self._on_load_error)
        self._load_worker.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_toolbars()

    def _reflow_toolbars(self):
        if self._nav_tb is None or self._view_tb is None:
            return
        needs_break = (
            self._nav_tb.sizeHint().width() + self._view_tb.sizeHint().width()
            > self.width()
        )
        if needs_break == self._toolbar_broken:
            return
        self._toolbar_broken = needs_break
        self.removeToolBar(self._nav_tb)
        self.removeToolBar(self._view_tb)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self._nav_tb)
        self._nav_tb.show()
        if needs_break:
            self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self._view_tb)
        self._view_tb.show()

    def closeEvent(self, event):
        self._blink_timer.stop()
        if self._preload_worker and self._preload_worker.isRunning():
            self._preload_worker.cancel()
            self._preload_worker.progress.disconnect()
            self._preload_worker.finished.disconnect()
            # Must wait: WA_DeleteOnClose destroys the C++ object after this returns,
            # so any in-flight signal emission into self would be a use-after-free crash.
            self._preload_worker.wait()
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.image_loaded.disconnect()
            self._load_worker.error.disconnect()
            self._load_worker.wait()
        if self._process_worker and self._process_worker.isRunning():
            self._process_worker.result.disconnect()
            self._process_worker.error.disconnect()
            self._process_worker.finished.disconnect()
            self._process_worker.wait()
        if self._cursor_overridden:
            QApplication.restoreOverrideCursor()
            self._cursor_overridden = False
        from photonfinder.image_processing import image_cache
        image_cache.clear()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Private: keyboard navigation
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Right:
            self._nav_next()
        elif key == Qt.Key.Key_Left:
            self._nav_prev()
        elif key == Qt.Key.Key_Home:
            self._nav_first()
        elif key == Qt.Key.Key_End:
            self._nav_last()
        else:
            super().keyPressEvent(event)

    def _nav_first(self):
        self._blink_timer.stop()
        self._navigate(0)

    def _nav_last(self):
        self._blink_timer.stop()
        if self._nav_rows is not None:
            self._navigate(len(self._nav_rows) - 1)
        elif self._nav_panel and self._nav_panel.has_more_results:
            # Not all rows loaded yet — go to a row past the end so _navigate defers via pending
            self._navigate(self._nav_panel.total_files - 1)
        elif self._nav_panel:
            self._navigate(self._nav_panel.dataView.model().rowCount() - 1)

    def _nav_next(self):
        self._blink_timer.stop()
        self._navigate(self._nav_row + 1)

    def _nav_prev(self):
        self._blink_timer.stop()
        self._navigate(self._nav_row - 1)

    def _navigate(self, row: int):
        if self._nav_panel is None or self._nav_row < 0:
            return

        if self._nav_rows is not None:
            # Navigate within the fixed selection list
            total = len(self._nav_rows)
            if total == 0:
                return
            row = row % total
            model_row = self._nav_rows[row]
            next_model_row = self._nav_rows[(row + 1) % total]
        else:
            # Navigate through all model rows
            model = self._nav_panel.dataView.model()
            total = model.rowCount()
            if total == 0:
                return
            if row >= total and self._nav_panel.has_more_results:
                # Data isn't fully loaded yet — kick off the load and defer navigation.
                self._pending_nav_row = row
                self._nav_panel.load_all_remaining_data()
                return
            row = row % total
            model_row = row
            next_model_row = (row + 1) % total

        file = self._nav_panel.get_file_at_row(model_row)
        if not file:
            return

        self._nav_row = row
        self._update_nav_label()

        # Record when the next auto-advance is due (for due-time blink logic)
        interval_ms = _BLINK_INTERVALS.get(self._blink_combo.currentText(), 0)
        self._blink_due_at = time.monotonic() + interval_ms / 1000.0

        self.load_file(file, preserve_view=self._fitted)

        # Pre-fetch the next image into cache
        from photonfinder.image_processing import prefetch_image
        next_file = self._nav_panel.get_file_at_row(next_model_row)
        if next_file:
            prefetch_image(next_file.full_filename())

    def _on_nav_data_loaded(self):
        """Called when the panel finishes loading all remaining rows."""
        if self._pending_nav_row is not None:
            target = self._pending_nav_row
            self._pending_nav_row = None
            self._navigate(target)

    # ------------------------------------------------------------------
    # Private: nav state (loading indicator + button enable)
    # ------------------------------------------------------------------

    def _update_nav_state(self):
        is_blinking = self._blink_interval_ms() > 0
        nav_enabled = not self._is_loading and not is_blinking
        self._first_btn.setEnabled(nav_enabled)
        self._prev_btn.setEnabled(nav_enabled)
        self._next_btn.setEnabled(nav_enabled)
        self._last_btn.setEnabled(nav_enabled)
        if self._is_loading and not self._cursor_overridden:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._cursor_overridden = True
        elif not self._is_loading and self._cursor_overridden:
            QApplication.restoreOverrideCursor()
            self._cursor_overridden = False

    # ------------------------------------------------------------------
    # Private: blink
    # ------------------------------------------------------------------

    def _on_blink_changed(self, text: str):
        if text == 'Off':
            self._blink_timer.stop()
            self._update_nav_state()
        else:
            self._update_nav_state()
            self._nav_next()

    def _blink_interval_ms(self) -> int:
        return _BLINK_INTERVALS.get(self._blink_combo.currentText(), 0)

    # ------------------------------------------------------------------
    # Private: nav index label
    # ------------------------------------------------------------------

    def _update_nav_label(self):
        if self._nav_panel is None or self._nav_row < 0:
            self._nav_label.setText("")
            return
        if self._nav_rows is not None:
            total = len(self._nav_rows)
            self._nav_label.setText(f"{self._nav_row + 1} / {total} (selection)")
        else:
            total = self._nav_panel.total_files
            self._nav_label.setText(f"{self._nav_row + 1} / {total}")

    # ------------------------------------------------------------------
    # Private: preload
    # ------------------------------------------------------------------

    def _on_preload_clicked(self):
        if self._nav_panel is None:
            return
        if self._nav_rows is None and self._nav_panel.has_more_results:
            self._nav_panel.load_all_remaining_data()
        model = self._nav_panel.dataView.model()
        rows = self._nav_rows if self._nav_rows is not None else range(model.rowCount())
        filenames = [
            fn for row in rows
            if (f := self._nav_panel.get_file_at_row(row)) and (fn := f.full_filename())
        ]
        if not filenames:
            return
        import psutil
        from photonfinder.image_processing import image_cache
        image_cache._max_bytes = int(psutil.virtual_memory().available * 0.90)
        self._preload_worker = PreloadWorker(filenames)
        self._preload_worker.progress.connect(self._on_preload_progress)
        self._preload_worker.finished.connect(self._on_preload_finished)
        self._preload_worker.start()
        self._preload_btn.setEnabled(False)
        self._preload_btn.setText("Preloading…")

    def _on_preload_progress(self, done: int, total: int):
        self.statusBar().showMessage(f"Preloading {done} / {total}…")

    def _on_preload_finished(self):
        self._preload_worker = None
        self._preload_btn.setEnabled(True)
        self._preload_btn.setText("Preload")
        self.statusBar().showMessage("Preload complete", 3000)

    # ------------------------------------------------------------------
    # Private: lock stretch
    # ------------------------------------------------------------------

    def _on_lock_stretch_toggled(self, checked: bool):
        if checked:
            self._locked_stretch = self._last_stretch  # may be None if no image processed yet
        else:
            self._locked_stretch = None

    # ------------------------------------------------------------------
    # Private: loading pipeline
    # ------------------------------------------------------------------

    def _on_raw_loaded(self, raw_data: np.ndarray, header: dict, is_fits: bool):
        from photonfinder.image_processing import (
            normalize_to_uint16, detect_image_type, detect_bayer_pattern, is_linear,
        )

        self._header = header

        t0 = time.perf_counter()
        self._raw_uint16 = normalize_to_uint16(raw_data, header)
        logger.debug("normalize_to_uint16 %.0fms", (time.perf_counter() - t0) * 1000)

        self._image_type = detect_image_type(self._raw_uint16, header)
        detected_pattern = detect_bayer_pattern(header)

        # Block signals while configuring controls to avoid spurious reprocessing
        for w in (self._debayer_check, self._pattern_combo, self._stretch_check):
            w.blockSignals(True)

        if self._debayer_override is not None:
            self._debayer_check.setChecked(self._debayer_override)
        elif self._image_type == 'bayer':
            self._debayer_check.setChecked(True)
        else:
            self._debayer_check.setChecked(False)

        if self._pattern_override is not None:
            idx = self._pattern_combo.findText(self._pattern_override)
            if idx >= 0:
                self._pattern_combo.setCurrentIndex(idx)
        elif detected_pattern:
            idx = self._pattern_combo.findText(detected_pattern)
            if idx >= 0:
                self._pattern_combo.setCurrentIndex(idx)

        self._debayer_check.setEnabled(self._image_type != 'rgb')
        self._pattern_combo.setEnabled(self._image_type != 'rgb')

        # Only update auto-stretch detection when stretch is not locked and not navigating
        if not self._lock_stretch_check.isChecked() and not self._preserve_view:
            self._stretch_check.setChecked(is_linear(self._raw_uint16))

        for w in (self._debayer_check, self._pattern_combo, self._stretch_check):
            w.blockSignals(False)

        h, w_px = self._raw_uint16.shape[-2], self._raw_uint16.shape[-1]
        self.statusBar().showMessage(f"{Path(self._filename).name}  |  {w_px}×{h}")

        self._run_processing()

    def _on_load_error(self, msg: str):
        self._is_loading = False
        self._update_nav_state()
        self.statusBar().showMessage(f"Error: {msg}")

    # ------------------------------------------------------------------
    # Private: processing pipeline
    # ------------------------------------------------------------------

    def _on_controls_changed(self):
        sender = self.sender()
        if sender is self._debayer_check:
            self._debayer_override = self._debayer_check.isChecked()
        elif sender is self._pattern_combo:
            self._pattern_override = self._pattern_combo.currentText()

        self._blink_timer.stop()
        self._blink_combo.blockSignals(True)
        self._blink_combo.setCurrentText('Off')
        self._blink_combo.blockSignals(False)
        self._update_nav_state()
        # Any manual control change clears the locked stretch
        if self._lock_stretch_check.isChecked():
            self._lock_stretch_check.blockSignals(True)
            self._lock_stretch_check.setChecked(False)
            self._lock_stretch_check.blockSignals(False)
            self._locked_stretch = None
        if self._raw_uint16 is not None:
            self._run_processing()

    def _run_processing(self):
        if self._process_worker and self._process_worker.isRunning():
            self._pending_process = True
            return

        self._pending_process = False
        debayer_on = self._debayer_check.isChecked() and self._image_type != 'rgb'
        pattern = self._pattern_combo.currentText() if debayer_on else None

        worker = ProcessWorker(
            raw_uint16=self._raw_uint16,
            img_type=self._image_type,
            debayer_on=debayer_on,
            pattern=pattern,
            stretch_on=self._stretch_check.isChecked(),
            target_bg=self._bg_spin.value(),
            clip_sigma=self._sigma_spin.value(),
            linked=self._linked_check.isChecked(),
            is_fits=self._is_fits,
            locked_params=self._locked_stretch,
        )
        worker.result.connect(self._on_processed)
        worker.error.connect(lambda msg: self.statusBar().showMessage(f"Processing error: {msg}"))
        worker.finished.connect(self._on_process_finished)
        self._process_worker = worker
        worker.start()

    def _on_process_finished(self):
        self._process_worker = None
        if self._pending_process:
            self._run_processing()

    def _on_processed(self, display_uint8: np.ndarray, src_uint16: np.ndarray, stretch_params):
        self._display_src = src_uint16
        self._display_buffer = np.ascontiguousarray(display_uint8)
        if stretch_params is not None:
            self._last_stretch = stretch_params

        self._is_loading = False
        self._update_nav_state()

        h, w = self._display_buffer.shape[:2]
        if self._display_buffer.ndim == 2:
            img = QImage(self._display_buffer.data, w, h, w,
                         QImage.Format.Format_Grayscale8)
        else:
            img = QImage(self._display_buffer.data, w, h, w * 3,
                         QImage.Format.Format_RGB888)

        pixmap = QPixmap.fromImage(img)
        self._canvas.set_pixmap(pixmap, w, h)

        if not self._fitted:
            self._canvas.fit_in_view()
            self._fitted = True

        name = Path(self._filename).name if self._filename else ""
        self.statusBar().showMessage(f"{name}  |  {w}×{h}")

        # Blink: start timer for remaining time (0 if load already exceeded interval)
        interval_ms = self._blink_interval_ms()
        if interval_ms > 0:
            remaining_ms = max(0, int((self._blink_due_at - time.monotonic()) * 1000))
            self._blink_timer.start(remaining_ms)

    # ------------------------------------------------------------------
    # Private: pixel info
    # ------------------------------------------------------------------

    def _on_pixel_hover(self, x: int, y: int):
        if self._display_src is None:
            return
        try:
            if self._display_src.ndim == 2:
                val = self._display_src[y, x] / 65535.0
                self._pixel_label.setText(f"x={x}, y={y}: {val:.4f}")
            else:
                r = self._display_src[0, y, x] / 65535.0
                g = self._display_src[1, y, x] / 65535.0
                b = self._display_src[2, y, x] / 65535.0
                self._pixel_label.setText(f"x={x}, y={y}:  R={r:.3f}  G={g:.3f}  B={b:.3f}")
        except (IndexError, ValueError):
            pass

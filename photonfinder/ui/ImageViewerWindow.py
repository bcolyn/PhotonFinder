"""Non-modal image viewer for FITS/XISF astronomical images."""
import logging
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QColor, QImage, QPixmap, QPainter
from PySide6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QToolBar, QLabel, QCheckBox, QDoubleSpinBox, QComboBox, QPushButton,
    QStatusBar,
)

from photonfinder.models import File

logger = logging.getLogger(__name__)


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
    result = Signal(object, object)  # (display_uint8 ndarray, src_uint16 ndarray)
    error = Signal(str)

    def __init__(self, raw_uint16: np.ndarray, img_type: str, debayer_on: bool,
                 pattern: str | None, stretch_on: bool, target_bg: float,
                 clip_sigma: float, linked: bool, is_fits: bool = False):
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

    def run(self):
        try:
            from photonfinder.image_processing import (
                debayer_opencv, stretch_uint16_to_uint8, uint16_to_uint8,
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

            if self.stretch_on:
                display = stretch_uint16_to_uint8(data, self.target_bg, self.clip_sigma, self.linked)
            else:
                display = uint16_to_uint8(data)
            t3 = time.perf_counter()

            logger.debug(
                "debayer %.0fms  flip %.0fms  stretch %.0fms  total %.0fms",
                (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000, (t3 - t0) * 1000,
            )

            self.result.emit(display, data)
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
        self.resize(960, 720)
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

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self._canvas = ImageCanvas(self)
        self._canvas.pixel_hovered.connect(self._on_pixel_hover)
        self.setCentralWidget(self._canvas)

        tb = QToolBar("Controls", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        # --- Stretch ---
        self._stretch_check = QCheckBox("Auto-stretch")
        self._stretch_check.setChecked(True)
        tb.addWidget(self._stretch_check)

        tb.addWidget(QLabel("  BG%:"))
        self._bg_spin = QDoubleSpinBox()
        self._bg_spin.setRange(0.01, 0.99)
        self._bg_spin.setValue(0.25)
        self._bg_spin.setSingleStep(0.05)
        self._bg_spin.setDecimals(2)
        self._bg_spin.setFixedWidth(70)
        self._bg_spin.setToolTip("Target background level after stretch (0–1)")
        tb.addWidget(self._bg_spin)

        tb.addWidget(QLabel("  σ:"))
        self._sigma_spin = QDoubleSpinBox()
        self._sigma_spin.setRange(0.5, 10.0)
        self._sigma_spin.setValue(2.8)
        self._sigma_spin.setSingleStep(0.5)
        self._sigma_spin.setDecimals(1)
        self._sigma_spin.setFixedWidth(60)
        self._sigma_spin.setToolTip("Clipping sigma for black point estimation")
        tb.addWidget(self._sigma_spin)

        self._linked_check = QCheckBox("  Linked")
        self._linked_check.setChecked(False)
        self._linked_check.setToolTip("Linked: derive stretch from green channel and apply to all")
        tb.addWidget(self._linked_check)

        tb.addSeparator()

        # --- Debayer ---
        self._debayer_check = QCheckBox("Debayer")
        self._debayer_check.setChecked(False)
        self._debayer_check.setToolTip("Apply Bayer demosaicing to single-channel image")
        tb.addWidget(self._debayer_check)

        tb.addWidget(QLabel("  Pattern:"))
        self._pattern_combo = QComboBox()
        self._pattern_combo.addItems(['RGGB', 'BGGR', 'GRBG', 'GBRG'])
        self._pattern_combo.setFixedWidth(80)
        self._pattern_combo.setToolTip("Bayer matrix pattern (override auto-detected value)")
        tb.addWidget(self._pattern_combo)

        tb.addSeparator()

        # --- Zoom ---
        for label, zoom in [('50%', 0.5), ('100%', 1.0), ('200%', 2.0)]:
            btn = QPushButton(label)
            btn.setFixedWidth(46)
            btn.clicked.connect(lambda _checked, z=zoom: self._canvas.set_zoom(z))
            tb.addWidget(btn)

        fit_btn = QPushButton("Fit")
        fit_btn.setFixedWidth(40)
        fit_btn.clicked.connect(self._canvas.fit_in_view)
        tb.addWidget(fit_btn)

        # --- Status bar ---
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._pixel_label = QLabel()
        sb.addPermanentWidget(self._pixel_label)

        # --- Connect controls ---
        self._stretch_check.toggled.connect(self._on_controls_changed)
        self._bg_spin.valueChanged.connect(self._on_controls_changed)
        self._sigma_spin.valueChanged.connect(self._on_controls_changed)
        self._linked_check.toggled.connect(self._on_controls_changed)
        self._debayer_check.toggled.connect(self._on_controls_changed)
        self._pattern_combo.currentTextChanged.connect(self._on_controls_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, file: File):
        """Load and display the image associated with a File model instance."""
        from photonfinder.filesystem import Importer
        filename = file.full_filename()
        if not filename:
            return
        self._filename = filename
        self._is_fits = Importer.is_fits_by_name(filename)
        self._fitted = False

        self.statusBar().showMessage(f"Loading {Path(filename).name}…")
        self.setWindowTitle(f"Image Viewer — {Path(filename).name}")

        # Discard any in-flight load worker
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.image_loaded.disconnect()
            self._load_worker.error.disconnect()

        self._load_worker = ImageLoadWorker(filename, self._is_fits)
        self._load_worker.image_loaded.connect(self._on_raw_loaded)
        self._load_worker.error.connect(self._on_load_error)
        self._load_worker.start()

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

        if self._image_type == 'bayer':
            self._debayer_check.setChecked(True)
            if detected_pattern:
                idx = self._pattern_combo.findText(detected_pattern)
                if idx >= 0:
                    self._pattern_combo.setCurrentIndex(idx)
        else:
            self._debayer_check.setChecked(False)

        # Disable debayer for native RGB images
        self._debayer_check.setEnabled(self._image_type != 'rgb')
        self._pattern_combo.setEnabled(self._image_type != 'rgb')

        self._stretch_check.setChecked(is_linear(self._raw_uint16))

        for w in (self._debayer_check, self._pattern_combo, self._stretch_check):
            w.blockSignals(False)

        h, w_px = self._raw_uint16.shape[-2], self._raw_uint16.shape[-1]
        self.statusBar().showMessage(f"{Path(self._filename).name}  |  {w_px}×{h}")

        self._run_processing()

    def _on_load_error(self, msg: str):
        self.statusBar().showMessage(f"Error: {msg}")

    # ------------------------------------------------------------------
    # Private: processing pipeline
    # ------------------------------------------------------------------

    def _on_controls_changed(self):
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

    def _on_processed(self, display_uint8: np.ndarray, src_uint16: np.ndarray):
        self._display_src = src_uint16
        # Keep buffer alive — QImage does not own the data
        self._display_buffer = np.ascontiguousarray(display_uint8)

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
                # (3, H, W) uint16
                r = self._display_src[0, y, x] / 65535.0
                g = self._display_src[1, y, x] / 65535.0
                b = self._display_src[2, y, x] / 65535.0
                self._pixel_label.setText(f"x={x}, y={y}:  R={r:.3f}  G={g:.3f}  B={b:.3f}")
        except (IndexError, ValueError):
            pass

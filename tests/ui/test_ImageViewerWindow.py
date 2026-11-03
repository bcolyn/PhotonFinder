import gc
import threading

import pytest

from photonfinder import image_processing
from photonfinder.models import File
from photonfinder.ui.ImageViewerWindow import ImageViewerWindow
from tests.conftest import DynamicSettings


def test_annotate_shown_for_plain_file_with_wcs(qtbot, sample):
    """A File fetched outside the search query (e.g. by the project editor) carries no
    has_wcs attribute; the viewer must look it up rather than hide Annotate."""
    ctx, objs = sample
    ctx.settings = DynamicSettings()
    ctx.settings.set_annotation_mag_limit(19.0)
    viewer = ImageViewerWindow(context=ctx)
    qtbot.addWidget(viewer)

    light = File.get_by_id(objs["light"].rowid)
    assert not hasattr(light, "has_wcs")
    viewer.load_file(light)
    assert viewer._annotate_action.isVisible()

    dark = File.get_by_id(objs["dark"].rowid)
    viewer.load_file(dark)
    assert not viewer._annotate_action.isVisible()


@pytest.fixture
def held_load(monkeypatch):
    """Keeps every image load running until the event is set, so that loads overlap."""
    release = threading.Event()

    def load(filename):
        assert release.wait(10)
        raise OSError("not a real file")

    monkeypatch.setattr(image_processing, "load_image_data", load)
    yield release
    release.set()


def _viewer(qtbot, sample):
    ctx, objs = sample
    ctx.settings = DynamicSettings()
    ctx.settings.set_annotation_mag_limit(19.0)
    viewer = ImageViewerWindow(context=ctx)
    qtbot.addWidget(viewer)
    return viewer, File.get_by_id(objs["light"].rowid), File.get_by_id(objs["dark"].rowid)


def test_loading_another_file_keeps_the_running_worker_alive(qtbot, sample, held_load):
    viewer, light, dark = _viewer(qtbot, sample)
    viewer.load_file(light)
    first = viewer._load_worker
    first_id = id(first)
    assert first.isRunning()
    del first

    viewer.load_file(dark)  # dropping the only reference to a running QThread crashes here
    gc.collect()

    assert [id(worker) for worker in viewer._retired_load_workers] == [first_id]
    assert all(worker.isRunning() for worker in viewer._retired_load_workers)
    current = viewer._load_worker

    held_load.set()
    qtbot.waitUntil(lambda: not viewer._retired_load_workers)
    current.wait()


def test_closing_waits_for_replaced_workers(qtbot, sample, held_load):
    viewer, light, dark = _viewer(qtbot, sample)
    viewer.load_file(light)
    viewer.load_file(dark)
    retired = list(viewer._retired_load_workers)
    current = viewer._load_worker
    assert retired

    timer = threading.Timer(0.2, held_load.set)
    timer.start()
    viewer.close()  # must not return while a thread could still signal into the window
    timer.join()

    assert not current.isRunning()
    assert all(worker.isFinished() for worker in retired)
    assert not viewer._retired_load_workers


# ---------------------------------------------------------------------------
# Loading state and navigation races (real FITS files, a stand-in search panel)
# ---------------------------------------------------------------------------

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, Signal
from PySide6.QtWidgets import QApplication, QTableView, QWidget

from photonfinder.models import LibraryRoot


class _NavPanel(QWidget):
    """Just what the viewer asks of a search panel when navigating a selection."""
    data_fully_loaded = Signal()
    has_more_results = False

    def __init__(self, files):
        super().__init__()
        self.files = files
        self.total_files = len(files)
        self.dataView = QTableView(self)

    def get_file_at_row(self, row):
        self.dataView.model()  # like SearchPanel: raises once the tab's widgets are deleted
        return self.files[row] if 0 <= row < len(self.files) else None

    def mark_file_as_bad(self, file, row, dialog_parent=None):
        self.dataView.model()
        return False


def _write_fits(path, value, size):
    from astropy.io import fits
    fits.PrimaryHDU(np.full((size, size), value, dtype=np.uint16)).writeto(path, overwrite=True)


@pytest.fixture
def fits_files(context, tmp_path):
    """Three real FITS files of different sizes, so the displayed one can be told apart."""
    root = LibraryRoot.create(name="tmp", path=str(tmp_path))
    files = []
    for name, value, size in (("a.fits", 1000, 8), ("b.fits", 20000, 16), ("c.fits", 40000, 24)):
        _write_fits(tmp_path / name, value, size)
        files.append(File.create(root=root, path="", name=name, size=1, mtime_millis=1))
    image_processing.image_cache.clear()
    yield files
    image_processing.image_cache.clear()


def _plain_viewer(qtbot, context):
    context.settings = DynamicSettings()
    context.settings.set_annotation_mag_limit(19.0)
    viewer = ImageViewerWindow(context=context)
    qtbot.addWidget(viewer)
    return viewer


def _settled(viewer):
    return (not viewer._is_loading and viewer._process_worker is None
            and not viewer._retired_load_workers
            and (viewer._load_worker is None or viewer._load_worker.isFinished()))


def test_processing_error_leaves_the_viewer_usable(qtbot, context, fits_files, monkeypatch):
    def broken(*_args, **_kwargs):
        raise ValueError("boom")
    monkeypatch.setattr(image_processing, "stretch_uint16_to_uint8", broken)
    monkeypatch.setattr(image_processing, "uint16_to_uint8", broken)
    viewer = _plain_viewer(qtbot, context)
    panel = _NavPanel(fits_files)
    viewer.set_nav_context(panel, 0, [0, 1, 2])

    viewer.load_file(fits_files[0])
    qtbot.waitUntil(lambda: _settled(viewer))

    assert viewer._next_btn.isEnabled()
    assert QApplication.overrideCursor() is None
    assert "boom" in viewer.statusBar().currentMessage()


def test_a_cache_hit_drops_the_older_load_still_in_flight(qtbot, context, fits_files, monkeypatch):
    a, b, _c = fits_files
    real_load = image_processing.load_image_data
    image_processing.load_image_data(b.full_filename())  # b is cached, a is not
    release = threading.Event()

    def slow_for_a(filename):
        if str(filename).endswith("a.fits"):
            assert release.wait(10)
        return real_load(filename)
    monkeypatch.setattr(image_processing, "load_image_data", slow_for_a)

    viewer = _plain_viewer(qtbot, context)
    shown = []
    real_set_pixmap = viewer._canvas.set_pixmap
    monkeypatch.setattr(viewer._canvas, "set_pixmap",
                        lambda pixmap, w, h: (shown.append((w, h)), real_set_pixmap(pixmap, w, h)))

    viewer.load_file(a)      # still loading when the user moves on
    viewer.load_file(b)      # cache hit
    release.set()
    qtbot.waitUntil(lambda: _settled(viewer))
    QCoreApplication.processEvents()

    assert viewer.windowTitle().endswith("b.fits")
    assert viewer._raw_uint16.shape == (16, 16)
    assert shown[-1] == (16, 16)
    assert (8, 8) not in shown


def test_a_result_for_an_image_already_left_is_not_shown(qtbot, context, fits_files, monkeypatch):
    a, b, _c = fits_files
    for f in (a, b):
        image_processing.load_image_data(f.full_filename())
    viewer = _plain_viewer(qtbot, context)
    shown = []
    real_set_pixmap = viewer._canvas.set_pixmap
    monkeypatch.setattr(viewer._canvas, "set_pixmap",
                        lambda pixmap, w, h: (shown.append((w, h)), real_set_pixmap(pixmap, w, h)))

    viewer.load_file(a)  # both cache hits: processing of a is under way when b arrives
    viewer.load_file(b)
    qtbot.waitUntil(lambda: _settled(viewer))

    assert shown == [(16, 16)]


def test_closing_the_search_tab_detaches_navigation(qtbot, context, fits_files):
    viewer = _plain_viewer(qtbot, context)
    panel = _NavPanel(fits_files)
    viewer.set_nav_context(panel, 0, [0, 1, 2])
    viewer.load_file(fits_files[0])
    qtbot.waitUntil(lambda: _settled(viewer))

    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    viewer._nav_next()  # raised RuntimeError on the deleted panel
    viewer._mark_current_as_bad()
    assert not viewer._next_btn.isEnabled()
    assert not viewer._preload_btn.isEnabled()
    assert not viewer._mark_bad_btn.isEnabled()
    assert viewer.windowTitle().endswith("a.fits")


def test_preload_cannot_be_started_twice(qtbot, context, fits_files, monkeypatch):
    release = threading.Event()

    def held(filename):
        assert release.wait(10)
        raise OSError("held")
    monkeypatch.setattr(image_processing, "load_image_data", held)
    viewer = _plain_viewer(qtbot, context)
    panel = _NavPanel(fits_files)
    viewer.set_nav_context(panel, 0, [0, 1, 2])
    viewer._on_preload_clicked()
    first = viewer._preload_worker
    assert first.isRunning()

    viewer.set_nav_context(panel, 1, [0, 1, 2])  # opening another file from the same grid
    assert not viewer._preload_btn.isEnabled()
    viewer._on_preload_clicked()
    assert viewer._preload_worker is first

    release.set()
    qtbot.waitUntil(lambda: viewer._preload_worker is None)
    first.wait()

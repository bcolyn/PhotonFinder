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

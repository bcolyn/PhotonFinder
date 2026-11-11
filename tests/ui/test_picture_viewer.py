"""Pictures in the image viewer: shown as they are, never stretched or debayered, and annotated
from a stored solution, which refers to the decoded array (top row first)."""
import cv2
import numpy as np
import pytest
from astropy.io import fits

from photonfinder import image_processing, pictures
from photonfinder.core import compress
from photonfinder.image_processing import uint16_to_uint8
from photonfinder.models import File, FileWCS, Image, LibraryRoot
from photonfinder.ui.ImageViewerWindow import ImageViewerWindow
from tests.conftest import DynamicSettings

M31 = (10.684791666666666, 41.26905555555555)  # its catalog position


def _dark_sky(height=300, width=400) -> np.ndarray:
    """16-bit mono that looks linear, so the viewer would stretch it if it were a FITS."""
    rng = np.random.default_rng(5)
    sky = rng.normal(1500, 30, (height, width)).clip(0, 65535).astype(np.uint16)
    sky[rng.integers(0, height, 40), rng.integers(0, width, 40)] = 60000
    return sky


@pytest.fixture
def files(context, tmp_path):
    root = LibraryRoot.create(name="tmp", path=str(tmp_path), include_pictures=True)
    sky = _dark_sky()
    ok, png = cv2.imencode(".png", sky)
    assert ok
    png.tofile(str(tmp_path / "sky.png"))
    fits.PrimaryHDU(sky).writeto(tmp_path / "sky.fits")
    picture = File.create(root=root, path="", name="sky.png", size=1, mtime_millis=1)
    Image.create(file=picture, hdu=0, image_type=pictures.PICTURE_TYPE, width=400, height=300)
    light = File.create(root=root, path="", name="sky.fits", size=1, mtime_millis=1)
    Image.create(file=light, hdu=0, image_type="LIGHT", width=400, height=300)
    image_processing.image_cache.clear()
    yield sky, picture, light
    image_processing.image_cache.clear()


def _viewer(qtbot, context):
    context.settings = DynamicSettings()
    context.settings.set_annotation_mag_limit(19.0)
    context.settings.set_annotation_collapsed_catalogs(set())  # the real settings give an empty set
    viewer = ImageViewerWindow(context=context)
    qtbot.addWidget(viewer)
    return viewer


def _show(qtbot, viewer, file):
    viewer.load_file(file)
    qtbot.waitUntil(lambda: not viewer._is_loading and viewer._process_worker is None, timeout=10_000)


def _stretch_controls(viewer):
    return (viewer._stretch_check, viewer._bg_spin, viewer._sigma_spin, viewer._linked_check,
            viewer._debayer_check, viewer._pattern_combo)


def test_a_picture_is_shown_as_it_is(qtbot, context, files):
    sky, picture, light = files
    viewer = _viewer(qtbot, context)

    _show(qtbot, viewer, light)
    assert viewer._stretch_check.isChecked(), "the same pixels as a FITS look linear, so they are stretched"
    assert not np.array_equal(viewer._display_buffer, uint16_to_uint8(sky))

    _show(qtbot, viewer, picture)
    np.testing.assert_array_equal(viewer._display_buffer, uint16_to_uint8(sky))  # top row first, unstretched
    assert not any(control.isEnabled() for control in _stretch_controls(viewer))


def test_a_locked_stretch_is_not_applied_to_a_picture_and_kept_for_the_next_image(qtbot, context, files):
    sky, picture, light = files
    viewer = _viewer(qtbot, context)
    _show(qtbot, viewer, light)
    stretched = viewer._display_buffer.copy()
    viewer._lock_stretch_check.setChecked(True)

    _show(qtbot, viewer, picture)
    np.testing.assert_array_equal(viewer._display_buffer, uint16_to_uint8(sky))

    _show(qtbot, viewer, light)
    assert viewer._stretch_check.isEnabled() and viewer._stretch_check.isChecked()
    assert viewer._lock_stretch_check.isChecked()
    np.testing.assert_array_equal(viewer._display_buffer, stretched)


def test_an_rgb_picture_keeps_its_colours(qtbot, context, tmp_path):
    root = LibraryRoot.create(name="rgb", path=str(tmp_path), include_pictures=True)
    rgb = np.zeros((3, 30, 40), np.uint8)
    rgb[0, :10] = 255  # red band at the top
    ok, jpeg = cv2.imencode(".png", np.ascontiguousarray(rgb[::-1].transpose(1, 2, 0)))
    jpeg.tofile(str(tmp_path / "card.png"))
    picture = File.create(root=root, path="", name="card.png", size=1, mtime_millis=1)
    Image.create(file=picture, hdu=0, image_type=pictures.PICTURE_TYPE, width=40, height=30)
    viewer = _viewer(qtbot, context)

    _show(qtbot, viewer, picture)

    np.testing.assert_array_equal(viewer._display_buffer, rgb.transpose(1, 2, 0))


def test_a_solved_picture_is_annotated_where_the_solution_says(qtbot, context, files):
    """The solution refers to the decoded array, top row first (FITS y 1 = decoded row 0), and
    the viewer shows that array as it is, so M31 is drawn at its pixel, near the top."""
    _, picture, _ = files
    header = fits.Header()
    header.update({"NAXIS": 2, "NAXIS1": 400, "NAXIS2": 300, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
                   "CRVAL1": M31[0], "CRVAL2": M31[1], "CRPIX1": 101.0, "CRPIX2": 51.0,  # 0-based (100, 50)
                   "CD1_1": -1 / 3600, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 1 / 3600})
    FileWCS.create(file=picture, wcs=compress(header.tostring().encode("ascii")))
    viewer = _viewer(qtbot, context)
    _show(qtbot, viewer, picture)

    viewer._annotate_btn.setChecked(True)

    m31 = [item for item in viewer._annotation_items if (item.entry.catalog, item.entry.catalog_id) == ("Messier", "31")]
    assert len(m31) == 1
    assert (m31[0].pos().x(), m31[0].pos().y()) == pytest.approx((100.0, 50.0), abs=0.01)

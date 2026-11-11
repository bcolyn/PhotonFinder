"""Thumbnails of real files: the render, the disk cache and when it is invalidated, and files that
can't be read. Plus the loader's memory of failures."""
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from photonfinder.thumbnails import THUMBNAIL_SIZE, ThumbnailCache, cache_key, render_thumbnail
from tests.multi_image_files import write_multi_hdu_fits

SMALL = Path(__file__).parent / "data-small"
M106 = "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"  # an OSC light, BAYERPAT RGGB


@pytest.fixture
def m106(global_test_data_dir, tmp_path) -> Path:
    """A copy, so its modification time can be changed."""
    return Path(shutil.copy(global_test_data_dir / M106, tmp_path / M106))


def _entries(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.jpg")) if folder.exists() else []


def test_a_bayer_light_is_debayered_shrunk_and_stretched(global_test_data_dir):
    pixels = render_thumbnail(global_test_data_dir / M106)

    assert pixels.dtype == np.uint8
    h, w, channels = pixels.shape
    assert channels == 3
    assert max(h, w) == THUMBNAIL_SIZE
    assert w > h  # landscape, as the sensor
    # Stretched: the background is lifted well off black, but not blown out.
    assert 20 < np.median(pixels) < 200


MASTER_FLAT = "masterFlat_BIN-1_5496x3672_FILTER-LP_CFA_SESS-2020-04-11.xisf"


def test_a_master_flat_is_stretched_by_its_library_type(global_test_data_dir):
    """is_linear takes a flat's high, smooth background for a stretched image; the library's
    type says it's a flat, which is always stretched, so its vignetting shows."""
    from photonfinder.image_hdus import load_hdu
    from photonfinder.image_processing import is_linear, normalize_to_uint16
    path = global_test_data_dir / MASTER_FLAT
    data, header = load_hdu(str(path), None)
    assert not is_linear(normalize_to_uint16(data, header))

    plain = render_thumbnail(path)
    flat = render_thumbnail(path, image_type="MASTER FLAT")

    assert plain.shape == flat.shape
    # Stretched: the spread across the field (vignetting) is widened.
    assert np.ptp(flat.astype(float).mean(axis=-1)) > np.ptp(plain.astype(float).mean(axis=-1)) * 2


def test_an_xisf_image(tmp_path):
    pixels = render_thumbnail(SMALL / "rev2.xisf")
    assert pixels.dtype == np.uint8 and max(pixels.shape[:2]) <= THUMBNAIL_SIZE


def test_each_image_of_a_multi_layer_file(tmp_path):
    path = write_multi_hdu_fits(tmp_path / "draco.fits")
    cache = ThumbnailCache(tmp_path / "thumbs")

    primary, extension = cache.thumbnail(path, 0), cache.thumbnail(path, 1)

    assert primary != extension
    assert len(_entries(tmp_path / "thumbs")) == 2


def test_an_unchanged_file_is_served_from_the_cache(m106, tmp_path):
    cache = ThumbnailCache(tmp_path / "thumbs")
    first = cache.thumbnail(m106)
    [entry] = _entries(tmp_path / "thumbs")
    written = entry.stat().st_mtime_ns

    assert cache.thumbnail(m106) == first
    assert _entries(tmp_path / "thumbs") == [entry]
    assert entry.stat().st_mtime_ns == written  # read, not written again


def test_a_changed_file_gets_a_new_thumbnail(m106, tmp_path):
    cache = ThumbnailCache(tmp_path / "thumbs")
    cache.thumbnail(m106)
    st = m106.stat()

    os.utime(m106, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    cache.thumbnail(m106)
    assert len(_entries(tmp_path / "thumbs")) == 2

    with open(m106, "ab") as f:  # same mtime, another size
        f.write(b"\0")
    os.utime(m106, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    cache.thumbnail(m106)
    assert len(_entries(tmp_path / "thumbs")) == 3


def test_the_key_tells_path_hdu_mtime_and_size_apart():
    base = cache_key("C:/a/b.fits", None, 1, 2)
    assert base == cache_key("C:/a/b.fits", None, 1, 2)
    assert len({base, cache_key("C:/a/c.fits", None, 1, 2), cache_key("C:/a/b.fits", 0, 1, 2),
                cache_key("C:/a/b.fits", 1, 1, 2), cache_key("C:/a/b.fits", None, 3, 2),
                cache_key("C:/a/b.fits", None, 1, 4), cache_key("C:/a/b.fits", None, 1, 2, "FLAT")}) == 7


def test_no_folder_caches_nothing(m106):
    assert ThumbnailCache(None).thumbnail(m106)[:2] == b"\xff\xd8"  # still a JPEG


@pytest.mark.parametrize("make_bad", [
    pytest.param(lambda tmp: shutil.copy(SMALL / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.wcs.fits",
                                         tmp / "header_only.fits"), id="header only"),
    pytest.param(lambda tmp: (tmp / "corrupt.fits").write_bytes(b"not a FITS file " * 400) and tmp / "corrupt.fits",
                 id="corrupt"),
    pytest.param(lambda tmp: tmp / "missing.fits", id="missing"),
])
def test_an_unreadable_file_raises_and_caches_nothing(tmp_path, make_bad):
    bad = make_bad(tmp_path)
    cache = ThumbnailCache(tmp_path / "thumbs")
    with pytest.raises(Exception):
        cache.thumbnail(bad)
    assert not _entries(tmp_path / "thumbs")


def test_the_loader_remembers_a_failure(qapp, tmp_path):
    from photonfinder.ui.ThumbnailView import ThumbnailLoader
    loader = ThumbnailLoader(tmp_path / "thumbs")
    bad = str(tmp_path / "missing.fits")
    key = (bad, None, 0, 0)

    loader.request(key, bad, None)
    loader.pool.waitForDone()
    QApplication.processEvents()

    assert loader.pixmap(key) is ThumbnailLoader.FAILED
    loader.request(key, bad, None)  # a repaint asks again
    assert loader.pending_count() == 0
    assert not _entries(tmp_path / "thumbs")
    loader.shutdown()


def test_the_loader_drops_requests_out_of_view(qapp, tmp_path, m106):
    from photonfinder.ui.ThumbnailView import ThumbnailLoader
    loader = ThumbnailLoader(tmp_path / "thumbs")
    loader.pool.setMaxThreadCount(0)  # the workers wait in the pool's queue meanwhile
    for n in range(5):
        loader.request((str(m106), n), str(m106), None, token=n)
    loader.retain(lambda token: token == 3)
    loader.pool.setMaxThreadCount(3)
    loader.pool.waitForDone()
    QApplication.processEvents()

    made = [n for n in range(5) if loader.pixmap((str(m106), n)) not in (None, ThumbnailLoader.FAILED)]
    assert made == [3]
    loader.shutdown()


def test_a_processed_image_is_not_stretched_again():
    from photonfinder.image_hdus import load_hdu
    from photonfinder.image_processing import normalize_to_uint16
    data, header = load_hdu(str(SMALL / "rev2.xisf"), None)
    original = normalize_to_uint16(data, header) >> 8  # what it looks like unstretched, in 8 bits

    pixels = render_thumbnail(SMALL / "rev2.xisf")

    assert abs(float(pixels.mean()) - float(original.mean())) < 3


def test_clearing_the_cache_deletes_only_its_thumbnails(tmp_path):
    path = write_multi_hdu_fits(tmp_path / "draco.fits")
    folder = tmp_path / "thumbs"
    cache = ThumbnailCache(folder)
    cache.thumbnail(path, 0)
    cache.thumbnail(path, 1)
    stranger = folder / "keep.txt"
    stranger.write_text("not ours")
    count, size = cache.usage()
    assert count == 2 and size > 0

    assert cache.clear() == (count, size)

    assert cache.usage() == (0, 0)
    assert sorted(p.name for p in folder.rglob("*")) == ["keep.txt"]
    assert cache.thumbnail(path, 0)  # made again
    assert cache.usage()[0] == 1


def test_clearing_a_missing_cache_does_nothing(tmp_path):
    assert ThumbnailCache(tmp_path / "never made").clear() == (0, 0)
    assert ThumbnailCache(None).clear() == (0, 0)


def test_a_linear_thumbnail_looks_like_the_viewer_shrunk(global_test_data_dir):
    """The viewer's stretch of the full frame, shrunk to the thumbnail's size, is what the
    thumbnail should look like. Measuring the stretch on the shrunk frame instead clips the
    black point far harsher: 11.6 levels off on this frame, against 2.8."""
    import cv2
    from photonfinder.image_hdus import load_hdu
    from photonfinder.image_processing import (debayer_opencv, detect_bayer_pattern, normalize_to_uint16,
                                               stretch_uint16_to_uint8)
    data, header = load_hdu(str(global_test_data_dir / M106), None)
    rgb = debayer_opencv(normalize_to_uint16(data, header), detect_bayer_pattern(header))
    viewer = stretch_uint16_to_uint8(rgb, 0.25, 2.8, False)  # the viewer's defaults

    thumbnail = render_thumbnail(global_test_data_dir / M106)

    expected = cv2.resize(viewer, (thumbnail.shape[1], thumbnail.shape[0]), interpolation=cv2.INTER_AREA)
    assert np.abs(thumbnail.astype(float) - expected).mean() < 6


def test_a_mosaic_with_no_bayer_pattern_is_not_black(tmp_path):
    """A CFA master flat whose header names no Bayer pattern is shown as mono, mosaic and all.
    Its stretch's black point sits just under the green pixels; shrinking before stretching
    averaged each 2x2 cell to below it, and the thumbnail came out black."""
    from astropy.io import fits
    rng = np.random.default_rng(1)
    h, w = 600, 900
    data = np.empty((h, w), dtype=np.float32)
    data[0::2, 0::2] = 9400   # R
    data[0::2, 1::2] = 21900  # G
    data[1::2, 0::2] = 21900  # G
    data[1::2, 1::2] = 20800  # B
    data = (data + rng.normal(0, 60, (h, w))) / 65535
    path = tmp_path / "mf.fits"
    fits.PrimaryHDU(data.astype(np.float32)).writeto(path)

    pixels = render_thumbnail(path, image_type="MASTER FLAT")

    assert pixels.ndim == 2 and max(pixels.shape) == THUMBNAIL_SIZE
    assert np.median(pixels) > 20

"""A picture's pixels: the one decode (pictures.load_picture), the image loaders built on it, and
its thumbnail, which is never stretched."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from astropy.io.fits import Header

from photonfinder import image_processing, pictures
from photonfinder.image_hdus import load_hdu, read_hdu
from photonfinder.image_processing import should_stretch, uint16_to_uint8
from photonfinder.thumbnails import render_thumbnail

NON_ASCII = "Ñébula ✓"


def _write(path: Path, pixels: np.ndarray, *params) -> Path:
    """cv2.imwrite can't write to a non-ASCII path on Windows; imencode + tofile can."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(path.suffix, pixels, list(params))
    assert ok
    data.tofile(str(path))
    return path


def _jpeg_with_exif_orientation(path: Path, bgr: np.ndarray, orientation: int) -> Path:
    """A JPEG with an EXIF APP1 segment holding only the Orientation tag (0x0112). Neither
    OpenCV nor Qt writes one (Qt's setTransformation rotates the pixels instead)."""
    import struct
    ok, jpeg = cv2.imencode(".jpg", bgr)
    assert ok
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8)  # big endian, first IFD at offset 8
    tiff += struct.pack(">H", 1) + struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0) + struct.pack(">I", 0)
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    data = jpeg.tobytes()
    path.write_bytes(data[:2] + app1 + data[2:])  # right after SOI
    return path


def _rgb_bgr(height=30, width=40, dtype=np.uint8):
    """A test card, as RGB (channels first) and as the BGR OpenCV writes. Rows and channels
    differ, so a flip or a channel swap shows."""
    top = np.iinfo(dtype).max
    rgb = np.zeros((3, height, width), dtype)
    rgb[0, :height // 3] = top  # red band at the top
    rgb[2, -height // 3:] = top // 2  # blue band at the bottom
    rgb[1, :, : width // 4] = top // 3  # green band on the left
    return rgb, np.ascontiguousarray(rgb[::-1].transpose(1, 2, 0))


class TestLoadPicture:
    def test_8_bit_png_rgb(self, tmp_path):
        rgb, bgr = _rgb_bgr()
        pixels = pictures.load_picture(_write(tmp_path / "a.png", bgr))
        assert pixels.dtype == np.uint8 and pixels.shape == (3, 30, 40)
        np.testing.assert_array_equal(pixels, rgb)

    def test_16_bit_png_rgb_kept_16_bit(self, tmp_path):
        rgb, bgr = _rgb_bgr(dtype=np.uint16)
        pixels = pictures.load_picture(_write(tmp_path / "a.png", bgr))
        assert pixels.dtype == np.uint16
        np.testing.assert_array_equal(pixels, rgb)

    def test_mono_png_8_and_16_bit(self, tmp_path):
        for dtype in (np.uint8, np.uint16):
            mono = np.arange(30 * 40, dtype=np.uint32).reshape(30, 40).astype(dtype)
            pixels = pictures.load_picture(_write(tmp_path / f"m{np.dtype(dtype).itemsize}.png", mono))
            assert pixels.dtype == dtype and pixels.shape == (30, 40)
            np.testing.assert_array_equal(pixels, mono)

    def test_jpeg_rgb_top_row_first(self, tmp_path):
        rgb, bgr = _rgb_bgr()
        pixels = pictures.load_picture(_write(tmp_path / "a.jpg", bgr, cv2.IMWRITE_JPEG_QUALITY, 100))
        assert pixels.dtype == np.uint8 and pixels.shape == (3, 30, 40)
        assert np.abs(pixels.astype(int) - rgb).mean() < 5  # lossy at the edges, but no flip or channel swap
        assert pixels[0, 2].mean() > 200 and pixels[2, -2].mean() > 100 and pixels[0, -2].mean() < 30

    def test_alpha_is_dropped(self, tmp_path):
        rgb, bgr = _rgb_bgr()
        bgra = np.dstack([bgr, np.full(bgr.shape[:2], 128, np.uint8)])
        pixels = pictures.load_picture(_write(tmp_path / "a.png", bgra))
        np.testing.assert_array_equal(pixels, rgb)

    def test_a_non_ascii_path(self, tmp_path):
        rgb, bgr = _rgb_bgr()
        pixels = pictures.load_picture(_write(tmp_path / NON_ASCII / f"{NON_ASCII}.png", bgr))
        np.testing.assert_array_equal(pixels, rgb)

    def test_exif_orientation_is_ignored(self, tmp_path):
        """A JPEG saying "rotate 90°" keeps its stored pixel grid: a plate solution refers to it."""
        from PySide6.QtGui import QImageIOHandler, QImageReader
        path = _jpeg_with_exif_orientation(tmp_path / "rotated.jpg", _rgb_bgr()[1], orientation=6)
        # The file must carry the orientation for this test to mean anything:
        assert QImageReader(str(path)).transformation() == QImageIOHandler.Transformation.TransformationRotate90
        assert cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR).shape == (40, 30, 3)

        assert pictures.load_picture(path).shape == (3, 30, 40)
        assert pictures.picture_size(path) == (40, 30)

    def test_not_a_picture_raises(self, tmp_path):
        bad = tmp_path / "broken.jpg"
        bad.write_bytes(b"not a jpeg")
        with pytest.raises(ValueError, match="broken.jpg"):
            pictures.load_picture(bad)


class TestLoaders:
    def test_load_hdu_gives_the_pixels_and_no_header(self, tmp_path):
        rgb, bgr = _rgb_bgr()
        path = _write(tmp_path / "a.png", bgr)
        for hdu in (None, 0):
            data, header = load_hdu(path, hdu)
            np.testing.assert_array_equal(data, rgb)
            assert header == {}

    def test_a_picture_has_no_second_image(self, tmp_path):
        path = _write(tmp_path / "a.png", _rgb_bgr()[1])
        with pytest.raises(ValueError, match="one image"):
            load_hdu(path, 1)

    def test_read_hdu_gives_an_empty_fits_header(self, tmp_path):
        rgb, bgr = _rgb_bgr()
        data, header = read_hdu(_write(tmp_path / "a.png", bgr))
        np.testing.assert_array_equal(data, rgb)
        assert isinstance(header, Header) and len(header) == 0

    def test_the_viewer_loader_caches_it(self, tmp_path):
        rgb, bgr = _rgb_bgr()
        path = str(_write(tmp_path / "a.png", bgr))
        image_processing.image_cache.clear()
        try:
            data, _ = image_processing.load_image_data(path)
            np.testing.assert_array_equal(data, rgb)
            assert image_processing.image_cache.get(path, None) is not None
        finally:
            image_processing.image_cache.clear()


def _dark_sky(height=300, width=400) -> np.ndarray:
    """16-bit mono that looks linear: a dark background and a few stars."""
    rng = np.random.default_rng(3)
    sky = rng.normal(1500, 30, (height, width)).clip(0, 65535).astype(np.uint16)
    sky[rng.integers(0, height, 40), rng.integers(0, width, 40)] = 60000
    return sky


class TestNeverStretched:
    def test_should_stretch_says_no_for_a_picture(self):
        sky = _dark_sky()
        assert image_processing.is_linear(sky)
        assert should_stretch(sky, None)
        assert not should_stretch(sky, pictures.PICTURE_TYPE)

    @pytest.mark.parametrize("image_type", [pictures.PICTURE_TYPE, None, "LIGHT"])
    def test_the_thumbnail_is_the_shrunk_decoded_picture(self, tmp_path, image_type):
        """Whatever type the caller passes: a picture is never stretched, even when it looks linear."""
        sky = _dark_sky()
        path = _write(tmp_path / "sky.png", sky)

        thumbnail = render_thumbnail(path, image_type=image_type)

        expected = cv2.resize(uint16_to_uint8(sky), (200, 150), interpolation=cv2.INTER_AREA)
        np.testing.assert_array_equal(thumbnail, expected)

    def test_an_rgb_thumbnail_keeps_its_colours_and_rows(self, tmp_path):
        rgb, bgr = _rgb_bgr(height=300, width=400)
        thumbnail = render_thumbnail(_write(tmp_path / "card.png", bgr), image_type=pictures.PICTURE_TYPE)

        expected = cv2.resize(np.ascontiguousarray(rgb.transpose(1, 2, 0)), (200, 150), interpolation=cv2.INTER_AREA)
        np.testing.assert_array_equal(thumbnail, expected)
        assert thumbnail[5, 100, 0] == 255  # red at the top

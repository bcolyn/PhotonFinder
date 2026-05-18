"""Unit tests for photonfinder.image_analysis."""

import math

import numpy as np
import pytest

from photonfinder.image_analysis import (
    CALIBRATION_TYPES,
    ImageAnalysisResult,
    _to_float32,
    analyze_image,
)
from photonfinder.platesolver import select_first_channel


class TestHelpers:
    def test_select_first_channel_2d_passthrough(self):
        arr = np.zeros((100, 100), dtype=np.float32)
        assert select_first_channel(arr) is arr

    def test_select_first_channel_3d_returns_first_plane(self):
        arr = np.zeros((3, 100, 100), dtype=np.float32)
        result = select_first_channel(arr)
        assert result.shape == (100, 100)

    def test_select_first_channel_invalid_shape_raises(self):
        with pytest.raises(ValueError):
            select_first_channel(np.zeros(100))

    def test_to_float32_converts_uint16(self):
        arr = np.array([[0, 32768, 65535]], dtype=np.uint16)
        result = _to_float32(arr)
        assert result.dtype == np.float32
        assert result.flags["C_CONTIGUOUS"]

    def test_to_float32_already_float32_no_copy(self):
        arr = np.ascontiguousarray(np.zeros((10, 10), dtype=np.float32))
        result = _to_float32(arr)
        assert result is arr  # fast path: no copy

    def test_to_float32_non_contiguous_copies(self):
        arr = np.zeros((10, 10), dtype=np.float32)[::2]  # non-contiguous slice
        result = _to_float32(arr)
        assert result.dtype == np.float32
        assert result.flags["C_CONTIGUOUS"]


class TestFormulas:
    def test_fwhm_formula_circular_star(self):
        a = b = 2.0
        expected = 2.3548 * math.sqrt((a ** 2 + b ** 2) / 2.0)
        a_arr = np.array([a])
        b_arr = np.array([b])
        actual = float(2.3548 * np.sqrt((a_arr ** 2 + b_arr ** 2) / 2.0)[0])
        assert abs(actual - expected) < 1e-6

    def test_elongation_formula(self):
        a, b = 3.0, 1.5
        expected = a / b
        actual = float(np.array([a])[0] / np.array([b])[0])
        assert abs(actual - expected) < 1e-6


class TestAnalyzeImage:
    @pytest.fixture
    def flat_image(self):
        rng = np.random.default_rng(42)
        return rng.normal(1000.0, 30.0, (256, 256)).astype(np.float32)

    @pytest.fixture
    def starfield_image(self):
        rng = np.random.default_rng(7)
        data = rng.normal(1000.0, 30.0, (512, 512)).astype(np.float32)
        sigma = 2.0
        for _ in range(20):
            cx = int(rng.integers(50, 462))
            cy = int(rng.integers(50, 462))
            peak = float(rng.uniform(5000, 20000))
            y, x = np.ogrid[cy - 20:cy + 21, cx - 20:cx + 21]
            data[cy - 20:cy + 21, cx - 20:cx + 21] += (
                peak * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma ** 2))
            ).astype(np.float32)
        return data

    def test_flat_image_background_close_to_1000(self, flat_image):
        result = analyze_image(flat_image)
        assert result.background_median is not None
        assert 900 < result.background_median < 1100

    def test_flat_image_background_rms_reasonable(self, flat_image):
        result = analyze_image(flat_image)
        assert result.background_rms is not None
        assert 5 < result.background_rms < 100

    def test_flat_image_few_or_no_stars(self, flat_image):
        result = analyze_image(flat_image)
        assert result.star_count is not None
        assert result.star_count < 10

    def test_starfield_detects_stars(self, starfield_image):
        result = analyze_image(starfield_image)
        assert result.error is None
        assert result.star_count is not None and result.star_count > 0

    def test_starfield_fwhm_plausible(self, starfield_image):
        result = analyze_image(starfield_image)
        assert result.fwhm_median is not None
        # Gaussian sigma=2 → FWHM ≈ 4.71 px
        assert 3.0 < result.fwhm_median < 9.0

    def test_starfield_elongation_near_one(self, starfield_image):
        result = analyze_image(starfield_image)
        assert result.elongation_median is not None
        assert 1.0 <= result.elongation_median < 1.5

    def test_3d_channels_first_input(self, starfield_image):
        data_3d = np.stack([starfield_image] * 3, axis=0)
        result = analyze_image(data_3d)
        assert result.error is None
        assert result.star_count is not None and result.star_count > 0

    def test_error_on_1d_input(self):
        result = analyze_image(np.zeros(10))
        assert result.error is not None

    def test_result_is_dataclass(self, flat_image):
        result = analyze_image(flat_image)
        assert isinstance(result, ImageAnalysisResult)

    def test_no_detection_skips_source_extraction(self, starfield_image):
        result = analyze_image(starfield_image, detect_sources=False)
        assert result.background_median is not None
        assert result.background_rms is not None
        assert result.star_count is None
        assert result.fwhm_median is None
        assert result.elongation_median is None

    def test_calibration_types_set(self):
        assert "DARK" in CALIBRATION_TYPES
        assert "FLAT" in CALIBRATION_TYPES
        assert "BIAS" in CALIBRATION_TYPES
        assert "MASTER DARK" in CALIBRATION_TYPES
        assert "MASTER FLAT" in CALIBRATION_TYPES
        assert "MASTER BIAS" in CALIBRATION_TYPES
        assert "LIGHT" not in CALIBRATION_TYPES

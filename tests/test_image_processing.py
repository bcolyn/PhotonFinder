import numpy as np
import pytest

from photonfinder.image_processing import is_linear


def _make_linear(background: float, n: int = 200_000, rng=None) -> np.ndarray:
    """Unprocessed sky image: very tight background peak + 0.1 % sparse stars.

    The background sigma is kept tiny (typical readout + photon noise) regardless
    of background level, reflecting a raw linear frame before any processing.
    """
    if rng is None:
        rng = np.random.default_rng(42)
    sigma = max(0.0005, background * 0.02)  # ~2 % of background, noise-floor bounded
    pixels = rng.normal(background, sigma, n)
    n_stars = max(1, n // 1000)
    pixels[rng.choice(n, n_stars, replace=False)] = rng.uniform(0.8, 1.0, n_stars)
    return np.clip(pixels, 0.0, 1.0)


def _make_stretched(background: float, content_frac: float = 0.08, n: int = 200_000,
                    rng=None) -> np.ndarray:
    """Processed sky image: noise-reduced tight background peak + extended content.

    ``content_frac`` is the fraction of pixels that are "extended content"
    (galaxies, nebulae) sitting well above the background.  The background
    sigma is very small to mimic post-noise-reduction data.
    """
    if rng is None:
        rng = np.random.default_rng(42)
    n_content = int(n * content_frac)
    n_bg = n - n_content
    sigma_bg = max(0.0005, background * 0.005)  # noise-reduced: very tight
    bg_pixels = rng.normal(background, sigma_bg, n_bg)
    content_high = min(1.0, background + 0.50)
    content_low  = background + 0.05
    content_pixels = rng.uniform(content_low, content_high, n_content)
    pixels = np.concatenate([bg_pixels, content_pixels])
    rng.shuffle(pixels)
    return np.clip(pixels, 0.0, 1.0)


def _to_uint16(norm: np.ndarray) -> np.ndarray:
    return (norm * 65535).astype(np.uint16)


# ---------------------------------------------------------------------------
# Linear cases: any background level, only sparse stars above it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,background", [
    ("dark_site",      0.01),
    ("suburban",       0.08),
    ("light_polluted", 0.25),
])
def test_is_linear_true(label, background):
    data = _to_uint16(_make_linear(background))
    assert is_linear(data), f"{label}: expected linear"


# ---------------------------------------------------------------------------
# Processed cases: significant extended content above background
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,background,content_frac", [
    ("normally_stretched",       0.25, 0.15),
    ("sparse_galaxies_at_010",   0.10, 0.05),  # user's failing case
    ("sparse_galaxies_at_005",   0.05, 0.05),
    ("heavily_stretched",        0.40, 0.20),
])
def test_is_linear_false(label, background, content_frac):
    data = _to_uint16(_make_stretched(background, content_frac))
    assert not is_linear(data), f"{label}: expected not linear"


# ---------------------------------------------------------------------------
# dtype coverage
# ---------------------------------------------------------------------------

def test_is_linear_float32_linear():
    pixels = _make_linear(0.02).astype(np.float32)
    assert is_linear(pixels)


def test_is_linear_float32_stretched():
    pixels = _make_stretched(0.10, 0.08).astype(np.float32)
    assert not is_linear(pixels)


def test_is_linear_rgb_shape():
    """3-D (3, H, W) uint16 arrays from debayering are handled."""
    rng = np.random.default_rng(1)
    channels = np.stack([_to_uint16(_make_linear(0.02, n=90_000, rng=rng)) for _ in range(3)])
    data = channels.reshape(3, 300, 300)
    assert is_linear(data)


def test_is_linear_all_zeros():
    data = np.zeros((100, 100), dtype=np.uint16)
    assert is_linear(data)


# ---------------------------------------------------------------------------
# normalize_to_uint16 / loading: every supported pixel layout is visible
# ---------------------------------------------------------------------------

from photonfinder.image_processing import (  # noqa: E402
    normalize_to_uint16, detect_image_type, load_image_data, image_cache,
    stretch_uint16_to_uint8,
)


def test_normalize_float_with_nan_uses_the_other_pixels():
    data = np.full((8, 8), 0.5, dtype=np.float32)
    data[0, 0] = np.nan
    out = normalize_to_uint16(data, {'BITPIX': -32})
    assert out[0, 0] == 0
    assert abs(int(out[1, 1]) - 0.5 * 65535) <= 1


def test_normalize_float_beyond_unit_range_with_nan():
    data = np.linspace(0, 40000, 64, dtype=np.float32).reshape(8, 8)
    data[0, 0] = np.nan
    out = normalize_to_uint16(data, {'BITPIX': -32})
    assert out[0, 0] == 0
    assert out.max() == 65535


def test_normalize_signed_int32_uses_its_range():
    data = np.zeros((8, 8), dtype=np.int32)
    data[4, 4] = 30000
    out = normalize_to_uint16(data, {'BITPIX': 32})
    assert out[4, 4] == 65535
    assert out[0, 0] == 0


def test_normalize_signed_int32_with_negative_values():
    data = np.array([[-1000, 0], [1000, 3000]], dtype=np.int32)
    out = normalize_to_uint16(data, {'BITPIX': 32})
    assert out[0, 0] == 0
    assert out[1, 1] == 65535
    assert out[0, 0] < out[0, 1] < out[1, 0] < out[1, 1]


def test_normalize_uint8_without_bitpix_uses_8_bits():
    """XISF headers carry no BITPIX: the dtype, not the 16-bit default, sets the scale."""
    data = np.array([[0, 128], [200, 255]], dtype=np.uint8)
    out = normalize_to_uint16(data, {})
    assert out[1, 1] == 65535
    assert out[0, 1] == 128 * 257


def test_normalize_uint8_of_only_zeros_and_ones_is_not_treated_as_unit_float():
    data = np.array([[0, 1], [1, 0]], dtype=np.uint8)
    out = normalize_to_uint16(data, {})
    assert out.max() == 257


def test_normalize_uint32_without_bitpix_does_not_clip():
    data = np.array([[0, 2 ** 31], [3 * 2 ** 30, 2 ** 32 - 1]], dtype=np.uint32)
    out = normalize_to_uint16(data, {})
    assert out[1, 1] == 65535
    assert 32000 < out[0, 1] < 33000
    assert out[0, 1] < out[1, 0] < out[1, 1]


def test_fits_with_a_single_plane_cube_loads_as_mono(tmp_path):
    from astropy.io import fits
    path = tmp_path / "cube.fits"
    fits.PrimaryHDU(np.arange(64, dtype=np.uint16).reshape(1, 8, 8)).writeto(path)
    image_cache.clear()
    try:
        data, header = load_image_data(path)
    finally:
        image_cache.clear()
    assert data.shape == (8, 8)
    norm = normalize_to_uint16(data, header)
    assert detect_image_type(norm, header) == 'mono'
    assert stretch_uint16_to_uint8(norm, 0.25, 2.8, False).shape == (8, 8)

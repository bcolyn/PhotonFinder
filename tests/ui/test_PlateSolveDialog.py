import json
from unittest.mock import MagicMock, patch

import pytest

from photonfinder.ui.PlateSolveDialog import (
    PlateSolveDialog, _infer_scale_from_header, _infer_scale_from_wcs,
)
from tests.sample_headers import wcs_header_m106
from tests.utils import fix_embedded_header

dedupe = PlateSolveDialog._dedupe_scales
otsu   = PlateSolveDialog._otsu_threshold


def _mock_file(name="image.fits"):
    f = MagicMock()
    f.name = name
    return f


# ---------------------------------------------------------------------------
# _infer_scale_from_header
# ---------------------------------------------------------------------------

def _call_infer_scale(header_dict, filename="image.fits"):
    file = _mock_file(filename)
    mock_fh = MagicMock()
    mock_fh.header = b"ignored"
    with patch("photonfinder.ui.PlateSolveDialog.FitsHeader") as mock_FH, \
         patch("photonfinder.ui.PlateSolveDialog.decompress", return_value=b"raw"), \
         patch("photonfinder.ui.PlateSolveDialog.parse_FITS_header", return_value=header_dict):
        mock_FH.get.return_value = mock_fh
        return _infer_scale_from_header(file)


def test_infer_scale_from_scale_keyword():
    assert _call_infer_scale({"SCALE": "1.234"}) == pytest.approx(1.234)


def test_infer_scale_from_pixscale_keyword():
    assert _call_infer_scale({"PIXSCALE": "0.654"}) == pytest.approx(0.654)


def test_infer_scale_scale_takes_priority_over_pixscale():
    assert _call_infer_scale({"SCALE": "1.0", "PIXSCALE": "2.0"}) == pytest.approx(1.0)


def test_infer_scale_from_focal_len_and_pixsz():
    result = _call_infer_scale({"FOCALLEN": "400", "YPIXSZ": "4.63"})
    expected = round(206.265 * 4.63 / 400, 3)
    assert result == pytest.approx(expected)


def test_infer_scale_rounded_to_3dp():
    result = _call_infer_scale({"FOCALLEN": "400", "YPIXSZ": "4.63"})
    assert result == round(result, 3)


def test_infer_scale_no_keywords_returns_none():
    assert _call_infer_scale({"OBJECT": "M57"}) is None


def test_infer_scale_focallen_without_pixsz_returns_none():
    assert _call_infer_scale({"FOCALLEN": "400"}) is None


def test_infer_scale_pixsz_without_focallen_returns_none():
    assert _call_infer_scale({"YPIXSZ": "4.63"}) is None


def test_infer_scale_db_exception_returns_none():
    file = _mock_file()
    with patch("photonfinder.ui.PlateSolveDialog.FitsHeader") as mock_FH:
        mock_FH.get.side_effect = Exception("not found")
        assert _infer_scale_from_header(file) is None


def test_infer_scale_xisf_uses_xisf_parser():
    file = _mock_file("image.xisf")
    mock_fh = MagicMock()
    mock_fh.header = b"ignored"
    header_mock = {"SCALE": "1.5"}
    with patch("photonfinder.ui.PlateSolveDialog.FitsHeader") as mock_FH, \
         patch("photonfinder.ui.PlateSolveDialog.decompress", return_value=json.dumps({}).encode()), \
         patch("photonfinder.ui.PlateSolveDialog.header_from_xisf_dict", return_value=header_mock) as mock_xisf, \
         patch("photonfinder.ui.PlateSolveDialog.parse_FITS_header") as mock_fits:
        mock_FH.get.return_value = mock_fh
        result = _infer_scale_from_header(file)
    assert result == pytest.approx(1.5)
    mock_xisf.assert_called_once()
    mock_fits.assert_not_called()


def test_infer_scale_fits_file_uses_fits_parser():
    file = _mock_file("image.fits")
    mock_fh = MagicMock()
    mock_fh.header = b"ignored"
    with patch("photonfinder.ui.PlateSolveDialog.FitsHeader") as mock_FH, \
         patch("photonfinder.ui.PlateSolveDialog.decompress", return_value=b"raw"), \
         patch("photonfinder.ui.PlateSolveDialog.parse_FITS_header", return_value={"SCALE": "0.5"}) as mock_fits, \
         patch("photonfinder.ui.PlateSolveDialog.header_from_xisf_dict") as mock_xisf:
        mock_FH.get.return_value = mock_fh
        _infer_scale_from_header(file)
    mock_fits.assert_called_once()
    mock_xisf.assert_not_called()


# ---------------------------------------------------------------------------
# _infer_scale_from_wcs
# ---------------------------------------------------------------------------

def test_infer_scale_from_wcs_no_record_returns_none():
    file = _mock_file()
    with patch("photonfinder.ui.PlateSolveDialog.FileWCS") as mock_FileWCS:
        mock_FileWCS.get_or_none.return_value = None
        assert _infer_scale_from_wcs(file) is None


def test_infer_scale_from_wcs_valid_header():
    file = _mock_file()
    raw_wcs = fix_embedded_header(wcs_header_m106)
    mock_rec = MagicMock()
    with patch("photonfinder.ui.PlateSolveDialog.FileWCS") as mock_FileWCS, \
         patch("photonfinder.ui.PlateSolveDialog.decompress", return_value=raw_wcs):
        mock_FileWCS.get_or_none.return_value = mock_rec
        result = _infer_scale_from_wcs(file)
    # wcs_header_m106: CDELT ~3.4455e-4 deg/pix → ~1.240 arcsec/pix
    assert result is not None
    assert result == pytest.approx(1.240, abs=0.01)


def test_infer_scale_from_wcs_result_is_positive():
    file = _mock_file()
    raw_wcs = fix_embedded_header(wcs_header_m106)
    mock_rec = MagicMock()
    with patch("photonfinder.ui.PlateSolveDialog.FileWCS") as mock_FileWCS, \
         patch("photonfinder.ui.PlateSolveDialog.decompress", return_value=raw_wcs):
        mock_FileWCS.get_or_none.return_value = mock_rec
        result = _infer_scale_from_wcs(file)
    assert result > 0


def test_infer_scale_from_wcs_rounded_to_3dp():
    file = _mock_file()
    raw_wcs = fix_embedded_header(wcs_header_m106)
    mock_rec = MagicMock()
    with patch("photonfinder.ui.PlateSolveDialog.FileWCS") as mock_FileWCS, \
         patch("photonfinder.ui.PlateSolveDialog.decompress", return_value=raw_wcs):
        mock_FileWCS.get_or_none.return_value = mock_rec
        result = _infer_scale_from_wcs(file)
    assert result == round(result, 3)


def test_infer_scale_from_wcs_db_exception_returns_none():
    file = _mock_file()
    with patch("photonfinder.ui.PlateSolveDialog.FileWCS") as mock_FileWCS:
        mock_FileWCS.get_or_none.side_effect = Exception("db error")
        assert _infer_scale_from_wcs(file) is None


def test_infer_scale_from_wcs_decompress_exception_returns_none():
    file = _mock_file()
    mock_rec = MagicMock()
    with patch("photonfinder.ui.PlateSolveDialog.FileWCS") as mock_FileWCS, \
         patch("photonfinder.ui.PlateSolveDialog.decompress", side_effect=Exception("corrupt")):
        mock_FileWCS.get_or_none.return_value = mock_rec
        assert _infer_scale_from_wcs(file) is None


# ---------------------------------------------------------------------------
# _otsu_threshold
# ---------------------------------------------------------------------------

def test_otsu_clear_separation():
    # One count is clearly noise compared to the rest
    assert otsu([1, 100, 120]) > 1

def test_otsu_similar_counts_returns_zero():
    # 40 vs 50 — both legitimate, should not threshold
    assert otsu([40, 50]) == 0

def test_otsu_single_value_returns_zero():
    assert otsu([42]) == 0

def test_otsu_all_equal_returns_zero():
    assert otsu([10, 10, 10]) == 0

def test_otsu_threshold_below_noise():
    # Threshold should be between 2 and 80
    t = otsu([2, 80, 95])
    assert 2 <= t < 80


# ---------------------------------------------------------------------------
# _dedupe_scales
# ---------------------------------------------------------------------------

def test_dedupe_empty():
    assert dedupe({}) == []

def test_dedupe_single():
    assert dedupe({1.5: 10}) == [1.5]

def test_dedupe_all_same_band():
    # 1.23, 1.24, 1.25 — all within 30% of each other → collapse to highest count
    result = dedupe({1.23: 2, 1.24: 45, 1.25: 3})
    assert result == [1.24]

def test_dedupe_two_distinct_setups():
    # 1.24 and 2.5 are more than 30% apart → both kept
    result = dedupe({1.24: 100, 2.50: 80})
    assert result == [1.24, 2.50]

def test_dedupe_30pct_boundary_exact():
    # scale * 1.30 == next scale → new band starts
    result = dedupe({1.00: 10, 1.30: 10})
    assert result == [1.00, 1.30]

def test_dedupe_just_inside_30pct():
    # 1.29 / 1.00 = 1.29 < 1.30 → same band
    result = dedupe({1.00: 5, 1.29: 5})
    assert len(result) == 1

def test_dedupe_winner_in_band_is_highest_count():
    result = dedupe({1.20: 3, 1.25: 50, 1.28: 4})
    assert result == [1.25]

def test_dedupe_otsu_removes_rare_outlier():
    # 0.5 has only 1 solve; 1.24 and 2.5 each have 80+ → Otsu should drop 0.5
    result = dedupe({0.50: 1, 1.24: 100, 2.50: 80})
    assert 0.50 not in result
    assert 1.24 in result
    assert 2.50 in result

def test_dedupe_otsu_keeps_minority_if_not_noise():
    # Two legitimate setups used at different rates (4:1 ratio) — not noise
    result = dedupe({1.24: 20, 2.50: 80})
    assert 1.24 in result
    assert 2.50 in result

def test_dedupe_otsu_never_removes_all():
    # Even if Otsu fires, at least one scale survives
    result = dedupe({1.24: 1})
    assert result == [1.24]

def test_dedupe_band_counts_accumulate_before_otsu():
    # 1.23 (2) + 1.24 (3) merge into one band with total 5;
    # 3.00 (1) is a separate band with total 1.
    # Otsu should see [5, 1] and drop the band total of 1 (3.00).
    result = dedupe({1.23: 2, 1.24: 3, 3.00: 1})
    assert 3.00 not in result
    assert any(s in result for s in (1.23, 1.24))

def test_dedupe_three_distinct_setups_all_kept():
    # Three clearly different focal lengths, all well-used
    result = dedupe({0.50: 30, 1.24: 100, 2.50: 60})
    assert result == [0.50, 1.24, 2.50]

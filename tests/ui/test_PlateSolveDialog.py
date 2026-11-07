from unittest.mock import MagicMock, patch

import pytest

from photonfinder.ui.PlateSolveDialog import (
    PlateSolveDialog, _infer_scale_from_header, _infer_scale_from_wcs,
)
from tests.sample_headers import wcs_header_m106
from tests.utils import fix_embedded_header


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
         patch("photonfinder.ui.PlateSolveDialog.decode_header_blob", return_value=header_dict):
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


def test_infer_scale_xisf_uses_decoded_header():
    file = _mock_file("image.xisf")
    mock_fh = MagicMock()
    mock_fh.header = b"ignored"
    with patch("photonfinder.ui.PlateSolveDialog.FitsHeader") as mock_FH, \
         patch("photonfinder.ui.PlateSolveDialog.decode_header_blob",
               return_value={"SCALE": "1.5"}) as mock_decode:
        mock_FH.get.return_value = mock_fh
        result = _infer_scale_from_header(file)
    assert result == pytest.approx(1.5)
    mock_decode.assert_called_once_with(b"ignored")


def test_infer_scale_fits_file_uses_decoded_header():
    file = _mock_file("image.fits")
    mock_fh = MagicMock()
    mock_fh.header = b"ignored"
    with patch("photonfinder.ui.PlateSolveDialog.FitsHeader") as mock_FH, \
         patch("photonfinder.ui.PlateSolveDialog.decode_header_blob",
               return_value={"SCALE": "0.5"}) as mock_decode:
        mock_FH.get.return_value = mock_fh
        result = _infer_scale_from_header(file)
    assert result == pytest.approx(0.5)
    mock_decode.assert_called_once_with(b"ignored")


# ---------------------------------------------------------------------------
# _infer_scale_from_wcs
# ---------------------------------------------------------------------------

def _file_with_solution(name, blob=None):
    """A file in the test library, with `blob` stored as its plate solution when given."""
    from photonfinder.models import File, FileWCS, LibraryRoot
    root = LibraryRoot.get_or_create(name="r", path="/data/")[0]
    file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
    if blob is not None:
        FileWCS.create(file=file, wcs=blob)
    return file


def _m106_blob():
    from photonfinder.core import compress
    return compress(fix_embedded_header(wcs_header_m106))


def test_infer_scale_from_wcs_without_a_solution_is_none(context):
    assert _infer_scale_from_wcs(_file_with_solution("unsolved.fits")) is None


def test_infer_scale_from_wcs_reads_the_stored_solution(context):
    result = _infer_scale_from_wcs(_file_with_solution("m106.fits", _m106_blob()))

    # wcs_header_m106: CDELT ~3.4455e-4 deg/pix -> ~1.240 arcsec/pix, rounded to 3 decimals
    assert result == pytest.approx(1.240, abs=0.01)
    assert result == round(result, 3)


@pytest.mark.parametrize("blob", [b"not compressed", None])
def test_infer_scale_from_wcs_with_an_unusable_solution_is_none(context, blob):
    from photonfinder.core import compress
    stored = blob if blob is not None else compress(b"SIMPLE  =                    T" + b" " * 50 + b"END" + b" " * 77)
    assert _infer_scale_from_wcs(_file_with_solution("bad.fits", stored)) is None


# ---------------------------------------------------------------------------
# Scale hint inferred when the dialog opens
# ---------------------------------------------------------------------------

def _file_with_focal_length(name, focal_length_mm):
    """A file whose header implies 206.265 * 3.76 / focal length arcsec/px."""
    from astropy.io.fits import Header
    from photonfinder.core import compress
    from photonfinder.models import File, FitsHeader, Image, LibraryRoot
    root = LibraryRoot.get_or_create(name="r", path="/data/")[0]
    file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
    Image.create(file=file, image_type="LIGHT")
    header = Header()
    header["FOCALLEN"] = focal_length_mm
    header["YPIXSZ"] = 3.76
    FitsHeader.create(file=file, header=compress(header.tostring().encode("ascii")))
    return file


def _scale_hint_on_opening(context, qtbot, files):
    from photonfinder.models import SearchCriteria
    dialog = PlateSolveDialog(context, files, SearchCriteria())
    qtbot.addWidget(dialog)
    return dialog.hint_scale_spin.value()


def test_a_scale_beyond_the_hint_maximum_is_skipped_for_the_next_file(context, qtbot):
    wrong_focal_length = _file_with_focal_length("bad_focallen.fits", 1)  # 775"/px
    good = _file_with_focal_length("good.fits", 400)

    assert _scale_hint_on_opening(context, qtbot, [wrong_focal_length, good]) == 1.939


def test_only_scales_beyond_the_maximum_leave_the_hint_as_it_was(context, qtbot):
    no_hint = _scale_hint_on_opening(context, qtbot, [])
    wrong_focal_length = _file_with_focal_length("bad_focallen.fits", 1)

    assert _scale_hint_on_opening(context, qtbot, [wrong_focal_length]) == no_hint

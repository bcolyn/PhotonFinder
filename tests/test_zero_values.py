"""Gain 0, offset 0, 0 °C and RA/Dec 0.0 are real values, not "not set"."""
import pytest
from astropy.io.fits import Header

from photonfinder import mcp_server
from photonfinder.models import File, Image, LibraryRoot, SearchCriteria
from photonfinder.platesolver import ASTAPSolver, file_position


def _add(root, name, **image_values):
    file = File.create(root=root, path="./", name=name, size=1, mtime_millis=1)
    return Image.create(file=file, **image_values)


def _names(criteria: SearchCriteria) -> set[str]:
    rows, _, _ = mcp_server.run_search_files(criteria, 0, 100)
    return {row.name for row in rows}


@pytest.fixture
def root(context):
    return LibraryRoot.create(name="Main", path="/data/")


@pytest.fixture
def reference_frame(root):
    """A light at gain 0, offset 0, 0 °C, the frame the calibration searches start from."""
    return _add(root, "light.fits", image_type="LIGHT", camera="Cam", exposure=60.0, binning=1,
                gain=0, offset=0, set_temp=0.0)


def test_a_search_for_offset_zero_returns_only_offset_zero_frames(context, root):
    _add(root, "zero.fits", image_type="LIGHT", offset=0)
    _add(root, "ten.fits", image_type="LIGHT", offset=10)
    _add(root, "unknown.fits", image_type="LIGHT")

    result = mcp_server.query_search(context, {"offset": 0})

    assert [row["name"] for row in result["results"]] == ["zero.fits"]


def test_no_offset_in_the_criteria_still_means_any_offset(context, root):
    _add(root, "zero.fits", image_type="LIGHT", offset=0)
    _add(root, "unknown.fits", image_type="LIGHT")

    assert _names(SearchCriteria()) == {"zero.fits", "unknown.fits"}


def test_matching_darks_keep_gain_offset_and_temperature_of_zero(root, reference_frame):
    dark = dict(image_type="DARK", camera="Cam", exposure=60.0, binning=1)
    _add(root, "match.fits", **dark, gain=0, offset=0, set_temp=0.0)
    _add(root, "other_gain.fits", **dark, gain=100, offset=0, set_temp=0.0)
    _add(root, "other_offset.fits", **dark, gain=0, offset=10, set_temp=0.0)
    _add(root, "other_temp.fits", **dark, gain=0, offset=0, set_temp=-10.0)

    assert _names(SearchCriteria.find_dark(reference_frame)) == {"match.fits"}


def test_matching_bias_keeps_gain_and_offset_of_zero(root, reference_frame):
    bias = dict(image_type="BIAS", camera="Cam", binning=1)
    _add(root, "match.fits", **bias, gain=0, offset=0, set_temp=-10.0)
    _add(root, "other_gain.fits", **bias, gain=100, offset=0)
    _add(root, "other_offset.fits", **bias, gain=0, offset=10)

    assert _names(SearchCriteria.find_bias(reference_frame)) == {"match.fits"}


def test_a_reference_frame_without_values_leaves_them_unconstrained(root):
    frame = _add(root, "light.fits", image_type="LIGHT", camera="Cam", exposure=60.0, binning=1)

    criteria = SearchCriteria.find_dark(frame)

    assert (criteria.gain, criteria.offset, criteria.temperature) == ("", None, "")


@pytest.mark.parametrize("ra, dec", [(0.0, 45.0), (150.0, 0.0), (0.0, 0.0)])
def test_astap_hint_includes_a_position_with_a_zero_coordinate(ra, dec):
    hint = ASTAPSolver.create_hint(ra, dec)

    assert float(hint["-ra"]) == ra / 15
    assert float(hint["-spd"]) == 90 + dec


def test_astap_hint_has_no_position_without_one():
    assert "-ra" not in ASTAPSolver.create_hint(None, None)
    assert "-ra" not in ASTAPSolver.create_hint(150.0, None)


def test_astap_hint_from_a_file_at_zero():
    hint = ASTAPSolver().extract_hint(Header({"RA": 0.0, "DEC": 0.0, "CRVAL1": 150.0, "CRVAL2": 45.0}), None)

    assert (float(hint["-ra"]), float(hint["-spd"])) == (0.0, 90.0)


def test_position_from_an_image_at_zero_wins_over_the_header():
    header = Header({"RA": 150.0, "DEC": 45.0})

    assert file_position(header, Image(coord_ra=0.0, coord_dec=0.0)) == (0.0, 0.0)


def test_position_from_a_header_at_zero_wins_over_the_wcs_cards():
    header = Header({"RA": 0.0, "DEC": 0.0, "CRVAL1": 150.0, "CRVAL2": 45.0})

    assert file_position(header, None) == (0.0, 0.0)


@pytest.mark.parametrize("header, expected", [
    ({"CRVAL1": 150.0, "CRVAL2": 45.0}, (150.0, 45.0)),
    ({"RA": "", "DEC": "", "CRVAL1": 150.0, "CRVAL2": 45.0}, (150.0, 45.0)),
    ({}, (None, None)),
])
def test_position_falls_back_past_missing_header_values(header, expected):
    assert file_position(Header(header), Image()) == expected


def test_position_without_a_header_or_image():
    assert file_position(None, None) == (None, None)


def test_sky_coord_of_an_image_at_zero():
    coord = Image(coord_ra=0.0, coord_dec=0.0).get_sky_coord()

    assert (coord.ra.deg, coord.dec.deg) == (0.0, 0.0)


def test_sky_coord_of_an_image_without_a_position():
    assert Image().get_sky_coord() is None

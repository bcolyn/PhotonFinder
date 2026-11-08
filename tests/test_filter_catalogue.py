"""The filter catalogue: each filter's label, summary, reset and round trip, without Qt."""
import dataclasses
from datetime import date, datetime

import pytest

from photonfinder import filter_catalogue
from photonfinder.filter_catalogue import IMAGE_QUALITY
from photonfinder.models import DIAGNOSIS_BAD_VALUES, NO_PROJECT, RootAndPath, SearchCriteria


def _quality(**ranges) -> SearchCriteria:
    return SearchCriteria(exposure="300", **ranges)


def test_an_unset_filter_is_inactive_and_adds_nothing_to_the_summary():
    assert not IMAGE_QUALITY.is_active(SearchCriteria())
    assert str(SearchCriteria()) == ""


@pytest.mark.parametrize("ranges, label", [
    (dict(fwhm_max=3.5), "Quality: FWHM"),
    (dict(star_count_min=100, fwhm_min=2.0, fwhm_max=3.5), "Quality: Stars, FWHM"),
    (dict(elongation_max=1.3, background_rms_min=2.0), "Quality: Bg RMS, Elong."),
])
def test_the_chip_and_the_summary_name_the_constrained_metrics(ranges, label):
    criteria = _quality(**ranges)

    assert IMAGE_QUALITY.is_active(criteria)
    assert IMAGE_QUALITY.chip_label(criteria) == label
    assert IMAGE_QUALITY.summary(criteria) == label
    assert str(criteria) == f"300, {label}"


def test_reset_leaves_the_default_criteria_and_other_filters_alone():
    criteria = _quality(star_count_min=100, star_count_max=900, fwhm_max=3.5, background_min=1.0,
                        background_max=2.0, background_rms_max=0.5, elongation_min=1.0, elongation_max=1.5)

    IMAGE_QUALITY.reset(criteria)

    assert criteria == SearchCriteria(exposure="300")
    assert not IMAGE_QUALITY.is_active(criteria)


def test_a_saved_filter_comes_back_the_same():
    criteria = _quality(star_count_min=100, fwhm_min=2.0, fwhm_max=3.5, elongation_max=1.3)

    restored = SearchCriteria.from_json(criteria.to_json())

    assert {f: getattr(restored, f) for f in IMAGE_QUALITY.fields} == \
        {f: getattr(criteria, f) for f in IMAGE_QUALITY.fields}
    assert IMAGE_QUALITY.chip_label(restored) == IMAGE_QUALITY.chip_label(criteria)


def test_every_entry_controls_real_criteria_fields_and_no_field_has_two_owners():
    names = {f.name for f in dataclasses.fields(SearchCriteria)}
    owned = [field for entry in filter_catalogue.ENTRIES for field in entry.fields]
    assert set(owned) <= names
    assert len(owned) == len(set(owned))


def test_the_mcp_search_input_covers_every_criteria_field_or_says_why_not():
    from photonfinder.mcp_server import NOT_FOR_MCP, SearchCriteriaInput
    names = {f.name for f in dataclasses.fields(SearchCriteria)}
    advertised = set(SearchCriteriaInput.model_fields)

    assert advertised | set(NOT_FOR_MCP) == names
    assert not advertised & set(NOT_FOR_MCP)


def test_mcp_describes_a_field_as_its_catalogue_entry_does():
    from photonfinder.mcp_server import SearchCriteriaInput
    for entry in filter_catalogue.ENTRIES:
        for field, description in entry.mcp_descriptions.items():
            assert SearchCriteriaInput.model_fields[field].description == description


# --- every filter: label, summary, reset, round trip -------------------------------------------

START, END = datetime(2024, 1, 1, 22, 0), datetime(2024, 1, 2, 5, 30)

# entry: (criteria setting only that filter, chip label or None, summary fragment)
EVERY_FILTER = {
    "paths": (dict(paths=[RootAndPath(1, "Main", "lights"), RootAndPath(2, "Other", None)]), None, "Main/lights+1"),
    "type": (dict(type="LIGHT"), None, "LIGHT"),
    "filter": (dict(filter="Ha"), None, "Ha"),
    "camera": (dict(camera="ASI2600"), None, "ASI2600"),
    "file_name": (dict(file_name="m31_"), None, "m31_"),
    "object_name": (dict(object_name="M31"), None, "M31"),
    "exposure": (dict(exposure="300.0", exposure_tolerance=5.0), "Exposure: 300.0 s ±5 s", "300.0 ±5s"),
    "telescope": (dict(telescope="RC8"), "Telescope: RC8", "RC8"),
    "binning": (dict(binning="2"), "Binning: 2", "2"),
    "gain": (dict(gain="100", offset=10), "Gain: 100, Offset: 10", "100, offset 10"),
    "temperature": (dict(temperature="-10"), "Temperature: -10 °C", "-10"),
    "header_text": (dict(header_text="FOCTEMP<0"), "Header: FOCTEMP<0", "FOCTEMP<0"),
    "plate_solved": (dict(plate_solved=False), "Plate Solved: Unsolved", "Unsolved"),
    "project": (dict(project=NO_PROJECT), f"Project: {NO_PROJECT.name}", NO_PROJECT.name),
    "coordinates": (dict(coord_ra="00:42:44.3", coord_dec="+41:16:09", coord_radius=0.25, coord_query="M31"),
                    "Coordinates: M31, r=0.25°", "M31 ±0.25°"),
    "dates": (dict(local_start=START, local_end=END),
              "Date (local) 2024-01-01 22:00:00 - 2024-01-02 05:30:00",
              "(local) 2024-01-01 22:00:00 to 2024-01-02 05:30:00"),
    "night": (dict(session_night=date(2024, 1, 1)), "Night 2024-01-01", "night 2024-01-01"),
    "image_size": (dict(width_min=4000, height_max=3000), "Size: W ≥4000, H ≤3000", "Size: W ≥4000, H ≤3000"),
    "plate_scale": (dict(scale_min=0.5, scale_max=1.5), "Scale: 0.5–1.5 arcsec/px", "Scale: 0.5–1.5 arcsec/px"),
    "image_quality": (dict(fwhm_max=3.5), "Quality: FWHM", "Quality: FWHM"),
    "quality_diagnosis": (dict(diagnosis_hidden=list(DIAGNOSIS_BAD_VALUES)), "Diagnosis: hide bad", "Diagnosis: hide bad"),
}


def test_every_entry_has_a_case():
    assert set(EVERY_FILTER) == set(filter_catalogue.BY_KEY)


@pytest.mark.parametrize("key", list(EVERY_FILTER))
def test_label_and_summary(key):
    entry = filter_catalogue.BY_KEY[key]
    values, chip, fragment = EVERY_FILTER[key]
    criteria = SearchCriteria(**values)

    assert entry.is_active(criteria)
    assert entry.has_chip == (chip is not None)
    if chip is not None:
        assert entry.chip_label(criteria) == chip
    assert entry.summary(criteria) == fragment
    assert str(criteria) == fragment


@pytest.mark.parametrize("key", list(EVERY_FILTER))
def test_reset_leaves_the_default_criteria(key):
    entry = filter_catalogue.BY_KEY[key]
    criteria = SearchCriteria(**EVERY_FILTER[key][0])

    entry.reset(criteria)

    assert criteria == SearchCriteria()
    assert not entry.is_active(criteria)


@pytest.mark.parametrize("key", list(EVERY_FILTER))
def test_a_saved_filter_comes_back_with_the_same_label(key):
    entry = filter_catalogue.BY_KEY[key]
    criteria = SearchCriteria(**EVERY_FILTER[key][0])

    restored = SearchCriteria.from_json(criteria.to_json())

    assert restored == criteria
    assert entry.summary(restored) == entry.summary(criteria)
    if entry.has_chip:
        assert entry.chip_label(restored) == entry.chip_label(criteria)


def test_a_utc_range_shows_as_typed_and_says_so():
    criteria = SearchCriteria(start_datetime=START, end_datetime=END)

    assert filter_catalogue.DATES.chip_label(criteria) == "Date (UTC) 2024-01-01 22:00:00 - 2024-01-02 05:30:00"
    assert str(criteria) == "(UTC) 2024-01-01 22:00:00 to 2024-01-02 05:30:00"
    assert SearchCriteria.from_json(criteria.to_json()) == criteria


def test_filters_combine_in_catalogue_order():
    criteria = SearchCriteria(type="LIGHT", fwhm_max=3.5, telescope="RC8", plate_solved=True)

    assert str(criteria) == "LIGHT, RC8, Solved, Quality: FWHM"

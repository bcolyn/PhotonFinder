"""The filter catalogue: each filter's label, summary, reset and round trip, without Qt."""
import dataclasses

import pytest

from photonfinder import filter_catalogue
from photonfinder.filter_catalogue import IMAGE_QUALITY
from photonfinder.models import SearchCriteria


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

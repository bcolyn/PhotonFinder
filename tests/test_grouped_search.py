"""Grouped search (SearchCriteria.one_per_group): one row per group of frames, its earliest frame,
with how many it stands for; paging and counts by group; the drill-in criteria that give back
exactly a group's frames; and only the grid and MCP search group, the reports don't."""
from datetime import datetime, timedelta

import pytest

from photonfinder import mcp_server, reports
from photonfinder.library_query import (
    count_distinct_files, count_files, group_value, matching_files, search_files,
)
from photonfinder.models import File, Image, LibraryRoot, SearchCriteria, group_of

EVENING = datetime(2026, 3, 5, 21, 0)  # local time, night of 5 March


@pytest.fixture
def library(context):
    """Two nights of M31 in Ha (40 and 25 subs), one of M31 in OIII and two exposures of darks,
    a night of frames without OBJECT or FILTER, and a copy of the first night in another folder."""
    root = LibraryRoot.create(name="Main", path="/data/")
    number = iter(range(100000))

    def frames(count, start, path="lights/", image_type="LIGHT", object_name="M31", filter_="Ha", exposure=300.0):
        for i in range(count):
            local = start + timedelta(minutes=5 * i)
            file = File.create(root=root, path=path, name=f"f{next(number):05d}.fits", size=1, mtime_millis=1)
            Image.create(file=file, image_type=image_type, object_name=object_name, filter=filter_,
                         exposure=exposure + (0.0001 if i % 2 else 0),  # header float noise: one group
                         date_obs=local - timedelta(hours=1), local_time=local, local_time_source="loc")
    frames(40, EVENING)
    frames(25, EVENING + timedelta(days=1))
    frames(10, EVENING, filter_="OIII")
    frames(5, EVENING, path="darks/", image_type="DARK", object_name=None, filter_=None, exposure=300.0)
    frames(5, EVENING, path="darks/", image_type="DARK", object_name=None, filter_=None, exposure=60.0)
    frames(3, EVENING, path="misc/", object_name=None, filter_=None)
    frames(40, EVENING, path="copy/")
    return root


def _groups(criteria=None) -> list:
    criteria = criteria or SearchCriteria(one_per_group=True, sorting_field="frames", sorting_desc=True)
    return search_files(criteria, page_size=1000).rows


def test_one_row_per_group_with_its_frame_count(library):
    rows = _groups()

    assert sorted(group_value(r, "group_frames") for r in rows) == [3, 5, 5, 10, 25, 40, 40]
    assert sum(group_value(r, "group_integration") for r in rows) == pytest.approx(
        300 * (40 + 25 + 10 + 5 + 3 + 40) + 60 * 5, abs=0.1)


def test_the_representative_is_the_earliest_frame(library):
    for row in _groups():
        members = matching_files(SearchCriteria(groups=[group_of(row)]), require_image=True)
        assert row.image.rowid == min(members, key=lambda m: m.image.date_obs).image.rowid


def test_the_drill_in_gives_exactly_the_group(library):
    for row in _groups():
        members = matching_files(SearchCriteria(groups=[group_of(row)]), require_image=True)
        assert len(members) == group_value(row, "group_frames"), group_of(row)


def test_several_groups_expand_in_one_query(library):
    rows = _groups()[:3]

    members = matching_files(SearchCriteria(groups=[group_of(r) for r in rows]), require_image=True)

    assert len(members) == sum(group_value(r, "group_frames") for r in rows)


def test_counts_and_pages_are_by_group(library):
    criteria = SearchCriteria(one_per_group=True, sorting_field="date_obs", sorting_desc=False)

    first, second = search_files(criteria, 0, 4), search_files(criteria, 1, 4)

    assert count_files(criteria) == first.total == 7
    assert count_distinct_files(criteria) == 128  # the files the groups hold
    assert first.has_more and not second.has_more
    assert len(first.rows) + len(second.rows) == 7
    assert not {r.image.rowid for r in first.rows} & {r.image.rowid for r in second.rows}


def test_the_search_still_filters_the_frames_a_group_counts(library):
    criteria = SearchCriteria(one_per_group=True, local_start=EVENING + timedelta(hours=1))

    counts = {(r.image.filter, r.path, group_of(r)["session_night"]): group_value(r, "group_frames")
              for r in _groups(criteria)}

    assert counts[("Ha", "lights/", "2026-03-05")] == 40 - 12  # from 22:00 on
    assert counts[("Ha", "lights/", "2026-03-06")] == 25  # all after it


def test_the_reports_ignore_grouping(library):
    grouped = reports.target_report(SearchCriteria(one_per_group=True))
    plain = reports.target_report(SearchCriteria())

    assert grouped == plain
    assert sum(r.file_count for r in grouped) == 40 + 25 + 10 + 40


def test_mcp_returns_frames_and_integration(context, library):
    result = mcp_server.query_search(context, {"one_per_group": True, "sorting_field": "frames"})

    assert result["total"] == 7
    assert result["results"][0]["frames"] == 40
    assert result["results"][0]["integration_seconds"] == pytest.approx(40 * 300, abs=0.1)
    plain = mcp_server.query_search(context, {})
    assert "frames" not in plain["results"][0]

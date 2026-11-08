"""The stored local time and the session night in search: the grid's columns, the Date filter in
local time or UTC, the Night filter, the target report's night count and the MCP fields."""
from datetime import date, datetime

import pytest

from photonfinder import filter_catalogue, mcp_server, reports
from photonfinder.library_query import COLUMN_INDEX, RESULT_COLUMNS, cell, search_files
from photonfinder.models import File, Image, LibraryRoot, SearchCriteria

DATE_COLUMN = RESULT_COLUMNS[COLUMN_INDEX["date_obs"]]
NIGHT_COLUMN = RESULT_COLUMNS[COLUMN_INDEX["session_night"]]


@pytest.fixture
def frames(context):
    """Sydney (UTC+11): an evening and an after-midnight frame of the night of 5 March, the next
    evening, and a frame not yet given a local time."""
    root = LibraryRoot.create(name="Main", path="/data/")

    def frame(name, date_obs, local_time):
        file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
        Image.create(file=file, image_type="LIGHT", object_name="M42", filter="Ha", exposure=60.0,
                     date_obs=date_obs, local_time=local_time, local_time_source="site" if local_time else None)
        return file
    return {
        "evening": frame("evening", datetime(2026, 3, 5, 10, 0), datetime(2026, 3, 5, 21, 0)),
        "after_midnight": frame("after_midnight", datetime(2026, 3, 5, 15, 30), datetime(2026, 3, 6, 2, 30)),
        "next_evening": frame("next_evening", datetime(2026, 3, 6, 10, 0), datetime(2026, 3, 6, 21, 0)),
        "pending": frame("pending", datetime(2026, 3, 5, 11, 0), None),
    }


def _names(criteria: SearchCriteria) -> list[str]:
    return sorted(row.name for row in search_files(criteria).rows)


class TestFilters:

    def test_a_night_takes_both_sides_of_midnight(self, frames):
        assert _names(SearchCriteria(session_night=date(2026, 3, 5))) == ["after_midnight", "evening"]

    def test_a_local_range_compares_local_times(self, frames):
        criteria = SearchCriteria(local_start=datetime(2026, 3, 5, 20, 0), local_end=datetime(2026, 3, 6, 3, 0))

        assert _names(criteria) == ["after_midnight", "evening"]

    def test_a_utc_range_compares_date_obs(self, frames):
        criteria = SearchCriteria(start_datetime=datetime(2026, 3, 5, 9, 0), end_datetime=datetime(2026, 3, 5, 12, 0))

        assert _names(criteria) == ["evening", "pending"]

    def test_night_and_dates_combine(self, frames):
        criteria = SearchCriteria(session_night=date(2026, 3, 5), local_start=datetime(2026, 3, 6, 0, 0))

        assert _names(criteria) == ["after_midnight"]

    def test_removing_the_date_chip_clears_both_bases(self):
        criteria = SearchCriteria(start_datetime=datetime(2026, 1, 1), local_end=datetime(2026, 1, 2))

        filter_catalogue.DATES.reset(criteria)

        assert criteria == SearchCriteria()


class TestGrid:

    def _row(self, name):
        return next(row for row in search_files(SearchCriteria()).rows if row.name == name)

    def test_the_date_column_shows_local_time_with_utc_in_the_tooltip(self, frames):
        row = self._row("after_midnight")

        assert cell(DATE_COLUMN, DATE_COLUMN.value(row))[0] == "2026-03-06 02:30:00"
        assert DATE_COLUMN.tooltip(row) == "UTC: 2026-03-05 15:30:00"
        assert cell(NIGHT_COLUMN, NIGHT_COLUMN.value(row))[0] == "2026-03-05"

    def test_without_a_local_time_yet_the_date_shows_utc_and_says_so(self, frames):
        row = self._row("pending")

        assert cell(DATE_COLUMN, DATE_COLUMN.value(row))[0] == "2026-03-05 11:00:00 UTC"
        assert cell(NIGHT_COLUMN, NIGHT_COLUMN.value(row)) == ("", None)

    def test_sorting_by_night_and_by_local_time(self, frames):
        by_night = [r.name for r in search_files(SearchCriteria(sorting_field="session_night", sorting_desc=False)).rows]
        by_time = [r.name for r in search_files(SearchCriteria(sorting_field="date_obs", sorting_desc=False)).rows]

        assert by_night[1:] == ["evening", "after_midnight", "next_evening"] or \
            by_night[1:] == ["after_midnight", "evening", "next_evening"]  # one night: either order
        assert by_night[0] == "pending"  # NULL first, as SQLite sorts it
        assert by_time == ["pending", "evening", "after_midnight", "next_evening"]


def test_the_target_report_counts_nights(frames):
    [row] = reports.target_report(SearchCriteria())

    assert (row.object_name, row.file_count, row.nights) == ("M42", 4, 2)  # "pending" has no night yet


class TestMcp:

    def test_results_carry_the_local_time_and_the_night(self, context, frames):
        result = mcp_server.query_search(context, {"session_night": "2026-03-05", "sorting_field": "date_obs",
                                                   "sorting_desc": False})

        assert [(r["name"], r["image"]["local_time"], r["image"]["session_night"]) for r in result["results"]] == [
            ("evening", "2026-03-05T21:00:00", "2026-03-05"),
            ("after_midnight", "2026-03-06T02:30:00", "2026-03-05")]

    def test_a_local_bound_with_an_offset_keeps_its_wall_clock_time(self, context, frames):
        result = mcp_server.query_search(context, {"local_start": "2026-03-06T00:00:00+11:00",
                                                   "local_end": "2026-03-06T03:00:00+11:00"})

        assert [r["name"] for r in result["results"]] == ["after_midnight"]

    def test_the_target_report_has_nights(self, context, frames):
        result = mcp_server.query_target_report(context, {})

        assert result["results"][0]["nights"] == 2

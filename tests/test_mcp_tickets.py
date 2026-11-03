"""MCP: timezone-aware dates (10), invalid filters reported (11), lifecycle and data shaping (22)."""
import asyncio
import json
from datetime import datetime

import pytest

from photonfinder import mcp_server, reports
from photonfinder.mcp_server import McpServerController
from photonfinder.models import File, Image, InvalidCriteria, Project, ProjectFile, SearchCriteria, search_files


def _names(result) -> list[str]:
    return sorted(r["name"] for r in result["results"])


# --- 10: dates with a timezone offset -------------------------------------------------
# The light was observed at 2024-01-01 22:00:00 UTC.

@pytest.mark.parametrize("start, expected", [
    ("2024-01-01T23:30:00+02:00", ["m31.fits"]),   # 21:30 UTC: before the light
    ("2024-01-02T00:30:00+02:00", []),             # 22:30 UTC: after it
    ("2024-01-01T17:30:00-05:00", []),             # 22:30 UTC
    ("2024-01-01T21:30:00", ["m31.fits"]),         # no offset: taken as UTC
    ("2024-01-01T22:30:00", []),
])
def test_a_start_time_with_an_offset_is_compared_in_utc(sample, start, expected):
    ctx, _ = sample
    result = mcp_server.query_search(ctx, {"type": "LIGHT", "start_datetime": start})
    assert _names(result) == expected


def test_an_end_time_with_an_offset_is_compared_in_utc(sample):
    ctx, _ = sample
    criteria = {"type": "LIGHT", "end_datetime": "2024-01-01T23:00:00+02:00"}  # 21:00 UTC
    assert _names(mcp_server.query_search(ctx, criteria)) == []
    criteria["end_datetime"] = "2024-01-02T01:00:00+02:00"  # 23:00 UTC
    assert _names(mcp_server.query_search(ctx, criteria)) == ["m31.fits"]


def test_the_date_descriptions_state_the_utc_convention():
    for name in ("start_datetime", "end_datetime"):
        description = mcp_server.SearchCriteriaInput.model_fields[name].description
        assert "UTC" in description and "offset" in description


# --- 11: invalid filter values -------------------------------------------------------

@pytest.mark.parametrize("criteria, field", [
    ({"exposure": "abc"}, "exposure"),
    ({"binning": "2x2"}, "binning"),
    ({"gain": "high"}, "gain"),
    ({"temperature": "cold"}, "temperature"),
    ({"coord_ra": "not an angle", "coord_dec": "41"}, "coord_ra/coord_dec"),
    ({"coord_ra": "00 42 44"}, "coord_dec"),
    ({"coord_dec": "+41 16 09"}, "coord_ra"),
    ({"coord_ra": "00 42 44", "coord_dec": "41", "coord_radius": 0}, "coord_radius"),
    ({"project": 999}, "project"),
])
def test_an_unusable_filter_value_is_an_error_naming_the_field(sample, criteria, field):
    ctx, _ = sample
    for query in (lambda: mcp_server.query_search(ctx, criteria),
                  lambda: mcp_server.query_target_report(ctx, criteria),
                  lambda: mcp_server.query_header_values(ctx, ["GAIN"], criteria)):
        with pytest.raises(InvalidCriteria) as raised:
            query()
        assert f"`{field}`" in str(raised.value)


@pytest.mark.parametrize("criteria", [
    {"exposure": "300"}, {"exposure": "300.0"}, {"binning": "1"}, {"gain": "100"},
    {"temperature": "-10"}, {"coord_ra": "00 42 44", "coord_dec": "+41 16 09"},
    {"coord_ra": "0.712", "coord_dec": "41.27", "coord_radius": 1.0},
])
def test_usable_values_still_filter(sample, criteria):
    ctx, _ = sample
    assert "results" in mcp_server.query_search(ctx, criteria)


def test_the_tool_answers_with_the_error_instead_of_a_whole_library(sample):
    ctx, _ = sample
    for tool, args in (("search_files", {"criteria": {"exposure": "abc"}}),
                       ("report_targets", {"criteria": {"gain": "high"}}),
                       ("report_catalog_coverage", {"catalog": "Messier", "criteria": {"binning": "x"}})):
        [block] = asyncio.run(mcp_server.build_mcp(ctx).call_tool(tool, args))
        answer = json.loads(block.text)
        assert set(answer) == {"error"}, (tool, answer)
        assert "Invalid value" in answer["error"]


def test_the_gui_search_still_ignores_an_unusable_value(sample):
    _, d = sample
    rows, total, _ = search_files(SearchCriteria(exposure="abc", coord_ra="00 42 44"), 0, 100)
    assert total == 2


# --- 22: port file, startup, data shaping --------------------------------------------

@pytest.fixture
def port_file(tmp_path, monkeypatch):
    path = tmp_path / "mcp_port.txt"
    monkeypatch.setattr("photonfinder.mcp_server.mcp_port_file", lambda: path)
    return path


def test_stopping_leaves_the_port_file_another_instance_published(context, port_file):
    first = McpServerController(context)
    first.start()
    try:
        port_file.write_text("54321", encoding="utf-8")  # a second instance started since
    finally:
        first.stop()
    assert port_file.read_text(encoding="utf-8") == "54321"


def test_stopping_removes_the_port_file_it_published(context, port_file):
    controller = McpServerController(context)
    controller.start()
    controller.stop()
    assert not port_file.exists()


def test_a_start_that_times_out_leaves_a_stopped_server_that_can_start_again(context, port_file, monkeypatch):
    controller = McpServerController(context)
    monkeypatch.setattr(McpServerController, "STARTUP_TIMEOUT", 0)
    controller.start()
    assert not controller.running
    assert controller.port is None
    assert not port_file.exists()

    monkeypatch.setattr(McpServerController, "STARTUP_TIMEOUT", 15)
    controller.start()
    try:
        assert controller.running
        assert port_file.read_text(encoding="utf-8") == str(controller.port)
    finally:
        controller.stop()


def test_a_project_name_with_a_comma_survives(sample):
    ctx, d = sample
    merged = Project.create(name="M31,M32")
    ProjectFile.create(project=merged, file=d["light"])

    [light] = [r for r in mcp_server.query_search(ctx, {})["results"] if r["name"] == "m31.fits"]

    assert sorted(light["projects"]) == ["Andromeda", "M31,M32"]


def test_file_details_with_a_date_stored_as_text(sample):
    ctx, d = sample
    ctx.database.execute_sql("UPDATE image SET date_obs = 'not a date' WHERE file_id = ?",
                             [d["light"].rowid])

    details = mcp_server.query_file_details(ctx, d["light"].rowid)

    assert details["image"]["date_obs"] == "not a date"


def test_a_list_longer_than_the_sql_variable_limit(sample):
    ids = list(range(1, 40_001))  # SQLite allows 32766 bound variables
    assert File.select().where(reports._in_json(File.rowid, ids)).count() == 2


def test_catalog_entries_by_a_list_longer_than_the_sql_variable_limit(sample):
    from photonfinder.models import CATALOG_MODELS, CatalogEntry
    ctx, _ = sample
    with ctx.database.bind_ctx(CATALOG_MODELS):
        wanted = [r.rowid for r in CatalogEntry.select(CatalogEntry.rowid)
                  .where(CatalogEntry.catalog == "Messier").limit(3)]
        assert len(wanted) == 3
        padding = list(range(10_000_000, 10_040_000))  # rowids no entry has

        entries = reports._catalog_entries("Messier", None, 0, rowids=wanted + padding)

    assert sorted(e.rowid for e in entries) == sorted(wanted)

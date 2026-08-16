"""Tests for the read-only MCP server query layer (photonfinder.mcp_server).

These exercise the synchronous query helpers directly against an in-memory database,
which validates criteria handling, result serialization, pagination and file details
without needing the stdio transport or a running event loop.
"""
from datetime import datetime

import pytest
from astropy.io.fits import Header

from photonfinder.models import File, Image, FileWCS
from photonfinder import mcp_server
from photonfinder.platesolver import SolverBase, SolverType
from tests.conftest import fixed_solution_header as _fixed_solution_header


class _FakeSolver(SolverBase):
    """Stand-in solver that returns a fixed WCS solution without running any executable."""

    def __init__(self, header: Header = None, error: Exception = None):
        super().__init__()
        self._header = header
        self._error = error

    @property
    def solver_type(self):
        return SolverType.ASTAP

    def solve(self, image_path, image=None, hint=None, output_callback=None, file_wcs=None):
        if self._error:
            raise self._error
        return self._header.copy()




def test_search_returns_all_files(sample):
    ctx, _ = sample
    result = mcp_server.query_search(ctx, {})
    assert result["total"] == 2
    assert result["has_more"] is False
    assert {r["name"] for r in result["results"]} == {"m31.fits", "dark.fits"}


def test_search_filter_by_type(sample):
    ctx, data = sample
    result = mcp_server.query_search(ctx, {"type": "LIGHT"})
    assert result["total"] == 1
    row = result["results"][0]
    assert row["name"] == "m31.fits"
    assert row["rowid"] == data["light"].rowid
    assert row["image"]["object_name"] == "M31"
    assert row["image"]["date_obs"] == "2024-01-01T22:00:00"
    assert row["has_wcs"] is True
    assert row["projects"] == ["Andromeda"]


def test_search_filter_by_paths(sample):
    ctx, data = sample
    root = data["root"]
    result = mcp_server.query_search(
        ctx, {"paths": [{"root_id": root.rowid, "root_label": root.name, "path": "darks/"}]})
    assert result["total"] == 1
    assert result["results"][0]["name"] == "dark.fits"


def test_search_filter_by_paths_without_root_label(sample):
    ctx, data = sample
    root = data["root"]
    result = mcp_server.query_search(ctx, {"paths": [{"root_id": root.rowid, "path": "darks/"}]})
    assert result["total"] == 1
    assert result["results"][0]["name"] == "dark.fits"


def test_search_filter_by_project_rowid(sample):
    ctx, data = sample
    result = mcp_server.query_search(ctx, {"project": data["project"].rowid})
    assert result["total"] == 1
    assert result["results"][0]["name"] == "m31.fits"


def test_search_pagination(sample):
    ctx, _ = sample
    page0 = mcp_server.query_search(ctx, {}, page=0, page_size=1)
    assert page0["total"] == 2
    assert page0["has_more"] is True
    assert len(page0["results"]) == 1

    page1 = mcp_server.query_search(ctx, {}, page=1, page_size=1)
    assert page1["has_more"] is False
    assert len(page1["results"]) == 1
    assert page0["results"][0]["rowid"] != page1["results"][0]["rowid"]


def test_search_ignores_unknown_criteria_keys(sample):
    ctx, _ = sample
    # Should not raise despite the bogus key.
    result = mcp_server.query_search(ctx, {"not_a_real_field": "x", "type": "DARK"})
    assert result["total"] == 1
    assert result["results"][0]["name"] == "dark.fits"


def test_search_sorting_field_ascending(sample):
    ctx, _ = sample
    result = mcp_server.query_search(ctx, {"sorting_field": "size", "sorting_desc": False})
    assert [r["name"] for r in result["results"]] == ["m31.fits", "dark.fits"]


def test_search_sorting_field_descending(sample):
    ctx, _ = sample
    result = mcp_server.query_search(ctx, {"sorting_field": "size", "sorting_desc": True})
    assert [r["name"] for r in result["results"]] == ["dark.fits", "m31.fits"]


def test_search_sorting_field_unknown_raises(sample):
    ctx, _ = sample
    with pytest.raises(ValueError):
        mcp_server.query_search(ctx, {"sorting_field": "not_a_real_field"})


def test_search_page_size_is_clamped(sample):
    ctx, _ = sample
    result = mcp_server.query_search(ctx, {}, page_size=10_000)
    assert result["page_size"] == mcp_server.MAX_PAGE_SIZE


# --- Report tools -------------------------------------------------------------------

def _extra_light(root, path, name):
    file = File.create(root=root, path=path, name=name, size=1000, mtime_millis=111)
    Image.create(file=file, image_type="LIGHT", filter="Ha", camera="ASI2600",
                 telescope="RC8", object_name="M31", exposure=300.0, gain=100, binning=1,
                 date_obs=datetime(2024, 1, 1, 22, 0, 0))
    return file


def test_target_report_shape(sample):
    ctx, _ = sample
    result = mcp_server.query_target_report(ctx, {})
    assert result["total"] == 1
    assert result["has_more"] is False
    assert result["total_exposure_seconds_all"] == 300.0

    row = result["results"][0]
    assert row["object_name"] == "M31"
    assert row["total_exposure_seconds"] == 300.0
    assert row["file_count"] == 1
    assert row["paths"] == ["lights/"]
    assert row["paths_truncated"] is False
    # Serialized as ISO-8601 even though the aggregate comes back as raw SQLite text.
    assert datetime.fromisoformat(row["last_date_obs"]) == datetime(2024, 1, 1, 22, 0, 0)


def test_target_report_page_size_is_clamped(sample):
    ctx, _ = sample
    result = mcp_server.query_target_report(ctx, {}, page_size=10_000)
    assert result["page_size"] == mcp_server.MAX_PAGE_SIZE


def test_target_report_total_exposure_covers_every_page(sample):
    ctx, data = sample
    _extra_light(data["root"], "lights/b/", "b.fits")
    Image.update(filter="OIII").where(Image.file == data["light"].rowid).execute()

    result = mcp_server.query_target_report(ctx, {}, page_size=1)
    assert result["total"] == 2
    assert len(result["results"]) == 1
    assert result["has_more"] is True
    # Both groups counted, even though only one is on this page.
    assert result["total_exposure_seconds_all"] == 600.0


def test_target_report_paths_are_capped(sample):
    ctx, data = sample
    for i in range(5):
        _extra_light(data["root"], f"lights/{i}/", f"m31_{i}.fits")

    result = mcp_server.query_target_report(ctx, {}, max_paths=2)
    row = result["results"][0]
    assert len(row["paths"]) == 2
    assert row["paths_truncated"] is True


def test_target_report_include_paths_false_omits_them(sample):
    ctx, _ = sample
    row = mcp_server.query_target_report(ctx, {}, include_paths=False)["results"][0]
    assert "paths" not in row
    assert "paths_truncated" not in row


def test_target_report_ignores_unknown_criteria_keys(sample):
    ctx, _ = sample
    result = mcp_server.query_target_report(ctx, {"type": "LIGHT", "bogus": "x"})
    assert result["total"] == 1


def test_catalog_report_finds_coverage(solved_sample):
    ctx, _ = solved_sample
    result = mcp_server.query_catalog_report(ctx, "NGC")
    assert result["catalog"] == "NGC"
    assert result["images_considered"] == 1
    assert result["objects_matched"] >= 1

    row = next(r for r in result["results"] if r["catalog_id"] == "224")
    assert row["file_count"] == 1
    assert row["files"][0]["rowid"] is not None
    assert row["files"][0]["full_path"].endswith("m31.fits")
    assert row["files_truncated"] is False


def test_catalog_report_defaults_to_only_matching(solved_sample):
    ctx, _ = solved_sample
    # Pinned deliberately: the GUI defaults this off, but a full NGC scan is ~785k rows
    # of which an agent wants the handful it has actually imaged.
    assert mcp_server.query_catalog_report(ctx, "NGC")["only_matching"] is True


def test_catalog_report_include_files_false_omits_them(solved_sample):
    ctx, _ = solved_sample
    row = mcp_server.query_catalog_report(ctx, "NGC", include_files=False)["results"][0]
    assert "files" not in row
    assert row["file_count"] == 1


def test_catalog_report_files_are_capped(solved_sample):
    ctx, data = solved_sample
    result = mcp_server.query_catalog_report(ctx, "NGC", max_files=0)
    row = next(r for r in result["results"] if r["catalog_id"] == "224")
    assert row["files"] == []
    assert row["files_truncated"] is True


def test_catalog_report_unknown_catalog_returns_error(sample):
    ctx, _ = sample
    result = mcp_server.query_catalog_report(ctx, "NOT_A_CATALOG")
    assert "error" in result
    assert "NOT_A_CATALOG" in result["error"]


def test_header_values_returns_requested_fields(solved_sample):
    ctx, data = solved_sample
    result = mcp_server.query_header_values(
        ctx, ["GAIN", "OBJECT", "Image.exposure", "WCS:CRVAL1"], {"type": "LIGHT"})

    assert result["total"] == 1
    entry = result["results"][0]
    assert entry["rowid"] == data["light"].rowid
    assert entry["values"] == {
        "GAIN": 100, "OBJECT": "M31", "Image.exposure": 300.0, "WCS:CRVAL1": 10.68,
    }


def test_header_values_missing_field_is_null(sample):
    ctx, _ = sample
    result = mcp_server.query_header_values(ctx, ["NOSUCHKW"], {"type": "LIGHT"})
    assert result["results"][0]["values"] == {"NOSUCHKW": None}


def test_header_values_keeps_files_without_a_header(sample):
    ctx, _ = sample
    result = mcp_server.query_header_values(ctx, ["GAIN"], {"type": "DARK"})
    assert result["total"] == 1
    assert result["results"][0]["values"] == {"GAIN": None}


def test_header_values_rejects_empty_field_list(sample):
    ctx, _ = sample
    assert "error" in mcp_server.query_header_values(ctx, [])
    assert "error" in mcp_server.query_header_values(ctx, None)


def test_header_values_rejects_too_many_fields(sample):
    ctx, _ = sample
    fields = [f"KW{i}" for i in range(mcp_server.MAX_HEADER_KEYWORDS + 1)]
    result = mcp_server.query_header_values(ctx, fields)
    assert "error" in result


def test_header_values_paginates(sample):
    ctx, data = sample
    _extra_light(data["root"], "lights/b/", "b.fits")
    result = mcp_server.query_header_values(ctx, ["GAIN"], {"type": "LIGHT"}, page_size=1)
    assert result["total"] == 2
    assert result["has_more"] is True
    assert len(result["results"]) == 1


def test_list_library_roots(sample):
    ctx, data = sample
    roots = mcp_server.query_library_roots(ctx)
    assert roots == [{"rowid": data["root"].rowid, "name": "Main", "path": "/data/",
                      "description": "Main imaging archive"}]


def test_list_projects(sample):
    ctx, data = sample
    projects = mcp_server.query_projects(ctx)
    assert len(projects) == 1
    project = projects[0]
    assert project["rowid"] == data["project"].rowid
    assert project["name"] == "Andromeda"
    assert project["file_count"] == 1
    assert project["last_date_obs"] == "2024-01-01T22:00:00"
    assert project["coord_ra"] == pytest.approx(10.68)
    assert project["coord_dec"] == pytest.approx(41.27)


def test_get_project_details(sample):
    ctx, data = sample
    details = mcp_server.query_project_details(ctx, data["project"].rowid)
    assert details["name"] == "Andromeda"
    assert details["file_count"] == 1


def test_get_project_details_missing(sample):
    ctx, _ = sample
    details = mcp_server.query_project_details(ctx, 999999)
    assert "error" in details


def test_search_files_without_project(sample):
    ctx, data = sample
    result = mcp_server.query_search(ctx, {"project": -1})
    assert result["total"] == 1
    assert result["results"][0]["name"] == "dark.fits"


def test_list_distinct_values(sample):
    ctx, _ = sample
    assert mcp_server.query_distinct_values(ctx, "type") == ["DARK", "LIGHT"]
    assert mcp_server.query_distinct_values(ctx, "filter") == ["Ha"]


def test_list_distinct_values_rejects_unknown_field(sample):
    ctx, _ = sample
    with pytest.raises(ValueError):
        mcp_server.query_distinct_values(ctx, "bogus")


def test_get_file_details_with_header(sample):
    ctx, data = sample
    details = mcp_server.query_file_details(ctx, data["light"].rowid)
    assert details["name"] == "m31.fits"
    assert details["has_wcs"] is True
    assert details["projects"] == ["Andromeda"]
    assert details["image"]["filter"] == "Ha"
    assert details["header"]["GAIN"] == 100
    assert details["header"]["OBJECT"] == "M31"
    assert details["root"]["description"] == "Main imaging archive"


def test_get_file_details_missing(sample):
    ctx, _ = sample
    details = mcp_server.query_file_details(ctx, 999999)
    assert "error" in details


def test_list_catalogs(sample):
    # Uses PhotonFinder's shipped local catalog database (data/catalog.db).
    ctx, _ = sample
    catalogs = mcp_server.query_list_catalogs(ctx)
    assert "NGC" in catalogs
    assert "Messier" in catalogs


def test_lookup_object_by_catalog_id(sample):
    ctx, _ = sample
    result = mcp_server.query_lookup_object(ctx, "NGC", "224")
    assert result["ra"] == pytest.approx(10.68, abs=0.01)
    assert result["dec"] == pytest.approx(41.27, abs=0.01)


def test_lookup_object_by_canonical_id(sample):
    ctx, _ = sample
    result = mcp_server.query_lookup_object(ctx, "Messier", "Melotte_22")
    assert result["catalog_id"] == "45"


def test_lookup_object_not_found(sample):
    ctx, _ = sample
    result = mcp_server.query_lookup_object(ctx, "NGC", "no-such-id")
    assert "error" in result


def test_build_mcp_registers_expected_tools():
    import asyncio
    mcp = mcp_server.build_mcp(None)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {
        "search_files", "list_library_roots", "list_projects",
        "list_distinct_values", "get_file_details", "list_catalogs", "lookup_object",
        "get_project_details", "plate_solve_files",
        "report_targets", "report_catalog_coverage", "get_header_values",
    }


def test_tool_set_does_not_depend_on_settings(sample):
    """The advertised tools must be identical for every run, so they can be generated.

    `plate_solve_files` used to appear and disappear with its setting; it is now always
    registered and refuses when disabled, which is what lets the stub serve the tool list
    without the application running.
    """
    import asyncio
    ctx, _ = sample

    ctx.settings._store["mcp_allow_plate_solve"] = False
    disabled = {t.name for t in asyncio.run(mcp_server.build_mcp(ctx).list_tools())}
    ctx.settings._store["mcp_allow_plate_solve"] = True
    enabled = {t.name for t in asyncio.run(mcp_server.build_mcp(ctx).list_tools())}

    assert disabled == enabled
    assert "plate_solve_files" in disabled


def test_plate_solve_refuses_when_the_setting_is_off(sample, monkeypatch):
    """Advertising the tool must not weaken the gate: no solver runs without the setting."""
    ctx, data = sample
    ctx.settings._store["mcp_allow_plate_solve"] = False

    def fail(*args, **kwargs):
        raise AssertionError("no solver may be built while the setting is off")

    monkeypatch.setattr(mcp_server, "_build_solver", fail)

    result = mcp_server.query_plate_solve(ctx, [data["dark"].rowid])

    assert "error" in result
    assert "Settings" in result["error"]
    assert not FileWCS.select().where(FileWCS.file == data["dark"]).exists()


def test_plate_solve_rejects_empty_batch(sample):
    ctx, _ = sample
    result = mcp_server.query_plate_solve(ctx, [])
    assert "error" in result


def test_plate_solve_rejects_oversized_batch(sample):
    ctx, _ = sample
    result = mcp_server.query_plate_solve(ctx, list(range(mcp_server.MAX_SOLVE_BATCH + 1)))
    assert "error" in result


def test_plate_solve_success_persists_wcs(sample, monkeypatch):
    ctx, data = sample
    fake = _FakeSolver(header=_fixed_solution_header())
    monkeypatch.setattr(mcp_server, "_build_solver", lambda context, index: fake)

    dark = data["dark"]
    result = mcp_server.query_plate_solve(ctx, [dark.rowid])

    assert result["solved"] == 1
    assert result["failed"] == 0
    row = result["results"][0]
    assert row["success"] is True
    assert row["ra"] == pytest.approx(10.68, abs=0.01)
    assert row["dec"] == pytest.approx(41.27, abs=0.01)

    assert FileWCS.select().where(FileWCS.file == dark).exists()
    updated = Image.get(Image.file == dark)
    assert updated.coord_ra == pytest.approx(10.68, abs=0.01)
    assert updated.coord_dec == pytest.approx(41.27, abs=0.01)


def test_plate_solve_unknown_rowid_does_not_abort_batch(sample, monkeypatch):
    ctx, data = sample
    fake = _FakeSolver(header=_fixed_solution_header())
    monkeypatch.setattr(mcp_server, "_build_solver", lambda context, index: fake)

    dark = data["dark"]
    result = mcp_server.query_plate_solve(ctx, [999999, dark.rowid])

    assert result["solved"] == 1
    assert result["failed"] == 1
    by_rowid = {r["rowid"]: r for r in result["results"]}
    assert by_rowid[999999]["success"] is False
    assert "error" in by_rowid[999999]
    assert by_rowid[dark.rowid]["success"] is True


def test_plate_solve_reports_solver_failure_per_file(sample, monkeypatch):
    from photonfinder.platesolver import SolverError
    ctx, data = sample
    fake = _FakeSolver(error=SolverError("no stars detected"))
    monkeypatch.setattr(mcp_server, "_build_solver", lambda context, index: fake)

    dark = data["dark"]
    result = mcp_server.query_plate_solve(ctx, [dark.rowid])

    assert result["solved"] == 0
    assert result["failed"] == 1
    assert "no stars detected" in result["results"][0]["error"]
    assert not FileWCS.select().where(FileWCS.file == dark).exists()


def test_plate_solve_rejects_unknown_solver_name(sample):
    ctx, _ = sample
    result = mcp_server.query_plate_solve(ctx, [1], solver="not-a-real-solver")
    assert "error" in result


def test_plate_solve_lock_contention(sample, monkeypatch):
    ctx, data = sample
    fake = _FakeSolver(header=_fixed_solution_header())
    monkeypatch.setattr(mcp_server, "_build_solver", lambda context, index: fake)

    dark = data["dark"]
    assert ctx.solve_lock.acquire(blocking=False)
    try:
        result = mcp_server.query_plate_solve(ctx, [dark.rowid])
    finally:
        ctx.solve_lock.release()

    assert "error" in result
    assert not FileWCS.select().where(FileWCS.file == dark).exists()

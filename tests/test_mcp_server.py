"""Tests for the read-only MCP server query layer (photonfinder.mcp_server).

These exercise the synchronous query helpers directly against an in-memory database,
which validates criteria handling, result serialization, pagination and file details
without needing the HTTP transport or a running event loop.
"""
from datetime import datetime

import pytest
from astropy.io.fits import Header

from photonfinder.core import ApplicationContext, StatusReporter, compress
from photonfinder.models import (
    LibraryRoot, File, Image, FitsHeader, FileWCS, Project, ProjectFile,
)
from photonfinder import mcp_server


class _Settings:
    """Minimal Settings stand-in for ApplicationContext."""
    def __init__(self):
        self._store = {}

    def get_last_database_path(self):
        return self._store.get("last_database_path", "")

    def set_last_database_path(self, value):
        self._store["last_database_path"] = value

    def get_known_fits_keywords(self):
        return []

    def sync(self):
        pass


@pytest.fixture
def context():
    ctx = ApplicationContext(":memory:", _Settings())
    ctx.set_status_reporter(StatusReporter())
    with ctx:
        yield ctx


@pytest.fixture
def sample(context):
    """Populate the database with two files (a LIGHT and a DARK) and a project."""
    root = LibraryRoot.create(name="Main", path="/data/")

    light = File.create(root=root, path="lights/", name="m31.fits", size=1000, mtime_millis=111)
    Image.create(file=light, image_type="LIGHT", filter="Ha", camera="ASI2600",
                 telescope="RC8", object_name="M31", exposure=300.0, gain=100, offset=10,
                 binning=1, set_temp=-10.0, date_obs=datetime(2024, 1, 1, 22, 0, 0),
                 coord_ra=10.68, coord_dec=41.27, coord_radius=0.5, width=6248, height=4176)
    hdr = Header()
    hdr["GAIN"] = 100
    hdr["OBJECT"] = "M31"
    FitsHeader.create(file=light, header=compress(hdr.tostring().encode("ascii")))
    FileWCS.create(file=light, wcs=compress(b"dummy-wcs"))

    dark = File.create(root=root, path="darks/", name="dark.fits", size=2000, mtime_millis=222)
    Image.create(file=dark, image_type="DARK", camera="ASI2600", exposure=300.0, gain=100,
                 binning=1, set_temp=-10.0, date_obs=datetime(2024, 1, 2, 3, 0, 0))

    project = Project.create(name="Andromeda")
    ProjectFile.create(project=project, file=light)

    return context, {"root": root, "light": light, "dark": dark, "project": project}


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


def test_search_page_size_is_clamped(sample):
    ctx, _ = sample
    result = mcp_server.query_search(ctx, {}, page_size=10_000)
    assert result["page_size"] == mcp_server.MAX_PAGE_SIZE


def test_list_library_roots(sample):
    ctx, data = sample
    roots = mcp_server.query_library_roots(ctx)
    assert roots == [{"rowid": data["root"].rowid, "name": "Main", "path": "/data/"}]


def test_list_projects(sample):
    ctx, _ = sample
    projects = mcp_server.query_projects(ctx)
    assert projects == [{"rowid": projects[0]["rowid"], "name": "Andromeda"}]


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


def test_get_file_details_missing(sample):
    ctx, _ = sample
    details = mcp_server.query_file_details(ctx, 999999)
    assert "error" in details


def test_build_mcp_registers_expected_tools():
    import asyncio
    mcp = mcp_server.build_mcp(None)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {
        "search_files", "list_library_roots", "list_projects",
        "list_distinct_values", "get_file_details",
    }

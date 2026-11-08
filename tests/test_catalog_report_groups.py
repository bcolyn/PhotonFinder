"""The catalog report counts a group's unsolved frames through its earliest solved frame
(models.group_key_sql), marks them, and leaves out groups with nothing solved and unsolved
frames without an object name. The fixture solution is centred on M31 (NGC 224)."""
from datetime import datetime, timedelta

import pytest

from photonfinder import mcp_server, reports
from photonfinder.core import compress
from photonfinder.models import File, FileWCS, Image, LibraryRoot, SearchCriteria
from tests.conftest import fixed_solution_header

NIGHT = datetime(2024, 1, 1, 22, 0)  # local time
SOLUTION = compress(fixed_solution_header().tostring().encode("ascii"))


@pytest.fixture
def library(context):
    root = LibraryRoot.create(name="Main", path="/data/")

    def frame(name, local, object_name="M31", filter_="Ha", solved=False, path="lights/"):
        file = File.create(root=root, path=path, name=name, size=1, mtime_millis=1)
        Image.create(file=file, image_type="LIGHT", object_name=object_name, filter=filter_, exposure=300.0,
                     date_obs=local - timedelta(hours=1), local_time=local, local_time_source="loc",
                     coord_ra=10.68 if solved else None, coord_dec=41.27 if solved else None,
                     coord_radius=0.14 if solved else None, width=200, height=200)
        if solved:
            FileWCS.create(file=file, wcs=SOLUTION)
        return file
    frames = {
        # one night of Ha: the third sub solved, the others not
        "ha_1": frame("ha_1.fits", NIGHT),
        "ha_2": frame("ha_2.fits", NIGHT + timedelta(minutes=5)),
        "ha_3": frame("ha_3.fits", NIGHT + timedelta(minutes=10), solved=True),
        "ha_4": frame("ha_4.fits", NIGHT + timedelta(minutes=15), solved=True),
        # OIII the same night, none solved: not counted
        "oiii": frame("oiii.fits", NIGHT, filter_="OIII"),
        # no object name: solved counts on its own, unsolved not at all
        "anon_solved": frame("anon_solved.fits", NIGHT, object_name=None, solved=True, path="misc/"),
        "anon": frame("anon.fits", NIGHT, object_name=None, path="misc/"),
    }
    return context, frames


def _m31_files(context, criteria=None):
    result = reports.catalog_report(context, "NGC", criteria or SearchCriteria(), only_matching=True)
    [entry] = [e for e in result.entries if e.catalog_id == "224"]
    return result, {m.file_name: m for m in result.matches[entry.rowid]}


def test_a_groups_unsolved_frames_count_through_its_earliest_solved_one(library):
    context, _ = library

    result, files = _m31_files(context)

    assert sorted(files) == ["anon_solved.fits", "ha_1.fits", "ha_2.fits", "ha_3.fits", "ha_4.fits"]
    assert result.images_considered == 5
    assert (files["ha_1.fits"].solved, files["ha_1.fits"].solution_file) == (False, "ha_3.fits")
    assert (files["ha_4.fits"].solved, files["ha_4.fits"].solution_file) == (True, "")


def test_the_criteria_still_choose_the_frames(library):
    context, _ = library

    _, files = _m31_files(context, SearchCriteria(local_start=NIGHT + timedelta(minutes=5)))

    assert sorted(files) == ["ha_2.fits", "ha_3.fits", "ha_4.fits"]


def test_without_a_solved_frame_nothing_counts(library):
    context, frames = library
    FileWCS.delete().execute()

    result = reports.catalog_report(context, "NGC", SearchCriteria(), only_matching=True)

    assert result.matches == {} and result.images_considered == 0


def test_mcp_says_which_files_are_solved(library):
    context, _ = library

    result = mcp_server.query_catalog_report(context, "NGC")

    [m31] = [r for r in result["results"] if r["catalog_id"] == "224"]
    assert {f["full_path"].replace("\\", "/").split("/")[-1]: f["solved"] for f in m31["files"]} == {
        "anon_solved.fits": True, "ha_1.fits": False, "ha_2.fits": False, "ha_3.fits": True, "ha_4.fits": True}

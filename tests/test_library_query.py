"""The library query module, through its interface only: criteria in, files out."""
from datetime import datetime

import pytest
from astropy.coordinates import SkyCoord
from astropy.io.fits import Header

from photonfinder.core import compress, hp
from photonfinder.library_query import (
    DEFAULT_ORDER, count_files, distinct_values, files_by_id, files_in_cone, matching_files, search_files,
    select_matching, unsolved_lights,
)
from photonfinder.models import (
    DIAGNOSIS_BAD_FWHM, DIAGNOSIS_NOT_ANALYSED, DIAGNOSIS_OK, NO_PROJECT, File, FileWCS, FitsHeader, Image,
    ImageStats, LibraryRoot, Project, ProjectFile, RootAndPath, SearchCriteria,
)

ALL = {"m31_ha", "m42_oiii", "dark", "flat", "notes.txt"}


def _header(**cards) -> bytes:
    header = Header()
    for key, value in cards.items():
        header[key] = value
    return compress(header.tostring().encode("ascii"))


@pytest.fixture
def library(database):
    """Five files that differ in every criterion. `notes.txt` has no Image row."""
    main = LibraryRoot.create(name="Main", path="/data/")
    other = LibraryRoot.create(name="Other", path="/other/")

    m31 = File.create(root=main, path="lights/m31/", name="m31_ha", size=1, mtime_millis=1)
    Image.create(file=m31, image_type="LIGHT", filter="Ha", camera="ASI2600", telescope="RC8",
                 object_name="M31", exposure=300.0, gain=100, offset=10, binning=1, set_temp=-10.0,
                 date_obs=datetime(2024, 1, 1, 22, 0), coord_ra=10.68, coord_dec=41.27,
                 coord_pix256=int(hp.skycoord_to_healpix(SkyCoord(ra=10.68, dec=41.27, unit="deg"))),
                 coord_radius=0.5, width=6248, height=4176)  # scale 0.48"/px
    FitsHeader.create(file=m31, header=_header(GAIN=100, OBJECT="M31"))
    FileWCS.create(file=m31, wcs=compress(b"wcs"))
    ImageStats.create(file=m31, star_count=500, fwhm_median=3.0, background_median=100.0,
                      background_rms=5.0, elongation_median=1.2, diagnosis=DIAGNOSIS_OK)

    m42 = File.create(root=main, path="lights/m42/", name="m42_oiii", size=1, mtime_millis=1)
    Image.create(file=m42, image_type="LIGHT", filter="OIII", camera="ASI2600", telescope="Esprit 100",
                 object_name="M42", exposure=120.0, gain=0, offset=0, binning=2, set_temp=-20.0,
                 date_obs=datetime(2024, 3, 1, 21, 0), coord_ra=83.82, coord_dec=-5.39,
                 coord_pix256=int(hp.skycoord_to_healpix(SkyCoord(ra=83.82, dec=-5.39, unit="deg"))),
                 coord_radius=0.6, width=3000, height=2000)  # scale 0.6"/px
    FitsHeader.create(file=m42, header=_header(GAIN=0, OBJECT="M42"))
    ImageStats.create(file=m42, star_count=50, fwhm_median=6.0, background_median=300.0,
                      background_rms=15.0, elongation_median=2.0, diagnosis=DIAGNOSIS_BAD_FWHM)

    dark = File.create(root=main, path="darks/", name="dark", size=1, mtime_millis=1)
    Image.create(file=dark, image_type="DARK", camera="ASI2600", exposure=300.0, gain=100, offset=10,
                 binning=1, set_temp=-10.0, date_obs=datetime(2024, 1, 2, 9, 0))

    flat = File.create(root=other, path="flats/", name="flat", size=1, mtime_millis=1)
    Image.create(file=flat, image_type="FLAT", filter="Ha", camera="ASI533", exposure=1.5, binning=1,
                 set_temp=-9.6, date_obs=datetime(2024, 1, 3, 9, 0))

    File.create(root=main, path="", name="notes.txt", size=1, mtime_millis=1)

    andromeda = Project.create(name="Andromeda")
    ProjectFile.create(project=andromeda, file=m31)
    return {"main": main, "other": other, "andromeda": andromeda}


def _names(criteria: SearchCriteria) -> set[str]:
    rows, total, has_more = search_files(criteria, 0, 100)
    names = {row.name for row in rows}
    assert total == len(rows) == count_files(criteria)
    assert not has_more
    return names


CASES = {
    # criterion: (criteria from the library fixture, expected file names)
    "no criteria": (lambda lib: SearchCriteria(), ALL),
    "type": (lambda lib: SearchCriteria(type="LIGHT"), {"m31_ha", "m42_oiii"}),
    "filter": (lambda lib: SearchCriteria(filter="Ha"), {"m31_ha", "flat"}),
    "camera": (lambda lib: SearchCriteria(camera="ASI533"), {"flat"}),
    "object name, contained": (lambda lib: SearchCriteria(object_name="M3"), {"m31_ha"}),
    "file name, contained": (lambda lib: SearchCriteria(file_name="oii"), {"m42_oiii"}),
    "telescope, contained": (lambda lib: SearchCriteria(telescope="Esprit"), {"m42_oiii"}),
    "exposure": (lambda lib: SearchCriteria(exposure="300"), {"m31_ha", "dark"}),
    "exposure with tolerance": (lambda lib: SearchCriteria(exposure="1.4", exposure_tolerance=0.2), {"flat"}),
    "exposure unparsable is skipped": (lambda lib: SearchCriteria(exposure="abc"), ALL),
    "gain": (lambda lib: SearchCriteria(gain="100"), {"m31_ha", "dark"}),
    "gain zero is a value": (lambda lib: SearchCriteria(gain="0"), {"m42_oiii"}),
    "offset zero is a value": (lambda lib: SearchCriteria(offset=0), {"m42_oiii"}),
    "binning": (lambda lib: SearchCriteria(binning="2"), {"m42_oiii"}),
    "temperature": (lambda lib: SearchCriteria(temperature="-10"), {"m31_ha", "dark"}),
    "temperature with tolerance": (lambda lib: SearchCriteria(temperature="-10", temperature_tolerance=0.5),
                                   {"m31_ha", "dark", "flat"}),
    "dates": (lambda lib: SearchCriteria(start_datetime=datetime(2024, 1, 1, 12, 0),
                                         end_datetime=datetime(2024, 1, 2, 12, 0)), {"m31_ha", "dark"}),
    "cone": (lambda lib: SearchCriteria(coord_ra="00 42 44", coord_dec="+41 16 09", coord_radius=1.0),
             {"m31_ha"}),
    "scale": (lambda lib: SearchCriteria(scale_min=0.4, scale_max=0.5), {"m31_ha"}),
    "width": (lambda lib: SearchCriteria(width_min=5000), {"m31_ha"}),
    "height": (lambda lib: SearchCriteria(height_max=2500), {"m42_oiii"}),
    "star count": (lambda lib: SearchCriteria(star_count_min=100), {"m31_ha"}),
    "fwhm": (lambda lib: SearchCriteria(fwhm_min=4.0), {"m42_oiii"}),
    "background": (lambda lib: SearchCriteria(background_max=200.0), {"m31_ha"}),
    "background rms": (lambda lib: SearchCriteria(background_rms_min=10.0), {"m42_oiii"}),
    "elongation": (lambda lib: SearchCriteria(elongation_max=1.5), {"m31_ha"}),
    "diagnosis hides a bad value": (lambda lib: SearchCriteria(diagnosis_hidden=[DIAGNOSIS_BAD_FWHM]),
                                    ALL - {"m42_oiii"}),
    "diagnosis hides the not analysed": (lambda lib: SearchCriteria(diagnosis_hidden=[DIAGNOSIS_NOT_ANALYSED]),
                                         {"m31_ha", "m42_oiii"}),
    "header text": (lambda lib: SearchCriteria(header_text="M42"), {"m42_oiii"}),
    "header value equal": (lambda lib: SearchCriteria(header_text="GAIN=0"), {"m42_oiii"}),
    "header value above": (lambda lib: SearchCriteria(header_text="GAIN>50"), {"m31_ha"}),
    "header value below": (lambda lib: SearchCriteria(header_text="GAIN<50"), {"m42_oiii"}),
    "plate solved": (lambda lib: SearchCriteria(plate_solved=True), {"m31_ha"}),
    "plate unsolved": (lambda lib: SearchCriteria(plate_solved=False), ALL - {"m31_ha"}),
    "project": (lambda lib: SearchCriteria(project=lib["andromeda"]), {"m31_ha"}),
    "no project": (lambda lib: SearchCriteria(project=NO_PROJECT), ALL - {"m31_ha"}),
    "path prefix": (lambda lib: SearchCriteria(paths=[RootAndPath(lib["main"].rowid, path="lights")]),
                    {"m31_ha", "m42_oiii"}),
    "library root": (lambda lib: SearchCriteria(paths=[RootAndPath(lib["other"].rowid)]), {"flat"}),
    "several paths": (lambda lib: SearchCriteria(paths=[RootAndPath(lib["other"].rowid),
                                                        RootAndPath(lib["main"].rowid, path="darks")]),
                      {"flat", "dark"}),
    "all libraries": (lambda lib: SearchCriteria(paths=[RootAndPath(None)]), ALL),
    "exact directory": (lambda lib: SearchCriteria(paths=[RootAndPath(lib["main"].rowid, path="lights/m31")],
                                                   paths_as_prefix=False), {"m31_ha"}),
    "exact directory of a root": (lambda lib: SearchCriteria(paths=[RootAndPath(lib["main"].rowid)],
                                                             paths_as_prefix=False), {"notes.txt"}),
    "exact directory of all libraries": (lambda lib: SearchCriteria(paths=[RootAndPath(None)],
                                                                    paths_as_prefix=False), ALL),
}


@pytest.mark.parametrize("make_criteria, expected", CASES.values(), ids=CASES.keys())
def test_each_criterion_selects_its_files(library, make_criteria, expected):
    assert _names(make_criteria(library)) == expected


@pytest.mark.parametrize("field", ["type", "filter", "camera"])
def test_an_empty_string_is_no_constraint_and_none_is_null(library, field):
    assert _names(SearchCriteria(**{field: ""})) == ALL
    without_value = {"type": {"notes.txt"}, "filter": {"dark", "notes.txt"}, "camera": {"notes.txt"}}
    assert _names(SearchCriteria(**{field: None})) == without_value[field]


def test_files_come_in_the_default_order(library):
    rows = search_files(SearchCriteria()).rows
    assert [(row.root.name, row.path, row.name) for row in rows] == [
        ("Main", "", "notes.txt"), ("Main", "darks/", "dark"), ("Main", "lights/m31/", "m31_ha"),
        ("Main", "lights/m42/", "m42_oiii"), ("Other", "flats/", "flat")]
    assert DEFAULT_ORDER == (File.root, File.path, File.name)


def test_a_named_sort_field_orders_the_files(library):
    rows = search_files(SearchCriteria(type="LIGHT", sorting_field="exposure", sorting_desc=False)).rows
    assert [row.name for row in rows] == ["m42_oiii", "m31_ha"]


def test_a_sort_column_index_orders_the_files(library):
    rows = search_files(SearchCriteria(sorting_index=0, sorting_desc=True)).rows  # 0: file name
    assert [row.name for row in rows] == ["notes.txt", "m42_oiii", "m31_ha", "flat", "dark"]


def test_an_unknown_sort_field_is_refused(library):
    with pytest.raises(ValueError, match="Unknown sorting_field"):
        search_files(SearchCriteria(sorting_field="colour"))


def test_pages_split_the_files_and_count_them_all(library):
    first = search_files(SearchCriteria(), page=0, page_size=2)
    last = search_files(SearchCriteria(), page=2, page_size=2)
    assert (len(first.rows), first.total, first.has_more) == (2, 5, True)
    assert (len(last.rows), last.total, last.has_more) == (1, 5, False)
    assert count_files(SearchCriteria()) == 5


def test_matching_files_follow_the_criteria_in_the_default_order(library):
    files = matching_files(SearchCriteria(camera="ASI2600"))
    assert [file.name for file in files] == ["dark", "m31_ha", "m42_oiii"]
    assert (files[1].image.filter, files[1].root.name) == ("Ha", "Main")


def test_matching_files_include_a_file_without_an_image(library):
    files = matching_files(SearchCriteria(file_name="notes"))
    assert [file.name for file in files] == ["notes.txt"]
    assert files[0].image is None


def test_matching_files_load_the_header_and_solution_when_asked(library):
    plain, = matching_files(SearchCriteria(file_name="m31"))
    loaded = {file.name: file for file in matching_files(SearchCriteria(), with_header=True, with_solution=True)}
    assert not hasattr(plain, "fitsheader") and not hasattr(plain, "filewcs")
    assert loaded["m31_ha"].fitsheader.header and loaded["m31_ha"].filewcs.wcs
    assert loaded["dark"].fitsheader is None and loaded["dark"].filewcs is None


def test_unsolved_lights_are_the_unsolved_light_frames_and_untyped_frames(library):
    master = File.create(root=library["main"], path="masters/", name="master_light", size=1, mtime_millis=1)
    Image.create(file=master, image_type="MASTER LIGHT", camera="ASI2600")
    untyped = File.create(root=library["main"], path="masters/", name="untyped", size=1, mtime_millis=1)
    Image.create(file=untyped, camera="ASI2600")
    # m31_ha is a solved LIGHT, dark and flat are calibration, notes.txt has no Image row
    assert [f.name for f in unsolved_lights(SearchCriteria())] == ["m42_oiii", "master_light", "untyped"]
    assert [f.name for f in unsolved_lights(SearchCriteria(camera="ASI2600"))] == [
        "m42_oiii", "master_light", "untyped"]
    assert unsolved_lights(SearchCriteria(plate_solved=True)) == []


def test_select_matching_projects_columns_of_the_matching_files(library):
    rows = select_matching(SearchCriteria(type="LIGHT"), File.name, Image.filter, FitsHeader.header,
                           with_header=True).order_by(File.name).tuples()
    assert [(name, filter_, bool(header)) for name, filter_, header in rows] == [
        ("m31_ha", "Ha", True), ("m42_oiii", "OIII", True)]
    no_image = select_matching(SearchCriteria(file_name="notes"), File.name, Image.image_type).tuples()
    assert list(no_image) == [("notes.txt", None)]


def test_select_matching_solved_only_keeps_plate_solved_files(library):
    def names(criteria):
        return {name for name, in select_matching(criteria, File.name, solved_only=True).tuples()}
    assert names(SearchCriteria()) == {"m31_ha"}
    assert names(SearchCriteria(camera="ASI533")) == set()
    assert names(SearchCriteria(plate_solved=False)) == set()


def test_require_image_leaves_out_a_file_without_an_image_row(library):
    criteria = SearchCriteria(paths=[RootAndPath(library["main"].rowid)], paths_as_prefix=False)
    assert [f.name for f in matching_files(criteria)] == ["notes.txt"]
    assert matching_files(criteria, require_image=True) == []
    assert {name for name, in select_matching(SearchCriteria(), File.name, require_image=True).tuples()} == \
           ALL - {"notes.txt"}


def test_distinct_values_follow_the_criteria_but_not_their_own(library):
    assert distinct_values(SearchCriteria(), Image.filter) == [None, "Ha", "OIII"]
    assert distinct_values(SearchCriteria(type="LIGHT"), Image.filter) == ["Ha", "OIII"]
    # the filter combo still lists the other filters while one is chosen
    assert distinct_values(SearchCriteria(type="LIGHT", filter="Ha"), Image.filter) == ["Ha", "OIII"]
    assert distinct_values(SearchCriteria(filter="Ha"), Image.camera) == ["ASI2600", "ASI533"]
    assert distinct_values(SearchCriteria(paths=[RootAndPath(library["other"].rowid)]), Image.image_type) == ["FLAT"]


def test_files_in_cone_measure_the_distance_the_pixels_only_approximate(library):
    near = File.create(root=library["main"], path="lights/m31/", name="m31_edge", size=1, mtime_millis=1)
    edge = SkyCoord(ra=10.68, dec=41.27 + 0.45, unit="deg")  # 0.45° from M31's centre, in a touched pixel
    Image.create(file=near, image_type="LIGHT", coord_ra=edge.ra.deg, coord_dec=edge.dec.deg,
                 coord_pix256=int(hp.skycoord_to_healpix(edge)))
    cone = dict(coord_ra=str(10.68 / 15), coord_dec="41.27")
    assert {f.name for f in search_files(SearchCriteria(**cone, coord_radius=0.4)).rows} >= {"m31_ha", "m31_edge"}
    assert [f.name for f in files_in_cone(SearchCriteria(**cone, coord_radius=0.4))] == ["m31_ha"]
    assert [f.name for f in files_in_cone(SearchCriteria(**cone, coord_radius=0.5))] == ["m31_edge", "m31_ha"]
    assert files_in_cone(SearchCriteria(**cone, coord_radius=0.5, type="DARK")) == []
    with pytest.raises(ValueError):
        files_in_cone(SearchCriteria())


def test_files_by_id_load_those_files(library):
    wanted = [f.rowid for f in matching_files(SearchCriteria(type="LIGHT"))]
    files = files_by_id([*wanted, 999_999])
    assert sorted(f.name for f in files) == ["m31_ha", "m42_oiii"]
    assert {f.image.image_type for f in files} == {"LIGHT"}


def test_a_row_carries_its_image_root_and_joined_columns(library):
    row = next(r for r in search_files(SearchCriteria(type="LIGHT")).rows if r.name == "m31_ha")
    assert (row.image.filter, row.root.name, bool(row.has_wcs)) == ("Ha", "Main", True)
    assert row.projectfile.project_names == "Andromeda"
    assert row.imagestats.stats_star_count == 500

"""The Plate solution module keeps a file's stored solution and the Image columns derived from it
in step: real database, real WCS headers, nothing stubbed but the solver."""
from pathlib import Path

import pytest
from astropy.io.fits import Header

from photonfinder import library_query, plate_solution, solve_service
from photonfinder.core import compress
from photonfinder.models import File, FileWCS, FitsHeader, Image, LibraryRoot, SearchCriteria
from photonfinder.platesolver import SolverBase, SolverType
from tests.conftest import fixed_solution_header

# Where the mount pointed (Orion), far from where fixed_solution_header() solves (M31).
POINTING_RA, POINTING_DEC = 83.82, -5.39


class FixedSolver(SolverBase):
    """Solves every file to fixed_solution_header()."""

    @property
    def solver_type(self):
        return SolverType.ASTAP

    def solve(self, image_path: Path, image=None, hint=None, output_callback=None, file_wcs=None, cancelled=None):
        return fixed_solution_header()


def _add_file(root, name, header: Header | None):
    file = File.create(root=root, path="lights/", name=name, size=1, mtime_millis=1)
    Image.create(file=file, image_type="LIGHT", width=200, height=200)
    if header is not None:
        FitsHeader.create(file=file, header=compress(header.tostring().encode("ascii")))
    return file


def _pointing_header():
    header = Header()
    header["IMAGETYP"] = "Light Frame"
    header["RA"] = POINTING_RA
    header["DEC"] = POINTING_DEC
    return header


def _solve(file):
    loaded = File.select(File, Image).join(Image).where(File.rowid == file.rowid).get()
    outcome = solve_service.solve_file(loaded, FixedSolver())
    assert outcome.success, outcome.error


def _image(file) -> Image:
    return Image.get(Image.file == file)


def _cone(ra_deg, dec_deg, radius=0.5) -> SearchCriteria:
    return SearchCriteria(coord_ra=str(ra_deg / 15.0), coord_dec=str(dec_deg), coord_radius=radius)


def _assert_invariant():
    """Has a stored solution exactly when radius and scale are known."""
    solved = {row.file_id for row in FileWCS.select(FileWCS.file)}
    for image in Image.select():
        has_radius_and_scale = image.coord_radius is not None and image.coord_scale is not None
        assert has_radius_and_scale == (image.file_id in solved), image.file.name


@pytest.fixture
def library(context):
    root = LibraryRoot.create(name="Main", path="/data/")
    return {
        "pointed": _add_file(root, "pointed.fits", _pointing_header()),
        "no_pointing": _add_file(root, "no_pointing.fits", Header()),
        "no_header": _add_file(root, "no_header.fits", None),
    }


def test_clearing_puts_the_centre_back_to_the_header_pointing(library):
    file = library["pointed"]
    _solve(file)

    assert plate_solution.clear(file) == (pytest.approx(POINTING_RA), pytest.approx(POINTING_DEC))

    image = _image(file)
    assert not FileWCS.select().where(FileWCS.file == file).exists()
    assert (image.coord_ra, image.coord_dec) == (pytest.approx(POINTING_RA), pytest.approx(POINTING_DEC))
    assert image.coord_radius is None and image.coord_scale is None


@pytest.mark.parametrize("name", ["no_pointing", "no_header"])
def test_clearing_without_a_header_pointing_leaves_no_centre(library, name):
    file = library[name]
    _solve(file)

    assert plate_solution.clear(file) == (None, None)

    image = _image(file)
    assert (image.coord_ra, image.coord_dec, image.coord_pix256, image.coord_radius) == (None, None, None, None)


def test_a_cleared_file_no_longer_matches_a_cone_search_around_its_solved_centre(library):
    file = library["pointed"]
    _solve(file)
    around_solution = _cone(10.68, 41.27)
    assert [f.name for f in library_query.files_in_cone(around_solution)] == ["pointed.fits"]

    plate_solution.clear(file)

    assert library_query.files_in_cone(around_solution) == []
    assert [f.name for f in library_query.files_in_cone(_cone(POINTING_RA, POINTING_DEC))] == ["pointed.fits"]


def test_a_cleared_file_no_longer_matches_a_scale_filter(library):
    file = library["pointed"]
    _solve(file)
    criteria = SearchCriteria(scale_min=3.0, scale_max=4.0)  # fixed_solution_header() is 3.6"/px
    assert [f.name for f in library_query.matching_files(criteria)] == ["pointed.fits"]

    plate_solution.clear(file)

    assert library_query.matching_files(criteria) == []


def test_solution_and_radius_go_together_through_solve_and_clear(library):
    _assert_invariant()
    for file in library.values():
        _solve(file)
    _assert_invariant()
    plate_solution.clear(library["pointed"])
    plate_solution.clear(library["no_header"])
    _assert_invariant()


def test_clearing_an_unsolved_file_changes_nothing_but_stale_coordinates(library):
    file = library["pointed"]
    Image.update(coord_ra=1.0, coord_dec=2.0, coord_radius=0.3).where(Image.file == file).execute()

    plate_solution.clear(file)

    image = _image(file)
    assert (image.coord_ra, image.coord_dec, image.coord_radius) == (
        pytest.approx(POINTING_RA), pytest.approx(POINTING_DEC), None)

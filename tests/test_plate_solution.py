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


# --- store and copy ---------------------------------------------------------------------------

M106_SIZE = (5496, 3672)  # the frame astrometry.net solved; its .wcs.fits leaves the size out


def _m106_solution(with_size=True) -> Header:
    header = Header.fromfile(Path(__file__).parent / "data-small/M106_2020-03-17T024357_60sec_LP__-15C_frame11.wcs.fits")
    if with_size:
        header["NAXIS1"], header["NAXIS2"] = M106_SIZE
    return header


def _sized_file(root, name, size):
    file = File.create(root=root, path="lights/", name=name, size=1, mtime_millis=1)
    Image.create(file=file, image_type="LIGHT", width=size[0] if size else None, height=size[1] if size else None)
    return file


def _assert_agrees_with_its_solution(file):
    """Centre, radius and scale on the Image are what its stored solution gives."""
    from astropy.wcs import WCS
    from astropy.wcs.utils import proj_plane_pixel_scales
    image = _image(file)
    solution = plate_solution.load(file)
    ra, dec, healpix, radius = plate_solution.image_centre(solution, image.width, image.height)
    assert (image.coord_ra, image.coord_dec, image.coord_pix256) == (pytest.approx(ra), pytest.approx(dec), healpix)
    assert image.coord_radius == pytest.approx(radius)
    pixel_scale = float(proj_plane_pixel_scales(WCS(solution))[0]) * 3600
    assert image.coord_scale == pytest.approx(pixel_scale, rel=0.02)


@pytest.fixture
def root(context):
    return LibraryRoot.create(name="Main", path="/data/")


def test_a_stored_solution_and_the_image_agree(root):
    file = _sized_file(root, "m106.fits", M106_SIZE)

    stored = plate_solution.store(file, _m106_solution(), "ASTROMETRY_NET")

    _assert_agrees_with_its_solution(file)
    assert stored.image_updated and (stored.width, stored.height) == M106_SIZE
    assert 1.13 < stored.radius < 1.14
    assert plate_solution.origin_of(plate_solution.load(file)) == "ASTROMETRY_NET"
    _assert_invariant()


def test_a_solution_without_its_size_takes_the_image_size(root):
    file = _sized_file(root, "m106.fits", M106_SIZE)

    stored = plate_solution.store(file, _m106_solution(with_size=False), "ASTROMETRY_NET")

    assert 1.13 < stored.radius < 1.14
    _assert_agrees_with_its_solution(file)
    _assert_invariant()


def test_a_solution_gives_an_image_of_unknown_size_its_size(root):
    file = _sized_file(root, "m106.fits", None)

    plate_solution.store(file, _m106_solution(), "ASTAP")

    assert (_image(file).width, _image(file).height) == M106_SIZE
    _assert_agrees_with_its_solution(file)


def test_an_unusable_solution_changes_nothing(root):
    file = _sized_file(root, "m106.fits", M106_SIZE)
    plate_solution.store(file, _m106_solution(), "ASTAP")
    before = _image(file).coord_ra
    broken = _m106_solution()
    del broken["CRVAL1"]

    with pytest.raises(ValueError):
        plate_solution.store(file, broken, "ASTAP")

    assert plate_solution.origin_of(plate_solution.load(file)) == "ASTAP"
    assert _image(file).coord_ra == before


def test_a_copied_solution_is_the_targets_own(root):
    source = _sized_file(root, "source.fits", M106_SIZE)
    target = _sized_file(root, "target.fits", M106_SIZE)
    plate_solution.store(source, _m106_solution(), "ASTROMETRY_NET")

    result = plate_solution.copy(source, [target], overwrite=False)

    assert result.copied == [target]
    _assert_agrees_with_its_solution(target)
    assert (_image(target).coord_ra, _image(target).coord_radius) == (
        pytest.approx(_image(source).coord_ra), pytest.approx(_image(source).coord_radius))
    copied = plate_solution.load(target)
    assert plate_solution.origin_of(copied) == "COPY:source.fits/ASTROMETRY_NET"
    assert sum(str(c).startswith("WCS_ORIGIN=") for c in copied["COMMENT"]) == 1
    _assert_invariant()


def test_a_copy_skips_frames_of_another_size(root):
    source = _sized_file(root, "source.fits", M106_SIZE)
    binned = _sized_file(root, "binned.fits", (2748, 1836))
    unknown = _sized_file(root, "unknown.fits", None)
    plate_solution.store(source, _m106_solution(with_size=False), "ASTAP")

    result = plate_solution.copy(source, [binned, unknown], overwrite=False)

    assert result.other_dimensions == [binned] and result.copied == [unknown]
    assert plate_solution.load(binned) is None
    assert (_image(unknown).width, _image(unknown).height) == M106_SIZE
    _assert_agrees_with_its_solution(unknown)
    _assert_invariant()


def test_a_copy_overwrites_a_solution_only_when_asked(root):
    source = _sized_file(root, "source.fits", (200, 200))
    target = _sized_file(root, "target.fits", (200, 200))
    plate_solution.store(source, fixed_solution_header(), "ASTAP")
    plate_solution.store(target, _m106_solution(with_size=False).copy(), "ASTROMETRY_NET")
    Image.update(width=200, height=200).where(Image.file == target).execute()

    kept = plate_solution.copy(source, [target], overwrite=False)
    assert kept.already_solved == [target]
    assert plate_solution.origin_of(plate_solution.load(target)) == "ASTROMETRY_NET"

    replaced = plate_solution.copy(source, [target], overwrite=True)
    assert replaced.copied == [target]
    assert plate_solution.origin_of(plate_solution.load(target)) == "COPY:source.fits/ASTAP"
    _assert_agrees_with_its_solution(target)


# --- import and Rebuild index share one path --------------------------------------------------

def _import(root, monkeypatch, name, header_text):
    """Import a file the way a scan does, without Qt; only reading its header off disk is stood in for."""
    from unittest.mock import MagicMock
    from photonfinder import filesystem
    from photonfinder.core import StatusReporter
    header_bytes = Header.fromstring(header_text.strip(), "\n").tostring().encode("ascii")
    monkeypatch.setattr(filesystem, "read_fits_header", lambda *_: header_bytes)
    file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
    filesystem.update_fits_header_cache(filesystem.ChangeList(new_files=[file]), StatusReporter(), MagicMock())
    return file


def _row(file):
    image = Image.select().where(Image.file == file).dicts().get()
    image.pop("rowid")
    solution = plate_solution.load(file)
    return image, solution.tostring() if solution is not None else None


def test_a_freshly_imported_pre_solved_file_has_radius_and_scale(root, monkeypatch):
    from tests.sample_headers import header_sgp_fixed_wcs

    file = _import(root, monkeypatch, "solved.fits", header_sgp_fixed_wcs)

    image = _image(file)
    assert image.coord_radius is not None and image.coord_scale is not None
    assert plate_solution.origin_of(plate_solution.load(file)) == "IMPORT"
    _assert_agrees_with_its_solution(file)
    _assert_invariant()


@pytest.mark.parametrize("header_name", ["header_sgp_fixed_wcs", "header_nina", "header_apt"])
def test_rebuild_index_gives_the_rows_import_gave(root, monkeypatch, header_name):
    from unittest.mock import MagicMock
    from photonfinder.core import StatusReporter
    from photonfinder.filesystem import reindex_library
    import tests.sample_headers as headers
    file = _import(root, monkeypatch, "frame.fits", getattr(headers, header_name))
    imported = _row(file)
    # What an older version may have left: no solution, a stale position.
    FileWCS.delete().where(FileWCS.file == file).execute()
    Image.update(coord_ra=1.0, coord_dec=2.0, coord_pix256=3, coord_radius=4.0).where(Image.file == file).execute()

    reindex_library(StatusReporter(), MagicMock())

    assert _row(file) == imported
    _assert_invariant()

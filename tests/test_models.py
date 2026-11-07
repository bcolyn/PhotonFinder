import pytest

from photonfinder.models import *

coord1 = SkyCoord(5.4778 * 15, 35.8239, unit=u.deg, frame='icrs')
coord2 = SkyCoord(5.4689 * 15, 35.3300, unit=u.deg, frame='icrs')


@pytest.mark.usefixtures("app_context")
class TestModels:
    # Class-level fixtures
    @pytest.fixture(scope="class", autouse=True)
    def setup_class(self, app_context):
        """Set up test data once for the entire class."""
        # Create a root
        root = LibraryRoot.create(name="Test Root", path="C:/test_path/")

        # Create files in different paths
        file1 = File.create(root=root, path="./", name="file1.fits", size=1000, mtime_millis=1000)
        file2 = File.create(root=root, path="subdir1/", name="file2.fits", size=2000, mtime_millis=1000)
        file3 = File.create(root=root, path="subdir1/", name="file3.fits", size=2000, mtime_millis=1000)
        file4 = File.create(root=root, path="subdir2/", name="file4.fits", size=1000, mtime_millis=1000)

        # Create images with different filters; file1/file3 are wide (4656px), file2/file4 are narrow (1920px)
        Image.create(file=file1, filter="Red", image_type="Light", exposure=10.0, gain=100, binning=1,
                     width=4656, height=3520, coord_radius=0.5)
        Image.create(file=file2, filter="Green", image_type="Dark", exposure=20.0, gain=100, binning=1,
                     width=1920, height=1080, coord_radius=0.2)
        Image.create(file=file3, filter="Blue", image_type="Light", exposure=20.0, gain=100, binning=1,
                     coord_ra=coord1.ra.value, coord_dec=coord1.dec.value,
                     coord_pix256=int(hp.skycoord_to_healpix(coord1)),
                     width=4656, height=3520, coord_radius=0.5)
        Image.create(file=file4, filter="Luminance", image_type="Light", exposure=10.0, gain=100, binning=1,
                     coord_ra=coord2.ra.value, coord_dec=coord2.dec.value,
                     coord_pix256=int(hp.skycoord_to_healpix(coord2)),
                     width=1920, height=1080, coord_radius=0.2)
        # ImageStats for file1 (100 stars, FWHM 2.5) and file3 (200 stars, FWHM 4.0)
        # file2 and file4 intentionally have no stats to test the inner-join behaviour
        ImageStats.create(file=file1, star_count=100, fwhm_median=2.5,
                          background_median=500.0, background_rms=10.0, elongation_median=1.2)
        ImageStats.create(file=file3, star_count=200, fwhm_median=4.0,
                          background_median=1000.0, background_rms=25.0, elongation_median=1.8)
        project = Project.create(name="TestProject")
        ProjectFile.create(project=project, file=file1)
        ProjectFile.create(project=project, file=file2)

        yield app_context

    @pytest.fixture(autouse=True)
    def setup_method(self, app_context):
        """Set up a transaction for each test method."""
        with app_context.database.atomic() as transaction:
            yield
            transaction.rollback()

    def test_find_by_filename(self):
        file = File.find_by_filename("C:/test_path/subdir1/file2.fits")
        assert file is not None
        assert file.name == "file2.fits"
        assert file.root.name == "Test Root"

    def test_find_projectfile_by_filename(self):
        project_file = ProjectFile.find_by_filename("C:/test_path/subdir1/file2.fits", 1)
        assert project_file is not None
        assert project_file.rowid
        assert project_file.file.name == "file2.fits"
        assert project_file.file.root.name == "Test Root"
        assert project_file.project.name == "TestProject"

        project_file = ProjectFile.find_by_filename("C:/test_path/subdir1/file3.fits", 1)
        assert project_file is not None
        assert project_file.rowid is None
        assert project_file.file.name == "file3.fits"
        assert project_file.file.root.name == "Test Root"
        assert project_file.project.name == "TestProject"

        project_file = ProjectFile.find_by_filename("does_not_match.fits", 1)
        assert project_file is None

    def test_sky_distance(self):
        value = (Image.select(fn.sky_distance(Image.coord_ra, Image.coord_dec, coord1.ra.value, coord1.dec.value))
                 .where(Image.rowid == 4).scalar())
        assert value == pytest.approx(0.5056942)


    def test_criteria_serde(self):
        file_object = (File.select(File, Image, LibraryRoot)
                       .join_from(File, LibraryRoot)
                       .join_from(File, Image, JOIN.LEFT_OUTER)
                       .where(File.rowid==1)).get()

        search_criteria = SearchCriteria(
            paths=[RootAndPath(1, "dummy", "subdir1")], paths_as_prefix=False, filter="Ha",
            reference_file=file_object, start_datetime=datetime.now(), end_datetime=datetime.now(),
        )
        json_bytes = search_criteria.to_json()
        deser = SearchCriteria.from_json(json_bytes)
        assert deser == search_criteria
        assert deser.reference_file.image.filter == "Red"

        empty_criteria = SearchCriteria()
        json_bytes = empty_criteria.to_json()
        deser = SearchCriteria.from_json(json_bytes)
        assert deser == empty_criteria

    def test_criteria_serde_all(self):
        all_search_criteria = [
            SearchCriteria(
                paths=[RootAndPath(1, "dummy", "subdir1")], paths_as_prefix=False, filter="Ha",
                reference_file=File.get_by_id(1), start_datetime=datetime.now(), end_datetime=datetime.now(),
            ),
            SearchCriteria(
                project=NO_PROJECT, type="LIGHT"
            ),
            SearchCriteria(
                gain=120, offset=10, camera="ZWO ASI 294MC"
            )
        ]
        json_bytes = SearchCriteria.list_to_json(all_search_criteria)
        deser = SearchCriteria.from_json(json_bytes)
        assert deser == all_search_criteria



class TestSearchCriteriaStr:
    """SearchCriteria.__str__ drives generated tab titles and filter-button labels."""

    def test_empty_criteria(self):
        assert str(SearchCriteria()) == ""

    def test_simple_text_fields_joined(self):
        criteria = SearchCriteria(type="Light", filter="Red", object_name="M31")
        assert str(criteria) == "Light, Red, M31"

    def test_single_path(self):
        criteria = SearchCriteria(paths=[RootAndPath(1, "Root", "sub/path")])
        assert str(criteria) == "Root/sub/path"

    def test_multiple_paths_shows_first_plus_count(self):
        criteria = SearchCriteria(paths=[
            RootAndPath(1, "Root", "sub/path"),
            RootAndPath(1, "Root", "other/path"),
        ])
        assert str(criteria) == "Root/sub/path+1"

    def test_coordinates_without_query_are_shortened(self):
        criteria = SearchCriteria(coord_ra="12:34:56.78", coord_dec="+45:12:33.10", coord_radius=0.5)
        assert str(criteria) == "(12:34:56+45:12:33) ±0.5°"

    def test_coordinates_with_query_prefer_the_query_label(self):
        criteria = SearchCriteria(coord_ra="12:34:56.78", coord_dec="+45:12:33.10",
                                   coord_radius=0.5, coord_query="M31")
        assert str(criteria) == "M31 ±0.5°"

    def test_plate_solved_true_and_false(self):
        assert "Solved" in str(SearchCriteria(plate_solved=True))
        assert "Unsolved" in str(SearchCriteria(plate_solved=False))
        assert str(SearchCriteria(plate_solved=None)) == ""

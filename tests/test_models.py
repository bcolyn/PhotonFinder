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

        # Create images with different filters
        Image.create(file=file1, filter="Red", image_type="Light", exposure=10.0, gain=100, binning=1)
        Image.create(file=file2, filter="Green", image_type="Dark", exposure=20.0, gain=100, binning=1)
        Image.create(file=file3, filter="Blue", image_type="Light", exposure=20.0, gain=100, binning=1,
                     coord_ra=coord1.ra.value, coord_dec=coord1.dec.value,
                     coord_pix256=int(hp.skycoord_to_healpix(coord1)))
        Image.create(file=file4, filter="Luminance", image_type="Light", exposure=10.0, gain=100, binning=1,
                     coord_ra=coord2.ra.value, coord_dec=coord2.dec.value,
                     coord_pix256=int(hp.skycoord_to_healpix(coord2)))
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

    def test_get_filters_empty_criteria(self):
        """Test get_filters with empty search criteria returns all distinct filters."""
        # Create empty search criteria
        search_criteria = SearchCriteria()

        # Get all filters
        filters = Image.get_distinct_values_available(search_criteria, Image.filter)

        # Assert that all filters are returned
        assert filters == ["Blue", "Green", "Luminance", "Red"]

    def test_get_image_type(self):
        types = Image.get_distinct_values_available(SearchCriteria(), Image.image_type)
        assert types == ["Dark", "Light"]

    def test_get_file_sizes(self):
        # not expected to be used this way, but we can test it anyway.
        sizes = Image.get_distinct_values_available(SearchCriteria(), File.size)
        assert sizes == [1000, 2000]

    def test_get_filters_with_path(self):
        """Test get_filters with a specific RootAndPath returns only filters in that path."""
        # Create search criteria with a specific path
        search_criteria = SearchCriteria([
            RootAndPath(1, "dummy", "subdir1")
        ])

        # Get filters for the specific path
        filters = Image.get_distinct_values_available(search_criteria, Image.filter)

        # Assert that only filters in the specified path are returned
        assert filters == ["Blue", "Green"]

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
        search_criteria = SearchCriteria(
            paths=[RootAndPath(1, "dummy", "subdir1")], paths_as_prefix=False, filter="Ha",
            reference_file=File.get_by_id(1), start_datetime=datetime.now(), end_datetime=datetime.now(),
        )
        json_bytes = search_criteria.to_json()
        deser = SearchCriteria.from_json(json_bytes)
        assert deser == search_criteria

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

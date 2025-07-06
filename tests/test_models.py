import pytest

from photonfinder.models import *


@pytest.mark.usefixtures("app_context")
class TestImage:
    # Class-level fixtures
    @pytest.fixture(scope="class", autouse=True)
    def setup_class(self, app_context):
        """Set up test data once for the entire class."""
        # Create a root
        root = LibraryRoot.create(name="Test Root", path="C:\\test_path")

        # Create files in different paths
        file1 = File.create(root=root, path="./", name="file1.fits", size=1000, mtime_millis=1000)
        file2 = File.create(root=root, path="subdir1/", name="file2.fits", size=2000, mtime_millis=1000)
        file3 = File.create(root=root, path="subdir1/", name="file3.fits", size=2000, mtime_millis=1000)
        file4 = File.create(root=root, path="subdir2/", name="file4.fits", size=1000, mtime_millis=1000)

        # Create images with different filters
        Image.create(file=file1, filter="Red", image_type="Light", exposure=10.0, gain=100, binning=1)
        Image.create(file=file2, filter="Green", image_type="Dark", exposure=20.0, gain=100, binning=1)
        Image.create(file=file3, filter="Blue", image_type="Light", exposure=20.0, gain=100, binning=1)
        Image.create(file=file4, filter="Luminance", image_type="Light", exposure=10.0, gain=100, binning=1)

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

    def test_criteria_serde(self):
        search_criteria = SearchCriteria(
            paths=[RootAndPath(1, "dummy", "subdir1")], paths_as_prefix=False, filter="Ha",
            reference_file=File.get_by_id(1), start_datetime=datetime.now(), end_datetime=datetime.now(),
        )
        json_bytes = search_criteria.to_json()
        print(json_bytes)
        deser = SearchCriteria.from_json(json_bytes)
        assert deser == search_criteria

        empty_criteria = SearchCriteria()
        json_bytes = empty_criteria.to_json()
        deser = SearchCriteria.from_json(json_bytes)
        assert deser == empty_criteria
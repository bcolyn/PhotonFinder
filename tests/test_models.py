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

    # --- helpers ---

    @staticmethod
    def _search_filenames(criteria: SearchCriteria) -> list[str]:
        """Run apply_search_criteria and return sorted file names."""
        query = (File.select(File, Image)
                 .join_from(File, Image, JOIN.LEFT_OUTER))
        query = Image.apply_search_criteria(query, criteria)
        return sorted(f.name for f in query)

    # --- image size filter tests ---

    def test_filter_by_width_min(self):
        criteria = SearchCriteria(width_min=4000)
        assert self._search_filenames(criteria) == ["file1.fits", "file3.fits"]

    def test_filter_by_width_max(self):
        criteria = SearchCriteria(width_max=2000)
        assert self._search_filenames(criteria) == ["file2.fits", "file4.fits"]

    def test_filter_by_height_min(self):
        criteria = SearchCriteria(height_min=3000)
        assert self._search_filenames(criteria) == ["file1.fits", "file3.fits"]

    def test_filter_by_height_max(self):
        criteria = SearchCriteria(height_max=1200)
        assert self._search_filenames(criteria) == ["file2.fits", "file4.fits"]

    def test_filter_by_width_and_height_combined(self):
        criteria = SearchCriteria(width_min=4000, height_max=4000)
        assert self._search_filenames(criteria) == ["file1.fits", "file3.fits"]

    # --- plate scale filter tests ---
    # coord_scale = ROUND((coord_radius*2*3600)/SQRT(w²+h²), 2)
    # file1/file3: (0.5*2*3600)/sqrt(4656²+3520²) ≈ 0.62  arcsec/px
    # file2/file4: (0.2*2*3600)/sqrt(1920²+1080²) ≈ 0.65  arcsec/px

    def test_filter_by_scale_min(self):
        # Only file2/file4 have scale ≥ 0.64 (≈0.65); file1/file3 have ≈0.62
        criteria = SearchCriteria(scale_min=0.64)
        assert self._search_filenames(criteria) == ["file2.fits", "file4.fits"]

    def test_filter_by_scale_max(self):
        # Only file1/file3 have scale ≤ 0.63 (≈0.62); file2/file4 have ≈0.65
        criteria = SearchCriteria(scale_max=0.63)
        assert self._search_filenames(criteria) == ["file1.fits", "file3.fits"]

    # --- image quality / stats filter tests ---

    def test_filter_by_star_count_min(self):
        criteria = SearchCriteria(star_count_min=150)
        # Only file3 has star_count=200; file1 has 100, file2/file4 have no stats
        assert self._search_filenames(criteria) == ["file3.fits"]

    def test_filter_by_star_count_max(self):
        criteria = SearchCriteria(star_count_max=150)
        # Only file1 has star_count=100 (≤150); file3 has 200, file2/file4 excluded by inner join
        assert self._search_filenames(criteria) == ["file1.fits"]

    def test_filter_by_fwhm_max(self):
        criteria = SearchCriteria(fwhm_max=3.0)
        # file1 has fwhm 2.5, file3 has 4.0 → only file1
        assert self._search_filenames(criteria) == ["file1.fits"]

    def test_filter_by_background_min(self):
        criteria = SearchCriteria(background_min=750.0)
        # file3 background 1000 qualifies; file1 background 500 does not
        assert self._search_filenames(criteria) == ["file3.fits"]

    def test_filter_by_elongation_max(self):
        criteria = SearchCriteria(elongation_max=1.5)
        # file1 elongation 1.2, file3 elongation 1.8 → only file1
        assert self._search_filenames(criteria) == ["file1.fits"]

    def test_no_stats_filter_does_not_exclude_images_without_stats(self):
        """Images without ImageStats records appear when no quality filter is active."""
        criteria = SearchCriteria()
        names = self._search_filenames(criteria)
        # All 4 files must appear
        assert "file2.fits" in names
        assert "file4.fits" in names
        assert len(names) == 4

    def test_stats_filter_excludes_images_without_stats(self):
        """Applying any quality filter inner-joins ImageStats, dropping un-analysed images."""
        criteria = SearchCriteria(star_count_min=1)
        names = self._search_filenames(criteria)
        # file2 and file4 have no stats; they must be absent
        assert "file2.fits" not in names
        assert "file4.fits" not in names

from unittest.mock import MagicMock, patch

from fs.info import Info
from fs.memoryfs import MemoryFS

from astrofilemanager.filesystem import ChangeList, Importer, fopen
from astrofilemanager.models import LibraryRoot, File


class TestChangeList:
    def test_init(self):
        """Test ChangeList initialization."""
        change_list = ChangeList()
        assert change_list.new_files == []
        assert change_list.removed_files == []
        assert change_list.changed_ids == []
        assert change_list.changed_files == []

    def test_apply_all(self, app_context, monkeypatch):
        """Test applying changes in ChangeList."""
        # Create a mock for bulk_create to avoid actual database operations
        bulk_create_mock = MagicMock()
        delete_by_id_mock = MagicMock()
        
        monkeypatch.setattr(File, 'bulk_create', bulk_create_mock)
        monkeypatch.setattr(File, 'delete_by_id', delete_by_id_mock)
        
        # Create a ChangeList with test data
        change_list = ChangeList()
        
        # Add mock files to the change list
        new_file = File(name="new.fits", path="path", size=100, mtime_millis=1000)
        removed_file = MagicMock()
        removed_file.rowid = 1
        changed_id = 2
        changed_file = File(name="changed.fits", path="path", size=200, mtime_millis=2000)
        
        change_list.new_files.append(new_file)
        change_list.removed_files.append(removed_file)
        change_list.changed_ids.append(changed_id)
        change_list.changed_files.append(changed_file)
        
        # Apply the changes
        change_list.apply_all()
        
        # Verify the correct methods were called
        bulk_create_mock.assert_any_call([new_file], batch_size=100)
        delete_by_id_mock.assert_any_call(removed_file.rowid)
        delete_by_id_mock.assert_any_call(changed_id)
        bulk_create_mock.assert_any_call([changed_file], batch_size=100)


class TestImporter:
    def test_marked_bad(self):
        """Test the marked_bad method."""
        # Create mock Info objects
        bad_file = MagicMock(spec=Info)
        bad_file.name = "bad_file.fits"
        
        normal_file = MagicMock(spec=Info)
        normal_file.name = "normal_file.fits"
        
        # Test the method
        assert Importer.marked_bad(bad_file) is True
        assert Importer.marked_bad(normal_file) is False

    def test_is_fits(self):
        """Test the is_fits method."""
        # Create mock Info objects for different file types
        fits_file = MagicMock(spec=Info)
        fits_file.name = "image.fits"
        
        fit_file = MagicMock(spec=Info)
        fit_file.name = "image.fit"
        
        compressed_fits = MagicMock(spec=Info)
        compressed_fits.name = "image.fits.xz"
        
        non_fits = MagicMock(spec=Info)
        non_fits.name = "image.jpg"
        
        # Test the method
        assert Importer.is_fits(fits_file) is True
        assert Importer.is_fits(fit_file) is True
        assert Importer.is_fits(compressed_fits) is True
        assert Importer.is_fits(non_fits) is False

    def test_is_compressed(self):
        """Test the is_compressed method."""
        # Create mock Info objects
        xz_file = MagicMock(spec=Info)
        xz_file.name = "file.xz"
        
        gz_file = MagicMock(spec=Info)
        gz_file.name = "file.gz"
        
        normal_file = MagicMock(spec=Info)
        normal_file.name = "file.txt"
        
        # Test the method
        assert Importer.is_compressed(xz_file) is True
        assert Importer.is_compressed(gz_file) is True
        assert Importer.is_compressed(normal_file) is False

    def test_file_filter(self):
        """Test the _file_filter method."""
        # Create mock Info objects
        good_fits = MagicMock(spec=Info)
        good_fits.name = "image.fits"
        
        bad_fits = MagicMock(spec=Info)
        bad_fits.name = "bad_image.fits"
        
        non_fits = MagicMock(spec=Info)
        non_fits.name = "image.jpg"
        
        # Test the method
        assert Importer._file_filter(good_fits) is True
        assert Importer._file_filter(bad_fits) is False
        assert Importer._file_filter(non_fits) is False

    def test_dir_filter(self):
        """Test the _dir_filter method."""
        # Create mock Info objects
        good_dir = MagicMock(spec=Info)
        good_dir.name = "good_dir"
        
        bad_dir = MagicMock(spec=Info)
        bad_dir.name = "bad_dir"
        
        # Test the method
        assert Importer._dir_filter(good_dir) is True
        assert Importer._dir_filter(bad_dir) is False

    @patch('astrofilemanager.filesystem.fs.open_fs')
    def test_import_files(self, mock_open_fs, app_context):
        """Test the import_files method."""
        # Create a memory filesystem for testing
        memory_fs = MemoryFS()
        mock_open_fs.return_value = memory_fs
        
        # Create a test library root
        lib_root = LibraryRoot.create(name="Test Library", path="/test/path")
        
        # Create an importer and mock import_files_from
        importer = Importer()
        importer.import_files_from = MagicMock(return_value=ChangeList())
        
        # Call import_files
        list(importer.import_files())
        
        # Verify import_files_from was called
        importer.import_files_from.assert_called_once()


@patch('astrofilemanager.filesystem.open')
@patch('astrofilemanager.filesystem.lzma.open')
@patch('astrofilemanager.filesystem.gzip.open')
@patch('astrofilemanager.filesystem.bz2.open')
def test_fopen(mock_bz2_open, mock_gzip_open, mock_lzma_open, mock_open, app_context):
    """Test the fopen function with different file types."""
    # Create a mock File object
    file = MagicMock(spec=File)
    file.full_filename.return_value = "/path/to/file"
    
    # Test regular file
    file.get_file_exts.return_value = ["fits"]
    fopen(file)
    mock_open.assert_called_with("/path/to/file", mode='rb')
    
    # Test xz compressed file
    file.get_file_exts.return_value = ["fits", "xz"]
    fopen(file)
    mock_lzma_open.assert_called_with("/path/to/file", mode='rb')
    
    # Test gz compressed file
    file.get_file_exts.return_value = ["fits", "gz"]
    fopen(file)
    mock_gzip_open.assert_called_with("/path/to/file", mode='rb')
    
    # Test bz2 compressed file
    file.get_file_exts.return_value = ["fits", "bz2"]
    fopen(file)
    mock_bz2_open.assert_called_with("/path/to/file", mode='rb')
import os
import tempfile
import pytest
from pathlib import Path

from astrofilemanager.models import LibraryRoot, File


class TestLibraryRoot:
    def test_is_valid_path(self):
        """Test the is_valid_path method with valid and invalid paths."""
        # Test with a valid directory
        with tempfile.TemporaryDirectory() as temp_dir:
            assert LibraryRoot.is_valid_path(temp_dir) is True
            
            # Test with a non-existent path
            non_existent_path = os.path.join(temp_dir, "non_existent")
            assert LibraryRoot.is_valid_path(non_existent_path) is False
            
            # Test with a file (not a directory)
            temp_file = os.path.join(temp_dir, "test_file.txt")
            with open(temp_file, 'w') as f:
                f.write("test")
            assert LibraryRoot.is_valid_path(temp_file) is False

    def test_library_root_crud(self, app_context):
        """Test creating, reading, updating, and deleting LibraryRoot objects."""
        # Create a temporary directory for testing
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a new library root
            lib_root = LibraryRoot.create(name="Test Library", path=temp_dir)
            
            # Verify it was created
            assert lib_root.id is not None
            assert lib_root.name == "Test Library"
            assert lib_root.path == temp_dir
            
            # Retrieve the library root
            retrieved_root = LibraryRoot.get(LibraryRoot.id == lib_root.id)
            assert retrieved_root.name == "Test Library"
            
            # Update the library root
            lib_root.name = "Updated Library"
            lib_root.save()
            
            # Verify the update
            updated_root = LibraryRoot.get(LibraryRoot.id == lib_root.id)
            assert updated_root.name == "Updated Library"
            
            # Delete the library root
            lib_root.delete_instance()
            
            # Verify it was deleted
            with pytest.raises(LibraryRoot.DoesNotExist):
                LibraryRoot.get(LibraryRoot.id == lib_root.id)


class TestFile:
    def test_get_file_exts(self):
        """Test the get_file_exts method with various file names."""
        # Create a mock File object
        file = File()
        
        # Test regular file extension
        file.name = "image.fits"
        assert file.get_file_exts() == ["fits"]
        
        # Test compressed file extensions
        file.name = "image.fits.xz"
        assert file.get_file_exts() == ["fits", "xz"]
        
        file.name = "image.fits.gz"
        assert file.get_file_exts() == ["fits", "gz"]
        
        file.name = "image.fits.bz2"
        assert file.get_file_exts() == ["fits", "bz2"]
        
        # Test file with no extension
        file.name = "README"
        assert file.get_file_exts() == []
        
        # Test hidden file
        file.name = ".gitignore"
        assert file.get_file_exts() == []

    def test_full_filename(self, app_context):
        """Test the full_filename method."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a library root
            lib_root = LibraryRoot.create(name="Test Library", path=temp_dir)
            
            # Create a file
            file = File.create(
                root=lib_root,
                path="subdir",
                name="test.fits",
                size=1024,
                mtime_millis=1620000000000
            )
            
            # Test full_filename
            expected_path = os.path.join(temp_dir, "subdir", "test.fits")
            assert file.full_filename() == expected_path
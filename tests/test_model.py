from astrofilemanager.filesystem import fopen
from astrofilemanager.models import *


class TestGetFileExts:
    def test_simple_ext(self):
        file = File(name="dummy.fits")
        assert file.get_file_exts() == ["fits"]

    def test_compressed_ext(self):
        file = File(name="dummy.fits.xz")
        assert file.get_file_exts() == ["fits", "xz"]

    def test_hidden_file_ext(self):
        file = File(name=".dummy.FITS")
        assert file.get_file_exts() == ["fits"]

    def test_hidden_file_ext_compressed(self):
        file = File(name=".DUMMY.FITS.GZ")
        assert file.get_file_exts() == ["fits", "gz"]

    def test_multiple_dots_in_name(self):
        file = File(name=".this.that.other.fits")
        assert file.get_file_exts() == ["fits"]

    def test_no_ext(self):
        file = File(name="testfile")
        assert file.get_file_exts() == []

    def test_no_ext_hidden(self):
        file = File(name=".testfile")
        assert file.get_file_exts() == []

    def test_from_db(self, database):
        root = LibraryRoot(name="dummy", path=r'C:\TEMP')
        file = File(root=root, path="subdir", name="image01.fits", size=0, mtime_millis=0)
        root.save()
        file.save()
        file = File.select().first()
        assert file.get_file_exts() == ["fits"]


class TestFOpen:
    FITS_START = bytes("SIMPLE  =                    T", 'ascii')

    @staticmethod
    def make_file(root, path, name):
        root = LibraryRoot(path=root)
        return File(name=name, path=path, root=root)

    @staticmethod
    def get_tests_dir():
        return os.path.abspath(__file__ + "/../..")

    @staticmethod
    def check_fits_contents(file):
        with fopen(file) as fd:
            data = fd.read(30)
            assert data == TestFOpen.FITS_START

    def test_fits_file(self):
        file = self.make_file(self.get_tests_dir(), "test-data",
                              "M57_2020-05-30T022249_30sec_HaOIII_COLD_-17C_frame19.fit")
        assert os.path.exists(file.full_filename())
        self.check_fits_contents(file)

    def test_xz_fits_file(self):
        file = self.make_file(self.get_tests_dir(), "test-data", "test_image.fits.xz")
        self.check_fits_contents(file)

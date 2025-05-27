import logging

from astrofilemanager.filesystem import Importer, fopen, read_fits_header
from astrofilemanager.models import LibraryRoot, File, Image

NUM_FILES = 6  # 8 images, 2 bad, 1 csv ignored

logger = logging.getLogger('peewee')
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


class TestImporter:
    @staticmethod
    def initial_import(filesystem, context):
        root = LibraryRoot(name="dummy", path=r'C:\TEMP')
        root.save()
        importer = Importer(context)
        change_list = importer.import_files_from(filesystem, root)
        change_list.apply_all()
        return root, importer

    def test_fixture(self, filesystem):
        for path, dirs, files in filesystem.walk("/", namespaces=['details']):
            print("[%s] %d subdirs %d files" % (path, len(dirs), len(files)))

    def test_initial_import(self, filesystem, database, app_context):
        self.initial_import(filesystem, app_context)
        assert File.select().count() == NUM_FILES

    def test_reimport(self, filesystem, database, app_context):
        (root, importer) = self.initial_import(filesystem, app_context)
        importer.import_files_from(filesystem, root).apply_all()
        assert File.select().count() == NUM_FILES

    def test_delete_file(self, filesystem, database, app_context):
        (root, importer) = self.initial_import(filesystem, app_context)

        filesystem.remove("image01.fits")
        importer.import_files_from(filesystem, root).apply_all()
        assert File.select().count() == NUM_FILES - 1

        filesystem.remove("test/2021-12-26/Darks/image06.fits")
        importer.import_files_from(filesystem, root).apply_all()
        assert File.select().count() == NUM_FILES - 2

    def test_delete_dirs(self, filesystem, database, app_context):
        (root, importer) = self.initial_import(filesystem, app_context)

        filesystem.removetree("test/2021-12-25")
        importer.import_files_from(filesystem, root).apply_all()
        assert File.select().count() == 3

    def test_changed_file(self, filesystem, database, app_context):
        (root, importer) = self.initial_import(filesystem, app_context)
        file = File.select().where(File.name == "image06.fits").get()
        Image.create(file=file)
        assert Image.select().count() == 1
        # if the file has been changed, we need to re-analyse it
        filesystem.touch("test/2021-12-26/Darks/image06.fits")
        change_list = importer.import_files_from(filesystem, root)
        assert len(change_list.changed_files) == 1
        assert len(change_list.new_files) == 0
        change_list.apply_all()
        assert File.select().count() == NUM_FILES
        assert Image.select().count() == 0


def test_read_fits_header(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    header_bytes = read_fits_header(file_path)
    assert len(header_bytes) % 2880 == 0, "FITS header should be multiple of 2880 bytes"
    assert header_bytes[:80].decode('ascii').startswith('SIMPLE  ='), "FITS header should start with SIMPLE"

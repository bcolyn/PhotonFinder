import logging
from typing import Iterable

from photonfinder.filesystem import Importer, read_fits_header, ChangeList, read_xisf_header, header_from_dict
from photonfinder.models import LibraryRoot, File, Image, FitsHeader
from photonfinder.filesystem import update_fits_header_cache, check_missing_header_cache
from photonfinder.fits_handlers import normalize_fits_header, NINAHandler, _normalize_image_type
from tests.utils import fix_embedded_header

NUM_FILES = 6  # 8 images, 2 bad, 1 csv ignored

logger = logging.getLogger('peewee')
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


class TestImporter:
    importer: Importer
    root: LibraryRoot

    def initial_import(self, context):
        change_lists = self.setup(context)
        for change_list in change_lists:
            change_list.apply_all()

    def setup(self, context) -> Iterable[ChangeList]:
        self.root = LibraryRoot(name="dummy", path=r'test://')
        self.root.save()
        self.importer = Importer(context)
        return self.importer.import_files()

    def test_fixture(self, filesystem):
        for path, dirs, files in filesystem.walk("/", namespaces=['details']):
            print("[%s] %d subdirs %d files" % (path, len(dirs), len(files)))

    def test_initial_import(self, filesystem, database, app_context):
        self.initial_import(app_context)
        assert File.select().count() == NUM_FILES

    def test_reimport(self, filesystem, database, app_context):
        self.initial_import(app_context)
        self.importer.import_files_from(filesystem, self.root).apply_all()
        assert File.select().count() == NUM_FILES

    def test_delete_file(self, filesystem, database, app_context):
        self.initial_import(app_context)

        filesystem.remove("image01.fits")
        self.importer.import_files_from(filesystem, self.root).apply_all()
        assert File.select().count() == NUM_FILES - 1

        filesystem.remove("test/2021-12-26/Darks/image06.fits")
        self.importer.import_files_from(filesystem, self.root).apply_all()
        assert File.select().count() == NUM_FILES - 2

    def test_delete_dirs(self, filesystem, database, app_context):
        self.initial_import(app_context)

        filesystem.removetree("test/2021-12-25")
        self.importer.import_files_from(filesystem, self.root).apply_all()
        assert File.select().count() == 3

    def test_changed_file(self, filesystem, database, app_context):
        self.initial_import(app_context)
        file = File.select().where(File.name == "image06.fits").get()
        Image.create(file=file)
        assert Image.select().count() == 1
        # if the file has been changed, we need to re-analyse it
        filesystem.touch("test/2021-12-26/Darks/image06.fits")
        change_list = self.importer.import_files_from(filesystem, self.root)
        assert len(change_list.changed_files) == 1
        assert len(change_list.new_files) == 0
        change_list.apply_all()
        assert File.select().count() == NUM_FILES
        assert Image.select().count() == 0

    def test_update_fits_header_cache(self, filesystem, database, app_context, mocker):
        from .sample_headers import header_apt
        mocker.patch('photonfinder.filesystem.read_fits_header',
                     return_value=fix_embedded_header(header_apt))
        change_lists = self.setup(app_context)
        for change_list in change_lists:
            change_list.apply_all()
            update_fits_header_cache(change_list, app_context.status_reporter)
        check_missing_header_cache(app_context.status_reporter)

        assert Image.select().count() == NUM_FILES
        assert FitsHeader.select().bind(database).count() == NUM_FILES


def test_read_fits_header(global_test_data_dir):
    file_path = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
    header_bytes = read_fits_header(file_path)
    assert len(header_bytes) % 2880 == 0, "FITS header should be multiple of 2880 bytes"
    assert header_bytes[:80].decode('ascii').startswith('SIMPLE  ='), "FITS header should start with SIMPLE"


def test_read_read_xisf_header(global_test_data_dir):
    file_path = global_test_data_dir / "2021-05-31_00-10-25__18.30_1.00s_0000.xisf"
    assert file_path.exists()
    header_bytes, header_dict = read_xisf_header(file_path)
    assert header_dict is not None
    assert header_bytes is not None
    header = header_from_dict(header_dict)
    assert NINAHandler().can_handle(header)
    image = normalize_fits_header(File(), header)
    assert image is not None

    file_path = global_test_data_dir / "masterFlat_BIN-1_5496x3672_FILTER-LP_CFA_SESS-2020-04-11.xisf"
    header_bytes, header_dict = read_xisf_header(file_path)
    assert header_dict['INSTRUME'][0]['value'] == 'ZWO ASI183MC Pro'

    file_path = global_test_data_dir / "masterLight_BIN-1_1080x1920_EXPOSURE-10.00s_FILTER-LP_RGB.xisf"
    header_bytes, header_dict = read_xisf_header(file_path)
    header = header_from_dict(header_dict)
    image = normalize_fits_header(File(), header)
    assert image is not None
    assert image.camera == "Seestar S50"
    assert image.object_name == 'NGC 2174'

    file_path = global_test_data_dir / "linear.xisf"
    header_bytes, header_dict = read_xisf_header(file_path)
    header = header_from_dict(header_dict)
    image = normalize_fits_header(File(), header)
    assert image is not None
    assert image.camera == "ZWO ASI183MC Pro"
    assert image.object_name == 'NGC 3319'

def test_type_normalization():
    assert _normalize_image_type("Dark Frame") == "DARK"
    assert _normalize_image_type("Light") == "LIGHT"
    assert _normalize_image_type("MasterLight") == "MASTER LIGHT"
    assert _normalize_image_type("Master Light Frame") == "MASTER LIGHT"
    assert _normalize_image_type("FLAT") == "FLAT"
import logging

from astrofilemanager.filesystem import Importer
from astrofilemanager.models import LibraryRoot, File

NUM_FILES = 6  # 8 images, 2 bad, 1 csv ignored

logger = logging.getLogger('peewee')
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


def initial_import(filesystem):
    root = LibraryRoot(name="dummy", path=r'C:\TEMP')
    root.save()
    importer = Importer()
    change_list = importer.import_files_from(filesystem, root)
    change_list.apply_all()
    return root, importer


def test_fixture(filesystem):
    for path, dirs, files in filesystem.walk("/", namespaces=['details']):
        print("[%s] %d subdirs %d files" % (path, len(dirs), len(files)))


def test_initial_import(filesystem, database):
    initial_import(filesystem)
    assert File.select().count() == NUM_FILES


def test_reimport(filesystem, database):
    (root, importer) = initial_import(filesystem)
    importer.import_files_from(filesystem, root).apply_all()
    assert File.select().count() == NUM_FILES


def test_delete_file(filesystem, database):
    (root, importer) = initial_import(filesystem)

    filesystem.remove("image01.fits")
    importer.import_files_from(filesystem, root).apply_all()
    assert File.select().count() == NUM_FILES - 1

    filesystem.remove("test/2021-12-26/Darks/image06.fits")
    importer.import_files_from(filesystem, root).apply_all()
    assert File.select().count() == NUM_FILES - 2


def test_delete_dirs(filesystem, database):
    (root, importer) = initial_import(filesystem)

    filesystem.removetree("test/2021-12-25")
    importer.import_files_from(filesystem, root).apply_all()
    assert File.select().count() == 3


def test_changed_file(filesystem, database):
    (root, importer) = initial_import(filesystem)
    # file = File.select().where(File.name == "image06.fits").get()
    # Image.create(file=file)
    # assert Image.select().count() == 1
    # # if the file has been changed, we need to re-analyse it
    # filesystem.touch("test/2021-12-26/Darks/image06.fits")
    # importer.import_files_from(filesystem, root).apply_all()
    # assert File.select().count() == NUM_FILES
    # assert Image.select().count() == 0

#TODO: detect moved files, detect compressed files
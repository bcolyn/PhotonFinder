import logging

from playhouse.reflection import print_table_sql

from astrofilemanager.models import File, LibraryRoot, Image

logger = logging.getLogger('peewee')
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


def test_print_sql(database):
    print_table_sql(LibraryRoot)


def test_create_root(database):
    root = LibraryRoot()
    root.name = "dummy"
    root.path = r'C:\TEMP'
    root.save()


def test_deletes_cascade(database):
    root = LibraryRoot(name="dummy", path=r'C:\TEMP')
    file = File(root=root, path="subdir", name="image01.fits", size=0, mtime_millis=0)
    image = Image(file=file)
    root.save()
    file.save()
    image.save()

    File.delete_by_id(file.rowid)
    for table in (File, Image):
        assert table.select().count() == 0


def test_model_str():
    root = LibraryRoot(name="dummy", path=r'C:\TEMP')
    assert str(root) == "LibraryRoot(name=dummy, path=C:\\TEMP)"


def test_model_eq():
    root = LibraryRoot(name="dummy", path=r'C:\TEMP')
    root2 = LibraryRoot(name="dummy", path=r'C:\TEMP')
    assert root == root2

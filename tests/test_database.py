import logging

from playhouse.reflection import print_table_sql

from astrofilemanager.models import File, LibraryRoot as Root

logger = logging.getLogger('peewee')
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


def test_print_sql(database):
    print_table_sql(Root)


def test_create_root(database):
    root = Root()
    root.name = "dummy"
    root.path = r'C:\TEMP'
    root.save()


def test_deletes_cascade(database):
    root = Root(name="dummy", path=r'C:\TEMP')
    file = File(root=root, path="subdir", name="image01.fits", size=0, mtime_millis=0)
    # image = Image(file=file)
    # meta1 = ImageMeta(image=image, key="key1", value="value1")
    # meta2 = ImageMeta(image=image, key="key2", value="value2")
    # for obj in (root, file, image, meta1, meta2):
    #     obj.save()
    #
    # assert ImageMeta.select().count() == 2
    # File.delete_by_id(file.rowid)
    # for table in (File, Image, ImageMeta):
    #     assert table.select().count() == 0


def test_model_str():
    root = Root(name="dummy", path=r'C:\TEMP')
    assert str(root) == "LibraryRoot(name=dummy, path=C:\\TEMP)"


def test_model_eq():
    root = Root(name="dummy", path=r'C:\TEMP')
    root2 = Root(name="dummy", path=r'C:\TEMP')
    assert root == root2

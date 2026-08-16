import logging

from playhouse.reflection import print_table_sql

from photonfinder.models import File, LibraryRoot, Image

logger = logging.getLogger('peewee')
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


def test_print_sql(database):
    print_table_sql(LibraryRoot)


def test_create_root(database):
    root = LibraryRoot()
    root.name = "dummy"
    root.path = r'C:/TEMP/'
    root.save()


def test_deletes_cascade(database):
    root = LibraryRoot(name="dummy", path=r'C:/TEMP/')
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


def test_library_root_description_added_to_existing_database(tmp_path):
    """A database created before `description` existed gains the column on open."""
    from peewee import SqliteDatabase

    from photonfinder.core import ApplicationContext
    from photonfinder.models import CORE_MODELS
    from tests.conftest import McpSettings

    db_path = tmp_path / "old.db"
    old = SqliteDatabase(str(db_path))
    old.connect()
    # The pre-migration schema: no `description` column.
    old.execute_sql('CREATE TABLE libraryroot ('
                    'id INTEGER NOT NULL PRIMARY KEY, '
                    'name VARCHAR(255) NOT NULL, path VARCHAR(255) NOT NULL)')
    old.execute_sql("INSERT INTO libraryroot (name, path) VALUES ('Main', '/data/')")
    old.close()

    with ApplicationContext(str(db_path), McpSettings()) as ctx:
        with ctx.database.bind_ctx(CORE_MODELS):
            root = LibraryRoot.get(LibraryRoot.name == 'Main')
            assert root.description is None
            root.description = "Main imaging archive"
            root.save()
            assert LibraryRoot.get_by_id(root.rowid).description == "Main imaging archive"

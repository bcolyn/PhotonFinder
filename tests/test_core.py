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


def _raw_header(*cards: str) -> bytes:
    return "".join(c.ljust(80) for c in (*cards, "END")).encode("ascii")


def _header_value(context, raw, key):
    from photonfinder.core import compress
    blob = compress(raw) if raw is not None else None
    return context.database.execute_sql("SELECT decompress_header_value(?, ?)", (blob, key)).fetchone()[0]


class TestHeaderUdfs:
    def test_null_header_gives_null(self, context):
        assert _header_value(context, None, "GAIN") is None
        assert context.database.execute_sql("SELECT decompress(NULL)").fetchone()[0] is None

    def test_card_with_tab_falls_back_to_repairing_parser(self, context):
        assert _header_value(context, _raw_header("OBJECT  = 'M\t31'"), "OBJECT") == "M 31"

    def test_malformed_value_is_repaired_instead_of_failing(self, context):
        assert _header_value(context, _raw_header("EXPTIME = 1.0.0", "GAIN    = 100"), "EXPTIME") == "1.0.0"

    def test_header_search_with_files_lacking_headers(self, sample):
        from photonfinder.models import SearchCriteria, search_files
        _, objs = sample  # the DARK has no FitsHeader row

        rows, total, _ = search_files(SearchCriteria(header_text="GAIN=100"))
        assert [r.rowid for r in rows] == [objs["light"].rowid]

        rows, total, _ = search_files(SearchCriteria(header_text="M31"))
        assert [r.rowid for r in rows] == [objs["light"].rowid]

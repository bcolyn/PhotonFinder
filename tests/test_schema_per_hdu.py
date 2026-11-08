"""Schema version 2: an image is (file, hdu). Migration of a version 1 library."""
import sqlite3

import peewee
import pytest

from photonfinder.core import ApplicationContext, StatusReporter
from photonfinder.models import File, FitsHeader, Image, ImageStats, LibraryRoot

from .conftest import McpSettings

# The three tables as a version 1 library has them (taken from a real library).
_V1_TABLES = {
    "image": 'CREATE TABLE "image" ("rowid" INTEGER NOT NULL PRIMARY KEY, "file_id" INTEGER NOT NULL, '
             '"image_type" VARCHAR(255), "camera" VARCHAR(255), "filter" VARCHAR(255), "exposure" REAL, '
             '"gain" INTEGER, "offset" INTEGER, "binning" INTEGER, "set_temp" REAL, "telescope" VARCHAR(255), '
             '"object_name" VARCHAR(255), "date_obs" DATETIME, "coord_ra" REAL, "coord_dec" REAL, '
             '"coord_pix256" INTEGER, "coord_radius" REAL, "width" INTEGER, "height" INTEGER, '
             '"coord_scale" REAL GENERATED ALWAYS AS (ROUND((coord_radius * 2.0 * 3600.0) / '
             'SQRT(CAST(width AS REAL) * width + CAST(height AS REAL) * height), 2)) VIRTUAL, '
             'FOREIGN KEY ("file_id") REFERENCES "file" ("rowid") ON DELETE CASCADE)',
    "fitsheader": 'CREATE TABLE "fitsheader" ("rowid" INTEGER NOT NULL PRIMARY KEY, "file_id" INTEGER NOT NULL, '
                  '"header" BLOB NOT NULL, FOREIGN KEY ("file_id") REFERENCES "file" ("rowid") ON DELETE CASCADE)',
    "imagestats": 'CREATE TABLE "imagestats" ("rowid" INTEGER NOT NULL PRIMARY KEY, "file_id" INTEGER NOT NULL, '
                  '"background_median" REAL, "background_rms" REAL, "star_count" INTEGER, "fwhm_median" REAL, '
                  '"elongation_median" REAL, diagnosis TEXT, '
                  'FOREIGN KEY ("file_id") REFERENCES "file" ("rowid") ON DELETE CASCADE)',
}
_V1_INDEXES = [
    'CREATE UNIQUE INDEX "image_file_id" ON "image" ("file_id")',
    'CREATE INDEX "image_filter" ON "image" ("filter")',
    'CREATE INDEX idx_image_camera_scale ON image(camera, coord_scale) '
    'WHERE camera IS NOT NULL AND coord_scale IS NOT NULL',
    'CREATE UNIQUE INDEX "fitsheader_file_id" ON "fitsheader" ("file_id")',
    'CREATE UNIQUE INDEX "imagestats_file_id" ON "imagestats" ("file_id")',
]


def _open(path) -> ApplicationContext:
    context = ApplicationContext(str(path), McpSettings())
    context.set_status_reporter(StatusReporter())
    return context


@pytest.fixture
def v1_library(tmp_path):
    """A version 1 library with two files, each with an Image and FitsHeader row; one has stats."""
    path = tmp_path / "v1.db"
    with _open(path):
        pass  # creates every other table as the current version does
    db = sqlite3.connect(path)
    for table, ddl in _V1_TABLES.items():
        db.execute(f'DROP TABLE "{table}"')
        db.execute(ddl)
    for ddl in _V1_INDEXES:
        db.execute(ddl)
    db.execute("INSERT INTO libraryroot (rowid, name, path) VALUES (1, 'lib', 'C:/lib')")
    for rowid in (1, 2):
        db.execute("INSERT INTO file (rowid, root_id, path, name, size, mtime_millis) VALUES (?, 1, 'p', ?, 1, 1)",
                   (rowid, f"f{rowid}.fits"))
        db.execute("INSERT INTO image (rowid, file_id, filter, coord_radius, width, height) "
                   "VALUES (?, ?, 'HO', 0.5, 4000, 3000)", (10 + rowid, rowid))
        db.execute("INSERT INTO fitsheader (rowid, file_id, header) VALUES (?, ?, ?)", (20 + rowid, rowid, b"h"))
    db.execute("INSERT INTO imagestats (file_id, fwhm_median, diagnosis) VALUES (1, 2.5, 'OK')")
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    return path


def _schema(path) -> dict:
    db = sqlite3.connect(path)
    try:
        return {
            "version": db.execute("PRAGMA user_version").fetchone()[0],
            "image": db.execute("SELECT rowid, file_id, hdu, extname, filter, coord_scale FROM image "
                                "ORDER BY rowid").fetchall(),
            "fitsheader": db.execute("SELECT rowid, file_id, hdu, header FROM fitsheader ORDER BY rowid").fetchall(),
            "imagestats": db.execute("SELECT file_id, hdu, fwhm_median, diagnosis FROM imagestats").fetchall(),
            "indexes": sorted(r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL "
                "AND tbl_name IN ('image', 'fitsheader', 'imagestats')")),
            "leftovers": db.execute("SELECT name FROM sqlite_master WHERE name LIKE '%before_hdu%'").fetchall(),
        }
    finally:
        db.close()


class TestMigration:
    def test_rows_become_hdu_0(self, v1_library):
        with _open(v1_library):
            pass

        schema = _schema(v1_library)

        assert schema["version"] == 2
        assert schema["image"] == [(11, 1, 0, None, "HO", pytest.approx(0.72)), (12, 2, 0, None, "HO", pytest.approx(0.72))]
        assert schema["fitsheader"] == [(21, 1, 0, b"h"), (22, 2, 0, b"h")]
        assert schema["imagestats"] == [(1, 0, 2.5, "OK")]
        assert "image_file_id" not in schema["indexes"]
        assert {"image_file_id_hdu", "fitsheader_file_id_hdu", "imagestats_file_id_hdu",
                "image_filter", "idx_image_camera_scale"} <= set(schema["indexes"])
        assert schema["leftovers"] == []

    def test_second_open_changes_nothing(self, v1_library):
        with _open(v1_library):
            pass
        first = _schema(v1_library)

        with _open(v1_library):
            pass

        assert _schema(v1_library) == first

    def test_a_file_can_have_several_images_but_not_the_same_hdu_twice(self, v1_library):
        with _open(v1_library):
            file = File.get_by_id(1)
            Image.create(file=file, hdu=1, extname="SOO", filter="SO")
            FitsHeader.create(file=file, hdu=1, header=b"x")
            ImageStats.create(file=file, hdu=1, fwhm_median=3.0)

            assert Image.select().where(Image.file == file).count() == 2
            with pytest.raises(peewee.IntegrityError):
                Image.create(file=file, hdu=1, filter="again")

    def test_a_failed_migration_leaves_version_1(self, v1_library, mocker):
        mocker.patch.object(FitsHeader, "create_table", side_effect=RuntimeError("disk full"))

        with pytest.raises(RuntimeError):
            with _open(v1_library):
                pass

        mocker.stopall()
        db = sqlite3.connect(v1_library)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "hdu" not in [r[1] for r in db.execute("PRAGMA table_info(image)")]
        db.close()

    def test_new_library_is_created_at_version_2(self, tmp_path):
        path = tmp_path / "new.db"
        with _open(path):
            root = LibraryRoot.create(name="r", path="C:/r")
            file = File.create(root=root, path="p", name="a.fits", size=1, mtime_millis=1)
            Image.create(file=file, filter="HO")

        schema = _schema(path)
        assert schema["version"] == 2
        assert schema["image"][0][2] == 0


class TestPerImageJoins:
    """A file's stats and header rows join to the Image row of the same hdu, not to every
    image of the file."""

    @pytest.fixture
    def two_layer_file(self, tmp_path):
        context = _open(tmp_path / "lib.db")
        with context:
            root = LibraryRoot.create(name="r", path="C:/r")
            file = File.create(root=root, path="p", name="draco.fits", size=1, mtime_millis=1)
            Image.create(file=file, hdu=0, filter="HO", image_type="LIGHT")
            Image.create(file=file, hdu=1, extname="SOO", filter="SO", image_type="LIGHT")
            FitsHeader.create(file=file, hdu=0, header=b"HO header")
            FitsHeader.create(file=file, hdu=1, header=b"SO header")
            ImageStats.create(file=file, hdu=0, fwhm_median=2.0)
            ImageStats.create(file=file, hdu=1, fwhm_median=3.0)
            yield context

    def test_search_rows_pair_each_image_with_its_own_stats(self, two_layer_file):
        from photonfinder import library_query
        from photonfinder.models import SearchCriteria

        rows = library_query.search_files(SearchCriteria()).rows

        assert sorted((row.image.filter, library_query.search_row_stat(row, 'stats_fwhm_median')) for row in rows) == [("HO", 2.0), ("SO", 3.0)]

    def test_matching_files_pairs_each_image_with_its_own_header(self, two_layer_file):
        from photonfinder import library_query
        from photonfinder.models import SearchCriteria

        rows = library_query.matching_files(SearchCriteria(), with_header=True)

        assert sorted((row.image.filter, row.fitsheader.header) for row in rows) == [("HO", b"HO header"),
                                                                                     ("SO", b"SO header")]


def test_reindex_keeps_one_image_per_header_row(tmp_path):
    """Rebuild index writes each header row's Image under that row's hdu (save_indexed replaces
    by (file, hdu)), so a second layer doesn't overwrite the first."""
    from astropy.io.fits import Header
    from photonfinder.core import compress
    from photonfinder.filesystem import reindex_library
    from .conftest import DynamicSettings

    def header(filter_name):
        h = Header()
        h.update({"SIMPLE": True, "BITPIX": 16, "NAXIS": 2, "NAXIS1": 10, "NAXIS2": 8, "IMAGETYP": "LIGHT",
                  "FILTER": filter_name, "EXPTIME": 300.0, "DATE-OBS": "2026-11-02T20:00:00"})
        return compress(h.tostring().encode("ascii"))

    with _open(tmp_path / "lib.db"):
        root = LibraryRoot.create(name="r", path="C:/r")
        file = File.create(root=root, path="p", name="draco.fits", size=1, mtime_millis=1)
        FitsHeader.create(file=file, hdu=0, header=header("HO"))
        FitsHeader.create(file=file, hdu=1, header=header("SO"))

        reindex_library(StatusReporter(), DynamicSettings())

        assert sorted(Image.select(Image.hdu, Image.filter).where(Image.file == file).tuples()) == [(0, "HO"),
                                                                                                   (1, "SO")]

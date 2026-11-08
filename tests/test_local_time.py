"""Image.local_time: computed at import from the real M106 frame's header (and copies of it with
cards taken out), filled in the background for older rows, recomputed when obs_timezone
changes; the session night derived from it in SQL and Python by one rule."""
import datetime
import sqlite3

import numpy as np
import pytest
from astropy.io import fits

from photonfinder import local_time
from photonfinder.core import ApplicationContext, Settings, StatusReporter
from photonfinder.filesystem import check_missing_header_cache, fopen
from photonfinder.local_time import FALLBACK, LOC, SITE, session_night, update_library
from photonfinder.models import File, FitsHeader, Image, LibraryProperty, LibraryRoot, session_night_sql

from .conftest import McpSettings

M106 = "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
# Its header: DATE-OBS 2020-03-17T01:43:57, DATE-LOC 2020-03-17T02:43:57, SITELAT 51.05 (Belgium)
M106_LOCAL = datetime.datetime(2020, 3, 17, 2, 43, 57)
SITE_CARDS = ("SITELAT", "SITELONG", "OBSGEO-B", "OBSGEO-L")


@pytest.fixture
def m106_header(global_test_data_dir) -> fits.Header:
    with fopen(str(global_test_data_dir / M106)) as f:
        with fits.open(f) as hdul:
            return hdul[0].header.copy()


def _write(path, header: fits.Header, remove=(), **cards):
    header = header.copy()
    for key in remove:
        header.remove(key, ignore_missing=True, remove_all=True)
    for key, value in cards.items():
        header[key.replace("_", "-")] = value
    for key in ("NAXIS1", "NAXIS2", "BITPIX", "BZERO", "BSCALE"):
        header.remove(key, ignore_missing=True)
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.int16), header=header).writeto(path)


def _import(root, *names) -> list[Image]:
    for name in names:
        File.create(root=root, path="", name=name, size=1, mtime_millis=1)
    check_missing_header_cache(None, root._context_settings)
    return list(Image.select().join(File).order_by(File.name, Image.hdu))


@pytest.fixture
def library(tmp_path):
    def make(zone="Europe/Brussels"):
        settings = McpSettings()
        settings.set_obs_timezone(zone)
        ctx = ApplicationContext(tmp_path / "library.db", settings)
        ctx.set_status_reporter(StatusReporter())
        return ctx
    return make


@pytest.fixture
def root(library, tmp_path):
    ctx = library()
    with ctx:
        root = LibraryRoot.create(name="r", path=str(tmp_path) + "/")
        root._context_settings = ctx.settings
        yield root


class TestImport:

    def test_date_loc_wins(self, root, m106_header, tmp_path):
        _write(tmp_path / "a.fits", m106_header)

        [image] = _import(root, "a.fits")

        assert (image.local_time, image.local_time_source) == (M106_LOCAL, LOC)
        assert session_night(image.local_time) == datetime.date(2020, 3, 16)

    def test_without_date_loc_the_site_zone(self, root, m106_header, tmp_path):
        _write(tmp_path / "a.fits", m106_header, remove=["DATE-LOC"])

        [image] = _import(root, "a.fits")

        assert (image.local_time, image.local_time_source) == (M106_LOCAL, SITE)  # Brussels, CET

    def test_without_either_the_obs_timezone(self, root, m106_header, tmp_path):
        root._context_settings.set_obs_timezone("America/New_York")
        _write(tmp_path / "a.fits", m106_header, remove=["DATE-LOC", *SITE_CARDS])

        [image] = _import(root, "a.fits")

        # EDT (UTC-4) since 8 March 2020
        assert (image.local_time, image.local_time_source) == (datetime.datetime(2020, 3, 16, 21, 43, 57), FALLBACK)

    def test_a_pre_dawn_frame_west_of_utc_is_on_the_previous_night(self, root, m106_header, tmp_path):
        _write(tmp_path / "a.fits", m106_header, remove=["DATE-LOC", *SITE_CARDS],
               SITELAT=37.0, SITELONG=-120.0, DATE_OBS="2020-03-17T12:30:00")

        [image] = _import(root, "a.fits")

        assert (image.local_time, image.local_time_source) == (datetime.datetime(2020, 3, 17, 5, 30), SITE)  # PDT
        assert session_night(image.local_time) == datetime.date(2020, 3, 16)

    def test_each_frame_gets_the_offset_of_its_own_date(self, root, m106_header, tmp_path):
        _write(tmp_path / "winter.fits", m106_header, remove=["DATE-LOC"])
        _write(tmp_path / "summer.fits", m106_header, remove=["DATE-LOC"], DATE_OBS="2020-07-17T01:43:57")

        summer, winter = _import(root, "summer.fits", "winter.fits")

        assert summer.local_time == datetime.datetime(2020, 7, 17, 3, 43, 57)  # CEST, UTC+2
        assert winter.local_time == datetime.datetime(2020, 3, 17, 2, 43, 57)  # CET, UTC+1

    def test_an_extension_is_as_far_from_date_loc_as_from_the_primary(self, root, m106_header, tmp_path):
        ext = fits.ImageHDU(np.zeros((4, 4), dtype=np.int16), name="LATER")
        ext.header["DATE-OBS"] = "2020-03-17T02:43:57"  # an hour after the primary
        primary = m106_header.copy()
        for key in ("NAXIS1", "NAXIS2", "BITPIX", "BZERO", "BSCALE"):
            primary.remove(key, ignore_missing=True)
        primary["EXTEND"] = True
        fits.HDUList([fits.PrimaryHDU(np.zeros((4, 4), dtype=np.int16), header=primary), ext]).writeto(
            tmp_path / "a.fits")

        first, later = _import(root, "a.fits")

        assert (first.local_time, first.local_time_source) == (M106_LOCAL, LOC)
        assert (later.local_time, later.local_time_source) == (M106_LOCAL + datetime.timedelta(hours=1), LOC)

        # the background fill, reading the primary header, gives the same
        Image.update(local_time=None, local_time_source=None).execute()
        assert update_library(local_time.obs_zone(root._context_settings))
        assert [(i.local_time, i.local_time_source) for i in Image.select().order_by(Image.hdu)] == [
            (first.local_time, LOC), (later.local_time, LOC)]


class TestBackfill:

    def _image(self, root, name, date_obs, header: bytes | None = None):
        file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
        if header is not None:
            FitsHeader.create(file=file, header=header)
        return Image.create(file=file, date_obs=date_obs)

    def test_fills_what_is_missing_and_leaves_rows_without_date_obs(self, root):
        from photonfinder.core import compress
        loc = self._image(root, "loc.fits", datetime.datetime(2020, 3, 17, 1, 0), compress(
            fits.Header([("SIMPLE", True), ("DATE-LOC", "2020-03-17T05:00:00")]).tostring().encode()))
        unreadable = self._image(root, "bad.fits", datetime.datetime(2020, 3, 17, 1, 0), b"not compressed")
        no_header = self._image(root, "none.fits", datetime.datetime(2020, 3, 17, 1, 0))
        no_date = self._image(root, "nodate.fits", None)

        assert update_library(local_time.obs_zone(root._context_settings))

        def stored(image):
            image = Image.get_by_id(image.rowid)
            return image.local_time, image.local_time_source
        assert stored(loc) == (datetime.datetime(2020, 3, 17, 5, 0), LOC)
        assert stored(unreadable) == (datetime.datetime(2020, 3, 17, 2, 0), FALLBACK)  # Brussels
        assert stored(no_header) == (datetime.datetime(2020, 3, 17, 2, 0), FALLBACK)
        assert stored(no_date) == (None, None)
        assert local_time.missing_count() == 0
        assert not local_time.needs_update(local_time.obs_zone(root._context_settings))

    def test_cancelling_leaves_the_rest_for_next_time(self, root, mocker):
        mocker.patch.object(local_time, "_BATCH_SIZE", 2)
        for i in range(5):
            self._image(root, f"{i}.fits", datetime.datetime(2020, 3, 17, 1, i))
        zone = local_time.obs_zone(root._context_settings)
        LibraryProperty.create(name="local_time_zone", value=zone.key)
        batches = iter([False, True])

        assert not update_library(zone, cancelled=lambda: next(batches))
        assert local_time.missing_count() == 3

        assert update_library(zone)
        assert local_time.missing_count() == 0

    def test_a_new_zone_recomputes_only_fallback_rows(self, root):
        obs = datetime.datetime(2020, 3, 17, 1, 0)
        fallback = Image.create(file=File.create(root=root, path="", name="f", size=1, mtime_millis=1),
                                date_obs=obs, local_time=obs, local_time_source=FALLBACK)
        site = Image.create(file=File.create(root=root, path="", name="s", size=1, mtime_millis=1),
                            date_obs=obs, local_time=obs, local_time_source=SITE)
        LibraryProperty.create(name="local_time_zone", value="UTC")

        assert update_library(local_time.ZoneInfo("Asia/Tokyo"))

        assert Image.get_by_id(fallback.rowid).local_time == datetime.datetime(2020, 3, 17, 10, 0)
        assert Image.get_by_id(site.rowid).local_time == obs
        assert local_time.recorded_zone() == "Asia/Tokyo"


class TestSessionNight:

    @pytest.mark.parametrize("local, night", [
        (datetime.datetime(2026, 3, 6, 1, 30), datetime.date(2026, 3, 5)),
        (datetime.datetime(2026, 3, 5, 21, 10, 0, 250000), datetime.date(2026, 3, 5)),
        (datetime.datetime(2026, 3, 6, 11, 59, 59, 999999), datetime.date(2026, 3, 5)),
        (datetime.datetime(2026, 3, 6, 12, 0), datetime.date(2026, 3, 6)),
    ])
    def test_sql_and_python_agree(self, root, local, night):
        Image.create(file=File.create(root=root, path="", name="a", size=1, mtime_millis=1), local_time=local)

        [(sql_night,)] = Image.select(session_night_sql()).tuples()

        assert session_night(local) == night
        assert sql_night == night

    def test_a_night_filter_uses_the_index(self, root):
        query = Image.select(Image.rowid).where(session_night_sql() == "2026-03-05")
        sql, params = query.sql()

        plan = Image._meta.database.execute_sql(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()

        assert "image_session_night" in str(plan)


class TestSchema:

    def test_a_version_2_library_gets_the_columns(self, library):
        ctx = library()
        with ctx:
            pass
        db = sqlite3.connect(ctx.database_path)
        db.execute("DROP INDEX image_session_night")
        db.execute("ALTER TABLE image DROP COLUMN local_time")
        db.execute("ALTER TABLE image DROP COLUMN local_time_source")
        db.execute("PRAGMA user_version = 2")
        db.commit()
        db.close()

        with library():
            pass

        db = sqlite3.connect(ctx.database_path)
        columns = [row[1] for row in db.execute("PRAGMA table_info(image)")]
        indexes = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")]
        assert {"local_time", "local_time_source"} <= set(columns)
        assert "image_session_night" in indexes
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        db.close()


class TestObsTimezoneSetting:

    def test_an_empty_setting_is_filled_with_the_system_zone(self, tmp_path, mocker):
        mocker.patch("tzlocal.get_localzone_name", return_value="Pacific/Auckland")

        assert Settings(path=tmp_path / "s.ini").get_obs_timezone() == "Pacific/Auckland"
        mocker.patch("tzlocal.get_localzone_name", return_value="Europe/Brussels")
        assert Settings(path=tmp_path / "s.ini").get_obs_timezone() == "Pacific/Auckland"  # kept from then on

    def test_utc_when_the_system_zone_is_unknown(self, tmp_path, mocker):
        mocker.patch("tzlocal.get_localzone_name", side_effect=OSError("no zone"))

        assert Settings(path=tmp_path / "s.ini").get_obs_timezone() == "UTC"

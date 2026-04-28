import datetime
from datetime import timedelta

from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from photonfinder.calibration import (
    CalibrationMatcher, SessionKey, CalibrationCandidate, session_date_for,
    _dms_to_deg, _session_info_from_header,
)
from photonfinder.models import File, Image, LibraryRoot, SearchCriteria
from .conftest import DynamicSettings


class TestDmsToDeg:
    def test_decimal(self):
        assert _dms_to_deg("51.05") == pytest.approx(51.05)

    def test_negative_decimal(self):
        assert _dms_to_deg("-34.5") == pytest.approx(-34.5)

    def test_space_separated_dms(self):
        assert _dms_to_deg("51 3 0.000") == pytest.approx(51.05)

    def test_negative_space_separated_dms(self):
        assert _dms_to_deg("-34 1 0.000") == pytest.approx(-34 - 1/60)

    def test_southern_prefix(self):
        assert _dms_to_deg("S34 1 0.000") == pytest.approx(-(34 + 1/60))

    def test_northern_prefix(self):
        assert _dms_to_deg("N51 3 0.000") == pytest.approx(51 + 3/60)

    def test_western_prefix(self):
        assert _dms_to_deg("W76 0 0.000") == pytest.approx(-76.0)

    def test_colon_separated_dms(self):
        assert _dms_to_deg("51:03:00.0") == pytest.approx(51.05)

    def test_dms_markers(self):
        assert _dms_to_deg("51d3m0s") == pytest.approx(51.05)

    def test_none_returns_none(self):
        assert _dms_to_deg(None) is None

    def test_empty_string_returns_none(self):
        assert _dms_to_deg("") is None

    def test_garbage_returns_none(self):
        assert _dms_to_deg("not a coordinate") is None


class TestSessionInfoFromHeader:
    def _make_header(self, **kwargs):
        from astropy.io.fits import Header
        hdr = Header()
        for k, v in kwargs.items():
            hdr[k.replace('_', '-')] = v
        return hdr

    def test_sitelat_sitelong_parsed(self):
        hdr = self._make_header(SITELAT="51 3 0.000", SITELONG="3 43 0.000")
        _, lat, lon = _session_info_from_header(hdr)
        assert lat == pytest.approx(51.05)
        assert lon == pytest.approx(3 + 43/60)

    def test_obsgeo_fallback_when_no_sitelat(self):
        hdr = self._make_header(OBSGEO_B="51.05", OBSGEO_L="3.716667")
        _, lat, lon = _session_info_from_header(hdr)
        assert lat == pytest.approx(51.05)
        assert lon == pytest.approx(3.716667)

    def test_sitelat_takes_priority_over_obsgeo(self):
        hdr = self._make_header(SITELAT="51 3 0.000", SITELONG="3 43 0.000",
                                OBSGEO_B="10.0", OBSGEO_L="20.0")
        _, lat, lon = _session_info_from_header(hdr)
        assert lat == pytest.approx(51.05)

    def test_sitelong_360_convention_normalized(self):
        hdr = self._make_header(SITELAT="51.05", SITELONG="283.716667")
        _, _, lon = _session_info_from_header(hdr)
        assert lon == pytest.approx(283.716667 - 360)

    def test_obsgeo_360_convention_normalized(self):
        hdr = self._make_header(OBSGEO_B="51.05", OBSGEO_L="283.716667")
        _, _, lon = _session_info_from_header(hdr)
        assert lon == pytest.approx(283.716667 - 360)

    def test_missing_coords_return_none(self):
        hdr = self._make_header()
        _, lat, lon = _session_info_from_header(hdr)
        assert lat is None
        assert lon is None


class TestSessionDateFor:
    """session_date_for priority logic."""

    def test_returns_none_when_date_obs_is_none(self):
        assert session_date_for(None) is None

    def test_basic_example(self):
        tz = ZoneInfo("Europe/Brussels")
        date1_utc = "2019-11-19T20:55:46"
        date2_utc = "2019-11-19T22:24:12"
        date3_utc = "2019-11-20T00:12:53"
        expected_session_key = "2019-11-19"
        for date in [date1_utc, date2_utc, date3_utc]:
            assert str(session_date_for(datetime.datetime.fromisoformat(date), fallback_tz=tz)) == expected_session_key


    def test_date_loc_takes_priority(self):
        # date_obs UTC −12h would give 2026-04-26; date_loc local −12h gives 2026-04-27
        date_obs = datetime.datetime(2026, 4, 27, 2, 0, 0)
        date_loc = datetime.datetime(2026, 4, 27, 14, 0, 0)
        result = session_date_for(date_obs, date_loc=date_loc)
        assert result == datetime.date(2026, 4, 27)

    def test_date_loc_takes_priority_over_coords(self):
        # coords (Belgium UTC+2) would give (04:00 local − 12h) = 2026-04-26
        # date_loc gives (14:00 local − 12h) = 2026-04-27 — date_loc wins
        date_obs = datetime.datetime(2026, 4, 27, 2, 0, 0)
        date_loc = datetime.datetime(2026, 4, 27, 14, 0, 0)
        result = session_date_for(
            date_obs,
            date_loc=date_loc,
            sitelat=51.0,
            sitelong=4.0,
        )
        assert result == datetime.date(2026, 4, 27)

    def test_coords_derive_timezone(self):
        # 2026-04-26T23:00 UTC → UTC+2 (CEST) → 2026-04-27T01:00 local − 12h = 2026-04-26
        date_obs = datetime.datetime(2026, 4, 26, 23, 0, 0)
        result = session_date_for(date_obs, sitelat=50.85, sitelong=4.35)
        assert result == datetime.date(2026, 4, 26)

    def test_fallback_tz_used_when_no_coords(self):
        # 2026-04-26T23:00 UTC → UTC-5 (US/Eastern) → 2026-04-26T18:00 local
        date_obs = datetime.datetime(2026, 4, 26, 23, 0, 0)
        tz = ZoneInfo("America/New_York")
        result = session_date_for(date_obs, fallback_tz=tz)
        assert result == datetime.date(2026, 4, 26)


def _make_file(name, image_type, filter_name=None, date_obs=None, camera="Cam",
               gain=100, binning=1, offset=10, set_temp=-10.0, exposure=60.0):
    root = LibraryRoot(name="root", path="/root")
    f = File(root=root, path="p", name=name, size=100, mtime_millis=0)
    img = Image(
        file=f,
        image_type=image_type,
        camera=camera,
        filter=filter_name,
        exposure=exposure,
        gain=gain,
        offset=offset,
        binning=binning,
        set_temp=set_temp,
        date_obs=date_obs,
    )
    f.image = img
    return f


class TestGetSessions:
    """CalibrationMatcher.get_sessions grouping logic."""

    def test_groups_by_night_and_filter(self):
        lights = [
            _make_file("a.fit", "LIGHT", "Ha",
                       date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0)),
            _make_file("b.fit", "LIGHT", "Ha",
                       date_obs=datetime.datetime(2026, 4, 26, 23, 30, 0)),
            _make_file("c.fit", "LIGHT", "OIII",
                       date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0)),
        ]
        matcher = CalibrationMatcher.__new__(CalibrationMatcher)
        sessions = matcher.get_sessions(lights)
        assert len(sessions) == 2
        keys = list(sessions.keys())
        assert SessionKey(session_date=datetime.date(2026, 4, 26), filter="Ha", exposure=60.0) in keys
        assert SessionKey(session_date=datetime.date(2026, 4, 26), filter="OIII", exposure=60.0) in keys

    def test_midnight_crossover_groups_to_previous_day(self):
        """Observation at 02:00 UTC+2 = 00:00 UTC, which minus 12h is the previous calendar day."""
        # 2026-04-27T00:04:00 UTC → minus 12h = 2026-04-26T12:04:00 → date = 2026-04-26
        lights = [
            _make_file("night.fit", "LIGHT", "Ha",
                       date_obs=datetime.datetime(2026, 4, 27, 0, 4, 0)),
        ]
        matcher = CalibrationMatcher.__new__(CalibrationMatcher)
        sessions = matcher.get_sessions(lights)
        key = list(sessions.keys())[0]
        assert key.session_date == datetime.date(2026, 4, 26)

    def test_sorted_by_date_then_filter(self):
        lights = [
            _make_file("x.fit", "LIGHT", "OIII",
                       date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0)),
            _make_file("y.fit", "LIGHT", "Ha",
                       date_obs=datetime.datetime(2026, 4, 27, 22, 0, 0)),
            _make_file("z.fit", "LIGHT", "Ha",
                       date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0)),
        ]
        matcher = CalibrationMatcher.__new__(CalibrationMatcher)
        sessions = matcher.get_sessions(lights)
        keys = list(sessions.keys())
        assert keys[0] == SessionKey(datetime.date(2026, 4, 26), "Ha", 60.0)
        assert keys[1] == SessionKey(datetime.date(2026, 4, 26), "OIII", 60.0)
        assert keys[2] == SessionKey(datetime.date(2026, 4, 27), "Ha", 60.0)

    def test_skips_files_without_image(self):
        root = LibraryRoot(name="root", path="/root")
        bare_file = File(root=root, path="p", name="bare.fit", size=0, mtime_millis=0)
        matcher = CalibrationMatcher.__new__(CalibrationMatcher)
        sessions = matcher.get_sessions([bare_file])
        assert sessions == {}

    def test_none_date_grouped_together(self):
        lights = [
            _make_file("a.fit", "LIGHT", "Ha", date_obs=None),
            _make_file("b.fit", "LIGHT", "Ha", date_obs=None),
        ]
        matcher = CalibrationMatcher.__new__(CalibrationMatcher)
        sessions = matcher.get_sessions(lights)
        assert len(sessions) == 1
        key = list(sessions.keys())[0]
        assert key.session_date is None


class TestPreselect:
    """CalibrationMatcher.preselect_from_existing logic."""

    def _make_sessions(self):
        lights = [
            _make_file("l1.fit", "LIGHT", "Ha",
                       date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0)),
        ]
        matcher = CalibrationMatcher.__new__(CalibrationMatcher)
        return matcher, matcher.get_sessions(lights)

    def test_dark_in_selection_preselects(self):
        matcher, sessions = self._make_sessions()
        dark_file = _make_file("d1.fit", "DARK", None,
                               date_obs=datetime.datetime(2026, 4, 26, 14, 0, 0))
        result = matcher.preselect_from_existing(sessions, [dark_file])
        key = list(sessions.keys())[0]
        assert result[key]["DARK"] is not None
        assert result[key]["DARK"].files[0] is dark_file

    def test_flat_wrong_filter_not_preselected(self):
        matcher, sessions = self._make_sessions()
        flat_file = _make_file("f1.fit", "FLAT", "OIII",
                               date_obs=datetime.datetime(2026, 4, 26, 14, 0, 0))
        result = matcher.preselect_from_existing(sessions, [flat_file])
        key = list(sessions.keys())[0]
        assert result[key]["FLAT"] is None

    def test_flat_matching_filter_preselects(self):
        matcher, sessions = self._make_sessions()
        flat_file = _make_file("f1.fit", "FLAT", "Ha",
                               date_obs=datetime.datetime(2026, 4, 26, 14, 0, 0))
        result = matcher.preselect_from_existing(sessions, [flat_file])
        key = list(sessions.keys())[0]
        assert result[key]["FLAT"] is not None

    def test_light_frames_ignored(self):
        matcher, sessions = self._make_sessions()
        light_file = _make_file("l2.fit", "LIGHT", "Ha",
                                date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0))
        result = matcher.preselect_from_existing(sessions, [light_file])
        key = list(sessions.keys())[0]
        for v in result[key].values():
            assert v is None


def _make_image(image_type, date_obs=None, camera="Cam", filter_name=None,
                gain=100, binning=1, offset=10, set_temp=-10.0, exposure=60.0):
    root = LibraryRoot(name="root", path="/root")
    f = File(root=root, path="p", name="x.fit", size=100, mtime_millis=0)
    img = Image(
        file=f,
        image_type=image_type,
        camera=camera,
        filter=filter_name,
        exposure=exposure,
        gain=gain,
        offset=offset,
        binning=binning,
        set_temp=set_temp,
        date_obs=date_obs,
    )
    f.image = img
    return img


class TestSearchCriteriaFindMaster:
    def test_empty_list_returns_empty_criteria(self):
        result = SearchCriteria.find_master([])
        assert result == SearchCriteria()

    def test_unknown_type_returns_empty_criteria(self):
        img = _make_image("SNAPSHOT")
        result = SearchCriteria.find_master([img])
        assert result == SearchCriteria()

    def test_dark_subs_produce_master_dark_type(self):
        img = _make_image("DARK", date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0))
        result = SearchCriteria.find_master([img])
        assert result.type == "MASTER DARK"

    def test_flat_subs_produce_master_flat_type(self):
        img = _make_image("FLAT", filter_name="Ha",
                          date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0))
        result = SearchCriteria.find_master([img])
        assert result.type == "MASTER FLAT"

    def test_bias_subs_produce_master_bias_type(self):
        img = _make_image("BIAS")
        result = SearchCriteria.find_master([img])
        assert result.type == "MASTER BIAS"

    def test_light_subs_produce_master_light_type(self):
        img = _make_image("LIGHT", filter_name="Ha",
                          date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0))
        result = SearchCriteria.find_master([img])
        assert result.type == "MASTER LIGHT"

    def test_unknown_type_like_dark_flat_returns_empty(self):
        img = _make_image("DARK FLAT")
        result = SearchCriteria.find_master([img])
        assert result == SearchCriteria()

    def test_dark_attributes_inherited(self):
        img = _make_image("DARK", camera="ZWO", gain=200, binning=2,
                          offset=5, set_temp=-15.0, exposure=120.0)
        result = SearchCriteria.find_master([img])
        assert result.camera == "ZWO"
        assert result.binning == "2"
        assert result.exposure == "120.0"
        assert result.gain is None
        assert result.offset is None
        assert result.temperature is None

    def test_flat_attributes_inherited(self):
        img = _make_image("FLAT", camera="ZWO", filter_name="OIII", binning=2,
                          date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0))
        result = SearchCriteria.find_master([img])
        assert result.camera == "ZWO"
        assert result.filter == "OIII"
        assert result.binning == "2"

    def test_light_attributes_inherited(self):
        img = _make_image("LIGHT", camera="ZWO", filter_name="Ha", binning=2,
                          date_obs=datetime.datetime(2026, 4, 26, 22, 0, 0))
        img.object_name = "M42"
        result = SearchCriteria.find_master([img])
        assert result.camera == "ZWO"
        assert result.filter == "Ha"
        assert result.binning == "2"
        assert result.object_name == "M42"

    def test_bias_attributes_inherited(self):
        img = _make_image("BIAS", camera="ZWO", gain=100, binning=1, offset=10)
        result = SearchCriteria.find_master([img])
        assert result.camera == "ZWO"
        assert result.binning == "1"
        assert result.gain is None
        assert result.offset is None

    def test_date_range_covers_single_sub_with_default_margin(self):
        d = datetime.datetime(2026, 4, 26, 22, 0, 0)
        img = _make_image("DARK", date_obs=d)
        margin = timedelta(minutes=5)
        result = SearchCriteria.find_master([img])
        assert result.start_datetime == d - margin
        assert result.end_datetime == d + margin

    def test_date_range_anchored_on_earliest_sub(self):
        d1 = datetime.datetime(2026, 4, 26, 21, 0, 0)
        d2 = datetime.datetime(2026, 4, 26, 22, 0, 0)
        d3 = datetime.datetime(2026, 4, 26, 23, 0, 0)
        imgs = [_make_image("DARK", date_obs=d) for d in [d2, d1, d3]]
        margin = timedelta(minutes=5)
        result = SearchCriteria.find_master(imgs)
        assert result.start_datetime == d1 - margin
        assert result.end_datetime == d1 + margin

    def test_no_dates_clears_date_range(self):
        img = _make_image("DARK", date_obs=None)
        result = SearchCriteria.find_master([img])
        assert result.start_datetime is None
        assert result.end_datetime is None

    def test_partial_dates_uses_available_only(self):
        d = datetime.datetime(2026, 4, 26, 22, 0, 0)
        imgs = [
            _make_image("DARK", date_obs=d),
            _make_image("DARK", date_obs=None),
        ]
        margin = timedelta(minutes=5)
        result = SearchCriteria.find_master(imgs)
        assert result.start_datetime == d - margin
        assert result.end_datetime == d + margin


class TestCalibrationMatcherFindMasters:
    def _matcher(self):
        return CalibrationMatcher.__new__(CalibrationMatcher)

    def test_empty_subs_returns_empty(self):
        assert self._matcher()._find_masters([]) == []

    def test_files_without_image_returns_empty(self):
        root = LibraryRoot(name="root", path="/root")
        bare = File(root=root, path="p", name="x.fit", size=0, mtime_millis=0)
        assert self._matcher()._find_masters([bare]) == []

    def test_unknown_type_returns_empty(self):
        f = _make_file("x.fit", "SNAPSHOT", "Ha")
        assert self._matcher()._find_masters([f]) == []

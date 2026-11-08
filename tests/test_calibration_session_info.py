"""Calibration nights come from each image's stored local time, which the background fill takes
from the file's cached header, not only from the fallback timezone.

A frame at 2026-04-26 10:30 UTC is on the night of the 25th for an observatory in Hawaii (00:30
local), but on the 26th by the Brussels fallback (12:30 local).
"""
import datetime

import pytest
from astropy.io.fits import Card, Header

from photonfinder import local_time
from photonfinder.calibration import CalibrationMatcher
from photonfinder.core import compress
from photonfinder.models import File, FitsHeader, Image, LibraryRoot, SearchCriteria

FRAME_TIME = datetime.datetime(2026, 4, 26, 10, 30)
HAWAII_NIGHT = datetime.date(2026, 4, 25)
BRUSSELS_NIGHT = datetime.date(2026, 4, 26)
HAWAII = {"SITELAT": 19.82, "SITELONG": -155.47}


@pytest.fixture
def matcher(context, mocker):
    mocker.patch.object(context.settings, "get_obs_timezone", create=True, return_value="Europe/Brussels")
    return CalibrationMatcher(context)


@pytest.fixture
def root(context):
    return LibraryRoot.create(name="r", path="/r/")


def _add(root, name, image_type="LIGHT", cards: dict = None):
    file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
    file.image = Image.create(file=file, image_type=image_type, filter="Ha", exposure=60.0, date_obs=FRAME_TIME)
    if cards is not None:
        header = Header([Card("SIMPLE", True), *(Card(key, value) for key, value in cards.items())])
        FitsHeader.create(file=file, header=compress(header.tostring().encode("ascii")))
    return file


def _fill(matcher):
    """Give every image its local time, as the background fill does after an upgrade."""
    assert local_time.update_library(local_time.obs_zone(matcher.context.settings))


def _reloaded(file):
    file.image = Image.get(Image.file == file)
    return file


def _night(matcher, file):
    _fill(matcher)
    [key] = matcher.get_sessions([_reloaded(file)])
    return key.session_date


@pytest.mark.parametrize("cards", [
    {"DATE-LOC": "2026-04-26T00:30:00"},
    HAWAII,
    {"OBSGEO-B": 19.82, "OBSGEO-L": 204.53},
], ids=["date_loc", "site", "obsgeo"])
def test_a_light_lands_on_the_local_night_of_its_header(matcher, root, cards):
    assert _night(matcher, _add(root, "a.fits", cards=cards)) == HAWAII_NIGHT


def test_an_xisf_header_is_used_too(matcher, root):
    file = File.create(root=root, path="", name="a.xisf", size=1, mtime_millis=1)
    file.image = Image.create(file=file, image_type="LIGHT", filter="Ha", exposure=60.0, date_obs=FRAME_TIME)
    blob = b'{"DATE-LOC": [{"value": "2026-04-26T00:30:00", "comment": ""}]}'
    FitsHeader.create(file=file, header=compress(blob))

    assert _night(matcher, file) == HAWAII_NIGHT


def test_a_file_without_a_cached_header_falls_back(matcher, root):
    assert _night(matcher, _add(root, "a.fits")) == BRUSSELS_NIGHT


def test_a_header_without_session_cards_falls_back(matcher, root):
    assert _night(matcher, _add(root, "a.fits", cards={"OBJECT": "M31"})) == BRUSSELS_NIGHT


def test_an_unreadable_header_falls_back(matcher, root):
    file = _add(root, "a.fits")
    FitsHeader.create(file=file, header=b"not compressed")

    assert _night(matcher, file) == BRUSSELS_NIGHT


def test_candidates_use_the_local_night(matcher, root):
    _add(root, "dark.fits", "DARK", cards=HAWAII)
    _fill(matcher)

    [candidate] = matcher.find_candidates(SearchCriteria(type="DARK"))

    assert candidate.session_date == HAWAII_NIGHT


def test_frames_of_one_local_night_stay_together_across_the_fallback_noon(matcher, root):
    first = _add(root, "first.fits", cards=HAWAII)
    second = _add(root, "second.fits", cards=HAWAII)
    Image.update(date_obs=datetime.datetime(2026, 4, 26, 9, 30)).where(Image.file == first).execute()
    _fill(matcher)

    assert len(matcher.get_sessions([_reloaded(first), _reloaded(second)])) == 1


def _frame(root, name, image_type, date_obs, cards=None):
    file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
    Image.create(file=file, image_type=image_type, filter="Ha", exposure=60.0, camera="Cam", binning=1,
                 date_obs=date_obs)
    if cards is not None:
        header = Header([Card("SIMPLE", True), *(Card(key, value) for key, value in cards.items())])
        FitsHeader.create(file=file, header=compress(header.tostring().encode("ascii")))
    return file


def test_flats_of_the_lights_night_rank_first(matcher, root):
    # Brussels, UTC+1: a light at 00:30 UTC on 6 March is 01:30 local, night 5 March. Flats shot
    # at dawn that morning share its night; flats from the evening of 6 March are a night later,
    # though on the light's UTC date. Ranking by the UTC date put the evening ones first.
    light = _frame(root, "light.fits", "LIGHT", datetime.datetime(2026, 3, 6, 0, 30))
    dawn = _frame(root, "dawn.fits", "FLAT", datetime.datetime(2026, 3, 6, 6, 0))
    _frame(root, "evening.fits", "FLAT", datetime.datetime(2026, 3, 6, 18, 0))
    _fill(matcher)

    flats = matcher.flat_candidates([_reloaded(light)])

    assert [f.name for f in flats[0].files] == [dawn.name]
    assert flats[0].session_date == datetime.date(2026, 3, 5)


def test_consumers_read_no_headers(matcher, root, context, mocker):
    from photonfinder.quality_diagnosis import load_scope
    light = _frame(root, "light.fits", "LIGHT", FRAME_TIME, cards=HAWAII)
    _frame(root, "dark.fits", "DARK", FRAME_TIME, cards=HAWAII)
    _fill(matcher)
    reads = mocker.spy(local_time, "load_session_info")

    assert [key.session_date for key in matcher.get_sessions([_reloaded(light)])] == [HAWAII_NIGHT]
    assert matcher.dark_candidates([_reloaded(light)])[0].session_date == HAWAII_NIGHT
    load_scope(context, SearchCriteria())

    assert reads.call_count == 0


@pytest.mark.parametrize("date_obs, date, night", [
    # Sydney (UTC+11 in March): 21:00 local is 10:00 UTC the same day; UTC-12 gave the day before.
    (datetime.datetime(2026, 3, 6, 10, 0), "2026-03-06", "2026-03-06"),
    # 02:00 local on the 7th is 15:00 UTC on the 6th: the local date is the 7th, the night the 6th.
    (datetime.datetime(2026, 3, 6, 15, 0), "2026-03-07", "2026-03-06"),
])
def test_export_dates_are_local(matcher, root, date_obs, date, night):
    import string
    from photonfinder.ui.ExportDialog import template_filename
    file = _frame(root, "a.fits", "LIGHT", date_obs, cards={"SITELAT": -33.87, "SITELONG": 151.21})
    _fill(matcher)
    settings = matcher.context.settings
    settings.get_last_light_path = lambda: ""
    settings.set_last_light_path = lambda value: None

    name = template_filename(_reloaded(file), string.Template("${date}|${date_minus12}|${sess_date}"), settings)

    assert name == f"{date}|{night}|{night}"

"""Calibration nights come from each file's cached header, not only from the fallback timezone.

A frame at 2026-04-26 10:30 UTC is on the night of the 25th for an observatory in Hawaii (00:30
local), but on the 26th by the Brussels fallback (12:30 local).
"""
import datetime

import pytest
from astropy.io.fits import Card, Header

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


def _night(matcher, file):
    [key] = matcher.get_sessions([file])
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

    [candidate] = matcher.find_candidates(SearchCriteria(type="DARK"))

    assert candidate.session_date == HAWAII_NIGHT


def test_frames_of_one_local_night_stay_together_across_the_fallback_noon(matcher, root):
    first = _add(root, "first.fits", cards=HAWAII)
    second = _add(root, "second.fits", cards=HAWAII)
    Image.update(date_obs=datetime.datetime(2026, 4, 26, 9, 30)).where(Image.file == first).execute()
    first.image.date_obs = datetime.datetime(2026, 4, 26, 9, 30)

    assert len(matcher.get_sessions([first, second])) == 1

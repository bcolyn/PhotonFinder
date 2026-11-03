"""Calibration candidates: frames of one night are grouped by their exact exposure, on a real
in-memory library."""
import datetime

import pytest

from photonfinder.calibration import CalibrationMatcher
from photonfinder.models import File, Image, LibraryRoot, SearchCriteria

NIGHT = datetime.datetime(2026, 4, 25, 22, 0)


@pytest.fixture
def matcher(context, mocker):
    mocker.patch.object(context.settings, "get_obs_timezone", create=True, return_value="UTC")
    return CalibrationMatcher(context)


@pytest.fixture
def root(context):
    return LibraryRoot.create(name="r", path="/r/")


def _add(root, name, image_type, exposure, minutes=0, set_temp=None):
    file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
    file.image = Image.create(file=file, image_type=image_type, filter="Ha", exposure=exposure,
                              set_temp=set_temp, date_obs=NIGHT + datetime.timedelta(minutes=minutes))
    return file


def _groups(matcher, image_type):
    candidates = matcher.find_candidates(SearchCriteria(type=image_type))
    return sorted((c.exposure, sorted(f.name for f in c.files)) for c in candidates)


def test_short_flats_of_one_night_are_separate_candidates(matcher, root):
    _add(root, "f02a.fits", "FLAT", 0.2)
    _add(root, "f02b.fits", "FLAT", 0.2, minutes=1)
    _add(root, "f04.fits", "FLAT", 0.4, minutes=2)

    assert _groups(matcher, "FLAT") == [(0.2, ["f02a.fits", "f02b.fits"]), (0.4, ["f04.fits"])]


def test_a_zero_exposure_is_its_own_group(matcher, root):
    _add(root, "b0.fits", "BIAS", 0.0)
    _add(root, "b02.fits", "BIAS", 0.2, minutes=1)

    assert _groups(matcher, "BIAS") == [(0.0, ["b0.fits"]), (0.2, ["b02.fits"])]


def test_float_noise_in_the_exposure_stays_one_group(matcher, root):
    _add(root, "a.fits", "FLAT", 0.2)
    _add(root, "b.fits", "FLAT", 0.2000001, minutes=1)

    [(exposure, names)] = _groups(matcher, "FLAT")
    assert names == ["a.fits", "b.fits"]
    assert exposure == 0.2


def test_long_darks_still_group_by_their_exposure(matcher, root):
    _add(root, "d300a.fits", "DARK", 300.0)
    _add(root, "d300b.fits", "DARK", 300.0, minutes=5)
    _add(root, "d120.fits", "DARK", 120.0, minutes=10)

    assert _groups(matcher, "DARK") == [(120.0, ["d120.fits"]), (300.0, ["d300a.fits", "d300b.fits"])]


@pytest.mark.parametrize("exposures, groups", [
    ((2.6, 2.61, 2.7, 2.86), [4]),           # within 10% of 2.6 s: one group
    ((2.6, 2.61, 2.87), [2, 1]),             # 2.87 is beyond 2.6 + 0.26
    ((10.0, 10.4, 10.5), [3]),               # 10% is 1 s, so the 0.5 s cap applies
    ((10.0, 10.51), [1, 1]),
    ((300.0, 300.4), [2]),                   # long frames: within 0.5 s
    ((300.0, 301.0), [1, 1]),
], ids=["auto_exposure", "beyond_10pct", "cap_at_10s", "beyond_cap", "long_within", "long_beyond"])
def test_exposures_close_enough_share_a_group(matcher, root, exposures, groups):
    """Automatic flat exposures vary a little from frame to frame: they're still one set."""
    for i, exposure in enumerate(exposures):
        _add(root, f"{i}.fits", "FLAT", exposure, minutes=i)

    assert sorted((len(names) for _, names in _groups(matcher, "FLAT")), reverse=True) == groups


def test_a_group_reports_its_median_exposure(matcher, root):
    for i, exposure in enumerate((2.6, 2.62, 2.7)):
        _add(root, f"{i}.fits", "FLAT", exposure, minutes=i)

    [(exposure, _)] = _groups(matcher, "FLAT")
    assert exposure == 2.62


def test_the_leeway_does_not_chain(matcher, root):
    """Each step is within the leeway of the last, but not of the first: two groups."""
    for i, exposure in enumerate((10.0, 10.3, 10.6, 10.9)):
        _add(root, f"{i}.fits", "FLAT", exposure, minutes=i)

    assert [len(names) for _, names in _groups(matcher, "FLAT")] == [2, 2]


@pytest.mark.parametrize("image_type", ["FLAT", "BIAS"])
def test_flats_and_bias_of_one_night_group_whatever_their_temperature(matcher, root, image_type):
    """Their exposures are too short for dark current: a set temperature that varies (or a
    cooler that was off) must not split one night's frames into groups that fall off the list."""
    for i, temp in enumerate((-10.0, -10.0, -5.0, 0.0, None)):
        _add(root, f"{i}.fits", image_type, 2.6, minutes=i, set_temp=temp)

    [(exposure, names)] = _groups(matcher, image_type)
    assert exposure == 2.6
    assert len(names) == 5


def test_darks_still_group_by_temperature(matcher, root):
    _add(root, "cold.fits", "DARK", 300.0, set_temp=-10.0)
    _add(root, "warm.fits", "DARK", 300.0, minutes=5, set_temp=0.0)

    assert _groups(matcher, "DARK") == [(300.0, ["cold.fits"]), (300.0, ["warm.fits"])]


def test_the_reference_time_is_the_median_of_the_utc_times(context, root):
    """date_obs is stored as naive UTC. Read as local time, the median would shift by the
    machine's UTC offset."""
    files = [_add(root, f"l{i}.fits", "LIGHT", 60.0, minutes=m) for i, m in enumerate((0, 10, 50))]

    reference = CalibrationMatcher._reference_image(files)

    assert reference.date_obs == NIGHT + datetime.timedelta(minutes=10)

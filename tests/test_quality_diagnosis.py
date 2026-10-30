import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from astropy.io.fits import Header
from peewee import SqliteDatabase

from photonfinder.core import ApplicationContext, compress
from photonfinder.filesystem import decode_header_blob
from photonfinder.calibration import _session_info_from_header
from photonfinder.image_analysis import ImageAnalysisResult
from photonfinder.models import (
    DIAGNOSIS_BAD_VALUES, DIAGNOSIS_NOT_ANALYSED, File, FitsHeader, Image, ImageStats, LibraryRoot, SearchCriteria,
    diagnosis_counts, search_files, search_row_stat,
)
from photonfinder.mcp_server import _criteria_from_dict
from photonfinder.models import DIAGNOSIS_MISSING_METRICS, DIAGNOSIS_OK
from photonfinder.quality_diagnosis import (
    DiagnosisScope, DiagnosisSettings, FrameStats, Metric, MetricRule,
    PeerGroup, PeerKey, RuleMode, _session_info_from_blob, diagnose, load_scope, save_diagnoses,
)
from tests.conftest import DynamicSettings, McpSettings

NIGHT = datetime(2024, 1, 1, 22, 0, 0)
KEY = PeerKey(NIGHT.date(), "M31", "Ha", 300.0, 100, 1, "ASI2600", "RC8")


def _values(fwhm=3.0, elongation=1.2, background=100.0, stars=500):
    return {Metric.FWHM: fwhm, Metric.ELONGATION: elongation, Metric.BACKGROUND: background,
            Metric.STAR_COUNT: stars}


_next_id = iter(range(1000, 100000))


def _frame(**values) -> FrameStats:
    return FrameStats(file_id=next(_next_id), values=_values(**values), background_rms=5.0)


def _steady(count=9, **values) -> list[FrameStats]:
    """Frames with a small spread: FWHM 2.6..3.4, so median 3.0 and MAD 0.2."""
    return [_frame(**{"fwhm": 2.6 + 0.1 * i, "elongation": 1.1 + 0.02 * i, "background": 96.0 + i,
                      "stars": 480 + 5 * i, **values}) for i in range(count)]


def _verdict(frame, peers, rules=None, min_group_size=5):
    settings = DiagnosisSettings(rules=rules or DiagnosisSettings().rules, min_group_size=min_group_size)
    return diagnose(DiagnosisScope([PeerGroup(KEY, [*peers, frame])]), settings).verdicts[frame.file_id]


@pytest.mark.parametrize("metric, values, label", [
    (Metric.FWHM, {"fwhm": 9.0}, "Bad FWHM"),
    (Metric.ELONGATION, {"elongation": 3.0}, "Bad elongation"),
    (Metric.BACKGROUND, {"background": 900.0}, "Bad background"),
    (Metric.STAR_COUNT, {"stars": 10}, "Bad star count"),
])
def test_each_metric_flags_its_bad_direction(metric, values, label):
    assert _verdict(_frame(**values), _steady()) == label


@pytest.mark.parametrize("values", [{"fwhm": 0.1}, {"elongation": 1.0}, {"background": 1.0}, {"stars": 90000}])
def test_checks_are_one_sided(values):
    assert _verdict(_frame(**values), _steady()) == DIAGNOSIS_OK


def test_mad_limit_is_a_maximum():
    peers = [_frame(fwhm=f) for f in (2.0, 2.0, 3.0, 3.0, 3.0, 4.0, 4.0)]  # median 3, MAD 1
    rules = {Metric.FWHM: MetricRule(RuleMode.MAD, 2.0)}
    assert _verdict(_frame(fwhm=5.0), peers, rules) == DIAGNOSIS_OK
    assert _verdict(_frame(fwhm=5.1), peers, rules) == "Bad FWHM"


def test_percent_limit_is_measured_from_the_median():
    peers = [_frame(fwhm=4.0) for _ in range(6)]
    rules = {Metric.FWHM: MetricRule(RuleMode.PERCENT, 25.0)}
    assert _verdict(_frame(fwhm=5.0), peers, rules) == DIAGNOSIS_OK
    assert _verdict(_frame(fwhm=5.1), peers, rules) == "Bad FWHM"


def test_modes_are_chosen_per_metric():
    rules = {Metric.FWHM: MetricRule(RuleMode.OFF), Metric.BACKGROUND: MetricRule(RuleMode.PERCENT, 10.0)}
    frame = _frame(fwhm=9.0, background=200.0)
    assert _verdict(frame, _steady(), rules) == "Bad background"


def test_zero_mad_skips_the_check_but_percent_still_judges():
    peers = [_frame(fwhm=3.0) for _ in range(6)]
    assert _verdict(_frame(fwhm=9.0), peers, {Metric.FWHM: MetricRule(RuleMode.MAD, 3.0)}) == DIAGNOSIS_OK
    assert _verdict(_frame(fwhm=9.0), peers, {Metric.FWHM: MetricRule(RuleMode.PERCENT, 10.0)}) == "Bad FWHM"


def test_several_failures_are_listed_in_a_fixed_order():
    assert _verdict(_frame(stars=10, fwhm=9.0), _steady()) == "Bad FWHM, Bad star count"


def test_no_stats_row_is_not_analysed():
    frame = FrameStats(file_id=1, has_stats=False)
    result = diagnose(DiagnosisScope([PeerGroup(KEY, [*_steady(), frame])]), DiagnosisSettings())
    assert result.verdicts[1] is None
    assert result.not_analysed == 1


def test_small_group_is_not_judged():
    peers = _steady(count=3)
    bad = _frame(fwhm=9.0)
    result = diagnose(DiagnosisScope([PeerGroup(KEY, [*peers, bad])]), DiagnosisSettings(min_group_size=5))
    assert result.verdicts[bad.file_id] is None
    assert result.too_few_peers == 4


def test_minimum_group_size_is_a_setting():
    rules = {Metric.FWHM: MetricRule()}
    assert _verdict(_frame(fwhm=9.0), _steady(count=3), rules, min_group_size=4) == "Bad FWHM"


def test_frames_without_a_date_have_no_peers():
    key = PeerKey(None, "M31", "Ha", 300.0, 100, 1, "ASI2600", "RC8")
    frame = _frame(fwhm=9.0)
    result = diagnose(DiagnosisScope([PeerGroup(key, [*_steady(), frame])]), DiagnosisSettings())
    assert result.verdicts[frame.file_id] is None


def test_read_failure_stays_not_analysed_and_is_counted():
    failed = FrameStats(file_id=2, values=_values(None, None, None, None))
    result = diagnose(DiagnosisScope([PeerGroup(KEY, [*_steady(), failed])]), DiagnosisSettings())
    assert result.verdicts[2] is None
    assert result.read_failed == 1
    assert result.rejected == 0


def test_no_stars_is_missing_metrics():
    no_stars = FrameStats(file_id=3, values=_values(fwhm=None, elongation=None, stars=0), background_rms=5.0)
    result = diagnose(DiagnosisScope([PeerGroup(KEY, [*_steady(), no_stars])]), DiagnosisSettings())
    assert result.verdicts[3] == DIAGNOSIS_MISSING_METRICS
    assert result.rejected == 1


def test_missing_metrics_applies_even_with_that_check_off():
    frame = FrameStats(file_id=4, values=_values(fwhm=None), background_rms=5.0)
    assert _verdict(frame, _steady(), {Metric.FWHM: MetricRule(RuleMode.OFF)}) == DIAGNOSIS_MISSING_METRICS


def test_missing_metrics_needs_no_peers():
    frame = FrameStats(file_id=5, values=_values(fwhm=None), background_rms=5.0)
    assert _verdict(frame, []) == DIAGNOSIS_MISSING_METRICS


def test_unusable_frames_are_left_out_of_medians_and_group_size():
    unusable = [FrameStats(file_id=10 + i, values=_values(fwhm=None, background=9999.0), background_rms=5.0)
                for i in range(4)]
    group = PeerGroup(KEY, [*_steady(), *unusable])
    assert group.baselines[Metric.BACKGROUND][0] == pytest.approx(100.0)

    too_small = PeerGroup(KEY, [*_steady(count=2), *unusable])
    good = too_small.frames[0]
    assert diagnose(DiagnosisScope([too_small]), DiagnosisSettings()).verdicts[good.file_id] is None


def test_counts_add_up():
    frames = [*_steady(), _frame(fwhm=9.0, stars=10), _frame(fwhm=9.0)]
    result = diagnose(DiagnosisScope([PeerGroup(KEY, frames)]), DiagnosisSettings())
    assert (result.ok, result.rejected) == (9, 2)
    assert result.by_label["Bad FWHM"] == 2
    assert result.by_label["Bad star count"] == 1


# --- Against a real database ----------------------------------------------------------


@pytest.fixture
def library(context):
    context.settings = DynamicSettings()
    context.settings.set_obs_timezone("UTC")
    return LibraryRoot.create(name="Main", path="/data/")


_file_number = iter(range(1, 100000))


def _light(root, *, date_obs=NIGHT, stats=True, path="lights/", header=None, image_type="LIGHT", **image_fields):
    fields = dict(object_name="M31", filter="Ha", exposure=300.0, gain=100, binning=1, camera="ASI2600",
                  telescope="RC8")
    fields.update(image_fields)
    file = File.create(root=root, path=path, name=f"f{next(_file_number)}.fits", size=1, mtime_millis=1)
    Image.create(file=file, image_type=image_type, date_obs=date_obs, **fields)
    if stats:
        ImageStats.create(file=file, fwhm_median=3.0, elongation_median=1.2, background_median=100.0,
                          background_rms=5.0, star_count=500)
    if header:
        h = Header()
        for key, value in header.items():
            h[key] = value
        FitsHeader.create(file=file, header=compress(h.tostring().encode("ascii")))
    return file


def _group_ids(context, criteria=None):
    scope = load_scope(context, criteria or SearchCriteria())
    return [sorted(frame.file_id for frame in group.frames) for group in scope.groups]


@pytest.mark.parametrize("difference", [
    {"object_name": "M33"}, {"filter": "OIII"}, {"exposure": 60.0}, {"gain": 200}, {"binning": 2},
    {"camera": "ASI6200"}, {"telescope": "FSQ"}, {"date_obs": NIGHT + timedelta(days=1)},
])
def test_each_peer_field_separates_groups(context, library, difference):
    a = _light(library)
    b = _light(library, **difference)
    assert sorted(_group_ids(context)) == sorted([[a.rowid], [b.rowid]])


def test_only_plain_lights_are_covered(context, library):
    light = _light(library)
    _light(library, image_type="MASTER LIGHT")
    _light(library, image_type="DARK")
    assert _group_ids(context) == [[light.rowid]]


def test_scope_expands_to_whole_peer_groups_only(context, library):
    seen = _light(library, path="seen/")
    hidden = _light(library, path="hidden/")
    _light(library, path="hidden/", object_name="M33")
    criteria = SearchCriteria(object_name="M31", file_name=seen.name)
    assert _group_ids(context, criteria) == [sorted([seen.rowid, hidden.rowid])]


def test_a_night_runs_across_midnight_using_the_cached_header(context, library):
    # UTC+12: by UTC alone, the first two would land in different nights.
    evening = _light(library, date_obs=datetime(2024, 1, 1, 10, 30), header={"DATE-LOC": "2024-01-01T22:30:00"})
    morning = _light(library, date_obs=datetime(2024, 1, 1, 15, 30), header={"DATE-LOC": "2024-01-02T03:30:00"})
    next_night = _light(library, date_obs=datetime(2024, 1, 2, 10, 30), header={"DATE-LOC": "2024-01-02T22:30:00"})
    assert sorted(_group_ids(context)) == sorted([[evening.rowid, morning.rowid], [next_night.rowid]])


def _fits_blob(*cards: str) -> bytes:
    return compress("".join(card.ljust(80) for card in (*cards, "END")).encode("ascii"))


def _session_blobs():
    xisf = json.loads((Path(__file__).parent / "sample_xisf_header.json").read_text())
    dates = {"DATE-LOC": [{"value": "2024-01-01T22:30:00", "comment": ""}],
             "SITELAT": [{"value": "50 51 00", "comment": ""}],
             "SITELONG": [{"value": "4.35", "comment": ""}]}
    yield "xisf sample", compress(json.dumps(xisf).encode("utf-8"))
    yield "xisf with site", compress(json.dumps({**xisf, **dates}).encode("utf-8"))
    yield "fits with site", _fits_blob("SIMPLE  =                    T", "DATE-LOC= '2024-01-01T22:30:00.123'",
                                       "SITELAT = '50 51 00'          / latitude", "SITELONG= '-4 21 00'")
    yield "fits numeric site", _fits_blob("SITELAT =              50.8500", "SITELONG=             355.6500")
    yield "fits obsgeo fallback", _fits_blob("OBSGEO-B=                50.85", "OBSGEO-L=                 4.35")
    yield "fits no session cards", _fits_blob("SIMPLE  =                    T", "GAIN    =                  100")
    yield "fits with tabs", _fits_blob("DATE-LOC= '2024-01-01T22:30:00'	/ local time")


@pytest.mark.parametrize("blob", [blob for _, blob in _session_blobs()], ids=[name for name, _ in _session_blobs()])
def test_quick_session_info_matches_the_full_parser(blob):
    assert _session_info_from_blob(blob) == _session_info_from_header(decode_header_blob(blob))


def test_unreadable_card_falls_back_to_the_full_parser():
    blob = _fits_blob("SITELAT = 50.85.1", "DATE-LOC= '2024-01-01T22:30:00'")
    assert _session_info_from_blob(blob) == _session_info_from_header(decode_header_blob(blob))


def test_save_stores_verdicts_and_overwrites_with_null(context, library):
    a, b = _light(library), _light(library)
    assert save_diagnoses(context, {a.rowid: "Bad FWHM", b.rowid: DIAGNOSIS_OK})
    assert ImageStats.get(ImageStats.file == a).diagnosis == "Bad FWHM"
    assert ImageStats.get(ImageStats.file == b).diagnosis == DIAGNOSIS_OK

    assert save_diagnoses(context, {a.rowid: None})
    assert ImageStats.get(ImageStats.file == a).diagnosis is None
    assert ImageStats.get(ImageStats.file == b).diagnosis == DIAGNOSIS_OK


def test_save_is_refused_while_another_writer_holds_the_library(context, library):
    file = _light(library)
    assert context.library_write_lock.acquire(blocking=False)
    try:
        assert save_diagnoses(context, {file.rowid: "Bad FWHM"}) is False
    finally:
        context.library_write_lock.release()
    assert ImageStats.get(ImageStats.file == file).diagnosis is None
    assert save_diagnoses(context, {file.rowid: "Bad FWHM"})  # and the lock was released again


def test_load_diagnose_save_round_trip(context, library):
    good = [_light(library) for _ in range(8)]
    for i, file in enumerate(good):
        ImageStats.update(fwhm_median=2.8 + 0.05 * i).where(ImageStats.file == file).execute()
    bad = _light(library)
    ImageStats.update(fwhm_median=9.0).where(ImageStats.file == bad).execute()

    result = diagnose(load_scope(context, SearchCriteria()), DiagnosisSettings())

    assert save_diagnoses(context, result.verdicts)
    assert ImageStats.get(ImageStats.file == bad).diagnosis == "Bad FWHM"
    assert ImageStats.get(ImageStats.file == good[0]).diagnosis == DIAGNOSIS_OK


def test_reanalysing_a_frame_clears_its_diagnosis(context, library):
    from photonfinder.ui.BackgroundLoader import ImageAnalysisTask
    file = _light(library)
    assert save_diagnoses(context, {file.rowid: "Bad FWHM"})

    task = ImageAnalysisTask(context, SearchCriteria(), [file])
    with patch("photonfinder.ui.BackgroundLoader.analyze_file", return_value=ImageAnalysisResult(star_count=7)):
        task._process_file(file, 0)

    stats = ImageStats.get(ImageStats.file == file)
    assert stats.star_count == 7
    assert stats.diagnosis is None


def test_old_database_gains_the_column_with_rows_left_null(tmp_path):
    db_path = tmp_path / "old.db"
    with ApplicationContext(str(db_path), McpSettings()) as ctx:
        root = LibraryRoot.create(name="Main", path="/data/")
        file = File.create(root=root, path="a/", name="a.fits", size=1, mtime_millis=1)
        ImageStats.create(file=file, star_count=5)
        rowid = file.rowid

    old = SqliteDatabase(str(db_path))
    old.connect()
    old.execute_sql('ALTER TABLE imagestats DROP COLUMN diagnosis')
    old.close()

    with ApplicationContext(str(db_path), McpSettings()):
        stats = ImageStats.get(ImageStats.file == rowid)
        assert stats.star_count == 5
        assert stats.diagnosis is None
        ImageStats.update(diagnosis="OK").where(ImageStats.file == rowid).execute()
        assert ImageStats.get(ImageStats.file == rowid).diagnosis == "OK"


# --- The filter ------------------------------------------------------------------------


@pytest.fixture
def diagnosed(library):
    """One file per interesting diagnosis, plus a light with no stats and a dark."""
    files = {}
    for diagnosis in (DIAGNOSIS_OK, "Bad FWHM", "Bad background", "Bad FWHM, Bad background",
                      DIAGNOSIS_MISSING_METRICS):
        file = _light(library)
        ImageStats.update(diagnosis=diagnosis).where(ImageStats.file == file).execute()
        files[diagnosis] = file
    files["no stats"] = _light(library, stats=False)
    files["dark"] = _light(library, stats=False, image_type="DARK")
    return files


def _shown(files, hidden):
    rows, total, _ = search_files(SearchCriteria(diagnosis_hidden=hidden))
    assert total == len(rows)
    by_id = {file.rowid: name for name, file in files.items()}
    return sorted(by_id[row.rowid] for row in rows)


def test_no_filter_shows_everything(diagnosed):
    assert _shown(diagnosed, []) == sorted(diagnosed)


def test_hiding_bad_keeps_ok_not_analysed_and_calibration(diagnosed):
    assert _shown(diagnosed, list(DIAGNOSIS_BAD_VALUES)) == sorted([DIAGNOSIS_OK, "no stats", "dark"])


def test_a_frame_is_hidden_if_any_of_its_values_is_hidden(diagnosed):
    shown = _shown(diagnosed, ["Bad FWHM"])
    assert "Bad FWHM, Bad background" not in shown
    assert "Bad FWHM" not in shown
    assert "Bad background" in shown


def test_hiding_ok_does_not_touch_bad_frames(diagnosed):
    shown = _shown(diagnosed, [DIAGNOSIS_OK])
    assert DIAGNOSIS_OK not in shown
    assert "Bad FWHM" in shown


def test_hiding_not_analysed_as_well_gives_a_strict_ok_view(diagnosed):
    assert _shown(diagnosed, [*DIAGNOSIS_BAD_VALUES, DIAGNOSIS_NOT_ANALYSED]) == [DIAGNOSIS_OK]


def test_a_stats_row_with_no_diagnosis_counts_as_not_analysed(library):
    file = _light(library)  # has stats, never diagnosed
    assert search_files(SearchCriteria(diagnosis_hidden=[DIAGNOSIS_NOT_ANALYSED]))[1] == 0
    assert search_files(SearchCriteria(diagnosis_hidden=[DIAGNOSIS_OK]))[0][0].rowid == file.rowid


def test_counts_overlap_and_ignore_the_diagnosis_filter(diagnosed):
    counts = diagnosis_counts(SearchCriteria(diagnosis_hidden=["Bad FWHM"]))
    assert counts["Bad FWHM"] == 2
    assert counts["Bad background"] == 2
    assert counts[DIAGNOSIS_OK] == 1
    assert counts[DIAGNOSIS_MISSING_METRICS] == 1
    assert counts[DIAGNOSIS_NOT_ANALYSED] == 2


def test_counts_follow_the_rest_of_the_search(diagnosed):
    counts = diagnosis_counts(SearchCriteria(type="DARK"))
    assert counts[DIAGNOSIS_NOT_ANALYSED] == 1
    assert counts[DIAGNOSIS_OK] == 0


def test_settings_survive_a_json_round_trip():
    settings = DiagnosisSettings(rules={**DiagnosisSettings().rules, Metric.FWHM: MetricRule(RuleMode.PERCENT, 25.0),
                                        Metric.BACKGROUND: MetricRule(RuleMode.OFF)}, min_group_size=8)
    assert DiagnosisSettings.from_json(settings.to_json()) == settings


@pytest.mark.parametrize("stored", ["", None, "not json", "[]", '{"rules": {"FWHM": {"mode": "nonsense"}}}'])
def test_unreadable_settings_fall_back_to_the_defaults(stored):
    assert DiagnosisSettings.from_json(stored) == DiagnosisSettings()


def test_settings_missing_a_metric_use_its_default():
    stored = '{"rules": {"FWHM": {"mode": "off", "limit": 1.0}}}'
    settings = DiagnosisSettings.from_json(stored)
    assert settings.rules[Metric.FWHM].mode == RuleMode.OFF
    assert settings.rules[Metric.BACKGROUND] == MetricRule()
    assert settings.min_group_size == 5


def test_filter_survives_a_json_round_trip(context):
    criteria = SearchCriteria(diagnosis_hidden=["Bad FWHM", DIAGNOSIS_NOT_ANALYSED])
    assert SearchCriteria.from_json(criteria.to_json()) == criteria


def test_session_saved_before_this_filter_still_loads(context):
    assert SearchCriteria.from_json('{"file_name": "a"}').diagnosis_hidden == []


def test_column_sorts_in_sql(diagnosed):
    rows, _, _ = search_files(SearchCriteria(type="LIGHT", sorting_index=24, sorting_desc=True))
    diagnoses = [search_row_stat(row, "stats_diagnosis") for row in rows]
    assert diagnoses[0] == DIAGNOSIS_OK  # "O" sorts after "B"
    assert diagnoses[-1] is None


def test_search_rows_carry_their_stats(library):
    with_stats = _light(library)
    without_stats = _light(library, stats=False)
    rows = {row.rowid: row for row in search_files(SearchCriteria())[0]}
    assert search_row_stat(rows[with_stats.rowid], "stats_star_count") == 500
    assert search_row_stat(rows[with_stats.rowid], "stats_fwhm_median") == 3.0
    assert search_row_stat(rows[without_stats.rowid], "stats_star_count") is None


def test_mcp_does_not_accept_the_filter(diagnosed):
    assert _criteria_from_dict({"diagnosis_hidden": ["OK"]}).diagnosis_hidden == []

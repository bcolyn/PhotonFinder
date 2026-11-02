"""Quality diagnosis: judges each LIGHT frame against its peers.

The stored ImageStats values say little in isolation, since a good FWHM or background
differs between setups. Here a frame is compared with the other frames of the same night,
target and equipment, and the verdict is stored as a plain string in ImageStats.diagnosis
so that the search grid can page, sort and filter on it.

Loading a scope reads the library once; `diagnose` then works in memory only, so a caller
can re-run it for every change of settings. Nothing is written until `save_diagnoses`.
"""
import datetime
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from typing import Optional

import numpy as np
from peewee import JOIN, chunked, fn

from photonfinder.calibration import CalibrationMatcher, load_session_info, session_date_for
from photonfinder.core import ApplicationContext
from photonfinder.models import (
    CORE_MODELS, DIAGNOSIS_BAD_BACKGROUND, DIAGNOSIS_BAD_ELONGATION, DIAGNOSIS_BAD_FWHM, DIAGNOSIS_BAD_STAR_COUNT,
    DIAGNOSIS_MISSING_METRICS, DIAGNOSIS_OK, DIAGNOSIS_SEPARATOR, File, Image, ImageStats, SearchCriteria,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


class Metric(Enum):
    """A judged ImageStats column, in the order its verdicts are listed."""
    FWHM = (DIAGNOSIS_BAD_FWHM, True)
    ELONGATION = (DIAGNOSIS_BAD_ELONGATION, True)
    BACKGROUND = (DIAGNOSIS_BAD_BACKGROUND, True)
    STAR_COUNT = (DIAGNOSIS_BAD_STAR_COUNT, False)

    def __init__(self, label: str, high_is_bad: bool):
        self.label = label
        self.high_is_bad = high_is_bad


class RuleMode(Enum):
    OFF = "off"
    MAD = "mad"  # max X times the median absolute deviation from the median
    PERCENT = "percent"  # max X% from the median


@dataclass(frozen=True)
class MetricRule:
    mode: RuleMode = RuleMode.MAD
    limit: float = 3.0


@dataclass(frozen=True)
class DiagnosisSettings:
    rules: dict[Metric, MetricRule] = field(default_factory=lambda: {metric: MetricRule() for metric in Metric})
    min_group_size: int = 5

    def to_json(self) -> str:
        return json.dumps({
            "min_group_size": self.min_group_size,
            "rules": {metric.name: {"mode": rule.mode.value, "limit": rule.limit}
                      for metric, rule in self.rules.items()},
        })

    @classmethod
    def from_json(cls, text: Optional[str]) -> 'DiagnosisSettings':
        """Stored settings, falling back to the defaults for anything missing or unreadable."""
        settings = cls()
        if not text:
            return settings
        try:
            stored = json.loads(text)
            for metric in Metric:
                if rule := stored.get("rules", {}).get(metric.name):
                    settings.rules[metric] = MetricRule(RuleMode(rule["mode"]), float(rule["limit"]))
            return cls(rules=settings.rules, min_group_size=int(stored.get("min_group_size", settings.min_group_size)))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Ignoring unreadable quality diagnosis settings: %s", exc)
            return cls()


@dataclass(frozen=True)
class PeerKey:
    """Frames sharing a key are comparable: metrics shift with any of these."""
    session_night: Optional[datetime.date]
    object_name: Optional[str]
    filter: Optional[str]
    exposure: Optional[float]
    gain: Optional[int]
    binning: Optional[int]
    camera: Optional[str]
    telescope: Optional[str]


@dataclass(frozen=True)
class FrameStats:
    file_id: int
    has_stats: bool = True
    values: dict[Metric, Optional[float]] = field(default_factory=dict)
    background_rms: Optional[float] = None

    @property
    def read_failed(self) -> bool:
        """Analysis stores a failure as a row with every value empty. Says nothing about quality."""
        return self.has_stats and self.background_rms is None and all(v is None for v in self.values.values())

    @property
    def missing_metrics(self) -> bool:
        """Readable, but no stars or an unmeasurable metric: too poor to be worth keeping."""
        return (self.has_stats and not self.read_failed
                and (any(self.values.get(metric) is None for metric in Metric)
                     or self.values[Metric.STAR_COUNT] == 0))

    @property
    def complete(self) -> bool:
        return self.has_stats and not self.read_failed and not self.missing_metrics


@dataclass
class PeerGroup:
    key: PeerKey
    frames: list[FrameStats]

    @cached_property
    def complete_frames(self) -> list[FrameStats]:
        return [frame for frame in self.frames if frame.complete]

    @cached_property
    def baselines(self) -> dict[Metric, tuple[float, float]]:
        """(median, MAD) per metric, over the complete frames only."""
        result = {}
        for metric in Metric:
            values = np.array([frame.values[metric] for frame in self.complete_frames], dtype=float)
            if values.size:
                median = float(np.median(values))
                result[metric] = (median, float(np.median(np.abs(values - median))))
        return result


@dataclass
class DiagnosisScope:
    groups: list[PeerGroup]
    selected_lights: int = 0  # how many selected lights it was built from; 0 if from a search

    @property
    def frame_count(self) -> int:
        return sum(len(group.frames) for group in self.groups)


@dataclass
class DiagnosisResult:
    verdicts: dict[int, Optional[str]] = field(default_factory=dict)
    by_label: Counter = field(default_factory=Counter)  # a frame counts under each label it failed
    rejected: int = 0
    ok: int = 0
    not_analysed: int = 0  # no stats yet
    read_failed: int = 0
    too_few_peers: int = 0


def _frame_labels(frame: FrameStats, group: PeerGroup, settings: DiagnosisSettings) -> list[str]:
    labels = []
    for metric in Metric:
        rule = settings.rules.get(metric, MetricRule(RuleMode.OFF))
        median, mad = group.baselines[metric]
        if rule.mode == RuleMode.OFF:
            continue
        if rule.mode == RuleMode.MAD:
            if mad == 0:
                continue  # a spread of zero can't judge anything
            allowed = rule.limit * mad
        else:
            allowed = abs(median) * rule.limit / 100.0
        value = frame.values[metric]
        deviation = value - median if metric.high_is_bad else median - value
        if deviation > allowed:
            labels.append(metric.label)
    return labels


def diagnose(scope: DiagnosisScope, settings: DiagnosisSettings) -> DiagnosisResult:
    """Judge every frame of the scope, without touching the database."""
    result = DiagnosisResult()
    for group in scope.groups:
        # Without an observation date a frame can't be placed in a night, so it has no peers.
        can_compare = (len(group.complete_frames) >= settings.min_group_size
                       and group.key.session_night is not None)
        for frame in group.frames:
            verdict = None
            if not frame.has_stats:
                result.not_analysed += 1
            elif frame.read_failed:
                result.read_failed += 1
            elif frame.missing_metrics:
                verdict = DIAGNOSIS_MISSING_METRICS  # needs no peers
                result.by_label[verdict] += 1
                result.rejected += 1
            elif not can_compare:
                result.too_few_peers += 1
            else:
                labels = _frame_labels(frame, group, settings)
                verdict = DIAGNOSIS_SEPARATOR.join(labels) or DIAGNOSIS_OK
                result.by_label.update(labels)
                result.rejected += bool(labels)
                result.ok += not labels
            result.verdicts[frame.file_id] = verdict
    return result


def frames_needing_analysis(scope: DiagnosisScope, settings: DiagnosisSettings) -> list[int]:
    """Frames without usable stats, in groups big enough to be judged once they have them."""
    return [frame.file_id
            for group in scope.groups
            if len(group.frames) >= settings.min_group_size and group.key.session_night is not None
            for frame in group.frames
            if not frame.has_stats or frame.read_failed]


def _lights_in_search(criteria: SearchCriteria) -> set[int]:
    query = File.select(File.rowid).join_from(File, Image, JOIN.LEFT_OUTER)
    query = Image.apply_search_criteria(query, criteria).where(fn.UPPER(Image.image_type) == "LIGHT")
    return {rowid for rowid, in query.tuples()}


def _lights_among(file_ids: set[int]) -> set[int]:
    lights = set()
    for batch in chunked(file_ids, _BATCH_SIZE):
        query = Image.select(Image.file).where(Image.file.in_(batch), fn.UPPER(Image.image_type) == "LIGHT")
        lights.update(file_id for file_id, in query.tuples())
    return lights


def load_scope(context: ApplicationContext, criteria: SearchCriteria,
               selected_file_ids: Optional[set[int]] = None) -> DiagnosisScope:
    """Load the peer groups of the selected LIGHT frames, or else of those matching `criteria`.

    Groups are always whole, including frames the search hid or the user didn't select, so
    that a group is judged together. Only frames that could be a peer need their header read
    for the session night.
    """
    with context.database.bind_ctx(CORE_MODELS):
        scope_ids = _lights_among(selected_file_ids) if selected_file_ids else _lights_in_search(criteria)
        if not scope_ids:
            return DiagnosisScope(groups=[])

        query = (Image
                 .select(Image.file, Image.object_name, Image.filter, Image.exposure, Image.gain, Image.binning,
                         Image.camera, Image.telescope, Image.date_obs, ImageStats.rowid, ImageStats.fwhm_median,
                         ImageStats.elongation_median, ImageStats.background_median, ImageStats.star_count,
                         ImageStats.background_rms)
                 .join(ImageStats, JOIN.LEFT_OUTER, on=(ImageStats.file == Image.file))
                 .where(fn.UPPER(Image.image_type) == "LIGHT"))
        rows = list(query.tuples())

        scope_equipment = {row[1:8] for row in rows if row[0] in scope_ids}
        candidates = [row for row in rows if row[1:8] in scope_equipment]
        session_info = load_session_info([row[0] for row in candidates])

    fallback_tz = CalibrationMatcher(context)._fallback_tz()
    groups: dict[PeerKey, list[FrameStats]] = {}
    scope_keys = set()
    for row in candidates:
        file_id, object_name, filter_, exposure, gain, binning, camera, telescope, date_obs, stats_id, \
            fwhm, elongation, background, star_count, background_rms = row
        date_loc, sitelat, sitelong = session_info.get(file_id, (None, None, None))
        key = PeerKey(session_date_for(date_obs, date_loc=date_loc, sitelat=sitelat, sitelong=sitelong,
                                       fallback_tz=fallback_tz),
                      object_name, filter_, exposure, gain, binning, camera, telescope)
        groups.setdefault(key, []).append(FrameStats(
            file_id=file_id,
            has_stats=stats_id is not None,
            values={Metric.FWHM: fwhm, Metric.ELONGATION: elongation, Metric.BACKGROUND: background,
                    Metric.STAR_COUNT: star_count},
            background_rms=background_rms,
        ))
        if file_id in scope_ids:
            scope_keys.add(key)
    return DiagnosisScope(groups=[PeerGroup(key, frames) for key, frames in groups.items() if key in scope_keys],
                          selected_lights=len(scope_ids) if selected_file_ids else 0)


SAVING_DIAGNOSIS = "Saving diagnosis"


def save_diagnoses(context: ApplicationContext, verdicts: dict[int, Optional[str]]) -> bool:
    """Store the verdicts. Returns False, writing nothing, if another writer holds the library."""
    if not context.library_write_lock.acquire(blocking=False, holder=SAVING_DIAGNOSIS):
        return False
    try:
        by_verdict: dict[Optional[str], list[int]] = {}
        for file_id, verdict in verdicts.items():
            by_verdict.setdefault(verdict, []).append(file_id)
        with context.database.bind_ctx(CORE_MODELS), context.database.atomic():
            for verdict, file_ids in by_verdict.items():
                for batch in chunked(file_ids, _BATCH_SIZE):
                    ImageStats.update(diagnosis=verdict).where(ImageStats.file.in_(batch)).execute()
        return True
    finally:
        context.library_write_lock.release()

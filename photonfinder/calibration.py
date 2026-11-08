import datetime
import logging
import statistics
import dataclasses
from dataclasses import dataclass
from typing import Optional

from photonfinder import library_query
from photonfinder.core import ApplicationContext
from photonfinder.local_time import load_session_info, obs_zone, session_date_for
from photonfinder.models import File, Image, SearchCriteria


_log = logging.getLogger(__name__)  # photonfinder.calibration: candidate searches and grouping


def _describe_criteria(criteria: SearchCriteria) -> str:
    """The criteria fields set away from their defaults, for the log."""
    parts = []
    for f in dataclasses.fields(criteria):
        value = getattr(criteria, f.name)
        if f.default is not dataclasses.MISSING:
            default = f.default
        elif f.default_factory is not dataclasses.MISSING:
            default = f.default_factory()
        else:
            default = None
        if f.name == "reference_file" or value == default:
            continue
        parts.append(f"{f.name}={value}")
    return ", ".join(parts)


def _describe_image(image: Optional[Image]) -> str:
    if image is None:
        return "no image"
    return (f"type={image.image_type} camera={image.camera} filter={image.filter} exposure={image.exposure} "
            f"gain={image.gain} offset={image.offset} binning={image.binning} set_temp={image.set_temp} "
            f"date_obs={image.date_obs}")


def _range(values) -> str:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return "?"
    return f"{clean[0]}" if clean[0] == clean[-1] else f"{clean[0]}..{clean[-1]}"


def _log_candidates(what: str, candidates: list['CalibrationCandidate'], shown: int = None):
    """Which candidates the dropdown gets, in order, and which are cut off."""
    shown = len(candidates) if shown is None else shown
    _log.info("%s: %d group(s), %d shown", what, len(candidates), min(shown, len(candidates)))
    for i, c in enumerate(candidates):
        _log.info("  %s #%d night=%s exposure=%s: %d files%s", "shown" if i < shown else "NOT SHOWN",
                  i + 1, c.session_date, c.exposure, c.count, f", master={c.master.name}" if c.master else "")


def _exposure_key(exposure: Optional[float]) -> Optional[float]:
    """An exposure to the millisecond, so float noise (0.2 vs 0.2000001) never splits a group."""
    return round(exposure, 3) if exposure is not None else None


_EXPOSURE_LEEWAY_SECONDS = 0.5
_EXPOSURE_LEEWAY_FRACTION = 0.10


def _exposure_clusters(files: list[File]) -> list[tuple[Optional[float], list[File]]]:
    """Split one night's frames into sets of about the same exposure: (median exposure, files).

    Automatic flat exposures vary a little from frame to frame, yet are one set. A set takes the
    frames within 0.5 s, or 10% if less, of its shortest exposure: 2.6 s flats allow 0.26 s, long
    darks 0.5 s, and 0.2 s and 0.4 s flats stay apart. Measured from the shortest, not from the
    previous frame, so small steps can't chain into a wide set. Frames without an exposure form
    their own set."""
    def exposure_of(f):
        image = getattr(f, 'image', None)
        return _exposure_key(image.exposure if image else None)

    without = [f for f in files if exposure_of(f) is None]
    with_exposure = sorted((f for f in files if exposure_of(f) is not None), key=exposure_of)
    clusters: list[list[File]] = []
    start = None
    for f in with_exposure:
        exposure = exposure_of(f)
        leeway = min(_EXPOSURE_LEEWAY_SECONDS, _EXPOSURE_LEEWAY_FRACTION * start) if start is not None else 0
        if start is None or exposure - start > leeway + 1e-9:
            clusters.append([])
            start = exposure
        clusters[-1].append(f)
    result = [(_exposure_key(statistics.median(exposure_of(f) for f in c)), c) for c in clusters]
    if without:
        result.append((None, without))
    return result


@dataclass(frozen=True, eq=True)
class SessionKey:
    session_date: Optional[datetime.date]
    filter: Optional[str]
    exposure: Optional[float]


@dataclass(eq=False)
class CalibrationCandidate:
    session_date: Optional[datetime.date]
    count: int
    files: list[File]
    exposure: Optional[float] = None
    master: Optional[File] = None

    def __eq__(self, other):
        if not isinstance(other, CalibrationCandidate):
            return NotImplemented
        return frozenset(f.rowid for f in self.files) == frozenset(f.rowid for f in other.files)

    def __hash__(self):
        return hash(frozenset(f.rowid for f in self.files))


class CalibrationMatcher:
    def __init__(self, context: ApplicationContext):
        self.context = context

    def _fallback_tz(self) -> Optional[datetime.tzinfo]:
        """The obs_timezone setting, for frames without DATE-LOC or a site; None to read them as UTC."""
        if not hasattr(self, 'context'):
            return None
        return obs_zone(self.context.settings)

    def _load_session_info(self, files: list[File]) -> dict[
        int, tuple[Optional[datetime.datetime], Optional[float], Optional[float]]]:
        """Batch-load FITS header info for given files."""
        if not hasattr(self, 'context') or not files:
            return {}
        return load_session_info([f.rowid for f in files if f.rowid is not None])

    def get_sessions(self, light_files: list[File]) -> dict[SessionKey, list[File]]:
        """Group LIGHT files by (session_date, filter), sorted by date then filter."""
        session_info = self._load_session_info(light_files)
        fallback_tz = self._fallback_tz()
        groups: dict[SessionKey, list] = {}
        for f in light_files:
            image = f.image if hasattr(f, 'image') else None
            if not image:
                continue
            info = session_info.get(f.rowid, (None, None, None))
            date_loc, sitelat, sitelong = info
            key = SessionKey(
                session_date=session_date_for(
                    image.date_obs,
                    date_loc=date_loc,
                    sitelat=sitelat,
                    sitelong=sitelong,
                    fallback_tz=fallback_tz,
                ),
                filter=image.filter,
                exposure=image.exposure,
            )
            if key not in groups:
                groups[key] = []
            groups[key].append(f)
        sessions = dict(sorted(
            groups.items(),
            key=lambda kv: (kv[0].session_date or datetime.date.min, kv[0].filter or "")
        ))
        _log.info("Light sessions for %d light(s): %d", len(light_files), len(sessions))
        for key, files in sessions.items():
            _log.info("  night=%s filter=%s exposure=%s: %d light(s), first %s",
                      key.session_date, key.filter, key.exposure, len(files), files[0].name)
        return sessions

    def _query_files(self, criteria: SearchCriteria) -> list[File]:
        """Run a SearchCriteria query and return File rows with joined Image data."""
        with self.context.database.bind_ctx([File, Image]):
            return library_query.matching_files(criteria)

    def find_candidates(self, criteria: SearchCriteria) -> list[CalibrationCandidate]:
        """Run a calibration search and group results by night into CalibrationCandidate list.

        Groups are per night, filter and exposure, and for darks (dark-flats included) per set
        temperature too: dark current depends on it. Flats and bias are too short for that, and
        their set temperature often varies or is missing (cooler off), so it doesn't split them.
        Exposures a little apart share a group (see ``_exposure_clusters``)."""
        files = self._query_files(criteria)
        by_temperature = (criteria.type or "").upper() == "DARK"

        session_info = self._load_session_info(files)
        fallback_tz = self._fallback_tz()
        cal_groups: dict[tuple, list] = {}
        for f in files:
            image = f.image if hasattr(f, 'image') else None
            info = session_info.get(f.rowid, (None, None, None))
            date_loc, sitelat, sitelong = info
            d = session_date_for(
                image.date_obs if image else None,
                date_loc=date_loc,
                sitelat=sitelat,
                sitelong=sitelong,
                fallback_tz=fallback_tz,
            )
            filt = image.filter if image else None
            temp_key = round(image.set_temp) if by_temperature and image and image.set_temp is not None else None
            cal_groups.setdefault((d, filt, temp_key), []).append(f)

        groups = []  # (night, filter, exposure, set_temp, files)
        for (d, filt, temp_key), group_files in cal_groups.items():
            for exposure, cluster in _exposure_clusters(group_files):
                groups.append((d, filt, exposure, temp_key, cluster))
        groups.sort(key=lambda g: (g[0] or datetime.date.min, g[1] or "", g[2] or 0, g[3] or 0), reverse=True)
        _log.info("  query [%s]: %d file(s) in %d group(s) by (night, filter, exposure, set_temp)",
                  _describe_criteria(criteria), len(files), len(groups))
        for d, filt, exposure, temp_key, group_files in groups:
            images = [g.image for g in group_files if getattr(g, 'image', None)]
            _log.info("    group night=%s filter=%s exposure=%s set_temp=%s: %d file(s); exposure %s, set_temp %s, "
                      "gain %s, date_obs %s, first %s",
                      d, filt, exposure, temp_key, len(group_files),
                      _range(i.exposure for i in images), _range(i.set_temp for i in images),
                      _range(i.gain for i in images), _range(i.date_obs for i in images), group_files[0].name)
        return [CalibrationCandidate(session_date=d, count=len(files), files=files, exposure=exposure)
                for d, _filt, exposure, _temp_key, files in groups]


    @staticmethod
    def _sort_by_exposure_then_date(candidates: list[CalibrationCandidate], ref_exposure: Optional[float],
                                    ref_date: Optional[datetime.date]) -> list[CalibrationCandidate]:
        def sort_key(c: CalibrationCandidate):
            exp_diff = abs(c.exposure - ref_exposure) if c.exposure is not None and ref_exposure is not None else float(
                'inf')
            date_diff = abs((c.session_date - ref_date).days) if c.session_date and ref_date else float('inf')
            return exp_diff, date_diff

        return sorted(candidates, key=sort_key)

    @staticmethod
    def _reference_image(image_files: list[File]) -> Optional[Image]:
        images = [f.image for f in image_files if hasattr(f, 'image') and f.image]
        if not images:
            return None
        if len(images) == 1:
            return images[0]

        def _median(vals):
            clean = [v for v in vals if v is not None]
            return statistics.median(clean) if clean else None

        def _int_median(vals):
            v = _median(vals)
            return round(v) if v is not None else None

        def _datetime_median(vals):
            # date_obs is naive UTC: without a tzinfo, .timestamp() would read it as local time.
            clean = [v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc) for v in vals if v is not None]
            if not clean:
                return None
            ts = statistics.median(v.timestamp() for v in clean)
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).replace(tzinfo=None)

        ref = Image()
        ref.image_type = images[0].image_type # these 3 should be homogenous anyway, by way of how the grouping works
        ref.camera = images[0].camera
        ref.filter = images[0].filter
        ref.exposure = _median(img.exposure for img in images)
        ref.gain = _int_median(img.gain for img in images)
        ref.offset = _int_median(img.offset for img in images)
        ref.binning = _int_median(img.binning for img in images)
        ref.set_temp = _median(img.set_temp for img in images)
        ref.date_obs = _datetime_median(img.date_obs for img in images)
        ref.file =  images[0].file # peewee enforces non-null, even on a transient object it seems
        return ref

    def dark_candidates(self, session_lights: list[File]) -> list[CalibrationCandidate]:
        ref = self._reference_image(session_lights)
        if not ref:
            return []
        _log.info("Dark candidates for %d light(s), reference %s", len(session_lights), _describe_image(ref))
        results = self.find_candidates(SearchCriteria.find_dark(ref))
        if not results:
            _log.info("  no darks: relaxed search (exposure ±1 s, set_temp ±5)")
            relaxed = SearchCriteria.find_dark_flat(ref)
            relaxed.exposure_tolerance = 1.0
            relaxed.temperature_tolerance = 5.0
            results = self.find_candidates(relaxed)
        ref_date = ref.date_obs.date() if ref.date_obs else None
        results = self._sort_by_exposure_then_date(results, ref.exposure, ref_date)
        _log_candidates("Dark candidates", results)
        return results

    def flat_candidates(self, session_lights: list[File]) -> list[CalibrationCandidate]:
        ref = self._reference_image(session_lights)
        if not ref:
            return []
        _log.info("Flat candidates for %d light(s), reference %s", len(session_lights), _describe_image(ref))
        ref_date = ref.date_obs.date() if ref.date_obs else None
        results = self.find_candidates(SearchCriteria.find_flat(ref))
        if not results:
            _log.info("  no flats within the date window: relaxed search without dates")
            relaxed = SearchCriteria.find_flat(ref)
            relaxed.start_datetime = None
            relaxed.end_datetime = None
            results = self.find_candidates(relaxed)
        if ref_date:
            results.sort(key=lambda c: abs((c.session_date - ref_date).days)
                         if c.session_date else float('inf'))
        _log_candidates(f"Flat candidates, sorted by nights from {ref_date}", results, shown=3)
        return results[:3]

    def bias_candidates(self, session_lights: list[File]) -> list[CalibrationCandidate]:
        ref = self._reference_image(session_lights)
        if not ref:
            return []
        _log.info("Bias candidates for %d light(s), reference %s", len(session_lights), _describe_image(ref))
        results = self.find_candidates(SearchCriteria.find_bias(ref))
        ref_date = ref.date_obs.date() if ref.date_obs else None
        results = self._sort_by_exposure_then_date(results, None, ref_date)
        _log_candidates("Bias candidates", results)
        return results

    def dark_flat_candidates(self, flat_candidate: Optional[CalibrationCandidate]) -> list[CalibrationCandidate]:
        if not flat_candidate or not flat_candidate.files:
            return []
        ref_file = flat_candidate.files[0]
        ref_image = ref_file.image if hasattr(ref_file, 'image') else None
        if not ref_image:
            return []
        _log.info("Dark-flat candidates for a flat group of %d (night %s), reference %s",
                  flat_candidate.count, flat_candidate.session_date, _describe_image(ref_image))
        results = self.find_candidates(SearchCriteria.find_dark_flat(ref_image))
        if not results:
            _log.info("  no dark-flats: relaxed search (exposure ±1 s, set_temp ±5)")
            relaxed = SearchCriteria.find_dark_flat(ref_image)
            relaxed.exposure_tolerance = 1.0
            relaxed.temperature_tolerance = 5.0
            results = self.find_candidates(relaxed)
        ref_date = ref_image.date_obs.date() if ref_image.date_obs else None
        results = self._sort_by_exposure_then_date(results, ref_image.exposure, ref_date)
        _log_candidates("Dark-flat candidates", results)
        return results

    def add_best_master(self, candidate: CalibrationCandidate) -> None:
        masters = self._find_masters(candidate.files)
        if not masters:
            return
        candidate.master = max(masters, key=lambda f: f.mtime_millis if f.mtime_millis is not None else 0)

    def _find_masters(self, subs: list[File]) -> list[File]:
        """Find pre-integrated master files matching a set of subs."""
        images = [f.image for f in subs if hasattr(f, 'image') and f.image]
        if not images:
            return []
        criteria = SearchCriteria.find_master(images)
        if not criteria.type:
            return []
        return self._query_files(criteria)


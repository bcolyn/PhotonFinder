import datetime
import logging
import re
import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

from peewee import JOIN

from photonfinder.core import ApplicationContext, decompress
from photonfinder.fits_handlers import _datetime, _float
from photonfinder.models import File, Image, FitsHeader, SearchCriteria


def _dms_to_deg(s: Optional[str]) -> Optional[float]:
    """Parse a coordinate string to decimal degrees.

    Handles decimal degrees, space/colon-separated DMS, d/m/s markers,
    and N/S/E/W sign prefixes. Returns None on any parse failure.
    """
    if not s:
        return None
    try:
        s = s.strip()
        sign = 1
        if s and s[0] in ('S', 's', 'W', 'w'):
            sign, s = -1, s[1:].strip()
        elif s and s[0] in ('N', 'n', 'E', 'e'):
            sign, s = 1, s[1:].strip()
        # Normalize separators (colons, d/m/s markers, degree symbols) to spaces
        s = re.sub(r"[d°:\'\"hms]+", ' ', s).strip()
        parts = s.split()
        if len(parts) == 3:
            d, m, sec = float(parts[0]), float(parts[1]), float(parts[2])
            d_sign = -1 if d < 0 else 1
            return sign * d_sign * (abs(d) + m / 60.0 + sec / 3600.0)
        if len(parts) == 2:
            d, m = float(parts[0]), float(parts[1])
            d_sign = -1 if d < 0 else 1
            return sign * d_sign * (abs(d) + m / 60.0)
        if len(parts) == 1:
            return sign * float(parts[0])
        return None
    except (ValueError, AttributeError):
        return None


_timezone_finder = None


def _get_timezone_finder():
    global _timezone_finder
    if _timezone_finder is None:
        from timezonefinder import TimezoneFinder
        _timezone_finder = TimezoneFinder()
    return _timezone_finder


@lru_cache(maxsize=256)
def _tz_from_coords(lat: float, lon: float) -> Optional[ZoneInfo]:
    """Look up timezone for coordinates. Cached so repeated calls for same observatory are free."""
    try:
        tz_name = _get_timezone_finder().timezone_at(lat=lat, lng=lon)
        return ZoneInfo(tz_name) if tz_name else None
    except Exception:
        logging.warning(f"Could not determine timezone for lat={lat}, lon={lon}")
        return None


def session_date_for(
        date_obs: Optional[datetime.datetime],
        *,
        date_loc: Optional[datetime.datetime] = None,
        sitelat: Optional[float] = None,
        sitelong: Optional[float] = None,
        fallback_tz=None,
) -> Optional[datetime.date]:
    """Return the observing-session date for a UTC timestamp.

    Derives local datetime via the best available source, then subtracts 12 hours
    so that all frames from one night share a single calendar-date key regardless
    of whether they were captured before or after midnight.

    Priority for deriving local time:
    1. date_loc (DATE-LOC from FITS header) — already local, use as-is.
    2. sitelat + sitelong — derive timezone via timezonefinder.
    3. fallback_tz — convert date_obs from UTC.
    4. Last resort: treat date_obs as local (no timezone conversion).
    """
    if date_obs is None:
        return None
    if date_loc is not None:
        local_dt = date_loc
    else:
        utc_dt = date_obs.replace(tzinfo=datetime.timezone.utc)  # Note that date_obs should always be UTC
        local_dt = utc_dt
        if sitelat is not None and sitelong is not None:
            tz = _tz_from_coords(sitelat, sitelong)
            if tz is not None:
                local_dt = utc_dt.astimezone(tz)
        elif fallback_tz is not None:
            local_dt = utc_dt.astimezone(fallback_tz)
    return (local_dt - timedelta(hours=12)).date()


def _session_info_from_header(hdr) -> tuple[Optional[datetime.datetime], Optional[float], Optional[float]]:
    """Extract (date_loc, sitelat, sitelong) from an astropy FITS header."""
    date_loc = _datetime(hdr.get('DATE-LOC'))
    sitelat = _dms_to_deg(hdr.get('SITELAT'))
    sitelong = _dms_to_deg(hdr.get('SITELONG'))
    # Fall back to OBSGEO-B/L (newer FITS standard, always decimal degrees)
    if sitelat is None:
        sitelat = _float(hdr.get('OBSGEO-B'))
    if sitelong is None:
        sitelong = _float(hdr.get('OBSGEO-L'))
    # SITELONG/OBSGEO-L may use 0..360 east convention; normalize to -180..180
    if sitelong is not None and sitelong > 180:
        sitelong -= 360
    return date_loc, sitelat, sitelong


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
        """Return the configured or system timezone, or None to trigger -12h heuristic."""
        if not hasattr(self, 'context'):
            return None
        tz_name = self.context.settings.get_obs_timezone()
        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except Exception:
                logging.warning(f"Invalid timezone name in settings: {tz_name!r}")
        # System local timezone
        return datetime.datetime.now().astimezone().tzinfo

    def _load_session_info(self, files: list[File]) -> dict[
        int, tuple[Optional[datetime.datetime], Optional[float], Optional[float]]]:
        """Batch-load FITS header info for given files."""
        if not hasattr(self, 'context') or not files:
            return {}
        import json
        from astropy.io.fits import Header as AstropyHeader
        from photonfinder.filesystem import header_from_xisf_dict
        file_ids = [f.id for f in files if hasattr(f, 'id') and f.id is not None]
        if not file_ids:
            return {}
        result = {}
        try:
            with self.context.database.bind_ctx([FitsHeader]):
                for fh in FitsHeader.select().where(FitsHeader.file_id.in_(file_ids)):
                    try:
                        raw = decompress(fh.header)
                        if raw.startswith(b'{'):
                            hdr = header_from_xisf_dict(json.loads(raw))
                        else:
                            hdr = AstropyHeader.fromstring(raw)
                        result[fh.file_id] = _session_info_from_header(hdr)
                    except Exception:
                        pass
        except Exception:
            pass
        return result

    def get_sessions(self, light_files: list[File]) -> dict[SessionKey, list[File]]:
        """Group LIGHT files by (session_date, filter), sorted by date then filter."""
        session_info = self._load_session_info(light_files)
        fallback_tz = self._fallback_tz()
        groups: dict[SessionKey, list] = {}
        for f in light_files:
            image = f.image if hasattr(f, 'image') else None
            if not image:
                continue
            info = session_info.get(getattr(f, 'id', None), (None, None, None))
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
        return dict(sorted(
            groups.items(),
            key=lambda kv: (kv[0].session_date or datetime.date.min, kv[0].filter or "")
        ))

    def find_candidates(self, criteria: SearchCriteria) -> list[CalibrationCandidate]:
        """Run a calibration search and group results by night into CalibrationCandidate list."""
        with self.context.database.bind_ctx([File, Image]):
            query = (File
                     .select(File, Image)
                     .join(Image, JOIN.LEFT_OUTER)
                     .order_by(File.root, File.path, File.name))
            query = Image.apply_search_criteria(query, criteria)
            files = list(query)

        session_info = self._load_session_info(files)
        fallback_tz = self._fallback_tz()
        cal_groups: dict[tuple, list] = {}
        for f in files:
            image = f.image if hasattr(f, 'image') else None
            info = session_info.get(getattr(f, 'id', None), (None, None, None))
            date_loc, sitelat, sitelong = info
            d = session_date_for(
                image.date_obs if image else None,
                date_loc=date_loc,
                sitelat=sitelat,
                sitelong=sitelong,
                fallback_tz=fallback_tz,
            )
            filt = image.filter if image else None
            exp_key = round(image.exposure) if image and image.exposure is not None else None
            temp_key = round(image.set_temp) if image and image.set_temp is not None else None
            key = (d, filt, exp_key, temp_key)
            if key not in cal_groups:
                cal_groups[key] = []
            cal_groups[key].append(f)

        candidates = []
        for (d, _filt, _exp_key, _temp_key), group_files in sorted(
                cal_groups.items(),
                key=lambda kv: (kv[0][0] or datetime.date.min, kv[0][1] or "", kv[0][2] or 0, kv[0][3] or 0),
                reverse=True):
            img = group_files[0].image if hasattr(group_files[0], 'image') else None
            candidates.append(CalibrationCandidate(
                session_date=d,
                count=len(group_files),
                files=group_files,
                exposure=img.exposure if img else None,
            ))
        return candidates

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
            clean = [v for v in vals if v is not None]
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
        results = self.find_candidates(SearchCriteria.find_dark(ref))
        if not results:
            relaxed = SearchCriteria.find_dark_flat(ref)
            relaxed.exposure_tolerance = 1.0
            relaxed.temperature_tolerance = 5.0
            results = self.find_candidates(relaxed)
        ref_date = ref.date_obs.date() if ref.date_obs else None
        return self._sort_by_exposure_then_date(results, ref.exposure, ref_date)

    def flat_candidates(self, session_lights: list[File]) -> list[CalibrationCandidate]:
        ref = self._reference_image(session_lights)
        if not ref:
            return []
        ref_date = ref.date_obs.date() if ref.date_obs else None
        results = self.find_candidates(SearchCriteria.find_flat(ref))
        if not results:
            relaxed = SearchCriteria.find_flat(ref)
            relaxed.start_datetime = None
            relaxed.end_datetime = None
            results = self.find_candidates(relaxed)
        if ref_date:
            results.sort(key=lambda c: abs((c.session_date - ref_date).days)
                         if c.session_date else float('inf'))
        return results[:3]

    def bias_candidates(self, session_lights: list[File]) -> list[CalibrationCandidate]:
        ref = self._reference_image(session_lights)
        if not ref:
            return []
        results = self.find_candidates(SearchCriteria.find_bias(ref))
        ref_date = ref.date_obs.date() if ref.date_obs else None
        return self._sort_by_exposure_then_date(results, None, ref_date)

    def dark_flat_candidates(self, flat_candidate: Optional[CalibrationCandidate]) -> list[CalibrationCandidate]:
        if not flat_candidate or not flat_candidate.files:
            return []
        ref_file = flat_candidate.files[0]
        ref_image = ref_file.image if hasattr(ref_file, 'image') else None
        if not ref_image:
            return []
        results = self.find_candidates(SearchCriteria.find_dark_flat(ref_image))
        if not results:
            relaxed = SearchCriteria.find_dark_flat(ref_image)
            relaxed.exposure_tolerance = 1.0
            relaxed.temperature_tolerance = 5.0
            results = self.find_candidates(relaxed)
        ref_date = ref_image.date_obs.date() if ref_image.date_obs else None
        return self._sort_by_exposure_then_date(results, ref_image.exposure, ref_date)

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
        with self.context.database.bind_ctx([File, Image]):
            query = (File
                     .select(File, Image)
                     .join(Image, JOIN.LEFT_OUTER)
                     .order_by(File.root, File.path, File.name))
            query = Image.apply_search_criteria(query, criteria)
            return list(query)

    def preselect_from_existing(self, sessions: dict[SessionKey, list[File]],
                                existing_files: list[File]) -> dict[
        SessionKey, dict[str, Optional[CalibrationCandidate]]]:
        """
        Match non-LIGHT files from existing_files to session rows.
        Returns {SessionKey: {"DARK": candidate|None, "FLAT": ..., "BIAS": ..., "DARKFLAT": ...}}
        """
        result = {key: {"DARK": None, "FLAT": None, "BIAS": None, "DARKFLAT": None}
                  for key in sessions}

        session_info = self._load_session_info(existing_files)
        fallback_tz = self._fallback_tz()

        for f in existing_files:
            image = f.image if hasattr(f, 'image') else None
            if not image or not image.image_type:
                continue
            itype = image.image_type.upper()
            if itype not in ("DARK", "FLAT", "BIAS"):
                continue

            info = session_info.get(getattr(f, 'id', None), (None, None, None))
            date_loc, sitelat, sitelong = info
            d = session_date_for(
                image.date_obs,
                date_loc=date_loc,
                sitelat=sitelat,
                sitelong=sitelong,
                fallback_tz=fallback_tz,
            )
            candidate = CalibrationCandidate(
                session_date=d,
                count=1,
                files=[f],
            )

            for key in sessions:
                if itype == "FLAT" and key.filter and image.filter and image.filter != key.filter:
                    continue
                if result[key][itype] is None:
                    result[key][itype] = candidate

        return result

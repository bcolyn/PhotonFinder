"""When an image was taken in local time, and the observing night that follows from it.

An image's local time is found once, when it is indexed, and stored in ``Image.local_time``
with where it came from in ``Image.local_time_source``:

- ``loc``: ``DATE-LOC``, the local time the capture software wrote. Always right.
- ``site``: ``DATE-OBS`` in the time zone of the observatory (``SITELAT``/``SITELONG``, or
  ``OBSGEO-B``/``OBSGEO-L``).
- ``fallback``: ``DATE-OBS`` in the ``obs_timezone`` setting.

Where and when come from the file's first header (the primary HDU, or the first XISF image):
an extension's header holds only what differs from the primary. Each image has its own
``DATE-OBS``, so a later image in a file with ``DATE-LOC`` is that far from it in local time.

The session night is the local time minus 12 hours, as a date, so the frames of one night share
it whether taken before or after midnight. It isn't stored: ``models.session_night_sql`` derives
it in SQL (an expression index makes that fast) and ``session_night`` in Python, from the same
rule. Time zones and DST are applied here, in Python, as SQLite can't.
"""
import datetime
import json
import logging
import re
from datetime import timedelta
from functools import lru_cache
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from astropy.io.fits import Card
from peewee import chunked

from photonfinder.core import decompress
from photonfinder.fits_handlers import _datetime, _float
from photonfinder.models import FitsHeader, Image, LibraryProperty

LOC, SITE, FALLBACK = 'loc', 'site', 'fallback'
NIGHT_OFFSET = timedelta(hours=12)

_BATCH_SIZE = 500
_CARD_LENGTH = 80
_SESSION_KEYWORDS = ('DATE-LOC', 'SITELAT', 'SITELONG', 'OBSGEO-B', 'OBSGEO-L')
_SESSION_KEYWORDS_BYTES = frozenset(keyword.encode('ascii') for keyword in _SESSION_KEYWORDS)
_UTC = datetime.timezone.utc
_ZONE_PROPERTY = 'local_time_zone'  # the obs_timezone the library's fallback rows were computed in

SessionInfo = tuple  # (date_loc, sitelat, sitelong), each possibly None
NO_SESSION_INFO: SessionInfo = (None, None, None)


# --- the observer's time zone --------------------------------------------------------------

def default_obs_timezone() -> str:
    """The system's time zone as an IANA name, to fill an empty obs_timezone setting with;
    UTC when the system's can't be told."""
    try:
        from tzlocal import get_localzone_name
        name = get_localzone_name()
        ZoneInfo(name)
        return name
    except Exception:  # pylint: disable=broad-exception-caught  # any failure means "unknown"
        logging.warning("Could not determine the system time zone; using UTC", exc_info=True)
        return 'UTC'


def obs_zone(settings) -> ZoneInfo:
    """The obs_timezone setting as a zone. It is always set (Settings fills an empty one), so
    the live system zone is never used: UTC stands in for an empty or unknown name."""
    name = settings.get_obs_timezone() if settings is not None else None
    if not name:
        return ZoneInfo('UTC')
    try:
        return ZoneInfo(name)
    except Exception:  # pylint: disable=broad-exception-caught  # ZoneInfo raises several kinds
        logging.warning(f"Unknown time zone in settings: {name!r}; using UTC")
        return ZoneInfo('UTC')


def is_valid_timezone(name: str) -> bool:
    if not name or not name.strip():
        return False
    try:
        ZoneInfo(name.strip())
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


# --- the rule ------------------------------------------------------------------------------

def session_night(local_time: Optional[datetime.datetime]) -> Optional[datetime.date]:
    """The night a local time belongs to. The Python twin of models.session_night_sql."""
    return (local_time - NIGHT_OFFSET).date() if local_time is not None else None


def local_time_for(date_obs: Optional[datetime.datetime], *, date_loc: Optional[datetime.datetime] = None,
                   sitelat: Optional[float] = None, sitelong: Optional[float] = None,
                   tz: Optional[datetime.tzinfo] = None) -> tuple[Optional[datetime.datetime], Optional[str]]:
    """(local time, source) of a UTC ``date_obs``, naive. (None, None) without ``date_obs``.

    The first source available wins: ``date_loc`` as given, then the zone of the site, then
    ``tz`` (the obs_timezone setting; UTC when None).
    """
    if date_obs is None:
        return None, None
    if date_loc is not None:
        return _naive(date_loc), LOC
    utc = date_obs.replace(tzinfo=_UTC)  # date_obs is always UTC
    if sitelat is not None and sitelong is not None:
        zone = _tz_from_coords(sitelat, sitelong)
        if zone is not None:
            return utc.astimezone(zone).replace(tzinfo=None), SITE
    return utc.astimezone(tz or _UTC).replace(tzinfo=None), FALLBACK


def file_local_times(dates_obs: list, info: SessionInfo, tz) -> list[tuple]:
    """(local time, source) per image of one file, given each image's ``date_obs`` in file order
    and the session info of the file's first header. ``DATE-LOC`` belongs to the first image's
    ``date_obs``; a later image is as far from it in local time as it is in UTC."""
    date_loc, sitelat, sitelong = info
    reference = dates_obs[0] if dates_obs else None
    result = []
    for date_obs in dates_obs:
        loc = date_loc
        if loc is not None and date_obs != reference:
            loc = loc + (date_obs - reference) if date_obs is not None and reference is not None else None
        result.append(local_time_for(date_obs, date_loc=loc, sitelat=sitelat, sitelong=sitelong, tz=tz))
    return result


def session_date_for(date_obs: Optional[datetime.datetime], *, date_loc: Optional[datetime.datetime] = None,
                     sitelat: Optional[float] = None, sitelong: Optional[float] = None,
                     fallback_tz=None) -> Optional[datetime.date]:
    """The night of a UTC timestamp, computed on the spot (see local_time_for)."""
    local, _ = local_time_for(date_obs, date_loc=date_loc, sitelat=sitelat, sitelong=sitelong, tz=fallback_tz)
    return session_night(local)


def _naive(value: datetime.datetime) -> datetime.datetime:
    """A local time as wall-clock time: an offset written with it is dropped, not applied."""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


# --- where and when, from a header ---------------------------------------------------------

def _dms_to_deg(s: Optional[str]) -> Optional[float]:
    """Parse a coordinate string to decimal degrees.

    Handles decimal degrees, space/colon-separated DMS, d/m/s markers,
    and N/S/E/W sign prefixes. Returns None on any parse failure.
    """
    if isinstance(s, (int, float)):  # N.I.N.A. writes SITELAT/SITELONG as numbers
        return float(s)
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
        # The sign comes from the text: -0 as a number can't say it is negative.
        d_sign = -1 if parts and parts[0].startswith('-') else 1
        if len(parts) == 3:
            d, m, sec = float(parts[0]), float(parts[1]), float(parts[2])
            return sign * d_sign * (abs(d) + m / 60.0 + sec / 3600.0)
        if len(parts) == 2:
            d, m = float(parts[0]), float(parts[1])
            return sign * d_sign * (abs(d) + m / 60.0)
        if len(parts) == 1:
            return sign * float(parts[0])
        return None
    except (ValueError, AttributeError):
        return None


_timezone_finder = None


def _get_timezone_finder():
    global _timezone_finder  # pylint: disable=global-statement  # lazily built: loading it is slow
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
    except Exception:  # pylint: disable=broad-exception-caught
        logging.warning(f"Could not determine timezone for lat={lat}, lon={lon}")
        return None


def session_info_from_header(hdr) -> SessionInfo:
    """Extract (date_loc, sitelat, sitelong) from an astropy FITS header (or a dict of cards)."""
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


def _session_cards(raw: bytes) -> Optional[dict]:
    """Just the session keywords of a stored header, or None if it needs the full parser.

    Parsing a whole header takes milliseconds, which adds up to minutes across a library,
    and a session night only needs these few cards.
    """
    if raw.startswith(b'{'):
        entries = json.loads(raw)
        return {key: entries[key][0].get('value') for key in _SESSION_KEYWORDS if entries.get(key)}
    if b'\t' in raw or len(raw) % _CARD_LENGTH:
        return None
    cards = {}
    for start in range(0, len(raw), _CARD_LENGTH):
        card = raw[start:start + _CARD_LENGTH]
        keyword = card[:8].rstrip().upper()
        if keyword == b'END':
            break
        if keyword == b'HIERARCH' and any(name in card.upper() for name in _SESSION_KEYWORDS_BYTES):
            return None
        name = keyword.decode('ascii')
        if keyword in _SESSION_KEYWORDS_BYTES and name not in cards:  # the first card wins, as in a Header
            cards[name] = Card.fromstring(card.decode('ascii')).value
            if isinstance(cards[name], str) and cards[name].endswith('&'):  # continues on the next card
                return None
    return cards


def _session_info_from_blob(blob: bytes) -> SessionInfo:
    """(date_loc, sitelat, sitelong) from a stored header."""
    from photonfinder.filesystem import decode_header_blob
    try:
        cards = _session_cards(decompress(blob))
    except Exception:  # pylint: disable=broad-exception-caught
        cards = None  # a card the quick route can't read: the full parser repairs those
    return session_info_from_header(decode_header_blob(blob) if cards is None else cards)


def load_session_info(file_ids: Iterable[int]) -> dict[int, SessionInfo]:
    """(date_loc, sitelat, sitelong) per file, from its cached primary header (its first, by
    hdu): an extension's header holds only what differs from the primary, never where and when.
    A file without a readable header is left out."""
    result = {}
    for batch in chunked(list(file_ids), _BATCH_SIZE):
        query = (FitsHeader.select(FitsHeader.file, FitsHeader.header).where(FitsHeader.file.in_(batch))
                 .order_by(FitsHeader.file, FitsHeader.hdu))
        for file_id, blob in query.tuples():
            if file_id in result:
                continue
            try:
                result[file_id] = _session_info_from_blob(blob)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logging.debug("No session info for file %s: %s", file_id, exc)
                result[file_id] = NO_SESSION_INFO
    return result


# --- keeping the library's local times up to date ------------------------------------------

def missing_count() -> int:
    """Images that have a date_obs but no local time yet."""
    return Image.select().where(Image.local_time.is_null() & Image.date_obs.is_null(False)).count()


def recorded_zone() -> Optional[str]:
    record = LibraryProperty.get_or_none(LibraryProperty.name == _ZONE_PROPERTY)
    return record.value if record else None


def needs_update(zone: ZoneInfo) -> bool:
    """Whether update_library has anything to do: fallback rows from another zone, or images
    without a local time."""
    return recorded_zone() != zone.key or missing_count() > 0


def update_library(zone: ZoneInfo, cancelled=lambda: False, progress=None) -> bool:
    """Bring the library's local times in line with the observer's zone. Hold the library write
    lock while it runs. Returns False when cancelled, with the rest left for next time.

    First the ``fallback`` rows are recomputed when they were computed in another zone, which
    needs only ``date_obs``. Then images without a local time get one from their file's cached
    header, read with the quick session-card parser. ``progress(done, total)`` is called after
    each batch. A batch is read in full before it is written.
    """
    if recorded_zone() != zone.key:
        if not _recompute_fallback(zone, cancelled, progress):
            return False
        LibraryProperty.replace(name=_ZONE_PROPERTY, value=zone.key).execute()
    return _fill_missing(zone, cancelled, progress)


def _recompute_fallback(zone, cancelled, progress) -> bool:
    total = Image.select().where(Image.local_time_source == FALLBACK).count()
    done, last = 0, 0
    while rows := list(Image.select(Image.rowid, Image.date_obs)
                       .where((Image.local_time_source == FALLBACK) & (Image.rowid > last))
                       .order_by(Image.rowid).limit(_BATCH_SIZE).tuples()):
        if cancelled():
            return False
        last = rows[-1][0]
        with Image._meta.database.atomic():
            for rowid, date_obs in rows:
                local, source = local_time_for(date_obs, tz=zone)
                Image.update(local_time=local, local_time_source=source).where(Image.rowid == rowid).execute()
        done += len(rows)
        if progress:
            progress(done, total)
    return True


def _fill_missing(zone, cancelled, progress) -> bool:
    total = missing_count()
    done = 0
    while file_ids := [row[0] for row in Image.select(Image.file).distinct()
                       .where(Image.local_time.is_null() & Image.date_obs.is_null(False))
                       .order_by(Image.file).limit(_BATCH_SIZE).tuples()]:
        if cancelled():
            return False
        images = list(Image.select(Image.rowid, Image.file, Image.date_obs, Image.local_time)
                      .where(Image.file.in_(file_ids)).order_by(Image.file, Image.hdu).tuples())
        infos = load_session_info(file_ids)
        by_file = {}
        for rowid, file_id, date_obs, local in images:
            by_file.setdefault(file_id, []).append((rowid, date_obs, local))
        updates = []
        for file_id, rows in by_file.items():
            times = file_local_times([date_obs for _, date_obs, _ in rows], infos.get(file_id, NO_SESSION_INFO), zone)
            updates += [(rowid, *time) for (rowid, _, local), time in zip(rows, times) if local is None and time[0]]
        with Image._meta.database.atomic():
            for rowid, local, source in updates:
                Image.update(local_time=local, local_time_source=source).where(Image.rowid == rowid).execute()
        done += len(updates)
        if progress:
            progress(done, total)
    return True

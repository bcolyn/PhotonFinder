"""Synthetic image libraries for the e2e tests: tiny FITS and XISF files with N.I.N.A.-style headers.

Three library roots, laid out the way people organise real data:

- ``calibration``: ``<camera>/<date>/<DARK|BIAS>/``; darks and bias are reused across
  sessions, so they live in their own root (think cloud storage).
- ``rig``: ``<camera>/<session date>/LIGHT/<object>/`` and ``<camera>/<session date>/FLAT/``.
- ``simple``: ``<session date>/<object>/``, with that night's flats in ``<session date>/FLAT/``.

The point is a populated grid with enough variety to exercise the UI: filtering, export,
mark-as-bad and calibration lookups. Correct parsing of each format belongs in the unit
tests. Within a series the header values vary the way real subs do (DATE-OBS advances,
CCD-TEMP wobbles around SET-TEMP), while camera, gain, offset, binning, SET-TEMP, exposure
and filter stay exact, so frames group up as they would in a real library. Formats and
compression vary (plain, .gz, .bz2, .xz, XISF), and a few files sit under a ``bad*`` name
or folder, which a scan skips.

Everything is generated from a fixed seed, so the same call yields the same files.
"""
import bz2
import gzip
import io
import itertools
import lzma
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import astropy.units as u
import numpy as np
from astropy.coordinates import Angle
from astropy.io import fits
from xisf import XISF

SITE_TZ = ZoneInfo("Europe/Brussels")
SITE_LAT, SITE_LONG = 51.05, 3.72
SWCREATE = "N.I.N.A. 3.1.2.9001"

MONO = "ZWO ASI2600MM Pro"
OSC = "ZWO ASI294MC Pro"
CAMERA_SETTINGS = {MONO: dict(gain=100, offset=50), OSC: dict(gain=120, offset=30)}
TELESCOPES = {MONO: ("Esprit 100ED", 550), OSC: ("RedCat 51", 250)}
TARGETS = {  # RA, Dec in degrees
    "NGC 7000": (314.75, 44.53),
    "M 31": (10.6847, 41.2690),
    "M 27": (299.9016, 22.7212),
}

PLAIN, GZ, BZ2, XZ, XISF_EXT = ".fits", ".fits.gz", ".fits.bz2", ".fits.xz", ".xisf"
ALL_FITS = (PLAIN, GZ, BZ2, XZ)


@dataclass(frozen=True)
class Frame:
    """What one generated file claims to be. Field names follow ``models.Image``."""
    image_type: str
    camera: str
    date_obs: datetime  # UTC, naive, like Image.date_obs
    exposure: float
    gain: int
    offset: int
    set_temp: float
    ccd_temp: float
    filter: str | None = None
    object_name: str | None = None
    coord_ra: float | None = None
    coord_dec: float | None = None
    telescope: str | None = None
    binning: int = 1
    width: int = 32
    height: int = 24
    sequence: int = 1

    @property
    def date_loc(self) -> datetime:
        """Local wall-clock time at the site, naive, as NINA writes DATE-LOC."""
        return self.date_obs.replace(tzinfo=timezone.utc).astimezone(SITE_TZ).replace(tzinfo=None)

    @property
    def session_date(self) -> date:
        """The night this frame belongs to: local time minus 12 hours."""
        return (self.date_loc - timedelta(hours=12)).date()


def series(count: int, start_local: datetime, *, image_type: str, camera: str, exposure: float,
           set_temp: float, rng: np.random.Generator, filter: str | None = None,
           object_name: str | None = None, gap: float = 4.0, **overrides) -> list[Frame]:
    """``count`` consecutive subs starting at ``start_local`` (naive local site time)."""
    settings = CAMERA_SETTINGS[camera] | overrides
    start = start_local.replace(tzinfo=SITE_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    telescope = TELESCOPES[camera][0] if image_type in ("LIGHT", "FLAT") else None
    frames = []
    for i in range(count):
        ra = dec = None
        if object_name:  # dithered around the target
            ra, dec = (round(v + rng.uniform(-0.01, 0.01), 6) for v in TARGETS[object_name])
        offset_s = i * (exposure + gap) + rng.uniform(0, 0.5)
        date_obs = (start + timedelta(seconds=offset_s)).replace(microsecond=0) \
            + timedelta(milliseconds=int(rng.integers(0, 1000)))
        frames.append(Frame(
            image_type=image_type, camera=camera, date_obs=date_obs, exposure=exposure,
            set_temp=set_temp, ccd_temp=round(set_temp + rng.uniform(-0.3, 0.3), 1),
            filter=filter, object_name=object_name, coord_ra=ra, coord_dec=dec,
            telescope=telescope, sequence=i + 1, **settings))
    return frames


def nina_header(f: Frame) -> fits.Header:
    h = fits.Header()
    h["IMAGETYP"] = (f.image_type, "Type of exposure")
    h["EXPOSURE"] = (f.exposure, "[s] Exposure duration")
    h["EXPTIME"] = (f.exposure, "[s] Exposure duration")
    h["DATE-LOC"] = (f.date_loc.isoformat(timespec="milliseconds"), "Time of observation (local)")
    h["DATE-OBS"] = (f.date_obs.isoformat(timespec="milliseconds"), "Time of observation (UTC)")
    h["XBINNING"] = (f.binning, "X axis binning factor")
    h["YBINNING"] = (f.binning, "Y axis binning factor")
    h["GAIN"] = (f.gain, "Sensor gain")
    h["OFFSET"] = (f.offset, "Sensor gain offset")
    h["XPIXSZ"] = (3.76, "[um] Pixel X axis size")
    h["YPIXSZ"] = (3.76, "[um] Pixel Y axis size")
    h["INSTRUME"] = (f.camera, "Imaging instrument name")
    h["SET-TEMP"] = (f.set_temp, "[degC] CCD temperature setpoint")
    h["CCD-TEMP"] = (f.ccd_temp, "[degC] CCD temperature")
    if f.camera == OSC:
        h["BAYERPAT"] = ("RGGB", "Sensor Bayer pattern")
    if f.telescope:
        h["TELESCOP"] = (f.telescope, "Name of telescope")
        h["FOCALLEN"] = (TELESCOPES[f.camera][1], "[mm] Focal length")
    h["SITELAT"] = (SITE_LAT, "[deg] Observation site latitude")
    h["SITELONG"] = (SITE_LONG, "[deg] Observation site longitude")
    if f.filter:
        h["FILTER"] = (f.filter, "Active filter name")
    if f.object_name:
        h["OBJECT"] = (f.object_name, "Name of the object of interest")
        h["OBJCTRA"] = (Angle(f.coord_ra, u.deg).to_string(unit=u.hour, sep=" ", precision=0, pad=True),
                        "[H M S] RA of imaged object")
        h["OBJCTDEC"] = (Angle(f.coord_dec, u.deg).to_string(sep=" ", precision=0, pad=True, alwayssign=True),
                         "[D M S] Declination of imaged object")
        h["RA"] = (f.coord_ra, "[deg] RA of telescope")
        h["DEC"] = (f.coord_dec, "[deg] Declination of telescope")
    h["EQUINOX"] = (2000.0, "Equinox of celestial coordinate system")
    h["SWCREATE"] = (SWCREATE, "Software that created this file")
    return h


def make_pixels(f: Frame, rng: np.random.Generator) -> np.ndarray:
    """A plausible, linear uint16 image: pedestal and read noise, plus whatever the type adds."""
    y, x = np.mgrid[0:f.height, 0:f.width]
    img = np.full((f.height, f.width), 500.0)
    if f.image_type in ("DARK", "LIGHT"):
        img += 0.02 * f.exposure  # dark current
    if f.image_type == "DARK":
        hot = rng.integers(0, f.height * f.width, 3)
        img.flat[hot] += 5000
    if f.image_type == "FLAT":
        r2 = ((x - f.width / 2) / f.width) ** 2 + ((y - f.height / 2) / f.height) ** 2
        img += 25000 * (1 - 1.2 * r2)  # vignetting
    if f.image_type == "LIGHT":
        img += 2 * f.exposure  # sky
        for _ in range(4):
            sx, sy = rng.uniform(3, f.width - 3), rng.uniform(3, f.height - 3)
            img += rng.uniform(2000, 20000) * np.exp(-((x - sx) ** 2 + (y - sy) ** 2) / (2 * 1.2 ** 2))
    img += rng.normal(0, 8, img.shape)
    return np.clip(img, 0, 65535).astype(np.uint16)


_COMPRESSORS = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}


def write_frame(path: Path, f: Frame, rng: np.random.Generator):
    data = make_pixels(f, rng)
    header = nina_header(f)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".xisf":
        keywords = {card.keyword: [{"value": str(card.value), "comment": card.comment}] for card in header.cards}
        XISF.write(str(path), data[:, :, np.newaxis], creator_app=SWCREATE,
                   image_metadata={"FITSKeywords": keywords})
    else:
        buffer = io.BytesIO()
        fits.PrimaryHDU(data, header).writeto(buffer)
        with _COMPRESSORS.get(path.suffix.lower(), open)(path, "wb") as out:
            out.write(buffer.getvalue())


def file_name(f: Frame, ext: str) -> str:
    """Like the production pattern: 2025-03-04_21-16-31_HaOIII_-14.80_120.00s_0021.fits"""
    return f"{f.date_loc:%Y-%m-%d_%H-%M-%S}_{f.filter or ''}_{f.ccd_temp:.2f}_{f.exposure:.2f}s_{f.sequence:04d}{ext}"


@dataclass
class SyntheticLibraries:
    base: Path
    roots: dict[str, Path] = field(default_factory=dict)
    files: dict[Path, Frame] = field(default_factory=dict)  # what a scan should import
    skipped: dict[Path, Frame] = field(default_factory=dict)  # valid files marked bad

    def root(self, name: str) -> Path:
        self.roots[name] = self.base / name
        return self.roots[name]

    def add(self, folder: Path, frames: list[Frame], ext: str | tuple[str, ...], rng: np.random.Generator,
            prefix: str = "", skipped: bool = False):
        """Write ``frames`` into ``folder``; a tuple of extensions is cycled through."""
        exts = itertools.cycle((ext,) if isinstance(ext, str) else ext)
        for frame, e in zip(frames, exts):
            path = folder / (prefix + file_name(frame, e))
            write_frame(path, frame, rng)
            (self.skipped if skipped else self.files)[path] = frame

    def frames(self, **criteria) -> list[Frame]:
        """The importable frames whose fields equal ``criteria``, e.g. ``frames(image_type="DARK")``."""
        return [f for f in self.files.values()
                if all(getattr(f, k) == v for k, v in criteria.items())]


def build_libraries(base: Path, seed: int = 20261015) -> SyntheticLibraries:
    rng = np.random.default_rng(seed)
    lib = SyntheticLibraries(base)
    at = datetime.fromisoformat

    def add(folder, ext, count, start, **kw):
        lib.add(folder, series(count, at(start), rng=rng, **kw), ext, rng)

    # --- calibration: <camera>/<date>/<type>, reused across sessions ---
    cal = lib.root("calibration")
    mono_cal = cal / MONO / "2026-09-20"
    add(mono_cal / "DARK", XZ, 5, "2026-09-20T22:00:00", image_type="DARK", camera=MONO, exposure=300.0, set_temp=-10.0)
    add(mono_cal / "DARK", GZ, 5, "2026-09-20T22:30:00", image_type="DARK", camera=MONO, exposure=120.0, set_temp=-10.0)
    # dark flats, matching the rig flats below
    add(mono_cal / "DARK", PLAIN, 5, "2026-09-20T22:45:00", image_type="DARK", camera=MONO, exposure=3.0, set_temp=-10.0)
    add(mono_cal / "DARK", PLAIN, 5, "2026-09-20T22:50:00", image_type="DARK", camera=MONO, exposure=0.5, set_temp=-10.0)
    add(mono_cal / "BIAS", BZ2, 5, "2026-09-20T22:55:00", image_type="BIAS", camera=MONO, exposure=0.0, set_temp=-10.0)
    # an older set at a colder setpoint and a different gain: must not match the -10C / gain 100 lights
    old_cal = cal / MONO / "2026-03-02"
    add(old_cal / "DARK", XZ, 3, "2026-03-02T22:00:00", image_type="DARK", camera=MONO, exposure=300.0, set_temp=-20.0)
    add(old_cal / "BIAS", PLAIN, 3, "2026-03-02T22:20:00", image_type="BIAS", camera=MONO, exposure=0.0,
        set_temp=-20.0, gain=0)
    osc_cal = cal / OSC / "2026-07-25"
    add(osc_cal / "DARK", ALL_FITS, 5, "2026-07-25T22:00:00", image_type="DARK", camera=OSC, exposure=60.0, set_temp=-10.0)
    add(osc_cal / "BIAS", GZ, 5, "2026-07-25T22:10:00", image_type="BIAS", camera=OSC, exposure=0.0, set_temp=-10.0)

    # --- rig: <camera>/<session date>/LIGHT/<object> and .../FLAT ---
    rig = lib.root("rig")
    night1 = rig / MONO / "2026-10-14"
    # crosses local midnight: every frame still belongs to the 2026-10-14 session
    add(night1 / "LIGHT" / "NGC 7000", XZ, 6, "2026-10-14T23:30:00", image_type="LIGHT", camera=MONO,
        exposure=300.0, set_temp=-10.0, filter="Ha", object_name="NGC 7000")
    add(night1 / "LIGHT" / "NGC 7000", XZ, 6, "2026-10-15T00:05:00", image_type="LIGHT", camera=MONO,
        exposure=300.0, set_temp=-10.0, filter="OIII", object_name="NGC 7000")
    # flats shot the next morning, still the 2026-10-14 session
    add(night1 / "FLAT", PLAIN, 5, "2026-10-15T07:30:00", image_type="FLAT", camera=MONO, exposure=3.0,
        set_temp=-10.0, filter="Ha")
    add(night1 / "FLAT", PLAIN, 5, "2026-10-15T07:32:00", image_type="FLAT", camera=MONO, exposure=3.0,
        set_temp=-10.0, filter="OIII")

    night2 = rig / MONO / "2026-10-15"
    ngc7000 = night2 / "LIGHT" / "NGC 7000"
    ha = series(5, at("2026-10-15T21:00:00"), rng=rng, image_type="LIGHT", camera=MONO, exposure=300.0,
                set_temp=-10.0, filter="Ha", object_name="NGC 7000")
    lib.add(ngc7000, ha[:4], GZ, rng)
    lib.add(ngc7000, ha[4:], GZ, rng, prefix="BAD_", skipped=True)
    clouds = series(2, at("2026-10-15T21:30:00"), rng=rng, image_type="LIGHT", camera=MONO, exposure=300.0,
                    set_temp=-10.0, filter="Ha", object_name="NGC 7000")
    lib.add(ngc7000 / "bad clouds", clouds, GZ, rng, skipped=True)
    add(night2 / "LIGHT" / "M 31", XISF_EXT, 4, "2026-10-15T23:00:00", image_type="LIGHT", camera=MONO,
        exposure=120.0, set_temp=-10.0, filter="L", object_name="M 31")
    add(night2 / "FLAT", BZ2, 5, "2026-10-16T07:30:00", image_type="FLAT", camera=MONO, exposure=0.5,
        set_temp=-10.0, filter="L")

    # --- simple: <session date>/<object>, one-shot-colour camera ---
    simple = lib.root("simple")
    add(simple / "2026-08-01" / "M 27", ALL_FITS, 6, "2026-08-01T22:45:00", image_type="LIGHT", camera=OSC,
        exposure=60.0, set_temp=-10.0, filter="L-eXtreme", object_name="M 27")
    add(simple / "2026-08-01" / "FLAT", PLAIN, 5, "2026-08-02T06:30:00", image_type="FLAT", camera=OSC,
        exposure=1.5, set_temp=-10.0, filter="L-eXtreme")
    add(simple / "2026-08-05" / "M 27", GZ, 4, "2026-08-05T22:30:00", image_type="LIGHT", camera=OSC,
        exposure=60.0, set_temp=-10.0, filter="L-eXtreme", object_name="M 27")
    add(simple / "2026-08-05" / "NGC 7000", (PLAIN, XISF_EXT), 4, "2026-08-06T01:00:00", image_type="LIGHT",
        camera=OSC, exposure=60.0, set_temp=-10.0, filter="L-eXtreme", object_name="NGC 7000")

    return lib

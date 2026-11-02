"""Coordinates read from FITS headers: a number is in degrees, text is sexagesimal, RA in hours."""
import pytest
from astropy.coordinates import SkyCoord
from astropy.io.fits import Header
from astropy import units as u

from photonfinder.calibration import _dms_to_deg
from photonfinder.core import compress, hp
from photonfinder.fits_handlers import GenericHandler
from photonfinder.models import FitsHeader, Image
from photonfinder.ui.BackgroundLoader import ImageReindexWorker
from tests.conftest import DynamicSettings


def _coordinates(**cards):
    return GenericHandler()._get_coordinates(Header(cards))


@pytest.mark.parametrize("ra, dec, expected", [
    (150.0, 0.0, (150.0, 0.0)),
    (0.0, 45.0, (0.0, 45.0)),
    (150, 0, (150.0, 0.0)),
    ("150.0", "0.0", (150.0, 0.0)),
    (150.0, 45.0, (150.0, 45.0)),
    (150.0, -0.5, (150.0, -0.5)),
    # mixed
    (150.0, "+45 00 00", (150.0, 45.0)),
    (150.0, "00 00 00", (150.0, 0.0)),
    (150.0, "-00 30 00", (150.0, -0.5)),
    ("10 00 00", 45.0, (150.0, 45.0)),
    ("10 00 00", 0.0, (150.0, 0.0)),
    ("00 00 00", 45.0, (0.0, 45.0)),
    # both text
    ("10 00 00", "+45 00 00", (150.0, 45.0)),
    ("10:00:00", "-45:00:00", (150.0, -45.0)),
    ("10 00 00", "-00 30 00", (150.0, -0.5)),
])
def test_ra_and_dec(ra, dec, expected):
    result_ra, result_dec, _ = _coordinates(RA=ra, DEC=dec)

    assert (result_ra, result_dec) == pytest.approx(expected)


def test_a_frame_on_the_celestial_equator_gets_the_right_sky_pixel():
    _, _, pixel = _coordinates(RA=150.0, DEC=0.0)

    assert pixel == hp.skycoord_to_healpix(SkyCoord(150.0, 0.0, unit=u.deg, frame="icrs"))


def test_a_cone_search_position_in_hours_finds_the_same_pixel():
    _, _, pixel = _coordinates(OBJCTRA="10 00 00", OBJCTDEC="00 00 00")

    assert pixel == hp.skycoord_to_healpix(SkyCoord(150.0, 0.0, unit=u.deg, frame="icrs"))


@pytest.mark.parametrize("ra, dec", [(0.0, 0.0), ("00 00 00", "00 00 00"), (0, "00 00 00")])
def test_zero_for_both_means_no_position(ra, dec):
    assert _coordinates(RA=ra, DEC=dec) == (None, None, None)


@pytest.mark.parametrize("cards", [{}, {"RA": 150.0}, {"DEC": 0.0}, {"RA": "junk", "DEC": 0.0}, {"RA": 150.0, "DEC": "junk"}])
def test_no_usable_position(cards):
    assert _coordinates(**cards) == (None, None, None)


@pytest.mark.parametrize("text, expected", [
    ("-0 30 00", -0.5),
    ("-00 30 00", -0.5),
    ("-0:30:00", -0.5),
    ("-0d30m00s", -0.5),
    ("-0 30", -0.5),
    ("+0 30 00", 0.5),
    ("0 30 00", 0.5),
    ("-1 30 00", -1.5),
    ("S0 30 00", -0.5),
    ("W0 30 00", -0.5),
    ("N0 30 00", 0.5),
])
def test_site_coordinates_keep_their_sign(text, expected):
    assert _dms_to_deg(text) == pytest.approx(expected)


def test_rebuild_index_corrects_a_stored_position(sample):
    context, objs = sample
    context.settings = DynamicSettings()
    header = Header({"IMAGETYP": "LIGHT", "RA": 150.0, "DEC": 0.0})
    FitsHeader.create(file=objs["dark"], header=compress(header.tostring().encode("ascii")))
    wrong = hp.skycoord_to_healpix(SkyCoord(90.0, 0.0, unit=u.deg, frame="icrs"))
    Image.update(coord_ra=90.0, coord_dec=0.0, coord_pix256=int(wrong)).where(Image.file == objs["dark"]).execute()

    assert context.library_write_lock.acquire(holder="Rebuild index")  # as reindex_images does; the task releases it
    ImageReindexWorker(context)._reindex_images_task()

    image = Image.get(Image.file == objs["dark"])
    assert (image.coord_ra, image.coord_dec) == pytest.approx((150.0, 0.0))
    assert image.coord_pix256 == hp.skycoord_to_healpix(SkyCoord(150.0, 0.0, unit=u.deg, frame="icrs"))

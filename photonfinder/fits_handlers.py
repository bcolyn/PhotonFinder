import logging
from logging import log
from logging import WARN
from datetime import datetime
from typing import Optional, Tuple

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io.fits import Header
from astropy_healpix import HEALPix

from photonfinder.models import File, Image
from photonfinder.core import StatusReporter


def _upper(value: str):
    return None if value is None else value.upper()


def _int(value):
    return None if value is None else int(value)


def _float(value):
    return None if value is None else float(value)

def _type(value):
    if value is None:
        return None
    value = value.upper()
    value = value.replace(' FRAME', '')
    return value

def _datetime(value):
    """Convert ISO 8601 formatted date string to datetime object."""
    if value is None or value == '' or value == 'N/A':
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        try:
            # Try a more flexible parsing for non-standard formats
            return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f')
        except ValueError:
            try:
                return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                log(WARN, f"Could not parse date string: {value}")
                return None


class FitsHeaderHandler:
    """
    Base class for FITS header handlers.
    Each handler is responsible for processing FITS headers from a specific program.
    """

    def can_handle(self, header: Header) -> bool:
        """
        Check if this handler can process the given FITS header.

        Args:
            header: The raw FITS header as a string

        Returns:
            bool: True if this handler can process the header, False otherwise
        """
        return False

    def process(self, file: File, header: Header) -> Optional[Image]:
        """
        Process the FITS header and create an Image object.

        Args:
            file: The File object associated with the FITS header
            header: The raw FITS header as a string

        Returns:
            Optional[Image]: An Image object if processing was successful, None otherwise
        """
        # Extract common fields from the header
        image_type = self._get_image_type(header)
        filter_name = self._get_filter(header)
        camera = self._get_camera(header)
        exposure = self._get_exposure(header)
        gain = self._get_gain(header)
        offset = self._get_offset(header)
        binning = self._get_binning(header)
        set_temp = self._get_set_temp(header)
        telescope = self._get_telescope(header)
        object_name = self._get_object_name(header)
        date_obs = self._get_date_obs(header)

        # Extract coordinates and HEALPix value
        coord_ra, coord_dec, coord_pix256 = self._get_coordinates(header)

        # Create and return an Image object
        return Image(
            file=file,
            image_type=image_type,
            filter=filter_name,
            exposure=exposure,
            gain=gain,
            offset=offset,
            binning=binning,
            set_temp=set_temp,
            camera=camera,
            telescope=telescope,
            object_name=object_name,
            date_obs=date_obs,
            coord_ra=coord_ra,
            coord_dec=coord_dec,
            coord_pix256=coord_pix256
        )

    # def get_wcs_values(self, header: Header) -> dict:
    # NOTE: getting a usable WCS from most raw FITS subs is tricky
    # we try and avoid it for now
    # see http://tdc-www.harvard.edu/wcstools/wcstools.wcs.html, but TL;DR: we need center, CDELT1/2 and CROTA2
    # For SGP
    #  - SCALE/PIXSCALE => CDELT1 = CDELT2 (0.000181708333333333 = deg per pixel, SGP has "/px)
    #  - ANGLE/POSANGLE => CROTA1 = CROTA2 ( 258.37 =>   78.36999511718801  ) (180 deg diff i.e upside down)
    # For NINA
    # - center from telescope
    # - scale = ? XPIXSZ + XBINNING + FOCALLEN
    # - rot = ignore? we do cone search anyway
    # For SeeStar - all OK

    def _get_image_type(self, header: Header) -> Optional[str]:
        return _type(header.get('IMAGETYP'))

    def _get_filter(self, header: Header) -> Optional[str]:
        return header.get('FILTER')

    def _get_camera(self, header: Header) -> Optional[str]:
        return header.get('INSTRUME')

    def _get_exposure(self, header: Header) -> Optional[float]:
        return _float(header.get('EXPOSURE'))

    def _get_gain(self, header: Header) -> Optional[int]:
        return _int(header.get('GAIN'))

    def _get_binning(self, header: Header) -> Optional[int]:
        return _int(header.get('XBINNING', 1))

    def _get_set_temp(self, header: Header) -> Optional[float]:
        return _float(header.get('SET-TEMP'))

    def _get_telescope(self, header: Header) -> Optional[str]:
        return header.get('TELESCOP')

    def _get_object_name(self, header: Header) -> Optional[str]:
        return header.get('OBJECT')

    def _get_date_obs(self, header: Header) -> Optional[datetime]:
        return _datetime(header.get('DATE-OBS'))

    def _get_offset(self, header: Header) -> Optional[int]:
        return _int(header.get('OFFSET'))

    def _get_coordinates(self, header: Header) -> Tuple[Optional[float], Optional[float], Optional[int]]:
        """
        Extract RA and DEC from the FITS header and calculate HEALPix value.

        RA can be in either "RA" (preferred) or "OBJCTRA" fields.
        DEC can be in either "DEC" (preferred) or "OBJCTDEC" fields.

        Returns:
            Tuple containing:
            - RA as a floating point value in hours
            - DEC as a floating point value in degrees
            - HEALPix value (integer) with nside=256
        """
        # Extract RA from header
        ra_value = header.get('RA')
        if ra_value is None:
            ra_value = header.get('OBJCTRA')

        # Extract DEC from header
        dec_value = header.get('DEC')
        if dec_value is None:
            dec_value = header.get('OBJCTDEC')

        # If we don't have both RA and DEC, return None for all values
        if ra_value is None or dec_value is None:
            return None, None, None

        try:
            # Check if RA and DEC are already in degrees (numeric values)
            ra_numeric = isinstance(ra_value, (int, float))
            dec_numeric = isinstance(dec_value, (int, float))

            if ra_numeric and dec_numeric:
                # RA and DEC are already in degrees, use them directly for SkyCoord
                ra_degrees = float(ra_value)
                dec_degrees = float(dec_value)
                # Create SkyCoord object for HEALPix calculation
                coords = SkyCoord(float(ra_value), dec_degrees, unit=u.deg, frame='icrs')
            else:
                # RA and DEC are in string format (HH:MM:SS), parse them
                coords = SkyCoord(ra_value, dec_value, unit=(u.hourangle, u.deg), frame='icrs')
                ra_degrees = coords.ra.degree
                dec_degrees = coords.dec.degree

            if ra_degrees == 0.0 and dec_degrees == 0.0:
                return None, None, None

            # Calculate HEALPix value with nside=256
            hp = HEALPix(nside=256, order='nested', frame='icrs')
            healpix_index = hp.skycoord_to_healpix(coords)

            return ra_degrees, dec_degrees, int(healpix_index)
        except Exception as e:
            print(f"Error processing coordinates: {str(e)}")
            return None, None, None


class SharpCapHandler(FitsHeaderHandler):
    """Handler for FITS files created by SharpCap."""

    def can_handle(self, header: Header) -> bool:
        # Check if the header contains SharpCap-specific keywords or values
        software = header.get('SWCREATE', '')
        return software is not None and 'SharpCap' in software

    def _get_image_type(self, header: Header) -> Optional[str]:
        return _upper(header.get('IMAGETYP'))

    def _get_exposure(self, header: Header) -> Optional[float]:
        return _float(header.get('EXPTIME'))


class SGPHandler(FitsHeaderHandler):
    """Handler for FITS files created by Sequence Generator Pro (SGP)."""

    def can_handle(self, header: Header) -> bool:
        # Check if the header contains SGP-specific keywords or values
        software = header.get('CREATOR', '')
        return software is not None and 'Sequence Generator Pro' in software


class NINAHandler(FitsHeaderHandler):
    """Handler for FITS files created by N.I.N.A. (Nighttime Imaging 'N' Astronomy)."""

    def can_handle(self, header: Header) -> bool:
        # Check if the header contains NINA-specific keywords or values
        software = header.get('SWCREATE', '')
        return software is not None and 'N.I.N.A.' in software


class GenericHandler(FitsHeaderHandler):
    """Generic handler for FITS files from unknown sources."""

    def can_handle(self, header: Header) -> bool:
        # This is a fallback handler that can handle any FITS header
        return True

    def _get_image_type(self, header: Header) -> Optional[str]:
        return _type(header.get('IMAGETYP', header.get('OBSTYPE')))

    def _get_filter(self, header: Header) -> Optional[str]:
        return header.get('FILTER', header.get('FILTNAME'))

    def _get_exposure(self, header: Header) -> Optional[float]:
        return _float(header.get('EXPTIME', header.get('EXPOSURE', header.get('EXP'))))

    def _get_gain(self, header: Header) -> Optional[int]:
        return _int(header.get('GAIN'))

    def _get_binning(self, header: Header) -> Optional[int]:
        bin = _int(header.get('XBINNING'))
        if bin is not None:
            return bin
        combined = header.get('BINNING', 1)
        if combined and '*' in str(combined):
            return int(combined.split('*')[0])
        return None

    def _get_set_temp(self, header: Header) -> Optional[float]:
        return _float(header.get('SET-TEMP', header.get('CCDTEMP')))


def normalize_fits_header(file: File, header: Header, status_reporter: StatusReporter = None) -> Image | None:
    """
    Normalize a FITS file header and return a processed Image object or None.

    This function attempts to process the provided FITS header using a series
    of predefined handlers. Each handler is checked in sequence to determine
    if it can process the header. If a suitable handler is found and it
    can successfully process the FITS file and header, the corresponding
    processed Image object is returned. If no appropriate handler is
    found or processing fails, the function returns None.

    :param file: The FITS file to be processed.
    :type file: File
    :param header: The header from the FITS file, as an astropy object.
    :type header: Header
    :return: The processed Image object if successful, otherwise None.
    :rtype: Image | None
    """
    # List of handlers to try, in order
    handlers = [
        SharpCapHandler(),
        SGPHandler(),
        NINAHandler(),
        GenericHandler()  # Fallback handler
    ]

    for handler in handlers:
        if handler.can_handle(header):
            try:
                image = handler.process(file, header)
            except Exception as e:
                logging.error(f"Error processing FITS header: {str(e)} for file {file.name}", exc_info=True)
                if status_reporter:
                    status_reporter.update_status(f"Error processing FITS header: {str(e)} for file {file.name}")
                return None
            if image:
                image.file = file
                return image

    return None

from abc import ABC, abstractmethod
from typing import Optional

from astropy.io.fits import Header

from astrofilemanager.models import File, Image


def _upper(value: str):
    return None if value is None else value.upper()


def _int(value):
    return None if value is None else int(value)


def _float(value):
    return None if value is None else float(value)


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
        try:
            # Extract common fields from the header
            image_type = self._get_image_type(header)
            filter_name = self._get_filter(header)
            camera = self._get_camera(header)
            exposure = self._get_exposure(header)
            gain = self._get_gain(header)
            binning = self._get_binning(header)
            set_temp = self._get_set_temp(header)
            telescope = self._get_telescope(header)
            object_name = self._get_object_name(header)

            # Create and return an Image object
            return Image(
                file=file, 
                image_type=image_type, 
                filter=filter_name, 
                exposure=exposure, 
                gain=gain,
                binning=binning, 
                set_temp=set_temp, 
                camera=camera,
                telescope=telescope,
                object_name=object_name
            )
        except Exception as e:
            print(f"Error processing header: {str(e)}")
            return None

    def _get_image_type(self, header: Header) -> Optional[str]:
        return header.get('IMAGETYP')

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
        return header.get('IMAGETYP', header.get('OBSTYPE'))

    def _get_filter(self, header: Header) -> Optional[str]:
        return header.get('FILTER', header.get('FILTNAME'))

    def _get_exposure(self, header: Header) -> Optional[float]:
        return _float(header.get('EXPTIME', header.get('EXPOSURE')))

    def _get_gain(self, header: Header) -> Optional[int]:
        return _int(header.get('GAIN', header.get('EGAIN')))

    def _get_binning(self, header: Header) -> Optional[int]:
        return _int(header.get('XBINNING', header.get('BINNING', 1)))

    def _get_set_temp(self, header: Header) -> Optional[float]:
        return _float(header.get('SET-TEMP', header.get('CCDTEMP')))


def normalize_fits_header(file: File, header: Header) -> Image | None:
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
            image = handler.process(file, header)
            image.file = file
            if image:
                return image

    return None

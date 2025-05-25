from abc import ABC, abstractmethod
from typing import Optional

from astropy.io.fits import Header

from astrofilemanager.models import File, Image


class FitsHeaderHandler(ABC):
    """
    Abstract base class for FITS header handlers.
    Each handler is responsible for processing FITS headers from a specific program.
    """

    @abstractmethod
    def can_handle(self, header: Header) -> bool:
        """
        Check if this handler can process the given FITS header.

        Args:
            header: The raw FITS header as a string

        Returns:
            bool: True if this handler can process the header, False otherwise
        """
        pass

    @abstractmethod
    def process(self, file: File, header: Header) -> Optional[Image]:
        """
        Process the FITS header and create an Image object.

        Args:
            file: The File object associated with the FITS header
            header: The raw FITS header as a string

        Returns:
            Optional[Image]: An Image object if processing was successful, None otherwise
        """
        pass


class SharpCapHandler(FitsHeaderHandler):
    """Handler for FITS files created by SharpCap."""

    def can_handle(self, header: Header) -> bool:
        # Check if the header contains SharpCap-specific keywords or values
        software = header.get('SWCREATE', '')
        return software is not None and 'SharpCap' in software

    def process(self, file: File, header: Header) -> Optional[Image]:
        try:
            # Extract relevant information from the header
            image_type = header.get('IMAGETYP', 'LIGHT')
            filter_name = header.get('FILTER', '')
            exposure = float(header.get('EXPTIME', 0))
            gain = int(float(header.get('GAIN', 0)))
            binning = int(float(header.get('XBINNING', 1)))
            set_temp = float(header.get('SET-TEMP', 0))

            # Create and return an Image object
            return Image(
                file=file,
                image_type=image_type,
                filter=filter_name,
                exposure=exposure,
                gain=gain,
                binning=binning,
                setTemp=set_temp
            )
        except Exception as e:
            print(f"Error processing SharpCap header: {str(e)}")
            return None


class SGPHandler(FitsHeaderHandler):
    """Handler for FITS files created by Sequence Generator Pro (SGP)."""

    def can_handle(self, header: Header) -> bool:
        # Check if the header contains SGP-specific keywords or values
        software = header.get('CREATOR', '')
        return software is not None and 'Sequence Generator Pro' in software

    def process(self, file: File, header: Header) -> Optional[Image]:
        try:
            # Extract relevant information from the header
            image_type = header.get('IMAGETYP', 'LIGHT')
            filter_name = header.get('FILTER', '')
            exposure = float(header.get('EXPOSURE', 0))
            gain = int(float(header.get('GAIN', 0)))
            binning = int(float(header.get('XBINNING', 1)))
            set_temp = float(header.get('SET-TEMP', 0))

            # Create and return an Image object
            return Image(
                file=file,
                image_type=image_type,
                filter=filter_name,
                exposure=exposure,
                gain=gain,
                binning=binning,
                setTemp=set_temp
            )
        except Exception as e:
            print(f"Error processing SGP header: {str(e)}")
            return None


class NINAHandler(FitsHeaderHandler):
    """Handler for FITS files created by N.I.N.A. (Nighttime Imaging 'N' Astronomy)."""

    def can_handle(self, header: Header) -> bool:
        # Check if the header contains NINA-specific keywords or values
        software = header.get('SWCREATE', '')
        return software is not None and 'N.I.N.A.' in software

    def process(self, file: File, header: Header) -> Optional[Image]:
        try:
            # Extract relevant information from the header
            image_type = header.get('IMAGETYP', 'LIGHT')
            filter_name = header.get('FILTER', '')
            exposure = float(header.get('EXPOSURE', 0))
            gain = int(float(header.get('GAIN', 0)))
            binning = int(float(header.get('XBINNING', 1)))
            set_temp = float(header.get('SET-TEMP', 0))

            # Create and return an Image object
            return Image(
                file=file,
                image_type=image_type,
                filter=filter_name,
                exposure=exposure,
                gain=gain,
                binning=binning,
                setTemp=set_temp
            )
        except Exception as e:
            print(f"Error processing NINA header: {str(e)}")
            return None


class GenericHandler(FitsHeaderHandler):
    """Generic handler for FITS files from unknown sources."""

    def can_handle(self, header: Header) -> bool:
        # This is a fallback handler that can handle any FITS header
        return True

    def process(self, file: File, header: Header) -> Optional[Image]:
        try:
            # Try to extract common FITS keywords
            image_type = (
                    header.get('IMAGETYP', 
                    header.get('OBSTYPE', 'LIGHT'))
            )

            filter_name = (
                    header.get('FILTER', 
                    header.get('FILTNAME', ''))
            )

            exposure = float(
                header.get('EXPTIME', 
                header.get('EXPOSURE', 0))
            )

            gain = int(float(
                header.get('GAIN', 
                header.get('EGAIN', 0))
            ))

            binning = int(float(
                header.get('XBINNING', 
                header.get('BINNING', 1))
            ))

            set_temp = float(
                header.get('SET-TEMP', 
                header.get('CCDTEMP', 0))
            )

            # Create and return an Image object
            return Image(
                file=file,
                image_type=image_type,
                filter=filter_name,
                exposure=exposure,
                gain=gain,
                binning=binning,
                setTemp=set_temp
            )
        except Exception as e:
            print(f"Error processing generic header: {str(e)}")
            return None


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

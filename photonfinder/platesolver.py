import configparser
import logging
import shutil
import subprocess
import tempfile
import typing
from abc import ABCMeta, abstractmethod
from enum import Enum
from pathlib import Path

from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.io.fits import Header
from PIL import Image
import numpy as np
from astroquery.astrometry_net import AstrometryNet
from xisf import XISF

from photonfinder.core import get_default_astap_path
from photonfinder.filesystem import fopen, Importer, header_from_xisf_dict
from photonfinder.fits_handlers import hp


class SolverType(Enum):
    ASTAP = 1
    ASTROMETRY_NET = 2


class SolverBase(metaclass=ABCMeta):
    keep_headers = {
        'CRPIX1', 'CRPIX2', 'CRVAL1', 'CRVAL2', 'CDELT1', 'CDELT2', 'CROTA1', 'CROTA2', 'CD1_1', 'CD1_2',
        'CD2_1', 'CD2_2', 'CUNIT1', 'CUNIT2', 'NAXIS1', 'NAXIS2', 'CTYPE1', 'CTYPE2'
    }

    def __init__(self):
        self.tmp_dir = None  # Initialize to None

    def __enter__(self):
        self.tmp_dir = tempfile.mkdtemp()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self.tmp_dir:
                shutil.rmtree(self.tmp_dir)
        except Exception as e:
            logging.error(str(e), exc_info=True)

    @abstractmethod
    def solve(self, image_path, image: Image = None):
        pass

    @staticmethod
    def extract_wcs_cards(header):
        cards_filtered = list(filter(lambda card: card.keyword in SolverBase.keep_headers, header.cards))
        result_header = Header(cards_filtered)
        result_header['NAXIS'] = 2
        return result_header

    def _create_temp_fits(self, input_image) -> Path:
        temp_image = Path(self.tmp_dir) / (input_image.name + ".fit")
        if Importer.is_fits_by_name(str(input_image)):
            with fopen(input_image) as source_file:
                with fits.open(source_file) as source_hdu:
                    main_image_data = source_hdu[0].data
                    main_header = source_hdu[0].header
                    data2d = select_first_channel(main_image_data)
                    header2d = Header(main_header.cards, copy=True)
                    header2d['NAXIS'] = 2
                    header2d.remove('NAXIS3', ignore_missing=True)
                    hdu = fits.PrimaryHDU(data=data2d, header=header2d)
                    hdu.writeto(temp_image, overwrite=False, output_verify='silentfix')
            return temp_image
        elif Importer.is_xisf_by_name(str(input_image)):
            xisf = XISF(input_image)
            metas = xisf.get_images_metadata()
            for i, meta in enumerate(metas):
                if "FITSKeywords" in meta:
                    main_header = header_from_xisf_dict(meta["FITSKeywords"])
                    main_header.remove("COMMENT", ignore_missing=True, remove_all=True)
                    main_header.remove("HISTORY", ignore_missing=True, remove_all=True)
                    main_header['NAXIS'] = 2
                    main_header.remove('NAXIS3', ignore_missing=True)
                    main_image_data = select_first_channel(xisf.read_image(i, 'channels_first'))
                    hdu = fits.PrimaryHDU(data=main_image_data, header=main_header)
                    hdu.writeto(temp_image, overwrite=False, output_verify='silentfix')
                    return temp_image

            raise SolverError("No suitable image with FITS keywords found in XISF file: " + str(input_image))
        else:
            raise SolverError("Unknown file type: " + str(input_image) + ". Only FITS and XISF files are supported.")


class ASTAPSolver(SolverBase):
    tmp_dir: str | None
    _exe: str
    _log: bool

    def __init__(self, exe=get_default_astap_path(), fallback_fov: float = 1.0):
        super().__init__()
        self._exe = exe
        self._log = True
        self.fallback_fov = fallback_fov

    def _solve(self, tmp_image_file: Path, hint: typing.Dict[str, str] = None) -> Header:

        wcs = tmp_image_file.with_suffix(".wcs")
        ini = tmp_image_file.with_suffix(".ini")
        log = tmp_image_file.with_suffix(".log")

        params = [self._exe, "-f", str(tmp_image_file), "-update",
                  "-platesolve"]  # update, since this is a temp copy anyway
        options = {"-r": "180", "-s": "100", "-z": "2"}
        if hint is not None:
            options.update(hint)
        params.extend([item for k in options for item in (k, options[k])])
        if self._log:
            params.append("-log")

        subprocess.run(params, capture_output=True, text=True)
        if not wcs.exists() and ini.exists():
            self._raise_error(ini, log, tmp_image_file)

        with fits.open(tmp_image_file) as hdul:
            header = hdul[0].header
            if not header.get('PLTSOLVD'):
                raise SolverError("Platesolver failed to solve image " + str(tmp_image_file))
            result_header = SolverBase.extract_wcs_cards(header)

        return result_header

    @staticmethod
    def is_pre_solved(header):
        return ASTAPSolver.keep_headers.issubset(header.keys()) and has_valid_scale(header)

    def solve(self, image_path: Path, image: Image = None) -> Header:
        if not image_path.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        if not self._exe:
            raise FileNotFoundError("ASTAP executable not found")
        if not self.tmp_dir:
            raise FileNotFoundError("Temporary directory not found, use with statement to create one. ")
        temp_image = self._create_temp_fits(image_path)
        hint = self.extract_hint(fits.getheader(temp_image), image)
        return self._solve(temp_image, hint)

    @staticmethod
    def create_hint(ra, dec, fov=None) -> typing.Dict[str, str]:
        hint = dict()
        if ra and dec:
            ra_str = str(float(ra) / 15)
            spd_str = str(90 + float(dec))
            hint["-ra"] = ra_str
            hint["-spd"] = spd_str
        if fov:
            hint["-fov"] = str(fov)
        return hint

    def extract_hint(self, header: Header, image: Image) -> typing.Dict[str, str]:
        if image and image.coord_ra:
            ra = image.coord_ra
            dec = image.coord_dec
        else:
            ra = header.get("RA") or header.get("CRVAL1")
            dec = header.get("DEC") or header.get("CRVAL2")
        scale = header.get("SCALE") or header.get("PIXSCALE")
        if scale is None:
            focal_len = header.get("FOCALLEN")
            pix_size = header.get("YPIXSZ")
            if focal_len and pix_size:
                scale = 206.265 * float(pix_size) / float(focal_len)
        height = header.get("NAXIS2")
        if scale is not None and height is not None:
            fov = int(height) * scale / 3600
        elif self.fallback_fov:
            fov = self.fallback_fov
        else:
            fov = None

        return ASTAPSolver.create_hint(ra, dec, fov)

    def _raise_error(self, ini_file, log_file, image_file):
        parser = configparser.ConfigParser()
        with open(ini_file) as stream:
            parser.read_string("[top]\n" + stream.read())
        plain_ini = parser["top"]
        if "ERROR" in plain_ini:
            raise SolverError(plain_ini["ERROR"])
        else:
            log = None
            if self._log:
                try:
                    with open(log_file) as logstream:
                        log = logstream.readlines()
                except:
                    pass
            raise SolverFailure("Failed to solve image " + str(image_file), log)


class AstrometryNetSolver(SolverBase):

    def __init__(self, api_key: str, force_image_upload: bool = False):
        super().__init__()
        if not api_key:
            raise FileNotFoundError("API key not found")
        self.api_key = api_key
        self.ast = AstrometryNet()
        self.ast.api_key = api_key
        self.force_image_upload = force_image_upload

    def solve(self, image_path, image: Image = None) -> Header:
        if not image_path.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        if not self.tmp_dir:
            raise FileNotFoundError("Temporary directory not found, use with statement to create one. ")
        temp_image = self._create_temp_fits(image_path)
        wcs_header: Header = self.ast.solve_from_image(temp_image, verbose=False, crpix_center=True,
                                                       force_image_upload=self.force_image_upload)
        result = SolverBase.extract_wcs_cards(wcs_header)
        original_header = fits.getheader(temp_image)
        result['NAXIS1'] = original_header['NAXIS1']
        result['NAXIS2'] = original_header['NAXIS2']
        return result


class SolverError(Exception):
    pass


class SolverFailure(Exception):
    def __init__(self, message, log) -> None:
        self.message = message
        self.log = log

    def __str__(self) -> str:
        return self.message


def get_image_center_coords(header):
    assert has_minimal_wcs(header)
    import astropy.units as u
    ra = header.get("CRVAL1")
    dec = header.get("CRVAL2")
    coords = SkyCoord(ra, dec, unit=(u.deg, u.deg), frame='icrs')
    healpix_index = int(hp.skycoord_to_healpix(coords))
    return ra, dec, healpix_index


def has_been_plate_solved(header):
    return has_minimal_wcs(header) and has_valid_scale(header) and has_rotation(header)


def has_minimal_wcs(header):
    required_keywords = ['CTYPE1', 'CTYPE2', 'CRVAL1', 'CRVAL2', 'CRPIX1', 'CRPIX2']
    return all(k in header for k in required_keywords)


def has_valid_scale(header):
    if all(k in header for k in ['CD1_1', 'CD2_2']):
        return header['CD1_1'] != 0 and header['CD2_2'] != 0
    if all(k in header for k in ['CDELT1', 'CDELT2']):
        return header['CDELT1'] != 0 and header['CDELT2'] != 0
    return False


def has_rotation(header):
    if all(k in header for k in ['CD1_1', 'CD2_2']):
        return header['CD1_1'] != 0 and header['CD2_2'] != 0
    if all(k in header for k in ['CROTA1', 'CROTA2']):
        return header['CROTA1'] != 0 and header['CROTA2'] != 0
    return False


def select_first_channel(data):
    """
    Reduce a 3D ndarray to 2D by selecting the first channel.
    Handles both 'channels_first' (Z, X, Y) and 'channels_last' (X, Y, Z) formats.

    Args:
        data: numpy ndarray of shape (X, Y, Z), (Z, X, Y), or (X, Y)

    Returns:
        numpy ndarray of shape (X, Y)
    """
    if len(data.shape) == 2:
        # Already 2D, return as is
        return data
    elif len(data.shape) == 3:
        # Determine if it's channels_first or channels_last
        # Assume channels_first if the first dimension is smaller (common for RGB = 3 channels)
        if data.shape[0] <= data.shape[2]:
            # Channels first format (Z, X, Y) -> select first channel
            return data[0, :, :]
        else:
            # Channels last format (X, Y, Z) -> select first channel
            return data[:, :, 0]
    else:
        raise ValueError(f"Unsupported array shape: {data.shape}. Expected 2D or 3D array.")


def _create_temp_jpeg(input_image, output_dir: Path) -> Path:
    """
    Creates a JPEG file from a XISF or FITS file.

    Args:
        input_image: Path to the XISF or FITS file
        output_dir: Directory where the JPEG file will be saved

    Returns:
        Path to the created JPEG file
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    jpeg_path = output_dir / (Path(input_image).stem + ".jpg")

    if Importer.is_fits_by_name(str(input_image)):
        with fopen(input_image) as source_file:
            with fits.open(source_file) as hdul:
                image_data = hdul[0].data
                # Convert to 8-bit for JPEG
                if image_data.dtype != np.uint8:
                    # Normalize to 0-255 range
                    image_data = image_data.astype(np.float32)
                    if image_data.min() != image_data.max():  # Avoid division by zero
                        image_data = 255 * (image_data - image_data.min()) / (image_data.max() - image_data.min())
                    image_data = image_data.astype(np.uint8)

                # Handle different dimensions
                if len(image_data.shape) == 2:  # Grayscale
                    img = Image.fromarray(image_data, mode='L')
                elif len(image_data.shape) == 3 and image_data.shape[0] == 3:  # RGB with channels first
                    img = Image.fromarray(np.transpose(image_data, (1, 2, 0)), mode='RGB')
                elif len(image_data.shape) == 3 and image_data.shape[2] == 3:  # RGB with channels last
                    img = Image.fromarray(image_data, mode='RGB')
                else:
                    raise SolverError(f"Unsupported image data shape: {image_data.shape}")

                img.save(jpeg_path, format='JPEG', quality=95)

    elif Importer.is_xisf_by_name(str(input_image)):
        xisf = XISF(input_image)
        metas = xisf.get_images_metadata()
        for i, meta in enumerate(metas):
            if "FITSKeywords" in meta:
                main_image_data = xisf.read_image(i, 'channels_first')

                # Convert to 8-bit for JPEG
                if main_image_data.dtype != np.uint8:
                    # Normalize to 0-255 range
                    main_image_data = main_image_data.astype(np.float32)
                    if main_image_data.min() != main_image_data.max():  # Avoid division by zero
                        main_image_data = 255 * (main_image_data - main_image_data.min()) / (
                                main_image_data.max() - main_image_data.min())
                    main_image_data = main_image_data.astype(np.uint8)

                # Handle different dimensions
                if len(main_image_data.shape) == 2:  # Grayscale
                    img = Image.fromarray(main_image_data, mode='L')
                elif len(main_image_data.shape) == 3 and main_image_data.shape[0] == 3:  # RGB with channels first
                    img = Image.fromarray(np.transpose(main_image_data, (1, 2, 0)), mode='RGB')
                elif len(main_image_data.shape) == 3 and main_image_data.shape[2] == 3:  # RGB with channels last
                    img = Image.fromarray(main_image_data, mode='RGB')
                else:
                    raise SolverError(f"Unsupported image data shape: {main_image_data.shape}")

                img.save(jpeg_path, format='JPEG', quality=95)
                return jpeg_path

        raise SolverError("No suitable image with FITS keywords found in XISF file: " + str(input_image))
    else:
        raise SolverError("Unknown file type: " + str(input_image) + ". Only FITS and XISF files are supported.")

    return jpeg_path

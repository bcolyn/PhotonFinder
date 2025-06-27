import configparser
import shutil
import subprocess
import tempfile
import typing
from pathlib import Path

from astropy.io import fits
from astropy.io.fits import Header
from xisf import XISF

from photonfinder.filesystem import fopen, Importer, header_from_xisf_dict


def get_default_astap_path():
    if Path("C:/Program Files/astap/astap.exe").exists():
        return "C:/Program Files/astap/astap.exe"
    else:  # else, assume it's on the PATH
        return "astap"


class ASTAPSolver:
    tmp_dir: str | None
    _exe: str
    _log: bool
    keep_headers = {
        'CRPIX1', 'CRPIX2', 'CRVAL1', 'CRVAL2', 'CDELT1', 'CDELT2', 'CROTA1', 'CROTA2', 'CD1_1', 'CD1_2',
        'CD2_1', 'CD2_2', 'CUNIT1', 'CUNIT2', 'NAXIS1', 'NAXIS2', 'CTYPE1', 'CTYPE2'
    }

    def __init__(self, exe=get_default_astap_path()):
        self._exe = exe
        self._log = True
        self.tmp_dir = None  # Initialize to None

    def _create_temp_fits(self, input_image) -> Path:
        temp_image = Path(self.tmp_dir) / (input_image.name + ".fit")
        if Importer.is_fits_by_name(str(input_image)):
            with fopen(input_image) as source_file:
                with open(temp_image, "wb") as destination_file:
                    shutil.copyfileobj(source_file, destination_file)
            return temp_image
        elif Importer.is_xisf_by_name(str(input_image)):
            xisf = XISF(input_image)
            metas = xisf.get_images_metadata()
            for i, meta in enumerate(metas):
                if "FITSKeywords" in meta:
                    main_header = header_from_xisf_dict(meta["FITSKeywords"])
                    main_header.remove("COMMENT", ignore_missing=True, remove_all=True)
                    main_header.remove("HISTORY", ignore_missing=True, remove_all=True)
                    main_image_data = xisf.read_image(i, 'channels_first')
                    hdu = fits.PrimaryHDU(data=main_image_data, header=main_header)
                    hdu.writeto(temp_image, overwrite=False)
                    return temp_image

            raise SolverError("No suitable image with FITS keywords found in XISF file: " + str(input_image))
        else:
            raise SolverError("Unknown file type: " + str(input_image) + ". Only FITS and XISF files are supported.")

    def _solve(self, tmp_image_file: Path, hint: typing.Dict[str, str] = None) -> Header:

        wcs = tmp_image_file.with_suffix(".wcs")
        ini = tmp_image_file.with_suffix(".ini")
        log = tmp_image_file.with_suffix(".log")

        params = [self._exe, "-f", str(tmp_image_file), "-update", "-platesolve"] # update, since this is a temp copy anyway
        options = {"-r": "180", "-s": "100"}
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
            result_header = ASTAPSolver.extract_wcs_cards(header)

        return result_header

    @staticmethod
    def extract_wcs_cards(header):
        cards_filtered = list(filter(lambda card: card.keyword in ASTAPSolver.keep_headers, header.cards))
        result_header = Header(cards_filtered)
        result_header['NAXIS'] = 2
        return result_header

    @staticmethod
    def is_pre_solved(header):
        return ASTAPSolver.keep_headers.issubset(header.keys()) and has_valid_scale(header)

    def solve(self, image_path: Path) -> Header:
        if not image_path.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        if not self._exe:
            raise FileNotFoundError("ASTAP executable not found")

        temp_image = self._create_temp_fits(image_path)
        hint = ASTAPSolver.extract_hint(fits.getheader(temp_image))
        return self._solve(temp_image, hint)

    def __enter__(self):
        self.tmp_dir = tempfile.mkdtemp()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.tmp_dir:
            shutil.rmtree(self.tmp_dir)

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

    @staticmethod
    def extract_hint(header: Header) -> typing.Dict[str, str]:
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


class SolverError(Exception):
    pass


class SolverFailure(Exception):
    def __init__(self, message, log) -> None:
        self.message = message
        self.log = log

    def __str__(self) -> str:
        return self.message


def solve_image_astap(image_path) -> str:
    with ASTAPSolver() as solver:
        header = solver.solve(image_path)
    return header.tostring()


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

import logging
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import typing
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from astropy.io import fits
from astropy.io.fits import Header
from astropy.wcs import WCS
from PIL import Image
import numpy as np
from astroquery.astrometry_net.core import AstrometryNetClass
from astroquery.exceptions import TimeoutError as AstroqueryTimeoutError
from astroquery.utils import url_helpers
from xisf import XISF

from photonfinder import plate_solution
from photonfinder.core import get_default_astap_path
from photonfinder.image_processing import is_linear
from photonfinder.filesystem import fopen, Importer, header_from_xisf_dict, repair_header


class SolverType(Enum):
    ASTAP = 1
    ASTROMETRY_NET = 2
    SOLVE_FIELD = 3


@dataclass
class SolverHint:
    ra: float | None = None    # degrees
    dec: float | None = None   # degrees
    scale: float | None = None  # arcsec/px
    mode: str = 'fallback'     # 'fallback' or 'override'


_SOLVER_TYPE_ORIGIN = {
    SolverType.ASTAP:          "ASTAP",
    SolverType.ASTROMETRY_NET: "ASTROMETRY.NET",
    SolverType.SOLVE_FIELD: "SOLVE-FIELD",
}


def _first_header_value(header, *keys):
    """The first of these cards that has a value. 0 is a value, a blank card isn't."""
    for key in keys:
        value = header.get(key)
        if value is not None and value != "":
            return value
    return None


def file_position(header, image) -> tuple:
    """The (ra, dec) a file already has: from its Image row, else its header, else (None, None)."""
    if image and image.coord_ra is not None and image.coord_dec is not None:
        return image.coord_ra, image.coord_dec
    if header is None:
        return None, None
    return _first_header_value(header, "RA", "CRVAL1"), _first_header_value(header, "DEC", "CRVAL2")


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

    @property
    @abstractmethod
    def solver_type(self) -> SolverType:
        pass

    @property
    def wcs_origin(self) -> str:
        return _SOLVER_TYPE_ORIGIN[self.solver_type]

    @abstractmethod
    def solve(self, image_path, image=None, hint: SolverHint = None,
              output_callback: typing.Callable[[str], None] = None, file_wcs=None,
              cancelled: typing.Callable[[], bool] = None):
        """Solve one image. ``cancelled`` is polled while the solver runs; once it returns
        True the run is stopped and :class:`SolverCancelled` is raised."""

    @staticmethod
    def _merge_wcs_into_header(header: Header, file_wcs) -> None:
        """Overwrite WCS cards in *header* with those stored in *file_wcs*."""
        for card in plate_solution.decode(file_wcs.wcs).cards:
            if card.keyword:
                header[card.keyword] = (card.value, card.comment)

    def _create_temp_fits(self, input_image, file_wcs=None) -> Path:
        temp_image = Path(self.tmp_dir) / (input_image.name + ".fit")
        if Importer.is_fits_by_name(str(input_image)):
            with fopen(input_image) as source_file:
                with fits.open(source_file) as source_hdu:
                    main_image_data = source_hdu[0].data
                    main_header = source_hdu[0].header
                    repair_header(main_header)
                    data2d = select_first_channel(main_image_data)
                    header2d = Header(main_header.cards, copy=True)
                    header2d['NAXIS'] = 2
                    header2d.remove('NAXIS3', ignore_missing=True)
                    if file_wcs is not None:
                        self._merge_wcs_into_header(header2d, file_wcs)
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
                    if file_wcs is not None:
                        self._merge_wcs_into_header(main_header, file_wcs)
                    main_image_data = select_first_channel(xisf.read_image(i, 'channels_first'))
                    hdu = fits.PrimaryHDU(data=main_image_data, header=main_header)
                    hdu.writeto(temp_image, overwrite=False, output_verify='silentfix')
                    return temp_image

            raise SolverError("No suitable image with FITS keywords found in XISF file: " + str(input_image))
        else:
            raise SolverError("Unknown file type: " + str(input_image) + ". Only FITS and XISF files are supported.")


ASTAP_TIMEOUT_SECONDS = 600  # far above a normal solve, which takes seconds even when blind
_CANCEL_POLL_SECONDS = 0.25


def _never_cancelled() -> bool:
    return False


def _wait_or_kill(proc: subprocess.Popen, timeout: float, cancelled: typing.Callable[[], bool]) -> str | None:
    """Wait for ``proc`` to exit. Returns None when it did, else "timeout" or "cancelled"
    once the process has been killed and has exited, so its files are no longer in use."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            proc.wait(timeout=min(_CANCEL_POLL_SECONDS, max(deadline - time.monotonic(), 0)))
            return None
        except subprocess.TimeoutExpired:
            pass
        if cancelled():
            reason = "cancelled"
        elif time.monotonic() >= deadline:
            reason = "timeout"
        else:
            continue
        proc.kill()
        proc.wait()
        return reason


class ASTAPSolver(SolverBase):
    tmp_dir: str | None
    _exe: str
    _log: bool

    @property
    def solver_type(self) -> SolverType:
        return SolverType.ASTAP

    def __init__(self, exe=get_default_astap_path(), timeout: int = ASTAP_TIMEOUT_SECONDS):
        super().__init__()
        self._exe = exe
        self._log = True
        self._timeout = timeout

    def _solve(self, tmp_image_file: Path, hint: typing.Dict[str, str] = None,
               cancelled: typing.Callable[[], bool] = _never_cancelled) -> Header:

        wcs = tmp_image_file.with_suffix(".wcs")
        ini = tmp_image_file.with_suffix(".ini")
        log = tmp_image_file.with_suffix(".log")

        for previous_output in (wcs, ini, log):
            previous_output.unlink(missing_ok=True)

        returncode = self._run_astap(tmp_image_file, hint, cancelled)

        # Only ASTAP's own output counts. The image header can't say: a file that was solved
        # before keeps its old solution there when this run fails.
        ini_values = self._read_ini(ini)
        if not wcs.exists() or ini_values.get("PLTSOLVD") != "T":
            self._raise_failure(ini_values, log, tmp_image_file, returncode)

        with fits.open(tmp_image_file) as hdul:
            header = hdul[0].header
            if not header.get('PLTSOLVD'):
                raise SolverError("Platesolver failed to solve image " + str(tmp_image_file))
            result_header = extract_wcs_cards(header)

        return result_header

    def _run_astap(self, tmp_image_file: Path, hint: typing.Dict[str, str] = None,
                   cancelled: typing.Callable[[], bool] = _never_cancelled) -> int:
        """Execute ASTAP with the given parameters and return its exit code. Its output goes
        to the files it writes next to the image, so stdout and stderr are discarded."""
        params = [self._exe, "-f", str(tmp_image_file), "-update", "-platesolve"]
        options = {"--speed": "slow", "-z": "1"}
        if hint is not None:
            options.update(hint)
        params.extend([item for k in options for item in (k, options[k])])
        if self._log:
            params.append("-log")

        proc = subprocess.Popen(params, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        stopped = _wait_or_kill(proc, self._timeout, cancelled)
        if stopped == "cancelled":
            raise SolverCancelled(f"ASTAP stopped on {tmp_image_file.name}")
        if stopped == "timeout":
            raise SolverFailure(f"ASTAP timed out after {self._timeout}s on {tmp_image_file.name}", None)
        return proc.returncode

    @staticmethod
    def is_pre_solved(header):
        return ASTAPSolver.keep_headers.issubset(header.keys()) and has_valid_scale(header)

    def solve(self, image_path: Path, image=None, hint: SolverHint = None,
              output_callback: typing.Callable[[str], None] = None, file_wcs=None,
              cancelled: typing.Callable[[], bool] = None) -> Header:
        if not image_path.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        if not self._exe:
            raise FileNotFoundError("ASTAP executable not found")
        if not self.tmp_dir:
            raise FileNotFoundError("Temporary directory not found, use with statement to create one. ")
        temp_image = self._create_temp_fits(image_path, file_wcs)
        header = fits.getheader(temp_image)
        astap_hint = self.extract_hint(header, image)
        file_has_position = '-ra' in astap_hint  # RA/DEC came from the file itself

        if hint:
            naxis2 = header.get('NAXIS2')
            if hint.mode == 'override':
                if hint.ra is not None and hint.dec is not None:
                    astap_hint['-ra'] = str(float(hint.ra) / 15)
                    astap_hint['-spd'] = str(90 + float(hint.dec))
                if hint.scale is not None and naxis2:
                    astap_hint['-fov'] = str(int(naxis2) * hint.scale / 3600)
            else:  # fallback
                if hint.ra is not None and hint.dec is not None and '-ra' not in astap_hint:
                    astap_hint['-ra'] = str(float(hint.ra) / 15)
                    astap_hint['-spd'] = str(90 + float(hint.dec))
                if hint.scale is not None and '-fov' not in astap_hint and naxis2:
                    astap_hint['-fov'] = str(int(naxis2) * hint.scale / 3600)

            # in practice, downscaling seems to often cause wider-field images (5-6 deg) to fail
            # if naxis2 > 2000:
            #     astap_hint['-z'] = "2"
            # else:
            #     astap_hint['-z'] = "0"

        # Reduce search radius when we have a reliable position; full-sky only when blind
        user_override_position = hint and hint.mode == 'override' and hint.ra is not None
        astap_hint.setdefault('-r', '30' if (file_has_position or user_override_position) else '180')

        return self._solve(temp_image, astap_hint, cancelled or _never_cancelled)

    @staticmethod
    def create_hint(ra, dec, fov=None) -> typing.Dict[str, str]:
        hint = dict()
        if ra is not None and dec is not None:
            ra_str = str(float(ra) / 15)
            spd_str = str(90 + float(dec))
            hint["-ra"] = ra_str
            hint["-spd"] = spd_str
        if fov:
            hint["-fov"] = str(fov)
        return hint

    def extract_hint(self, header: Header, image: Image) -> typing.Dict[str, str]:
        ra, dec = file_position(header, image)
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

    @staticmethod
    def _read_ini(ini_file: Path) -> dict[str, str]:
        """ASTAP's KEY=value lines. Its CMDLINE holds arbitrary paths and keys can repeat, which
        an ini parser would refuse, and only the first = separates."""
        if not ini_file.exists():
            return {}
        values = {}
        for line in ini_file.read_text(encoding="utf-8", errors="replace").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip().upper()] = value.strip()
        return values

    def _raise_failure(self, ini_values: dict[str, str], log_file: Path, image_file: Path, returncode: int):
        if ini_values.get("ERROR"):
            raise SolverError(ini_values["ERROR"])
        log = None
        if self._log and log_file.exists():
            log = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if ini_values:
            raise SolverFailure("Failed to solve image " + str(image_file), log)
        raise SolverFailure(f"ASTAP gave no result for {image_file.name} (exit code {returncode})", log)


class _CancellableAstrometryNet(AstrometryNetClass):
    """astroquery's client, polling the submission itself so that it can stop between polls.

    astroquery's ``solve_from_*`` upload the job and then call ``monitor_submission``, which
    polls until the job ends. This replaces that loop with one that also checks ``cancelled``.
    A cancelled job is abandoned: nova.astrometry.net has no way to cancel one, so nothing
    more is sent for it."""

    POLL_SECONDS = 1.0

    def __init__(self):
        super().__init__()
        self.cancelled: typing.Callable[[], bool] = _never_cancelled

    def _sleep_unless_cancelled(self, seconds: float):
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            if self.cancelled():
                raise SolverCancelled("Astrometry.net job abandoned")
            time.sleep(min(left, _CANCEL_POLL_SECONDS))
        if self.cancelled():
            raise SolverCancelled("Astrometry.net job abandoned")

    def solve_by_upload(self, image_path: Path, *, solve_timeout: int, **settings):
        """Upload the image itself for nova to find the stars. astroquery's ``solve_from_image``
        only uploads when photutils is missing, and its argument to force it is deprecated."""
        self._validate_settings(settings)
        if self.cancelled():
            raise SolverCancelled("Astrometry.net upload not started")
        if self._session_id is None:
            self._login()
        settings['session'] = self._session_id
        url = url_helpers.join(self.API_URL, 'upload')
        with open(image_path, 'rb') as image:
            response = self._request('POST', url, data=self._construct_payload(settings), cache=False,
                                     files={'file': image})
        if response.status_code != 200:
            raise RuntimeError('Post of job failed')
        return self.monitor_submission(response.json()['subid'], solve_timeout=solve_timeout, verbose=False)

    def monitor_submission(self, submission_id, *, solve_timeout=AstrometryNetClass.TIMEOUT, verbose=True,
                           return_submission_id=False):
        """Same results as astroquery's: the WCS header on success, ``{}`` on failure, and
        astroquery's ``TimeoutError`` once ``solve_timeout`` has passed."""
        job_id = None
        status = ''
        start = time.monotonic()
        while True:
            self._sleep_unless_cancelled(self.POLL_SECONDS)
            if job_id is None:
                url = url_helpers.join(self.API_URL, 'submissions', str(submission_id))
                jobs = self._request('GET', url, cache=False).json()['jobs']
                if jobs:
                    job_id = jobs[0]
            if job_id:
                url = url_helpers.join(self.API_URL, 'jobs', str(job_id), 'info')
                status = self._request('GET', url, cache=False).json()['status']
            if status in ('success', 'failure'):
                break
            if time.monotonic() - start > solve_timeout:
                raise AstroqueryTimeoutError('Solve timed out without success or failure', submission_id)
        if status == 'success':
            wcs_url = url_helpers.join(self.URL, 'wcs_file', str(job_id))
            wcs = fits.Header.fromstring(self._request('GET', wcs_url).text)
        else:
            wcs = {}
        return (wcs, submission_id) if return_submission_id else wcs


class AstrometryNetSolver(SolverBase):

    @property
    def solver_type(self) -> SolverType:
        return SolverType.ASTROMETRY_NET

    def __init__(self, api_key: str, force_image_upload: bool = False):
        super().__init__()
        if not api_key:
            raise FileNotFoundError("API key not found")
        self.api_key = api_key
        self.ast = _CancellableAstrometryNet()
        self.ast.api_key = api_key
        self.force_image_upload = force_image_upload

    def solve(self, image_path, image=None, hint: SolverHint = None,
              output_callback: typing.Callable[[str], None] = None, file_wcs=None,
              cancelled: typing.Callable[[], bool] = None) -> Header:
        if not image_path.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        if not self.tmp_dir:
            raise FileNotFoundError("Temporary directory not found, use with statement to create one. ")
        self.ast.cancelled = cancelled or _never_cancelled
        temp_image = self._create_temp_fits(image_path, file_wcs)
        if self.force_image_upload:
            wcs_header = self._submit(image_path, lambda: self.ast.solve_by_upload(
                temp_image, crpix_center=True, solve_timeout=self.SOLVE_TIMEOUT_SECONDS,
            ))
            original_header = fits.getheader(temp_image)
            result = extract_wcs_cards(wcs_header)
            result['NAXIS1'] = original_header['NAXIS1']
            result['NAXIS2'] = original_header['NAXIS2']
        else:
            x, y, _flux, width, height = extract_sources(temp_image)
            if len(x) == 0:
                raise SolverError(f"No sources detected in {image_path.name}")
            wcs_header = self._submit(image_path, lambda: self.ast.solve_from_source_list(
                x, y, width, height,
                verbose=False, crpix_center=True, solve_timeout=self.SOLVE_TIMEOUT_SECONDS,
            ))
            result = extract_wcs_cards(wcs_header)
            result['NAXIS1'] = width
            result['NAXIS2'] = height
        return result

    SOLVE_TIMEOUT_SECONDS = 5 * 60

    def _submit(self, image_path: Path, submit: typing.Callable[[], Header]) -> Header:
        """Run an astroquery submission. Its two ways of not solving, an empty result when
        the job fails and a TimeoutError when it runs too long, become SolverFailure, so the
        backup solver gets its turn."""
        try:
            wcs_header = submit()
        except AstroqueryTimeoutError:
            raise SolverFailure(f"Astrometry.net timed out after {self.SOLVE_TIMEOUT_SECONDS}s "
                                f"on {image_path.name}", None) from None
        if not isinstance(wcs_header, Header):
            raise SolverFailure(f"Astrometry.net could not solve {image_path.name}", None)
        return wcs_header


def _decode_wsl_output(data: bytes) -> str:
    """Programs run in the distro write UTF-8; wsl.exe's own messages (no such distro, WSL not
    installed) are UTF-16, on stdout."""
    if b"\x00" in data:
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def _to_wsl_path(path: Path, distro: str = "") -> str:
    cmd = ["wsl"]
    if distro:
        cmd += ["-d", distro]
    cmd += ["wslpath", str(path).replace("\\", "/")]
    result = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
    output = _decode_wsl_output(result.stdout).strip()
    if result.returncode != 0 or not output:
        message = " ".join(part for part in (_decode_wsl_output(result.stderr).strip(), output) if part)
        raise SolverError(f"WSL could not translate {path}: {message or f'exit code {result.returncode}'}")
    return output


def _to_cygwin_path(path: Path, cygpath_exe: str = "", env: dict = None) -> str:
    """Convert a Windows path to a Cygwin Unix-style path using cygpath, with string fallback."""
    if cygpath_exe:
        result = subprocess.run(
            [cygpath_exe, "-u", str(path)],
            capture_output=True, text=True, timeout=30, env=env, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    s = str(path)
    if len(s) >= 2 and s[1] == ':':
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/")
        return f"/cygdrive/{drive}{rest}"
    return s.replace("\\", "/")


class SolveFieldSolver(SolverBase):
    """Plate solver using astrometry.net's solve-field, via WSL (exe_path empty) or Cygwin."""

    @property
    def solver_type(self) -> SolverType:
        return SolverType.SOLVE_FIELD

    def __init__(self, exe_path: str = "", timeout: int = 300, wsl_distro: str = ""):
        super().__init__()
        self._exe_path = exe_path  # empty → WSL mode; set → path to solve-field (Cygwin/native)
        self._timeout = timeout
        self._wsl_distro = wsl_distro  # WSL distro name; empty → default distro

    def _cygwin_root(self) -> Path:
        # solve-field lives at <cygwin_root>/lib/astrometry/bin/solve-field.exe
        return Path(self._exe_path).parents[3]

    def _cygpath_exe(self) -> str:
        """Return cygpath.exe derived from the Cygwin root."""
        candidate = self._cygwin_root() / "bin" / "cygpath.exe"
        return str(candidate) if candidate.exists() else ""

    def _cygwin_env(self) -> dict:
        """Return an env dict with Cygwin directories prepended to PATH."""
        import os
        root = self._cygwin_root()
        extra = [
            root / "lib" / "astrometry" / "bin",
            root / "usr" / "local" / "bin",
            root / "bin",
            root / "lib" / "lapack",
        ]
        prepend = os.pathsep.join(str(p) for p in extra if p.exists())
        env = os.environ.copy()
        env["PATH"] = prepend + os.pathsep + env.get("PATH", "")
        return env

    def _to_unix_path(self, path: Path) -> str:
        if self._exe_path:
            return _to_cygwin_path(path, self._cygpath_exe(), self._cygwin_env())
        return _to_wsl_path(path, self._wsl_distro)

    @staticmethod
    def _derive_scale(header: Header):
        """Return (scale_low, scale_high) in arcsec/px derived from header, or (None, None)."""
        scale = header.get("SCALE") or header.get("PIXSCALE")
        if scale is None:
            focal_len = header.get("FOCALLEN")
            pix_size = header.get("YPIXSZ")
            if focal_len and pix_size:
                scale = 206.265 * float(pix_size) / float(focal_len)
        if scale is None:
            return None, None
        scale = float(scale)
        return scale * 0.8, scale * 1.2

    def _run_solve_field(self, cmd: list, env: dict,
                         output_callback: typing.Callable[[str], None] = None,
                         cancelled: typing.Callable[[], bool] = _never_cancelled) -> list[str]:
        """Run solve-field, streaming output to callback and returning all output lines.

        Killing the process we started is enough to stop the whole run: through WSL, the
        distro ends solve-field and astrometry-engine once wsl.exe is gone, and Cygwin ends
        the children solve-field forks (both checked by hand on 2026-11-03)."""
        output_lines: list[str] = []
        line_queue: queue.Queue = queue.Queue()

        try:
            # solve-field writes UTF-8, through WSL and Cygwin alike. A stray byte must not end
            # the reader thread, which would silently stop the log.
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
            )
        except Exception as e:
            raise SolverFailure(f"Failed to launch solve-field: {e}", None) from e

        def _reader():
            try:
                for line in proc.stdout:
                    line_queue.put(line.rstrip('\r\n'))
            finally:
                line_queue.put(None)

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        deadline = time.monotonic() + self._timeout + 15
        timed_out = was_cancelled = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                proc.kill()
                break
            if cancelled():
                was_cancelled = True
                proc.kill()
                break
            try:
                line = line_queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if line is None:
                break
            output_lines.append(line)
            if output_callback and line.strip():
                output_callback(line)

        proc.wait()
        # The process can exit before the reader has passed on its last lines, the ones
        # that say why it didn't solve. Wait for the end of its output and keep those too.
        reader.join(5)
        while True:
            try:
                line = line_queue.get_nowait()
            except queue.Empty:
                break
            if line is None:
                continue
            output_lines.append(line)
            if output_callback and line.strip():
                output_callback(line)

        if was_cancelled:
            raise SolverCancelled("solve-field stopped")
        if timed_out:
            logging.error("solve-field timed out:\n%s", "\n".join(output_lines))
            raise SolverFailure(
                f"solve-field timed out after {self._timeout}s", output_lines or None
            )

        return output_lines

    def solve(self, image_path: Path, image=None, hint: SolverHint = None,
              output_callback: typing.Callable[[str], None] = None, file_wcs=None,
              cancelled: typing.Callable[[], bool] = None) -> Header:
        if not image_path.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        if not self.tmp_dir:
            raise FileNotFoundError("Temporary directory not found, use with statement to create one.")

        header = None
        try:
            with fits.open(image_path, memmap=False) as hdul:
                header = hdul[0].header
                repair_header(header)
        except Exception:
            pass

        if header is not None and ASTAPSolver.is_pre_solved(header):
            return extract_wcs_cards(header)

        temp_image = self._create_temp_fits(image_path, file_wcs)
        temp_unix = self._to_unix_path(temp_image)
        tmp_unix = self._to_unix_path(temp_image.parent)

        # Scale range
        scale_low = None
        scale_high = None
        if hint and hint.scale is not None:
            if hint.mode == 'override':
                scale_low = hint.scale * 0.9
                scale_high = hint.scale * 1.1
            else:  # fallback
                derived_low, derived_high = self._derive_scale(header) if header is not None else (None, None)
                if derived_low is not None:
                    scale_low, scale_high = derived_low, derived_high
                else:
                    scale_low = hint.scale * 0.5
                    scale_high = hint.scale * 2.0
        elif header is not None:
            scale_low, scale_high = self._derive_scale(header)

        if self._exe_path:
            cmd = [self._exe_path]
        else:
            cmd = ["wsl"]
            if self._wsl_distro:
                cmd += ["-d", self._wsl_distro]
            cmd.append("solve-field")
        cmd += [
            "--no-plots", "--overwrite", "--new-fits", "none",
            "--downsample", "2",
            "--dir", tmp_unix,
            "--cpulimit", str(self._timeout),
        ]
        if scale_low is not None:
            cmd += ["--scale-units", "arcsecperpix", "--scale-low", str(scale_low), "--scale-high", str(scale_high)]

        # RA/Dec hint
        hint_ra = None
        hint_dec = None
        if hint and hint.ra is not None and hint.dec is not None and hint.mode == 'override':
            hint_ra = hint.ra
            hint_dec = hint.dec
        else:
            hint_ra, hint_dec = file_position(header, image)
            if hint_ra is None and hint and hint.ra is not None:
                hint_ra = hint.ra
                hint_dec = hint.dec

        if hint_ra is not None and hint_dec is not None:
            cmd += ["--ra", str(hint_ra), "--dec", str(hint_dec), "--radius", "2.0"]
        else:
            try:
                with fits.open(temp_image, memmap=False) as hdul:
                    if is_linear(hdul[0].data):
                        cmd += ["--objs", "500"]
            except Exception:
                pass

        cmd.append(temp_unix)

        env = self._cygwin_env() if self._exe_path else None
        cmd_str = " ".join(cmd)
        logging.info("solve-field command: %s", cmd_str)
        if output_callback:
            output_callback(f"$ {cmd_str}")
        output_lines = self._run_solve_field(cmd, env, output_callback, cancelled or _never_cancelled)

        solved_marker = temp_image.with_suffix(".solved")
        if not solved_marker.exists():
            output = "\n".join(output_lines)
            logging.error("solve-field failed for %s:\n%s", image_path.name, output)
            if "child_info_fork::abort" in output:
                raise SolverFailure(
                    "Cygwin DLL address conflict — run 'rebaseall' in a Cygwin shell (with all Cygwin processes stopped) then retry.",
                    output_lines,
                )
            raise SolverFailure(f"solve-field could not solve {image_path.name}", output_lines or None)

        wcs_file = temp_image.with_suffix(".wcs")
        if not wcs_file.exists():
            raise SolverFailure(f"solve-field produced no .wcs file for {image_path.name}", None)

        with fits.open(wcs_file, memmap=False) as hdul:
            wcs_header = hdul[0].header

        result = extract_wcs_cards(wcs_header)
        original_header = header or {}
        naxis1 = (original_header.get("NAXIS1") if hasattr(original_header, "get") else None)
        naxis2 = (original_header.get("NAXIS2") if hasattr(original_header, "get") else None)
        if naxis1:
            result["NAXIS1"] = naxis1
        if naxis2:
            result["NAXIS2"] = naxis2
        return result


class SolverError(Exception):
    pass


class SolverFailure(Exception):
    def __init__(self, message, log) -> None:
        self.message = message
        self.log = log

    def __str__(self) -> str:
        return self.message


class SolverCancelled(Exception):
    """The run was stopped because the solve was cancelled. Not a failure: no backup solver
    or retry follows it."""


def get_image_center_coords(header):
    """(ra, dec, healpix, radius) of a solution; see plate_solution.image_centre."""
    assert has_minimal_wcs(header)
    return plate_solution.image_centre(header)


def extract_wcs_cards(header):
    cards_filtered = list(filter(lambda card: card.keyword in SolverBase.keep_headers, header.cards))
    result_header = Header(cards_filtered)
    result_header['NAXIS'] = 2
    return result_header


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


def flip_wcs_vertical(wcs: WCS, naxis2: int) -> WCS:
    """
    Correct a WCS from PixInsight's ImageSolver for its inverted-Y convention.

    PixInsight measures CRPIX from the top-left of the image (Y increases
    downward), whereas the FITS/astropy convention measures from the
    bottom-left (Y increases upward).  Apply this function to every WCS
    object read from an XISF file before passing it to downstream code.
    Do NOT apply it to WCS objects produced by ASTAP or solve-field.

    Sample CD-matrix values (4656 x 3520 image, east-left orientation):
        CD1_1 = -0.000168   CD1_2 = 0.0
        CD2_1 =  0.0        CD2_2 = +0.000168
        CRPIX1 = 2328.0     CRPIX2 = 1760.0
        CRVAL1 = 83.82      CRVAL2 = -5.39
    After the flip: CRPIX2 = 1761.0, CD2_2 = -0.000168.
    """
    from copy import deepcopy
    result = deepcopy(wcs)
    result.sip = None
    result.wcs.crpix[1] = naxis2 + 1 - result.wcs.crpix[1]
    if result.wcs.has_cd():
        result.wcs.cd[:, 1] *= -1
        cd = result.wcs.cd
        cdelt1 = np.sign(cd[0, 0]) * np.sqrt(cd[0, 0] ** 2 + cd[1, 0] ** 2)
        cdelt2 = np.sign(cd[1, 1]) * np.sqrt(cd[0, 1] ** 2 + cd[1, 1] ** 2)
        rho_a = np.degrees(np.arctan2(cd[1, 0], cd[0, 0]))
        rho_b = np.degrees(np.arctan2(-cd[0, 1], cd[1, 1]))
        result.wcs.cdelt = [cdelt1, cdelt2]
        result.wcs.crota = [(rho_a + rho_b) / 2.0] * 2
    else:
        result.wcs.pc[:, 1] *= -1
        result.wcs.cdelt[1] *= -1
    result.wcs.set()
    return result


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


def _extract_sources(data: np.ndarray, max_sources: int = 300) -> tuple:
    """Detect stars in a 2D float array. Returns (x, y, flux) 1-indexed pixel arrays."""
    from photutils.background import Background2D, MedianBackground
    from photutils.detection import DAOStarFinder

    data = data.astype(np.float32)
    try:
        bkg = Background2D(data, (64, 64), filter_size=(3, 3), bkg_estimator=MedianBackground())
        data_sub = data - bkg.background
        std = float(bkg.background_rms_median)
    except Exception:
        data_sub = data - float(np.median(data))
        std = float(np.std(data_sub))

    sources = DAOStarFinder(fwhm=3.0, threshold=5.0 * std)(data_sub)
    if sources is None or len(sources) == 0:
        return np.array([]), np.array([]), np.array([])

    sources.sort('peak', reverse=True)
    if len(sources) > max_sources:
        sources = sources[:max_sources]

    return (
        np.array(sources['xcentroid'] + 1, dtype=np.float32),  # 1-indexed
        np.array(sources['ycentroid'] + 1, dtype=np.float32),
        np.array(sources['peak'],          dtype=np.float32),
    )


def extract_sources(temp_fits: Path) -> tuple:
    """Load a temp FITS and extract star positions. Returns (x, y, flux, width, height)."""
    with fits.open(temp_fits) as hdul:
        data = hdul[0].data
        width  = int(hdul[0].header.get('NAXIS1', 0))
        height = int(hdul[0].header.get('NAXIS2', 0))
    if data.ndim == 3:
        data = select_first_channel(data)
    x, y, flux = _extract_sources(data)
    return x, y, flux, width, height


def create_xylist(temp_fits: Path, output_dir: Path) -> Path:
    """Extract sources from a temp FITS and write an astrometry.net xylist FITS binary table."""
    x, y, flux, width, height = extract_sources(temp_fits)
    if len(x) == 0:
        raise SolverError(f"No sources detected in {temp_fits.name}")
    logging.info("Extracted %d sources for xylist from %s", len(x), temp_fits.name)

    bintable = fits.BinTableHDU.from_columns([
        fits.Column(name='X',    format='E', array=x),
        fits.Column(name='Y',    format='E', array=y),
        fits.Column(name='FLUX', format='E', array=flux),
    ])
    bintable.header['IMAGEW'] = width
    bintable.header['IMAGEH'] = height

    xylist_path = output_dir / (temp_fits.stem + ".xyls")
    fits.HDUList([fits.PrimaryHDU(), bintable]).writeto(str(xylist_path), overwrite=True)
    return xylist_path


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

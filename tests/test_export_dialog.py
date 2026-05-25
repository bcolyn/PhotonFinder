import logging
import string
from datetime import date

import pytest

from photonfinder.calibration import CalibrationCandidate, SessionKey
from photonfinder.models import File, LibraryRoot
from photonfinder.ui.ExportDialog import (
    template_filename,
    template_filename_with_ref,
    ExportWorker,
    _make_shared_template_str,
    build_file_session_dates,
    collect_calibration_files,
    build_file_headers_map,
)
from .sample_models import *


def _file(rowid, name="f.fits"):
    root = LibraryRoot(name="r", path="/r")
    f = File(root=root, path="p", name=name, size=0, mtime_millis=0)
    f.rowid = rowid
    return f


def _candidate(files, master=None):
    return CalibrationCandidate(session_date=None, count=len(files), files=files, master=master)


def _key(date):
    return SessionKey(session_date=date, filter=None, exposure=None)


class TestTemplateFilename:
    """Tests for the template_filename function."""

    def test_basic_template(self, light1, settings):
        """Test basic template substitution."""
        template = string.Template("$object_name/$filter/$filename")
        result = template_filename(light1, template, settings)
        assert result == "Test Object/Test Filter/test_file1.fits"

    def test_decompress_option(self, light1, settings):
        light1.name = "test_file.fits.xz"
        """Test the decompress option."""
        template = string.Template("$filename")

        # Without decompress
        result = template_filename(light1, template, settings, decompress=False)
        assert result == "test_file.fits.xz"

        # With decompress
        result = template_filename(light1, template, settings, decompress=True)
        assert result == "test_file.fits"

    def test_last_light_path_update(self, light1, settings):
        """Test that last_light_path is updated for LIGHT images."""
        template = string.Template("output/dir/$filename")
        result = template_filename(light1, template, settings)
        assert result == "output/dir/test_file1.fits"
        assert settings.get_last_light_path() == Path("output/dir")


class TestTemplateFilenameWithRef:
    """Tests for the template_filename_with_ref function."""

    def test_with_ref_file(self, dark1, light1, settings):
        """Test template_filename_with_ref with a reference file."""
        template = string.Template("output/$object_name/$filename")

        # First set up a path with the reference file
        ref_result = template_filename(dark1, template, settings)
        assert ref_result == "output/None/test_file2.fits"  # No object_name for dark1 file

        # Now use the reference file
        result = template_filename_with_ref(dark1, light1, template, settings)
        assert Path(result) == Path("output/Test Object/test_file2.fits")  # Should use ref path but original filename

    def test_without_ref_file(self, light1, settings):
        """Test template_filename_with_ref without a reference file."""
        template = string.Template("output/$object_name/$filename")
        result = template_filename_with_ref(light1, None, template, settings)
        assert result == "output/Test Object/test_file1.fits"  # Should use normal template_filename


class TestExportWorker:
    """Tests for the ExportWorker class."""

    @pytest.fixture
    def export_worker(self, app_context):
        """Fixture that provides an ExportWorker."""
        return ExportWorker(app_context)

    def assert_wcs(self, filename):
        from astropy.io import fits
        with fits.open(filename) as hdul:
            header = hdul[0].header
            assert header["CROTA1"] == 1.786642450287E+002
            assert header["CROTA2"] == 1.786635472113E+002

    def assert_no_wcs(self, filename):
        from astropy.io import fits
        with fits.open(filename) as hdul:
            header = hdul[0].header
            assert header.get("CROTA1") is None
            assert header.get("CROTA2") is None

    def test_copy_xisf_as_fits(self, export_worker, light1_wcs, global_test_data_dir, tmpdir):
        xisf_file = global_test_data_dir / "2021-05-31_00-10-25__18.30_1.00s_0000.xisf"
        export_worker.override_platesolve = False
        export_worker.export_xisf_as_fits = True
        output_path = os.path.join(tmpdir, "output.fit")
        export_worker.copy_file(str(xisf_file), output_path, light1_wcs)
        assert os.path.exists(output_path)
        assert output_path.endswith(".fit")
        self.assert_no_wcs(output_path)

    def test_copy_xisf_as_fits_wcs(self, export_worker, light1_wcs, global_test_data_dir, tmpdir):
        # Find a XISF file in the test data directory
        xisf_file = global_test_data_dir / "2021-05-31_00-10-25__18.30_1.00s_0000.xisf"
        assert xisf_file.exists(), f"Test file not found: {xisf_file}"

        # Set up the export worker
        export_worker.override_platesolve = True
        export_worker.export_xisf_as_fits = True

        # Create output path
        output_path = os.path.join(tmpdir, "output.fit")

        # Copy the file
        export_worker.copy_file(str(xisf_file), output_path, light1_wcs)

        # Verify the file was copied and converted to FITS
        assert os.path.exists(output_path)
        assert output_path.endswith(".fit")
        self.assert_wcs(output_path)

    def test_copy_compressed_fits_with_override_platesolve(self, export_worker, light1_wcs, global_test_data_dir,
                                                           tmpdir):
        # Find a compressed FITS file in the test data directory
        fits_file = global_test_data_dir / "M106_2020-03-17T024357_60sec_LP__-15C_frame11.fit.xz"
        assert fits_file.exists(), f"Test file not found: {fits_file}"

        # Set up the export worker
        export_worker.override_platesolve = True
        export_worker.decompress = True

        # Create output path
        output_path = os.path.join(tmpdir, "output.fit")

        # Copy the file
        export_worker.copy_file(str(fits_file), output_path, light1_wcs)

        # Verify the file was copied and decompressed
        assert os.path.exists(output_path)
        self.assert_wcs(output_path)


class TestMakeSharedTemplateStr:
    def test_braced_sess_date_replaced(self):
        assert _make_shared_template_str("$object/${sess_date}/lights") == "$object/shared/lights"

    def test_bare_sess_date_replaced(self):
        assert _make_shared_template_str("foo/$sess_date/bar") == "foo/shared/bar"

    def test_no_sess_date_unchanged(self):
        assert _make_shared_template_str("$object_name/$filter/$filename") == "$object_name/$filter/$filename"

    def test_backslash_separator_preserved(self):
        assert _make_shared_template_str("root\\${sess_date}\\lights") == "root\\shared\\lights"

    def test_multiple_segments_replaced(self):
        assert _make_shared_template_str("${sess_date}/sub/${sess_date}") == "shared/sub/shared"


D1 = date(2023, 1, 15)
D2 = date(2023, 1, 16)


class TestBuildFileSessionDates:
    def test_light_files_get_session_date(self):
        key = _key(D1)
        f = _file(1)
        result = build_file_session_dates([key], {key: [f]}, {0: {}}, use_master=False)
        assert result[1] == D1

    def test_calib_files_get_matched_session_date_no_master(self):
        key = _key(D1)
        light = _file(1)
        calib = _file(100)
        cand = _candidate([calib])
        result = build_file_session_dates(
            [key], {key: [light]}, {0: {"DARK": cand}}, use_master=False
        )
        assert result[100] == D1

    def test_master_file_gets_session_date_when_use_master(self):
        """The bug fix: master rowid must be in the map when use_master=True."""
        key = _key(D1)
        light = _file(1)
        sub = _file(100)
        master = _file(101)
        cand = _candidate([sub], master=master)
        result = build_file_session_dates(
            [key], {key: [light]}, {0: {"DARK": cand}}, use_master=True
        )
        assert result[101] == D1
        assert 100 not in result

    def test_sub_files_used_when_no_master(self):
        key = _key(D1)
        light = _file(1)
        sub = _file(100)
        cand = _candidate([sub], master=None)
        result = build_file_session_dates(
            [key], {key: [light]}, {0: {"DARK": cand}}, use_master=True
        )
        assert result[100] == D1

    def test_first_assignment_wins_for_shared_calib(self):
        k1, k2 = _key(D1), _key(D2)
        shared = _file(50)
        cand1 = _candidate([shared])
        cand2 = _candidate([shared])
        sessions = {k1: [_file(1)], k2: [_file(2)]}
        calib_sel = {0: {"DARK": cand1}, 1: {"DARK": cand2}}
        result = build_file_session_dates([k1, k2], sessions, calib_sel, use_master=False)
        assert result[50] == D1

    def test_none_rowid_skipped(self):
        key = _key(D1)
        f = _file(None)
        result = build_file_session_dates([key], {key: [f]}, {0: {}}, use_master=False)
        assert result == {}

    def test_none_session_date_skipped(self):
        key = _key(None)
        f = _file(1)
        result = build_file_session_dates([key], {key: [f]}, {0: {}}, use_master=False)
        assert result == {}


class TestCollectCalibrationFiles:
    def test_no_selections_returns_empty(self):
        assert collect_calibration_files({0: {"DARK": None}}, use_master=False) == []

    def test_candidate_files_returned(self):
        f1, f2 = _file(1), _file(2)
        cand = _candidate([f1, f2])
        result = collect_calibration_files({0: {"DARK": cand}}, use_master=False)
        assert [x.rowid for x in result] == [1, 2]

    def test_master_returned_when_use_master(self):
        sub = _file(100)
        master = _file(999)
        cand = _candidate([sub], master=master)
        result = collect_calibration_files({0: {"DARK": cand}}, use_master=True)
        assert [x.rowid for x in result] == [999]

    def test_subs_returned_when_no_master_and_use_master(self):
        sub = _file(100)
        cand = _candidate([sub], master=None)
        result = collect_calibration_files({0: {"DARK": cand}}, use_master=True)
        assert [x.rowid for x in result] == [100]

    def test_deduplication_across_types(self):
        shared = _file(1)
        cand1 = _candidate([shared])
        cand2 = _candidate([shared, _file(2)])
        result = collect_calibration_files({0: {"DARK": cand1, "FLAT": cand2}}, use_master=False)
        assert [x.rowid for x in result] == [1, 2]

    def test_none_candidates_skipped(self):
        f = _file(1)
        cand = _candidate([f])
        result = collect_calibration_files({0: {"DARK": None, "FLAT": cand}}, use_master=False)
        assert [x.rowid for x in result] == [1]


class TestBuildFileHeadersMap:
    def test_no_headers_returns_empty(self):
        key = _key(D1)
        result = build_file_headers_map([key], {key: []}, {0: {}}, {})
        assert result == {}

    def test_headers_applied_to_light_files(self):
        key = _key(D1)
        light = _file(1)
        result = build_file_headers_map(
            [key], {key: [light]}, {0: {}}, {0: "OBSERVER=Alice"}
        )
        assert result[1] == {"OBSERVER": "Alice"}

    def test_value_coercion(self):
        key = _key(D1)
        light = _file(1)
        result = build_file_headers_map(
            [key], {key: [light]}, {0: {}}, {0: "INT=42\nFLOAT=3.14\nSTR=hello"}
        )
        assert result[1] == {"INT": 42, "FLOAT": 3.14, "STR": "hello"}

    def test_headers_applied_to_calib_files(self):
        key = _key(D1)
        light = _file(1)
        calib = _file(100)
        cand = _candidate([calib])
        result = build_file_headers_map(
            [key], {key: [light]}, {0: {"DARK": cand}}, {0: "SITE=obs"}
        )
        assert result[100] == {"SITE": "obs"}

    def test_first_assignment_wins_for_shared_calib(self):
        k1, k2 = _key(D1), _key(D2)
        shared = _file(50)
        cand1 = _candidate([shared])
        cand2 = _candidate([shared])
        sessions = {k1: [_file(1)], k2: [_file(2)]}
        calib_sel = {0: {"DARK": cand1}, 1: {"DARK": cand2}}
        headers = {0: "SESSION=1", 1: "SESSION=2"}
        result = build_file_headers_map([k1, k2], sessions, calib_sel, headers)
        assert result[50] == {"SESSION": 1}

    def test_malformed_lines_skipped(self, caplog):
        key = _key(D1)
        light = _file(1)
        with caplog.at_level(logging.WARNING):
            result = build_file_headers_map(
                [key], {key: [light]}, {0: {}}, {0: "VALID=yes\nNO_EQUALS\nALSO=ok"}
            )
        assert result[1] == {"VALID": "yes", "ALSO": "ok"}
        assert any("NO_EQUALS" in m for m in caplog.messages)

    def test_blank_lines_silently_ignored(self):
        key = _key(D1)
        light = _file(1)
        result = build_file_headers_map(
            [key], {key: [light]}, {0: {}}, {0: "A=1\n\n   \nB=2"}
        )
        assert result[1] == {"A": 1, "B": 2}

    def test_headers_use_candidate_files_not_master(self):
        """build_file_headers_map always applies headers to candidate.files, not master.
        This is intentional: custom headers are per-session metadata, not per-file-mode."""
        key = _key(D1)
        light = _file(1)
        sub = _file(100)
        master = _file(101)
        cand = _candidate([sub], master=master)
        result = build_file_headers_map(
            [key], {key: [light]}, {0: {"DARK": cand}}, {0: "K=v"}
        )
        assert result[100] == {"K": "v"}
        assert 101 not in result

import string
from datetime import date

import pytest

from photonfinder.models import File, LibraryRoot
from photonfinder.export_plan import ExportEntry
from photonfinder.ui.ExportDialog import (
    template_filename,
    template_filename_with_ref,
    ExportWorker,
    _make_shared_template_str,
    resolve_output_path,
    template_output_root,
)
from .multi_image_files import write_multi_hdu_fits, write_two_image_xisf
from .sample_models import *


def _file(rowid, name="f.fits"):
    root = LibraryRoot(name="r", path="/r")
    f = File(root=root, path="p", name=name, size=0, mtime_millis=0)
    f.rowid = rowid
    return f


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
        result = template_filename(light1, template, settings, decompress=".gz")
        assert result == "test_file.fits"

    def test_last_light_path_update(self, light1, settings):
        """Test that last_light_path is updated for LIGHT images."""
        template = string.Template("output/dir/$filename")
        result = template_filename(light1, template, settings)
        assert result == "output/dir/test_file1.fits"
        assert settings.get_last_light_path() == "output/dir"


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




class TestTemplateSanitizing:
    def test_invalid_windows_characters_replaced(self, light1, settings):
        light1.image.object_name = 'Sh2-129: Flying "Bat"?'
        result = template_filename(light1, string.Template("$object_name/$filename"), settings)
        assert result == "Sh2-129_ Flying _Bat__/test_file1.fits"

    def test_slash_in_value_does_not_create_subfolder(self, light1, settings):
        light1.image.filter = "Ha/OIII"
        result = template_filename(light1, string.Template("$filter/$filename"), settings)
        assert result == "Ha_OIII/test_file1.fits"

    def test_date_obs_is_path_safe(self, light1, settings):
        result = template_filename(light1, string.Template("$date_obs/$filename"), settings)
        assert result == "2023-01-01T12_00_00/test_file1.fits"

    def test_lib_path_keeps_separators(self, light1, settings):
        light1.path = "2023/lights/"
        result = template_filename(light1, string.Template("${lib_path}$filename"), settings)
        assert result == "2023/lights/test_file1.fits"


class TestResolveOutputPath:
    def test_leading_separator_stays_under_root(self, tmp_path):
        assert resolve_output_path(str(tmp_path), "/a/b.fits") == str(tmp_path / "a" / "b.fits")

    def test_escaping_root_is_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            resolve_output_path(str(tmp_path), "../outside.fits")

    def test_relative_root_is_rejected(self):
        with pytest.raises(ValueError):
            resolve_output_path("Test Object/out", "b.fits")


class TestTemplateOutputRoot:
    def test_variables_are_filled_in(self, light1, settings):
        assert template_output_root(light1, None, "D:/proc/$object_name/$filter", settings) \
            == "D:/proc/Test Object/Test Filter"

    def test_values_cannot_add_folders(self, light1, settings):
        light1.image.object_name = "M31/../.."
        assert template_output_root(light1, None, "D:/proc/$object_name", settings) == "D:/proc/M31_.._"

    def test_root_without_variables_is_untouched(self, light1, settings):
        assert template_output_root(light1, None, "\\\\nas\\D$\\proc", settings) == "\\\\nas\\D$\\proc"

    def test_reference_file_fills_the_root(self, dark1, light1, settings):
        assert template_output_root(dark1, light1, "D:/proc/$image_type", settings) == "D:/proc/LIGHT"

    def test_shared_frame_gets_shared_for_sess_date(self, dark1, settings):
        root = template_output_root(dark1, None, "D:/proc/${sess_date}", settings,
                                    sess_date=date(2024, 3, 12), shared=True)
        assert root == "D:/proc/shared"

    def test_sess_date_comes_from_the_session(self, dark1, settings):
        root = template_output_root(dark1, None, "D:/proc/${sess_date}", settings, sess_date=date(2024, 3, 12))
        assert root == "D:/proc/2024-03-12"


class TestExportWorkerWrites:
    @pytest.fixture
    def worker(self, app_context, tmp_path):
        worker = ExportWorker(app_context)
        worker.output_path = str(tmp_path / "out")
        worker.pattern = string.Template("$filename")
        worker.total_files = 10
        return worker

    @staticmethod
    def _source(tmp_path, rowid, subdir, content):
        directory = tmp_path / "lib" / subdir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "light.fits").write_bytes(content)
        root = LibraryRoot(name="r", path=str(tmp_path / "lib"))
        f = File(root=root, path=subdir, name="light.fits", size=len(content), mtime_millis=0)
        f.rowid = rowid
        return f

    def test_different_sources_with_same_output_name_are_reported(self, worker, tmp_path):
        first = self._source(tmp_path, 1, "night1", b"first")
        second = self._source(tmp_path, 2, "night2", b"second")

        worker._process_entry(ExportEntry(first, None), 0)
        worker._process_entry(ExportEntry(second, None), 1)

        assert (tmp_path / "out" / "light.fits").read_bytes() == b"first"
        assert len(worker.collisions) == 1
        assert "night2" in worker.collisions[0]

    def test_same_file_twice_is_not_a_collision(self, worker, tmp_path):
        f = self._source(tmp_path, 1, "night1", b"data")

        worker._process_entry(ExportEntry(f, None), 0)
        worker._process_entry(ExportEntry(f, None), 1)

        assert worker.collisions == []

    def test_interrupted_copy_leaves_nothing_behind(self, worker, tmp_path, mocker):
        f = self._source(tmp_path, 1, "night1", b"data")

        def partial_copy(src, dst):
            with open(dst, "wb") as out:
                out.write(b"da")
            raise OSError("disk full")

        mocker.patch("photonfinder.ui.ExportDialog.shutil.copy2", side_effect=partial_copy)

        with pytest.raises(OSError):
            worker._process_entry(ExportEntry(f, None), 0)

        assert list((tmp_path / "out").iterdir()) == []

    def test_variables_in_root_are_filled_in_per_file(self, worker, tmp_path):
        f = self._source(tmp_path, 1, "night1", b"data")
        worker.output_path = str(tmp_path / "out" / "$filename_no_ext")

        worker._process_entry(ExportEntry(f, None), 0)

        assert (tmp_path / "out" / "light" / "light.fits").read_bytes() == b"data"

    def test_existing_file_from_earlier_export_is_kept(self, worker, tmp_path):
        f = self._source(tmp_path, 1, "night1", b"new")
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "light.fits").write_bytes(b"earlier")

        worker._process_entry(ExportEntry(f, None), 0)

        assert (tmp_path / "out" / "light.fits").read_bytes() == b"earlier"
        assert worker.collisions == []


class TestExportKeepsAllHdus:
    @pytest.fixture
    def worker(self, app_context):
        return ExportWorker(app_context)

    @staticmethod
    def _assert_layers_kept(source, output):
        import numpy as np
        from astropy.io import fits
        with fits.open(source) as src, fits.open(output) as out:
            assert [h.name for h in out] == ["PRIMARY", "SOO", "STARS"]
            assert out["SOO"].header["FILTER"] == "SO"
            assert out["SOO"].header["EXPTIME"] == 11700
            for name in ("PRIMARY", "SOO"):
                if src[name].data is None:
                    assert out[name].data is None
                else:
                    assert out[name].data.dtype == src[name].data.dtype
                    np.testing.assert_array_equal(out[name].data, src[name].data)
            np.testing.assert_array_equal(out["STARS"].data["x"], src["STARS"].data["x"])

    def test_custom_header_keeps_extensions(self, worker, light1, tmp_path):
        source = write_multi_hdu_fits(tmp_path / "draco.fits")
        output = str(tmp_path / "out.fits")
        worker.override_platesolve = False

        worker.copy_file(source, output, light1, {"MYKEY": "hello"})

        self._assert_layers_kept(source, output)
        from astropy.io import fits
        with fits.open(output) as out:
            assert out[0].header["MYKEY"] == "hello"
            assert out[0].header["FILTER"] == "HO"
            assert "MYKEY" not in out["SOO"].header

    def test_compressed_source_with_decompress_keeps_extensions(self, worker, light1, tmp_path):
        source = write_multi_hdu_fits(tmp_path / "draco.fits", compress=".gz")
        output = str(tmp_path / "out.fits")
        worker.override_platesolve = False
        worker.decompress = True

        worker.copy_file(source, output, light1, {"MYKEY": "hello"})

        with open(output, "rb") as f:
            assert f.read(6) == b"SIMPLE"  # really decompressed
        self._assert_layers_kept(source, output)

    def test_wcs_override_goes_to_layers_with_the_same_size(self, worker, light1_wcs, tmp_path):
        source = write_multi_hdu_fits(tmp_path / "draco.fits")
        output = str(tmp_path / "out.fits")
        worker.override_platesolve = True

        worker.copy_file(source, output, light1_wcs)

        self._assert_layers_kept(source, output)
        from astropy.io import fits
        with fits.open(output) as out:
            assert out[0].header["CROTA2"] == pytest.approx(1.786635472113E+002)
            assert out["SOO"].header["CROTA2"] == pytest.approx(1.786635472113E+002)
            assert out["SOO"].header["NAXIS1"] == 10  # the solution's NAXIS cards are not copied
            assert "CROTA2" not in out["STARS"].header

    def test_wcs_override_skips_a_layer_of_another_size(self, worker, light1_wcs, tmp_path):
        source = write_multi_hdu_fits(tmp_path / "draco.fits", extension_shape=(3, 4, 5))
        output = str(tmp_path / "out.fits")
        worker.override_platesolve = True

        worker.copy_file(source, output, light1_wcs)

        from astropy.io import fits
        with fits.open(output) as out:
            assert "CROTA2" in out[0].header
            assert "CROTA2" not in out["SOO"].header

    def test_wcs_override_with_empty_primary_goes_to_the_first_image(self, worker, light1_wcs, tmp_path):
        source = write_multi_hdu_fits(tmp_path / "draco.fits", empty_primary=True)
        output = str(tmp_path / "out.fits")
        worker.override_platesolve = True

        worker.copy_file(source, output, light1_wcs, {"MYKEY": "hello"})

        self._assert_layers_kept(source, output)
        from astropy.io import fits
        with fits.open(output) as out:
            assert out[0].header["NAXIS"] == 0
            assert "CROTA2" not in out[0].header
            assert out[0].header["MYKEY"] == "hello"
            assert out["SOO"].header["CROTA2"] == pytest.approx(1.786635472113E+002)


class TestExportXisfAllImages:
    def test_every_xisf_image_becomes_an_hdu(self, app_context, light1_wcs, tmp_path):
        import numpy as np
        from astropy.io import fits
        source = tmp_path / "two.xisf"
        first, second = write_two_image_xisf(source)
        worker = ExportWorker(app_context)
        worker.export_xisf_as_fits = True
        worker.override_platesolve = True
        output = str(tmp_path / "out.fits")

        worker.copy_file(str(source), output, light1_wcs, {"MYKEY": "hello"})

        with fits.open(output) as out:
            assert len(out) == 2
            assert out[0].header["FILTER"] == "HO"
            assert out[0].header["MYKEY"] == "hello"
            assert out[1].name == "SOO"
            assert out[1].header["FILTER"] == "SO"
            assert "MYKEY" not in out[1].header
            np.testing.assert_array_equal(out[0].data, np.squeeze(first))
            np.testing.assert_array_equal(out[1].data, np.squeeze(second))
            assert out[0].header["CROTA2"] == pytest.approx(1.786635472113E+002)
            assert out[1].header["CROTA2"] == pytest.approx(1.786635472113E+002)

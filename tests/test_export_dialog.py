import string

from photonfinder.ui.ExportDialog import template_filename, template_filename_with_ref, ExportWorker
from .sample_models import *


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

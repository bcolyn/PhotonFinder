"""Finding, reading and loading every image in a multi-image FITS or XISF file."""
import numpy as np
import pytest
from astropy.io import fits
from astropy.io.fits import Card, Header

from photonfinder import filesystem
from photonfinder.filesystem import read_fits_header, read_xisf_header
from photonfinder.image_hdus import list_image_hdus, merged_header, FITS_BLOCK
from photonfinder.image_processing import image_cache, load_image_data

from .multi_image_files import PRIMARY_SHAPE, write_multi_hdu_fits, write_two_image_xisf


@pytest.fixture(autouse=True)
def _clear_cache():
    image_cache.clear()
    yield
    image_cache.clear()


def _source_hdus(path):
    with fits.open(path) as hdul:
        return [(h.data.copy() if h.data is not None else None, h.header.copy()) for h in hdul]


class TestListImageHdus:
    @pytest.mark.parametrize("compress", [None, ".gz", ".bz2", ".xz"])
    def test_primary_and_image_extension(self, tmp_path, compress):
        path = write_multi_hdu_fits(tmp_path / "draco.fits", compress=compress)

        images = list_image_hdus(path)

        assert [(i.index, i.extname, i.shape) for i in images] == [(0, None, PRIMARY_SHAPE), (1, "SOO", PRIMARY_SHAPE)]
        assert Header.fromstring(images[1].header)["FILTER"] == "SO"
        assert Header.fromstring(images[0].header)["FILTER"] == "HO"

    def test_a_table_is_not_an_image(self, tmp_path):
        path = str(tmp_path / "table.fits")
        fits.HDUList([fits.PrimaryHDU(np.zeros((4, 5), np.uint16)),
                      fits.BinTableHDU.from_columns([fits.Column(name="x", format="E", array=np.arange(3.0))])]
                     ).writeto(path)

        assert [i.index for i in list_image_hdus(path)] == [0]

    def test_empty_primary(self, tmp_path):
        path = write_multi_hdu_fits(tmp_path / "draco.fits", empty_primary=True)

        assert [(i.index, i.extname) for i in list_image_hdus(path)] == [(1, "SOO")]

    def test_not_a_fits_file(self, tmp_path):
        path = tmp_path / "x.fits"
        path.write_bytes(b"not fits" * 400)

        assert not list_image_hdus(path)

    def test_plain_fits_reads_headers_only(self, tmp_path, mocker):
        path = write_multi_hdu_fits(tmp_path / "draco.fits")
        bytes_read = []
        real_fopen = filesystem.fopen

        class Counting:
            def __init__(self, f):
                self.f = f

            def read(self, n=-1):
                data = self.f.read(n)
                bytes_read.append(len(data))
                return data

            def seek(self, *args):
                return self.f.seek(*args)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.f.close()

        mocker.patch("photonfinder.filesystem.fopen", lambda p: Counting(real_fopen(p)))

        list_image_hdus(path)

        # one header block per HDU (primary, SOO, STARS), plus the read that finds the end
        assert sum(bytes_read) == 3 * FITS_BLOCK


class TestMergedHeader:
    def test_extension_completed_with_primary(self):
        primary = Header([Card("SIMPLE", True), Card("BITPIX", 16), Card("NAXIS", 3), Card("NAXIS1", 10),
                          Card("EXTEND", True), Card("BZERO", 32768), Card("FILTER", "HO"),
                          Card("EXPTIME", 6000), Card("DATE-OBS", "2026-11-02T20:00:00"), Card("OBJECT", "C 20"),
                          Card("HISTORY", "stacked")])
        ext = Header([Card("XTENSION", "IMAGE"), Card("BITPIX", -32), Card("NAXIS", 2), Card("NAXIS1", 7),
                      Card("PCOUNT", 0), Card("GCOUNT", 1), Card("EXTNAME", "SOO"), Card("FILTER", "SO"),
                      Card("EXPTIME", 11700), Card("HISTORY", "SO layer")])

        merged = merged_header(primary, ext)

        assert merged["DATE-OBS"] == "2026-11-02T20:00:00"
        assert merged["OBJECT"] == "C 20"
        assert merged["FILTER"] == "SO"
        assert merged["EXPTIME"] == 11700
        assert list(merged.keys())[0] == "XTENSION"
        assert merged["BITPIX"] == -32
        assert merged["NAXIS"] == 2 and merged["NAXIS1"] == 7
        assert "BZERO" not in merged and "SIMPLE" not in merged and "EXTEND" not in merged
        assert list(merged["HISTORY"]) == ["stacked", "SO layer"]
        assert list(merged.keys()).count("FILTER") == 1

    def test_the_inputs_are_left_unchanged(self):
        primary = Header([Card("SIMPLE", True), Card("FILTER", "HO"), Card("DATE-OBS", "2026-11-02")])
        ext = Header([Card("XTENSION", "IMAGE"), Card("FILTER", "SO")])

        merged_header(primary, ext)["DATE-OBS"] = "changed"

        assert primary["FILTER"] == "HO"
        assert primary["DATE-OBS"] == "2026-11-02"
        assert ext["FILTER"] == "SO"

    def test_primary_is_returned_unchanged(self):
        primary = Header([Card("SIMPLE", True), Card("FILTER", "HO")])

        assert merged_header(primary, primary) is primary


class TestReadFitsHeader:
    def test_primary_image_reads_only_the_primary_header(self, tmp_path):
        path = write_multi_hdu_fits(tmp_path / "draco.fits")
        with open(path, "rb") as f:
            primary = f.read(FITS_BLOCK)

        assert read_fits_header(path) == primary

    @pytest.mark.parametrize("compress", [None, ".gz"])
    def test_empty_primary_gives_the_first_image_merged(self, tmp_path, compress):
        path = write_multi_hdu_fits(tmp_path / "draco.fits", empty_primary=True, compress=compress)

        header = Header.fromstring(read_fits_header(path))

        assert header["FILTER"] == "SO"
        assert header["DATE-OBS"] == "2026-11-02T20:00:00"
        assert header["NAXIS"] == 3 and header["NAXIS1"] == 10
        assert header["XTENSION"] == "IMAGE"


class TestLoadImageData:
    def test_each_hdu_has_its_own_pixels_and_header(self, tmp_path):
        path = write_multi_hdu_fits(tmp_path / "draco.fits")
        source = _source_hdus(path)

        primary_data, primary_header = load_image_data(path)
        soo_data, soo_header = load_image_data(path, 1)

        np.testing.assert_array_equal(primary_data, source[0][0])
        np.testing.assert_array_equal(soo_data, source[1][0])
        assert primary_header["FILTER"] == "HO"
        assert soo_header["FILTER"] == "SO"
        assert soo_header["DATE-OBS"] == "2026-11-02T20:00:00"
        np.testing.assert_array_equal(load_image_data(path, 0)[0], source[0][0])
        assert image_cache.get(path, 1) is not None

    @pytest.mark.parametrize("compress", [None, ".gz", ".xz"])
    def test_empty_primary_loads_the_first_image(self, tmp_path, compress):
        path = write_multi_hdu_fits(tmp_path / "draco.fits", empty_primary=True, compress=compress)
        plain = _source_hdus(str(tmp_path / "draco.fits"))

        data, header = load_image_data(path)

        np.testing.assert_array_equal(data, plain[1][0])
        assert header["FILTER"] == "SO"
        assert header["OBJECT"] == "C 20"

    def test_explicit_empty_hdu_has_no_image_data(self, tmp_path):
        path = write_multi_hdu_fits(tmp_path / "draco.fits", empty_primary=True)

        with pytest.raises(ValueError, match="No image data"):
            load_image_data(path, 0)


class TestXisf:
    def test_two_images(self, tmp_path):
        path = tmp_path / "two.xisf"
        _, second = write_two_image_xisf(path)

        images = list_image_hdus(path)

        assert [(i.index, i.extname, i.shape) for i in images] == [(0, None, (1, 3, 4)), (1, "SOO", (1, 3, 4))]
        assert images[1].keywords["FILTER"][0]["value"] == "SO"
        data, header = load_image_data(path, 1)
        np.testing.assert_array_equal(data, np.squeeze(second))
        assert header["FILTER"] == "SO"
        assert load_image_data(path)[1]["FILTER"] == "HO"

    def test_header_and_pixels_come_from_the_same_image(self, tmp_path):
        """Image 0 has no keywords: the first image is the SOO one, for header and pixels alike."""
        path = tmp_path / "two.xisf"
        _, second = write_two_image_xisf(path, first_keywords=False)

        assert [i.index for i in list_image_hdus(path)] == [1]
        _, keywords = read_xisf_header(path)
        assert keywords["FILTER"][0]["value"] == "SO"
        data, header = load_image_data(path)
        np.testing.assert_array_equal(data, np.squeeze(second))
        assert header["FILTER"] == "SO"

    def test_single_image_without_keywords_is_still_read(self, tmp_path):
        import xisf
        path = tmp_path / "no_keywords.xisf"
        xisf.XISF.write(str(path), np.zeros((3, 4, 1), np.uint16))

        _, keywords = read_xisf_header(path)

        assert keywords == {"NAXIS": [{"value": "2", "comment": ""}], "NAXIS1": [{"value": "4", "comment": ""}],
                            "NAXIS2": [{"value": "3", "comment": ""}]}
        assert load_image_data(path)[0].shape == (3, 4)

"""FITS header ends, binning formats and matching a path to its library root."""
import pytest
from astropy.io.fits import Card, Header

from photonfinder import filesystem
from photonfinder.core import compress, decompress
from photonfinder.filesystem import Importer, ScanCancelled, parse_FITS_header, read_fits_header
from photonfinder.fits_handlers import GenericHandler
from photonfinder.models import File, FileWCS, FitsHeader, Image, ImageStats, LibraryRoot

BLOCK = 2880


def _two_block_header(end_like_keyword="ENDTIME") -> bytes:
    """A header with a keyword starting with END in block 1, and GAIN in block 2."""
    cards = [Card("SIMPLE", True), Card("BITPIX", 16), Card("NAXIS", 0), Card("IMAGETYP", "LIGHT"),
             Card(end_like_keyword, "2024-01-01T22:00:00")]
    cards += [Card(f"PAD{i}", i) for i in range(40)]
    cards += [Card("GAIN", 120), Card("OFFSET", 30)]
    raw = Header(cards).tostring().encode("ascii")
    assert len(raw) == 2 * BLOCK
    return raw


def _write(path, raw: bytes):
    path.write_bytes(raw + b"\0" * BLOCK)
    return path


@pytest.mark.parametrize("keyword", ["ENDTIME", "END-OBS", "ENDED"])
def test_a_keyword_starting_with_end_does_not_end_the_header(tmp_path, keyword):
    raw = _two_block_header(keyword)

    assert read_fits_header(_write(tmp_path / "a.fits", raw)) == raw


def test_a_header_ends_at_the_first_real_end_card(tmp_path):
    raw = Header([Card("SIMPLE", True), Card("OBJECT", "M31")]).tostring().encode("ascii")

    assert read_fits_header(_write(tmp_path / "a.fits", raw)) == raw


def test_an_end_card_padded_with_nulls_still_ends_the_header(tmp_path):
    raw = Header([Card("SIMPLE", True), Card("OBJECT", "M31")]).tostring().encode("ascii")
    padded = raw.replace(b"END" + b" " * 77, b"END" + b"\0" * 77)

    assert read_fits_header(_write(tmp_path / "a.fits", padded)) == padded


def test_a_file_with_no_end_card_at_all_has_no_header(tmp_path):
    raw = _two_block_header().replace(b"END" + b" " * 77, b" " * 80)
    path = tmp_path / "a.fits"
    path.write_bytes(raw)

    assert read_fits_header(path) is None


class TestRepairingTruncatedHeaders:
    @pytest.fixture
    def root(self, context, tmp_path):
        return LibraryRoot.create(name="r", path=str(tmp_path).replace("\\", "/") + "/")

    def _truncated_file(self, root, tmp_path, name="a.fits", raw=None):
        raw = raw or _two_block_header()
        _write(tmp_path / name, raw)
        file = File.create(root=root, path="", name=name, size=1, mtime_millis=1)
        FitsHeader.create(file=file, header=compress(raw[:BLOCK]))
        Image.create(file=file, image_type="LIGHT", camera=None, gain=None, offset=None)
        return file

    def _repair(self, context, cancelled=lambda: False):
        filesystem.repair_truncated_header_cache(context.status_reporter, context.settings, cancelled)

    def test_a_truncated_header_is_read_again(self, context, root, tmp_path):
        raw = _two_block_header()
        file = self._truncated_file(root, tmp_path, raw=raw)

        self._repair(context)

        assert decompress(FitsHeader.get(FitsHeader.file == file).header) == raw
        image = Image.get(Image.file == file)
        assert (image.gain, image.offset) == (120, 30)

    def test_the_repair_keeps_the_plate_solution_and_stats(self, context, root, tmp_path):
        file = self._truncated_file(root, tmp_path)
        Image.update(coord_ra=10.5, coord_dec=41.25, coord_pix256=1234, coord_radius=0.75).execute()
        FileWCS.create(file=file, wcs=compress(b"the stored solution"))
        ImageStats.create(file=file, star_count=100)

        self._repair(context)

        image = Image.get(Image.file == file)
        assert image.gain == 120
        assert (image.coord_ra, image.coord_dec, image.coord_pix256, image.coord_radius) == (10.5, 41.25, 1234, 0.75)
        assert decompress(FileWCS.get(FileWCS.file == file).wcs) == b"the stored solution"
        assert ImageStats.get(ImageStats.file == file).star_count == 100

    def test_a_solution_lost_with_the_truncation_is_recovered(self, context, root, tmp_path):
        cards = [Card("SIMPLE", True), Card("BITPIX", 16), Card("NAXIS", 2), Card("NAXIS1", 100),
                 Card("NAXIS2", 100), Card("ENDTIME", "x")]
        cards += [Card(f"PAD{i}", i) for i in range(40)]
        cards += [Card("CTYPE1", "RA---TAN"), Card("CTYPE2", "DEC--TAN"), Card("CRVAL1", 10.0), Card("CRVAL2", 20.0),
                  Card("CRPIX1", 50.0), Card("CRPIX2", 50.0), Card("CD1_1", 0.001), Card("CD1_2", 0.0),
                  Card("CD2_1", 0.0), Card("CD2_2", 0.001), Card("PLTSOLVD", True)]
        file = self._truncated_file(root, tmp_path, raw=Header(cards).tostring().encode("ascii"))

        self._repair(context)

        assert FileWCS.get_or_none(FileWCS.file == file) is not None

    def test_a_file_that_cannot_be_read_keeps_what_was_cached(self, context, root, tmp_path):
        raw = _two_block_header()
        file = self._truncated_file(root, tmp_path, raw=raw)
        (tmp_path / "a.fits").unlink()

        self._repair(context)

        assert decompress(FitsHeader.get(FitsHeader.file == file).header) == raw[:BLOCK]
        assert Image.get_or_none(Image.file == file) is not None

    def test_one_bad_file_does_not_stop_the_others(self, context, root, tmp_path, mocker):
        self._truncated_file(root, tmp_path, name="bad.fits")
        good = self._truncated_file(root, tmp_path, name="good.fits")
        real_parse = parse_FITS_header
        calls = []

        def parse(raw):
            calls.append(raw)
            if len(calls) == 1:
                raise ValueError("corrupt")
            return real_parse(raw)
        mocker.patch("photonfinder.filesystem.parse_FITS_header", side_effect=parse)

        self._repair(context)

        assert len(calls) == 2
        assert Image.get(Image.file == good).gain == 120

    def test_complete_headers_are_left_alone(self, context, root, tmp_path, mocker):
        complete = File.create(root=root, path="", name="complete.fits", size=1, mtime_millis=1)
        FitsHeader.create(file=complete, header=compress(_two_block_header()))
        xisf = File.create(root=root, path="", name="b.xisf", size=1, mtime_millis=1)
        FitsHeader.create(file=xisf, header=compress(b'{"OBJECT": [{"value": "M31", "comment": ""}]}'))
        read = mocker.patch("photonfinder.filesystem.read_fits_header")

        self._repair(context)

        read.assert_not_called()

    def test_a_library_scan_repairs_an_unchanged_file(self, context, root, tmp_path, mocker):
        from photonfinder.ui.MainWindow import LibraryScanWorker
        mocker.patch.object(context.settings, "get_bad_file_patterns", create=True, return_value="bad*")
        mocker.patch.object(context.settings, "get_bad_dir_patterns", create=True, return_value="bad*")
        raw = _two_block_header()
        _write(tmp_path / "a.fits", raw)
        LibraryScanWorker(context, roots=[root]).import_roots()
        file = File.get(File.name == "a.fits")
        FitsHeader.update(header=compress(raw[:BLOCK])).execute()
        Image.update(gain=None, offset=None).execute()

        LibraryScanWorker(context, roots=[root]).import_roots()

        assert decompress(FitsHeader.get(FitsHeader.file == file).header) == raw
        assert Image.get(Image.file == file).gain == 120

    def test_cancelling_stops_the_repair(self, context, root, tmp_path):
        file = self._truncated_file(root, tmp_path)

        with pytest.raises(ScanCancelled):
            self._repair(context, cancelled=lambda: True)

        assert Image.get(Image.file == file).gain is None


@pytest.mark.parametrize("value, expected", [
    (2, 2), ("2", 2), ("2x2", 2), ("2X2", 2), ("2*2", 2), ("3 x 3", 3), (2.0, 2),
    ("junk", None), ("", None), ("2x", None),
])
def test_binning_formats(value, expected):
    assert GenericHandler()._get_binning(Header({"BINNING": value})) == expected


def test_binning_prefers_xbinning():
    assert GenericHandler()._get_binning(Header({"XBINNING": 3, "BINNING": "2x2"})) == 3


def test_no_binning_card_leaves_binning_unknown():
    assert GenericHandler()._get_binning(Header()) is None


class TestFindingARoot:
    @pytest.fixture(autouse=True)
    def roots(self, context):
        LibraryRoot.create(name="astro", path="C:/Astro/")
        LibraryRoot.create(name="astro2", path="C:/Astro2/")
        LibraryRoot.create(name="nested", path="C:/Astro/Nested/")
        LibraryRoot.create(name="wild", path="D:/my_lib%/")

    @pytest.mark.parametrize("path, expected", [
        ("C:/Astro/a.fits", "astro"),
        ("C:/Astro2/a.fits", "astro2"),
        ("C:/Astro/Nested/a.fits", "nested"),
        ("C:/Astro/NestedNot/a.fits", "astro"),
        ("C:/Astro", "astro"),
        ("C:/Astro/", "astro"),
        ("C:\\Astro2\\a.fits", "astro2"),
        ("c:/astro2/a.fits", "astro2"),
        ("D:/my_lib%/a.fits", "wild"),
    ])
    def test_the_most_specific_root_with_a_path_boundary(self, path, expected):
        assert LibraryRoot.find_for_file(path).name == expected

    @pytest.mark.parametrize("path", ["C:/Astro3/a.fits", "C:/Astr", "D:/myXlibY/a.fits", "E:/a.fits"])
    def test_a_path_in_no_root(self, path):
        assert LibraryRoot.find_for_file(path) is None

    def test_importing_a_file_in_no_root_skips_it(self, context):
        importer = Importer(context)

        changes = importer.import_selection(["E:/a.fits"])

        assert changes.new_files == []


class TestRootIdentity:
    def test_equal_roots_hash_alike(self):
        first = LibraryRoot(name="a", path="C:/a/")
        second = LibraryRoot(name="a", path="C:/a/")

        assert first == second
        assert len({first, second}) == 1

    def test_different_roots_are_not_equal(self):
        assert LibraryRoot(name="a", path="C:/a/") != LibraryRoot(name="b", path="C:/a/")

    @pytest.mark.parametrize("other", [None, "a", 1])
    def test_a_non_root_is_not_equal(self, other):
        assert LibraryRoot(name="a", path="C:/a/") != other

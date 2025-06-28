import pytest

from photonfinder.core import compress
from photonfinder.models import *
from .utils import fix_embedded_header
from .sample_headers import *


@pytest.fixture
def root1():
    return LibraryRoot(name="test_root", path=r"/test_root")


@pytest.fixture
def file1(root1):
    return File(
        root=root1,
        path="test/path1",
        name="test_file1.fits",
        size=1000,
        mtime_millis=1000
    )


@pytest.fixture
def file2(root1):
    return File(
        root=root1,
        path="test/path2",
        name="test_file2.fits",
        size=1000,
        mtime_millis=1000
    )


@pytest.fixture
def light1(file1) -> File:
    image = Image(
        file=file1,
        image_type="LIGHT",
        camera="Test Camera",
        filter="Test Filter",
        exposure=10.0,
        gain=100,
        offset=10,
        binning=1,
        set_temp=-10.0,
        telescope="Test Telescope",
        object_name="Test Object",
        date_obs=datetime(2023, 1, 1, 12, 0, 0)
    )
    file1.image = image
    return file1


@pytest.fixture
def dark1(file2) -> File:
    image = Image(
        file=file2,
        image_type="DARK",
        camera="Test Camera",
        filter=None,
        exposure=10.0,
        gain=100,
        offset=10,
        binning=1,
        set_temp=-10.0,
        telescope="Test Telescope",
        object_name=None,
        date_obs=datetime(2023, 1, 1, 12, 0, 0)
    )
    file2.image = image
    return file2


@pytest.fixture
def light1_wcs(light1) -> File:
    wcs = FileWCS(file=light1, wcs=compress(fix_embedded_header(wcs_header_m106)))
    light1.filewcs = wcs
    return light1

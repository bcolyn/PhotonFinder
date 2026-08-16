import warnings
from pathlib import Path

import pytest
from fs.memoryfs import MemoryFS
from fs.opener import Opener
from peewee import SqliteDatabase

from photonfinder.core import ApplicationContext, Settings, StatusReporter, decompress, register_udfs
from photonfinder.models import CORE_MODELS


def _test_data_available() -> bool:
    data_dir = Path(__file__).parent / "data"
    return data_dir.exists() and any(data_dir.iterdir())


class DynamicSettings:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._storage = {}

    def __getattr__(self, name):
        if name.startswith("set_"):
            key = name[4:]
            return lambda value: self._storage.__setitem__(key, value)
        elif name.startswith("get_"):
            key = name[4:]
            return lambda: self._storage.get(key)
        return super().__getattr__(name)

    def add_known_fits_keywords(self, param):
        pass

    def sync(self):
        pass


@pytest.fixture(scope="class")
def settings():
    yield DynamicSettings()


@pytest.fixture(scope="class")
def app_context(settings: Settings):
    """
    Fixture that provides an application context for tests.
    Uses an in-memory database for testing.
    """
    # Create an application context with an in-memory database
    context = ApplicationContext(":memory:", settings)
    context.set_status_reporter(StatusReporter())
    with context:
        yield context


# --- Fixtures for the headless query layers (mcp_server / reports) -------------------
# Shared by tests/test_mcp_server.py and tests/test_reports.py: a real ApplicationContext
# over an in-memory database, populated with a small but representative library.

class McpSettings:
    """Minimal Settings stand-in for ApplicationContext."""

    def __init__(self):
        self._store = {}

    def get_last_database_path(self):
        return self._store.get("last_database_path", "")

    def set_last_database_path(self, value):
        self._store["last_database_path"] = value

    def get_known_fits_keywords(self):
        return []

    def sync(self):
        pass

    def get_mcp_allow_plate_solve(self):
        return self._store.get("mcp_allow_plate_solve", True)

    def get_plate_solve_primary_solver(self):
        return 0

    def get_plate_solve_backup_solver(self):
        return -1

    def get_plate_solve_hint_ra(self):
        return ""

    def get_plate_solve_hint_dec(self):
        return ""

    def get_plate_solve_hint_scale(self):
        return 0.0

    def get_plate_solve_hint_mode(self):
        return "fallback"


def fixed_solution_header():
    """A real, parseable WCS solution centred on M31 over a 200x200 frame."""
    from astropy.io.fits import Header
    hdr = Header()
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    hdr["CRVAL1"] = 10.68
    hdr["CRVAL2"] = 41.27
    hdr["CRPIX1"] = 100.0
    hdr["CRPIX2"] = 100.0
    hdr["CDELT1"] = -0.001
    hdr["CDELT2"] = 0.001
    hdr["CUNIT1"] = "deg"
    hdr["CUNIT2"] = "deg"
    hdr["NAXIS1"] = 200
    hdr["NAXIS2"] = 200
    return hdr


@pytest.fixture
def context():
    ctx = ApplicationContext(":memory:", McpSettings())
    ctx.set_status_reporter(StatusReporter())
    with ctx:
        yield ctx


@pytest.fixture
def sample(context):
    """Populate the database with two files (a LIGHT and a DARK) and a project."""
    from datetime import datetime
    from astropy.io.fits import Header
    from photonfinder.core import compress
    from photonfinder.models import (
        LibraryRoot, File, Image, FitsHeader, FileWCS, Project, ProjectFile,
    )

    root = LibraryRoot.create(name="Main", path="/data/", description="Main imaging archive")

    light = File.create(root=root, path="lights/", name="m31.fits", size=1000, mtime_millis=111)
    Image.create(file=light, image_type="LIGHT", filter="Ha", camera="ASI2600",
                 telescope="RC8", object_name="M31", exposure=300.0, gain=100, offset=10,
                 binning=1, set_temp=-10.0, date_obs=datetime(2024, 1, 1, 22, 0, 0),
                 coord_ra=10.68, coord_dec=41.27, coord_radius=0.5, width=6248, height=4176)
    hdr = Header()
    hdr["GAIN"] = 100
    hdr["OBJECT"] = "M31"
    FitsHeader.create(file=light, header=compress(hdr.tostring().encode("ascii")))
    FileWCS.create(file=light, wcs=compress(b"dummy-wcs"))

    dark = File.create(root=root, path="darks/", name="dark.fits", size=2000, mtime_millis=222)
    Image.create(file=dark, image_type="DARK", camera="ASI2600", exposure=300.0, gain=100,
                 binning=1, set_temp=-10.0, date_obs=datetime(2024, 1, 2, 3, 0, 0))

    project = Project.create(name="Andromeda")
    ProjectFile.create(project=project, file=light)

    return context, {"root": root, "light": light, "dark": dark, "project": project}


@pytest.fixture
def solved_sample(sample):
    """`sample` with a genuinely parseable WCS on the LIGHT.

    `sample` stores `b"dummy-wcs"`, which is enough for `has_wcs` but not for anything
    that decodes the solution -- the catalog report silently finds zero matches against
    it. This fixture writes a real header so the footprint test has something to project.
    """
    from astropy.coordinates import SkyCoord
    from photonfinder.core import compress, hp
    from photonfinder.models import FileWCS, Image

    ctx, objs = sample
    hdr = fixed_solution_header()
    FileWCS.update(wcs=compress(hdr.tostring().encode("ascii"))) \
        .where(FileWCS.file == objs["light"].rowid).execute()
    healpix = int(hp.skycoord_to_healpix(SkyCoord(ra=10.68, dec=41.27, unit="deg")))
    Image.update(coord_pix256=healpix).where(Image.file == objs["light"].rowid).execute()
    return ctx, objs


@pytest.fixture
def database():
    db = SqliteDatabase(':memory:', pragmas={
        'journal_mode': 'wal',
        'cache_size': -1 * 64000,  # 64MB
        'foreign_keys': 1
    })

    register_udfs(db)

    db.bind(CORE_MODELS, bind_refs=False, bind_backrefs=False)
    try:
        db.connect()
        db.create_tables(CORE_MODELS)
        yield db
    finally:
        db.close()


@pytest.fixture()
def filesystem():
    dummy_bytes = bytes("DUMMY CONTENT", "UTF-8")
    mem_fs = MemoryFS()
    try:
        dir_light = "test/2021-12-25/Crab Nebula/Light/"
        dir_light_bad = dir_light + "/BAD/"
        dir_flats = "test/2021-12-25/Crab Nebula/Flats/"
        dir_darks = "test/2021-12-26/Darks/"

        mem_fs.makedirs(dir_light_bad)
        mem_fs.makedirs(dir_flats)
        mem_fs.makedirs(dir_darks)
        mem_fs.appendbytes("image01.fits", dummy_bytes)
        mem_fs.appendbytes(dir_light + "image02.fits", dummy_bytes)
        mem_fs.appendbytes(dir_light_bad + "image03.fits", dummy_bytes)
        mem_fs.appendbytes(dir_light + "image04.fits", dummy_bytes)
        mem_fs.appendbytes(dir_flats + "image05.fits", dummy_bytes)
        mem_fs.appendbytes(dir_darks + "image06.fits", dummy_bytes)
        mem_fs.appendbytes(dir_light + "BAD_image07.fits", dummy_bytes)
        mem_fs.appendbytes(dir_darks + "image08.fits.xz", dummy_bytes)
        mem_fs.appendbytes(dir_darks + "statistics.csv", dummy_bytes)

        class FSOpener(Opener):
            protocols = ['test']

            def open_fs(self, fs_url, parse_result, writeable, create, cwd):
                return mem_fs

        from fs.opener import registry
        registry.install(FSOpener())

        yield mem_fs
    finally:
        mem_fs.close()


_HF_REPO = "bcolyn/PhotonFinder-test-data"


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_data():
    if _test_data_available():
        return
    data_dir = Path(__file__).parent / "data"
    try:
        from huggingface_hub import snapshot_download
        print(f"\nTest data not found — downloading {_HF_REPO} from Hugging Face...")
        data_dir.mkdir(exist_ok=True)
        snapshot_download(repo_id=_HF_REPO, repo_type="dataset", local_dir=data_dir)
    except Exception as e:
        warnings.warn(
            f"Could not download test data ({e}) — "
            "tests requiring sample image files will be skipped. "
            f"Download manually from Hugging Face: {_HF_REPO}",
            stacklevel=1,
        )


@pytest.fixture(scope="session")
def global_test_data_dir() -> Path:
    """Provides the test data directory; skips the test if data files are absent."""
    data_dir = Path(__file__).parent / "data"
    if not _test_data_available():
        pytest.skip("Test data files not available (see tests/data/)")
    return data_dir


@pytest.fixture(autouse=True)
def inject_class_fixtures(request):
    """
    Automatically inject fixtures as attributes into test class instances.
    """
    if request.cls:
        cls = request.cls
        fixture_names = getattr(cls, "inject_fixtures", [])
        for name in fixture_names:
            value = request.getfixturevalue(name)
            setattr(request.cls, name, value)

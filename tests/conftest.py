from pathlib import Path

import pytest
from fs.memoryfs import MemoryFS
from fs.opener import Opener
from peewee import SqliteDatabase

from photonfinder.core import ApplicationContext, Settings, StatusReporter
from photonfinder.models import CORE_MODELS


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


@pytest.fixture
def database():
    db = SqliteDatabase(':memory:', pragmas={
        'journal_mode': 'wal',
        'cache_size': -1 * 64000,  # 64MB
        'foreign_keys': 1
    })

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


@pytest.fixture(scope="session")
def global_test_data_dir() -> Path:
    """
    Provides a Path object pointing to the global test data directory.
    """
    return Path(__file__).parent / "data"


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

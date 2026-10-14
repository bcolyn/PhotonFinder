import os
from unittest.mock import patch

import pytest

from photonfinder.models import LibraryRoot, File
from photonfinder.ui.CompressFilesDialog import CompressFilesWorker


@pytest.fixture
def library(context, tmp_path):
    root = LibraryRoot.create(name="Main", path=str(tmp_path).replace("\\", "/") + "/")
    src = tmp_path / "light.fits"
    src.write_bytes(b"SIMPLE  =                    T" + b"\x00" * 2000)
    file = File.create(root=root, path="", name="light.fits", size=src.stat().st_size, mtime_millis=1)
    return CompressFilesWorker(context), file, src


def test_process_renames_row_and_removes_original(library):
    worker, file, src = library

    worker._process(file, ".gz", verify=True, level=1)

    assert File.get_by_id(file.rowid).name == "light.fits.gz"
    assert not src.exists()
    assert (src.parent / "light.fits.gz").exists()


def test_process_rolls_back_when_original_cannot_be_deleted(library):
    worker, file, src = library
    real_unlink = os.unlink

    def unlink(path):
        if os.path.normpath(path) == os.path.normpath(str(src)):
            raise PermissionError("file in use")
        real_unlink(path)

    with patch("photonfinder.ui.CompressFilesDialog.os.unlink", side_effect=unlink):
        worker._process(file, ".gz", verify=True, level=1)

    assert File.get_by_id(file.rowid).name == "light.fits"
    assert src.exists()
    assert sorted(p.name for p in src.parent.iterdir()) == ["light.fits"]


def test_process_keeps_original_when_db_update_fails(library):
    worker, file, src = library

    with patch("photonfinder.ui.CompressFilesDialog.File.update", side_effect=RuntimeError("database is locked")):
        worker._process(file, ".gz", verify=True, level=1)

    assert File.get_by_id(file.rowid).name == "light.fits"
    assert sorted(p.name for p in src.parent.iterdir()) == ["light.fits"]


def test_process_refuses_to_overwrite_existing_archive(library):
    worker, file, src = library
    existing = src.parent / "light.fits.gz"
    existing.write_bytes(b"precious")

    worker._process(file, ".gz", verify=True, level=1)

    assert existing.read_bytes() == b"precious"
    assert src.exists()
    assert File.get_by_id(file.rowid).name == "light.fits"

"""Settings hold plain values. PySide 6.12 refuses to read back a pickled object from the registry."""
import logging
import string
from pathlib import Path

import pytest

from photonfinder.core import Settings
from photonfinder.models import File, Image, LibraryRoot
from photonfinder.ui.ExportDialog import template_filename

winreg = pytest.importorskip("winreg")

ORGANIZATION = "PhotonFinderTests"


def _delete_key(path: str):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, access=winreg.KEY_ALL_ACCESS) as key:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_key(f"{path}\\{child}")
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


@pytest.fixture
def settings():
    """Real Settings on a throwaway registry key, as an INI file doesn't show the problem."""
    settings = Settings(ORGANIZATION, "Values")
    yield settings
    settings.settings.clear()
    settings.settings.sync()
    del settings
    try:
        _delete_key(f"Software\\{ORGANIZATION}")
    except FileNotFoundError:
        pass


@pytest.fixture
def light():
    root = LibraryRoot(name="r", path="/r/")
    file = File(root=root, path="p/", name="light.fits", size=1, mtime_millis=1)
    file.image = Image(file=file, image_type="LIGHT", filter="Ha")
    return file


def _stored(key: str):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\{ORGANIZATION}\\Values") as registry:
        return winreg.QueryValueEx(registry, key)


def test_a_stored_path_setting_is_a_registry_string(settings, light):
    template_filename(light, string.Template("SESS_2020-11-04/$filename"), settings)
    settings.settings.sync()

    assert _stored("last_light_path") == ("SESS_2020-11-04", winreg.REG_SZ)


def test_last_light_path_can_be_used_by_the_next_file(settings, light):
    template_filename(light, string.Template("lights/night/$filename"), settings)

    result = template_filename(light, string.Template("$last_light_path/$filename"), settings)

    assert result == "lights/night/light.fits"


def test_a_setter_stores_any_path_as_text(settings):
    settings.set_last_export_path(Path("some/folder"))
    settings.settings.sync()

    assert _stored("last_export_path") == ("some/folder", winreg.REG_SZ)
    assert Settings(ORGANIZATION, "Values").get_last_export_path() == "some/folder"


class TestAValueSavedByAnOlderVersion:
    @pytest.fixture
    def pickled(self, settings):
        settings.settings.setValue("last_light_path", Path("SESS_2020-11-04"))  # what ExportDialog used to do
        settings.settings.sync()
        return settings

    def test_reads_as_unset(self, pickled):
        assert pickled.get_last_light_path() == ""

    def test_is_removed_and_logged(self, pickled, caplog):
        with caplog.at_level(logging.WARNING):
            pickled.get_last_light_path()
        pickled.settings.sync()

        with pytest.raises(FileNotFoundError):
            _stored("last_light_path")
        assert "last_light_path" in caplog.text

    def test_export_works_and_stores_a_string(self, pickled, light):
        result = template_filename(light, string.Template("SESS_2021/$filename"), pickled)
        pickled.settings.sync()

        assert result == "SESS_2021/light.fits"
        assert _stored("last_light_path") == ("SESS_2021", winreg.REG_SZ)

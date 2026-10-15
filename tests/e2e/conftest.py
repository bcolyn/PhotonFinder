"""End-to-end GUI fixtures: the real MainWindow on a real database, in a sandbox.

Everything a running instance persists -- database, session file, settings -- goes to a
per-test folder (the same layout ``--data-dir`` gives a manual run, which also runs without
the MCP server), and the OS boundary (native file dialogs, message boxes, opening
URLs/Explorer) is replaced by stubs, so a test can neither touch the user's own
PhotonFinder nor hang on an invisible modal. ``no_leaks`` checks the former after the
whole session.

Every test in this folder is marked ``e2e`` and so excluded from a default run; select
them with ``pytest -m e2e``.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox, QInputDialog

from photonfinder.core import ApplicationContext, MCP_PORT_FILENAME, user_app_data_dir
from photonfinder.main import sandbox_settings, default_data_dir
from photonfinder.ui.MainWindow import MainWindow
from tests.e2e.synthetic_library import SyntheticLibraries, build_libraries

_E2E_DIR = Path(__file__).parent
_REGISTRY_KEY = r"Software\AstroFileManager\AstroFileManager"
_GUARDED_FILES = ("session_v1.json", MCP_PORT_FILENAME, "astroFileManager.db")


def _registry_stamp():
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
            return winreg.QueryInfoKey(key)[2]  # last write time
    except FileNotFoundError:
        return None


def _app_data_stamps():
    dirs = {user_app_data_dir(), Path(default_data_dir())}
    return {d / name: (d / name).stat().st_mtime_ns
            for d in dirs for name in _GUARDED_FILES if (d / name).exists()}


def pytest_collection_modifyitems(items):
    for item in items:
        if _E2E_DIR in item.path.parents:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session", autouse=True)
def no_leaks():
    """Fail the run if any e2e test wrote to the user's real settings or app data.

    A copy of PhotonFinder the user is running at the same time can trip this too, by
    saving a setting or closing; rerun with it closed before suspecting the sandbox.
    """
    registry, files = _registry_stamp(), _app_data_stamps()
    yield
    assert _registry_stamp() == registry, f"HKCU\\{_REGISTRY_KEY} was modified"
    assert _app_data_stamps() == files, "the user's PhotonFinder app-data folder was modified"


class Boundary:
    """Records what the app tried to do outside its own window, and answers the static
    dialogs it would otherwise block on.

    Queue answers with ``answer(kind, value)`` before triggering the action; kinds are the
    patched function names (``question``, ``getSaveFileName``, ...). A dialog with no
    queued answer fails the test instead of blocking it.
    """

    def __init__(self):
        self.answers: dict[str, list] = {}
        self.calls: list[tuple[str, tuple]] = []
        self.opened_urls: list = []
        self.processes: list = []

    def answer(self, kind: str, value):
        self.answers.setdefault(kind, []).append(value)

    def _reply(self, kind):
        def reply(*args, **kwargs):
            self.calls.append((kind, args))
            queued = self.answers.get(kind)
            if not queued:
                raise AssertionError(f"Unexpected {kind} dialog: {args[1:3]}")
            return queued.pop(0)
        return staticmethod(reply)


@pytest.fixture
def boundary(monkeypatch) -> Boundary:
    b = Boundary()
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, b._reply(name))
    for name in ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, b._reply(name))
    for name in ("getText", "getItem", "getInt", "getDouble"):
        monkeypatch.setattr(QInputDialog, name, b._reply(name))
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: b.opened_urls.append(url) or True))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: b.processes.append(args))
    return b


class Sandbox:
    """One application data folder, which can be started and stopped like the real app."""

    def __init__(self, data_dir: Path, qapp, qtbot):
        self.data_dir = data_dir
        self.qapp = qapp
        self.qtbot = qtbot
        self.context: ApplicationContext | None = None
        self.window: MainWindow | None = None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "astroFileManager.db"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.ini"

    def start(self) -> MainWindow:
        assert self.window is None, "already running"
        settings = sandbox_settings(self.data_dir)
        # Never launch a solver the developer happens to have installed.
        settings.set_astap_path(str(self.data_dir / "no-astap.exe"))
        settings.set_solve_field_path(str(self.data_dir / "no-solve-field"))
        settings.set_obs_timezone("UTC")
        self.context = ApplicationContext.create_in_app_data(str(self.data_dir), settings)
        self.context.__enter__()
        self.window = MainWindow(self.qapp, self.context, enable_mcp=False)
        self.window.show()
        self.qtbot.waitExposed(self.window)
        self.wait_idle()
        return self.window

    def wait_idle(self):
        """Let background loaders finish and deliver their results to the UI."""
        QThreadPool.globalInstance().waitForDone()
        self.qapp.processEvents()

    def stop(self):
        """Close the window the way the user would, then the database."""
        if self.window is None:
            return
        try:
            self.window.close()
            self.wait_idle()
        finally:
            self.window.deleteLater()
            self.window = None
            self.context.__exit__(None, None, None)
            self.context = None

    def restart(self) -> MainWindow:
        self.stop()
        return self.start()


@pytest.fixture
def sandbox(tmp_path, qapp, qtbot, boundary) -> Sandbox:
    data_dir = tmp_path / "appdata"
    data_dir.mkdir()
    sb = Sandbox(data_dir, qapp, qtbot)
    yield sb
    sb.stop()


@pytest.fixture
def app(sandbox) -> MainWindow:
    """A running app on a fresh, empty sandbox."""
    return sandbox.start()


@pytest.fixture(scope="session", autouse=True)
def libraries() -> SyntheticLibraries:
    """Three library roots of tiny generated FITS/XISF files; see ``synthetic_library``.

    Generated once, before the first e2e test, and removed after the last one, pass or
    fail. It lives in its own temp folder rather than under ``tmp_path``, which pytest keeps
    around for the last few runs. Shared by every test, so a test must not rename, delete
    or mark files bad in it.
    """
    base = Path(tempfile.mkdtemp(prefix="photonfinder-e2e-libraries-"))
    try:
        yield build_libraries(base)
    finally:
        shutil.rmtree(base)

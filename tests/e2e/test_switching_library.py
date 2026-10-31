"""Switching library is refused while something could write to the current one."""
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool

from photonfinder import filesystem, mcp_server
from photonfinder.core import ActiveWriters, DatabaseLock, LibraryBusyError
from photonfinder.filesystem import Importer
from photonfinder.models import CORE_MODELS, File, Image, LibraryRoot, Project, SearchCriteria
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase, ImageAnalysisTask, ImageReindexWorker, \
    MissingFramesAnalysisTask, PlateSolveTask
from photonfinder.ui.CompressFilesDialog import CompressFilesWorker
from photonfinder.ui.DiagnoseQualityDialog import _DiagnosisLoader
from photonfinder.ui.ExportDialog import ExportDialog, ExportWorker
from photonfinder.ui.ImageAnalysisDialog import ImageAnalysisDialog
from photonfinder.ui.ImageViewerWindow import ImageViewerWindow
from photonfinder.ui.MainWindow import LibraryScanWorker
from photonfinder.ui.PlateSolveDialog import PlateSolveDialog
from photonfinder.ui.ProjectEditDialog import ProjectEditDialog
from tests.e2e.helpers import add_libraries, wait_for_scan
from tests.e2e.test_diagnose_quality import GOOD_RESULT, add_unanalysed, search


def switching_is_allowed(app) -> bool:
    app.menu_File.aboutToShow.emit()
    assert app.action_Open_Database.isEnabled() == app.action_Create_Database.isEnabled()
    return app.action_Open_Database.isEnabled()


def stats_rows(path) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("select file_id from imagestats order by 1").fetchall()
    finally:
        connection.close()


def test_a_running_analysis_keeps_writing_to_its_own_library(sandbox, app, monkeypatch, tmp_path):
    first = str(sandbox.context.database_path)
    other = str(tmp_path / "other.db")
    app._switch_database(other)  # same file ids as the first library, but different files
    add_unanalysed(sandbox, "OTHER", 5)
    app._switch_database(first)
    add_unanalysed(sandbox, "M81", 5)
    search(sandbox, app, object_name="M81")

    reached, release = threading.Event(), threading.Event()
    calls = []

    def analyze(name, **_):
        calls.append(name)
        if len(calls) == 3:
            reached.set()
            release.wait(20)
        return GOOD_RESULT

    monkeypatch.setattr("photonfinder.ui.BackgroundLoader.analyze_file", analyze)
    panel = app.get_current_search_panel()
    panel.analyse_images()
    assert reached.wait(10)

    assert not switching_is_allowed(app)
    assert "Image analysis" in app.action_Open_Database.toolTip()
    with pytest.raises(LibraryBusyError, match="Image analysis"):
        app._switch_database(other)
    assert app.tabWidget.count() == 1 and app.get_current_search_panel() is panel  # refused before touching anything
    assert sandbox.context.database_path == first

    release.set()
    sandbox.wait_idle()
    assert stats_rows(first) == [(i,) for i in range(1, 6)]
    assert stats_rows(other) == []

    panel.findChild(ImageAnalysisDialog).close()
    assert switching_is_allowed(app)
    assert "Image analysis" not in app.action_Open_Database.toolTip()


# --- Every writer ----------------------------------------------------------------------


def _scan(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(LibraryScanWorker, "run", lambda self: wait())
    assert app._start_scan(lambda: LibraryScanWorker(sandbox.context, roots=[]))


def _compress(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(CompressFilesWorker, "_run", lambda self, *a: wait())
    return CompressFilesWorker(sandbox.context).compress([], "gz", False, 1, 1)


def _analyse_images(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(ImageAnalysisTask, "_process_files", lambda self: wait())
    task = ImageAnalysisTask(sandbox.context, SearchCriteria(), [])
    task.start()
    return task


def _analyse_missing(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(MissingFramesAnalysisTask, "_process_files", lambda self: wait())
    task = MissingFramesAnalysisTask(sandbox.context, [])
    task.start()
    return task


def _reindex(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(ImageReindexWorker, "_reindex_images_task", lambda self: wait())
    worker = ImageReindexWorker(sandbox.context)
    worker.reindex_images()
    return worker


def _plate_solve(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(PlateSolveTask, "_process_files", lambda self: wait())
    task = PlateSolveTask(sandbox.context, SearchCriteria(), [], solver=None)
    task.start()
    return task


def _plate_solve_from_mcp(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(sandbox.context.settings, "get_mcp_allow_plate_solve", lambda: True)
    entered = threading.Event()

    def build_solver(*_):
        entered.set()
        wait()

    monkeypatch.setattr(mcp_server, "_build_solver", build_solver)
    thread = threading.Thread(target=mcp_server.query_plate_solve, args=(sandbox.context, [1]), daemon=True)
    thread.start()
    assert entered.wait(10)
    return thread


def _export(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(ExportWorker, "_export_files_task", lambda self: wait())
    worker = ExportWorker(sandbox.context)
    worker.export_files(SearchCriteria(), [], "", False, "", 0)
    return worker


def _save_diagnosis(sandbox, app, monkeypatch, wait):
    monkeypatch.setattr(_DiagnosisLoader, "_save", lambda self, verdicts: wait())
    loader = _DiagnosisLoader(sandbox.context)
    loader.save({})
    return loader


@pytest.mark.parametrize("start, name", [
    (_scan, "Library scan"),
    (_compress, "Compress Files"),
    (_analyse_images, "Image analysis"),
    (_analyse_missing, "Image analysis"),
    (_reindex, "Rebuild index"),
    (_plate_solve, "Plate solving"),
    (_plate_solve_from_mcp, "Plate solving (MCP)"),
    (_export, "Export"),
    (_save_diagnosis, "Saving diagnosis"),
], ids=lambda value: value if isinstance(value, str) else None)
def test_a_running_writer_blocks_switching_until_it_ends(sandbox, app, monkeypatch, tmp_path, start, name):
    release = threading.Event()
    keep_alive = start(sandbox, app, monkeypatch, lambda: release.wait(20))

    assert sandbox.context.writers.running() == [name]
    assert not switching_is_allowed(app)
    with pytest.raises(LibraryBusyError, match=name.replace("(", r"\(").replace(")", r"\)")):
        app._switch_database(str(tmp_path / "other.db"))

    release.set()
    sandbox.qtbot.waitUntil(lambda: not sandbox.context.writers.running())
    del keep_alive
    sandbox.wait_idle()
    assert switching_is_allowed(app)
    app._switch_database(str(tmp_path / "other.db"))
    sandbox.wait_idle()
    assert sandbox.context.database_path == str(tmp_path / "other.db")


def test_a_writer_that_fails_still_allows_switching(sandbox, app, monkeypatch):
    def fail():
        raise RuntimeError("the job failed")

    _reindex(sandbox, app, monkeypatch, fail)
    sandbox.wait_idle()
    assert switching_is_allowed(app)


# --- Every window that can start a writer ----------------------------------------------


def _export_window(sandbox, app):
    return ExportDialog(sandbox.context, SearchCriteria(), [], parent=app.get_current_search_panel())


def _plate_solve_window(sandbox, app):
    return PlateSolveDialog(sandbox.context, [], SearchCriteria(), parent=app.get_current_search_panel())


def _analysis_window(sandbox, app):
    return ImageAnalysisDialog(sandbox.context, [], SearchCriteria(), parent=app)


def _project_window(sandbox, app):
    return ProjectEditDialog(sandbox.context, project=Project(name="P"), parent=app)


def _viewer_window(sandbox, app):
    return ImageViewerWindow(context=sandbox.context, parent=app)


@pytest.mark.parametrize("create, label", [
    (_export_window, "Export"),
    (_plate_solve_window, "Plate Solve"),
    (_analysis_window, "Analyse Images"),
    (_project_window, "Edit Project"),
    (_viewer_window, "Image Viewer"),
], ids=lambda value: value if isinstance(value, str) else None)
def test_an_open_window_that_can_write_blocks_switching_until_closed(sandbox, app, tmp_path, create, label):
    window = create(sandbox, app)
    assert switching_is_allowed(app)  # created but not shown yet

    window.show()
    sandbox.wait_idle()
    assert not switching_is_allowed(app)
    assert label in app.action_Open_Database.toolTip()
    with pytest.raises(LibraryBusyError, match=label):
        app._switch_database(str(tmp_path / "other.db"))

    window.close()
    sandbox.wait_idle()
    assert switching_is_allowed(app)


def test_read_only_windows_do_not_block_switching(sandbox, app):
    search(sandbox, app)
    app.get_current_search_panel().report_catalog()
    sandbox.wait_idle()
    assert switching_is_allowed(app)


# --- Around the check ------------------------------------------------------------------


def test_a_writer_in_another_window_does_not_block_switching(sandbox, app, tmp_path):
    other_window = DatabaseLock(sandbox.context.database_path, ".write.lock")
    assert other_window.acquire(blocking=False)
    try:
        assert not sandbox.context.library_write_lock.acquire(blocking=False)  # it really does hold the library
        assert switching_is_allowed(app)
        app._switch_database(str(tmp_path / "other.db"))
    finally:
        other_window.release()
    sandbox.wait_idle()
    assert sandbox.context.database_path == str(tmp_path / "other.db")


@pytest.mark.parametrize("action", ["action_Open_Database", "action_Create_Database"])
def test_the_action_explains_a_refusal_before_asking_for_a_file(sandbox, app, boundary, action):
    window = _viewer_window(sandbox, app)
    window.show()
    boundary.answer("warning", None)

    getattr(app, action).trigger()

    assert [kind for kind, _ in boundary.calls] == ["warning"]
    assert "Image Viewer" in boundary.calls[0][1][2]
    window.close()


def test_the_menu_action_switches_library_when_nothing_blocks_it(sandbox, app, boundary, tmp_path):
    path = str(tmp_path / "other.db")
    boundary.answer("getSaveFileName", (path, ""))
    app.menu_File.aboutToShow.emit()
    app.action_Create_Database.trigger()
    sandbox.wait_idle()
    assert sandbox.context.database_path == path


def test_mcp_cannot_start_a_writer_during_a_switch(sandbox, monkeypatch):
    sandbox.start()
    monkeypatch.setattr(sandbox.context.settings, "get_mcp_allow_plate_solve", lambda: True)
    with sandbox.context.writers.switching():
        result = mcp_server.query_plate_solve(sandbox.context, [1])
    assert "being switched" in result["error"]
    assert sandbox.context.writers.running() == []


# --- A read-only job spanning a switch ------------------------------------------------


def _row_count(path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"select count(*) from {table}").fetchone()[0]
    finally:
        connection.close()


@pytest.mark.parametrize("repeat", range(5))
@pytest.mark.parametrize("nested", [False, True], ids=["plain", "nested_bind"])
def test_a_reader_spanning_a_switch_leaves_the_models_on_the_new_library(sandbox, app, tmp_path, nested, repeat):
    context = sandbox.context
    first = str(context.database_path)
    other = str(tmp_path / "other.db")
    reached, release = threading.Event(), threading.Event()

    def read_only_job():
        if nested:
            with context.database.bind_ctx([File, Image]):
                reached.set()
                release.wait(20)
        else:
            reached.set()
            release.wait(20)

    loader = BackgroundLoaderBase(context)
    loader.run_in_thread(read_only_job)
    assert reached.wait(10)
    assert not context.writers.running()  # a reader doesn't stop the switch

    app._switch_database(other)
    # The new tab's loaders finish first, so nothing rebinds the models after this job ends.
    sandbox.qtbot.waitUntil(lambda: QThreadPool.globalInstance().activeThreadCount() == 1)
    release.set()
    sandbox.wait_idle()

    assert [m.__name__ for m in CORE_MODELS if m._meta.database is not context.database] == []
    LibraryRoot.create(name="after the switch", path="/x/")  # as LibraryRootEditDialog writes
    assert _row_count(first, "libraryroot") == 0
    assert _row_count(other, "libraryroot") == 1


def test_a_job_that_runs_with_no_database_open_is_skipped_without_an_error(sandbox, app, caplog):
    ran = []
    loader = BackgroundLoaderBase(sandbox.context)
    runnable = loader._create_runnable(lambda: ran.append(True))
    database, sandbox.context.database = sandbox.context.database, None
    try:
        runnable.run()
    finally:
        sandbox.context.database = database
    assert not ran
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


# --- Closing the window ----------------------------------------------------------------


def _scan_until_reached(sandbox, monkeypatch, target, attribute: str):
    """Start a real scan and slow down its file loop, so that closing lands in the middle.
    Returns the original, as the shared monkeypatch also holds the boundary's patches."""
    reached = threading.Event()
    original = getattr(target, attribute)

    def slowed(*args, **kwargs):
        reached.set()
        time.sleep(0.02)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, attribute, slowed)
    sandbox.window.action_Scan_Libraries.trigger()
    assert reached.wait(20)
    return original


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]


def _scan_stopped(sandbox, worker) -> bool:
    return (worker.isFinished() and sandbox.window.scan_worker is None and not sandbox.context.writers.running()
            and sandbox.context.library_write_lock.acquire(blocking=False))


def test_closing_during_a_scan_cancels_it_and_keeps_the_library(sandbox, app, modals, boundary, libraries, monkeypatch,
                                                                caplog):
    root = libraries.roots["rig"]
    add_libraries(sandbox, modals, boundary, {"rig": root}, scan=True)
    wait_for_scan(sandbox)
    expected = sorted(sandbox.grid().column("File name"))
    assert expected

    # A rescan abandoned partway has only seen some folders, and must not count the rest as deleted.
    original = _scan_until_reached(sandbox, monkeypatch, Importer, "_import_file")
    worker = app.scan_worker
    app.close()

    assert _scan_stopped(sandbox, worker)
    sandbox.context.library_write_lock.release()
    assert _errors(caplog) == []
    monkeypatch.setattr(Importer, "_import_file", original)
    sandbox.restart()
    assert sorted(sandbox.grid().column("File name")) == expected


def test_a_scan_closed_during_its_header_stage_is_finished_by_the_next_scan(sandbox, app, modals, boundary, libraries,
                                                                            monkeypatch, caplog):
    root = libraries.roots["rig"]
    add_libraries(sandbox, modals, boundary, {"rig": root}, scan=False)

    original = _scan_until_reached(sandbox, monkeypatch, filesystem, "_handle_file_metadata_safely")
    worker = app.scan_worker
    app.close()

    assert _scan_stopped(sandbox, worker)
    sandbox.context.library_write_lock.release()
    assert _errors(caplog) == []
    files = _row_count(sandbox.database_path, "file")
    assert 0 < _row_count(sandbox.database_path, "image") < files  # really stopped partway

    monkeypatch.setattr(filesystem, "_handle_file_metadata_safely", original)
    sandbox.restart()
    sandbox.window.action_Scan_Libraries.trigger()
    wait_for_scan(sandbox)
    assert _row_count(sandbox.database_path, "image") == files


def test_closing_stops_the_mcp_server_even_if_saving_the_session_fails(sandbox, app, monkeypatch, caplog):
    stopped = []
    app.mcp_controller = SimpleNamespace(stop=lambda: stopped.append(sandbox.context.database is not None))

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(app, "save_session", fail)
    app.close()

    assert stopped == [True]  # before the database closes
    assert any("disk full" in message for message in _errors(caplog))
    app.mcp_controller = None


def test_two_writers_with_one_name_both_have_to_end():
    writers = ActiveWriters()
    writers.begin("Export")
    writers.begin("Export")
    writers.end("Export")
    assert writers.running() == ["Export"]
    with pytest.raises(LibraryBusyError):
        with writers.switching():
            pass
    writers.end("Export")
    assert writers.running() == []
    with writers.switching():
        with pytest.raises(LibraryBusyError):
            writers.begin("Export")
    writers.begin("Export")  # the switch is over
    assert writers.running() == ["Export"]

"""Search results and filter-combo loaders only deliver results of the latest request.

The database query is replaced by a fake whose speed the test controls, so a superseded
search can be made to finish after the newer one.
"""
import threading

import pytest
from PySide6.QtWidgets import QComboBox

from photonfinder.models import SearchCriteria
from photonfinder.ui import BackgroundLoader
from photonfinder.ui.BackgroundLoader import SearchResultsLoader, GenericControlLoader


class _ControlledSearch:
    """Stands in for search_files. A search on a name in `held` waits until released."""

    def __init__(self):
        self.held: dict[str, threading.Event] = {}
        self.seen: list[tuple[str, int]] = []
        self.started = threading.Semaphore(0)

    def hold(self, name):
        self.held[name] = threading.Event()

    def release(self, name):
        self.held[name].set()

    def __call__(self, criteria, page, page_size):
        name = criteria.object_name
        self.seen.append((name, page))
        self.started.release()
        if name in self.held:
            assert self.held[name].wait(5)
        if name == "boom":
            raise RuntimeError("query failed")
        # read the criteria again after waiting: a query reads them throughout its run
        return [f"{criteria.object_name}-p{page}"], 1000, True


@pytest.fixture
def fake_search(monkeypatch):
    fake = _ControlledSearch()
    monkeypatch.setattr(BackgroundLoader, "search_files", fake)
    return fake


@pytest.fixture
def loader(qtbot, context):
    loader = SearchResultsLoader(context)
    received = []
    loader.results_loaded.connect(lambda *args: received.append(args))
    loader.received = received
    yield loader
    BackgroundLoader.QThreadPool.globalInstance().waitForDone(5000)


def _wait_started(fake, n=1):
    for _ in range(n):
        assert fake.started.acquire(timeout=5)


def test_a_newer_search_is_not_dropped_while_an_older_one_runs(qtbot, loader, fake_search):
    criteria = SearchCriteria(object_name="old")
    fake_search.hold("old")
    loader.search(criteria)
    _wait_started(fake_search)

    criteria.object_name = "new"  # the panel edits the same object in place
    loader.search(criteria)
    qtbot.waitUntil(lambda: bool(loader.received), timeout=5000)
    fake_search.release("old")
    BackgroundLoader.QThreadPool.globalInstance().waitForDone(5000)
    qtbot.wait(50)  # let the late, superseded page arrive

    assert ("old", 0) in fake_search.seen and ("new", 0) in fake_search.seen
    assert loader.received == [(["new-p0"], 0, 1000, True)]


def test_the_running_query_keeps_the_criteria_it_started_with(qtbot, loader, fake_search):
    criteria = SearchCriteria(object_name="snap")
    fake_search.hold("snap")
    loader.search(criteria)
    _wait_started(fake_search)
    criteria.object_name = "edited"
    fake_search.release("snap")
    qtbot.waitUntil(lambda: bool(loader.received), timeout=5000)

    assert loader.received == [(["snap-p0"], 0, 1000, True)]


def test_a_late_page_of_a_superseded_search_is_dropped(qtbot, loader, fake_search):
    loader.search(SearchCriteria(object_name="first"))
    qtbot.waitUntil(lambda: len(loader.received) == 1, timeout=5000)

    fake_search.hold("first")
    loader.load_more()  # page 1 of the first search, held back
    _wait_started(fake_search, 2)
    loader.search(SearchCriteria(object_name="second"))
    qtbot.waitUntil(lambda: len(loader.received) == 2, timeout=5000)
    fake_search.release("first")
    BackgroundLoader.QThreadPool.globalInstance().waitForDone(5000)
    qtbot.wait(50)

    assert [r[0] for r in loader.received] == [["first-p0"], ["second-p0"]]


def test_more_pages_continue_the_current_search(qtbot, loader, fake_search):
    loader.search(SearchCriteria(object_name="m"))
    qtbot.waitUntil(lambda: len(loader.received) == 1, timeout=5000)
    loader.load_more()
    qtbot.waitUntil(lambda: len(loader.received) == 2, timeout=5000)

    assert loader.received[1] == (["m-p1"], 1, 1000, True)


def test_the_same_search_is_not_started_twice(qtbot, loader, fake_search):
    fake_search.hold("same")
    loader.search(SearchCriteria(object_name="same"))
    loader.search(SearchCriteria(object_name="same"))
    _wait_started(fake_search)
    fake_search.release("same")
    qtbot.waitUntil(lambda: bool(loader.received), timeout=5000)
    BackgroundLoader.QThreadPool.globalInstance().waitForDone(5000)
    qtbot.wait(50)

    assert fake_search.seen == [("same", 0)]
    assert len(loader.received) == 1


def test_a_failing_search_reports_an_empty_result(qtbot, loader, fake_search, caplog):
    loader.search(SearchCriteria(object_name="boom"))
    qtbot.waitUntil(lambda: bool(loader.received), timeout=5000)

    assert loader.received == [([], 0, 0, False)]
    assert "query failed" in caplog.text


def test_combo_options_of_a_superseded_refresh_are_dropped(qtbot, context):
    combo = QComboBox()
    qtbot.addWidget(combo)
    loader = GenericControlLoader(context)
    received = []
    loader.data_ready.connect(lambda widget, data: received.append(data))
    old_running, release_old = threading.Event(), threading.Event()

    def slow_old(criteria):
        old_running.set()
        assert release_old.wait(5)
        return ["old"]

    loader.run_tasks([(combo, slow_old)], SearchCriteria())
    assert old_running.wait(5)
    loader.run_tasks([(combo, lambda criteria: ["new"])], SearchCriteria())
    qtbot.waitUntil(lambda: bool(received), timeout=5000)
    release_old.set()
    BackgroundLoader.QThreadPool.globalInstance().waitForDone(5000)
    qtbot.wait(50)

    assert received == [["new"]]

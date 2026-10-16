"""Helpers for driving the running app from an e2e test: modal dialogs and the search grid."""
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from PySide6.QtCore import Qt, QTimer
from pytestqt.exceptions import TimeoutError
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from photonfinder.ui.SearchPanel import SearchPanel

_POLL_MS = 50


class _Expectation:
    def __init__(self, dialog_type: type, fn: Callable[[QWidget], None]):
        self.dialog_type = dialog_type
        self.fn = fn
        self.started = False
        self.done = False
        self.error: BaseException | None = None
        self.timer: QTimer | None = None


class Modals:
    """Handles the dialogs the app opens with ``exec()``, and keeps unhandled ones from
    hanging the run.

    ``exec()`` blocks the test until the dialog closes, so a dialog has to be handled from
    inside its own event loop: ``handle`` polls for it with a timer, runs ``fn(dialog)``,
    and accepts the dialog if ``fn`` left it open::

        with modals.handle(LibraryRootDialog, add_root):
            app.actionManage_Libraries.trigger()

    Expectations are served in the order they were registered, so a dialog that opens a
    nested one can be handled by registering both up front. Leaving the ``with`` block
    waits for every expected dialog to have been handled, and re-raises what ``fn`` raised.

    Any other modal is the watchdog's: one that stays open for ``timeout`` seconds without
    an expectation for it is rejected, so ``exec()`` returns, and the test fails at teardown.
    """

    def __init__(self, qtbot, timeout: float = 5.0):
        self.qtbot = qtbot
        self.timeout = timeout
        self.unhandled: list[str] = []
        self._pending: list[_Expectation] = []
        self._claimed: set[int] = set()
        self._first_seen: tuple[int, float] | None = None
        self._watchdog = QTimer()
        self._watchdog.setInterval(_POLL_MS)
        self._watchdog.timeout.connect(self._watch)
        self._watchdog.start()

    @contextmanager
    def handle(self, dialog_type: type = QDialog, fn: Callable[[QWidget], None] = lambda d: None) -> Iterator[None]:
        exp = _Expectation(dialog_type, fn)
        exp.timer = QTimer()
        exp.timer.setInterval(_POLL_MS)
        exp.timer.timeout.connect(lambda: self._try(exp))
        self._pending.append(exp)
        exp.timer.start()
        try:
            yield
            try:
                self.qtbot.waitUntil(lambda: exp.done, timeout=int(self.timeout * 1000))
            except TimeoutError:
                what = "was opened" if not exp.started else "was handled in time"
                raise AssertionError(f"No {dialog_type.__name__} {what}") from None
        finally:
            exp.timer.stop()
            if exp in self._pending:
                self._pending.remove(exp)
        if exp.error is not None:
            raise exp.error

    def _try(self, exp: _Expectation):
        if exp.started or self._pending[0] is not exp:
            return
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, exp.dialog_type) or id(dialog) in self._claimed:
            return
        exp.started = True
        exp.timer.stop()
        self._pending.remove(exp)
        self._claimed.add(id(dialog))
        try:
            exp.fn(dialog)
            if dialog.isVisible():
                _close(dialog, accept=True)
                if dialog.isVisible():  # e.g. a dialog whose accept() refused invalid input
                    raise AssertionError(f"{type(dialog).__name__} stayed open after accept()")
        except BaseException as e:
            exp.error = e
            if dialog.isVisible():
                _close(dialog, accept=False)
        finally:
            self._claimed.discard(id(dialog))
            exp.done = True

    def _watch(self):
        dialog = QApplication.activeModalWidget()
        if dialog is None or id(dialog) in self._claimed or any(
                isinstance(dialog, e.dialog_type) for e in self._pending):
            self._first_seen = None
            return
        now = time.monotonic()
        if self._first_seen is None or self._first_seen[0] != id(dialog):
            self._first_seen = (id(dialog), now)
        elif now - self._first_seen[1] >= self.timeout:
            self._first_seen = None
            self.unhandled.append(f"{type(dialog).__name__} '{dialog.windowTitle()}'")
            _close(dialog, accept=False)

    def stop(self):
        self._watchdog.stop()
        for exp in self._pending:
            exp.timer.stop()


def _close(dialog: QWidget, accept: bool):
    if isinstance(dialog, QDialog):
        dialog.accept() if accept else dialog.reject()
    else:
        dialog.close()


class Grid:
    """Reads a search panel's result grid through its model, in the order the view shows it."""

    def __init__(self, panel: SearchPanel, wait_idle: Callable[[], None]):
        self.panel = panel
        self.wait_idle = wait_idle

    @property
    def model(self):
        return self.panel.proxy_model

    @property
    def total(self) -> int:
        """The number of matching files the search reported, loaded or not."""
        return self.panel.total_files

    def headers(self) -> list[str]:
        return [self.model.headerData(c, Qt.Orientation.Horizontal) for c in range(self.model.columnCount())]

    def load_all(self):
        """Fetch the remaining pages; the panel loads 100 rows at a time as the user scrolls."""
        while self.panel.has_more_results:
            loaded = self.model.rowCount()
            self.panel.load_all_remaining_data()
            self.wait_idle()
            assert self.model.rowCount() > loaded, "loading the next page of results added no rows"

    def row_count(self) -> int:
        self.load_all()
        return self.model.rowCount()

    def column(self, name: str) -> list[str]:
        self.load_all()
        col = self.headers().index(name)
        return [self.model.index(row, col).data() for row in range(self.model.rowCount())]

    def rows(self) -> list[dict[str, str]]:
        self.load_all()
        headers = self.headers()
        return [{h: self.model.index(row, col).data() for col, h in enumerate(headers)}
                for row in range(self.model.rowCount())]

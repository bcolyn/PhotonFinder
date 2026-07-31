"""Tests for the stdio stub and the cross-process plumbing it relies on.

The stub is what MCP clients spawn: it discovers the port the running application
advertises, starts the application if nothing is listening, and relays JSON-RPC. These
tests cover the discovery and relay logic without a real server or a real GUI.
"""
import json
import sys
from pathlib import Path

import pytest

from photonfinder.core import SolveLock, mcp_port_file, user_app_data_dir
from photonfinder import mcp_stub

# Captured before the autouse fixture redirects it, so the drift check below compares the
# stub's real derivation rather than the test's stand-in.
_STUB_PORT_FILE = mcp_stub.mcp_port_file


@pytest.fixture(autouse=True)
def isolated_port_file(tmp_path, monkeypatch):
    """Keep the tests off the real port file, which a running PhotonFinder may own."""
    path = tmp_path / "mcp_port.txt"
    monkeypatch.setattr(mcp_stub, "mcp_port_file", lambda: path)
    return path


@pytest.fixture
def stub_manifest(monkeypatch):
    """Replace the baked manifest, for tests about serving rather than its contents."""
    def use(manifest):
        monkeypatch.setattr(mcp_stub, "load_manifest", lambda: manifest)
    return use


def test_manifest_is_up_to_date():
    """The baked tool list must match what mcp_server would actually advertise.

    This is what makes generating it safe: change a tool without regenerating and this
    fails, rather than agents being shown a stale list. Regenerate with
    `uv run python scripts/build_mcp_manifest.py`.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_mcp_manifest",
        Path(__file__).resolve().parent.parent / "scripts" / "build_mcp_manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from photonfinder.mcp_manifest import MANIFEST
    assert module.build_manifest() == MANIFEST, (
        "photonfinder/mcp_manifest.py is stale -- run "
        "`uv run python scripts/build_mcp_manifest.py`")


def test_every_tool_declares_whether_it_only_reads():
    """Clients prompt from annotations, so a tool that writes must not look harmless.

    Only `plate_solve_files` may be non-read-only: it runs an external solver, writes a
    solution to the library, and can upload frames to astrometry.net.
    """
    from photonfinder.mcp_manifest import MANIFEST

    writers = set()
    for tool in MANIFEST["tools"]:
        annotations = tool.get("annotations")
        assert annotations, f"{tool['name']} declares no annotations"
        assert "readOnlyHint" in annotations, f"{tool['name']} does not say if it writes"
        if not annotations["readOnlyHint"]:
            writers.add(tool["name"])

    assert writers == {"plate_solve_files"}


def test_read_only_tools_are_marked_local():
    """None of the read-only tools contacts an online service; say so to the client."""
    from photonfinder.mcp_manifest import MANIFEST
    for tool in MANIFEST["tools"]:
        annotations = tool["annotations"]
        if annotations["readOnlyHint"]:
            assert annotations["openWorldHint"] is False, tool["name"]


def test_baked_manifest_lists_every_server_tool():
    """A sanity check on the generator itself, independent of the file on disk."""
    from photonfinder.mcp_manifest import MANIFEST
    names = {t["name"] for t in MANIFEST["tools"]}
    assert {"search_files", "get_file_details", "lookup_object"} <= names
    # Always advertised, and refused at call time when the setting is off -- a tool that
    # came and went with a setting could not be baked.
    assert "plate_solve_files" in names


class _Out:
    """Collects the JSON-RPC messages the stub writes to stdout."""

    def __init__(self):
        self.raw = b""

    def write(self, data):
        self.raw += data

    def flush(self):
        pass

    @property
    def messages(self):
        return [json.loads(line) for line in self.raw.decode().splitlines() if line]

    @property
    def one(self):
        assert len(self.messages) == 1, self.messages
        return self.messages[0]


def send(message: dict) -> _Out:
    out = _Out()
    mcp_stub.handle(json.dumps(message), out)
    return out


def _never_start(monkeypatch):
    """Fail loudly if anything tries to launch the application."""
    def boom():
        raise AssertionError("the application must not be started here")
    monkeypatch.setattr(mcp_stub, "launch", boom)


# --- Opening a session must not start anything --------------------------------------

def test_initialize_does_not_touch_the_application(monkeypatch):
    _never_start(monkeypatch)
    monkeypatch.setattr(mcp_stub, "probe", lambda port: False)

    reply = send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18"}}).one

    assert reply["result"]["serverInfo"]["name"] == "PhotonFinder"
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    # The baked list never changes, so we must not promise clients that it might.
    assert reply["result"]["capabilities"]["tools"]["listChanged"] is False


def test_tools_list_works_while_the_application_is_closed(monkeypatch):
    """The whole point of baking the manifest: a full tool list with nothing running."""
    _never_start(monkeypatch)

    names = [t["name"] for t in send({"jsonrpc": "2.0", "id": 1,
                                      "method": "tools/list"}).one["result"]["tools"]]

    assert "search_files" in names
    assert "plate_solve_files" in names
    assert names[0] == mcp_stub.START_TOOL


def test_initialize_serves_the_baked_instructions(monkeypatch, stub_manifest):
    _never_start(monkeypatch)
    stub_manifest({"instructions": "real ones", "tools": []})
    reply = send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).one
    assert reply["result"]["instructions"].startswith("real ones")


def test_the_agent_is_told_to_ask_before_starting_the_app(monkeypatch):
    """Launching a desktop application is the user's decision, not the agent's."""
    _never_start(monkeypatch)

    instructions = send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {}}).one["result"]["instructions"]
    assert "ask the user" in instructions.lower()

    start_tool = next(t for t in send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                      .one["result"]["tools"] if t["name"] == mcp_stub.START_TOOL)
    assert "ask the user for permission" in start_tool["description"].lower()
    # The machine-readable half: clients prompt based on annotations, not prose.
    assert start_tool["annotations"]["readOnlyHint"] is False
    assert start_tool["annotations"]["idempotentHint"] is True


def test_refusal_tells_the_agent_to_ask_first(monkeypatch):
    _never_start(monkeypatch)
    monkeypatch.setattr(mcp_stub, "running_port", lambda: None)

    text = send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "search_files", "arguments": {}}}
                ).one["result"]["content"][0]["text"]

    assert "ask the user" in text.lower()
    assert mcp_stub.START_TOOL in text


def test_notifications_are_swallowed(monkeypatch):
    _never_start(monkeypatch)
    assert send({"jsonrpc": "2.0", "method": "notifications/initialized"}).raw == b""


# --- Calling a tool while the application is closed ----------------------------------

def test_data_tool_refuses_instead_of_starting_the_app(monkeypatch):
    _never_start(monkeypatch)
    monkeypatch.setattr(mcp_stub, "running_port", lambda: None)

    reply = send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "search_files", "arguments": {}}}).one

    assert reply["result"]["isError"] is True
    assert mcp_stub.START_TOOL in reply["result"]["content"][0]["text"]


def test_start_tool_starts_the_application(monkeypatch):
    calls = {"n": 0}

    def start():
        calls["n"] += 1
        return True, "PhotonFinder started and is ready."

    monkeypatch.setattr(mcp_stub, "start_application", start)

    reply = send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": mcp_stub.START_TOOL, "arguments": {}}}).messages[0]

    assert calls["n"] == 1
    assert reply["result"]["isError"] is False


def test_start_tool_reports_failure_as_a_tool_error(monkeypatch):
    monkeypatch.setattr(mcp_stub, "start_application", lambda: (False, "nope"))
    reply = send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": mcp_stub.START_TOOL, "arguments": {}}}).messages[0]
    assert reply["result"]["isError"] is True
    assert "nope" in reply["result"]["content"][0]["text"]


def test_starting_never_changes_the_tool_list(monkeypatch):
    """The list is baked, so starting the application cannot alter it."""
    monkeypatch.setattr(mcp_stub, "start_application", lambda: (True, "started"))
    out = send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": mcp_stub.START_TOOL, "arguments": {}}})
    assert len(out.messages) == 1
    assert not any(m.get("method") == "notifications/tools/list_changed"
                   for m in out.messages)


def test_start_tool_is_a_no_op_when_already_running(monkeypatch):
    _never_start(monkeypatch)
    monkeypatch.setattr(mcp_stub, "running_port", lambda: 4321)
    ok, text = mcp_stub.start_application()
    assert ok and "already running" in text


# --- Calling a tool while the application is running ---------------------------------

def test_data_tool_is_forwarded_when_running(monkeypatch):
    _never_start(monkeypatch)
    monkeypatch.setattr(mcp_stub, "running_port", lambda: 4321)
    forwarded = {}

    def fake_forward(port, payload):
        forwarded["port"] = port
        forwarded["payload"] = payload
        return ['{"jsonrpc":"2.0","id":1,"result":{"content":[]}}']

    monkeypatch.setattr(mcp_stub, "forward", fake_forward)

    reply = send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "search_files", "arguments": {}}}).one

    assert forwarded["port"] == 4321
    assert reply["result"] == {"content": []}


# --- Port discovery ----------------------------------------------------------------

def test_read_port_returns_none_without_a_file(isolated_port_file):
    assert mcp_stub.read_port() is None


def test_read_port_parses_the_file(isolated_port_file):
    isolated_port_file.write_text("54321", encoding="utf-8")
    assert mcp_stub.read_port() == 54321


def test_read_port_survives_a_corrupt_file(isolated_port_file):
    isolated_port_file.write_text("not-a-port", encoding="utf-8")
    assert mcp_stub.read_port() is None


def test_stub_and_core_agree_on_the_port_file():
    """The stub duplicates the path derivation, so it must not drift from core's."""
    assert _STUB_PORT_FILE() == mcp_port_file()
    assert mcp_stub.user_app_data_dir() == user_app_data_dir()


def test_a_stale_port_file_is_not_trusted(isolated_port_file, monkeypatch):
    """A crash leaves the file behind; only a live probe decides."""
    isolated_port_file.write_text("54321", encoding="utf-8")
    monkeypatch.setattr(mcp_stub, "probe", lambda port: False)
    assert mcp_stub.running_port() is None


def test_running_port_accepts_a_live_port(isolated_port_file, monkeypatch):
    isolated_port_file.write_text("54321", encoding="utf-8")
    monkeypatch.setattr(mcp_stub, "probe", lambda port: True)
    assert mcp_stub.running_port() == 54321


def test_start_application_waits_for_the_app_to_serve(isolated_port_file, monkeypatch):
    monkeypatch.setattr(mcp_stub, "POLL_INTERVAL", 0)
    started = {"done": False}

    def start():
        started["done"] = True
        isolated_port_file.write_text("6000", encoding="utf-8")
        return True, None

    monkeypatch.setattr(mcp_stub, "launch", start)
    monkeypatch.setattr(mcp_stub, "probe", lambda port: started["done"])

    ok, text = mcp_stub.start_application()
    assert ok and "ready" in text


def test_start_application_gives_up_after_the_timeout(isolated_port_file, monkeypatch):
    monkeypatch.setattr(mcp_stub, "POLL_INTERVAL", 0)
    monkeypatch.setattr(mcp_stub, "STARTUP_TIMEOUT", 0.01)
    monkeypatch.setattr(mcp_stub, "launch", lambda: (True, None))
    monkeypatch.setattr(mcp_stub, "probe", lambda port: False)

    ok, text = mcp_stub.start_application()
    assert not ok and "did not finish starting" in text


# --- Launching outside the sandbox --------------------------------------------------

def test_frozen_builds_launch_through_explorer(monkeypatch, tmp_path):
    """Launching the app directly would pass the client's package identity to it."""
    exe = tmp_path / "photonfinder.exe"
    exe.touch()
    monkeypatch.setattr(mcp_stub.sys, "frozen", True, raising=False)
    monkeypatch.setattr(mcp_stub.sys, "executable", str(tmp_path / "photonfinder-mcp.exe"))

    command, problem = mcp_stub.application_command()
    assert problem is None
    assert command == ["explorer.exe", str(exe)]


def test_frozen_build_without_the_application_gives_up(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_stub.sys, "frozen", True, raising=False)
    monkeypatch.setattr(mcp_stub.sys, "executable", str(tmp_path / "photonfinder-mcp.exe"))
    command, problem = mcp_stub.application_command()
    assert command is None
    assert "photonfinder.exe was not found" in problem


def test_source_checkout_starts_the_module_directly(monkeypatch):
    monkeypatch.delattr(mcp_stub.sys, "frozen", raising=False)
    monkeypatch.setattr(mcp_stub, "in_package_sandbox", lambda: False)
    command, problem = mcp_stub.application_command()
    assert problem is None
    assert command[1:] == ["-m", "photonfinder.main"]


def test_source_checkout_refuses_to_start_inside_a_sandbox(monkeypatch):
    """A packaged client running the dev stub: launching directly would sandbox the app.

    There is no photonfinder.exe to hand to explorer here, and an application started
    inside the client's container reads a virtualized registry -- default settings and an
    empty library. Failing with an explanation beats starting the wrong thing.
    """
    monkeypatch.delattr(mcp_stub.sys, "frozen", raising=False)
    monkeypatch.setattr(mcp_stub, "in_package_sandbox", lambda: True)

    command, problem = mcp_stub.application_command()

    assert command is None
    assert "sandboxed" in problem
    assert "photonfinder-mcp.exe" in problem


def test_the_sandbox_refusal_reaches_the_agent(monkeypatch):
    monkeypatch.delattr(mcp_stub.sys, "frozen", raising=False)
    monkeypatch.setattr(mcp_stub, "in_package_sandbox", lambda: True)
    monkeypatch.setattr(mcp_stub, "running_port", lambda: None)

    reply = send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": mcp_stub.START_TOOL, "arguments": {}}}).messages[0]

    assert reply["result"]["isError"] is True
    assert "start PhotonFinder" in reply["result"]["content"][0]["text"]


def test_log_file_option_is_honoured(tmp_path, monkeypatch):
    """Client configs carry `--log-file`; ignoring it loses the user their diagnostics."""
    target = tmp_path / "mcp.log"
    monkeypatch.setattr(mcp_stub, "_log_file", None)
    mcp_stub.parse_args(["--log-file", str(target)])
    try:
        mcp_stub.log("hello")
        assert "hello" in target.read_text(encoding="utf-8")
    finally:
        mcp_stub._log_file = None


def test_log_file_accepts_the_equals_form(tmp_path, monkeypatch):
    target = tmp_path / "mcp.log"
    monkeypatch.setattr(mcp_stub, "_log_file", None)
    mcp_stub.parse_args([f"--log-file={target}"])
    try:
        assert mcp_stub._log_file == target
    finally:
        mcp_stub._log_file = None


def test_unknown_options_are_ignored(monkeypatch):
    """A stale flag in a hand-edited client config must not kill the server."""
    monkeypatch.setattr(mcp_stub, "_log_file", None)
    mcp_stub.parse_args(["--database", "C:/old/path.db", "--nonsense"])
    assert mcp_stub._log_file is None


def test_logging_survives_an_unwritable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_stub, "_log_file", tmp_path / "no-such-dir" / "mcp.log")
    try:
        mcp_stub.log("must not raise")
    finally:
        mcp_stub._log_file = None


def test_package_detection_says_no_outside_a_package():
    """This test process is not packaged, so the real detection must agree."""
    assert mcp_stub.in_package_sandbox() is False


def test_source_checkout_uses_the_windowless_interpreter(monkeypatch, tmp_path):
    """python.exe is a console application: starting the GUI with it opens a terminal."""
    pythonw = tmp_path / "pythonw.exe"
    pythonw.touch()
    monkeypatch.setattr(mcp_stub.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(mcp_stub.sys, "platform", "win32")

    assert mcp_stub.python_for_gui() == str(pythonw)


def test_falls_back_when_there_is_no_windowless_interpreter(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    monkeypatch.setattr(mcp_stub.sys, "executable", str(python))
    monkeypatch.setattr(mcp_stub.sys, "platform", "win32")

    assert mcp_stub.python_for_gui() == str(python)


# --- Relaying ------------------------------------------------------------------------

class _Response:
    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(monkeypatch, response):
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["body"] = request.data
        sent["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return response

    monkeypatch.setattr(mcp_stub.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_forward_returns_a_plain_json_response(monkeypatch):
    body = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    sent = _capture(monkeypatch, _Response(body, "application/json"))

    assert mcp_stub.forward(1234, b'{"id":1}') == [body.decode()]
    assert sent["url"] == "http://127.0.0.1:1234/mcp"
    assert sent["body"] == b'{"id":1}'


def test_forward_unwraps_an_sse_response(monkeypatch):
    body = (b"event: message\n"
            b'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
            b"\n")
    _capture(monkeypatch, _Response(body, "text/event-stream"))

    messages = mcp_stub.forward(1234, b'{"id":1}')

    assert json.loads(messages[0])["result"] == {"ok": True}


def test_forward_returns_nothing_for_an_empty_response(monkeypatch):
    _capture(monkeypatch, _Response(b"", "application/json"))
    assert mcp_stub.forward(1234, b'{"id":1}') == []


def test_error_response_is_valid_jsonrpc():
    payload = json.loads(mcp_stub.error_response(7, "boom"))
    assert payload["id"] == 7
    assert payload["jsonrpc"] == "2.0"
    assert "boom" in payload["error"]["message"]


# --- The controller in a windowed process --------------------------------------------

def test_controller_starts_without_a_stdout(monkeypatch, tmp_path):
    """The windowed build has sys.stdout = None, which uvicorn's default log config dies on.

    That failure only ever appeared in the packaged application -- running from source
    there is always a stdout -- so it is worth reproducing here rather than in a build.
    """
    from photonfinder.core import ApplicationContext, StatusReporter
    from photonfinder.mcp_server import McpServerController

    port_file = tmp_path / "mcp_port.txt"
    monkeypatch.setattr("photonfinder.mcp_server.mcp_port_file", lambda: port_file)
    monkeypatch.setattr(sys, "stdout", None)

    class _Settings:
        def get_last_database_path(self): return ""
        def set_last_database_path(self, value): pass
        def get_known_fits_keywords(self): return []
        def get_mcp_allow_plate_solve(self): return False
        def sync(self): pass

    context = ApplicationContext(":memory:", _Settings())
    context.set_status_reporter(StatusReporter())
    with context:
        controller = McpServerController(context)
        try:
            controller.start()
            assert controller.running
            assert port_file.read_text(encoding="utf-8") == str(controller.port)
        finally:
            controller.stop()
    assert not port_file.exists()


# --- Plate-solve lock (still cross-process: GUI vs. anything else) -------------------

def test_solve_lock_is_held_against_another_process(tmp_path):
    db = tmp_path / "library.db"
    first, second = SolveLock(db), SolveLock(db)

    assert first.acquire(blocking=False)
    try:
        assert not second.acquire(blocking=False)
    finally:
        first.release()

    assert second.acquire(blocking=False)
    second.release()


def test_solve_lock_without_a_database_file():
    lock = SolveLock(":memory:")
    assert lock.acquire(blocking=False)
    assert not lock.acquire(blocking=False)
    lock.release()
    assert lock.acquire(blocking=False)
    lock.release()


def test_solve_lock_follows_a_database_switch(tmp_path):
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    lock = SolveLock(first)
    lock.set_database_path(second)

    assert lock.acquire(blocking=False)
    try:
        assert SolveLock(first).acquire(blocking=False)
        assert not SolveLock(second).acquire(blocking=False)
    finally:
        lock.release()

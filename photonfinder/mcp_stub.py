"""stdio stub that bridges an MCP client to a running PhotonFinder.

This is the program MCP clients spawn (`photonfinder-mcp`). It does no library work of
its own: it forwards tool calls to the loopback server the application hosts.

Two things it deliberately does *not* do:

* It does not start PhotonFinder just because a client connected. Opening an agent
  session is not a request to launch a desktop application, so `initialize` and
  `tools/list` are answered here, from the manifest the application publishes when it
  runs. An agent can see what is on offer without anything being started.
* It does not start PhotonFinder behind the user's back when a tool is called. Starting
  it is itself a tool, `start_photonfinder`, so the client asks for approval the same way
  it does for anything else. Data tools called while the application is closed come back
  with an error saying so.

Why the work happens in the application rather than here: a sandboxed client -- Claude
Desktop is MSIX-packaged -- gives everything it launches a *virtualized* registry and
redirected app data. A server running there would read default settings rather than the
user's (wrong ASTAP path, no astrometry.net key, plate solving silently disabled), and
would hand that same broken environment to ASTAP and solve-field.

Depends on the standard library only: no Qt, no peewee, no astropy. It is launched afresh
for every client session, so it must not drag in the application's dependency tree just
to relay JSON.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONNECT_TIMEOUT = 5
REQUEST_TIMEOUT = 300  # plate solving can legitimately take minutes
STARTUP_TIMEOUT = 90  # cold-starting the application, including its splash and DB open
POLL_INTERVAL = 0.5

PROTOCOL_VERSION = "2025-06-18"
START_TOOL = "start_photonfinder"
START_TOOL_DEFINITION = {
    "name": START_TOOL,
    "description": (
        "Start the PhotonFinder desktop application, which every other tool needs in "
        "order to run. ASK THE USER FOR PERMISSION BEFORE CALLING THIS: it launches a "
        "desktop application and opens a window on their screen, so it should never be "
        "called speculatively or without their agreement. Call it when another tool "
        "reports that PhotonFinder is not running, and prefer letting the user start it "
        "themselves if they would rather. Returns immediately if it is already running."
    ),
    # Annotations are the machine-readable half of the same request: clients decide
    # whether to prompt from these rather than by reading the description. Not read-only
    # because it starts a process on the user's machine; idempotent because a second call
    # while it is up does nothing.
    "annotations": {
        "title": "Start PhotonFinder",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}
NOT_RUNNING_MESSAGE = (
    "PhotonFinder is not running, so this tool cannot reach the library. Ask the user "
    f"whether to start it, and if they agree call `{START_TOOL}`, then retry. Do not "
    "start it without asking."
)


_log_file: Path | None = None


def log(message: str) -> None:
    """Diagnostics go to stderr; stdout carries the MCP protocol and nothing else.

    The packaged build is windowless, so there is no console to fall back on: when a
    client spawns us it supplies the standard handles, and when nothing does, sys.stderr
    is None and there is simply nowhere to write -- hence `--log-file`, which does not
    depend on the client surfacing our stderr anywhere the user can find.
    """
    line = f"photonfinder-mcp: {message}"
    if sys.stderr is not None:
        print(line, file=sys.stderr, flush=True)
    if _log_file is not None:
        try:
            with _log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {line}\n")
        except OSError:
            pass  # logging must never break the relay


# --- Talking to the application ------------------------------------------------------

def user_app_data_dir() -> Path:
    """Mirror of ``core.user_app_data_dir``, duplicated to keep this module dependency-free.

    Derived from the user profile rather than %LOCALAPPDATA%, because that environment
    variable is redirected inside a packaged client's sandbox and would point at a private
    copy the application never writes to.
    """
    if sys.platform == 'win32':
        return Path.home() / "AppData" / "Local" / "photonfinder"
    return Path.home() / ".local" / "share" / "photonfinder"


def mcp_port_file() -> Path:
    return user_app_data_dir() / "mcp_port.txt"


def read_port() -> int | None:
    try:
        return int(mcp_port_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}/mcp"


def probe(port: int) -> bool:
    """Check that something is actually serving MCP on this port.

    The port file outlives a crash, so its presence proves nothing on its own. An empty
    POST is answered by the server (with a protocol error) but refused if it is gone --
    either way we learn what we need without a real request.
    """
    try:
        request = urllib.request.Request(
            endpoint(port), data=b"{}", method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
        urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT).read()
        return True
    except urllib.error.HTTPError:
        return True  # answered, just not happily -- the server is there
    except OSError:
        return False


def running_port() -> int | None:
    """The port of a live application, or None. Never starts anything."""
    port = read_port()
    return port if port is not None and probe(port) else None


def load_manifest() -> dict:
    """The tool set, generated from mcp_server.py at build time (see mcp_manifest.py).

    Baked rather than fetched: it is the same for every run, so there is no reason for
    listing the tools to depend on the application being up, or on a file it wrote.
    """
    from photonfinder.mcp_manifest import MANIFEST
    return MANIFEST


# --- Starting the application --------------------------------------------------------

def in_package_sandbox() -> bool:
    """Are we running with MSIX package identity, inherited from the client that spawned us?

    This, not whether we are frozen, is what decides whether launching a child directly is
    safe: package identity passes to child processes, so an application started from here
    would inherit a virtualized registry and redirected app data -- reading default
    settings and an empty library instead of the user's.
    """
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        length = ctypes.c_uint32(0)
        rc = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        return rc != 15700  # APPMODEL_ERROR_NO_PACKAGE
    except Exception:
        # Better to assume we are not sandboxed than to refuse to start on a machine
        # where this API is unavailable; the packaged path below is safe regardless.
        return False


def application_command() -> tuple[list[str] | None, str | None]:
    """How to start PhotonFinder, as (command, problem). Exactly one is set."""
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable).parent / "photonfinder.exe"
        if not exe.exists():
            return None, ("Could not start PhotonFinder: photonfinder.exe was not found "
                          f"next to this program ({exe.parent}). Start PhotonFinder "
                          "manually and retry.")
        # Launching it ourselves would pass our package identity on to it and put the
        # application inside the client's sandbox -- the very thing we are avoiding.
        # explorer.exe runs outside, so what it starts does too.
        return ["explorer.exe", str(exe)], None

    if in_package_sandbox():
        # A source checkout started by a packaged client. There is no photonfinder.exe to
        # hand to explorer, and launching the interpreter directly would drag the
        # application into the sandbox, where it would find none of the user's settings
        # and an empty library -- worse than not starting at all.
        return None, ("Cannot start PhotonFinder from a source checkout when this client "
                      "runs sandboxed: the application would start with the wrong "
                      "settings and an empty library. Ask the user to start PhotonFinder "
                      "themselves and retry, or to point this client at the packaged "
                      "photonfinder-mcp.exe, which can start it correctly.")

    # Source checkout, no sandbox: start it directly. pythonw.exe rather than python.exe --
    # the latter is a console application, so Windows gives it a console window of its own
    # and the user gets a stray terminal alongside the GUI.
    return [python_for_gui(), "-m", "photonfinder.main"], None


def python_for_gui() -> str:
    """The interpreter to start a GUI program with: windowless where one exists."""
    if sys.platform == 'win32':
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys.executable


def launch() -> tuple[bool, str | None]:
    """Start the application. Returns (started, problem to report to the agent)."""
    command, problem = application_command()
    if problem is not None:
        log(problem)
        return False, problem
    log(f"starting PhotonFinder: {' '.join(command)}")
    try:
        if sys.platform == 'win32' and not getattr(sys, 'frozen', False):
            if start_outside_job(command):
                return True, None
            log("falling back to a direct start; PhotonFinder will close with this session")
        # explorer.exe returns immediately (and often non-zero); it is fire-and-forget.
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True, None
    except OSError as e:
        log(f"could not start PhotonFinder: {e}")
        return False, f"Could not start PhotonFinder: {e}"


def start_outside_job(command: list[str]) -> bool:
    """Start `command` via WMI, so it does not belong to our process's job object.

    Clients run their servers inside a job object that kills everything in it when the
    session ends. A process we create joins that job, so ending a chat would take the
    user's PhotonFinder down with it -- abruptly, losing the session state it would
    normally save on close. WMI creates the process from its own service instead, leaving
    it outside the job entirely.

    The packaged build does not need this: `explorer.exe` hands the request to the running
    shell, which starts the application outside the job for the same reason. Direct
    execution is not equivalent -- `os.startfile` on an .exe calls CreateProcess in this
    process, and the child joins the job like any other.
    """
    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
              f"-Arguments @{{CommandLine={quote(subprocess.list2cmdline(command))}; "
              f"CurrentDirectory={quote(str(Path(__file__).resolve().parent.parent))}}}; "
              "exit $r.ReturnValue")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"could not start PhotonFinder outside the client's job object: {e}")
        return False
    if result.returncode != 0:
        log("could not start PhotonFinder outside the client's job object "
            f"(Win32_Process.Create returned {result.returncode})")
        return False
    return True


def start_application() -> tuple[bool, str]:
    """Start PhotonFinder and wait for it to serve. Returns (ok, message for the agent)."""
    port = running_port()
    if port is not None:
        return True, "PhotonFinder is already running."

    started, problem = launch()
    if not started:
        return False, problem

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        port = running_port()
        if port is not None:
            log(f"connected to PhotonFinder on port {port}")
            return True, "PhotonFinder started and is ready."
    return False, (f"PhotonFinder did not finish starting within {STARTUP_TIMEOUT} "
                   "seconds. It may still be loading; retry shortly.")


# --- Relaying ------------------------------------------------------------------------

def forward(port: int, payload: bytes):
    """POST one MCP message and return every JSON-RPC message in the response.

    The application's server is stateless, so each request stands on its own -- no
    handshake to replay, which is what lets this stub answer `initialize` itself.
    """
    request = urllib.request.Request(
        endpoint(port), data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")

    if not body:
        return []
    if "text/event-stream" in content_type:
        return [line[5:].strip() for line in body.decode("utf-8").splitlines()
                if line.startswith("data:") and line[5:].strip()]
    return [body.decode("utf-8").strip()]


# --- Responses we build ourselves ----------------------------------------------------

def result(request_id, payload) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": payload})


def tool_text(request_id, text: str, is_error: bool = False) -> str:
    return result(request_id, {"content": [{"type": "text", "text": text}],
                               "isError": is_error})


def error_response(request_id, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": -32603, "message": message}})


def initialize_result(request_id, params: dict) -> str:
    instructions = load_manifest().get("instructions", "")
    # Stated up front, not just on the tool: the application is the user's, and an agent
    # should not decide on its own to put a window on their screen.
    instructions += (
        f" All tools require the PhotonFinder application to be running. `{START_TOOL}` "
        "starts it, but ask the user before calling it -- it opens a window on their "
        "desktop. If the user prefers, they can simply start PhotonFinder themselves."
    )
    payload = {
        "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "PhotonFinder", "version": "1.0.0"},
        "instructions": instructions,
    }
    return result(request_id, payload)


def tools_list_result(request_id) -> str:
    tools = load_manifest().get("tools", [])
    return result(request_id, {"tools": [START_TOOL_DEFINITION] + tools})


# --- Main loop -----------------------------------------------------------------------

def handle(line: str, out) -> None:
    """Handle one incoming JSON-RPC message, writing any response to `out`."""
    try:
        message = json.loads(line)
    except ValueError:
        return
    if not isinstance(message, dict):
        return

    request_id = message.get("id")
    method = message.get("method")

    # Notifications: nothing to answer. The session lives here, not in the application.
    if request_id is None:
        return

    def reply(text: str) -> None:
        out.write((text + "\n").encode("utf-8"))
        out.flush()

    if method == "initialize":
        reply(initialize_result(request_id, message.get("params") or {}))
        return
    if method == "tools/list":
        reply(tools_list_result(request_id))
        return
    if method == "ping":
        reply(result(request_id, {}))
        return

    if method == "tools/call" and (message.get("params") or {}).get("name") == START_TOOL:
        ok, text = start_application()
        reply(tool_text(request_id, text, is_error=not ok))
        return

    port = running_port()
    if port is None:
        # Refuse rather than launch: starting a desktop application is the user's call,
        # and `start_photonfinder` is where they get asked.
        if method == "tools/call":
            reply(tool_text(request_id, NOT_RUNNING_MESSAGE, is_error=True))
        else:
            reply(error_response(request_id, NOT_RUNNING_MESSAGE))
        return

    try:
        for response in forward(port, line.encode("utf-8")):
            reply(response)
    except Exception as e:
        log(f"request failed: {e}")
        reply(error_response(request_id, f"Could not reach PhotonFinder: {e}"))


def parse_args(argv: list[str]) -> None:
    """Handle the few options a client config may carry.

    Unknown options are ignored rather than fatal: this program is launched by a client
    config the user edits by hand, and a stale flag should not take the whole server down.
    """
    global _log_file
    for index, argument in enumerate(argv):
        if argument == "--log-file" and index + 1 < len(argv):
            _log_file = Path(argv[index + 1])
        elif argument.startswith("--log-file="):
            _log_file = Path(argument.split("=", 1)[1])


def main() -> None:
    parse_args(sys.argv[1:])

    if sys.stdin is None or sys.stdout is None:
        # Started outside an MCP client -- double-clicked, most likely. There is no
        # protocol stream to serve and, being windowless, nowhere to say so.
        log("no stdin/stdout: this program is meant to be started by an MCP client")
        raise SystemExit(2)

    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").strip()
        if line:
            handle(line, out)


if __name__ == '__main__':
    main()

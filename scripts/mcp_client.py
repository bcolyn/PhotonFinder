"""A small MCP client for exercising PhotonFinder's server by hand.

The packaged stub is awkward to debug -- it is windowless and driven by a client you do
not control -- so this drives the source version the same way a real client would, using
the official MCP SDK rather than hand-rolled JSON, so protocol mistakes surface here.

    uv run python scripts/mcp_client.py                     # interactive (IPython)
    uv run python scripts/mcp_client.py list
    uv run python scripts/mcp_client.py schema search_files
    uv run python scripts/mcp_client.py call list_library_roots
    uv run python scripts/mcp_client.py call search_files '{"criteria": {"type": "LIGHT"}}'

By default it spawns `python -m photonfinder.mcp_stub`, so you exercise the whole chain:
stub, its start-the-application logic, and the server inside the application.

    --direct    skip the stub and talk to the running application's own port, which is
                how you tell a stub problem from a server problem.

`list` shows each tool's read-only annotation, since that is what decides whether a client
asks the user before running it. `schema` prints one tool's full JSON input schema.

Interactive mode drops into an IPython shell with `tools()`, `schema(name)` and
`call(name, {...})` bound to a live session, so you get history, completion and
multi-line editing for free.
"""
import argparse
import asyncio
import json
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from photonfinder.core import mcp_port_file


@asynccontextmanager
async def connect(direct: bool):
    """Open a session, either through the stub or straight at the application."""
    if direct:
        from mcp.client.streamable_http import streamable_http_client

        try:
            port = int(mcp_port_file().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            raise SystemExit("PhotonFinder does not appear to be running: no port file at "
                             f"{mcp_port_file()}. Start it, or drop --direct.")
        try:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except* Exception as failures:
            # A port file outlives a crash, so a dead port is the common failure here, and
            # the raw ExceptionGroup traceback buries it. Keep the cause, drop the noise.
            cause = failures.exceptions[0]
            raise SystemExit(f"Could not talk to PhotonFinder on port {port} "
                             f"({type(cause).__name__}: {cause}). The port file at "
                             f"{mcp_port_file()} may be stale; start PhotonFinder again.")
        return

    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "photonfinder.mcp_stub"],
                                   cwd=str(Path(__file__).resolve().parent.parent))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()
            print(f"connected to {result.serverInfo.name} {result.serverInfo.version}")
            yield session


async def show_tools(session) -> None:
    for tool in (await session.list_tools()).tools:
        annotations = tool.annotations
        access = "read-only" if annotations and annotations.readOnlyHint else "NEEDS APPROVAL"
        description = tool.description or ""
        first_line = description.splitlines()[0] if description else ""
        print(f"{tool.name:22} {access:15} {first_line[:60]}")
    sys.stdout.flush()


async def show_schema(session, name: str) -> None:
    for tool in (await session.list_tools()).tools:
        if tool.name == name:
            print(f"{tool.name} - {tool.description or ''}")
            print(json.dumps(tool.inputSchema, indent=2))
            return
    print(f"no such tool: {name}")


async def call_tool(session, name: str, arguments: dict) -> None:
    result = await session.call_tool(name, arguments)
    if result.isError:
        print("ERROR")
    for block in result.content:
        print(getattr(block, "text", block))


class AsyncBridge:
    """Runs the MCP session on a background thread's event loop, so an IPython shell
    (which has no event loop of its own) can drive it with plain synchronous calls.

    The `connect()` context manager is entered and exited from the same long-lived task
    (`_hold_session`), since anyio's cancel scopes are bound to the task that opened
    them -- entering via one submitted coroutine and exiting via another blows up with
    "cancel scope in a different task".
    """

    def __init__(self, direct: bool):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None
        self.session = None
        self._session_future = asyncio.run_coroutine_threadsafe(
            self._hold_session(direct), self.loop)
        self._ready.wait()
        if self._error is not None:
            raise self._error

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _hold_session(self, direct: bool) -> None:
        self._stop = asyncio.Event()
        try:
            async with connect(direct) as session:
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except Exception as e:
            self._error = e
            self._ready.set()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def tools(self) -> None:
        self._submit(show_tools(self.session)).result()

    def schema(self, name: str) -> None:
        self._submit(show_schema(self.session, name)).result()

    def call(self, name: str, arguments: dict | None = None) -> None:
        self._submit(call_tool(self.session, name, arguments or {})).result()

    def close(self) -> None:
        if self.session is not None:
            self.loop.call_soon_threadsafe(self._stop.set)
            self._session_future.result()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join()


REPL_BANNER = 'tools()  |  schema("tool_name")  |  call("tool_name", {...})  |  quit to exit'


def repl(direct: bool) -> None:
    import pydoc

    from IPython import embed

    def help_(*args, **kwargs):
        # bare `help()` is far more likely to mean "what can I do here" than "start
        # pydoc's interactive browser"; anything with arguments still goes to pydoc.
        if not args and not kwargs:
            print(REPL_BANNER)
        else:
            pydoc.help(*args, **kwargs)

    bridge = AsyncBridge(direct)
    try:
        embed(
            user_ns={"tools": bridge.tools, "schema": bridge.schema, "call": bridge.call,
                    "help": help_},
            banner1=REPL_BANNER + "\n",
            colors="neutral",
        )
    finally:
        bridge.close()


async def run(args) -> None:
    async with connect(args.direct) as session:
        if args.command == "list":
            await show_tools(session)
        elif args.command == "schema":
            await show_schema(session, args.tool)
        elif args.command == "call":
            arguments = json.loads(args.arguments) if args.arguments else {}
            await call_tool(session, args.tool, arguments)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--direct", action="store_true",
                        help="talk to the running application instead of via the stub")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="list the available tools")
    schema = sub.add_parser("schema", help="show one tool's input schema")
    schema.add_argument("tool")
    call = sub.add_parser("call", help="call one tool")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", help="JSON object of arguments")
    args = parser.parse_args()
    if args.command is None:
        repl(args.direct)
    else:
        asyncio.run(run(args))


if __name__ == '__main__':
    main()

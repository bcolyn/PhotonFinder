"""A small MCP client for exercising PhotonFinder's server by hand.

The packaged stub is awkward to debug -- it is windowless and driven by a client you do
not control -- so this drives the source version the same way a real client would, using
the official MCP SDK rather than hand-rolled JSON, so protocol mistakes surface here.

    uv run python scripts/mcp_client.py                     # interactive
    uv run python scripts/mcp_client.py list
    uv run python scripts/mcp_client.py call list_library_roots
    uv run python scripts/mcp_client.py call search_files '{"criteria": {"type": "LIGHT"}}'

By default it spawns `python -m photonfinder.mcp_stub`, so you exercise the whole chain:
stub, its start-the-application logic, and the server inside the application.

    --direct    skip the stub and talk to the running application's own port, which is
                how you tell a stub problem from a server problem.

`list` shows each tool's read-only annotation, since that is what decides whether a client
asks the user before running it.
"""
import argparse
import asyncio
import json
import sys
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
        from mcp.client.streamable_http import streamablehttp_client

        try:
            port = int(mcp_port_file().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            raise SystemExit("PhotonFinder does not appear to be running: no port file at "
                             f"{mcp_port_file()}. Start it, or drop --direct.")
        try:
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
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
        print(f"{tool.name:22} {access:15} {(tool.description or '').splitlines()[0][:60]}")
    sys.stdout.flush()


async def call_tool(session, name: str, arguments: dict) -> None:
    result = await session.call_tool(name, arguments)
    if result.isError:
        print("ERROR")
    for block in result.content:
        print(getattr(block, "text", block))


async def repl(session) -> None:
    # TODO: this hand-rolled loop is fine for now, but if it grows (history, completion,
    # multi-line args) consider swapping it for an IPython embedded shell instead.
    print("tools | <name> [json args] | quit")
    loop = asyncio.get_running_loop()
    while True:
        try:
            print("> ", end="", flush=True)
            # readline off the event loop, so a slow tool call cannot block input.
            line = (await loop.run_in_executor(None, sys.stdin.readline)).strip()
            if not sys.stdin.isatty():
                # no keystroke echo to end the prompt line, so supply it ourselves.
                print(line)
        except KeyboardInterrupt:
            return
        if not line or line in ("quit", "exit"):
            return
        name, _, raw = line.partition(" ")
        if name == "tools":
            await show_tools(session)
            continue
        try:
            arguments = json.loads(raw) if raw.strip() else {}
        except ValueError as e:
            print(f"arguments are not JSON: {e}")
            continue
        try:
            await call_tool(session, name, arguments)
        except Exception as e:
            print(f"call failed: {e}")


async def run(args) -> None:
    async with connect(args.direct) as session:
        if args.command == "list":
            await show_tools(session)
        elif args.command == "call":
            arguments = json.loads(args.arguments) if args.arguments else {}
            await call_tool(session, args.tool, arguments)
        else:
            await repl(session)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--direct", action="store_true",
                        help="talk to the running application instead of via the stub")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="list the available tools")
    call = sub.add_parser("call", help="call one tool")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", help="JSON object of arguments")
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()

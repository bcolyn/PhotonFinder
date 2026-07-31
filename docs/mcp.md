# AI Agent Access (MCP)

PhotonFinder ships an [MCP](https://modelcontextprotocol.io) server that lets AI agents —
Claude Code, Claude Desktop, and any other MCP client — search your library, inspect file
metadata and FITS headers, and resolve objects against the bundled catalog.

## How it fits together

```
your MCP client
  └─ photonfinder-mcp        a small stdio program the client starts
       └── loopback ──▶ PhotonFinder
                          ├─ your settings, your library
                          └─ ASTAP / solve-field
```

`photonfinder-mcp` does no work of its own. It relays tool calls to the server the
application hosts on a loopback port. You never see a "server disconnected" error just
because the application was closed.

**Connecting an agent does not start PhotonFinder.** The stub answers the handshake and
lists the available tools by itself, from a manifest generated when PhotonFinder is built,
so an agent can see what is on offer without a window appearing on your desktop.
The application is only started when you agree to it: starting it is a tool of its own,
`start_photonfinder`, so your client asks for approval exactly as it does for any other
tool. A tool that needs the library while it is closed comes back with an error telling
the agent to call `start_photonfinder` first — never a silent launch.

If PhotonFinder is already running, none of this applies: tools are simply forwarded.

Once started, PhotonFinder is yours to close. It keeps running when the agent's session
ends, so a chat you finish does not take your library window with it.

One combination cannot start it for you: a **source checkout driven by a packaged client**
(Claude Desktop). The packaged stub starts `photonfinder.exe` via `explorer.exe`, which
runs outside the sandbox; a source checkout has no such executable to hand over, and
starting the interpreter directly would pull the application into the sandbox — where it
would find none of your settings and an empty library. The stub detects this and reports
it instead. Start PhotonFinder yourself, or use the packaged `photonfinder-mcp.exe`.

The work happens in the application's process on purpose. Claude Desktop is an MSIX
packaged app, so everything it launches inherits a *virtualized* registry and redirected
app data: a server running there would read default settings instead of yours — wrong
ASTAP path, no astrometry.net key, plate solving silently disabled — and would hand that
same environment to ASTAP and solve-field. Keeping the stub thin means PhotonFinder, and
everything it spawns, runs outside that sandbox with your real configuration.

## Setting it up

`photonfinder-mcp.exe` sits next to `photonfinder.exe` in the folder you unzipped.

```
claude mcp add photonfinder -- "C:\Program Files\PhotonFinder\photonfinder-mcp.exe"
```

Per-client configuration — Claude Code and Claude Desktop, running from a release or from
a source checkout — is in the [project README](../Readme.md#-ai-agent-access-mcp).

## Which database it uses

Whichever one PhotonFinder has open — there is nothing to configure. The agent sees
exactly the library you see, including a database you switch to mid-session.

If the application is not running and you approve `start_photonfinder`, it opens the
database it last used, exactly as it would had you launched it yourself.

The running application publishes its port to `%LOCALAPPDATA%\photonfinder\mcp_port.txt`,
which the stub reads from your user profile — the one path a sandboxed client cannot
redirect. That is the only state passed between them. A port left behind by a crash is
harmless: the stub checks that something is actually answering before trusting it.

The tool list is not passed at runtime at all. It is the same for every run, so it is
generated into `photonfinder/mcp_manifest.py` at build time, much as the Qt `.ui` files
are. This is why `plate_solve_files` is always listed even when the setting is off: a tool
that came and went would not be bakeable, and calling it while disabled returns an error
naming the setting to turn on, which an agent can pass along.

## Logs

The stub writes diagnostics to standard error, which the MCP client captures; standard
output carries the MCP protocol and never receives log output. Add `--log-file <path>` to
the stub's arguments in your client config to get a copy you control — useful when a
client buries its server logs:

```
photonfinder-mcp.exe --log-file "C:\Users\you\photonfinder-mcp.log"
```

Give an absolute path: the stub's working directory is whatever the client chose. The
interesting log is usually the application's own, since that is where the tools run:

- **PhotonFinder**: `%LOCALAPPDATA%\photonfinder\photonfinder.log` (daily rotation, 9 kept)
- **Claude Code** captures stub output under
  `%LOCALAPPDATA%\claude-cli-nodejs\Cache\<project-dir>\mcp-logs-photonfinder\*.jsonl`
- **Claude Desktop** keeps it in `%APPDATA%\Claude\logs\`, one file per server
  (`mcp-server-photonfinder.log`)

## What agents can do

| Tool | Purpose |
| --- | --- |
| `start_photonfinder` | Start the application. Every other tool needs it running |

The agent is told, in the server instructions, in the tool description and through the
tool's MCP annotations (`readOnlyHint: false`), that it should ask you before calling
`start_photonfinder` — launching a desktop application is your decision. Most clients also
prompt for approval on their own account. You can always just start PhotonFinder yourself
instead.

The eight tools below are annotated `readOnlyHint: true`, so a client can let an agent
search and read metadata freely while still asking you about the two that are not —
`start_photonfinder` and `plate_solve_files`. They are also marked as contacting no online
service.

Read-only, always available once it is running:

| Tool | Purpose |
| --- | --- |
| `search_files` | Search the library with the same criteria as the Advanced Search panel |
| `get_file_details` | Full metadata and decompressed FITS header for one file |
| `list_library_roots` | The configured library roots |
| `list_projects` / `get_project_details` | Projects and their summaries |
| `list_distinct_values` | Valid values for filter, type, camera, telescope, object name |
| `list_catalogs` / `lookup_object` | Resolve an object's RA/Dec from the local catalog |

`lookup_object` uses PhotonFinder's bundled catalog database only — no online service
(Simbad, Telescopius) is ever contacted.

### Plate solving (opt-in)

**Settings → MCP Server → "Allow AI agents to trigger plate solving"** enables
`plate_solve_files`, which runs your configured solver on up to 10 files per call and
writes the resulting WCS and coordinates to the library. It is off by default, and it is
the only tool that modifies anything. The tool is always listed; while the setting is off
it refuses every call, so no solver runs without your say-so. Because the solver is
launched by PhotonFinder rather than by the agent's client, it runs with your real solver
paths and outside any client sandbox.

A plate solve started by an agent and one started from the application's Plate Solve
dialog cannot run at the same time; whichever comes second reports that a solve is already
running.

## Upgrading from earlier setups

Earlier versions exposed `http://127.0.0.1:8765/mcp` directly and were typically reached
through `mcp_proxy`. Remove that entry and point your client at `photonfinder-mcp`
instead: the same endpoint underneath, but on a port chosen automatically, and a closed
application gives you an offer to start it rather than a connection failure. The
`mcp_enabled` and `mcp_port` settings are gone.

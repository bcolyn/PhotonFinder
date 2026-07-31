# PhotonFinder

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-blue.svg)](https://github.com/your-repo/PhotonFinder)

A versatile desktop application for managing and organizing astronomical image files. PhotonFinder helps amateur astrophotographers efficiently search, filter, and manage their astronomical data collections.

## 🌟 Key Features

### Core Functionality
- **Tabbed Interface** - Multitasking support with multiple search tabs for efficient workflow
- **Offline Operations** - Most functions work offline; searches can be performed without original files available (perfect for external media)
- **Advanced Search & Filtering** - Narrow down searches using multiple criteria including:
  - Cone search around given coordinates
  - Exposure time, date ranges, telescope, binning, gain, temperature
  - FITS header text search
  - Custom metadata filters

### File Format Support
- **Compressed FITS Files** - Full support for gzip, bzip2, and xz compressed FITS files for space saving
- **XISF Files** - Supports XISF files with FITS keywords (as produced by PixInsight, N.I.N.A.)

### Advanced Tools
- **Batch Plate Solving** - Automated plate solving using ASTAP and Astrometry.net
- **Calibration Frame Matching** - Find matching calibration files (darks, flats) for your light frames
- **Data Export** - Export LIGHT files with calibration data for easy import into stacking programs
- **File Management** - Find files used in processing and identify unprocessed data
- **Telescopius Integration** - Check Telescopius lists for objects that have already been imaged
- **AI Agent Access (MCP)** - Let Claude Code, Claude Desktop or any other MCP client search your library and read file metadata

### Database & Library Management
- **SQLite Database** - Efficient metadata storage and retrieval
- **Library Roots** - Define and manage multiple library directories
- **Automated Scanning** - Keep your database updated with file changes
- **Backup & Restore** - Database backup and restoration capabilities

### Reporting & Analysis
- **Comprehensive Reports** - Generate metadata reports, file lists, and data usage statistics
- **Target Lists** - Compare the database with Telescopius target lists, find which objects have already been imaged.
- **Usage Analytics** - Track your imaging data and storage usage

## 🖥️ Platform Support

- **Windows** - Full support (current)
- **Linux** - Support coming soon

## 📋 Requirements

- Python 3.12+
- PySide6 (Qt for Python)
- SQLite
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## 🚀 Installation

### For Users

Binary releases are published on GitHub. Those are the recommended way to run PhotonFinder. 

### For Developers

1. Clone and install development dependencies:
```bash
git clone https://github.com/your-repo/PhotonFinder.git
cd PhotonFinder
uv sync --extra dev
```

2. Build UI files:
```bash
uv run python setup.py build_ui
```

   The MCP tool manifest is generated the same way, and is only needed after adding or
   re-describing a tool in `photonfinder/mcp_server.py`:
```bash
uv run python scripts/build_mcp_manifest.py
```
   `test_manifest_is_up_to_date` fails if you forget; both generated files are committed.

3. Run tests:
```bash
uv run pytest tests/
```

## 🏁 Quick Start

1. **Create or Open Database** - Start by creating a new database or opening an existing one
2. **Add Library Roots** - Define directories containing your astronomical files
3. **Scan Libraries** - Let PhotonFinder index your files and extract metadata
4. **Search & Filter** - Use the powerful search tools to find specific files
5. **Explore Features** - Try plate solving, calibration matching, and reporting tools

## 🤖 AI Agent Access (MCP)

PhotonFinder ships an [MCP](https://modelcontextprotocol.io) server that lets AI agents
search your library, inspect file metadata and FITS headers, and resolve objects against
the bundled catalog.

Your client spawns a small stdio program, `photonfinder-mcp`, which relays to the server
PhotonFinder hosts on a loopback port. A closed application never shows up as a connection
error, and **connecting an agent does not launch it** — the stub handles the handshake and
tool listing on its own. Starting PhotonFinder is a tool, `start_photonfinder`, so your
client asks you first; tools that need the library while it is closed say so rather than
opening a window behind your back. The agent sees whichever library you have open, with
your own settings and solver paths. See [docs/mcp.md](docs/mcp.md) for why the stub is thin
and the work stays in the application.

### Claude Code

Production (installed release) — point at the exe next to `photonfinder.exe`:

```bash
claude mcp add photonfinder -- "C:\Program Files\PhotonFinder\photonfinder-mcp.exe"
```

Development (running from source):

```bash
claude mcp add photonfinder -- uv run --directory C:\path\to\PhotonFinder python -m photonfinder.mcp_stub
```

Verify with `/mcp` inside Claude Code.

### Claude Desktop

Use **Settings → Developer → Edit Config** and restart Claude Desktop. Open it that way
rather than editing `%APPDATA%\Claude\claude_desktop_config.json` by hand: Claude Desktop
is an MSIX package, so its real config lives in the package's private store
(`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`) and a file written to the
plain `%APPDATA%` path is shadowed by it.

Production:

```json
{
  "mcpServers": {
    "photonfinder": {
      "command": "C:\\Program Files\\PhotonFinder\\photonfinder-mcp.exe"
    }
  }
}
```

Development:

```json
{
  "mcpServers": {
    "photonfinder": {
      "command": "C:\\Users\\<you>\\.local\\bin\\uv.exe",
      "args": [
        "run", "--directory", "C:\\path\\to\\PhotonFinder",
        "python", "-m", "photonfinder.mcp_stub"
      ]
    }
  }
}
```

Claude Desktop does not inherit your shell's `PATH`, so give `uv` as an absolute path
(`where uv` will tell you where it lives). Backslashes must be escaped in JSON.

### Dev vs. production at a glance

| | Command | Starts |
| --- | --- | --- |
| Production | `photonfinder-mcp.exe` | `photonfinder.exe` beside it, via `explorer.exe` |
| Development | `uv run --directory <repo> python -m photonfinder.mcp_stub` | `pythonw.exe -m photonfinder.main` |

Either form accepts `--log-file <absolute path>` to copy the stub's diagnostics somewhere
you choose; `pythonw.exe` is used rather than `python.exe` so no console window appears
alongside the GUI.

A packaged client launching the application directly would pass its sandbox on to it, so
the frozen stub hands the path to `explorer.exe`, which runs outside and starts the
application there.

The source-checkout stub cannot do that — there is no single executable to hand over — so
**`start_photonfinder` does not work from a source checkout under Claude Desktop**, which
is MSIX packaged. It detects the sandbox and says so rather than starting the application
with the wrong settings and an empty library. Start PhotonFinder yourself, or point Claude
Desktop at the packaged `photonfinder-mcp.exe`. Claude Code is not packaged, so the source
checkout starts the application fine there.

In a source checkout the catalog database is not in the repo — build it once with
`uv run python scripts/build_catalog.py`, otherwise `lookup_object` and `list_catalogs`
have nothing to read.

All tools are read-only unless you enable **Settings → MCP Server → "Allow AI agents to
trigger plate solving"**, which is off by default. See [docs/mcp.md](docs/mcp.md) for the
full tool list.

## 📚 Documentation

- [Installation Guide](docs/installation.md) - Detailed installation instructions
- [Menu Items Reference](docs/menu-items.md) - Complete guide to all features and menu options
- [AI Agent Access (MCP)](docs/mcp.md) - Tool reference and plate-solving opt-in
- [User Guide](docs/index.md) - Comprehensive documentation

## 🔧 Building

To create a standalone executable:

```bash
uv run --extra build pyinstaller --noconfirm photonfinder.spec
```

## 🤝 Contributing

We welcome contributions! Please feel free to submit issues, feature requests, or pull requests.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

For questions, issues, or feature requests, please:
- Open an issue on GitHub
- Check the documentation in the `docs/` folder
- Review existing issues and discussions

## 🙏 Acknowledgments

- ASTAP and Astrometry.net for plate solving capabilities
- Telescopius for target list integration
- OpenNGC for NGC/IC object database (see Mattia Verga https://github.com/mattiaverga/OpenNGC)
- 

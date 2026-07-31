"""Build script: generates photonfinder/mcp_manifest.py from the MCP tool definitions.

The MCP stub has to answer `tools/list` without starting PhotonFinder, and it is a
standard-library-only program, so it cannot import the server to ask. The tool set is the
same for every run, so it is baked in here instead -- the same arrangement as the Qt
`.ui` files: generated from the source of truth, committed, and regenerated when that
source changes.

Run after adding, removing or re-describing a tool in ``photonfinder/mcp_server.py``:

  uv run python scripts/build_mcp_manifest.py

``test_manifest_is_up_to_date`` fails if you forget.
"""
import asyncio
import pprint
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from photonfinder.mcp_server import build_mcp

HEADER = '''"""Tool manifest served by the MCP stub. GENERATED -- do not edit by hand.

Regenerate with `uv run python scripts/build_mcp_manifest.py` after changing the tools in
`mcp_server.py`; `test_manifest_is_up_to_date` fails if this drifts.

Kept as a Python module rather than a data file so the stub can import it with no file IO
and no PyInstaller `datas` entry, working identically frozen and from source.
"""
'''


def build_manifest() -> dict:
    """The tool list and instructions exactly as the server would advertise them."""
    # No ApplicationContext: nothing about the tool set depends on runtime state, which
    # is the property that makes baking it possible in the first place.
    mcp = build_mcp(None)
    tools = asyncio.run(mcp.list_tools())
    return {
        "instructions": mcp.instructions,
        "tools": [t.model_dump(by_alias=True, mode="json", exclude_none=True)
                  for t in tools],
    }


def render(manifest: dict) -> str:
    # pprint, not json.dumps: the output is Python source, and JSON's null/true/false are
    # not Python literals.
    return f"{HEADER}\nMANIFEST = {pprint.pformat(manifest, width=100, sort_dicts=False)}\n"


def main() -> None:
    target = Path(__file__).resolve().parent.parent / "photonfinder" / "mcp_manifest.py"
    manifest = build_manifest()
    target.write_text(render(manifest), encoding="utf-8")
    print(f"wrote {target} ({len(manifest['tools'])} tools)")


if __name__ == '__main__':
    main()

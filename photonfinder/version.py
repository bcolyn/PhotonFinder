"""Application version, read from pyproject.toml (the single source of truth).

The project is not installed as a package (tool.uv package = false), so
importlib.metadata cannot see it; instead pyproject.toml is read directly. In a
PyInstaller build it is bundled next to the other data files (see photonfinder.spec).
Standard library only, so the MCP stub can use it too.
"""
import sys
import tomllib
from functools import cache
from pathlib import Path


def _pyproject_path() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'pyproject.toml'
    return Path(__file__).parent.parent / 'pyproject.toml'


def _build_date_path() -> Path:
    # Written by photonfinder.spec at build time; only exists in a frozen build
    return Path(getattr(sys, '_MEIPASS', '.')) / 'build_date.txt'


@cache
def get_version() -> str:
    try:
        # utf-8-sig: tolerate a BOM, which tomllib would otherwise reject
        text = _pyproject_path().read_text(encoding='utf-8-sig')
        return tomllib.loads(text)['project']['version']
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return 'unknown'


@cache
def get_build_date() -> str | None:
    """The YYYYMMDD date the executable was built, or None when running from source."""
    if not getattr(sys, 'frozen', False):
        return None
    try:
        return _build_date_path().read_text(encoding='utf-8').strip() or None
    except OSError:
        return None

"""Build script: generates photonfinder/ui/generated/ from the Qt Designer sources.

Each ``photonfinder/ui/*.ui`` becomes ``generated/<name>_ui.py`` and each ``*.qrc`` becomes
``generated/<name>_rc.py``. Run after editing a ``.ui`` or ``.qrc`` file, or with ``--force``
after upgrading PySide6, since an up-to-date ``.ui`` file is otherwise skipped:

  uv run python scripts/build_ui.py [--force]

Exits non-zero if any file fails to compile, so CI stops here instead of at a confusing
import error later on.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent.parent / "photonfinder" / "ui"
GENERATED_DIR = UI_DIR / "generated"


def tool(name: str) -> str:
    """Find a PySide6 tool next to this interpreter, so it matches the installed PySide6."""
    path = shutil.which(name, path=str(Path(sys.executable).parent))
    if path is None:
        sys.exit(f"{name} not found next to {sys.executable}")
    return path


def is_outdated(src: Path, dst: Path) -> bool:
    return not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime


def compile_file(cmd: list[str], src: Path, dst: Path) -> bool:
    """Run one compiler, reporting a failure rather than raising so the rest still build."""
    result = subprocess.run([*cmd, str(src), "-o", str(dst)])
    if result.returncode != 0:
        print(f"FAILED {src.name} (exit code {result.returncode})", file=sys.stderr)
        return False
    print(f"built {dst.name}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="rebuild every .ui file, even if up to date")
    args = parser.parse_args()

    GENERATED_DIR.mkdir(exist_ok=True)
    uic = [tool("pyside6-uic"), "--from-imports"]
    rcc = [tool("pyside6-rcc")]
    failed = 0

    for src in sorted(UI_DIR.glob("*.ui")):
        dst = GENERATED_DIR / f"{src.stem}_ui.py"
        if args.force or is_outdated(src, dst):
            failed += not compile_file(uic, src, dst)

    # Always rebuilt: a .qrc is also out of date when any resource it lists has changed.
    for src in sorted(UI_DIR.glob("*.qrc")):
        failed += not compile_file(rcc, src, GENERATED_DIR / f"{src.stem}_rc.py")

    if failed:
        sys.exit(f"{failed} file(s) failed to compile")


if __name__ == '__main__':
    main()

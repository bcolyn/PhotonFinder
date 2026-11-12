"""Fail if the PyInstaller bundle contains anything outside PhotonFinder's runtime dependencies.

PyInstaller bundles every installed package its import analysis reaches, including through
optional imports (inside functions, in try/except ImportError). A dev or build tool installed in
the build environment can get into the release that way. This lists the top-level modules in the
executables' PYZ archives and the entries in _internal/, maps them to distributions and checks
each against `uv export` of the runtime dependencies.

Run it with the Python of the environment the bundle was built in, so the module-to-distribution
mapping covers everything that could have been bundled:

    uv run --no-sync python scripts/check_bundle.py [dist/main]

scripts/build_exe.py runs it after every build.
"""
import inspect
import re
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path

# In the build group only, so absent from the everyday .venv.
from PyInstaller.archive.readers import CArchiveReader  # pylint: disable=import-error

ROOT = Path(__file__).resolve().parent.parent

# The application itself.
APP_MODULES = {'photonfinder'}
# PyInstaller's bootstrap and runtime hooks; they come with every build.
PYINSTALLER_RUNTIME = re.compile(r'^(pyimod\d+_\w+|pyiboot\d+_\w+|pyi_rth_\w+|_pyi_rth_utils)$')
# Files photonfinder.spec adds as datas, and PyInstaller's archive of the standard library.
EXPECTED_FILES = {
    'LICENSE', 'THIRD-PARTY-NOTICES.txt', 'LGPL-3.0.txt', 'icon.png', 'catalog.db',
    'pyproject.toml', 'build_date.txt',
    'base_library.zip',
}
# The Windows and Visual C++ runtimes, which PyInstaller may copy from the system.
SYSTEM_DLLS = re.compile(r'^(ucrtbase|vcruntime140(_1)?|msvcp140(_\w+)?|api-ms-win-[\w-]+)\.dll$',
                         re.IGNORECASE)


def interpreter_files() -> set[str]:
    """Files that ship with the Python that ran the build (python3xx.dll, OpenSSL, sqlite3, ...).

    Read from the interpreter rather than listed: their names differ between Python builds
    (libssl-3-x64.dll locally, libssl-3.dll on CI's).
    """
    base = Path(sys.base_prefix)
    return {p.name.lower() for folder in (base, base / 'DLLs') if folder.is_dir()
            for p in folder.iterdir() if p.is_file()}


def normalize(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def runtime_distributions() -> set[str]:
    """The project's runtime dependencies and everything they need, from the lock file."""
    out = subprocess.run(
        ['uv', 'export', '--frozen', '--no-default-groups', '--no-emit-project', '--no-hashes',
         '--no-header', '--no-annotate', '--format', 'requirements.txt'],
        cwd=ROOT, check=True, capture_output=True, text=True).stdout
    names = {normalize(m.group(1)) for m in re.finditer(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==', out, re.MULTILINE)}
    if not names:
        sys.exit('uv export listed no runtime dependencies')
    return names


def installed_top_levels() -> dict[str, set[str]]:
    """Top-level names (modules, packages, .libs folders, DLLs) to the distributions that install them.

    From each distribution's file list rather than packages_distributions(), which skips dotted
    names like numpy.libs and prefers top_level.txt, which pywin32's leaves incomplete. A name is
    taken relative to every import root inside site-packages, since .pth files add some (pywin32's
    win32 and win32/lib hold top-level modules such as win32con).
    """
    top_levels: dict[str, set[str]] = {}
    for dist in distributions():
        site = Path(dist.locate_file('')).resolve()
        roots = [()] + [Path(p).resolve().relative_to(site).parts for p in sys.path
                        if p and Path(p).resolve().is_relative_to(site) and Path(p).resolve() != site]
        for file in dist.files or ():
            parts = file.parts
            if not parts or parts[0] == '..' or parts[0].endswith(('.dist-info', '.data')):
                continue
            for root in roots:
                if len(parts) <= len(root) or parts[:len(root)] != root:
                    continue
                rest = parts[len(root):]
                first = rest[0] if len(rest) > 1 else (inspect.getmodulename(rest[0]) or rest[0])
                top_levels.setdefault(first, set()).add(dist.metadata['Name'])
    return top_levels


def bundled_names(dist_dir: Path) -> dict[str, set[str]]:
    """Top-level names in the bundle, each with where it was found."""
    names: dict[str, set[str]] = {}
    exes = sorted(dist_dir.glob('*.exe'))
    if not exes:
        sys.exit(f'No executables in {dist_dir}')
    for exe in exes:
        pkg = CArchiveReader(str(exe))
        for entry, (*_, typecode) in pkg.toc.items():
            if typecode != 'z':
                continue
            for module in pkg.open_embedded_archive(entry).toc:
                names.setdefault(module.split('.')[0], set()).add(f'{exe.name}:PYZ')
    internal = dist_dir / '_internal'
    for path in internal.iterdir():
        if path.is_dir() and path.name.endswith('.dist-info'):
            # copy_metadata(); named <dist>-<version>.dist-info.
            name = path.name[:-len('.dist-info')].rsplit('-', 1)[0]
            names.setdefault(f'dist-info:{name}', set()).add('_internal')
        elif path.is_dir():
            names.setdefault(path.name, set()).add('_internal')
        else:
            names.setdefault(inspect.getmodulename(path.name) or path.name, set()).add('_internal')
    return names


def check(dist_dir: Path) -> list[str]:
    allowed = runtime_distributions()
    module_dists = installed_top_levels()
    stdlib = sys.stdlib_module_names
    interpreter = interpreter_files()
    problems = []
    for name, where in sorted(bundled_names(dist_dir).items()):
        if name.startswith('dist-info:'):
            dist = name.split(':', 1)[1]
            if normalize(dist) not in allowed:
                problems.append(f'{dist}: metadata of a distribution that is not a runtime dependency')
            continue
        if (name in APP_MODULES or name in stdlib or PYINSTALLER_RUNTIME.match(name)
                or name in EXPECTED_FILES or name.lower() in interpreter or SYSTEM_DLLS.match(name)):
            continue
        dists = module_dists.get(name)
        if not dists:
            problems.append(f'{name} ({", ".join(sorted(where))}): not part of any installed distribution')
            continue
        outside = sorted(d for d in dists if normalize(d) not in allowed)
        if outside and len(outside) == len(dists):
            problems.append(f'{name} ({", ".join(sorted(where))}): from {", ".join(outside)}, '
                            'not a runtime dependency')
    return problems


def main():
    dist_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'dist' / 'main'
    problems = check(dist_dir)
    if problems:
        print(f'{dist_dir} contains things outside the runtime dependencies:', file=sys.stderr)
        for problem in problems:
            print(f'  {problem}', file=sys.stderr)
        print('Exclude them in photonfinder.spec, or build in an environment without them '
              '(scripts/build_exe.py).', file=sys.stderr)
        sys.exit(1)
    print(f'{dist_dir}: only runtime dependencies bundled')


if __name__ == '__main__':
    main()

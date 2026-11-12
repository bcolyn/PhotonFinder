"""Build the release bundle (dist/main) from the runtime dependencies and the build group only.

PyInstaller bundles whatever installed package its analysis reaches, so the build runs in its own
environment, .venv-build, without the dev group; the everyday .venv is left alone. The bundle is
then checked with scripts/check_bundle.py.

    uv run python scripts/build_exe.py

Set UV_PROJECT_ENVIRONMENT to build in another environment (it is synced exactly: anything not in
the runtime dependencies or the build group is removed from it).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    env = dict(os.environ)
    env.setdefault('UV_PROJECT_ENVIRONMENT', '.venv-build')
    # Set by `uv run` for .venv; it would only produce a mismatch warning here.
    env.pop('VIRTUAL_ENV', None)

    def uv(*args):
        print('>', 'uv', *args, flush=True)
        subprocess.run(['uv', *args], cwd=ROOT, env=env, check=True)

    uv('sync', '--frozen', '--no-default-groups', '--group', 'build')
    uv('run', '--no-sync', 'pyinstaller', '--noconfirm', 'photonfinder.spec')
    uv('run', '--no-sync', 'python', 'scripts/check_bundle.py')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)

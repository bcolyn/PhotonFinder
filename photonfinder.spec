# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import copy_metadata
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('icon.png', '.'), ('data/catalog.db', '.')]
datas += copy_metadata('xisf')
datas += copy_metadata('mcp')
datas += collect_data_files('astroquery')
datas += collect_data_files('photutils')
datas += collect_data_files('timezonefinder')
datas += collect_data_files('tzdata')

# uvicorn and the MCP SDK import their loop/protocol/transport implementations
# dynamically, which PyInstaller cannot detect by static analysis. These are for the
# application; the stub below deliberately takes none of them.
hiddenimports = collect_submodules('uvicorn') + collect_submodules('mcp')

excludes = [
    'pytest',
    'pytest_qt',
    'pytest_mock',
    'pytest_cov',
    '_pytest',
    'pluggy',
    'py',
    'coverage',
    'unittest',
    'doctest',
    'matplotlib'
]

block_cipher = None

a = Analysis(['photonfinder\\main.py'],
             binaries=[],
             datas=datas,
             hiddenimports=hiddenimports,
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             excludes=excludes,
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)

# The MCP stub is a second, console-mode executable: MCP clients spawn it themselves and
# talk to it over stdio, and the windowed GUI exe below (console=False) has no usable
# stdin/stdout on Windows. It only relays JSON to the application's loopback server, so it
# needs nothing but the standard library; it still shares the single COLLECT below.
a_mcp = Analysis(['photonfinder\\mcp_stub.py'],
             binaries=[],
             datas=[],
             # Imported inside a function, so state it rather than rely on the analysis
             # spotting it; without it the stub cannot list any tools.
             hiddenimports=['photonfinder.mcp_manifest'],
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             # Nothing from the application's dependency tree belongs in the stub. Listing
             # them keeps an accidental import from quietly inflating it back to ~27 MB.
             excludes=excludes + ['PySide6', 'peewee', 'astropy', 'numpy', 'mcp', 'uvicorn',
                                  'photutils', 'astroquery', 'cv2', 'PIL', 'sep'],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz_mcp = PYZ(a_mcp.pure, a_mcp.zipped_data,
             cipher=block_cipher)

exe = EXE(pyz,
          a.scripts, 
          [],
          exclude_binaries=True,
          name='photonfinder',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          console=False,
          disable_windowed_traceback=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None,
          icon='icon.png' )

exe_mcp = EXE(pyz_mcp,
          a_mcp.scripts,
          [],
          exclude_binaries=True,
          name='photonfinder-mcp',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          # Windowless: an MCP client spawning this must not flash a console at the user.
          # stdio still works because the client hands us pipes for the standard handles;
          # mcp_stub bails out cleanly if it is ever started without them.
          console=False,
          disable_windowed_traceback=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None,
          icon='icon.png' )

coll = COLLECT(exe,
               exe_mcp,
               a.binaries,
               a.zipfiles,
               a.datas, 
               strip=False,
               upx=True,
               upx_exclude=[],
               name='main')

# --- Post-build zip creation ---
import shutil, os
from datetime import datetime

app_name = "PhotonFinder"
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
zip_name = f"{app_name}-{timestamp}"

dist_folder = os.path.join('dist', 'main')

# Make archive (will overwrite if it exists, no prompt)
shutil.make_archive(zip_name, 'zip', dist_folder)

print(f"Created {zip_name}.zip from {dist_folder}")
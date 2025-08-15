# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import copy_metadata
from PyInstaller.utils.hooks import collect_data_files

datas = [('icon.png', '.')]
datas += copy_metadata('xisf')
datas += collect_data_files('astroquery')
datas += collect_data_files('photutils')

block_cipher = None

a = Analysis(['photonfinder\\main.py'],
             binaries=[],
             datas=datas,
             hiddenimports=[],
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             excludes=[
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
             ],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
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
coll = COLLECT(exe,
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
# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['..\\main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('tidal_dl/assets/fonts', 'tidal_dl/assets/fonts'),
        ('tidal_dl/assets/icons', 'tidal_dl/assets/icons')
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=True,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('v', None, 'OPTION')],
    name='tidal-dl-gui',
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

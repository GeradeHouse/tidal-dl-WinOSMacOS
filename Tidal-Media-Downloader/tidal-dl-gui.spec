# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=['TIDALDL-PY', '..\\AIGPY'],
    binaries=[],
    datas=[('TIDALDL-PY/tidal_dl/assets/icons', 'tidal_dl/assets/icons'), ('TIDALDL-PY/tidal_dl/assets/fonts', 'tidal_dl/assets/fonts'), ('TIDALDL-PY/tidal_dl/assets/images', 'tidal_dl/assets/images'), ('TIDALDL-PY/tidal_dl/metadata', 'tidal_dl/metadata')],
    hiddenimports=['aigpy', 'tidal_dl.gui.gui_playlist_item_widget'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PySide2', 'torch', 'torchvision', 'tensorflow', 'tkinter', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
splash = Splash(
    'C:\\PythonProjects\\tidal-dl-WinOSMacOS\\Tidal-Media-Downloader\\TIDALDL-PY\\tidal_dl\\assets\\images\\splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    text_size=12,
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    [],
    exclude_binaries=True,
    name='tidal-dl-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\PythonProjects\\tidal-dl-WinOSMacOS\\Tidal-Media-Downloader\\TIDALDL-PY\\tidal_dl\\assets\\icons\\icon-tidal-dl-gui.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    splash.binaries,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='tidal-dl-gui',
)

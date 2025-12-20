# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for MT2 Fishing Bot
# Bundles the executable with the icon and GIF resources

a = Analysis(
    ['Fishing puzzle player.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('monkey-eating.gif', '.'),
        ('monkey.ico', '.'),
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'PIL',
        'pynput',
        'cv2',
        'numpy',
        'psutil',
        'pyautogui',
        'mss',
        'pygetwindow',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MT2 Fishing Bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon='monkey.ico',
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MT2 Fishing Bot',
)

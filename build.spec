# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for MT2 Fishing Bot
# Bundles the executable with the icon and GIF resources

a = Analysis(
    ['src/fishing_bot.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('assets/monkey-eating.gif', '.'),
        ('assets/monkey.ico', '.'),
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
    name='Fishing Puzzle Player v1.0.2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon='assets/monkey.ico',
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

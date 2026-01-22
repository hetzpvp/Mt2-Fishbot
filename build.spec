# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for MT2 Fishing Bot
# Bundles the executable with the icon and GIF resources

import os

# No module exclusions - include everything to avoid missing library errors
# This will result in a larger executable but ensures all dependencies are present
excludes = []

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
        # Only explicitly list what's actually imported
        'PIL.Image',
        'PIL.ImageTk',
        'PIL.GifImagePlugin',  # For monkey-eating.gif
        'PIL.PngImagePlugin',  # For PNG support
        'pynput.keyboard',
        'cv2',
        'numpy',
        'numpy.core',
        'psutil',
        'pyautogui',
        'mss',
        'pygetwindow',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    optimize=0,  # No bytecode optimization (NumPy requires docstrings)
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Fishing Puzzle Player v1.0.5.1',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # strip is Linux-only, doesn't work on Windows
    upx=False,     # UPX compression disabled
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(SPECPATH, 'assets/monkey.ico'),
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

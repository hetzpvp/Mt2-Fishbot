# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for MT2 Fishing Bot
# Bundles the executable with the icon and GIF resources

import os

PROJECT_ROOT = os.path.dirname(SPECPATH)

# No module exclusions - include everything to avoid missing library errors
# This will result in a larger executable but ensures all dependencies are present
excludes = []


a = Analysis(
    [os.path.join(PROJECT_ROOT, 'src', 'fishing_bot.py')],
    pathex=[os.path.join(PROJECT_ROOT, 'src'), PROJECT_ROOT],
    binaries=[],
    datas=[
        (os.path.join(PROJECT_ROOT, 'assets'), 'assets'),
    ],
    hiddenimports=[
        # Only explicitly list what's actually imported
        'version',
        'diag',
        'PIL.Image',
        'PIL.ImageTk',
        'PIL.GifImagePlugin',  # For monkey-eating.gif
        'PIL.PngImagePlugin',  # For PNG support
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtSvg',
        'PySide6.QtWidgets',
        'pynput.keyboard',
        'pynput.mouse',
        'cv2',
        'numpy',
        'numpy.core',
        'numba',
        'llvmlite',
        'psutil',
        'pyautogui',
        'mss',
        'pygetwindow',
        'platforms',
        'platforms.win32',
        'screen_capture',
        'input_backend',
        'jigsaw_bot',
        'jigsaw_detector',
        'jigsaw_solver',
        'debug_host',          # Tk debug windows (DEBUG_MODE_EN), imported lazily
        'jigsaw_solver.deterministic',
        'jigsaw_solver.jigsaw',
        'jigsaw_solver.solver',
        'jigsaw_solver.cache_location',
        # Not bundled: jigsaw_solver.app / .main / .fishing_jigsaw_solver are
        # the old stand-alone Tk solver, which the bot does not use.
    ],
    hookspath=[os.path.join(PROJECT_ROOT, 'packaging', 'hooks')],
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
    name='Fishing Puzzle Player v2.0.2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # strip is Linux-only, doesn't work on Windows
    upx=False,     # UPX compression disabled
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(PROJECT_ROOT, 'assets', 'ui', 'monkey.ico'),
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

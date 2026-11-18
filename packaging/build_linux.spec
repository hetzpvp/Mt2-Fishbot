# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Linux build of MT2 Fishing Bot (X11 / Wine target).
# Build with:  .venv/bin/pyinstaller --clean --noconfirm packaging/build_linux.spec
import os

PROJECT_ROOT = os.path.dirname(SPECPATH)

# Qt modules the bot never uses (WebEngine alone is hundreds of MB).
excludes = [
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebChannel', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia',
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'src', 'fishing_bot.py')],
    pathex=[os.path.join(PROJECT_ROOT, 'src'), PROJECT_ROOT],
    binaries=[],
    datas=[(os.path.join(PROJECT_ROOT, 'assets'), 'assets')],
    hiddenimports=[
        'version', 'PIL.Image', 'PIL.GifImagePlugin', 'PIL.PngImagePlugin',
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtSvg', 'PySide6.QtWidgets',
        'pynput.keyboard', 'pynput.mouse', 'pynput.keyboard._xorg', 'pynput.mouse._xorg',
        'tkinter', 'Xlib', 'Xlib.display', 'Xlib.ext.xtest', 'Xlib.XK',
        'cv2', 'numpy', 'numba', 'llvmlite', 'psutil', 'mss',
        'platforms', 'platforms.x11', 'platforms.uinput',
        'screen_capture', 'input_backend', 'jigsaw_bot', 'jigsaw_detector',
        'jigsaw_solver', 'jigsaw_solver.deterministic', 'jigsaw_solver.jigsaw',
        'jigsaw_solver.solver', 'jigsaw_solver.cache_location',
    ],
    hookspath=[os.path.join(PROJECT_ROOT, 'packaging', 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,  # NumPy needs docstrings
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='Fishing Puzzle Player v2.0.0 (linux)',
    debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, upx_exclude=[],
    runtime_tmpdir=None, console=False,
    disable_windowed_traceback=False,
)

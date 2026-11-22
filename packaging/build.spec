# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for MT2 Fishing Bot
# Bundles the executable with the icon and GIF resources

import os

PROJECT_ROOT = os.path.dirname(SPECPATH)

# Modules the Windows exe never imports. Excluding them keeps them out of the
# archive, so every launch has less to unpack (onefile extracts it all first).
excludes = [
    # Tk debug windows (DEBUG_MODE_EN) and Pillow, which only they use. The
    # NuGet Python the exe is built with has no tkinter, so in the exe they
    # could never open anyway. pyautogui imports Pillow (via pyscreeze) inside
    # a try and keeps its mouse functions without it.
    'tkinter', '_tkinter', 'PIL', 'pyscreeze', 'mouseinfo', 'pymsgbox',
    # numba pulls these in for debug dumps (pygments) and its AOT compiler
    # (numba.pycc, setuptools). The JIT the jigsaw solver uses needs neither.
    'pygments', 'setuptools', 'numba.pycc',
    # Qt modules the widgets GUI does not use.
    'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtPdf',
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
    'PySide6.QtMultimedia', 'PySide6.Qt3DCore', 'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets',
]

# Binaries and data the hooks collect that this app never loads, matched on
# the destination path (lower case, "/" separators):
_DROP_EXACT = {
    'pyside6/opengl32sw.dll',          # software OpenGL; the GUI draws with the raster engine
    'pyside6/qt6quick.dll', 'pyside6/qt6qml.dll', 'pyside6/qt6qmlmodels.dll',
    'pyside6/qt6qmlmeta.dll', 'pyside6/qt6qmlworkerscript.dll',
    'pyside6/qt6virtualkeyboard.dll', 'pyside6/qt6pdf.dll', 'pyside6/qt6network.dll',
}
_DROP_PREFIXES = (
    'pyside6/translations/',           # no QTranslator is installed
    'pyside6/plugins/platforminputcontexts/',   # virtual keyboard (needs Quick/QML)
    'pyside6/plugins/tls/', 'pyside6/plugins/networkinformation/',
    'pyside6/plugins/generic/',        # TUIO touch input (needs Qt6Network)
    'pyside6/plugins/platforms/qdirect2d', 'pyside6/plugins/platforms/qminimal',
    'pyside6/plugins/platforms/qoffscreen',     # qwindows is the one that runs
    'pyside6/plugins/imageformats/qpdf', 'pyside6/plugins/imageformats/qicns',
    'pyside6/plugins/imageformats/qtga', 'pyside6/plugins/imageformats/qtiff',
    'pyside6/plugins/imageformats/qwbmp', 'pyside6/plugins/imageformats/qwebp',
    # FFmpeg backend of cv2.VideoCapture/VideoWriter. cv2.pyd loads it only
    # when a video is opened; the bot only matches templates on screen grabs.
    'cv2/opencv_videoio_ffmpeg',
)


def _keep(entry):
    dest = entry[0].replace('\\', '/').lower()
    return dest not in _DROP_EXACT and not dest.startswith(_DROP_PREFIXES)


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

a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Fishing Puzzle Player v2.0.5',
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

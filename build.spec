# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for MT2 Fishing Bot
# Bundles the executable with the icon and GIF resources
# Optimized for minimal size without performance impact

import os

# Modules to exclude - these bloat the executable significantly
# These are test frameworks, documentation, unused backends, and optional features
# AGGRESSIVE SIZE OPTIMIZATION - excludes everything not explicitly used
excludes = [
    # Testing frameworks (not needed in production)
    'pytest', 'unittest', 'doctest', 'test', 'tests', '_pytest',
    'nose', 'hypothesis', 'mock',
    
    # Unused PIL/Pillow plugins (keep only GIF/PNG - minimal set)
    'PIL.ImageQt', 'PIL.SpiderImagePlugin',
    'PIL.FitsImagePlugin', 'PIL.Hdf5StubImagePlugin',
    'PIL.IptcImagePlugin', 'PIL.McIdasImagePlugin',
    'PIL.MicImagePlugin', 'PIL.MpegImagePlugin',
    'PIL.PixarImagePlugin', 'PIL.PsdImagePlugin',
    'PIL.BmpImagePlugin', 'PIL.BufrStubImagePlugin',
    'PIL.CurImagePlugin', 'PIL.DcxImagePlugin',
    'PIL.DdsImagePlugin', 'PIL.EpsImagePlugin',
    'PIL.FliImagePlugin', 'PIL.FpxImagePlugin',
    'PIL.FtexImagePlugin', 'PIL.GbrImagePlugin',
    'PIL.GdImageFile', 'PIL.GribStubImagePlugin',
    'PIL.IcnsImagePlugin', 'PIL.IcoImagePlugin',
    'PIL.ImImagePlugin', 'PIL.ImtImagePlugin',
    'PIL.PalmImagePlugin', 'PIL.PcxImagePlugin',
    'PIL.PpmImagePlugin', 'PIL.SgiImagePlugin',
    'PIL.SunImagePlugin', 'PIL.TgaImagePlugin',
    'PIL.TiffImagePlugin', 'PIL.WebPImagePlugin',
    'PIL.WmfImagePlugin', 'PIL.XbmImagePlugin',
    'PIL.XpmImagePlugin', 'PIL.XVThumbImagePlugin',
    
    # OpenCV modules we don't use (using headless version, only need basic cv2 functions)
    'cv2.aruco', 'cv2.bgsegm', 'cv2.bioinspired',
    'cv2.ccalib', 'cv2.datasets', 'cv2.dnn',
    'cv2.dnn_superres', 'cv2.dpm', 'cv2.face',
    'cv2.freetype', 'cv2.fuzzy', 'cv2.hfs',
    'cv2.img_hash', 'cv2.intensity_transform',
    'cv2.line_descriptor', 'cv2.mcc', 'cv2.ml',
    'cv2.optflow', 'cv2.ovis', 'cv2.phase_unwrapping',
    'cv2.photo', 'cv2.plot', 'cv2.quality',
    'cv2.rapid', 'cv2.reg', 'cv2.rgbd',
    'cv2.saliency', 'cv2.shape', 'cv2.stereo',
    'cv2.structured_light', 'cv2.superres',
    'cv2.surface_matching', 'cv2.text', 'cv2.tracking',
    'cv2.videoio', 'cv2.video', 'cv2.videostab',
    'cv2.viz', 'cv2.wechat_qrcode', 'cv2.ximgproc',
    'cv2.xobjdetect', 'cv2.xphoto',
    
    # NumPy modules we don't need
    'numpy.distutils', 'numpy.f2py', 'numpy.testing',
    'numpy.doc', 'numpy.random._examples',
    'numpy.random.tests', 'numpy.core.tests',
    'numpy.fft.tests', 'numpy.linalg.tests',
    'numpy.ma.tests', 'numpy.matrixlib.tests',
    'numpy.polynomial.tests', 'numpy.lib.tests',
    'numpy.typing.tests',
    
    # IPython/Jupyter (sometimes pulled in by numpy/matplotlib)
    'IPython', 'jupyter', 'notebook', 'ipykernel',
    'jupyter_client', 'jupyter_core', 'nbformat',
    'ipython_genutils', 'traitlets',
    
    # Matplotlib (not used at all)
    'matplotlib', 'mpl_toolkits', 'pylab',
    
    # Cryptography/Security libs (not needed)
    'cryptography', 'OpenSSL', 'ssl', '_ssl',
    'hashlib', '_hashlib',
    
    # XML/HTML parsers (not used)
    'xml', 'xmlrpc', 'html', 'html.parser',
    'xml.etree', 'xml.dom', 'xml.parsers',
    'lxml', 'BeautifulSoup', 'bs4',
    
    # Email/Network libs (not used)
    'email', 'smtplib', 'imaplib', 'poplib',
    'ftplib', 'telnetlib', 'urllib', 'urllib2',
    'http', 'http.client', 'http.server',
    
    # Debugging tools
    'pdb', 'trace', 'cProfile', 'profile', 'pstats',
    'timeit', 'tracemalloc',
    
    # Other unused standard library modules
    'sqlite3', 'lib2to3', 'pydoc', 'pydoc_data',
    'distutils', 'setuptools', 'pip', 'wheel',
    'ensurepip', 'venv', 'zipapp',
    'turtledemo', 'turtle', 'tkinter.test',
    'idlelib', 'asyncio', 'concurrent',
    'multiprocessing', 'queue',
]

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
    name='Fishing Puzzle Player v1.0.5',
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

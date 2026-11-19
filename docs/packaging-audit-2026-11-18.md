# Packaging and dependency audit

18 November 2026. Read-only, plus measurement builds under
`/tmp/fishbot-packaging-audit-2026-11-18`. Nothing in `src/`, the specs,
`requirements.txt`, or `tools/build.py` was edited. `dist/` and `releases/`
were not deleted or overwritten. No mouse, keyboard, or focus was sent to
the real desktop. Smoke tests used a private Xvfb display, the same check
as `tools/release.py`.

## 1. Summary

The Linux binary is 234 MB and the Windows binary is 153 MB because each
onefile archive compresses a small set of large libraries. On Linux the
compressed weight is llvmlite 59 MB, OpenCV 61 MB (the library plus its own
OpenBLAS and FFmpeg), Qt 37 MB, and then ICU, GTK, and Pillow. WebEngine
(204 MB on disk) is already left out. Numba is not on the fishing hot path.
It builds the jigsaw move table once, when that cache is missing.

| Change | Linux size | Confidence | Risk | Effort |
| --- | --- | --- | --- | --- |
| Drop the GTK theme plugin, QML/Quick/PDF, translations, unused Qt image plugins, and Pillow's AVIF codec. Built and smoked as `qt-slim` | 234.13 → 210.94 MB (−23.2 MB) | Measured. GUI stayed up 25 s and the numba table build ran | Low for the widgets the bot uses. The native GTK file dialog and Wayland-only startup were not exercised | S. A filter in both specs. Windows needs its own build to confirm the same cut |
| Stop shipping numba, llvmlite, and the pygments/setuptools they pull in. Built and smoked as `no-numba` | 234.13 → 165.40 MB (−68.7 MB) | Measured. GUI stayed up and created the cache directory. The pure-Python table was not timed to completion | Medium. First jigsaw setup uses the pure-Python DP. Later launches load the cache and do not need numba | S in the spec. Needs an in-game check of a first jigsaw run with no cache |
| Point the Windows build at the stack the tests already run, and move Pillow off 12.2.0 | Windows exe size unknown until that build. Linux is already on this stack | pip-audit and the version list below | Medium for detection scores if OpenCV moves from 4.13 to 5.0. Pillow 13 is already what the tests import | S for the pins, then one Windows rebuild |
| Remove `psutil` | About 0.05 MB | No import under `src/` | Low | S |
| `strip` on `libllvmlite.so` | About 4.5 MB, estimated with zlib, not a full rebuild | Measured on a scratch copy of the .so | Low, but it was not smoked | S |
| Leave UPX off | — | Spec and a missing `upx` binary | UPX on a Windows exe is a common antivirus false positive | None |

Doing the Qt filter and dropping numba touches different files, so the
saves should add. That combination was not built. A fair estimate is
about 142 MB on Linux. Windows was not rebuilt. From the existing Windows
archive, llvmlite alone is 37.8 MB of the 152.6 MB exe, and
`opengl32sw.dll` is another 7.6 MB.

## 2. What was measured

- Python 3.14.10 in `.venv`. The Windows build pin is Python 3.14.4
  (`tools/build_windows_exe.sh` line 22, `tools/build.py` lines 143–158).
- Linux baseline rebuilt from the current `packaging/build_linux.spec`
  with PyInstaller 6.22.3. File size 234,129,528 bytes.
- Existing `dist/Fishing Puzzle Player v2.0.0 (linux)` is 233,948,440 bytes
  (15 November). Existing `dist/Fishing Puzzle Player v2.0.2.exe` is
  152,649,240 bytes (18 November). Those files were only read.
- Archive contents come from `PyInstaller.archive.readers.CArchiveReader`
  (compressed size of each entry). The November 15 Analysis TOC was used
  to see what the spec asked for; file sizes in that TOC were re-stated
  from today's libraries, so the archive numbers are the ones to trust.
- Unit tests: `nice .venv/bin/python tools/run_tests.py -q` while a
  packaging build was also running. **520 passed, 1 failed, 201.53 s.**
  The failure is `PageSwitchTests.test_tabs_that_cannot_be_read_cost_two_timeouts_at_most`:
  the call took 0.257 s against a 0.25 s ceiling (`tests/test_fishing_bot.py:349`).
  Run alone afterward, that test passed in 1.39 s. It is a timing bound
  that slipped under load. It is not a packaging failure.
- Smoke: `xvfb-run -a -s "-screen 0 1920x1080x24" timeout -s TERM 25 <binary>`,
  with `WAYLAND_DISPLAY` unset and `QT_QPA_PLATFORM=xcb`. Exit 124 means
  the process was still running. Baseline, `qt-slim`, and `no-numba` all
  returned 124. The local config's `ui_mode` is `jigsaw` and
  `set_mode(..., save=False)` starts a cache build when the cache is
  missing (`src/qt_gui.py:5277`, `5615–5618`). The scratch binaries had
  no cache, so baseline and `qt-slim` started the numba DP and wrote
  partial files under `/tmp`. `bot_config.json` in the repo was not
  modified (mtime still 16 November). The copy the frozen app made sits
  next to each scratch binary.

## 3. Dependency inventory

### Three version lists

`requirements.txt` is a floor. `tools/build.py` is an exact pin, and
`tools/build_windows_exe.sh` installs those pins into the Wine environment
on every Windows build. The Linux binary is whatever `.venv` has.

| Package | requirements.txt | Windows pin | .venv | Latest on PyPI |
| --- | --- | --- | --- | --- |
| Python | — | 3.14.4 | 3.14.10 | (NuGet pin, not PyPI) |
| numpy | `>=2.4` | 2.4.4 | 2.5.4 | 2.5.5 |
| opencv-python-headless | `>=4.13` | 4.13.0 (wheel 4.13.0.92) | 5.0.0.93 | 5.1.0.95 |
| Pillow | `>=12.2` | 12.2.0 | 13.0.0 | 13.0.0 |
| PySide6 | `>=6.11` | 6.11.0 | 6.12.0 | 6.12.0 |
| pynput | `>=1.8.1` | 1.8.1 | 1.8.2 | 1.8.2 |
| numba | `>=0.65` | 0.65.1 | 0.68.1 | 0.68.1 |
| llvmlite | (via numba) | 0.47.0 | 0.50.0 | 0.50.0 |
| psutil | `>=7.2` | 7.2.2 | 7.2.2 | 7.2.2 |
| mss | `>=10.2` | 10.2.0 | 10.2.0 | 10.2.0 |
| PyAutoGUI | Windows only `>=0.9.54` | 0.9.54 | not installed | 0.9.54 |
| pygetwindow | Windows only `>=0.0.9` | 0.0.9 | not installed | 0.0.9 |
| python-xlib | Linux only `>=0.33` | not pinned | 0.33 | 0.33 |
| pytest | `>=8` | not pinned | 9.2.0 | 9.2.0 |
| pytest-xdist | `>=3.8` | not pinned | 3.8.0 | 3.8.0 |
| PyInstaller | not listed | 6.20.0 | 6.22.3 | 6.22.3 |

`psutil`, `mss`, and `python-xlib` match. Everything else the two builds
share has drifted. A release built today would test and ship the Linux
binary on OpenCV 5.0 / PySide 6.12 / Pillow 13 / numba 0.68, and the
Windows exe on OpenCV 4.13 / PySide 6.11 / Pillow 12.2 / numba 0.65.

### What each direct dependency is for

| Package | Imported by the app? | Needed at runtime? |
| --- | --- | --- |
| numpy | Yes. Detectors, capture, the solver tables | Yes |
| opencv-python-headless | Yes. `cv2` in the fish and jigsaw detectors and in capture | Yes. GUI is `NONE` in this wheel, which is the right wheel |
| mss | `src/screen_capture.py` | Yes, for grabs |
| Pillow | `src/debug_ui.py`, `src/utils.py` (`Image`, `ImageTk`) | Yes for the Tk debug windows. The Qt window draws SVG with `QSvgRenderer` and the GIF with `QMovie` |
| PySide6 | `QtCore`, `QtGui`, `QtSvg`, `QtWidgets` only | Yes. No QML, WebEngine, 3D, Multimedia, or PDF in `src/` |
| pynput | Global F5 / click capture, and the XTest keyboard fallback | Yes. Linux backend is Xorg (`pynput/_util/__init__.py` chooses `xorg`) |
| numba + llvmlite | `src/jigsaw_solver/deterministic.py` | Only to **build** the move table. `solve()` reads the numpy cache |
| psutil | Nowhere under `src/` or `tools/` except the version list in `tools/build.py` | No. It is still a hidden import in both specs, and the Linux exe contains `psutil/_psutil_linux.abi3.so` (0.05 MB compressed) |
| PyAutoGUI | `src/input_backend.py`, `src/fishing_bot.py` | Yes on Windows, as the fallback mouse path. Not installed on Linux, and not in the Linux exe |
| pygetwindow | `src/platforms/win32.py` | Yes on Windows for the window list. Linux implements the same shape itself in `src/platforms/x11.py` |
| python-xlib | `src/platforms/x11.py` and pynput's Xorg backend | Yes on Linux |
| pytest, pytest-xdist | tests and `tools/run_tests.py` | No. Neither name is in the frozen archive |
| evdev | Required by pynput's metadata on Linux. Not installed. The project's uinput code uses `fcntl`, not evdev | No for the default backend |

`requirements.txt` still says pynput pulls in evdev and that the Python
headers are needed to compile it. pynput 1.8.2's default Linux backend
does not import evdev. A clean `pip install -r requirements.txt` will
still try to build evdev, because pynput's `Requires-Dist` asks for it.
This `.venv` does not have evdev, so it was not produced by a plain
install of that file.

PySide6 on pip is four packages. Installed size on disk:

| Piece | On disk |
| --- | --- |
| PySide6_WebEngine 6.12.0.140 | 278 MB (`libQt6WebEngineCore.so.6` is 204 MB) |
| PySide6_Essentials 6.12.0 | 250 MB |
| PySide6_Addons 6.12.0 | 161 MB |
| PySide6_Pdf | 6 MB |
| The `PySide6/` tree (the union) | 693 MB |

The frozen app does not ship WebEngine. The Linux spec excludes it
(`packaging/build_linux.spec` lines 9–13). The Windows spec's exclude
list is empty (`packaging/build.spec` line 11), and WebEngine is still
absent, because nothing imports it. The empty list is safe only while
that stays true.

### Transitive weight that actually lands in the exe

From the scratch Linux baseline archive (compressed / uncompressed):

| Group | Compressed | Uncompressed | What it is |
| --- | --- | --- | --- |
| llvmlite | 59.09 MB | 178.75 MB | One unstripped `libllvmlite.so` |
| OpenCV core + its OpenBLAS | 45.70 MB | 122.14 MB | `cv2.abi3.so` 31.2 MB compressed, plus `libopenblasp-….so` 11.1 MB |
| Qt (widgets, gui, plugins, svg) | 21.53 MB | 58.24 MB | What the window uses, mixed with extra plugins |
| ICU | 14.92 MB | 39.08 MB | `libicudata.so.73` is 12.3 MB compressed. `libQt6Core.so.6` needs it |
| OpenCV FFmpeg / AV1 / VPX | 14.92 MB | 36.50 MB | `cv2.abi3.so` lists these as `NEEDED`. They cannot be deleted from the archive |
| PYZ (bytecode) | 13.08 MB | 13.08 MB | Already compressed |
| GTK, cairo, glycin, pango | 8.14 MB | 18.82 MB | Pulled by `platformthemes/libqgtk3.so`, not by QtWidgets |
| numpy + its OpenBLAS | 13.23 MB | 42 MB | A second OpenBLAS (`libscipy_openblas64_….so`, 7.40 MB compressed) |
| Pillow | 6.67 MB | 18.93 MB | Includes Pillow's own libavif (2.21 MB compressed) |
| Qt Quick / Qml | 6.47 MB | 17.42 MB | Not imported. Brought in by the virtual-keyboard and PDF plugins |
| Python runtime | 4.92 MB | 12.32 MB | `libpython3.14.so.1.0` is 2.72 MB compressed |
| crypto, glib, assets, Tcl/Tk, translations, PDF, numba sources | the rest | | GIF is 2.59 MB and does not shrink. Tcl/Tk is 2.60 MB. Translations 2.02 MB. PDF 2.38 MB. numba's own files 1.22 MB |

`readelf -d` on `cv2.abi3.so` needs the OpenCV OpenBLAS, libavif, libpng,
libavcodec, libavformat, libavutil, and libswscale. Those stay as long as
this wheel stays. `libQt6Widgets.so.6` needs Core, Gui, and GL. It does
not need QML, PDF, or GTK.

NumPy's OpenBLAS and OpenCV's OpenBLAS are two copies. They are different
files with different sonames, so the linker will not share them.

## 4. Versions and what to do with them

Windows Python is 3.14.4. Every library below publishes a `cp314` or
`abi3` Windows wheel, so the Wine build can install it. The NuGet pin
does not have to move for these upgrades.

| Package | Recommendation | Why |
| --- | --- | --- |
| numpy 2.4.4 → 2.5.4 (tested) or 2.5.5 (latest) | Align the Windows pin with the tested 2.5.4. 2.5.5 is a one-patch bump worth taking after a test run | 2.5.0 dropped Python 3.11 and `numpy.distutils`, and expired a set of 2.0 deprecations. This code does not use those. 2.5.5 (15 November 2026) is a patch: the notes mention fixes tied to the eMudhra compromise, and casting a structured void or subarray dtype to `StringDType` now raises `TypeError`. `src/` does not use `StringDType`. I did not install 2.5.5, so the suite has not run on it |
| opencv 4.13.0.92 → 5.0.0.93 (tested). Hold 5.1.0.95 | Put Windows on 5.0.0.93 so the exe matches the tests. Do not step to 5.1 until the suite has run there | OpenCV 5 removes the C API, moves Haar/HOG and `cv2.ml` to contrib, and moves some geometry functions into a new module. The Python names this bot calls (`matchTemplate`, `cvtColor`, `Sobel`, `Canny`, `resize`, `threshold`, `boundingRect`, `imread`) stay on `cv2`. There is no `CascadeClassifier`, `dnn`, or `convexHull` in `src/`. The remaining risk is a score change in `matchTemplate` between 4.13 and 5.0. The Linux wheel sizes are almost the same (60.4 MB vs 61.2 MB), so this is a correctness alignment, not a shrink. 5.1's Windows wheel is 47.8 MB against 4.13's 40.1 MB |
| Pillow 12.2.0 → 13.0.0 | Move the Windows pin to 13.0.0 | 13.0.0 drops Python 3.10 and stops saving mode `I` as PNG, among other API cuts. The bot opens GIF, PNG, and ICO. pip-audit reports many advisories for 12.2.0, all fixed in 12.3.0 or 13.0.0. Pillow 13.0.0 in `.venv` was not reported. The tests already import 13.0.0 |
| PySide6 6.11.0 → 6.12.0 | Move the Windows pin to 6.12.0 | 6.12 is already what Linux runs, and it is the latest. It removes the copy constructors of the item classes for the tree, table, and list widgets. `src/` does not construct those copies. It also blocks unpickling arbitrary classes from `QDataStream` / `QSettings`. The app does not use `QSettings`. The Essentials wheel grew (Windows 75.8 → 82.2 MB). The frozen app only packs a slice of that, so the exe change has to be measured on a Windows build |
| pynput 1.8.1 → 1.8.2 | Move the Windows pin to 1.8.2 | 1.8.2 fixes duplicate scroll events on Windows and fills in a macOS callback argument. It also edits the Xorg backend. Linux is already on 1.8.2 and the smoke test's F5 listener loaded `pynput.keyboard._xorg` |
| numba 0.65.1 / llvmlite 0.47.0 → 0.68.1 / 0.50.0 | Move the pins if numba stays in the exe | 0.66 moved to LLVM 22 and restored `sys.monitoring` on Python 3.14.4+. 0.67 added NumPy 2.5. 0.68 added Python 3.15 and left the `njit` / `prange` usage here intact. Wheel sizes barely changed (llvmlite Linux 56.3 → 59.7 MB). Dropping numba entirely is the size decision in section 6, and then these pins go away |
| psutil 7.2.2 | Remove it | Same version everywhere, and nothing imports it |
| mss 10.2.0, python-xlib 0.33, PyAutoGUI 0.9.54, pygetwindow 0.0.9 | Hold | They are the latest, and they are the versions in use |
| pytest 9.2.0, pytest-xdist 3.8.0 | Hold | Test-only. The suite runs on 9.2.0. They are not in the exe |
| PyInstaller 6.20.0 → 6.22.3 | Move the Windows pin to 6.22.3 | The Linux binary was just built with 6.22.3. Both versions have Windows wheels. The Wine environment can install 6.22.3 |

## 5. Why the executables are this size

Both specs are **onefile**. `upx=False` and `strip=False` in
`packaging/build.spec` (lines 81–82) and `packaging/build_linux.spec`
(line 45). `upx` is not installed. `optimize=0` because a comment says
NumPy needs docstrings.

Onefile zlib-compresses the archive. That is why 634 MB of uncompressed
inputs become a 234 MB Linux file, and why UPX is a poor next step:
`libllvmlite.so` is already 179 MB → 59 MB (3.0×). Compressing it again
with UPX, then storing it in a zlib archive, rarely wins, and UPX'd
Windows exes are a regular antivirus false positive. Leave UPX off.

`strip --strip-unneeded` on a scratch copy of `libllvmlite.so` took it
from 178.7 MB to 159.8 MB. zlib of those two copies differs by 4.5 MB.
Qt's own libraries are already stripped (`file` reports `stripped` and
a `.gnu_debuglink`). OpenCV's `cv2.abi3.so` is stripped. NumPy's OpenBLAS
still has a symbol table; stripping a copy saved about 1.4 MB uncompressed,
which will be well under 1 MB after zlib. A `strip=True` rebuild was not
done.

### Linux 234 MB vs Windows 153 MB

| Compressed piece | Linux baseline | Windows 2.0.2 exe | Gap |
| --- | --- | --- | --- |
| llvmlite | 59.1 | 37.8 (`llvmlite.dll` 106.6 MB raw) | Linux LLVM build is larger and unstripped |
| OpenCV + FFmpeg | 60.6 | 38.1 (`cv2.pyd` 26.3 + `opencv_videoio_ffmpeg4130_64.dll` 11.8) | Linux 5.0 wheel also ships its own OpenBLAS, libaom, and libvpx |
| ICU | 14.9 | not present | The Linux Qt wheel links ICU. The Windows Qt DLLs do not |
| GTK / glycin | 8.1 | not present | `libqgtk3.so` |
| Qt Quick + PDF | 8.9 | 8.2 | Same accident on both platforms |
| `opengl32sw.dll` | — | 7.6 | Software OpenGL, Windows only |
| Pillow AVIF | 2.2 of the 6.7 Pillow group | 4.3 (`PIL\_avif.pyd`) | |
| Tcl/Tk | 2.6 | not in the Windows archive groups | Linux spec lists `tkinter` as a hidden import |
| Translations | 2.0 | 1.9 | The app does not install a translator |

WebEngine is on neither side.

### What the specs already exclude, and what still gets in

Linux excludes WebEngine, WebChannel, PDF, QML, Quick, Qt3D, and
Multimedia. Those Python modules stay out. The **shared libraries** come
back through plugins that QtGui collects as a set:

- `imageformats/libqpdf.so` needs `libQt6Pdf.so.6` (2.38 MB compressed).
- `platforminputcontexts/libqtvirtualkeyboardplugin.so` needs Quick, QML,
  and VirtualKeyboard (about 6.5 MB).
- `platformthemes/libqgtk3.so` needs system GTK, which then pulls gdk,
  pango, cairo, epoxy, and glycin (8.1 MB). `libQt6Gui` itself does not
  need GTK. It does need glib, freetype, fontconfig, and freetype needs
  harfbuzz, so those stay.
- Every `qtbase_*.qm` is packed (2.0 MB). Nothing calls `QTranslator`.

Windows has the same Quick/PDF/translation set, plus `opengl32sw.dll`,
which the PySide6 hook adds for software GL. A machine with a normal GPU
does not load it. Dropping it is a Windows-only experiment. It was not
built here.

### Data files

| Path | In the exe? |
| --- | --- |
| `assets/` (2.9 MB, including `monkey-eating.gif` at 2.60 MB) | Once. The archive has a single GIF entry |
| `docs/` | No |
| `data/cache/jigsaw/solver_cache_f64.npz` (90.9 MB) | No. Built on first jigsaw use, next to the exe |
| `interception.dll` (11 KB, also under `tools/interception/`) | No. The loader looks beside the exe and on `PATH` (`src/interception_wrapper.py`) |
| pytest, pip | No |

Shipping the 91 MB cache in order to delete llvmlite would make the
archive larger: the npz is already compressed, and llvmlite's compressed
size is 59 MB.

Both platforms bundle `assets/` and nothing from `docs/` or the cache.
Linux does not bundle `interception.dll`. Windows does not either.

## 6. Size experiments

Scratch specs only. Each Linux binary was smoked for 25 s on Xvfb.

| Variant | Linux bytes | Linux MB | Against baseline | Smoke | What was removed |
| --- | --- | --- | --- | --- | --- |
| `dist/` Linux, v2.0.0, 15 Nov | 233,948,440 | 233.95 | — | not re-run | Previous build, same libraries |
| `dist/` Windows, v2.0.2, 18 Nov | 152,649,240 | 152.65 | — | not re-run | Current Windows pins. No experiment |
| baseline, current Linux spec | 234,129,528 | 234.13 | 0 | pass (exit 124) | — |
| `qt-slim` | 210,936,088 | 210.94 | −23.19 MB | pass. Numba DP ran (height 0 took 3.1 s, which is the JIT) | GTK theme plugin and the GTK stack, Quick/QML/PDF/VirtualKeyboard, translations, tiff/webp/tga/icns/wbmp, eglfs/minimal/linuxfb/vnc, Pillow AVIF. Kept xcb, wayland client, SVG, GIF, ICO, JPEG, ibus, the xdg-desktop-portal theme |
| `no-numba` | 165,401,520 | 165.40 | −68.73 MB | pass. Cache directory created, no finished npz in 25 s | `numba` and `llvmlite` excluded. That also dropped pygments (340 modules) and setuptools, which the numba hook had pulled in |
| `strip` of `libllvmlite.so` only | — | about −4.5 | estimated | not a rebuilt exe | zlib of the stripped .so vs the original |
| slim + no-numba together | — | about 142 | estimated, not built | — | The two cuts do not overlap |

`qt-slim` still contains `libqxcb.so`, `libQt6Svg`, `libqgif`,
`libqsvgicon`, `cv2.abi3.so`, and llvmlite. `no-numba` still contains
those Qt pieces and does not contain `libllvmlite.so`. pynput's Xorg
backend is inside the PYZ (10 modules) because the spec lists
`pynput.keyboard._xorg` and `pynput.mouse._xorg`. The shutdown traceback
on the baseline smoke is that listener losing the X server when `timeout`
killed the process.

The smoke does not open a file dialog, does not start on Wayland, does
not turn on `DEBUG_MODE_EN`, and does not click in the game. A missing
GIF plugin would not have killed the process. The GIF plugin was kept
on purpose.

Building with `DISPLAY` empty makes the pynput hook warn
(`Failed to collect submodules for 'pynput'`). The explicit hidden
imports still packaged the Xorg backend, and the smoke loaded it.
Building under Xvfb avoids the warning.

## 7. Hot path and startup

Fishing does not import numba. `src/fishing_bot.py` imports `cv2` and
numpy at load (lines 26–27). The fish and jigsaw detectors use
`matchTemplate`, color converts, Sobel, and Canny. The performance audit
measured a stock detect at 0.48 ms median on a real panel crop. That is
OpenCV, and it is small next to the 8 ms budget.

Numba is the DP that fills `solver_cache_f64.npz`
(`src/jigsaw_solver/deterministic.py` lines 14–19 and 388–396). After
`load_cache()` succeeds, `solve()` only indexes the numpy tables. The
GUI starts that build when jigsaw mode is selected and the cache is
missing (`src/qt_gui.py:5615–5618`), not on every fishing launch.

Cold import, one process each, `DISPLAY` unset, `QT_QPA_PLATFORM=offscreen`:

| Import | Time |
| --- | --- |
| numpy | 84 ms |
| cv2 | 145 ms |
| PySide6.QtWidgets | 99 ms |
| PySide6.QtSvg | 102 ms |
| numba | 245 ms |
| numba, then a one-line `@njit` first call | 261 ms import + 158 ms compile |
| `import fishing_bot` (includes cv2) | 180 ms |
| PIL.Image | 29 ms |
| mss | 15 ms |

On the smoked baseline, the real solver's first height took 2.5 s and
3.1 s in two runs. That is the JIT of the actual DP, not the one-line
probe. By 25 s the build had reached the middle heights and was still
going. The in-app text says 1–5 minutes, which matches a full table.
The pure-Python fallback was not timed. The source's own comment says
10–50×, which would turn that first setup into tens of minutes. I did
not run it, because a half-hour of CPU was not a reasonable thing to
start.

`src/qt_gui.py` imports `debug_ui` (and therefore Tk and Pillow) on every
GUI start, inside a `try` (lines 69–85). `DEBUG_MODE_EN` is `False` in
`src/utils.py`. The debug windows are never opened in normal play. That
import is why Tcl/Tk (2.6 MB) and Pillow are in the Linux exe. Making
the import conditional is a small code change. Excluding Tk entirely is
safe for normal play and breaks the debug windows. It was not split out
as its own build; 2.6 MB is the upper bound.

Onefile also extracts the archive to a temp directory before Python
starts. That cost was not timed on its own. It is paid on every launch,
including a fishing-only launch, and it includes llvmlite even when
jigsaw is never opened.

## 8. Security and supply chain

- `requirements.txt` floats. The Windows build is exact. The Linux build
  is "whatever `.venv` last installed". Those three disagree today, which
  is the supply-chain issue that matters: two binaries, two stacks, one
  test run.
- `pip-audit`  (installed in a throwaway venv, not into `.venv`) against
  the Windows pins reported Pillow 12.2.0 only. The identifiers are
  duplicated in the database. Every one is fixed in 12.3.0 or 13.0.0,
  including CVE-2026-96638, CVE-2026-112895, CVE-2026-128067, and
  GHSA-78g4-xqwv-q3g8. Pillow is in the Windows exe.
- The same audit against `.venv` reported only `pip` 26.0.1
  (PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721,
  fixed in 26.1 through 26.2). `pip` is not in the frozen archive. The
  game binary does not ship it. Upgrading pip in `.venv` is still worth
  doing before the next build.
- numpy 2.5.4, OpenCV 5.0.0.93, PySide6 6.12.0, numba 0.68.1, and the
  other installed runtime libraries were not reported.
- There is no `VERIFIED.json` and no `SHA256SUMS.txt` in the tree.
  `tools/release.py` writes them into `dist/release-<version>/` after a
  build. That directory is not present. The GitHub release
  `Mt2-Fishbot-v2.0.1` (the latest) has an empty asset list. The README
  still points at the MediaFire file for v2.0.1. The exes in `dist/`
  are local builds with no recorded hash to compare.

## 9. Plan, in order

**Quick wins, after you say yes**

1. Apply the `qt-slim` filter to `packaging/build_linux.spec`, and the
   matching Windows filter (QML/Quick/PDF, translations, unused image
   plugins, Pillow AVIF, and `opengl32sw.dll`) to `packaging/build.spec`.
   Linux is already smoked. Windows gets its check on the next
   `tools/release.py build`, plus a look at the file dialog and the
   header GIF.
2. Delete `psutil` from `requirements.txt`, both specs, and the pin list.
3. Fix the evdev sentence in `requirements.txt` so a fresh install is
   not described as something the running app needs.
4. Leave UPX off. Optionally set `strip=True` on the Linux spec for the
   ~4.5 MB. Smoke it once if you want that included.

**Pin alignment, then one Windows build**

5. Set the `tools/build.py` pins to the tested stack: numpy 2.5.4 (or
   2.5.5 if you want the patch and a fresh test run),
   opencv-python-headless 5.0.0.93, Pillow 13.0.0, PySide6 6.12.0,
   pynput 1.8.2, numba 0.68.1, llvmlite 0.50.0, PyInstaller 6.22.3.
   Keep mss, psutil-if-it-stays, PyAutoGUI, and pygetwindow.
   Leave the Wine Python at 3.14.4 unless you also want a new NuGet
   SHA-256 in `tools/build_windows_exe.sh`. 3.14.4 can install these
   wheels.
6. Reinstall `.venv` from those pins so the next Linux binary is the
   same stack as Windows, then run `tools/run_tests.py` before any
   release build.

**Your call, because it changes first-run jigsaw**

7. Drop numba and llvmlite from both specs (and from the hidden imports,
   including letting `jigsaw_solver.deterministic` fail the numba import
   the way the `try` already allows). Linux save is 68.7 MB on top of
   the Qt filter only if both are done; they do not overlap, and the
   combination was not built. Keep the pure-Python fallback. The first
   jigsaw launch with no cache becomes the slow path. Anyone who already
   has `solver_cache_f64.npz` keeps the fast solver.

**Not now**

- OpenCV 5.1, a custom OpenCV without FFmpeg, and merging the two
  OpenBLAS copies. That is where the remaining ~60 MB of Linux OpenCV
  sits, and it means a private wheel. High effort, easy to change
  detection.
- Shipping the 91 MB solver cache inside the exe.
- UPX.
- A Python upgrade past 3.14.4 for the Wine build, until you want to
  re-pin the NuGet hash.

## 10. Decisions

1. Drop numba and llvmlite from the frozen binaries, knowing the first
   jigsaw table build becomes the pure-Python path?
2. Apply the Qt/GTK/translation/AVIF filter on Linux, and the same idea
   plus `opengl32sw.dll` on Windows?
3. Move the Windows pins onto the tested stack (Pillow 13.0.0,
   PySide 6.12.0, OpenCV 5.0.0.93, numpy 2.5.4, numba 0.68.1 if it
   stays)? Numpy 2.5.5 as well, after a test run?
4. Remove `psutil`?
5. Leave the Wine Python at 3.14.4?

## 11. Review and what was done (Windows focus, 18 November 2026)

Verified against the archive of the shipped `dist/Fishing Puzzle Player v2.0.2.exe`
and a new Wine build (`tools/build_windows_exe.sh`). Linux items were not
pursued beyond removing `psutil` from its spec.

**Done**

| Item | Result |
| --- | --- |
| Windows binary filter in `packaging/build.spec` (§9.1) | Dropped `opengl32sw.dll`, Qt Quick/QML/PDF/VirtualKeyboard/Network, translations, the TUIO/tls/networkinformation plugins, the direct2d/minimal/offscreen platform plugins and unused image formats. Kept qwindows, svg, gif, ico, jpeg (fish thumbnails), qmodernwindowsstyle |
| **Not in the audit:** `cv2/opencv_videoio_ffmpeg*.dll` (11.8 MB compressed) | On Windows it is loaded at run time only by `VideoCapture`/`VideoWriter`, and `cv2.pyd` does not import it. `src/` never opens a video. Dropped |
| **Not in the audit:** Pillow and tkinter | The NuGet Python the exe is built with has **no tkinter**, so the Tk debug windows never worked in the exe and Pillow (6.7 MB, incl. `_avif`) was dead weight. Excluded together with pyscreeze/mouseinfo/pymsgbox; pyautogui imports them in `try` and keeps its mouse functions |
| pygments / setuptools / numba.pycc | Pulled in by the numba hook for debug dumps and AOT. Excluded; the PYZ went 12.85 → 8.72 MB |
| psutil (§9.2) | Removed from `requirements.txt`, both specs and `tools/build.py` |
| Pin alignment (§9.5) | `tools/build.py` now pins the tested stack: numpy 2.5.4, OpenCV 5.0.0, Pillow 13.0.0 (fixes the 12.2.0 advisories), PySide6 6.12.0, pynput 1.8.2, numba 0.68.1, llvmlite 0.50.0, PyInstaller 6.22.3. `release.py status` now reports only Python 3.14.4 vs 3.14.10 as drift. `requirements.txt` floors raised to match |
| `qt_gui.py` | The Tk + `debug_ui` import is skipped unless `DEBUG_MODE_EN`, so normal starts no longer import Tk/Pillow (or log a failed import on Windows) |

**Measured**

| | 2.0.2 exe (old pins) | New build |
| --- | --- | --- |
| File | 152.6 MB | **117.7 MB** |
| Unpacked on every launch | 382.6 MB, 323 files | **310.7 MB, 190 files** |

The upgrade itself grew llvmlite (106.6 → 120.4 MB raw, LLVM 22) and
`cv2.pyd` (74.5 → 85.9 MB raw), so the cuts are larger than the net change.
Every DLL/PYD import of the new archive resolves to a packed file or a
Windows system DLL (checked with pefile). Under Wine with `mscms.dll` present
the exe opened the full GUI in jigsaw mode and numba built the complete
90.6 MB solver cache in under 50 s. Unit tests: 521 passed.

**Problem found: the Windows smoke test could not fail on a broken GUI**

PySide6 6.12's `qwindows.dll` imports `mscms.dll`. Windows always has it;
Fedora's Wine ships it in the separate `wine-cms` package, which is not
installed here. Without it Qt shows "no Qt platform plugin could be
initialized", and that message box keeps the process alive, so the old
`release.py` smoke (alive after 45 s = pass) would have recorded the exe as
verified. It also passed `QT_QPA_PLATFORM=xcb` to the Windows exe.
`tools/release.py` now:

- runs the Windows smoke on a copy in a fresh folder with a config that opens
  jigsaw mode, and requires the `data/cache/jigsaw` folder the main window
  creates; no longer sets `QT_QPA_PLATFORM` for Wine. Checked: the old 2.0.2
  exe passes, the new exe fails on a Wine without `mscms.dll`;
- shows `Wine mscms.dll` in `status` tooling;
- deletes the `_MEI*` folders the killed smoke runs leave in the build
  prefix (3.4 GB had piled up).

**Not done**

- Dropping numba/llvmlite (§9.7, 37–41 MB): kept. The pure-Python table
  build would be 10–50× slower (tens of minutes to hours on first jigsaw use),
  while numba builds it in under a minute.
- UPX, OpenCV without FFmpeg as a custom wheel, shipping the cache: as the audit says.
- `libcrypto`/`libssl` (2.1 MB): the stdlib `ssl` is reached through asyncio/http
  imports; not worth the risk.
- Onedir instead of onefile would remove the per-launch unpack entirely, but
  changes the release asset to a zip. Not changed.

# Running the bot on Linux

Metin2 is a Windows game, so on Linux it runs under **Wine** (plain wine,
Lutris, Bottles…). The bot then runs natively on Linux next to it.

> Playing in a **Windows VM** instead? Run the normal Windows `.exe` inside the
> VM. Nothing on this page applies.

## How it works

| Need | Windows | Linux |
|---|---|---|
| Find / focus the game window | Win32 API | X11. Wine draws its windows through X11 (XWayland on a Wayland desktop), so the game is an ordinary X11 window |
| Read the game picture | screen copy, or `PrintWindow` | the game window's own pixels (X11 `GetImage`). Copying the whole screen is impossible on Wayland; reading the window works even while it is covered |
| Mouse / keyboard | Interception driver, or PyAutoGUI/pynput | **uinput**: a virtual kernel mouse + keyboard ("Fishbot virtual mouse/keyboard"). Kernel level like Interception, works on Wayland and Xorg. Fallback: XTest |

All OS code is in `src/platforms/` (`win32.py`, `x11.py`, `uinput.py`); the
detection, bot logic, timing and GUI are shared by both systems.

Tested on Fedora 44, KDE Plasma 6.8 (Wayland), Wine 11.20.

## Setup

1. **Wine must use its X11 driver.** That is the default whenever `$DISPLAY` is
   set (every normal desktop session). Don't enable Wine's Wayland driver
   (`winewayland`, Proton's `PROTON_ENABLE_WAYLAND=1`). Otherwise the game window is
   invisible to the bot.

2. **Python 3.14 and the dependencies**

   ```bash
   # Fedora
   sudo dnf install python3-devel python3-tkinter
   # Debian / Ubuntu
   sudo apt install python3-dev python3-venv python3-tk

   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

   (`python3-devel`/`python3-dev` is only needed because pynput builds `evdev`.)

3. **Allow the virtual mouse/keyboard** (`/dev/uinput`). Check first:

   ```bash
   test -w /dev/uinput && echo "uinput OK"
   ```

   If it is not writable, give the logged-in user access:

   ```bash
   echo 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
     | sudo tee /etc/udev/rules.d/60-fishbot-uinput.rules
   sudo modprobe uinput
   sudo udevadm control --reload && sudo udevadm trigger
   ```

   (Log out and back in if it still isn't writable.) Without it the bot falls back to
   XTest, which Wayland desktops restrict.

4. **Run**

   ```bash
   .venv/bin/python src/fishing_bot.py
   ```

   The header shows the input method in use: `UINPUT` (good) or `XTEST` (fallback,
   hover for the reason).

## Differences from Windows

- **Saved positions.** On Linux the window rectangle is the game's client area. The
  title bar is drawn by the desktop and isn't part of it, while on Windows it is.
  Positions captured on Windows (drop/confirm buttons, inventory pages, armor slot)
  are therefore off by the title bar height. Capture them again on Linux.
- **F5 (pause)** works while the game or the bot window has focus. It doesn't
  work while a native Wayland app has focus.
- **Display scaling**: use 100% (as on Windows). Multi-monitor and fractional
  scaling aren't tested yet.

## Tests

```bash
python tools/run_tests.py              # unit tests in parallel (~2.5 min); never touch the desktop
python tools/run_tests.py --serial     # same, one process (slow, ~11 min)
python tools/run_tests.py --os --xvfb  # + OS-layer tests on a private virtual display
                                       #   (needs: sudo dnf install xorg-x11-server-Xvfb openbox)
python tools/run_tests.py --os         # + OS-layer tests on your desktop (they take focus briefly)
python tools/run_tests.py --live       # + real mouse/keyboard tests and the Wine end-to-end test
```

`tests/os_layer/test_contract.py` is one set of checks that runs against the
Windows code on Windows and the Linux code on Linux. CI
(`.github/workflows/tests.yml`) runs everything except `--live` on both systems
for every push.

The Wine end-to-end test (`tests/os_layer/test_live_wine.py`) uses
`tools/wine_probe/d3dprobe.c`, a tiny Direct3D 9 program that stands in for the
game. It needs `mingw64-gcc` (Fedora) or `gcc-mingw-w64-x86-64` (Debian/Ubuntu)
to build. `FISHBOT_WINE` / `FISHBOT_WINEPREFIX` select another wine binary or
prefix.

## Building the Windows .exe on Linux

PyInstaller cannot cross-compile, so the Windows exe is built by a Windows
Python running under Wine. One command:

```bash
tools/build_windows_exe.sh                          # -> dist/Fishing Puzzle Player vX.Y.Z.exe
tools/build_windows_exe.sh --distpath dist/test     # elsewhere, e.g. not to overwrite a release
```

- Build environment: `~/.local/share/fishbot-winbuild` (own Wine prefix +
  Windows Python 3.14 from the NuGet package `python` + `requirements.txt` +
  PyInstaller). Created automatically on the first run, reused afterwards.
- It is headless: no window opens and nothing takes the focus, so it is safe
  to run while playing.
- Name and version of the exe come from `packaging/build.spec`
  (`python tools/build.py --version X.Y.Z --no-build` updates them).
- A build takes a few minutes; the exe is ~150 MB.
- The native Linux binary is built from `packaging/build_linux.spec`:
  `.venv/bin/pyinstaller --clean --noconfirm packaging/build_linux.spec`.
- A full release (version bump, both binaries, GitHub release) is
  `tools/release.py` — see its docstring for the steps.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Game window not in the list | Wine is using its Wayland driver, see setup step 1. Check with `xprop` (click the game window: it must respond) |
| Header shows `XTEST` | `/dev/uinput` not writable, see setup step 3 |
| Clicks land off target | Display scaling isn't 100%, or a multi-monitor layout. Please report it |
| Debug view black | The game window is minimized. It can be covered, but not minimized |

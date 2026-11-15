"""
End to end on Linux: the bot's real OS layer against a Direct3D 9 window
running under Wine — the situation the Linux port exists for.

tools/wine_probe/d3dprobe.c stands in for the Metin2 client: an 800x600 D3D9
window with corner markers, a minigame panel and a moving "fish" in the stock
minigame colors, which prints every click/key it receives in client
coordinates. These tests check that the game window is found, captured with
its exact colors, focused, and that clicks and keys arrive where aimed.

Live (takes focus, moves the mouse, types): pytest tests/os_layer --live

Needs wine, plus mingw-w64 to build the probe (Fedora: mingw64-gcc,
Debian/Ubuntu: gcc-mingw-w64-x86-64). FISHBOT_WINE picks another wine binary
(e.g. a Lutris runner), FISHBOT_WINEPREFIX another prefix. The probe exits by
itself; it never stops the prefix (that would also close the game).
"""

import os
import queue
import shutil
import subprocess
import threading
import time
import uuid

import pytest

import platforms
from fish_detector import DetectionProfile, FishDetector
from screen_capture import WindowCapture
from window_manager import WindowManager

pytestmark = [pytest.mark.linux, pytest.mark.live]

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROBE_SRC = os.path.join(ROOT, "tools", "wine_probe", "d3dprobe.c")
PROBE_EXE = os.path.join(ROOT, "tools", "wine_probe", "build", "d3dprobe.exe")
CW, CH = 800, 600
MAGENTA, YELLOW, PANEL, BG = (255, 0, 255), (0, 255, 255), (220, 147, 37), (40, 40, 40)  # BGR


def _probe_exe():
    fresh = os.path.exists(PROBE_EXE) and os.path.getmtime(PROBE_EXE) >= os.path.getmtime(PROBE_SRC)
    if fresh:
        return PROBE_EXE
    cc = shutil.which("x86_64-w64-mingw32-gcc")
    if not cc:
        pytest.skip("mingw-w64 not installed (needed to build tools/wine_probe/d3dprobe.c)")
    os.makedirs(os.path.dirname(PROBE_EXE), exist_ok=True)
    subprocess.run([cc, "-O2", "-o", PROBE_EXE, PROBE_SRC, "-ld3d9", "-lgdi32", "-luser32"], check=True)
    return PROBE_EXE


class Probe:
    def __init__(self):
        wine = os.environ.get("FISHBOT_WINE") or shutil.which("wine")
        if not wine:
            pytest.skip("wine not installed")
        self.title = f"Metin2 Probe {uuid.uuid4().hex[:6]}"
        env = dict(os.environ, WINEDEBUG="-all")
        if os.environ.get("FISHBOT_WINEPREFIX"):
            env["WINEPREFIX"] = os.environ["FISHBOT_WINEPREFIX"]
        self.proc = subprocess.Popen([wine, _probe_exe(), self.title, "90"], env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.expect(lambda l: l.startswith("READY"), timeout=60, what="probe start")
        self.window = None
        end = time.time() + 15
        while time.time() < end and self.window is None:
            self.window = next((w for n, w in WindowManager.get_all_windows() if w.title == self.title), None)
            time.sleep(0.2)
        assert self.window is not None, "probe window not in the window list"
        self.wm = WindowManager()
        self.wm.selected_window = self.window

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line.strip())

    def drain(self):
        while not self.lines.empty():
            self.lines.get_nowait()

    def expect(self, pred, timeout=3.0, what="event"):
        end = time.time() + timeout
        seen = []
        while time.time() < end:
            try:
                line = self.lines.get(timeout=0.05)
            except queue.Empty:
                continue
            seen.append(line)
            if pred(line):
                return line
        raise AssertionError(f"{what}: not received; got {seen}")

    def close(self):
        try:
            self.proc.stdin.close()          # probe exits on EOF
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()


@pytest.fixture(scope="module")
def probe():
    if platforms.native is None or not platforms.native.available():
        pytest.skip("no X11 display")
    p = Probe()
    yield p
    p.close()


def _near(a, b, tol=3):
    return all(abs(int(p) - int(q)) <= tol for p, q in zip(a, b))


def test_found_as_a_metin2_window(probe):
    names = [n for n, _ in WindowManager.get_all_windows()]
    assert probe.title in names
    # Titles with "metin2" come before everything else
    assert names.index(probe.title) < len([n for n in names if "metin" in n.lower() or "mt2" in n.lower()])


def test_rect_is_the_800x600_client_area(probe):
    probe.wm.invalidate_rect_cache()
    assert probe.wm.get_window_rect()[2:] == (CW, CH)


def test_activation_reaches_the_game(probe):
    probe.drain()
    assert probe.wm.activate_window()
    assert probe.wm.is_foreground()


def test_capture_shows_the_d3d_frame_with_exact_colors(probe):
    assert probe.wm.activate_window()
    cap = WindowCapture(probe.wm, "auto")
    try:
        frame = cap.grab(0, 0, CW, CH)
        assert frame is not None and frame.shape == (CH, CW, 3)
        for (x, y), want, what in (((4, 4), MAGENTA, "top-left marker"),
                                   ((CW - 5, CH - 5), YELLOW, "bottom-right marker"),
                                   ((250, 130), PANEL, "panel"),
                                   ((600, 450), BG, "background")):
            got = tuple(int(v) for v in frame[y, x])
            assert _near(got, want), f"{what} at ({x},{y}) is {got}, expected {want}"
        time.sleep(0.25)
        cap.invalidate()
        later = cap.grab(0, 0, CW, CH)
        assert (frame[190:210, 100:400] != later[190:210, 100:400]).any(), "frames are not live"
    finally:
        cap.close()


def test_capture_works_while_window_is_not_in_front(probe):
    # Another X11 window takes focus (the probe may then be covered).
    import tkinter as tk
    root = tk.Tk()
    cover_title = f"cover {uuid.uuid4().hex[:6]}"
    root.title(cover_title)
    root.geometry(f"400x300+{probe.wm.get_window_rect()[0]}+{probe.wm.get_window_rect()[1]}")
    try:
        cover = WindowManager()
        end = time.time() + 5
        while cover.selected_window is None and time.time() < end:
            root.update()
            cover.selected_window = next((w for n, w in WindowManager.get_all_windows() if n == cover_title), None)
            time.sleep(0.05)
        assert cover.selected_window is not None, "cover window never listed"
        assert cover.activate_window()
        root.update()
        cap = WindowCapture(probe.wm, "auto")
        try:
            frame = cap.grab(0, 0, CW, CH)
            assert _near(tuple(int(v) for v in frame[130, 250]), PANEL)
            assert _near(tuple(int(v) for v in frame[4, 4]), MAGENTA)
        finally:
            cap.close()
    finally:
        root.destroy()


def test_fish_detector_sees_panel_and_fish(probe):
    assert probe.wm.activate_window()
    cap = WindowCapture(probe.wm, "auto")
    try:
        frame = cap.grab(0, 0, CW, CH)
    finally:
        cap.close()
    det = FishDetector(DetectionProfile())
    bounds = det.find_fishing_window_bounds(frame, observe_only=True)
    assert bounds is not None, "panel not detected"
    active, fish = det.detect_window_and_fish(frame, observe_only=True)
    assert active and fish is not None, "fish not detected"
    assert 100 <= fish[0] <= 400 and 185 <= fish[1] <= 215


def test_clicks_arrive_at_client_coordinates(probe):
    from input_backend import create_mouse_backend
    mouse = create_mouse_backend({})
    assert probe.wm.activate_window()
    left, top, _, _ = probe.wm.get_window_rect()
    for (x, y), button, tag in (((250, 150), "left", "L"), ((640, 480), "right", "R")):
        probe.drain()
        mouse.move_to(left + x, top + y)
        time.sleep(0.05)
        mouse.mouse_down(button)
        time.sleep(0.02)
        mouse.mouse_up(button)
        down = probe.expect(lambda l: l.startswith(tag + "DOWN"), what=f"{button} down")
        assert down == f"{tag}DOWN {x} {y}", f"{mouse.name}: aimed at {(x, y)}, game got {down}"
        probe.expect(lambda l: l.startswith(tag + "UP"), what=f"{button} up")


def test_hotkeys_arrive_as_the_right_virtual_keys(probe):
    from input_backend import create_keyboard_controller
    try:
        from pynput.keyboard import Key
        space, ctrl = Key.space, Key.ctrl
    except Exception:
        space, ctrl = "space", "ctrl"
    kb = create_keyboard_controller({})
    assert probe.wm.activate_window()
    probe.drain()

    def tap(key):
        kb.press(key)
        time.sleep(0.03)
        kb.release(key)
        time.sleep(0.03)

    tap("1")                       # bait key -> VK '1' (49)
    tap(space)                     # cast     -> VK_SPACE (32)
    kb.press(ctrl)                 # quick skip CTRL+G -> VK_CONTROL (17), 'G' (71)
    tap("g")
    kb.release(ctrl)
    downs = []
    end = time.time() + 3
    while time.time() < end and len(downs) < 4:
        try:
            line = probe.lines.get(timeout=0.05)
        except queue.Empty:
            continue
        if line.startswith("KEYDOWN"):
            downs.append(int(line.split()[1]))
    assert downs == [49, 32, 17, 71], f"game saw virtual keys {downs}"

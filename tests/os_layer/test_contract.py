"""
Platform contract: what the shared code (WindowManager, WindowCapture,
Humanizer) needs from the OS, checked against the real OS.

The same tests run on Windows (platforms/win32.py) and on Linux
(platforms/x11.py + uinput.py). A change made for one OS that breaks a
promise the other relies on fails here, on the OS it broke.

The test window is a Tk window with known colors at known client-area
positions:

    magenta 10x10 at client (0, 0)       yellow 10x10 at client (310, 230)
    panel color 120x80 at client (100, 80), the rest dark blue-gray
"""

import time
import uuid

import numpy as np
import pytest

import platforms
from screen_capture import WindowCapture
from window_manager import WindowManager

pytestmark = pytest.mark.skipif(platforms.native is None or not platforms.native.available(),
                                reason="no desktop session (Windows desktop or X11 $DISPLAY)")

W, H = 320, 240
BG = (80, 60, 40)          # BGR of #283c50
MAGENTA = (255, 0, 255)
YELLOW = (0, 255, 255)
PANEL = (220, 147, 37)     # BGR of #2593dc (stock minigame panel color)


def _pump(root, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.01)


def _wait(root, cond, timeout=3.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class TkTarget:
    def __init__(self):
        import tkinter as tk
        self.title = f"Fishbot contract {uuid.uuid4().hex[:8]}"
        self.root = tk.Tk()
        self.root.title(self.title)
        self.root.geometry(f"{W}x{H}+160+140")
        self.root.resizable(False, False)
        c = tk.Canvas(self.root, width=W, height=H, highlightthickness=0, bd=0, bg="#283c50")
        c.pack()
        c.create_rectangle(0, 0, 10, 10, fill="#ff00ff", outline="")
        c.create_rectangle(W - 10, H - 10, W, H, fill="#ffff00", outline="")
        c.create_rectangle(100, 80, 220, 160, fill="#2593dc", outline="")
        self.canvas = c
        self.clicks = []
        self.keys = []
        c.bind("<ButtonPress>", lambda e: self.clicks.append((e.num, e.x, e.y)))
        self.root.bind("<KeyPress>", lambda e: self.keys.append((e.keysym.lower(), bool(e.state & 0x4))))
        self.root.update()
        _pump(self.root, 0.5)

    def client_origin(self):
        self.root.update()
        return self.canvas.winfo_rootx(), self.canvas.winfo_rooty()

    def find(self):
        for name, win in WindowManager.get_all_windows():
            if win.title == self.title:
                return name, win
        return None, None

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


@pytest.fixture
def target():
    t = TkTarget()
    assert _wait(t.root, lambda: t.find()[1] is not None), "test window never appeared in the window list"
    yield t
    t.close()


@pytest.fixture
def wm(target):
    m = WindowManager()
    m.selected_window = target.find()[1]
    return m


def _color_at(frame, x, y):
    return tuple(int(v) for v in frame[y, x, :3])


def _close(a, b, tol=3):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def _check_picture(frame, ox, oy, label):
    """The test picture at client offset (ox, oy) inside ``frame`` (BGR/BGRA)."""
    for (x, y), want, what in (((4, 4), MAGENTA, "magenta corner"),
                               ((W - 5, H - 5), YELLOW, "yellow corner"),
                               ((160, 120), PANEL, "panel"),
                               ((50, 200), BG, "background")):
        got = _color_at(frame, ox + x, oy + y)
        assert _close(got, want), f"{label}: {what} at client ({x},{y}) is {got}, expected {want}"


# ---------------------------------------------------------------- windows

def test_window_is_listed_visible_and_titled(target):
    name, win = target.find()
    assert name == target.title
    assert win.visible
    assert platforms.window_handle(win)


def test_rect_contains_the_client_area(target, wm):
    left, top, width, height = wm.get_window_rect()
    cx, cy = target.client_origin()
    # Windows: the rect includes the title bar and borders.
    # Linux: the rect *is* the client area.
    assert left <= cx and top <= cy
    assert cx + W <= left + width and cy + H <= top + height
    if platforms.IS_LINUX:
        assert (left, top, width, height) == (cx, cy, W, H)


def test_activate_and_foreground(target, wm):
    assert wm.is_window_available()
    assert wm.activate_window()
    assert wm.is_foreground()


def test_minimize_then_activate_restores(target, wm):
    _, win = target.find()
    assert wm.activate_window()
    win.minimize()
    assert _wait(target.root, lambda: platforms.native.is_minimized(platforms.window_handle(win))), \
        "window did not report minimized"
    assert wm.activate_window()
    assert _wait(target.root, lambda: not platforms.native.is_minimized(platforms.window_handle(win)))
    assert wm.is_foreground()


def test_closed_window_is_unavailable(target, wm):
    target.close()
    time.sleep(0.3)
    wm.invalidate_rect_cache()
    assert not wm.is_window_available()
    assert not wm.activate_window()


# ---------------------------------------------------------------- capture

def test_render_window_returns_the_window_pixels(target, wm):
    assert wm.activate_window()
    _pump(target.root, 0.3)
    left, top, width, height = wm.get_window_rect()
    frame = platforms.native.render_window(wm._hwnd(), width, height)
    assert frame is not None and frame.dtype == np.uint8 and frame.ndim == 3 and frame.shape[2] == 4
    assert frame.shape[0] <= height and frame.shape[1] <= width
    cx, cy = target.client_origin()
    _check_picture(frame, cx - left, cy - top, "render_window")


def test_render_window_area_matches_the_full_render(target, wm):
    area = getattr(platforms.native, "render_window_area", None)
    if area is None:
        pytest.skip("this OS renders only whole windows")
    assert wm.activate_window()
    _pump(target.root, 0.3)
    left, top, width, height = wm.get_window_rect()
    hwnd = wm._hwnd()
    full = platforms.native.render_window(hwnd, width, height)
    assert full is not None
    fh, fw = full.shape[:2]
    # Inside: the same pixels as the full render's crop.
    part = area(hwnd, 100, 80, 120, 80)
    assert part is not None and part.shape == (80, 120, 4)
    assert np.array_equal(part[:, :, :3], full[80:160, 100:220, :3])
    # Hanging over the right/bottom edge: the outside part is black.
    part = area(hwnd, fw - 5, fh - 5, 20, 20)
    assert part is not None and part.shape == (20, 20, 4)
    assert np.array_equal(part[:5, :5, :3], full[fh - 5:, fw - 5:, :3])
    assert not part[5:, :, :3].any() and not part[:, 5:, :3].any()
    # Hanging over the left/top edge.
    part = area(hwnd, -3, -2, 10, 10)
    assert np.array_equal(part[2:, 3:, :3], full[:8, :7, :3])
    assert not part[:2, :, :3].any() and not part[:, :3, :3].any()
    # Entirely outside.
    assert area(hwnd, fw + 10, 0, 10, 10) is None


def test_window_capture_auto_mode_reads_the_game(target, wm):
    assert wm.activate_window()
    _pump(target.root, 0.3)
    cap = WindowCapture(wm, "auto")
    try:
        for _ in range(3):
            cap.verify()
        left, top, _, _ = wm.get_window_rect()
        cx, cy = target.client_origin()
        frame = cap.grab(cx - left, cy - top, W, H)
        assert frame is not None and frame.shape == (H, W, 3) and frame.flags["C_CONTIGUOUS"]
        _check_picture(frame, 0, 0, f"WindowCapture({cap.requested})")
    finally:
        cap.close()


def test_screen_capture_flag_is_bool():
    assert isinstance(platforms.native.screen_capture_reliable(), bool)


@pytest.mark.linux
def test_screen_copy_flag_matches_what_the_x_server_can_do():
    # XWayland refuses to copy the root window; Xorg and Xvfb allow it. The
    # capture mode choice hangs on this flag being right.
    from Xlib import X
    from platforms import x11
    try:
        x11._root().get_image(0, 0, 8, 8, X.ZPixmap, 0xFFFFFFFF)
        can_copy = True
    except Exception:
        can_copy = False
    assert x11.screen_capture_reliable() == can_copy


# ---------------------------------------------------------------- input (live)

@pytest.mark.live
def test_mouse_backend_clicks_land_on_exact_client_pixel(target, wm):
    from input_backend import create_mouse_backend
    mouse = create_mouse_backend({})
    assert wm.activate_window()
    _pump(target.root, 0.2)
    cx, cy = target.client_origin()
    for (x, y), button, num in (((123, 77), "left", 1), ((250, 190), "right", 3)):
        mouse.move_to(cx + x, cy + y)
        _pump(target.root, 0.1)
        mouse.mouse_down(button)
        time.sleep(0.02)
        mouse.mouse_up(button)
        assert _wait(target.root, lambda: any(c[0] == num for c in target.clicks)), \
            f"{mouse.name}: {button} click never arrived"
        got = [c for c in target.clicks if c[0] == num][-1]
        assert (got[1], got[2]) == (x, y), f"{mouse.name}: {button} click landed at {got[1:]}, aimed at {(x, y)}"
        assert mouse.position() == (cx + x, cy + y)


@pytest.mark.live
def test_keyboard_backend_types_hotkeys(target, wm):
    from input_backend import create_keyboard_controller
    try:
        from pynput.keyboard import Key
        space, ctrl = Key.space, Key.ctrl
    except Exception:
        space, ctrl = "space", "ctrl"
    kb = create_keyboard_controller({})
    assert kb is not None
    assert wm.activate_window()
    _pump(target.root, 0.2)

    def tap(key):
        kb.press(key)
        time.sleep(0.02)
        kb.release(key)

    tap("1")
    tap(space)
    kb.press(ctrl)
    tap("g")
    kb.release(ctrl)
    assert _wait(target.root, lambda: len([k for k in target.keys if k[0] not in ("control_l", "control_r")]) >= 3)
    keys = [k for k in target.keys if k[0] not in ("control_l", "control_r")]
    assert keys[0] == ("1", False)
    assert keys[1] == ("space", False)
    assert keys[2] == ("g", True), f"ctrl+g arrived as {keys[2]}"

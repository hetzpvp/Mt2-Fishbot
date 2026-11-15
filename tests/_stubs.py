"""Test support: stub the Windows-only / GUI-only modules so the bot's logic
can be imported and exercised on any OS, plus a tiny simulated game screen."""

import os
import sys
import types

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")

# Set once install() ran: the process now has stub modules in place of the
# OS ones, so tests/os_layer/ (which needs the real ones) must run in a
# process of its own (tools/run_tests.py does that).
INSTALLED = False


def install():
    global INSTALLED
    INSTALLED = True
    if SRC not in sys.path:
        sys.path.insert(0, SRC)

    def stub(name, **attrs):
        if name in sys.modules:
            return sys.modules[name]
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    noop = lambda *a, **k: None
    stub("pyautogui", FAILSAFE=False, moveTo=noop, mouseDown=noop, mouseUp=noop,
         click=noop, position=lambda: (0, 0))
    stub("mss", mss=lambda: None)
    stub("pygetwindow", getAllWindows=lambda: [])

    class _Key:
        ctrl = "ctrl"; space = "space"; f1 = "f1"; f2 = "f2"; f3 = "f3"; f4 = "f4"; f5 = "f5"

    class _Controller:
        def __init__(self):
            self.events = []

        def press(self, key):
            self.events.append(("press", key))

        def release(self, key):
            self.events.append(("release", key))

    class _Listener:
        def __init__(self, *a, **k):
            self.daemon = True

        def start(self):
            pass

        def stop(self):
            pass

    kb = stub("pynput.keyboard", Controller=_Controller, Key=_Key, Listener=_Listener)
    stub("pynput", keyboard=kb)

    # No real desktop in unit tests: no window lookups, no window capture and,
    # above all, no real input devices (on Linux the bots would otherwise
    # create uinput devices and move the real mouse). The OS layer itself is
    # tested in tests/os_layer/.
    import platforms
    platforms.native = None


def hsv_to_bgr(h, s, v):
    px = np.uint8([[[h, s, v]]])
    return tuple(int(c) for c in cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0, 0])


class FakeWindowManager:
    def __init__(self, width=800, height=600):
        self.selected_window = object()
        self.rect = (0, 0, width, height)
        self.activations = 0

    def is_window_available(self):
        return True

    def activate_window(self, force_activate=False):
        self.activations += 1
        return True

    def get_window_rect(self):
        return self.rect

    def invalidate_rect_cache(self):
        pass


class FakeMouse:
    name = "fake"

    def __init__(self):
        self.pos = (0, 0)
        self.clicks = []

    def move_to(self, x, y):
        self.pos = (int(x), int(y))

    def position(self):
        return self.pos

    def mouse_down(self, button="left"):
        self.clicks.append((self.pos, button))

    def mouse_up(self, button="left"):
        pass

    def click(self, button="left"):
        self.clicks.append((self.pos, button))


class SimScreen:
    """An 800x600 'game window' with an optional minigame panel.

    The fish position is a function of a virtual clock that the test advances,
    so runs are deterministic and fast."""

    W, H = 800, 600
    PANEL = (260, 150, 280, 220)  # x, y, w, h
    RADIUS = 67

    def __init__(self, panel_bgr, fish_bgr, seed=7):
        rng = np.random.default_rng(seed)
        base = rng.integers(40, 90, size=(self.H, self.W, 3), dtype=np.uint8)
        self.background = cv2.GaussianBlur(base, (9, 9), 0)
        self.panel_bgr = panel_bgr
        self.fish_bgr = fish_bgr
        self.panel_open = False
        self.t = 0.0
        self.blank = False  # simulate a capture hiccup

    def fish_center(self):
        x, y, w, h = self.PANEL
        cx, cy = x + w // 2, y + h // 2
        # Sweeps left-right through the click circle
        return (int(cx + 115 * np.sin(self.t * 2.2)), int(cy + 25 * np.cos(self.t * 1.3)))

    def fish_in_circle(self):
        x, y, w, h = self.PANEL
        fx, fy = self.fish_center()
        return (fx - (x + w // 2)) ** 2 + (fy - (y + h // 2)) ** 2 < self.RADIUS ** 2

    def render(self):
        frame = self.background.copy()
        if self.blank:
            frame[:] = 0
            return frame
        if self.panel_open:
            x, y, w, h = self.PANEL
            cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), self.panel_bgr, -1)
            cv2.circle(frame, (x + w // 2, y + h // 2), self.RADIUS, (255, 255, 255), 2)
            cv2.ellipse(frame, self.fish_center(), (14, 7), 0, 0, 360, self.fish_bgr, -1)
        return frame

    def grab(self, left, top, width, height):
        frame = self.render()
        return np.ascontiguousarray(frame[top:top + height, left:left + width])

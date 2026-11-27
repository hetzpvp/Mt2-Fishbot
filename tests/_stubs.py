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


class InventoryScreen:
    """A game window with an inventory laid out like a real client's: a
    clock that changes, empty equipment slots, two rows of page tabs (the
    active one lighter) and the 5x9 item grid right below them.

    A tab click shows its page ``page_delay`` seconds later (a slow machine);
    the first ``lost_clicks`` tab clicks are not noticed by the game at all."""

    W, H = 800, 700
    SLOT, COLS, ROWS, PAGES = 32, 5, 9, 4
    GRID = (620, 361)                                       # top-left corner of the grid
    TAB_BOXES = {1: (623, 320, 699, 337), 2: (702, 320, 778, 337),
                 3: (623, 341, 699, 358), 4: (702, 341, 778, 358)}
    # Where the user clicked when setting the tabs up: nowhere near the centers
    TABS = {1: (660, 329), 2: (752, 323), 3: (671, 350), 4: (733, 348)}
    EQUIPMENT = [(740, 230), (740, 270), (640, 250)]        # empty equipment slots (top-left)

    def __init__(self, page_delay=0.0, lost_clicks=0, highlight=True, dead_spots=()):
        rng = np.random.default_rng(3)
        base = rng.integers(40, 90, size=(self.H, self.W, 3), dtype=np.uint8)
        self.background = cv2.GaussianBlur(base, (9, 9), 0)
        self.slot_img = cv2.imread(os.path.join(ROOT, "assets", "fishing", "empty_slot.jpg"))
        self.pages = [dict() for _ in range(self.PAGES)]    # slot index -> item image
        self.page = 0
        self.page_delay = page_delay
        self.lost_clicks = lost_clicks
        self.dead_spots = list(dead_spots)                  # points where a click is swallowed (a tab label)
        self.highlight = highlight
        self.tab_clicks = []
        self.clicks = []
        self._pending = None                                # (page, time it shows)
        self.clock = 0

    def config(self):
        return {f"inv_page_{page}_pos": list(point) for page, point in self.TABS.items()}

    def slot_center(self, slot):
        return (self.GRID[0] + (slot % self.COLS) * self.SLOT + self.SLOT // 2,
                self.GRID[1] + (slot // self.COLS) * self.SLOT + self.SLOT // 2)

    def fill(self, page, count, item):
        for slot in range(count):
            self.pages[page][slot] = item

    def click(self, pos, button="left"):
        import time
        self.clicks.append((tuple(pos), button))
        for page, (x1, y1, x2, y2) in self.TAB_BOXES.items():
            if x1 <= pos[0] <= x2 and y1 <= pos[1] <= y2 and button == "left":
                self.tab_clicks.append(page)
                if any(abs(pos[0] - dx) <= 4 and abs(pos[1] - dy) <= 4 for dx, dy in self.dead_spots):
                    return
                if self.lost_clicks > 0:
                    self.lost_clicks -= 1
                    return
                self._pending = (page - 1, time.perf_counter() + self.page_delay)

    def render(self):
        import time
        if self._pending and time.perf_counter() >= self._pending[1]:
            self.page, self._pending = self._pending[0], None
        frame = self.background.copy()
        self.clock += 1                                     # the clock never stands still
        cv2.putText(frame, f"17:02:{self.clock % 60:02d}", (630, 216), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1)
        cv2.rectangle(frame, (614, 224), (786, self.GRID[1] + self.ROWS * self.SLOT + 6), (38, 34, 30), -1)
        for x, y in self.EQUIPMENT:
            frame[y:y + self.SLOT, x:x + self.SLOT] = self.slot_img
        for page, (x1, y1, x2, y2) in self.TAB_BOXES.items():
            lit = self.highlight and page - 1 == self.page
            cv2.rectangle(frame, (x1, y1), (x2, y2), (134, 122, 112) if lit else (84, 72, 62), -1)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (10, 10, 10), 1)
            cv2.putText(frame, "I" * page if page < 4 else "IV", ((x1 + x2) // 2 - 8, y2 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (217, 217, 217), 1)
        items = self.pages[self.page]
        for slot in range(self.COLS * self.ROWS):
            x = self.GRID[0] + (slot % self.COLS) * self.SLOT
            y = self.GRID[1] + (slot // self.COLS) * self.SLOT
            frame[y:y + self.SLOT, x:x + self.SLOT] = items.get(slot, self.slot_img)
        return frame

    def grab(self, left, top, width, height):
        frame = self.render()
        return np.ascontiguousarray(frame[top:top + height, left:left + width])

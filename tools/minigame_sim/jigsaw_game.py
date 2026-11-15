"""A simulated jigsaw event window.

One board, two crate slots, an inventory strip with page tabs, the open /
discard / result dialogs and a piece that follows the cursor. It is drawn
from the bot's own detection templates (assets/jigsaw), so what the detector
sees is what it is tuned for.

Hard-mode options: ``dialog_delay`` / ``piece_delay`` may be (min, max) ranges
(a different delay every time), ``lose_clicks`` drops placement clicks,
``lose_confirms`` drops clicks on a dialog's confirm button, ``lose_slot_clicks``
drops that many clicks that would put a carried crate into its slot, and the
window can sit anywhere on the desktop (``left``, ``top``).

Used headless by tests/test_jigsaw_sim.py (several windows on one simulated
desktop) and live by the simulator window (python tools/minigame_sim)."""

import os
import random
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from jigsaw_solver.jigsaw import FIGURES  # noqa: E402

ASSETS = os.path.join(ROOT, "assets", "jigsaw")
W, H = 800, 600
GRID = (100, 150, 264, 176)
CELL = 44
CONFIRM = (400, 380)
SLOTS = {"special": (414, 218), "normal": (414, 273)}   # the detector's default layout
PAGES = {1: (640, 180), 2: (680, 180), 3: (720, 180), 4: (760, 180)}
INV_ORIGIN = (W - 200, 200)                              # the bot's inventory strip
FIGURE_FILES = ["Jigsaw_piece6.png", "Jigsaw_piece4.png", "Jigsaw_piece1.png",
                "Jigsaw_piece2.png", "Jigsaw_piece3.png", "Jigsaw_piece5.png"]


def _load(name, flags=cv2.IMREAD_UNCHANGED):
    img = cv2.imread(os.path.join(ASSETS, name), flags)
    assert img is not None, name
    return img


SPECIAL = 6            # the piece a special crate always gives: 3 columns x 2 rows
SPECIAL_FILE = "Jigsaw_piece_deluxe.png"


def figure_cells(index):
    """(col, row) offsets of a figure, read from its bitmask."""
    if index == SPECIAL:
        return [(c, r) for c in range(3) for r in range(2)]
    mask = int(FIGURES[index].value)
    return [(a >> 2, a & 3) for a in range(24) if mask & ((1 << 23) >> a)]


class JigsawGame:
    def __init__(self, left=0, seed=1, normal=10 ** 6, special=0, inventory=None,
                 lose_clicks=0.0, dialog_delay=0.0, piece_delay=0.02, pieces=None,
                 gives_pieces=True, top=0, lose_confirms=0.0, lose_slot_clicks=0):
        self.left, self.top = left, top
        self.rng = random.Random(seed)
        self.board = set()                 # (col, row)
        self.cell_figure = {}
        self.slots = {"normal": normal, "special": special}
        # page -> [(section, (x, y) in the strip, stack), ...]; one stack per
        # page may be given without the list.
        self.inventory = {page: (list(items) if isinstance(items, list) else [items])
                          for page, items in (inventory or {}).items()}
        self.page = 1
        self.carrying = None
        self.cursor = (10, 10)
        self.held = None
        self.held_at = 0.0
        self.dialog = None                 # "open" | "discard" | "result"
        self.dialog_at = 0.0
        self.dialog_delay = dialog_delay
        self.piece_delay = piece_delay
        self.lose_clicks = lose_clicks
        self.lose_confirms = lose_confirms
        self.lose_slot_clicks = lose_slot_clicks
        self.lost_confirms = 0
        self.lost_slot_clicks = 0
        self.pieces = list(pieces or [])
        self.gives_pieces = gives_pieces
        # outcome counters
        self.opened = 0
        self.opened_by = {"normal": 0, "special": 0}
        self.opening = "normal"            # kind of the crate being opened
        self.special_opened_on = []        # filled cells when each special crate was opened
        self.special_placed = 0
        self.refills = 0                   # stacks taken out of the inventory
        self.placed = 0
        self.discarded = 0
        self.completed = 0
        self.lost = 0
        self.errors = []
        self.page_clicks = 0
        self.early_confirms = 0            # confirm clicked before its dialog was up

        rng = np.random.default_rng(seed)
        # Dark, low-contrast window background (same value on all channels)
        gray = cv2.GaussianBlur(rng.integers(20, 44, size=(H, W), dtype=np.uint8), (15, 15), 0)
        self.background = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        self.grid_img = cv2.resize(_load("jigsaw_empty_grid.png", cv2.IMREAD_COLOR), GRID[2:])
        self.crate_img = {s: _load(f"jigsaw_{s}_crate.png", cv2.IMREAD_COLOR) for s in ("normal", "special")}
        self.piece_img = [_load(f) for f in FIGURE_FILES] + [_load(SPECIAL_FILE)]

    # ------------------------------------------------------------------ state

    def mask(self):
        m = 0
        for col, row in self.board:
            m |= (1 << 23) >> (col * 4 + row)
        return m

    def _delay(self, value):
        """A fixed delay, or a new one from a (min, max) range every time."""
        if isinstance(value, (tuple, list)):
            return self.rng.uniform(*value)
        return value

    def _dialog_up(self):
        return self.dialog is not None and time.perf_counter() >= self.dialog_at

    def _holding(self):
        return self.held is not None and time.perf_counter() >= self.held_at

    @staticmethod
    def _near(x, y, point, rx=30, ry=14):
        return abs(x - point[0]) <= rx and abs(y - point[1]) <= ry

    # ------------------------------------------------------------------ input

    def click(self, x, y, button):
        if button == "right":
            if self._holding() and self.dialog is None:
                self.dialog = "discard"
                self.dialog_at = time.perf_counter() + self._delay(self.dialog_delay)
            return

        if self.dialog is not None:
            if not self._dialog_up():
                if self._near(x, y, CONFIRM):
                    self.early_confirms += 1
                return
            if self.dialog == "result":
                if self._near(x, y, (W // 2, int(H * 0.63)), 40, 16):
                    self.dialog = None
                    self.board.clear()
                    self.cell_figure.clear()
                return
            if self._near(x, y, CONFIRM):
                if self.rng.random() < self.lose_confirms:
                    self.lost_confirms += 1
                    return
                if self.dialog == "open":
                    if self.gives_pieces:
                        if self.opening == "special":
                            self.held = SPECIAL
                        else:
                            self.held = self.pieces.pop(0) if self.pieces else self.rng.randrange(6)
                        self.held_at = time.perf_counter() + self._delay(self.piece_delay)
                else:
                    self.held = None
                    self.discarded += 1
                self.dialog = None
            return

        for page, point in PAGES.items():
            if self._near(x, y, point, 12, 10):
                self.page = page
                self.page_clicks += 1
                return

        if self.carrying is None:
            items = self.inventory.get(self.page, [])
            for item in items:
                section, (ix, iy), stack = item
                if self._near(x, y, (INV_ORIGIN[0] + ix, INV_ORIGIN[1] + iy), 18, 16):
                    self.carrying = (section, stack)
                    items.remove(item)
                    self.refills += 1
                    return

        for section, point in SLOTS.items():
            if self._near(x, y, point, 20, 17):
                if self.carrying is not None:
                    if self.lost_slot_clicks < self.lose_slot_clicks:
                        self.lost_slot_clicks += 1
                        return
                    if self.carrying[0] == section:
                        self.slots[section] += self.carrying[1]
                        self.carrying = None
                    return
                if self.held is not None:
                    self.errors.append("crate opened while a piece is on the cursor")
                    return
                if self.slots[section] > 0:
                    self.slots[section] -= 1
                    self.opened += 1
                    self.opened_by[section] += 1
                    if section == "special":
                        self.special_opened_on.append(len(self.board))
                    self.opening = section
                    self.dialog = "open"
                    self.dialog_at = time.perf_counter() + self._delay(self.dialog_delay)
                return

        gx, gy, gw, gh = GRID
        if self._holding() and gx <= x < gx + gw and gy <= y < gy + gh:
            if self.rng.random() < self.lose_clicks:
                self.lost += 1
                return
            cells = figure_cells(self.held)
            wc = max(c for c, _ in cells) + 1
            hc = max(r for _, r in cells) + 1
            col0 = int(round((x - gx) / CELL - wc / 2.0))
            row0 = int(round((y - gy) / CELL - hc / 2.0))
            target = [(col0 + c, row0 + r) for c, r in cells]
            if any(not (0 <= c < 6 and 0 <= r < 4) or (c, r) in self.board for c, r in target):
                self.errors.append(f"illegal placement of figure {self.held} at {target}")
                return
            for cell in target:
                self.board.add(cell)
                self.cell_figure[cell] = self.held
            if self.held == SPECIAL:
                self.special_placed += 1
            self.held = None
            self.placed += 1
            if len(self.board) == 24:
                self.completed += 1
                self.dialog = "result"
                self.dialog_at = time.perf_counter() + 0.05

    # ----------------------------------------------------------------- render

    def _paste(self, frame, img, center):
        h, w = img.shape[:2]
        x1, y1 = center[0] - w // 2, center[1] - h // 2
        fx1, fy1 = max(0, x1), max(0, y1)
        fx2, fy2 = min(W, x1 + w), min(H, y1 + h)
        if fx2 <= fx1 or fy2 <= fy1:
            return
        part = img[fy1 - y1:fy2 - y1, fx1 - x1:fx2 - x1]
        if part.shape[2] == 4:
            alpha = part[:, :, 3:4] > 24
            frame[fy1:fy2, fx1:fx2] = np.where(alpha, part[:, :, :3], frame[fy1:fy2, fx1:fx2])
        else:
            frame[fy1:fy2, fx1:fx2] = part

    def render(self):
        frame = self.background.copy()
        gx, gy, gw, gh = GRID
        frame[gy:gy + gh, gx:gx + gw] = self.grid_img
        for (col, row), figure in list(self.cell_figure.items()):
            tile = self.piece_img[figure]
            color = tile[tile[:, :, 3] > 24][:, :3].mean(axis=0)
            cv2.rectangle(frame, (gx + col * CELL + 2, gy + row * CELL + 2),
                          (gx + (col + 1) * CELL - 3, gy + (row + 1) * CELL - 3),
                          tuple(int(v) for v in color), -1)
        for section, point in SLOTS.items():
            if self.slots[section] > 0:
                self._paste(frame, self.crate_img[section], point)
            else:
                pass   # an empty slot is just the dark window background
        for page, (px, py) in PAGES.items():
            shade = 120 if page == self.page else 62
            cv2.rectangle(frame, (px - 12, py - 9), (px + 12, py + 9), (shade, shade, shade), -1)
        for section, (ix, iy), _ in self.inventory.get(self.page, []):
            self._paste(frame, self.crate_img[section], (INV_ORIGIN[0] + ix, INV_ORIGIN[1] + iy))
        if self.carrying is not None:
            self._paste(frame, self.crate_img[self.carrying[0]], self.cursor)
        if self._dialog_up():
            cv2.rectangle(frame, (300, 320), (500, 400), (70, 70, 70), -1)
            cv2.rectangle(frame, (CONFIRM[0] - 30, CONFIRM[1] - 12), (CONFIRM[0] + 30, CONFIRM[1] + 12),
                          (190, 190, 190), -1)
        if self._holding():
            self._paste(frame, self.piece_img[self.held], self.cursor)
        return frame

    def grab(self, x, y, w, h):
        frame = self.render()
        out = np.zeros((h, w, 3), np.uint8)
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(W, x + w), min(H, y + h)
        if x2 > x1 and y2 > y1:
            out[y1 - y:y2 - y, x1 - x:x2 - x] = frame[y1:y2, x1:x2]
        return out

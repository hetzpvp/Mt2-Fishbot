"""A simulated jigsaw event window.

One board, two crate slots, an inventory strip with page tabs, the open /
discard / result dialogs and a piece that follows the cursor. It is drawn
from the bot's own detection templates (assets/jigsaw), so what the detector
sees is what it is tuned for.

Hard-mode options: ``dialog_delay`` / ``piece_delay`` may be (min, max) ranges
(a different delay every time), ``lose_clicks`` drops placement clicks,
``lose_confirms`` drops clicks on a dialog's confirm button, ``lose_slot_clicks``
drops that many clicks that would put a carried crate into its slot,
``discard_delay`` keeps a discarded piece on the cursor until the "server"
answers, ``place_dialog`` asks "Place the piece here?" before a placement, and the window can sit anywhere on the desktop (``left``, ``top``).
``puzzle_at=(x, y)`` draws the real Fish Puzzle window (its textures, 1:1,
the game's layout) with its corner there instead of the plain board: the
bot then has to find it by itself (no grid is defined).
As on a real client, a left click beside the board with a piece held asks
"Discard this piece?" (``stray_discard_dialogs``).
``lose_crate_clicks`` drops that share of crate clicks (a real client lost
some right after a focus switch: no "Open this box?" came). ``confirm_flicker``
is the world behind the Confirm button moving: every ``flicker_period``
seconds its gray goes up by that much and back (a real character's idle
animation there changed the watched patch by up to 15). ``world_clicks``
counts left clicks that hit nothing but the world (on a real client the
character walks there).

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
# The real slot layout (measured on two clients) scaled to this board, which
# is the detector's default: 0.193 grid widths right of the grid, 0.168 grid
# heights above and 0.234 below its middle.
SLOTS = {"special": (GRID[0] + GRID[2] + round(GRID[2] * 0.193), GRID[1] + GRID[3] // 2 - round(GRID[3] * 0.168)),
         "normal": (GRID[0] + GRID[2] + round(GRID[2] * 0.193), GRID[1] + GRID[3] // 2 + round(GRID[3] * 0.234))}
PAGES = {1: (640, 180), 2: (680, 180), 3: (720, 180), 4: (760, 180)}
INV_ORIGIN = (W - 200, 200)                              # the bot's inventory strip
FREE_SLOT = (INV_ORIGIN[0] + 150, INV_ORIGIN[1] + 230)   # the empty slot of a page that has room
# The real puzzle window (tools/mt2_assets.py puzzle): grid and slots in it
REAL_ASSETS = os.path.join(ROOT, "tools", "minigame_sim", "assets", "real_mt2")
PUZZLE_GRID_IN_WINDOW = (18, 34)
PUZZLE_CELL = 32
PUZZLE_SLOTS_IN_WINDOW = {"special": (248, 77), "normal": (248, 128)}
# Per figure, one cell of its sprite in the puzzle atlas (x, y)
PUZZLE_CELL_SPRITES = {0: (0, 235), 1: (0, 139), 2: (192, 139), 3: (128, 139), 4: (32, 203), 5: (96, 203), 6: (32, 139)}
FIGURE_FILES = ["Jigsaw_piece6.png", "Jigsaw_piece4.png", "Jigsaw_piece1.png",
                "Jigsaw_piece2.png", "Jigsaw_piece3.png", "Jigsaw_piece5.png"]


def _load(name, flags=cv2.IMREAD_UNCHANGED):
    img = cv2.imread(os.path.join(ASSETS, name), flags)
    assert img is not None, name
    return img


SPECIAL = 6            # the piece a special crate always gives: 3 columns x 2 rows
# Where a real client holds each figure, in cells from its top-left corner
# (measured; see JigsawBot._HOTSPOTS). The special piece is not measured.
HOTSPOTS = {0: (0.5, 0.5), 1: (0.5, 1.5), 2: (0.5, 0.5), 3: (0.5, 0.5), 4: (0.5, 0.5), 5: (1.25, 0.5)}
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
                 gives_pieces=True, top=0, lose_confirms=0.0, lose_slot_clicks=0, shows_result=True,
                 dialog_shade=190, full_pages=(), discard_delay=0.0, place_dialog=False,
                 result_button=(W // 2, int(H * 0.63)), result_delay=0.05, puzzle_at=None,
                 lose_crate_clicks=0.0, confirm_flicker=0, flicker_period=0.12):
        self.left, self.top = left, top
        self.puzzle_at = puzzle_at
        if puzzle_at is None:
            self.grid, self.cell, self.slot_pos = GRID, CELL, SLOTS
        else:
            px, py = puzzle_at
            self.grid = (px + PUZZLE_GRID_IN_WINDOW[0], py + PUZZLE_GRID_IN_WINDOW[1], 6 * PUZZLE_CELL, 4 * PUZZLE_CELL)
            self.cell = PUZZLE_CELL
            self.slot_pos = {k: (px + x, py + y) for k, (x, y) in PUZZLE_SLOTS_IN_WINDOW.items()}
        self.rng = random.Random(seed)
        self.board = set()                 # (col, row)
        self.cell_figure = {}
        self.slots = {"normal": normal, "special": special}
        # page -> [(section, (x, y) in the strip, stack), ...]; one stack per
        # page may be given without the list.
        self.inventory = {page: (list(items) if isinstance(items, list) else [items])
                          for page, items in (inventory or {}).items()}
        self.page = 1
        # Pages with no empty slot (the bot checks for room before a board)
        self.full_pages = set(full_pages)
        self.carrying = None
        self.cursor = (10, 10)
        self.held = None
        self.held_at = 0.0
        # A confirmed discard takes the piece off the cursor only when the
        # server answers, this many seconds later (a real client: > 150 ms).
        self.discard_delay = discard_delay
        self.discard_at = None
        # Some servers ask "Place the piece here?" after a click on the board
        # and place the piece only on its Yes.
        self.place_dialog = place_dialog
        self.pending_place = None
        self.dialog = None                 # "open" | "discard" | "result"
        self.dialog_at = 0.0
        self.dialog_delay = dialog_delay
        self.piece_delay = piece_delay
        self.lose_clicks = lose_clicks
        self.lose_confirms = lose_confirms
        self.lose_slot_clicks = lose_slot_clicks
        self.lose_crate_clicks = lose_crate_clicks
        self.lost_crate_clicks = 0
        self.confirm_flicker = confirm_flicker
        self.flicker_period = flicker_period
        self.world_clicks = 0              # left clicks on nothing but the world
        self.lost_confirms = 0
        self.lost_slot_clicks = 0
        self.pieces = list(pieces or [])
        self.gives_pieces = gives_pieces
        self.shows_result = shows_result   # False: a full board brings up no result dialog
        self.result_button = result_button # the result dialog's OK (a real client: middle, 52.5 % down)
        self.result_delay = result_delay   # seconds from the last piece to the result dialog
        self.result_shown_at = None
        self.result_answered_after = []    # seconds each result dialog waited for its OK
        # Gray of the dialog's Confirm button. The default stands out far more than
        # a real client's (whose button changes the watched patch by 29-47 levels):
        # about 75 on this ~32 background is the real contrast.
        self.dialog_shade = dialog_shade
        # outcome counters
        self.opened = 0
        self.opened_by = {"normal": 0, "special": 0}
        self.opening = "normal"            # kind of the crate being opened
        self.special_opened_on = []        # filled cells when each special crate was opened
        self.special_placed = 0
        self.refills = 0                   # stacks taken out of the inventory
        self.put_back = 0                  # stacks put back into the inventory
        self.placed = 0
        self.discarded = 0
        self.completed = 0
        self.lost = 0
        self.errors = []
        self.page_clicks = 0
        self.early_confirms = 0            # confirm clicked before its dialog was up
        self.stray_discard_dialogs = 0     # "Discard this piece?" asked by a left click beside the board

        rng = np.random.default_rng(seed)
        # Dark, low-contrast window background (same value on all channels)
        gray = cv2.GaussianBlur(rng.integers(20, 44, size=(H, W), dtype=np.uint8), (15, 15), 0)
        self.background = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        self.grid_img = cv2.resize(_load("jigsaw_empty_grid.png", cv2.IMREAD_COLOR), GRID[2:])
        self.crate_img = {s: _load(f"jigsaw_{s}_crate.png", cv2.IMREAD_COLOR) for s in ("normal", "special")}
        self.piece_img = [_load(f) for f in FIGURE_FILES] + [_load(SPECIAL_FILE)]
        self.empty_slot_img = cv2.imread(os.path.join(ROOT, "assets", "fishing", "empty_slot.jpg"),
                                         cv2.IMREAD_COLOR)
        if puzzle_at is not None:
            self.puzzle_window = cv2.imread(os.path.join(REAL_ASSETS, "puzzle_window.png"), cv2.IMREAD_COLOR)
            atlas = cv2.imread(os.path.join(REAL_ASSETS, "puzzle_atlas.png"), cv2.IMREAD_UNCHANGED)
            assert self.puzzle_window is not None and atlas is not None, "run tools/mt2_assets.py puzzle"
            self.cell_sprites = {f: atlas[y:y + PUZZLE_CELL, x:x + PUZZLE_CELL]
                                 for f, (x, y) in PUZZLE_CELL_SPRITES.items()}
            self.slot_crate_img = {s: cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
                                   for s, img in self.crate_img.items()}

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

    def _settle(self):
        """Applies a discard whose server answer has arrived."""
        if self.discard_at is not None and time.perf_counter() >= self.discard_at:
            self.discard_at = None
            self.held = None
            self.discarded += 1

    def _holding(self):
        self._settle()
        return self.held is not None and time.perf_counter() >= self.held_at

    @staticmethod
    def _near(x, y, point, rx=30, ry=14):
        return abs(x - point[0]) <= rx and abs(y - point[1]) <= ry

    # ------------------------------------------------------------------ input

    def click(self, x, y, button):
        self._settle()
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
                if self._near(x, y, self.result_button, 28, 8):
                    self.result_answered_after.append(time.perf_counter() - self.dialog_at)
                    self.dialog = None
                    self.board.clear()
                    self.cell_figure.clear()
                return
            if self._near(x, y, CONFIRM):
                if self.rng.random() < self.lose_confirms:
                    self.lost_confirms += 1
                    return
                if self.dialog == "place":
                    self.dialog = None
                    self._place(self.pending_place)
                    self.pending_place = None
                    return
                if self.dialog == "open":
                    if self.gives_pieces:
                        if self.opening == "special":
                            self.held = SPECIAL
                        else:
                            self.held = self.pieces.pop(0) if self.pieces else self.rng.randrange(6)
                        self.held_at = time.perf_counter() + self._delay(self.piece_delay)
                elif self.discard_delay:
                    if self.discard_at is None:    # a second Yes does not hurry the server
                        self.discard_at = time.perf_counter() + self.discard_delay
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

        if self.carrying is not None and x >= INV_ORIGIN[0] and y >= INV_ORIGIN[1]:
            # Put a carried stack back into the inventory where it was clicked
            section, stack = self.carrying
            self.inventory.setdefault(self.page, []).append(
                (section, (x - INV_ORIGIN[0], y - INV_ORIGIN[1]), stack))
            self.carrying = None
            self.put_back += 1
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

        for section, point in self.slot_pos.items():
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
                if self.slots[section] > 0 and self.lose_crate_clicks \
                        and self.rng.random() < self.lose_crate_clicks:
                    self.lost_crate_clicks += 1
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

        gx, gy, gw, gh = self.grid
        if self._holding() and gx <= x < gx + gw and gy <= y < gy + gh:
            if self.rng.random() < self.lose_clicks:
                self.lost += 1
                return
            cells = figure_cells(self.held)
            wc = max(c for c, _ in cells) + 1
            hc = max(r for _, r in cells) + 1
            # As a real client: the piece is held at a fixed point (HOTSPOTS)
            # and its top-left corner snaps to the nearest cell.
            hx, hy = HOTSPOTS.get(self.held, (wc / 2.0, hc / 2.0))
            col0 = int(round((x - gx) / self.cell - hx))
            row0 = int(round((y - gy) / self.cell - hy))
            target = [(col0 + c, row0 + r) for c, r in cells]
            if any(not (0 <= c < 6 and 0 <= r < 4) or (c, r) in self.board for c, r in target):
                self.errors.append(f"illegal placement of figure {self.held} at {target}")
                return
            if self.place_dialog:
                self.pending_place = target
                self.dialog = "place"
                self.dialog_at = time.perf_counter() + self._delay(self.dialog_delay)
                return
            self._place(target)
            return

        if button == "left" and self._holding():
            # A left click beside the board with a piece held asks to discard
            # it, as on a real client.
            self.stray_discard_dialogs += 1
            self.dialog = "discard"
            self.dialog_at = time.perf_counter() + self._delay(self.dialog_delay)
        elif button == "left" and not (gx <= x < gx + gw and gy <= y < gy + gh):
            self.world_clicks += 1

    def _place(self, target):
        for cell in target:
            self.board.add(cell)
            self.cell_figure[cell] = self.held
        if self.held == SPECIAL:
            self.special_placed += 1
        self.held = None
        self.placed += 1
        if len(self.board) == 24:
            self.completed += 1
            if self.shows_result:
                self.dialog = "result"
                self.dialog_at = time.perf_counter() + self.result_delay

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
        if self.confirm_flicker and int(time.perf_counter() / self.flicker_period) % 2:
            # The world behind the Confirm button moving (drawn under any dialog)
            cx, cy = CONFIRM
            area = frame[cy - 20:cy + 20, cx - 40:cx + 40]
            area[:] = np.clip(area.astype(np.int16) + self.confirm_flicker, 0, 255).astype(np.uint8)
        gx, gy, gw, gh = self.grid
        if self.puzzle_at is not None:
            px, py = self.puzzle_at
            ph, pw = self.puzzle_window.shape[:2]
            frame[py:py + ph, px:px + pw] = self.puzzle_window
            for (col, row), figure in list(self.cell_figure.items()):
                self._paste(frame, self.cell_sprites[figure],
                            (gx + col * PUZZLE_CELL + 16, gy + row * PUZZLE_CELL + 16))
            for section, point in self.slot_pos.items():
                if self.slots[section] > 0:
                    self._paste(frame, self.slot_crate_img[section], point)
        else:
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
        if self.page not in self.full_pages and self.empty_slot_img is not None:
            self._paste(frame, self.empty_slot_img, FREE_SLOT)
        for section, (ix, iy), _ in self.inventory.get(self.page, []):
            self._paste(frame, self.crate_img[section], (INV_ORIGIN[0] + ix, INV_ORIGIN[1] + iy))
        if self.carrying is not None:
            self._paste(frame, self.crate_img[self.carrying[0]], self.cursor)
        if self._dialog_up() and self.dialog == "result":
            bx, by = self.result_button
            cv2.rectangle(frame, (bx - 120, by - 50), (bx + 120, by + 22), (70, 70, 70), -1)
            cv2.rectangle(frame, (bx - 28, by - 8), (bx + 28, by + 8), (self.dialog_shade,) * 3, -1)
        elif self._dialog_up():
            cv2.rectangle(frame, (300, 320), (500, 400), (70, 70, 70), -1)
            cv2.rectangle(frame, (CONFIRM[0] - 30, CONFIRM[1] - 12), (CONFIRM[0] + 30, CONFIRM[1] + 12),
                          (self.dialog_shade,) * 3, -1)
        if self._holding() and self.dialog != "place":
            # A real client stops drawing the piece while it asks where to place it
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

"""A simulated fishing window: cast, bite, minigame panel, inventory.

The picture is plain shapes in configurable colors (a "skin"), not the game's
artwork: what matters to the bot is the structure — a panel that opens after
the cast, a click circle, a moving fish, a timer — and that is what other
clients keep while changing every color. The inventory strip is drawn from
the bot's own item templates (assets/fishing) so auto fish handling can run.

Rules (same as the game's):
- a bait key, then space, casts; the panel opens after a random bite delay
- a left click on the fish while it is inside the circle is a hit, anything
  else in the panel is a miss and costs time
- after a click the panel takes no further click for ``click_cooldown``
  seconds (about half a second in the game); clicks in that time do nothing
- three hits before the timer runs out is a catch: an item appears in the
  first free inventory slot
- space while the line is out reels it in again: no bite, and the rod needs
  its usual moment before the next cast
- right click on a living fish opens it; left click on an item picks it up,
  a click in the world then asks to drop it (DROP, then YES)

Hard-mode options (all off by default) make the window behave like the
awkward clients and connections the bot has to cope with:

- ``wander``        the panel opens somewhere else for every game
- ``skins``         every game uses the next look from this list
- ``catch_delay``   the caught item lands this long after the panel closed (lag)
- ``blank_chance``  share of captures that come back black (capture hiccups)
- ``popup_chance``  chance per cast that some other game window (not the
                    minigame) pops up while the line is out
- ``move_panel()``  the player drags the panel during a game

The game is pure logic + numpy: ``key`` / ``click`` / ``move`` in, ``render``
out. It is driven headless by the tests and live by the simulator window.
"""

import glob
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS = os.path.join(ROOT, "assets", "fishing")

HSV = Tuple[int, int, int]


def hsv_to_bgr(h, s, v):
    px = np.uint8([[[h, s, v]]])
    return tuple(int(c) for c in cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0, 0])


@dataclass(frozen=True)
class Skin:
    """The look of the minigame panel (colors in OpenCV HSV: H 0-179)."""
    name: str
    panel: HSV
    fish: HSV
    panel_size: Tuple[int, int] = (280, 220)
    circle: Optional[Tuple[int, int, int]] = None   # (dx, dy, r) in the panel; None = middle, r 67
    circle_color: HSV = (0, 0, 255)                 # outline
    disc: Optional[HSV] = None                      # filled click zone instead of an outline
    timer: Optional[HSV] = (28, 220, 240)
    textured: bool = False                          # noisy panel background
    animated: bool = False                          # moving "water"
    decoration: bool = False                        # fish-colored icon that never moves
    fish_size: Tuple[int, int] = (14, 7)            # ellipse half-axes


SKINS = {s.name: s for s in (
    # The stock colors the bot's default profile is written for
    Skin("official", panel=(102, 200, 220), fish=(104, 138, 120)),
    # Same layout, every color different
    Skin("emerald", panel=(65, 180, 170), fish=(12, 220, 240)),
    Skin("crimson", panel=(176, 190, 170), fish=(30, 60, 250), circle_color=(25, 200, 255)),
    # Near-gray panel: hue carries no information
    Skin("charcoal", panel=(110, 25, 25), fish=(0, 0, 235), timer=(60, 200, 220)),
    # Small panel
    Skin("compact", panel=(20, 170, 200), fish=(110, 220, 120), panel_size=(220, 180),
         circle=(110, 86, 52), fish_size=(11, 6)),
    # Backgrounds that are not one flat color
    Skin("sand-textured", panel=(22, 110, 190), fish=(115, 200, 110), textured=True),
    # Stock panel, different fish, plus a decoration in the fish's color
    Skin("official-redfish", panel=(102, 200, 220), fish=(3, 215, 225), decoration=True),

    # Filled click zone, off-center, smaller
    Skin("violet-disc", panel=(140, 150, 150), fish=(25, 230, 250), disc=(140, 90, 215),
         circle=(120, 118, 58)),
    # Animated water with white foam lines that move like a fish does
    Skin("water", panel=(78, 190, 170), fish=(20, 230, 240), animated=True),
    # ... the same in a cyan that half-matches the stock panel color
    Skin("cyan-water", panel=(100, 190, 185), fish=(8, 210, 235), animated=True),
    # --- Hard case: the bot does not play this one. See HARD_SKINS. ---
    # Mid-gray panel, as bright as what is behind it
    Skin("low-contrast", panel=(110, 25, 70), fish=(0, 0, 235), timer=(60, 200, 220)),
)}

# Skins the bot is known not to handle. The benchmark still runs them (that is
# how a fix shows up); tests/test_minigame_sim.py expects the others to pass.
HARD_SKINS = ("low-contrast",)

# Inventory strip. The bot reads the right-hand 200 px from y=200 down.
INV_COLS, INV_ROWS, SLOT = 5, 9, 32
INV_PAGES = 4


class FishingGame:
    HITS_NEEDED = 3

    def __init__(self, skin="official", size=(800, 600), seed=1, bite_delay=(1.5, 4.0),
                 duration=15.0, miss_penalty=1.0, fish_speed=(70.0, 230.0), hit_radius=16,
                 click_cooldown=0.5, recast_cooldown=1.5, catch_chance=1.0, dead_fish_chance=0.0, require_bait=True,
                 inventory=True, feedback_text=True, clock=time.perf_counter,
                 space_cancels=True, wander=False, skins=None, catch_delay=(0.0, 0.0),
                 blank_chance=0.0, popup_chance=0.0):
        self.W, self.H = size
        self.rng = random.Random(seed)
        self.clock = clock
        self.bite_delay = bite_delay
        self.duration = duration
        self.miss_penalty = miss_penalty
        self.fish_speed = fish_speed
        self.hit_radius = hit_radius
        self.click_cooldown = click_cooldown
        self.recast_cooldown = recast_cooldown
        self._click_ready = 0.0            # panel clicks before this are ignored
        self.catch_chance = catch_chance
        self.dead_fish_chance = dead_fish_chance
        self.require_bait = require_bait
        self.feedback_text = feedback_text
        self.lock = threading.RLock()
        self.on_event = None               # callable(kind, detail)
        self.space_cancels = space_cancels
        self.wander = wander
        self.skins = list(skins) if skins else None
        self._skin_index = 0
        self.catch_delay = catch_delay
        self._pending_catch = None         # time at which the caught item lands
        self.blank_chance = blank_chance
        self._grab_rng = random.Random(seed + 1000)   # capture noise: not part of the game's dice
        self.popup_chance = popup_chance
        self._popup = None                 # (x, y, w, h, bgr, from, until)

        rng = np.random.default_rng(seed)
        base = rng.integers(40, 90, size=(self.H, self.W, 3), dtype=np.uint8)
        self.background = cv2.GaussianBlur(base, (9, 9), 0)

        # state: "idle" | "waiting" (line is out) | "playing" | "closing"
        self.state = "idle"
        self.state_until = 0.0
        self.ready_at = 0.0                # casts before this are ignored (rod animation)
        self.bait_ready = False
        self.hits = 0
        self.time_left = 0.0
        self.fish = (0.0, 0.0)
        self._target = (0.0, 0.0)
        self._speed = 0.0
        self._pause_until = 0.0
        self._flash = None                 # (text, until)
        self._last = self.clock()
        self._t0 = self._last
        self.cursor = (0, 0)

        self.stats = dict(casts=0, casts_ignored=0, casts_without_bait=0, games=0, hits=0,
                          misses=0, clicks_ignored=0, caught=0, timeouts=0, opened=0, dropped=0,
                          drop_cancelled=0, inventory_full=0, stray_clicks=0, casts_cancelled=0,
                          popups=0)

        # inventory
        self.inventory_enabled = inventory
        self.inv_origin = (self.W - 180, 240)
        self.tabs = [(self.W - 180 + 21 + i * 40, 222) for i in range(INV_PAGES)]
        self.pages = [dict() for _ in range(INV_PAGES)]      # slot index -> file name
        self.page = 0
        self.carrying = None               # (page, slot, file name)
        self.dialog = None                 # "drop" | "confirm"
        self.drop_button = (self.W // 2 - 50, self.H // 2 + 45)
        self.confirm_button = (self.W // 2 - 40, self.H // 2 + 20)
        self._items = {}
        self._slot_img = None
        if inventory:
            slot = cv2.imread(os.path.join(ASSETS, "empty_slot.jpg"))
            if slot is None:
                self.inventory_enabled = False
            else:
                self._slot_img = slot
                for path in sorted(glob.glob(os.path.join(ASSETS, "*_living.jpg"))
                                   + glob.glob(os.path.join(ASSETS, "*_item.jpg"))):
                    img = cv2.imread(path)
                    if img is not None and img.shape[:2] == (SLOT, SLOT):
                        self._items[os.path.basename(path)] = img
        self._item_names = sorted(self._items)

        self.set_skin(skin)

    # ------------------------------------------------------------------- skin

    def set_skin(self, skin):
        with self.lock:
            self.skin = skin if isinstance(skin, Skin) else SKINS[skin]
            s = self.skin
            w, h = s.panel_size
            self.panel = ((self.W - w) // 2, 150, w, h)
            self.circle = s.circle or (w // 2, h // 2, 67)
            self._panel_bgr = hsv_to_bgr(*s.panel)
            self._fish_bgr = hsv_to_bgr(*s.fish)
            self._circle_bgr = hsv_to_bgr(*s.circle_color)
            self._disc_bgr = hsv_to_bgr(*s.disc) if s.disc else None
            self._timer_bgr = hsv_to_bgr(*s.timer) if s.timer else None
            self._texture = None
            if s.textured:
                rng = np.random.default_rng(3)
                noise = cv2.GaussianBlur(rng.integers(-22, 22, size=(h, w)).astype(np.float32), (7, 7), 0)
                self._texture = np.clip(np.array(self._panel_bgr, np.float32) + noise[:, :, None] * 3,
                                        0, 255).astype(np.uint8)
            if s.animated:
                yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
                self._wave_phase = xx * 0.09 + yy * 0.05

    def _place_panel(self):
        """Puts the panel somewhere else in the window (clear of the inventory)."""
        _, _, w, h = self.panel
        max_x = (self.W - 200 if self.inventory_enabled else self.W) - w - 10
        self.panel = (self.rng.randint(10, max(10, max_x)), self.rng.randint(10, max(10, self.H - h - 10)), w, h)

    def move_panel(self, dx, dy):
        """The player drags the open panel (fish and all) by (dx, dy)."""
        with self.lock:
            x, y, w, h = self.panel
            nx = min(max(0, x + dx), self.W - w)
            ny = min(max(0, y + dy), self.H - h)
            dx, dy = nx - x, ny - y
            self.panel = (nx, ny, w, h)
            self.fish = (self.fish[0] + dx, self.fish[1] + dy)
            self._target = (self._target[0] + dx, self._target[1] + dy)

    def _new_popup(self, now):
        """Some other window of the game (a whisper, a shop, a quest text):
        a flat rectangle that is neither the size nor the look of the panel."""
        w, h = self.rng.choice([(200, 130), (340, 150), (180, 270), (150, 110)])
        x = self.rng.randint(10, max(10, self.W - 200 - w - 10))
        y = self.rng.randint(10, self.H - h - 10)
        color = hsv_to_bgr(self.rng.randrange(180), self.rng.randint(40, 200), self.rng.randint(120, 230))
        start = now + self.rng.uniform(0.1, 0.4)
        self._popup = (x, y, w, h, color, start, start + self.rng.uniform(0.8, 2.0))
        self.stats["popups"] += 1

    def circle_abs(self):
        x, y, _, _ = self.panel
        dx, dy, r = self.circle
        return x + dx, y + dy, r

    def fish_in_circle(self):
        cx, cy, r = self.circle_abs()
        return (self.fish[0] - cx) ** 2 + (self.fish[1] - cy) ** 2 < r * r

    @property
    def panel_open(self):
        return self.state in ("playing", "closing")

    # ------------------------------------------------------------------ clock

    def _emit(self, kind, detail=""):
        if self.on_event:
            self.on_event(kind, detail)

    def _new_target(self):
        x, y, w, h = self.panel
        m = 22
        self._target = (self.rng.uniform(x + m, x + w - m), self.rng.uniform(y + m, y + h - m - 14))
        self._speed = self.rng.uniform(*self.fish_speed)

    def update(self):
        """Advances the game to the clock's current time."""
        with self.lock:
            now = self.clock()
            dt = min(0.1, max(0.0, now - self._last))
            self._last = now

            if self._pending_catch is not None and now >= self._pending_catch:
                self._pending_catch = None
                self._add_catch()

            if self.state == "waiting" and now >= self.state_until:
                self.state = "playing"
                self.hits = 0
                self.time_left = self.duration
                self.stats["games"] += 1
                if self.skins:
                    self.set_skin(self.skins[self._skin_index % len(self.skins)])
                    self._skin_index += 1
                if self.wander:
                    self._place_panel()
                x, y, w, h = self.panel
                self.fish = (self.rng.uniform(x + 30, x + w - 30), self.rng.uniform(y + 30, y + h - 44))
                self._new_target()
                self._emit("bite")
            elif self.state == "playing":
                self.time_left -= dt
                if self.time_left <= 0:
                    self.stats["timeouts"] += 1
                    self._end(now, caught=False)
                elif now >= self._pause_until:
                    fx, fy = self.fish
                    tx, ty = self._target
                    dist = float(np.hypot(tx - fx, ty - fy))
                    step = self._speed * dt
                    if dist <= step:
                        self.fish = (tx, ty)
                        self._new_target()
                        if self.rng.random() < 0.25:
                            self._pause_until = now + self.rng.uniform(0.15, 0.6)
                    else:
                        self.fish = (fx + (tx - fx) / dist * step, fy + (ty - fy) / dist * step)
            elif self.state == "closing" and now >= self.state_until:
                self.state = "idle"

    def _end(self, now, caught):
        self.state = "closing"
        self.state_until = now + 0.25
        self.ready_at = now + 0.25 + self.recast_cooldown
        if caught and self.rng.random() < self.catch_chance:
            delay = self.rng.uniform(*self.catch_delay) if max(self.catch_delay) > 0 else 0.0
            if delay > 0:
                self._pending_catch = now + delay
            else:
                self._add_catch()
        self._emit("caught" if caught else "timeout", f"hits {self.hits}")

    def _add_catch(self):
        self.stats["caught"] += 1
        if not (self.inventory_enabled and self._item_names):
            return
        name = self.rng.choice(self._item_names)
        for page in self.pages:
            for slot in range(INV_COLS * INV_ROWS):
                if slot not in page:
                    page[slot] = name
                    return
        self.stats["inventory_full"] += 1

    # ------------------------------------------------------------------ input

    def move(self, x, y):
        self.cursor = (int(x), int(y))

    def key(self, name):
        """'space' casts; any other key counts as a bait hotkey."""
        with self.lock:
            self.update()
            if name != "space":
                self.bait_ready = True
                return
            now = self.clock()
            if self.state == "waiting" and self.space_cancels:
                # Space with the line out pulls it in: no bite from this cast
                self.state = "idle"
                self.ready_at = now + self.recast_cooldown
                self._popup = None
                self.stats["casts_cancelled"] += 1
                self._emit("reeled in")
                return
            if self.state != "idle" or now < self.ready_at or self.dialog or self.carrying:
                self.stats["casts_ignored"] += 1
                return
            if not self.bait_ready:
                self.stats["casts_without_bait"] += 1
                if self.require_bait:
                    return
            self.bait_ready = False
            self.stats["casts"] += 1
            self.state = "waiting"
            self.state_until = now + self.rng.uniform(*self.bite_delay)
            if self.popup_chance and self.stats["games"] >= 1 and self.rng.random() < self.popup_chance:
                self._new_popup(now)
            self._emit("cast")

    @staticmethod
    def _near(x, y, point, rx, ry):
        return abs(x - point[0]) <= rx and abs(y - point[1]) <= ry

    def _slot_at(self, x, y):
        ox, oy = self.inv_origin
        col, row = (x - ox) // SLOT, (y - oy) // SLOT
        if 0 <= col < INV_COLS and 0 <= row < INV_ROWS and x >= ox and y >= oy:
            return int(row * INV_COLS + col)
        return None

    def slot_center(self, slot):
        """Window coordinates of an inventory slot's center."""
        ox, oy = self.inv_origin
        return (ox + (slot % INV_COLS) * SLOT + SLOT // 2, oy + (slot // INV_COLS) * SLOT + SLOT // 2)

    def click(self, x, y, button="left"):
        with self.lock:
            self.update()
            now = self.clock()
            x, y = int(x), int(y)

            if self.state == "playing":
                px, py, pw, ph = self.panel
                if button == "left" and px <= x < px + pw and py <= y < py + ph:
                    if now < self._click_ready:
                        # The game takes one click, then none for a moment
                        self.stats["clicks_ignored"] += 1
                        return
                    self._click_ready = now + self.click_cooldown
                    on_fish = (x - self.fish[0]) ** 2 + (y - self.fish[1]) ** 2 <= self.hit_radius ** 2
                    if on_fish and self.fish_in_circle():
                        self.hits += 1
                        self.stats["hits"] += 1
                        self._flash = ("HIT", now + 0.4)
                        self._emit("hit", f"{self.hits}/{self.HITS_NEEDED}")
                        if self.hits >= self.HITS_NEEDED:
                            self._end(now, caught=True)
                    else:
                        self.stats["misses"] += 1
                        self.time_left -= self.miss_penalty
                        self._flash = ("MISS", now + 0.4)
                        self._emit("miss", "fish outside the circle" if on_fish else "not on the fish")
                else:
                    self.stats["stray_clicks"] += 1
                return

            if not self.inventory_enabled:
                return

            if self.dialog is not None:
                if button == "left" and self.dialog == "drop" and self._near(x, y, self.drop_button, 32, 12):
                    self.dialog = "confirm"
                elif button == "left" and self.dialog == "confirm" and self._near(x, y, self.confirm_button, 32, 12):
                    self.dialog = None
                    self.carrying = None
                    self.stats["dropped"] += 1
                    self._emit("dropped")
                else:
                    # Anything else cancels: the item goes back to its slot
                    page, slot, name = self.carrying
                    self.pages[page][slot] = name
                    self.carrying = None
                    self.dialog = None
                    self.stats["drop_cancelled"] += 1
                    self._emit("drop cancelled")
                return

            if button == "left" and self.carrying is None:
                for i, tab in enumerate(self.tabs):
                    if self._near(x, y, tab, 18, 9):
                        self.page = i
                        return

            slot = self._slot_at(x, y)
            page = self.pages[self.page]
            if slot is not None:
                if self.carrying is not None:
                    if button == "left" and slot not in page:   # put it down
                        page[slot] = self.carrying[2]
                        self.carrying = None
                    return
                name = page.get(slot)
                if name is None:
                    return
                if button == "right":
                    if "_living" in name and self.rng.random() >= self.dead_fish_chance:
                        del page[slot]
                        self.stats["opened"] += 1
                        self._emit("opened", name)
                elif button == "left":
                    del page[slot]
                    self.carrying = (self.page, slot, name)
                return

            if self.carrying is not None and button == "left":
                self.dialog = "drop"

    # ----------------------------------------------------------------- render

    def _draw_inventory(self, frame):
        ox, oy = self.inv_origin
        cv2.rectangle(frame, (ox - 6, self.tabs[0][1] - 14), (ox + INV_COLS * SLOT + 5, oy + INV_ROWS * SLOT + 5),
                      (38, 34, 30), -1)
        for i, (tx, ty) in enumerate(self.tabs):
            color = (150, 140, 120) if i == self.page else (80, 74, 66)
            cv2.rectangle(frame, (tx - 18, ty - 9), (tx + 18, ty + 9), color, -1)
            cv2.putText(frame, str(i + 1), (tx - 4, ty + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (20, 20, 20), 1, cv2.LINE_AA)
        page = self.pages[self.page]
        for slot in range(INV_COLS * INV_ROWS):
            x = ox + (slot % INV_COLS) * SLOT
            y = oy + (slot // INV_COLS) * SLOT
            name = page.get(slot)
            frame[y:y + SLOT, x:x + SLOT] = self._items[name] if name else self._slot_img

    def _button(self, frame, center, text):
        cx, cy = center
        cv2.rectangle(frame, (cx - 32, cy - 12), (cx + 32, cy + 12), (190, 190, 190), -1)
        size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
        cv2.putText(frame, text, (cx - size[0] // 2, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (20, 20, 20), 1, cv2.LINE_AA)

    def _draw_panel(self, frame, now):
        s = self.skin
        x, y, w, h = self.panel
        if s.animated:
            hue, sat, val = s.panel
            wave = np.sin(self._wave_phase + (now - self._t0) * 5.0)
            hsv = np.empty((h, w, 3), np.uint8)
            hsv[:, :, 0] = hue
            hsv[:, :, 1] = sat
            hsv[:, :, 2] = np.clip(val + wave * 28, 0, 255)
            water = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            water[wave > 0.985] = (255, 255, 255)
            frame[y:y + h, x:x + w] = water
        elif self._texture is not None:
            frame[y:y + h, x:x + w] = self._texture
        else:
            cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), self._panel_bgr, -1)

        cx, cy, r = self.circle_abs()
        if self._disc_bgr is not None:
            cv2.circle(frame, (cx, cy), r, self._disc_bgr, -1)
        else:
            cv2.circle(frame, (cx, cy), r, self._circle_bgr, 2)
        if s.decoration:
            cv2.ellipse(frame, (x + 30, y + 24), (16, 8), 0, 0, 360, self._fish_bgr, -1)
        # hit counter: one pip per hit, top right
        for i in range(self.HITS_NEEDED):
            cv2.circle(frame, (x + w - 16 - i * 14, y + 14), 4,
                       (255, 255, 255) if i < self.hits else (60, 60, 60), -1)
        if self._timer_bgr is not None:
            left = max(0.0, self.time_left / self.duration)
            cv2.rectangle(frame, (x + 20, y + h - 14), (x + 20 + int((w - 40) * left), y + h - 8),
                          self._timer_bgr, -1)
        fx, fy = int(round(self.fish[0])), int(round(self.fish[1]))
        cv2.ellipse(frame, (fx, fy), s.fish_size, 0, 0, 360, self._fish_bgr, -1)
        if self.feedback_text and self._flash and now < self._flash[1]:
            text = self._flash[0]
            size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
            cv2.putText(frame, text, (x + (w - size[0]) // 2, y + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 2, cv2.LINE_AA)

    def render(self):
        """The window's picture (BGR, H x W x 3) at the current time."""
        with self.lock:
            self.update()
            now = self.clock()
            frame = self.background.copy()
            if self.inventory_enabled:
                self._draw_inventory(frame)
            if self._popup is not None and self._popup[5] <= now < self._popup[6]:
                x, y, w, h, color = self._popup[:5]
                cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), color, -1)
                cv2.rectangle(frame, (x, y), (x + w - 1, y + 16), (40, 40, 40), -1)   # title bar
            if self.panel_open:
                self._draw_panel(frame, now)
            if self.dialog is not None:
                cx, cy = self.W // 2, self.H // 2 + 30
                cv2.rectangle(frame, (cx - 110, cy - 40), (cx + 110, cy + 40), (70, 70, 70), -1)
                if self.dialog == "drop":
                    self._button(frame, self.drop_button, "DROP")
                else:
                    self._button(frame, self.confirm_button, "YES")
            if self.carrying is not None and self.dialog is None:
                cx, cy = self.cursor
                x1, y1 = cx - SLOT // 2, cy - SLOT // 2
                if 0 <= x1 and 0 <= y1 and x1 + SLOT <= self.W and y1 + SLOT <= self.H:
                    frame[y1:y1 + SLOT, x1:x1 + SLOT] = self._items[self.carrying[2]]
            return frame

    def grab(self, left, top, width, height):
        """A sub-rectangle of the picture, like the bot's screen capture."""
        frame = self.render()
        out = np.zeros((height, width, 3), np.uint8)
        if self.blank_chance and self._grab_rng.random() < self.blank_chance:
            return out                     # a capture that came back black
        x1, y1 = max(0, left), max(0, top)
        x2, y2 = min(self.W, left + width), min(self.H, top + height)
        if x2 > x1 and y2 > y1:
            out[y1 - top:y2 - top, x1 - left:x2 - left] = frame[y1:y2, x1:x2]
        return out

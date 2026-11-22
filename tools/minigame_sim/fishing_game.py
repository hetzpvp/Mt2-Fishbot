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
- the stock white ring turns red while the fish is inside it, except for
  ``click_cooldown`` after a hit, when it stays white (``ring_feedback``;
  "official" skin only: that is the look it was measured on)
- space while the line is out reels it in again: no bite, and the rod needs
  its usual moment before the next cast
- right click on a living fish opens it; left click on an item picks it up,
  a click in the world then asks to drop it (DROP, then YES). The picked-up
  item stays drawn in its slot (only a copy follows the cursor) until it is
  dropped or put somewhere else; a click on its own slot puts it back

Hard-mode options (all off by default) make the window behave like the
awkward clients and connections the bot has to cope with:

- ``wander``        the panel opens somewhere else for every game
- ``skins``         every game uses the next look from this list
- ``catch_delay``   the caught item lands this long after the panel closed (lag)
- ``blank_chance``  share of captures that come back black (capture hiccups)
- ``popup_chance``  chance per cast that some other game window (not the
                    minigame) pops up while the line is out
- ``move_panel()``  the player drags the panel during a game
- ``blocked_ground_clicks``  this many clicks into the world with an item on
                    the cursor hit some UI instead: no drop dialog, the item
                    stays on the cursor
- ``lost_drop_buttons``  this many clicks on DROP are lost (no confirm dialog)
- ``direct_yes_no``  the ground click asks yes / no at once, with no DROP step
                    (dyes on a real client, diag 20261121_200639). A set of item
                    names does this for those items only
- ``catch_names``   catches cycle through these item names
- ``dialog_steps``  ``"drop"`` or ``"confirm"`` per ground click, in order;
                    the last entry repeats. Overrides ``direct_yes_no``
- ``shift_confirm_world``  after the first YES, the world behind that button
                    stays 17 gray levels off (the rod and character moved)
- ``drop_completes``  the DROP click drops the item; no YES dialog follows
- ``pickup_hides_slot``  the slot of a picked-up item shows empty (a client
                    that does not keep drawing it there)
- ``held_icon_anchor``  where the copy on the cursor is drawn: "center" on the
                    cursor, "left" (the cursor tip at its left edge) or
                    "nowhere" (a client whose copy cannot be seen there)
- ``lost_confirm_clicks``  this many clicks on YES are lost: the dialog stays
                    up with the item in it (the game missed the click)
- ``answer_delay``  a right-click on an item is answered this long after it
                    (the server): the item opens / goes on the rod only then

Real-client options (measured on the two-client session in diagnostics
20261120_195925: 330 casts, 324 games, a 10 fps recording of each window;
``bench.realistic_game`` turns them all on with the measured values):

- ``bite_steps``    the bite comes one of these many seconds after the cast
                    (the server works in whole seconds: 1, 2 or 3 s, each
                    about a third of the casts), plus up to 60 ms
- ``fish_motion``   "diagonal": the fish swims only at 45 degrees, bounces
                    off the panel's edges, now and then turns by 90 degrees or
                    back, and rarely stops for a moment; speed about 80 px/s
                    (the default "free" picks random targets at any angle)
- ``frame_rate``    the game moves the fish only on its own frames (60 fps):
                    captures between two frames see the same picture
- ``catch_chance``  share of won games that give an item at all (0.35 on the
                    real client; the item lands 0.1-0.2 s after the panel
                    closes: ``catch_delay``)
- ``living_world``  the world behind the windows moves (players walking with
                    their name tags), also next to the inventory grid where
                    the bot's strip reaches into it
- ``prefill``       this many slots of page 1 hold items when the game starts;
                    some carry a stack count, some are animated (a sparkle
                    that comes and goes, an icon that blinks), like the
                    player's real inventory
- ``rod_animation`` after a game the rod is busy this long; a quick skip
                    (Ctrl+G to mount, Ctrl+G to get off) cuts it short.
                    Mounted, a cast does nothing. A (dis)mount takes
                    ``mount_time`` seconds; a Ctrl+G meanwhile is lost, and
                    with ``slow_mount_chance`` one takes ``slow_mount_time``
                    (seen once: 250 ms, and the second Ctrl+G was dropped)

Holding Ctrl draws every stack count in the inventory bigger (the real client
does that), so a quick skip changes the strip for a moment.

Bait fish (Shrimp, Vai): a right-click puts one on the rod instead of opening
it. The next cast then needs no bait key; a bait key pressed meanwhile puts
the usual bait on instead (``stats["fish_bait_replaced"]``). A left click in
the world with nothing on the cursor makes the character walk
(``stats["walks"]``): the bot should never send one.

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
# A real Metin2 client's own textures (tools/mt2_assets.py; published with
# the server owner's permission)
REAL_MT2_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "real_mt2")

# Items that are drawable but never a random catch. They were not 32x32 in the
# first item templates; keeping them out keeps the catches each seed gives.
NOT_CAUGHT = ("Black_Dye_item.jpg", "Brown_Dye_item.jpg", "Crab_living.jpg", "Goldring_item.jpg",
              "Lucys_ring_item.jpg", "Red_Dye_item.jpg", "Refugee_cape_item.jpg", "Sage_King_Glove_item.jpg",
              "Symbol_wise_emperors_item.jpg", "Vai_living.jpg", "White_Dye_item.jpg", "Yellow_Dye_item.jpg")

HSV = Tuple[int, int, int]


class RealMt2Look:
    """The fishing window of a real Metin2 client, drawn from its textures.

    Layout from the client's uiscript (FishingGameWindow): a 292x263 board
    with a title bar; the water (278x226) at (7, 29); a box outline at (15, 15)
    in the water, 248x196, which is the area the bot finds as the panel (the
    simulator's ``panel``); the ring (142x142, radius 63) at (68, 42) in the
    water; the catch counter at (212, 34) and the timer at (35, 223) in the
    board. Measured on the recording of diagnostics 20261120_195925:

    - the ring is drawn tinted while the fish is inside it (BGR x 0.76, 0.72,
      0.94), over the fish
    - the fish is the atlas sprite, unscaled, turned to where it swims
      (head first; 45-degree lines on the real client)
    - after a hit the counter shows the new count and its fish icon is drawn
      at 60 % for the click cooldown (the ring stays white as long)
    - "Hit" appears 15 px left and 20 px above the click, rises 60 px/s and
      is gone after about 0.45 s ("Miss" is assumed to do the same: the
      recording has no miss)
    - the timer gauge is cut from the right as the time runs out

    - a ripple 28 px ahead of the fish (lossless shot: fish at (103, 90),
      heading up-left, ripple at (83, 70))

    Not measured, so assumed: the fish's tail animation (frames 1-2-1-3,
    0.15 s each) and how often the ripple comes (every second, 4 frames of
    0.1 s each).
    """

    BOARD_SIZE = (292, 263)
    WATER = (7, 29)                       # in the board
    NAV = (15, 15, 248, 196)              # in the water: the panel
    RING = (68, 42)                       # ring sprite, in the water
    COUNTER = (212, 34)                   # in the board
    TIMER = (35, 223)                     # in the board
    NAV_BGR = (0x2D, 0x30, 0x35)
    RED_TINT = (0.76, 0.72, 0.94)
    COUNTER_DIM = 0.6
    HIT_OFFSET = (-15, -20)
    HIT_RISE = 60.0
    HIT_TIME = 0.45
    RIPPLE_AHEAD = 28.0                   # ripple center, px ahead of the fish (lossless shot)
    # where the panel's top-left is from the board's: board = panel - this
    PANEL_IN_BOARD = (WATER[0] + NAV[0], WATER[1] + NAV[1])

    _cache = None

    @classmethod
    def load(cls):
        if cls._cache is not None:
            return cls._cache
        atlas = cv2.imread(os.path.join(REAL_MT2_ASSETS, "fishing_atlas.png"), cv2.IMREAD_UNCHANGED)
        board = cv2.imread(os.path.join(REAL_MT2_ASSETS, "board.png"), cv2.IMREAD_UNCHANGED)
        if atlas is None or board is None:
            raise FileNotFoundError("real Metin2 textures missing: run tools/mt2_assets.py (see its docstring)")

        def sub(x1, y1, x2, y2):
            return atlas[y1:y2, x1:x2].astype(np.float32)

        cls._cache = dict(
            board=board,
            water=atlas[0:226, 0:278, :3].copy(),
            ring=sub(138, 312, 280, 454),
            counter=sub(0, 312, 70, 340),
            timer_base=sub(0, 256, 223, 284),
            timer_gauge=sub(0, 284, 223, 312),
            fish=[sub(78, 340, 108, 370), sub(108, 340, 138, 370), sub(78, 340, 108, 370), sub(78, 370, 108, 400)],
            ripple=[sub(30, 422, 60, 452), sub(0, 422, 30, 452), sub(30, 392, 60, 422), sub(0, 392, 30, 422)],
            hit=sub(62, 400, 102, 422),
            miss=sub(102, 400, 138, 415),
            counter_text=[cv2.imread(os.path.join(REAL_MT2_ASSETS, f"counter_{n}.png")) for n in range(3)],
            rotated={},
        )
        return cls._cache


def blend(frame, sprite, x, y, tint=None, scale=None):
    """Draws a BGRA float sprite with its alpha at (x, y), clipped to the frame."""
    h, w = sprite.shape[:2]
    x1, y1, x2, y2 = max(0, x), max(0, y), min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if x2 <= x1 or y2 <= y1:
        return
    s = sprite[y1 - y:y2 - y, x1 - x:x2 - x]
    rgb = s[:, :, :3]
    if tint is not None:
        rgb = rgb * np.asarray(tint, np.float32)
    if scale is not None:
        rgb = rgb * scale
    a = s[:, :, 3:] / 255.0
    area = frame[y1:y2, x1:x2].astype(np.float32)
    frame[y1:y2, x1:x2] = np.clip(rgb * a + area * (1 - a) + 0.5, 0, 255).astype(np.uint8)


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
    # The real client's drawing (recording 20261120_195925): water with
    # moving light lines, a small dark fish pointing where it swims with a
    # ripple ahead of it, and the ring with its glow drawn OVER the fish, so a
    # detector loses the fish while it crosses the ring
    real_look: bool = False
    # Drawn from a real client's own textures and layout (RealMt2Look);
    # the colors above are not used
    real_mt2: bool = False


SKINS = {s.name: s for s in (
    # The stock colors the bot's default profile is written for
    Skin("official", panel=(102, 200, 220), fish=(104, 138, 120)),
    # The stock look at the real client's size (247x195 at 800x600, ring r~65)
    Skin("official-real", panel=(102, 200, 220), fish=(104, 138, 120), panel_size=(247, 195),
         circle=(123, 97, 63), fish_size=(7, 3), real_look=True),
    # The real client, pixel for pixel: its own textures (RealMt2Look)
    Skin("real-mt2", panel=(102, 200, 220), fish=(110, 120, 90), panel_size=RealMt2Look.NAV[2:],
         circle=(123, 97, 63), real_mt2=True),
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

# Inventory strip. The bot reads the grid below the page tabs (without tabs
# configured: the right-hand 200 px from y=200 down).
INV_COLS, INV_ROWS, SLOT = 5, 9, 32
INV_PAGES = 4


class FishingGame:
    HITS_NEEDED = 3

    def __init__(self, skin="official", size=(800, 600), seed=1, bite_delay=(1.5, 4.0),
                 duration=15.0, miss_penalty=1.0, fish_speed=(70.0, 230.0), hit_radius=16,
                 click_cooldown=0.5, recast_cooldown=1.5, catch_chance=1.0, dead_fish_chance=0.0, require_bait=True,
                 inventory=True, feedback_text=True, clock=time.perf_counter,
                 space_cancels=True, wander=False, skins=None, catch_delay=(0.0, 0.0),
                 blank_chance=0.0, popup_chance=0.0, blocked_ground_clicks=0, lost_drop_buttons=0,
                 pickup_hides_slot=False, held_icon_anchor="center", ring_feedback=True,
                 answer_delay=0.0, lost_confirm_clicks=0, golden_tuna_games=(), tuna_confirm=False,
                 tuna_delay=0.1, tuna_ok=(True, True, True), bite_steps=None, fish_motion="free",
                 frame_rate=0, living_world=False, prefill=0, rod_animation=0.0, mount_time=(0.15, 0.2),
                 slow_mount_chance=0.0, slow_mount_time=0.3, mount_key="g", direct_yes_no=False,
                 dialog_steps=None, shift_confirm_world=False, drop_completes=False,
                 catch_names=None):
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
        self.blocked_ground_clicks = blocked_ground_clicks
        self.lost_drop_buttons = lost_drop_buttons
        self.direct_yes_no = direct_yes_no
        self.dialog_steps = list(dialog_steps) if dialog_steps else None
        self._dialog_step = 0
        self.catch_names = list(catch_names) if catch_names else None
        self._catch_i = 0
        self.shift_confirm_world = shift_confirm_world
        self._confirm_shifted = False
        self.drop_completes = drop_completes
        self.pickup_hides_slot = pickup_hides_slot
        self.ring_feedback = ring_feedback
        self._ring_white_until = 0.0       # after a hit the ring stays white this long
        self.held_icon_anchor = held_icon_anchor
        self._grab_rng = random.Random(seed + 1000)   # capture noise: not part of the game's dice
        self.popup_chance = popup_chance
        self._popup = None                 # (x, y, w, h, bgr, from, until)
        self.answer_delay = answer_delay
        self.lost_confirm_clicks = lost_confirm_clicks
        self._answers = []                 # (time, page, slot, name) right-clicks not answered yet
        self.fish_bait = None              # the bait fish on the rod
        # Golden Tuna: the won games (1, 2, ...) after which its dialog opens.
        # Pages as on the real client (2026-11-20): three answers, then a
        # text with OK. ``tuna_confirm`` adds a Yes/No page in between.
        # ``tuna_delay``: seconds from the game end to the dialog, one number
        # or a list used in turn (one entry per tuna). ``tuna_ok[i]``: whether
        # answer i is followed by the OK page (True) or closes the dialog at
        # once (False); only "Let it go" -> OK is seen on the real client.
        # A dialog due while the line is out pulls it in; one due during a
        # minigame waits for that game to end.
        self.golden_tuna_games = set(golden_tuna_games)
        self.tuna_confirm = tuna_confirm
        self.tuna_delays = list(tuna_delay) if isinstance(tuna_delay, (list, tuple)) else [tuna_delay]
        self.tuna_ok = tuple(tuna_ok)
        self._tuna_due = None              # time the next dialog opens
        self.tuna = None                   # None | (page, shown_from) ; page: "choice" | "confirm" | "ok" | "closing"
        self.tuna_answers = []             # the answers clicked on the choice page (0, 1, 2)
        self.bite_steps = tuple(bite_steps) if bite_steps else None
        self.fish_motion = fish_motion
        self.frame_rate = frame_rate
        self._dir = (1.0, 1.0)             # diagonal motion: unit steps in x and y
        self._next_turn = 0.0
        self.living_world = living_world
        self._walkers = []
        self.rod_animation = rod_animation
        self.mount_time = mount_time
        self.slow_mount_chance = slow_mount_chance
        self.slow_mount_time = slow_mount_time
        self.mount_key = mount_key
        self.mounted = False
        self._mount_done = None            # time the (dis)mount in progress finishes
        self.ctrl = False                  # Ctrl held
        self.stacks = {}                   # (page, slot) -> stack count drawn on the icon
        self.animated = {}                 # (page, slot) -> "sparkle" | "blink"

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
        self._flash_at = None              # (x, y, time) of the click that made it
        self._last = self.clock()
        self._t0 = self._last
        self.cursor = (0, 0)

        self.stats = dict(casts=0, casts_ignored=0, casts_without_bait=0, games=0, hits=0,
                          misses=0, clicks_ignored=0, caught=0, timeouts=0, opened=0, dropped=0,
                          drop_cancelled=0, inventory_full=0, stray_clicks=0, casts_cancelled=0,
                          popups=0, blocked_ground_clicks=0, lost_drop_buttons=0, fish_bait_used=0,
                          fish_bait_replaced=0, casts_with_fish_bait=0, walks=0,
                          lost_confirm_clicks=0, tuna_dialogs=0, tuna_stray_clicks=0,
                          tuna_keys_ignored=0, tuna_closed=0, tuna_cancelled_casts=0, mounts=0,
                          dismounts=0, mount_keys_lost=0, casts_mounted=0, quick_skips=0,
                          clicks_lost=0, empty_catches=0)

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
        self._extra_items = {}
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
                    name = os.path.basename(path)
                    if img is None or max(abs(img.shape[0] - SLOT), abs(img.shape[1] - SLOT)) > 2:
                        continue
                    if img.shape[:2] != (SLOT, SLOT):
                        img = cv2.resize(img, (SLOT, SLOT))
                    # NOT_CAUGHT: drawable when a test places one, but not
                    # among the random catches (same dice)
                    (self._extra_items if name in NOT_CAUGHT else self._items)[name] = img
        self._item_names = sorted(self._items)
        self._items.update(self._extra_items)
        if prefill and self._item_names:
            self._prefill(prefill)
        if living_world:
            wr = random.Random(seed + 2000)
            for _ in range(4):
                self._walkers.append(dict(x=wr.uniform(0, self.W), y=wr.uniform(60, self.H - 60),
                                          vx=wr.choice((-1, 1)) * wr.uniform(25, 70),
                                          vy=wr.uniform(-15, 15),
                                          color=hsv_to_bgr(wr.randrange(180), wr.randint(60, 200),
                                                           wr.randint(90, 200))))

        self.set_skin(skin)

    def _prefill(self, count):
        """Items already in the bag on page 1, spread like a player's: some
        stacked (a count on the icon), a few animated."""
        rng = random.Random(self.rng.random())
        slots = rng.sample(range(INV_COLS * INV_ROWS), min(count, INV_COLS * INV_ROWS))
        for i, slot in enumerate(sorted(slots)):
            self.pages[0][slot] = rng.choice(self._item_names)
            if rng.random() < 0.4:
                self.stacks[(0, slot)] = rng.choice((2, 5, 9, 35, 46, 47, 230, 260))
            if i % 6 == 2:
                self.animated[(0, slot)] = "sparkle"
            elif i % 11 == 7:
                self.animated[(0, slot)] = "blink"

    # ------------------------------------------------------------------- skin

    def set_skin(self, skin):
        with self.lock:
            self.skin = skin if isinstance(skin, Skin) else SKINS[skin]
            s = self.skin
            w, h = s.panel_size
            self.panel = ((self.W - w) // 2, 150, w, h)
            self._real_mt2 = RealMt2Look.load() if s.real_mt2 else None
            if s.real_mt2:
                # Where the client opens it at 800x600 (window at 254, 168)
                left, top, _, bottom = self._frame_margins()
                y = min(212, max(top, self.H - h - bottom))
                self.panel = ((self.W - w) // 2, y, w, h)
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
            if s.animated or s.real_look:
                yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
                self._wave_phase = xx * 0.09 + yy * 0.05
                self._wave_phase2 = xx * 0.05 - yy * 0.11
            if s.real_look:
                cx, cy, r = self.circle
                yy, xx = np.mgrid[0:h, 0:w]
                d = np.hypot(xx - cx, yy - cy)
                self._ring_core = np.abs(d - r) <= 1.0
                self._ring_glow = (np.abs(d - r) <= 4.5) & ~self._ring_core

    def _frame_margins(self):
        """(left, top, right, bottom): how far the window drawn around the
        panel reaches past it (the real window's board), so it stays on screen."""
        if not self.skin.real_mt2:
            return 0, 0, 0, 0
        left, top = RealMt2Look.PANEL_IN_BOARD
        bw, bh = RealMt2Look.BOARD_SIZE
        w, h = self.skin.panel_size
        return left, top, bw - left - w, bh - top - h

    def _place_panel(self):
        """Puts the panel somewhere else in the window (clear of the inventory)."""
        _, _, w, h = self.panel
        left, top, right, bottom = self._frame_margins()
        max_x = (self.W - 200 if self.inventory_enabled else self.W) - w - 10 - right
        lo_x, lo_y = 10 + left, 10 + top
        self.panel = (self.rng.randint(lo_x, max(lo_x, max_x)),
                      self.rng.randint(lo_y, max(lo_y, self.H - h - 10 - bottom)), w, h)

    def move_panel(self, dx, dy):
        """The player drags the open panel (fish and all) by (dx, dy)."""
        with self.lock:
            x, y, w, h = self.panel
            left, top, right, bottom = self._frame_margins()
            nx = min(max(left, x + dx), self.W - w - right)
            ny = min(max(top, y + dy), self.H - h - bottom)
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
            # The fish moves on the game's own frames only; everything else
            # (bites, keys, the mount) happens at the moment it is due.
            frame_now = int(now * self.frame_rate) / self.frame_rate if self.frame_rate else now
            dt = min(0.1, max(0.0, frame_now - self._last))
            self._last = max(self._last, frame_now)
            if self._mount_done is not None and now >= self._mount_done:
                self._mount_done = None
                self.mounted = not self.mounted
                self.stats["mounts" if self.mounted else "dismounts"] += 1
                self._emit("mounted" if self.mounted else "dismounted")
                if not self.mounted and self.state == "idle" and now < self.ready_at:
                    self.ready_at = now          # the quick skip: the rod animation is cut short
                    self.stats["quick_skips"] += 1

            if self.tuna is not None and self.tuna[0] == "closing" and now >= self.tuna[1] + 0.42:
                self.tuna = None
                self.stats["tuna_closed"] += 1
            if self._tuna_due is not None and now >= self._tuna_due and self.state != "playing":
                self._tuna_due = None
                if self.state == "waiting":
                    self.state = "idle"            # the dialog pulls the line in
                    self.ready_at = now + self.recast_cooldown
                    self.stats["tuna_cancelled_casts"] += 1
                self.tuna = ("choice", now)
            if self._pending_catch is not None and now >= self._pending_catch:
                self._pending_catch = None
                self._add_catch()
            while self._answers and now >= self._answers[0][0]:
                _, page, slot, name = self._answers.pop(0)
                self._answer_right_click(page, slot, name)

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
                if self.fish_motion == "diagonal":
                    self._dir = (self.rng.choice((-1.0, 1.0)), self.rng.choice((-1.0, 1.0)))
                    self._next_turn = now + self.rng.expovariate(0.6)
                self._emit("bite")
            elif self.state == "playing":
                self.time_left -= dt
                if self.time_left <= 0:
                    self.stats["timeouts"] += 1
                    self._end(now, caught=False)
                elif now >= self._pause_until and self.fish_motion == "diagonal":
                    self._swim_diagonal(now, dt)
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

    # Where the real fish swims in its 247x195 panel (min/max of 59k positions)
    _SWIM_MARGINS = (12, 29, 19, 15)        # left, top, right, bottom

    def _swim_diagonal(self, now, dt):
        """The real client's fish: 45-degree lines at ~80 px/s, a bounce off
        each edge, a 90-degree turn or a turn back now and then, a rare
        short stop (1.7 per game, 0.13-0.25 s)."""
        x, y, w, h = self.panel
        ml, mt, mr, mb = self._SWIM_MARGINS
        x1, y1, x2, y2 = x + ml, y + mt, x + w - mr, y + h - mb
        if now >= self._next_turn:
            self._next_turn = now + self.rng.expovariate(0.6)
            roll = self.rng.random()
            dx, dy = self._dir
            if roll < 0.45:
                self._dir = (-dx, -dy)                    # back the way it came
            elif roll < 0.9:
                self._dir = (-dx, dy) if self.rng.random() < 0.5 else (dx, -dy)
            else:
                self._pause_until = now + self.rng.uniform(0.13, 0.25)
            self._speed = 0.0
        if not self._speed:
            self._speed = min(210.0, max(30.0, self.rng.gauss(80.0, 25.0)))
        step = self._speed * dt / 2 ** 0.5
        fx, fy = self.fish[0] + self._dir[0] * step, self.fish[1] + self._dir[1] * step
        dx, dy = self._dir
        if not x1 <= fx <= x2:
            fx = min(max(fx, x1), x2)
            dx = -dx
        if not y1 <= fy <= y2:
            fy = min(max(fy, y1), y2)
            dy = -dy
        self._dir = (dx, dy)
        self.fish = (fx, fy)

    def _end(self, now, caught):
        self.state = "closing"
        self.state_until = now + 0.25
        self.ready_at = now + 0.25 + max(self.recast_cooldown, self.rod_animation)
        lucky = caught and self.rng.random() < self.catch_chance
        if caught and not lucky:
            self.stats["empty_catches"] += 1      # won, but the game gives nothing
        if lucky:
            delay = self.rng.uniform(*self.catch_delay) if max(self.catch_delay) > 0 else 0.0
            if delay > 0:
                self._pending_catch = now + delay
            else:
                self._add_catch()
        self._emit("caught" if caught else "timeout", f"hits {self.hits}")
        if caught and (self.stats["caught"] + self.stats["timeouts"]) in self.golden_tuna_games:
            delay = self.tuna_delays[self.stats["tuna_dialogs"] % len(self.tuna_delays)]
            self.stats["tuna_dialogs"] += 1
            self._tuna_due = now + delay

    def _add_catch(self):
        self.stats["caught"] += 1
        if not (self.inventory_enabled and self._item_names):
            return
        if self.catch_names:
            name = self.catch_names[self._catch_i % len(self.catch_names)]
            self._catch_i += 1
        else:
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

    MODIFIERS = ("ctrl", "ctrl_l", "ctrl_r", "Key.ctrl", "Key.ctrl_l")

    def key_up(self, name):
        """A key is let go (only Ctrl matters)."""
        with self.lock:
            if str(name) in self.MODIFIERS:
                self.ctrl = False

    def _mount_toggle(self, now):
        """Ctrl + the mount key: get on the horse, or off it. One toggle at a
        time: a Ctrl+G while one is still in progress is lost."""
        if self._mount_done is not None:
            self.stats["mount_keys_lost"] += 1
            return
        if self.state == "waiting":
            self.state = "idle"                # mounting pulls the line in
            self.stats["casts_cancelled"] += 1
        took = (self.slow_mount_time if self.slow_mount_chance and self.rng.random() < self.slow_mount_chance
                else self.rng.uniform(*self.mount_time))
        self._mount_done = now + took

    def key(self, name):
        """'space' casts; Ctrl is held until ``key_up``; Ctrl + the mount
        key toggles the horse; any other key counts as a bait hotkey."""
        with self.lock:
            self.update()
            name = str(name)
            if name in self.MODIFIERS:
                self.ctrl = True
                return
            if self.tuna is not None:
                self.stats["tuna_keys_ignored"] += 1
                return
            if self.ctrl:
                if name == self.mount_key:
                    self._mount_toggle(self.clock())
                return                         # other Ctrl combinations: nothing here
            if name != "space":
                if self.fish_bait is not None:
                    self.stats["fish_bait_replaced"] += 1
                    self.fish_bait = None
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
            if self.state == "idle" and (self.mounted or self._mount_done is not None):
                self.stats["casts_mounted"] += 1      # no fishing from the horse
                return
            if self.state != "idle" or now < self.ready_at or self.dialog or self.carrying:
                self.stats["casts_ignored"] += 1
                return
            if not self.bait_ready:
                self.stats["casts_without_bait"] += 1
                if self.require_bait:
                    return
            self.bait_ready = False
            if self.fish_bait is not None:
                self.stats["casts_with_fish_bait"] += 1
                self.fish_bait = None
            self.stats["casts"] += 1
            self.state = "waiting"
            if self.bite_steps:
                self.state_until = now + self.rng.choice(self.bite_steps) + self.rng.uniform(0.0, 0.06)
            else:
                self.state_until = now + self.rng.uniform(*self.bite_delay)
            if self.popup_chance and self.stats["games"] >= 1 and self.rng.random() < self.popup_chance:
                self._new_popup(now)
            self._emit("cast")

    def _ground_asks_yes_no(self) -> bool:
        """The dialog this ground click opens: yes / no, or Drop / Sell first."""
        if self.dialog_steps:
            step = self.dialog_steps[min(self._dialog_step, len(self.dialog_steps) - 1)]
            self._dialog_step += 1
            return step == "confirm"
        names = self.direct_yes_no
        if isinstance(names, (set, frozenset)):
            return self.carrying is not None and self.carrying[2] in names
        return bool(names)

    def _shift_confirm_world(self) -> None:
        """Leaves the confirm button's patch 17 levels off, and leaves it there."""
        if not self.shift_confirm_world or self._confirm_shifted:
            return
        x, y = self.confirm_button
        w, h = 40, 16
        x0, y0 = x - w // 2, y - h // 2
        area = self.background[y0:y0 + h, x0:x0 + w].astype(np.int16)
        self.background[y0:y0 + h, x0:x0 + w] = np.clip(area + 17, 0, 255).astype(np.uint8)
        self._confirm_shifted = True

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

            if self.tuna is not None:
                page, shown = self.tuna
                bars = self._tuna_bars(page) if now >= shown else []
                hit = next((i for i, (x1, y1, x2, y2) in enumerate(bars)
                            if button == "left" and x1 <= x <= x2 and y1 <= y <= y2), None)
                if hit is None:
                    self.stats["tuna_stray_clicks"] += 1
                    return
                if page == "choice":
                    self.tuna_answers.append(hit)
                    self._tuna_choice = hit
                    if self.tuna_confirm:
                        self.tuna = ("confirm", now + 0.12)
                    else:
                        self.tuna = ("ok", now + 0.12) if self.tuna_ok[hit] else ("closing", now)
                elif page == "confirm":
                    if hit != 0:
                        self.tuna = ("choice", now + 0.12)
                    else:
                        self.tuna = ("ok", now + 0.12) if self.tuna_ok[self._tuna_choice] else ("closing", now)
                else:
                    self.tuna = ("closing", now)
                return

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
                        self._ring_white_until = now + self.click_cooldown
                        self._flash = ("HIT", now + 0.4)
                        self._flash_at = (x, y, now)
                        self._emit("hit", f"{self.hits}/{self.HITS_NEEDED}")
                        if self.hits >= self.HITS_NEEDED:
                            self._end(now, caught=True)
                    else:
                        self.stats["misses"] += 1
                        self.time_left -= self.miss_penalty
                        self._flash = ("MISS", now + 0.4)
                        self._flash_at = (x, y, now)
                        self._emit("miss", "fish outside the circle" if on_fish else "not on the fish")
                else:
                    self.stats["stray_clicks"] += 1
                return

            if not self.inventory_enabled:
                return

            if self.dialog is not None:
                if button == "left" and self.dialog == "drop" and self._near(x, y, self.drop_button, 32, 12):
                    if self.lost_drop_buttons > 0:
                        self.lost_drop_buttons -= 1
                        self.stats["lost_drop_buttons"] += 1
                        return
                    if self.drop_completes:
                        page, slot, _ = self.carrying
                        self._forget_slot(page, slot)
                        if not self.pickup_hides_slot:
                            del self.pages[page][slot]
                        self.dialog = None
                        self.carrying = None
                        self.stats["dropped"] += 1
                        self._emit("dropped")
                        return
                    self.dialog = "confirm"
                elif button == "left" and self.dialog == "confirm" and self._near(x, y, self.confirm_button, 32, 12):
                    if self.lost_confirm_clicks > 0:
                        self.lost_confirm_clicks -= 1
                        self.stats["lost_confirm_clicks"] += 1
                        return
                    page, slot, _ = self.carrying
                    self._forget_slot(page, slot)
                    if not self.pickup_hides_slot:
                        del self.pages[page][slot]
                    self.dialog = None
                    self.carrying = None
                    self.stats["dropped"] += 1
                    self._shift_confirm_world()
                    self._emit("dropped")
                else:
                    # Anything else cancels: the item goes back to its slot
                    page, slot, name = self.carrying
                    self.pages[page][slot] = name           # (still there unless hidden)
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
                    if button != "left":
                        return
                    c_page, c_slot, c_name = self.carrying
                    if (c_page, c_slot) == (self.page, slot):    # its own slot: put back
                        page[slot] = c_name
                        self.carrying = None
                    elif slot not in page:                       # moved to a free slot
                        self.pages[c_page].pop(c_slot, None)
                        for extra in (self.stacks, self.animated):
                            if (c_page, c_slot) in extra:
                                extra[(self.page, slot)] = extra.pop((c_page, c_slot))
                        page[slot] = c_name
                        self.carrying = None
                    return
                name = page.get(slot)
                if name is None:
                    return
                if button == "right":
                    if self.answer_delay > 0:
                        self._answers.append((now + self.answer_delay, self.page, slot, name))
                    else:
                        self._answer_right_click(self.page, slot, name)
                elif button == "left":
                    if self.pickup_hides_slot:
                        del page[slot]
                    self.carrying = (self.page, slot, name)
                return

            if self.carrying is None and button == "left":
                self.stats["walks"] += 1
                self._emit("walk", f"({x},{y})")
                return
            if self.carrying is not None and button == "left":
                if self.blocked_ground_clicks > 0:
                    self.blocked_ground_clicks -= 1
                    self.stats["blocked_ground_clicks"] += 1
                    return
                self.dialog = "confirm" if self._ground_asks_yes_no() else "drop"

    BAIT_FISH = ("Shrimp_living.jpg", "Vai_living.jpg")

    def _forget_slot(self, page, slot):
        self.stacks.pop((page, slot), None)
        self.animated.pop((page, slot), None)

    def _answer_right_click(self, page_index, slot, name):
        """The game's answer to a right-click on ``name``: a bait fish goes
        on the rod (when nothing is cast), a living fish opens (or dies)."""
        page = self.pages[page_index]
        if page.get(slot) != name:
            return
        if name in self.BAIT_FISH:
            if self.state != "idle":
                return
            gone = True
            self.fish_bait = name
            self.bait_ready = True
            self.stats["fish_bait_used"] += 1
            self._emit("bait fish on the rod", name)
        elif "_living" in name and self.rng.random() >= self.dead_fish_chance:
            gone = True
            self.stats["opened"] += 1
            self._emit("opened", name)
        else:
            gone = False
        if gone and self.stacks.get((page_index, slot), 1) > 1:
            # A stack loses one; the count is not drawn for the last one
            left = self.stacks[(page_index, slot)] - 1
            if left > 1:
                self.stacks[(page_index, slot)] = left
            else:
                self.stacks.pop((page_index, slot))
        elif gone:
            del page[slot]
            self._forget_slot(page_index, slot)
            if self.carrying is not None and self.carrying[:2] == (page_index, slot):
                # Picked up meanwhile: the held copy goes with it
                self.carrying = None
                self.dialog = None

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
        now = self._last
        for slot in range(INV_COLS * INV_ROWS):
            x = ox + (slot % INV_COLS) * SLOT
            y = oy + (slot // INV_COLS) * SLOT
            name = page.get(slot)
            key = (self.page, slot)
            anim = self.animated.get(key) if name else None
            if anim == "blink" and int(now * 2.5 + slot) % 3 == 0:
                name = None                    # an icon that is drawn only part of the time
            frame[y:y + SLOT, x:x + SLOT] = self._items[name] if name else self._slot_img
            if anim == "sparkle":
                glow = 0.5 + 0.5 * np.sin(now * 7.0 + slot)
                cell = frame[y + 2:y + SLOT - 2, x + 2:x + SLOT - 2].astype(np.float32)
                frame[y + 2:y + SLOT - 2, x + 2:x + SLOT - 2] = np.clip(
                    cell + glow * np.array((120, 90, 60), np.float32), 0, 255).astype(np.uint8)
            count = self.stacks.get(key) if name else None
            if count:
                # Ctrl held: the real client draws every count bigger and bolder
                scale, thick = (0.5, 2) if self.ctrl else (0.32, 1)
                text = str(count)
                tw = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)[0][0]
                cv2.putText(frame, text, (x + SLOT - 2 - tw, y + SLOT - 3), cv2.FONT_HERSHEY_SIMPLEX,
                            scale, (235, 235, 235), thick, cv2.LINE_AA)

    def _draw_world(self, frame, now):
        """Other players walking around, each with a name tag over the head."""
        for wk in self._walkers:
            x = (wk["x"] + wk["vx"] * (now - self._t0)) % (self.W + 80) - 40
            y = wk["y"] + 25 * np.sin((now - self._t0) * 0.7 + wk["x"])
            x, y = int(x), int(y)
            cv2.ellipse(frame, (x, y), (11, 24), 0, 0, 360, wk["color"], -1)
            cv2.circle(frame, (x, y - 30), 8, (120, 150, 190), -1)
            cv2.putText(frame, "Lv 30 Player", (x - 38, y - 44), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                        (120, 230, 140), 1, cv2.LINE_AA)

    # The Golden Tuna dialog, measured on the real client at 800x600
    _TUNA_BARS = {"choice": [(120, 239, 679, 264), (120, 271, 679, 296), (120, 303, 679, 328)],
                  "confirm": [(260, 251, 539, 276), (260, 283, 539, 308)],
                  "ok": [(260, 251, 539, 276)]}

    def _tuna_bars(self, page):
        sx, sy = self.W / 800.0, self.H / 600.0
        return [(int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy))
                for x1, y1, x2, y2 in self._TUNA_BARS.get(page, [])]

    def _draw_tuna(self, frame, now):
        """The HUD is hidden, black bars top and bottom, the board with the
        page's text and answer bars. Between pages the board is not drawn
        for a moment; after the last answer the bars slide away."""
        page, shown = self.tuna
        bar = int(self.H * 0.12)
        if page == "closing":
            bar = max(0, int(bar * (1 - (now - shown) / 0.42)))
        if bar:
            frame[:bar] = 0
            frame[self.H - bar:] = 0
        if page == "closing" or now < shown:
            return
        bx1, by1 = (self.W - 700) // 2, (self.H - 300) // 2
        bx2, by2 = bx1 + 699, by1 + 299
        inner = frame[by1:by2 + 1, bx1:bx2 + 1].astype(np.int16)
        frame[by1:by2 + 1, bx1:bx2 + 1] = (inner * 0.45).astype(np.uint8)       # dark, see-through
        cv2.rectangle(frame, (bx1, by1), (bx2 - 1, by2 - 1), (85, 85, 85), 1)
        cv2.rectangle(frame, (bx1 + 1, by1 + 1), (bx2, by2), (0, 0, 0), 1)
        for i in range(4 if page == "choice" else 5):
            cv2.putText(frame, "Tuna text line", (bx1 + 20, by1 + 30 + 16 * i), cv2.FONT_HERSHEY_SIMPLEX,
                        0.4, (230, 230, 230), 1, cv2.LINE_AA)
        for x1, y1, x2, y2 in self._tuna_bars(page):
            area = frame[y1:y2 + 1, x1:x2 + 1].astype(np.int16)
            frame[y1:y2 + 1, x1:x2 + 1] = np.clip(area + 30, 0, 255).astype(np.uint8)
            cv2.putText(frame, "answer", ((x1 + x2) // 2 - 25, (y1 + y2) // 2 + 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.4, (240, 240, 240), 1, cv2.LINE_AA)

    def _button(self, frame, center, text):
        cx, cy = center
        cv2.rectangle(frame, (cx - 32, cy - 12), (cx + 32, cy + 12), (190, 190, 190), -1)
        size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
        cv2.putText(frame, text, (cx - size[0] // 2, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (20, 20, 20), 1, cv2.LINE_AA)

    def _fish_heading(self):
        """Unit vector of where the fish swims."""
        if self.fish_motion == "diagonal":
            dx, dy = self._dir
        else:
            dx, dy = self._target[0] - self.fish[0], self._target[1] - self.fish[1]
        n = float(np.hypot(dx, dy)) or 1.0
        return dx / n, dy / n

    def _draw_real_mt2(self, frame, now):
        """The real fishing window from its textures (see RealMt2Look)."""
        look, L = self._real_mt2, RealMt2Look
        x, y, w, h = self.panel
        bx, by = x - L.PANEL_IN_BOARD[0], y - L.PANEL_IN_BOARD[1]
        wx, wy = bx + L.WATER[0], by + L.WATER[1]
        board = look["board"]
        bh, bw = board.shape[:2]
        x1, y1, x2, y2 = max(0, bx), max(0, by), min(self.W, bx + bw), min(self.H, by + bh)
        if x2 <= x1 or y2 <= y1:
            return
        part = board[y1 - by:y2 - by, x1 - bx:x2 - bx]
        opaque = part[:, :, 3] > 0
        frame[y1:y2, x1:x2][opaque] = part[:, :, :3][opaque]
        water = look["water"]
        sx1, sy1 = max(0, wx), max(0, wy)
        sx2, sy2 = min(self.W, wx + water.shape[1]), min(self.H, wy + water.shape[0])
        frame[sy1:sy2, sx1:sx2] = water[sy1 - wy:sy2 - wy, sx1 - wx:sx2 - wx]
        nx, ny, nw, nh = L.NAV
        # the client's box spans width + 1 pixels (both edges inclusive)
        cv2.rectangle(frame, (wx + nx, wy + ny), (wx + nx + nw, wy + ny + nh), L.NAV_BGR, 1)

        t = now - self._t0
        fx, fy = self.fish
        hx, hy = self._fish_heading()
        # ripple: every second, 28 px ahead of the fish, growing over 4 frames
        phase = t % 1.0
        if phase < 0.4:
            rip = look["ripple"][int(phase / 0.1)]
            blend(frame, rip, int(round(fx + hx * L.RIPPLE_AHEAD)) - 15, int(round(fy + hy * L.RIPPLE_AHEAD)) - 15)
        # fish: the sprite turned to its heading (head first; the sprite looks up)
        angle = int(round(-np.degrees(np.arctan2(hx, -hy)))) % 360
        index = int(t / 0.15) % 4
        key = (index, angle)
        sprite = look["rotated"].get(key)
        if sprite is None:
            m = cv2.getRotationMatrix2D((14.5, 14.5), angle, 1.0)
            sprite = cv2.warpAffine(look["fish"][index], m, (30, 30), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
            look["rotated"][key] = sprite
        blend(frame, sprite, int(round(fx)) - 15, int(round(fy)) - 15)
        # ring over the fish, tinted while the fish is inside it
        red = (self.ring_feedback and now >= self._ring_white_until and self.fish_in_circle())
        blend(frame, look["ring"], wx + L.RING[0], wy + L.RING[1], tint=L.RED_TINT if red else None)
        # counter: the fish icon is dimmed right after a hit; then the text
        cx, cy = bx + L.COUNTER[0], by + L.COUNTER[1]
        counter = look["counter"]
        if now < self._ring_white_until and self.hits:
            blend(frame, counter[:, :28], cx, cy, scale=L.COUNTER_DIM)
            blend(frame, counter[:, 28:], cx + 28, cy)
        else:
            blend(frame, counter, cx, cy)
        text = look["counter_text"][min(self.hits, 2)]
        if text is not None and 0 <= cx + 30 and cx + 66 <= self.W and 0 <= cy and cy + 28 <= self.H:
            frame[cy:cy + 28, cx + 30:cx + 66] = text
        # timer: the gauge is cut from the right as the time runs out
        tx, ty = bx + L.TIMER[0], by + L.TIMER[1]
        blend(frame, look["timer_base"], tx, ty)
        left = max(0.0, min(1.0, self.time_left / self.duration))
        cut = 28 + int(round(190 * left))
        if cut > 28:
            blend(frame, look["timer_gauge"][:, :cut], tx, ty)
        # "Hit" / "Miss" at the click, rising
        if self.feedback_text and self._flash and self._flash_at and now - self._flash_at[2] < L.HIT_TIME:
            px, py, t0 = self._flash_at
            sprite = look["hit"] if self._flash[0] == "HIT" else look["miss"]
            blend(frame, sprite, px + L.HIT_OFFSET[0], int(py + L.HIT_OFFSET[1] - L.HIT_RISE * (now - t0)))

    def _draw_panel(self, frame, now):
        s = self.skin
        if s.real_mt2:
            self._draw_real_mt2(frame, now)
            return
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
        elif s.real_look:
            # Caustics: thin light lines drifting over the water (about 8% of it)
            hue, sat, val = s.panel
            t = now - self._t0
            lines = np.sin(self._wave_phase * 1.7 + t * 1.3) * np.sin(self._wave_phase2 * 1.9 - t * 0.9)
            light = np.clip((lines - 0.55) * 2.2, 0, 1)
            hsv = np.empty((h, w, 3), np.uint8)
            hsv[:, :, 0] = hue
            hsv[:, :, 1] = np.clip(sat - light * 70, 0, 255)
            hsv[:, :, 2] = np.clip(val + light * 25, 0, 255)
            frame[y:y + h, x:x + w] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        elif self._texture is not None:
            frame[y:y + h, x:x + w] = self._texture
        else:
            cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), self._panel_bgr, -1)

        cx, cy, r = self.circle_abs()
        if s.real_look:
            pass                                  # the ring comes after the fish
        elif self._disc_bgr is not None:
            cv2.circle(frame, (cx, cy), r, self._disc_bgr, -1)
        else:
            ring_bgr = self._circle_bgr
            if (self.ring_feedback and s.name in ("official", "official-real") and now >= self._ring_white_until
                    and self.fish_in_circle()):
                ring_bgr = (140, 120, 245)       # the official client's red ring
            cv2.circle(frame, (cx, cy), r, ring_bgr, 2)
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
        if s.real_look:
            dx, dy = self._dir if self.fish_motion == "diagonal" else (1.0, 0.0)
            angle = float(np.degrees(np.arctan2(dy, dx)))
            cv2.ellipse(frame, (fx, fy), s.fish_size, angle, 0, 360, self._fish_bgr, -1)
            # the ripple a little ahead of the fish
            rx, ry = int(fx + dx * 9), int(fy + dy * 9)
            for rad in (3, 6):
                cv2.circle(frame, (rx, ry), rad, (250, 240, 225), 1, cv2.LINE_AA)
            red = (self.ring_feedback and now >= self._ring_white_until and self.fish_in_circle())
            core = np.array((220, 180, 255) if red else (255, 255, 235), np.float32)
            panel = frame[y:y + h, x:x + w]
            glow = panel[self._ring_glow].astype(np.float32)
            panel[self._ring_glow] = (glow * 0.45 + core * 0.55).astype(np.uint8)
            panel[self._ring_core] = core.astype(np.uint8)
        else:
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
            if self._walkers:
                self._draw_world(frame, now)
            if self.tuna is not None:
                self._draw_tuna(frame, now)
                return frame
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
            if self.carrying is not None and self.dialog is None and self.held_icon_anchor != "nowhere":
                cx, cy = self.cursor
                x1, y1 = (cx if self.held_icon_anchor == "left" else cx - SLOT // 2), cy - SLOT // 2
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

"""
MT2 Fishing Bot - Multi-Window Support
Automated fishing minigame bot for Metin2
Author: boristei

Main entry point - imports all modules and starts the GUI
"""

import ctypes

import diag

# Fix for high DPI displays (125%, 150%, etc.) where UI elements may be cut off
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    pass  # Older Windows versions may not support this

import os
import sys
import threading
import time
from collections import deque
from typing import Optional, Tuple, Dict

import cv2
import numpy as np

try:
    import pyautogui
    # Disable PyAutoGUI fail-safe for multi-window automation
    # When multiple bots run simultaneously, mouse movements can trigger the fail-safe
    # This is safe because we have explicit click logic and input_lock synchronization
    pyautogui.FAILSAFE = False
except Exception:  # not used (or installed) on Linux
    pyautogui = None

try:
    from pynput import keyboard
    from pynput.keyboard import Controller, Key
except ImportError:
    from utils import DEBUG_PRINTS
    if DEBUG_PRINTS:
        print("ERROR: pynput not available (not installed, or no X display)")
    keyboard = None
    Controller = None
    from types import SimpleNamespace
    # Key names as strings: the Linux uinput keyboard accepts them directly.
    Key = SimpleNamespace(ctrl="ctrl", space="space", f1="f1", f2="f2", f3="f3", f4="f4", f5="f5")

from utils import get_resource_path, input_lock, DEBUG_PRINTS


def game_key(name: str):
    """The key to send for a configured key name ('1', 'F2', 'space', ...).

    On Windows a top-row digit goes out as its virtual key: pynput sends a
    character that needs Shift on the active layout (AZERTY, Czech, ...) as
    a Unicode packet instead of the key, and the game then selects nothing."""
    name = str(name)
    named = {'space': Key.space, 'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
             'f5': Key.f5}.get(name.lower()) if len(name) > 1 else None
    if named is not None:
        return named
    if sys.platform == 'win32' and len(name) == 1 and name.isdigit() and keyboard is not None:
        return keyboard.KeyCode.from_vk(0x30 + int(name))
    return name
from window_manager import WindowManager, GameRegion
from fish_detector import FishDetector, DetectionProfile, WindowLearner
from humanize import Humanizer
from input_backend import create_keyboard_controller, create_mouse_backend
import screen_capture
from screen_capture import WindowCapture, frame_usable
import inventory_layout
import dialog_watch


class RecastWaitTuner:
    """Finds the shortest wait between the end of one catch and the next cast.

    How long the character needs before it can cast again differs per server
    and nothing on screen says when it is ready, but the *result* of a cast
    does: after a wait that was too short the cast is ignored and no bite
    follows. So the wait starts at the configured (safe) value and is
    shortened step by step while casts keep succeeding. The first shortened
    wait that is followed by a missed bite is taken as too short: the tuner
    returns to the last wait that worked and stays there.

    A miss at a wait that has already worked is not blamed on the wait (empty
    bait, lag) unless it happens twice in a row, in which case one step is
    given back."""

    STEP = 0.8        # each probe shortens the wait to this share
    CONFIRM = 2       # consecutive successes before a wait counts as proven
    MIN_WAIT = 0.5    # never probe below this (seconds)

    def __init__(self, configured: float):
        self.ceiling = max(0.0, float(configured))
        self.wait = self.ceiling            # wait to use for the next cast
        self.good = self.ceiling            # shortest wait proven to work
        self.settled = self.ceiling <= self.MIN_WAIT
        self._streak = 0
        self._misses = 0

    def report(self, waited: float, success: bool) -> Optional[str]:
        """Feeds back the result of a cast made after waiting ``waited``.
        Returns a message when the wait changed, else None."""
        if abs(waited - self.wait) > 1e-6:
            return None  # result of an older setting
        if success:
            self._misses = 0
            self._streak += 1
            if self._streak < self.CONFIRM:
                return None
            self._streak = 0
            self.good = min(self.good, self.wait)
            if self.settled:
                return None
            shorter = max(self.MIN_WAIT, self.wait * self.STEP)
            if shorter > self.wait - 0.05:
                self.settled = True
                return f"Wait before next cast settled at {self.wait:.1f}s"
            self.wait = shorter
            return None

        self._streak = 0
        if self.wait < self.good:
            # The probe failed: this wait is too short.
            self.wait = self.good
            self.settled = True
            return f"Wait before next cast settled at {self.wait:.1f}s"
        self._misses += 1
        if self._misses >= 2 and self.wait < self.ceiling:
            self._misses = 0
            self.wait = self.good = min(self.ceiling, self.wait / self.STEP)
            return f"Casts keep failing — wait before next cast raised to {self.wait:.1f}s"
        return None


def _diag_callback(attr: str, kind: str):
    """Property for a GUI callback: while diagnostics is on, every call is
    also written to the log (with the source line of the bot code that made
    it). Off: the plain callback, exactly as before."""
    def get(self):
        cb = self.__dict__.get(attr)
        if cb is None or not diag.ENABLED:
            return cb

        def logged(*args):
            try:
                if kind == "status":
                    diag.log("STAT", str(args[0]) if args else "", tag=self.diag_tag, depth=1)
                elif kind == "stats":
                    diag.log("STAT", f"stats hits={args[1]} games={args[2]} bait={args[3]}",
                             tag=self.diag_tag, depth=1)
                elif kind == "bait":
                    diag.log("STAT", f"bait counter -> {args[1]}", tag=self.diag_tag, depth=1)
                else:
                    diag.log("STAT", f"{attr.strip('_')}{diag.summarize(args)}", tag=self.diag_tag, depth=1)
            except Exception:
                diag.exc("status callback log", tag=self.diag_tag)
            return cb(*args)
        return logged

    def set_(self, value):
        self.__dict__[attr] = value
    return property(get, set_)


class FishingBot:
    """Main bot that plays the fishing minigame - one instance per game window"""
    STOP_BAIT_DEPLETED = "bait_depleted"
    STOP_MANUAL = "manual"
    STOP_WINDOW_UNAVAILABLE = "window_unavailable"
    STOP_INVENTORY_FULL = "inventory_full"
    STOP_ERROR = "error"
    STOP_CAPTURE_UNAVAILABLE = "capture_unavailable"   # the window could not be seen for too long
    STOP_CAPTURE_OVERLAP = "capture_overlap"           # covered windows would be read from the screen
    STOP_INVENTORY_NOT_VISIBLE = "inventory_not_visible"  # the inventory is not where it was configured
    STOP_FALSE_DETECTION = "false_detection"   # something that is not the minigame was taken for it, again
    STOP_FISH_NOT_DETECTED = "fish_not_detected"   # the panel opens but its fish is never seen
    STOP_NO_BITE = "no_bite"                   # no minigame / bite at all this session
    STOP_KEYBOARD_ERROR = "keyboard_error"     # the cast keys could not be sent
    STOP_GAME_SCREEN_LOST = "game_screen_lost"  # disconnect, death, login screen: the inventory is gone
    STOP_REPEATED_ERROR = "repeated_error"     # the same loop kept failing
    STOP_DROP_FAILING = "drop_failing"         # drops failed several times in a row: Drop / Confirm points?
    STOP_CATCH_NOT_RECOGNIZED = "catch_not_recognized"   # catches land but none was ever recognized
    
    # Class-level template cache (shared by all bot instances - loaded only once)
    _template_cache = None
    _template_border_crop = 7  # Pixels to crop from each edge of templates
    # Empty-slot template score from which a slot counts as empty: the same
    # for the page scan and for "is the item at this slot gone". Measured on
    # two real clients (tests/fixtures/real_client*): empty slots score
    # 0.81-0.92 on a 1:1 client but 0.47-0.76 once the picture is rescaled
    # (a UI drawn at another size, a capture that was scaled), while occupied
    # slots and everything between the slots stay below 0.40. 0.70 missed
    # about half the empty slots on the rescaled client.
    _EMPTY_SLOT_THRESHOLD = 0.55
    _classic_fish_template = None  # Cache for classic fish detection template
    _classic_fish_scaled_cache = None  # Pre-computed scaled variants (shared across calls)
    _classic_fish_last_scale = None    # Last successful scale — tried first on next poll
    
    # Color templates for fish that look identical in grayscale
    _confusable_fish = {
        'Goldfish_living.jpg',
        'Large_zander_living.jpg',
        'Red_Dye_item.jpg',
        'White_Dye_item.jpg',
        'Yellow_Dye_item.jpg',
        'Brown_Dye_item.jpg',
        'Black_Dye_item.jpg',
        'Bleach_item.jpg',
    }
    _color_template_cache = None  # Cache for colored versions of confusable fish
    _empty_slot_template = None   # Cache for empty_slot.jpg template
    # Guards the lazy class-level caches: all bots start at the same moment,
    # and a half-filled cache must never be visible to another bot's thread.
    _template_lock = threading.Lock()

    # Detection profile learned by self-calibration in this session. Other
    # windows of the same client adopt it instead of learning it again.
    _shared_learned_profile = None
    _profile_lock = threading.Lock()
    # Recast wait another window has already settled on. The windows play on
    # the same server, so the rest adopt it instead of each paying for its
    # own too-short probe.
    _shared_recast_wait = None
    # Every look learned in this session (newest first). A client that
    # alternates between looks is recognized instead of learned again.
    _known_profiles: list = []

    # Minigame loop tuning
    # Min seconds between minigame detection passes (config: timing_detect_interval).
    # The game draws at ~60 fps, so 8 ms still looks at every frame twice. In the
    # simulator at 60 fps, 4 / 8 / 16.7 ms all caught 60 of 60 fish in the same
    # time, and 8 ms used 36% less CPU per game than 4 ms.
    _DETECT_INTERVAL = 0.008
    _END_CONFIRM_FRAMES = 3       # consecutive "panel gone" frames before the game counts as over
    _END_CONFIRM_GAP = 0.015      # seconds between those confirmation frames
    _RECALIBRATE_AFTER = 2.0      # seconds without the panel before re-scanning the full window
    _MIN_PANEL_FILL = 0.5         # share of a found panel's box that must have the panel color
    _ADAPTIVE_AFTER = 1.2         # seconds without the panel before trying self-calibration
    _FISH_MISSING_AFTER = 2.0     # seconds of open panel without any fish before learning it by motion
    _FISH_LOST_AFTER = 4.0        # ... when the profile did find fish in earlier games (the look changed)
    _MOVED_RESCAN_GAP = 0.15      # full-window scan interval on clients whose panel opens in different places
    _MAX_KNOWN_PROFILES = 6       # learned looks remembered per session
    _FOCUS_LOG_GAP = 5.0          # seconds between "could not focus" messages
    _MAX_GAME_SECONDS = 90.0      # a "minigame" open longer than this is a false detection
    _MAX_STUCK_GAMES = 2          # such false detections in a row before the bot stops
    _PANEL_MIN_SIZE = (120, 100)  # first calibration: a panel candidate smaller than this is scenery
    _PANEL_MAX_SHARE = 0.70       # ... and so is one wider or taller than this share of the window
    _MAX_BITE_FAILURES = 5        # casts without a minigame before the bait key is assumed empty
    _NO_FISH_GAMES = 3            # games in a row with the panel open and no fish seen before acting
    _FOCUS_GIVE_UP = 60.0         # seconds the window cannot be brought to the front before stopping
    _CAST_ERROR_LIMIT = 3         # casts in a row whose keys could not be sent before stopping
    _LOOP_ERROR_LIMIT = 10        # main-loop exceptions within _LOOP_ERROR_WINDOW before stopping
    _LOOP_ERROR_WINDOW = 60.0
    _TRACK_WINDOW = 0.15          # seconds of fish positions kept for the speed estimate
    _TRACK_MIN_SPAN = 0.03        # shortest time base for a usable speed estimate
    _CLICK_OVERHEAD = 0.004       # seconds from "decided" to the button going down, besides the cursor settle
    _CAPTURE_WAIT_AFTER = 5.0     # seconds without a usable frame before casting is put on hold
    _CAPTURE_STOP_AFTER = 30.0    # ... before the bot stops (capture_unavailable)

    # GUI callbacks; logged to the diagnostic log while it is on.
    on_status_update = _diag_callback("_on_status_update", "status")
    on_stats_update = _diag_callback("_on_stats_update", "stats")
    on_bait_update = _diag_callback("_on_bait_update", "bait")
    on_bot_stop = _diag_callback("_on_bot_stop", "stop")
    
    def __init__(self, region: GameRegion, config: dict, window_manager: WindowManager, 
                 bait_counter: int = 800, bait_keys: list = None, bot_id: int = 0):
        # Core components
        self.region = region
        self.config = config
        self.window_manager = window_manager
        # Window tag for the diagnostic log ("W1"); also given to the window
        # manager and capture so their lines carry it.
        self.diag_tag = f"W{bot_id + 1}"
        try:
            window_manager.diag_tag = self.diag_tag
        except Exception:
            pass
        self.stop_message = None          # human-readable reason shown by the GUI after a stop
        self._diag_last_bait_key = None
        self._diag_inv_rect = None        # window-relative rect of the last inventory capture
        self._diag_cap_fail_time = 0.0
        self._diag_paused = False
        self.detector = FishDetector(DetectionProfile.from_dict(config.get('detection_profile')))
        # Screen/window capture. Created here, first used on the bot thread.
        self.capture = WindowCapture(
            window_manager, config.get('capture_backend', 'auto'), log=self._log)
        
        # State tracking
        self.running = False
        self.paused = False
        self.hits = 0
        self.total_games = 0
        self.bait_counter = bait_counter
        self.bait_keys = bait_keys if bait_keys else ['1', '2', '3', '4']
        self.region_auto_calibrated = False
        self.consecutive_failures = 0
        self.bot_id = bot_id
        self._manually_stopped = False  # Flag to track if user manually stopped the bot
        self.stop_reason = None
        self._stop_notified = False
        
        # Cached circle values for performance
        self._circle_center = None
        self._circle_radius_sq = self.detector.profile.circle_radius ** 2

        # Self-calibration state (re-skinned clients)
        self._adaptive = bool(config.get('adaptive_detection', True))
        self._window_learner = WindowLearner()
        self._reference_frame = None      # full window just before the cast
        self._pending_bounds = None       # panel bounds awaiting a confirming frame
        self._longest_bite = 0.0          # longest cast->minigame delay seen (s)
        self._games_without_fish = 0      # learned profile sanity check
        self._fish_ever_seen = False      # the active profile has detected a fish this session
        self._minigame_ever_seen = False  # a minigame was played this session
        self._fish_track = deque(maxlen=64)  # (time, x, y) of recent fish positions
        self._recast_tuner = None         # RecastWaitTuner, created in _load_timings
        self._last_cast_wait = None       # recast wait that preceded the current cast
        self._game_end_time = None        # when the last game ended (recast wait is counted from here)
        self._wait_interrupted = False    # the wait for the bite was cut short by a pause
        self._cast_error: Optional[str] = None   # why the last bait_and_cast sent nothing
        self._cast_errors = 0             # casts in a row that failed with an error
        self._full_rescan_wanted = False  # next resume reads every page again, not just the current one
        self._loop_errors: list = []      # times of recent exceptions in the main loop
        self._was_paused = False          # a pause happened: re-read the inventory before going on
        self._game_fish_seen = False      # a fish was detected in the current game
        self._panel_moves = 0             # times the panel was found somewhere else than before
        self._stuck_games = 0             # "games" in a row whose panel never closed
        self._no_fish_games = 0           # games in a row in which no fish was seen
        self._no_fish_warned = False
        self._bite_ever_seen = False      # a minigame (or classic bite) was seen this session
        self._focus_fail_since = None     # first of the casts in a row that could not be sent
        self._mount_toggled = False       # classic: the bot pressed CTRL+G after the last miss
        self._last_focus_log = 0.0
        # Capture health: a frame that shows nothing (black, flat, failed) is
        # not evidence of anything. These track how long that has lasted.
        self._last_frame_usable = True    # the most recent window/region capture showed a picture
        self._capture_bad_since = None    # perf_counter() of the first unusable frame in a row
        self._capture_wait_logged = False # "waiting for the capture" was said for this outage

        # Callbacks for GUI updates
        self.on_status_update = None
        self.on_stats_update = None
        self.on_bait_update = None  # Callback for bait counter changes
        self.on_bot_stop = None  # Callback when bot stops
        self.on_profile_learned = None  # Callback(profile_dict) when self-calibration succeeds
        
        # Setup keyboard controller (shared, but access controlled by lock).
        # pynput on Windows, the uinput virtual keyboard on Linux.
        self.keyboard_controller = create_keyboard_controller(config, bot_id=self.bot_id)

        # Humanizer — wraps every mouse/keyboard call. When disabled, all wrappers
        # forward directly to pyautogui/pynput (current behavior preserved).
        self.mouse_backend = create_mouse_backend(config, bot_id=self.bot_id)
        self.human = Humanizer(
            enabled=bool(config.get('human_like_clicking', False)),
            keyboard_controller=self.keyboard_controller,
            mouse_backend=self.mouse_backend,
        )
        self.human.diag_tag = self.diag_tag
        self.human.window_rect = getattr(window_manager, "get_window_rect", None)
        
        # Inventory strip. Its rectangle comes from the configured page tabs
        # (see _inventory_rect); these two describe the strip used when no
        # tab is configured: the right-hand 200 px from y=200 down.
        self._inventory_width = inventory_layout.FALLBACK_WIDTH
        self._inventory_y_offset = inventory_layout.FALLBACK_TOP
        self._inv_rect = None             # window-relative rect of the strip (x, y, w, h)
        self._inv_rect_source = None      # "tabs" or "fixed"

        # Waiting for a clicked page tab to become the active one
        self._tab_check_seen = False      # the active tab was recognized at least once
        self._tab_check_misses = 0        # waits in a row that timed out
        self._tab_check_off = False       # this client's tabs cannot be read: fixed wait
        self._fill_page_pending = False   # armor quick skip left another page on screen
        self._drop_failures = 0           # drops in a row that failed at a dialog step
        self._catches_recognized = 0      # catches identified this run
        self._unrecognized_in_a_row = 0   # catches that filled a slot but were not identified
        self._armor_refusal = None        # last reason the armor quick skip was not sent
        
        # Dead fish tracking: ignored slot positions (10 pixel radius around center)
        self._ignored_positions = set()  # Positions confirmed as dead fish

        # Empty slot tracking for inventory management
        self._empty_slot_positions = []  # Ordered (inv_x, inv_y) of empty slots on current page
        self._current_inv_page = 0       # Current inventory page index (0-based)
        self._full_doubts = 0            # "page full" readings in a row that were not believed

        # Timing cache — read from config once per session in play_game(), never inside loops
        self._load_timings()

    @classmethod
    def reset_session_state(cls) -> None:
        """Forgets what the windows of the last run shared: learned looks,
        the recast wait they agreed on, the classic icon's scale. Called
        when a new run starts — the windows may be other clients now."""
        with cls._profile_lock:
            cls._shared_learned_profile = None
            cls._known_profiles = []
        cls._shared_recast_wait = None
        cls._classic_fish_last_scale = None
        diag.log("STATE", "shared session state reset (learned looks, recast wait, classic scale)")

    def _load_timings(self):
        """Caches all timing values from config (called at init and at session start)."""
        config = self.config

        def get(key, default):
            # A hand-edited or damaged config must not crash the bot thread.
            try:
                value = float(config.get(key, default))
            except (TypeError, ValueError):
                diag.log("CONFIG", f"{key}={config.get(key)!r} is not a number: using default {default}",
                         tag=self.diag_tag)
                return default
            if not 0.0 <= value < 3600.0:
                diag.log("CONFIG", f"{key}={value} out of range [0, 3600): using default {default}",
                         tag=self.diag_tag)
                return default
            if diag.ENABLED and key not in config:
                diag.log("CONFIG", f"{key} not in config: default {default}", tag=self.diag_tag)
            return value

        self._t_cursor   = get('timing_cursor_settle',  0.012)
        self._t_hold     = get('timing_button_hold',    0.008)
        self._t_post     = get('timing_post_click',     0.035)
        self._t_human_mn = get('timing_human_min',      0.15)
        self._t_human_mx = max(self._t_human_mn, get('timing_human_max', 0.40))
        self._t_key_hold = get('timing_key_hold',       0.025)
        self._t_key_set  = get('timing_key_settle',     0.030)
        self._t_interkey = get('timing_cast_interkey',  0.050)
        self._t_detect   = min(0.05, get('timing_detect_interval', self._DETECT_INTERVAL))
        # Game-response waits (tunable via Timing Settings window)
        self._t_catch_wait  = get('timing_catch_wait',        0.400)
        self._t_open_wait   = get('timing_open_wait',         0.100)
        self._t_dead_check  = get('timing_dead_fish_check',   0.100)
        self._t_drop_settle = get('timing_drop_settle',       0.120)
        self._t_drop_dialog = get('timing_drop_dialog_timeout', 1.0)   # longest wait for a drop/confirm dialog
        self._t_qs_between  = get('timing_quickskip_between', 0.100)
        self._t_qs_after    = get('timing_quickskip_after',   0.100)
        # Server-dependent waits
        self._t_bite_timeout = max(1.0, get('timing_minigame_timeout', 6.0))
        # Slow servers bite later than 30 s: a recast before the bite reels
        # the line in, so the cap has room above that.
        self._t_bite_cap     = max(5.0, get('timing_minigame_timeout_cap', 60.0))
        self._t_recast_wait  = max(0.0, get('timing_recast_wait', 4.0))
        if self.config.get('adaptive_recast_wait', True):
            if self._recast_tuner is None or self._recast_tuner.ceiling != self._t_recast_wait:
                self._recast_tuner = RecastWaitTuner(self._t_recast_wait)
        else:
            self._recast_tuner = None

    def _log(self, message: str):
        cb = self.__dict__.get("_on_status_update")
        if cb:
            if diag.ENABLED:
                diag.log("STAT", f"[W{self.bot_id+1}] {message}", tag=self.diag_tag, depth=1)
            cb(f"[W{self.bot_id+1}] {message}")

    def _set_stop_reason(self, reason: str):
        """Records the first reason this bot stopped."""
        if self.stop_reason is None:
            self.stop_reason = reason

    def _notify_bot_stop_once(self):
        """Runs the GUI stop callback at most once per bot lifetime."""
        if self._stop_notified:
            return
        self._stop_notified = True
        if self.on_bot_stop:
            self.on_bot_stop(self.bot_id)

    def _stop_bot(self, reason: str, status: str = None):
        """Stops the worker and records why, without playing UI alerts."""
        self._set_stop_reason(reason)
        if status and self.stop_message is None:
            self.stop_message = status.split("] ", 1)[1] if status.startswith("[W") and "] " in status else status
        diag.log("DECISION", f"STOP reason={reason} (recorded: {self.stop_reason}) message={status!r}",
                 tag=self.diag_tag)
        self.running = False
        if status and self.on_status_update:
            self.on_status_update(status)
        self._notify_bot_stop_once()

    def _focus(self, force: bool = False) -> bool:
        """Brings this bot's window to the front. Must be called with
        input_lock held, before any click or key press.

        False means the OS did not put the window in front: whatever is sent
        now would go to another window (with several clients, into another
        client's game), so the caller must send nothing."""
        ok = self.window_manager.activate_window(force_activate=True) if force \
            else self.window_manager.activate_window()
        if ok is False:
            ok = self.window_manager.activate_window(force_activate=True)
        if ok is False:
            diag.log("DECISION", "window not in front: input skipped", tag=self.diag_tag)
            now = time.perf_counter()
            if now - self._last_focus_log >= self._FOCUS_LOG_GAP:
                self._last_focus_log = now
                self._log("Could not bring the game window to the front — input skipped")
            return False
        return True

    def _stop_if_window_unavailable(self) -> bool:
        """Stops cleanly if the selected game window has closed/crashed."""
        if self.window_manager and self.window_manager.is_window_available():
            return False
        self._stop_bot(
            self.STOP_WINDOW_UNAVAILABLE,
            f"[W{self.bot_id+1}] Selected window is no longer available. Stopping bot."
        )
        return True
        
    def _load_template_cache(self) -> Dict[str, tuple]:
        """Loads all fish/item templates from assets folder into class-level cache.
        Returns dict of {filename: (grayscale_template, half_width, half_height)}
        Templates are cropped by 7 pixels on each edge to focus on center.
        Cache is shared by all bot instances - loaded only once globally.
        Pre-computes half dimensions for faster center calculation."""
        # Check class-level cache first (shared by all instances)
        cache = FishingBot._template_cache
        if cache is not None:
            return cache

        with FishingBot._template_lock:
            if FishingBot._template_cache is None:
                # Build privately, publish once complete.
                FishingBot._template_cache = self._build_template_cache()
            return FishingBot._template_cache

    def _build_template_cache(self) -> Dict[str, tuple]:
        cache: Dict[str, tuple] = {}
        assets_path = get_resource_path("assets")

        if not os.path.exists(assets_path):
            diag.log("ANALYSIS", f"item templates: assets folder {assets_path} NOT FOUND "
                                 f"(_MEIPASS={getattr(__import__('sys'), '_MEIPASS', None)})", tag=self.diag_tag)
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Assets folder not found!")
            return cache

        border = FishingBot._template_border_crop
        
        for f in os.listdir(assets_path):
            if f.endswith('_living.jpg') or f.endswith('_living.png') or \
               f.endswith('_item.jpg') or f.endswith('_item.png'):
                try:
                    img_path = os.path.join(assets_path, f)
                    template = cv2.imread(img_path)
                    if template is not None:
                        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

                        # Crop border from all edges (focus on center)
                        h, w = template_gray.shape
                        if h > border * 2 and w > border * 2:
                            template_gray = template_gray[border:h-border, border:w-border]
                        
                        # Pre-compute half dimensions for center calculation
                        h, w = template_gray.shape
                        cache[f] = (template_gray, w >> 1, h >> 1)
                except Exception as e:
                    diag.exc(tag=self.diag_tag)
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Error loading template {f}: {e}")
        
        if diag.ENABLED:
            diag.log("ANALYSIS", f"item templates from {assets_path}: {len(cache)} loaded: "
                                 + ", ".join(f"{n} {t.shape[1]}x{t.shape[0]}" for n, (t, _, _) in sorted(cache.items())),
                     tag=self.diag_tag)
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Loaded {len(cache)} item templates (grayscale, cropped {border}px)")
        return cache
    
    def _load_color_template_cache(self) -> Dict[str, tuple]:
        """Loads color versions of confusable fish templates for disambiguation.
        Returns dict of {filename: (bgr_template, half_width, half_height)}"""
        cache = FishingBot._color_template_cache
        if cache is not None:
            return cache

        with FishingBot._template_lock:
            if FishingBot._color_template_cache is None:
                FishingBot._color_template_cache = self._build_color_template_cache()
            return FishingBot._color_template_cache

    def _build_color_template_cache(self) -> Dict[str, tuple]:
        cache: Dict[str, tuple] = {}
        assets_path = get_resource_path("assets")

        if not os.path.exists(assets_path):
            return cache

        border = FishingBot._template_border_crop
        
        for filename in FishingBot._confusable_fish:
            try:
                img_path = os.path.join(assets_path, filename)
                if os.path.exists(img_path):
                    template = cv2.imread(img_path)
                    if template is not None:
                        h, w = template.shape[:2]
                        if h > border * 2 and w > border * 2:
                            template = template[border:h-border, border:w-border]
                        
                        h, w = template.shape[:2]
                        cache[filename] = (template, w >> 1, h >> 1)
            except Exception:
                diag.exc(tag=self.diag_tag)
                continue
        
        if self.on_status_update and cache:
            self.on_status_update(f"[W{self.bot_id+1}] Loaded {len(cache)} color templates for disambiguation")

        return cache

    def _load_empty_slot_template(self):
        """Loads assets/fishing/empty_slot.jpg for empty inventory slot detection."""
        if FishingBot._empty_slot_template is not None:
            return FishingBot._empty_slot_template

        assets_path = get_resource_path("assets")
        img_path = os.path.join(assets_path, "empty_slot.jpg")

        if not os.path.exists(img_path):
            diag.log("ANALYSIS", f"empty slot template missing: {img_path}", tag=self.diag_tag)
            return None

        try:
            template = cv2.imread(img_path)
            if template is None:
                return None

            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            border = FishingBot._template_border_crop
            h, w = template_gray.shape
            if h > border * 2 and w > border * 2:
                template_gray = template_gray[border:h-border, border:w-border]

            h, w = template_gray.shape
            FishingBot._empty_slot_template = (template_gray, w >> 1, h >> 1)
            diag.log("ANALYSIS", f"empty slot template {img_path}: {template.shape[1]}x{template.shape[0]} "
                                 f"cropped by {border}px -> {w}x{h}", tag=self.diag_tag)
        except Exception as e:
            diag.exc("loading empty_slot.jpg", tag=self.diag_tag)
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Error loading empty slot template: {e}")

        return FishingBot._empty_slot_template

    def _scan_empty_slots(self, inventory_frame: np.ndarray) -> list:
        """Finds all empty inventory slot positions in the frame using template matching.
        Returns an ordered list of (inv_x, inv_y) sorted top-to-bottom, left-to-right.

        Optimized: single matchTemplate + vectorized np.where + sort-based NMS,
        replacing the previous iterative minMaxLoc+mask loop that re-scanned the
        result array once per detected slot (~25 full passes)."""
        slot_template = self._load_empty_slot_template()
        if slot_template is None:
            diag.log("ANALYSIS", "empty slot scan impossible: no empty_slot.jpg template -> []", tag=self.diag_tag)
            return []

        template, half_w, half_h = slot_template
        t_h, t_w = template.shape

        inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape

        if t_h > inv_h or t_w > inv_w:
            diag.log("ANALYSIS", f"empty slot scan: strip {inv_w}x{inv_h} is smaller than the "
                                 f"{t_w}x{t_h} template -> []", tag=self.diag_tag)
            if diag.ENABLED:
                self._diag_negative("no_empty_slots_strip_too_small", inventory_frame, self._diag_inv_rect)
            return []

        THRESHOLD = FishingBot._EMPTY_SLOT_THRESHOLD
        try:
            result = cv2.matchTemplate(inventory_gray, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= THRESHOLD)
            if diag.ENABLED:
                self._diag_empty_slot_scores(result, ys.size, THRESHOLD, inventory_frame, inventory_gray, template)
            if ys.size == 0:
                return []

            # Sort all candidates by score descending, then keep only those
            # outside a 10px radius of any already-kept point. A coarse spatial
            # bucket index (>10 px) replaces the O(K^2) per-candidate scan over
            # already-kept points with amortized O(1) lookups. A point within
            # 10 px can lie in any of the 3x3 neighbouring buckets: a slot drawn
            # half a pixel off (fractional DPI scaling) scores two adjacent
            # positions equally, and they can straddle a bucket edge.
            scores = result[ys, xs]
            order = np.argsort(scores)[::-1]

            BUCKET = 16
            kept = []
            kept_buckets: dict = {}  # (bx, by) -> list of indices into kept
            xs_arr = xs
            ys_arr = ys
            for idx in order:
                cx = int(xs_arr[idx]) + half_w
                cy = int(ys_arr[idx]) + half_h
                bx, by = cx // BUCKET, cy // BUCKET
                dup = False
                for ddx in (-1, 0, 1):
                    for ddy in (-1, 0, 1):
                        cand = kept_buckets.get((bx + ddx, by + ddy))
                        if not cand:
                            continue
                        for ki in cand:
                            ex, ey = kept[ki]
                            if abs(cx - ex) < 10 and abs(cy - ey) < 10:
                                dup = True
                                break
                        if dup:
                            break
                    if dup:
                        break
                if not dup:
                    kept.append((cx, cy))
                    kept_buckets.setdefault((bx, by), []).append(len(kept) - 1)

            kept = self._slot_reading_order(kept)
            if diag.ENABLED:
                diag.log("ANALYSIS", f"empty slots: {len(kept)} after merging {ys.size} candidates "
                                     f"(10 px radius): {kept}", tag=self.diag_tag)
                diag.first("empty_slots_found", inventory_frame, tag=self.diag_tag,
                           note=f"{len(kept)} empty slots found")
            return kept

        except Exception:
            diag.exc("empty slot scan", tag=self.diag_tag)
            return []

    @staticmethod
    def _slot_reading_order(points: list) -> list:
        """Slot centers in reading order: rows top to bottom, each left to
        right. A row is the run of centers within 10 px (the merge radius) of
        the previous one in y; a fixed y // 30 bucket put two centers of the
        same row one pixel apart into different rows when they straddled a
        multiple of 30."""
        rows, row, last_y = [], [], None
        for p in sorted(points, key=lambda p: (p[1], p[0])):
            if last_y is not None and p[1] - last_y >= 10:
                rows.append(row)
                row = []
            row.append(p)
            last_y = p[1]
        if row:
            rows.append(row)
        return [p for r in rows for p in sorted(r, key=lambda p: p[0])]

    # ------------------------------------------------------------ diagnostics

    def _diag_empty_slot_scores(self, result, n_above, threshold, frame, gray, template) -> None:
        """Logs what the empty-slot match measured (also when nothing reached
        the threshold), and for an empty result the scores at other scales:
        a slot drawn at another size (DPI / UI scaling) scores low at 1.0x
        but high at its own scale."""
        try:
            _, best, _, loc = cv2.minMaxLoc(result)
            t_h, t_w = template.shape
            diag.log("ANALYSIS", f"empty_slot.jpg ({t_w}x{t_h}) on strip {gray.shape[1]}x{gray.shape[0]} "
                                 f"rect={self._diag_inv_rect}: best={best:.3f} at ({loc[0] + (t_w >> 1)},"
                                 f"{loc[1] + (t_h >> 1)}) threshold={threshold} positions>=threshold={n_above} "
                                 f"verdict={'slots found' if n_above else 'NO EMPTY SLOT'}; strip "
                                 f"{diag.array_summary(frame)}", tag=self.diag_tag, depth=1)
            if n_above:
                return
            probes = []
            for scale in (0.75, 0.85, 0.9, 1.1, 1.15, 1.25, 1.5, 1.75, 2.0):
                w, h = int(round(t_w * scale)), int(round(t_h * scale))
                if w < 8 or h < 8 or h > gray.shape[0] or w > gray.shape[1]:
                    continue
                scaled = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
                _, sbest, _, sloc = cv2.minMaxLoc(cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED))
                probes.append(f"{scale:g}x={sbest:.3f}@({sloc[0] + (w >> 1)},{sloc[1] + (h >> 1)})")
            diag.log("ANALYSIS", "empty slot template at other scales (diagnostic only): " + ", ".join(probes),
                     tag=self.diag_tag, depth=1)
            self._diag_negative("no_empty_slots", frame, self._diag_inv_rect)
        except Exception:
            diag.exc("empty slot score log", tag=self.diag_tag)

    def _diag_negative(self, label: str, frame, rel_rect=None, note: str = "") -> None:
        """Saves the frame an analysis came out negative on, and the same
        rectangle through the other capture method when there is one."""
        try:
            diag.image(label, frame, tag=self.diag_tag, note=note or f"rect={rel_rect}", depth=1)
            if rel_rect is not None and hasattr(self.capture, "grab_other_method"):
                method, other, why = self.capture.grab_other_method(*rel_rect)
                if other is not None:
                    diag.image(f"{label}_via_{method}", other, tag=self.diag_tag,
                               note=f"same rect through the other capture method ({method})", depth=1)
                else:
                    diag.log("IMAGE", f"no {method or 'other'}-method copy of {label}: {why}",
                             tag=self.diag_tag, depth=1)
        except Exception:
            diag.exc("saving negative frame", tag=self.diag_tag)

    def _diag_capture(self, what: str, rel, frame) -> None:
        """Logs a capture: window-relative and screen rectangle, window rect,
        frame summary. Failures and black/flat frames always (throttled to
        one line per 2 s inside per-frame loops), the rest as detail."""
        try:
            win = self.window_manager.get_window_rect()
            screen = (win[0] + rel[0], win[1] + rel[1], rel[2], rel[3])
            st = diag.frame_stats(frame) if frame is not None else None
            bad = "FAILED (blank frame substituted)" if frame is None else (
                "BLACK" if st and st["black"] else ("FLAT" if st and st["flat"] else ""))
            text = (f"{what} rel={tuple(rel)} screen={screen} window={win} -> "
                    f"{bad + ' ' if bad else ''}{'' if frame is None else diag.array_summary(frame)}")
            if not bad:
                diag.trace("CAPTURE", text, tag=self.diag_tag, depth=1)
                return
            diag.current_loop().count(f"capture_{bad.split()[0].lower()}")
            now = time.perf_counter()
            if diag.VERBOSE or not diag.in_loop() or now - self._diag_cap_fail_time >= 2.0:
                self._diag_cap_fail_time = now
                diag.log("CAPTURE", text, tag=self.diag_tag, depth=1)
        except Exception:
            diag.exc("capture log", tag=self.diag_tag)

    def _disambiguate_confusable_fish(self, inventory_frame_color: np.ndarray, inv_x: int, inv_y: int, matched_filename: str) -> str:
        """Disambiguates between fish that look identical in grayscale using color comparison.
        Returns the correct filename after color-based verification.
        
        Args:
            inventory_frame_color: BGR color inventory frame
            inv_x, inv_y: Center position of the detected fish in inventory
            matched_filename: The filename that was matched in grayscale
        
        Returns:
            Correct filename after color verification
        """
        color_templates = self._load_color_template_cache()
        if not color_templates:
            return matched_filename  # Fallback to original match
        
        # Get dimensions from matched template to extract region
        gray_templates = self._load_template_cache()
        if matched_filename not in gray_templates:
            return matched_filename
        
        _, half_w, half_h = gray_templates[matched_filename]
        
        # Extract region around the detected fish (use template size)
        inv_h, inv_w = inventory_frame_color.shape[:2]
        x1 = max(0, inv_x - half_w - 5)
        y1 = max(0, inv_y - half_h - 5)
        x2 = min(inv_w, inv_x + half_w + 5)
        y2 = min(inv_h, inv_y + half_h + 5)
        
        region = inventory_frame_color[y1:y2, x1:x2]
        if region.size == 0:
            return matched_filename
        
        best_match = matched_filename
        best_confidence = 0.0
        diag_scores = [] if diag.ENABLED else None
        
        # Compare against all confusable fish color templates
        for filename in FishingBot._confusable_fish:
            if filename not in color_templates:
                continue
            
            color_template, _, _ = color_templates[filename]
            t_h, t_w = color_template.shape[:2]
            r_h, r_w = region.shape[:2]
            
            # Skip if template is larger than region
            if t_h > r_h or t_w > r_w:
                continue
            
            try:
                # Color template matching (BGR)
                result = cv2.matchTemplate(region, color_template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if diag_scores is not None:
                    diag_scores.append((max_val, filename))
                
                if max_val > best_confidence:
                    best_confidence = max_val
                    best_match = filename
            except Exception:
                diag.exc(f"color match {filename}", tag=self.diag_tag)
                continue

        if diag_scores is not None:
            diag.log("ANALYSIS", f"color disambiguation at ({inv_x},{inv_y}) of gray match {matched_filename}: "
                                 + ", ".join(f"{n}={v:.3f}" for v, n in sorted(diag_scores, reverse=True))
                                 + f" -> {best_match}", tag=self.diag_tag)
        
        if best_match != matched_filename and self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Color disambiguation: {matched_filename} -> {best_match} (conf: {best_confidence:.2f})")
        
        return best_match
    
    def _grab(self, rel_left: int, rel_top: int, width: int, height: int) -> Optional[np.ndarray]:
        """Grabs a window-relative rectangle as a contiguous BGR array (None on failure)."""
        return self.capture.grab(rel_left, rel_top, width, height)

    def _verify_capture(self):
        """While per-window capture is still unproven, check it now. Must be
        called with input_lock held, right after activating the window."""
        if self.capture.needs_verification:
            # Give the desktop a moment to show the window after a focus switch
            settle = 0.1 - (time.perf_counter() - getattr(self.window_manager, 'last_switch_time', 0.0))
            if settle > 0:
                time.sleep(settle)
            self.capture.verify()

    def _refuse_overlapping_windows(self) -> bool:
        """When a covered window would be read from the screen (the window
        render is not trusted) and this game window overlaps another bot's,
        the bot would read the other client's panel and inventory. Settle
        the capture check now and refuse to start if it still fails.
        input_lock held, window in front. Returns False after stopping."""
        capture = self.capture
        if not capture.reads_screen_when_covered:
            return True
        peers = capture.overlapping_peers()
        if not peers:
            return True
        diag.log("DECISION", f"window overlaps {peers} and the window render is not trusted: "
                             f"running the remaining capture checks now", tag=self.diag_tag)
        while capture.needs_verification and not capture.window_trusted and self.running:
            time.sleep(0.1)
            capture.verify()
        if capture.window_trusted:
            return True
        names = ", ".join(peers)
        self._stop_bot(
            self.STOP_CAPTURE_OVERLAP,
            f"[W{self.bot_id+1}] This game window overlaps {names}, and this client can only be "
            f"read from the screen. Move the game windows apart so none covers another. Not started.")
        return False

    _EMPTY_FRAME_SHAPE = (100, 100, 3)

    def _inventory_rect(self) -> Tuple[int, int, int, int]:
        """Window-relative rectangle (x, y, w, h) of the inventory strip: the
        item grid below the configured page tabs, or the fixed right-hand
        strip when no tab is configured.

        Every slot position the bot keeps is relative to this rectangle's
        corner, so when it changes (window resized, tabs set again) the kept
        positions mean nothing any more and are dropped; the next look at
        the page reads them again."""
        _, _, win_width, win_height = self.window_manager.get_window_rect()
        rect, source = inventory_layout.strip_rect(
            self.config, win_width, win_height, self._inventory_width, self._inventory_y_offset)
        if rect != self._inv_rect:
            if self._inv_rect is None:
                diag.log("STATE", f"inventory strip rect={rect} (from {source}) in a {win_width}x{win_height} "
                                  f"window, tabs {inventory_layout.tab_points(self.config)}", tag=self.diag_tag)
            else:
                diag.log("STATE", f"inventory strip moved {self._inv_rect} -> {rect} (from {source}): "
                                  f"{len(self._ignored_positions)} occupied and "
                                  f"{len(self._empty_slot_positions)} empty slot positions dropped",
                         tag=self.diag_tag)
                self._ignored_positions.clear()
                self._empty_slot_positions.clear()
            self._inv_rect, self._inv_rect_source = rect, source
        return rect

    def _inventory_origin(self) -> Tuple[int, int]:
        """Window-relative corner of the strip the kept slot positions refer
        to (the one last captured)."""
        rect = self._inv_rect or self._inventory_rect()
        return rect[0], rect[1]

    def capture_inventory_area(self) -> np.ndarray:
        """Captures the inventory strip (see _inventory_rect)."""
        if not self._stop_if_window_unavailable():
            rel = self._inventory_rect()
            frame = self._grab(*rel)
            if diag.ENABLED:
                self._diag_inv_rect = rel
                self._diag_capture("inventory strip", rel, frame)
            self._note_frame(frame)
            if frame is not None:
                return frame
        else:
            self._note_frame(None)
        return np.zeros(self._EMPTY_FRAME_SHAPE, dtype=np.uint8)

    def identify_item_in_inventory(self, inventory_frame: np.ndarray, ignore_positions: set = None,
                                   roi: Optional[Tuple[int, int, int, int]] = None) -> Optional[Tuple[str, Tuple[int, int]]]:
        """Identifies an item in the inventory using template matching with high precision.
        Returns (filename, (x, y)) of best match or None if no match found.
        Coordinates are relative to inventory area.
        ignore_positions: set of (x, y) tuples to skip (dead fish locations).
        If first match is ignored, tries to find another match within same template.
        
        For confusable fish (Goldfish vs Large_zander), uses color-based disambiguation."""
        if not diag.ENABLED:
            return self._identify_item(inventory_frame, ignore_positions, roi, None)
        scores = []
        match = self._identify_item(inventory_frame, ignore_positions, roi, scores)
        try:
            ranked = sorted(scores, key=lambda x: -x[1])
            where = f"roi={roi}" if roi is not None else "whole strip"
            diag.log("ANALYSIS",
                     f"identify item ({where}, {len(scores)} templates scored, threshold 0.80, early exit 0.90, "
                     f"{len(ignore_positions or ())} ignored positions): result={match}; scores: "
                     + ", ".join(f"{n}={v:.3f}@{p}{' IGNORED-slot' if ig else ''}" for n, v, p, ig in ranked),
                     tag=self.diag_tag)
            if match is not None:
                diag.first("item_identified", inventory_frame, tag=self.diag_tag, note=str(match))
        except Exception:
            diag.exc("identify log", tag=self.diag_tag)
        return match

    def _identify_item(self, inventory_frame, ignore_positions, roi, scores):
        templates = self._load_template_cache()
        if not templates:
            return None

        if roi is not None:
            # Search only where the inventory changed: ~40 tiny matches instead
            # of ~40 full-strip matches. Coordinates are mapped back afterwards.
            x1, y1, x2, y2 = roi
            sub_ignore = {(ix - x1, iy - y1) for ix, iy in ignore_positions} if ignore_positions else None
            sub_scores = [] if scores is not None else None
            match = self._identify_item(inventory_frame[y1:y2, x1:x2], sub_ignore, None, sub_scores)
            if sub_scores:
                scores.extend((n, v, (p[0] + x1, p[1] + y1), ig) for n, v, p, ig in sub_scores)
            if match is None:
                return None
            name, (cx, cy) = match
            return name, (cx + x1, cy + y1)

        # Convert inventory to grayscale once (keep color frame for disambiguation)
        inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape
        
        # Local references for speed
        match_template = cv2.matchTemplate
        minMaxLoc = cv2.minMaxLoc
        TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
        CONFIDENCE_THRESHOLD = 0.80  # Lowered from 0.8 for better detection
        EARLY_EXIT_THRESHOLD = 0.90  # Near-perfect match, skip remaining templates
        confusable_fish = FishingBot._confusable_fish
        
        best_match = None
        best_confidence = CONFIDENCE_THRESHOLD  # Start at threshold (only accept better)
        
        # Pre-extract the ignore set into a local list of (ix, iy) once — avoids
        # paying the truthiness check + iterator setup inside the per-template loop.
        ignored_list = list(ignore_positions) if ignore_positions else None

        for filename, (template, half_w, half_h) in templates.items():
            t_h, t_w = template.shape

            # Skip if template larger than inventory
            if t_h > inv_h or t_w > inv_w:
                continue

            try:
                result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)

                # Cheap upfront peak check — avoids result.copy() (full-array
                # allocation) and the iterative masking loop when this template
                # has nothing above the current best, or nothing at all.
                _, peak_val, _, peak_loc = minMaxLoc(result)
                if scores is not None:
                    scores.append((filename, peak_val, (peak_loc[0] + half_w, peak_loc[1] + half_h), False))
                if peak_val <= best_confidence:
                    continue

                pt_x, pt_y = peak_loc
                center_x = pt_x + half_w
                center_y = pt_y + half_h

                # Fast-path: top match isn't in the ignore set → take it directly,
                # skip the result.copy() + iterative-minMaxLoc loop entirely.
                top_ignored = False
                if ignored_list is not None:
                    for ix, iy in ignored_list:
                        if abs(center_x - ix) < 10 and abs(center_y - iy) < 10:
                            top_ignored = True
                            break

                if top_ignored and scores is not None:
                    scores[-1] = (filename, peak_val, (center_x, center_y), True)
                if not top_ignored:
                    matched_filename = filename
                    if filename in confusable_fish:
                        matched_filename = self._disambiguate_confusable_fish(
                            inventory_frame, center_x, center_y, filename
                        )
                    if self._colors_match(inventory_frame, pt_x, pt_y, matched_filename, peak_val):
                        best_confidence = peak_val
                        best_match = (matched_filename, (center_x, center_y))
                        if best_confidence >= EARLY_EXIT_THRESHOLD and filename not in confusable_fish:
                            return best_match
                        continue  # Move to next template
                    # Same shape, other colors: another item. Look for this
                    # template's next-best place instead.

                # Slow path: top match is ignored — fall back to iterative
                # minMaxLoc with masking to find the next-best non-ignored hit
                # within this template's response map.
                result_copy = result.copy()
                # Mask the already-rejected peak before re-scanning
                mask_x1 = max(0, pt_x - (t_w >> 1))
                mask_y1 = max(0, pt_y - (t_h >> 1))
                mask_x2 = min(result_copy.shape[1], pt_x + (t_w >> 1) + 1)
                mask_y2 = min(result_copy.shape[0], pt_y + (t_h >> 1) + 1)
                result_copy[mask_y1:mask_y2, mask_x1:mask_x2] = -1.0

                while True:
                    _, max_val, _, max_loc = minMaxLoc(result_copy)
                    if max_val <= best_confidence:
                        break

                    pt_x, pt_y = max_loc
                    center_x = pt_x + half_w
                    center_y = pt_y + half_h

                    is_ignored = False
                    for ix, iy in ignored_list or ():
                        if abs(center_x - ix) < 10 and abs(center_y - iy) < 10:
                            is_ignored = True
                            break

                    if not is_ignored:
                        matched_filename = filename
                        if filename in confusable_fish:
                            matched_filename = self._disambiguate_confusable_fish(
                                inventory_frame, center_x, center_y, filename
                            )
                        if self._colors_match(inventory_frame, pt_x, pt_y, matched_filename, max_val):
                            best_confidence = max_val
                            best_match = (matched_filename, (center_x, center_y))
                            if best_confidence >= EARLY_EXIT_THRESHOLD and filename not in confusable_fish:
                                return best_match
                            break

                    mask_x1 = max(0, pt_x - (t_w >> 1))
                    mask_y1 = max(0, pt_y - (t_h >> 1))
                    mask_x2 = min(result_copy.shape[1], pt_x + (t_w >> 1) + 1)
                    mask_y2 = min(result_copy.shape[0], pt_y + (t_h >> 1) + 1)
                    result_copy[mask_y1:mask_y2, mask_x1:mask_x2] = -1.0

            except Exception:
                diag.exc(f"template {filename}", tag=self.diag_tag)
                continue

        return best_match
    
    # How far the average color of an item's icon may be from its
    # template's: distance of the a/b (color) means in Lab, 8-bit units.
    # Lightness is left out: a freshly caught item is drawn up to 35 units
    # darker for a moment, and brightness, gamma and blur move it too.
    # The gray match compares shapes only, and the normalized color match
    # cancels a tint as well, so an item of the same shape in other colors
    # scored like the real one: a puzzle arrow read as Large_zander at 0.81.
    # Measured on two real clients: the same icon 2.3-4.2 (also rescaled,
    # glowing or fading in); every template under brightness +-8%, gamma
    # 0.9-1.1, a 1.5x rescale, a 3x3 blur and JPEG at most 4.7; the arrows 18.
    _ICON_COLOR_MAX_DE = 10.0
    _icon_color_cache: Optional[Dict[str, tuple]] = None

    def _icon_color(self, filename: str) -> Optional[tuple]:
        """(icon mask, average Lab color of the icon) of a template, cropped
        like the gray templates. None when the file cannot be read."""
        cache = FishingBot._icon_color_cache
        if cache is None:
            cache = FishingBot._icon_color_cache = {}
        if filename not in cache:
            entry = None
            try:
                img = cv2.imread(os.path.join(get_resource_path("assets"), filename))
                if img is not None:
                    border = FishingBot._template_border_crop
                    h, w = img.shape[:2]
                    if h > border * 2 and w > border * 2:
                        img = img[border:h - border, border:w - border]
                    # The icon: pixels clearly brighter than the dark slot
                    # behind it, without its outline (blur mixes the
                    # background into it).
                    mask = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > 45).astype(np.uint8)
                    inner = cv2.erode(mask, np.ones((3, 3), np.uint8))
                    mask = (inner if inner.sum() >= 12 else mask).astype(bool)
                    if mask.sum() >= 12:
                        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
                        entry = (mask, lab[mask].mean(axis=0))
            except Exception:
                diag.exc(f"icon color of {filename}", tag=self.diag_tag)
            cache[filename] = entry
        return cache[filename]

    def _colors_match(self, frame: np.ndarray, x: int, y: int, filename: str, score: float) -> bool:
        """Is the icon whose template-sized area starts at (x, y) in ``frame``
        drawn in the colors of ``filename``? Costs one Lab conversion of an
        18x18 patch, and only for a candidate that passed the gray match."""
        limit = float(self.config.get("item_color_max_de", self._ICON_COLOR_MAX_DE) or 0)
        if limit <= 0:
            return True                      # check switched off in the config
        sig = self._icon_color(filename)
        if sig is None:
            return True                      # nothing to compare with: the gray match decides
        mask, want = sig
        patch = frame[y:y + mask.shape[0], x:x + mask.shape[1]]
        if patch.shape[:2] != mask.shape:
            mask = mask[:patch.shape[0], :patch.shape[1]]
        if patch.size == 0 or mask.sum() < 12:
            return True
        got = cv2.cvtColor(np.ascontiguousarray(patch), cv2.COLOR_BGR2LAB)[mask].mean(axis=0)
        de = float(np.linalg.norm(got[1:] - want[1:]))
        if de <= limit:
            return True
        diag.log("ANALYSIS", f"{filename} gray {score:.3f} at ({x},{y}) rejected: icon colors differ "
                             f"(dE {de:.1f} > {limit:g}) -> another item of the same shape",
                 tag=self.diag_tag)
        return False

    def _is_item_at_position(self, inventory_frame: np.ndarray, x: int, y: int, radius: int = 10) -> bool:
        """Checks if a slot at (x, y) is still occupied by an item.
        Optimized: instead of running matchTemplate against every fish/item template
        (O(N) heavy convolutions), we check whether the empty_slot template fits at
        this position. If it matches strongly, the slot is empty -> item is gone.
        Single matchTemplate call on a tiny crop instead of N full-frame searches."""
        slot_template = self._load_empty_slot_template()
        if slot_template is None:
            # Fallback to old path if empty_slot template missing
            return self._is_item_at_position_fallback(inventory_frame, x, y, radius)

        template, half_w, half_h = slot_template
        t_h, t_w = template.shape

        # Crop a small search window around the target position. The match window
        # only needs to be slightly bigger than the template + radius.
        pad = radius + 4
        x1 = max(0, x - half_w - pad)
        y1 = max(0, y - half_h - pad)
        x2 = min(inventory_frame.shape[1], x + half_w + pad)
        y2 = min(inventory_frame.shape[0], y + half_h + pad)

        crop = inventory_frame[y1:y2, x1:x2]
        if crop.shape[0] < t_h or crop.shape[1] < t_w:
            diag.log("ANALYSIS", f"slot ({x},{y}): crop {crop.shape[1]}x{crop.shape[0]} smaller than template "
                                 f"-> fallback check with every item template", tag=self.diag_tag)
            return self._is_item_at_position_fallback(inventory_frame, x, y, radius)

        try:
            crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            result = cv2.matchTemplate(crop_gray, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if diag.ENABLED:
                diag.log("ANALYSIS", f"slot ({x},{y}): empty_slot score={max_val:.3f} threshold={FishingBot._EMPTY_SLOT_THRESHOLD:.2f} -> "
                                     f"{'EMPTY (item gone)' if max_val >= FishingBot._EMPTY_SLOT_THRESHOLD else 'OCCUPIED (item still there)'}",
                         tag=self.diag_tag)
            # High empty-slot confidence => slot is empty => no item present
            return max_val < FishingBot._EMPTY_SLOT_THRESHOLD
        except Exception:
            diag.exc("slot check", tag=self.diag_tag)
            return self._is_item_at_position_fallback(inventory_frame, x, y, radius)

    _STACK_CHANGED = 8.0   # mean gray difference of a slot whose stack count changed

    @staticmethod
    def _slot_patch(frame: Optional[np.ndarray], x: int, y: int, half: int = 15) -> Optional[np.ndarray]:
        """Grayscale picture of the slot centered at (x, y) of the strip."""
        if frame is None or frame.ndim != 3:
            return None
        y1, y2 = max(0, y - half), min(frame.shape[0], y + half)
        x1, x2 = max(0, x - half), min(frame.shape[1], x + half)
        if y2 - y1 < 8 or x2 - x1 < 8:
            return None
        return cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)

    def _slot_changed(self, before: Optional[np.ndarray], x: int, y: int) -> bool:
        """Does the slot look different from ``before`` (stack count down)?"""
        after = self._slot_patch(self.capture_inventory_area(), x, y)
        if before is None or after is None or before.shape != after.shape:
            return False
        change = float(cv2.absdiff(before, after).mean())
        diag.log("ANALYSIS", f"slot ({x},{y}) change after the open: {change:.1f} "
                             f"(stack threshold {self._STACK_CHANGED})", tag=self.diag_tag)
        return change > self._STACK_CHANGED

    def _is_item_at_position_fallback(self, inventory_frame: np.ndarray, x: int, y: int, radius: int = 10) -> bool:
        """Fallback heavy check: matches every item template (used only if empty_slot
        template is unavailable or the crop is too small)."""
        templates = self._load_template_cache()
        if not templates:
            return False

        inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape

        match_template = cv2.matchTemplate
        where = np.where
        TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED

        for template, half_w, half_h in templates.values():
            t_h, t_w = template.shape
            if t_h > inv_h or t_w > inv_w:
                continue
            try:
                result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)
                locations = where(result >= 0.8)
                if locations[0].size == 0:
                    continue
                for pt_y, pt_x in zip(locations[0], locations[1]):
                    if abs((pt_x + half_w) - x) < radius and abs((pt_y + half_h) - y) < radius:
                        return True
            except Exception:
                diag.exc(tag=self.diag_tag)
                continue
        return False
    
    # ---------------------------------------------------------- catch handling

    _CATCH_EXTRA_WAIT = 0.6   # extra seconds to wait for a late item (high ping)

    @staticmethod
    def _changed_box(base_gray: np.ndarray, frame: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        """Bounding box (x1, y1, x2, y2) of what changed versus the baseline,
        padded so a whole item icon fits. None if nothing (or everything) changed."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if gray.shape != base_gray.shape:
            return None
        _, mask = cv2.threshold(cv2.absdiff(base_gray, gray), 30, 255, cv2.THRESH_BINARY)
        changed = cv2.countNonZero(mask)
        if changed < 40:
            diag.current_loop().frame(f"inventory diff: {changed} px changed (<40: no change)")
            return None
        x, y, w, h = cv2.boundingRect(mask)
        fh, fw = gray.shape
        if w * h > fw * fh * 0.6:
            diag.current_loop().note(f"inventory diff: {changed} px in box {(x, y, w, h)} covers >60% of the "
                                     f"strip: page switch / overlay, not a new item", "ANALYSIS")
            return None  # page switch / overlay — not a single new item
        diag.current_loop().note(f"inventory diff: {changed} px changed in box {(x, y, w, h)}", "ANALYSIS")
        pad = 20
        return (max(0, x - pad), max(0, y - pad), min(fw, x + w + pad), min(fh, y + h + pad))

    def _wait_inventory_change(self, base_gray: np.ndarray, until: float):
        """Polls the inventory until it differs from the baseline or ``until``
        (perf_counter time) passes. Returns (latest_frame, changed_box or None)."""
        with diag.loop("inventory change wait", tag=self.diag_tag,
                       seconds=round(until - time.perf_counter(), 3)) as lp:
            while True:
                frame = self.capture_inventory_area()
                lp.frame()
                box = self._changed_box(base_gray, frame)
                if box is not None:
                    lp.set("result", f"changed box {box}")
                    return frame, box
                if not self.running or time.perf_counter() >= until:
                    lp.set("result", "no change" if self.running else "bot stopped")
                    return frame, None
                time.sleep(0.04)

    def _find_new_item(self, allow_extra: bool):
        """Waits for the caught item to land and identifies it.

        Instead of sleeping a fixed time and looking once, the inventory is
        watched for a change: on a fast server the item is handled as soon as
        it appears, on a slow one the wait stretches past the configured time.
        At the configured time a full scan runs regardless (original behavior).
        Returns (match or None, last_frame)."""
        baseline = self.capture_inventory_area()
        base_gray = cv2.cvtColor(baseline, cv2.COLOR_BGR2GRAY)
        deadline = time.perf_counter() + self._t_catch_wait
        hard_deadline = deadline + self._CATCH_EXTRA_WAIT
        full_scan_done = False
        frame = baseline

        while self.running:
            frame, box = self._wait_inventory_change(
                base_gray, hard_deadline if full_scan_done else deadline)
            if box is not None:
                match = self.identify_item_in_inventory(
                    frame, ignore_positions=self._ignored_positions, roi=box)
                if match:
                    return match, frame
                # Something else changed (stack count, effect) — absorb it and keep waiting
                diag.log("DECISION", f"inventory changed in {box} but no item recognized there: "
                                     f"taking it as the new baseline", tag=self.diag_tag)
                base_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            now = time.perf_counter()
            if not full_scan_done:
                if now >= deadline:
                    full_scan_done = True
                    diag.log("DECISION", f"catch wait {self._t_catch_wait:.2f}s over: full-strip scan",
                             tag=self.diag_tag)
                    match = self.identify_item_in_inventory(
                        frame, ignore_positions=self._ignored_positions)
                    if match or not allow_extra:
                        if match is None and diag.ENABLED:
                            self._diag_negative("expected_item_not_found", frame, self._diag_inv_rect)
                        return match, frame
                    diag.log("DECISION", f"nothing found yet: waiting up to {self._CATCH_EXTRA_WAIT}s more "
                                         f"for a late item", tag=self.diag_tag)
            elif now >= hard_deadline:
                break
        if diag.ENABLED and self.running:
            diag.log("DECISION", f"no new item identified within {self._t_catch_wait + self._CATCH_EXTRA_WAIT:.2f}s "
                                 f"(allow_extra={allow_extra})", tag=self.diag_tag)
            self._diag_negative("expected_item_not_found", frame, self._diag_inv_rect)
        return None, frame

    _UNRECOGNIZED_LIMIT = 6   # catches in a row, none recognized this run, before the bot stops

    def _note_unrecognized_catch(self, filled: bool) -> None:
        """A catch was not identified. When it did take a slot, again and
        again, and no catch at all was identified this run, the item
        pictures do not match this client: the fish are kept, not handled,
        and the player should know rather than think they are."""
        if not filled:
            return
        self._unrecognized_in_a_row += 1
        diag.log("DECISION", f"unrecognized catch took a slot: {self._unrecognized_in_a_row} in a row, "
                             f"{self._catches_recognized} recognized this run", tag=self.diag_tag)
        if self._catches_recognized == 0 and self._unrecognized_in_a_row >= self._UNRECOGNIZED_LIMIT:
            self._stop_bot(
                self.STOP_CATCH_NOT_RECOGNIZED,
                f"[W{self.bot_id+1}] {self._unrecognized_in_a_row} catches in a row landed in the inventory but "
                f"none was recognized, so Automatic Fish Handling does nothing on this client. Turn it off to "
                f"fish without it, or turn on diagnostics and send the zip so the item pictures can be added.")

    def _drop_failed(self, hint: str) -> None:
        """A drop failed at a dialog step. Once is a window in the way;
        several in a row is a Drop / Sell or Confirm point that is wrong."""
        self._drop_failures += 1
        diag.log("DECISION", f"drop failed ({hint}): {self._drop_failures} in a row", tag=self.diag_tag)
        if self._drop_failures >= self._DROP_FAILURE_LIMIT and self.running:
            self._stop_bot(self.STOP_DROP_FAILING,
                           f"[W{self.bot_id+1}] {self._drop_failures} drops in a row failed: {hint}")

    _DROP_FAILURE_LIMIT = 3

    def _mark_occupied(self, inv_x: int, inv_y: int) -> None:
        """The slot holds an item that stays: never look at it again."""
        self._ignored_positions.add((inv_x, inv_y))
        self._empty_slot_positions = [
            (ex, ey) for ex, ey in self._empty_slot_positions
            if not (abs(inv_x - ex) < 15 and abs(inv_y - ey) < 15)
        ]

    def _release_freed_slots(self, empty_slots: list) -> None:
        """A slot marked occupied (a fish that did not open, a drop that
        did not happen) that now reads as empty was emptied after all — a
        late open on a slow server, the player moved it. Kept as occupied,
        the next catch landing there would never be looked at."""
        if not self._ignored_positions or not empty_slots:
            return
        freed = {p for p in self._ignored_positions
                 if any(abs(p[0] - ex) < 12 and abs(p[1] - ey) < 12 for ex, ey in empty_slots)}
        if freed:
            self._ignored_positions -= freed
            diag.log("STATE", f"{len(freed)} slot(s) marked occupied read as empty again: released {sorted(freed)}",
                     tag=self.diag_tag)

    def _page_has_space(self, frame: Optional[np.ndarray] = None, expected: Optional[list] = None) -> bool:
        """Re-reads the empty slots of the visible inventory page.

        The list is read from the picture rather than kept by bookkeeping:
        an opened fish can leave something in another slot, a drop can fail,
        the player can move things. But "no free slot" must not be believed
        lightly — leaving a page that still has room would hide every later
        catch (new items go to the first free slot of the whole inventory).
        So a page only counts as full when two looks agree, and when
        ``expected`` (the slots that were free a moment ago, minus the one
        just filled) says several should be left, a reading of none is taken
        for a covered inventory (tooltip, another window) up to two catches
        in a row before it is believed."""
        if frame is None:
            frame = self.capture_inventory_area()
        slots = self._scan_empty_slots(frame)
        if not slots and self.running:
            diag.log("DECISION", "page reads as full: looking again in 0.15s", tag=self.diag_tag)
            time.sleep(0.15)   # a tooltip or an item in flight may have covered the slots
            slots = self._scan_empty_slots(self.capture_inventory_area())
        diag.log("DECISION", f"page {self._current_inv_page + 1}: {len(slots)} empty slots read, "
                             f"{len(expected) if expected else 0} expected, doubts={self._full_doubts}",
                 tag=self.diag_tag)
        if not slots and expected and len(expected) >= 3 and self._full_doubts < 2:
            diag.log("DECISION", f"not believing 'full' ({len(expected)} were free a moment ago): "
                                 f"keeping the expected slots, doubt {self._full_doubts + 1}/2", tag=self.diag_tag)
            self._full_doubts += 1
            self._log(f"Inventory page reads as full although {len(expected)} slots were free "
                      f"a moment ago — is something covering it? Checking again after the next catch")
            self._empty_slot_positions = list(expected)
            return True
        self._full_doubts = 0
        self._empty_slot_positions = slots
        self._release_freed_slots(slots)
        return bool(slots)

    def handle_caught_item(self, may_have_catch: bool = True):
        """Identifies the caught item and keeps, opens or drops it as
        configured in fish_actions. Called after every finished game.

        ``may_have_catch`` is False when the minigame ended without a single
        hit, in which case no time is spent waiting for a late item."""
        self._restore_fill_page()
        if not self.config.get('auto_fish_handling', False):
            if not may_have_catch:
                return  # nothing was caught: the inventory is as it was
            # Even without auto handling, track inventory fullness so page-switching works
            try:
                baseline = self.capture_inventory_area()
                inventory_frame, _ = self._wait_inventory_change(
                    cv2.cvtColor(baseline, cv2.COLOR_BGR2GRAY),
                    time.perf_counter() + self._t_catch_wait)
                if not self._page_has_space(inventory_frame, self._empty_slot_positions) and self.running:
                    diag.log("DECISION", "auto handling off, page full -> switch page", tag=self.diag_tag)
                    self._switch_inventory_page()
            except Exception as e:
                diag.exc("inventory check", tag=self.diag_tag)
                self._log(f"Inventory check failed: {e}")
            return

        fish_actions = self.config.get('fish_actions', {})
        if not fish_actions:
            return

        try:
            # Screen capture needs neither focus nor the input lock, so the
            # wait for the item does not block the other windows.
            match, inventory_frame = self._find_new_item(allow_extra=may_have_catch)
            if not match or not self.running:
                diag.log("DECISION", f"no caught item to handle (match={match}, running={self.running})",
                         tag=self.diag_tag)
                # An item the bot has no template for still takes a slot:
                # without this check a page filled with unknown items is
                # never left and later catches land where nobody looks.
                if may_have_catch and self.running:
                    before = len(self._empty_slot_positions)
                    has_space = self._page_has_space(inventory_frame, self._empty_slot_positions)
                    self._note_unrecognized_catch(len(self._empty_slot_positions) < before)
                    if not has_space and self.running:
                        diag.log("DECISION", "unrecognized catch, page full -> switch page", tag=self.diag_tag)
                        self._switch_inventory_page()
                return

            self._catches_recognized += 1
            self._unrecognized_in_a_row = 0

            filename, (inv_x, inv_y) = match
            action = fish_actions.get(filename, 'keep')
            fish_name = filename.replace('_living.jpg', '').replace('_item.jpg', '')
            diag.log("DECISION", f"caught {filename} at strip ({inv_x},{inv_y}): configured action "
                                 f"{fish_actions.get(filename)!r} -> {action}", tag=self.diag_tag)

            if action == 'drop' and not self.config.get('confirm_button_pos'):
                diag.log("DECISION", "drop wanted but confirm_button_pos not set -> keep", tag=self.diag_tag)
                self._log(f"Confirm button not configured! Keeping: {fish_name}")
                action = 'keep'

            if action == 'keep':
                # Pure bookkeeping — no input, so no lock and no focus switch.
                self._mark_occupied(inv_x, inv_y)
                has_space = self._page_has_space(inventory_frame, self._empty_slot_positions)
                self._log(f"Keeping: {fish_name} "
                          f"(ignored, {len(self._empty_slot_positions)} empty slots left)")
            else:
                self._handle_item(filename, inv_x, inv_y, action)
                if not self.running:
                    return
                has_space = self._page_has_space(None, self._empty_slot_positions)

            if not has_space and self.running:
                diag.log("DECISION", "no empty slot left on this page -> switch page", tag=self.diag_tag)
                self._switch_inventory_page()

        except Exception as e:
            diag.exc("handle_caught_item", tag=self.diag_tag)
            self._log(f"Item handling failed: {e}")

    def _handle_item(self, filename: str, inv_x: int, inv_y: int, action: str,
                     label: str = "") -> bool:
        """Opens or drops the item in the slot at (inv_x, inv_y) of the
        inventory strip. An item that is still in its slot afterwards (dead
        fish, refused drop) is marked as staying.

        One lock hold covers the whole sequence, so no other window can move
        the mouse between its steps. Returns False when nothing could be sent
        (window not in front, bot stopped)."""
        fish_name = filename.replace('_living.jpg', '').replace('_item.jpg', '')
        if action == 'open':
            return self._open_item(fish_name, inv_x, inv_y, label)
        try:
            with input_lock:
                # Paused (F5): the player has the controls. Nothing is picked
                # up; the page is re-read and handled after the resume.
                if not self.running or self.paused or not self._focus(force=True):
                    if self.paused:
                        # Even when F5 is pressed again before the main loop
                        # looks: the resume re-reads the page and handles it.
                        self._was_paused = True
                        diag.log("DECISION", f"paused: {action} of {fish_name} not started", tag=self.diag_tag)
                    return False
                win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
                strip_x, strip_y = self._inventory_origin()
                screen_x = win_left + strip_x + inv_x
                screen_y = win_top + strip_y + inv_y
                win_cx = win_left + win_width // 2
                win_cy = win_top + win_height // 2

                def right_click_and_check() -> bool:
                    """Right-clicks the item; True when it is still there."""
                    self.human.move_to(screen_x, screen_y)
                    self.human.sleep(0.05)
                    self.human.click(button='right')
                    self.human.sleep(self._t_open_wait)
                    self.human.move_to(win_cx, win_cy)
                    time.sleep(self._t_open_wait)
                    return self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y)

                if action == 'drop':
                    confirm_pos = self.config.get('confirm_button_pos')
                    drop_pos = self.config.get('drop_button_pos')
                    if not confirm_pos:
                        self._mark_occupied(inv_x, inv_y)
                        return True

                    self._log(f"{label}Dropping: {fish_name}")
                    # A living fish is opened instead when the game allows it
                    still_there = right_click_and_check() if '_living' in filename else True

                    diag.log("DECISION", f"drop {fish_name}: {'still in slot -> drop sequence' if still_there else 'gone after the right-click (opened instead)'}",
                             tag=self.diag_tag)
                    if still_there:
                        sure, why = self._drop_match_sure(filename, inv_x, inv_y)
                        if not sure:
                            # A drop cannot be undone: an item that is only
                            # probably this fish stays where it is.
                            self._log(f"Not sure the item is {fish_name} ({why}) — keeping it instead of dropping")
                            self._mark_occupied(inv_x, inv_y)
                        else:
                            # Where the item is dropped: the window center
                            # unless a free spot was set (a shop, storage or
                            # trade window open there would take the item).
                            ground_pos = self.config.get('drop_ground_pos')
                            ground = ((win_left + int(ground_pos[0]), win_top + int(ground_pos[1]))
                                      if ground_pos else (win_cx, win_cy))
                            self._drop_sequence(fish_name, inv_x, inv_y, (screen_x, screen_y),
                                                ground, (win_left, win_top), drop_pos, confirm_pos)
            return True
        except Exception as e:
            diag.exc(f"handling {fish_name}", tag=self.diag_tag)
            self._log(f"Error handling {fish_name}: {e}")
            self._mark_occupied(inv_x, inv_y)
            return True

    def _open_item(self, fish_name: str, inv_x: int, inv_y: int, label: str = "") -> bool:
        """Right-clicks an item to open it. Only the click holds the input
        lock; waiting for the game and looking at the slot do not (nothing
        is on the cursor), so other windows' fish clicks are not held up.
        A fish still in its slot afterwards (dead) is marked as staying."""
        try:
            with input_lock:
                # Paused (F5): the player has the controls (see _handle_item).
                if not self.running or self.paused or not self._focus(force=True):
                    if self.paused:
                        # Even when F5 is pressed again before the main loop
                        # looks: the resume re-reads the page and handles it.
                        self._was_paused = True
                        diag.log("DECISION", f"paused: opening {fish_name} not started", tag=self.diag_tag)
                    return False
                win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
                strip_x, strip_y = self._inventory_origin()
                self._log(f"{label}Opening: {fish_name}")
                # The slot before the click: a stack that shrank by one was
                # opened although its slot still holds an item.
                before = self._slot_patch(self.capture_inventory_area(), inv_x, inv_y)
                self.human.move_to(win_left + strip_x + inv_x, win_top + strip_y + inv_y)
                self.human.sleep(0.05)
                self.human.click(button='right')
                # Off the slot, so a tooltip does not hide what is in it
                self.human.move_to(win_left + win_width // 2, win_top + win_height // 2)
            time.sleep(2 * self._t_open_wait)
            still_there = self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y)
            if still_there:
                diag.log("DECISION", f"{fish_name} still there after opening: checking again in "
                                     f"{self._t_dead_check:.2f}s", tag=self.diag_tag)
                time.sleep(self._t_dead_check)
                still_there = self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y)
            if still_there and self._slot_changed(before, inv_x, inv_y):
                # A stack: one was opened, the rest stays in the slot. Not a
                # dead fish — the next catch adds to it and is opened again.
                diag.log("DECISION", f"{fish_name} still there but the slot changed: a stack, opened one",
                         tag=self.diag_tag)
            elif still_there:
                # Dead fish — occupies this slot (released if it empties later)
                diag.log("DECISION", f"{fish_name} did not open (dead fish?): slot ({inv_x},{inv_y}) "
                                     f"marked occupied", tag=self.diag_tag)
                self._mark_occupied(inv_x, inv_y)
            else:
                diag.log("DECISION", f"{fish_name} opened: slot ({inv_x},{inv_y}) is empty now",
                         tag=self.diag_tag)
            return True
        except Exception as e:
            diag.exc(f"opening {fish_name}", tag=self.diag_tag)
            self._log(f"Error handling {fish_name}: {e}")
            self._mark_occupied(inv_x, inv_y)
            return True

    _DROP_SURE_GRAY = 0.90     # gray template score that needs no other confirmation
    _DROP_SURE_COLOR = 0.85    # ... or a color match of the chosen fish at least this good

    def _drop_match_sure(self, filename: str, inv_x: int, inv_y: int) -> Tuple[bool, str]:
        """Is the item at (inv_x, inv_y) surely ``filename``? Identification
        accepts a gray match from 0.80, which is fine for keep and open but
        not for a drop. Re-scores the slot: sure at a gray score of 0.90, or
        with a color match of that very fish of 0.85."""
        frame = self.capture_inventory_area()
        templates = self._load_template_cache()
        entry = templates.get(filename)
        if entry is None:
            return False, "no template"
        template, half_w, half_h = entry
        h, w = frame.shape[:2]
        x1, y1 = max(0, inv_x - half_w - 6), max(0, inv_y - half_h - 6)
        x2, y2 = min(w, inv_x + half_w + 6), min(h, inv_y + half_h + 6)
        region = frame[y1:y2, x1:x2]
        if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
            return False, "slot outside the capture"
        gray = float(cv2.minMaxLoc(cv2.matchTemplate(
            cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), template, cv2.TM_CCOEFF_NORMED))[1])
        if gray >= self._DROP_SURE_GRAY:
            return True, f"gray {gray:.2f}"
        color = None
        color_entry = self._load_color_template_cache().get(filename)
        if color_entry is not None and gray >= 0.80:
            ct = color_entry[0]
            if region.shape[0] >= ct.shape[0] and region.shape[1] >= ct.shape[1]:
                color = float(cv2.minMaxLoc(cv2.matchTemplate(region, ct, cv2.TM_CCOEFF_NORMED))[1])
                if color >= self._DROP_SURE_COLOR:
                    return True, f"gray {gray:.2f}, color {color:.2f}"
        detail = f"gray {gray:.2f}" + (f", color {color:.2f}" if color is not None else "")
        diag.log("DECISION", f"drop of {filename} at ({inv_x},{inv_y}) not sure enough: {detail}", tag=self.diag_tag)
        return False, detail

    def _drop_sequence(self, fish_name: str, inv_x: int, inv_y: int, slot_screen, ground_screen,
                       win_origin, drop_pos, confirm_pos) -> None:
        """Drops the item in a slot, each step confirmed before the next:
        picked up (slot empty), dialog up after the ground click, confirm
        dialog up after the drop button, dialog gone and slot still empty
        after the confirm. A step that did not happen puts the item back in
        its slot and sends nothing more. input_lock held, window in front."""
        win_left, win_top = win_origin
        timeout = self._t_drop_dialog
        keep_going = lambda: self.running  # noqa: E731

        def grab(x, y, w, h):
            self.capture.invalidate()
            return self._grab(x, y, w, h)

        drop_pt = (int(drop_pos[0]), int(drop_pos[1])) if drop_pos else None
        confirm_pt = (int(confirm_pos[0]), int(confirm_pos[1]))
        # What the button areas look like with no dialog up
        base_drop = dialog_watch.read_patch(grab, drop_pt) if drop_pt else None
        base_confirm = dialog_watch.read_patch(grab, confirm_pt)

        class FocusLost(Exception):
            pass

        def click_at(point, settle=True):
            # A pop-up (or the player) that took the focus mid-sequence
            # would get this click: check before every one.
            if not self.window_manager.is_foreground():
                raise FocusLost()
            self.human.move_to(*point)
            self.human.sleep(0.05)
            self.human.click()
            if settle:
                self.human.sleep(self._t_drop_settle)

        def slot_has_item() -> bool:
            # Off the slot first: an item still on the cursor is drawn under
            # it and would look like the item in its slot.
            self.human.move_to(*ground_screen)
            time.sleep(0.03)
            return self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y)

        def put_back(why: str) -> None:
            """The item is on the cursor and must not be carried into the next
            click: back into its slot, which is empty since it was picked up."""
            for _ in range(2):
                click_at(slot_screen)
                if slot_has_item():
                    break
            back = slot_has_item()
            self._log(f"Drop of {fish_name} stopped: {why} — "
                      + ("item put back in its slot" if back else "could not put the item back, check the cursor"))
            diag.log("DECISION", f"drop of {fish_name} aborted ({why}): put back={back}", tag=self.diag_tag)
            if diag.ENABLED:
                self._diag_negative("drop_aborted", self.capture_full_window(),
                                    (0, 0) + tuple(self.window_manager.get_window_rect()[2:]), note=why)
            self._mark_occupied(inv_x, inv_y)

        try:
            self._drop_steps(fish_name, inv_x, inv_y, slot_screen, ground_screen, win_left, win_top,
                             drop_pt, confirm_pt, base_drop, base_confirm, timeout, keep_going, grab,
                             click_at, slot_has_item, put_back)
        except FocusLost:
            diag.log("DECISION", f"drop of {fish_name}: the window lost the focus mid-sequence", tag=self.diag_tag)
            if self.running and self._focus(force=True):
                if not slot_has_item():
                    put_back("the game window lost the focus")
                    return
            else:
                self._log(f"Drop of {fish_name} stopped: the game window lost the focus — check the cursor")
            self._mark_occupied(inv_x, inv_y)

    def _drop_steps(self, fish_name, inv_x, inv_y, slot_screen, ground_screen, win_left, win_top,
                    drop_pt, confirm_pt, base_drop, base_confirm, timeout, keep_going, grab,
                    click_at, slot_has_item, put_back) -> None:
        """The steps of _drop_sequence (see there)."""
        # 1. Pick the item up
        click_at(slot_screen)
        if slot_has_item():
            self._log(f"{fish_name} could not be picked up — leaving it there")
            self._mark_occupied(inv_x, inv_y)
            return

        # 2. Onto the ground; then rest the cursor over the slot, so the item
        #    it may still carry covers neither button area while they are read.
        click_at(ground_screen, settle=False)
        self.human.move_to(*slot_screen)
        first_pt, first_base = (drop_pt, base_drop) if drop_pt else (confirm_pt, base_confirm)
        if not dialog_watch.wait(grab, first_pt, first_base, timeout, keep_going,
                                 minimum=self._t_drop_settle, what="drop dialog", tag=self.diag_tag):
            # Also when stopped meanwhile: the item is on the cursor and
            # must not be left there for the player.
            running = self.running
            put_back("no drop dialog after the ground click (a window in the way?)"
                     if running else "bot stopped")
            if running:
                self._drop_failed(
                    "nothing changed at the Drop / Sell point after a fish was dropped on the ground. Check "
                    "Drop / Sell, or clear it if your client asks only yes / no." if drop_pt else
                    "nothing changed at the Confirm point after a fish was dropped on the ground. Check "
                    "Confirm, and set Drop / Sell if your client asks drop or sell first.")
            return

        # 3. Drop button, then the confirm dialog
        if drop_pt:
            click_at((win_left + drop_pt[0], win_top + drop_pt[1]), settle=False)
            self.human.move_to(*slot_screen)
            if not dialog_watch.wait(grab, confirm_pt, base_confirm, timeout, keep_going,
                                     minimum=self._t_drop_settle, what="confirm dialog", tag=self.diag_tag):
                running = self.running
                put_back("no confirm dialog after the drop button" if running else "bot stopped")
                if running:
                    self._drop_failed("nothing changed at the Confirm point after the Drop / Sell click. "
                                      "Check Drop / Sell and Confirm.")
                return

        # 4. Confirm, then check the dialog went away and the slot stayed empty
        click_at((win_left + confirm_pt[0], win_top + confirm_pt[1]), settle=False)
        self.human.move_to(*ground_screen)
        gone = dialog_watch.wait(grab, confirm_pt, base_confirm, timeout, keep_going, want_change=False,
                                 minimum=self._t_drop_settle, what="confirm dialog", tag=self.diag_tag)
        if slot_has_item():
            self._log(f"{fish_name} is still in its slot after the drop — leaving it there")
            if diag.ENABLED:
                self._diag_negative("drop_failed_item_still_there", self.capture_inventory_area(),
                                    self._diag_inv_rect)
            self._mark_occupied(inv_x, inv_y)
            self._drop_failed("the fish was still in its slot after the Confirm click. Check Confirm.")
        elif not gone and base_confirm is not None:
            self._log(f"The drop dialog for {fish_name} is still open after the confirm — check the "
                      f"Confirm coordinate")
            diag.log("DECISION", "confirm dialog still up after the confirm click", tag=self.diag_tag)
            self._drop_failed("the dialog was still open after the Confirm click. Check Confirm.")
        else:
            self._drop_failures = 0
            # The whole drop happened: what was seen at the button points
            # really was the dialog. Later drops then need that look, not
            # just any change (moving scenery changes the patch too).
            dialog_watch.confirm(confirm_pt, "confirm dialog" if drop_pt else "drop dialog")
            if drop_pt:
                dialog_watch.confirm(drop_pt, "drop dialog")

    def _update_region_cache(self):
        """Updates cached constants when region changes."""
        if self.region:
            self._circle_center = self.detector.circle_center_for(self.region.width, self.region.height)
            radius = self.detector.circle_radius_for(self.region.width, self.region.height)
            self._circle_radius_sq = radius * radius
        else:
            self._circle_center = None
    
    def capture_full_window(self) -> np.ndarray:
        """Captures the entire game window for initial detection."""
        if not self._stop_if_window_unavailable():
            _, _, win_width, win_height = self.window_manager.get_window_rect()
            frame = self._grab(0, 0, win_width, win_height)
            if diag.ENABLED:
                self._diag_capture("full window", (0, 0, win_width, win_height), frame)
            self._note_frame(frame)
            if frame is not None:
                return frame
        else:
            self._note_frame(None)
        return np.zeros(self._EMPTY_FRAME_SHAPE, dtype=np.uint8)

    def capture_window_region(self, rel_left: int, rel_top: int, width: int, height: int) -> np.ndarray:
        """Captures a sub-rectangle of the game window (window-relative coordinates)."""
        if not self._stop_if_window_unavailable():
            frame = self._grab(rel_left, rel_top, width, height)
            if diag.ENABLED:
                self._diag_capture("region", (rel_left, rel_top, width, height), frame)
            self._note_frame(frame)
            if frame is not None:
                return frame
        else:
            self._note_frame(None)
        return np.zeros((max(1, height), max(1, width), 3), dtype=np.uint8)

    def _note_frame(self, frame) -> bool:
        """Records whether a window/region capture showed a picture at all.
        The callers still get a frame (black when the capture failed); the
        loops ask ``_last_frame_usable`` before reading "nothing there" as
        an answer."""
        usable = frame_usable(frame)
        self._last_frame_usable = usable
        if usable:
            if self._capture_bad_since is not None:
                lasted = time.perf_counter() - self._capture_bad_since
                diag.log("STATE", f"capture usable again after {lasted:.1f}s", tag=self.diag_tag)
                if self._capture_wait_logged:
                    self._log("Game window visible again — carrying on")
                self._capture_bad_since = None
                self._capture_wait_logged = False
        elif self._capture_bad_since is None:
            self._capture_bad_since = time.perf_counter()
            diag.log("STATE", "capture shows nothing usable (None, black or flat): not read as game state",
                     tag=self.diag_tag)
        return usable

    def _capture_outage(self) -> float:
        """Seconds the capture has shown nothing usable (0 while it works)."""
        since = self._capture_bad_since
        return 0.0 if since is None else time.perf_counter() - since

    def _check_capture_outage(self) -> bool:
        """Puts casting on hold while the window cannot be seen and stops
        the bot when that lasts. Returns False once the bot was stopped."""
        outage = self._capture_outage()
        if outage >= self._CAPTURE_STOP_AFTER:
            self._stop_bot(
                self.STOP_CAPTURE_UNAVAILABLE,
                f"[W{self.bot_id+1}] The game window could not be seen for {outage:.0f} s "
                f"(black or failed capture: minimized, covered or not rendering). Stopping bot.")
            return False
        if outage >= self._CAPTURE_WAIT_AFTER and not self._capture_wait_logged:
            self._capture_wait_logged = True
            self._log("The game window shows nothing (black or failed capture) — waiting, "
                      "no bait is counted. Restore or uncover the window.")
        return True

    def capture_screen(self) -> np.ndarray:
        """Captures the calibrated minigame region as a numpy array for processing."""
        region = self.region
        if not region:
            return self.capture_full_window()
        return self.capture_window_region(region.left, region.top, region.width, region.height)

    def atomic_capture_and_click(self, reaction_delay: float = 0.0) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Captures the panel and clicks the fish if it is inside the circle.

        ``reaction_delay`` (human-like mode) is waited *after* the fish was
        seen inside the circle and before the fresh re-check, so detection
        itself never goes blind.

        Returns: (minigame_active, fish_position_clicked or None)"""
        if self.paused:
            return (True, None)

        capture = self.capture_screen
        detect = self.detector.detect_window_and_fish
        cx, cy = self._circle_center
        radius_sq = self._circle_radius_sq

        lp = diag.current_loop()
        try:
            # ========== PHASE 1: Quick pre-check (NO LOCK) ==========
            window_active, fish_pos = detect(capture())

            if not window_active:
                lp.frame("frame: panel not found")
                lp.count("panel_missing")
                return (False, None)
            if not fish_pos:
                lp.frame("frame: panel open, no fish")
                lp.count("no_fish")
                return (True, None)
            if lp.active:
                lp.count("fish_seen")
                if not self._game_fish_seen:
                    lp.note(f"first fish of this game at {fish_pos}", "ANALYSIS")
                    if diag.first_pending("fish_detected", self.diag_tag):
                        diag.first("fish_detected", self.capture_screen(), tag=self.diag_tag,
                                   note=f"fish at {fish_pos} in region {self.region}")
            self._fish_ever_seen = self._game_fish_seen = True
            self._fish_track.append((time.perf_counter(), fish_pos[0], fish_pos[1]))

            if self.detector.learning and self.detector.profile.circle_center is None:
                # The fish is being followed by its movement, but where the
                # click circle is has not been worked out yet. A click now is
                # aimed at a guessed circle, and a miss costs game time.
                return (True, None)

            dx, dy = fish_pos[0] - cx, fish_pos[1] - cy
            if lp.active:
                lp.score("fish_dist", (dx * dx + dy * dy) ** 0.5)
            if (dx * dx + dy * dy) >= radius_sq:
                lp.frame(f"frame: fish {fish_pos} outside circle ({cx},{cy}) r={radius_sq ** 0.5:.0f} "
                         f"dist={(dx * dx + dy * dy) ** 0.5:.0f}")
                return (True, None)
            lp.frame(f"frame: fish {fish_pos} INSIDE circle ({cx},{cy}) r={radius_sq ** 0.5:.0f} -> click path")

            if reaction_delay > 0:
                time.sleep(reaction_delay)
            if self.paused:
                return (True, None)

            # ========== PHASE 2: Fresh capture + click (WITH LOCK) ==========
            # Urgent priority: a fish inside the circle goes ahead of any
            # queued cast / inventory sequence from another window.
            with input_lock.urgent():
                if self.paused:
                    return (True, None)

                # No-op when this window is already in front (single window);
                # otherwise switches and confirms before we look again.
                switch_before = getattr(self.window_manager, "last_switch_time", 0.0)
                if not self._focus():
                    return (True, None)
                switched = getattr(self.window_manager, "last_switch_time", 0.0) != switch_before

                window_active, fish_pos = detect(capture())
                if not window_active:
                    lp.note("re-check under the lock: panel gone", "DECISION")
                    return (False, None)
                if not fish_pos:
                    lp.count("recheck_no_fish")
                    return (True, None)

                fx, fy = fish_pos
                dx, dy = fx - cx, fy - cy
                if (dx * dx + dy * dy) >= radius_sq:
                    lp.count("recheck_left_circle")
                    return (True, None)

                # Aim where the fish will be when the button goes down, and
                # skip a fish that is about to leave the circle.
                now = time.perf_counter()
                self._fish_track.append((now, fx, fy))
                target = self._click_point(fish_pos, now, switched)
                if target is None:
                    lp.count("skipped_leaving_circle")
                    lp.frame(f"fish {fish_pos} predicted to leave the circle: no click", "DECISION")
                    return (True, None)
                fx, fy = target

                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                if diag.ENABLED:
                    lp.count("clicks")
                    diag.log("DECISION", f"click fish seen at {fish_pos}, aimed at {target} "
                                         f"(velocity {self._fish_velocity(now)}, circle ({cx},{cy}) "
                                         f"r={radius_sq ** 0.5:.0f}, region {self.region}, "
                                         f"reaction delay {reaction_delay * 1000:.0f} ms)", tag=self.diag_tag)
                self.human.move_to(win_left + self.region.left + fx,
                                   win_top + self.region.top + fy)
                time.sleep(self._cursor_settle(switched))
                self.human.click(hold=self._t_hold)
            # ========== LOCK RELEASED ==========

            # Post-click settle happens outside the lock so other windows can
            # use the mouse meanwhile. The lock itself is FIFO-fair.
            time.sleep(self._t_post)
            return (True, fish_pos)

        except Exception as e:
            diag.exc("click", tag=self.diag_tag)
            self._log(f"Click error: {e}")
            return (True, None)

    def _fish_velocity(self, now: float) -> Optional[Tuple[float, float]]:
        """Fish speed in px/s from the recent track, or None when the track is
        too short or too old to say."""
        track = self._fish_track
        while track and now - track[0][0] > self._TRACK_WINDOW:
            track.popleft()
        if len(track) < 2:
            return None
        t0, x0, y0 = track[0]
        t1, x1, y1 = track[-1]
        span = t1 - t0
        if span < self._TRACK_MIN_SPAN or now - t1 > self._TRACK_WINDOW * 0.5:
            return None
        return ((x1 - x0) / span, (y1 - y0) / span)

    _SWITCH_SETTLE = 0.04   # seconds the game needs to track the cursor after a focus switch

    def _cursor_settle(self, switched: bool) -> float:
        """Wait between moving the cursor and clicking. After a real focus
        switch the game has not followed the cursor yet: a click sent sooner
        lands where the cursor was the last time this window had it (the
        jigsaw bot waits the same)."""
        return max(self._t_cursor, self._SWITCH_SETTLE) if switched else self._t_cursor

    def _click_point(self, fish_pos: Tuple[int, int], now: float,
                     switched: bool = False) -> Optional[Tuple[int, int]]:
        """Where to click for a fish seen at ``fish_pos``: where it will be
        when the button goes down. None when it will have left the circle by
        then (a fish on its way out is not worth a miss)."""
        fx, fy = fish_pos
        velocity = self._fish_velocity(now)
        if velocity is None:
            return (fx, fy)
        lead = self._cursor_settle(switched) + self._CLICK_OVERHEAD
        px = int(round(fx + velocity[0] * lead))
        py = int(round(fy + velocity[1] * lead))
        cx, cy = self._circle_center
        dx, dy = px - cx, py - cy
        if (dx * dx + dy * dy) >= self._circle_radius_sq:
            return None
        return (px, py)

    def _bite_timeout(self) -> float:
        """How long to wait for the minigame after a cast.

        Bite delay differs per server. Recasting too early cancels the cast
        (and triggers "cannot change bait while fishing"), so the timeout
        stretches after every miss — up to ``timing_minigame_timeout_cap``
        (60 s by default) by the last attempt before the bait key is given
        up — and remembers the longest bite seen."""
        base = self._t_bite_timeout
        learned = self._longest_bite * 1.5 + 1.0 if self._longest_bite else 0.0
        grown = base * (1.5 ** min(self.consecutive_failures, 4))
        return min(max(base, learned, grown), max(base, self._t_bite_cap))

    def _next_recast_wait(self) -> float:
        """Base wait before the next cast (quick skip off), remembered so the
        outcome of that cast can be fed back to the tuner."""
        tuner = self._recast_tuner
        if tuner is None:
            wait = self._t_recast_wait
        else:
            shared = FishingBot._shared_recast_wait
            if not tuner.settled and shared is not None and shared <= tuner.ceiling:
                tuner.wait = tuner.good = shared
                tuner.settled = True
                self._log(f"Wait before next cast: using {shared:.1f}s found by another window")
            wait = tuner.wait
        self._last_cast_wait = wait
        return wait

    _RECAST_SLACK = 0.5   # a cast this much later than planned says nothing about the wait

    def _wait_before_recast(self) -> bool:
        """Waits until the character can cast again (quick skip off).
        Returns True when a pause interrupted the wait.

        With the adaptive wait the time is counted from the end of the game,
        not from the end of item handling: what the server makes the
        character wait for starts when the game ends, so the time spent
        looking at the inventory, dropping a fish or waiting for another
        window to release the mouse is part of the wait, not added to it.
        The fixed wait (adaptive off) is slept in full, as configured."""
        tuner = self._recast_tuner
        base = self._next_recast_wait()
        jitter = float(np.random.uniform(0, 0.15 if tuner is not None else 0.5))
        if tuner is not None and self._game_end_time is not None:
            wait_end = self._game_end_time + base + jitter
        else:
            wait_end = time.perf_counter() + base + jitter
        diag.log("DECISION", f"wait before next cast: base {base:.2f}s + jitter {jitter:.2f}s counted from "
                             f"{'game end' if tuner is not None and self._game_end_time is not None else 'now'} "
                             f"-> {max(0.0, wait_end - time.perf_counter()):.2f}s left "
                             f"(adaptive={'on' if tuner else 'off'}"
                             f"{', settled' if tuner and tuner.settled else ''})", tag=self.diag_tag)
        paused = False
        while self.running and time.perf_counter() < wait_end:
            if self.paused:
                paused = True
                time.sleep(0.1)
                continue
            time.sleep(min(0.05, max(0.0, wait_end - time.perf_counter())))
        if paused:
            self._last_cast_wait = None   # waited longer than planned
        return paused

    def _report_cast(self, waited: Optional[float], success: bool) -> None:
        """Tells the tuner how the cast that followed a recast wait went."""
        tuner = self._recast_tuner
        if tuner is None or waited is None:
            return
        message = tuner.report(waited, success)
        if diag.ENABLED:
            diag.log("DECISION", f"recast tuner: cast after {waited:.2f}s {'got a bite' if success else 'MISSED'} "
                                 f"-> next wait {tuner.wait:.2f}s (proven {tuner.good:.2f}s, ceiling "
                                 f"{tuner.ceiling:.2f}s, settled={tuner.settled})", tag=self.diag_tag)
        if message:
            self._log(message)
            # Share a settled wait; withdraw it if this window had to back off.
            FishingBot._shared_recast_wait = (
                tuner.wait if tuner.settled and tuner.wait < tuner.ceiling else None)

    def get_bait_key(self, bait_count: int) -> str:
        """Determines which keyboard key to press based on bait counter and selected keys."""
        if not self.bait_keys:
            return '1'
        
        num_keys = len(self.bait_keys)
        bait_per_key = self.config.get("bait_quantity", 200)
        
        # Calculate which key index to use based on bait count
        # Keys are used from first to last as bait depletes
        for i, key in enumerate(self.bait_keys):
            threshold = (num_keys - i - 1) * bait_per_key
            if bait_count > threshold:
                return key
        
        # If bait count is very low, use the last key
        return self.bait_keys[-1]
    
    def get_tier_thresholds(self) -> list:
        """Returns list of tier thresholds based on selected keys."""
        num_keys = len(self.bait_keys)
        bait_per_key = self.config.get("bait_quantity", 200)
        # Create thresholds: e.g., for 4 keys: [600, 400, 200, 0]
        return [(num_keys - i - 1) * bait_per_key for i in range(num_keys)]
    
    def _click_page_tab(self, screen_x: int, screen_y: int):
        """Clicks an inventory page tab reliably.
        Uses explicit mouseDown + hold + mouseUp (instead of the instant click()
        call) so the game's UI overlay has time to register the press. Must be
        called while holding input_lock with the target window already activated."""
        self.human.move_to(screen_x, screen_y)
        self.human.sleep(0.10)            # cursor settle
        self.human.click(hold=0.05)       # hold — game needs to see the down event

    _PAGE_WAIT_TIMEOUT = 2.0   # longest wait for a clicked tab to become the active one
    _PAGE_WAIT_POLL = 0.05

    def _wait_for_page(self, page_pos, settle: float) -> None:
        """After a tab click: waits until the game shows that page.

        A slow machine can take longer than any fixed wait to draw the page,
        and the page read too early is the previous one. The grid cannot
        tell (two empty pages look the same), the tabs can: the wait ends
        when the clicked tab is the only one drawn as active. Halfway
        through, the tab is clicked once more in case the game missed the
        click. After the timeout the bot goes on as it did before this
        check existed. Without readable tabs it is the fixed ``settle`` wait."""
        _, _, win_width, win_height = self.window_manager.get_window_rect()
        points = inventory_layout.tab_points(self.config, win_width, win_height)
        try:
            clicked = (int(page_pos[0]), int(page_pos[1]))
        except (TypeError, ValueError, IndexError):
            clicked = None
        target = next((page for page, point in points.items() if point == clicked), None)
        if target is None or len(points) < 3 or self._tab_check_off:
            if diag.ENABLED:
                why = ("tab reading switched off for this session" if self._tab_check_off else
                       f"tab {clicked} is not one of the configured tabs" if target is None else
                       f"only {len(points)} tabs configured")
                diag.log("DECISION", f"page wait: fixed {settle:.2f}s ({why})", tag=self.diag_tag)
            time.sleep(settle)
            return

        def click_again():
            diag.log("DECISION", f"page wait: tab {target} not active after {self._PAGE_WAIT_TIMEOUT / 2:.2f}s: "
                                 f"clicking it once more", tag=self.diag_tag)
            with input_lock:
                if self.running and self._focus(force=True):
                    win_left, win_top, _, _ = self.window_manager.get_window_rect()
                    self._click_page_tab(win_left + clicked[0], win_top + clicked[1])

        with diag.loop("page wait", tag=self.diag_tag, tab=target, timeout=self._PAGE_WAIT_TIMEOUT) as lp:
            ok, waited, active, detail, looks = inventory_layout.wait_for_tab(
                self._grab, points, win_width, win_height, target, lambda: self.running,
                self._PAGE_WAIT_TIMEOUT, self._PAGE_WAIT_POLL, click_again)
            lp.count("looks", looks)
            lp.set("result", f"tab {target} active after {waited:.2f}s" if ok else
                             f"timeout after {waited:.2f}s, active tab read as {active}")
        if ok:
            self._tab_check_seen = True
            self._tab_check_misses = 0
            diag.log("STATE", f"page wait: tab {target} active after {waited:.2f}s ({looks} looks; {detail})",
                     tag=self.diag_tag)
            return
        if not self.running:
            return
        self._tab_check_misses += 1
        diag.log("DECISION", f"page wait: TIMEOUT after {waited:.2f}s, tab {target} never read as active "
                             f"(last read: {active}; {detail}; {looks} looks): going on as if the page "
                             f"were showing", tag=self.diag_tag)
        if not self._tab_check_seen and self._tab_check_misses >= 2:
            self._tab_check_off = True
            diag.log("DECISION", "page wait: the active tab was never recognized on this client: "
                                 "using the fixed wait from now on", tag=self.diag_tag)

    def _goto_page(self, page_pos, settle: float = 0.3) -> bool:
        """Clicks an inventory page tab (window-relative position) and waits
        until the game shows the page. False when the click could not be
        sent: the page on screen is then still the old one."""
        ok = False
        for _attempt in range(3):
            if not self.running:
                return False
            with input_lock:
                if self._focus(force=True):
                    win_left, win_top, _, _ = self.window_manager.get_window_rect()
                    diag.log("ACTION", f"inventory page tab at win={tuple(page_pos)} "
                                       f"screen=({win_left + page_pos[0]},{win_top + page_pos[1]})", tag=self.diag_tag)
                    self._click_page_tab(win_left + page_pos[0], win_top + page_pos[1])
                    ok = True
            if ok:
                break
            diag.log("DECISION", f"page tab not clicked (attempt {_attempt + 1}/3): retrying in 0.5s",
                     tag=self.diag_tag)
            time.sleep(0.5)
        if ok:
            self._fill_page_pending = False   # the bot's own page is being shown
            self._wait_for_page(page_pos, settle)
        else:
            diag.log("DECISION", "page tab could not be clicked: the old page is still showing", tag=self.diag_tag)
        return ok

    def adjust_bait_tier(self):
        """Adjusts bait counter to next lower tier when 2 consecutive failures occur."""
        thresholds = self.get_tier_thresholds()
        before = self.bait_counter
        
        # Find current tier and drop to next one
        for threshold in thresholds:
            if self.bait_counter > threshold:
                self.bait_counter = threshold
                break
        else:
            # Already at or below lowest threshold
            self.bait_counter = 0
        
        self.consecutive_failures = 0
        diag.log("DECISION", f"bait tier drop: thresholds {thresholds}, counter {before} -> {self.bait_counter} "
                             f"(key {self.get_bait_key(before)} -> {self.get_bait_key(self.bait_counter)})",
                 tag=self.diag_tag)
        
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] No bite on this bait key — assuming it is empty. Bait adjusted to {self.bait_counter}")
        if self.on_bait_update:
            self.on_bait_update(self.bot_id, self.bait_counter)
        if self.on_stats_update:
            self.on_stats_update(self.bot_id, self.hits, self.total_games, self.bait_counter)
    
    def press_ctrl_key(self, key: str) -> bool:
        """Presses CTRL+key combination once. Uses input lock for thread safety.
        Returns True when the keys were sent."""
        if not self.keyboard_controller:
            return False
        
        with input_lock:
            try:
                # Paused: the player has the controls, a Ctrl+G now would
                # mount or dismount under them.
                if not self.running or self.paused or not self._focus():
                    return False
                self.human.sleep(self._t_key_set)
                held = []
                try:
                    self.keyboard_controller.press(Key.ctrl)
                    held.append(Key.ctrl)
                    self.human.sleep(self._t_key_hold)
                    self.keyboard_controller.press(key)
                    held.append(key)
                    self.human.sleep(self._t_key_hold)
                finally:
                    # Whatever went wrong, no key stays down: a Ctrl left
                    # held turns every later bait key into Ctrl+key.
                    for k in reversed(held):
                        try:
                            self.keyboard_controller.release(k)
                        except Exception:
                            diag.exc(f"releasing {k}", tag=self.diag_tag)
                        if k is not held[0]:
                            self.human.sleep(self._t_key_hold)
                diag.log("ACTION", f"CTRL+{key} sent via {type(self.keyboard_controller).__name__}",
                         tag=self.diag_tag)
                return True
            except Exception as e:
                diag.exc(f"CTRL+{key}", tag=self.diag_tag)
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error pressing CTRL+{key}: {e}")
                return False
    
    def bait_and_cast(self) -> bool:
        """Selects bait and casts fishing line in a single lock acquisition.
        Returns False when no cast was sent: the window could not be focused,
        or sending the keys failed (``_cast_error`` then says why, so the
        caller does not count it as a missed bite)."""
        self._cast_error = None
        if not self.keyboard_controller:
            self._cast_error = "no keyboard input is available"
            return False

        bait_key = self.get_bait_key(self.bait_counter)
        if diag.ENABLED and bait_key != self._diag_last_bait_key:
            diag.log("DECISION", f"bait key {self._diag_last_bait_key} -> {bait_key} (bait counter "
                                 f"{self.bait_counter}, thresholds {self.get_tier_thresholds()}, keys {self.bait_keys})",
                     tag=self.diag_tag)
            self._diag_last_bait_key = bait_key

        with input_lock:
            try:
                # Paused (F5) while waiting for the lock: the player has the
                # controls, a cast now would land in their game.
                if self.paused:
                    self._was_paused = True
                    diag.log("DECISION", "paused: cast not sent", tag=self.diag_tag)
                    return False
                if not self.running or not self._focus():
                    return False
                self._verify_capture()
                self.human.sleep(self._t_key_set)

                self.human.tap_key(game_key(bait_key), hold=self._t_key_hold)
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Pressed key {bait_key}")

                self.human.sleep(self._t_interkey)

                self.human.tap_key(game_key(self.config.get('cast_key', 'space')), hold=self._t_key_hold)
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Cast fishing line")
            except Exception as e:
                diag.exc("bait_and_cast", tag=self.diag_tag)
                self._cast_error = str(e) or type(e).__name__
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error in bait_and_cast: {e}")
                return False

        time.sleep(0.05)
        return True
    
    def quickskip(self):
        """Performs quick skip - uses different method based on mode (horse or armour)."""
        # Get quick skip mode from config (default to 'horse' if not set)
        quick_skip_mode = self.config.get('quick_skip_mode', 'horse')
        
        if quick_skip_mode == 'horse':
            # Horse mode: double press CTRL+G
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Quick skip (Horse mode - CTRL+G)...")
            self.press_ctrl_key(self.config.get('mount_key', 'g'))
            time.sleep(self._t_qs_between)  # game must process the first CTRL+G
            self.press_ctrl_key(self.config.get('mount_key', 'g'))
            time.sleep(self._t_qs_after)    # before the next action
        else:
            # Armour mode: right-click the spare armor to swap it with the worn one
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Quick skip (Armor mode - right-click)...")
            if not self._armor_quickskip() and self._wait_before_recast():
                self._was_paused = True
            return

    _ARMOR_TAB_TIMEOUT = 0.6   # longest wait for the armor's page tab to read as active

    def _armor_plan(self) -> Tuple[Optional[str], Optional[int], str]:
        """How to reach the spare armor: ("here", page, why) right-click it
        straight away, ("switch", page, why) show its page first, (None,
        None, why) not at all: a right-click on a page that is not
        confirmed to be the armor's would use whatever item sits there."""
        _, _, win_w, win_h = self.window_manager.get_window_rect()
        points = inventory_layout.tab_points(self.config, win_w, win_h)
        if len(points) < 2:
            return "here", None, "at most one page tab configured: the page never changes"
        try:
            page = int(self.config.get("armor_slot_page"))
        except (TypeError, ValueError):
            page = None
        if page is None or not 1 <= page <= 8:
            return None, None, ("it is not known which inventory page the spare armor is on "
                                "(capture Spare Armor again while the game shows that page)")
        if page == self._current_inv_page + 1:
            return "here", page, f"the armor's page {page} is the page on screen"
        if page not in points:
            return None, page, f"the tab of the armor's page {page} is not configured"
        if len(points) < 3 or self._tab_check_off:
            return None, page, "the page tabs cannot be read on this client, so the armor's page cannot be confirmed"
        return "switch", page, f"armor on page {page}, page {self._current_inv_page + 1} on screen"

    def _armor_tab_active(self, points, win_w: int, win_h: int, page: int) -> bool:
        """One look at the tabs: is ``page`` the active one?"""
        rect = inventory_layout.tabs_rect(points, win_w, win_h)
        frame = self._grab(*rect) if rect else None
        if frame is None:
            return False
        active, detail = inventory_layout.active_tab(frame, rect[:2], points)
        if active != page:
            diag.log("DECISION", f"armor: tab {page} not active (read {active}; {detail})", tag=self.diag_tag)
        return active == page

    def _armor_quickskip(self) -> bool:
        """Right-clicks the spare armor. When it lies on another page than
        the one being filled, that page is shown first and only clicked
        once its tab reads as active; the page being filled is clicked
        again right after (checked before the next inventory read, see
        ``_restore_fill_page``). False when the armor was not right-clicked
        and the caller should wait for the animation instead (True as well
        when the window could not be focused: nothing was sent, as before)."""
        armor_pos = self.config.get('armor_slot_pos')
        if not armor_pos:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Armor slot position not set! Falling back to wait.")
            return False
        plan, page, why = self._armor_plan()
        if plan is None:
            if why != getattr(self, "_armor_refusal", None):
                self._armor_refusal = why
                self._log(f"Armor quick skip skipped: {why}. Waiting for the animation instead.")
            diag.log("DECISION", f"armor quick skip not sent: {why}", tag=self.diag_tag)
            return False
        self._armor_refusal = None
        fill_pos = self.config.get(f'inv_page_{self._current_inv_page + 1}_pos')
        if plan == "switch" and not fill_pos:
            diag.log("DECISION", "armor quick skip not sent: the page being filled has no tab to come back to",
                     tag=self.diag_tag)
            return False

        # The human-like pause comes before the lock: nothing is on the
        # cursor yet, and other windows' fish clicks need not wait for it.
        time.sleep(np.random.uniform(0.15, 0.2))
        _, _, win_w, win_h = self.window_manager.get_window_rect()
        points = inventory_layout.tab_points(self.config, win_w, win_h)
        if plan == "switch":
            diag.log("DECISION", f"armor quick skip: {why}: showing page {page}", tag=self.diag_tag)
            tab = points[page]
            with input_lock:
                if not self.running or not self._focus(force=True):
                    return True   # nothing sent; as before, no wait either
                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                self._click_page_tab(win_left + tab[0], win_top + tab[1])
            self._fill_page_pending = True   # the armor's page may be on screen from here on

            def click_again():
                with input_lock:
                    if self.running and self._focus(force=True):
                        left, top, _, _ = self.window_manager.get_window_rect()
                        self._click_page_tab(left + tab[0], top + tab[1])

            ok, waited, active, detail, looks = inventory_layout.wait_for_tab(
                self._grab, points, win_w, win_h, page, lambda: self.running,
                self._ARMOR_TAB_TIMEOUT, self._PAGE_WAIT_POLL, click_again)
            diag.log("STATE" if ok else "DECISION",
                     f"armor: tab {page} {'active' if ok else 'NOT active'} after {waited:.2f}s "
                     f"({looks} looks; last read {active}; {detail})", tag=self.diag_tag)
            if ok:
                self._tab_check_seen = True
                self._tab_check_misses = 0
            else:
                if self.running and not self._tab_check_seen:
                    self._tab_check_misses += 1
                    if self._tab_check_misses >= 2:
                        self._tab_check_off = True   # no more armor page switches either
                        diag.log("DECISION", "armor: the active tab was never recognized on this client: "
                                             "page tabs are not read from now on", tag=self.diag_tag)
                self._restore_fill_page()
                return False

        with input_lock:
            switch_before = getattr(self.window_manager, "last_switch_time", 0.0)
            if not self.running or not self._focus(force=True):
                return True   # nothing sent; as before, no wait either
            switched = getattr(self.window_manager, "last_switch_time", 0.0) != switch_before
            # Another window may have had the cursor since the tab was
            # confirmed: one more look right before the click.
            if plan == "switch" and not self._armor_tab_active(points, win_w, win_h, page):
                clicked = False
            else:
                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                self.human.move_to(win_left + armor_pos[0], win_top + armor_pos[1])
                time.sleep(max(0.05, self._cursor_settle(switched)))   # the game registers the hover
                self.human.click(button='right')
                clicked = True
                if plan == "switch":
                    # Back to the page being filled; whether it shows is
                    # checked before the next inventory read, which is
                    # seconds away, so the cast is not held up here.
                    self.human.sleep(0.05)
                    self._click_page_tab(win_left + fill_pos[0], win_top + fill_pos[1])
        # ========== LOCK RELEASED ==========
        if plan == "switch" and not clicked:
            self._restore_fill_page()
            return False
        time.sleep(np.random.uniform(0.05, 0.07))  # animation settle outside lock
        return True

    def _restore_fill_page(self) -> None:
        """After an armor quick skip on another page: makes sure the page
        being filled is the one on screen before it is read. One look at the
        tabs normally; a tab click and wait only when it is not."""
        if not getattr(self, "_fill_page_pending", False) or not self.running:
            return
        self._fill_page_pending = False
        page = self._current_inv_page + 1
        page_pos = self.config.get(f'inv_page_{page}_pos')
        if not page_pos:
            return
        _, _, win_w, win_h = self.window_manager.get_window_rect()
        points = inventory_layout.tab_points(self.config, win_w, win_h)
        if self._armor_tab_active(points, win_w, win_h, page):
            return
        diag.log("DECISION", f"armor: page {page} is not on screen after the quick skip: clicking its tab",
                 tag=self.diag_tag)
        if not self._goto_page(page_pos):
            self._fill_page_pending = True   # try again before the next read

    def press_key(self, key: str, description: str = ""):
        """Presses a keyboard key using pynput. Uses input lock for thread safety."""
        if not self.keyboard_controller:
            return
        
        with input_lock:
            try:
                if not self.running or not self._focus():
                    return
                self.human.sleep(self._t_key_set)

                self.human.tap_key(game_key(key), hold=self._t_key_hold)

                if description and self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] {description}")
            except Exception as e:
                diag.exc(f"press_key {key}", tag=self.diag_tag)
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error pressing key '{key}': {e}")
    
    def wait_for_minigame_window(self, timeout: float = 6.0) -> bool:
        """Waits for the fishing minigame panel after a cast.

        First sighting calibrates the region (confirmed over two frames so a
        half-drawn panel is never locked in). After that only the region is
        polled; if nothing shows up for a while the whole window is re-scanned
        in case the panel moved, the game window was resized or the panel
        looks different this time. On a client whose panel has been seen to
        open in different places the whole window is scanned from the start.
        Returns True if the minigame is open, False on timeout. A pause ends
        the wait (``_wait_interrupted``): what happens in the game while the
        player has the controls says nothing about this cast."""
        start = time.perf_counter()
        last_full_scan = start
        self._pending_bounds = None
        self._wait_interrupted = False
        wanders = self._panel_moves > 0
        rescan_after = 0.0 if wanders else self._RECALIBRATE_AFTER
        rescan_gap = self._MOVED_RESCAN_GAP if wanders else 0.5
        last_adapt = start
        changed_since = None
        partial_since = None

        self._diag_saved_calib = False
        prev_now = None
        with diag.loop("wait for minigame", tag=self.diag_tag, timeout=round(timeout, 2),
                       calibrated=self.region_auto_calibrated, region=self.region,
                       profile=self.detector.profile.name) as lp:
            while self.running:
                now = time.perf_counter()
                if prev_now is not None and not self._last_frame_usable:
                    # The last look showed nothing at all (black / failed
                    # capture): that time says nothing about the bite, so it
                    # does not count towards the timeout.
                    start += now - prev_now
                    lp.count("unusable_frames")
                    if not self._check_capture_outage():
                        lp.set("result", "capture unavailable")
                        return False
                prev_now = now
                elapsed = now - start
                if elapsed >= timeout:
                    break
                if self.paused:
                    self._wait_interrupted = True
                    lp.set("result", "paused")
                    return False

                try:
                    lp.frame(f"t={elapsed:.2f}s calibrated={self.region_auto_calibrated} "
                             f"pending={self._pending_bounds}")
                    if not self.region_auto_calibrated:
                        lp.count("full_scans")
                        if self._try_calibrate(self.capture_full_window(), elapsed):
                            lp.set("result", f"found+calibrated after {elapsed:.2f}s")
                            return True
                    else:
                        frame = self.capture_screen()
                        window_active, _ = self.detector.detect_window_and_fish(frame)
                        if window_active:
                            if not self.detector.partly_visible:
                                lp.set("result", f"panel open in region after {elapsed:.2f}s")
                                return True
                            if partial_since is None:
                                lp.note("panel only partly inside the calibrated region: searching the window")
                            # The panel is open, but only part of it is where it
                            # was last time: it opened somewhere else. Playing it
                            # here would aim at a circle that is not there.
                            if partial_since is None:
                                partial_since = now
                            if now - last_full_scan >= 0.1 or self._pending_bounds is not None:
                                last_full_scan = now
                                # Not found elsewhere with the active colors either:
                                # it may be another look whose colors overlap them.
                                adapt = now - partial_since >= self._LOOK_CHANGE_CONFIRM
                                lp.count("full_scans")
                                if self._try_calibrate(self.capture_full_window(), elapsed, adapt):
                                    lp.set("result", f"re-found elsewhere after {elapsed:.2f}s")
                                    return True
                            if now - partial_since >= self._PARTIAL_PATIENCE:
                                lp.set("result", "partly visible panel played as is")
                                return True   # nothing better found: something covers it
                            time.sleep(0.02)
                            continue
                        partial_since = None

                        # Something filled the place where the panel opens, and
                        # the active profile does not recognize it: most likely
                        # the panel in a different look. Worth a closer look now.
                        look_changed = False
                        if self._adaptive and self._region_changed(frame):
                            if changed_since is None:
                                changed_since = now
                            look_changed = now - changed_since >= self._LOOK_CHANGE_CONFIRM
                            lp.count("region_changed_frames")
                        else:
                            changed_since = None

                        pending = self._pending_bounds is not None
                        rescan_due = now - last_full_scan >= rescan_gap or pending
                        if (elapsed >= rescan_after and rescan_due) or (
                                look_changed and now - last_full_scan >= self._LOOK_CHANGE_SCAN_GAP):
                            last_full_scan = now
                            # Other looks are tried seldom while nothing points to
                            # one (each try costs several full-window passes).
                            adapt = look_changed or pending or now - last_adapt >= self._ADAPT_RETRY_GAP
                            if adapt:
                                last_adapt = now
                            lp.count("full_scans")
                            if self._try_calibrate(self.capture_full_window(), elapsed, adapt):
                                lp.set("result", f"found elsewhere after {elapsed:.2f}s")
                                return True

                    time.sleep(0.05)
                except Exception as e:
                    diag.exc("waiting for the minigame", tag=self.diag_tag)
                    self._log(f"Error: {e}")
                    time.sleep(0.05)

            lp.set("result", "timeout" if self.running else "bot stopped")
        if diag.ENABLED and self.running:
            diag.log("DECISION", f"no minigame within {timeout:.1f}s after the cast "
                                 f"(calibrated={self.region_auto_calibrated})", tag=self.diag_tag)
            self._diag_negative("no_minigame", self.capture_full_window(),
                                (0, 0) + tuple(self.window_manager.get_window_rect()[2:]))
            if self._reference_frame is not None:
                diag.image("no_minigame_before_cast", self._reference_frame, tag=self.diag_tag,
                           note="full window just before the cast")
        return False

    def _panel_elsewhere(self) -> bool:
        """The panel left the calibrated region: was it moved rather than
        closed? Looks at the whole window (twice — a move must be confirmed)
        and re-calibrates when the same panel is found in another place."""
        if not self.region_auto_calibrated:
            return False
        try:
            self._pending_bounds = None
            for _ in range(2):
                # Only the active profile: nothing is learned here
                if self._try_calibrate(self.capture_full_window(), 0.0, adapt=False):
                    return True
                if self._pending_bounds is None:
                    return False
                time.sleep(self._END_CONFIRM_GAP)
        except Exception as e:
            diag.exc("panel moved check", tag=self.diag_tag)
            self._log(f"Error: {e}")
        finally:
            self._pending_bounds = None
        return False

    # ------------------------------------------------------------ calibration

    @staticmethod
    def _bounds_close(a, b, tol: int = 3) -> bool:
        return all(abs(a[i] - b[i]) <= tol for i in range(4))

    _PARTIAL_PATIENCE = 1.0       # seconds a partly visible panel is searched for elsewhere before it is played as is
    _PARTIAL_CHECK_GAP = 0.5      # seconds between such searches during a game
    _LOOK_CHANGE_SCAN_GAP = 0.3   # seconds between full-window scans while it does
    _LOOK_CHANGE_CONFIRM = 0.25   # seconds the panel's place must look different before acting on it
    _ADAPT_RETRY_GAP = 1.5        # seconds between tries of other looks while calibrated

    def _region_changed(self, frame: np.ndarray) -> bool:
        """Whether the calibrated region looks substantially different from
        how it looked just before the cast."""
        ref, region = self._reference_frame, self.region
        if ref is None or region is None or frame is None:
            return False
        before = ref[region.top:region.top + region.height, region.left:region.left + region.width]
        if before.shape != frame.shape or before.size == 0:
            return False
        diff = cv2.absdiff(before, frame).max(axis=2)
        return cv2.countNonZero(cv2.compare(diff, 25, cv2.CMP_GT)) > diff.size * 0.5

    def _try_calibrate(self, frame: np.ndarray, elapsed: float, adapt: Optional[bool] = None) -> bool:
        """Looks for the minigame panel in a full-window frame.

        ``adapt`` says whether looks other than the active profile's may be
        tried (default: once the active one has had ``_ADAPTIVE_AFTER``
        seconds).

        Tries the active profile first; when that finds nothing for a while
        and adaptive detection is on, falls back to other known profiles and
        finally to learning the panel from the pre-cast reference frame.
        A candidate must be seen in two consecutive frames at the same place
        before the region is (re)calibrated."""
        profile = None
        bounds = self.detector.find_fishing_window_bounds(frame)
        pixels = self.detector._last_bounds_pixels if bounds else 0
        if diag.ENABLED:
            fill = pixels / float(bounds[2] * bounds[3]) if bounds else 0.0
            diag.trace("ANALYSIS", f"panel search with profile '{self.detector.profile.name}': bounds={bounds} "
                                   f"color pixels={pixels} fill={fill:.2f} (min {self._MIN_PANEL_FILL})",
                       tag=self.diag_tag)

        if (bounds is not None and self._adaptive and not self.region_auto_calibrated
                and pixels < bounds[2] * bounds[3] * self._MIN_PANEL_FILL):
            if diag.ENABLED:
                diag.current_loop().count("rejected_low_fill")
                diag.trace("DECISION", f"panel candidate {bounds} rejected: profile color covers only "
                                       f"{pixels / float(bounds[2] * bounds[3]):.2f} of it", tag=self.diag_tag)
                if not getattr(self, "_diag_saved_calib", True):
                    self._diag_saved_calib = True
                    diag.image("calibration_rejected_low_fill", frame, tag=self.diag_tag,
                               note=f"candidate {bounds} fill {pixels / float(bounds[2] * bounds[3]):.2f}")
            # The profile's color covers only patches of what it found: a
            # panel in a similar but different color (or scenery). Taking the
            # patches' outline as the panel puts the click circle in the wrong
            # place, so let the panel be learned instead.
            bounds, pixels = None, 0

        def fits(b) -> bool:
            # The panel is known: it can move and it can change its look, but
            # it does not change size. Anything else is scenery of a similar
            # color, or some other window that opened in the game.
            if b is None or not self.region_auto_calibrated:
                return b is not None
            rw, rh = self.region.width, self.region.height
            return abs(b[2] - rw) <= rw * 0.15 and abs(b[3] - rh) <= rh * 0.15

        if bounds is not None and not self.region_auto_calibrated and not self.detector.profile.learned:
            why = self._first_panel_doubt(frame, bounds, pixels)
            if why:
                if diag.ENABLED:
                    diag.current_loop().count("rejected_first_calibration")
                    diag.trace("DECISION", f"panel candidate {bounds} rejected: {why}", tag=self.diag_tag)
                bounds, pixels = None, 0

        if not fits(bounds):
            if bounds is not None and diag.ENABLED:
                diag.current_loop().count("rejected_size")
                diag.trace("DECISION", f"panel candidate {bounds} rejected: size differs >15% from the known "
                                       f"panel {self.region}", tag=self.diag_tag)
            bounds, pixels = None, 0
        if adapt is None:
            adapt = elapsed >= self._ADAPTIVE_AFTER
        if bounds is None and self._adaptive and adapt:
            bounds, profile, pixels = self._find_with_other_profiles(frame, fits)

        if bounds is None:
            if self._pending_bounds is not None and diag.ENABLED:
                diag.log("DECISION", f"calibration candidate {self._pending_bounds} NOT confirmed by the next "
                                     f"frame: discarded", tag=self.diag_tag)
                if not getattr(self, "_diag_saved_calib", True):
                    self._diag_saved_calib = True
                    diag.image("calibration_discarded", frame, tag=self.diag_tag,
                               note=f"candidate {self._pending_bounds} vanished")
            self._pending_bounds = None
            return False

        if (profile is None and self.region_auto_calibrated and self._bounds_close(
                bounds, (self.region.left, self.region.top, self.region.width, self.region.height))):
            # Right where it is known to be: nothing to re-calibrate (and
            # nothing a learned profile has learned about it to throw away).
            self._pending_bounds = None
            return True

        pending = self._pending_bounds
        self._pending_bounds = bounds
        if pending is None or not self._bounds_close(pending, bounds):
            diag.log("DECISION", f"panel candidate {bounds} (profile "
                                 f"'{profile.name if profile else self.detector.profile.name}', {pixels} px)"
                                 f"{' replaces ' + str(pending) if pending else ''}: waiting for a confirming frame",
                     tag=self.diag_tag)
            return False  # wait for a confirming frame

        self._pending_bounds = None
        if profile is not None:
            self.detector.set_profile(profile)
            self._games_without_fish = 0
            self._fish_ever_seen = False
            self._log(f"Adapted to this client's minigame colors (profile '{profile.name}')")
            diag.image("adapted_profile", frame, tag=self.diag_tag,
                       note=f"profile {profile.to_dict() if hasattr(profile, 'to_dict') else profile}")
            if self._reference_frame is not None:
                diag.image("adapted_profile_before_cast", self._reference_frame, tag=self.diag_tag)

        x, y, w, h = bounds
        moved = self.region_auto_calibrated
        if (moved and profile is None and x + self.region.width <= frame.shape[1]
                and y + self.region.height <= frame.shape[0]):
            # The same panel in another place: it keeps its size. The
            # measured height is not stable on a real client: the strip of
            # water under the timer bar comes and goes with the water
            # animation (172-194 px for a 190 px panel), and the circle is
            # placed at half the height, so taking a mid-game measurement
            # put it up to 9 px off for the rest of the game.
            w, h = self.region.width, self.region.height
        if moved and (abs(x - self.region.left) > 8 or abs(y - self.region.top) > 8):
            self._panel_moves += 1
        self.region = GameRegion(x, y, w, h)
        self.region_auto_calibrated = True
        self.detector.calibrate(pixels)
        self._update_region_cache()
        self._log(f"{'Re-calibrated' if moved else 'Auto-calibrated'} region: {w}x{h} at ({x},{y})")
        if diag.ENABLED:
            diag.log("STATE", f"calibrated: region={self.region} circle center={self._circle_center} "
                              f"r={self._circle_radius_sq ** 0.5:.0f} profile={self.detector.profile.name} "
                              f"panel_moves={self._panel_moves}", tag=self.diag_tag)
            diag.first("minigame_calibrated", frame, tag=self.diag_tag, note=f"region {self.region}")
        return True

    def _first_panel_doubt(self, frame: np.ndarray, bounds, pixels: int) -> Optional[str]:
        """Why a first sighting with the stock colors is not the panel (None
        when it may be). The stock colors also match scenery: a candidate
        must have a panel's size, and must not have been there before the
        cast (the minigame opens after it)."""
        x, y, w, h = bounds
        fh, fw = frame.shape[:2]
        min_w, min_h = self._PANEL_MIN_SIZE
        if w < min_w or h < min_h:
            return f"{w}x{h} is smaller than a panel ({min_w}x{min_h})"
        if w > fw * self._PANEL_MAX_SHARE or h > fh * self._PANEL_MAX_SHARE:
            return f"{w}x{h} is larger than {self._PANEL_MAX_SHARE:.0%} of the {fw}x{fh} window"
        ref = self._reference_frame
        if ref is None or ref.shape[:2] != frame.shape[:2] or not frame_usable(ref):
            return None
        before = self.detector.panel_pixels(ref[y:y + h, x:x + w])
        if pixels > 0 and before >= pixels * 0.5:
            return (f"already there before the cast ({before} of {pixels} panel-colored pixels): "
                    f"scenery, not the minigame")
        return None

    def _find_with_other_profiles(self, frame: np.ndarray, fits=None):
        """Returns (bounds, profile, pixel_count) from a profile other than the
        active one, or (None, None, 0). ``fits(bounds)`` can veto a find."""
        if fits is None:
            fits = lambda b: True  # noqa: E731
        active = self.detector.profile
        candidates = []
        shared = FishingBot._shared_learned_profile
        if shared is not None and shared is not active:
            candidates.append(shared)
        if active.name != "official":
            candidates.append(DetectionProfile())
        # Looks this session has already learned (a client that alternates)
        for known in list(FishingBot._known_profiles):
            if known is not active and all(known is not c for c in candidates):
                candidates.append(known)

        for candidate in candidates:
            probe = FishDetector(candidate)
            bounds = probe.find_fishing_window_bounds(frame)
            if diag.ENABLED:
                diag.trace("ANALYSIS", f"other profile '{candidate.name}': bounds={bounds} "
                                       f"pixels={probe._last_bounds_pixels if bounds else 0}", tag=self.diag_tag)
            # Same rule as for the active profile: a color that covers only
            # patches of what it outlines belongs to a different look.
            if (bounds and fits(bounds)
                    and probe._last_bounds_pixels >= bounds[2] * bounds[3] * self._MIN_PANEL_FILL):
                return bounds, candidate, probe._last_bounds_pixels

        learned = self._window_learner.learn(self._reference_frame, frame)
        if diag.ENABLED:
            diag.trace("ANALYSIS", f"panel learned from the pre-cast frame: "
                                   f"{learned if learned is None else (learned[0], learned[1], learned[2], learned[3])} "
                                   f"(reference frame {'present' if self._reference_frame is not None else 'MISSING'})",
                       tag=self.diag_tag)
        if learned is not None and fits(learned[0]):
            bounds, lower, upper, pixels = learned
            profile = DetectionProfile(
                name="learned", window_lower=lower, window_upper=upper,
                fish_lower=None, fish_upper=None,
                circle_radius=active.circle_radius, learned=True,
            )
            return bounds, profile, pixels
        return None, None, 0

    def _publish_learned_profile(self):
        """Shares a completed learned profile with the other bots and the GUI."""
        self.detector.profile_updated = False
        profile = self.detector.profile
        self._update_region_cache()  # the click circle may have been located
        if profile.fish_lower is None:
            return  # nothing worth saving yet
        with FishingBot._profile_lock:
            FishingBot._shared_learned_profile = profile
            known = [k for k in FishingBot._known_profiles if k is not profile]
            FishingBot._known_profiles = [profile] + known[:self._MAX_KNOWN_PROFILES - 1]
        where = f", circle r={profile.circle_radius} at {profile.circle_center}" if profile.circle_center else ""
        self._log(f"Learned this client's minigame: fish HSV {profile.fish_lower} - {profile.fish_upper}{where}")
        if self.on_profile_learned:
            try:
                self.on_profile_learned(profile.to_dict())
            except Exception as e:
                diag.exc(tag=self.diag_tag)
                self._log(f"Could not save learned profile: {e}")

    def _discard_learned_profile(self, why: str = "found no fish in 2 games"):
        """Drops a learned profile that does not work (bad calibration)."""
        profile = self.detector.profile
        with FishingBot._profile_lock:
            if FishingBot._shared_learned_profile is profile:
                FishingBot._shared_learned_profile = None
            FishingBot._known_profiles = [k for k in FishingBot._known_profiles if k is not profile]
        saved = DetectionProfile.from_dict(self.config.get('detection_profile'))
        if saved is not None and saved.learned:
            # The saved profile is the one that failed (or an older attempt):
            # forget it, otherwise every session starts with it again.
            saved = None
            self.config['detection_profile'] = None
            if self.on_profile_learned:
                try:
                    self.on_profile_learned(None)
                except Exception as e:
                    diag.exc(tag=self.diag_tag)
                    self._log(f"Could not clear saved profile: {e}")
        if diag.ENABLED:
            diag.log("DECISION", f"discarding learned profile {profile.to_dict()}: {why}; back to "
                                 f"{(saved or DetectionProfile()).name}", tag=self.diag_tag)
            diag.image("learned_profile_discarded", self.capture_full_window(), tag=self.diag_tag, note=why)
        self.detector.set_profile(saved or DetectionProfile())
        self._reset_calibration()
        self._games_without_fish = 0
        self._fish_ever_seen = False
        self._log(f"Learned minigame profile {why} — discarded, recalibrating")

    def _reset_calibration(self):
        """Forgets where the panel is; the next cast looks for it again."""
        self.region = None
        self.region_auto_calibrated = False
        self._pending_bounds = None
        self._update_region_cache()

    def _learn_fish_by_motion(self):
        """The panel is open but the profile's fish color matches nothing:
        this client draws a different fish. Keep the panel, learn the fish."""
        self.detector.learn_fish_by_motion()
        self._games_without_fish = 0
        self._fish_ever_seen = False
        self._update_region_cache()
        self._log("Panel found but no fish with the known colors — learning the fish from its movement")

    def _note_fishless_game(self) -> bool:
        """Counts games in which the panel was open but no fish was ever
        seen (each costs a bait). After ``_NO_FISH_GAMES`` in a row: warn;
        without adaptive detection nothing will change, so stop. Returns
        False after stopping."""
        if self._game_fish_seen:
            self._no_fish_games = 0
            self._no_fish_warned = False
            return True
        self._no_fish_games += 1
        diag.log("DECISION", f"game ended without a fish seen ({self._no_fish_games}/{self._NO_FISH_GAMES})",
                 tag=self.diag_tag)
        if self._no_fish_games < self._NO_FISH_GAMES:
            return True
        if not self._adaptive:
            self._stop_bot(
                self.STOP_FISH_NOT_DETECTED,
                f"[W{self.bot_id+1}] The minigame opened {self._no_fish_games} times but its fish was never "
                f"seen: this client draws it differently. Turn on 'Adapt to re-skinned minigames'. "
                f"Stopping bot.")
            return False
        if not self._no_fish_warned:
            self._no_fish_warned = True
            self._log(f"The fish was not seen in the last {self._no_fish_games} games — each one costs a "
                      f"bait. If this goes on, check the game window is uncovered.")
        return True

    def _abort_stuck_game(self):
        """The 'panel' never closed: what was detected is not the minigame.
        Twice in a row: something on screen keeps being taken for it, and
        each round costs a cast and 90 s — the bot stops instead."""
        self._stuck_games += 1
        if self._stuck_games >= self._MAX_STUCK_GAMES:
            if diag.ENABLED:
                self._diag_negative("stuck_game_stop", self.capture_full_window(),
                                    (0, 0) + tuple(self.window_manager.get_window_rect()[2:]))
            self._stop_bot(
                self.STOP_FALSE_DETECTION,
                f"[W{self.bot_id+1}] Something on screen was taken for the minigame {self._stuck_games} times "
                f"in a row (it never closed). Move the camera away from water/UI of the minigame's color "
                f"and start again. Stopping bot.")
            return
        self._log(f"Minigame still 'open' after {self._MAX_GAME_SECONDS:.0f}s — false detection, recalibrating")
        if diag.ENABLED:
            region = self.region
            rel = (region.left, region.top, region.width, region.height) if region else None
            self._diag_negative("stuck_game", self.capture_full_window(),
                                (0, 0) + tuple(self.window_manager.get_window_rect()[2:]),
                                note=f"'panel' at {rel} never closed")
        self._game_end_time = None
        if self.detector.profile.learned:
            self._discard_learned_profile("matched something that is not the minigame")
        else:
            self._reset_calibration()

    def _scan_existing_inventory(self):
        """Scans inventory for all existing items and adds their positions to ignore list.
        Called at bot start to prevent re-processing items already in inventory.
        Uses iterative minMaxLoc with masking to find ALL distinct items (same logic as identify_item_in_inventory)."""
        templates = self._load_template_cache()
        if not templates:
            diag.log("DECISION", "no item templates loaded: inventory scan (and its empty-slot scan) SKIPPED, "
                                 "the page is left with 0 known empty slots", tag=self.diag_tag)
            return
        
        try:
            # mss captures by absolute screen coordinates and does NOT need the
            # window to be focused. Calling activate_window here (without holding
            # input_lock) would race with the other bot's click sequences and
            # steal focus mid-click, causing missed inputs. Callers that need the
            # window active (page switches, handle_item) activate it under the
            # input_lock before calling this function.
            inventory_frame = self.capture_inventory_area()
            inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
            inv_h, inv_w = inventory_gray.shape

            # Local references for speed
            match_template = cv2.matchTemplate
            minMaxLoc = cv2.minMaxLoc
            TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
            CONFIDENCE_THRESHOLD = 0.80

            found_count = 0
            ignored = self._ignored_positions
            diag_peaks = [] if diag.ENABLED else None
            diag_hits = [] if diag.ENABLED else None

            # Spatial bucket index over `ignored` for O(1) duplicate checks.
            # Without this, every new candidate scans the entire ignore set
            # (which grows monotonically as items are discovered).
            BUCKET = 16
            ignored_buckets: dict = {}
            for ix, iy in ignored:
                ignored_buckets.setdefault((ix // BUCKET, iy // BUCKET), []).append((ix, iy))

            for filename, (template, half_w, half_h) in templates.items():
                t_h, t_w = template.shape

                if t_h > inv_h or t_w > inv_w:
                    continue

                try:
                    result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)

                    # Cheap upfront rejection: if the global max is already below
                    # threshold, this template has no occurrence anywhere.
                    _, peak, _, peak_loc = minMaxLoc(result)
                    if diag_peaks is not None:
                        diag_peaks.append((peak, filename, (peak_loc[0] + half_w, peak_loc[1] + half_h)))
                    if peak < CONFIDENCE_THRESHOLD:
                        continue

                    half_t_w = t_w >> 1
                    half_t_h = t_h >> 1
                    res_w = result.shape[1]
                    res_h = result.shape[0]

                    # Find ALL matches using iterative minMaxLoc with masking.
                    # Disambiguation is skipped here — we only need positions for the
                    # ignore-list, not species identity.
                    while True:
                        _, max_val, _, max_loc = minMaxLoc(result)
                        if max_val < CONFIDENCE_THRESHOLD:
                            break

                        pt_x, pt_y = max_loc
                        center_x = pt_x + half_w
                        center_y = pt_y + half_h

                        bx, by = center_x // BUCKET, center_y // BUCKET
                        is_duplicate = False
                        # A point within 10 px can lie in any of the 3x3
                        # neighbouring buckets (see _scan_empty_slots).
                        for ddx in (-1, 0, 1):
                            for ddy in (-1, 0, 1):
                                cand = ignored_buckets.get((bx + ddx, by + ddy))
                                if not cand:
                                    continue
                                for ix, iy in cand:
                                    if abs(center_x - ix) < 10 and abs(center_y - iy) < 10:
                                        is_duplicate = True
                                        break
                                if is_duplicate:
                                    break
                            if is_duplicate:
                                break

                        if not is_duplicate:
                            ignored.add((center_x, center_y))
                            ignored_buckets.setdefault((bx, by), []).append((center_x, center_y))
                            found_count += 1
                            if diag_hits is not None:
                                diag_hits.append(f"{filename}={max_val:.3f}@({center_x},{center_y})")

                        # Mask out this match area to find the next one
                        mask_x1 = max(0, pt_x - half_t_w)
                        mask_y1 = max(0, pt_y - half_t_h)
                        mask_x2 = min(res_w, pt_x + half_t_w + 1)
                        mask_y2 = min(res_h, pt_y + half_t_h + 1)
                        result[mask_y1:mask_y2, mask_x1:mask_x2] = -1.0

                except Exception:
                    diag.exc("inventory scan template", tag=self.diag_tag)
                    continue

            if diag_peaks is not None:
                diag.log("ANALYSIS", f"existing items on page {self._current_inv_page + 1} (threshold "
                                     f"{CONFIDENCE_THRESHOLD}): {found_count} new positions ignored: {diag_hits}",
                         tag=self.diag_tag)
                diag.log("ANALYSIS", "best score per template: " + ", ".join(
                    f"{n}={v:.3f}@{p}" for v, n, p in sorted(diag_peaks, reverse=True)), tag=self.diag_tag)

            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Inventory scan: found {found_count} existing items (ignoring)")

            # Detect empty slots on current page
            self._empty_slot_positions = self._scan_empty_slots(inventory_frame)
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Found {len(self._empty_slot_positions)} empty inventory slots")

        except Exception as e:
            diag.exc("inventory scan", tag=self.diag_tag)
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Error scanning inventory: {e}")
    
    def _rescan_inventory_state(self):
        """Clears all slot state and rebuilds it from the currently visible inventory page.
        When auto handling is enabled, also processes any open/drop items on the page."""
        self._ignored_positions.clear()
        self._empty_slot_positions.clear()
        if self.config.get('auto_fish_handling', False):
            fish_actions = self.config.get('fish_actions', {})
            self._startup_process_page(fish_actions)
        else:
            self._scan_existing_inventory()

    def _wait_for_inventory_capture(self) -> bool:
        """Waits while the inventory capture shows nothing usable. False
        when the bot was stopped meanwhile (capture_unavailable or manual)."""
        while self.running:
            self.capture_inventory_area()
            if self._last_frame_usable:
                return True
            if not self._check_capture_outage():
                return False
            if not self._sleep_interruptible(0.25):
                return False
        return False

    def _inventory_visible(self) -> Tuple[bool, str]:
        """Is the inventory where it was configured? Yes when the active
        page tab can be read, or the strip shows an empty slot or a known
        item. A closed or dragged inventory shows neither."""
        _, _, win_w, win_h = self.window_manager.get_window_rect()
        points = inventory_layout.tab_points(self.config, win_w, win_h)
        tab_detail = "tabs not readable"
        if len(points) >= 3 and not self._tab_check_off:
            rect = inventory_layout.tabs_rect(points, win_w, win_h)
            frame = self._grab(*rect) if rect else None
            if frame is not None:
                active, tab_detail = inventory_layout.active_tab(frame, rect[:2], points)
                if active is not None:
                    return True, f"tab {active} active ({tab_detail})"
        strip = self.capture_inventory_area()
        slots = self._scan_empty_slots(strip)
        if slots:
            return True, f"{len(slots)} empty slots"
        item = self.identify_item_in_inventory(strip)
        if item:
            return True, f"item {item[0]} at {item[1]}"
        return False, f"no readable tab ({tab_detail}), no empty slot, no known item in the strip {self._inv_rect}"

    def _require_inventory_visible(self, when: str) -> bool:
        """Checks the inventory before clicking its tabs or judging its
        pages. Stops the bot (inventory_not_visible) when it is not there,
        so no click lands on the game world. False after stopping."""
        if not self.running:
            return False
        for attempt in range(2):
            if not self._wait_for_inventory_capture():
                return False
            visible, detail = self._inventory_visible()
            diag.log("DECISION", f"inventory visible {when}: {visible} ({detail})", tag=self.diag_tag)
            if visible:
                return True
            if attempt == 0 and not self._sleep_interruptible(0.4):
                return False   # a tooltip or a passing window: look once more
        if diag.ENABLED:
            self._diag_negative("inventory_not_visible", self.capture_inventory_area(), self._diag_inv_rect)
        self._stop_bot(
            self.STOP_INVENTORY_NOT_VISIBLE,
            f"[W{self.bot_id+1}] The inventory is not where it was configured {when} (closed, moved, or "
            f"no slot or tab recognized). Open it at the configured place, or set the page tabs again. "
            f"Stopping bot.")
        return False

    def _inventory_full_stop(self, when: str = ""):
        self.capture_inventory_area()
        if not self._last_frame_usable:
            # A blank strip has no empty slot in it, but that says nothing
            # about the inventory. Wait for a picture, then read the pages
            # again from the start (the pause/resume path does exactly that).
            diag.log("DECISION", f"'all pages full'{when} rests on a blank inventory capture: "
                                 f"not believed", tag=self.diag_tag)
            self._log("The inventory could not be seen (blank capture) — not judging it full")
            if self._wait_for_inventory_capture():
                self._was_paused = True
                self._full_rescan_wanted = True
            return
        if not self._require_inventory_visible("when judging it full"):
            return
        if diag.ENABLED:
            diag.log("DECISION", f"every configured inventory page reads as full{when}: page coords "
                                 f"{[self.config.get(f'inv_page_{p}_pos') for p in range(1, 9)]}, strip "
                                 f"rect={self._inv_rect} (from {self._inv_rect_source})",
                     tag=self.diag_tag)
            self._diag_negative("inventory_full_stop", self.capture_inventory_area(), self._diag_inv_rect)
        self._stop_bot(self.STOP_INVENTORY_FULL,
                       f"[W{self.bot_id+1}] All inventory pages full{when} — stopping bot")

    def _switch_inventory_page(self):
        """Advances through remaining configured inventory pages until one with empty slots is found.
        If all pages are full, stops the bot."""
        if not self._require_inventory_visible("before switching pages"):
            return
        while self.running:
            next_page = self._current_inv_page + 1
            page_num = next_page + 1  # 1-based

            if page_num > 8:
                break

            page_pos = self.config.get(f'inv_page_{page_num}_pos')
            if not page_pos:
                diag.log("DECISION", f"page {page_num} is not configured: no further page", tag=self.diag_tag)
                break

            self._log(f"Switching to inventory page {page_num}")
            if diag.ENABLED:
                diag.log("DECISION", f"switching from page {self._current_inv_page + 1} to {page_num} at {page_pos}",
                         tag=self.diag_tag)
            if not self._goto_page(page_pos):
                # The tab was not clicked, so the old page is still showing.
                # Try again after the next catch rather than judging it.
                return
            self._current_inv_page = next_page
            self._rescan_inventory_state()

            if self._empty_slot_positions:
                return  # Found a page with space — done

            self._log(f"Page {page_num} also full, trying next...")

        if self.running:
            self._inventory_full_stop()

    def _resume_inventory_check(self):
        """After a pause: the player may have moved items or clicked another
        page tab. Going through every page again (the startup scan) clicks
        all tabs on every resume; showing the page the bot was on and
        re-reading it is enough. A full page moves on as after a catch."""
        if not self._require_inventory_visible("after the pause"):
            return
        page_pos = self.config.get(f'inv_page_{self._current_inv_page + 1}_pos')
        if page_pos and not self._goto_page(page_pos):
            # The tab could not be clicked: read the pages from the start
            # at the next chance rather than judging a page that may not show.
            diag.log("DECISION", "resume: page tab not clicked -> full rescan next time", tag=self.diag_tag)
            self._was_paused = self._full_rescan_wanted = True
            return
        self._rescan_inventory_state()
        if self.running and not self._empty_slot_positions:
            self._switch_inventory_page()

    def _startup_scan_and_process_all_pages(self):
        """At startup, navigate every configured inventory page and find one with empty slots.
        Auto handling on: open/drop processable items on each page before scanning.
        Auto handling off: just scan each page and mark existing items as ignored.
        After scanning all pages, navigates to the first page that has empty slots.
        If every page is full, stops the bot immediately."""
        auto = self.config.get('auto_fish_handling', False)
        fish_actions = self.config.get('fish_actions', {})
        first_page_with_empty = None
        nav_failed = False
        if not self._require_inventory_visible("at startup"):
            return

        for page_idx in range(8):
            if not self._wait_while_paused():
                return
            page_num = page_idx + 1
            page_pos = self.config.get(f'inv_page_{page_num}_pos')
            if page_idx > 0 and not page_pos:
                break  # Hit an unconfigured page — no more pages

            self._log(f"Startup: {'scanning' if auto else 'checking'} inventory page {page_num}")

            if page_pos:
                if not self._goto_page(page_pos):
                    # Cannot switch pages right now. Nothing can be said about
                    # the pages not seen; the first catch re-reads the page.
                    diag.log("DECISION", f"startup: could not click page {page_num}: stopping the page scan "
                                         f"(no 'full' verdict)", tag=self.diag_tag)
                    nav_failed = True
                    break
            else:
                time.sleep(0.3)
            if diag.ENABLED:
                strip = self.capture_inventory_area()
                diag.image(f"startup_page{page_num}_strip", strip, tag=self.diag_tag,
                           note=f"inventory strip rect={self._diag_inv_rect} after clicking page tab {page_pos}")

            self._current_inv_page = page_idx
            self._ignored_positions.clear()
            self._empty_slot_positions.clear()
            if auto:
                # Scan every page and process (open/drop) its items before
                # deciding where to settle.
                self._startup_process_page(fish_actions)
                if self._empty_slot_positions and first_page_with_empty is None:
                    first_page_with_empty = page_idx
                diag.log("DECISION", f"startup page {page_num}: {len(self._empty_slot_positions)} empty slots; "
                                     f"first page with room so far: "
                                     f"{None if first_page_with_empty is None else first_page_with_empty + 1}",
                         tag=self.diag_tag)
            else:
                # Stop on the first page with room — that is where fish go.
                self._scan_existing_inventory()
                diag.log("DECISION", f"startup page {page_num}: {len(self._empty_slot_positions)} empty slots -> "
                                     f"{'settle here' if self._empty_slot_positions else 'try the next page'}",
                         tag=self.diag_tag)
                if self._empty_slot_positions:
                    return

        if not self.running:
            return
        if first_page_with_empty is None:
            diag.log("DECISION", f"startup: no page with an empty slot (nav_failed={nav_failed}, "
                                 f"auto handling={auto})", tag=self.diag_tag)
            if not nav_failed:
                self._inventory_full_stop(" at startup")
            return

        # Navigate back to the first page that has empty slots (if we advanced past it)
        if self._current_inv_page != first_page_with_empty:
            target_page_num = first_page_with_empty + 1
            page_pos = self.config.get(f'inv_page_{target_page_num}_pos')
            if page_pos:
                self._log(f"Startup: returning to page {target_page_num} (first with empty slots)")
                if not self._goto_page(page_pos, settle=0.5):
                    # The last page scanned is still on screen, and it may be
                    # full: watched instead of the page catches land on, the
                    # first catch would read as "all pages full". The pages
                    # are read again from the start before the first cast.
                    diag.log("DECISION", f"startup: could not go back to page {target_page_num}: full rescan "
                                         f"before casting", tag=self.diag_tag)
                    self._full_rescan_wanted = True
                    return
                self._current_inv_page = first_page_with_empty
                self._ignored_positions.clear()
                self._empty_slot_positions.clear()
                self._startup_process_page(fish_actions)
                # The page turned out to be full after all (the tab click did
                # not register, or items landed there meanwhile): find the
                # next page with room rather than starting with none tracked.
                if not self._empty_slot_positions and self.running:
                    self._log(f"Startup: page {target_page_num} now full — scanning for next available page")
                    self._switch_inventory_page()

    def _startup_process_page(self, fish_actions: dict) -> None:
        """Processes a single inventory page during startup.
        Pass 1: scan all items — 'keep' items go into _ignored_positions.
        Pass 2: loop identify_item_in_inventory (which skips ignored) and call
                _handle_item for each open/drop item until none remain.
        Finally rescans empty slots."""
        templates = self._load_template_cache()
        if not templates:
            diag.log("DECISION", "no item templates loaded: page processing (and its empty-slot scan) SKIPPED",
                     tag=self.diag_tag)
            return

        try:
            inventory_frame = self.capture_inventory_area()
            inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
            inv_h, inv_w = inventory_gray.shape

            match_template = cv2.matchTemplate
            minMaxLoc = cv2.minMaxLoc
            TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
            CONFIDENCE_THRESHOLD = 0.80
            confusable_fish = FishingBot._confusable_fish
            keep_count = 0
            diag_peaks = [] if diag.ENABLED else None

            # Pass 1: collect every template hit, cluster by position with a
            # coarse spatial bucket index, then for each cluster pick the
            # highest-confidence template as the slot's true identity.
            # The bucket index turns the previous O(N*M) merge (every hit linearly
            # scanning every accumulated slot) into amortized O(N).
            #
            # Without this clustering step, a weakly-matching 'keep' template can
            # win over the actually-correct 'drop' template at the same slot
            # purely because of dict iteration order, causing the slot to be
            # ignored.
            BUCKET = 16  # > merge radius (10): a neighbour within it is in the 3x3 buckets around
            slot_best = []                  # list of [cx, cy, best_filename, best_conf]
            bucket_index: dict = {}         # (bx, by) -> list of slot indices

            for filename, (template, half_w, half_h) in templates.items():
                t_h, t_w = template.shape
                if t_h > inv_h or t_w > inv_w:
                    continue
                try:
                    result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)

                    # Cheap upfront rejection: if the global peak is already
                    # below threshold, this template hits nowhere.
                    _, peak, _, peak_loc = minMaxLoc(result)
                    if diag_peaks is not None:
                        diag_peaks.append((peak, filename, (peak_loc[0] + half_w, peak_loc[1] + half_h)))
                    if peak < CONFIDENCE_THRESHOLD:
                        continue

                    half_t_w = t_w >> 1
                    half_t_h = t_h >> 1
                    res_w = result.shape[1]
                    res_h = result.shape[0]

                    while True:
                        _, max_val, _, max_loc = minMaxLoc(result)
                        if max_val < CONFIDENCE_THRESHOLD:
                            break
                        pt_x, pt_y = max_loc
                        cx = pt_x + half_w
                        cy = pt_y + half_h

                        # Probe the 3x3 buckets around (cx, cy): a slot within
                        # radius 10 can lie on either side of a bucket edge.
                        # Probing only the left/upper ones left a hit 1-2 px
                        # up-left of an earlier one unmerged, and a weaker
                        # 'keep' hit then shadowed the slot's real 'drop' item.
                        bx, by = cx // BUCKET, cy // BUCKET
                        merged = False
                        for ddx in (-1, 0, 1):
                            for ddy in (-1, 0, 1):
                                cand_list = bucket_index.get((bx + ddx, by + ddy))
                                if not cand_list:
                                    continue
                                for si in cand_list:
                                    slot = slot_best[si]
                                    if abs(cx - slot[0]) < 10 and abs(cy - slot[1]) < 10:
                                        if max_val > slot[3]:
                                            slot[2] = filename
                                            slot[3] = max_val
                                        merged = True
                                        break
                                if merged:
                                    break
                            if merged:
                                break

                        if not merged:
                            slot_best.append([cx, cy, filename, max_val])
                            bucket_index.setdefault((bx, by), []).append(len(slot_best) - 1)

                        mask_x1 = max(0, pt_x - half_t_w)
                        mask_y1 = max(0, pt_y - half_t_h)
                        mask_x2 = min(res_w, pt_x + half_t_w + 1)
                        mask_y2 = min(res_h, pt_y + half_t_h + 1)
                        result[mask_y1:mask_y2, mask_x1:mask_x2] = -1.0
                except Exception:
                    diag.exc("startup page template", tag=self.diag_tag)
                    continue

            if diag_peaks is not None:
                diag.log("ANALYSIS", f"startup page {self._current_inv_page + 1}: {len(slot_best)} occupied slots "
                                     f"(threshold {CONFIDENCE_THRESHOLD}): " + ", ".join(
                                         f"{f}={c:.3f}@({x},{y})" for x, y, f, c in slot_best), tag=self.diag_tag)
                diag.log("ANALYSIS", "best score per template: " + ", ".join(
                    f"{n}={v:.3f}@{p}" for v, n, p in sorted(diag_peaks, reverse=True)), tag=self.diag_tag)

            # Now classify each slot by its best-matching template's action.
            ignored_snapshot = list(self._ignored_positions)
            for cx, cy, best_filename, _ in slot_best:
                already_ignored = False
                for ix, iy in ignored_snapshot:
                    if abs(cx - ix) < 10 and abs(cy - iy) < 10:
                        already_ignored = True
                        break
                if already_ignored:
                    continue
                matched_filename = best_filename
                if best_filename in confusable_fish:
                    matched_filename = self._disambiguate_confusable_fish(
                        inventory_frame, cx, cy, best_filename)
                diag.log("DECISION", f"slot ({cx},{cy}) {matched_filename}: "
                                     f"{fish_actions.get(matched_filename, 'keep')}", tag=self.diag_tag)
                if fish_actions.get(matched_filename, 'keep') == 'keep':
                    self._ignored_positions.add((cx, cy))
                    keep_count += 1

            if self.on_status_update and keep_count:
                self.on_status_update(f"[W{self.bot_id+1}] Startup: {keep_count} 'keep' items (ignored)")

            # Pass 2: open/drop everything else (identify_item skips _ignored_positions)
            processed_count = 0
            max_items = 50  # Safety cap against infinite loops
            while processed_count < max_items and self._wait_while_paused():
                inventory_frame = self.capture_inventory_area()
                match = self.identify_item_in_inventory(inventory_frame, ignore_positions=self._ignored_positions)
                if not match:
                    break
                filename, (inv_x, inv_y) = match
                action = fish_actions.get(filename, 'keep')
                if action == 'keep':
                    self._ignored_positions.add((inv_x, inv_y))
                    continue
                if not self._handle_item(filename, inv_x, inv_y, action, label="Startup: "):
                    if self.paused and self.running:
                        continue   # paused before the click: wait it out, then look again
                    break   # no input possible right now; the item is handled later
                processed_count += 1

            if self.on_status_update and processed_count:
                self.on_status_update(f"[W{self.bot_id+1}] Startup: processed {processed_count} open/drop items")

            # Final empty-slot scan after all items have been handled
            inventory_frame = self.capture_inventory_area()
            self._empty_slot_positions = self._scan_empty_slots(inventory_frame)
            if self.on_status_update:
                self.on_status_update(
                    f"[W{self.bot_id+1}] Startup: {len(self._empty_slot_positions)} empty slots on this page")

        except Exception as e:
            diag.exc("processing inventory page", tag=self.diag_tag)
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Error processing inventory page: {e}")

    def _load_classic_fish_template(self):
        """Loads the classic_fish.jpg template for classic fishing mode."""
        if FishingBot._classic_fish_template is not None:
            return FishingBot._classic_fish_template
        
        template_path = get_resource_path("classic_fish.jpg")
        if not os.path.exists(template_path):
            # Try .png extension
            template_path = get_resource_path("classic_fish.png")
        
        if os.path.exists(template_path):
            try:
                template = cv2.imread(template_path)
                if template is not None:
                    FishingBot._classic_fish_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Loaded classic fish template")
            except Exception as e:
                diag.exc(tag=self.diag_tag)
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error loading classic fish template: {e}")
        else:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Classic fish template not found at assets/fishing/classic_fish.jpg")
        
        return FishingBot._classic_fish_template
    
    def wait_for_classic_fish(self, timeout: float = 10.0) -> bool:
        """Waits for the classic fish image to appear in the game window.
        Returns True if found, False if timeout. A pause ends the wait
        (``_wait_interrupted``), as in wait_for_minigame_window."""
        self._wait_interrupted = False
        template = self._load_classic_fish_template()
        if template is None:
            # Without the bite picture there is no bite to wait for. Going on
            # anyway reeled in at once after every cast and counted each one
            # as a catch (bait counter running down, misses never noticed).
            self._stop_bot(
                self.STOP_ERROR,
                f"[W{self.bot_id+1}] Classic fishing needs assets/fishing/classic_fish.jpg, which could not "
                f"be loaded. Reinstall the bot. Stopping bot.")
            return False
        
        start_time = time.perf_counter()
        t_h, t_w = template.shape

        # Build (and class-cache) scaled variants once — shared across all calls
        # and all bot instances. Previously rebuilt on every wait_for_classic_fish
        # invocation, allocating ~20 resize buffers per cast.
        if FishingBot._classic_fish_scaled_cache is None:
            _scales_raw = [0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8, 0.9,
                           1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.25, 2.5, 2.75, 3.0]
            _scaled = []
            for _s in _scales_raw:
                _nw, _nh = int(t_w * _s), int(t_h * _s)
                if _nw >= 10 and _nh >= 10:
                    _interp = cv2.INTER_AREA if _s < 1 else cv2.INTER_LINEAR
                    _scaled.append((_s, _nw, _nh, cv2.resize(template, (_nw, _nh), interpolation=_interp)))
            FishingBot._classic_fish_scaled_cache = _scaled

        scaled_templates = FishingBot._classic_fish_scaled_cache

        # Promote the last-known-good scale to the front so subsequent polls
        # (and subsequent casts) early-exit on the very first matchTemplate.
        last_scale = FishingBot._classic_fish_last_scale
        if last_scale is not None:
            scaled_templates = sorted(
                scaled_templates,
                key=lambda st: 0 if st[0] == last_scale else 1,
            )

        # Local refs for tight inner loop
        match_template = cv2.matchTemplate
        minMaxLoc = cv2.minMaxLoc
        TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
        cvtColor = cv2.cvtColor
        BGR2GRAY = cv2.COLOR_BGR2GRAY
        ACCEPT_THRESHOLD = 0.7  # Lowered early-exit to match acceptance threshold

        capture_region = self.capture_window_region
        get_rect = self.window_manager.get_window_rect
        perf_counter = time.perf_counter

        frame = None
        with diag.loop("wait for classic fish", tag=self.diag_tag, timeout=timeout,
                       last_scale=last_scale) as lp:
            while self.running and perf_counter() - start_time < timeout:
                if self.paused:
                    # What happens while the player has the controls says
                    # nothing about this cast: not a missed bite.
                    self._wait_interrupted = True
                    lp.set("result", "paused")
                    return False

                try:
                    # Grab only the 250px-wide centered bar of the upper half —
                    # the indicator never appears elsewhere, and capturing the
                    # whole window just to crop it wasted most of the grab time.
                    _, _, win_w, win_h = get_rect()
                    crop_left = max(0, (win_w >> 1) - 125)
                    crop_w = min(250, win_w - crop_left)
                    poll_start = perf_counter()
                    frame = capture_region(crop_left, 0, crop_w, win_h >> 1)
                    if not self._last_frame_usable:
                        # Nothing to see: not a "no bite" either. The time
                        # does not count towards the timeout.
                        lp.count("unusable_frames")
                        if not self._check_capture_outage():
                            lp.set("result", "capture unavailable")
                            return False
                        time.sleep(0.05)
                        start_time += perf_counter() - poll_start
                        continue
                    frame_gray = cvtColor(frame, BGR2GRAY)

                    f_h, f_w = frame_gray.shape

                    # Multi-scale template matching (uses pre-computed scaled templates)
                    best_match_val = 0.0
                    best_scale = 1.0

                    for scale, new_w, new_h, scaled_template in scaled_templates:
                        # Skip if scaled template is larger than current cropped frame
                        if new_h > f_h or new_w > f_w:
                            continue

                        result = match_template(frame_gray, scaled_template, TM_CCOEFF_NORMED)
                        _, max_val, _, _ = minMaxLoc(result)

                        if max_val > best_match_val:
                            best_match_val = max_val
                            best_scale = scale

                        # Early exit at acceptance threshold — no need to keep
                        # exploring scales once we have a winning match.
                        if max_val >= ACCEPT_THRESHOLD:
                            break

                    lp.score("best", best_match_val)
                    lp.frame(f"classic fish best={best_match_val:.3f} at scale {best_scale} "
                             f"(accept {ACCEPT_THRESHOLD})")
                    if best_match_val >= ACCEPT_THRESHOLD:  # Found the classic fish indicator
                        lp.set("result", f"found best={best_match_val:.3f} scale={best_scale}")
                        diag.first("classic_fish", frame, tag=self.diag_tag,
                                   note=f"score {best_match_val:.3f} scale {best_scale}")
                        diag.log("DECISION", f"classic fish found (score {best_match_val:.3f} >= "
                                             f"{ACCEPT_THRESHOLD} at scale {best_scale}): waiting "
                                             f"classic_fishing_delay {self.config.get('classic_fishing_delay', 3.0)}s "
                                             f"before reeling in", tag=self.diag_tag)
                        # Persist the winning scale for the next poll/cast
                        FishingBot._classic_fish_last_scale = best_scale
                        # Start timer IMMEDIATELY after detection (configurable delay)
                        delay = self.config.get('classic_fishing_delay', 3.0)
                        # Use interruptible sleep that checks running/paused state
                        delay_start = perf_counter()   # monotonic: a clock change must not skew it
                        while perf_counter() - delay_start < (delay - 0.05):
                            if not self.running:
                                return False  # Bot stopped during delay
                            if self.paused:
                                time.sleep(0.1)
                                delay_start = perf_counter()  # Reset delay when paused
                                continue
                            time.sleep(0.05)  # Small sleep increments
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] Classic fish detected (confidence: {best_match_val:.2f}, scale: {best_scale:.1f}x, delay: {delay}s)")
                        return True
                
                    time.sleep(0.02)  # Fast polling
                except Exception as e:
                    diag.exc("classic fish detection", tag=self.diag_tag)
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Error detecting classic fish: {e}")
                    time.sleep(0.1)
        
            lp.set("result", "timeout" if self.running else "stopped")
        if diag.ENABLED and self.running and frame is not None:
            self._diag_negative("classic_fish_not_found", frame, None,
                                note="centered 250 px bar of the upper half, last frame of the wait")
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Classic fish detection timeout")
        return False
    
    def _wait_while_paused(self) -> bool:
        """Holds still while paused (between steps of a longer job, so F5
        takes effect before the next click). False when stopped."""
        while self.paused and self.running:
            self._was_paused = True
            time.sleep(0.1)
        return self.running

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleeps unless the bot is stopped meanwhile (False). Time spent
        paused does not count."""
        end = time.perf_counter() + seconds
        while self.running and time.perf_counter() < end:
            if self.paused:
                self._was_paused = True
                time.sleep(0.1)
                end += 0.1
                continue
            time.sleep(min(0.05, max(0.0, end - time.perf_counter())))
        return self.running

    def _game_screen_still_there(self) -> bool:
        """Misses in a row: is the game still showing the fishing screen?
        A disconnect, death or login screen also ends every bite wait, and
        counted as misses it would write off bait tier after tier and end in
        a false "bait depleted". The inventory must be open while fishing,
        so its absence (looked at twice) means the game screen is gone.
        Stops the bot and returns False then."""
        try:
            for attempt in range(2):
                self.capture_inventory_area()
                if not self._last_frame_usable:
                    return True   # nothing to judge on (blank capture is handled elsewhere)
                visible, detail = self._inventory_visible()
                if visible:
                    return True
                diag.log("DECISION", f"miss {self.consecutive_failures}: inventory not visible ({detail})"
                                     f"{', looking again' if attempt == 0 else ''}", tag=self.diag_tag)
                if attempt == 0 and not self._sleep_interruptible(0.4):
                    return True
        except Exception:
            diag.exc("game screen check", tag=self.diag_tag)
            return True
        if diag.ENABLED:
            self._diag_negative("game_screen_lost", self.capture_full_window(),
                                (0, 0) + tuple(self.window_manager.get_window_rect()[2:]))
        self._stop_bot(
            self.STOP_GAME_SCREEN_LOST,
            f"[W{self.bot_id+1}] No bite and the inventory is gone from the screen — disconnected, dead, "
            f"or on the login screen? Bait was not written off. Stopping bot.")
        return False

    def _on_missed_bite(self, cast_wait: Optional[float], limit: int, what: str) -> bool:
        """A cast was not followed by a bite. After ``limit`` in a row the
        bait key is taken to be empty. Returns False when that used up the
        last bait and the bot stopped."""
        if self._capture_bad_since is not None:
            # The window could not be seen at the end of the wait: no
            # evidence about the bite (or the bait) either way — not for the
            # miss counter and not for the recast-wait tuner either.
            diag.log("DECISION", "missed bite while the capture is blank: not counted", tag=self.diag_tag)
            return True
        self._report_cast(cast_wait, False)
        self.consecutive_failures += 1
        if self.consecutive_failures >= 2 and not self._game_screen_still_there():
            return False
        self._log(f"{what} ({self.consecutive_failures}/{limit})")
        diag.log("DECISION", f"missed bite {self.consecutive_failures}/{limit}: "
                             f"{'cast again' if self.consecutive_failures < limit else 'bait key assumed empty'}",
                 tag=self.diag_tag)
        if self.consecutive_failures < limit:
            return True
        if not self._bite_ever_seen:
            # Nothing has ever bitten in this session: there is no telling
            # whether the bait key is empty or the bite is just not seen.
            # Writing off a bait tier on that would end in a false "bait
            # depleted"; the player has to look.
            classic = self.config.get('classic_fishing', False)
            hint = ("If the fish icon does appear: keep the game window uncovered and the camera as "
                    "usual." if classic else
                    "If the minigame does open on screen: keep the game window uncovered, make sure "
                    "'Adapt to re-skinned minigames' is on, or use Classic fishing if this server has "
                    "no minigame.")
            self._stop_bot(
                self.STOP_NO_BITE,
                f"[W{self.bot_id+1}] {self.consecutive_failures} casts and no "
                f"{'bite' if classic else 'minigame'} seen in this session. Is there bait on key "
                f"{self.get_bait_key(self.bait_counter)}? {hint} Stopping bot.")
            return False
        self.adjust_bait_tier()
        if self.bait_counter <= 0:
            self._stop_bot(
                self.STOP_BAIT_DEPLETED,
                f"[W{self.bot_id+1}] Bait depleted after consecutive failures. Stopping bot."
            )
            return False
        return True

    def _play_minigame(self) -> Tuple[bool, int]:
        """Plays one open minigame until its panel closes.
        Returns (stuck, hits): ``stuck`` when the "panel" never closed, i.e.
        what was detected is not the minigame."""
        human_like = self.human.enabled
        perf_counter = time.perf_counter
        detect_interval = self._t_detect
        gone_frames = 0
        game_hits = 0
        game_start = perf_counter()
        self._fish_track.clear()
        self._game_fish_seen = False
        relearned = False
        last_partial_check = 0.0

        with diag.loop("minigame", tag=self.diag_tag, region=self.region, circle=self._circle_center,
                       radius=round(self._circle_radius_sq ** 0.5), profile=self.detector.profile.name,
                       human_like=human_like) as lp:
            while self.running:
                if self.paused:
                    self._was_paused = True
                    time.sleep(0.1)
                    game_start += 0.1  # paused time does not count
                    continue

                loop_start = perf_counter()
                game_time = loop_start - game_start
                if game_time > self._MAX_GAME_SECONDS:
                    lp.set("result", f"STUCK: still open after {self._MAX_GAME_SECONDS:.0f}s")
                    return True, game_hits
                if self._adaptive and not relearned and not self._game_fish_seen and not self.detector.learning:
                    # The panel is open and no fish is found. Stock profile that
                    # never found one: this client draws another fish. A profile
                    # that did find fish before: the fish looks different now.
                    # Either way the fish is the thing that moves.
                    patience = self._FISH_LOST_AFTER if self._fish_ever_seen else self._FISH_MISSING_AFTER
                    if game_time > patience:
                        relearned = True
                        lp.note(f"no fish found for {game_time:.1f}s (patience {patience}s, fish seen before "
                                f"this session: {self._fish_ever_seen}): learning the fish by motion", "DECISION")
                        if diag.ENABLED:
                            diag.image("no_fish_in_panel", self.capture_screen(), tag=self.diag_tag,
                                       note=f"region {self.region} profile {self.detector.profile.name}")
                        self._learn_fish_by_motion()
                try:
                    # Human-like mode delays the *reaction* to a fish in
                    # the circle; detection keeps running at full rate.
                    reaction = np.random.uniform(self._t_human_mn, self._t_human_mx) if human_like else 0.0
                    window_active, fish_pos = self.atomic_capture_and_click(reaction)
                except Exception as e:
                    diag.exc("minigame frame", tag=self.diag_tag)
                    self._log(f"Error: {e}")
                    time.sleep(0.05)
                    continue

                if not window_active and not self._last_frame_usable:
                    # A black / failed capture is not "the panel closed":
                    # the game goes on where it cannot be seen. Not counted
                    # as a gone frame, and the time does not count as game time.
                    lp.count("unusable_frames")
                    if not self._check_capture_outage():
                        lp.set("result", "capture unavailable")
                        return False, game_hits
                    time.sleep(self._END_CONFIRM_GAP)
                    game_start += perf_counter() - loop_start
                    continue

                if not window_active:
                    # One missing frame is not the end of the game: a
                    # tooltip, a capture hiccup or another window passing
                    # over would otherwise trigger a recast mid-game.
                    gone_frames += 1
                    lp.count("gone_frames")
                    if gone_frames < self._END_CONFIRM_FRAMES:
                        time.sleep(self._END_CONFIRM_GAP)
                        continue
                    # Gone from where it was — or dragged somewhere else?
                    if self._panel_elsewhere():
                        lp.note(f"panel moved: now {self.region}", "STATE")
                        gone_frames = 0
                        self._fish_track.clear()
                        continue
                    if not self._last_frame_usable:
                        # The whole-window look came back blank: no proof
                        # that the game is over. Look again.
                        lp.note("window look for a moved panel came back blank: not ending the game", "STATE")
                        gone_frames = 0
                        continue

                    self._game_end_time = perf_counter()
                    self.total_games += 1
                    self.bait_counter -= 1

                    lp.set("result", f"game over after {game_time:.2f}s, hits={game_hits}, "
                                     f"fish seen={self._game_fish_seen}")
                    self._log(f"Game finished. Total: {self.total_games}, Bait: {self.bait_counter}")
                    if self.on_bait_update:
                        self.on_bait_update(self.bot_id, self.bait_counter)
                    if self.on_stats_update:
                        self.on_stats_update(self.bot_id, self.hits, self.total_games, self.bait_counter)

                    # Handle caught item (if auto fish handling is enabled)
                    self.handle_caught_item(may_have_catch=game_hits > 0)
                    break

                gone_frames = 0
                if self.detector.partly_visible and loop_start - last_partial_check >= self._PARTIAL_CHECK_GAP:
                    # Open, but part of it has left the region: dragged?
                    last_partial_check = loop_start
                    before = self._panel_moves
                    lp.count("partial_checks")
                    if self._panel_elsewhere() and self._panel_moves != before:
                        lp.note(f"panel dragged: now {self.region}", "STATE")
                        self._fish_track.clear()
                        continue
                if fish_pos:
                    lp.count("hits")
                    game_hits += 1
                    self.hits += 1
                    if self.on_stats_update:
                        self.on_stats_update(self.bot_id, self.hits, self.total_games, self.bait_counter)

                if self.detector.profile_updated:
                    lp.note("detector learned a profile update: publishing", "STATE")
                    self._publish_learned_profile()

                # Pace the loop: the game renders at ~60 fps, so polling
                # flat-out only burns a CPU core the game clients need.
                spare = detect_interval - (perf_counter() - loop_start)
                if spare > 0:
                    time.sleep(spare)

            lp.set("result", "bot stopped")
        return False, game_hits

    def play_game(self):
        """Main game loop implementing the fishing minigame workflow."""
        # Known to the other bots while it runs (overlapping-window check).
        screen_capture.register_active(self.capture)
        try:
            self._play_game()
        finally:
            screen_capture.unregister_active(self.capture)

    def _play_game(self):
        # Refresh timing cache from config once per session — never read inside loops
        self._load_timings()

        requested_backend = str(self.config.get('input_backend', 'auto')).lower()
        if requested_backend == 'interception' and getattr(self.mouse_backend, 'name', '') != 'interception':
            self._log("Interception requested but unavailable; using PyAutoGUI")
        if diag.ENABLED:
            self._diag_bot_header(requested_backend)

        # Reset bait if starting with 0 or negative bait
        bait_per_key = self.config.get("bait_quantity", 200)
        max_bait = len(self.bait_keys) * bait_per_key
        if self.bait_counter <= 0:
            self.bait_counter = max_bait
            if self.on_bait_update:
                self.on_bait_update(self.bot_id, self.bait_counter)
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Bait counter was 0! Reset to {max_bait}.")
        
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Bot started! Bait: {self.bait_counter}")

        if self._stop_if_window_unavailable():
            return
        
        # Prove per-window capture while this window is in front, so covered
        # windows can be read from then on.
        with input_lock:
            self.window_manager.activate_window()
            self._verify_capture()
            overlap_ok = self._refuse_overlapping_windows()
            if diag.ENABLED and overlap_ok:
                self._diag_startup_images()
        if not overlap_ok:
            return

        # Scan all inventory pages at startup, process open/drop items, then settle on
        # the first page that still has empty slots.
        self._startup_scan_and_process_all_pages()

        # A pause during the startup scan needs no re-read afterwards; a scan
        # that could not settle on its page does (see _full_rescan_wanted).
        self._was_paused = self._full_rescan_wanted

        while self.running and self.bait_counter > 0:
            if self._stop_if_window_unavailable():
                break

            if self.paused:
                if not self._diag_paused:
                    self._diag_paused = True
                    diag.log("STATE", "paused", tag=self.diag_tag)
                self._was_paused = True
                time.sleep(0.1)
                continue
            if self._diag_paused:
                self._diag_paused = False
                diag.log("STATE", "resumed", tag=self.diag_tag)

            if self._was_paused:
                self._was_paused = False
                self._last_cast_wait = None
                self._game_end_time = None
                self._mount_toggled = False   # the player had the controls: mount state unknown
                if self._full_rescan_wanted:
                    self._full_rescan_wanted = False
                    self._log("Re-scanning inventory...")
                    self._startup_scan_and_process_all_pages()
                else:
                    self._log("Resumed — re-reading the current inventory page...")
                    self._resume_inventory_check()
                if not self.running:
                    break

            try:
                classic = self.config.get('classic_fishing', False)

                if self._capture_bad_since is not None:
                    # The last look at the window showed nothing. Casting now
                    # would spend bait on a game that cannot be seen; look
                    # again first and wait while the window stays blank.
                    self.capture_full_window()
                    if not self._last_frame_usable:
                        if not self._check_capture_outage():
                            break
                        self._sleep_interruptible(0.25)
                        continue

                # Self-calibration needs to know what the window looked like
                # before the minigame opened: one full grab per cast.
                if not classic and self._adaptive:
                    self._reference_frame = self.capture_full_window()
                if diag.ENABLED:
                    diag.log("DECISION", f"--- cast #{self.total_games + 1}: bait {self.bait_counter}, "
                                         f"failures {self.consecutive_failures}, classic={classic}, "
                                         f"bite timeout {self._bite_timeout():.1f}s ---", tag=self.diag_tag)

                if self.bait_and_cast() is False:
                    # Nothing was sent (the window could not be brought to the
                    # front, or the keys could not be sent): not a cast, so not
                    # a missed bite either.
                    self._last_cast_wait = None
                    if self.paused:
                        continue   # not a focus failure: the pause is handled at the top of the loop
                    if self._cast_error is not None:
                        self._cast_errors += 1
                        if self._cast_errors >= self._CAST_ERROR_LIMIT:
                            self._stop_bot(
                                self.STOP_KEYBOARD_ERROR,
                                f"[W{self.bot_id+1}] The cast keys could not be sent "
                                f"({self._cast_error}). Stopping bot.")
                            break
                        self._sleep_interruptible(0.5)
                        continue
                    now = time.perf_counter()
                    if self._focus_fail_since is None:
                        self._focus_fail_since = now
                    elif now - self._focus_fail_since >= self._FOCUS_GIVE_UP:
                        self._stop_bot(
                            self.STOP_WINDOW_UNAVAILABLE,
                            f"[W{self.bot_id+1}] The game window could not be brought to the front for "
                            f"{self._FOCUS_GIVE_UP:.0f} s. Stopping bot.")
                        break
                    self._sleep_interruptible(0.5)
                    continue
                self._focus_fail_since = None
                self._cast_errors = 0
                cast_time = time.perf_counter()
                cast_wait, self._last_cast_wait = self._last_cast_wait, None
                if (cast_wait is not None and self._game_end_time is not None
                        and cast_time - self._game_end_time > cast_wait + self._RECAST_SLACK):
                    # Cast much later than planned (item handling, another
                    # window had the mouse): its result says nothing about
                    # whether the planned wait is long enough.
                    cast_wait = None

                if not classic:
                    minigame_detected = self.wait_for_minigame_window(timeout=self._bite_timeout())
                    if not self.running:
                        break
                    if self._wait_interrupted:
                        continue   # paused: handled at the top of the loop
                    if not minigame_detected:
                        if not self._on_missed_bite(cast_wait, self._MAX_BITE_FAILURES, "Minigame not detected"):
                            break
                        if (self.consecutive_failures >= 2 and self._adaptive
                                and self.region_auto_calibrated):
                            # Misses in a row: maybe the panel is not what it
                            # used to be. Look for it from scratch.
                            diag.log("DECISION", f"{self.consecutive_failures} misses in a row: forgetting the "
                                                 f"calibration", tag=self.diag_tag)
                            self._reset_calibration()
                        continue

                    # Reset failure counter on successful minigame detection
                    self.consecutive_failures = 0
                    self._minigame_ever_seen = self._bite_ever_seen = True
                    self._report_cast(cast_wait, True)
                    self._longest_bite = max(self._longest_bite, time.perf_counter() - cast_time)

                    stuck, game_hits = self._play_minigame()
                    self.hits = 0
                    if stuck:
                        self._abort_stuck_game()
                        continue
                    self._stuck_games = 0
                    if self.running and not self._note_fishless_game():
                        break
                    if self.detector.profile.learned and self.running:
                        self._games_without_fish = 0 if game_hits else self._games_without_fish + 1
                        diag.log("DECISION", f"learned profile: games without fish = {self._games_without_fish}/2",
                                 tag=self.diag_tag)
                        if self._games_without_fish >= 2:
                            self._discard_learned_profile()
                else:
                    # Classic Fishing system - wait for fish indicator, then reel in
                    fish_found = self.wait_for_classic_fish(timeout=40)
                    if not self.running:
                        break
                    if self._wait_interrupted:
                        continue   # paused: handled at the top of the loop

                    if not fish_found:
                        mount_key = self.config.get('mount_key', 'g')
                        if self._mount_toggled:
                            # The CTRL+G after the last miss may have *mounted* the
                            # character (it toggles), and no cast works while
                            # mounted: this miss may be the bot's own doing, so it
                            # is not counted towards writing off the bait key.
                            # CTRL+G again restores the state before that toggle.
                            self._mount_toggled = False
                            diag.log("DECISION", "classic miss right after the bot's own CTRL+G: not counted, "
                                                 "toggling the mount back", tag=self.diag_tag)
                            self._log("No bite after CTRL+G — pressing CTRL+G again (miss not counted)")
                            self.press_ctrl_key(mount_key)
                            time.sleep(0.15)
                            continue
                        classic_limit = max(1, int(self.config.get('classic_miss_limit', 3)))
                        if not self._on_missed_bite(cast_wait, classic_limit, "No fish bite detected"):
                            break
                        # CTRL+G toggles the mount: it dismounts when the miss
                        # was caused by riding. When the character was on foot
                        # it mounts instead, and the next miss is handled above.
                        self._log("Pressing CTRL+G to dismount horse...")
                        self._mount_toggled = self.press_ctrl_key(mount_key)
                        time.sleep(0.15)
                        continue

                    self._mount_toggled = False

                    # Reset failure counter on successful fish detection
                    self.consecutive_failures = 0
                    self._bite_ever_seen = True
                    self._report_cast(cast_wait, True)

                    # Handle pause before reeling in
                    while self.paused and self.running:
                        self._was_paused = True
                        time.sleep(0.1)
                    if not self.running:
                        break

                    # Timer already elapsed in wait_for_classic_fish - press space to reel in
                    self.press_key(self.config.get('cast_key', 'space'), "Reel in fish")
                    self._game_end_time = time.perf_counter()
                    time.sleep(0.05)

                    self.total_games += 1
                    self.bait_counter -= 1
                    self._log(f"Classic catch! Total: {self.total_games}, Bait: {self.bait_counter}")
                    if self.on_bait_update:
                        self.on_bait_update(self.bot_id, self.bait_counter)
                    if self.on_stats_update:
                        self.on_stats_update(self.bot_id, self.hits, self.total_games, self.bait_counter)

                    # Handle caught item (if auto fish handling is enabled)
                    self.handle_caught_item()

                # Quick skip or wait before the next cast. Nothing is sent
                # once the bot was stopped (manually, inventory full, ...).
                if not self.running or self.bait_counter <= 0:
                    continue
                if self.config.get('quick_skip', False):
                    if classic and not self._sleep_interruptible(0.5):
                        break
                    self.quickskip()
                elif self._wait_before_recast():
                    self._was_paused = True

            except Exception as e:
                diag.exc("play_game loop", tag=self.diag_tag)
                self._log(f"Error in play_game: {e}")
                now = time.perf_counter()
                self._loop_errors = [t for t in self._loop_errors if now - t < self._LOOP_ERROR_WINDOW]
                self._loop_errors.append(now)
                if len(self._loop_errors) >= self._LOOP_ERROR_LIMIT:
                    # The same failure over and over: retrying forever only
                    # fills the log. Stop with a reason the player can see.
                    self._stop_bot(
                        self.STOP_REPEATED_ERROR,
                        f"[W{self.bot_id+1}] {len(self._loop_errors)} errors within "
                        f"{self._LOOP_ERROR_WINDOW:.0f} s (last: {e}). Stopping bot.")
                    break
                time.sleep(0.5)

        if self.stop_reason is None:
            if self._manually_stopped:
                self._set_stop_reason(self.STOP_MANUAL)
            elif self.bait_counter <= 0:
                self._set_stop_reason(self.STOP_BAIT_DEPLETED)
            else:
                self._set_stop_reason(self.STOP_ERROR)

        diag.log("DECISION", f"bot loop ended: stop_reason={self.stop_reason} message={self.stop_message!r} "
                             f"games={self.total_games} bait={self.bait_counter} manual={self._manually_stopped}",
                 tag=self.diag_tag)
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Bot finished! Total games: {self.total_games}")
        self.running = False
        self._notify_bot_stop_once()

    def _diag_bot_header(self, requested_backend: str) -> None:
        """Start-of-bot block: what this bot actually uses (backends, capture,
        timings, coordinates), also written to config_effective.json."""
        try:
            cfg = self.config
            capture = self.capture
            timings = {k: getattr(self, k) for k in sorted(vars(self)) if k.startswith("_t_")}
            effective = {
                "window_rect": self.window_manager.get_window_rect(),
                "input_backend_requested": requested_backend,
                "mouse_backend_used": getattr(self.mouse_backend, "name", type(self.mouse_backend).__name__),
                "keyboard_used": type(self.keyboard_controller).__name__ if self.keyboard_controller else None,
                "capture_requested": cfg.get("capture_backend", "auto"),
                "capture_mode": getattr(capture, "requested", None),
                "capture_window_trusted": getattr(capture, "window_trusted", None),
                "bait_keys": list(self.bait_keys),
                "bait_quantity": cfg.get("bait_quantity", 200),
                "bait_counter": self.bait_counter,
                "tier_thresholds": self.get_tier_thresholds(),
                "inventory_pages": {p: cfg.get(f"inv_page_{p}_pos") for p in range(1, 9)},
                "inventory_strip": {"rect": self._inventory_rect(), "source": self._inv_rect_source,
                                    "bottom_margin": inventory_layout.BOTTOM_MARGIN,
                                    "fixed_fallback": {"width_from_right": self._inventory_width,
                                                       "y_offset": self._inventory_y_offset}},
                "confirm_button_pos": cfg.get("confirm_button_pos"),
                "drop_button_pos": cfg.get("drop_button_pos"),
                "armor_slot_pos": cfg.get("armor_slot_pos"),
                "detection_profile": self.detector.profile.to_dict(),
                "adaptive_detection": self._adaptive,
                "adaptive_recast_wait": self._recast_tuner is not None,
                "classic_fishing": cfg.get("classic_fishing", False),
                "classic_fishing_delay": cfg.get("classic_fishing_delay", 3.0),
                "quick_skip": cfg.get("quick_skip", False),
                "quick_skip_mode": cfg.get("quick_skip_mode", "horse"),
                "auto_fish_handling": cfg.get("auto_fish_handling", False),
                "human_like_clicking": self.human.enabled,
                "fish_actions": cfg.get("fish_actions", {}),
                "timings": timings,
                "assets_path": get_resource_path("assets"),
                "item_templates_loaded": len(self._load_template_cache()),
                "empty_slot_template": (None if self._load_empty_slot_template() is None
                                        else list(self._load_empty_slot_template()[0].shape)),
            }
            diag.set_effective(self.diag_tag, effective)
            diag.block("SESSION", f"bot {self.diag_tag} starts with:", [f"{k} = {v}" for k, v in effective.items()],
                       tag=self.diag_tag)
        except Exception:
            diag.exc("bot header", tag=self.diag_tag)

    def _diag_startup_images(self) -> None:
        """Reference images at start: the full window (with the inventory
        strip outlined) through both capture methods."""
        try:
            full = self.capture_full_window()
            _, _, w, h = self.window_manager.get_window_rect()
            diag.image("startup_full_window", full, tag=self.diag_tag, note="game window at start")
            inv = self._inventory_rect()
            marked = full.copy()
            if marked.shape[0] >= h and marked.shape[1] >= w:
                cv2.rectangle(marked, (inv[0], inv[1]), (inv[0] + inv[2] - 1, inv[1] + inv[3] - 1), (0, 0, 255), 2)
                for p in range(1, 9):
                    pos = self.config.get(f"inv_page_{p}_pos")
                    if pos:
                        cv2.circle(marked, (int(pos[0]), int(pos[1])), 6, (0, 255, 255), 2)
                        cv2.putText(marked, str(p), (int(pos[0]) + 8, int(pos[1]) + 5),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            diag.image("startup_full_window_marked", marked, tag=self.diag_tag,
                       note=f"red: inventory strip {inv} (from {self._inv_rect_source}); yellow: configured page tabs")
            method, other, why = self.capture.grab_other_method(0, 0, w, h) \
                if hasattr(self.capture, "grab_other_method") else (None, None, "n/a")
            if other is not None:
                diag.image(f"startup_full_window_via_{method}", other, tag=self.diag_tag,
                           note="same window through the other capture method")
            else:
                diag.log("IMAGE", f"no other-method startup image: {why}", tag=self.diag_tag)
        except Exception:
            diag.exc("startup images", tag=self.diag_tag)
    
    def start(self):
        """Starts the bot"""
        diag.set_thread_tag(self.diag_tag)
        self._manually_stopped = False  # Reset flag when starting
        self.stop_reason = None
        self._stop_notified = False
        self.running = True
        try:
            self.play_game()
        except Exception as e:
            # Never leave the GUI showing a bot that is no longer running.
            diag.exc("bot thread", tag=self.diag_tag)
            self._log(f"Bot stopped by an unexpected error: {e}")
            self._stop_bot(self.STOP_ERROR)
        finally:
            self.capture.close()
    
    def stop(self):
        """Stops the bot"""
        diag.log("DECISION", "stop requested (manual)", tag=self.diag_tag)
        self._manually_stopped = True  # Mark that user manually stopped the bot
        self._set_stop_reason(self.STOP_MANUAL)
        self.running = False
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Bot stopped")


# Diagnostic log: entry/exit of every method that takes part in a bot's work
# (no-op wrappers while diagnostics is off). Excluded: trivial helpers and
# what tests replace on the instance.
diag.trace_class(RecastWaitTuner)
diag.trace_class(FishingBot, exclude=(
    "_log", "_grab", "_bounds_close", "_set_stop_reason", "_notify_bot_stop_once",
    "_stop_if_window_unavailable", "_fish_velocity", "_diag_empty_slot_scores", "_diag_negative",
    "_diag_capture", "_diag_bot_header", "_diag_startup_images", "_identify_item",
    "_inventory_rect", "_inventory_origin",
))


if __name__ == "__main__":
    # The Qt GUI is the only GUI. DEBUG_MODE_EN (utils.py) adds its live
    # debug windows (debug_ui.py) to each running bot.
    from qt_gui import main
    main()

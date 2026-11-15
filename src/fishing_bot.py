"""
MT2 Fishing Bot - Multi-Window Support
Automated fishing minigame bot for Metin2
Author: boristei

Main entry point - imports all modules and starts the GUI
"""

import ctypes

# Fix for high DPI displays (125%, 150%, etc.) where UI elements may be cut off
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    pass  # Older Windows versions may not support this

import os
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
from window_manager import WindowManager, GameRegion
from fish_detector import FishDetector, DetectionProfile, WindowLearner
from humanize import Humanizer
from input_backend import create_keyboard_controller, create_mouse_backend
from screen_capture import WindowCapture


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


class FishingBot:
    """Main bot that plays the fishing minigame - one instance per game window"""
    STOP_BAIT_DEPLETED = "bait_depleted"
    STOP_MANUAL = "manual"
    STOP_WINDOW_UNAVAILABLE = "window_unavailable"
    STOP_INVENTORY_FULL = "inventory_full"
    STOP_ERROR = "error"
    
    # Class-level template cache (shared by all bot instances - loaded only once)
    _template_cache = None
    _template_border_crop = 7  # Pixels to crop from each edge of templates
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
    _DETECT_INTERVAL = 0.004      # min seconds between detection passes (caps CPU use)
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
    _MAX_BITE_FAILURES = 5        # casts without a minigame before the bait key is assumed empty
    _TRACK_WINDOW = 0.15          # seconds of fish positions kept for the speed estimate
    _TRACK_MIN_SPAN = 0.03        # shortest time base for a usable speed estimate
    _CLICK_OVERHEAD = 0.004       # seconds from "decided" to the button going down, besides the cursor settle
    
    def __init__(self, region: GameRegion, config: dict, window_manager: WindowManager, 
                 bait_counter: int = 800, bait_keys: list = None, bot_id: int = 0):
        # Core components
        self.region = region
        self.config = config
        self.window_manager = window_manager
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
        self._was_paused = False          # a pause happened: re-read the inventory before going on
        self._game_fish_seen = False      # a fish was detected in the current game
        self._panel_moves = 0             # times the panel was found somewhere else than before
        self._last_focus_log = 0.0

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
        
        # Inventory capture width (right side of window where items appear)
        self._inventory_width = 200
        
        # Inventory capture Y offset (skip top 300px of window)
        self._inventory_y_offset = 200
        
        # Dead fish tracking: ignored slot positions (10 pixel radius around center)
        self._ignored_positions = set()  # Positions confirmed as dead fish

        # Empty slot tracking for inventory management
        self._empty_slot_positions = []  # Ordered (inv_x, inv_y) of empty slots on current page
        self._current_inv_page = 0       # Current inventory page index (0-based)
        self._full_doubts = 0            # "page full" readings in a row that were not believed

        # Timing cache — read from config once per session in play_game(), never inside loops
        self._load_timings()

    def _load_timings(self):
        """Caches all timing values from config (called at init and at session start)."""
        config = self.config

        def get(key, default):
            # A hand-edited or damaged config must not crash the bot thread.
            try:
                value = float(config.get(key, default))
            except (TypeError, ValueError):
                return default
            return value if 0.0 <= value < 3600.0 else default

        self._t_cursor   = get('timing_cursor_settle',  0.012)
        self._t_hold     = get('timing_button_hold',    0.008)
        self._t_post     = get('timing_post_click',     0.035)
        self._t_human_mn = get('timing_human_min',      0.15)
        self._t_human_mx = max(self._t_human_mn, get('timing_human_max', 0.40))
        self._t_key_hold = get('timing_key_hold',       0.025)
        self._t_key_set  = get('timing_key_settle',     0.030)
        self._t_interkey = get('timing_cast_interkey',  0.050)
        # Game-response waits (tunable via Timing Settings window)
        self._t_catch_wait  = get('timing_catch_wait',        0.400)
        self._t_open_wait   = get('timing_open_wait',         0.100)
        self._t_dead_check  = get('timing_dead_fish_check',   0.100)
        self._t_drop_settle = get('timing_drop_settle',       0.120)
        self._t_qs_between  = get('timing_quickskip_between', 0.100)
        self._t_qs_after    = get('timing_quickskip_after',   0.100)
        # Server-dependent waits
        self._t_bite_timeout = max(1.0, get('timing_minigame_timeout', 6.0))
        self._t_recast_wait  = max(0.0, get('timing_recast_wait', 4.0))
        if self.config.get('adaptive_recast_wait', True):
            if self._recast_tuner is None or self._recast_tuner.ceiling != self._t_recast_wait:
                self._recast_tuner = RecastWaitTuner(self._t_recast_wait)
        else:
            self._recast_tuner = None

    def _log(self, message: str):
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] {message}")

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
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Error loading template {f}: {e}")
        
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
        except Exception as e:
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
            return []

        template, half_w, half_h = slot_template
        t_h, t_w = template.shape

        inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape

        if t_h > inv_h or t_w > inv_w:
            return []

        THRESHOLD = 0.70
        try:
            result = cv2.matchTemplate(inventory_gray, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= THRESHOLD)
            if ys.size == 0:
                return []

            # Sort all candidates by score descending, then keep only those
            # outside a 10px radius of any already-kept point. A coarse spatial
            # bucket index (>10 px) replaces the O(K^2) per-candidate scan over
            # already-kept points with amortized O(1) lookups.
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
                for ddx in (-1, 0):
                    for ddy in (-1, 0):
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

            kept.sort(key=lambda p: (p[1] // 30, p[0]))
            return kept

        except Exception:
            return []

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
                
                if max_val > best_confidence:
                    best_confidence = max_val
                    best_match = filename
            except Exception:
                continue
        
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

    _EMPTY_FRAME_SHAPE = (100, 100, 3)

    def capture_inventory_area(self) -> np.ndarray:
        """Captures the inventory area (right-hand strip of the game window, below the top offset)."""
        if not self._stop_if_window_unavailable():
            _, _, win_width, win_height = self.window_manager.get_window_rect()
            frame = self._grab(
                win_width - self._inventory_width,
                self._inventory_y_offset,
                self._inventory_width,
                win_height - self._inventory_y_offset - 30,
            )
            if frame is not None:
                return frame
        return np.zeros(self._EMPTY_FRAME_SHAPE, dtype=np.uint8)

    def identify_item_in_inventory(self, inventory_frame: np.ndarray, ignore_positions: set = None,
                                   roi: Optional[Tuple[int, int, int, int]] = None) -> Optional[Tuple[str, Tuple[int, int]]]:
        """Identifies an item in the inventory using template matching with high precision.
        Returns (filename, (x, y)) of best match or None if no match found.
        Coordinates are relative to inventory area.
        ignore_positions: set of (x, y) tuples to skip (dead fish locations).
        If first match is ignored, tries to find another match within same template.
        
        For confusable fish (Goldfish vs Large_zander), uses color-based disambiguation."""
        templates = self._load_template_cache()
        if not templates:
            return None

        if roi is not None:
            # Search only where the inventory changed: ~40 tiny matches instead
            # of ~40 full-strip matches. Coordinates are mapped back afterwards.
            x1, y1, x2, y2 = roi
            sub_ignore = {(ix - x1, iy - y1) for ix, iy in ignore_positions} if ignore_positions else None
            match = self.identify_item_in_inventory(inventory_frame[y1:y2, x1:x2], sub_ignore)
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

                if not top_ignored:
                    matched_filename = filename
                    if filename in confusable_fish:
                        matched_filename = self._disambiguate_confusable_fish(
                            inventory_frame, center_x, center_y, filename
                        )
                    best_confidence = peak_val
                    best_match = (matched_filename, (center_x, center_y))
                    if best_confidence >= EARLY_EXIT_THRESHOLD and filename not in confusable_fish:
                        return best_match
                    continue  # Move to next template

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
                    for ix, iy in ignored_list:
                        if abs(center_x - ix) < 10 and abs(center_y - iy) < 10:
                            is_ignored = True
                            break

                    if not is_ignored:
                        matched_filename = filename
                        if filename in confusable_fish:
                            matched_filename = self._disambiguate_confusable_fish(
                                inventory_frame, center_x, center_y, filename
                            )
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
                continue

        return best_match
    
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
            return self._is_item_at_position_fallback(inventory_frame, x, y, radius)

        try:
            crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            result = cv2.matchTemplate(crop_gray, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            # High empty-slot confidence => slot is empty => no item present
            return max_val < 0.70
        except Exception:
            return self._is_item_at_position_fallback(inventory_frame, x, y, radius)

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
        if cv2.countNonZero(mask) < 40:
            return None
        x, y, w, h = cv2.boundingRect(mask)
        fh, fw = gray.shape
        if w * h > fw * fh * 0.6:
            return None  # page switch / overlay — not a single new item
        pad = 20
        return (max(0, x - pad), max(0, y - pad), min(fw, x + w + pad), min(fh, y + h + pad))

    def _wait_inventory_change(self, base_gray: np.ndarray, until: float):
        """Polls the inventory until it differs from the baseline or ``until``
        (perf_counter time) passes. Returns (latest_frame, changed_box or None)."""
        while True:
            frame = self.capture_inventory_area()
            box = self._changed_box(base_gray, frame)
            if box is not None:
                return frame, box
            if not self.running or time.perf_counter() >= until:
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
                base_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            now = time.perf_counter()
            if not full_scan_done:
                if now >= deadline:
                    full_scan_done = True
                    match = self.identify_item_in_inventory(
                        frame, ignore_positions=self._ignored_positions)
                    if match or not allow_extra:
                        return match, frame
            elif now >= hard_deadline:
                break
        return None, frame

    def _mark_occupied(self, inv_x: int, inv_y: int) -> None:
        """The slot holds an item that stays: never look at it again."""
        self._ignored_positions.add((inv_x, inv_y))
        self._empty_slot_positions = [
            (ex, ey) for ex, ey in self._empty_slot_positions
            if not (abs(inv_x - ex) < 15 and abs(inv_y - ey) < 15)
        ]

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
            time.sleep(0.15)   # a tooltip or an item in flight may have covered the slots
            slots = self._scan_empty_slots(self.capture_inventory_area())
        if not slots and expected and len(expected) >= 3 and self._full_doubts < 2:
            self._full_doubts += 1
            self._log(f"Inventory page reads as full although {len(expected)} slots were free "
                      f"a moment ago — is something covering it? Checking again after the next catch")
            self._empty_slot_positions = list(expected)
            return True
        self._full_doubts = 0
        self._empty_slot_positions = slots
        return bool(slots)

    def handle_caught_item(self, may_have_catch: bool = True):
        """Identifies the caught item and keeps, opens or drops it as
        configured in fish_actions. Called after every finished game.

        ``may_have_catch`` is False when the minigame ended without a single
        hit, in which case no time is spent waiting for a late item."""
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
                    self._switch_inventory_page()
            except Exception as e:
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
                return

            filename, (inv_x, inv_y) = match
            action = fish_actions.get(filename, 'keep')
            fish_name = filename.replace('_living.jpg', '').replace('_item.jpg', '')

            if action == 'drop' and not self.config.get('confirm_button_pos'):
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
                self._switch_inventory_page()

        except Exception as e:
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
        try:
            with input_lock:
                if not self.running or not self._focus(force=True):
                    return False
                win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
                screen_x = win_left + win_width - self._inventory_width + inv_x
                screen_y = win_top + self._inventory_y_offset + inv_y
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

                if action == 'open':
                    self._log(f"{label}Opening: {fish_name}")
                    still_there = right_click_and_check()
                    if still_there:
                        time.sleep(self._t_dead_check)
                        still_there = self._is_item_at_position(
                            self.capture_inventory_area(), inv_x, inv_y)
                    if still_there:
                        # Dead fish — permanently occupies this slot
                        self._mark_occupied(inv_x, inv_y)

                elif action == 'drop':
                    confirm_pos = self.config.get('confirm_button_pos')
                    drop_pos = self.config.get('drop_button_pos')
                    if not confirm_pos:
                        self._mark_occupied(inv_x, inv_y)
                        return True

                    self._log(f"{label}Dropping: {fish_name}")
                    # A living fish is opened instead when the game allows it
                    still_there = right_click_and_check() if '_living' in filename else True

                    if still_there:
                        steps = [(screen_x, screen_y), (win_cx, win_cy)]
                        if drop_pos:
                            steps.append((win_left + drop_pos[0], win_top + drop_pos[1]))
                        steps.append((win_left + confirm_pos[0], win_top + confirm_pos[1]))
                        for x, y in steps:
                            self.human.move_to(x, y)
                            self.human.sleep(0.05)
                            self.human.click()
                            self.human.sleep(self._t_drop_settle)
                        self.human.move_to(win_cx, win_cy)

                        # Verify while still holding the lock
                        time.sleep(self._t_drop_settle)
                        if self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y):
                            self._log(f"{fish_name} is still in its slot after the drop — leaving it there")
                            self._mark_occupied(inv_x, inv_y)
            return True
        except Exception as e:
            self._log(f"Error handling {fish_name}: {e}")
            self._mark_occupied(inv_x, inv_y)
            return True

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
            if frame is not None:
                return frame
        return np.zeros(self._EMPTY_FRAME_SHAPE, dtype=np.uint8)

    def capture_window_region(self, rel_left: int, rel_top: int, width: int, height: int) -> np.ndarray:
        """Captures a sub-rectangle of the game window (window-relative coordinates)."""
        if not self._stop_if_window_unavailable():
            frame = self._grab(rel_left, rel_top, width, height)
            if frame is not None:
                return frame
        return np.zeros((max(1, height), max(1, width), 3), dtype=np.uint8)

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

        try:
            # ========== PHASE 1: Quick pre-check (NO LOCK) ==========
            window_active, fish_pos = detect(capture())

            if not window_active:
                return (False, None)
            if not fish_pos:
                return (True, None)
            self._fish_ever_seen = self._game_fish_seen = True
            self._fish_track.append((time.perf_counter(), fish_pos[0], fish_pos[1]))

            if self.detector.learning and self.detector.profile.circle_center is None:
                # The fish is being followed by its movement, but where the
                # click circle is has not been worked out yet. A click now is
                # aimed at a guessed circle, and a miss costs game time.
                return (True, None)

            dx, dy = fish_pos[0] - cx, fish_pos[1] - cy
            if (dx * dx + dy * dy) >= radius_sq:
                return (True, None)

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
                if not self._focus():
                    return (True, None)

                window_active, fish_pos = detect(capture())
                if not window_active:
                    return (False, None)
                if not fish_pos:
                    return (True, None)

                fx, fy = fish_pos
                dx, dy = fx - cx, fy - cy
                if (dx * dx + dy * dy) >= radius_sq:
                    return (True, None)

                # Aim where the fish will be when the button goes down, and
                # skip a fish that is about to leave the circle.
                now = time.perf_counter()
                self._fish_track.append((now, fx, fy))
                target = self._click_point(fish_pos, now)
                if target is None:
                    return (True, None)
                fx, fy = target

                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                self.human.move_to(win_left + self.region.left + fx,
                                   win_top + self.region.top + fy)
                time.sleep(self._t_cursor)
                self.human.click(hold=self._t_hold)
            # ========== LOCK RELEASED ==========

            # Post-click settle happens outside the lock so other windows can
            # use the mouse meanwhile. The lock itself is FIFO-fair.
            time.sleep(self._t_post)
            return (True, fish_pos)

        except Exception as e:
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

    def _click_point(self, fish_pos: Tuple[int, int], now: float) -> Optional[Tuple[int, int]]:
        """Where to click for a fish seen at ``fish_pos``: where it will be
        when the button goes down. None when it will have left the circle by
        then (a fish on its way out is not worth a miss)."""
        fx, fy = fish_pos
        velocity = self._fish_velocity(now)
        if velocity is None:
            return (fx, fy)
        lead = self._t_cursor + self._CLICK_OVERHEAD
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
        stretches after every miss — up to 30 s by the last attempt before
        the bait key is given up — and remembers the longest bite seen."""
        base = self._t_bite_timeout
        learned = self._longest_bite * 1.5 + 1.0 if self._longest_bite else 0.0
        grown = base * (1.5 ** min(self.consecutive_failures, 4))
        return min(max(base, learned, grown), max(base, 30.0))

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

    def _goto_page(self, page_pos, settle: float = 0.3) -> bool:
        """Clicks an inventory page tab (window-relative position) and gives
        the game time to show the page. False when the click could not be
        sent: the page on screen is then still the old one."""
        ok = False
        for _attempt in range(3):
            if not self.running:
                return False
            with input_lock:
                if self._focus(force=True):
                    win_left, win_top, _, _ = self.window_manager.get_window_rect()
                    self._click_page_tab(win_left + page_pos[0], win_top + page_pos[1])
                    ok = True
            if ok:
                break
            time.sleep(0.5)
        if ok:
            time.sleep(settle)
        return ok

    def adjust_bait_tier(self):
        """Adjusts bait counter to next lower tier when 2 consecutive failures occur."""
        thresholds = self.get_tier_thresholds()
        
        # Find current tier and drop to next one
        for threshold in thresholds:
            if self.bait_counter > threshold:
                self.bait_counter = threshold
                break
        else:
            # Already at or below lowest threshold
            self.bait_counter = 0
        
        self.consecutive_failures = 0
        
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] No bite on this bait key — assuming it is empty. Bait adjusted to {self.bait_counter}")
        if self.on_bait_update:
            self.on_bait_update(self.bot_id, self.bait_counter)
        if self.on_stats_update:
            self.on_stats_update(self.bot_id, self.hits, self.total_games, self.bait_counter)
    
    def press_ctrl_key(self, key: str):
        """Presses CTRL+key combination once. Uses input lock for thread safety."""
        if not self.keyboard_controller:
            return
        
        with input_lock:
            try:
                if not self.running or not self._focus():
                    return
                self.human.sleep(self._t_key_set)
                self.keyboard_controller.press(Key.ctrl)
                self.human.sleep(self._t_key_hold)
                self.keyboard_controller.press(key)
                self.human.sleep(self._t_key_hold)
                self.keyboard_controller.release(key)
                self.human.sleep(self._t_key_hold)
                self.keyboard_controller.release(Key.ctrl)
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error pressing CTRL+{key}: {e}")
    
    def bait_and_cast(self) -> bool:
        """Selects bait and casts fishing line in a single lock acquisition.
        Returns False when no cast was sent (window could not be focused)."""
        if not self.keyboard_controller:
            return True

        bait_key = self.get_bait_key(self.bait_counter)
        key_map = {'space': Key.space, 'F1': Key.f1, 'F2': Key.f2, 'F3': Key.f3, 'F4': Key.f4}

        with input_lock:
            try:
                if not self.running or not self._focus():
                    return False
                self._verify_capture()
                self.human.sleep(self._t_key_set)

                pynput_bait = key_map.get(bait_key.upper() if len(bait_key) > 1 else bait_key, key_map.get(bait_key, bait_key))
                self.human.tap_key(pynput_bait, hold=self._t_key_hold)
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Pressed key {bait_key}")

                self.human.sleep(self._t_interkey)

                self.human.tap_key(Key.space, hold=self._t_key_hold)
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Cast fishing line")
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error in bait_and_cast: {e}")

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
            self.press_ctrl_key('g')
            time.sleep(self._t_qs_between)  # game must process the first CTRL+G
            self.press_ctrl_key('g')
            time.sleep(self._t_qs_after)    # before the next action
        else:
            # Armour mode: right-click on armor slot to equip/unequip
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Quick skip (Armor mode - right-click)...")
            
            armor_pos = self.config.get('armor_slot_pos')
            if not armor_pos:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Armor slot position not set! Falling back to wait.")
                time.sleep(0.3)  # Fallback delay
                return
            
            # Acquire lock for mouse operation
            with input_lock:
                # Activate window
                if not self.running or not self._focus(force=True):
                    return
                time.sleep(0.03)
                
                # Convert armor slot position (relative to window) to screen coordinates
                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                screen_x = win_left + armor_pos[0]
                screen_y = win_top + armor_pos[1]
                
                # Right-click on armor slot
                self.human.move_to(screen_x, screen_y)
                time.sleep(np.random.uniform(0.2, 0.25))  # cursor settle + human-like jitter
                self.human.click(button='right')
                # click() already sent to OS — release lock now
            # ========== LOCK RELEASED ==========
            time.sleep(np.random.uniform(0.05, 0.07))  # animation settle outside lock
            return
    
    def press_key(self, key: str, description: str = ""):
        """Presses a keyboard key using pynput. Uses input lock for thread safety."""
        if not self.keyboard_controller:
            return
        
        # Map keys to pynput Key objects
        key_map = {
            'space': Key.space, 'F1': Key.f1, 'F2': Key.f2, 'F3': Key.f3, 'F4': Key.f4
        }
        
        with input_lock:
            try:
                if not self.running or not self._focus():
                    return
                self.human.sleep(self._t_key_set)

                pynput_key = key_map.get(key.upper() if len(key) > 1 else key, key_map.get(key, key))

                self.human.tap_key(pynput_key, hold=self._t_key_hold)

                if description and self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] {description}")
            except Exception as e:
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

        while self.running:
            now = time.perf_counter()
            elapsed = now - start
            if elapsed >= timeout:
                break
            if self.paused:
                self._wait_interrupted = True
                return False

            try:
                if not self.region_auto_calibrated:
                    if self._try_calibrate(self.capture_full_window(), elapsed):
                        return True
                else:
                    frame = self.capture_screen()
                    window_active, _ = self.detector.detect_window_and_fish(frame)
                    if window_active:
                        if not self.detector.partly_visible:
                            return True
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
                            if self._try_calibrate(self.capture_full_window(), elapsed, adapt):
                                return True
                        if now - partial_since >= self._PARTIAL_PATIENCE:
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
                        if self._try_calibrate(self.capture_full_window(), elapsed, adapt):
                            return True

                time.sleep(0.05)
            except Exception as e:
                self._log(f"Error: {e}")
                time.sleep(0.05)

        if not self.region_auto_calibrated:
            self._dump_debug_frames("no_minigame")
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

        if (bounds is not None and self._adaptive and not self.region_auto_calibrated
                and pixels < bounds[2] * bounds[3] * self._MIN_PANEL_FILL):
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

        if not fits(bounds):
            bounds, pixels = None, 0
        if adapt is None:
            adapt = elapsed >= self._ADAPTIVE_AFTER
        if bounds is None and self._adaptive and adapt:
            bounds, profile, pixels = self._find_with_other_profiles(frame, fits)

        if bounds is None:
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
            return False  # wait for a confirming frame

        self._pending_bounds = None
        if profile is not None:
            self.detector.set_profile(profile)
            self._games_without_fish = 0
            self._fish_ever_seen = False
            self._log(f"Adapted to this client's minigame colors (profile '{profile.name}')")
            self._dump_debug_frames("adapted", frame)

        x, y, w, h = bounds
        moved = self.region_auto_calibrated
        if moved and (abs(x - self.region.left) > 8 or abs(y - self.region.top) > 8):
            self._panel_moves += 1
        self.region = GameRegion(x, y, w, h)
        self.region_auto_calibrated = True
        self.detector.calibrate(pixels)
        self._update_region_cache()
        self._log(f"{'Re-calibrated' if moved else 'Auto-calibrated'} region: {w}x{h} at ({x},{y})")
        return True

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
            # Same rule as for the active profile: a color that covers only
            # patches of what it outlines belongs to a different look.
            if (bounds and fits(bounds)
                    and probe._last_bounds_pixels >= bounds[2] * bounds[3] * self._MIN_PANEL_FILL):
                return bounds, candidate, probe._last_bounds_pixels

        learned = self._window_learner.learn(self._reference_frame, frame)
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
                    self._log(f"Could not clear saved profile: {e}")
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

    def _abort_stuck_game(self):
        """The 'panel' never closed: what was detected is not the minigame."""
        self._log(f"Minigame still 'open' after {self._MAX_GAME_SECONDS:.0f}s — false detection, recalibrating")
        self._dump_debug_frames("stuck")
        self._game_end_time = None
        if self.detector.profile.learned:
            self._discard_learned_profile("matched something that is not the minigame")
        else:
            self._reset_calibration()

    def _dump_debug_frames(self, tag: str, frame: Optional[np.ndarray] = None):
        """Saves calibration frames for troubleshooting unsupported clients
        (config 'detection_debug_dump'). At most a handful per session."""
        if not self.config.get('detection_debug_dump', False):
            return
        count = getattr(self, '_debug_dump_count', 0)
        if count >= 6:
            return
        self._debug_dump_count = count + 1
        try:
            out_dir = os.path.join(os.getcwd(), "debug_frames")
            os.makedirs(out_dir, exist_ok=True)
            stamp = f"w{self.bot_id+1}_{int(time.time())}_{tag}"
            if frame is None:
                frame = self.capture_full_window()
            cv2.imwrite(os.path.join(out_dir, f"{stamp}_current.png"), frame)
            if self._reference_frame is not None:
                cv2.imwrite(os.path.join(out_dir, f"{stamp}_before_cast.png"), self._reference_frame)
            self._log(f"Saved debug frames to {out_dir}")
        except Exception as e:
            self._log(f"Could not save debug frames: {e}")

    def _scan_existing_inventory(self):
        """Scans inventory for all existing items and adds their positions to ignore list.
        Called at bot start to prevent re-processing items already in inventory.
        Uses iterative minMaxLoc with masking to find ALL distinct items (same logic as identify_item_in_inventory)."""
        templates = self._load_template_cache()
        if not templates:
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

            # Spatial bucket index over `ignored` for O(1) duplicate checks.
            # Without this, every new candidate scans the entire ignore set
            # (which grows monotonically as items are discovered).
            BUCKET = 16
            ignored_buckets: dict = {}
            for ix, iy in ignored:
                ignored_buckets.setdefault((ix // BUCKET, iy // BUCKET), []).append((ix, iy))

            for template, half_w, half_h in templates.values():
                t_h, t_w = template.shape

                if t_h > inv_h or t_w > inv_w:
                    continue

                try:
                    result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)

                    # Cheap upfront rejection: if the global max is already below
                    # threshold, this template has no occurrence anywhere.
                    _, peak, _, _ = minMaxLoc(result)
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
                        for ddx in (-1, 0):
                            for ddy in (-1, 0):
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

                        # Mask out this match area to find the next one
                        mask_x1 = max(0, pt_x - half_t_w)
                        mask_y1 = max(0, pt_y - half_t_h)
                        mask_x2 = min(res_w, pt_x + half_t_w + 1)
                        mask_y2 = min(res_h, pt_y + half_t_h + 1)
                        result[mask_y1:mask_y2, mask_x1:mask_x2] = -1.0

                except Exception:
                    continue

            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Inventory scan: found {found_count} existing items (ignoring)")

            # Detect empty slots on current page
            self._empty_slot_positions = self._scan_empty_slots(inventory_frame)
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Found {len(self._empty_slot_positions)} empty inventory slots")

        except Exception as e:
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

    def _inventory_full_stop(self, when: str = ""):
        self._stop_bot(self.STOP_INVENTORY_FULL,
                       f"[W{self.bot_id+1}] All inventory pages full{when} — stopping bot")

    def _switch_inventory_page(self):
        """Advances through remaining configured inventory pages until one with empty slots is found.
        If all pages are full, stops the bot."""
        while self.running:
            next_page = self._current_inv_page + 1
            page_num = next_page + 1  # 1-based

            if page_num > 8:
                break

            page_pos = self.config.get(f'inv_page_{page_num}_pos')
            if not page_pos:
                break

            self._log(f"Switching to inventory page {page_num}")
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

        for page_idx in range(8):
            if not self.running:
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
                    nav_failed = True
                    break
            else:
                time.sleep(0.3)

            self._current_inv_page = page_idx
            self._ignored_positions.clear()
            self._empty_slot_positions.clear()
            if auto:
                # Scan every page and process (open/drop) its items before
                # deciding where to settle.
                self._startup_process_page(fish_actions)
                if self._empty_slot_positions and first_page_with_empty is None:
                    first_page_with_empty = page_idx
            else:
                # Stop on the first page with room — that is where fish go.
                self._scan_existing_inventory()
                if self._empty_slot_positions:
                    return

        if not self.running:
            return
        if first_page_with_empty is None:
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
            BUCKET = 16  # > merge radius (10) so a slot maps to <=4 buckets to probe
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
                    _, peak, _, _ = minMaxLoc(result)
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

                        # Probe the 4 buckets that any (cx,cy) within radius 10
                        # could fall into. Only existing slots in those buckets
                        # are candidates for merging.
                        bx, by = cx // BUCKET, cy // BUCKET
                        merged = False
                        for ddx in (-1, 0):
                            for ddy in (-1, 0):
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
                    continue

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
                if fish_actions.get(matched_filename, 'keep') == 'keep':
                    self._ignored_positions.add((cx, cy))
                    keep_count += 1

            if self.on_status_update and keep_count:
                self.on_status_update(f"[W{self.bot_id+1}] Startup: {keep_count} 'keep' items (ignored)")

            # Pass 2: open/drop everything else (identify_item skips _ignored_positions)
            processed_count = 0
            max_items = 50  # Safety cap against infinite loops
            while processed_count < max_items and self.running:
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
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error loading classic fish template: {e}")
        else:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Classic fish template not found at assets/fishing/classic_fish.jpg")
        
        return FishingBot._classic_fish_template
    
    def wait_for_classic_fish(self, timeout: float = 10.0) -> bool:
        """Waits for the classic fish image to appear in the game window.
        Returns True if found, False if timeout."""
        template = self._load_classic_fish_template()
        if template is None:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] No classic fish template, using fallback timing")
            return True  # Fallback: proceed anyway
        
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

        while self.running and perf_counter() - start_time < timeout:
            if self.paused:
                time.sleep(0.1)
                continue

            try:
                # Grab only the 250px-wide centered bar of the upper half —
                # the indicator never appears elsewhere, and capturing the
                # whole window just to crop it wasted most of the grab time.
                _, _, win_w, win_h = get_rect()
                crop_left = max(0, (win_w >> 1) - 125)
                crop_w = min(250, win_w - crop_left)
                frame = capture_region(crop_left, 0, crop_w, win_h >> 1)
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

                if best_match_val >= ACCEPT_THRESHOLD:  # Found the classic fish indicator
                    # Persist the winning scale for the next poll/cast
                    FishingBot._classic_fish_last_scale = best_scale
                    # Start timer IMMEDIATELY after detection (configurable delay)
                    delay = self.config.get('classic_fishing_delay', 3.0)
                    # Use interruptible sleep that checks running/paused state
                    delay_start = time.time()
                    while time.time() - delay_start < (delay - 0.05):
                        if not self.running:
                            return False  # Bot stopped during delay
                        if self.paused:
                            time.sleep(0.1)
                            delay_start = time.time()  # Reset delay when paused
                            continue
                        time.sleep(0.05)  # Small sleep increments
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Classic fish detected (confidence: {best_match_val:.2f}, scale: {best_scale:.1f}x, delay: {delay}s)")
                    return True
                
                time.sleep(0.02)  # Fast polling
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error detecting classic fish: {e}")
                time.sleep(0.1)
        
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Classic fish detection timeout")
        return False
    
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

    def _on_missed_bite(self, cast_wait: Optional[float], limit: int, what: str) -> bool:
        """A cast was not followed by a bite. After ``limit`` in a row the
        bait key is taken to be empty. Returns False when that used up the
        last bait and the bot stopped."""
        self._report_cast(cast_wait, False)
        self.consecutive_failures += 1
        self._log(f"{what} ({self.consecutive_failures}/{limit})")
        if self.consecutive_failures < limit:
            return True
        if not self._minigame_ever_seen and not self.config.get('classic_fishing', False):
            self._log("The minigame was never seen in this session. If it does open on "
                      "screen: keep the game window uncovered, make sure 'Adapt to "
                      "re-skinned minigames' is on, or use Classic fishing if this "
                      "server has no minigame.")
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
        detect_interval = self._DETECT_INTERVAL
        gone_frames = 0
        game_hits = 0
        game_start = perf_counter()
        self._fish_track.clear()
        self._game_fish_seen = False
        relearned = False
        last_partial_check = 0.0

        while self.running:
            if self.paused:
                self._was_paused = True
                time.sleep(0.1)
                game_start += 0.1  # paused time does not count
                continue

            loop_start = perf_counter()
            game_time = loop_start - game_start
            if game_time > self._MAX_GAME_SECONDS:
                return True, game_hits
            if self._adaptive and not relearned and not self._game_fish_seen and not self.detector.learning:
                # The panel is open and no fish is found. Stock profile that
                # never found one: this client draws another fish. A profile
                # that did find fish before: the fish looks different now.
                # Either way the fish is the thing that moves.
                patience = self._FISH_LOST_AFTER if self._fish_ever_seen else self._FISH_MISSING_AFTER
                if game_time > patience:
                    relearned = True
                    self._learn_fish_by_motion()
            try:
                # Human-like mode delays the *reaction* to a fish in
                # the circle; detection keeps running at full rate.
                reaction = np.random.uniform(self._t_human_mn, self._t_human_mx) if human_like else 0.0
                window_active, fish_pos = self.atomic_capture_and_click(reaction)
            except Exception as e:
                self._log(f"Error: {e}")
                time.sleep(0.05)
                continue

            if not window_active:
                # One missing frame is not the end of the game: a
                # tooltip, a capture hiccup or another window passing
                # over would otherwise trigger a recast mid-game.
                gone_frames += 1
                if gone_frames < self._END_CONFIRM_FRAMES:
                    time.sleep(self._END_CONFIRM_GAP)
                    continue
                # Gone from where it was — or dragged somewhere else?
                if self._panel_elsewhere():
                    gone_frames = 0
                    self._fish_track.clear()
                    continue

                self._game_end_time = perf_counter()
                self.total_games += 1
                self.bait_counter -= 1

                self._log(f"Game finished. Total: {self.total_games}, Bait: {self.bait_counter}")
                if self.on_bait_update:
                    self.on_bait_update(self.bot_id, self.bait_counter)
                if self.on_stats_update:
                    self.on_stats_update(self.bot_id, 0, self.total_games, self.bait_counter)

                # Handle caught item (if auto fish handling is enabled)
                self.handle_caught_item(may_have_catch=game_hits > 0)
                break

            gone_frames = 0
            if self.detector.partly_visible and loop_start - last_partial_check >= self._PARTIAL_CHECK_GAP:
                # Open, but part of it has left the region: dragged?
                last_partial_check = loop_start
                before = self._panel_moves
                if self._panel_elsewhere() and self._panel_moves != before:
                    self._fish_track.clear()
                    continue
            if fish_pos:
                game_hits += 1
                self.hits += 1
                if self.on_stats_update:
                    self.on_stats_update(self.bot_id, self.hits, self.total_games, self.bait_counter)

            if self.detector.profile_updated:
                self._publish_learned_profile()

            # Pace the loop: the game renders at ~60 fps, so polling
            # flat-out only burns a CPU core the game clients need.
            spare = detect_interval - (perf_counter() - loop_start)
            if spare > 0:
                time.sleep(spare)

        return False, game_hits

    def play_game(self):
        """Main game loop implementing the fishing minigame workflow."""
        # Refresh timing cache from config once per session — never read inside loops
        self._load_timings()

        requested_backend = str(self.config.get('input_backend', 'auto')).lower()
        if requested_backend == 'interception' and getattr(self.mouse_backend, 'name', '') != 'interception':
            self._log("Interception requested but unavailable; using PyAutoGUI")

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

        # Scan all inventory pages at startup, process open/drop items, then settle on
        # the first page that still has empty slots.
        self._startup_scan_and_process_all_pages()

        self._was_paused = False

        while self.running and self.bait_counter > 0:
            if self._stop_if_window_unavailable():
                break

            if self.paused:
                self._was_paused = True
                time.sleep(0.1)
                continue

            if self._was_paused:
                self._was_paused = False
                self._last_cast_wait = None
                self._game_end_time = None
                self._log("Resumed — re-scanning inventory...")
                self._startup_scan_and_process_all_pages()
                if not self.running:
                    break

            try:
                classic = self.config.get('classic_fishing', False)

                # Self-calibration needs to know what the window looked like
                # before the minigame opened: one full grab per cast.
                if not classic and self._adaptive:
                    self._reference_frame = self.capture_full_window()

                if self.bait_and_cast() is False:
                    # Nothing was sent (the window could not be brought to the
                    # front): not a cast, so not a missed bite either.
                    self._last_cast_wait = None
                    self._sleep_interruptible(0.5)
                    continue
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
                            self._reset_calibration()
                        continue

                    # Reset failure counter on successful minigame detection
                    self.consecutive_failures = 0
                    self._minigame_ever_seen = True
                    self._report_cast(cast_wait, True)
                    self._longest_bite = max(self._longest_bite, time.perf_counter() - cast_time)

                    stuck, game_hits = self._play_minigame()
                    self.hits = 0
                    if stuck:
                        self._abort_stuck_game()
                        continue
                    if self.detector.profile.learned and self.running:
                        self._games_without_fish = 0 if game_hits else self._games_without_fish + 1
                        if self._games_without_fish >= 2:
                            self._discard_learned_profile()
                else:
                    # Classic Fishing system - wait for fish indicator, then reel in
                    fish_found = self.wait_for_classic_fish(timeout=40)
                    if not self.running:
                        break

                    if not fish_found:
                        if not self._on_missed_bite(cast_wait, 2, "No fish bite detected"):
                            break
                        # Press CTRL+G once per failure to dismount horse if that's the issue
                        # First failure: try to dismount if on horse
                        # Second failure: you actually mounted in first attemp and now you need to unmount
                        self._log("Pressing CTRL+G to dismount horse...")
                        self.press_ctrl_key('g')
                        time.sleep(0.15)
                        continue

                    # Reset failure counter on successful fish detection
                    self.consecutive_failures = 0
                    self._report_cast(cast_wait, True)

                    # Handle pause before reeling in
                    while self.paused and self.running:
                        self._was_paused = True
                        time.sleep(0.1)
                    if not self.running:
                        break

                    # Timer already elapsed in wait_for_classic_fish - press space to reel in
                    self.press_key('space', "Reel in fish")
                    self._game_end_time = time.perf_counter()
                    time.sleep(0.05)

                    self.total_games += 1
                    self.bait_counter -= 1
                    self._log(f"Classic catch! Total: {self.total_games}, Bait: {self.bait_counter}")
                    if self.on_bait_update:
                        self.on_bait_update(self.bot_id, self.bait_counter)
                    if self.on_stats_update:
                        self.on_stats_update(self.bot_id, 0, self.total_games, self.bait_counter)

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
                self._log(f"Error in play_game: {e}")
                time.sleep(0.5)

        if self.stop_reason is None:
            if self._manually_stopped:
                self._set_stop_reason(self.STOP_MANUAL)
            elif self.bait_counter <= 0:
                self._set_stop_reason(self.STOP_BAIT_DEPLETED)
            else:
                self._set_stop_reason(self.STOP_ERROR)

        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Bot finished! Total games: {self.total_games}")
        self.running = False
        self._notify_bot_stop_once()
    
    def start(self):
        """Starts the bot"""
        self._manually_stopped = False  # Reset flag when starting
        self.stop_reason = None
        self._stop_notified = False
        self.running = True
        if self.config.get('jigsaw_solver_enabled', False):
            try:
                from jigsaw_bot import JigsawBot
                jigsaw_bot = JigsawBot(
                    self.window_manager,
                    self.config,
                    bot_id=self.bot_id,
                    on_status_update=self.on_status_update,
                    on_bot_stop=self.on_bot_stop,
                )
                self._jigsaw_bot = jigsaw_bot
                jigsaw_bot.running = True
                while self.running and jigsaw_bot.running:
                    jigsaw_bot.paused = self.paused
                    jigsaw_bot.start()
                    break
                self.running = False
                return
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Jigsaw bot failed: {e}")
                self._stop_bot(self.STOP_ERROR)
                return
        try:
            self.play_game()
        except Exception as e:
            # Never leave the GUI showing a bot that is no longer running.
            self._log(f"Bot stopped by an unexpected error: {e}")
            self._stop_bot(self.STOP_ERROR)
        finally:
            self.capture.close()
    
    def stop(self):
        """Stops the bot"""
        self._manually_stopped = True  # Mark that user manually stopped the bot
        self._set_stop_reason(self.STOP_MANUAL)
        self.running = False
        if hasattr(self, '_jigsaw_bot') and self._jigsaw_bot:
            self._jigsaw_bot.stop()
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Bot stopped")


if __name__ == "__main__":
    from utils import DEBUG_MODE_EN
    if DEBUG_MODE_EN:
        from bot_gui import BotGUI
        gui = BotGUI()
        gui.run()
    else:
        from qt_gui import main
        main()

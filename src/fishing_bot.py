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
import time
from typing import Optional, Tuple, Dict

import cv2
import numpy as np
import pyautogui

# Disable PyAutoGUI fail-safe for multi-window automation
# When multiple bots run simultaneously, mouse movements can trigger the fail-safe
# This is safe because we have explicit click logic and input_lock synchronization
pyautogui.FAILSAFE = False

from mss import mss

try:
    from pynput import keyboard
    from pynput.keyboard import Controller, Key
except ImportError:
    from utils import DEBUG_PRINTS
    if DEBUG_PRINTS:
        print("ERROR: pynput not installed! Install with: pip install pynput")
    keyboard = None
    Controller = None
    Key = None

from utils import get_resource_path, input_lock, DEBUG_PRINTS
from window_manager import WindowManager, GameRegion
from fish_detector import FishDetector
from humanize import Humanizer
from input_backend import create_mouse_backend


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
    
    def __init__(self, region: GameRegion, config: dict, window_manager: WindowManager, 
                 bait_counter: int = 800, bait_keys: list = None, bot_id: int = 0):
        # Core components
        self.region = region
        self.config = config
        self.window_manager = window_manager
        self.detector = FishDetector()
        self.sct = None  # Screen capture (created per-thread)
        
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
        self._circle_radius_sq = 67 * 67
        
        # Lock fairness: prevent one thread from hogging the lock
        self._consecutive_lock_acquisitions = 0
        self._lock_acquisition_limit = 3  # Max consecutive acquisitions before yielding
        
        # Callbacks for GUI updates
        self.on_status_update = None
        self.on_stats_update = None
        self.on_bait_update = None  # Callback for bait counter changes
        self.on_bot_stop = None  # Callback when bot stops
        
        # Setup keyboard controller (shared, but access controlled by lock)
        self.keyboard_controller = None
        if keyboard and Controller:
            self.keyboard_controller = Controller()

        # Humanizer — wraps every mouse/keyboard call. When disabled, all wrappers
        # forward directly to pyautogui/pynput (current behavior preserved).
        self.mouse_backend = create_mouse_backend(
            config,
            status_callback=self.on_status_update,
            bot_id=self.bot_id,
        )
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

        # Timing cache — read from config once per session in play_game(), never inside loops
        self._t_cursor   = config.get('timing_cursor_settle',  0.012)
        self._t_hold     = config.get('timing_button_hold',    0.008)
        self._t_post     = config.get('timing_post_click',     0.035)
        self._t_human_mn = config.get('timing_human_min',      0.15)
        self._t_human_mx = config.get('timing_human_max',      0.40)
        self._t_key_hold = config.get('timing_key_hold',       0.025)
        self._t_key_set  = config.get('timing_key_settle',     0.030)
        self._t_interkey = config.get('timing_cast_interkey',  0.050)
        # Game-response waits (tunable via Timing Settings window)
        self._t_catch_wait  = config.get('timing_catch_wait',        0.400)
        self._t_open_wait   = config.get('timing_open_wait',         0.100)
        self._t_dead_check  = config.get('timing_dead_fish_check',   0.100)
        self._t_drop_settle = config.get('timing_drop_settle',       0.120)
        self._t_qs_between  = config.get('timing_quickskip_between', 0.100)
        self._t_qs_after    = config.get('timing_quickskip_after',   0.100)

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
        if FishingBot._template_cache is not None:
            return FishingBot._template_cache
        
        FishingBot._template_cache = {}
        assets_path = get_resource_path("assets")
        
        if not os.path.exists(assets_path):
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Assets folder not found!")
            return FishingBot._template_cache
        
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
                        FishingBot._template_cache[f] = (template_gray, w >> 1, h >> 1)
                except Exception as e:
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Error loading template {f}: {e}")
        
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Loaded {len(FishingBot._template_cache)} item templates (grayscale, cropped {border}px)")
        return FishingBot._template_cache
    
    def _load_color_template_cache(self) -> Dict[str, tuple]:
        """Loads color versions of confusable fish templates for disambiguation.
        Returns dict of {filename: (bgr_template, half_width, half_height)}"""
        if FishingBot._color_template_cache is not None:
            return FishingBot._color_template_cache
        
        FishingBot._color_template_cache = {}
        assets_path = get_resource_path("assets")
        
        if not os.path.exists(assets_path):
            return FishingBot._color_template_cache
        
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
                        FishingBot._color_template_cache[filename] = (template, w >> 1, h >> 1)
            except Exception:
                continue
        
        if self.on_status_update and FishingBot._color_template_cache:
            self.on_status_update(f"[W{self.bot_id+1}] Loaded {len(FishingBot._color_template_cache)} color templates for disambiguation")

        return FishingBot._color_template_cache

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
    
    def capture_inventory_area(self) -> np.ndarray:
        """Captures the inventory area (right 270px of the game window, starting at y=300)."""
        try:
            if self._stop_if_window_unavailable():
                return np.zeros((100, 100, 3), dtype=np.uint8)

            if self.sct is None:
                self.sct = mss()
            
            win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
            
            # Capture right 270px of window, starting from y=300 (skip top 300px and bottom 30px)
            monitor = {
                "left": win_left + win_width - self._inventory_width,
                "top": win_top + self._inventory_y_offset,
                "width": self._inventory_width,
                "height": max(0, win_height - self._inventory_y_offset - 30)
            }
            
            sct_img = self.sct.grab(monitor)
            return np.ascontiguousarray(np.asarray(sct_img, dtype=np.uint8)[:, :, :3])
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Error capturing inventory: {e}")
            return np.zeros((100, 100, 3), dtype=np.uint8)
    
    def identify_item_in_inventory(self, inventory_frame: np.ndarray, ignore_positions: set = None) -> Optional[Tuple[str, Tuple[int, int]]]:
        """Identifies an item in the inventory using template matching with high precision.
        Returns (filename, (x, y)) of best match or None if no match found.
        Coordinates are relative to inventory area.
        ignore_positions: set of (x, y) tuples to skip (dead fish locations).
        If first match is ignored, tries to find another match within same template.
        
        For confusable fish (Goldfish vs Large_zander), uses color-based disambiguation."""
        templates = self._load_template_cache()
        if not templates:
            return None
        
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
    
    def handle_caught_item(self):
        """Identifies and handles caught item based on fish_actions config.
        Should be called after a successful catch.
        After clicking, immediately checks if fish is still there - if so, adds to ignore list.
        
        IMPORTANT: The entire detection + click sequence must be atomic to prevent
        another bot from interfering between detection and action."""
        if not self.config.get('auto_fish_handling', False):
            # Even without auto handling, track inventory fullness so page-switching works
            try:
                time.sleep(self._t_catch_wait)
                inventory_frame = self.capture_inventory_area()
                self._empty_slot_positions = self._scan_empty_slots(inventory_frame)
                if not self._empty_slot_positions:
                    self._switch_inventory_page()
            except Exception:
                pass
            return

        fish_actions = self.config.get('fish_actions', {})
        if not fish_actions:
            return

        try:
            # Activate window and wait for item to land in inventory (outside lock — no input needed)
            with input_lock:
                self.window_manager.activate_window(force_activate=True)
            time.sleep(self._t_catch_wait)

            # Identify item (outside lock — read-only screen capture)
            inventory_frame = self.capture_inventory_area()
            match = self.identify_item_in_inventory(inventory_frame, ignore_positions=self._ignored_positions)
            if not match:
                return

            filename, (inv_x, inv_y) = match
            action = fish_actions.get(filename, 'keep')

            fish_name = filename.replace('_living.jpg', '').replace('_item.jpg', '')

            # ========== SINGLE LOCK COVERS THE ENTIRE INTERACTION ==========
            # Holding the lock from first click to last click prevents another
            # bot from moving the mouse between our steps.  Screen captures
            # (mss.grab) are safe inside the lock — they don't send input.
            with input_lock:
                self.window_manager.activate_window(force_activate=True)
                win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
                screen_x = win_left + win_width - self._inventory_width + inv_x
                screen_y = win_top + self._inventory_y_offset + inv_y
                win_center_x = win_left + win_width // 2
                win_center_y = win_top + win_height // 2

                if action == 'keep':
                    self._ignored_positions.add((inv_x, inv_y))
                    self._empty_slot_positions = [
                        (ex, ey) for ex, ey in self._empty_slot_positions
                        if not (abs(inv_x - ex) < 15 and abs(inv_y - ey) < 15)
                    ]
                    if self.on_status_update:
                        self.on_status_update(
                            f"[W{self.bot_id+1}] Keeping: {fish_name} "
                            f"(ignored, {len(self._empty_slot_positions)} empty slots left)")

                elif action == 'open':
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Opening: {fish_name}")

                    self.human.move_to(screen_x, screen_y)
                    self.human.sleep(0.05)
                    self.human.click(button='right')
                    self.human.sleep(self._t_open_wait)
                    self.human.move_to(win_center_x, win_center_y)

                    # Verify while still holding the lock (no other bot can interfere)
                    time.sleep(self._t_open_wait)
                    inv_check = self.capture_inventory_area()
                    still_there = self._is_item_at_position(inv_check, inv_x, inv_y)

                    if still_there:
                        time.sleep(self._t_dead_check)
                        inv_check2 = self.capture_inventory_area()
                        still_there = self._is_item_at_position(inv_check2, inv_x, inv_y)

                    if still_there:
                        # Dead fish — permanently occupies this slot
                        self._ignored_positions.add((inv_x, inv_y))
                        self._empty_slot_positions = [
                            (ex, ey) for ex, ey in self._empty_slot_positions
                            if not (abs(inv_x - ex) < 15 and abs(inv_y - ey) < 15)
                        ]
                    else:
                        # Fish was opened — slot is now free
                        if not any(abs(inv_x - ex) < 15 and abs(inv_y - ey) < 15
                                   for ex, ey in self._empty_slot_positions):
                            self._empty_slot_positions.append((inv_x, inv_y))
                            self._empty_slot_positions.sort(key=lambda p: (p[1] // 30, p[0]))

                elif action == 'drop':
                    confirm_pos = self.config.get('confirm_button_pos')
                    drop_pos    = self.config.get('drop_button_pos')

                    if not confirm_pos:
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] Confirm button not configured! Keeping: {fish_name}")
                        self._ignored_positions.add((inv_x, inv_y))
                        self._empty_slot_positions = [
                            (ex, ey) for ex, ey in self._empty_slot_positions
                            if not (abs(inv_x - ex) < 15 and abs(inv_y - ey) < 15)
                        ]
                        return  # exit inside lock — lock released by context manager

                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Dropping: {fish_name}")

                    is_fish = '_living' in filename
                    still_there = True

                    if is_fish:
                        # Right-click to test if fish can be opened
                        self.human.move_to(screen_x, screen_y)
                        self.human.sleep(0.05)
                        self.human.click(button='right')
                        self.human.sleep(self._t_open_wait)
                        self.human.move_to(win_center_x, win_center_y)

                        # Check result while still holding the lock
                        time.sleep(self._t_open_wait)
                        inv_check = self.capture_inventory_area()
                        still_there = self._is_item_at_position(inv_check, inv_x, inv_y)

                    if still_there:
                        # ========== DROP SEQUENCE (all inside lock) ==========
                        self.human.move_to(screen_x, screen_y)
                        self.human.sleep(0.05)
                        self.human.click()
                        self.human.sleep(self._t_drop_settle)

                        self.human.move_to(win_center_x, win_center_y)
                        self.human.sleep(0.05)
                        self.human.click()
                        self.human.sleep(self._t_drop_settle)

                        if drop_pos:
                            self.human.move_to(win_left + drop_pos[0], win_top + drop_pos[1])
                            self.human.sleep(0.05)
                            self.human.click()
                            self.human.sleep(self._t_drop_settle)

                        self.human.move_to(win_left + confirm_pos[0], win_top + confirm_pos[1])
                        self.human.sleep(0.05)
                        self.human.click()
                        self.human.sleep(self._t_drop_settle)

                        self.human.move_to(win_center_x, win_center_y)
            # ========== LOCK RELEASED ==========

            # Page-switch if needed — acquires its own lock internally
            if action == 'keep' and not self._empty_slot_positions:
                self._switch_inventory_page()
            elif action == 'open' and not self._empty_slot_positions:
                self._switch_inventory_page()

        except Exception:
            pass
        
    def _update_region_cache(self):
        """Updates cached constants when region changes."""
        if self.region:
            self._circle_center = (self.region.width >> 1, self.region.height >> 1)  # Bitwise divide by 2
        else:
            self._circle_center = None
    
    def capture_full_window(self) -> np.ndarray:
        """Captures the entire game window for initial detection."""
        try:
            if self._stop_if_window_unavailable():
                return np.zeros((100, 100, 3), dtype=np.uint8)

            if self.sct is None:
                self.sct = mss()
            
            win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
            
            monitor = {
                "left": win_left,
                "top": win_top,
                "width": win_width,
                "height": win_height
            }
            
            sct_img = self.sct.grab(monitor)
            return np.ascontiguousarray(np.asarray(sct_img, dtype=np.uint8)[:, :, :3])
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"Screenshot error: {e}")
            return np.zeros((100, 100, 3), dtype=np.uint8)
    
    def capture_screen(self) -> np.ndarray:
        """Captures the game region as a numpy array for processing."""
        try:
            if self._stop_if_window_unavailable():
                if self.region:
                    return np.zeros((self.region.height, self.region.width, 3), dtype=np.uint8)
                return np.zeros((100, 100, 3), dtype=np.uint8)

            if self.sct is None:
                self.sct = mss()

            if not self.region:
                return self.capture_full_window()

            win_left, win_top, _, _ = self.window_manager.get_window_rect()
            screen_left = win_left + self.region.left
            screen_top = win_top + self.region.top

            monitor = {
                "left": screen_left,
                "top": screen_top,
                "width": self.region.width,
                "height": self.region.height
            }

            sct_img = self.sct.grab(monitor)
            # Direct slice from BGRA buffer to contiguous BGR view — avoids the full
            # cvtColor pass over every pixel (significant on hot-loop captures).
            return np.ascontiguousarray(np.asarray(sct_img, dtype=np.uint8)[:, :, :3])
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"Screenshot error: {e}")
            if self.region:
                return np.zeros((self.region.height, self.region.width, 3), dtype=np.uint8)
            return np.zeros((100, 100, 3), dtype=np.uint8)
    
    def atomic_capture_and_click(self) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Captures screen and clicks fish if in circle. Optimized single-pass detection.
        Returns: (minigame_active, fish_position_clicked or None)"""
        # Check pause state before starting any operation
        if self.paused:
            return (True, None)

        # Local references for speed
        capture = self.capture_screen
        detect = self.detector.detect_window_and_fish
        circle_center = self._circle_center
        radius_sq = self._circle_radius_sq
        region_left = self.region.left
        region_top = self.region.top
        
        try:
            # ========== PHASE 1: Quick pre-check (NO LOCK) ==========
            frame = capture()
            window_active, fish_pos = detect(frame)
            
            if not window_active:
                return (False, None)
            if not fish_pos:
                return (True, None)
            
            # Inline circle check for speed
            fx, fy = fish_pos
            cx, cy = circle_center
            dx, dy = fx - cx, fy - cy
            if (dx * dx + dy * dy) >= radius_sq:
                # Fish not in circle - reset consecutive lock counter
                self._consecutive_lock_acquisitions = 0
                return (True, None)
            
            # Check pause again before acquiring lock
            if self.paused:
                return (True, None)

            # Fish is in circle! Now get lock and click
            # ========== PHASE 2: Fresh capture + click (WITH LOCK) ==========
            with input_lock:
                # Check pause inside lock before any actions
                if self.paused:
                    self._consecutive_lock_acquisitions = 0
                    return (True, None)

                # Activate window
                self.window_manager.activate_window(force_activate=True)
                
                # RE-CAPTURE fresh frame
                frame = capture()
                window_active, fish_pos = detect(frame)
                
                if not window_active:
                    self._consecutive_lock_acquisitions = 0
                    return (False, None)
                if not fish_pos:
                    self._consecutive_lock_acquisitions = 0
                    return (True, None)
                
                # Inline circle check
                fx, fy = fish_pos
                dx, dy = fx - cx, fy - cy
                if (dx * dx + dy * dy) >= radius_sq:
                    self._consecutive_lock_acquisitions = 0
                    return (True, None)
                
                # Check pause one final time before clicking
                if self.paused:
                    self._consecutive_lock_acquisitions = 0
                    return (True, None)

                # Click at FRESH position
                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                screen_x = win_left + region_left + fx
                screen_y = win_top + region_top + fy
                
                # Click sequence — uses the full humanized path (curved, 130-750ms).
                # When the human-like setting is OFF, move_to falls through to instant
                # pyautogui.moveTo so the minigame hit path stays as fast as before.
                self.human.move_to(screen_x, screen_y)
                time.sleep(self._t_cursor)
                self.human.click(hold=self._t_hold)
                # mouseUp is already sent to the OS — release lock immediately
                self._consecutive_lock_acquisitions += 1
            # ========== LOCK RELEASED ==========

            # Post-click settle and fairness yield both happen outside lock so
            # other threads can acquire it without waiting for our sleeps
            time.sleep(self._t_post)

            # Fairness: yield to other threads if this thread has been acquiring lock too often
            if self._consecutive_lock_acquisitions >= self._lock_acquisition_limit:
                self._consecutive_lock_acquisitions = 0
                time.sleep(0.05)  # 50ms yield to allow other threads to compete for lock
            
            return (True, fish_pos)
            
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Click error: {e}")
            return (True, None)
    
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
            self.on_status_update(f"[W{self.bot_id+1}] 2 consecutive failures! Bait adjusted to {self.bait_counter}")
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
                self.window_manager.activate_window()
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
    
    def bait_and_cast(self):
        """Selects bait and casts fishing line in a single lock acquisition."""
        if not self.keyboard_controller:
            return

        bait_key = self.get_bait_key(self.bait_counter)
        key_map = {'space': Key.space, 'F1': Key.f1, 'F2': Key.f2, 'F3': Key.f3, 'F4': Key.f4}

        with input_lock:
            try:
                self.window_manager.activate_window()
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
    
    def quickskip(self):
        """Performs quick skip - uses different method based on mode (horse or armour)."""
        # Get quick skip mode from config (default to 'horse' if not set)
        quick_skip_mode = self.config.get('quick_skip_mode', 'horse')
        
        if quick_skip_mode == 'horse':
            # Horse mode: double press CTRL+G
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Quick skip (Horse mode - CTRL+G)...")
            self.press_ctrl_key('g')
            time.sleep(0.1)  # Longer delay for game to process first CTRL+G
            self.press_ctrl_key('g')
            time.sleep(0.1)  # Delay after second press before next action
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
                self.window_manager.activate_window(force_activate=True)
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
                self.window_manager.activate_window()
                self.human.sleep(self._t_key_set)

                pynput_key = key_map.get(key.upper() if len(key) > 1 else key, key_map.get(key, key))

                self.human.tap_key(pynput_key, hold=self._t_key_hold)

                if description and self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] {description}")
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error pressing key '{key}': {e}")
    
    def wait_for_minigame_window(self, timeout: float = 6.0) -> bool:
        """Waits for and finds the fishing minigame window. Auto-calibrates region on first detection.
        Returns True if minigame detected, False otherwise."""
        start_time = time.time()
        
        while self.running and time.time() - start_time < timeout:
            if self.paused:
                time.sleep(0.1)
                continue
            
            try:
                # On first detection, find and calibrate the region
                if not self.region_auto_calibrated:
                    frame = self.capture_full_window()
                    bounds = self.detector.find_fishing_window_bounds(frame)
                    if bounds:
                        x, y, w, h = bounds
                        self.region = GameRegion(x, y, w, h)
                        self.region_auto_calibrated = True
                        self._update_region_cache()  # Update cached constants
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] Auto-calibrated region: {w}x{h} at ({x},{y})")
                        return True
                else:
                    # Use standard detection after calibration
                    frame = self.capture_screen()
                    window_active, _ = self.detector.detect_window_and_fish(frame)
                    if window_active:
                        return True
                
                time.sleep(0.05)  # Faster polling for quicker minigame detection
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error: {e}")
                time.sleep(0.05)
        
        return False
    
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

    def _switch_inventory_page(self):
        """Advances through remaining configured inventory pages until one with empty slots is found.
        If all pages are full, stops the bot and plays the rickroll beep."""
        while True:
            next_page = self._current_inv_page + 1
            page_num = next_page + 1  # 1-based

            if page_num > 8:
                break

            page_pos = self.config.get(f'inv_page_{page_num}_pos')
            if not page_pos:
                break

            self._current_inv_page = next_page
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Switching to inventory page {page_num}")

            with input_lock:
                self.window_manager.activate_window(force_activate=True)
                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                self._click_page_tab(win_left + page_pos[0], win_top + page_pos[1])

            time.sleep(0.3)
            self._rescan_inventory_state()

            if self._empty_slot_positions:
                return  # Found a page with space — done

            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Page {page_num} also full, trying next...")

        # All configured pages are full — stop the bot
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] All inventory pages full — stopping bot")
        self._set_stop_reason(self.STOP_INVENTORY_FULL)
        self.running = False
        self._notify_bot_stop_once()

    def _startup_scan_and_process_all_pages(self):
        """At startup, navigate every configured inventory page and find one with empty slots.
        Auto handling on: open/drop processable items on each page before scanning.
        Auto handling off: just scan each page and mark existing items as ignored.
        After scanning all pages, navigates to the first page that has empty slots.
        If every page is full, stops the bot immediately (plays rickroll)."""
        auto = self.config.get('auto_fish_handling', False)

        if not auto:
            # Non-auto mode: navigate pages in order, stop on the first one with empty slots.
            # No need to scan every page — we just need somewhere to put fish.
            for page_idx in range(8):
                page_num = page_idx + 1
                page_pos = self.config.get(f'inv_page_{page_num}_pos')
                if page_idx > 0 and not page_pos:
                    break  # Hit an unconfigured page — no more pages

                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Startup: checking inventory page {page_num}")

                if page_pos:
                    with input_lock:
                        self.window_manager.activate_window(force_activate=True)
                        win_left, win_top, _, _ = self.window_manager.get_window_rect()
                        self._click_page_tab(win_left + page_pos[0], win_top + page_pos[1])
                    time.sleep(0.3)
                else:
                    with input_lock:
                        self.window_manager.activate_window(force_activate=True)
                    time.sleep(0.3)

                self._current_inv_page = page_idx
                self._ignored_positions.clear()
                self._empty_slot_positions.clear()
                self._scan_existing_inventory()

                if self._empty_slot_positions:
                    return  # Found a page with space — done

            # All configured pages are full
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] All inventory pages full at startup — stopping bot")
            self._set_stop_reason(self.STOP_INVENTORY_FULL)
            self.running = False
            self._notify_bot_stop_once()
            return

        # Auto mode: scan every page and process (open/drop) items before deciding where to settle.
        fish_actions = self.config.get('fish_actions', {})
        first_page_with_empty = None

        for page_idx in range(8):
            page_num = page_idx + 1
            page_pos = self.config.get(f'inv_page_{page_num}_pos')

            if page_idx > 0 and not page_pos:
                break

            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Startup: scanning inventory page {page_num}")

            if page_pos:
                with input_lock:
                    self.window_manager.activate_window(force_activate=True)
                    win_left, win_top, _, _ = self.window_manager.get_window_rect()
                    self._click_page_tab(win_left + page_pos[0], win_top + page_pos[1])
                time.sleep(0.3)
            else:
                with input_lock:
                    self.window_manager.activate_window(force_activate=True)
                time.sleep(0.3)

            self._current_inv_page = page_idx
            self._ignored_positions.clear()
            self._empty_slot_positions.clear()
            self._startup_process_page(fish_actions)

            if self._empty_slot_positions and first_page_with_empty is None:
                first_page_with_empty = page_idx

        if first_page_with_empty is None:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] All inventory pages full at startup — stopping bot")
            self._set_stop_reason(self.STOP_INVENTORY_FULL)
            self.running = False
            self._notify_bot_stop_once()
            return

        # Navigate back to the first page that has empty slots (if we advanced past it)
        navigated_back = False
        if self._current_inv_page != first_page_with_empty:
            target_page_num = first_page_with_empty + 1
            page_pos = self.config.get(f'inv_page_{target_page_num}_pos')
            if page_pos:
                if self.on_status_update:
                    self.on_status_update(
                        f"[W{self.bot_id+1}] Startup: returning to page {target_page_num} (first with empty slots)")
                with input_lock:
                    self.window_manager.activate_window(force_activate=True)
                    win_left, win_top, _, _ = self.window_manager.get_window_rect()
                    self._click_page_tab(win_left + page_pos[0], win_top + page_pos[1])
                time.sleep(0.5)  # increased: give the game time to switch pages
                self._current_inv_page = first_page_with_empty
                self._ignored_positions.clear()
                self._empty_slot_positions.clear()
                self._startup_process_page(fish_actions)
                navigated_back = True

        # Fallback: if the target page turned out to be full (items placed into its
        # empty slots while processing other pages, or the tab click didn't register),
        # find the next page that has space rather than starting with 0 tracked slots.
        if navigated_back and not self._empty_slot_positions:
            if self.on_status_update:
                self.on_status_update(
                    f"[W{self.bot_id+1}] Startup: page {first_page_with_empty + 1} now full — scanning for next available page")
            self._switch_inventory_page()

    def _startup_process_page(self, fish_actions: dict) -> None:
        """Processes a single inventory page during startup.
        Pass 1: scan all items — 'keep' items go into _ignored_positions.
        Pass 2: loop identify_item_in_inventory (which skips ignored) and call
                _startup_handle_item for each open/drop item until none remain.
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
            while processed_count < max_items:
                inventory_frame = self.capture_inventory_area()
                match = self.identify_item_in_inventory(inventory_frame, ignore_positions=self._ignored_positions)
                if not match:
                    break
                filename, (inv_x, inv_y) = match
                action = fish_actions.get(filename, 'keep')
                if action == 'keep':
                    self._ignored_positions.add((inv_x, inv_y))
                    continue
                self._startup_handle_item(filename, inv_x, inv_y, action)
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

    def _startup_handle_item(self, filename: str, inv_x: int, inv_y: int, action: str) -> None:
        """Executes open/drop for a single item found during startup scan.
        Mirrors handle_caught_item() — single lock covers the entire sequence so
        no other bot can interleave mouse operations mid-action."""
        fish_name = filename.replace('_living.jpg', '').replace('_item.jpg', '')
        try:
            with input_lock:
                self.window_manager.activate_window(force_activate=True)
                win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
                screen_x = win_left + win_width - self._inventory_width + inv_x
                screen_y = win_top + self._inventory_y_offset + inv_y
                win_cx = win_left + win_width // 2
                win_cy = win_top + win_height // 2

                if action == 'open':
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Startup opening: {fish_name}")

                    self.human.move_to(screen_x, screen_y)
                    self.human.sleep(0.05)
                    self.human.click(button='right')
                    self.human.sleep(self._t_open_wait)
                    self.human.move_to(win_cx, win_cy)

                    time.sleep(self._t_open_wait)
                    still_there = self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y)
                    if still_there:
                        time.sleep(self._t_dead_check)
                        if self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y):
                            self._ignored_positions.add((inv_x, inv_y))

                elif action == 'drop':
                    confirm_pos = self.config.get('confirm_button_pos')
                    if not confirm_pos:
                        self._ignored_positions.add((inv_x, inv_y))
                        return

                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Startup dropping: {fish_name}")

                    is_fish = '_living' in filename
                    still_there = True

                    if is_fish:
                        self.human.move_to(screen_x, screen_y)
                        self.human.sleep(0.05)
                        self.human.click(button='right')
                        self.human.sleep(self._t_open_wait)
                        self.human.move_to(win_cx, win_cy)

                        time.sleep(self._t_open_wait)
                        still_there = self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y)

                    if still_there:
                        drop_pos = self.config.get('drop_button_pos')

                        self.human.move_to(screen_x, screen_y)
                        time.sleep(np.random.uniform(0.05, 0.07))
                        self.human.click()
                        self.human.sleep(self._t_drop_settle)

                        self.human.move_to(win_cx, win_cy)
                        time.sleep(np.random.uniform(0.05, 0.07))
                        self.human.click()
                        self.human.sleep(self._t_drop_settle)

                        if drop_pos:
                            self.human.move_to(win_left + drop_pos[0], win_top + drop_pos[1])
                            time.sleep(np.random.uniform(0.05, 0.07))
                            self.human.click()
                            self.human.sleep(self._t_drop_settle)

                        self.human.move_to(win_left + confirm_pos[0], win_top + confirm_pos[1])
                        time.sleep(np.random.uniform(0.05, 0.07))
                        self.human.click()
                        self.human.sleep(self._t_drop_settle)

                        self.human.move_to(win_cx, win_cy)

                        # Verify drop succeeded while still holding lock
                        time.sleep(self._t_drop_settle)
                        if self._is_item_at_position(self.capture_inventory_area(), inv_x, inv_y):
                            self._ignored_positions.add((inv_x, inv_y))

        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Error in startup item handler: {e}")
            self._ignored_positions.add((inv_x, inv_y))

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

        capture_full_window = self.capture_full_window
        perf_counter = time.perf_counter

        while self.running and perf_counter() - start_time < timeout:
            if self.paused:
                time.sleep(0.1)
                continue

            try:
                frame = capture_full_window()
                frame_gray = cvtColor(frame, BGR2GRAY)

                # Crop to 250px wide centered bar, upper half only for performance
                f_h, f_w = frame_gray.shape
                center_x = f_w >> 1
                crop_left = max(0, center_x - 125)
                crop_right = min(f_w, center_x + 125)
                crop_bottom = f_h >> 1  # Only upper half
                frame_gray = frame_gray[:crop_bottom, crop_left:crop_right]

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
    
    def play_game(self):
        """Main game loop implementing the fishing minigame workflow."""
        # Refresh timing cache from config once per session — never read inside loops
        self._t_cursor   = self.config.get('timing_cursor_settle',  0.012)
        self._t_hold     = self.config.get('timing_button_hold',    0.008)
        self._t_post     = self.config.get('timing_post_click',     0.035)
        self._t_human_mn = self.config.get('timing_human_min',      0.15)
        self._t_human_mx = self.config.get('timing_human_max',      0.40)
        self._t_key_hold = self.config.get('timing_key_hold',       0.025)
        self._t_key_set  = self.config.get('timing_key_settle',     0.030)
        self._t_interkey = self.config.get('timing_cast_interkey',  0.050)
        self._t_catch_wait  = self.config.get('timing_catch_wait',        0.400)
        self._t_open_wait   = self.config.get('timing_open_wait',         0.100)
        self._t_dead_check  = self.config.get('timing_dead_fish_check',   0.100)
        self._t_drop_settle = self.config.get('timing_drop_settle',       0.120)
        self._t_qs_between  = self.config.get('timing_quickskip_between', 0.100)
        self._t_qs_after    = self.config.get('timing_quickskip_after',   0.100)

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
        
        # Scan all inventory pages at startup, process open/drop items, then settle on
        # the first page that still has empty slots.
        self._startup_scan_and_process_all_pages()

        _was_paused = False

        while self.running and self.bait_counter > 0:
            if self._stop_if_window_unavailable():
                break

            if self.paused:
                _was_paused = True
                time.sleep(0.1)
                continue

            if _was_paused:
                _was_paused = False
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Resumed — re-scanning inventory...")
                self._startup_scan_and_process_all_pages()
                if not self.running:
                    break

            try:
                self.bait_and_cast()
                
                # Only play minigame if Classic Fishing system is NOT enabled
                if not self.config.get('classic_fishing', False):
                    minigame_detected = self.wait_for_minigame_window(timeout=6)
                    if not self.running:
                        break
                    if not minigame_detected:
                        self.consecutive_failures += 1
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] Minigame not detected ({self.consecutive_failures}/5)")
                        
                        if self.consecutive_failures >= 5:
                            self.adjust_bait_tier()
                            if self.bait_counter <= 0:
                                self._stop_bot(
                                    self.STOP_BAIT_DEPLETED,
                                    f"[W{self.bot_id+1}] Bait depleted after consecutive failures. Stopping bot."
                                )
                                break
                        
                        continue
                    
                    # Reset failure counter on successful minigame detection
                    self.consecutive_failures = 0
                    
                    minigame_active = True
                    human_like = self.config.get('human_like_clicking', True)
                    
                    while self.running and minigame_active:
                        if self.paused:
                            _was_paused = True
                            time.sleep(0.1)
                            continue

                        # Small delay between attempts (minimized for responsiveness)
                        if human_like:
                            time.sleep(np.random.uniform(self._t_human_mn, self._t_human_mx))
                        
                        try:
                            # Atomic operation: capture + detect + click all within lock
                            window_active, fish_pos = self.atomic_capture_and_click()
                            
                            if not window_active:
                                # Minigame ended
                                minigame_active = False
                                self.total_games += 1
                                self.bait_counter -= 1
                                
                                if self.on_status_update:
                                    self.on_status_update(f"[W{self.bot_id+1}] Game finished. Total: {self.total_games}, Bait: {self.bait_counter}")
                                if self.on_bait_update:
                                    self.on_bait_update(self.bot_id, self.bait_counter)
                                if self.on_stats_update:
                                    self.on_stats_update(self.bot_id, 0, self.total_games, self.bait_counter)
                                
                                # Handle caught item (if auto fish handling is enabled)
                                self.handle_caught_item()
                                break
                            
                            if fish_pos:
                                self.hits += 1
                                if self.on_stats_update:
                                    self.on_stats_update(self.bot_id, self.hits, self.total_games, self.bait_counter)
                                
                        except Exception as e:
                            if self.on_status_update:
                                self.on_status_update(f"[W{self.bot_id+1}] Error: {e}")
                    
                    self.hits = 0
                    if self.bait_counter > 0:
                        if self.config.get('quick_skip', False):
                            self.quickskip()
                        else:
                            # Interruptible wait that respects pause state
                            wait_time = np.random.uniform(4, 4.5)
                            wait_end = time.time() + wait_time
                            while time.time() < wait_end and self.running:
                                if self.paused:
                                    _was_paused = True
                                    time.sleep(0.1)
                                    continue
                                time.sleep(0.05)
                else:
                    # Classic Fishing system - wait for fish indicator, then reel in
                    # Step 1: Wait for classic fish image to appear
                    fish_found = self.wait_for_classic_fish(timeout=40)
                    
                    # Check if bot was stopped during wait
                    if not self.running:
                        break
                    
                    if not fish_found:
                        # Timeout waiting for fish - handle consecutive failures
                        self.consecutive_failures += 1
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] No fish bite detected ({self.consecutive_failures}/2), recasting...")
                        
                        if self.consecutive_failures >= 2:
                            self.adjust_bait_tier()
                            if self.bait_counter <= 0:
                                self._stop_bot(
                                    self.STOP_BAIT_DEPLETED,
                                    f"[W{self.bot_id+1}] Bait depleted after consecutive failures. Stopping bot."
                                )
                                break
                            
                        # Press CTRL+G once per failure to dismount horse if that's the issue
                        # First failure: try to dismount if on horse
                        # Second failure: you actually mounted in first attemp and now you need to unmount
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] Pressing CTRL+G to dismount horse...")
                        self.press_ctrl_key('g')
                        time.sleep(0.15)
                        continue
                    
                    # Reset failure counter on successful fish detection
                    self.consecutive_failures = 0
                    
                    # Handle pause before reeling in
                    while self.paused and self.running:
                        _was_paused = True
                        time.sleep(0.1)
                    if not self.running:
                        break
                    
                    # Timer already elapsed in wait_for_classic_fish - press space to reel in
                    # Acquire lock and activate window BEFORE pressing space (critical timing)
                    self.press_key('space', "Reel in fish")
                    time.sleep(0.05)
                    
                    # Handle caught item (if auto fish handling is enabled)
                    self.handle_caught_item()
                    
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Reeling in fish")
                    
                    # Update counters
                    self.total_games += 1
                    self.bait_counter -= 1
                    
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Classic catch! Total: {self.total_games}, Bait: {self.bait_counter}")
                    if self.on_bait_update:
                        self.on_bait_update(self.bot_id, self.bait_counter)
                    if self.on_stats_update:
                        self.on_stats_update(self.bot_id, 0, self.total_games, self.bait_counter)
                    
                    # Check if bot stopped before waiting
                    if not self.running:
                        break
                    
                    # Step 4: Quick skip or wait before next cast (with interruptible waits)
                    if self.bait_counter > 0:
                        if self.config.get('quick_skip', False):
                            # Interruptible 1 second wait
                            wait_end = time.time() + 0.5
                            while time.time() < wait_end and self.running:
                                if self.paused:
                                    _was_paused = True
                                    time.sleep(0.1)
                                    continue
                                time.sleep(0.05)
                            if not self.running:
                                break
                            self.quickskip()
                        else:
                            # Interruptible random wait
                            wait_time = np.random.uniform(4, 4.5)
                            wait_end = time.time() + wait_time
                            while time.time() < wait_end and self.running:
                                if self.paused:
                                    _was_paused = True
                                    time.sleep(0.1)
                                    continue
                                time.sleep(0.05)
                
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error in play_game: {e}")
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
        self.play_game()
    
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

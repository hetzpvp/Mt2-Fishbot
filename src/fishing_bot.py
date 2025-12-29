"""
MT2 Fishing Bot - Multi-Window Support
Automated fishing minigame bot for Metin2
Author: boristei

Main entry point - imports all modules and starts the GUI
"""

import ctypes

# === Windows DPI Awareness ===
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
    print("ERROR: pynput not installed! Install with: pip install pynput")
    keyboard = None
    Controller = None
    Key = None

from utils import get_resource_path, input_lock, play_rickroll_beep
from window_manager import WindowManager, GameRegion
from fish_detector import FishDetector


class FishingBot:
    """Main bot that plays the fishing minigame - one instance per game window"""
    
    # Class-level template cache (shared by all bot instances - loaded only once)
    _template_cache = None
    _template_border_crop = 7  # Pixels to crop from each edge of templates
    _classic_fish_template = None  # Cache for classic fish detection template
    
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
        
        # Cached circle values for performance
        self._circle_center = None
        self._circle_radius = 67
        self._circle_radius_sq = 67 * 67
        
        # Callbacks for GUI updates
        self.on_status_update = None
        self.on_stats_update = None
        self.on_pause_toggle = None
        self.on_bait_update = None  # Callback for bait counter changes
        self.on_bot_stop = None  # Callback when bot stops
        
        # Setup keyboard controller (shared, but access controlled by lock)
        self.keyboard_controller = None
        if keyboard and Controller:
            self.keyboard_controller = Controller()
        
        # Inventory capture width (right side of window where items appear)
        self._inventory_width = 200
        
        # Inventory capture Y offset (skip top 300px of window)
        self._inventory_y_offset = 200
        
        # Dead fish tracking: ignored slot positions (10 pixel radius around center)
        self._ignored_positions = set()  # Positions confirmed as dead fish
        
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
                        # Convert to grayscale for matching
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
    
    def capture_inventory_area(self) -> np.ndarray:
        """Captures the inventory area (right 270px of the game window, starting at y=300)."""
        try:
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
            frame = np.array(sct_img)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            return frame
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Error capturing inventory: {e}")
            return np.zeros((100, 100, 3), dtype=np.uint8)
    
    def identify_item_in_inventory(self, inventory_frame: np.ndarray, ignore_positions: set = None) -> Optional[Tuple[str, Tuple[int, int]]]:
        """Identifies an item in the inventory using template matching with high precision.
        Returns (filename, (x, y)) of best match or None if no match found.
        Coordinates are relative to inventory area.
        ignore_positions: set of (x, y) tuples to skip (dead fish locations).
        If first match is ignored, tries to find another match within same template."""
        templates = self._load_template_cache()
        if not templates:
            return None
        
        # Convert inventory to grayscale once
        inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape
        
        # Local references for speed
        match_template = cv2.matchTemplate
        minMaxLoc = cv2.minMaxLoc
        TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
        CONFIDENCE_THRESHOLD = 0.80  # Lowered from 0.8 for better detection
        EARLY_EXIT_THRESHOLD = 0.90  # Near-perfect match, skip remaining templates
        
        best_match = None
        best_confidence = CONFIDENCE_THRESHOLD  # Start at threshold (only accept better)
        
        for filename, (template, half_w, half_h) in templates.items():
            t_h, t_w = template.shape
            
            # Skip if template larger than inventory
            if t_h > inv_h or t_w > inv_w:
                continue
            
            try:
                result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)
                result_copy = result.copy()
                
                # Try to find first non-ignored match for this template
                match_count = 0
                while True:
                    _, max_val, _, max_loc = minMaxLoc(result_copy)
                    
                    # Stop if no more good matches
                    if max_val <= 0.5:
                        break
                    
                    pt_x, pt_y = max_loc
                    center_x = pt_x + half_w
                    center_y = pt_y + half_h
                    match_count += 1
                    
                    # Check if this match is in ignore list
                    is_ignored = False
                    if ignore_positions:
                        for ix, iy in ignore_positions:
                            if abs(center_x - ix) < 10 and abs(center_y - iy) < 10:
                                is_ignored = True
                                break
                    
                    # If not ignored and better than current best, accept it
                    if not is_ignored and max_val > best_confidence:
                        best_confidence = max_val
                        best_match = (filename, (center_x, center_y))
                        
                        # Early exit on near-perfect match
                        if best_confidence >= EARLY_EXIT_THRESHOLD:
                            return best_match
                        break  # Found good match for this template, move to next template
                    
                    # Mask out this match to try next one within same template
                    mask_x1 = max(0, pt_x - t_w // 2)
                    mask_y1 = max(0, pt_y - t_h // 2)
                    mask_x2 = min(result_copy.shape[1], pt_x + t_w // 2 + 1)
                    mask_y2 = min(result_copy.shape[0], pt_y + t_h // 2 + 1)
                    result_copy[mask_y1:mask_y2, mask_x1:mask_x2] = -1.0
                    
            except Exception as e:
                continue
        
        return best_match
    
    def _is_item_at_position(self, inventory_frame: np.ndarray, x: int, y: int, radius: int = 10) -> bool:
        """Checks if any fish/item template matches at the given position (within radius).
        Used for dead fish detection - checks if an item is still there after clicking.
        Optimized: pre-computed dimensions, local variable caching, early termination."""
        templates = self._load_template_cache()
        if not templates:
            return False
        
        # Convert once
        inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape
        
        # Local references for speed
        match_template = cv2.matchTemplate
        where = np.where
        TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
        
        for filename, (template, half_w, half_h) in templates.items():
            t_h, t_w = template.shape
            
            if t_h > inv_h or t_w > inv_w:
                continue
            
            try:
                result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)
                locations = where(result >= 0.8)
                
                # Fast path: no matches
                if locations[0].size == 0:
                    continue
                
                # Check if any match is at our target position
                for pt_y, pt_x in zip(locations[0], locations[1]):
                    center_x = pt_x + half_w
                    center_y = pt_y + half_h
                    
                    if abs(center_x - x) < radius and abs(center_y - y) < radius:
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
            return
        
        fish_actions = self.config.get('fish_actions', {})
        if not fish_actions:
            return
        
        try: 
            # ========== ACQUIRE LOCK FOR ENTIRE DETECTION + ACTION SEQUENCE ==========
            # This prevents another bot from moving the mouse/clicking between our
            # detection and action, which could cause clicking on the wrong fish
            with input_lock:
                # Activate our window first
                self.window_manager.activate_window(force_activate=True)
                # Small delay for item to appear in inventory
                time.sleep(0.2)
                
                # Capture inventory area
                inventory_frame = self.capture_inventory_area()
                
                # Identify the item (ignoring known dead fish positions)
                match = self.identify_item_in_inventory(inventory_frame, ignore_positions=self._ignored_positions)
                            
                if not match:
                    return  # No item found, that's OK (not every catch gives an item)
                
                filename, (inv_x, inv_y) = match
                
                action = fish_actions.get(filename, 'keep')
                
                if action == 'keep':
                    # Item stays in inventory - add to ignore list so we don't process it again
                    self._ignored_positions.add((inv_x, inv_y))
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Keeping: {filename.replace('_living.jpg', '').replace('_item.jpg', '')} (ignored)")
                        
                elif action == 'open':
                    # Right-click to open fish - coordinates already computed, window already active
                    if self.on_status_update:
                        self.on_status_update(f"[W{self.bot_id+1}] Opening: {filename.replace('_living.jpg', '').replace('_item.jpg', '')}")
                    
                    # Convert inventory-relative coords to screen coords
                    win_left, win_top, win_width, _ = self.window_manager.get_window_rect()
                    screen_x = win_left + win_width - self._inventory_width + inv_x
                    screen_y = win_top + self._inventory_y_offset + inv_y
                    
                    # Right-click sequence (already inside lock)
                    pyautogui.moveTo(screen_x, screen_y, _pause=False)
                    time.sleep(0.05)
                    pyautogui.click(button='right', _pause=False)
                    time.sleep(0.2)  # Wait for game to process
                    
                    # Move cursor to center of the window (safe position)
                    win_center_x = win_left + win_width // 2
                    win_center_y = win_top + 400  # Upper-middle area of window
                    pyautogui.moveTo(win_center_x, win_center_y, _pause=False)
                    
            # ========== LOCK RELEASED ==========
            
            # Dead fish detection can happen outside lock (read-only captures)
            if action == 'open':
                time.sleep(0.1)  # Wait for right click to register
                
                inventory_frame_after = self.capture_inventory_area()
                
                # Check if the SAME fish is still at the SAME position
                still_there = self._is_item_at_position(inventory_frame_after, inv_x, inv_y)
                
                # Safety check: wait 100ms and verify again to be absolutely sure
                if still_there:
                    time.sleep(0.1)  # Safety delay
                    inventory_frame_safety = self.capture_inventory_area()
                    still_there_safety = self._is_item_at_position(inventory_frame_safety, inv_x, inv_y)
                    
                    # Only mark as dead if BOTH checks confirm it's still there
                    if still_there_safety:
                        self._ignored_positions.add((inv_x, inv_y))
                
            elif action == 'drop':
                # TODO: Implement drop functionality later
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Drop not implemented yet: {filename}")
                pass
                
        except Exception as e:
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
            frame = np.array(sct_img)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            return frame
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"Screenshot error: {e}")
            return np.zeros((100, 100, 3), dtype=np.uint8)
    
    def capture_screen(self) -> np.ndarray:
        """Captures the game region as a numpy array for processing."""
        try:
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
            frame = np.array(sct_img)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            return frame
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"Screenshot error: {e}")
            if self.region:
                return np.zeros((self.region.height, self.region.width, 3), dtype=np.uint8)
            return np.zeros((100, 100, 3), dtype=np.uint8)
    
    def atomic_capture_and_click(self) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Captures screen and clicks fish if in circle. Two-phase: pre-check then lock+click.
        Returns: (minigame_active, fish_position_clicked or None)"""
        try:
            # ========== PHASE 1: Quick pre-check (NO LOCK) ==========
            frame = self.capture_screen()
            
            # Combined detection: single HSV conversion
            window_active, fish_pos = self.detector.detect_window_and_fish(frame)
            if not window_active:
                return (False, None)
            if not fish_pos:
                return (True, None)
            
            # Use cached circle values (no division)
            if not self.is_fish_in_circle(fish_pos):
                return (True, None)
            
            # Fish is in circle! Now get lock and capture FRESH position
            
            # ========== PHASE 2: Fresh capture + click (WITH LOCK) ==========
            # Pre-compute region offset once (outside critical section)
            region_left, region_top = self.region.left, self.region.top
            
            with input_lock:
                # Activate window
                self.window_manager.activate_window(force_activate=True)
                
                # RE-CAPTURE fresh frame
                frame = self.capture_screen()
                
                # Combined detection with single HSV conversion
                window_active, fish_pos = self.detector.detect_window_and_fish(frame)
                if not window_active:
                    return (False, None)
                if not fish_pos or not self.is_fish_in_circle(fish_pos):
                    return (True, None)
                
                # Click at FRESH position - inline coordinate conversion
                x, y = fish_pos
                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                screen_x = win_left + region_left + x
                screen_y = win_top + region_top + y
                
                # Optimized click sequence
                pyautogui.moveTo(screen_x, screen_y, _pause=False)
                time.sleep(0.012)  # Slightly reduced settle time
                pyautogui.mouseDown(_pause=False)
                time.sleep(0.008)  # Minimal down time
                pyautogui.mouseUp(_pause=False)
                time.sleep(0.035)  # Post-click settle
            # ========== LOCK RELEASED ==========
            
            return (True, fish_pos)
            
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Click error: {e}")
            return (True, None)
    
    def is_fish_in_circle(self, fish_pos: Tuple[int, int], 
                          circle_info: Tuple[int, int, int] = None) -> bool:
        """Checks if the detected fish is within the game circle.
        Uses squared distance comparison to avoid expensive sqrt."""
        fx, fy = fish_pos
        if circle_info:
            cx, cy, radius = circle_info
            radius_sq = radius * radius
        else:
            # Use cached values for speed
            cx, cy = self._circle_center
            radius_sq = self._circle_radius_sq
        dx, dy = fx - cx, fy - cy
        return (dx * dx + dy * dy) < radius_sq
    
    def get_bait_key(self, bait_count: int) -> str:
        """Determines which keyboard key to press based on bait counter and selected keys."""
        if not self.bait_keys:
            return '1'
        
        num_keys = len(self.bait_keys)
        bait_per_key = 200
        
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
        # Create thresholds: e.g., for 4 keys: [600, 400, 200, 0]
        return [(num_keys - i - 1) * 200 for i in range(num_keys)]
    
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
                # Activate window before sending keys
                self.window_manager.activate_window()
                time.sleep(0.03)
                self.keyboard_controller.press(Key.ctrl)
                time.sleep(0.02)
                self.keyboard_controller.press(key)
                time.sleep(0.02)
                self.keyboard_controller.release(key)
                time.sleep(0.02)
                self.keyboard_controller.release(Key.ctrl)
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error pressing CTRL+{key}: {e}")
    
    def quickskip(self):
        """Performs quick skip by double pressing CTRL+G."""
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Quick skip...")
        self.press_ctrl_key('g')
        time.sleep(0.15)  # Longer delay for game to process first CTRL+G
        self.press_ctrl_key('g')
        time.sleep(0.15)  # Delay after second press before next action
    
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
                # Activate window before sending keys
                self.window_manager.activate_window()
                time.sleep(0.03)
                
                # Get the key object or use the key directly for number keys
                pynput_key = key_map.get(key.upper() if len(key) > 1 else key, key_map.get(key, key))
                
                self.keyboard_controller.press(pynput_key)
                time.sleep(0.025)
                self.keyboard_controller.release(pynput_key)
                
                if description and self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] {description}")
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error pressing key '{key}': {e}")
    
    def wait_for_minigame_window(self, timeout: float = 4.0) -> Optional[GameRegion]:
        """Waits for and finds the fishing minigame window. Auto-calibrates region on first detection."""
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
                        return self.region
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
        
        return None
    
    def _scan_existing_inventory(self):
        """Scans inventory for all existing items and adds their positions to ignore list.
        Called at bot start to prevent re-processing items already in inventory.
        Uses iterative minMaxLoc with masking to find ALL distinct items (same logic as identify_item_in_inventory)."""
        templates = self._load_template_cache()
        if not templates:
            return
        
        try:
            # Activate window before capturing
            self.window_manager.activate_window(force_activate=True)
            time.sleep(0.3)  # Give window time to come into focus
            
            inventory_frame = self.capture_inventory_area()
            inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
            inv_h, inv_w = inventory_gray.shape
            
            # Local references for speed
            match_template = cv2.matchTemplate
            minMaxLoc = cv2.minMaxLoc
            TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
            CONFIDENCE_THRESHOLD = 0.80
            
            found_count = 0
            
            for filename, (template, half_w, half_h) in templates.items():
                t_h, t_w = template.shape
                
                if t_h > inv_h or t_w > inv_w:
                    continue
                
                try:
                    result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)
                    
                    # Find ALL matches using iterative minMaxLoc with masking
                    while True:
                        _, max_val, _, max_loc = minMaxLoc(result)
                        
                        # Stop if best remaining match is below threshold
                        if max_val < CONFIDENCE_THRESHOLD:
                            break
                        
                        pt_x, pt_y = max_loc
                        center_x = pt_x + half_w
                        center_y = pt_y + half_h
                        
                        # Check if position already in ignore list (within 10px radius)
                        is_duplicate = False
                        for ix, iy in self._ignored_positions:
                            if abs(center_x - ix) < 10 and abs(center_y - iy) < 10:
                                is_duplicate = True
                                break
                        
                        if not is_duplicate:
                            self._ignored_positions.add((center_x, center_y))
                            found_count += 1
                        
                        # Mask out this match area to find next one (set to -1 so it won't be found again)
                        # Mask a region around the match point
                        mask_x1 = max(0, pt_x - t_w // 2)
                        mask_y1 = max(0, pt_y - t_h // 2)
                        mask_x2 = min(result.shape[1], pt_x + t_w // 2 + 1)
                        mask_y2 = min(result.shape[0], pt_y + t_h // 2 + 1)
                        result[mask_y1:mask_y2, mask_x1:mask_x2] = -1.0
                        
                except Exception:
                    continue
            
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Inventory scan: found {found_count} existing items (ignoring)")
                
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Error scanning inventory: {e}")
    
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
                self.on_status_update(f"[W{self.bot_id+1}] Classic fish template not found at assets/classic_fish.jpg")
        
        return FishingBot._classic_fish_template
    
    def wait_for_classic_fish(self, timeout: float = 10.0) -> bool:
        """Waits for the classic fish image to appear in the game window.
        Returns True if found, False if timeout."""
        template = self._load_classic_fish_template()
        if template is None:
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] No classic fish template, using fallback timing")
            return True  # Fallback: proceed anyway
        
        start_time = time.time()
        t_h, t_w = template.shape
        
        # Multi-scale detection: check scales from 25% to 300% of original template size
        scales = [0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.25, 2.5, 2.75, 3.0]
        
        while self.running and time.time() - start_time < timeout:
            if self.paused:
                time.sleep(0.1)
                continue
            
            try:
                frame = self.capture_full_window()
                frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                
                # Crop to 250px wide centered bar, upper half only for performance
                f_h, f_w = frame_gray.shape
                center_x = f_w // 2
                crop_left = max(0, center_x - 125)
                crop_right = min(f_w, center_x + 125)
                crop_bottom = f_h // 2  # Only upper half
                frame_gray = frame_gray[:crop_bottom, crop_left:crop_right]
                
                f_h, f_w = frame_gray.shape
                
                # Multi-scale template matching
                best_match_val = 0
                best_scale = 1.0
                
                for scale in scales:
                    # Resize template to current scale
                    new_w = int(t_w * scale)
                    new_h = int(t_h * scale)
                    
                    # Skip if scaled template is larger than frame or too small
                    if new_h > f_h or new_w > f_w or new_w < 10 or new_h < 10:
                        continue
                    
                    scaled_template = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
                    
                    # Template matching
                    result = cv2.matchTemplate(frame_gray, scaled_template, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, _ = cv2.minMaxLoc(result)
                    
                    if max_val > best_match_val:
                        best_match_val = max_val
                        best_scale = scale
                    
                    # Early exit if we found a very good match
                    if max_val >= 0.8:
                        break
                
                if best_match_val >= 0.7:  # Found the classic fish indicator
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
        # Reset bait if starting with 0 or negative bait
        max_bait = len(self.bait_keys) * 200
        if self.bait_counter <= 0:
            self.bait_counter = max_bait
            if self.on_bait_update:
                self.on_bait_update(self.bot_id, self.bait_counter)
            if self.on_status_update:
                self.on_status_update(f"[W{self.bot_id+1}] Bait counter was 0! Reset to {max_bait}.")
        
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Bot started! Bait: {self.bait_counter}")
        
        # Scan inventory for existing items before starting (add to ignore list)
        self._scan_existing_inventory()
        
        while self.running and self.bait_counter > 0:
            if self.paused:
                time.sleep(0.1)
                continue
            
            try:
                bait_key = self.get_bait_key(self.bait_counter)
                self.press_key(bait_key, f"Pressed key {bait_key}")
                time.sleep(0.05)
                
                self.press_key('space', "Cast fishing line")
                time.sleep(0.05)
                
                # Only play minigame if Classic Fishing system is NOT enabled
                if not self.config.get('classic_fishing', False):
                    minigame_detected = self.wait_for_minigame_window(timeout=4)
                    if not minigame_detected:
                        self.consecutive_failures += 1
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] Minigame not detected ({self.consecutive_failures}/5)")
                        
                        if self.consecutive_failures >= 5:
                            self.adjust_bait_tier()
                            if self.bait_counter <= 0:
                                if self.on_status_update:
                                    self.on_status_update(f"[W{self.bot_id+1}] Bait depleted after consecutive failures. Stopping bot.")
                                self.running = False
                                if self.on_bot_stop:
                                    self.on_bot_stop(self.bot_id)
                                break
                        
                        # Press CTRL+G once per failure to dismount horse if that's the issue
                        # First failure: try to dismount if on horse
                        # Second failure: you actually mounted in first attemp and now you need to unmount
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] Pressing CTRL+G to dismount horse...")
                        self.press_ctrl_key('g')
                        time.sleep(0.15)
                        continue
                    
                    # Reset failure counter on successful minigame detection
                    self.consecutive_failures = 0
                    
                    minigame_active = True
                    human_like = self.config.get('human_like_clicking', True)
                    
                    while self.running and minigame_active:
                        if self.paused:
                            time.sleep(0.1)
                            continue
                        
                        # Small delay between attempts (minimized for responsiveness)
                        if human_like:
                            time.sleep(np.random.uniform(0.15, 0.7))
                        
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
                            wait_time = np.random.uniform(4, 4.5)
                            time.sleep(wait_time)
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
                                if self.on_status_update:
                                    self.on_status_update(f"[W{self.bot_id+1}] Bait depleted after consecutive failures. Stopping bot.")
                                self.running = False
                                if self.on_bot_stop:
                                    self.on_bot_stop(self.bot_id)
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
                                    time.sleep(0.1)
                                    continue
                                time.sleep(0.05)
                
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"[W{self.bot_id+1}] Error in play_game: {e}")
                time.sleep(0.5)
        
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Bot finished! Total games: {self.total_games}")
        if self.bait_counter <= 0 and self.config.get('sound_alert_on_finish', True):
            play_rickroll_beep()
        self.running = False
        if self.on_bot_stop:
            self.on_bot_stop(self.bot_id)
    
    def start(self):
        """Starts the bot"""
        self.running = True
        self.play_game()
    
    def stop(self):
        """Stops the bot"""
        self.running = False
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id+1}] Bot stopped")


if __name__ == "__main__":
    from bot_gui import BotGUI
    gui = BotGUI()
    gui.run()

"""
MT2 Fishing Bot - Multi-Window Support
Automated fishing minigame bot for Metin2
Author: boristei
"""

# === Standard Library ===
import os
import sys
import json
import time
import threading
import winsound
import ctypes
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

# === Windows DPI Awareness ===
# Fix for high DPI displays (125%, 150%, etc.) where UI elements may be cut off
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
except Exception:
    pass  # Older Windows versions may not support this

# === Third Party ===
import cv2
import numpy as np
import pyautogui
# Disable PyAutoGUI fail-safe for multi-window automation
# When multiple bots run simultaneously, mouse movements can trigger the fail-safe
# This is safe because we have explicit click logic and input_lock synchronization
pyautogui.FAILSAFE = False

from mss import mss
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, messagebox
import pygetwindow as gw
import psutil

# === Resource Path Helper ===
def get_resource_path(filename: str) -> str:
    """Get the path to a bundled resource (works both in dev and in PyInstaller exe)"""
    if hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle
        return os.path.join(sys._MEIPASS, filename)
    else:
        # Running as script
        return os.path.join(os.path.dirname(__file__), filename)

# === Keyboard/Mouse Control ===
try:
    from pynput import keyboard
    from pynput.keyboard import Controller, Key
except ImportError:
    print("ERROR: pynput not installed! Install with: pip install pynput")
    keyboard = None
    Controller = None
    Key = None

# Thread synchronization for mouse/keyboard - prevents race conditions
input_lock = threading.Lock()

# Max simultaneous game windows
MAX_WINDOWS = 8

# Debug mode - enable/disable IgnoredPositionsWindow
DEBUG_MODE_EN = False

# Debug prints - enable/disable verbose debug print statements
DEBUG_PRINTS = False

def play_rickroll_beep():
    """Plays a Rick Roll-themed beep sequence (intro)."""
    melody = [
        (554, 600),   # C5s - strong opening
        (622, 1000),  # E5f - longer note
        (622, 600),   # E5f
        (698, 600),   # F5
        (831, 100),   # A5f - quick notes
        (740, 100),   # F5s
        (698, 100),   # F5
        (622, 100),   # E5f
        (554, 600),   # C5s
        (622, 800),   # E5f - held note
        (415, 400),   # A4f - step down
        (415, 200),   # A4f
    ]
    for frequency, duration in melody:
        winsound.Beep(frequency, duration)

class WindowManager:
    """Manages window detection and focus for the bot"""
    
    def __init__(self):
        self.selected_window = None
    
    @staticmethod
    def get_all_windows() -> List[Tuple[str, gw.Win32Window]]:
        """Gets all visible windows on Windows 10+. Returns list of (display_name, window)"""
        windows = []
        priority_windows = []  # Windows with 'mt2', 'metin2', 'metin 2', or words with '2'
        
        try:
            # Use getAllWindows() directly - more reliable on Windows 10 than iterating processes
            all_wins = gw.getAllWindows()
            
            for win in all_wins:
                try:
                    # Skip empty titles
                    if not win.title or not win.title.strip():
                        continue
                    
                    # Check if window is visible (use 'visible' property, not 'isVisible')
                    if not getattr(win, 'visible', True):
                        continue
                    
                    display_name = win.title
                    
                    # Check if window matches Metin2 patterns (prioritize these)
                    title_lower = win.title.lower()
                    if any(pattern in title_lower for pattern in ['mt2', 'metin2', 'metin 2']) or \
                       any(word.endswith('2') for word in title_lower.split()):
                        priority_windows.append((display_name, win))
                    else:
                        windows.append((display_name, win))
                except Exception:
                    pass
        except Exception as e:
            print(f"Error getting windows: {e}")
        
        # Combine all windows (prioritize Metin2 windows)
        all_windows = priority_windows + windows
        
        # Count occurrences of each display name
        name_counts = {}
        for display_name, win in all_windows:
            name_counts[display_name] = name_counts.get(display_name, 0) + 1
        
        # Add suffixes to duplicate names
        name_indices = {}
        result = []
        for display_name, win in all_windows:
            if name_counts[display_name] > 1:
                # This name appears multiple times, add a suffix
                index = name_indices.get(display_name, 0) + 1
                name_indices[display_name] = index
                final_name = f"{display_name} ({index})"
            else:
                final_name = display_name
            result.append((final_name, win))
        
        return result
    
    def activate_window(self, force_activate: bool = False):
        """Activates and brings the selected window to focus"""
        if not self.selected_window:
            return
        try:
            # Check if window is already active (skip activation for speed)
            if not force_activate:
                try:
                    active_win = gw.getActiveWindow()
                    if active_win and active_win._hWnd == self.selected_window._hWnd:
                        return  # Already active, skip
                except:
                    pass
            
            # Try to activate the window (single attempt for speed)
            try:
                # Restore window if minimized
                if self.selected_window.isMinimized:
                    self.selected_window.restore()
                    time.sleep(0.05)
                
                # Activate the window
                self.selected_window.activate()
                time.sleep(0.025)  # Minimal delay
            except Exception:
                # Retry once on failure
                try:
                    time.sleep(0.05)
                    self.selected_window.activate()
                    time.sleep(0.025)
                except:
                    pass
        except Exception as e:
            print(f"Error activating window: {e}")
    
    def get_window_rect(self) -> Tuple[int, int, int, int]:
        """Gets the selected window's position and size (left, top, width, height)"""
        if not self.selected_window:
            return (0, 0, 0, 0)
        
        try:
            left = self.selected_window.left
            top = self.selected_window.top
            width = self.selected_window.width
            height = self.selected_window.height
            return (left, top, width, height)
        except Exception as e:
            print(f"Error getting window rect: {e}")
            return (0, 0, 0, 0)

@dataclass
class GameRegion:
    """Stores the coordinates of the game window region (relative to selected window)"""
    left: int
    top: int
    width: int
    height: int

class FishDetector:
    """Detects fish and game elements using computer vision (HSV color detection)"""
    
    def __init__(self):
        # HSV color range for fish (blue-ish)
        self.fish_color_lower = np.array([97, 130, 108])
        self.fish_color_upper = np.array([110, 146, 133])
        
        # HSV color range for minigame window background (cyan)
        self.window_color_lower = np.array([98, 170, 189])
        self.window_color_upper = np.array([106, 255, 250])
    
    def find_fishing_window_bounds(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        """Finds the bounding box of the fishing window. Returns (x, y, width, height) or None."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.window_color_lower, self.window_color_upper)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return None
        
        largest_contour = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(largest_contour)
        
        if w > 50 and h > 50:
            return (x, y, w, h)
        return None
    
    def detect_window_and_fish(self, frame: np.ndarray) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Combined detection: single HSV conversion for both window and fish.
        Returns: (window_active, fish_position or None)"""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # Check window first
        window_mask = cv2.inRange(hsv, self.window_color_lower, self.window_color_upper)
        if cv2.countNonZero(window_mask) <= 10000:
            return (False, None)
        
        # Find fish using same HSV
        fish_mask = cv2.inRange(hsv, self.fish_color_lower, self.fish_color_upper)
        contours, _ = cv2.findContours(fish_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return (True, None)
        
        largest_contour = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest_contour)
        if M["m00"] != 0:
            return (True, (int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])))
        
        return (True, None)
     
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
                if not fish_pos or not self. is_fish_in_circle(fish_pos):
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
        
        template_path = get_resource_path(os.path.join("assets", "classic_fish.jpg"))
        if not os.path.exists(template_path):
            # Try .png extension
            template_path = get_resource_path(os.path.join("assets", "classic_fish.png"))
        
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
                    time.sleep(delay-0.05)  # Subtract small polling delay
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
                    
                    if not fish_found:
                        # Timeout waiting for fish - continue to next cast
                        if self.on_status_update:
                            self.on_status_update(f"[W{self.bot_id+1}] No fish bite detected, recasting...")
                        continue
                    
                    # Timer already elapsed in wait_for_classic_fish - press space to reel in
                    # Acquire lock and activate window BEFORE pressing space (critical timing)
                    with input_lock:
                        self.window_manager.activate_window(force_activate=True)
                        time.sleep(0.05)
                        if self.keyboard_controller:
                            self.keyboard_controller.press(Key.space)
                            time.sleep(0.025)
                            self.keyboard_controller.release(Key.space)
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
                    
                    # Step 4: Quick skip or wait before next cast
                    if self.bait_counter > 0:
                        if self.config.get('quick_skip', False):
                            time.sleep(1)
                            self.quickskip()
                        else:
                            wait_time = np.random.uniform(4, 4.5)
                            time.sleep(wait_time)
                
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

class IgnoredPositionsWindow:
    """Window displaying ignored positions with 10px radius visualization"""
    
    def __init__(self, parent, bot_instance):
        self.parent = parent
        self.bot = bot_instance
        self.window = None
        self.canvas = None
        self.photo_image = None
        self.sct = None  # Own screen capture instance (thread-safe)
        self._create_window()
        self._update_loop_id = None
    
    def _create_window(self):
        """Creates the ignored positions visualization window"""
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Ignored Positions - [W{self.bot.bot_id+1}]")
        self.window.geometry("320x400")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(False, False)
        
        # Try to load and set window icon
        icon_path = get_resource_path("monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.window.iconbitmap(icon_path)
            except Exception as e:
                print(f"Error loading icon: {e}")
        
        # Header
        header = tk.Frame(self.window, bg="#000000", height=35)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        title = tk.Label(header, text="🎯 Ignored Positions", 
                        font=("Courier New", 11, "bold"),
                        bg="#000000", fg="#FFD700")
        title.pack(pady=6)
        
        # Counter label
        self.counter_label = tk.Label(self.window, text="Count: 0",
                                     font=("Courier New", 10),
                                     bg="#1a1a1a", fg="#00ff00")
        self.counter_label.pack(pady=3)
        
        # Canvas for image display (fixed size with dark background)
        self.canvas = tk.Canvas(self.window, bg="#000000", width=280, height=280,
                               highlightthickness=1, highlightbackground="#333333")
        self.canvas.pack(fill=tk.BOTH, expand=False, padx=5, pady=5)
        
        # Store a reference to the placeholder image
        self.placeholder_image = None
        self.photo_image = None
        
        # Draw initial placeholder
        self._draw_placeholder()
        
        # Start update loop
        self._schedule_update()
        
        # Handle window close
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _draw_placeholder(self):
        """Draws a placeholder image on the canvas"""
        try:
            # Create a test pattern image to verify canvas works
            placeholder = np.zeros((280, 280, 3), dtype=np.uint8)
            # Fill with dark blue background
            placeholder[:] = (50, 50, 100)
            # Draw border
            cv2.rectangle(placeholder, (10, 10), (270, 270), (100, 255, 100), 3)
            # Add text
            cv2.putText(placeholder, "Waiting for", (60, 120), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(placeholder, "inventory...", (50, 160), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            
            # Convert and display
            rgb_frame = cv2.cvtColor(placeholder, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame)
            self.placeholder_image = ImageTk.PhotoImage(pil_image)
            
            self.canvas.delete("all")
            self.canvas.create_image(140, 140, image=self.placeholder_image, anchor="center")
            self.canvas.update()
        except Exception as e:
            print(f"Error drawing placeholder: {e}")
            import traceback
            traceback.print_exc()
    
    def _schedule_update(self):
        """Schedule next update"""
        if self.window and self.window.winfo_exists():
            self._update_loop_id = self.window.after(500, self._update_display)
    
    def _on_close(self):
        """Handle window close"""
        if self._update_loop_id:
            self.window.after_cancel(self._update_loop_id)
        if self.window:
            self.window.destroy()
            self.window = None
    
    def _update_display(self):
        """Update the ignored positions visualization"""
        try:
            # Safety check: window must still exist
            if not self.window or not self.window.winfo_exists():
                return
            
            # Only attempt capture if bot has a selected window
            if not self.bot.window_manager or not self.bot.window_manager.selected_window:
                # Show placeholder
                if self.placeholder_image:
                    self.canvas.delete("all")
                    self.canvas.create_image(140, 140, image=self.placeholder_image, anchor="center")
                self._schedule_update()
                return
            
            # Initialize mss if needed (own instance for this thread)
            if self.sct is None:
                self.sct = mss()
            
            # Manually capture inventory area using our own mss instance
            try:
                win_left, win_top, win_width, win_height = self.bot.window_manager.get_window_rect()
                
                # Capture right 200px of window, starting from y=300
                monitor = {
                    "left": win_left + win_width - self.bot._inventory_width,
                    "top": win_top + self.bot._inventory_y_offset,
                    "width": self.bot._inventory_width,
                    "height": max(0, win_height - self.bot._inventory_y_offset - 30)
                }
                
                sct_img = self.sct.grab(monitor)
                inventory_frame = np.array(sct_img)
                inventory_frame = cv2.cvtColor(inventory_frame, cv2.COLOR_BGRA2BGR)
                
            except Exception as e:
                if DEBUG_PRINTS:
                    print(f"DEBUG: Manual capture failed: {e}")
                self._schedule_update()
                return
            
            # Validate capture
            if inventory_frame is None or not isinstance(inventory_frame, np.ndarray):
                if DEBUG_PRINTS:
                    print(f"DEBUG: Invalid capture returned")
                self._schedule_update()
                return
            
            inv_h, inv_w = inventory_frame.shape[:2]
            if DEBUG_PRINTS:
                print(f"DEBUG: Captured frame {inv_w}x{inv_h}")
            
            if inv_h <= 0 or inv_w <= 0:
                if DEBUG_PRINTS:
                    print(f"DEBUG: Invalid dimensions")
                self._schedule_update()
                return
            
            # Check if mostly black (brightness < 20)
            mean_val = np.mean(inventory_frame)
            if DEBUG_PRINTS:
                print(f"DEBUG: Mean brightness: {mean_val:.1f}")
            
            if mean_val < 10:
                # Captured image is completely black - likely capturing wrong area
                if DEBUG_PRINTS:
                    print(f"DEBUG: Image is black, showing test pattern instead")
                # Create test pattern to show capture coordinates
                test_img = np.zeros((280, 280, 3), dtype=np.uint8)
                test_img[:] = (100, 50, 50)  # Dark red
                cv2.putText(test_img, "Capture failed", (40, 120), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                cv2.putText(test_img, "Check window", (50, 160), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                rgb_test = cv2.cvtColor(test_img, cv2.COLOR_BGR2RGB)
                pil_test = Image.fromarray(rgb_test)
                self.photo_image = ImageTk.PhotoImage(pil_test)
                self.canvas.delete("all")
                self.canvas.create_image(140, 140, image=self.photo_image, anchor="center")
                self.canvas.update()
                self._schedule_update()
                return
            
            # Ensure it's BGR format with 3 channels
            if len(inventory_frame.shape) != 3 or inventory_frame.shape[2] != 3:
                if DEBUG_PRINTS:
                    print(f"DEBUG: Wrong format - shape: {inventory_frame.shape}")
                self._schedule_update()
                return
            
            # Create visualization - make a copy to draw on
            viz_frame = inventory_frame.copy()
            
            # Draw circles for each ignored position
            for ix, iy in self.bot._ignored_positions:
                ix, iy = int(ix), int(iy)
                
                # Only draw if within bounds
                if 0 <= ix < inv_w and 0 <= iy < inv_h:
                    cv2.circle(viz_frame, (ix, iy), 10, (0, 0, 255), 2)
                    cv2.circle(viz_frame, (ix, iy), 2, (255, 255, 255), -1)
            
            # Resize to fit canvas
            scale = min(280.0 / inv_w, 280.0 / inv_h, 1.0)
            new_w = int(inv_w * scale)
            new_h = int(inv_h * scale)
            
            if new_w > 0 and new_h > 0:
                viz_resized = cv2.resize(viz_frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            else:
                self._schedule_update()
                return
            
            # Convert to RGB
            rgb_frame = cv2.cvtColor(viz_resized, cv2.COLOR_BGR2RGB)
            
            # Create PIL image
            pil_image = Image.fromarray(rgb_frame)
            
            # Create PhotoImage and store it
            self.photo_image = ImageTk.PhotoImage(pil_image)
            
            # Update canvas
            self.canvas.delete("all")
            self.canvas.create_image(140, 140, image=self.photo_image, anchor="center")
            
            # Update counter
            count = len(self.bot._ignored_positions)
            self.counter_label.config(text=f"Count: {count}")
            
        except Exception as e:
            print(f"Error updating display: {e}")
            import traceback
            traceback.print_exc()
        
        self._schedule_update()
    
    def show(self):
        """Show the window"""
        if self.window:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
    
    def hide(self):
        """Hide the window"""
        if self.window:
            self.window.withdraw()
    
    def is_visible(self) -> bool:
        """Check if window is visible"""
        if self.window:
            return self.window.winfo_viewable()
        return False
    
    def destroy(self):
        """Destroy the window"""
        if self._update_loop_id:
            self.window.after_cancel(self._update_loop_id)
        if self.window:
            self.window.destroy()
            self.window = None

class FishDetectorDebugWindow:
    """Debug window for visualizing fish detection (window bounds and fish position)"""
    
    def __init__(self, parent, bot_instance):
        self.parent = parent
        self.bot = bot_instance
        self.window = None
        self.canvas = None
        self.photo_image = None
        self.sct = None  # Own screen capture instance (thread-safe)
        self.status_label = None
        self._create_window()
        self._update_loop_id = None
    
    def _create_window(self):
        """Creates the fish detector debug window"""
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Fish Detector Debug - [W{self.bot.bot_id+1}]")
        self.window.geometry("600x550")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(False, False)
        
        # Try to load and set window icon
        icon_path = get_resource_path("monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.window.iconbitmap(icon_path)
            except Exception as e:
                pass
        
        # Header
        header = tk.Frame(self.window, bg="#000000", height=35)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        title = tk.Label(header, text="🎣 Fish Detector Debug", 
                        font=("Courier New", 11, "bold"),
                        bg="#000000", fg="#FFD700")
        title.pack(pady=6)
        
        # Status info frame
        info_frame = tk.Frame(self.window, bg="#2a2a2a")
        info_frame.pack(fill=tk.X, padx=5, pady=3)
        
        info_text = tk.Label(info_frame, 
                            text="Window (green) | Fish (red) | Classic fish (magenta) | Click zone (yellow)",
                            font=("Courier New", 8),
                            bg="#2a2a2a", fg="#ffffff",
                            justify=tk.LEFT)
        info_text.pack(anchor="w", padx=5, pady=2)
        
        # Status label
        self.status_label = tk.Label(self.window, text="Status: Ready",
                                    font=("Courier New", 9),
                                    bg="#1a1a1a", fg="#00ff00")
        self.status_label.pack(pady=2)
        
        # Canvas for image display
        self.canvas = tk.Canvas(self.window, bg="#000000", width=560, height=380,
                               highlightthickness=1, highlightbackground="#333333")
        self.canvas.pack(fill=tk.BOTH, expand=False, padx=5, pady=5)
        
        # Info panel at bottom
        info_panel = tk.Frame(self.window, bg="#1a1a1a")
        info_panel.pack(fill=tk.X, padx=5, pady=5)
        
        self.info_text = tk.Label(info_panel, 
                                 text="",
                                 font=("Courier New", 8),
                                 bg="#1a1a1a", fg="#00ff00",
                                 justify=tk.LEFT)
        self.info_text.pack(anchor="w")
        
        # Store a reference to the placeholder image
        self.placeholder_image = None
        self.photo_image = None
        
        # Draw initial placeholder
        self._draw_placeholder()
        
        # Start update loop
        self._schedule_update()
        
        # Handle window close
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _draw_placeholder(self):
        """Draws a placeholder image on the canvas"""
        try:
            placeholder = np.zeros((380, 560, 3), dtype=np.uint8)
            placeholder[:] = (50, 50, 100)
            cv2.rectangle(placeholder, (10, 10), (550, 370), (100, 255, 100), 3)
            cv2.putText(placeholder, "Waiting for detection...", (80, 180), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
            
            rgb_frame = cv2.cvtColor(placeholder, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame)
            self.placeholder_image = ImageTk.PhotoImage(pil_image)
            
            self.canvas.delete("all")
            self.canvas.create_image(280, 190, image=self.placeholder_image, anchor="center")
            self.canvas.update()
        except Exception as e:
            pass
    
    def _schedule_update(self):
        """Schedule next update"""
        if self.window and self.window.winfo_exists():
            self._update_loop_id = self.window.after(200, self._update_display)
    
    def _on_close(self):
        """Handle window close"""
        if self._update_loop_id:
            self.window.after_cancel(self._update_loop_id)
        if self.window:
            self.window.destroy()
            self.window = None
    
    def _update_display(self):
        """Update the detection visualization"""
        try:
            if not self.window or not self.window.winfo_exists():
                return
            
            # Only attempt capture if bot has a selected window
            if not self.bot.window_manager or not self.bot.window_manager.selected_window:
                if self.placeholder_image:
                    self.canvas.delete("all")
                    self.canvas.create_image(280, 190, image=self.placeholder_image, anchor="center")
                self.status_label.config(text="Status: No window selected")
                self._schedule_update()
                return
            
            # Initialize mss if needed
            if self.sct is None:
                self.sct = mss()
            
            # Capture full window
            try:
                win_left, win_top, win_width, win_height = self.bot.window_manager.get_window_rect()
                
                monitor = {
                    "left": win_left,
                    "top": win_top,
                    "width": win_width,
                    "height": win_height
                }
                
                sct_img = self.sct.grab(monitor)
                frame = np.array(sct_img)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                
            except Exception as e:
                self.status_label.config(text=f"Status: Capture failed - {str(e)[:40]}")
                self._schedule_update()
                return
            
            if frame is None or frame.size == 0:
                self.status_label.config(text="Status: Invalid frame")
                self._schedule_update()
                return
            
            # Run detections
            viz_frame = frame.copy()
            h, w = frame.shape[:2]
            
            try:
                # Draw results on visualization
                status_msg = []
                
                # Check which fishing mode is active
                is_classic_mode = self.bot.config.get('classic_fishing', False)
                
                # Show fishing mode status in top-right
                if is_classic_mode:
                    cv2.putText(viz_frame, "MODE: CLASSIC", (w - 180, 40),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
                else:
                    cv2.putText(viz_frame, "MODE: MINIGAME", (w - 180, 40),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                
                if not is_classic_mode:
                    # ========== MINIGAME MODE DETECTION ==========
                    # Detection 1: find_fishing_window_bounds
                    window_bounds = self.bot.detector.find_fishing_window_bounds(frame)
                    
                    # Detection 2: detect_window_and_fish
                    window_active, fish_pos = self.bot.detector.detect_window_and_fish(frame)
                    
                    # Draw window bounds if found
                    if window_bounds:
                        x, y, bw, bh = window_bounds
                        cv2.rectangle(viz_frame, (x, y), (x + bw, y + bh), (0, 255, 0), 3)
                        cv2.putText(viz_frame, f"Window: {bw}x{bh}", (x, y - 5),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                        status_msg.append(f"Window bounds: ({x}, {y}) {bw}x{bh}")
                    else:
                        status_msg.append("Window bounds: NOT FOUND")
                    
                    # Draw window active status
                    if window_active:
                        cv2.putText(viz_frame, "WINDOW ACTIVE", (20, 40),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                        status_msg.append("Window active: YES")
                    else:
                        cv2.putText(viz_frame, "WINDOW INACTIVE", (20, 40),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                        status_msg.append("Window active: NO")
                    
                    # Draw fish position if detected
                    if fish_pos:
                        fx, fy = fish_pos
                        cv2.circle(viz_frame, (fx, fy), 12, (0, 0, 255), 2)
                        cv2.circle(viz_frame, (fx, fy), 3, (255, 255, 255), -1)
                        cv2.putText(viz_frame, f"Fish ({fx},{fy})", (fx + 15, fy - 5),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                        status_msg.append(f"Fish position: ({fx}, {fy})")
                    else:
                        status_msg.append("Fish position: NOT DETECTED")
                    
                    # Draw circle region if region is calibrated
                    if self.bot.region and self.bot.region_auto_calibrated:
                        cx = self.bot.region.left + self.bot.region.width // 2
                        cy = self.bot.region.top + self.bot.region.height // 2
                        radius = 67
                        cv2.circle(viz_frame, (cx, cy), radius, (255, 255, 0), 2)
                        cv2.putText(viz_frame, "Click zone", (cx - 40, cy - radius - 10),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
                
                else:
                    # ========== CLASSIC FISHING MODE DETECTION ==========
                    # Load classic fish template if available
                    template = self.bot._load_classic_fish_template()
                    if template is not None:
                        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        t_h, t_w = template.shape
                        f_h, f_w = frame_gray.shape
                        
                        # Crop to 250px wide centered bar, upper half only (same as detection)
                        center_x = f_w // 2
                        crop_left = max(0, center_x - 125)
                        crop_right = min(f_w, center_x + 125)
                        crop_bottom = f_h // 2  # Only upper half
                        frame_gray_cropped = frame_gray[:crop_bottom, crop_left:crop_right]
                        
                        # Draw the search region on viz_frame (semi-transparent overlay)
                        overlay = viz_frame.copy()
                        # Darken areas outside the search region
                        overlay[:, :crop_left] = (overlay[:, :crop_left] * 0.3).astype(np.uint8)
                        overlay[:, crop_right:] = (overlay[:, crop_right:] * 0.3).astype(np.uint8)
                        overlay[crop_bottom:, :] = (overlay[crop_bottom:, :] * 0.3).astype(np.uint8)  # Darken lower half
                        # Draw lines to mark search region
                        cv2.line(overlay, (crop_left, 0), (crop_left, crop_bottom), (0, 255, 255), 2)
                        cv2.line(overlay, (crop_right, 0), (crop_right, crop_bottom), (0, 255, 255), 2)
                        cv2.line(overlay, (crop_left, crop_bottom), (crop_right, crop_bottom), (0, 255, 255), 2)  # Bottom line
                        cv2.putText(overlay, "Search region (250px, upper, multi-scale)", (crop_left + 5, 25),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
                        viz_frame = overlay
                        
                        f_h_cropped, f_w_cropped = frame_gray_cropped.shape
                        
                        # Multi-scale template matching (same as detection code)
                        scales = [0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.25, 2.5, 2.75, 3.0]
                        best_match_val = 0
                        best_scale = 1.0
                        best_loc = (0, 0)
                        best_size = (t_w, t_h)
                        
                        for scale in scales:
                            new_w = int(t_w * scale)
                            new_h = int(t_h * scale)
                            
                            if new_h > f_h_cropped or new_w > f_w_cropped or new_w < 10 or new_h < 10:
                                continue
                            
                            scaled_template = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
                            result = cv2.matchTemplate(frame_gray_cropped, scaled_template, cv2.TM_CCOEFF_NORMED)
                            _, max_val, _, max_loc = cv2.minMaxLoc(result)
                            
                            if max_val > best_match_val:
                                best_match_val = max_val
                                best_scale = scale
                                best_loc = max_loc
                                best_size = (new_w, new_h)
                            
                            if max_val >= 0.8:
                                break
                        
                        if best_match_val >= 0.7:
                            # Draw rectangle around detected classic fish (adjust x for crop offset)
                            pt1 = (best_loc[0] + crop_left, best_loc[1])
                            pt2 = (best_loc[0] + crop_left + best_size[0], best_loc[1] + best_size[1])
                            cv2.rectangle(viz_frame, pt1, pt2, (255, 0, 255), 3)  # Magenta
                            cv2.putText(viz_frame, f"CLASSIC FISH DETECTED!", 
                                       (pt1[0], pt1[1] - 30),
                                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
                            cv2.putText(viz_frame, f"Conf: {best_match_val:.2f}, Scale: {best_scale:.1f}x", 
                                       (pt1[0], pt1[1] - 10),
                                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
                            status_msg.append(f"Classic fish: DETECTED ({best_match_val:.2f}, {best_scale:.1f}x)")
                        else:
                            # Show confidence while searching
                            cv2.putText(viz_frame, f"Searching (multi-scale 0.5x-1.5x)...", (20, 40),
                                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (128, 128, 128), 2)
                            cv2.putText(viz_frame, f"Best: {best_match_val:.2f} @ {best_scale:.1f}x (need 0.70)", (20, 70),
                                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (128, 128, 128), 2)
                            status_msg.append(f"Classic fish: searching ({best_match_val:.2f})")
                    else:
                        cv2.putText(viz_frame, "NO CLASSIC FISH TEMPLATE!", (20, 40),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                        cv2.putText(viz_frame, "Add assets/classic_fish.jpg", (20, 70),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                        status_msg.append("Classic fish: NO TEMPLATE")
                
                self.status_label.config(text=f"Status: {' | '.join(status_msg[:2])}")
                
            except Exception as e:
                status_msg = [f"Detection error: {str(e)[:50]}"]
                self.status_label.config(text=f"Status: ERROR - {str(e)[:40]}")
            
            # Resize to fit canvas
            scale = min(560.0 / w, 380.0 / h, 1.0)
            new_w = int(w * scale)
            new_h = int(h * scale)
            
            if new_w > 0 and new_h > 0:
                viz_resized = cv2.resize(viz_frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            else:
                self._schedule_update()
                return
            
            # Convert to RGB
            rgb_frame = cv2.cvtColor(viz_resized, cv2.COLOR_BGR2RGB)
            
            # Create PIL image
            pil_image = Image.fromarray(rgb_frame)
            
            # Create PhotoImage
            self.photo_image = ImageTk.PhotoImage(pil_image)
            
            # Update canvas
            self.canvas.delete("all")
            self.canvas.create_image(280, 190, image=self.photo_image, anchor="center")
            
            # Update info panel
            info_str = " | ".join(status_msg)
            self.info_text.config(text=info_str)
            
        except Exception as e:
            self.status_label.config(text=f"Status: ERROR")
        
        self._schedule_update()
    
    def show(self):
        """Show the window"""
        if self.window:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
    
    def hide(self):
        """Hide the window"""
        if self.window:
            self.window.withdraw()
    
    def is_visible(self) -> bool:
        """Check if window is visible"""
        if self.window:
            return self.window.winfo_viewable()
        return False
    
    def destroy(self):
        """Destroy the window"""
        if self._update_loop_id:
            self.window.after_cancel(self._update_loop_id)
        if self.window:
            self.window.destroy()

class StatusLogWindow:
    """Separate window for displaying status log messages"""
    
    def __init__(self, parent):
        self.parent = parent
        self.window = None
        self.status_text = None
        self._create_window()
    
    def _create_window(self):
        """Creates the status log window"""
        self.window = tk.Toplevel(self.parent)
        self.window.title("Status Log")
        self.window.geometry("900x500")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(True, True)
        
        # Try to load and set window icon
        icon_path = get_resource_path("monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.window.iconbitmap(icon_path)
            except Exception as e:
                print(f"Error loading icon: {e}")
        
        # Header
        header = tk.Frame(self.window, bg="#000000", height=35)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        title = tk.Label(header, text="📋 Status Log", 
                        font=("Courier New", 11, "bold"),
                        bg="#000000", fg="#FFD700")
        title.pack(pady=6)
        
        # Main content frame
        content_frame = tk.Frame(self.window, bg="#1a1a1a")
        content_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Status text with scrollbar
        status_scroll = tk.Scrollbar(content_frame)
        status_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.status_text = tk.Text(content_frame, 
                                   bg="#1a1a1a", fg="#00ff00",
                                   font=("Courier", 9),
                                   yscrollcommand=status_scroll.set,
                                   state=tk.DISABLED)
        self.status_text.pack(fill=tk.BOTH, expand=True)
        status_scroll.config(command=self.status_text.yview)
        
        # Bottom button frame
        button_frame = tk.Frame(self.window, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Clear button
        tk.Button(button_frame, text="🗑️ Clear Log", 
                 command=self.clear_log,
                 bg="#e74c3c", fg="white", 
                 font=("Courier New", 9, "bold"),
                 cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT, padx=5)
        
        # Close button
        tk.Button(button_frame, text="Close", 
                 command=self.hide,
                 bg="#555555", fg="white", 
                 font=("Courier New", 9, "bold"),
                 cursor="hand2", padx=15, pady=3).pack(side=tk.RIGHT, padx=5)
        
        # Handle window close button (X) - just hide instead of destroy
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        
        # Start hidden
        self.window.withdraw()
    
    def add_message(self, message: str):
        """Adds a message to the status log"""
        if self.status_text:
            self.status_text.config(state=tk.NORMAL)
            timestamp = time.strftime("%H:%M:%S")
            self.status_text.insert(tk.END, f"[{timestamp}] {message}\n")
            self.status_text.see(tk.END)
            self.status_text.config(state=tk.DISABLED)
    
    def clear_log(self):
        """Clears all messages from the log"""
        if self.status_text:
            self.status_text.config(state=tk.NORMAL)
            self.status_text.delete(1.0, tk.END)
            self.status_text.config(state=tk.DISABLED)
    
    def show(self):
        """Shows the status log window"""
        if self.window:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
    
    def hide(self):
        """Hides the status log window"""
        if self.window:
            self.window.withdraw()
    
    def is_visible(self) -> bool:
        """Returns True if the window is currently visible"""
        if self.window:
            return self.window.winfo_viewable()
        return False
    
    def destroy(self):
        """Destroys the window"""
        if self.window:
            self.window.destroy()
            self.window = None

class FishSelectionWindow:
    """Window for selecting fish/item actions (keep, drop, open)"""
    
    # Action colors for visual feedback
    ACTION_COLORS = {
        'keep': '#2ecc71',    # Green
        'drop': '#e74c3c',    # Red
        'open': '#3498db',    # Blue
        None: '#555555'       # Gray (not set)
    }
    
    def __init__(self, parent, current_actions: dict, on_save_callback):
        self.parent = parent
        self.current_actions = current_actions.copy()
        self.on_save_callback = on_save_callback
        self.item_widgets = {}  # {filename: {'frame': frame, 'action_var': var, 'buttons': {}}}
        self.photo_images = []  # Keep references to prevent garbage collection
        
        # Create window
        self.window = tk.Toplevel(parent)
        self.window.title("Fish & Item Selection")
        self.window.geometry("570x615")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(False, False)
        
        # Try to load and set window icon
        icon_path = get_resource_path("monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.window.iconbitmap(icon_path)
            except Exception as e:
                print(f"Error loading icon: {e}")
        
        # Make window modal
        self.window.transient(parent)
        self.window.grab_set()
        
        self.setup_ui()
        self.load_items()
        
    def setup_ui(self):
        """Creates the fish selection window UI"""
        # Header
        header = tk.Frame(self.window, bg="#000000", height=35)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        title = tk.Label(header, text="Fish & Item Actions", 
                        font=("Courier New", 11, "bold"),
                        bg="#000000", fg="#FFD700")
        title.pack(pady=6)
        
        # Instructions
        instructions_frame = tk.Frame(self.window, bg="#2a2a2a")
        instructions_frame.pack(fill=tk.X, padx=5, pady=2)
        
        instructions = tk.Label(instructions_frame, 
                               text="K=Keep (default)  | D=Drop  | O=Open (fish only)",
                               font=("Courier New", 8),
                               bg="#2a2a2a", fg="#ffffff",
                               justify=tk.CENTER)
        instructions.pack(pady=2)
        
        # Scrollable container
        container = tk.Frame(self.window, bg="#1a1a1a")
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        
        # Canvas without scrollbar
        self.canvas = tk.Canvas(container, bg="#1a1a1a", highlightthickness=0)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#1a1a1a")
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Bottom buttons
        button_frame = tk.Frame(self.window, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Set All buttons
        set_all_frame = tk.Frame(button_frame, bg="#1a1a1a")
        set_all_frame.pack(side=tk.LEFT)
        
        tk.Button(set_all_frame, text="Keep All", command=lambda: self.set_all_actions('keep'),
                 bg="#2ecc71", fg="white", font=("Courier New", 8),
                 cursor="hand2", padx=5).pack(side=tk.LEFT, padx=2)
        
        tk.Button(set_all_frame, text="Drop All", command=lambda: self.set_all_actions('drop'),
                 bg="#e74c3c", fg="white", font=("Courier New", 8),
                 cursor="hand2", padx=5).pack(side=tk.LEFT, padx=2)
        
        tk.Button(set_all_frame, text="Open All (Fish)", command=self.set_all_fish_open,
                 bg="#3498db", fg="white", font=("Courier New", 8),
                 cursor="hand2", padx=5).pack(side=tk.LEFT, padx=2)
        
        # Save/Cancel buttons
        save_cancel_frame = tk.Frame(button_frame, bg="#1a1a1a")
        save_cancel_frame.pack(side=tk.RIGHT)
        
        tk.Button(save_cancel_frame, text="Cancel", command=self.window.destroy,
                 bg="#555555", fg="white", font=("Courier New", 9, "bold"),
                 cursor="hand2", padx=15, pady=5).pack(side=tk.LEFT, padx=5)
        
        tk.Button(save_cancel_frame, text="Save", command=self.save_and_close,
                 bg="#2ecc71", fg="white", font=("Courier New", 9, "bold"),
                 cursor="hand2", padx=15, pady=5).pack(side=tk.LEFT, padx=5)
    
    def load_items(self):
        """Loads fish and item images from the assets folder"""
        assets_path = get_resource_path("assets")
        
        if not os.path.exists(assets_path):
            tk.Label(self.scrollable_frame, text="Assets folder not found!",
                    bg="#1a1a1a", fg="#e74c3c",
                    font=("Courier New", 12)).pack(pady=20)
            return
        
        # Get all fish and item files
        files = []
        for f in os.listdir(assets_path):
            if f.endswith('_living.jpg') or f.endswith('_living.png'):
                files.append(('fish', f))
            elif f.endswith('_item.jpg') or f.endswith('_item.png'):
                files.append(('item', f))
        
        if not files:
            tk.Label(self.scrollable_frame, text="No fish or item images found in assets folder!",
                    bg="#1a1a1a", fg="#e74c3c",
                    font=("Courier New", 12)).pack(pady=20)
            return
        
        # Sort: fish first, then items
        files.sort(key=lambda x: (0 if x[0] == 'fish' else 1, x[1]))
        
        # Create section labels
        current_type = None
        row = 0
        col = 0
        items_per_row = 6
        
        for item_type, filename in files:
            # Add section header if type changes
            if item_type != current_type:
                if col != 0:
                    row += 1
                    col = 0
                
                section_label = tk.Label(self.scrollable_frame, 
                                        text=f"{'Fish' if item_type == 'fish' else 'Items'}",
                                        font=("Courier New", 9, "bold"),
                                        bg="#1a1a1a", fg="#FFD700")
                section_label.grid(row=row, column=0, columnspan=items_per_row, sticky="w", pady=(8, 2), padx=3)
                row += 1
                current_type = item_type
            
            # Create item frame
            self.create_item_widget(filename, assets_path, row, col, item_type)
            
            col += 1
            if col >= items_per_row:
                col = 0
                row += 1
    
    def create_item_widget(self, filename: str, assets_path: str, row: int, col: int, item_type: str):
        """Creates a widget for a single fish/item"""
        # Item container
        item_frame = tk.Frame(self.scrollable_frame, bg="#2a2a2a", padx=2, pady=2)
        item_frame.grid(row=row, column=col, padx=2, pady=2, sticky="nsew")
        
        # Load and resize image
        try:
            img_path = os.path.join(assets_path, filename)
            img = Image.open(img_path)
            img = img.resize((36, 36), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.photo_images.append(photo)  # Keep reference
            
            img_label = tk.Label(item_frame, image=photo, bg="#2a2a2a")
            img_label.pack(pady=1)
        except Exception as e:
            # Fallback if image can't be loaded
            img_label = tk.Label(item_frame, text="?", font=("Courier New", 12),
                               bg="#2a2a2a", fg="#888888", width=3, height=1)
            img_label.pack(pady=1)
        
        # Item name (cleaned up)
        name = filename.replace('_living.jpg', '').replace('_living.png', '')
        name = name.replace('_item.jpg', '').replace('_item.png', '')
        name = name.replace('_', ' ')
        # Truncate long names
        if len(name) > 10:
            name = name[:9] + '..'
        
        name_label = tk.Label(item_frame, text=name, font=("Courier New", 7),
                             bg="#2a2a2a", fg="#ffffff")
        name_label.pack(pady=0)
        
        # Action buttons frame (single row layout)
        buttons_frame = tk.Frame(item_frame, bg="#2a2a2a")
        buttons_frame.pack(pady=1)
        
        # Store current action - DEFAULT to 'keep' if not previously set
        current_action = self.current_actions.get(filename, 'keep')
        
        # Create action buttons: Fish get K D O, Items get K D only
        buttons = {}
        if item_type == 'fish':
            button_actions = [('keep', 'K'), ('drop', 'D'), ('open', 'O')]
        else:
            button_actions = [('keep', 'K'), ('drop', 'D')]
        
        for idx, (action, symbol) in enumerate(button_actions):
            btn = tk.Button(buttons_frame, text=symbol, width=3,
                           font=("Courier New", 6, "bold"),
                           cursor="hand2",
                           padx=2, pady=0,
                           command=lambda f=filename, a=action: self.toggle_action(f, a))
            btn.grid(row=0, column=idx, padx=1, pady=0)
            buttons[action] = btn
        
        # Store widget references
        self.item_widgets[filename] = {
            'frame': item_frame,
            'buttons': buttons,
            'current_action': current_action,
            'item_type': item_type
        }
        
        # Make sure default action is saved
        if current_action and filename not in self.current_actions:
            self.current_actions[filename] = current_action
        
        # Update button colors to reflect current action
        self.update_button_colors(filename)
    
    def toggle_action(self, filename: str, action: str):
        """Sets an action for a fish/item (only allows switching to different actions)"""
        widget = self.item_widgets.get(filename)
        if not widget:
            return
        
        # Only change if selecting a different action
        if widget['current_action'] != action:
            widget['current_action'] = action
            self.current_actions[filename] = action
            self.update_button_colors(filename)
    
    def update_button_colors(self, filename: str):
        """Updates button colors based on current action"""
        widget = self.item_widgets.get(filename)
        if not widget:
            return
        
        current = widget['current_action']
        
        for action, btn in widget['buttons'].items():
            if action == current:
                btn.config(bg=self.ACTION_COLORS[action], fg="white", relief=tk.SUNKEN)
            else:
                btn.config(bg="#555555", fg="#aaaaaa", relief=tk.RAISED)
    
    def set_all_actions(self, action: str):
        """Sets the same action for all items"""
        for filename, widget in self.item_widgets.items():
            widget['current_action'] = action
            if action:
                self.current_actions[filename] = action
            else:
                self.current_actions.pop(filename, None)
            self.update_button_colors(filename)
    
    def set_all_fish_open(self):
        """Sets 'open' action for all fish only (items are not affected)"""
        for filename, widget in self.item_widgets.items():
            # Only apply to fish, not items
            if widget['item_type'] == 'fish':
                widget['current_action'] = 'open'
                self.current_actions[filename] = 'open'
                self.update_button_colors(filename)
    
    def save_and_close(self):
        """Saves the current actions and closes the window"""
        # Validate: all items must have an action selected
        items_without_action = [filename for filename, action in self.current_actions.items() if action is None]
        
        if items_without_action:
            messagebox.showwarning("Incomplete Selection", 
                                 "All fish and items must have an action selected!\n\n"
                                 "Fish: Keep, Drop, or Open\n"
                                 "Items: Keep or Drop")
            return
        
        # Call the callback with the actions
        if self.on_save_callback:
            self.on_save_callback(self.current_actions)
        
        # Unbind mousewheel before destroying
        self.canvas.unbind_all("<MouseWheel>")
        
        self.window.destroy()

class BotGUI:
    """GUI for the fishing bot - supports up to 8 simultaneous windows"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Fishing puzzle player (Multi-Window)")
        
        # Calculate window height based on DPI scaling
        base_height = 890
        try:
            dpi_scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
            # Increase height proportionally for high DPI (add extra space)
            window_height = int(base_height * max(1.0, dpi_scale * 0.9))
        except Exception:
            window_height = base_height
        
        self.root.geometry(f"600x{window_height}")
        self.root.resizable(False, True)  # Allow vertical resize for DPI scaling
        self.root.minsize(600, 750)
        self.root.configure(bg="#000000")
        
        # Try to load and set window icon
        icon_path = get_resource_path("monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception as e:
                print(f"Error loading icon: {e}")
        
        self.window_manager = WindowManager()
        self.bot: Optional[FishingBot] = None  # For compatibility
        
        # Multi-window support: up to 8 bots
        self.bots: Dict[int, FishingBot] = {}  # bot_id -> FishingBot
        self.bot_threads: Dict[int, threading.Thread] = {}  # bot_id -> Thread
        self.window_managers: Dict[int, WindowManager] = {}  # bot_id -> WindowManager
        self.window_selections: Dict[int, tk.StringVar] = {}  # bot_id -> selected window name
        self.window_stats: Dict[int, dict] = {}  # bot_id -> {hits, games, bait}
        self.ignored_positions_windows: Dict[int, IgnoredPositionsWindow] = {}  # bot_id -> IgnoredPositionsWindow
        self.fish_detector_debug_windows: Dict[int, FishDetectorDebugWindow] = {}  # bot_id -> FishDetectorDebugWindow
        
        # Global keyboard listener for F5 pause
        self.global_key_listener = None
        if keyboard:
            self.global_key_listener = keyboard.Listener(on_press=self.on_global_key_press)
            self.global_key_listener.start()
        
        # Cooldown for button presses (1 second between actions)
        self.last_action_time = 0
        self.action_cooldown = 3.0  # seconds
        self.in_cooldown = False  # Flag to prevent button re-enabling during cooldown
        
        # Config file path in the current working directory
        self.config_file = os.path.join(os.getcwd(), "bot_config.json")
        
        self.config = {
            'human_like_clicking': True,
            'quick_skip': True,
            'sound_alert_on_finish': True,
            'classic_fishing': False,
            'classic_fishing_delay': 3.0,  # Delay in seconds after fish detection
            'auto_fish_handling': False,
            'fish_actions': {},  # {filename: 'keep'|'drop'|'open'}
        }
        
        # Bait counter
        self.bait = 800
        
        # Fish selection window reference
        self.fish_selection_window = None
        
        # Load config from file if it exists
        self.load_config()
        
        self.setup_ui()
        
    def setup_ui(self):
        """Creates the GUI elements"""
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        
        # Try to load and display GIF
        gif_path = get_resource_path("monkey-eating.gif")
        self.photo_images = []
        self.current_frame = 0
        self.gif_label_left = None
        self.gif_label_right = None
        
        if os.path.exists(gif_path):
            try:
                img = Image.open(gif_path)
                # Extract all frames from the GIF
                for frame_index in range(img.n_frames):
                    img.seek(frame_index)
                    frame = img.convert("RGBA")
                    frame.thumbnail((200, 120), Image.Resampling.LANCZOS)
                    self.photo_images.append(ImageTk.PhotoImage(frame))
            except Exception as e:
                print(f"Error loading GIF: {e}")
        
        # Header (always created)
        header = tk.Frame(self.root, bg="#000000", height=100 if self.photo_images else 45)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        # Header content frame
        header_content = tk.Frame(header, bg="#000000")
        header_content.pack(pady=5)
        
        # Left GIF (only if loaded)
        if self.photo_images:
            self.gif_label_left = tk.Label(header_content, image=self.photo_images[0], bg="#000000")
            self.gif_label_left.pack(side=tk.LEFT, padx=10)
        
        # Title and Discord info container
        title_container = tk.Frame(header_content, bg="#000000")
        title_container.pack(side=tk.LEFT, padx=10)
        
        # Title (always shown)
        title = tk.Label(title_container, text="Fishing Puzzle Player", 
                        font=("Courier New", 16, "bold"), 
                        bg="#000000", fg="#FFD700")
        title.pack(anchor=tk.CENTER)
        
        # Discord info
        discord_label = tk.Label(title_container, text="Discord: boristei", 
                                font=("Courier New", 10), 
                                bg="#000000", fg="#FFD700")
        discord_label.pack(anchor=tk.CENTER)
        
        # Right GIF (only if loaded)
        if self.photo_images:
            self.gif_label_right = tk.Label(header_content, image=self.photo_images[0], bg="#000000")
            self.gif_label_right.pack(side=tk.LEFT, padx=10)
            
            # Start GIF animation (only if GIFs loaded)
            self.animate_gif()
        
        # Main container
        main = tk.Frame(self.root, bg="#1a1a1a")
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)
        
        # Multi-Window Selection Section
        windows_frame = tk.LabelFrame(main, text="Game Windows (up to 8)", 
                                     font=("Courier New", 10, "bold"),
                                     bg="#2a2a2a", fg="#FFD700",
                                     padx=8, pady=5)
        windows_frame.pack(fill=tk.X, pady=3)
        
        # Refresh button at top
        refresh_row = tk.Frame(windows_frame, bg="#2a2a2a")
        refresh_row.pack(fill=tk.X, pady=2)
        
        tk.Button(refresh_row, text="🔄 Refresh Windows",
                 command=self.refresh_windows,
                 bg="#2a2a2a", fg="#FFD700",
                 activebackground="#3a3a3a", activeforeground="#FFD700",
                 font=("Courier New", 9),
                 cursor="hand2",
                 relief=tk.FLAT,
                 padx=8, pady=1).pack(side=tk.LEFT, padx=3)
        
        # Window combos storage
        self.window_combos = {}
        self.window_status_labels = {}
        self.window_bait_labels = {}
        self.window_games_labels = {}
        
        # Create 4 window selection rows
        for i in range(MAX_WINDOWS):
            row_frame = tk.Frame(windows_frame, bg="#2a2a2a")
            row_frame.pack(fill=tk.X, pady=2)
            
            # Window label
            tk.Label(row_frame, text=f"W{i+1}:", 
                    bg="#2a2a2a", fg="#ffffff",
                    font=("Courier New", 9, "bold")).pack(side=tk.LEFT, padx=2)
            
            # Window selection combo
            self.window_selections[i] = tk.StringVar()
            combo = ttk.Combobox(row_frame, textvariable=self.window_selections[i], 
                                state="readonly", width=32)
            combo.pack(side=tk.LEFT, padx=2)
            # Bind selection change event to update bait display
            combo.bind("<<ComboboxSelected>>", lambda event, idx=i: self.on_window_selected(idx))
            self.window_combos[i] = combo
            
            # Status indicator
            status_label = tk.Label(row_frame, text="⚪", 
                                   bg="#2a2a2a", fg="#888888",
                                   font=("Courier New", 10))
            status_label.pack(side=tk.LEFT, padx=3)
            self.window_status_labels[i] = status_label
            
            # Bait counter
            bait_label = tk.Label(row_frame, text="B:---", 
                                 bg="#2a2a2a", fg="#FFD700",
                                 font=("Courier New", 8))
            bait_label.pack(side=tk.LEFT, padx=3)
            self.window_bait_labels[i] = bait_label
            
            # Games counter
            games_label = tk.Label(row_frame, text="G:0", 
                                  bg="#2a2a2a", fg="#00ff00",
                                  font=("Courier New", 8))
            games_label.pack(side=tk.LEFT, padx=3)
            self.window_games_labels[i] = games_label
            
            # Initialize stats
            self.window_stats[i] = {'hits': 0, 'games': 0, 'bait': self.bait}
        
        # Bot Configuration Section
        config_frame = tk.LabelFrame(main, text="Bot Configuration", 
                                    font=("Courier New", 10, "bold"),
                                    bg="#2a2a2a", fg="#FFD700",
                                    padx=8, pady=5)
        config_frame.pack(fill=tk.X, pady=3)
        
        # Classic Fishing checkbox (no minigame) - FIRST option
        classic_frame = tk.Frame(config_frame, bg="#2a2a2a")
        classic_frame.pack(anchor=tk.W, fill=tk.X, pady=2)
        
        self.classic_fishing_var = tk.BooleanVar(value=self.config.get('classic_fishing', False))
        self.classic_fishing_check = tk.Checkbutton(classic_frame, 
                                              text="Classic Fishing",
                                              variable=self.classic_fishing_var,
                                              command=self.toggle_classic_fishing,
                                              bg="#2a2a2a", fg="#ffffff",
                                              selectcolor="#1a1a1a",
                                              activebackground="#2a2a2a",
                                              font=("Courier New", 9))
        self.classic_fishing_check.pack(side=tk.LEFT)
        
        # Delay input for classic fishing
        tk.Label(classic_frame, text="Delay:", bg="#2a2a2a", fg="#aaaaaa",
                font=("Courier New", 8)).pack(side=tk.LEFT, padx=(10, 2))
        
        self.classic_delay_var = tk.StringVar(value=str(self.config.get('classic_fishing_delay', 3.0)))
        self.classic_delay_entry = tk.Entry(classic_frame, textvariable=self.classic_delay_var,
                                           width=5, bg="#1a1a1a", fg="#00ff00",
                                           font=("Courier New", 9), insertbackground="#00ff00")
        self.classic_delay_entry.pack(side=tk.LEFT)
        self.classic_delay_entry.bind('<FocusOut>', self.update_classic_delay)
        self.classic_delay_entry.bind('<Return>', self.update_classic_delay)
        
        tk.Label(classic_frame, text="sec", bg="#2a2a2a", fg="#aaaaaa",
                font=("Courier New", 8)).pack(side=tk.LEFT, padx=(2, 0))
        
        # Human-like clicking
        self.human_like_var = tk.BooleanVar(value=self.config.get('human_like_clicking', True))
        self.human_like_check = tk.Checkbutton(config_frame, 
                                    text="Human-like clicking (random offset)",
                                    variable=self.human_like_var,
                                    bg="#2a2a2a", fg="#ffffff",
                                    selectcolor="#1a1a1a",
                                    activebackground="#2a2a2a",
                                    disabledforeground="#666666",
                                    font=("Courier New", 9))
        self.human_like_check.pack(anchor=tk.W, pady=2)
        
        # Quick skip checkbox
        self.quick_skip_var = tk.BooleanVar(value=self.config.get('quick_skip', False))
        quick_skip_check = tk.Checkbutton(config_frame, 
                                         text="Quick skip (double press CTRL+G)",
                                         variable=self.quick_skip_var,
                                         bg="#2a2a2a", fg="#ffffff",
                                         selectcolor="#1a1a1a",
                                         activebackground="#2a2a2a",
                                         font=("Courier New", 9))
        quick_skip_check.pack(anchor=tk.W, pady=2)
        
        # Sound alert checkbox
        self.sound_alert_var = tk.BooleanVar(value=self.config.get('sound_alert_on_finish', True))
        sound_alert_check = tk.Checkbutton(config_frame, 
                                          text="No bait alert",
                                          variable=self.sound_alert_var,
                                          bg="#2a2a2a", fg="#ffffff",
                                          selectcolor="#1a1a1a",
                                          activebackground="#2a2a2a",
                                          font=("Courier New", 9))
        sound_alert_check.pack(anchor=tk.W, pady=2)
        
        # Bait Keys Selection Section
        bait_keys_frame = tk.LabelFrame(config_frame, text="Bait Keys (200 bait each)", 
                                        font=("Courier New", 9),
                                        bg="#2a2a2a", fg="#FFD700",
                                        padx=4, pady=3)
        bait_keys_frame.pack(fill=tk.X, pady=2)
        
        # Get saved bait keys or default to ['1', '2', '3', '4']
        saved_bait_keys = self.config.get('bait_keys', ['1', '2', '3', '4'])
        
        # Number keys row
        num_keys_frame = tk.Frame(bait_keys_frame, bg="#2a2a2a")
        num_keys_frame.pack(fill=tk.X)
        
        self.bait_key_vars = {}
        for key in ['1', '2', '3', '4']:
            var = tk.BooleanVar(value=key in saved_bait_keys)
            self.bait_key_vars[key] = var
            cb = tk.Checkbutton(num_keys_frame, text=key, variable=var,
                               command=self.update_bait_capacity,
                               bg="#2a2a2a", fg="#ffffff",
                               selectcolor="#1a1a1a",
                               activebackground="#2a2a2a",
                               font=("Courier New", 9),
                               width=1)
            cb.pack(side=tk.LEFT, padx=(1, 18))
        
        # Function keys row
        fn_keys_frame = tk.Frame(bait_keys_frame, bg="#2a2a2a")
        fn_keys_frame.pack(fill=tk.X)
        
        for key in ['F1', 'F2', 'F3', 'F4']:
            var = tk.BooleanVar(value=key in saved_bait_keys)
            self.bait_key_vars[key] = var
            cb = tk.Checkbutton(fn_keys_frame, text=key, variable=var,
                               command=self.update_bait_capacity,
                               bg="#2a2a2a", fg="#ffffff",
                               selectcolor="#1a1a1a",
                               activebackground="#2a2a2a",
                               font=("Courier New", 9),
                               width=1)
            cb.pack(side=tk.LEFT, padx=(4, 14))
        
        # Capacity label
        self.bait_capacity_label = tk.Label(bait_keys_frame, text="", 
                                           bg="#2a2a2a", fg="#00ff00",
                                           font=("Courier New", 8))
        self.bait_capacity_label.pack(anchor=tk.W, pady=1)
        
        # Reset All Bait button
        self.reset_btn = tk.Button(bait_keys_frame,
                                  text="Reset All Bait",
                                  command=self.reset_bait,
                                  font=("Courier New", 8),
                                  bg="#e74c3c", fg="white",
                                  activebackground="#c0392b",
                                  cursor="hand2",
                                  padx=3, pady=2)
        self.reset_btn.pack(anchor=tk.W, pady=2)
        
        # Automatic Fish Handling Section
        fish_handling_frame = tk.LabelFrame(config_frame, text="Automatic Fish Handling", 
                                           font=("Courier New", 9),
                                           bg="#2a2a2a", fg="#FFD700",
                                           padx=4, pady=3)
        fish_handling_frame.pack(fill=tk.X, pady=2)
        
        # Enable checkbox and button row
        fish_handling_row = tk.Frame(fish_handling_frame, bg="#2a2a2a")
        fish_handling_row.pack(fill=tk.X)
        
        # Automatic fish handling checkbox
        self.auto_fish_var = tk.BooleanVar(value=self.config.get('auto_fish_handling', False))
        auto_fish_check = tk.Checkbutton(fish_handling_row, 
                                        text="Enable",
                                        variable=self.auto_fish_var,
                                        command=self.toggle_auto_fish_handling,
                                        bg="#2a2a2a", fg="#ffffff",
                                        selectcolor="#1a1a1a",
                                        activebackground="#2a2a2a",
                                        font=("Courier New", 9))
        auto_fish_check.pack(side=tk.LEFT, padx=2)
        
        # Select Fishes button
        self.select_fishes_btn = tk.Button(fish_handling_row,
                                          text="🐟 Select Fishes",
                                          command=self.open_fish_selection_window,
                                          font=("Courier New", 8),
                                          bg="#3498db", fg="white",
                                          activebackground="#2980b9",
                                          cursor="hand2",
                                          state=tk.DISABLED,
                                          padx=5, pady=2)
        self.select_fishes_btn.pack(side=tk.LEFT, padx=10)
        
        # Update button state based on checkbox
        self.toggle_auto_fish_handling()
        
        # Update human-like clicking state based on classic fishing (no warning on startup)
        self.toggle_classic_fishing(show_warning=False)
        
        # Statistics Section (Total across all windows)
        stats_frame = tk.LabelFrame(main, text="Total Statistics", 
                                   font=("Courier New", 10, "bold"),
                                   bg="#2a2a2a", fg="#FFD700",
                                   padx=8, pady=5)
        stats_frame.pack(fill=tk.X, pady=3)
        
        stats_grid = tk.Frame(stats_frame, bg="#2a2a2a")
        stats_grid.pack(fill=tk.X)
        
        # Total games across all windows
        tk.Label(stats_grid, text="Total Games:", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 9)).grid(row=0, column=0, sticky=tk.W, pady=1)
        self.total_games_label = tk.Label(stats_grid, text="0", 
                                         bg="#2a2a2a", fg="#FFD700",
                                         font=("Courier New", 9, "bold"))
        self.total_games_label.grid(row=0, column=1, sticky=tk.W, padx=15, pady=1)
        
        # Active windows count
        tk.Label(stats_grid, text="Active Windows:", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 9)).grid(row=0, column=2, sticky=tk.W, padx=15, pady=1)
        self.active_windows_label = tk.Label(stats_grid, text="0", 
                                            bg="#2a2a2a", fg="#00ff00",
                                            font=("Courier New", 9, "bold"))
        self.active_windows_label.grid(row=0, column=3, sticky=tk.W, padx=5, pady=1)
        
        # Reset all bait button
        tk.Label(stats_grid, text="Total bait:", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 9)).grid(row=1, column=0, sticky=tk.W, pady=1)
        # Calculate total bait across selected windows only
        total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
        self.bait_label = tk.Label(stats_grid, text=str(total_bait), 
                                  bg="#2a2a2a", fg="#FFD700",
                                  font=("Courier New", 9, "bold"))
        self.bait_label.grid(row=1, column=1, sticky=tk.W, padx=15, pady=1)
        
        # Now that bait_label exists, update capacity for the first time
        self.update_bait_capacity()
        
        # Create separate status log window (only if DEBUG_MODE_EN is true)
        self.status_log_window = None
        if DEBUG_MODE_EN:
            self.status_log_window = StatusLogWindow(self.root)
            self.status_log_window.show()  # Show it by default in debug mode
        
        # Control Buttons Section
        button_frame = tk.Frame(main, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, pady=5)
        
        # Start/Pause button (combines start, pause, resume functionality)
        self.start_pause_btn = tk.Button(button_frame, 
                                       text="▶ Start All",
                                       command=self.start_or_pause_bots,
                                       font=("Courier New", 11, "bold"),
                                       bg="#2ecc71", fg="white",
                                       activebackground="#27ae60",
                                       cursor="hand2",
                                       state=tk.NORMAL,
                                       padx=40, pady=8)
        self.start_pause_btn.pack(side=tk.LEFT, expand=True, padx=3)
        
        # Stop All button
        self.stop_all_btn = tk.Button(button_frame, 
                                      text="⏹ Stop All",
                                      command=self.stop_all_bots,
                                      font=("Courier New", 11, "bold"),
                                      bg="#e74c3c", fg="white",
                                      activebackground="#c0392b",
                                      cursor="hand2",
                                      state=tk.DISABLED,
                                      padx=40, pady=8)
        self.stop_all_btn.pack(side=tk.LEFT, expand=True, padx=3)
        
        self.add_status("Welcome! Select up to 8 windows and click Start All to begin.")
        self.add_status("Press F5 to pause/resume all bots.")
        
        # Refresh windows list after UI is fully initialized
        self.refresh_windows()
        
        # Restore previously selected windows if they exist
        if self.previous_windows:
            try:
                current_windows = set(self.window_combos[0]['values'])
                for i, prev_win in enumerate(self.previous_windows):
                    if i < MAX_WINDOWS and prev_win and prev_win in current_windows:
                        self.window_selections[i].set(prev_win)
                        # Update bait label for restored window
                        self.window_stats[i]['bait'] = self.bait
                        self.window_bait_labels[i].config(text=f"B:{self.bait}")
                        self.add_status(f"Restored window {i+1}: {prev_win}")
                # Update total bait label after restoring windows
                total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
                self.bait_label.config(text=str(total_bait))
            except Exception as e:
                print(f"Error restoring window selection: {e}")
        
        # Donations Section (at the very bottom)
        donations_frame = tk.Frame(self.root, bg="#000000")
        donations_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        donations_text_frame = tk.Frame(donations_frame, bg="#000000")
        donations_text_frame.pack(pady=2)
        
        self.btc_address = "3AGrrTf1v9QZsMPEoezYTRbf9JyW4nQtHu"
        donations_label = tk.Label(donations_text_frame, 
                                  text=f"Donations in BTC: {self.btc_address}",
                                  font=("Courier New", 9),
                                  bg="#000000", fg="#FFD700",
                                  wraplength=600, justify=tk.CENTER)
        donations_label.pack(side=tk.LEFT, padx=3)
        
        copy_btn = tk.Button(donations_text_frame,
                            text="📋",
                            command=self.copy_btc_address,
                            font=("Courier New", 10),
                            bg="#000000", fg="#FFD700",
                            activebackground="#1a1a1a", activeforeground="#FFD700",
                            relief=tk.FLAT,
                            cursor="hand2",
                            padx=3, pady=0)
        copy_btn.pack(side=tk.LEFT, padx=2)
    
    def load_config(self):
        """
        Loads configuration from the config file if it exists.
        Restores human_like_clicking, quick_skip, bait counter, bait keys, and window selections.
        """
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    saved_config = json.load(f)
                    # Restore config settings
                    if 'human_like_clicking' in saved_config:
                        self.config['human_like_clicking'] = saved_config['human_like_clicking']
                    if 'quick_skip' in saved_config:
                        self.config['quick_skip'] = saved_config['quick_skip']
                    if 'sound_alert_on_finish' in saved_config:
                        self.config['sound_alert_on_finish'] = saved_config['sound_alert_on_finish']
                    if 'classic_fishing' in saved_config:
                        self.config['classic_fishing'] = saved_config['classic_fishing']
                    if 'classic_fishing_delay' in saved_config:
                        self.config['classic_fishing_delay'] = saved_config['classic_fishing_delay']
                    # Restore bait keys
                    if 'bait_keys' in saved_config:
                        self.config['bait_keys'] = saved_config['bait_keys']
                    # Restore bait counter
                    if 'bait' in saved_config:
                        self.bait = saved_config['bait']
                    # Restore auto fish handling settings
                    if 'auto_fish_handling' in saved_config:
                        self.config['auto_fish_handling'] = saved_config['auto_fish_handling']
                    if 'fish_actions' in saved_config:
                        self.config['fish_actions'] = saved_config['fish_actions']
                    # Store previously selected windows for later restoration (multi-window)
                    self.previous_windows = saved_config.get('selected_windows', [])
                    # Also support legacy single window
                    if not self.previous_windows and saved_config.get('selected_window'):
                        self.previous_windows = [saved_config.get('selected_window')]
            except Exception as e:
                print(f"Error loading config: {e}")
                self.previous_windows = []
        else:
            self.previous_windows = []
    
    def save_config(self):
        """
        Saves current configuration to the config file.
        Saves human_like_clicking, quick_skip, bait counter, and selected windows.
        """
        try:
            # Get selected bait keys
            selected_bait_keys = self.get_selected_bait_keys() if hasattr(self, 'bait_key_vars') else ['1', '2', '3', '4']
            
            # Get all selected windows
            selected_windows = []
            if hasattr(self, 'window_selections'):
                for i in range(MAX_WINDOWS):
                    win_name = self.window_selections[i].get() if i in self.window_selections else ""
                    selected_windows.append(win_name)
            
            config_data = {
                'human_like_clicking': self.config.get('human_like_clicking', True),
                'quick_skip': self.config.get('quick_skip', False),
                'sound_alert_on_finish': self.config.get('sound_alert_on_finish', True),
                'classic_fishing': self.config.get('classic_fishing', False),
                'classic_fishing_delay': self.config.get('classic_fishing_delay', 3.0),
                'auto_fish_handling': self.config.get('auto_fish_handling', False),
                'fish_actions': self.config.get('fish_actions', {}),
                'bait_keys': selected_bait_keys,
                'bait': self.bait,
                'selected_windows': selected_windows,
                'selected_window': selected_windows[0] if selected_windows else None  # Legacy support
            }
            with open(self.config_file, 'w') as f:
                json.dump(config_data, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def get_selected_bait_keys(self) -> list:
        """Returns list of selected bait keys in order."""
        key_order = ['1', '2', '3', '4', 'F1', 'F2', 'F3', 'F4']
        return [key for key in key_order if self.bait_key_vars.get(key, tk.BooleanVar(value=False)).get()]
    
    def get_max_bait_capacity(self) -> int:
        """Returns max bait capacity based on selected keys (200 per key)."""
        return len(self.get_selected_bait_keys()) * 200
    
    def update_bait_capacity(self):
        """Updates the bait capacity label based on selected keys."""
        selected_keys = self.get_selected_bait_keys()
        capacity = len(selected_keys) * 200
        if capacity > 0:
            self.bait_capacity_label.config(
                text=f"Max capacity: {capacity} bait ({len(selected_keys)} keys)",
                fg="#00ff00"
            )
            # Reset bait to new capacity
            self.bait = capacity
            
            # Update bait counters and labels for all windows
            for i in range(MAX_WINDOWS):
                self.window_stats[i]['bait'] = capacity
                # Update label - show capacity if selected, otherwise show B:---
                is_selected = self.window_selections[i].get()
                if is_selected:
                    self.window_bait_labels[i].config(text=f"B:{capacity}")
                else:
                    self.window_bait_labels[i].config(text="B:---")
            
            # Update total bait label with sum of selected windows only
            total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
            self.bait_label.config(text=str(total_bait))
            
            self.save_config()
        else:
            # No keys selected - set bait to 0
            self.bait = 0
            self.bait_capacity_label.config(
                text="⚠ Select at least one bait key!",
                fg="#e74c3c"
            )
            self.bait_label.config(text="0")
            # Reset all window bait displays - selected windows show B:0, unselected show B:---
            for i in range(MAX_WINDOWS):
                self.window_stats[i]['bait'] = 0
                is_selected = self.window_selections[i].get()
                self.window_bait_labels[i].config(text="B:0" if is_selected else "B:---")
            self.save_config()
        
    def refresh_windows(self):
        """Refreshes the list of available windows for all window combos"""
        try:
            windows = WindowManager.get_all_windows()
            window_names = [name for name, _ in windows]
            
            # Add empty option at the start to allow unselecting
            window_names_with_empty = [""] + window_names
            window_names_set = set(window_names)
            
            # Update all window combos and preserve current selections if still available
            for i in range(MAX_WINDOWS):
                current_sel = self.window_selections[i].get()
                self.window_combos[i]['values'] = window_names_with_empty
                # Restore selection if it's still available
                if current_sel and current_sel in window_names_set:
                    self.window_selections[i].set(current_sel)
            
            if window_names:
                self.add_status(f"Found {len(window_names)} visible window(s)")
            else:
                self.add_status("No visible windows found")
        except Exception as e:
            self.add_status(f"Error getting windows: {e}")
        
    def add_status(self, message: str):
        """
        Adds a status message to the status log window.
        
        Args:
            message: The status message to display
        """
        if not DEBUG_MODE_EN or not hasattr(self, 'status_log_window') or not self.status_log_window:
            return
        self.status_log_window.add_message(message)
    
    def toggle_log_visibility(self):
        """Toggles the visibility of the status log window."""
        if self.status_log_window is None:
            return
        if self.show_log_var.get():
            self.status_log_window.show()
        else:
            self.status_log_window.hide()
    
    def toggle_classic_fishing(self, show_warning: bool = True):
        """Toggles the classic fishing mode and disables human-like clicking when enabled.
        show_warning: If True, shows warning message when enabling. Set to False when loading from config."""
        enabled = self.classic_fishing_var.get()
        self.config['classic_fishing'] = enabled
        
        if enabled:
            # Show warning message about classic fishing mode (only if user clicked, not on config load)
            if show_warning:
                messagebox.showwarning(
                    "Classic Fishing Mode",
                    "Classic Fishing Mode\n\n"
                    "This mode only works with the OLD Metin2 fishing system!\n\n"
                    "It will NOT work with the minigame fishing system.\n"
                    "Make sure your server uses the classic fishing mechanics."
                )
            # Disable human-like clicking when classic fishing is enabled
            self.human_like_check.config(state=tk.DISABLED)
            self.classic_delay_entry.config(state=tk.NORMAL)
        else:
            # Re-enable human-like clicking when classic fishing is disabled
            self.human_like_check.config(state=tk.NORMAL)
        
        self.save_config()
    
    def update_classic_delay(self, event=None):
        """Updates the classic fishing delay from the entry field."""
        try:
            delay = float(self.classic_delay_var.get())
            if delay < 0:
                delay = 0
            elif delay > 30:
                delay = 30  # Max 30 seconds
            self.config['classic_fishing_delay'] = delay
            self.classic_delay_var.set(str(delay))
            self.save_config()
            
            # Update running bots with new delay value
            for bot_id, bot in self.bots.items():
                if bot and bot.running:
                    bot.config['classic_fishing_delay'] = delay
        except ValueError:
            # Reset to current config value if invalid
            self.classic_delay_var.set(str(self.config.get('classic_fishing_delay', 3.0)))
    
    def toggle_auto_fish_handling(self):
        """Toggles the automatic fish handling feature and updates button state."""
        enabled = self.auto_fish_var.get()
        self.config['auto_fish_handling'] = enabled
        
        if enabled:
            self.select_fishes_btn.config(state=tk.NORMAL)
        else:
            self.select_fishes_btn.config(state=tk.DISABLED)
        
        self.save_config()
    
    def open_fish_selection_window(self):
        """Opens the fish selection window for configuring fish/item actions."""
        # Check if window already exists and is open
        if self.fish_selection_window is not None:
            try:
                self.fish_selection_window.window.lift()
                self.fish_selection_window.window.focus_force()
                return
            except tk.TclError:
                # Window was closed, create a new one
                self.fish_selection_window = None
        
        # Create new fish selection window
        self.fish_selection_window = FishSelectionWindow(
            self.root, 
            self.config.get('fish_actions', {}),
            self.on_fish_actions_saved
        )
    
    def on_fish_actions_saved(self, fish_actions: dict):
        """Callback when fish actions are saved from the selection window."""
        self.config['fish_actions'] = fish_actions
        self.save_config()
        self.add_status(f"Fish actions saved: {len(fish_actions)} items configured")

    def on_window_selected(self, window_id: int):
        """Updates bait display when a window is selected."""
        selected_name = self.window_selections[window_id].get()
        
        # Check if this window is already selected in another slot (optimized check)
        if selected_name:
            selected_windows = {self.window_selections[i].get() for i in range(MAX_WINDOWS) if i != window_id}
            if selected_name in selected_windows:
                # Window already selected elsewhere, prevent duplicate
                self.window_selections[window_id].set("")
                self.add_status(f"Window '{selected_name}' is already selected in another slot")
                # Reset display
                self.window_stats[window_id]['bait'] = 0
                self.window_bait_labels[window_id].config(text="B:---")
                return
            
            # Window is selected - update bait to current capacity
            self.window_stats[window_id]['bait'] = self.bait
            self.window_bait_labels[window_id].config(text=f"B:{self.bait}")
        else:
            # Window is unselected - show --- and reset bait to 0
            self.window_stats[window_id]['bait'] = 0
            self.window_bait_labels[window_id].config(text="B:---")
        
        # Update total bait label to reflect new sum of selected windows
        total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
        self.bait_label.config(text=str(total_bait))
    
    def animate_gif(self):
        """Animates the GIF frames."""
        if self.photo_images and (self.gif_label_left or self.gif_label_right):
            self.current_frame = (self.current_frame + 1) % len(self.photo_images)
            if self.gif_label_left:
                self.gif_label_left.config(image=self.photo_images[self.current_frame])
            if self.gif_label_right:
                self.gif_label_right.config(image=self.photo_images[self.current_frame])
            # Schedule next frame update (50ms for smooth animation)
            self.root.after(50, self.animate_gif)
    
    def on_global_key_press(self, key):
        """Global key press handler for F5 pause/resume all bots."""
        try:
            if key == keyboard.Key.f5:
                self.root.after(0, self.toggle_pause_all_bots)
        except AttributeError:
            pass
    
    def disable_buttons_for_cooldown(self):
        """Disables all control buttons during cooldown period."""
        self.in_cooldown = True
        self.start_pause_btn.config(state=tk.DISABLED)
        self.stop_all_btn.config(state=tk.DISABLED)
        # Re-enable buttons after cooldown
        self.root.after(int(self.action_cooldown * 1000), self.end_cooldown_and_update_buttons)
    
    def end_cooldown_and_update_buttons(self):
        """Ends cooldown period and updates button states."""
        self.in_cooldown = False
        self.update_all_button_states()
    
    def start_or_pause_bots(self):
        """Combined Start/Pause/Resume handler - decides action based on current state."""
        any_running = any(bot.running for bot in self.bots.values()) if self.bots else False
        
        if not any_running:
            # No bots running - start them
            self.start_all_bots()
        else:
            # Bots are running - toggle pause
            self.toggle_pause_all_bots()
    
    def toggle_pause_all_bots(self):
        """Toggles pause state for all running bots."""
        # Check cooldown
        current_time = time.time()
        if current_time - self.last_action_time < self.action_cooldown:
            return
        self.last_action_time = current_time
        self.disable_buttons_for_cooldown()
        
        any_running = any(bot.running for bot in self.bots.values())
        if not any_running:
            return
        
        any_paused = any(bot.paused for bot in self.bots.values() if bot.running)
        
        for bot_id, bot in self.bots.items():
            if bot.running:
                bot.paused = not any_paused
                # Update status indicator
                if bot_id in self.window_status_labels:
                    if bot.paused:
                        self.window_status_labels[bot_id].config(text="🟡", fg="#f39c12")
                    else:
                        self.window_status_labels[bot_id].config(text="🟢", fg="#00ff00")
        
        status = "PAUSED" if not any_paused else "RESUMED"
        self.add_status(f"All bots {status} (F5)")
    
    def update_stats(self, bot_id: int, hits: int, total_games: int, bait: int):
        """Updates the statistics display for a specific bot."""
        if bot_id in self.window_stats:
            self.window_stats[bot_id] = {'hits': hits, 'games': total_games, 'bait': bait}
        
        # Update individual window labels
        if bot_id in self.window_bait_labels:
            self.window_bait_labels[bot_id].config(text=f"B:{bait}")
        if bot_id in self.window_games_labels:
            self.window_games_labels[bot_id].config(text=f"G:{total_games}")
        
        # Update total statistics
        total_all_games = sum(s['games'] for s in self.window_stats.values())
        self.total_games_label.config(text=str(total_all_games))
        
        # Update total bait across selected windows only
        total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
        self.bait_label.config(text=str(total_bait))
        
        # Count active windows
        active_count = len([b for b in self.bots.values() if b.running])
        self.active_windows_label.config(text=str(active_count))
    
    def reset_bait(self):
        """Resets the bait counter to max capacity for selected windows, 0 for unselected"""
        max_bait = self.get_max_bait_capacity()
        self.bait = max_bait
        self.bait_label.config(text=str(max_bait))
        
        # Reset all bots' bait counters
        for bot_id, bot in self.bots.items():
            bot.bait_counter = max_bait
            self.window_stats[bot_id]['bait'] = max_bait
            self.window_bait_labels[bot_id].config(text=f"B:{max_bait}")
        
        # Reset stats for all non-running windows
        for i in range(MAX_WINDOWS):
            if i not in self.bots:
                is_selected = self.window_selections[i].get()
                if is_selected:
                    self.window_stats[i]['bait'] = max_bait
                    self.window_bait_labels[i].config(text=f"B:{max_bait}")
                else:
                    self.window_stats[i]['bait'] = 0
                    self.window_bait_labels[i].config(text="B:---")
        
        self.add_status(f"All bait counters reset to {max_bait}")
        self.save_config()
    
    def update_bait_from_bot(self, bot_id: int, new_bait: int):
        """Updates GUI bait counter when bot adjusts bait tier."""
        if bot_id in self.window_stats:
            self.window_stats[bot_id]['bait'] = new_bait
        if bot_id in self.window_bait_labels:
            self.window_bait_labels[bot_id].config(text=f"B:{new_bait}")
        self.save_config()
    
    def start_all_bots(self):
        """Starts bots for all selected windows."""
        # Check cooldown
        current_time = time.time()
        if current_time - self.last_action_time < self.action_cooldown:
            return
        self.last_action_time = current_time
        self.disable_buttons_for_cooldown()
        
        # Get config
        self.config['human_like_clicking'] = self.human_like_var.get()
        self.config['quick_skip'] = self.quick_skip_var.get()
        self.config['sound_alert_on_finish'] = self.sound_alert_var.get()
        self.config['classic_fishing'] = self.classic_fishing_var.get()
        # Update delay from entry field
        try:
            self.config['classic_fishing_delay'] = float(self.classic_delay_var.get())
        except ValueError:
            self.config['classic_fishing_delay'] = 3.0
        self.save_config()
        
        # Get selected bait keys
        selected_bait_keys = self.get_selected_bait_keys()
        if not selected_bait_keys:
            messagebox.showerror("Error", "Please select at least one bait key!")
            return
        
        # Get all available windows
        all_windows = WindowManager.get_all_windows()
        window_dict = {name: win for name, win in all_windows}
        
        # Start a bot for each selected window
        started_count = 0
        for bot_id in range(MAX_WINDOWS):
            selected_name = self.window_selections[bot_id].get()
            if not selected_name:
                continue
            
            if selected_name not in window_dict:
                self.add_status(f"[W{bot_id+1}] Window not found: {selected_name}")
                continue
            
            # Skip if bot already running for this window
            if bot_id in self.bots and self.bots[bot_id].running:
                continue
            
            selected_window = window_dict[selected_name]
            
            # Create window manager for this bot
            wm = WindowManager()
            wm.selected_window = selected_window
            self.window_managers[bot_id] = wm
            
            # Create and configure bot
            bot = FishingBot(
                None, 
                self.config.copy(), 
                wm, 
                bait_counter=self.bait, 
                bait_keys=selected_bait_keys.copy(),
                bot_id=bot_id
            )
            bot.on_status_update = self.add_status
            bot.on_stats_update = self.update_stats
            bot.on_pause_toggle = self.on_bot_pause_toggle
            bot.on_bait_update = self.update_bait_from_bot
            bot.on_bot_stop = self.on_bot_stopped
            
            bot.running = True
            self.bots[bot_id] = bot
            
            # Initialize stats
            self.window_stats[bot_id] = {'hits': 0, 'games': 0, 'bait': self.bait}
            
            # Create ignored positions debug window (only if DEBUG_MODE_EN is true)
            if DEBUG_MODE_EN:
                self.ignored_positions_windows[bot_id] = IgnoredPositionsWindow(self.root, bot)
                self.fish_detector_debug_windows[bot_id] = FishDetectorDebugWindow(self.root, bot)
            
            # Start bot thread
            thread = threading.Thread(target=bot.start, daemon=True)
            thread.start()
            self.bot_threads[bot_id] = thread
            
            # Update status indicator
            self.window_status_labels[bot_id].config(text="🟢", fg="#00ff00")
            self.window_combos[bot_id].config(state="disabled")
            
            started_count += 1
            self.add_status(f"[W{bot_id+1}] Bot started for: {selected_name}")
        
        if started_count == 0:
            messagebox.showerror("Error", "Please select at least one window!")
            return
        
        self.add_status(f"Started {started_count} bot(s)")
        
        # Keep bot reference for compatibility
        if self.bots:
            self.bot = list(self.bots.values())[0]
    
    def stop_all_bots(self):
        """Stops all running bots."""
        # Check cooldown
        current_time = time.time()
        if current_time - self.last_action_time < self.action_cooldown:
            return
        self.last_action_time = current_time
        self.disable_buttons_for_cooldown()
        
        for bot_id, bot in list(self.bots.items()):
            bot.running = False
            bot.stop()
            self.window_status_labels[bot_id].config(text="⚪", fg="#888888")
            self.window_combos[bot_id].config(state="readonly")
        
        self.bots.clear()
        self.bot_threads.clear()
        self.bot = None
        
        self.add_status("All bots stopped")
    
    def update_all_button_states(self):
        """Updates all control buttons based on bot states."""
        # Don't update buttons during cooldown period
        if self.in_cooldown:
            return
        
        any_running = any(bot.running for bot in self.bots.values()) if self.bots else False
        any_paused = any(bot.paused for bot in self.bots.values() if bot.running) if self.bots else False
        
        if any_running:
            self.start_pause_btn.config(state=tk.NORMAL)
            self.stop_all_btn.config(state=tk.NORMAL)
            
            if any_paused:
                # Show Resume button
                self.start_pause_btn.config(text="▶ Resume All (F5)", bg="#2ecc71", activebackground="#27ae60")
            else:
                # Show Pause button
                self.start_pause_btn.config(text="⏸ Pause All (F5)", bg="#f39c12", activebackground="#e67e22")
        else:
            # No bots running - show Start button
            self.start_pause_btn.config(state=tk.NORMAL, text="▶ Start All", bg="#2ecc71", activebackground="#27ae60")
            self.stop_all_btn.config(state=tk.DISABLED)
        
        # Update active windows count
        active_count = len([b for b in self.bots.values() if b.running])
        self.active_windows_label.config(text=str(active_count))
    
    def on_bot_pause_toggle(self, bot_id: int, is_paused: bool):
        """Updates UI when a bot's pause state changes."""
        if bot_id in self.window_status_labels:
            if is_paused:
                self.window_status_labels[bot_id].config(text="🟡", fg="#f39c12")
            else:
                self.window_status_labels[bot_id].config(text="🟢", fg="#00ff00")
        self.update_all_button_states()
    
    def on_bot_stopped(self, bot_id: int):
        """Updates UI when a bot stops running."""
        if bot_id in self.window_status_labels:
            self.window_status_labels[bot_id].config(text="🔴", fg="#e74c3c")
        if bot_id in self.window_combos:
            self.window_combos[bot_id].config(state="readonly")
        
        # Destroy ignored positions window
        if bot_id in self.ignored_positions_windows:
            self.ignored_positions_windows[bot_id].destroy()
            del self.ignored_positions_windows[bot_id]
        
        # Destroy fish detector debug window
        if bot_id in self.fish_detector_debug_windows:
            self.fish_detector_debug_windows[bot_id].destroy()
            del self.fish_detector_debug_windows[bot_id]
        
        # Remove from active bots
        if bot_id in self.bots:
            del self.bots[bot_id]
        if bot_id in self.bot_threads:
            del self.bot_threads[bot_id]
        
        # Check if all bots stopped
        if not self.bots:
            self.bot = None
        
        self.update_all_button_states()
    
    def run(self):
        """Starts the GUI application."""
        # FAILSAFE already disabled at module level for multi-window support
        pyautogui.PAUSE = 0.01
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()
    
    def on_close(self):
        """
        Handles the window close event.
        Stops all bots and saves configuration before closing.
        """
        # Stop all running bots
        for bot in self.bots.values():
            bot.running = False
        
        # Stop global keyboard listener
        if self.global_key_listener:
            self.global_key_listener.stop()
        
        # Destroy status log window
        if hasattr(self, 'status_log_window') and self.status_log_window:
            self.status_log_window.destroy()
        
        # Destroy all ignored positions windows
        for window in self.ignored_positions_windows.values():
            try:
                window.destroy()
            except:
                pass
        
        # Destroy all fish detector debug windows
        for window in self.fish_detector_debug_windows.values():
            try:
                window.destroy()
            except:
                pass
        
        self.save_config()
        self.root.destroy()
    
    def copy_btc_address(self):
        """Copies the BTC address to clipboard."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.btc_address)
            self.add_status(f"BTC address copied: {self.btc_address}")
        except Exception as e:
            self.add_status(f"Error copying address: {e}")

if __name__ == "__main__":
    gui = BotGUI()
    gui.run()
"""
Metin2 Fishing Bot - Automated fishing minigame player with GUI

This bot uses computer vision (OpenCV) to detect game elements and automate
the Metin2 fishing minigame. It detects fish positions and the "Try Again" 
button to automatically complete fishing sessions.

Requirements:
pip install opencv-python numpy pillow pyautogui mss tkinter pygetwindow psutil pynput

Usage:
1. Run the script to open the GUI
2. Select a process/window from the dropdown
3. Click "Select Region" and define the fishing window area
4. Adjust bot settings if needed
5. Click "Start Bot" to begin automation
6. The bot will automatically click "Try Again" when games end

Key Components:
- WindowManager: Handles window detection and focus
- FishDetector: Computer vision detection of fish and UI elements
- FishingBot: Main bot logic and game loop
- BotGUI: Tkinter GUI for user interaction
"""

import cv2
import numpy as np
import time
import pyautogui
from mss import mss
from PIL import Image, ImageTk
import threading
from dataclasses import dataclass
from typing import Optional, Tuple, List
import tkinter as tk
from tkinter import ttk, messagebox
import pygetwindow as gw
import psutil
try:
    from pynput import mouse, keyboard
except ImportError:
    print("ERROR: pynput not installed!")
    print("Install with: pip install pynput")
    mouse = None
    keyboard = None

class WindowManager:
    """Manages window detection and focus for the bot"""
    
    def __init__(self):
        self.selected_window = None
    
    @staticmethod
    def get_all_windows() -> List[Tuple[str, gw.Win32Window]]:
        """Gets all visible windows grouped by process name"""
        windows = []
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    process_name = proc.info['name']
                    wins = gw.getWindowsWithTitle(process_name)
                    for win in wins:
                        try:
                            # Check if window is visible - handle both property and method
                            is_visible = getattr(win, 'isVisible', True)
                            if callable(is_visible):
                                is_visible = is_visible()
                            if is_visible:
                                display_name = f"{process_name} - {win.title}"
                                windows.append((display_name, win))
                        except Exception:
                            # If we can't determine visibility, include it anyway
                            display_name = f"{process_name} - {win.title}"
                            windows.append((display_name, win))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            print(f"Error getting windows: {e}")
        
        return windows
    
    def select_window(self, window: gw.Win32Window):
        """Selects and activates a window"""
        self.selected_window = window
        try:
            window.activate()
            time.sleep(0.5)
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
    
    def convert_to_absolute_coords(self, rel_x: int, rel_y: int) -> Tuple[int, int]:
        """Converts relative window coordinates to absolute screen coordinates"""
        if not self.selected_window:
            return (rel_x, rel_y)
        
        left, top, _, _ = self.get_window_rect()
        return (left + rel_x, top + rel_y)

@dataclass
class GameRegion:
    """Stores the coordinates of the game window region (relative to selected window)"""
    left: int
    top: int
    width: int
    height: int

class FishDetector:
    """Detects fish and game elements using computer vision"""
    
    def __init__(self):
        # Color ranges for detecting the fish (gray/dark colors)
        self.fish_color_lower = np.array([0, 0, 30])
        self.fish_color_upper = np.array([180, 50, 120])
        
        # Color range for the circle - blue/purple circle
        self.circle_color_lower = np.array([90, 100, 100])  # Blue hue range
        self.circle_color_upper = np.array([130, 255, 255])  # Blue hue range
        
        # Color range for "Try Again" button (green)
        self.button_color_lower = np.array([40, 100, 100])
        self.button_color_upper = np.array([80, 255, 255])
        
    def detect_fishing_window(self, frame: np.ndarray) -> bool:
        """
        Detects if the fishing window is currently active by looking for the blue circle.
        The circle is a key element that appears during active fishing gameplay.
        
        Args:
            frame: The captured game frame to analyze
            
        Returns:
            True if fishing window is active (circle detected), False otherwise
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # Create mask for blue/purple circle (hue 90-130)
        mask = cv2.inRange(hsv, self.circle_color_lower, self.circle_color_upper)
        
        # If we detect enough blue pixels, the fishing window is active
        blue_pixel_count = cv2.countNonZero(mask)
        return blue_pixel_count > 100
    
    def detect_try_again_button(self, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        """
        Detects the "Try Again" button that appears when a fishing game ends.
        Uses green color detection and contour analysis to identify the button shape.
        
        Args:
            frame: The captured game frame to analyze
            
        Returns:
            Tuple of (x, y) center coordinates if button found, None otherwise
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # Create mask for green button
        mask = cv2.inRange(hsv, self.button_color_lower, self.button_color_upper)
        
        # Find contours in the mask
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Look for a contour that matches button characteristics
        for contour in contours:
            area = cv2.contourArea(contour)
            
            # Button should be reasonably large (area > 500 pixels)
            if area > 500:
                x, y, w, h = cv2.boundingRect(contour)
                
                # Button is roughly rectangular (wider than tall, aspect ratio 1.5-3.5)
                aspect_ratio = w / float(h) if h > 0 else 0
                if 1.5 < aspect_ratio < 3.5:
                    # Return center of button for clicking
                    return (x + w // 2, y + h // 2)
        
        return None
    
    def detect_try_again(self, frame: np.ndarray) -> bool:
        """
        Detects if the game has ended by looking for the green "Try Again" button.
        Checks the center region of the frame for a cluster of green pixels.
        
        Args:
            frame: The captured game frame to analyze
            
        Returns:
            True if "Try Again" button is detected (game ended), False otherwise
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # Create mask for green button
        green_mask = cv2.inRange(hsv, self.button_color_lower, self.button_color_upper)
        
        # Extract center region (middle third of frame horizontally and vertically)
        # This is where the button typically appears
        h, w = frame.shape[:2]
        center_region = green_mask[h//3:2*h//3, w//3:2*w//3]
        
        # Count green pixels in center region
        center_count = cv2.countNonZero(center_region)
        
        # Game has ended if we detect enough green pixels (button visible)
        return center_count > 50
    
    def find_fish(self, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        """
        Finds the fish position in the current frame using dark object detection.
        The fish appears as a dark circular object on the screen.
        Uses contour analysis to identify potential fish and selects the best match
        based on circularity (how round the shape is).
        
        Args:
            frame: The captured game frame to analyze
            
        Returns:
            Tuple of (x, y) fish center coordinates if found, None otherwise
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Apply binary threshold to isolate dark objects (fish)
        # THRESH_BINARY_INV inverts so dark areas become white
        _, thresh = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
        
        # Apply morphological operations to clean up noise and small artifacts
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        # Find all contours in the processed image
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        best_fish = None
        best_circularity = 0
        
        # Evaluate each contour to find the best fish match
        for contour in contours:
            area = cv2.contourArea(contour)
            
            # Fish should be between 30-1000 pixels in area
            if 30 < area < 1000:
                perimeter = cv2.arcLength(contour, True)
                if perimeter > 0:
                    # Circularity = 4π × Area / Perimeter²
                    # Value of 1.0 = perfect circle, lower = more irregular
                    circularity = 4 * np.pi * area / (perimeter * perimeter)
                    
                    # Update best match if this contour is more circular
                    if circularity > best_circularity and circularity > 0.3:
                        best_circularity = circularity
                        # Calculate center of mass for the contour
                        M = cv2.moments(contour)
                        if M["m00"] != 0:
                            cx = int(M["m10"] / M["m00"])
                            cy = int(M["m01"] / M["m00"])
                            best_fish = (cx, cy)
        
        return best_fish
    
    def find_circle_center(self, frame: np.ndarray) -> Optional[Tuple[int, int, int]]:
        """
        Finds the blue circle center and radius in the game window.
        The circle represents the valid clicking area during fishing.
        
        Args:
            frame: The captured game frame to analyze
            
        Returns:
            Tuple of (x, y, radius) for circle, or None if not found
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # Create mask for blue circle
        mask = cv2.inRange(hsv, self.circle_color_lower, self.circle_color_upper)
        
        # Find contours to identify circle edges
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return None
        
        # Get the largest contour (the circle)
        largest_contour = max(contours, key=cv2.contourArea)
        
        # Calculate the minimum enclosing circle
        (x, y), radius = cv2.minEnclosingCircle(largest_contour)
        
        # Ensure radius is valid (not too small)
        if radius <= 1:
            return None
        
        return (int(x), int(y), int(radius))
    
    def visualize_detections(self, frame: np.ndarray, circle_info: Optional[Tuple[int, int, int]], 
                           fish_pos: Optional[Tuple[int, int]]) -> np.ndarray:
        """Creates a visualization of detected elements"""
        # Create a copy for drawing
        vis = frame.copy()
        
        # Draw circle if found
        if circle_info:
            cx, cy, radius = circle_info
            cv2.circle(vis, (cx, cy), radius, (0, 255, 0), 2)  # Green circle
            cv2.circle(vis, (cx, cy), 3, (0, 255, 0), -1)  # Green center dot
        
        # Draw fish if found
        if fish_pos:
            fx, fy = fish_pos
            cv2.circle(vis, (fx, fy), 5, (0, 0, 255), -1)  # Red dot for fish
            cv2.drawMarker(vis, (fx, fy), (255, 0, 0), cv2.MARKER_CROSS, 15, 2)  # Blue cross
        
        # Draw info text
        h, w = frame.shape[:2]
        y_offset = 20
        
        if circle_info:
            cx, cy, radius = circle_info
            text = f"Circle: ({cx}, {cy}), r={radius}"
            cv2.putText(vis, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            y_offset += 25
        
        if fish_pos:
            fx, fy = fish_pos
            text = f"Fish: ({fx}, {fy})"
            cv2.putText(vis, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
            y_offset += 25
            
            if circle_info:
                cx, cy, radius = circle_info
                distance = np.sqrt((fx - cx)**2 + (fy - cy)**2)
                in_circle = distance < radius
                color = (0, 255, 0) if in_circle else (0, 0, 255)
                text = f"Distance: {distance:.1f}, In circle: {in_circle}"
                cv2.putText(vis, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        return vis
    
    def get_fish_detection_visualization(self, frame: np.ndarray) -> np.ndarray:
        """Creates a visualization showing the fish detection process"""
        # Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Apply threshold
        _, thresh = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
        
        # Apply morphological operations
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        morph = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        # Create a display image showing the processing steps
        h, w = frame.shape[:2]
        
        # Resize images for display (make them smaller to fit in one window)
        display_h = h // 2
        display_w = w // 2
        
        original = cv2.resize(frame, (display_w, display_h))
        gray_display = cv2.cvtColor(cv2.resize(gray, (display_w, display_h)), cv2.COLOR_GRAY2BGR)
        thresh_display = cv2.cvtColor(cv2.resize(thresh, (display_w, display_h)), cv2.COLOR_GRAY2BGR)
        morph_display = cv2.cvtColor(cv2.resize(morph, (display_w, display_h)), cv2.COLOR_GRAY2BGR)
        
        # Create a 2x2 grid showing all processing steps
        top_row = np.hstack([original, gray_display])
        bottom_row = np.hstack([thresh_display, morph_display])
        grid = np.vstack([top_row, bottom_row])
        
        # Add labels to each quadrant
        cv2.putText(grid, "Original Frame", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(grid, "Grayscale", (display_w + 10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(grid, "Threshold (Inverted)", (10, display_h + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(grid, "Morphological Open", (display_w + 10, display_h + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        # Find and draw contours on the original frame
        contours, _ = cv2.findContours(morph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Draw detected contours
        detection_display = original.copy()
        for i, contour in enumerate(contours):
            area = cv2.contourArea(contour)
            if 30 < area < 1000:  # Only show contours in the valid size range
                # Scale contour coordinates to match resized display
                scaled_contour = (contour * np.array([display_w / w, display_h / h])).astype(np.int32)
                cv2.drawContours(detection_display, [scaled_contour], 0, (0, 255, 0), 1)
        
        # Replace original with detection display
        top_row = np.hstack([detection_display, gray_display])
        grid = np.vstack([top_row, bottom_row])
        
        cv2.putText(grid, "Contour Detection", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(grid, "Grayscale", (display_w + 10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(grid, "Threshold (Inverted)", (10, display_h + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(grid, "Morphological Open", (display_w + 10, display_h + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        return grid

class FishingBot:
    """Main bot that plays the fishing minigame"""
    
    def __init__(self, region: GameRegion, config: dict, window_manager: WindowManager):
        self.region = region
        self.config = config
        self.window_manager = window_manager
        self.detector = FishDetector()
        self.sct = None  # Will be created in thread
        self.running = False
        self.paused = False
        self.hits = 0
        self.game_active = False
        self.total_games = 0
        self.successful_games = 0
        
        self.last_click_time = 0
        self.click_cooldown = config.get('click_cooldown', 0.1)
        self.last_try_again_click = 0
        self.try_again_cooldown = 2.0  # Wait 2 seconds before clicking again
        
        # Debug visualization
        self.show_debug_window = config.get('show_debug_window', False)
        self.debug_window_name = "Bot Debug - Real-time Detection"
        
        # Callbacks for GUI updates
        self.on_status_update = None
        self.on_stats_update = None
        self.on_pause_toggle = None
        
        # Setup keyboard listener for pause
        if keyboard:
            self.key_listener = keyboard.Listener(on_press=self.on_key_press)
            self.key_listener.start()
        
    def capture_screen(self) -> np.ndarray:
        """
        Captures the game region as a numpy array for processing.
        Uses MSS (Multi-Screen Screenshots) for fast screen capture.
        Converts screen coordinates from absolute to the selected region.
        
        Returns:
            Numpy array representing the captured frame, or black frame on error
        """
        try:
            # Create mss instance in the current thread (thread-safe)
            if self.sct is None:
                self.sct = mss()
            
            # Get window position
            win_left, win_top, _, _ = self.window_manager.get_window_rect()
            
            # Convert region coordinates to absolute screen coordinates
            screen_left = win_left + self.region.left
            screen_top = win_top + self.region.top
            
            # Define the monitor region to capture
            monitor = {
                "left": screen_left,
                "top": screen_top,
                "width": self.region.width,
                "height": self.region.height
            }
            
            # Capture the region
            sct_img = self.sct.grab(monitor)
            # Convert to numpy array in RGB format
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            return np.array(img)
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"Screenshot error: {e}")
            # Return black frame on error so bot can continue
            return np.zeros((self.region.height, self.region.width, 3), dtype=np.uint8)
    
    def click_at(self, x: int, y: int, description: str = ""):
        """
        Clicks at coordinates relative to the game region.
        Handles coordinate conversion from region-relative to absolute screen coordinates.
        Optionally adds small random offsets for human-like clicking behavior.
        
        Args:
            x: X coordinate relative to the game region
            y: Y coordinate relative to the game region
            description: Optional status message to log
        """
        # Convert relative region coordinates to absolute window coordinates
        abs_x = self.region.left + x
        abs_y = self.region.top + y
        
        # Convert absolute window coordinates to absolute screen coordinates
        screen_x, screen_y = self.window_manager.convert_to_absolute_coords(abs_x, abs_y)
        
        # Add small random offsets if human-like clicking is enabled
        if self.config.get('human_like_clicking', True):
            offset_x = np.random.randint(-2, 3)
            offset_y = np.random.randint(-2, 3)
        else:
            offset_x = offset_y = 0
        
        # Perform the click
        pyautogui.click(screen_x + offset_x, screen_y + offset_y)
        
        # Log the action if callback is set
        if description and self.on_status_update:
            self.on_status_update(description)
    
    def click_fish(self, x: int, y: int):
        """
        Clicks on the detected fish position.
        Enforces a cooldown period to prevent multiple clicks in quick succession.
        Tracks hit count for the current game.
        
        Args:
            x: X coordinate of the fish
            y: Y coordinate of the fish
        """
        current_time = time.time()
        
        # Enforce click cooldown to prevent spamming clicks
        if current_time - self.last_click_time < self.click_cooldown:
            return
        
        # Perform the click and record the hit
        self.click_at(x, y, f"Hit #{self.hits + 1} at ({x}, {y})")
        self.hits += 1
        self.last_click_time = current_time
        
        # Update GUI stats if callback is set
        if self.on_stats_update:
            self.on_stats_update(self.hits, self.total_games, self.successful_games)
    
    def click_try_again(self, x: int, y: int):
        """
        Clicks the "Try Again" button that appears at the end of a game.
        Resets game state (hits count, game_active flag) for the next game.
        Enforces a cooldown period to prevent double-clicking the button.
        
        Args:
            x: X coordinate of the "Try Again" button
            y: Y coordinate of the "Try Again" button
        """
        current_time = time.time()
        
        # Enforce cooldown to prevent double-clicking (minimum 2 seconds between clicks)
        if current_time - self.last_try_again_click < self.try_again_cooldown:
            return
        
        # Click the button
        self.click_at(x, y, "Clicking 'Try Again' button")
        self.last_try_again_click = current_time
        
        # Reset game state for next round
        self.game_active = False
        self.hits = 0
        
        # Wait for transition animation to complete
        time.sleep(0.5)
    
    def on_key_press(self, key):
        """
        Handles keyboard input for bot control.
        F1 key toggles pause/resume state of the bot.
        
        Args:
            key: The key pressed (from pynput keyboard listener)
        """
        try:
            if key == keyboard.Key.f1:
                # Toggle pause state
                self.paused = not self.paused
                status = "PAUSED" if self.paused else "RESUMED"
                
                # Update status in GUI
                if self.on_status_update:
                    self.on_status_update(f"Bot {status} (F1 pressed)")
                
                # Notify GUI of pause state change
                if self.on_pause_toggle:
                    self.on_pause_toggle(self.paused)
        except AttributeError:
            pass
    
    def is_fish_in_circle(self, fish_pos: Tuple[int, int], 
                          circle_info: Tuple[int, int, int]) -> bool:
        """
        Checks if the detected fish is within the game circle.
        The circle defines the valid clicking area - fish outside the circle shouldn't be clicked.
        
        Args:
            fish_pos: Tuple of (x, y) fish center coordinates
            circle_info: Tuple of (cx, cy, radius) for the circle
            
        Returns:
            True if fish is inside circle, False otherwise
        """
        fx, fy = fish_pos
        cx, cy, radius = circle_info
        
        # Calculate Euclidean distance from fish to circle center
        distance = np.sqrt((fx - cx)**2 + (fy - cy)**2)
        
        # Check if distance is less than radius
        return distance < radius
    
    def play_game(self):
        """
        Main game loop that continuously monitors and automates the fishing minigame.
        Flow:
        1. Check for "Try Again" screen (game end) - HIGHEST PRIORITY
        2. If Try Again detected and ready, click it and increment game count
        3. If fishing window active, detect and click fish within the circle
        4. Display debug visualization if enabled
        
        The loop respects the paused flag and can be paused/resumed via F1 key.
        """
        if self.on_status_update:
            self.on_status_update("Bot is monitoring for fishing window...")
        
        missed_frames = 0  # Track consecutive frames without fish detection
        last_failed_time = 0  # Track when we last saw/clicked Try Again screen
        last_wait_message_time = 0  # Track for throttling debug messages
        
        while self.running:
            # Skip processing if paused - just sleep to reduce CPU usage
            if self.paused:
                time.sleep(0.1)
                continue
            
            try:
                # Capture current frame from game window
                frame = self.capture_screen()
                circle_info = None
                fish_pos = None
                
                # PRIORITY 1: Check for "Try Again" button (game end screen)
                # This must be checked first before normal game detection
                button_pos = self.detector.detect_try_again_button(frame)
                if self.detector.detect_try_again(frame):
                    current_time = time.time()
                    
                    # Display in debug window if enabled
                    if self.show_debug_window:
                        debug_vis = frame.copy()
                        cv2.putText(debug_vis, "TRY AGAIN DETECTED", (10, 50), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                        cv2.imshow(self.debug_window_name, debug_vis)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            self.running = False
                    
                    # Wait at least 1.5 seconds before clicking to avoid double-clicks
                    if current_time - last_failed_time > 1.5:
                        if button_pos and self.config.get('auto_retry', True):
                            # Game completed - increment counters
                            self.total_games += 1
                            self.successful_games += 1
                            self.click_try_again(button_pos[0], button_pos[1])
                            if self.on_stats_update:
                                self.on_stats_update(0, self.total_games, self.successful_games)
                            last_failed_time = current_time
                        missed_frames = 0
                    else:
                        # Still waiting for cooldown - show wait timer in debug mode
                        elapsed = current_time - last_failed_time
                        if self.show_debug_window and current_time - last_wait_message_time > 1.0:
                            if self.on_status_update:
                                self.on_status_update(f"Try again screen - waiting ({1.5 - elapsed:.1f}s remaining)...")
                            last_wait_message_time = current_time
                    
                    # Skip normal game detection when Try Again screen is showing
                    time.sleep(0.01)
                    continue
                
                # PRIORITY 2: Check if fishing window is active
                is_active = self.detector.detect_fishing_window(frame)
                status_msg = f"Fishing window active: {is_active}"
                
                if is_active:
                    # Mark that a game is currently active
                    if not self.game_active:
                        if self.on_status_update:
                            self.on_status_update("Fishing minigame detected!")
                        self.game_active = True
                        self.hits = 0
                        missed_frames = 0
                    
                    # Detect the blue circle (defines valid clicking area)
                    circle_info = self.detector.find_circle_center(frame)
                    if circle_info:
                        cx, cy, radius = circle_info
                        status_msg += f" | Circle: center=({cx}, {cy}), radius={radius}"
                        if self.show_debug_window and self.on_status_update:
                            self.on_status_update(f"CIRCLE FOUND: position=({cx}, {cy}), radius={radius}")
                    else:
                        status_msg += " | Circle: NOT FOUND"
                        if self.show_debug_window and self.on_status_update:
                            self.on_status_update("DEBUG: Circle detection failed - checking HSV mask...")
                    
                    # Detect the fish
                    fish_pos = self.detector.find_fish(frame)
                    
                    if fish_pos:
                        missed_frames = 0  # Reset counter when fish found
                        fx, fy = fish_pos
                        status_msg += f" | Fish: ({fx}, {fy})"
                        if self.show_debug_window and self.on_status_update:
                            self.on_status_update(f"FISH FOUND: position=({fx}, {fy})")
                        
                        # Only click fish if it's within the circle
                        if circle_info:
                            if self.is_fish_in_circle(fish_pos, circle_info):
                                self.click_fish(fish_pos[0], fish_pos[1])
                            cx, cy, radius = circle_info
                            distance = np.sqrt((fx - cx)**2 + (fy - cy)**2)
                            in_circle = distance < radius
                            status_msg += f" | Distance: {distance:.1f}, In circle: {in_circle}"
                            if self.show_debug_window and self.on_status_update:
                                self.on_status_update(f"Distance from center: {distance:.1f}px, In circle: {in_circle}")
                        else:
                            # If no circle detected, still click the fish
                            self.click_fish(fish_pos[0], fish_pos[1])
                        
                        if self.show_debug_window and self.on_status_update:
                            self.on_status_update(status_msg)
                    else:
                        # Fish not detected
                        missed_frames += 1
                        status_msg += " | Fish: NOT FOUND"
                        if self.on_status_update:
                            self.on_status_update(f"Fish detection failed (frame {missed_frames}) - Circle found: {circle_info is not None}")
                        # Log if we haven't detected fish for many frames
                        if missed_frames == 30:
                            if self.on_status_update:
                                self.on_status_update(f"DEBUG: Fish not detected for 30 frames (circle found: {circle_info is not None})")
                else:
                    # Fishing window not active
                    if self.game_active:
                        if self.on_status_update:
                            self.on_status_update("Waiting for fishing window...")
                        self.game_active = False
                        self.hits = 0
                        missed_frames = 0
                
                # Display debug visualization if enabled
                if self.show_debug_window:
                    try:
                        # Create visualization showing detected circle and fish
                        debug_vis = self.detector.visualize_detections(frame, circle_info, fish_pos)
                        
                        # Add current hit counter to visualization
                        cv2.putText(debug_vis, f"Hits: {self.hits}/3", (10, debug_vis.shape[0] - 10),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                        
                        # Display main debug window
                        cv2.imshow(self.debug_window_name, debug_vis)
                        
                        # Show fish detection processing steps in separate window
                        try:
                            processing_vis = self.detector.get_fish_detection_visualization(frame)
                            cv2.imshow("Fish Detection - Processing Steps", processing_vis)
                        except Exception as e:
                            if self.on_status_update:
                                self.on_status_update(f"DEBUG: Processing window error - {e}")
                        
                        # Allow user to press 'q' to stop bot from debug window
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            self.running = False
                    except Exception as e:
                        if self.on_status_update:
                            self.on_status_update(f"DEBUG: Visualization error - {e}")
                
                # Small sleep to prevent CPU spinning
                time.sleep(0.01)
                
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"Error: {e}")
                time.sleep(0.1)
    
    def cleanup_debug_window(self):
        """Cleans up the debug visualization windows"""
        try:
            cv2.destroyWindow(self.debug_window_name)
            cv2.destroyWindow("Fish Detection - Processing Steps")
        except:
            pass
    
    def start(self):
        """Starts the bot"""
        self.running = True
        self.play_game()
    
    def stop(self):
        """Stops the bot"""
        self.running = False
        if keyboard and hasattr(self, 'key_listener'):
            self.key_listener.stop()
        self.cleanup_debug_window()
        if self.on_status_update:
            self.on_status_update("Bot stopped")

class BotGUI:
    """GUI for the fishing bot"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Metin2 Fishing Bot")
        self.root.geometry("650x1000")
        self.root.resizable(True, True)
        
        self.window_manager = WindowManager()
        self.region: Optional[GameRegion] = None
        self.bot: Optional[FishingBot] = None
        self.bot_thread: Optional[threading.Thread] = None
        self.region_selection_active = False
        self.selection_points = []
        self.mouse_listener = None
        self.selection_label = None
        
        self.config = {
            'click_cooldown': 0.1,
            'human_like_clicking': True,
            'auto_retry': True,
        }
        
        self.setup_ui()
        
    def setup_ui(self):
        """Creates the GUI elements"""
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        
        # Header
        header = tk.Frame(self.root, bg="#8b4513", height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        title = tk.Label(header, text="METIN2 FISHING BOT", 
                        font=("Arial", 18, "bold"), 
                        bg="#8b4513", fg="#f4e4c1")
        title.pack(pady=15)
        
        # Main container
        main = tk.Frame(self.root, bg="#2c3e50")
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Process & Window Selection Section
        process_frame = tk.LabelFrame(main, text="Process & Window Selection", 
                                     font=("Arial", 12, "bold"),
                                     bg="#34495e", fg="#ecf0f1",
                                     padx=10, pady=10)
        process_frame.pack(fill=tk.X, pady=5)
        
        tk.Label(process_frame, text="Select Window:", 
                bg="#34495e", fg="#ecf0f1",
                font=("Arial", 10)).pack(side=tk.LEFT, padx=5)
        
        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(process_frame, textvariable=self.window_var, 
                                         state="readonly", width=45)
        self.window_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(process_frame, text="Refresh", 
                   command=self.refresh_windows).pack(side=tk.LEFT, padx=5)
        
        # Region Selection Section
        region_frame = tk.LabelFrame(main, text="Region Selection", 
                                    font=("Arial", 12, "bold"),
                                    bg="#34495e", fg="#ecf0f1",
                                    padx=10, pady=10)
        region_frame.pack(fill=tk.X, pady=5)
        
        self.region_label = tk.Label(region_frame, 
                                     text="No region selected",
                                     font=("Arial", 10),
                                     bg="#34495e", fg="#ecf0f1")
        self.region_label.pack(pady=5)
        
        self.select_region_btn = tk.Button(region_frame, 
                                          text="Select Region",
                                          command=self.start_region_selection,
                                          font=("Arial", 11, "bold"),
                                          bg="#3498db", fg="white",
                                          activebackground="#2980b9",
                                          cursor="hand2",
                                          padx=20, pady=8)
        self.select_region_btn.pack(pady=5)
        
        # Bot Configuration Section
        config_frame = tk.LabelFrame(main, text="Bot Configuration", 
                                    font=("Arial", 12, "bold"),
                                    bg="#34495e", fg="#ecf0f1",
                                    padx=10, pady=10)
        config_frame.pack(fill=tk.X, pady=5)
        
        # Click cooldown
        cooldown_frame = tk.Frame(config_frame, bg="#34495e")
        cooldown_frame.pack(fill=tk.X, pady=5)
        
        tk.Label(cooldown_frame, text="Click Cooldown (ms):", 
                bg="#34495e", fg="#ecf0f1",
                font=("Arial", 10)).pack(side=tk.LEFT, padx=5)
        
        self.cooldown_var = tk.IntVar(value=100)
        cooldown_spin = tk.Spinbox(cooldown_frame, from_=50, to=500, 
                                   increment=10, textvariable=self.cooldown_var,
                                   width=10)
        cooldown_spin.pack(side=tk.LEFT, padx=5)
        
        # Human-like clicking
        self.human_like_var = tk.BooleanVar(value=True)
        human_check = tk.Checkbutton(config_frame, 
                                    text="Human-like clicking (random offset)",
                                    variable=self.human_like_var,
                                    bg="#34495e", fg="#ecf0f1",
                                    selectcolor="#2c3e50",
                                    activebackground="#34495e",
                                    font=("Arial", 10))
        human_check.pack(anchor=tk.W, pady=5)
        
        # Auto retry
        self.auto_retry_var = tk.BooleanVar(value=True)
        retry_check = tk.Checkbutton(config_frame, 
                                    text="Auto-click 'Try Again' button",
                                    variable=self.auto_retry_var,
                                    bg="#34495e", fg="#ecf0f1",
                                    selectcolor="#2c3e50",
                                    activebackground="#34495e",
                                    font=("Arial", 10))
        retry_check.pack(anchor=tk.W, pady=5)
        
        # Debug visualization
        self.debug_window_var = tk.BooleanVar(value=True)
        debug_check = tk.Checkbutton(config_frame, 
                                    text="Show debug visualization window",
                                    variable=self.debug_window_var,
                                    bg="#34495e", fg="#ecf0f1",
                                    selectcolor="#2c3e50",
                                    activebackground="#34495e",
                                    font=("Arial", 10))
        debug_check.pack(anchor=tk.W, pady=5)
        
        # Statistics Section
        stats_frame = tk.LabelFrame(main, text="Statistics", 
                                   font=("Arial", 12, "bold"),
                                   bg="#34495e", fg="#ecf0f1",
                                   padx=10, pady=10)
        stats_frame.pack(fill=tk.X, pady=5)
        
        stats_grid = tk.Frame(stats_frame, bg="#34495e")
        stats_grid.pack(fill=tk.X)
        
        # Current hits
        tk.Label(stats_grid, text="Current Hits:", 
                bg="#34495e", fg="#ecf0f1",
                font=("Arial", 10)).grid(row=0, column=0, sticky=tk.W, pady=2)
        self.hits_label = tk.Label(stats_grid, text="0 / 3", 
                                  bg="#34495e", fg="#ffeb3b",
                                  font=("Arial", 10, "bold"))
        self.hits_label.grid(row=0, column=1, sticky=tk.W, padx=20, pady=2)
        
        # Total games
        tk.Label(stats_grid, text="Total Games:", 
                bg="#34495e", fg="#ecf0f1",
                font=("Arial", 10)).grid(row=1, column=0, sticky=tk.W, pady=2)
        self.games_label = tk.Label(stats_grid, text="0", 
                                   bg="#34495e", fg="#3498db",
                                   font=("Arial", 10, "bold"))
        self.games_label.grid(row=1, column=1, sticky=tk.W, padx=20, pady=2)
        
        # Status Log Section
        status_frame = tk.LabelFrame(main, text="Status Log", 
                                    font=("Arial", 12, "bold"),
                                    bg="#34495e", fg="#ecf0f1",
                                    padx=10, pady=10)
        status_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Status text with scrollbar
        status_scroll = tk.Scrollbar(status_frame)
        status_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.status_text = tk.Text(status_frame, height=5, 
                                   bg="#2c3e50", fg="#ecf0f1",
                                   font=("Courier", 9),
                                   yscrollcommand=status_scroll.set,
                                   state=tk.DISABLED)
        self.status_text.pack(fill=tk.BOTH, expand=True)
        status_scroll.config(command=self.status_text.yview)
        
        # Control Buttons
        button_frame = tk.Frame(main, bg="#2c3e50")
        button_frame.pack(fill=tk.X, pady=10)
        
        self.start_btn = tk.Button(button_frame, 
                                   text="▶ Start Bot",
                                   command=self.start_bot,
                                   font=("Arial", 12, "bold"),
                                   bg="#2ecc71", fg="white",
                                   activebackground="#27ae60",
                                   cursor="hand2",
                                   state=tk.DISABLED,
                                   padx=30, pady=12)
        self.start_btn.pack(side=tk.LEFT, expand=True, padx=5)
        
        self.pause_btn = tk.Button(button_frame, 
                                 text="⏸ Pause Bot",
                                 command=self.pause_bot,
                                 font=("Arial", 12, "bold"),
                                 bg="#f39c12", fg="white",
                                 activebackground="#e67e22",
                                 cursor="hand2",
                                 state=tk.DISABLED,
                                 padx=30, pady=12)
        self.pause_btn.pack(side=tk.LEFT, expand=True, padx=5)
        
        self.add_status("Welcome! Select a window and region to begin.")
        
        # Refresh windows list after UI is fully initialized
        self.refresh_windows()
        
    def refresh_windows(self):
        """Refreshes the list of available windows"""
        try:
            windows = self.window_manager.get_all_windows()
            window_names = [name for name, _ in windows]
            self.window_combo['values'] = window_names
            if window_names:
                self.add_status(f"Found {len(window_names)} visible window(s)")
            else:
                self.add_status("No visible windows found")
        except Exception as e:
            self.add_status(f"Error getting windows: {e}")
        
    def add_status(self, message: str):
        """
        Adds a status message to the GUI log with timestamp.
        
        Args:
            message: The status message to display
        """
        self.status_text.config(state=tk.NORMAL)
        timestamp = time.strftime("%H:%M:%S")
        self.status_text.insert(tk.END, f"[{timestamp}] {message}\n")
        # Auto-scroll to show latest message
        self.status_text.see(tk.END)
        self.status_text.config(state=tk.DISABLED)
    
    def start_region_selection(self):
        """
        Initiates region selection mode by listening for mouse clicks.
        User must click two corners (top-left, then bottom-right) to define the fishing window.
        The listener is cleaned up before starting a new one to avoid conflicts.
        """
        # Validate that a window is selected
        if not self.window_var.get():
            messagebox.showerror("Error", "Please select a window first!")
            return
        
        # Clean up any previous listener before starting new one
        self.region_selection_active = False
        time.sleep(0.3)  # Wait for old listener to stop processing
        
        if self.mouse_listener is not None:
            try:
                self.mouse_listener.stop()
            except:
                pass
            self.mouse_listener = None
            time.sleep(0.1)
        
        # Get the selected window
        try:
            windows = self.window_manager.get_all_windows()
            selected_name = self.window_var.get()
            selected_window = None
            
            for name, win in windows:
                if name == selected_name:
                    selected_window = win
                    break
            
            if not selected_window:
                messagebox.showerror("Error", "Selected window not found!")
                return
            
            # Activate and focus the selected window
            self.window_manager.select_window(selected_window)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to select window: {e}")
            return
        
        try:
            # Clear previous selection points
            self.selection_points = []
            self.select_region_btn.config(state=tk.DISABLED)
            self.add_status("Click top-left corner of fishing window...")
            
            # Show visual indicator that region selection is active
            label = tk.Label(self.root, 
                            text="[Region Selection Active] Click on target window - Press ESC to cancel",
                            font=("Arial", 10, "bold"),
                            bg='yellow', fg='red')
            label.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
            self.selection_label = label
            self.root.update()
            
            # Activate region selection and start listening for clicks
            self.region_selection_active = True
            self.mouse_listener = mouse.Listener(on_click=self.on_mouse_click)
            self.mouse_listener.start()
        except Exception as e:
            self.region_selection_active = False
            messagebox.showerror("Error", f"Failed to start region selection: {e}")
            self.select_region_btn.config(state=tk.NORMAL)
            self.add_status(f"Region selection failed: {e}")
        
    def on_mouse_click(self, x, y, button, pressed):
        """
        Handles mouse clicks during region selection.
        Records two corner clicks to define the rectangular fishing window region.
        Only responds to left mouse button clicks.
        
        Args:
            x: Absolute screen X coordinate
            y: Absolute screen Y coordinate
            button: Which mouse button was clicked
            pressed: Whether button was pressed or released
        """
        # Ignore if region selection is not active or if button is being released
        if not self.region_selection_active or not pressed:
            return
        
        # Only handle left mouse button
        if button != mouse.Button.left:
            return
        
        # Convert absolute screen coordinates to window-relative coordinates
        win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
        rel_x = x - win_left
        rel_y = y - win_top
        
        # Validate that click is within window bounds
        if rel_x < 0 or rel_y < 0 or rel_x > win_width or rel_y > win_height:
            self.add_status(f"Click outside window bounds ({rel_x}, {rel_y}) - try again")
            return
        
        # Record this click
        self.selection_points.append((rel_x, rel_y))
        
        # Guide user for first click
        if len(self.selection_points) == 1:
            self.add_status(f"Top-left set: ({rel_x}, {rel_y}) [relative to window]")
            self.add_status("Now click bottom-right corner...")
        # Complete selection on second click
        elif len(self.selection_points) == 2:
            self.add_status(f"Bottom-right set: ({rel_x}, {rel_y}) [relative to window]")
            self.finish_region_selection()
    
    def finish_region_selection(self):
        """
        Completes region selection after two clicks.
        Creates GameRegion object from the two corner points.
        Cleans up the mouse listener and visual indicator.
        Re-enables the Start button.
        """
        # Deactivate region selection and stop listening
        self.region_selection_active = False
        time.sleep(0.1)
        
        # Stop and clean up mouse listener
        if self.mouse_listener is not None:
            try:
                self.mouse_listener.stop()
            except:
                pass
            self.mouse_listener = None
        
        # Remove visual indicator
        if self.selection_label is not None:
            try:
                self.selection_label.destroy()
            except:
                pass
            self.selection_label = None
        
        # Calculate region from the two points
        p1, p2 = self.selection_points
        self.region = GameRegion(
            left=min(p1[0], p2[0]),
            top=min(p1[1], p2[1]),
            width=abs(p2[0] - p1[0]),
            height=abs(p2[1] - p1[1])
        )
        
        # Update GUI with selected region info
        self.region_label.config(
            text=f"Region: {self.region.width}x{self.region.height} at ({self.region.left}, {self.region.top})"
        )
        
        self.add_status(f"Region configured: {self.region.width}x{self.region.height}")
        self.select_region_btn.config(state=tk.NORMAL)
        self.start_btn.config(state=tk.NORMAL)
    
    def cancel_region_selection(self):
        """Cancels region selection"""
        self.region_selection_active = False
        time.sleep(0.1)
        
        # Stop mouse listener
        if self.mouse_listener is not None:
            try:
                self.mouse_listener.stop()
            except:
                pass
            self.mouse_listener = None
        
        if self.selection_label is not None:
            try:
                self.selection_label.destroy()
            except:
                pass
            self.selection_label = None
        
        self.select_region_btn.config(state=tk.NORMAL)
        self.add_status("Region selection cancelled")
    
    def start_bot(self):
        """
        Starts the bot for a new fishing session or resumes a paused bot.
        - If bot is paused: Resumes from pause state
        - If bot is stopped: Creates new FishingBot instance and starts it in a thread
        Validates that window and region are selected before starting.
        """
        # If bot is paused, resume it without creating a new instance
        if self.bot and self.bot.paused:
            self.bot.paused = False
            self.add_status("Bot RESUMED")
            self.start_btn.config(state=tk.DISABLED)
            self.pause_btn.config(state=tk.NORMAL)
            # Disable region selection when running
            self.select_region_btn.config(state=tk.DISABLED)
            return
        
        # Validate that window is selected
        if not self.window_var.get():
            messagebox.showerror("Error", "Please select a window first!")
            return
        
        # Validate that region is selected
        if not self.region:
            messagebox.showerror("Error", "Please select a region first!")
            return
        
        # Find and select the window
        windows = self.window_manager.get_all_windows()
        selected_name = self.window_var.get()
        selected_window = None
        
        for name, win in windows:
            if name == selected_name:
                selected_window = win
                break
        
        if not selected_window:
            messagebox.showerror("Error", "Selected window not found!")
            return
        
        # Activate the window
        self.window_manager.select_window(selected_window)
        
        # Update config from current GUI settings
        self.config['click_cooldown'] = self.cooldown_var.get() / 1000.0
        self.config['human_like_clicking'] = self.human_like_var.get()
        self.config['auto_retry'] = self.auto_retry_var.get()
        self.config['show_debug_window'] = self.debug_window_var.get()
        
        # Create new FishingBot instance with current config
        self.bot = FishingBot(self.region, self.config, self.window_manager)
        
        # Connect bot callbacks to GUI update methods
        self.bot.on_status_update = self.add_status
        self.bot.on_stats_update = self.update_stats
        
        # Start bot in a separate daemon thread so it doesn't block the GUI
        self.bot_thread = threading.Thread(target=self.bot.start, daemon=True)
        self.bot_thread.start()
        
        # Update UI button states
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL)
        self.select_region_btn.config(state=tk.DISABLED)
        self.window_combo.config(state="disabled")
        self.add_status("Bot started!")
    
    def pause_bot(self):
        """
        Pauses the currently running bot.
        Sets the paused flag which causes the main game loop to sleep and skip processing.
        Updates UI buttons to allow resuming via the Start button.
        Note: Region selection is disabled during pause due to pynput listener restart issues.
        """
        if self.bot and not self.bot.paused:
            # Set pause flag in the bot
            self.bot.paused = True
            self.add_status("Bot PAUSED")
            
            # Enable start button for resume
            self.start_btn.config(state=tk.NORMAL)
            
            # Disable pause button until resumed
            self.pause_btn.config(state=tk.DISABLED)
    
    def stop_bot(self):
        """
        Stops the running bot completely.
        Closes the bot instance and resets UI state to allow a fresh start.
        Re-enables all controls for the user.
        """
        if self.bot:
            # Stop the bot and clean up resources
            self.bot.stop()
            self.bot = None
        
        # Update UI - re-enable all controls
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED, text="⏸ Pause Bot")
        self.select_region_btn.config(state=tk.NORMAL)
        self.window_combo.config(state="readonly")
        self.add_status("Bot stopped")
    
    def run(self):
        """
        Starts the GUI application.
        Configures pyautogui safety settings and enters the Tkinter event loop.
        """
        # Enable failsafe (move mouse to corner to emergency stop)
        pyautogui.FAILSAFE = True
        # Small delay between clicks to prevent spam
        pyautogui.PAUSE = 0.01
        
        # Handle window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Start the GUI event loop
        self.root.mainloop()
    
    def on_close(self):
        """
        Handles the window close event.
        Ensures the bot is properly stopped before closing the application.
        """
        if self.bot:
            self.stop_bot()
        self.root.destroy()

if __name__ == "__main__":
    gui = BotGUI()
    gui.run()
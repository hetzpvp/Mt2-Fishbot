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
import json
import os
import winsound
try:
    from pynput import keyboard, mouse
    from pynput.keyboard import Controller, Key
    mouse_controller = mouse.Controller()
except ImportError:
    print("ERROR: pynput not installed!")
    print("Install with: pip install pynput")
    keyboard = None
    mouse = None
    Controller = None
    Key = None
    mouse_controller = None

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
        """Gets all visible windows grouped by process name"""
        windows = []
        priority_windows = []  # Windows with 'mt2', 'metin2', 'metin 2', or words with '2'
        
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    process_name = proc.info['name']
                    wins = gw.getWindowsWithTitle(process_name)
                    for win in wins:
                        try:
                            # Skip empty titles
                            if not win.title or not win.title.strip():
                                continue
                            
                            # Check if window is visible - handle both property and method
                            is_visible = getattr(win, 'isVisible', True)
                            if callable(is_visible):
                                is_visible = is_visible()
                            if is_visible:
                                display_name = f"{process_name} - {win.title}"
                                
                                # Check if window matches Metin2 patterns
                                title_lower = win.title.lower()
                                if any(pattern in title_lower for pattern in ['mt2', 'metin2', 'metin 2']) or \
                                   any(word.endswith('2') for word in title_lower.split()):
                                    priority_windows.append((display_name, win))
                                else:
                                    windows.append((display_name, win))
                        except Exception:
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            print(f"Error getting windows: {e}")
        
        # Combine all windows
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
    
    def select_window(self, window: gw.Win32Window):
        """Selects and activates a window"""
        self.selected_window = window
        self.activate_window()
    
    def activate_window(self):
        """Activates and brings the selected window to focus"""
        if not self.selected_window:
            return
        try:
            # Try to activate the window multiple times for reliability
            for attempt in range(3):
                try:
                    # Restore window if minimized
                    if self.selected_window.isMinimized:
                        self.selected_window.restore()
                        time.sleep(0.2)
                    
                    # Activate the window
                    self.selected_window.activate()
                    time.sleep(0.3)
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(0.2)
                    else:
                        raise
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
        # Color ranges for detecting the fish
        # Calibrated ranges for accurate fish detection
        self.fish_color_lower = np.array([97, 130, 108])
        self.fish_color_upper = np.array([110, 146, 133])
        
        # Color range for the fishing window background
        # Calibrated ranges for minigame window detection
        self.window_color_lower = np.array([98, 170, 189])
        self.window_color_upper = np.array([106, 255, 250])
         
    def detect_fishing_window(self, frame: np.ndarray) -> bool:
        """Detects if the fishing window is currently active."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.window_color_lower, self.window_color_upper)
        pixel_count = cv2.countNonZero(mask)
        return pixel_count > 10000
    
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
    
    def find_fish(self, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        """Finds the fish position in the current frame using color detection."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.fish_color_lower, self.fish_color_upper)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return None
        
        largest_contour = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest_contour)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            return (cx, cy)
        
        return None
     

class FishingBot:
    """Main bot that plays the fishing minigame"""
    
    def __init__(self, region: GameRegion, config: dict, window_manager: WindowManager, bait_counter: int = 800, bait_keys: list = None):
        self.region = region
        self.config = config
        self.window_manager = window_manager
        self.detector = FishDetector()
        self.sct = None  # Will be created in thread
        self.running = False
        self.paused = False
        self.hits = 0
        self.total_games = 0
        self.bait_counter = bait_counter  # Current bait count
        self.bait_keys = bait_keys if bait_keys else ['1', '2', '3', '4']  # Selected bait keys
        self.region_auto_calibrated = False  # Track if region has been auto-calibrated
        self.consecutive_failures = 0  # Track consecutive minigame detection failures
        
        # Callbacks for GUI updates
        self.on_status_update = None
        self.on_stats_update = None
        self.on_pause_toggle = None
        self.on_bait_update = None  # Callback for bait counter changes
        self.on_bot_stop = None  # Callback when bot stops
        
        # Setup keyboard listener for pause
        self.keyboard_controller = None
        if keyboard:
            self.key_listener = keyboard.Listener(on_press=self.on_key_press)
            self.key_listener.start()
            if Controller:
                self.keyboard_controller = Controller()
        
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
    
    def click_at(self, x: int, y: int, description: str = ""):
        """Clicks at coordinates relative to the game region without moving mouse cursor."""
        screen_x, screen_y = self.window_manager.convert_to_absolute_coords(
            self.region.left + x, self.region.top + y
        )
        
        try:
            pyautogui.click(screen_x, screen_y)
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"Click error: {e}")
        
        if description and self.on_status_update:
            self.on_status_update(description)
    
    def click_fish(self, x: int, y: int):
        """Clicks on the detected fish if it's within the valid clicking circle."""
        circle_center = (self.region.width // 2, self.region.height // 2)
        circle_radius = 67
        
        if not self.is_fish_in_circle((x, y), (*circle_center, circle_radius)):
            return
        
        self.click_at(x, y, f"Hit #{self.hits + 1}")
        self.hits += 1
        
        if self.on_stats_update:
            self.on_stats_update(self.hits, self.total_games)
    
    def on_key_press(self, key):
        """
        Handles keyboard input for bot control.
        F5 key toggles pause/resume state of the bot.
        
        Args:
            key: The key pressed (from pynput keyboard listener)
        """
        try:
            if key == keyboard.Key.f5:
                # Toggle pause state
                self.paused = not self.paused
                status = "PAUSED" if self.paused else "RESUMED"
                
                # Update status in GUI
                if self.on_status_update:
                    self.on_status_update(f"Bot {status} (F5 pressed)")
                
                # Notify GUI of pause state change
                if self.on_pause_toggle:
                    self.on_pause_toggle(self.paused)
        except AttributeError:
            pass
    
    def is_fish_in_circle(self, fish_pos: Tuple[int, int], 
                          circle_info: Tuple[int, int, int]) -> bool:
        """Checks if the detected fish is within the game circle."""
        fx, fy = fish_pos
        cx, cy, radius = circle_info
        distance = np.sqrt((fx - cx)**2 + (fy - cy)**2)
        return distance < radius
    
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
            self.on_status_update(f"2 consecutive failures! Bait adjusted to {self.bait_counter}")
        if self.on_bait_update:
            self.on_bait_update(self.bait_counter)
        if self.on_stats_update:
            self.on_stats_update(self.hits, self.total_games)
    
    def press_ctrl_key(self, key: str):
        """Presses CTRL+key combination once."""
        if not self.keyboard_controller:
            return
        try:
            self.keyboard_controller.press(Key.ctrl)
            time.sleep(0.05)
            self.keyboard_controller.press(key)
            time.sleep(0.05)
            self.keyboard_controller.release(key)
            time.sleep(0.05)
            self.keyboard_controller.release(Key.ctrl)
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"Error pressing CTRL+{key}: {e}")
    
    def quickskip(self):
        """Performs quick skip by double pressing CTRL+G."""
        if self.on_status_update:
            self.on_status_update("Quick skip...")
        self.press_ctrl_key('g')
        time.sleep(0.05)
        self.press_ctrl_key('g')
        time.sleep(0.05)
    
    def press_key(self, key: str, description: str = ""):
        """Presses a keyboard key using pynput."""
        if not self.keyboard_controller:
            return
        try:
            if key == 'space':
                self.keyboard_controller.press(Key.space)
                time.sleep(0.05)
                self.keyboard_controller.release(Key.space)
            elif key in ('1', '2', '3', '4'):
                self.keyboard_controller.press(key)
                time.sleep(0.05)
                self.keyboard_controller.release(key)
            elif key.upper() == 'F1':
                self.keyboard_controller.press(Key.f1)
                time.sleep(0.05)
                self.keyboard_controller.release(Key.f1)
            elif key.upper() == 'F2':
                self.keyboard_controller.press(Key.f2)
                time.sleep(0.05)
                self.keyboard_controller.release(Key.f2)
            elif key.upper() == 'F3':
                self.keyboard_controller.press(Key.f3)
                time.sleep(0.05)
                self.keyboard_controller.release(Key.f3)
            elif key.upper() == 'F4':
                self.keyboard_controller.press(Key.f4)
                time.sleep(0.05)
                self.keyboard_controller.release(Key.f4)
            
            if description and self.on_status_update:
                self.on_status_update(description)
        except Exception as e:
            if self.on_status_update:
                self.on_status_update(f"Error pressing key '{key}': {e}")
    
    def wait_for_minigame_window(self, timeout: float = 2.0) -> Optional[GameRegion]:
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
                        self.region = GameRegion(left=x, top=y, width=w, height=h)
                        self.region_auto_calibrated = True
                        if self.on_status_update:
                            self.on_status_update(f"Region auto-calibrated at ({x}, {y}) size: {w}x{h}")
                        return True
                else:
                    # Use standard detection after calibration
                    frame = self.capture_screen()
                    if self.detector.detect_fishing_window(frame):
                        return True
                
                time.sleep(0.1)
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"Error: {e}")
                time.sleep(0.1)
        
        return None
    
    def play_game(self):
        """Main game loop implementing the fishing minigame workflow."""
        # Reset bait if starting with 0 or negative bait
        max_bait = len(self.bait_keys) * 200
        if self.bait_counter <= 0:
            self.bait_counter = max_bait
            if self.on_bait_update:
                self.on_bait_update(self.bait_counter)
            if self.on_status_update:
                self.on_status_update(f"Bait counter was 0! Reset to {max_bait}.")
        
        if self.on_status_update:
            self.on_status_update(f"Bot started! Bait: {self.bait_counter}")
        
        while self.running and self.bait_counter > 0:
            if self.paused:
                time.sleep(0.1)
                continue
            
            try:
                bait_key = self.get_bait_key(self.bait_counter)
                self.press_key(bait_key, f"Pressed key {bait_key}")
                time.sleep(0.1)
                
                self.press_key('space', "Cast fishing line")
                time.sleep(0.1)
                
                minigame_detected = self.wait_for_minigame_window(timeout=0.5)
                if not minigame_detected:
                    self.consecutive_failures += 1
                    if self.on_status_update:
                        self.on_status_update(f"Minigame not detected ({self.consecutive_failures}/2)")
                    
                    if self.consecutive_failures >= 2:
                        self.adjust_bait_tier()
                        if self.bait_counter <= 0:
                            if self.on_status_update:
                                self.on_status_update("Bait depleted after consecutive failures. Stopping bot.")
                            self.running = False
                            if self.on_bot_stop:
                                self.on_bot_stop()
                            break
                    
                    self.quickskip()
                    time.sleep(0.1)
                    continue
                
                # Reset failure counter on successful minigame detection
                self.consecutive_failures = 0
                
                minigame_active = True
                capture = self.capture_screen
                detect_window = self.detector.detect_fishing_window
                find_fish = self.detector.find_fish
                human_like = self.config.get('human_like_clicking', True)
                
                while self.running and minigame_active:
                    if self.paused:
                        time.sleep(0.1)
                        continue
                    
                    if human_like:
                        time.sleep(np.random.uniform(0.15, 0.3))
                    try:
                        frame = capture()
                        
                        if not detect_window(frame):
                            minigame_active = False
                            self.total_games += 1
                            self.bait_counter -= 1
                            
                            if self.on_status_update:
                                self.on_status_update(f"Game finished. Total: {self.total_games}, Bait: {self.bait_counter}")
                            if self.on_bait_update:
                                self.on_bait_update(self.bait_counter)
                            if self.on_stats_update:
                                self.on_stats_update(0, self.total_games)
                            break
                        
                        fish_pos = find_fish(frame)
                        if fish_pos:
                            self.click_fish(*fish_pos)
                            
                    except Exception as e:
                        if self.on_status_update:
                            self.on_status_update(f"Error: {e}")
                
                self.hits = 0
                if self.bait_counter > 0:
                    if self.config.get('quick_skip', False):
                        self.quickskip()
                    else:
                        wait_time = np.random.uniform(3.5, 4)
                        time.sleep(wait_time)
                
            except Exception as e:
                if self.on_status_update:
                    self.on_status_update(f"Error in play_game: {e}")
                time.sleep(0.5)
        
        if self.on_status_update:
            self.on_status_update(f"Bot finished! Total games: {self.total_games}")
        if self.bait_counter <= 0 and self.config.get('sound_alert_on_finish', True):
            play_rickroll_beep()
        self.running = False
        if self.on_bot_stop:
            self.on_bot_stop()
    
    def start(self):
        """Starts the bot"""
        self.running = True
        self.play_game()
    
    def stop(self):
        """Stops the bot"""
        self.running = False
        if keyboard and hasattr(self, 'key_listener'):
            self.key_listener.stop()
        if self.on_status_update:
            self.on_status_update("Bot stopped")

class BotGUI:
    """GUI for the fishing bot"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MT2 Fishing Bot by boristei")
        self.root.geometry("500x600")
        self.root.resizable(False, False)
        self.root.configure(bg="#000000")
        
        # Try to load and set window icon
        icon_path = os.path.join(os.path.dirname(__file__), "monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception as e:
                print(f"Error loading icon: {e}")
        
        self.window_manager = WindowManager()
        self.bot: Optional[FishingBot] = None
        self.bot_thread: Optional[threading.Thread] = None
        
        # Config file path in the current working directory
        self.config_file = os.path.join(os.getcwd(), "bot_config.json")
        
        self.config = {
            'human_like_clicking': True,
            'quick_skip': True,
            'sound_alert_on_finish': True,
        }
        
        # Bait counter
        self.bait = 800
        self.last_total_games = 0  # Track previous game count to detect new games
        
        # Load config from file if it exists
        self.load_config()
        
        self.setup_ui()
        
    def setup_ui(self):
        """Creates the GUI elements"""
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        
        # Try to load and display GIF
        gif_path = os.path.join(os.path.dirname(__file__), "monkey-eating.gif")
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
        title = tk.Label(title_container, text="MT2 Fishing bot", 
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
        
        # Process & Window Selection Section
        process_frame = tk.LabelFrame(main, text="Process & Window Selection", 
                                     font=("Courier New", 10, "bold"),
                                     bg="#2a2a2a", fg="#FFD700",
                                     padx=8, pady=5)
        process_frame.pack(fill=tk.X, pady=3)
        
        tk.Label(process_frame, text="Select Window:", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 9)).pack(side=tk.LEFT, padx=3)
        
        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(process_frame, textvariable=self.window_var, 
                                         state="readonly", width=40)
        self.window_combo.pack(side=tk.LEFT, padx=3)
        
        tk.Button(process_frame, text="Refresh",
                 command=self.refresh_windows,
                 bg="#2a2a2a", fg="#FFD700",
                 activebackground="#3a3a3a", activeforeground="#FFD700",
                 font=("Courier New", 9),
                 cursor="hand2",
                 relief=tk.FLAT,
                 padx=8, pady=1).pack(side=tk.LEFT, padx=3)
        
        # Bot Configuration Section
        config_frame = tk.LabelFrame(main, text="Bot Configuration", 
                                    font=("Courier New", 10, "bold"),
                                    bg="#2a2a2a", fg="#FFD700",
                                    padx=8, pady=5)
        config_frame.pack(fill=tk.X, pady=3)
        
        # Human-like clicking
        self.human_like_var = tk.BooleanVar(value=self.config.get('human_like_clicking', True))
        human_check = tk.Checkbutton(config_frame, 
                                    text="Human-like clicking (random offset)",
                                    variable=self.human_like_var,
                                    bg="#2a2a2a", fg="#ffffff",
                                    selectcolor="#1a1a1a",
                                    activebackground="#2a2a2a",
                                    font=("Courier New", 9))
        human_check.pack(anchor=tk.W, pady=2)
        
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
        self.update_bait_capacity()
        
        # Show status log checkbox
        self.show_log_var = tk.BooleanVar(value=False)
        show_log_check = tk.Checkbutton(config_frame, 
                                       text="Show status log",
                                       variable=self.show_log_var,
                                       command=self.toggle_log_visibility,
                                       bg="#2a2a2a", fg="#ffffff",
                                       selectcolor="#1a1a1a",
                                       activebackground="#2a2a2a",
                                       font=("Courier New", 9))
        show_log_check.pack(anchor=tk.W, pady=2)
        
        # Statistics Section
        stats_frame = tk.LabelFrame(main, text="Statistics", 
                                   font=("Courier New", 10, "bold"),
                                   bg="#2a2a2a", fg="#FFD700",
                                   padx=8, pady=5)
        stats_frame.pack(fill=tk.X, pady=3)
        
        stats_grid = tk.Frame(stats_frame, bg="#2a2a2a")
        stats_grid.pack(fill=tk.X)
        
        # Click attempts
        tk.Label(stats_grid, text="Click Attempts:", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 9)).grid(row=0, column=0, sticky=tk.W, pady=1)
        self.hits_label = tk.Label(stats_grid, text="0", 
                                  bg="#2a2a2a", fg="#FFD700",
                                  font=("Courier New", 9, "bold"))
        self.hits_label.grid(row=0, column=1, sticky=tk.W, padx=15, pady=1)
        
        # Bait counter with reset button
        tk.Label(stats_grid, text="Bait:", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 9)).grid(row=1, column=0, sticky=tk.W, pady=1)
        self.bait_label = tk.Label(stats_grid, text=str(self.bait), 
                                  bg="#2a2a2a", fg="#FFD700",
                                  font=("Courier New", 9, "bold"))
        self.bait_label.grid(row=1, column=1, sticky=tk.W, padx=15, pady=1)
        
        self.reset_btn = tk.Button(stats_grid,
                                  text="Reset",
                                  command=self.reset_bait,
                                  font=("Courier New", 8),
                                  bg="#e74c3c", fg="white",
                                  activebackground="#c0392b",
                                  cursor="hand2",
                                  padx=3, pady=0,
                                  state=tk.NORMAL)
        self.reset_btn.grid(row=1, column=2, sticky=tk.W, padx=8, pady=1)
        
        # Status Log Section
        self.status_frame = tk.LabelFrame(main, text="Status Log", 
                                    font=("Courier New", 10, "bold"),
                                    bg="#2a2a2a", fg="#FFD700",
                                    padx=8, pady=5)
        self.status_frame.pack(fill=tk.BOTH, expand=True, pady=3)
        
        # Status text with scrollbar
        status_scroll = tk.Scrollbar(self.status_frame)
        status_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.status_text = tk.Text(self.status_frame, height=4, 
                                   bg="#1a1a1a", fg="#00ff00",
                                   font=("Courier", 8),
                                   yscrollcommand=status_scroll.set,
                                   state=tk.DISABLED)
        self.status_text.pack(fill=tk.BOTH, expand=True)
        status_scroll.config(command=self.status_text.yview)
        
        # Control Button (combined Start/Pause)
        button_frame = tk.Frame(main, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, pady=5)
        
        self.control_btn = tk.Button(button_frame, 
                                     text="▶ Start",
                                     command=self.toggle_bot,
                                     font=("Courier New", 12, "bold"),
                                     bg="#2ecc71", fg="white",
                                     activebackground="#27ae60",
                                     cursor="hand2",
                                     state=tk.NORMAL,
                                     padx=40, pady=10)
        self.control_btn.pack(side=tk.LEFT, expand=True, padx=5)
        
        self.add_status("Welcome! Select a window and click Start to begin.")
        
        # Refresh windows list after UI is fully initialized
        self.refresh_windows()
        
        # Restore previously selected window if it still exists
        if hasattr(self, 'previous_window') and self.previous_window:
            try:
                current_windows = self.window_combo['values']
                if self.previous_window in current_windows:
                    self.window_var.set(self.previous_window)
                    self.add_status(f"Restored previous window: {self.previous_window}")
            except Exception as e:
                print(f"Error restoring window selection: {e}")
        
        # Apply initial log visibility state
        self.toggle_log_visibility()
        
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
        Restores human_like_clicking, quick_skip, bait counter, bait keys, and window selection.
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
                    # Restore bait keys
                    if 'bait_keys' in saved_config:
                        self.config['bait_keys'] = saved_config['bait_keys']
                    # Restore bait counter
                    if 'bait' in saved_config:
                        self.bait = saved_config['bait']
                    # Store previously selected window for later restoration
                    self.previous_window = saved_config.get('selected_window', None)
            except Exception as e:
                print(f"Error loading config: {e}")
                self.previous_window = None
        else:
            self.previous_window = None
    
    def save_config(self):
        """
        Saves current configuration to the config file.
        Saves human_like_clicking, quick_skip, bait counter, and selected window.
        """
        try:
            # Get selected bait keys
            selected_bait_keys = self.get_selected_bait_keys() if hasattr(self, 'bait_key_vars') else ['1', '2', '3', '4']
            
            config_data = {
                'human_like_clicking': self.config.get('human_like_clicking', True),
                'quick_skip': self.config.get('quick_skip', False),
                'sound_alert_on_finish': self.config.get('sound_alert_on_finish', True),
                'bait_keys': selected_bait_keys,
                'bait': self.bait,
                'selected_window': self.window_var.get() if hasattr(self, 'window_var') else None
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
            if hasattr(self, 'bait_label'):
                self.bait_label.config(text=str(self.bait))
            self.save_config()
        else:
            self.bait_capacity_label.config(
                text="⚠ Select at least one bait key!",
                fg="#e74c3c"
            )
        
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
    
    def toggle_log_visibility(self):
        """Toggles the visibility of the status log."""
        if self.show_log_var.get():
            self.status_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            self.root.geometry("500x800")
        else:
            self.status_frame.pack_forget()
            self.root.geometry("500x600")
    
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
    
    def update_stats(self, hits: int, total_games: int):
        """Updates the statistics display with current game progress."""
        self.hits_label.config(text=f"{hits}")
        self.last_total_games = total_games
    
    def reset_bait(self):
        """Resets the bait counter to max capacity based on selected keys"""
        max_bait = self.get_max_bait_capacity()
        self.bait = max_bait
        self.bait_label.config(text=str(self.bait))
        # Also reset the bot's bait counter if it's running
        if self.bot:
            self.bot.bait_counter = max_bait
        self.add_status(f"Bait counter reset to {max_bait}")
        # Save config when bait is reset
        self.save_config()
    
    def update_bait_from_bot(self, new_bait: int):
        """Updates GUI bait counter when bot adjusts bait tier."""
        self.bait = new_bait
        self.bait_label.config(text=str(self.bait))
        self.save_config()
    
    def toggle_bot(self):
        """Toggles bot start/pause state."""
        # If bot is running and not paused, pause it
        if self.bot and self.bot.running and not self.bot.paused:
            self.bot.paused = True
            self.add_status("Bot paused")
            self.window_combo.config(state="readonly")
            self.reset_btn.config(state=tk.NORMAL)
            self.update_button_state()
            return
        
        # If bot is paused, resume it
        if self.bot and self.bot.paused:
            self.config['human_like_clicking'] = self.human_like_var.get()
            self.config['quick_skip'] = self.quick_skip_var.get()
            # Activate window before resuming
            self.window_manager.activate_window()
            self.bot.paused = False
            self.add_status("Bot resumed")
            self.window_combo.config(state="disabled")
            self.reset_btn.config(state=tk.DISABLED)
            self.update_button_state()
            return
        
        if not self.window_var.get():
            messagebox.showerror("Error", "Please select a window first!")
            return
        
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
        
        self.window_manager.select_window(selected_window)
        
        self.config['human_like_clicking'] = self.human_like_var.get()
        self.config['quick_skip'] = self.quick_skip_var.get()
        self.config['sound_alert_on_finish'] = self.sound_alert_var.get()
        self.save_config()
        
        self.add_status(f"Window '{selected_name}' activated")
        time.sleep(0.5)  # Give window time to focus before starting bot
        
        # Get selected bait keys
        selected_bait_keys = self.get_selected_bait_keys()
        if not selected_bait_keys:
            messagebox.showerror("Error", "Please select at least one bait key!")
            return
        
        # Pass None region - bot will auto-detect on first fishing window
        self.bot = FishingBot(None, self.config, self.window_manager, bait_counter=self.bait, bait_keys=selected_bait_keys)
        self.bot.on_status_update = self.add_status
        self.bot.on_stats_update = self.update_stats
        self.bot.on_pause_toggle = self.on_bot_pause_toggle
        self.bot.on_bait_update = self.update_bait_from_bot
        self.bot.on_bot_stop = self.on_bot_stopped
        
        self.bot.running = True
        self.bot_thread = threading.Thread(target=self.bot.start, daemon=True)
        self.bot_thread.start()
        
        self.update_button_state()
        self.window_combo.config(state="disabled")
        self.reset_btn.config(state=tk.DISABLED)
        self.add_status("Bot started!")
    
    def update_button_state(self):
        """Updates the control button text and state based on bot status."""
        if self.bot and self.bot.running and not self.bot.paused:
            self.control_btn.config(text="⏸ Pause (F5)", bg="#f39c12", activebackground="#e67e22")
        else:
            self.control_btn.config(text="▶ Start", bg="#2ecc71", activebackground="#27ae60")
    
    def on_bot_pause_toggle(self, is_paused: bool):
        """Updates UI when bot pause state changes."""
        if is_paused:
            self.update_button_state()
            self.window_combo.config(state="readonly")
            self.reset_btn.config(state=tk.NORMAL)
        else:
            self.update_button_state()
            self.window_combo.config(state="disabled")
            self.reset_btn.config(state=tk.DISABLED)
    
    def on_bot_stopped(self):
        """Updates UI when bot stops running."""
        self.bot = None
        self.update_button_state()
        self.window_combo.config(state="readonly")
        self.reset_btn.config(state=tk.NORMAL)
    
    def run(self):
        """Starts the GUI application."""
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.01
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()
    
    def on_close(self):
        """
        Handles the window close event.
        Saves the configuration before closing the application.
        """
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
import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from mss import mss
import pygetwindow as gw
import psutil
import time
from typing import List, Tuple, Optional
from dataclasses import dataclass

@dataclass
class GameRegion:
    """Stores the coordinates of the game window region"""
    left: int
    top: int
    width: int
    height: int

class WindowManager:
    """Manages window detection"""
    
    def __init__(self):
        self.selected_window = None
    
    @staticmethod
    def get_all_windows() -> List[Tuple[str, gw.Win32Window]]:
        """Gets all visible windows"""
        windows = []
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    process_name = proc.info['name']
                    wins = gw.getWindowsWithTitle(process_name)
                    for win in wins:
                        try:
                            is_visible = getattr(win, 'isVisible', True)
                            if callable(is_visible):
                                is_visible = is_visible()
                            if is_visible:
                                display_name = f"{process_name} - {win.title}"
                                windows.append((display_name, win))
                        except Exception:
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
            time.sleep(0.3)
        except Exception as e:
            print(f"Error activating window: {e}")
    
    def get_window_rect(self) -> Tuple[int, int, int, int]:
        """Gets window position and size (left, top, width, height)"""
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

class CapturePreview:
    """Preview what the bot is capturing with processing visualization"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Capture Preview - Processing Pipeline")
        self.root.geometry("1200x900")
        
        self.window_manager = WindowManager()
        self.sct = mss()
        self.square_radius = 68
        
        # Color ranges (same as bot)
        self.window_color_lower = np.array([98, 170, 189])
        self.window_color_upper = np.array([106, 255, 250])
        
        # Fish color ranges
        self.fish_color_lower = np.array([102, 136, 122])
        self.fish_color_upper = np.array([108, 141, 129])
        
        # Detection mode: 'window' or 'fish'
        self.detection_mode = 'window'
        
        # Adjustable thresholds
        self.window_threshold = 10000
        self.fish_threshold = 1
        
        self.setup_ui()
        self.update_loop()
    
    def setup_ui(self):
        """Create UI elements"""
        # Window selection
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(control_frame, text="Select Window:").pack(side=tk.LEFT, padx=5)
        
        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(control_frame, textvariable=self.window_var, 
                                         state="readonly", width=40)
        self.window_combo.pack(side=tk.LEFT, padx=5)
        self.window_combo.bind("<<ComboboxSelected>>", self.on_window_selected)
        
        ttk.Button(control_frame, text="Refresh", 
                   command=self.refresh_windows).pack(side=tk.LEFT, padx=5)
        
        # Detection mode selection
        mode_frame = ttk.Frame(self.root)
        mode_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(mode_frame, text="Detection Mode:").pack(side=tk.LEFT, padx=5)
        
        self.mode_var = tk.StringVar(value='window')
        self.mode_combo = ttk.Combobox(mode_frame, textvariable=self.mode_var, 
                                       values=['window', 'fish'], state="readonly", width=15)
        self.mode_combo.pack(side=tk.LEFT, padx=5)
        self.mode_combo.bind("<<ComboboxSelected>>", self.on_mode_changed)
        
        # Radius control
        radius_frame = ttk.Frame(self.root)
        radius_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(radius_frame, text="Region Radius (px):").pack(side=tk.LEFT, padx=5)
        self.radius_var = tk.IntVar(value=68)
        radius_spin = tk.Spinbox(radius_frame, from_=30, to=200, textvariable=self.radius_var,
                                 command=self.on_radius_changed, width=10)
        radius_spin.pack(side=tk.LEFT, padx=5)
        
        # Threshold controls
        threshold_frame = ttk.LabelFrame(self.root, text="Detection Thresholds")
        threshold_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Window color range controls
        window_color_frame = ttk.LabelFrame(threshold_frame, text="Window Color Range (HSV)")
        window_color_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Window lower bound (H, S, V)
        window_lower_frame = ttk.Frame(window_color_frame)
        window_lower_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(window_lower_frame, text="Lower Bound:").pack(side=tk.LEFT, padx=5)
        self.window_h_lower_var = tk.IntVar(value=98)
        self.window_s_lower_var = tk.IntVar(value=170)
        self.window_v_lower_var = tk.IntVar(value=189)
        
        ttk.Label(window_lower_frame, text="H:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(window_lower_frame, from_=0, to=180, orient=tk.HORIZONTAL, 
                 variable=self.window_h_lower_var, command=self.on_window_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.window_h_lower_label = ttk.Label(window_color_frame, text="98", width=3)
        self.window_h_lower_label.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(window_lower_frame, text="S:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(window_lower_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                 variable=self.window_s_lower_var, command=self.on_window_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.window_s_lower_label = ttk.Label(window_lower_frame, text="170", width=3)
        self.window_s_lower_label.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(window_lower_frame, text="V:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(window_lower_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                 variable=self.window_v_lower_var, command=self.on_window_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.window_v_lower_label = ttk.Label(window_color_frame, text="189", width=3)
        self.window_v_lower_label.pack(side=tk.LEFT, padx=2)
        
        # Window upper bound
        window_upper_frame = ttk.Frame(window_color_frame)
        window_upper_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(window_upper_frame, text="Upper Bound:").pack(side=tk.LEFT, padx=5)
        self.window_h_upper_var = tk.IntVar(value=106)
        self.window_s_upper_var = tk.IntVar(value=255)
        self.window_v_upper_var = tk.IntVar(value=250)
        
        ttk.Label(window_upper_frame, text="H:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(window_upper_frame, from_=0, to=180, orient=tk.HORIZONTAL,
                 variable=self.window_h_upper_var, command=self.on_window_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.window_h_upper_label = ttk.Label(window_upper_frame, text="106", width=3)
        self.window_h_upper_label.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(window_upper_frame, text="S:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(window_upper_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                 variable=self.window_s_upper_var, command=self.on_window_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.window_s_upper_label = ttk.Label(window_upper_frame, text="255", width=3)
        self.window_s_upper_label.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(window_upper_frame, text="V:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(window_upper_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                 variable=self.window_v_upper_var, command=self.on_window_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.window_v_upper_label = ttk.Label(window_upper_frame, text="250", width=3)
        self.window_v_upper_label.pack(side=tk.LEFT, padx=2)
        
        # Fish color range controls
        fish_color_frame = ttk.LabelFrame(threshold_frame, text="Fish Color Range (HSV)")
        fish_color_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Fish lower bound
        fish_lower_frame = ttk.Frame(fish_color_frame)
        fish_lower_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(fish_lower_frame, text="Lower Bound:").pack(side=tk.LEFT, padx=5)
        self.fish_h_lower_var = tk.IntVar(value=102)
        self.fish_s_lower_var = tk.IntVar(value=136)
        self.fish_v_lower_var = tk.IntVar(value=122)
        
        ttk.Label(fish_lower_frame, text="H:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(fish_lower_frame, from_=0, to=180, orient=tk.HORIZONTAL,
                 variable=self.fish_h_lower_var, command=self.on_fish_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.fish_h_lower_label = ttk.Label(fish_lower_frame, text="102", width=3)
        self.fish_h_lower_label.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(fish_lower_frame, text="S:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(fish_lower_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                 variable=self.fish_s_lower_var, command=self.on_fish_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.fish_s_lower_label = ttk.Label(fish_lower_frame, text="136", width=3)
        self.fish_s_lower_label.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(fish_lower_frame, text="V:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(fish_lower_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                 variable=self.fish_v_lower_var, command=self.on_fish_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.fish_v_lower_label = ttk.Label(fish_color_frame, text="122", width=3)
        self.fish_v_lower_label.pack(side=tk.LEFT, padx=2)
        
        # Fish upper bound
        fish_upper_frame = ttk.Frame(fish_color_frame)
        fish_upper_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(fish_upper_frame, text="Upper Bound:").pack(side=tk.LEFT, padx=5)
        self.fish_h_upper_var = tk.IntVar(value=108)
        self.fish_s_upper_var = tk.IntVar(value=141)
        self.fish_v_upper_var = tk.IntVar(value=129)
        
        ttk.Label(fish_upper_frame, text="H:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(fish_upper_frame, from_=0, to=180, orient=tk.HORIZONTAL,
                 variable=self.fish_h_upper_var, command=self.on_fish_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.fish_h_upper_label = ttk.Label(fish_upper_frame, text="108", width=3)
        self.fish_h_upper_label.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(fish_upper_frame, text="S:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(fish_upper_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                 variable=self.fish_s_upper_var, command=self.on_fish_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.fish_s_upper_label = ttk.Label(fish_upper_frame, text="141", width=3)
        self.fish_s_upper_label.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(fish_upper_frame, text="V:").pack(side=tk.LEFT, padx=2)
        ttk.Scale(fish_upper_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                 variable=self.fish_v_upper_var, command=self.on_fish_color_changed, length=250).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.fish_v_upper_label = ttk.Label(fish_upper_frame, text="129", width=3)
        self.fish_v_upper_label.pack(side=tk.LEFT, padx=2)
        
        # Pixel count thresholds
        pixel_thresh_frame = ttk.LabelFrame(threshold_frame, text="Pixel Count Thresholds")
        pixel_thresh_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Window pixel threshold
        window_pixel_frame = ttk.Frame(pixel_thresh_frame)
        window_pixel_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(window_pixel_frame, text="Window Threshold:").pack(side=tk.LEFT, padx=5)
        self.window_thresh_var = tk.IntVar(value=10000)
        self.window_thresh_scale = ttk.Scale(window_pixel_frame, from_=0, to=50000, 
                                             orient=tk.HORIZONTAL, variable=self.window_thresh_var,
                                             command=self.on_window_threshold_changed)
        self.window_thresh_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.window_thresh_label = ttk.Label(window_pixel_frame, text="10000", width=6)
        self.window_thresh_label.pack(side=tk.LEFT, padx=5)
        
        # Fish pixel threshold
        fish_pixel_frame = ttk.Frame(pixel_thresh_frame)
        fish_pixel_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(fish_pixel_frame, text="Fish Threshold:").pack(side=tk.LEFT, padx=5)
        self.fish_thresh_var = tk.IntVar(value=1)
        self.fish_thresh_scale = ttk.Scale(fish_pixel_frame, from_=1, to=1000,
                                           orient=tk.HORIZONTAL, variable=self.fish_thresh_var,
                                           command=self.on_fish_threshold_changed)
        self.fish_thresh_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.fish_thresh_label = ttk.Label(fish_pixel_frame, text="1", width=6)
        self.fish_thresh_label.pack(side=tk.LEFT, padx=5)
        
        # Canvas for image display (2x2 grid)
        canvas_frame = ttk.Frame(self.root)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Original frame
        ttk.Label(canvas_frame, text="Original Frame").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.canvas1 = tk.Canvas(canvas_frame, bg="black", height=350, width=550)
        self.canvas1.grid(row=1, column=0, padx=5, pady=5, sticky=tk.NSEW)
        
        # HSV conversion
        ttk.Label(canvas_frame, text="HSV Conversion").grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        self.canvas2 = tk.Canvas(canvas_frame, bg="black", height=350, width=550)
        self.canvas2.grid(row=1, column=1, padx=5, pady=5, sticky=tk.NSEW)
        
        # Color range mask
        ttk.Label(canvas_frame, text="Color Range Mask").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.canvas3 = tk.Canvas(canvas_frame, bg="black", height=350, width=550)
        self.canvas3.grid(row=3, column=0, padx=5, pady=5, sticky=tk.NSEW)
        
        # Pixel count info
        ttk.Label(canvas_frame, text="Detection Info").grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        self.info_frame = tk.Frame(canvas_frame, bg="gray20", height=350, width=550)
        self.info_frame.grid(row=3, column=1, padx=5, pady=5, sticky=tk.NSEW)
        
        self.info_text = tk.Text(self.info_frame, bg="gray20", fg="lime", 
                                 font=("Courier", 11), height=20, width=60)
        self.info_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.info_text.config(state=tk.DISABLED)
        
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.columnconfigure(1, weight=1)
        canvas_frame.rowconfigure(1, weight=1)
        canvas_frame.rowconfigure(3, weight=1)
        
        # Refresh windows on startup
        self.refresh_windows()
    
    def refresh_windows(self):
        """Refresh available windows"""
        windows = self.window_manager.get_all_windows()
        window_names = [name for name, _ in windows]
        self.window_combo['values'] = window_names
    
    def on_window_selected(self, event=None):
        """Handle window selection"""
        windows = self.window_manager.get_all_windows()
        selected_name = self.window_var.get()
        
        for name, win in windows:
            if name == selected_name:
                self.window_manager.select_window(win)
                break
    
    def on_radius_changed(self):
        """Handle radius slider change"""
        self.square_radius = self.radius_var.get()
    
    def on_mode_changed(self, event=None):
        """Handle detection mode change"""
        self.detection_mode = self.mode_var.get()
    
    def on_window_color_changed(self, value=None):
        """Handle window color range change"""
        self.window_color_lower = np.array([
            self.window_h_lower_var.get(),
            self.window_s_lower_var.get(),
            self.window_v_lower_var.get()
        ])
        self.window_color_upper = np.array([
            self.window_h_upper_var.get(),
            self.window_s_upper_var.get(),
            self.window_v_upper_var.get()
        ])
        self.window_h_lower_label.config(text=str(self.window_h_lower_var.get()))
        self.window_s_lower_label.config(text=str(self.window_s_lower_var.get()))
        self.window_v_lower_label.config(text=str(self.window_v_lower_var.get()))
        self.window_h_upper_label.config(text=str(self.window_h_upper_var.get()))
        self.window_s_upper_label.config(text=str(self.window_s_upper_var.get()))
        self.window_v_upper_label.config(text=str(self.window_v_upper_var.get()))
    
    def on_fish_color_changed(self, value=None):
        """Handle fish color range change"""
        self.fish_color_lower = np.array([
            self.fish_h_lower_var.get(),
            self.fish_s_lower_var.get(),
            self.fish_v_lower_var.get()
        ])
        self.fish_color_upper = np.array([
            self.fish_h_upper_var.get(),
            self.fish_s_upper_var.get(),
            self.fish_v_upper_var.get()
        ])
        self.fish_h_lower_label.config(text=str(self.fish_h_lower_var.get()))
        self.fish_s_lower_label.config(text=str(self.fish_s_lower_var.get()))
        self.fish_v_lower_label.config(text=str(self.fish_v_lower_var.get()))
        self.fish_h_upper_label.config(text=str(self.fish_h_upper_var.get()))
        self.fish_s_upper_label.config(text=str(self.fish_s_upper_var.get()))
        self.fish_v_upper_label.config(text=str(self.fish_v_upper_var.get()))
    
    def on_window_threshold_changed(self, value):
        """Handle window threshold change"""
        self.window_threshold = int(float(value))
        self.window_thresh_label.config(text=str(self.window_threshold))
    
    def on_fish_threshold_changed(self, value):
        """Handle fish threshold change"""
        self.fish_threshold = int(float(value))
        self.fish_thresh_label.config(text=str(self.fish_threshold))
    
    def capture_region(self) -> Optional[Tuple[np.ndarray, Tuple]]:
        """Capture the game region"""
        try:
            if not self.window_manager.selected_window:
                return None
            
            win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
            
            if win_width == 0 or win_height == 0:
                return None
            
            # Calculate center square region
            center_x = win_width // 2
            center_y = (win_height + 40) // 2
            
            left = max(0, center_x - self.square_radius)
            top = max(0, center_y - self.square_radius)
            size = self.square_radius * 2
            
            width = min(size, win_width - left)
            height = min(size, win_height - top)
            
            # Convert to absolute screen coordinates
            screen_left = win_left + left
            screen_top = win_top + top
            
            # Capture region
            monitor = {
                "left": screen_left,
                "top": screen_top,
                "width": width,
                "height": height
            }
            
            sct_img = self.sct.grab(monitor)
            frame = np.array(sct_img)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            return frame, (left, top, width, height)
        except Exception as e:
            print(f"Capture error: {e}")
            return None
    
    def process_frame(self, frame):
        """Process frame and extract HSV/mask information"""
        # Convert to HSV
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        if self.detection_mode == 'window':
            # Window detection mode
            mask = cv2.inRange(hsv, self.window_color_lower, self.window_color_upper)
            pixel_count = cv2.countNonZero(mask)
            threshold = self.window_threshold
            detected = pixel_count > threshold
            color_lower = self.window_color_lower
            color_upper = self.window_color_upper
            mode_name = "WINDOW COLOR"
        else:
            # Fish detection mode
            mask = cv2.inRange(hsv, self.fish_color_lower, self.fish_color_upper)
            pixel_count = cv2.countNonZero(mask)
            threshold = self.fish_threshold
            detected = pixel_count > threshold
            color_lower = self.fish_color_lower
            color_upper = self.fish_color_upper
            mode_name = "FISH COLOR"
        
        return hsv, mask, pixel_count, detected, threshold, mode_name, color_lower, color_upper
    
    def display_processing(self, frame, region_info):
        """Display the full processing pipeline"""
        try:
            h, w = frame.shape[:2]
            
            # Process frame
            hsv, mask, pixel_count, detected, threshold, mode_name, color_lower, color_upper = self.process_frame(frame)
            
            # Convert BGR to RGB for display
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hsv_rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            hsv_rgb = cv2.cvtColor(hsv_rgb, cv2.COLOR_BGR2RGB)
            mask_rgb = cv2.cvtColor(mask, cv2.COLOR_GRAY2RGB)
            
            # Display on canvases
            self.display_on_canvas(self.canvas1, frame_rgb)
            self.display_on_canvas(self.canvas2, hsv_rgb)
            self.display_on_canvas(self.canvas3, mask_rgb)
            
            # Update info text
            left, top, width, height = region_info
            info_text = f"""
CAPTURE REGION
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Size: {width}×{height}px
Position: ({left}, {top})
Radius: {self.square_radius}px

{mode_name} DETECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Color Lower: [{color_lower[0]}, {color_lower[1]}, {color_lower[2]}]
Color Upper: [{color_upper[0]}, {color_upper[1]}, {color_upper[2]}]

PIXEL COUNT
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Threshold: {threshold:,} pixels
Current: {pixel_count:,} pixels

DETECTION STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Detected: {'✓ YES' if detected else '✗ NO'}
Match Ratio: {(pixel_count/max(threshold,1))*100:.1f}%
"""
            
            self.info_text.config(state=tk.NORMAL)
            self.info_text.delete(1.0, tk.END)
            
            # Color the status based on detection
            if detected:
                self.info_text.insert(tk.END, info_text, "detected")
            else:
                self.info_text.insert(tk.END, info_text, "not_detected")
            
            self.info_text.tag_config("detected", foreground="lime")
            self.info_text.tag_config("not_detected", foreground="orange")
            self.info_text.config(state=tk.DISABLED)
            
        except Exception as e:
            print(f"Display error: {e}")
    
    def display_on_canvas(self, canvas, frame_rgb):
        """Display frame on specific canvas"""
        try:
            # Get canvas size
            canvas_width = canvas.winfo_width()
            canvas_height = canvas.winfo_height()
            
            if canvas_width < 2 or canvas_height < 2:
                canvas_width = 550
                canvas_height = 350
            
            # Calculate scale to fit canvas
            h, w = frame_rgb.shape[:2]
            scale = min(canvas_width / w, canvas_height / h, 1.0)
            new_w = int(w * scale)
            new_h = int(h * scale)
            
            # Resize frame
            frame_resized = cv2.resize(frame_rgb, (new_w, new_h))
            
            # Convert to PIL Image
            pil_image = Image.fromarray(frame_resized)
            photo = ImageTk.PhotoImage(pil_image)
            
            # Display on canvas
            canvas.delete("all")
            canvas.create_image(canvas_width // 2, canvas_height // 2, 
                               image=photo, anchor=tk.CENTER)
            canvas.image = photo  # Keep a reference
            
        except Exception as e:
            print(f"Canvas error: {e}")
    
    def update_loop(self):
        """Continuous update loop"""
        result = self.capture_region()
        
        if result:
            frame, region_info = result
            self.display_processing(frame, region_info)
        
        # Schedule next update
        self.root.after(100, self.update_loop)

if __name__ == "__main__":
    root = tk.Tk()
    app = CapturePreview(root)
    root.mainloop()

"""
Visualizes the fishing bot's capture region on the selected window.
Shows a preview of exactly what area the bot will be capturing and processing.
"""

import cv2
import numpy as np
from mss import mss
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk
import pygetwindow as gw
import psutil
from typing import List, Tuple

class WindowManager:
    """Manages window detection and focus"""
    
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
        except Exception as e:
            print(f"Error activating window: {e}")
    
    def get_window_rect(self) -> Tuple[int, int, int, int]:
        """Gets the selected window's position and size"""
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

class RegionVisualizer:
    """Visualizes the fishing bot's capture region"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Fishing Bot Region Visualizer")
        self.root.geometry("600x500")
        
        self.window_manager = WindowManager()
        self.square_radius = 87
        self.sct = mss()
        
        self.setup_ui()
        self.refresh_windows()
    
    def setup_ui(self):
        """Create UI elements"""
        # Window selection frame
        select_frame = ttk.Frame(self.root)
        select_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(select_frame, text="Select Window:").pack(side=tk.LEFT, padx=5)
        
        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(select_frame, textvariable=self.window_var, 
                                         state="readonly", width=50)
        self.window_combo.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.window_combo.bind("<<ComboboxSelected>>", self.on_window_selected)
        
        ttk.Button(select_frame, text="Refresh", 
                   command=self.refresh_windows).pack(side=tk.LEFT, padx=5)
        
        # Radius control frame
        radius_frame = ttk.Frame(self.root)
        radius_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(radius_frame, text="Square Radius:").pack(side=tk.LEFT, padx=5)
        
        self.radius_var = tk.IntVar(value=250)
        self.radius_scale = ttk.Scale(radius_frame, from_=50, to=500, orient=tk.HORIZONTAL,
                                      variable=self.radius_var, command=self.on_radius_changed)
        self.radius_scale.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        self.radius_label = ttk.Label(radius_frame, text="250px")
        self.radius_label.pack(side=tk.LEFT, padx=5)
        
        # Canvas for preview
        self.canvas = tk.Canvas(self.root, bg="black", height=400)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Info label
        self.info_label = ttk.Label(self.root, text="Select a window and adjust radius to preview region")
        self.info_label.pack(fill=tk.X, padx=10, pady=5)
    
    def refresh_windows(self):
        """Refresh window list"""
        windows = self.window_manager.get_all_windows()
        window_names = [name for name, _ in windows]
        self.window_combo['values'] = window_names
        self.windows_dict = {name: win for name, win in windows}
    
    def on_window_selected(self, event=None):
        """Handle window selection"""
        selected = self.window_var.get()
        if selected in self.windows_dict:
            self.window_manager.select_window(self.windows_dict[selected])
            self.visualize_region()
    
    def on_radius_changed(self, value):
        """Handle radius slider change"""
        self.square_radius = int(float(value))
        self.radius_label.config(text=f"{self.square_radius}px")
        self.visualize_region()
    
    def visualize_region(self):
        """Display the region visualization"""
        win_left, win_top, win_width, win_height = self.window_manager.get_window_rect()
        
        if win_width == 0 or win_height == 0:
            self.info_label.config(text="Error: Could not get window size")
            return
        
        try:
            # Capture the window
            monitor = {
                "left": win_left,
                "top": win_top,
                "width": win_width,
                "height": win_height
            }
            
            sct_img = self.sct.grab(monitor)
            frame = np.array(Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX"))
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            
            # Calculate center square region
            center_x = win_width // 2
            center_y = (win_height+40) // 2
            left = max(0, center_x - self.square_radius)
            top = max(0, center_y - self.square_radius)
            size = self.square_radius * 2
            width = min(size, win_width - left)
            height = min(size, win_height - top)
            
            # Draw rectangle on frame
            cv2.rectangle(frame, (left, top), (left + width, top + height), (0, 255, 0), 3)
            cv2.rectangle(frame, (left, top), (left + width, top + height), (0, 255, 0), 3)
            
            # Add center point
            center_x_abs = left + width // 2
            center_y_abs = top + height // 2
            cv2.circle(frame, (center_x_abs, center_y_abs), 5, (255, 0, 0), -1)
            cv2.circle(frame, (center_x_abs, center_y_abs), 10, (255, 0, 0), 2)
            
            # Add labels
            cv2.putText(frame, f"Capture Region: {width}x{height}px", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, f"Position: ({left}, {top})", (10, 65),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, f"Window Size: {win_width}x{win_height}px", (10, 100),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            
            # Draw the captured region as an inset
            region_frame = frame[top:top+height, left:left+width]
            
            # Resize preview frame to fit canvas
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            
            if canvas_width > 1 and canvas_height > 1:
                scale = min(canvas_width / frame.shape[1], canvas_height / frame.shape[0])
                display_frame = cv2.resize(frame, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)))
            else:
                display_frame = cv2.resize(frame, (600, 400))
            
            # Convert to PhotoImage
            display_frame_rgb = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(display_frame_rgb)
            photo = ImageTk.PhotoImage(pil_image)
            
            # Update canvas
            self.canvas.create_image(0, 0, image=photo, anchor=tk.NW)
            self.canvas.image = photo  # Keep reference
            
            # Update info
            self.info_label.config(text=f"✓ Capture Region: {width}x{height}px at ({left}, {top}) | Window: {win_width}x{win_height}px")
            
        except Exception as e:
            self.info_label.config(text=f"Error: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = RegionVisualizer(root)
    root.mainloop()

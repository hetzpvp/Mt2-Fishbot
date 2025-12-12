"""
Real-time HSV Color Range Tuner for Metin2 Fishing Bot
Allows live adjustment of color detection thresholds with instant visual feedback
"""

import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
from mss import mss
import pygetwindow as gw

class ColorTuner:
    """Real-time HSV color range tuner with live preview"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("HSV Color Range Tuner")
        self.root.geometry("1400x900")
        
        # Current frame
        self.current_frame = None
        self.sct = mss()
        self.selected_window = None
        
        # Default ranges (fishing window background detection)
        self.hue_lower = 98
        self.hue_upper = 106
        self.sat_lower = 170
        self.sat_upper = 255
        self.val_lower = 183
        self.val_upper = 249
        
        self.setup_ui()
        self.refresh_windows()
        
    def setup_ui(self):
        """Create the UI with sliders and preview"""
        # Main container
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Window selection frame
        window_frame = ttk.LabelFrame(main_frame, text="Window Selection", padding=10)
        window_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(window_frame, text="Select Window:").pack(side=tk.LEFT, padx=5)
        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(window_frame, textvariable=self.window_var, 
                                         state="readonly", width=40)
        self.window_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(window_frame, text="Refresh", command=self.refresh_windows).pack(side=tk.LEFT, padx=5)
        ttk.Button(window_frame, text="Start Capture", command=self.start_window_capture).pack(side=tk.LEFT, padx=5)
        
        # Left side: Video preview
        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        ttk.Label(left_frame, text="Original Frame:", font=("Arial", 10, "bold")).pack()
        self.original_label = ttk.Label(left_frame, background="black")
        self.original_label.pack(fill=tk.BOTH, expand=True, pady=5)
        
        ttk.Label(left_frame, text="Masked Frame (Detected Color):", font=("Arial", 10, "bold")).pack()
        self.masked_label = ttk.Label(left_frame, background="black")
        self.masked_label.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Right side: Controls
        right_frame = ttk.Frame(main_frame, width=300)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=5)
        right_frame.pack_propagate(False)
        
        # Title
        ttk.Label(right_frame, text="HSV Range Control", font=("Arial", 14, "bold")).pack(pady=10)
        
        # HUE controls
        hue_frame = ttk.LabelFrame(right_frame, text="HUE", padding=10)
        hue_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(hue_frame, text="Lower:").grid(row=0, column=0, sticky=tk.W)
        self.hue_lower_var = tk.IntVar(value=self.hue_lower)
        self.hue_lower_scale = ttk.Scale(hue_frame, from_=0, to=180, orient=tk.HORIZONTAL, 
                                         variable=self.hue_lower_var, command=self.on_change)
        self.hue_lower_scale.grid(row=0, column=1, sticky=tk.EW, padx=5)
        self.hue_lower_label = ttk.Label(hue_frame, text=str(self.hue_lower), width=5)
        self.hue_lower_label.grid(row=0, column=2)
        
        ttk.Label(hue_frame, text="Upper:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.hue_upper_var = tk.IntVar(value=self.hue_upper)
        self.hue_upper_scale = ttk.Scale(hue_frame, from_=0, to=180, orient=tk.HORIZONTAL,
                                         variable=self.hue_upper_var, command=self.on_change)
        self.hue_upper_scale.grid(row=1, column=1, sticky=tk.EW, padx=5)
        self.hue_upper_label = ttk.Label(hue_frame, text=str(self.hue_upper), width=5)
        self.hue_upper_label.grid(row=1, column=2)
        
        hue_frame.columnconfigure(1, weight=1)
        
        # SATURATION controls
        sat_frame = ttk.LabelFrame(right_frame, text="SATURATION", padding=10)
        sat_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(sat_frame, text="Lower:").grid(row=0, column=0, sticky=tk.W)
        self.sat_lower_var = tk.IntVar(value=self.sat_lower)
        self.sat_lower_scale = ttk.Scale(sat_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                                         variable=self.sat_lower_var, command=self.on_change)
        self.sat_lower_scale.grid(row=0, column=1, sticky=tk.EW, padx=5)
        self.sat_lower_label = ttk.Label(sat_frame, text=str(self.sat_lower), width=5)
        self.sat_lower_label.grid(row=0, column=2)
        
        ttk.Label(sat_frame, text="Upper:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.sat_upper_var = tk.IntVar(value=self.sat_upper)
        self.sat_upper_scale = ttk.Scale(sat_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                                         variable=self.sat_upper_var, command=self.on_change)
        self.sat_upper_scale.grid(row=1, column=1, sticky=tk.EW, padx=5)
        self.sat_upper_label = ttk.Label(sat_frame, text=str(self.sat_upper), width=5)
        self.sat_upper_label.grid(row=1, column=2)
        
        sat_frame.columnconfigure(1, weight=1)
        
        # VALUE controls
        val_frame = ttk.LabelFrame(right_frame, text="VALUE (Brightness)", padding=10)
        val_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(val_frame, text="Lower:").grid(row=0, column=0, sticky=tk.W)
        self.val_lower_var = tk.IntVar(value=self.val_lower)
        self.val_lower_scale = ttk.Scale(val_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                                         variable=self.val_lower_var, command=self.on_change)
        self.val_lower_scale.grid(row=0, column=1, sticky=tk.EW, padx=5)
        self.val_lower_label = ttk.Label(val_frame, text=str(self.val_lower), width=5)
        self.val_lower_label.grid(row=0, column=2)
        
        ttk.Label(val_frame, text="Upper:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.val_upper_var = tk.IntVar(value=self.val_upper)
        self.val_upper_scale = ttk.Scale(val_frame, from_=0, to=255, orient=tk.HORIZONTAL,
                                         variable=self.val_upper_var, command=self.on_change)
        self.val_upper_scale.grid(row=1, column=1, sticky=tk.EW, padx=5)
        self.val_upper_label = ttk.Label(val_frame, text=str(self.val_upper), width=5)
        self.val_upper_label.grid(row=1, column=2)
        
        val_frame.columnconfigure(1, weight=1)
        
        # Statistics
        stats_frame = ttk.LabelFrame(right_frame, text="Detection Info", padding=10)
        stats_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(stats_frame, text="Detected Pixels:").pack(anchor=tk.W)
        self.pixel_count_label = ttk.Label(stats_frame, text="0", font=("Arial", 12, "bold"), foreground="green")
        self.pixel_count_label.pack(anchor=tk.W, pady=5)
        
        # Current range display
        range_frame = ttk.LabelFrame(right_frame, text="Current Range (Copy for code)", padding=10)
        range_frame.pack(fill=tk.X, pady=5)
        
        self.range_text = tk.Text(range_frame, height=5, width=35, font=("Courier", 9))
        self.range_text.pack(fill=tk.BOTH)
        
        # Buttons
        button_frame = ttk.Frame(right_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(button_frame, text="Capture Frame", command=self.capture_manual).pack(fill=tk.X, pady=2)
        ttk.Button(button_frame, text="Reset to Default", command=self.reset_defaults).pack(fill=tk.X, pady=2)
        ttk.Button(button_frame, text="Copy Python Code", command=self.copy_code).pack(fill=tk.X, pady=2)
        
    def refresh_windows(self):
        """Refresh the list of available windows"""
        try:
            windows = gw.getAllWindows()
            window_names = [w.title for w in windows if w.title]
            self.window_combo['values'] = window_names
            if window_names:
                self.window_combo.current(0)
                self.selected_window = gw.getWindowsWithTitle(window_names[0])[0] if gw.getWindowsWithTitle(window_names[0]) else None
        except Exception as e:
            messagebox.showerror("Error", f"Failed to get windows: {e}")
    
    def start_window_capture(self):
        """Start capturing from selected window"""
        if not self.window_var.get():
            messagebox.showwarning("No Window", "Please select a window first")
            return
        
        try:
            windows = gw.getWindowsWithTitle(self.window_var.get())
            if windows:
                self.selected_window = windows[0]
                self.update_frame()
            else:
                messagebox.showwarning("Window not found", f"Could not find window: {self.window_var.get()}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to select window: {e}")
    
    def capture_manual(self):
        """Manually capture frame from selected window"""
        try:
            if self.selected_window:
                monitor = {
                    "left": self.selected_window.left,
                    "top": self.selected_window.top,
                    "width": self.selected_window.width,
                    "height": self.selected_window.height
                }
                sct_img = self.sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                self.current_frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                self.update_display()
            else:
                messagebox.showwarning("No Window Selected", "Please select and start capture from a window first")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to capture: {e}")
    
    def update_frame(self):
        """Continuously capture and update frame from selected window"""
        try:
            if self.selected_window:
                monitor = {
                    "left": self.selected_window.left,
                    "top": self.selected_window.top,
                    "width": self.selected_window.width,
                    "height": self.selected_window.height
                }
                sct_img = self.sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                self.current_frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                self.update_display()
        except Exception as e:
            print(f"Frame update error: {e}")
        
        # Schedule next update
        self.root.after(100, self.update_frame)  # Update every 100ms
    
    def on_change(self, value=None):
        """Called when any slider changes"""
        self.hue_lower = self.hue_lower_var.get()
        self.hue_upper = self.hue_upper_var.get()
        self.sat_lower = self.sat_lower_var.get()
        self.sat_upper = self.sat_upper_var.get()
        self.val_lower = self.val_lower_var.get()
        self.val_upper = self.val_upper_var.get()
        
        self.hue_lower_label.config(text=str(self.hue_lower))
        self.hue_upper_label.config(text=str(self.hue_upper))
        self.sat_lower_label.config(text=str(self.sat_lower))
        self.sat_upper_label.config(text=str(self.sat_upper))
        self.val_lower_label.config(text=str(self.val_lower))
        self.val_upper_label.config(text=str(self.val_upper))
        
        self.update_display()
    
    def update_display(self):
        """Update the display with original and masked images"""
        if self.current_frame is None:
            return
        
        # Resize for display
        display_height = 350
        scale = display_height / self.current_frame.shape[0]
        display_size = (int(self.current_frame.shape[1] * scale), display_height)
        
        # Original frame
        original_resized = cv2.resize(self.current_frame, display_size)
        original_rgb = cv2.cvtColor(original_resized, cv2.COLOR_BGR2RGB)
        original_pil = Image.fromarray(original_rgb)
        original_photo = ImageTk.PhotoImage(original_pil)
        
        self.original_label.config(image=original_photo)
        self.original_label.image = original_photo
        
        # Process HSV mask
        hsv = cv2.cvtColor(self.current_frame, cv2.COLOR_BGR2HSV)
        lower = np.array([self.hue_lower, self.sat_lower, self.val_lower])
        upper = np.array([self.hue_upper, self.sat_upper, self.val_upper])
        mask = cv2.inRange(hsv, lower, upper)
        
        # Count detected pixels
        pixel_count = cv2.countNonZero(mask)
        self.pixel_count_label.config(text=f"{pixel_count} pixels", 
                                     foreground="green" if pixel_count > 100 else "orange")
        
        # Masked frame
        masked_resized = cv2.resize(mask, display_size)
        masked_pil = Image.fromarray(masked_resized)
        masked_photo = ImageTk.PhotoImage(masked_pil)
        
        self.masked_label.config(image=masked_photo)
        self.masked_label.image = masked_photo
        
        # Update range display
        range_text = f"""Python Code:
lower = np.array([{self.hue_lower}, {self.sat_lower}, {self.val_lower}])
upper = np.array([{self.hue_upper}, {self.sat_upper}, {self.val_upper}])

For FishDetector:
self.circle_color_lower = np.array([{self.hue_lower}, {self.sat_lower}, {self.val_lower}])
self.circle_color_upper = np.array([{self.hue_upper}, {self.sat_upper}, {self.val_upper}])"""
        
        self.range_text.config(state=tk.NORMAL)
        self.range_text.delete("1.0", tk.END)
        self.range_text.insert("1.0", range_text)
        self.range_text.config(state=tk.DISABLED)
    
    def reset_defaults(self):
        """Reset to default circle detection ranges"""
        self.hue_lower_var.set(98)
        self.hue_upper_var.set(106)
        self.sat_lower_var.set(170)
        self.sat_upper_var.set(255)
        self.val_lower_var.set(183)
        self.val_upper_var.set(249)
        self.on_change()
    
    def copy_code(self):
        """Copy the Python code to clipboard"""
        code = f"""# Update these in FishDetector.__init__()
self.circle_color_lower = np.array([{self.hue_lower}, {self.sat_lower}, {self.val_lower}])
self.circle_color_upper = np.array([{self.hue_upper}, {self.sat_upper}, {self.val_upper}])"""
        
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        messagebox.showinfo("Copied", "Code copied to clipboard!")

if __name__ == "__main__":
    root = tk.Tk()
    tuner = ColorTuner(root)
    root.mainloop()

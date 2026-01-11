"""
Debug Windows for the Fishing Bot
IgnoredPositionsWindow, FishDetectorDebugWindow, and StatusLogWindow
"""

import os
import time
import tkinter as tk

import cv2
import numpy as np
from PIL import Image, ImageTk
from mss import mss

from utils import get_resource_path, DEBUG_PRINTS


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
                from utils import DEBUG_PRINTS
                if DEBUG_PRINTS:
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
                from utils import DEBUG_PRINTS
                if DEBUG_PRINTS:
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
            from utils import DEBUG_PRINTS
            if DEBUG_PRINTS:
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
            from utils import DEBUG_PRINTS
            if DEBUG_PRINTS:
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



class Aelys2DebugWindow:
    """Debug window for Aelys2 minigame target detection"""
    
    def __init__(self, parent, bot_instance):
        self.parent = parent
        self.bot = bot_instance
        self.window = None
        self.canvas = None
        self.photo_image = None
        self.status_label = None
        self.timeout_label = None
        self.info_text = None
        self._create_window()
        self._update_loop_id = None
        self.is_paused = False
    
    def _create_window(self):
        """Creates the Aelys2 debug window"""
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Aelys2 Target Detection - [W{self.bot.bot_id+1}]")
        self.window.geometry("600x520")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(False, False)
        
        icon_path = get_resource_path("monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.window.iconbitmap(icon_path)
            except Exception:
                pass
        
        header = tk.Frame(self.window, bg="#000000", height=35)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        title = tk.Label(header, text="🎯 Aelys2 Target Detection", 
                        font=("Courier New", 11, "bold"),
                        bg="#000000", fg="#FFD700")
        title.pack(pady=6)
        
        status_frame = tk.Frame(self.window, bg="#1a1a1a")
        status_frame.pack(fill=tk.X, padx=5, pady=3)
        
        self.status_label = tk.Label(status_frame, text="Status: Idle",
                                     font=("Courier New", 9, "bold"),
                                     bg="#1a1a1a", fg="#00ff00",
                                     anchor="w")
        self.status_label.pack(fill=tk.X, pady=2)
        
        self.timeout_label = tk.Label(status_frame, text="Timeout: --",
                                      font=("Courier New", 9),
                                      bg="#1a1a1a", fg="#FFD700",
                                      anchor="w")
        self.timeout_label.pack(fill=tk.X, pady=2)
        
        self.canvas = tk.Canvas(self.window, bg="#000000", width=580, height=350,
                               highlightthickness=1, highlightbackground="#333333")
        self.canvas.pack(padx=5, pady=5)
        
        self.info_text = tk.Label(self.window, text="Waiting for minigame...",
                                 font=("Courier New", 8),
                                 bg="#1a1a1a", fg="#888888",
                                 anchor="w", justify=tk.LEFT)
        self.info_text.pack(fill=tk.X, padx=10, pady=3)
        
        button_frame = tk.Frame(self.window, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.pause_btn = tk.Button(button_frame, text="⏸ Pause Updates", 
                                   command=self.toggle_pause,
                                   bg="#3498db", fg="white", 
                                   font=("Courier New", 9, "bold"),
                                   cursor="hand2", padx=10, pady=3)
        self.pause_btn.pack(side=tk.LEFT, padx=5)
        
        tk.Button(button_frame, text="Close", 
                 command=self._on_close,
                 bg="#555555", fg="white", 
                 font=("Courier New", 9, "bold"),
                 cursor="hand2", padx=15, pady=3).pack(side=tk.RIGHT, padx=5)
        
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_update()
    
    def toggle_pause(self):
        """Toggle pause state"""
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.pause_btn.config(text="▶ Resume Updates", bg="#e74c3c")
        else:
            self.pause_btn.config(text="⏸ Pause Updates", bg="#3498db")
    
    def _schedule_update(self):
        """Schedule next update"""
        if self.window and self.window.winfo_exists():
            self._update_loop_id = self.window.after(50, self._update_display)
    
    def _on_close(self):
        """Handle window close"""
        if self._update_loop_id:
            self.window.after_cancel(self._update_loop_id)
        if self.window:
            self.window.destroy()
            self.window = None
    
    def _update_display(self):
        """Update the display with current detection results"""
        try:
            if not self.window or not self.window.winfo_exists():
                return
            
            if self.is_paused:
                self._schedule_update()
                return
            
            if not self.bot.running:
                self.status_label.config(text="Status: Bot not running", fg="#ff8800")
                self._schedule_update()
                return
            
            try:
                frame = self.bot.capture_screen()
            except Exception as e:
                self.status_label.config(text=f"Status: Capture error", fg="#ff0000")
                self._schedule_update()
                return
            
            if frame is None or frame.size == 0:
                self.status_label.config(text="Status: No frame", fg="#ff8800")
                self._schedule_update()
                return
            
            h, w = frame.shape[:2]
            viz_frame = frame.copy()
            
            status_msg = []
            
            try:
                window_active = self.bot.detector.detect_aelys2_window(frame)
                
                if window_active:
                    cv2.putText(viz_frame, "WINDOW DETECTED (1.png)", (10, 30),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    status_msg.append("Window: YES")
                    
                    target_detected = self.bot.detector.detect_aelys2_targets(frame)
                    
                    if target_detected:
                        cv2.putText(viz_frame, "TARGET DETECTED! (2.png or 3.png)", (10, 70),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 3)
                        cv2.rectangle(viz_frame, (5, 5), (w-5, h-5), (0, 255, 255), 5)
                        status_msg.append("Target: FOUND!")
                        self.status_label.config(text="Status: TARGET FOUND - Press SPACE", fg="#00ffff")
                    else:
                        cv2.putText(viz_frame, "Searching for target...", (10, 70),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                        status_msg.append("Target: searching")
                        self.status_label.config(text="Status: Searching for target", fg="#ffff00")
                else:
                    cv2.putText(viz_frame, "WINDOW NOT DETECTED", (10, 30),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    status_msg.append("Window: NO")
                    self.status_label.config(text="Status: Minigame window not detected", fg="#ff0000")
                
                if self.bot._space_pressed_once:
                    cv2.putText(viz_frame, "SPACE PRESSED - ENDING", (10, h-20),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
                    status_msg.append("SPACE PRESSED")
                
            except Exception as e:
                status_msg.append(f"Error: {str(e)[:40]}")
                self.status_label.config(text=f"Status: Detection error", fg="#ff0000")
            
            timeout_text = "Timeout: 20s (check minigame loop)"
            self.timeout_label.config(text=timeout_text)
            
            scale = min(580.0 / w, 350.0 / h, 1.0)
            new_w = int(w * scale)
            new_h = int(h * scale)
            
            if new_w > 0 and new_h > 0:
                viz_resized = cv2.resize(viz_frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            else:
                self._schedule_update()
                return
            
            rgb_frame = cv2.cvtColor(viz_resized, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame)
            self.photo_image = ImageTk.PhotoImage(pil_image)
            
            self.canvas.delete("all")
            self.canvas.create_image(290, 175, image=self.photo_image, anchor="center")
            
            info_str = " | ".join(status_msg)
            self.info_text.config(text=info_str)
            
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Aelys2 debug window error: {e}")
        
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
    
    def is_visible(self):
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

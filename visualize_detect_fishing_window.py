import cv2
import numpy as np
from mss import mss
import pygetwindow as gw
import time
import tkinter as tk
from tkinter import ttk, messagebox

selected_window = None

def list_windows():
    """Lists all visible windows"""
    try:
        windows = gw.getAllWindows()
        visible = [w for w in windows if w.width > 100 and w.height > 100]
        return visible
    except Exception as e:
        print(f"Error listing windows: {e}")
    return []

def show_window_selector():
    """Shows GUI to select a window"""
    global selected_window
    
    windows = list_windows()
    if not windows:
        messagebox.showerror("Error", "No visible windows found!")
        return None
    
    root = tk.Tk()
    root.title("Select Game Window")
    root.geometry("500x400")
    
    tk.Label(root, text="Select a window:", font=("Arial", 12, "bold")).pack(pady=10)
    
    # Listbox with scrollbar
    frame = tk.Frame(root)
    frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    scrollbar = tk.Scrollbar(frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set, font=("Arial", 10))
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.config(command=listbox.yview)
    
    # Populate listbox
    for i, win in enumerate(windows):
        display_text = f"{win.title[:50]} ({win.width}x{win.height})"
        listbox.insert(tk.END, display_text)
    
    if windows:
        listbox.selection_set(0)
    
    def on_select():
        global selected_window
        selection = listbox.curselection()
        if selection:
            selected_window = windows[selection[0]]
            root.destroy()
        else:
            messagebox.showwarning("Warning", "Please select a window!")
    
    def on_cancel():
        root.destroy()
    
    button_frame = tk.Frame(root)
    button_frame.pack(pady=10)
    
    tk.Button(button_frame, text="Select", command=on_select, width=15, bg="#2ecc71", fg="white").pack(side=tk.LEFT, padx=5)
    tk.Button(button_frame, text="Cancel", command=on_cancel, width=15, bg="#e74c3c", fg="white").pack(side=tk.LEFT, padx=5)
    
    root.mainloop()
    
    return selected_window

def visualize_detect_fishing_window(window, duration=0):
    """
    Visualizes the detect_fishing_window method in real-time.
    Shows the detection result (True/False), pixel count, and HSV mask.
    
    Args:
        window: The game window to analyze
        duration: How long to run in seconds (0 = infinite)
    """
    
    # Color ranges from FishDetector
    window_color_lower = np.array([98, 170, 189])
    window_color_upper = np.array([106, 255, 250])
    detection_threshold = 10000
    
    if not window:
        print("ERROR: No window selected!")
        return
    
    print(f"Selected: {window.title} ({window.width}x{window.height})")
    print("\nStarting detection visualization...")
    print(f"Detection threshold: {detection_threshold} pixels")
    print("Press 'q' to quit, 's' to save frame")
    
    sct = mss()
    start_time = time.time()
    frame_count = 0
    
    try:
        while True:
            if duration > 0 and time.time() - start_time > duration:
                break
            
            # Capture window
            monitor = {
                "left": window.left,
                "top": window.top,
                "width": window.width,
                "height": window.height
            }
            
            sct_img = sct.grab(monitor)
            frame = np.array(sct_img)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            # Convert to HSV
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Create mask for fishing window color
            mask = cv2.inRange(hsv, window_color_lower, window_color_upper)
            
            # Count pixels (this is what detect_fishing_window does)
            pixel_count = cv2.countNonZero(mask)
            is_detected = pixel_count > detection_threshold
            
            # Create visualization
            frame_display = frame.copy()
            mask_colored = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            
            # Add detection status to frame
            status_text = "✓ DETECTED" if is_detected else "✗ NOT DETECTED"
            status_color = (0, 255, 0) if is_detected else (0, 0, 255)
            
            # Draw large status indicator
            cv2.putText(frame_display, status_text, (20, 50), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1.5, status_color, 3)
            
            # Draw pixel count
            cv2.putText(frame_display, f"Pixels: {pixel_count} / {detection_threshold}", 
                       (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            
            # Draw detection confidence bar
            bar_width = 300
            bar_height = 30
            bar_x, bar_y = 20, 130
            bar_fill = min(int((pixel_count / detection_threshold) * bar_width), bar_width)
            
            cv2.rectangle(frame_display, (bar_x, bar_y), (bar_x + bar_width, bar_y + bar_height), 
                         (255, 255, 255), 2)
            cv2.rectangle(frame_display, (bar_x, bar_y), (bar_x + bar_fill, bar_y + bar_height), 
                         status_color, -1)
            
            # Percentage text
            percentage = min(100, int((pixel_count / detection_threshold) * 100))
            cv2.putText(frame_display, f"{percentage}%", (bar_x + bar_width + 10, bar_y + 22), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            
            # Resize for display if too large
            scale = 1
            if frame.shape[1] > 1600:
                scale = 1600 / frame.shape[1]
            
            if scale < 1:
                frame_display = cv2.resize(frame_display, (int(frame.shape[1]*scale), int(frame.shape[0]*scale)))
                mask_display = cv2.resize(mask_colored, (int(mask.shape[1]*scale), int(mask.shape[0]*scale)))
            else:
                mask_display = mask_colored
            
            # Combine horizontally
            combined = np.hstack([frame_display, mask_display])
            
            # Display
            cv2.imshow("detect_fishing_window() | Original (left) | Mask (right) | Press 'q' to quit", combined)
            
            frame_count += 1
            
            # Handle key press
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("\nQuitting...")
                break
            elif key == ord('s'):
                filename = f"detect_fishing_frame_{frame_count}.png"
                cv2.imwrite(filename, combined)
                print(f"Saved frame to {filename}")
    
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    
    finally:
        cv2.destroyAllWindows()
        elapsed = time.time() - start_time
        fps = frame_count / elapsed if elapsed > 0 else 0
        print(f"\nProcessed {frame_count} frames in {elapsed:.1f} seconds ({fps:.1f} FPS)")

if __name__ == "__main__":
    import sys
    
    duration = 0  # 0 = infinite, set to N for N seconds
    
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
            print(f"Running for {duration} seconds")
        except:
            print("Usage: python visualize_detect_fishing_window.py [duration_in_seconds]")
    
    # Show window selector
    window = show_window_selector()
    if window:
        visualize_detect_fishing_window(window, duration)

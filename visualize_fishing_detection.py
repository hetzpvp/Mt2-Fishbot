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
        visible = [w for w in windows if w.isVisible and w.width > 100 and w.height > 100]
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

def visualize_fishing_detection(window, duration=30):
    """
    Visualizes the fishing window detection in real-time.
    Shows original frame, HSV mask, and bounding box.
    
    Args:
        duration: How long to run in seconds (0 = infinite)
    """
    
    # Color ranges from FishDetector
    window_color_lower = np.array([98, 170, 189])
    window_color_upper = np.array([106, 255, 250])
    
    if not window:
        print("ERROR: No window selected!")
        return
    
    print(f"Found window: {window.title}")
    print(f"Window size: {window.width}x{window.height}")
    print(f"Position: ({window.left}, {window.top})")
    print("\nStarting visualization...")
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
            
            # Find contours
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Create visualization
            frame_with_box = frame.copy()
            
            if contours:
                largest_contour = max(contours, key=cv2.contourArea)
                x, y, w, h = cv2.boundingRect(largest_contour)
                
                # Draw bounding box
                cv2.rectangle(frame_with_box, (x, y), (x + w, y + h), (0, 255, 0), 3)
                cv2.putText(frame_with_box, f"Box: ({x},{y}) {w}x{h}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                
                # Draw center
                cx, cy = x + w // 2, y + h // 2
                cv2.circle(frame_with_box, (cx, cy), 5, (255, 0, 0), -1)
                cv2.putText(frame_with_box, f"Center: ({cx},{cy})", (10, 60), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
                
                pixel_count = cv2.countNonZero(mask)
                cv2.putText(frame_with_box, f"Pixels detected: {pixel_count}", (10, 90), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            else:
                cv2.putText(frame_with_box, "No fishing window detected", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
            # Create 3-panel view
            mask_colored = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            
            # Resize for display if too large
            scale = 1
            if frame.shape[1] > 1600:
                scale = 1600 / frame.shape[1]
            
            if scale < 1:
                frame_display = cv2.resize(frame_with_box, (int(frame.shape[1]*scale), int(frame.shape[0]*scale)))
                mask_display = cv2.resize(mask_colored, (int(mask.shape[1]*scale), int(mask.shape[0]*scale)))
            else:
                frame_display = frame_with_box
                mask_display = mask_colored
            
            # Combine horizontally
            combined = np.hstack([frame_display, mask_display])
            
            # Display
            cv2.imshow("Fishing Window Detection | Original (left) | Mask (right) | Press 'q' to quit", combined)
            
            frame_count += 1
            
            # Handle key press
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("\nQuitting...")
                break
            elif key == ord('s'):
                filename = f"fishing_detection_frame_{frame_count}.png"
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
            print("Usage: python visualize_fishing_detection.py [duration_in_seconds]")
    
    # Show window selector
    window = show_window_selector()
    if window:
        print(f"\nSelected: {window.title} ({window.width}x{window.height})")
        visualize_fishing_detection(window, duration)

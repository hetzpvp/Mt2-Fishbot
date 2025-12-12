"""Debug script to test fish and circle detection"""
import cv2
import numpy as np
from mss import mss
from PIL import Image
import time
import psutil
import pygetwindow as gw
from dataclasses import dataclass

try:
    from pynput import mouse
except ImportError:
    print("ERROR: pynput not installed!")
    print("Install with: pip install pynput")
    mouse = None

# Color ranges for detecting the fish (gray/dark colors)
fish_color_lower = np.array([0, 0, 30])
fish_color_upper = np.array([180, 50, 120])

# Color range for the circle - updated to detect blue/purple circle
# The circle in the game appears to be blue/purple, not pink/red
circle_color_lower = np.array([90, 100, 100])  # Blue hue range
circle_color_upper = np.array([130, 255, 255])  # Blue hue range

@dataclass
class GameRegion:
    """Stores the coordinates of the game window region"""
    left: int
    top: int
    width: int
    height: int

def capture_screen(region_left, region_top, region_width, region_height):
    """Captures a screen region"""
    try:
        sct = mss()
        monitor = {
            "left": region_left,
            "top": region_top,
            "width": region_width,
            "height": region_height
        }
        
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        return np.array(img)
    except Exception as e:
        print(f"Screenshot error: {e}")
        return None

def get_all_windows():
    """Gets all visible windows"""
    windows = []
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                for window in gw.getWindowsWithTitle(proc.info['name']):
                    try:
                        # Check if window is visible
                        is_visible = window.isVisible
                        if callable(is_visible):
                            is_visible = is_visible()
                    except:
                        is_visible = True
                    
                    if is_visible and window.width > 0 and window.height > 0:
                        windows.append((f"{proc.info['name']} - {window.title}", window))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        print(f"Error getting windows: {e}")
    
    return windows

def get_window_rect(window):
    """Gets the window's position and size"""
    try:
        return (window.left, window.top, window.width, window.height)
    except Exception as e:
        print(f"Error getting window rect: {e}")
        return (0, 0, 0, 0)

def find_circle_center(frame):
    """Finds the circle center and radius"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    mask = cv2.inRange(hsv, circle_color_lower, circle_color_upper)
    
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if not contours:
        return None
    
    largest_contour = max(contours, key=cv2.contourArea)
    (x, y), radius = cv2.minEnclosingCircle(largest_contour)
    
    # Ensure radius is valid
    if radius <= 1:
        return None
    
    return (int(x), int(y), int(radius))

def detect_failed_text(frame):
    """Detects if 'FAILED!' text is present"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Look for red text (FAILED! is red)
    mask1 = cv2.inRange(hsv, np.array([0, 100, 100]), np.array([10, 255, 255]))
    mask2 = cv2.inRange(hsv, np.array([170, 100, 100]), np.array([180, 255, 255]))
    mask = cv2.bitwise_or(mask1, mask2)
    
    red_pixel_count = cv2.countNonZero(mask)
    
    # If we have a cluster of red pixels in the center area, it's probably "FAILED!"
    h, w = frame.shape[:2]
    center_region = mask[h//3:2*h//3, w//3:2*w//3]
    center_red_count = cv2.countNonZero(center_region)
    
    return center_red_count > 50

def find_fish(frame):
    """Finds the fish position in the frame"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    # Try multiple thresholds to find dark spots (fish)
    _, thresh = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
    
    # Apply morphological operations to clean up
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    
    # Find contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    best_fish = None
    best_circularity = 0
    
    for contour in contours:
        area = cv2.contourArea(contour)
        
        # Relaxed size range - fish can be various sizes
        if 30 < area < 1000:
            perimeter = cv2.arcLength(contour, True)
            if perimeter > 0:
                circularity = 4 * np.pi * area / (perimeter * perimeter)
                
                # Relaxed circularity threshold to catch more fish shapes
                if circularity > best_circularity and circularity > 0.3:
                    best_circularity = circularity
                    M = cv2.moments(contour)
                    if M["m00"] != 0:
                        cx = int(M["m10"] / M["m00"])
                        cy = int(M["m01"] / M["m00"])
                        best_fish = (cx, cy)
    
    return best_fish

def detect_try_again_button(frame):
    """Detects the 'Try Again' button and returns its center"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Create mask for green button
    mask = cv2.inRange(hsv, np.array([40, 100, 100]), np.array([80, 255, 255]))
    
    # Find contours
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # Look for rectangular button shape
    for contour in contours:
        area = cv2.contourArea(contour)
        
        # Button should be reasonably large
        if area > 500:
            x, y, w, h = cv2.boundingRect(contour)
            
            # Check if it's roughly rectangular (button shape)
            aspect_ratio = w / float(h) if h > 0 else 0
            if 1.5 < aspect_ratio < 3.5:
                # Return center of button
                return (x + w // 2, y + h // 2)
    
    return None

def detect_fishing_window(frame):
    """Detects if the fishing window is active"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Create mask for blue/purple circle
    mask = cv2.inRange(hsv, circle_color_lower, circle_color_upper)
    
    blue_pixel_count = cv2.countNonZero(mask)
    return blue_pixel_count > 100

def visualize_detections(frame, circle_info, fish_pos):
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

print("Debug Fish Detection Script")
print("=" * 50)
print()

# Get list of windows
print("Getting available windows...")
windows = get_all_windows()

if not windows:
    print("No visible windows found!")
    exit(1)

print(f"Found {len(windows)} window(s):\n")
for i, (name, _) in enumerate(windows):
    print(f"{i}: {name}")

print()
window_idx = int(input("Select window number: "))

if window_idx < 0 or window_idx >= len(windows):
    print("Invalid window index!")
    exit(1)

selected_name, selected_window = windows[window_idx]
print(f"\nSelected: {selected_name}")

# Activate the window
try:
    selected_window.activate()
    time.sleep(0.5)
except Exception as e:
    print(f"Warning: Could not activate window: {e}")

# Region selection
print("\nRegion Selection Mode")
print("=" * 50)
print("Click top-left corner of the fishing game area...")

selection_points = []
mouse_listener = None

def on_mouse_move(x, y):
    """Handles mouse movement to display position"""
    win_left, win_top, _, _ = get_window_rect(selected_window)
    rel_x = x - win_left
    rel_y = y - win_top
    print(f"Mouse: screen=({x}, {y}), window-relative=({rel_x}, {rel_y})    ", end='\r')

def on_mouse_click(x, y, button, pressed):
    """Handles mouse clicks during region selection"""
    if not pressed or button != mouse.Button.left:
        return
    
    # Convert to window-relative coordinates
    win_left, win_top, win_width, win_height = get_window_rect(selected_window)
    rel_x = x - win_left
    rel_y = y - win_top
    
    # Validate that click is within the window bounds
    if rel_x < 0 or rel_y < 0 or rel_x > win_width or rel_y > win_height:
        print(f"\nClick outside window bounds ({rel_x}, {rel_y}) - try again")
        return
    
    selection_points.append((rel_x, rel_y))
    
    if len(selection_points) == 1:
        print(f"\nTop-left set: ({rel_x}, {rel_y})")
        print("Now click bottom-right corner...")
    elif len(selection_points) == 2:
        print(f"\nBottom-right set: ({rel_x}, {rel_y})")
        global mouse_listener
        if mouse_listener:
            mouse_listener.stop()

# Start mouse listener
mouse_listener = mouse.Listener(on_move=on_mouse_move, on_click=on_mouse_click)
mouse_listener.start()

# Wait for two clicks
while len(selection_points) < 2:
    time.sleep(0.1)

# Stop listener
mouse_listener.stop()

# Create region from selected points
p1, p2 = selection_points
region = GameRegion(
    left=min(p1[0], p2[0]),
    top=min(p1[1], p2[1]),
    width=abs(p2[0] - p1[0]),
    height=abs(p2[1] - p1[1])
)

print(f"\nRegion configured: {region.width}x{region.height} at ({region.left}, {region.top})")
print()
print("Starting capture... Press Ctrl+C to stop.")
print("A window will display the detection results. Press 'q' in the window to quit.")
print()

try:
    last_failed_time = 0
    
    while True:
        # Capture from window-relative coordinates
        win_left, win_top, _, _ = get_window_rect(selected_window)
        frame = capture_screen(
            win_left + region.left,
            win_top + region.top,
            region.width,
            region.height
        )
        
        if frame is None:
            print("Failed to capture screen")
            time.sleep(0.5)
            continue
        
        # Check for FAILED screen FIRST - this takes priority over everything
        is_failed = detect_failed_text(frame)
        if is_failed:
            current_time = time.time()
            
            # Wait a bit, then try to find and click the button
            if current_time - last_failed_time > 1.5:  # Wait 1.5 seconds before trying to click
                button_pos = detect_try_again_button(frame)
                if button_pos:
                    bx, by = button_pos
                    print(f"\nFAILED detected! Clicking Try Again at ({bx}, {by})")
                    last_failed_time = current_time
                else:
                    print("\nFAILED detected but Try Again button not found")
            else:
                elapsed = current_time - last_failed_time
                print(f"FAILED screen - waiting ({1.5 - elapsed:.1f}s remaining)...", end='\r')
            
            # Create and display visualization
            vis = visualize_detections(frame, None, None)
            cv2.putText(vis, "FAILED SCREEN DETECTED", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            cv2.imshow("Detection Results", vis)
            
            # Check for 'q' key to quit
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
            time.sleep(0.1)
            continue  # Skip normal game detection when FAILED screen is showing
        
        # Check fishing window
        is_fishing = detect_fishing_window(frame)
        print(f"Fishing window active: {is_fishing}", end=" | ")
        
        # Find circle
        circle_info = find_circle_center(frame)
        if circle_info:
            cx, cy, radius = circle_info
            print(f"Circle: center=({cx}, {cy}), radius={radius}", end=" | ")
        else:
            print("Circle: NOT FOUND", end=" | ")
        
        # Find fish
        fish_pos = find_fish(frame)
        if fish_pos:
            fx, fy = fish_pos
            print(f"Fish: ({fx}, {fy})")
            
            # Check if in circle
            if circle_info:
                cx, cy, radius = circle_info
                distance = np.sqrt((fx - cx)**2 + (fy - cy)**2)
                in_circle = distance < radius
                print(f"  -> Distance from circle: {distance:.1f}, In circle: {in_circle}")
        else:
            print("Fish: NOT FOUND")
        
        # Create and display visualization
        vis = visualize_detections(frame, circle_info, fish_pos)
        cv2.imshow("Detection Results", vis)
        
        # Check for 'q' key to quit
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
        
        time.sleep(0.05)

except KeyboardInterrupt:
    print("\n\nDebug session ended.")
finally:
    cv2.destroyAllWindows()

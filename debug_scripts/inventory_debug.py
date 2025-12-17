"""
Inventory Debug Script - Real-time template matching and item detection
Helps diagnose issues with capture_inventory_area and identify_item_in_inventory
"""

import sys
import os
import cv2
import numpy as np
from pathlib import Path
import time
import threading

# Add parent directory to path to import from main bot
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mss import mss
import pyautogui

try:
    import pygetwindow as gw
except ImportError:
    gw = None
    print("Warning: pygetwindow not installed. Install with: pip install pygetwindow")

def get_resource_path(filename: str) -> str:
    """Get the path to a bundled resource"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename)

def get_all_windows():
    """Get all available windows"""
    if gw is None:
        return []
    
    windows = []
    try:
        all_wins = gw.getAllWindows()
        for win in all_wins:
            try:
                if win.title and win.title.strip():
                    if getattr(win, 'visible', True):
                        windows.append((win.title, win))
            except:
                pass
    except Exception as e:
        print(f"Error getting windows: {e}")
    
    return windows

class InventoryDebugger:
    def __init__(self):
        self.sct = mss()
        self.template_cache = {}
        self.load_templates()
    
    def load_templates(self):
        """Load all templates from assets folder"""
        assets_path = get_resource_path("assets")
        
        if not os.path.exists(assets_path):
            print(f"ERROR: Assets folder not found at {assets_path}")
            return
        
        border = 7  # Match the bot's border crop
        
        for f in os.listdir(assets_path):
            if f.endswith(('_living.jpg', '_living.png', '_item.jpg', '_item.png')):
                try:
                    img_path = os.path.join(assets_path, f)
                    template = cv2.imread(img_path)
                    if template is not None:
                        # Convert to grayscale for matching
                        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
                        
                        # Crop border from all edges
                        h, w = template_gray.shape
                        if h > border * 2 and w > border * 2:
                            template_gray = template_gray[border:h-border, border:w-border]
                        
                        # Pre-compute half dimensions
                        h, w = template_gray.shape
                        self.template_cache[f] = (template_gray, w >> 1, h >> 1, img_path)
                        print(f"✓ Loaded: {f} ({w}x{h})")
                except Exception as e:
                    print(f"✗ Error loading {f}: {e}")
        
        print(f"\nTotal templates loaded: {len(self.template_cache)}\n")
    
    def capture_inventory_area(self, window_pos: tuple) -> np.ndarray:
        """Capture inventory area from window position
        window_pos: (left, top, width, height)
        """
        win_left, win_top, win_width, win_height = window_pos
        inventory_width = 180
        
        monitor = {
            "left": win_left + win_width - inventory_width,
            "top": win_top + 330,
            "width": inventory_width,
            "height": max(0, win_height - 30)
        }
        
        sct_img = self.sct.grab(monitor)
        frame = np.array(sct_img)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        
        return frame
    
    def identify_item_in_inventory(self, inventory_frame: np.ndarray, ignore_positions: set = None, visualize: bool = False):
        """Identify items in inventory with optional visualization
        
        Returns: (filename, (x, y), match_score) or None
        """
        inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape
        
        # Local references for speed
        match_template = cv2.matchTemplate
        where = np.where
        TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
        
        # Pre-convert ignore_positions to list
        ignore_list = list(ignore_positions) if ignore_positions else None
        
        best_match = None
        best_score = 0
        
        for filename, (template, half_w, half_h, img_path) in self.template_cache.items():
            t_h, t_w = template.shape
            
            if t_h > inv_h or t_w > inv_w:
                continue
            
            try:
                result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)
                locations = where(result >= 0.8)
                
                # Fast path: no matches
                if locations[0].size == 0:
                    continue
                
                # Iterate matches
                for pt_y, pt_x in zip(locations[0], locations[1]):
                    score = result[pt_y, pt_x]
                    center_x = pt_x + half_w
                    center_y = pt_y + half_h
                    
                    # Check ignore list
                    if ignore_list:
                        is_ignored = False
                        for ix, iy in ignore_list:
                            if abs(center_x - ix) < 10 and abs(center_y - iy) < 10:
                                is_ignored = True
                                break
                        if is_ignored:
                            continue
                    
                    if score > best_score:
                        best_score = score
                        best_match = (filename, (center_x, center_y), score, img_path, (pt_y, pt_x, t_h, t_w))
                    
            except Exception as e:
                print(f"Error matching {filename}: {e}")
                continue
        
        if visualize and best_match:
            self._visualize_match(inventory_frame, best_match)
        
        if best_match:
            return (best_match[0], best_match[1], best_match[2])
        return None
    
    def _visualize_match(self, frame, match_info):
        """Visualize the matched item on the frame"""
        filename, (center_x, center_y), score, img_path, (pt_y, pt_x, t_h, t_w) = match_info
        
        frame_vis = frame.copy()
        
        # Draw template match rectangle
        cv2.rectangle(frame_vis, (pt_x, pt_y), (pt_x + t_w, pt_y + t_h), (0, 255, 0), 2)
        
        # Draw center point
        cv2.circle(frame_vis, (center_x, center_y), 5, (0, 0, 255), -1)
        
        # Draw text info
        text = f"{filename[:20]} ({score:.3f})"
        cv2.putText(frame_vis, text, (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        cv2.putText(frame_vis, f"Center: ({center_x}, {center_y})", (5, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        
        # Show image
        cv2.imshow("Item Match Visualization", frame_vis)
    
    def debug_capture(self, window_left=None, window_top=None, window_width=None, window_height=None):
        """Debug inventory capture
        If no window position provided, will get active window from PyAutoGUI
        """
        
        if window_left is None:
            # Get active window position
            import subprocess
            try:
                # PowerShell command to get active window
                result = subprocess.run(
                    ["powershell", "-Command", 
                     "[System.Windows.Forms.Screen]::PrimaryScreen.Bounds | Select-Object Left,Top,Width,Height | ConvertTo-Json"],
                    capture_output=True, text=True
                )
                # For now, use a default area
                window_left, window_top = 0, 0
                window_width, window_height = 800, 600
                print(f"Using default window: {window_width}x{window_height} at ({window_left}, {window_top})")
            except:
                window_left, window_top = 0, 0
                window_width, window_height = 800, 600
                print(f"Using default window: {window_width}x{window_height} at ({window_left}, {window_top})")
        
        print("\n" + "="*60)
        print("INVENTORY CAPTURE DEBUG")
        print("="*60)
        print(f"Window Position: ({window_left}, {window_top})")
        print(f"Window Size: {window_width}x{window_height}")
        
        # Capture inventory area
        print("\nCapturing inventory area...")
        inventory = self.capture_inventory_area((window_left, window_top, window_width, window_height))
        print(f"✓ Inventory captured: {inventory.shape}")
        
        # Identify items
        print("\nIdentifying items...")
        matches = []
        
        inventory_gray = cv2.cvtColor(inventory, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape
        match_template = cv2.matchTemplate
        where = np.where
        TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
        
        for filename, (template, half_w, half_h, img_path) in self.template_cache.items():
            t_h, t_w = template.shape
            
            if t_h > inv_h or t_w > inv_w:
                continue
            
            try:
                result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)
                locations = where(result >= 0.8)
                
                if locations[0].size == 0:
                    continue
                
                for pt_y, pt_x in zip(locations[0], locations[1]):
                    score = result[pt_y, pt_x]
                    center_x = pt_x + half_w
                    center_y = pt_y + half_h
                    
                    matches.append({
                        'filename': filename,
                        'center': (center_x, center_y),
                        'score': float(score),
                        'topleft': (pt_x, pt_y),
                        'size': (t_w, t_h)
                    })
            except:
                continue
        
        # Sort by score
        matches.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"\nFound {len(matches)} items:")
        for i, match in enumerate(matches[:10]):  # Show top 10
            print(f"  {i+1}. {match['filename']}")
            print(f"     Center: {match['center']}, Score: {match['score']:.4f}")
        
        # Visualize all matches
        if matches:
            self._visualize_all_matches(inventory, matches)
        
        # Save capture for inspection
        output_path = os.path.join(os.path.dirname(__file__), "inventory_capture.png")
        cv2.imwrite(output_path, inventory)
        print(f"\n✓ Capture saved to: {output_path}")
        
        return inventory, matches
    
    def _visualize_all_matches(self, frame, matches):
        """Visualize all matches on the frame"""
        frame_vis = frame.copy()
        
        # Color palette for different matches
        colors = [
            (0, 255, 0),    # Green
            (255, 0, 0),    # Blue
            (0, 0, 255),    # Red
            (255, 255, 0),  # Cyan
            (255, 0, 255),  # Magenta
        ]
        
        for i, match in enumerate(matches[:10]):
            color = colors[i % len(colors)]
            center_x, center_y = match['center']
            pt_x, pt_y = match['topleft']
            t_w, t_h = match['size']
            
            # Draw rectangle
            cv2.rectangle(frame_vis, (pt_x, pt_y), (pt_x + t_w, pt_y + t_h), color, 2)
            
            # Draw center circle
            cv2.circle(frame_vis, (center_x, center_y), 5, color, -1)
            
            # Draw label
            label = f"{i+1}: {match['filename'][:15]} ({match['score']:.3f})"
            cv2.putText(frame_vis, label, (5, 20 + i*15), cv2.FONT_HERSHEY_SIMPLEX, 0.3, color, 1)
        
        cv2.imshow("All Inventory Matches", frame_vis)
        print("\nPress any key to close visualization...")
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    
    def test_dead_fish_detection(self, inventory_frame: np.ndarray, check_x: int, check_y: int, radius: int = 10):
        """Test if an item still exists at a specific position"""
        print(f"\n" + "="*60)
        print(f"DEAD FISH DETECTION TEST")
        print(f"Position: ({check_x}, {check_y}), Radius: {radius}")
        print("="*60)
        
        inventory_gray = cv2.cvtColor(inventory_frame, cv2.COLOR_BGR2GRAY)
        inv_h, inv_w = inventory_gray.shape
        
        match_template = cv2.matchTemplate
        where = np.where
        TM_CCOEFF_NORMED = cv2.TM_CCOEFF_NORMED
        
        found = False
        for filename, (template, half_w, half_h, img_path) in self.template_cache.items():
            t_h, t_w = template.shape
            
            if t_h > inv_h or t_w > inv_w:
                continue
            
            try:
                result = match_template(inventory_gray, template, TM_CCOEFF_NORMED)
                locations = where(result >= 0.8)
                
                if locations[0].size == 0:
                    continue
                
                for pt_y, pt_x in zip(locations[0], locations[1]):
                    center_x = pt_x + half_w
                    center_y = pt_y + half_h
                    score = result[pt_y, pt_x]
                    
                    if abs(center_x - check_x) < radius and abs(center_y - check_y) < radius:
                        print(f"\n✓ ITEM FOUND AT POSITION!")
                        print(f"  Filename: {filename}")
                        print(f"  Center: ({center_x}, {center_y})")
                        print(f"  Score: {score:.4f}")
                        print(f"  Distance from check point: ({abs(center_x - check_x)}, {abs(center_y - check_y)})")
                        found = True
            except:
                continue
        
        if not found:
            print(f"\n✗ No item found at position ({check_x}, {check_y})")
        
        return found

def select_window():
    """Let user select a window from available windows"""
    windows = get_all_windows()
    
    if not windows:
        print("\n✗ No windows found. Make sure your game is running.")
        return None
    
    print("\n" + "="*60)
    print("AVAILABLE WINDOWS")
    print("="*60)
    for i, (title, win) in enumerate(windows):
        try:
            print(f"{i+1}. {title}")
            print(f"   Position: ({win.left}, {win.top}), Size: {win.width}x{win.height}")
        except:
            print(f"{i+1}. {title}")
    
    while True:
        try:
            choice = input("\nSelect window number (or 'q' to cancel): ").strip()
            if choice.lower() == 'q':
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(windows):
                title, win = windows[idx]
                print(f"\n✓ Selected: {title}")
                return (win.left, win.top, win.width, win.height)
            else:
                print(f"Invalid selection. Enter 1-{len(windows)}")
        except ValueError:
            print(f"Invalid input. Enter a number or 'q'")

def realtime_monitor(window_pos):
    """Real-time inventory monitoring with live window display"""
    if window_pos is None:
        return
    
    debugger = InventoryDebugger()
    
    print("\n" + "="*60)
    print("REAL-TIME INVENTORY MONITOR")
    print("="*60)
    print("Controls (in the image window):")
    print("  'c' - Capture snapshot")
    print("  's' - Show templates")
    print("  'd' - Test dead fish detection")
    print("  'q' - Quit real-time monitoring\n")
    
    capture_counter = 0
    
    try:
        while True:
            # Get latest window position (in case it moved)
            try:
                if gw and hasattr(gw, 'getWindowsWithTitle'):
                    # Try to follow window if it moves
                    pass
            except:
                pass
            
            # Capture inventory
            try:
                inventory = debugger.capture_inventory_area(window_pos)
                capture_counter += 1
                
                # Analyze items
                inventory_gray = cv2.cvtColor(inventory, cv2.COLOR_BGR2GRAY)
                inv_h, inv_w = inventory_gray.shape
                
                matches = []
                for filename, (template, half_w, half_h, img_path) in debugger.template_cache.items():
                    t_h, t_w = template.shape
                    
                    if t_h > inv_h or t_w > inv_w:
                        continue
                    
                    try:
                        result = cv2.matchTemplate(inventory_gray, template, cv2.TM_CCOEFF_NORMED)
                        locations = np.where(result >= 0.8)
                        
                        if locations[0].size == 0:
                            continue
                        
                        for pt_y, pt_x in zip(locations[0], locations[1]):
                            score = result[pt_y, pt_x]
                            center_x = pt_x + half_w
                            center_y = pt_y + half_h
                            
                            matches.append({
                                'filename': filename,
                                'center': (center_x, center_y),
                                'score': float(score),
                                'topleft': (pt_x, pt_y),
                                'size': (t_w, t_h)
                            })
                    except:
                        continue
                
                matches.sort(key=lambda x: x['score'], reverse=True)
                
                # Create visualization frame
                display_frame = inventory.copy()
                
                # Draw all matches on the frame
                colors = [
                    (0, 255, 0),    # Green
                    (255, 0, 0),    # Blue
                    (0, 0, 255),    # Red
                    (255, 255, 0),  # Cyan
                    (255, 0, 255),  # Magenta
                ]
                
                for i, match in enumerate(matches[:10]):
                    color = colors[i % len(colors)]
                    center_x, center_y = match['center']
                    pt_x, pt_y = match['topleft']
                    t_w, t_h = match['size']
                    
                    # Draw rectangle
                    cv2.rectangle(display_frame, (pt_x, pt_y), (pt_x + t_w, pt_y + t_h), color, 2)
                    
                    # Draw center circle
                    cv2.circle(display_frame, (center_x, center_y), 4, color, -1)
                    
                    # Draw label
                    label = f"{i+1}: {match['filename'][:12]} ({match['score']:.3f})"
                    cv2.putText(display_frame, label, (5, 18 + i*12), cv2.FONT_HERSHEY_SIMPLEX, 0.3, color, 1)
                
                # Draw header info
                info_text = f"Frame: {capture_counter} | Items: {len(matches)} | Size: {inv_w}x{inv_h}"
                cv2.putText(display_frame, info_text, (5, display_frame.shape[0] - 5), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
                
                # Display the frame
                cv2.imshow("Inventory Monitor (Press c/s/d/q)", display_frame)
                
                # Print status
                print(f"\r[Frame {capture_counter}] Items: {len(matches)} | ", end='', flush=True)
                
                if matches:
                    print(f"Top: {matches[0]['filename'][:20]} ({matches[0]['score']:.3f})", end='', flush=True)
                else:
                    print("No items detected", end='', flush=True)
                
                # Check for keyboard input
                key = cv2.waitKey(100) & 0xFF
                
                if key == ord('c'):  # Capture
                    print(f"\n\n[CAPTURE #{capture_counter}]")
                    print(f"Inventory Size: {inventory.shape}")
                    print(f"Items Found: {len(matches)}")
                    for i, match in enumerate(matches[:5]):
                        print(f"  {i+1}. {match['filename']}")
                        print(f"     Center: {match['center']}, Score: {match['score']:.4f}")
                    
                    # Save capture for inspection
                    output_path = os.path.join(os.path.dirname(__file__), f"capture_{capture_counter}.png")
                    cv2.imwrite(output_path, inventory)
                    print(f"  Saved to: {output_path}\n")
                
                elif key == ord('s'):  # Show templates
                    print(f"\n\nLoaded Templates ({len(debugger.template_cache)}):")
                    for i, (filename, (template, half_w, half_h, img_path)) in enumerate(debugger.template_cache.items()):
                        h, w = template.shape
                        print(f"  {i+1}. {filename} ({w}x{h})")
                    print()
                
                elif key == ord('d'):  # Dead fish test
                    print("\n\nEnter position to check (x y):")
                    pos_input = input("Position: ").strip().split()
                    try:
                        check_x, check_y = int(pos_input[0]), int(pos_input[1])
                        found = debugger.test_dead_fish_detection(inventory, check_x, check_y)
                    except ValueError:
                        print("Invalid input")
                    print()
                
                elif key == ord('q'):  # Quit
                    print("\n\nExiting real-time monitor...")
                    break
                
            except Exception as e:
                print(f"\rError: {e}        ", flush=True)
                time.sleep(0.1)
    
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    finally:
        cv2.destroyAllWindows()

def main():
    print("\n" + "="*60)
    print("FISHING BOT - REAL-TIME INVENTORY DEBUG")
    print("="*60)
    
    # Select window
    window_pos = select_window()
    if window_pos is None:
        print("\nNo window selected. Exiting.")
        return
    
    # Start real-time monitoring
    realtime_monitor(window_pos)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()

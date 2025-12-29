"""
Window Manager and Game Region classes for the Fishing Bot
"""

import time
from dataclasses import dataclass
from typing import List, Tuple

import pygetwindow as gw


class WindowManager:
    """Manages window detection and focus for the bot"""
    
    def __init__(self):
        self.selected_window = None
    
    @staticmethod
    def get_all_windows() -> List[Tuple[str, gw.Win32Window]]:
        """Gets all visible windows on Windows 10+. Returns list of (display_name, window)"""
        windows = []
        priority_windows = []  # Windows with 'mt2', 'metin2', 'metin 2', or words with '2'
        
        try:
            # Use getAllWindows() directly - more reliable on Windows 10 than iterating processes
            all_wins = gw.getAllWindows()
            
            for win in all_wins:
                try:
                    # Skip empty titles
                    if not win.title or not win.title.strip():
                        continue
                    
                    # Check if window is visible (use 'visible' property, not 'isVisible')
                    if not getattr(win, 'visible', True):
                        continue
                    
                    display_name = win.title
                    
                    # Check if window matches Metin2 patterns (prioritize these)
                    title_lower = win.title.lower()
                    if any(pattern in title_lower for pattern in ['mt2', 'metin2', 'metin 2']) or \
                       any(word.endswith('2') for word in title_lower.split()):
                        priority_windows.append((display_name, win))
                    else:
                        windows.append((display_name, win))
                except Exception:
                    pass
        except Exception as e:
            print(f"Error getting windows: {e}")
        
        # Combine all windows (prioritize Metin2 windows)
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
    
    def activate_window(self, force_activate: bool = False):
        """Activates and brings the selected window to focus"""
        if not self.selected_window:
            return
        try:
            # Check if window is already active (skip activation for speed)
            if not force_activate:
                try:
                    active_win = gw.getActiveWindow()
                    if active_win and active_win._hWnd == self.selected_window._hWnd:
                        return  # Already active, skip
                except:
                    pass
            
            # Try to activate the window (single attempt for speed)
            try:
                # Restore window if minimized
                if self.selected_window.isMinimized:
                    self.selected_window.restore()
                    time.sleep(0.05)
                
                # Activate the window
                self.selected_window.activate()
                time.sleep(0.025)  # Minimal delay
            except Exception:
                # Retry once on failure
                try:
                    time.sleep(0.05)
                    self.selected_window.activate()
                    time.sleep(0.025)
                except:
                    pass
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


@dataclass
class GameRegion:
    """Stores the coordinates of the game window region (relative to selected window)"""
    left: int
    top: int
    width: int
    height: int

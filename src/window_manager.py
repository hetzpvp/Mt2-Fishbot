"""
Window Manager and Game Region classes for the Fishing Bot
"""

import time
from dataclasses import dataclass
from typing import List, Tuple

import pygetwindow as gw
from utils import DEBUG_PRINTS


class WindowManager:
    """Manages window detection and focus for the bot"""
    
    def __init__(self):
        self.selected_window = None
        self._rect_cache = None
        self._rect_cache_time = 0.0
    
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
            if DEBUG_PRINTS:
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
            if DEBUG_PRINTS:
                print(f"Error activating window: {e}")
    
    def get_window_rect(self) -> Tuple[int, int, int, int]:
        """Gets the selected window's position and size (left, top, width, height).
        Cached for 50ms — pygetwindow does a Win32 GetWindowRect on every attribute
        access, so without caching this costs 4 syscalls per invocation and is
        hit on every detection cycle."""
        if not self.selected_window:
            return (0, 0, 0, 0)

        now = time.perf_counter()
        if now - self._rect_cache_time < 0.050 and self._rect_cache is not None:
            return self._rect_cache

        try:
            left = self.selected_window.left
            top = self.selected_window.top
            width = self.selected_window.width
            height = self.selected_window.height
            rect = (left, top, width, height)
            self._rect_cache = rect
            self._rect_cache_time = now
            return rect
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Error getting window rect: {e}")
            return self._rect_cache if self._rect_cache is not None else (0, 0, 0, 0)

    def invalidate_rect_cache(self):
        """Forces the next get_window_rect() to refetch (e.g. after move/resize)."""
        self._rect_cache = None
        self._rect_cache_time = 0.0


@dataclass
class GameRegion:
    """Stores the coordinates of the game window region (relative to selected window)"""
    left: int
    top: int
    width: int
    height: int

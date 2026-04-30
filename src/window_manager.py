"""
Window Manager and Game Region classes for the Fishing Bot
"""

import ctypes
import time
from dataclasses import dataclass
from typing import List, Tuple

import pygetwindow as gw
from utils import DEBUG_PRINTS

_user32 = ctypes.windll.user32
_SW_RESTORE = 9


class WindowManager:
    """Manages window detection and focus for the bot"""
    
    def __init__(self):
        self.selected_window = None
        self._rect_cache = None
        self._rect_cache_time = 0.0

    def is_window_available(self) -> bool:
        """Returns False when the selected window handle no longer exists."""
        if not self.selected_window:
            return False

        try:
            hwnd = getattr(self.selected_window, "_hWnd", None)
            if not hwnd or not _user32.IsWindow(hwnd):
                self.invalidate_rect_cache()
                return False
            return True
        except Exception:
            self.invalidate_rect_cache()
            return False
    
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
        """Activates and brings the selected window to focus.
        Retries until GetForegroundWindow confirms our HWND is active, so that
        callers holding input_lock can be sure the click lands on the right window.
        Uses ctypes directly instead of pygetwindow.activate() to avoid a GIL
        corruption bug in pygetwindow 0.0.9 on Python 3.13+."""
        if not self.selected_window:
            return
        try:
            target_hwnd = self.selected_window._hWnd
            if not target_hwnd or not _user32.IsWindow(target_hwnd):
                self.invalidate_rect_cache()
                return

            # Fast path: already active — skip everything.
            if not force_activate:
                if _user32.GetForegroundWindow() == target_hwnd:
                    return

            # Restore if minimized before trying to activate.
            try:
                if _user32.IsIconic(target_hwnd):
                    _user32.ShowWindow(target_hwnd, _SW_RESTORE)
                    time.sleep(0.05)
            except Exception:
                pass

            # Retry loop: call SetForegroundWindow and verify via GetForegroundWindow.
            # Windows can silently ignore SetForegroundWindow when the calling
            # thread doesn't own the foreground, causing the click to land on
            # whatever window is currently in front (typically the other bot's
            # window). Three attempts with 30 ms gaps covers all real-world cases.
            for attempt in range(3):
                try:
                    _user32.SetForegroundWindow(target_hwnd)
                except Exception:
                    pass

                time.sleep(0.030)  # Let OS process the activation request

                if _user32.GetForegroundWindow() == target_hwnd:
                    return  # Confirmed active — safe to click

            # Fell through all attempts — log once and continue (best effort).
            if DEBUG_PRINTS:
                print(f"Warning: could not confirm window activation after 3 attempts")

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

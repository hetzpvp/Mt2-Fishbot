"""
Window Manager and Game Region classes for the Fishing Bot

The policy here (retries, confirmation, caching, ordering) is shared by all
operating systems; the OS calls themselves live in platforms/ (win32.py on
Windows, x11.py on Linux).
"""

import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import diag
import platforms
from utils import DEBUG_PRINTS

# How long to wait for the OS to confirm a foreground switch (per attempt),
# and how long to let the game process WM_ACTIVATE after a real switch.
_ACTIVATE_CONFIRM_TIMEOUT = 0.030
_ACTIVATE_SETTLE = 0.012


class WindowManager:
    """Manages window detection and focus for the bot"""

    def __init__(self):
        self.selected_window = None
        self._rect_cache = None
        self._rect_cache_time = 0.0
        # perf_counter time of the last real focus switch to this window
        # (used by screen_capture to know when the desktop shows it again).
        self.last_switch_time = 0.0
        self.diag_tag = None        # window tag in the diagnostic log ("W1")
        self._diag_last_rect = None

    def is_window_available(self) -> bool:
        """Returns False when the selected window handle no longer exists."""
        if not self.selected_window:
            return False

        try:
            hwnd = self._hwnd()
            if not hwnd or not platforms.native.is_window(hwnd):
                self.invalidate_rect_cache()
                diag.log("STATE", f"window {hwnd!r} no longer exists", tag=self.diag_tag)
                return False
            return True
        except Exception:
            diag.exc("is_window_available", tag=self.diag_tag)
            self.invalidate_rect_cache()
            return False

    @staticmethod
    def get_all_windows() -> List[Tuple[str, object]]:
        """Gets all visible windows. Returns list of (display_name, window)"""
        return WindowManager._order_windows(WindowManager._raw_windows())

    @staticmethod
    def _raw_windows() -> list:
        try:
            return list(platforms.native.list_windows()) if platforms.native else []
        except Exception as e:
            diag.exc("list_windows")
            if DEBUG_PRINTS:
                print(f"Error getting windows: {e}")
            return []

    @staticmethod
    def _order_windows(all_wins) -> List[Tuple[str, object]]:
        """Filters, orders and names the windows (pure, OS independent)."""
        windows = []
        priority_windows = []  # Windows with 'mt2', 'metin2', 'metin 2', or words with '2'

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

        # The OS lists windows in z-order, which changes every time a window is
        # activated. Sort by (title, handle) so that "Metin2 (1)", "Metin2 (2)"
        # keep pointing at the same clients between refreshes — otherwise the
        # window picked in the GUI and the one the bot attaches to can differ.
        def _stable_key(item):
            return (item[0].lower(), platforms.window_handle(item[1]) or 0)

        priority_windows.sort(key=_stable_key)
        windows.sort(key=_stable_key)

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

    def _hwnd(self):
        return platforms.window_handle(self.selected_window)

    def is_foreground(self) -> bool:
        hwnd = self._hwnd()
        if not hwnd or platforms.native is None:
            return False
        try:
            return platforms.native.get_foreground() == hwnd
        except Exception:
            return False

    def _wait_foreground(self, hwnd, timeout: float) -> bool:
        """Polls the foreground window until it is ``hwnd`` or ``timeout`` elapses."""
        deadline = time.perf_counter() + timeout
        while True:
            if platforms.native.get_foreground() == hwnd:
                return True
            if time.perf_counter() >= deadline:
                return False
            time.sleep(0.001)

    def activate_window(self, force_activate: bool = False) -> bool:
        """Brings the selected window to the foreground and confirms it.

        Returns True when the OS reports our window in front, so callers
        holding input_lock know the next click/key lands on the right window.

        If the window is already in front this returns immediately — no
        activation request and no sleep. ``force_activate`` is kept for
        API compatibility; the foreground check is authoritative either way
        (it queries the OS, it is not a cached guess).

        On Windows this uses ctypes directly instead of pygetwindow.activate()
        to avoid a GIL corruption bug in pygetwindow 0.0.9 on Python 3.13+."""
        target_hwnd = self._hwnd()
        if not target_hwnd or platforms.native is None:
            diag.log("FOCUS", f"activate {target_hwnd!r}: no window handle / no OS layer -> False", tag=self.diag_tag)
            return False
        native = platforms.native
        try:
            if not native.is_window(target_hwnd):
                self.invalidate_rect_cache()
                diag.log("FOCUS", f"activate {target_hwnd!r}: window no longer exists -> False", tag=self.diag_tag)
                return False

            fg = native.get_foreground()
            if fg == target_hwnd:
                if diag.ENABLED:
                    diag.trace("FOCUS", f"activate {target_hwnd!r}: already in front", tag=self.diag_tag)
                return True
            if diag.ENABLED:
                diag.log("FOCUS", f"activate {target_hwnd!r} (force={force_activate}): foreground is {fg!r}",
                         tag=self.diag_tag)

            # Restore if minimized before trying to activate.
            try:
                if native.is_minimized(target_hwnd):
                    diag.log("FOCUS", "window is minimized: restoring", tag=self.diag_tag)
                    native.restore(target_hwnd)
                    self.invalidate_rect_cache()
                    time.sleep(0.05)
            except Exception:
                diag.exc("restore before activate", tag=self.diag_tag)

            # The OS can silently ignore a plain activation request (Windows
            # when the calling thread doesn't own the foreground). First
            # attempt is the plain call; retries use the stronger variant.
            for attempt in range(3):
                try:
                    if attempt == 0:
                        native.set_foreground(target_hwnd)
                    else:
                        native.force_foreground(target_hwnd)
                except Exception:
                    diag.exc(f"activation request attempt {attempt + 1}", tag=self.diag_tag)

                if self._wait_foreground(target_hwnd, _ACTIVATE_CONFIRM_TIMEOUT):
                    # Real switch happened: give the game a moment to process
                    # the activation before input arrives.
                    time.sleep(_ACTIVATE_SETTLE)
                    self.last_switch_time = time.perf_counter()
                    diag.log("FOCUS", f"activate {target_hwnd!r}: confirmed in front on attempt {attempt + 1} "
                                      f"({'plain' if attempt == 0 else 'forced'} request)", tag=self.diag_tag)
                    return True
                if diag.ENABLED:
                    diag.log("FOCUS", f"attempt {attempt + 1} ({'plain' if attempt == 0 else 'forced'}): "
                                      f"not confirmed within {_ACTIVATE_CONFIRM_TIMEOUT * 1000:.0f} ms, "
                                      f"foreground={native.get_foreground()!r}", tag=self.diag_tag)

            diag.log("FOCUS", f"activate {target_hwnd!r}: FAILED after 3 attempts", tag=self.diag_tag)
            if DEBUG_PRINTS:
                print("Warning: could not confirm window activation after 3 attempts")
            return False

        except Exception as e:
            diag.exc("activate_window", tag=self.diag_tag)
            if DEBUG_PRINTS:
                print(f"Error activating window: {e}")
            return False

    def get_window_rect(self) -> Tuple[int, int, int, int]:
        """Gets the selected window's position and size (left, top, width, height).
        Cached for 50ms — this is hit on every detection cycle."""
        if not self.selected_window:
            return (0, 0, 0, 0)

        now = time.perf_counter()
        if now - self._rect_cache_time < 0.050 and self._rect_cache is not None:
            return self._rect_cache

        try:
            rect = self._query_rect()
            self._rect_cache = rect
            self._rect_cache_time = now
            if diag.ENABLED and rect != self._diag_last_rect:
                diag.log("CAPTURE", f"window rect changed: {self._diag_last_rect} -> {rect} "
                                    f"(left, top, width, height)", tag=self.diag_tag)
                self._diag_last_rect = rect
            return rect
        except Exception as e:
            diag.exc("get_window_rect", tag=self.diag_tag)
            if DEBUG_PRINTS:
                print(f"Error getting window rect: {e}")
            return self._rect_cache if self._rect_cache is not None else (0, 0, 0, 0)

    def _query_rect(self) -> Tuple[int, int, int, int]:
        """One OS call for the whole rectangle (pygetwindow issues one per attribute)."""
        hwnd = self._hwnd()
        if hwnd and platforms.native is not None:
            rect = platforms.native.get_window_rect(hwnd)
            if rect is not None:
                return rect
        win = self.selected_window
        return (win.left, win.top, win.width, win.height)

    def invalidate_rect_cache(self):
        """Forces the next get_window_rect() to refetch (e.g. after move/resize)."""
        self._rect_cache = None
        self._rect_cache_time = 0.0


def window_size_mismatch(sizes: List[Tuple[str, Tuple[int, int]]]) -> Optional[str]:
    """Checks that every selected game window has the same size.

    All configured positions (inventory tabs, confirm button, jigsaw grid)
    are pixel offsets from the window corner, captured on one window and
    used for all, so a client with another resolution clicks beside them.
    ``sizes`` is [(label, (width, height)), ...]. Returns None when they all
    match, otherwise a message listing each window's size."""
    distinct = {tuple(size) for _, size in sizes}
    if len(distinct) <= 1:
        return None
    lines = [f"  {label}: {size[0]} x {size[1]}" for label, size in sizes]
    return ("The selected game windows do not all have the same size:\n\n"
            + "\n".join(lines)
            + "\n\nAll positions (inventory tabs, confirm button, jigsaw grid) are "
              "measured from one window and used for every window, so every client "
              "must run at the same resolution and window size. Set them all to the "
              "same resolution (and restore any minimized window), then start again.")


@dataclass
class GameRegion:
    """Stores the coordinates of the game window region (relative to selected window)"""
    left: int
    top: int
    width: int
    height: int

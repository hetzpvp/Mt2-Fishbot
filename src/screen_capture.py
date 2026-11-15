"""
Game-window capture shared by the fishing and jigsaw bots.

Two ways to get pixels:

- ``screen``: copy a rectangle of the desktop (mss). Fast, but it sees
  whatever is on top — a game window that is covered by another window
  cannot be read, so windows must not overlap.

- ``window``: ask the OS for the game window's own pixels
  (``platforms.native.render_window``: ``PrintWindow`` + ``PW_RENDERFULLCONTENT``
  on Windows, the X11 window image on Linux). Works while the window is
  covered by others (not while minimized), so game windows may be stacked.

``auto`` (default) is a hybrid. The window render is first proven: while the
game window is in front, its output is compared with the screen copy and it
is trusted only when the two agree — clients whose anti-capture or renderer
makes PrintWindow return black or stale frames stay on ``screen``. Once
trusted, the focused window is still read with the fast screen copy and only
windows that are *not* in front (and may be covered) use the window render.
A single-window setup therefore keeps its original capture speed.

Where the desktop cannot be copied at all (Linux under XWayland, i.e. any
Wayland desktop) the window render is the only source: ``auto`` and
``screen`` both become ``window``.

All coordinates are relative to the window rectangle reported by
WindowManager.get_window_rect, which is the frame of reference the rest of
the bot already uses.
"""

import time
from typing import Callable, Optional

import numpy as np
from mss import mss

import platforms

MODE_AUTO = "auto"
MODE_SCREEN = "screen"
MODE_WINDOW = "window"


def print_window(hwnd, width: int, height: int) -> Optional[np.ndarray]:
    """The native window render for this OS: (height, width, 4) BGRA or None."""
    native = platforms.native
    if native is None:
        return None
    return native.render_window(hwnd, width, height)


def frames_agree(a: np.ndarray, b: np.ndarray, cells: int = 8, tolerance: float = 12.0,
                 min_share: float = 0.6) -> bool:
    """True when two captures of the same window show the same picture.

    Compared cell by cell on a coarse grid so a partly covered window (the
    bot's own GUI on top of a corner, say) still counts as agreeing, while a
    black / frozen / shifted frame does not. A flat frame never agrees —
    a real game view is not one solid color."""
    if a is None or b is None or a.shape[:2] != b.shape[:2] or a.size == 0:
        return False
    h, w = a.shape[:2]
    if h < cells * 4 or w < cells * 4:
        return False
    if float(a[:, :, :3].std()) < 2.0:
        return False

    a16 = a[:, :, :3].astype(np.int16)
    b16 = b[:, :, :3].astype(np.int16)
    diff = np.abs(a16 - b16).mean(axis=2)
    ys = np.linspace(0, h, cells + 1, dtype=int)
    xs = np.linspace(0, w, cells + 1, dtype=int)
    good = 0
    for i in range(cells):
        for j in range(cells):
            if float(diff[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].mean()) <= tolerance:
                good += 1
    return good >= cells * cells * min_share


class WindowCapture:
    """Per-bot capture object. Not thread-safe: each bot thread owns one."""

    _FRAME_REUSE = 0.003       # seconds a window render may be reused for another crop
    _VERIFY_ATTEMPTS = 12      # auto mode: checks before settling on screen capture
    _FOREGROUND_SETTLE = 0.10  # seconds after a focus switch before the screen copy is trusted

    def __init__(self, window_manager, mode: str = MODE_AUTO,
                 log: Optional[Callable[[str], None]] = None,
                 window_grabber: Optional[Callable] = None):
        self.window_manager = window_manager
        self._log = log or (lambda _msg: None)
        self._print_window = window_grabber or print_window
        mode = str(mode or MODE_AUTO).lower()
        if mode not in (MODE_AUTO, MODE_SCREEN, MODE_WINDOW):
            mode = MODE_AUTO
        if window_grabber is None:
            native = platforms.native
            if native is None or not native.available():
                mode = MODE_SCREEN  # no window render without the OS API
            elif mode != MODE_WINDOW and not native.screen_capture_reliable():
                # XWayland: the desktop can't be copied, the game's own
                # window is the only picture there is.
                self._log("Capture: desktop copy not available here — reading the game window directly")
                mode = MODE_WINDOW
        self.requested = mode
        # Whether the window render may be used. "window" trusts it blindly;
        # "auto" starts on the safe path until verify() has proven it.
        self.window_trusted = mode == MODE_WINDOW
        self._verify_left = self._VERIFY_ATTEMPTS if mode == MODE_AUTO else 0
        self._sct = None
        self._full = None          # last full-window render (BGRA)
        self._full_time = 0.0
        self._last_error_log = 0.0

    # -------------------------------------------------------------- public API

    def grab(self, rel_left: int, rel_top: int, width: int, height: int) -> Optional[np.ndarray]:
        """Returns a contiguous BGR array of the given window-relative rectangle,
        or None when nothing could be captured."""
        rel_left, rel_top, width, height = int(rel_left), int(rel_top), int(width), int(height)
        if width <= 0 or height <= 0:
            return None

        if self.window_trusted and (self.requested == MODE_WINDOW or not self._screen_is_reliable()):
            frame = self._grab_window(rel_left, rel_top, width, height)
            if frame is not None or self.requested == MODE_WINDOW:
                return frame
            # auto: render failed (e.g. minimized) — the screen copy is the best we have

        return self._grab_screen(rel_left, rel_top, width, height)

    @property
    def needs_verification(self) -> bool:
        return self._verify_left > 0

    def verify(self) -> bool:
        """Auto mode: decide whether the window render can be trusted.

        Call right after the game window was brought to the front (so the
        screen shows it): the render is adopted only if it shows what the
        screen shows. Returns True once trusted."""
        if self.window_trusted or self._verify_left <= 0:
            return self.window_trusted
        self._verify_left -= 1
        try:
            _, _, win_w, win_h = self.window_manager.get_window_rect()
            self._full = None
            rendered = self._render_window()
            screen = self._grab_screen(0, 0, win_w, win_h) if rendered is not None else None
            if frames_agree(rendered, screen):
                self.window_trusted = True
                self._verify_left = 0
                self._log("Capture: per-window render verified — game windows may overlap")
                return True
        except Exception as e:
            self._throttled_log(f"Capture check error: {e}")
        if self._verify_left <= 0:
            self._log("Capture: per-window render not usable on this client — "
                      "using screen capture, keep game windows uncovered")
        return False

    def _screen_is_reliable(self) -> bool:
        """The screen shows this window when it is in front and has been for a
        moment (right after a focus switch the desktop may still show the
        window that used to cover it)."""
        wm = self.window_manager
        try:
            if not wm.is_foreground():
                return False
            return time.perf_counter() - getattr(wm, "last_switch_time", 0.0) >= self._FOREGROUND_SETTLE
        except Exception:
            return True

    def invalidate(self) -> None:
        """Drops the cached window render (call after sending input when the
        very next frame must reflect it)."""
        self._full = None

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
        self._full = None

    # ---------------------------------------------------------------- backends

    def _grab_screen(self, rel_left, rel_top, width, height) -> Optional[np.ndarray]:
        try:
            win_left, win_top, _, _ = self.window_manager.get_window_rect()
            if self._sct is None:
                self._sct = mss()
            shot = self._sct.grab({"left": win_left + rel_left, "top": win_top + rel_top,
                                   "width": width, "height": height})
            # BGRA buffer sliced straight to contiguous BGR — no cvtColor pass.
            return np.ascontiguousarray(np.asarray(shot, dtype=np.uint8)[:, :, :3])
        except Exception as e:
            # A capture handle that starts failing (display mode change, RDP
            # reconnect, GPU reset) does not recover: drop it, re-create next call.
            try:
                if self._sct is not None:
                    self._sct.close()
            except Exception:
                pass
            self._sct = None
            self._throttled_log(f"Screenshot error: {e}")
            return None

    def _render_window(self) -> Optional[np.ndarray]:
        """Full-window BGRA render, reused for a few ms so several crops taken
        in one step cost a single PrintWindow."""
        now = time.perf_counter()
        if self._full is not None and now - self._full_time <= self._FRAME_REUSE:
            return self._full
        try:
            hwnd = platforms.window_handle(self.window_manager.selected_window)
            _, _, win_w, win_h = self.window_manager.get_window_rect()
            full = self._print_window(hwnd, win_w, win_h) if hwnd else None
        except Exception as e:
            self._throttled_log(f"Window capture error: {e}")
            full = None
        self._full = full
        self._full_time = time.perf_counter()
        return full

    def _grab_window(self, rel_left, rel_top, width, height) -> Optional[np.ndarray]:
        full = self._render_window()
        if full is None:
            return None
        fh, fw = full.shape[:2]
        x1, y1 = max(0, rel_left), max(0, rel_top)
        x2, y2 = min(fw, rel_left + width), min(fh, rel_top + height)
        if x2 <= x1 or y2 <= y1:
            return None
        if (x2 - x1, y2 - y1) == (width, height):
            return np.ascontiguousarray(full[y1:y2, x1:x2, :3])
        # Requested rectangle hangs over the window edge: pad with black so the
        # caller still gets the size it asked for (same as a screen grab would).
        out = np.zeros((height, width, 3), dtype=np.uint8)
        out[y1 - rel_top:y2 - rel_top, x1 - rel_left:x2 - rel_left] = full[y1:y2, x1:x2, :3]
        return out

    def _throttled_log(self, message: str) -> None:
        now = time.perf_counter()
        if now - self._last_error_log > 2.0:
            self._last_error_log = now
            self._log(message)

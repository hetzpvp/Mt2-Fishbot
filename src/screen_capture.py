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

import threading
import time
from typing import Callable, List, Optional

import cv2
import numpy as np
from mss import mss

import diag
import platforms

# One OpenCV worker per calling thread. Every bot runs on its own thread and
# hands OpenCV frame-sized images (a ~260x200 panel, a 40x16 dialog patch);
# farming those out to OpenCV's pool only costs CPU. Measured on 12 cores:
# one bot polling every 4 ms used 155% CPU with the pool and 25% without it,
# eight bots at 60 fps 245% vs 51%, at the same frame rate. The largest calls
# (item identification, the jigsaw grid read) were no slower single-threaded.
# Set here because every bot imports this module, whatever the entry point.
cv2.setNumThreads(1)

MODE_AUTO = "auto"
MODE_SCREEN = "screen"
MODE_WINDOW = "window"


def frame_usable(frame) -> bool:
    """False for a capture that shows nothing: missing, empty, black or one
    flat color (a failed render, a minimized window, a capture handle that
    died). Such a frame says nothing about the game, so it must not be read
    as "no minigame" or "panel closed". Cheap: looks at a subsample of
    about 2500 pixels."""
    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0 or frame.ndim < 2:
        return False
    h, w = frame.shape[:2]
    if h * w < 64:
        return True          # too small to judge; a real crop that size is not a failure sign
    step = max(1, int((h * w / 2500) ** 0.5))
    sub = frame[::step, ::step]
    hi = int(sub.max())
    if hi < 8:
        return False         # black
    return hi - int(sub.min()) > 3   # one flat color (a failed render), not a dark scene


def rects_overlap(a, b) -> bool:
    """Two (left, top, width, height) rectangles share at least one pixel."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


# Captures of the bots that are running now (one per game window), so a
# capture that has to read the screen can tell whether another game window
# lies on top of the area it reads.
_active_lock = threading.Lock()
_active: List["WindowCapture"] = []


def register_active(capture: "WindowCapture") -> None:
    with _active_lock:
        if capture not in _active:
            _active.append(capture)


def unregister_active(capture: "WindowCapture") -> None:
    with _active_lock:
        if capture in _active:
            _active.remove(capture)


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


def frames_agree_detail(a, b, cells: int = 8, tolerance: float = 12.0) -> str:
    """Why frames_agree said what it said (diagnostics only, same rules)."""
    if a is None or b is None:
        return f"render={'None' if a is None else a.shape} screen={'None' if b is None else b.shape}"
    if a.shape[:2] != b.shape[:2]:
        return f"size differs: render {a.shape[:2]} vs screen {b.shape[:2]}"
    h, w = a.shape[:2]
    if h < cells * 4 or w < cells * 4:
        return f"too small ({w}x{h})"
    std = float(a[:, :, :3].std())
    diff = np.abs(a[:, :, :3].astype(np.int16) - b[:, :, :3].astype(np.int16)).mean(axis=2)
    ys = np.linspace(0, h, cells + 1, dtype=int)
    xs = np.linspace(0, w, cells + 1, dtype=int)
    good = sum(1 for i in range(cells) for j in range(cells)
               if float(diff[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].mean()) <= tolerance)
    return (f"render std={std:.1f} (flat if <2), mean abs diff={float(diff.mean()):.1f}, "
            f"cells agreeing={good}/{cells * cells} (need {int(cells * cells * 0.6)}), "
            f"render: {diag.array_summary(a[:, :, :3])}, screen: {diag.array_summary(b[:, :, :3])}")


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
        self.diag_tag = getattr(window_manager, "diag_tag", None)
        self._last_method = None
        self._diag_anomaly_time = 0.0
        requested_raw = mode
        mode = str(mode or MODE_AUTO).lower()
        if mode not in (MODE_AUTO, MODE_SCREEN, MODE_WINDOW):
            diag.log("CAPTURE", f"unknown capture_backend {requested_raw!r}: using auto", tag=self.diag_tag)
            mode = MODE_AUTO
        if window_grabber is None:
            native = platforms.native
            if native is None or not native.available():
                diag.log("CAPTURE", f"no OS window API (native={getattr(native, '__name__', None)}): "
                                    f"screen capture only", tag=self.diag_tag)
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
        diag.log("CAPTURE", f"capture mode requested={requested_raw!r} using={mode!r} "
                            f"window_render_trusted={self.window_trusted} verify_attempts={self._verify_left}",
                 tag=self.diag_tag)

    # -------------------------------------------------------------- public API

    def grab(self, rel_left: int, rel_top: int, width: int, height: int) -> Optional[np.ndarray]:
        """Returns a contiguous BGR array of the given window-relative rectangle,
        or None when nothing could be captured."""
        rel_left, rel_top, width, height = int(rel_left), int(rel_top), int(width), int(height)
        if width <= 0 or height <= 0:
            diag.log("CAPTURE", f"grab of empty rect ({rel_left},{rel_top},{width},{height}) -> None",
                     tag=self.diag_tag)
            return None

        if self.window_trusted and (self.requested == MODE_WINDOW or not self._screen_is_reliable()):
            frame = self._grab_window(rel_left, rel_top, width, height)
            if frame is not None or self.requested == MODE_WINDOW:
                if diag.ENABLED:
                    self._diag_grab((rel_left, rel_top, width, height), MODE_WINDOW, frame)
                self._last_method = MODE_WINDOW
                return frame
            # auto: render failed (e.g. minimized) — the screen copy is the best we have
            diag.log("CAPTURE", "window render returned None: falling back to screen copy", tag=self.diag_tag)

        cover = self._foreground_cover()
        if cover is not None:
            # The screen copy would show that window, and the bot would read
            # it as game state ("no minigame", "panel closed"). Nothing to
            # see is the honest answer: no miss is counted on a blank frame.
            self._throttled_log(f"'{cover}' is in front of the game window — "
                                f"not reading the screen until it is out of the way")
            frame = None
        else:
            frame = self._grab_screen(rel_left, rel_top, width, height)
        if diag.ENABLED:
            self._diag_grab((rel_left, rel_top, width, height), MODE_SCREEN, frame)
        self._last_method = MODE_SCREEN
        return frame

    def _diag_grab(self, rect, method: str, frame) -> None:
        """Logs one grab. Anomalies (None / black / flat) always, throttled
        to one line per 2 s; ordinary grabs as per-frame detail."""
        try:
            st = diag.frame_stats(frame) if frame is not None else None
            anomaly = "None" if frame is None else ("BLACK" if st and st["black"] else ("FLAT" if st and st["flat"] else ""))
            text = (f"grab rel={rect} via {method} -> "
                    f"{'None' if frame is None else diag.array_summary(frame)}")
            if anomaly:
                now = time.perf_counter()
                if diag.VERBOSE or not diag.in_loop() or now - self._diag_anomaly_time >= 2.0:
                    self._diag_anomaly_time = now
                    diag.log("CAPTURE", f"{anomaly} frame: {text}", tag=self.diag_tag, depth=1)
            else:
                diag.trace("CAPTURE", text, tag=self.diag_tag, depth=1)
        except Exception:
            diag.exc("capture log", tag=self.diag_tag)

    def grab_other_method(self, rel_left: int, rel_top: int, width: int, height: int):
        """Diagnostics: the same rectangle through the method that was NOT
        used for the last grab, so the two can be compared. Returns
        (method, frame or None, note). Never changes the capture state. The
        screen copy is only taken while the game window is in front (it
        shows whatever is on top of that area)."""
        try:
            other = MODE_SCREEN if self._last_method == MODE_WINDOW else MODE_WINDOW
            if other == MODE_WINDOW:
                native = platforms.native
                if self._print_window is print_window and (native is None or not native.available()):
                    return other, None, "no window render on this system"
                saved = (self._full, self._full_time)
                self._full = None
                try:
                    frame = self._grab_window(rel_left, rel_top, width, height)
                finally:
                    self._full, self._full_time = saved
                return other, frame, "" if frame is not None else "window render returned None"
            if platforms.native is not None and not platforms.native.screen_capture_reliable():
                return other, None, "desktop copy impossible here (XWayland)"
            if not self.window_manager.is_foreground():
                return other, None, "game window not in front: screen copy would show another window"
            return other, self._grab_screen(rel_left, rel_top, width, height), ""
        except Exception as e:
            diag.exc("grab_other_method", tag=self.diag_tag)
            return None, None, f"error {e}"

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
            agree = frames_agree(rendered, screen)
            if diag.ENABLED:
                diag.log("CAPTURE", f"verify attempt {self._VERIFY_ATTEMPTS - self._verify_left}/"
                                    f"{self._VERIFY_ATTEMPTS}: agree={agree} foreground="
                                    f"{self.window_manager.is_foreground()} — "
                                    f"{frames_agree_detail(rendered, screen)}", tag=self.diag_tag)
                if not agree:
                    if rendered is not None:
                        diag.image("verify_render", rendered[:, :, :3], tag=self.diag_tag,
                                   note="window render that did not match the screen")
                    if screen is not None:
                        diag.image("verify_screen", screen, tag=self.diag_tag, note="screen copy at the same time")
            if agree:
                self.window_trusted = True
                self._verify_left = 0
                self._log("Capture: per-window render verified — game windows may overlap")
                return True
        except Exception as e:
            diag.exc("capture verify", tag=self.diag_tag)
            self._throttled_log(f"Capture check error: {e}")
        if self._verify_left <= 0:
            self._log("Capture: per-window render not usable on this client — "
                      "using screen capture, keep game windows uncovered")
        return False

    _COVER_CHECK_EVERY = 0.25   # seconds a foreground-cover verdict is reused

    def _foreground_cover(self) -> Optional[str]:
        """Title of a non-game window in front of this one (see
        platforms.win32.foreground_cover), or None. Platforms without the
        check, and errors, say None."""
        native = platforms.native
        check = getattr(native, "foreground_cover", None) if native is not None else None
        if check is None:
            return None
        now = time.perf_counter()
        cached = getattr(self, "_cover_cache", None)
        if cached is not None and now - cached[0] < self._COVER_CHECK_EVERY:
            return cached[1]
        try:
            hwnd = platforms.window_handle(self.window_manager.selected_window)
            with _active_lock:
                peers = [c for c in _active if c is not self]
            ignore = {platforms.window_handle(c.window_manager.selected_window) for c in peers}
            cover = check(hwnd, ignore)
        except Exception:
            diag.exc("foreground cover check", tag=self.diag_tag)
            cover = None
        if cover is not None and (cached is None or cached[1] != cover):
            diag.log("CAPTURE", f"foreground window {cover!r} covers the game: screen copy refused",
                     tag=self.diag_tag)
        self._cover_cache = (now, cover)
        return cover

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
            # Unknown is not "in front": the window's own render is read
            # instead, so a covering window is never taken for this client.
            diag.exc("screen reliability check", tag=self.diag_tag)
            return False

    @property
    def reads_screen_when_covered(self) -> bool:
        """True when a window that is not in front is still read from the
        screen, i.e. whatever lies on top of it is what the bot sees."""
        return not self.window_trusted

    def overlapping_peers(self) -> List[str]:
        """Tags (W1, W2...) of the other running bots' game windows that
        overlap this one. Empty when the rectangles cannot be read."""
        try:
            mine = tuple(self.window_manager.get_window_rect())
        except Exception:
            diag.exc("overlap check: own window rect", tag=self.diag_tag)
            return []
        with _active_lock:
            peers = [c for c in _active if c is not self]
        out = []
        for other in peers:
            try:
                if other.window_manager.selected_window is self.window_manager.selected_window:
                    continue
                theirs = tuple(other.window_manager.get_window_rect())
            except Exception:
                diag.exc("overlap check: other window rect", tag=self.diag_tag)
                continue
            if rects_overlap(mine, theirs):
                out.append(str(other.diag_tag or "another window"))
        return out

    def invalidate(self) -> None:
        """Drops the cached window render (call after sending input when the
        very next frame must reflect it)."""
        self._full = None

    def close(self) -> None:
        unregister_active(self)
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
            diag.exc("screen copy (mss) failed", tag=self.diag_tag)
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
            diag.exc("window render failed", tag=self.diag_tag)
            self._throttled_log(f"Window capture error: {e}")
            full = None
        self._full = full
        self._full_time = time.perf_counter()
        return full

    def _area_renderer(self) -> Optional[Callable]:
        """The OS call that renders only part of the window, when there is
        one (X11) and no test grabber replaced the window render."""
        if self._print_window is not print_window or platforms.native is None:
            return None
        return getattr(platforms.native, "render_window_area", None)

    def _grab_window(self, rel_left, rel_top, width, height) -> Optional[np.ndarray]:
        area = self._area_renderer()
        if area is not None:
            # Only the rectangle asked for crosses the wire: the minigame
            # panel and the dialog patches are a few % of the window.
            try:
                hwnd = platforms.window_handle(self.window_manager.selected_window)
                part = area(hwnd, rel_left, rel_top, width, height) if hwnd else None
            except Exception as e:
                diag.exc("window area render failed", tag=self.diag_tag)
                self._throttled_log(f"Window capture error: {e}")
                part = None
            if part is None:
                if diag.ENABLED:
                    diag.trace("CAPTURE", f"area render of ({rel_left},{rel_top},{width},{height}) -> None",
                               tag=self.diag_tag)
                return None
            return np.ascontiguousarray(part[:, :, :3])
        full = self._render_window()
        if full is None:
            return None
        fh, fw = full.shape[:2]
        x1, y1 = max(0, rel_left), max(0, rel_top)
        x2, y2 = min(fw, rel_left + width), min(fh, rel_top + height)
        if x2 <= x1 or y2 <= y1:
            diag.log("CAPTURE", f"rect ({rel_left},{rel_top},{width},{height}) lies outside the "
                                f"{fw}x{fh} window render -> None", tag=self.diag_tag)
            return None
        if (x2 - x1, y2 - y1) == (width, height):
            return np.ascontiguousarray(full[y1:y2, x1:x2, :3])
        if diag.ENABLED:
            diag.trace("CAPTURE", f"rect ({rel_left},{rel_top},{width},{height}) hangs over the {fw}x{fh} "
                                  f"window render edge: padded with black", tag=self.diag_tag)
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

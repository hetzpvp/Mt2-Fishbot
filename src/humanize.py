"""
Humanized input wrappers for mouse and keyboard.

When the bot's "Human-like behavior" setting is OFF, every wrapper forwards
directly to the underlying pyautogui / pynput call — behavior is byte-for-byte
identical to direct calls.

When the setting is ON, only click delays between fish clicks are maintained.
Mouse movement is direct (not curved). The humanize class provides unified
input handling with optional delays.

Use Humanizer instances per bot — they cache the enabled flag so hot loops
don't re-read the config on every input.
"""

from __future__ import annotations

import random
import time
from typing import Optional

import diag
from input_backend import PyAutoGuiMouseBackend
from utils import note_click_event, note_key_event, wait_focus_settled_for_keys


def _key_label(key) -> str:
    name = getattr(key, "name", None)
    if name:
        return str(name)
    char = getattr(key, "char", None)
    return repr(char) if char is not None else repr(key)


class Humanizer:
    """Per-bot input humanizer. Construct once, reuse for all inputs."""

    def __init__(self, enabled: bool, keyboard_controller=None, mouse_backend=None):
        self.enabled = bool(enabled)
        self.keyboard_controller = keyboard_controller
        self.mouse = mouse_backend or PyAutoGuiMouseBackend()
        # Diagnostics: the bot sets these so every input event can be logged
        # with its window tag and window-relative coordinates.
        self.diag_tag = None
        self.window_rect = None   # callable -> (left, top, width, height)
        self._last_move = None
        # Called before every cursor move and click (None: no check). A bot
        # sets it to stop sending input the moment it is paused or stopped:
        # the gate raises, and the action is not sent.
        self.gate = None

    def _where(self, x, y) -> str:
        """'screen=(x,y) win=(x,y)' for the diagnostic log."""
        text = f"screen=({int(x)},{int(y)})"
        try:
            if self.window_rect is not None:
                left, top, w, h = self.window_rect()
                rx, ry = int(x) - left, int(y) - top
                inside = 0 <= rx < w and 0 <= ry < h
                text += f" win=({rx},{ry}){'' if inside else ' OUTSIDE the window'}"
        except Exception:
            diag.exc("window rect for log", tag=self.diag_tag)
        return text

    def _act(self, msg: str) -> None:
        diag.log("ACTION", f"{msg} via {getattr(self.mouse, 'name', type(self.mouse).__name__)}",
                 tag=self.diag_tag, depth=1)

    # ---------------- Mouse ----------------

    def move_to(self, x: float, y: float) -> None:
        """Move cursor directly to (x, y)."""
        if self.gate is not None:
            self.gate()
        if diag.ENABLED:
            self._last_move = (x, y)
            self._act(f"move_to {self._where(x, y)}")
        self.mouse.move_to(x, y)

    def glide_to(self, x: float, y: float, duration: float) -> None:
        """Moves the cursor to (x, y) in a few steps over ``duration`` seconds
        instead of one jump. A game reads the cursor once per frame: after a
        jump it may only notice the new place frames later, and the button
        under the cursor is not "hovered" yet when the click comes. A plain
        ``move_to`` when ``duration`` is 0, the start is unknown or the
        distance is a few pixels."""
        if self.gate is not None:
            self.gate()
        start = None
        if duration > 0:
            try:
                pos = self.mouse.position()
                start = (float(pos[0]), float(pos[1]))
            except Exception:
                start = None
        if start is not None:
            dx, dy = float(x) - start[0], float(y) - start[1]
            dist = (dx * dx + dy * dy) ** 0.5
            steps = int(max(2, min(8, dist // 30)))
            if dist >= 4:
                for i in range(1, steps):
                    t = i / steps
                    t = 1.0 - (1.0 - t) * (1.0 - t)   # ease out: slows down onto the target
                    self.mouse.move_to(start[0] + dx * t, start[1] + dy * t)
                    time.sleep(duration / steps)
                if diag.ENABLED:
                    diag.trace("ACTION", f"glide from ({int(start[0])},{int(start[1])}) in {steps} steps "
                                         f"over {duration * 1000:.0f} ms", tag=self.diag_tag, depth=1)
        self.move_to(x, y)

    def click(self, button: str = "left", hold: Optional[float] = None) -> None:
        """Single click.

        When humanization is enabled, hold duration is randomized: if `hold` is
        given, it's used as the center value (±40%); otherwise a default 35-85ms
        range is used.

        When humanization is disabled, behavior depends on `hold`:
        - hold is None → instant pyautogui.click (current/legacy behavior)
        - hold is given → explicit mouseDown + sleep(hold) + mouseUp, so callers
          that need a guaranteed hold duration (e.g. game UI overlays that
          require the down-event to be visible long enough) keep working.
        """
        if self.gate is not None:
            self.gate()
        if diag.ENABLED:
            at = self._where(*self._last_move) if self._last_move else "at current cursor"
            self._act(f"click {button} hold={'instant' if hold is None and not self.enabled else hold} "
                      f"humanized={self.enabled} last_move {at}")
        if not self.enabled and hold is None:
            try:
                self.mouse.click(button=button)
            finally:
                note_click_event()
            return

        if not self.enabled:
            duration = hold
        elif hold is None:
            duration = random.uniform(0.035, 0.085)
        else:
            duration = hold * random.uniform(0.6, 1.4)

        self.mouse.mouse_down(button=button)
        try:
            if duration and duration > 0:
                time.sleep(duration)
        finally:
            try:
                self.mouse.mouse_up(button=button)
            finally:
                note_click_event()

    def mouse_down(self, button: str = "left") -> None:
        if self.gate is not None:
            self.gate()     # never gates mouse_up: a pressed button is always released
        if diag.ENABLED:
            self._act(f"mouse_down {button}")
        self.mouse.mouse_down(button=button)

    def mouse_up(self, button: str = "left") -> None:
        if diag.ENABLED:
            self._act(f"mouse_up {button}")
        try:
            self.mouse.mouse_up(button=button)
        finally:
            note_click_event()

    # ---------------- Sleeps ----------------

    def sleep(self, seconds: float, jitter_ratio: float = 0.25) -> None:
        """Sleep with optional humanizing jitter (±jitter_ratio of base when enabled)."""
        if not self.enabled or seconds <= 0:
            if diag.ENABLED:
                diag.trace("ACTION", f"sleep {seconds * 1000:.0f} ms", tag=self.diag_tag, depth=1)
            time.sleep(seconds)
            return
        spread = seconds * jitter_ratio
        actual = max(0.0, seconds + random.uniform(-spread, spread))
        if diag.ENABLED:
            diag.trace("ACTION", f"sleep {actual * 1000:.0f} ms (base {seconds * 1000:.0f} ms, jittered)",
                       tag=self.diag_tag, depth=1)
        time.sleep(actual)

    # ---------------- Keyboard ----------------

    def key_press(self, key) -> None:
        """Press a pynput key (no release). Pass-through; jitter belongs in surrounding sleeps."""
        if self.keyboard_controller is None:
            diag.log("ACTION", f"key_press {_key_label(key)} NOT SENT: no keyboard", tag=self.diag_tag, depth=1)
            return
        self._settle_for_keys()
        if diag.ENABLED:
            diag.log("ACTION", f"key_press {_key_label(key)}", tag=self.diag_tag, depth=1)
        try:
            self.keyboard_controller.press(key)
        finally:
            note_key_event()

    def key_release(self, key) -> None:
        if self.keyboard_controller is None:
            return
        if diag.ENABLED:
            diag.log("ACTION", f"key_release {_key_label(key)}", tag=self.diag_tag, depth=1)
        try:
            self.keyboard_controller.release(key)
        finally:
            note_key_event()

    def _settle_for_keys(self) -> None:
        """A key sent right after a focus switch can reach the window that
        had the focus before (see utils.KEY_AFTER_SWITCH)."""
        waited = wait_focus_settled_for_keys()
        if waited and diag.ENABLED:
            diag.log("ACTION", f"waited {waited * 1000:.0f} ms after the focus switch before the key",
                     tag=self.diag_tag, depth=2)

    def tap_key(self, key, hold: float = 0.025) -> None:
        """Press + release a key with a humanized hold duration when enabled."""
        if self.keyboard_controller is None:
            diag.log("ACTION", f"tap_key {_key_label(key)} NOT SENT: no keyboard", tag=self.diag_tag, depth=1)
            return
        held = random.uniform(hold * 0.7, hold * 1.6) if self.enabled else hold
        self._settle_for_keys()
        try:
            self.keyboard_controller.press(key)
            time.sleep(held)
        finally:
            # Released whatever happened in between: a key left down keeps
            # repeating into the game.
            try:
                self.keyboard_controller.release(key)
            finally:
                note_key_event()
        if diag.ENABLED:
            diag.log("ACTION", f"tap_key {_key_label(key)} held {held * 1000:.0f} ms via "
                               f"{type(self.keyboard_controller).__name__}", tag=self.diag_tag, depth=1)


# Module-level fallback singleton: used by call sites that don't yet have a
# bot-attached Humanizer (e.g. early-startup helpers). Defaults to disabled
# to preserve current behavior. Bots construct their own per-instance.
_default = Humanizer(enabled=False)


def default() -> Humanizer:
    return _default

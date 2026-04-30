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

from input_backend import PyAutoGuiMouseBackend


class Humanizer:
    """Per-bot input humanizer. Construct once, reuse for all inputs."""

    def __init__(self, enabled: bool, keyboard_controller=None, mouse_backend=None):
        self.enabled = bool(enabled)
        self.keyboard_controller = keyboard_controller
        self.mouse = mouse_backend or PyAutoGuiMouseBackend()

    # ---------------- Mouse ----------------

    def move_to(self, x: float, y: float) -> None:
        """Move cursor directly to (x, y)."""
        self.mouse.move_to(x, y)

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
        if not self.enabled and hold is None:
            self.mouse.click(button=button)
            return

        if not self.enabled:
            duration = hold
        elif hold is None:
            duration = random.uniform(0.035, 0.085)
        else:
            duration = hold * random.uniform(0.6, 1.4)

        self.mouse.mouse_down(button=button)
        if duration and duration > 0:
            time.sleep(duration)
        self.mouse.mouse_up(button=button)

    def mouse_down(self, button: str = "left") -> None:
        self.mouse.mouse_down(button=button)

    def mouse_up(self, button: str = "left") -> None:
        self.mouse.mouse_up(button=button)

    # ---------------- Sleeps ----------------

    def sleep(self, seconds: float, jitter_ratio: float = 0.25) -> None:
        """Sleep with optional humanizing jitter (±jitter_ratio of base when enabled)."""
        if not self.enabled or seconds <= 0:
            time.sleep(seconds)
            return
        spread = seconds * jitter_ratio
        time.sleep(max(0.0, seconds + random.uniform(-spread, spread)))

    # ---------------- Keyboard ----------------

    def key_press(self, key) -> None:
        """Press a pynput key (no release). Pass-through; jitter belongs in surrounding sleeps."""
        if self.keyboard_controller is None:
            return
        self.keyboard_controller.press(key)

    def key_release(self, key) -> None:
        if self.keyboard_controller is None:
            return
        self.keyboard_controller.release(key)

    def tap_key(self, key, hold: float = 0.025) -> None:
        """Press + release a key with a humanized hold duration when enabled."""
        if self.keyboard_controller is None:
            return
        self.keyboard_controller.press(key)
        if self.enabled:
            time.sleep(random.uniform(hold * 0.7, hold * 1.6))
        else:
            time.sleep(hold)
        self.keyboard_controller.release(key)


# Module-level fallback singleton: used by call sites that don't yet have a
# bot-attached Humanizer (e.g. early-startup helpers). Defaults to disabled
# to preserve current behavior. Bots construct their own per-instance.
_default = Humanizer(enabled=False)


def default() -> Humanizer:
    return _default

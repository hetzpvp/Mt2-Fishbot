"""Human-looking cursor moves for the trailer, through the bot's own uinput mouse.

The real desktop cursor follows: only use this while nobody is at the PC.

    from cursor_path import Hand
    hand = Hand(log=events)          # events: callable(str) for the edit's event log
    hand.click_at(1500, 300)         # glide there, click
    hand.click_at(x, y, hold=0.13)   # game UI wants a held click
    hand.wheel(-3)
"""
from __future__ import annotations

import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

HZ = 120.0


def _ease(t: float) -> float:
    # ease-in-out with a quicker start than smoothstep (people accelerate fast, brake late)
    return t * t * (3 - 2 * t) * 0.35 + (1 - (1 - t) ** 3) * 0.65


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


class Hand:
    def __init__(self, log=None, start=None, speed=1.0, mouse=None, keyboard=None):
        from platforms import uinput as _u  # noqa: F401  (fails early without /dev/uinput)
        import input_backend
        self.m = mouse or input_backend._get_uinput_mouse()
        self._kb = keyboard
        self.log = log or (lambda s: None)
        self.speed = speed
        w, h = self.m._size
        self.pos = start or (w * 0.62, h * 0.55)
        self.m.move_to(*self.pos)
        self.rng = random.Random(11)

    # ------------------------------------------------------------ movement
    def move_to(self, x, y, duration=None):
        x0, y0 = self.pos
        dx, dy = x - x0, y - y0
        dist = math.hypot(dx, dy)
        if dist < 2:
            self.m.move_to(x, y)
            self.pos = (x, y)
            return
        if duration is None:
            duration = min(0.95, 0.30 + 0.22 * math.log10(1 + dist / 12.0)) / self.speed
        # bowed path: control points pushed sideways by up to ~14 % of the distance
        nx, ny = -dy / dist, dx / dist
        bow = self.rng.uniform(-0.14, 0.14) * dist
        c1 = (x0 + dx * 0.3 + nx * bow, y0 + dy * 0.3 + ny * bow)
        c2 = (x0 + dx * 0.75 + nx * bow * 0.5, y0 + dy * 0.75 + ny * bow * 0.5)
        # long moves overshoot a hair and settle back
        over = dist > 250
        end = (x + dx / dist * self.rng.uniform(4, 9), y + dy / dist * self.rng.uniform(2, 6)) if over else (x, y)
        steps = max(8, int(duration * HZ))
        t0 = time.perf_counter()
        for i in range(1, steps + 1):
            t = _ease(i / steps)
            px, py = _bezier((x0, y0), c1, c2, end, t)
            self.m.move_to(px, py)
            target = t0 + i / HZ
            d = target - time.perf_counter()
            if d > 0:
                time.sleep(d)
        if over:
            for i in range(1, 10):
                k = i / 9
                self.m.move_to(end[0] + (x - end[0]) * k, end[1] + (y - end[1]) * k)
                time.sleep(1 / HZ)
        self.m.move_to(x, y)
        self.pos = (x, y)

    def pause(self, a=0.15, b=0.35):
        time.sleep(self.rng.uniform(a, b) / self.speed)

    # ------------------------------------------------------------ buttons
    def click(self, button="left", hold=None):
        hold = hold if hold is not None else self.rng.uniform(0.06, 0.11)
        x, y = self.pos
        self.log(f"click {int(x)} {int(y)} {button}")
        self.m.mouse_down(button)
        time.sleep(hold)
        self.m.mouse_up(button)

    def click_at(self, x, y, button="left", hold=None, settle=None):
        self.move_to(x, y)
        time.sleep(settle if settle is not None else self.rng.uniform(0.07, 0.16))
        self.click(button, hold)

    def wheel(self, notches: int, at=None):
        from platforms.uinput import EV_REL, REL_WHEEL
        if at:
            self.move_to(*at)
        step = -1 if notches < 0 else 1
        for _ in range(abs(int(notches))):
            self.m._dev.emit((EV_REL, REL_WHEEL, step))
            time.sleep(self.rng.uniform(0.05, 0.09))

    def key(self, name: str, hold=0.08):
        if self._kb is None:
            import input_backend
            self._kb = input_backend._get_uinput_keyboard()
        self.log(f"key {name}")
        self._kb.press(name)
        time.sleep(hold)
        self._kb.release(name)

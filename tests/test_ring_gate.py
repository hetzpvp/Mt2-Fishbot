"""Fixes from the 2026-11-19 diagnostic session (two clients, KDE Wayland +
Wine): only 2 of 9 minigames gave a catch, and one cast never reached its
window.

- Ring gate: the official client draws the click ring red while it counts
  the fish as inside, white otherwise and for a while after a hit. The bot
  clicked every ~70 ms whenever the fish was inside its own circle; now it
  clicks only on a red ring, once per red phase. Clients whose ring never
  turns red keep the old circle rule.
- Keys vs. focus switches: a Ctrl+G release sent 0-1 ms before another bot
  switched the focus, and a bait key 56 ms after the switch back, were lost.
  Focus switches now wait for the last key to arrive, keys wait for a fresh
  switch to settle."""

import json
import os
import time
import unittest

import cv2

from _stubs import install, ROOT

install()

import sys  # noqa: E402

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import utils  # noqa: E402
from fish_detector import RING_RED, RING_UNKNOWN, RING_WHITE, ring_color  # noqa: E402
from humanize import Humanizer  # noqa: E402
from minigame_sim import bench  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


class RingColorTests(unittest.TestCase):
    """The classifier on real client pictures."""

    def test_lossless_captures_from_the_session(self):
        # 247x195 panels, bot circle (123, 97) r=67. The first two are 12 ms
        # apart with the fish touching the ring: the ring turns red a frame late.
        cases = [("white_fish_on_ring.png", None, RING_WHITE),
                 ("red_fish_on_ring.png", (135, 39), RING_RED),
                 ("white_fish_outside.png", None, RING_WHITE)]
        for name, fish, want in cases:
            panel = cv2.imread(os.path.join(FIX, "ring", name))
            self.assertEqual(ring_color(panel, (123, 97), 67, fish), want, name)

    def test_real_client_video_frames(self):
        with open(os.path.join(FIX, "real_client", "labels.json")) as f:
            labels = json.load(f)
        red = {"0358", "0434", "1111", "1314", "1413", "1557", "2054"}
        white = {"0231", "0336", "0390", "0479", "0867"}
        center = labels["ring"]["center_from_top_left"]
        for frame in sorted(red | white):
            panel = cv2.imread(os.path.join(FIX, "real_client", f"panel_{frame}.png"))
            want = RING_RED if frame in red else RING_WHITE
            self.assertEqual(ring_color(panel, center, 67), want, frame)

    def test_other_ring_colors_are_unknown(self):
        panel = cv2.imread(os.path.join(FIX, "ring", "white_fish_outside.png"))
        self.assertEqual(ring_color(panel, (123, 97), 30), RING_UNKNOWN)   # no ring there
        self.assertEqual(ring_color(None, (123, 97), 67), RING_UNKNOWN)


class RingGateSimTests(unittest.TestCase):
    """Whole sessions in the simulator, whose official skin turns the ring red
    like the real client and keeps it white for the cooldown after a hit."""

    def test_official_ring_clicks_only_on_red(self):
        game = bench.quick_game("official")
        bot, logs = bench.run(game, games=4, timeout=160)
        s = game.stats
        self.assertIs(bot._ring_turns_red, True, logs[-12:])
        self.assertGreaterEqual(s["caught"], 4, logs[-12:])
        self.assertEqual(s["timeouts"], 0)
        # Before the gate about 13 clicks per game landed in the cooldown
        self.assertLessEqual(s["clicks_ignored"], 2, s)
        self.assertLessEqual(s["misses"], 2, s)

    def test_client_without_a_red_ring_keeps_the_circle_rule(self):
        game = bench.quick_game("official", ring_feedback=False)
        bot, logs = bench.run(game, games=3, timeout=120)
        self.assertIs(bot._ring_turns_red, False, logs[-12:])
        self.assertGreaterEqual(game.stats["caught"], 3, logs[-12:])
        self.assertEqual(game.stats["timeouts"], 0)


class KeyFocusTests(unittest.TestCase):

    class Keys:
        def __init__(self):
            self.events = []

        def press(self, k):
            self.events.append(("down", k, time.perf_counter()))

        def release(self, k):
            self.events.append(("up", k, time.perf_counter()))

    def test_key_waits_for_a_fresh_focus_switch(self):
        keys = self.Keys()
        h = Humanizer(enabled=False, keyboard_controller=keys)
        utils.note_focus_switch()
        start = time.perf_counter()
        h.tap_key("1", hold=0.001)
        self.assertGreaterEqual(keys.events[0][2] - start, utils.KEY_AFTER_SWITCH - 0.01)

    def test_key_is_not_delayed_long_after_a_switch(self):
        keys = self.Keys()
        h = Humanizer(enabled=False, keyboard_controller=keys)
        utils._key_focus["switch"] = time.perf_counter() - 10
        start = time.perf_counter()
        h.tap_key("1", hold=0.001)
        self.assertLess(keys.events[0][2] - start, 0.03)

    def test_switch_waits_for_the_last_key_release(self):
        keys = self.Keys()
        h = Humanizer(enabled=False, keyboard_controller=keys)
        utils._key_focus["switch"] = time.perf_counter() - 10
        h.key_press("g")
        h.key_release("g")
        released = keys.events[-1][2]
        utils.wait_keys_delivered()
        self.assertGreaterEqual(time.perf_counter() - released, utils.KEY_FLUSH - 0.005)


class WindowSwitchWaitsForKeysTests(unittest.TestCase):
    """WindowManager.activate_window: no switch while a key may be in flight,
    and a real switch is recorded for the key gate."""

    def test_activate_waits_and_records(self):
        import platforms
        from window_manager import WindowManager
        from test_window_manager import FakeNative

        native = FakeNative()
        native.add(1, "METIN2 (1)")
        native.add(2, "METIN2 (2)")
        native.foreground = 1
        old = platforms.native
        platforms.native = native
        try:
            wm = WindowManager()
            wm.selected_window = native.windows[2]
            utils._key_focus["switch"] = 0.0
            utils.note_key_event()
            sent = time.perf_counter()
            self.assertTrue(wm.activate_window())
            self.assertGreaterEqual(time.perf_counter() - sent, utils.KEY_FLUSH - 0.005)
            self.assertGreater(utils._key_focus["switch"], sent)
            # Already in front: no wait, no new switch stamp
            stamp = utils._key_focus["switch"]
            utils.note_key_event()
            t = time.perf_counter()
            self.assertTrue(wm.activate_window())
            self.assertLess(time.perf_counter() - t, 0.02)
            self.assertEqual(utils._key_focus["switch"], stamp)
        finally:
            platforms.native = old


if __name__ == "__main__":
    unittest.main()

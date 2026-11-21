"""A bite while paused (2026-11-20 diagnostics): the minigame opened during
the pause. After the resume the bot took its pre-cast picture with that panel
open and cast again; the panel then closed (bait lost), and the learner took
the change, the panel closing, for one opening. It learned the water as the
panel and a pet's flame as the fish, and clicked the flame for 19 s.
Stub-only: nothing touches the desktop."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np

from _stubs import install, ROOT

install()

from fish_detector import WindowLearner  # noqa: E402
from test_items_during_cast import loop_bot  # noqa: E402
from test_real_client import window_bot  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "bite_while_paused")


def image(name):
    img = cv2.imread(os.path.join(FIXTURES, name))
    assert img is not None, name
    return img


class RealFramesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.open = image("panel_open_before_cast.png")
        cls.closed = image("panel_closed.png")

    def test_open_panel_is_seen_before_the_cast(self):
        bot = window_bot(self.open)
        self.assertEqual(bot._known_panel_in(self.open), (277, 213, 247, 195))
        self.assertIsNone(bot._known_panel_in(self.closed))

    def test_a_closing_panel_is_not_learned(self):
        self.assertIsNone(WindowLearner().learn(self.open, self.closed))

    def test_the_bot_does_not_learn_after_an_open_reference(self):
        bot = window_bot(self.closed)
        bot._reference_frame = self.open
        self.assertEqual(bot._find_with_other_profiles(self.closed), (None, None, 0))


class SavedSceneryLookTests(unittest.TestCase):
    """2026-11-20 diagnostics 091913: the look learned from the water was
    saved, loaded at the next start and matched the whole scene 0.2 s after
    the cast; the real panel was never looked for. The first-sighting checks
    skipped learned looks."""

    BAD = {"name": "learned", "window_lower": [73, 20, 29], "window_upper": [94, 102, 114],
           "fish_lower": [16, 121, 237], "fish_upper": [34, 244, 255], "circle_radius": 67,
           "circle_center": None, "window_min_pixels": 10000, "fish_min_pixels": 24, "learned": True}

    def _bot(self, frame):
        from fishing_bot import FishingBot
        from _stubs import FakeWindowManager, FakeMouse
        FishingBot.reset_session_state()
        h, w = frame.shape[:2]
        bot = FishingBot(None, {"detection_profile": dict(self.BAD)}, FakeWindowManager(w, h),
                         bait_counter=10, bot_id=0)
        bot.human.mouse = bot.mouse_backend = FakeMouse()
        bot.running = True
        return bot

    def test_a_run_starts_with_the_stock_look(self):
        from fishing_bot import FishingBot
        bot = self._bot(image("scene_after_cast.png"))
        self.assertEqual(bot.detector.profile.name, "official")
        self.assertTrue(any(k.to_dict()["window_lower"] == self.BAD["window_lower"]
                            for k in FishingBot._known_profiles))      # only a candidate

    def test_scenery_look_is_dropped_and_forgotten(self):
        from fishing_bot import FishingBot
        before, after = image("scene_before_cast.png"), image("scene_after_cast.png")
        bot = self._bot(after)
        saved = []
        bot.on_profile_learned = saved.append
        bot._reference_frame = before
        # Past the time after which other looks are tried: the saved one matches the scene
        self.assertFalse(any(bot._try_calibrate(after, 2.0) for _ in range(3)))
        self.assertIsNone(bot.region)
        self.assertEqual(bot.detector.profile.name, "official")
        self.assertIsNone(bot.config["detection_profile"])
        self.assertEqual(saved, [None])                        # the saved copy is cleared too
        self.assertFalse(FishingBot._known_profiles)

    def test_a_learned_look_that_matches_scenery_is_dropped_too(self):
        from fish_detector import DetectionProfile
        before, after = image("scene_before_cast.png"), image("scene_after_cast.png")
        bot = self._bot(after)
        bot.detector.set_profile(DetectionProfile.from_dict(self.BAD))    # learned this run
        bot._reference_frame = before
        self.assertFalse(any(bot._try_calibrate(after, 0.1) for _ in range(3)))
        self.assertEqual(bot.detector.profile.name, "official")
        self.assertIsNone(bot.config["detection_profile"])


class AlreadyOpenTests(unittest.TestCase):
    def test_open_minigame_is_played_not_cast_over(self):
        bot, events, _ = loop_bot(bait=1)
        open_frame = np.zeros((600, 800, 3), np.uint8)
        bot.capture_full_window = lambda: open_frame
        bot._known_panel_in = lambda frame: (277, 213, 247, 195)
        bot._pending_catch = (np.zeros((40, 40, 3), np.uint8), True)
        bot._line_out = True              # cast, then paused before its bite was seen
        bot._play_game()
        # No cast, no item handling while it runs: straight to the game
        self.assertEqual(events[:2], ["wait", "game"])
        self.assertNotIn("cast", events)

    def test_panel_colored_scenery_does_not_stop_the_cast(self):
        bot, events, _ = loop_bot(bait=1)
        bot.capture_full_window = lambda: np.zeros((600, 800, 3), np.uint8)
        bot._known_panel_in = lambda frame: (277, 213, 247, 195)
        bot._play_game()                  # no cast out: whatever looks like a panel is scenery
        self.assertEqual(events[0], "cast")

    def test_after_a_paused_wait_the_next_cast_looks_for_its_minigame(self):
        bot, events, _ = loop_bot(bait=1)
        bot.capture_full_window = lambda: np.zeros((600, 800, 3), np.uint8)
        looked = []

        def known_panel_in(frame):
            looked.append(1)
            return (277, 213, 247, 195)   # the bite came during the pause

        calls = []

        def wait(timeout=6.0):
            calls.append(1)
            bot._wait_interrupted = len(calls) == 1      # the first wait: paused
            return len(calls) > 1

        bot._known_panel_in = known_panel_in
        bot._game_running_at_start = lambda: None   # its own looks are not counted here
        bot.wait_for_minigame_window = wait
        bot._play_game()
        self.assertEqual(looked, [1])                 # only once a cast was out
        self.assertEqual(events.count("cast"), 1)     # its minigame was played, not cast over
        self.assertIn("game", events)


if __name__ == "__main__":
    unittest.main()

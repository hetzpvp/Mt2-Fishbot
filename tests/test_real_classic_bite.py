"""Classic bite detection on frames of a real classic client
(tests/fixtures/real_classic, recorded on the local test rig's classic
instance at 800x600 by tools/realclient/record_bite.py).

That client draws the bite as a small white bubble round the hook-and-fish
icon, its lower edge under the character's name. The icon template alone
(classic_fish.jpg) scored at most 0.56 on it, below the 0.7 acceptance, and
0.60 on frames without a bite: the bot saw no bite at all and stopped with
"no bite" after three casts. The bubble template (classic_fish_bubble.png)
finds every bite and nothing else."""

import json
import os
import unittest

import cv2
import numpy as np

from _stubs import install, ROOT

install()

from fishing_bot import FishingBot  # noqa: E402
from test_review_fixes import quiet_bot  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "real_classic")
with open(os.path.join(FIXTURES, "labels.json")) as _f:
    LABELS = json.load(_f)["bite"]


class RealClassicBiteTests(unittest.TestCase):
    def setUp(self):
        FishingBot._classic_fish_scaled_cache = None
        FishingBot._classic_fish_last_scale = None

    tearDown = setUp

    def _found(self, name):
        _, bot, _ = quiet_bot({"classic_fishing_delay": 0.0})
        strip = cv2.imread(os.path.join(FIXTURES, name))
        self.assertIsNotNone(strip, name)
        bot.capture_window_region = lambda *_a, **_k: strip
        bot._last_frame_usable = True
        # the strip is what an 800x600 window gives (x 275-525, top half)
        bot.window_manager.get_window_rect = lambda: (0, 0, 800, 600)
        return bot.wait_for_classic_fish(timeout=0.4)

    def test_bubble_template_is_shipped(self):
        self.assertIsNotNone(FishingBot._load_classic_bubble_template())

    def test_every_real_bite_is_found(self):
        for name, bite in LABELS.items():
            if bite:
                with self.subTest(name):
                    self.assertTrue(self._found(name))

    def test_the_moon_is_not_a_bite(self):
        """2026-11-26: the moon matched the bubble picture at 0.74 (above the
        0.7 acceptance) on a character whose camera had it in the bite strip;
        the bot reeled in on it. A matched bubble must hold the icon."""
        strip = cv2.imread(os.path.join(FIXTURES, "moon_W4.png"))
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        bubble = FishingBot._load_classic_bubble_template()
        t = cv2.resize(bubble, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
        _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))
        self.assertGreaterEqual(score, 0.7)          # the picture alone takes it for a bite
        self.assertFalse(FishingBot._bubble_has_icon(gray[y:y + t.shape[0], x:x + t.shape[1]], t))
        self.assertFalse(self._found("moon_W4.png"))

    def test_a_small_glow_is_not_a_bite(self):
        """2026-11-26: a small white glow in front of the dock matched at 0.71
        for a character without a rod (its miss count never reached three, so
        the bot never stopped with no_bite). It has some contrast round it,
        but it does not fill the bubble's disc."""
        strip = cv2.imread(os.path.join(FIXTURES, "glow_W2.png"))
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        t = cv2.resize(FishingBot._load_classic_bubble_template(), None, fx=0.8, fy=0.8,
                       interpolation=cv2.INTER_AREA)
        _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))
        self.assertGreaterEqual(score, 0.7)
        self.assertFalse(FishingBot._bubble_has_icon(gray[y:y + t.shape[0], x:x + t.shape[1]], t))
        self.assertFalse(self._found("glow_W2.png"))

    def test_real_bubbles_hold_the_icon_under_other_looks(self):
        """The icon check holds for the real bites under gamma, brightness,
        gray and blur (docs/TESTRIG_SCENARIOS.md, color sweeps)."""
        looks = {
            "gamma 0.6": lambda f: cv2.LUT(f, (((np.arange(256) / 255.0) ** 0.6) * 255).astype(np.uint8)),
            "gamma 1.6": lambda f: cv2.LUT(f, np.clip(((np.arange(256) / 255.0) ** 1.6) * 255, 0, 255).astype(np.uint8)),
            "darker": lambda f: (f * 0.6).astype(np.uint8),
            "gray": lambda f: cv2.cvtColor(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR),
            "blur 3": lambda f: cv2.blur(f, (3, 3)),
            "blur 5": lambda f: cv2.blur(f, (5, 5)),
            "jpeg 30": lambda f: cv2.imdecode(cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 30])[1], 1),
        }
        for name, bite in LABELS.items():
            if not bite:
                continue
            for look, fn in looks.items():
                with self.subTest(name=name, look=look):
                    _, bot, _ = quiet_bot({"classic_fishing_delay": 0.0})
                    strip = fn(cv2.imread(os.path.join(FIXTURES, name)))
                    bot.capture_window_region = lambda *_a, _s=strip, **_k: _s
                    bot._last_frame_usable = True
                    bot.window_manager.get_window_rect = lambda: (0, 0, 800, 600)
                    FishingBot._classic_fish_scaled_cache = None
                    FishingBot._classic_fish_last_scale = None
                    self.assertTrue(bot.wait_for_classic_fish(timeout=0.4))

    def test_frames_without_a_bite_stay_quiet(self):
        for name, bite in LABELS.items():
            if not bite:
                with self.subTest(name):
                    self.assertFalse(self._found(name))


if __name__ == "__main__":
    unittest.main()

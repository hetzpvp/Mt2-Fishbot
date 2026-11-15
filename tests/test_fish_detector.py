import unittest

import numpy as np

from _stubs import install, hsv_to_bgr, SimScreen

install()

from fish_detector import (  # noqa: E402
    DetectionProfile, FishDetector, FishLearner, WindowLearner,
)

OFFICIAL_PANEL = hsv_to_bgr(102, 200, 220)
OFFICIAL_FISH = hsv_to_bgr(104, 138, 120)


class StockProfileTests(unittest.TestCase):
    """The default profile must behave exactly like the original hard-coded detector."""

    def setUp(self):
        self.screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        self.detector = FishDetector()

    def test_defaults_match_original_constants(self):
        p = DetectionProfile()
        self.assertEqual((p.window_lower, p.window_upper), ((98, 170, 189), (106, 255, 250)))
        self.assertEqual((p.fish_lower, p.fish_upper), ((97, 130, 108), (110, 146, 133)))
        self.assertEqual((p.circle_radius, p.window_min_pixels, p.fish_min_pixels), (67, 10000, 24))
        self.assertFalse(self.detector.learning)

    def test_no_panel_no_detection(self):
        frame = self.screen.render()
        self.assertIsNone(self.detector.find_fishing_window_bounds(frame))
        self.assertEqual(self.detector.detect_window_and_fish(frame), (False, None))

    def test_finds_panel_and_fish(self):
        self.screen.panel_open = True
        frame = self.screen.render()
        bounds = self.detector.find_fishing_window_bounds(frame)
        self.assertEqual(bounds, SimScreen.PANEL)

        x, y, w, h = bounds
        active, fish = self.detector.detect_window_and_fish(frame[y:y + h, x:x + w])
        self.assertTrue(active)
        fx, fy = self.screen.fish_center()
        self.assertLessEqual(abs(fish[0] - (fx - x)), 2)
        self.assertLessEqual(abs(fish[1] - (fy - y)), 2)

    def test_large_panel_keeps_original_threshold(self):
        self.screen.panel_open = True
        self.detector.find_fishing_window_bounds(self.screen.render())
        self.detector.calibrate()
        self.assertEqual(self.detector._window_px_threshold, 10000)

    def test_small_panel_gets_relative_threshold(self):
        """A 110x80 panel (8800 px) could never pass the fixed 10000 px test."""
        frame = np.full((300, 400, 3), 60, np.uint8)
        frame[100:180, 100:210] = OFFICIAL_PANEL
        bounds = self.detector.find_fishing_window_bounds(frame)
        self.assertEqual(bounds, (100, 100, 110, 80))
        self.detector.calibrate()
        active, _ = self.detector.detect_window_and_fish(frame[100:180, 100:210])
        self.assertTrue(active)
        active, _ = self.detector.detect_window_and_fish(np.full((80, 110, 3), 60, np.uint8))
        self.assertFalse(active)

    def test_circle_radius_shrinks_only_for_small_panels(self):
        self.assertEqual(self.detector.circle_radius_for(280, 220), 67)
        self.assertEqual(self.detector.circle_radius_for(120, 100), 42)


class ProfileSerializationTests(unittest.TestCase):
    def test_round_trip(self):
        p = DetectionProfile(name="learned", window_lower=(50, 100, 100), window_upper=(70, 255, 255),
                             fish_lower=(170, 120, 90), fish_upper=(8, 255, 255), learned=True)
        q = DetectionProfile.from_dict(p.to_dict())
        self.assertEqual(p, q)

    def test_invalid_data_is_rejected(self):
        self.assertIsNone(DetectionProfile.from_dict(None))
        self.assertIsNone(DetectionProfile.from_dict({"window_lower": [1, 2]}))
        self.assertIsNone(DetectionProfile.from_dict({"fish_lower": None}))

    def test_hue_wraparound_mask(self):
        """A red fish straddles hue 0/179 — the range must wrap."""
        p = DetectionProfile(name="red", fish_lower=(172, 150, 100), fish_upper=(6, 255, 255))
        det = FishDetector(p)
        frame = np.zeros((200, 260, 3), np.uint8)
        frame[:] = OFFICIAL_PANEL
        frame[90:110, 40:70] = hsv_to_bgr(177, 220, 200)
        self.assertEqual(det.detect_window_and_fish(frame)[1], (54, 99))
        frame[90:110, 40:70] = hsv_to_bgr(3, 220, 200)
        self.assertEqual(det.detect_window_and_fish(frame)[1], (54, 99))


class WindowLearnerTests(unittest.TestCase):
    def _learn(self, panel_bgr):
        screen = SimScreen(panel_bgr, hsv_to_bgr(0, 0, 30))
        before = screen.render()
        screen.panel_open = True
        return WindowLearner().learn(before, screen.render())

    def test_learns_recolored_panels(self):
        for name, hsv in {"green": (60, 180, 200), "purple": (140, 150, 160),
                          "red": (2, 200, 190), "light gray": (0, 0, 200)}.items():
            with self.subTest(panel=name):
                result = self._learn(hsv_to_bgr(*hsv))
                self.assertIsNotNone(result)
                bounds, lower, upper, pixels = result
                for got, want in zip(bounds, SimScreen.PANEL):
                    self.assertLessEqual(abs(got - want), 3)
                self.assertGreater(pixels, 0.8 * 280 * 220)

                # The learned range must work as an ordinary profile
                det = FishDetector(DetectionProfile(name="t", window_lower=lower, window_upper=upper))
                screen = SimScreen(hsv_to_bgr(*hsv), hsv_to_bgr(0, 0, 30))
                self.assertIsNone(det.find_fishing_window_bounds(screen.render()))
                screen.panel_open = True
                found = det.find_fishing_window_bounds(screen.render())
                self.assertIsNotNone(found)
                self.assertLessEqual(abs(found[0] - 260), 3)

    def test_ignores_unchanged_scene(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        frame = screen.render()
        self.assertIsNone(WindowLearner().learn(frame, frame.copy()))

    def test_ignores_small_or_irregular_changes(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        before = screen.render()
        after = before.copy()
        after[300:340, 380:420] = (0, 255, 0)            # small blob (character animation)
        rng = np.random.default_rng(1)
        ys = rng.integers(100, 500, 4000)
        xs = rng.integers(100, 700, 4000)
        after[ys, xs] = (255, 255, 255)                  # scattered sparkle
        self.assertIsNone(WindowLearner().learn(before, after))

    def test_ignores_fullscreen_change(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        before = screen.render()
        after = np.full_like(before, 200)                # loading screen / map change
        self.assertIsNone(WindowLearner().learn(before, after))


class FishLearnerTests(unittest.TestCase):
    def _run(self, panel_hsv, fish_hsv, seconds=1.5, dt=0.02):
        panel_bgr = hsv_to_bgr(*panel_hsv)
        screen = SimScreen(panel_bgr, hsv_to_bgr(*fish_hsv))
        before = screen.render()
        screen.panel_open = True
        bounds, lower, upper, pixels = WindowLearner().learn(before, screen.render())
        det = FishDetector(DetectionProfile(name="learned", window_lower=lower, window_upper=upper,
                                            fish_lower=None, fish_upper=None, learned=True))
        det.calibrate(pixels)
        self.assertTrue(det.learning)

        x, y, w, h = bounds
        # Drive the learner with a virtual clock
        learner = det._learner
        real_feed = learner.feed
        learner.feed = lambda f, hsv, m, now=None: real_feed(f, hsv, m, now=screen.t)

        errors = []
        steps = int(seconds / dt)
        for _ in range(steps):
            screen.t += dt
            active, pos = det.detect_window_and_fish(screen.grab(x, y, w, h))
            self.assertTrue(active)
            if pos is not None:
                fx, fy = screen.fish_center()
                errors.append(max(abs(pos[0] - (fx - x)), abs(pos[1] - (fy - y))))
        return det, screen, bounds, errors

    def test_learns_fish_color_and_tracks_it(self):
        cases = {
            "red fish on green": ((60, 180, 200), (2, 210, 200)),
            "yellow fish on purple": ((140, 150, 160), (28, 220, 230)),
            "dark fish on cyan": ((102, 200, 220), (104, 138, 60)),
            "white fish on black": ((0, 0, 8), (0, 0, 240)),
        }
        for name, (panel, fish) in cases.items():
            with self.subTest(case=name):
                det, screen, bounds, errors = self._run(panel, fish)
                self.assertFalse(det.learning, "learning should have completed")
                self.assertTrue(det.profile_updated)
                self.assertIsNotNone(det.profile.fish_lower)
                self.assertGreater(len(errors), 20)
                self.assertLessEqual(float(np.median(errors)), 12)

                # After learning: color path, accurate, and the static circle
                # outline is never mistaken for the fish.
                x, y, w, h = bounds
                post = []
                for _ in range(60):
                    screen.t += 0.03
                    _, pos = det.detect_window_and_fish(screen.grab(x, y, w, h))
                    self.assertIsNotNone(pos)
                    fx, fy = screen.fish_center()
                    post.append(max(abs(pos[0] - (fx - x)), abs(pos[1] - (fy - y))))
                self.assertLessEqual(max(post), 9)

    def test_static_scene_never_finishes(self):
        learner = FishLearner()
        frame = np.full((220, 280, 3), 120, np.uint8)
        hsv = np.zeros_like(frame)
        mask = np.full((220, 280), 255, np.uint8)
        for i in range(100):
            self.assertIsNone(learner.feed(frame, hsv, mask, now=i * 0.02))
        self.assertFalse(learner.done)


if __name__ == "__main__":
    unittest.main()

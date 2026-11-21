"""Minigame variants seen on other clients: the click circle somewhere else
or a different size, a filled disc instead of an outline, a textured panel,
decorations in the fish's own color, the standard panel with another fish —
and the failure paths (false detection that never closes, bad saved profile)."""

import threading
import time
import unittest

import cv2
import numpy as np

from _stubs import install, hsv_to_bgr, SimScreen, FakeWindowManager, FakeMouse

install()

from fish_detector import (  # noqa: E402
    BackgroundModel, DetectionProfile, FishDetector, find_click_circle)
from fishing_bot import FishingBot, RecastWaitTuner  # noqa: E402

OFFICIAL_PANEL = hsv_to_bgr(102, 200, 220)
OFFICIAL_FISH = hsv_to_bgr(104, 138, 120)


class SkinScreen(SimScreen):
    """SimScreen with a configurable look."""

    def __init__(self, panel_bgr, fish_bgr, circle=None, disc_bgr=None, textured=False,
                 decoration_bgr=None, timer_bgr=None, animated=False, **kw):
        super().__init__(panel_bgr, fish_bgr, **kw)
        x, y, w, h = self.PANEL
        # (dx, dy, r): circle center relative to the panel, and its radius
        self.circle = circle or (w // 2, h // 2, self.RADIUS)
        self.disc_bgr = disc_bgr
        self.decoration_bgr = decoration_bgr
        self.timer_bgr = timer_bgr
        self.texture = None
        self.animated = animated
        if animated:
            # Water: brightness waves that travel across the panel, plus foam
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            self._wave_phase = xx * 0.09 + yy * 0.05
            self._panel_hsv = cv2.cvtColor(np.uint8([[panel_bgr]]), cv2.COLOR_BGR2HSV)[0, 0]
        if textured:
            rng = np.random.default_rng(3)
            noise = cv2.GaussianBlur(rng.integers(-22, 22, size=(h, w)).astype(np.float32), (7, 7), 0)
            self.texture = np.clip(np.array(panel_bgr, np.float32) + noise[:, :, None] * 3,
                                   0, 255).astype(np.uint8)

    def circle_abs(self):
        x, y, _, _ = self.PANEL
        dx, dy, r = self.circle
        return x + dx, y + dy, r

    def fish_center(self):
        cx, cy, _ = self.circle_abs()
        return (int(cx + 95 * np.sin(self.t * 2.2)), int(cy + 22 * np.cos(self.t * 1.3)))

    def fish_in_circle(self):
        cx, cy, r = self.circle_abs()
        fx, fy = self.fish_center()
        return (fx - cx) ** 2 + (fy - cy) ** 2 < r ** 2

    def render(self):
        frame = self.background.copy()
        if self.blank:
            frame[:] = 0
            return frame
        if not self.panel_open:
            return frame
        x, y, w, h = self.PANEL
        if self.animated:
            hue, sat, val = (int(v) for v in self._panel_hsv)
            wave = np.sin(self._wave_phase + self.t * 5.0)
            hsv = np.empty((h, w, 3), np.uint8)
            hsv[:, :, 0] = hue
            hsv[:, :, 1] = sat
            hsv[:, :, 2] = np.clip(val + wave * 28, 0, 255)
            water = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            water[wave > 0.985] = (255, 255, 255)          # thin moving foam lines
            frame[y:y + h, x:x + w] = water
        elif self.texture is not None:
            frame[y:y + h, x:x + w] = self.texture
        else:
            cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), self.panel_bgr, -1)
        cx, cy, r = self.circle_abs()
        if self.disc_bgr is not None:
            cv2.circle(frame, (cx, cy), r, self.disc_bgr, -1)
        else:
            cv2.circle(frame, (cx, cy), r, (255, 255, 255), 2)
        if self.decoration_bgr is not None:
            # A fish-colored icon that never moves, well outside the circle
            cv2.ellipse(frame, (x + 30, y + 24), (16, 8), 0, 0, 360, self.decoration_bgr, -1)
        if self.timer_bgr is not None:
            left = max(0.0, 1.0 - self.t / 15.0)
            cv2.rectangle(frame, (x + 20, y + h - 14), (x + 20 + int((w - 40) * left), y + h - 8),
                          self.timer_bgr, -1)
        cv2.ellipse(frame, self.fish_center(), (14, 7), 0, 0, 360, self.fish_bgr, -1)
        return frame


def make_bot(screen, config=None, keep_shared=False):
    FishingBot._shared_learned_profile = None
    FishingBot._known_profiles = []
    if not keep_shared:
        FishingBot._shared_recast_wait = None
    bot = FishingBot(None, dict(config or {}), FakeWindowManager(), bait_counter=10, bot_id=0)
    mouse = FakeMouse()
    bot.human.mouse = mouse
    bot.mouse_backend = mouse
    bot._grab = screen.grab
    bot.running = True
    logs = []
    bot.on_status_update = logs.append
    return bot, mouse, logs


def play(bot, screen, seconds):
    """Runs the click loop against the simulated screen in real time.
    Returns the clicks as (in_circle_at_click_time, seconds_into_run)."""
    results = []
    mouse = bot.human.mouse
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        screen.t = time.perf_counter() - t0
        before = len(mouse.clicks)
        active, _ = bot.atomic_capture_and_click()
        assert active
        if len(mouse.clicks) > before:
            results.append((screen.fish_in_circle(), screen.t))
        if bot.detector.profile_updated:
            bot._publish_learned_profile()
        time.sleep(0.004)
    return results


def adapt(test, screen, config=None):
    bot, mouse, logs = make_bot(screen, dict({"adaptive_detection": True}, **(config or {})))
    bot._ADAPTIVE_AFTER = 0.0
    bot._reference_frame = bot.capture_full_window()
    screen.panel_open = True
    test.assertTrue(bot.wait_for_minigame_window(timeout=2.0))
    test.assertTrue(bot.detector.profile.learned)
    return bot, mouse, logs


class ClickCircleTests(unittest.TestCase):
    def _panel(self):
        return np.full((220, 280, 3), (200, 120, 40), np.uint8)

    def test_outline_and_disc(self):
        outline = self._panel()
        cv2.circle(outline, (150, 100), 55, (255, 255, 255), 2)
        disc = self._panel()
        cv2.circle(disc, (120, 118), 62, (230, 170, 90), -1)
        for name, img, want in (("outline", outline, (150, 100, 55)), ("disc", disc, (120, 118, 62))):
            with self.subTest(case=name):
                found = find_click_circle(img)
                self.assertIsNotNone(found)
                for got, exp in zip(found, want):
                    self.assertLessEqual(abs(got - exp), 3)

    def test_rejects_panels_without_a_circle(self):
        plain = self._panel()
        self.assertIsNone(find_click_circle(plain))
        boxes = self._panel()
        cv2.rectangle(boxes, (60, 50), (220, 170), (255, 255, 255), 2)
        cv2.circle(boxes, (262, 14), 9, (255, 255, 255), -1)      # close button
        cv2.ellipse(boxes, (140, 110), (60, 60), 0, 200, 290, (255, 255, 255), 2)  # a short arc
        self.assertIsNone(find_click_circle(boxes))
        self.assertIsNone(find_click_circle(np.zeros((30, 30, 3), np.uint8)))

    def test_profile_keeps_the_circle(self):
        p = DetectionProfile(name="learned", circle_center=(150, 100), circle_radius=50, learned=True)
        back = DetectionProfile.from_dict(p.to_dict())
        self.assertEqual(back.circle_center, (150, 100))
        self.assertEqual(back.circle_radius, 50)
        self.assertIsNone(DetectionProfile.from_dict({"circle_center": [1, 2, 3]}))
        self.assertIsNone(DetectionProfile.from_dict({}).circle_center)
        # A center outside the panel is ignored
        det = FishDetector(DetectionProfile(circle_center=(900, 10)))
        self.assertEqual(det.circle_center_for(280, 220), (140, 110))


class BackgroundModelTests(unittest.TestCase):
    def test_median_removes_the_moving_fish(self):
        model = BackgroundModel()
        base = np.full((100, 160, 3), 90, np.uint8)
        cv2.circle(base, (80, 50), 30, (255, 255, 255), 2)
        for i in range(BackgroundModel.FULL):
            frame = base.copy()
            cv2.circle(frame, (10 + i * 5, 50), 8, (0, 0, 255), -1)
            model.add(frame, now=i * 1.0)
        self.assertTrue(model.complete)
        self.assertTrue(np.array_equal(model.image, base))

        frame = base.copy()
        cv2.circle(frame, (40, 20), 8, (0, 0, 255), -1)
        changed = model.changed(frame)
        self.assertEqual(changed[20, 40], 255)
        self.assertEqual(cv2.countNonZero(changed[40:, :]), 0)   # the outline did not "change"
        self.assertIsNone(model.changed(np.zeros((10, 10, 3), np.uint8)))

    def test_samples_are_rate_limited(self):
        model = BackgroundModel()
        frame = np.zeros((20, 20, 3), np.uint8)
        for i in range(100):
            model.add(frame, now=i * 0.001)
        self.assertLessEqual(model.count, 3)


class SkinTests(unittest.TestCase):
    def _assert_all_in_circle(self, results, min_clicks=3, after=0.0):
        clicks = [ok for ok, t in results if t >= after]
        self.assertGreaterEqual(len(clicks), min_clicks, "too few clicks")
        self.assertTrue(all(clicks), f"{clicks.count(False)} of {len(clicks)} clicks outside the circle")

    def test_small_off_center_circle_is_learned(self):
        screen = SkinScreen(hsv_to_bgr(60, 180, 200), hsv_to_bgr(2, 210, 200), circle=(165, 95, 42))
        bot, _mouse, logs = adapt(self, screen)
        play(bot, screen, 1.6)                      # learning phase
        profile = bot.detector.profile
        self.assertIsNotNone(profile.circle_center, logs)
        self.assertLessEqual(abs(profile.circle_center[0] - 165), 4)
        self.assertLessEqual(abs(profile.circle_center[1] - 95), 4)
        self.assertLess(profile.circle_radius, 42)
        self.assertGreater(profile.circle_radius, 33)
        # From here on every click has to be inside the real (small) circle
        self._assert_all_in_circle(play(bot, screen, 3.0))

    def test_filled_disc_does_not_hide_the_fish(self):
        screen = SkinScreen(hsv_to_bgr(110, 200, 150), hsv_to_bgr(25, 230, 240),
                            disc_bgr=hsv_to_bgr(110, 120, 215))
        bot, _mouse, _logs = adapt(self, screen)
        play(bot, screen, 1.6)
        self.assertFalse(bot.detector.learning)
        self._assert_all_in_circle(play(bot, screen, 3.0))

    def test_textured_panel_with_timer_and_fish_colored_icon(self):
        fish = hsv_to_bgr(2, 210, 200)
        screen = SkinScreen(hsv_to_bgr(60, 180, 190), fish, textured=True,
                            decoration_bgr=fish, timer_bgr=hsv_to_bgr(30, 255, 255))
        bot, mouse, _logs = adapt(self, screen)
        play(bot, screen, 1.6)
        self.assertFalse(bot.detector.learning)
        results = play(bot, screen, 3.0)
        self._assert_all_in_circle(results)
        # ... and each click is on the fish, not on the icon or the timer bar
        x, y, _, _ = SkinScreen.PANEL
        for (px, py), _b in mouse.clicks[-len(results):]:
            self.assertGreater(px, x + 60)
            self.assertLess(py, y + 200)

    def test_animated_water_background(self):
        screen = SkinScreen(hsv_to_bgr(78, 190, 170), hsv_to_bgr(20, 230, 240), animated=True)
        bot, _mouse, logs = adapt(self, screen)
        play(bot, screen, 4.5)                       # learning + refined background
        self.assertFalse(bot.detector.learning, logs)
        self.assertTrue(bot.detector._background.complete)
        self._assert_all_in_circle(play(bot, screen, 3.0), min_clicks=5)

    def test_learned_circle_is_reused_from_a_saved_profile(self):
        screen = SkinScreen(hsv_to_bgr(60, 180, 200), hsv_to_bgr(2, 210, 200), circle=(165, 95, 42))
        bot, _mouse, _logs = adapt(self, screen)
        saved = []
        bot.on_profile_learned = saved.append
        play(bot, screen, 1.6)
        self.assertTrue(saved and saved[-1]["circle_center"])

        screen2 = SkinScreen(hsv_to_bgr(60, 180, 200), hsv_to_bgr(2, 210, 200), circle=(165, 95, 42))
        bot2, _m2, _l2 = make_bot(screen2, {"detection_profile": saved[-1]})
        screen2.panel_open = True
        self.assertTrue(bot2.wait_for_minigame_window(timeout=2.0))
        self.assertLessEqual(abs(bot2._circle_center[0] - 165), 4)   # known before any learning
        bot2._ring_turns_red = False   # simulated ring is never red: test the circle rule (see test_ring_gate.py)
        self._assert_all_in_circle(play(bot2, screen2, 2.5))


class ClickPredictionTests(unittest.TestCase):
    def _bot(self):
        screen = SkinScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, _ = make_bot(screen, {"timing_cursor_settle": 0.02})
        screen.panel_open = True
        self.assertTrue(bot.wait_for_minigame_window(timeout=1.0))
        return bot

    def _track(self, bot, speed, end, now=100.0):
        """A fish that moved at ``speed`` px/s and is at ``end`` now."""
        bot._fish_track.clear()
        for i in range(6, -1, -1):
            t = now - i * 0.012
            bot._fish_track.append((t, end[0] - speed[0] * i * 0.012, end[1] - speed[1] * i * 0.012))
        return now

    def test_aims_ahead_of_a_moving_fish(self):
        bot = self._bot()
        cx, cy = bot._circle_center
        now = self._track(bot, (400.0, 0.0), (cx, cy))
        target = bot._click_point((cx, cy), now)
        lead = 400.0 * (0.02 + bot._CLICK_OVERHEAD)
        self.assertLessEqual(abs(target[0] - (cx + lead)), 1.5)
        self.assertEqual(target[1], cy)

    def test_skips_a_fish_that_is_leaving_the_circle(self):
        bot = self._bot()
        cx, cy = bot._circle_center
        edge = (cx + 64, cy)                         # inside (radius 67), heading out fast
        now = self._track(bot, (400.0, 0.0), edge)
        self.assertIsNone(bot._click_point(edge, now))
        now = self._track(bot, (-400.0, 0.0), edge)  # same spot, heading in
        self.assertIsNotNone(bot._click_point(edge, now))

    def test_no_or_stale_track_clicks_the_fish_itself(self):
        bot = self._bot()
        cx, cy = bot._circle_center
        bot._fish_track.clear()
        self.assertEqual(bot._click_point((cx + 10, cy), 50.0), (cx + 10, cy))
        now = self._track(bot, (400.0, 0.0), (cx, cy))
        self.assertEqual(bot._click_point((cx + 10, cy), now + 1.0), (cx + 10, cy))


class RecastWaitTests(unittest.TestCase):
    def _drive(self, tuner, needed, casts=40):
        """Casts succeed only when the wait is at least what the server needs."""
        misses = 0
        for _ in range(casts):
            wait = tuner.wait
            ok = wait >= needed
            misses += not ok
            tuner.report(wait, ok)
        return misses

    def test_finds_the_shortest_working_wait(self):
        tuner = RecastWaitTuner(4.0)
        misses = self._drive(tuner, needed=1.8)
        self.assertEqual(misses, 1)                  # exactly one probe went too far
        self.assertTrue(tuner.settled)
        self.assertGreaterEqual(tuner.wait, 1.8)
        self.assertLess(tuner.wait, 1.8 / RecastWaitTuner.STEP + 0.01)

    def test_never_goes_below_the_floor_or_above_the_setting(self):
        tuner = RecastWaitTuner(4.0)
        self.assertEqual(self._drive(tuner, needed=0.0), 0)
        self.assertGreaterEqual(tuner.wait, RecastWaitTuner.MIN_WAIT)
        self.assertLess(tuner.wait, RecastWaitTuner.MIN_WAIT + 0.1)
        self.assertTrue(tuner.settled)
        slow = RecastWaitTuner(4.0)
        self._drive(slow, needed=99.0, casts=10)     # nothing ever works (e.g. no bait)
        self.assertEqual(slow.wait, 4.0)

    def test_unrelated_miss_does_not_move_a_proven_wait(self):
        tuner = RecastWaitTuner(4.0)
        self._drive(tuner, needed=1.8)
        settled = tuner.wait
        tuner.report(settled, False)                 # lag spike
        self.assertEqual(tuner.wait, settled)
        tuner.report(settled, True)
        tuner.report(settled, False)
        tuner.report(settled, False)                 # twice in a row: give a step back
        self.assertGreater(tuner.wait, settled)
        self.assertLessEqual(tuner.wait, 4.0)

    def test_bot_uses_and_reports_the_wait(self):
        screen = SkinScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _m, logs = make_bot(screen, {"timing_recast_wait": 2.0})
        self.assertEqual(bot._next_recast_wait(), 2.0)
        for _ in range(RecastWaitTuner.CONFIRM):
            bot._report_cast(bot._next_recast_wait(), True)
        self.assertLess(bot._next_recast_wait(), 2.0)
        # A second window adopts what the first one settled on, without probing
        while not bot._recast_tuner.settled:
            wait = bot._next_recast_wait()
            bot._report_cast(wait, wait >= 1.2)
        settled = bot._recast_tuner.wait
        self.assertEqual(FishingBot._shared_recast_wait, settled)
        other, _m, other_logs = make_bot(screen, {"timing_recast_wait": 2.0}, keep_shared=True)
        self.assertEqual(other._next_recast_wait(), settled)
        self.assertTrue(other._recast_tuner.settled)
        FishingBot._shared_recast_wait = None

        off, _m, _l = make_bot(screen, {"timing_recast_wait": 2.0, "adaptive_recast_wait": False})
        self.assertIsNone(off._recast_tuner)
        self.assertEqual(off._next_recast_wait(), 2.0)


class GameLoopAdaptTests(unittest.TestCase):
    def _run(self, bot, screen, mouse, hits_to_win=3, panel_closes=True):
        state = {"hits": 0, "casts": 0, "t0": time.perf_counter()}

        def cast():
            state["casts"] += 1
            state["hits"] = 0
            screen.panel_open = True

        def on_click(button="left"):
            mouse.clicks.append((mouse.pos, button))
            if screen.fish_in_circle():
                state["hits"] += 1
                if panel_closes and state["hits"] >= hits_to_win:
                    screen.panel_open = False

        def grab(left, top, w, h):
            screen.t = time.perf_counter() - state["t0"]
            return screen.grab(left, top, w, h)

        mouse.mouse_down = on_click
        bot._grab = grab
        bot.bait_and_cast = cast
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._resume_inventory_check = lambda: None
        bot.handle_caught_item = lambda may_have_catch=True, baseline=None, **kw: None
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        return state, thread

    def test_standard_panel_with_a_different_fish(self):
        """Stock panel colors match, the fish does not: learn it by motion."""
        screen = SkinScreen(OFFICIAL_PANEL, hsv_to_bgr(15, 230, 240))
        bot, mouse, logs = make_bot(screen, {
            "timing_recast_wait": 0.0, "human_like_clicking": False, "quick_skip": False,
            "adaptive_detection": True})
        bot.bait_counter = 2
        bot._FISH_MISSING_AFTER = 0.5
        saved = []
        bot.on_profile_learned = saved.append

        state, thread = self._run(bot, screen, mouse)
        thread.join(timeout=40)
        self.assertFalse(thread.is_alive(), "play_game did not finish")
        self.assertEqual(bot.total_games, 2, logs)
        self.assertEqual(state["casts"], 2)
        self.assertTrue(bot.detector.profile.learned)
        self.assertTrue(any("learning the fish" in m for m in logs))
        self.assertTrue(saved and saved[-1]["fish_lower"] is not None)

    def test_official_client_never_switches_to_learning(self):
        screen = SkinScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen, {
            "timing_recast_wait": 0.0, "human_like_clicking": False, "quick_skip": False,
            "adaptive_detection": True})
        bot.bait_counter = 2
        bot._FISH_MISSING_AFTER = 0.05
        state, thread = self._run(bot, screen, mouse)
        thread.join(timeout=40)
        self.assertFalse(thread.is_alive())
        self.assertEqual(bot.detector.profile.name, "official")
        self.assertFalse(bot.detector.profile.learned)

    def test_panel_that_never_closes_is_abandoned(self):
        screen = SkinScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen, {
            "timing_recast_wait": 0.0, "human_like_clicking": False, "quick_skip": False,
            "timing_minigame_timeout": 1.0})
        bot.bait_counter = 3
        bot._MAX_GAME_SECONDS = 0.6
        state, thread = self._run(bot, screen, mouse, panel_closes=False)
        deadline = time.time() + 20
        while time.time() < deadline and not any("false detection" in m for m in logs):
            time.sleep(0.05)
        bot.running = False
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(any("false detection" in m for m in logs), logs)
        self.assertEqual(bot.total_games, 0)
        self.assertEqual(bot.bait_counter, 3)       # no bait was charged for it

    def test_bad_saved_profile_is_forgotten(self):
        screen = SkinScreen(hsv_to_bgr(60, 180, 200), hsv_to_bgr(2, 210, 200))
        bad = DetectionProfile(name="learned", window_lower=(55, 150, 170), window_upper=(65, 210, 230),
                               fish_lower=(140, 200, 200), fish_upper=(150, 255, 255), learned=True)
        bot, _mouse, logs = make_bot(screen, {"detection_profile": bad.to_dict()})
        told = []
        bot.on_profile_learned = told.append
        bot._discard_learned_profile()
        self.assertEqual(told, [None])
        self.assertIsNone(bot.config["detection_profile"])
        self.assertEqual(bot.detector.profile.name, "official")
        self.assertFalse(bot.region_auto_calibrated)


if __name__ == "__main__":
    unittest.main()

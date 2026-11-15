import os
import threading
import time
import unittest

import cv2
import numpy as np

from _stubs import install, hsv_to_bgr, SimScreen, FakeWindowManager, FakeMouse, ROOT

install()

from fishing_bot import FishingBot  # noqa: E402
from utils import PriorityInputLock  # noqa: E402

OFFICIAL_PANEL = hsv_to_bgr(102, 200, 220)
OFFICIAL_FISH = hsv_to_bgr(104, 138, 120)


def make_bot(screen, config=None):
    FishingBot._shared_learned_profile = None
    FishingBot._shared_recast_wait = None
    FishingBot._known_profiles = []
    bot = FishingBot(None, dict(config or {}), FakeWindowManager(), bait_counter=10, bot_id=0)
    mouse = FakeMouse()
    bot.human.mouse = mouse
    bot.mouse_backend = mouse
    bot._grab = screen.grab
    bot.running = True
    logs = []
    bot.on_status_update = logs.append
    return bot, mouse, logs


def assert_clicks_on_fish(test, screen, mouse):
    """Every click must land inside the click circle."""
    x, y, w, h = SimScreen.PANEL
    cx, cy = x + w // 2, y + h // 2
    test.assertGreater(len(mouse.clicks), 0, "bot never clicked")
    for (px, py), _button in mouse.clicks:
        test.assertLess((px - cx) ** 2 + (py - cy) ** 2, SimScreen.RADIUS ** 2 + 1)


class OfficialClientTests(unittest.TestCase):
    def test_calibrates_and_clicks_fish_in_circle(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen)

        self.assertFalse(bot.wait_for_minigame_window(timeout=0.2))  # panel closed
        screen.panel_open = True
        self.assertTrue(bot.wait_for_minigame_window(timeout=1.0))
        self.assertEqual((bot.region.left, bot.region.top, bot.region.width, bot.region.height),
                         SimScreen.PANEL)
        self.assertEqual(bot._circle_radius_sq, 67 * 67)
        self.assertEqual(bot.detector.profile.name, "official")

        # This test steps a virtual clock; speed prediction needs real time.
        bot._TRACK_MIN_SPAN = float("inf")
        outside_clicks = 0
        for _ in range(150):
            screen.t += 0.02
            before = len(mouse.clicks)
            active, pos = bot.atomic_capture_and_click()
            self.assertTrue(active)
            if len(mouse.clicks) > before:
                # Clicked exactly on the fish, and only while it is in the circle
                self.assertTrue(screen.fish_in_circle())
                fx, fy = screen.fish_center()
                (px, py), _ = mouse.clicks[-1]
                self.assertLessEqual(max(abs(px - fx), abs(py - fy)), 2)
            elif pos is not None:
                outside_clicks += 1
        self.assertEqual(outside_clicks, 0)
        assert_clicks_on_fish(self, screen, mouse)

    def test_panel_that_moved_is_recalibrated(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _mouse, logs = make_bot(screen)
        screen.panel_open = True
        self.assertTrue(bot.wait_for_minigame_window(timeout=1.0))

        SimScreen.PANEL = (60, 300, 280, 220)  # player dragged the panel
        try:
            bot._RECALIBRATE_AFTER = 0.1
            self.assertTrue(bot.wait_for_minigame_window(timeout=2.0))
            self.assertEqual((bot.region.left, bot.region.top), (60, 300))
            self.assertTrue(any("Re-calibrated" in m for m in logs))
        finally:
            SimScreen.PANEL = (260, 150, 280, 220)

    def test_bite_timeout_adapts(self):
        bot, _, _ = make_bot(SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH))
        self.assertEqual(bot._bite_timeout(), 6.0)
        bot.consecutive_failures = 1
        self.assertEqual(bot._bite_timeout(), 9.0)
        bot.consecutive_failures = 2
        self.assertEqual(bot._bite_timeout(), 13.5)
        bot.consecutive_failures = 4
        self.assertEqual(bot._bite_timeout(), 30.0)   # growth is capped
        bot.consecutive_failures = 0
        bot._longest_bite = 10.0                      # slow-bite server seen once
        self.assertEqual(bot._bite_timeout(), 16.0)


class ReskinnedClientTests(unittest.TestCase):
    """A client with a green panel and a red fish — nothing matches the stock colors."""

    def test_adapts_and_clicks(self):
        screen = SimScreen(hsv_to_bgr(60, 180, 200), hsv_to_bgr(2, 210, 200))
        bot, mouse, logs = make_bot(screen, {"adaptive_detection": True})
        bot._ADAPTIVE_AFTER = 0.0
        learned = []
        bot.on_profile_learned = learned.append

        bot._reference_frame = bot.capture_full_window()   # taken just before the cast
        screen.panel_open = True
        self.assertTrue(bot.wait_for_minigame_window(timeout=2.0))
        self.assertTrue(bot.detector.profile.learned)
        for got, want in zip((bot.region.left, bot.region.top, bot.region.width, bot.region.height),
                             SimScreen.PANEL):
            self.assertLessEqual(abs(got - want), 3)

        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 3.0:
            screen.t = time.perf_counter() - t0
            active, _ = bot.atomic_capture_and_click()
            self.assertTrue(active)
            if bot.detector.profile_updated:
                bot._publish_learned_profile()
            time.sleep(0.004)

        assert_clicks_on_fish(self, screen, mouse)
        self.assertEqual(len(learned), 1)
        self.assertIsNotNone(learned[0]["fish_lower"])
        self.assertIs(FishingBot._shared_learned_profile, bot.detector.profile)

        # A second window of the same client reuses the profile instead of relearning
        bot2, _, _ = make_bot(screen)
        FishingBot._shared_learned_profile = bot.detector.profile
        bot2._ADAPTIVE_AFTER = 0.0
        self.assertTrue(bot2.wait_for_minigame_window(timeout=2.0))
        self.assertIs(bot2.detector.profile, bot.detector.profile)

    def test_disabled_adaptive_does_nothing(self):
        screen = SimScreen(hsv_to_bgr(60, 180, 200), hsv_to_bgr(2, 210, 200))
        bot, _mouse, _ = make_bot(screen, {"adaptive_detection": False})
        bot._ADAPTIVE_AFTER = 0.0
        bot._reference_frame = bot.capture_full_window()
        screen.panel_open = True
        self.assertFalse(bot.wait_for_minigame_window(timeout=0.4))
        self.assertFalse(bot.region_auto_calibrated)


class GameLoopTests(unittest.TestCase):
    def test_capture_hiccup_does_not_end_the_game(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen, {
            "timing_recast_wait": 0.0, "human_like_clicking": False, "quick_skip": False,
        })
        bot.bait_counter = 2
        state = {"hits": 0, "casts": 0, "blanked": 0, "t0": time.perf_counter()}

        def cast():
            state["casts"] += 1
            state["hits"] = 0
            screen.panel_open = True

        def on_click(button="left"):
            mouse.clicks.append((mouse.pos, button))
            if screen.fish_in_circle():
                state["hits"] += 1
                if state["hits"] == 1 and not state["blanked"]:
                    state["blanked"] = 1
                    screen.blank = True          # one bad frame right after the first hit
                if state["hits"] >= 3:
                    screen.panel_open = False    # caught

        def grab(left, top, w, h):
            screen.t = time.perf_counter() - state["t0"]
            frame = screen.grab(left, top, w, h)
            screen.blank = False
            return frame

        handled = []
        mouse.mouse_down = on_click
        bot._grab = grab
        bot.bait_and_cast = cast
        bot._startup_scan_and_process_all_pages = lambda: None
        bot.handle_caught_item = lambda may_have_catch=True: handled.append(may_have_catch)

        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "play_game did not finish")

        self.assertEqual(state["blanked"], 1)
        self.assertEqual(state["casts"], 2)          # the hiccup did not cause a recast
        self.assertEqual(bot.total_games, 2)
        self.assertEqual(handled, [True, True])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_BAIT_DEPLETED)


class InputSafetyTests(unittest.TestCase):
    """Nothing is typed or clicked unless this bot's window is in front and
    the bot is running."""

    def _bot(self, config=None):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen, config)
        keys = bot.keyboard_controller.events
        return bot, screen, mouse, keys, logs

    def _try_everything(self, bot):
        bot.bait_and_cast()
        bot.press_key("space")
        bot.press_ctrl_key("g")
        bot.config.update(quick_skip_mode="armor", armor_slot_pos=[50, 50])
        bot.quickskip()
        bot.config.update(quick_skip_mode="horse")
        bot.quickskip()
        bot._handle_item("Carp_living.jpg", 40, 40, "open")
        bot._goto_page((700, 222), settle=0.0)

    def test_nothing_is_sent_when_the_window_cannot_be_focused(self):
        bot, screen, mouse, keys, logs = self._bot()
        bot.window_manager.activate_window = lambda force_activate=False: False
        self._try_everything(bot)
        screen.panel_open = True
        screen.t = 0.0                                   # fish in the middle of the circle
        bot.window_manager.activate_window = lambda force_activate=False: True
        self.assertTrue(bot.wait_for_minigame_window(timeout=1.0))
        bot.window_manager.activate_window = lambda force_activate=False: False
        self.assertEqual(bot.atomic_capture_and_click(), (True, None))
        self.assertEqual((keys, mouse.clicks, mouse.pos), ([], [], (0, 0)))
        self.assertEqual(sum("Could not bring the game window" in m for m in logs), 1)   # said once

    def test_failed_cast_is_reported_to_the_caller(self):
        bot, _screen, _mouse, keys, _logs = self._bot()
        self.assertTrue(bot.bait_and_cast())
        self.assertIn(("press", "space"), keys)
        bot.window_manager.activate_window = lambda force_activate=False: False
        self.assertFalse(bot.bait_and_cast())

    def test_nothing_is_sent_after_stop(self):
        bot, _screen, mouse, keys, _logs = self._bot()
        bot.running = False
        self._try_everything(bot)
        self.assertEqual((keys, mouse.clicks, mouse.pos), ([], [], (0, 0)))

    def test_no_quick_skip_after_the_inventory_filled_up(self):
        """The game that fills the last slot stops the bot; the keys of the
        quick skip that used to follow went to a game nobody was playing."""
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen, {"quick_skip": True, "quick_skip_mode": "horse"})
        keys = bot.keyboard_controller.events
        bot._startup_scan_and_process_all_pages = lambda: None
        bot.bait_and_cast = lambda: setattr(screen, "panel_open", True) or True

        def full(may_have_catch=True):
            screen.panel_open = False
            bot._inventory_full_stop()

        bot._play_minigame = lambda: (full(), (False, 3))[1]
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(bot.stop_reason, FishingBot.STOP_INVENTORY_FULL)
        self.assertEqual(keys, [])


class PauseTests(unittest.TestCase):
    def test_pause_while_waiting_for_the_bite_is_not_a_missed_bite(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _mouse, logs = make_bot(screen, {"timing_minigame_timeout": 1.0, "timing_recast_wait": 0.0})
        bot._startup_scan_and_process_all_pages = lambda: None
        casts = []

        def cast():
            casts.append(time.perf_counter())
            if len(casts) == 1:
                bot.paused = True                        # the player takes over right after the cast
                threading.Timer(1.5, lambda: setattr(bot, "paused", False)).start()
            else:
                bot.running = False
            return True

        bot.bait_and_cast = cast
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(casts), 2)
        self.assertGreaterEqual(casts[1] - casts[0], 1.4)   # waited for the resume, then cast again
        self.assertEqual(bot.consecutive_failures, 0)
        self.assertFalse(any("not detected" in m for m in logs), logs)

    def test_cast_after_a_pause_is_not_fed_to_the_recast_tuner(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _mouse, _logs = make_bot(screen, {"timing_recast_wait": 0.3, "adaptive_recast_wait": True})
        bot._game_end_time = time.perf_counter()
        bot.paused = True
        threading.Timer(0.5, lambda: setattr(bot, "paused", False)).start()
        self.assertTrue(bot._wait_before_recast())
        self.assertIsNone(bot._last_cast_wait)

    def test_recast_wait_counts_from_the_end_of_the_game(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _mouse, _logs = make_bot(screen, {"timing_recast_wait": 0.6, "adaptive_recast_wait": True})
        bot._game_end_time = time.perf_counter() - 0.5   # item handling took half a second
        start = time.perf_counter()
        self.assertFalse(bot._wait_before_recast())
        self.assertLess(time.perf_counter() - start, 0.35)

        fixed, _, _ = make_bot(screen, {"timing_recast_wait": 0.6, "adaptive_recast_wait": False})
        fixed._game_end_time = time.perf_counter() - 0.5
        start = time.perf_counter()
        fixed._wait_before_recast()
        self.assertGreaterEqual(time.perf_counter() - start, 0.6)   # the fixed wait is slept in full


class InventoryTests(unittest.TestCase):
    def setUp(self):
        FishingBot._template_cache = None
        self.item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        rng = np.random.default_rng(3)
        self.empty = cv2.GaussianBlur(
            rng.integers(30, 70, size=(370, 200, 3), dtype=np.uint8), (5, 5), 0)
        self.appear_at = None
        self.t0 = time.perf_counter()

    def _grab(self, left, top, w, h):
        frame = self.empty.copy()
        if self.appear_at is not None and time.perf_counter() - self.t0 >= self.appear_at:
            ih, iw = self.item.shape[:2]
            frame[100:100 + ih, 60:60 + iw] = self.item
        return frame

    def _bot(self):
        bot, _, _ = make_bot(SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH))
        bot._grab = self._grab
        return bot

    def _expect_carp(self, match):
        self.assertIsNotNone(match)
        name, (x, y) = match
        ih, iw = self.item.shape[:2]
        self.assertEqual(name, "Carp_living.jpg")
        self.assertLessEqual(abs(x - (60 + iw // 2)), 2)
        self.assertLessEqual(abs(y - (100 + ih // 2)), 2)

    def test_item_is_handled_as_soon_as_it_appears(self):
        bot = self._bot()
        self.appear_at = 0.10
        self.t0 = start = time.perf_counter()
        match, _ = bot._find_new_item(allow_extra=True)
        elapsed = time.perf_counter() - start
        self._expect_carp(match)
        self.assertLess(elapsed, 0.3)   # configured wait is 0.4 s

    def test_late_item_is_still_found(self):
        bot = self._bot()
        self.appear_at = 0.65            # after the configured 0.4 s
        self.t0 = time.perf_counter()
        match, _ = bot._find_new_item(allow_extra=True)
        self._expect_carp(match)

    def test_no_extra_wait_without_a_catch(self):
        bot = self._bot()
        self.appear_at = None
        start = time.perf_counter()
        match, _ = bot._find_new_item(allow_extra=False)
        self.assertIsNone(match)
        self.assertLess(time.perf_counter() - start, 0.8)

    def test_item_already_present_is_found_by_full_scan(self):
        bot = self._bot()
        self.appear_at = 0.0             # landed before the baseline was taken
        self.t0 = time.perf_counter() - 1
        match, _ = bot._find_new_item(allow_extra=True)
        self._expect_carp(match)

    def test_ignored_slot_is_skipped(self):
        bot = self._bot()
        self.appear_at = 0.05
        self.t0 = time.perf_counter()
        ih, iw = self.item.shape[:2]
        bot._ignored_positions.add((60 + iw // 2, 100 + ih // 2))
        match, _ = bot._find_new_item(allow_extra=False)
        self.assertIsNone(match)

    def test_page_is_not_given_up_for_one_bad_look(self):
        """A page counts as full only when two looks agree: leaving a page
        that still has room hides every later catch."""
        bot = self._bot()
        looks = iter([[], [(20, 20), (52, 20)]])
        bot._scan_empty_slots = lambda frame: next(looks)
        self.assertTrue(bot._page_has_space(self.empty))
        self.assertEqual(len(bot._empty_slot_positions), 2)
        looks = iter([[], []])
        self.assertFalse(bot._page_has_space(self.empty))

    def test_page_that_went_from_many_free_slots_to_none_is_doubted_first(self):
        bot = self._bot()
        bot._scan_empty_slots = lambda frame: []                 # e.g. a tooltip over the inventory
        free = [(20 + 32 * i, 20) for i in range(5)]
        self.assertTrue(bot._page_has_space(self.empty, free))    # not believed ...
        self.assertTrue(bot._page_has_space(self.empty, free))
        self.assertFalse(bot._page_has_space(self.empty, free))   # ... the third time in a row it is
        self.assertFalse(bot._page_has_space(self.empty, [(20, 20)]))   # one slot left, then none: plausible

    def test_template_cache_is_safe_under_concurrent_start(self):
        FishingBot._template_cache = None
        results, errors = [], []

        def load():
            try:
                bot, _, _ = make_bot(SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH))
                cache = bot._load_template_cache()
                results.append((id(cache), len(list(cache.items()))))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=load) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)      # same object, fully populated, for everyone
        self.assertGreater(results[0][1], 30)


class PriorityLockTests(unittest.TestCase):
    def test_mutual_exclusion(self):
        lock = PriorityInputLock()
        inside, peak = [0], [0]

        def work():
            for _ in range(200):
                with lock:
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                    inside[0] -= 1

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(peak[0], 1)
        self.assertFalse(lock.locked())

    def test_urgent_waiters_go_first_and_normal_waiters_are_fifo(self):
        lock = PriorityInputLock()
        order = []
        lock.acquire()

        def waiter(name, urgent):
            ctx = lock.urgent() if urgent else lock
            with ctx:
                order.append(name)

        threads = []
        for name, urgent in [("inv-1", False), ("inv-2", False), ("fish", True), ("inv-3", False)]:
            t = threading.Thread(target=waiter, args=(name, urgent))
            t.start()
            threads.append(t)
            time.sleep(0.05)   # deterministic arrival order
        self.assertTrue(lock.contended())
        lock.release()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(order, ["fish", "inv-1", "inv-2", "inv-3"])


if __name__ == "__main__":
    unittest.main()

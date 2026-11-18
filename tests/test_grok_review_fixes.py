"""Regression tests for the 2026-11-18 code review (docs/code-review-findings.md).
Each test fails when its problem is back. Stub/simulator only: nothing here
touches the desktop."""

import threading
import time
import unittest
from unittest import mock

from _stubs import install

install()

import numpy as np  # noqa: E402

import dialog_watch  # noqa: E402
import screen_capture  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402
from jigsaw_detector import JigsawDetector, JigsawGridResult  # noqa: E402
from test_reliability import run_bot  # noqa: E402  (also puts tools/ on the path)
from minigame_sim import bench  # noqa: E402
from test_review_fixes import quiet_bot  # noqa: E402


def _drop_config(game):
    cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
    cfg.update({"auto_fish_handling": True, "fish_actions": {n: "drop" for n in game._item_names},
                "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)})
    return cfg


class DropErrorPutsItemBackTests(unittest.TestCase):
    """#3: an error other than a lost focus after the pickup left the item
    on the cursor, with its slot marked occupied."""

    def test_error_after_pickup_puts_the_item_back(self):
        game = bench.quick_game("official", dead_fish_chance=1.0)
        bot, logs = bench.make_bot(game, _drop_config(game))
        real_wait = dialog_watch.wait
        state = {"failed": 0}

        def wait(*args, **kwargs):
            if not state["failed"] and game.carrying is not None:
                state["failed"] = 1
                raise RuntimeError("capture broke")
            return real_wait(*args, **kwargs)

        with mock.patch.object(dialog_watch, "wait", wait):
            run_bot(bot, lambda: state["failed"] and game.stats["caught"] >= 2, timeout=90)
        self.assertEqual(state["failed"], 1)
        self.assertIsNone(game.carrying, "item left on the cursor after the error")
        self.assertTrue(any("item put back" in m for m in logs), logs[-10:])

    def test_stops_when_the_item_cannot_be_put_back(self):
        game = bench.quick_game("official", dead_fish_chance=1.0)
        bot, logs = bench.make_bot(game, _drop_config(game))
        state = {"broken": False}
        real_wait = dialog_watch.wait
        real_inventory = bot.capture_inventory_area

        def wait(*args, **kwargs):
            if game.carrying is not None:
                state["broken"] = True      # from here on nothing can be read
                raise RuntimeError("capture broke")
            return real_wait(*args, **kwargs)

        def capture_inventory_area():
            if state["broken"]:
                raise RuntimeError("capture broke")
            return real_inventory()

        bot.capture_inventory_area = capture_inventory_area
        with mock.patch.object(dialog_watch, "wait", wait):
            run_bot(bot, lambda: bot.stop_reason is not None, timeout=90)
        self.assertTrue(state["broken"])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_ERROR)
        self.assertIn("may still be on the cursor", bot.stop_message or "")


class QuickSkipNotSentTests(unittest.TestCase):
    """#5: a quick skip that sent nothing did not wait out the animation."""

    def _bot(self, mode):
        _, bot, _ = quiet_bot({"quick_skip": True, "quick_skip_mode": mode, "armor_slot_pos": [50, 50]})
        bot._t_qs_between = bot._t_qs_after = 0.0
        waits = []
        bot._wait_before_recast = lambda: waits.append(1) and False
        return bot, waits

    def test_horse_skip_not_sent_waits_and_sends_no_lone_ctrl_g(self):
        bot, waits = self._bot("horse")
        presses = []

        def press(key):
            presses.append(key)
            return False

        bot.press_ctrl_key = press
        bot.quickskip()
        self.assertEqual(presses, ["g"], "a second CTRL+G alone would mount the character")
        self.assertEqual(waits, [1])

    def test_horse_skip_second_press_lost_is_retried_then_waited(self):
        bot, waits = self._bot("horse")
        results = iter([True, False, False])
        bot.press_ctrl_key = lambda key: next(results)
        bot.quickskip()
        self.assertEqual(waits, [1])

    def test_horse_skip_sent_does_not_wait(self):
        bot, waits = self._bot("horse")
        bot.press_ctrl_key = lambda key: True
        bot.quickskip()
        self.assertEqual(waits, [])

    def test_armor_skip_without_focus_waits(self):
        bot, waits = self._bot("armor")
        bot.window_manager.activate_window = lambda force_activate=False: False
        self.assertFalse(bot._armor_quickskip())
        bot.quickskip()
        self.assertEqual(waits, [1])


class ClassicReelDeadlineTests(unittest.TestCase):
    """#4: a pause during the classic reel delay restarted the whole delay."""

    def test_pause_does_not_restart_the_delay(self):
        _, bot, _ = quiet_bot({"classic_fishing_delay": 0.6})
        bot.capture_window_region = lambda *a: np.zeros((100, 250, 3), np.uint8)
        bot._last_frame_usable = True
        template = np.full((20, 20), 200, np.uint8)
        template[5:15, 5:15] = 30
        bot._load_classic_fish_template = lambda: template
        FishingBot._classic_fish_scaled_cache = None

        def unpause_later():
            time.sleep(0.3)
            bot.paused = True
            time.sleep(0.2)
            bot.paused = False

        with mock.patch("fishing_bot.cv2.minMaxLoc", return_value=(0.0, 0.95, (0, 0), (0, 0))):
            threading.Thread(target=unpause_later, daemon=True).start()
            start = time.perf_counter()
            self.assertTrue(bot.wait_for_classic_fish(timeout=5))
            took = time.perf_counter() - start
        FishingBot._classic_fish_scaled_cache = None
        self.assertLess(took, 0.6 + 0.3, "the pause restarted the reel delay")


class DialogLookPerWindowTests(unittest.TestCase):
    """#7: the dialog look one window learned was required of every window,
    and it outlived the run."""

    def setUp(self):
        dialog_watch.forget()

    def tearDown(self):
        dialog_watch.forget()

    @staticmethod
    def _grab_from(patches):
        it = iter(patches)
        return lambda x, y, w, h: next(it)

    def test_other_window_with_another_skin_still_sees_its_dialog(self):
        baseline = np.zeros((16, 40), np.uint8)
        dialog_a = np.full((16, 40, 3), 120, np.uint8)
        dialog_b = np.full((16, 40, 3), 220, np.uint8)
        self.assertTrue(dialog_watch.wait(self._grab_from([dialog_a] * 10), (5, 5), baseline, 0.5,
                                          lambda: True, poll=0.001, what="d", owner=0))
        dialog_watch.confirm((5, 5), "d", owner=0)
        # Window 0 now needs its own look ...
        self.assertFalse(dialog_watch.wait(self._grab_from([dialog_b] * 400), (5, 5), baseline, 0.2,
                                           lambda: True, poll=0.001, what="d", owner=0))
        # ... window 1, another skin, is not held to it
        self.assertTrue(dialog_watch.wait(self._grab_from([dialog_b] * 10), (5, 5), baseline, 0.5,
                                          lambda: True, poll=0.001, what="d", owner=1))

    def test_new_run_forgets_the_learned_look(self):
        dialog_watch._learned[(0, "d", 5, 5)] = np.zeros((16, 40), np.uint8)
        FishingBot.reset_session_state()
        self.assertEqual(dialog_watch._learned, {})


class OffFrameGridTests(unittest.TestCase):
    """#9: a grid sticking out of the frame was shifted onto the frame and
    read and clicked there; an off-frame crate slot read as empty (refill)."""

    def _detector(self, bounds, slots=None):
        cfg = {"jigsaw_grid_bounds": bounds, "jigsaw_grid_check": False}
        if slots:
            cfg["jigsaw_crate_slot_centers"] = slots
        return JigsawDetector(cfg)

    def test_grid_partly_off_frame_is_not_read(self):
        frame = np.zeros((300, 400, 3), np.uint8)
        self.assertIsNone(self._detector([-30, 10, 240, 160])._configured_grid_bounds(frame))
        self.assertIsNone(self._detector([200, 10, 240, 160])._configured_grid_bounds(frame))
        self.assertFalse(self._detector([-30, 10, 240, 160]).detect_grid(frame).grid_found)

    def test_grid_inside_or_a_pixel_over_is_read(self):
        frame = np.zeros((300, 400, 3), np.uint8)
        self.assertEqual(self._detector([10, 10, 240, 160])._configured_grid_bounds(frame), (10, 10, 240, 160))
        self.assertEqual(self._detector([-1, 10, 240, 160])._configured_grid_bounds(frame), (0, 10, 240, 160))

    def test_off_frame_slot_is_neither_missing_nor_available(self):
        frame = np.zeros((300, 400, 3), np.uint8)
        det = self._detector([10, 10, 240, 160], {"normal": [600, 100], "special": [300, 100]})
        grid = JigsawGridResult(True, (10, 10, 240, 160), [], 0, 1.0)
        slots = det.detect_crate_slots(frame, grid)
        self.assertNotIn("normal", slots.missing)
        self.assertNotIn("normal", slots.available)
        self.assertIn("special", slots.missing | slots.available)
        self.assertIsNone(det.detect_crate(frame, grid, "normal_first", available_sections={"normal"}))


class ScreenCheckErrorTests(unittest.TestCase):
    """#13: a failed foreground check counted as "the screen shows this client"."""

    def test_failed_check_is_not_reliable(self):
        cap = screen_capture.WindowCapture.__new__(screen_capture.WindowCapture)
        cap.diag_tag = "W1"

        class WM:
            def is_foreground(self):
                raise OSError("gone")

        cap.window_manager = WM()
        self.assertFalse(cap._screen_is_reliable())


class ChannelMaxDiffTests(unittest.TestCase):
    """docs/performance-review.md: the fast channel max must give exactly
    what ``absdiff(...).max(axis=2)`` gave, or the learned panel and the
    "region changed" decision would move."""

    def test_same_as_numpy_reduction(self):
        from fish_detector import abs_diff_max
        rng = np.random.default_rng(7)
        for shape in [(220, 280, 3), (37, 53, 3), (6, 5, 4), (9, 11)]:
            a = rng.integers(0, 256, shape, dtype=np.uint8)
            b = rng.integers(0, 256, shape, dtype=np.uint8)
            diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).astype(np.uint8)
            want = diff if diff.ndim == 2 else diff.max(axis=2)
            got = abs_diff_max(a, b)
            self.assertEqual(got.dtype, np.uint8)
            self.assertTrue(got.flags.c_contiguous)
            np.testing.assert_array_equal(got, want)


if __name__ == "__main__":
    unittest.main()

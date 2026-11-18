"""Regression tests for the "Bug and Portability Review" findings. Each test
fails when its bug is back. Stub-only: nothing here touches the desktop."""

import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import numpy as np  # noqa: E402

import dialog_watch  # noqa: E402
import fishing_bot as fishing_bot_module  # noqa: E402
from fishing_bot import FishingBot, game_key  # noqa: E402
from minigame_sim import bench  # noqa: E402
from minigame_sim.jigsaw_game import JigsawGame  # noqa: E402
import test_jigsaw_sim as jsim  # noqa: E402


def run_bot(bot, until, timeout):
    bot.running = True
    thread = threading.Thread(target=bot.play_game, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    while time.time() < deadline and thread.is_alive() and not until():
        time.sleep(0.02)
    stopped_by_itself = not thread.is_alive()
    bot.stop()
    thread.join(timeout=15)
    assert not thread.is_alive(), "the bot did not stop"
    return stopped_by_itself


def quiet_bot(config=None):
    game = bench.quick_game("official")
    bot, logs = bench.make_bot(game, config)
    bot.running = True
    return game, bot, logs


class UnknownCatchFillsPageTests(unittest.TestCase):
    """Critical 1: with auto handling on, a catch with no template still
    takes a slot. A full page must be left."""

    def test_full_page_of_unknown_items_switches_page(self):
        _, bot, _ = quiet_bot({"auto_fish_handling": True, "fish_actions": {"x_item.jpg": "keep"}})
        frame = np.zeros((40, 40, 3), np.uint8)
        bot._find_new_item = lambda allow_extra: (None, frame)
        bot._page_has_space = lambda f=None, expected=None: False
        switches = []
        bot._switch_inventory_page = lambda: switches.append(1)
        bot.handle_caught_item(may_have_catch=True)
        self.assertEqual(switches, [1])

    def test_game_without_a_hit_does_not_judge_the_page(self):
        _, bot, _ = quiet_bot({"auto_fish_handling": True, "fish_actions": {"x_item.jpg": "keep"}})
        bot._find_new_item = lambda allow_extra: (None, None)
        bot._page_has_space = lambda f=None, expected=None: self.fail("page judged without a catch")
        bot.handle_caught_item(may_have_catch=False)


class ClassicPauseTests(unittest.TestCase):
    """Critical 2: a pause during the classic bite wait is no miss, and no
    Ctrl+G goes out while paused."""

    def test_pause_ends_the_wait_as_interrupted(self):
        _, bot, _ = quiet_bot()
        bot._load_classic_fish_template = lambda: np.zeros((20, 20), np.uint8)
        bot.paused = True
        start = time.perf_counter()
        self.assertFalse(bot.wait_for_classic_fish(timeout=5))
        self.assertTrue(bot._wait_interrupted)
        self.assertLess(time.perf_counter() - start, 1.0)

    def test_ctrl_key_is_not_sent_while_paused(self):
        _, bot, _ = quiet_bot()
        events = []
        bot.keyboard_controller = types.SimpleNamespace(press=lambda k: events.append(k),
                                                        release=lambda k: events.append(k))
        bot.paused = True
        bot.press_ctrl_key("g")
        self.assertEqual(events, [])


class DialogWatchTests(unittest.TestCase):
    """Critical 3: moving scenery is not a dialog."""

    def setUp(self):
        dialog_watch.forget()

    def _grab_from(self, frames):
        it = iter(frames)
        last = [None]

        def grab(x, y, w, h):
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            return last[0]
        return grab

    def test_moving_scenery_is_not_a_dialog(self):
        rng = np.random.default_rng(3)
        baseline = np.full((16, 40), 100, np.uint8)
        moving = lambda x, y, w, h: rng.integers(0, 255, (16, 40, 3), dtype=np.uint8)  # noqa: E731
        seen = dialog_watch.wait(moving, (100, 100), baseline, 0.3, lambda: True, poll=0.001)
        self.assertFalse(seen)

    def test_a_still_dialog_is_seen(self):
        baseline = np.full((16, 40), 100, np.uint8)
        dialog = np.full((16, 40, 3), 30, np.uint8)
        seen = dialog_watch.wait(self._grab_from([dialog] * 10), (100, 100), baseline, 0.5, lambda: True,
                                 poll=0.001)
        self.assertTrue(seen)

    def test_once_learned_only_the_dialog_look_counts(self):
        baseline = np.full((16, 40), 100, np.uint8)
        dialog = np.full((16, 40, 3), 30, np.uint8)
        other = np.full((16, 40, 3), 200, np.uint8)     # changed and still, but not the dialog
        self.assertTrue(dialog_watch.wait(self._grab_from([dialog] * 10), (5, 5), baseline, 0.5,
                                          lambda: True, poll=0.001, what="d"))
        dialog_watch.confirm((5, 5), "d")
        self.assertFalse(dialog_watch.wait(self._grab_from([other] * 400), (5, 5), baseline, 0.2,
                                           lambda: True, poll=0.001, what="d"))
        self.assertTrue(dialog_watch.wait(self._grab_from([dialog] * 10), (5, 5), baseline, 0.5,
                                          lambda: True, poll=0.001, what="d"))


class JigsawNoDialogTests(unittest.TestCase):
    """Critical 4: no confirm click without its dialog."""

    def test_crate_without_a_dialog_gets_no_confirm(self):
        game = JigsawGame(seed=11, dialog_delay=999.0)
        rig = jsim.Rig([game], {"jigsaw_dialog_wait_timeout": 0.05})
        rig.run(lambda: game.opened >= 3, timeout=60)
        self.assertGreaterEqual(game.opened, 1, rig.logs[-8:])
        self.assertEqual(game.early_confirms, 0, rig.logs[-8:])


class CastErrorTests(unittest.TestCase):
    """Critical 5: a cast that raised is not reported as sent."""

    def test_failed_key_press_is_not_a_cast(self):
        _, bot, _ = quiet_bot()

        def boom(*a, **k):
            raise OSError("device gone")
        bot.human.tap_key = boom
        self.assertFalse(bot.bait_and_cast())
        self.assertIn("device gone", bot._cast_error)

    def test_no_keyboard_is_not_a_cast(self):
        _, bot, _ = quiet_bot()
        bot.keyboard_controller = None
        self.assertFalse(bot.bait_and_cast())
        self.assertIsNotNone(bot._cast_error)

    def test_repeated_cast_errors_stop_with_their_own_reason(self):
        game, bot, logs = quiet_bot()
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._resume_inventory_check = lambda: None

        def boom(*a, **k):
            raise OSError("device gone")
        bot.human.tap_key = boom
        self.assertTrue(run_bot(bot, lambda: False, timeout=30), logs[-5:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_KEYBOARD_ERROR)
        self.assertEqual(bot.consecutive_failures, 0, "a failed cast was counted as a missed bite")


class MissHandlingTests(unittest.TestCase):
    def test_blank_capture_miss_is_not_fed_to_the_tuner(self):
        _, bot, _ = quiet_bot()
        reported = []
        bot._report_cast = lambda waited, success: reported.append(success)
        bot._capture_bad_since = time.perf_counter()
        self.assertTrue(bot._on_missed_bite(2.0, 5, "x"))
        self.assertEqual(reported, [])
        self.assertEqual(bot.consecutive_failures, 0)

    def test_lost_game_screen_stops_without_writing_off_bait(self):
        game = bench.quick_game("official", bite_delay=(999.0, 999.0))
        bot, logs = bench.make_bot(game, {"timing_minigame_timeout": 0.3})
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._resume_inventory_check = lambda: None
        bot._bite_ever_seen = True
        bait = bot.bait_counter
        bot._inventory_visible = lambda: (False, "test: inventory gone")
        self.assertTrue(run_bot(bot, lambda: False, timeout=60), logs[-5:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_GAME_SCREEN_LOST, logs[-5:])
        self.assertEqual(bot.bait_counter, bait)

    def test_repeating_loop_error_stops(self):
        game, bot, logs = quiet_bot()
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._resume_inventory_check = lambda: None
        bot._LOOP_ERROR_LIMIT = 3

        def boom():
            raise RuntimeError("always")
        bot.bait_and_cast = boom
        self.assertTrue(run_bot(bot, lambda: False, timeout=30), logs[-5:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_REPEATED_ERROR)


class ResumeTests(unittest.TestCase):
    def test_resume_clicks_only_the_current_page(self):
        _, bot, _ = quiet_bot({f"inv_page_{p}_pos": [10 * p, 5] for p in range(1, 5)})
        bot._current_inv_page = 2
        clicked = []
        bot._require_inventory_visible = lambda when: True
        bot._goto_page = lambda pos, settle=0.3: clicked.append(tuple(pos)) or True
        bot._rescan_inventory_state = lambda: setattr(bot, "_empty_slot_positions", [(1, 1)])
        bot._resume_inventory_check()
        self.assertEqual(clicked, [(30, 5)])


class StackTests(unittest.TestCase):
    def test_changed_slot_after_open_is_not_a_dead_fish(self):
        _, bot, _ = quiet_bot()
        before = np.full((30, 30), 90, np.uint8)
        changed = np.full((40, 40, 3), 90, np.uint8)
        changed[25:35, 25:35] = 250                  # the count in the corner went down
        bot.capture_inventory_area = lambda: changed
        self.assertTrue(bot._slot_changed(before, 20, 20))
        bot.capture_inventory_area = lambda: np.full((40, 40, 3), 90, np.uint8)
        self.assertFalse(bot._slot_changed(before, 20, 20))


class StopMidDropTests(unittest.TestCase):
    """Reliability 7: Stop pressed mid-drop still puts the item back."""

    def test_item_is_put_back_when_stopped_waiting_for_the_dialog(self):
        game = bench.quick_game("official")
        cfg = {"auto_fish_handling": True, "drop_button_pos": list(game.drop_button),
               "confirm_button_pos": list(game.confirm_button)}
        bot, logs = bench.make_bot(game, cfg)
        name = next(n for n in game._item_names if "_living" not in n)   # a living fish would be opened
        game.pages[0][0] = name
        bot.running = True
        rx, ry = bot._inventory_rect()[:2]
        cx, cy = game.slot_center(0)
        bot._drop_match_sure = lambda *a: (True, "test")

        def stop_while_waiting(*a, **k):
            bot.running = False
            return False

        with mock.patch.object(fishing_bot_module.dialog_watch, "wait", stop_while_waiting):
            bot._handle_item(name, cx - rx, cy - ry, "drop")
        self.assertIsNone(game.carrying, logs[-5:])
        self.assertEqual(game.pages[0].get(0), name, (game.pages[0], logs[-5:]))


class KeyTests(unittest.TestCase):
    def test_digits_go_out_as_virtual_keys_on_windows(self):
        fake = types.SimpleNamespace(KeyCode=types.SimpleNamespace(from_vk=lambda vk: ("vk", vk)))
        with mock.patch.object(fishing_bot_module, "keyboard", fake), \
                mock.patch.object(fishing_bot_module.sys, "platform", "win32"):
            self.assertEqual(game_key("1"), ("vk", 0x31))
            self.assertEqual(game_key("F2"), fishing_bot_module.Key.f2)
        with mock.patch.object(fishing_bot_module.sys, "platform", "linux"):
            self.assertEqual(game_key("1"), "1")
        self.assertEqual(game_key("space"), fishing_bot_module.Key.space)

    def test_key_is_released_when_the_hold_is_interrupted(self):
        from humanize import Humanizer
        events = []
        kb = types.SimpleNamespace(press=lambda k: events.append(("press", k)),
                                   release=lambda k: events.append(("release", k)))
        h = Humanizer(enabled=False)
        h.keyboard_controller = kb
        with mock.patch("humanize.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                h.tap_key("a")
        self.assertIn(("release", "a"), events)

    def test_bite_timeout_cap_is_configurable(self):
        _, bot, _ = quiet_bot({"timing_minigame_timeout_cap": 40.0})
        bot._longest_bite = 100.0
        self.assertEqual(bot._bite_timeout(), 40.0)


if __name__ == "__main__":
    unittest.main()

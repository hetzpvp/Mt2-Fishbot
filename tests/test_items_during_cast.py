"""The caught item is handled after the quick skip, before the next cast
(2026-11-20, second report): the quick skip cancels the end-of-game
animation, then the catch is opened, dropped or queued as bait with no line
out, then the bait is cast. (Handled while waiting for the bite, drops
rarely fitted before bites 1-2 s after the cast and the order looked
random.) Stub-only: nothing touches the desktop."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from _stubs import install

install()

from test_review_fixes import quiet_bot  # noqa: E402


def loop_bot(bait=2, **config):
    """A bot whose loop steps only write down what they did."""
    _, bot, _ = quiet_bot(dict({"classic_fishing": False, "quick_skip": True, "auto_fish_handling": True},
                               **config))
    bot.running = True
    bot.bait_counter = bait
    bot.total_games = 0
    bot._adaptive = False
    events = []
    bot._stop_if_window_unavailable = lambda: False
    bot._startup_scan_and_process_all_pages = lambda: None
    bot._refuse_overlapping_windows = lambda: True
    bot._verify_capture = lambda: None
    bot.window_manager.activate_window = lambda force_activate=False: True
    bot.capture_inventory_area = lambda: np.zeros((40, 40, 3), np.uint8)
    bot.bait_and_cast = lambda: events.append("cast") or True
    bot.quickskip = lambda: events.append("skip")
    bot.handle_caught_item = lambda may_have_catch=True, baseline=None, **kw: events.append(
        ("handle", may_have_catch, baseline is not None))
    waits = []

    def wait_for_minigame_window(timeout=6.0):
        waits.append(timeout)
        events.append("wait")
        return True

    def play_minigame():
        events.append("game")
        bot.total_games += 1
        bot.bait_counter -= 1
        bot._defer_catch(may_have_catch=True)
        return False, 1

    bot.wait_for_minigame_window = wait_for_minigame_window
    bot._play_minigame = play_minigame
    bot._note_fishless_game = lambda: True
    return bot, events, waits


class ItemsDuringCastTests(unittest.TestCase):
    def test_item_is_handled_after_quick_skip_before_the_cast(self):
        bot, events, _ = loop_bot(bait=2)
        bot._play_game()
        self.assertEqual(events, [
            "cast", "wait", "game", "skip",
            ("handle", True, True), "cast", "wait", "game",
            ("handle", True, True),            # bait gone: the last catch after the loop
        ])

    def test_without_quick_skip_the_catch_is_handled_within_the_recast_wait(self):
        bot, events, _ = loop_bot(bait=2, quick_skip=False)
        bot._wait_before_recast = lambda: events.append("recast wait") and False
        bot._play_game()
        self.assertEqual(events[:6], ["cast", "wait", "game", ("handle", True, True), "recast wait", "cast"])

    def test_drop_without_time_comes_after_the_quick_skip(self):
        # The report of 2026-11-20 10:08: a put-aside drop ran at the game
        # end, before the quick skip. It belongs after it, before the cast.
        bot, events, _ = loop_bot(bait=3)
        bot.config["fish_actions"] = {"Carp_dead.png": "drop"}
        bot._item_in_slot = lambda x, y: "Carp_dead.png"
        bot._handle_item = lambda fn, x, y, a, label="": events.append(("drop", fn)) or True

        def handle_caught_item(may_have_catch=True, baseline=None, **kw):
            events.append(("handle", may_have_catch, baseline is not None))
            bot._postponed_items.append(("Carp_dead.png", 40, 40))   # no time before the bite

        bot.handle_caught_item = handle_caught_item
        bot._play_game()
        skip = [i for i, e in enumerate(events) if e == "skip"][0]
        self.assertEqual(events[skip:skip + 4], ["skip", ("handle", True, True), ("drop", "Carp_dead.png"),
                                                 "cast"])

    def test_bite_wait_is_not_shortened_by_the_handling(self):
        bot, events, waits = loop_bot(bait=2)
        clock = [0.0]
        import fishing_bot
        real = fishing_bot.time.perf_counter
        try:
            fishing_bot.time.perf_counter = lambda: clock[0]
            bot.handle_caught_item = lambda may_have_catch=True, baseline=None, **kw: clock.__setitem__(0, clock[0] + 2.0)
            bot._bite_timeout = lambda: 6.0
            bot._play_game()
        finally:
            fishing_bot.time.perf_counter = real
        self.assertEqual(waits[0], 6.0)
        self.assertAlmostEqual(waits[1], 6.0)      # handled before the cast, not out of the bite wait

    def test_stopped_bot_does_not_handle_the_last_catch(self):
        bot, events, _ = loop_bot(bait=5)

        def skip():
            events.append("skip")
            bot._manually_stopped = True
            bot.running = False

        bot.quickskip = skip
        bot._play_game()
        self.assertEqual(events, ["cast", "wait", "game", "skip"])
        self.assertIsNone(bot._pending_catch)

    def test_pause_leaves_the_catch_to_the_resume_check(self):
        bot, events, _ = loop_bot(bait=2)
        resumed = []
        bot._resume_inventory_check = lambda: resumed.append(1)

        def skip():
            events.append("skip")
            bot._was_paused = True        # paused and resumed before the next cast

        bot.quickskip = skip
        bot._play_game()
        self.assertEqual(resumed, [1])
        # The second cast has nothing left to handle; the last catch is handled at the end
        self.assertEqual(events, ["cast", "wait", "game", "skip", "cast", "wait", "game", ("handle", True, True)])

    def test_classic_fishing_handles_right_away(self):
        bot, events, _ = loop_bot(bait=1, classic_fishing=True)
        bot.wait_for_classic_fish = lambda timeout=40: True
        bot._send_reel = lambda: True
        bot._play_game()
        self.assertEqual(events[:2], ["cast", ("handle", True, False)])


class DropBeforeTheBiteTests(unittest.TestCase):
    """A drop has the item on the cursor and its dialogs where the minigame
    opens: between cast and bite it is only started when it is done before
    the shortest bite seen. Otherwise it is put aside and handled at the end
    of the next game."""

    def _bot(self):
        bot, _events, _waits = loop_bot()
        bot._in_cast_window = True
        bot._minigame_showing = lambda: False
        return bot

    def test_no_drop_before_a_bite_was_timed(self):
        bot = self._bot()
        bot._cast_time = 0.0
        self.assertTrue(bot._postpone_if_bitten("Carp_dead.png", 40, 40, "drop"))
        self.assertEqual(bot._postponed_items, [("Carp_dead.png", 40, 40)])
        self.assertFalse(bot._postpone_if_bitten("Carp_living.jpg", 72, 40, "open"))    # one click: fine

    def test_drop_only_when_it_fits(self):
        import fishing_bot
        bot = self._bot()
        bot._bite_delays.extend([2.4, 2.0, 3.1])
        bot._drop_durations.append(0.8)
        real = fishing_bot.time.perf_counter
        try:
            bot._cast_time = 100.0
            fishing_bot.time.perf_counter = lambda: 100.3          # 0.3 + 0.8 <= 0.85 * 2.0
            self.assertFalse(bot._postpone_if_bitten("Carp_dead.png", 40, 40, "drop"))
            fishing_bot.time.perf_counter = lambda: 101.0          # 1.0 + 0.8 > 1.7
            self.assertTrue(bot._postpone_if_bitten("Carp_dead.png", 40, 40, "drop"))
        finally:
            fishing_bot.time.perf_counter = real

    def test_nothing_starts_once_the_minigame_shows(self):
        bot = self._bot()
        bot._minigame_showing = lambda: True
        self.assertTrue(bot._postpone_if_bitten("Carp_living.jpg", 40, 40, "open"))

    def test_not_between_cast_and_bite_nothing_is_put_aside(self):
        bot = self._bot()
        bot._in_cast_window = False
        self.assertFalse(bot._postpone_if_bitten("Carp_dead.png", 40, 40, "drop"))

    def test_game_end_handles_nothing(self):
        # Nothing before the quick skip: the game end only remembers the strip
        bot = self._bot()
        bot._in_cast_window = False
        bot._postponed_items = [("Carp_dead.png", 40, 40)]
        bot._finish_put_back = lambda: self.fail("put back before the quick skip")
        bot._handle_item = lambda *a, **k: self.fail("item handled before the quick skip")
        bot._defer_catch(may_have_catch=True)
        self.assertEqual(bot._postponed_items, [("Carp_dead.png", 40, 40)])
        self.assertIsNotNone(bot._pending_catch)

    def test_put_aside_items_are_handled_before_the_cast(self):
        bot = self._bot()
        bot._in_cast_window = False
        bot._postponed_items = [("Carp_dead.png", 40, 40)]
        bot.config["fish_actions"] = {"Carp_dead.png": "drop"}
        bot._item_in_slot = lambda x, y: "Carp_dead.png"
        handled = []
        bot._handle_item = lambda fn, x, y, a, label="": handled.append((fn, x, y, a)) or True
        bot._before_cast()
        self.assertEqual(handled, [("Carp_dead.png", 40, 40, "drop")])
        self.assertEqual(bot._postponed_items, [])

    def test_item_held_since_the_bite_goes_back_before_the_cast(self):
        bot = self._bot()
        bot._in_cast_window = False
        bot._focus = lambda force=False: True
        calls = []
        bot._finish_put_back = lambda: calls.append(1) or True
        bot._before_cast()
        self.assertEqual(calls, [1])
        self.assertIsNone(bot._finish_put_back)

    def test_strip_from_the_game_start_is_the_baseline(self):
        bot = self._bot()
        before = np.full((40, 40, 3), 7, np.uint8)
        bot._catch_baseline = before
        bot._defer_catch(may_have_catch=True)
        self.assertIs(bot._pending_catch[0], before)
        self.assertIsNone(bot._catch_baseline)


class LateCatchWatchTests(unittest.TestCase):
    """2026-11-21: a game without an item cost the full 1.0 s catch wait
    before the cast (two games in three). The look before the cast ends at
    the configured time counted from the game end; the wait for a late item
    runs after the cast, screen only."""

    def test_late_watch_runs_after_the_cast_before_the_bite_wait(self):
        bot, events, _ = loop_bot(bait=2)

        def handle_caught_item(may_have_catch=True, baseline=None, defer_late=False):
            events.append(("handle", defer_late, bot._in_cast_window))
            if defer_late:
                bot._late_watch = (np.zeros((40, 40, 3), np.uint8), 0.0, 1.0)

        bot.handle_caught_item = handle_caught_item
        bot._play_game()
        skip = events.index("skip")
        self.assertEqual(events[skip:skip + 5], [
            "skip", ("handle", True, False),      # the look before the cast
            "cast", ("handle", False, True),      # the late watch, line out
            "wait"])
        self.assertIsNone(bot._late_watch)

    def test_nothing_is_clicked_for_a_late_catch_in_the_cast_window(self):
        bot, _events, _ = loop_bot()
        del bot.handle_caught_item                  # the real one
        bot.config["fish_actions"] = {"Carp_living.jpg": "drop"}
        bot.config["confirm_button_pos"] = (1, 1)
        bot._empty_slot_positions = [(40, 40), (80, 40)]
        bot._find_new_item = lambda allow_extra, baseline=None, **kw: (("Carp_living.jpg", (40, 40)), None)
        bot._handle_item = lambda *a, **k: self.fail("clicked while the line is out")
        bot._switch_inventory_page = lambda: self.fail("page switched while the line is out")
        bot._page_has_space = lambda f=None, expected=None: False
        bot._late_watch = (np.zeros((40, 40, 3), np.uint8), 0.0, 0.0)
        bot._watch_late_catch(in_cast_window=True)
        self.assertEqual(bot._postponed_items, [("Carp_living.jpg", 40, 40)])
        self.assertTrue(bot._page_switch_wanted)
        self.assertIn((40, 40), bot._ignored_positions)

    def test_page_switch_wanted_runs_after_the_next_quick_skip(self):
        bot, _events, _ = loop_bot()
        bot._page_switch_wanted = True
        bot._page_has_space = lambda f=None, expected=None: False
        switches = []
        bot._switch_inventory_page = lambda: switches.append(1)
        bot._before_cast()
        self.assertEqual(switches, [1])
        self.assertFalse(bot._page_switch_wanted)

    def test_pause_forgets_the_late_watch(self):
        bot, events, _ = loop_bot(bait=2)
        bot._resume_inventory_check = lambda: None

        def skip():
            events.append("skip")
            bot._late_watch = (np.zeros((40, 40, 3), np.uint8), 0.0, 1.0)
            bot._was_paused = True

        bot.quickskip = skip
        pending = []
        real = bot._watch_late_catch
        bot._watch_late_catch = lambda in_cast_window: pending.append(bot._late_watch is not None) or \
            real(in_cast_window)
        bot._play_game()
        self.assertTrue(pending)
        self.assertNotIn(True, pending)           # the resume dropped it before the next cast


if __name__ == "__main__":
    unittest.main()

"""How long one window keeps the shared mouse and keyboard (2026-11-21).

With two clients (diagnostics 20261121_110550) a drop held input_lock 3.3 s,
most of it waiting for the game to answer the Confirm click, while the
other window stood still ready to cast. And the quick skip let the lock go
between its two Ctrl+G, so another window could take the focus in between
(8 of 40 times), which cost two more focus switches. Nothing here touches
the desktop."""

import os
import sys
import threading
import time
import unittest

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import dialog_watch  # noqa: E402
import fishing_bot  # noqa: E402
from minigame_sim import bench  # noqa: E402
from utils import input_lock  # noqa: E402


def drop_game_and_config(**kw):
    game = bench.quick_game("official", dead_fish_chance=1.0, **kw)
    cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
    cfg.update({"auto_fish_handling": True, "fish_actions": {n: "drop" for n in game._item_names},
                "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)})
    return game, cfg


class ConfirmWaitTests(unittest.TestCase):
    """After the Confirm click nothing is on the cursor: the answer is
    watched without the lock, and it is held again before the next click."""

    def setUp(self):
        self.waits = []      # (want_change, lock held by the waiting bot)
        real_wait = dialog_watch.wait

        def wait(*args, **kw):
            self.waits.append((kw.get("want_change", True), input_lock.held_by_me()))
            return real_wait(*args, **kw)

        dialog_watch.wait = wait
        self.addCleanup(setattr, dialog_watch, "wait", real_wait)

    def run_drops(self, game, cfg, catches=4):
        bot, logs = bench.run(game, cfg, games=catches, timeout=120)
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-10:])
        self.assertIsNone(game.carrying, "item left on the cursor")
        self.assertFalse(input_lock.locked(), "input_lock left held")
        return bot, logs

    def test_confirm_answer_is_watched_without_the_lock(self):
        game, cfg = drop_game_and_config()
        self.run_drops(game, cfg)
        confirm = [held for want_change, held in self.waits if not want_change]
        before = [held for want_change, held in self.waits if want_change]
        self.assertTrue(confirm)
        self.assertFalse(any(confirm), "lock held while the game answers the Confirm click")
        # Up to the Confirm click the item may be on the cursor: lock held
        self.assertTrue(before and all(before))
        self.assertEqual(game.stats["drop_cancelled"], 0)

    def test_missed_confirm_is_clicked_again_with_the_lock_taken_back(self):
        game, cfg = drop_game_and_config(lost_confirm_clicks=1)
        self.run_drops(game, cfg)
        self.assertEqual(game.stats["lost_confirm_clicks"], 1)
        self.assertGreaterEqual(sum(1 for want_change, _ in self.waits if not want_change), 3)

    def test_other_window_gets_the_lock_during_the_confirm_wait(self):
        # A second thread asks for the lock while the drop waits for the
        # game: it gets it well before the drop is over.
        game, cfg = drop_game_and_config()
        got = []
        real_wait = dialog_watch.wait

        def wait(*args, **kw):
            if not kw.get("want_change", True) and not got:
                asked = time.perf_counter()

                def other():
                    with input_lock:
                        got.append(time.perf_counter() - asked)

                t = threading.Thread(target=other)
                t.start()
                t.join(timeout=2.0)
            return real_wait(*args, **kw)

        dialog_watch.wait = wait
        self.run_drops(game, cfg)
        self.assertTrue(got)
        self.assertLess(got[0], 0.1)

    def test_error_while_the_lock_is_let_go_still_takes_it_back(self):
        game, cfg = drop_game_and_config()
        bot, _logs = bench.make_bot(game, cfg)
        input_lock.acquire()
        try:
            with self.assertRaises(RuntimeError):
                with bot._input_lock_released("test"):
                    self.assertFalse(input_lock.held_by_me())
                    raise RuntimeError("boom")
            self.assertTrue(input_lock.held_by_me())
        finally:
            input_lock.release()


class QuickSkipHoldTests(unittest.TestCase):
    """Both Ctrl+G of the horse quick skip go out in one lock hold."""

    def _bot(self):
        game = bench.quick_game("official")
        bot, _logs = bench.make_bot(game, {"quick_skip": True, "quick_skip_mode": "horse"})
        bot.running = True
        bot._t_qs_between = 0.01
        bot._t_qs_after = 0.0
        return bot

    def test_both_presses_in_one_hold(self):
        bot = self._bot()
        held, owners = [], []
        real_acquire = input_lock.acquire

        def acquire(*a, **kw):
            owners.append(threading.get_ident())
            return real_acquire(*a, **kw)

        input_lock.acquire = acquire
        self.addCleanup(setattr, input_lock, "acquire", real_acquire)
        bot.press_ctrl_key = lambda key: held.append(input_lock.held_by_me()) or True
        bot.quickskip()
        self.assertEqual(held, [True, True])
        self.assertEqual(len(owners), 1)       # taken once for both
        self.assertFalse(input_lock.locked())

    def test_real_presses_inside_the_hold(self):
        # press_ctrl_key does not take the lock again (it is not re-entrant)
        bot = self._bot()
        sent = []
        real = bot._press_ctrl_key_locked
        bot._press_ctrl_key_locked = lambda key: sent.append(input_lock.held_by_me()) or real(key)
        bot.quickskip()
        self.assertEqual(sent, [True, True])
        self.assertFalse(input_lock.locked())

    def test_press_ctrl_key_alone_still_takes_the_lock(self):
        bot = self._bot()
        sent = []
        real = bot._press_ctrl_key_locked
        bot._press_ctrl_key_locked = lambda key: sent.append(input_lock.held_by_me()) or real(key)
        self.assertTrue(bot.press_ctrl_key("g"))
        self.assertEqual(sent, [True])
        self.assertFalse(input_lock.locked())

    def test_waiting_fish_click_goes_between_the_presses(self):
        # Another window's fish click must not wait for the whole quick
        # skip; a normal request (a cast) still does.
        bot = self._bot()
        bot._t_qs_between = 0.05
        order, threads = [], []

        def ask(name, urgent):
            def run():
                with (input_lock.urgent() if urgent else input_lock):
                    order.append(name)
            t = threading.Thread(target=run)
            t.start()
            threads.append(t)

        def press(key):
            order.append("ctrl")
            if len(order) == 1:
                ask("cast", urgent=False)
                ask("click", urgent=True)
                deadline = time.perf_counter() + 2.0
                while not input_lock.urgent_waiting() and time.perf_counter() < deadline:
                    time.sleep(0.001)
            return True

        bot.press_ctrl_key = press
        bot.quickskip()
        for t in threads:
            t.join(timeout=2.0)
        self.assertEqual(order, ["ctrl", "click", "ctrl", "cast"])
        self.assertFalse(input_lock.locked())

    def test_failed_first_press_sends_no_second(self):
        bot = self._bot()
        calls = []
        bot.press_ctrl_key = lambda key: calls.append(key) and False
        bot._wait_before_recast = lambda: False
        bot.quickskip()
        self.assertEqual(calls, ["g"])
        self.assertFalse(input_lock.locked())


if __name__ == "__main__":
    unittest.main()

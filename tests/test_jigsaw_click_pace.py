"""How long each jigsaw crate keeps the one shared mouse (2026-11-22).

Diag 20261122_132850 (4 clients, 909 crates in 16 min): every crate held the
mouse ~1.0 s and the mouse was busy 97 % of the run, so four windows opened
no more crates per minute than one. ~80 % of that hold was fixed pauses:
100 ms on every button before the press, 50 ms holds, minimum dialog waits,
a 0.6 s wait for a dialog that always came within 45 ms when it came at all,
and ~130 ms after every discard waiting for the piece to leave the cursor.

Now: clicks in the window that already has the focus are quick (the first
one after a focus switch keeps the careful timing), lost quick clicks bring
the careful timing back for a while, the piece is looked for every 30 ms,
and a discard's piece is checked at the start of the window's next round.
Nothing here touches the desktop."""

import time
import unittest

from _stubs import install

install()

import test_jigsaw_sim as jsim  # noqa: E402
from jigsaw_bot import JigsawBot  # noqa: E402
from minigame_sim.jigsaw_game import JigsawGame, W  # noqa: E402

DEFAULT_PACE = {"jigsaw_click_hover": 0.04, "jigsaw_click_hover_switch": 0.10,
                "jigsaw_click_hold": 0.035, "jigsaw_click_hold_careful": 0.05}


class Recorder:
    """Stands in for the Humanizer: records sleeps and click holds."""

    def __init__(self):
        self.sleeps = []
        self.holds = []

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))

    def click(self, button="left", hold=0.0):
        self.holds.append(round(hold, 3))


def paced_bot():
    rig = jsim.Rig([JigsawGame(seed=70)], DEFAULT_PACE)
    bot = rig.bots[0]
    bot.human = Recorder()
    bot._release_settle = 0.0
    return bot


class ClickPaceTests(unittest.TestCase):
    def setUp(self):
        JigsawBot.reset_learned_settings()
        self.addCleanup(JigsawBot.reset_learned_settings)

    def test_click_in_the_focused_window_is_quick(self):
        bot = paced_bot()
        bot._press()
        self.assertEqual(bot.human.sleeps, [0.04])
        self.assertEqual(bot.human.holds, [0.035])
        self.assertTrue(bot._last_click_quick)

    def test_first_click_after_a_focus_switch_is_careful(self):
        bot = paced_bot()
        bot._press(switched=True)
        self.assertEqual(bot.human.sleeps, [0.10])
        self.assertEqual(bot.human.holds, [0.05])
        self.assertFalse(bot._last_click_quick)

    def test_one_lost_quick_click_keeps_the_quick_timing(self):
        bot = paced_bot()
        bot._press()
        bot._click_lost("test")
        bot._press()
        self.assertEqual(bot.human.sleeps, [0.04, 0.04])

    def test_two_lost_quick_clicks_close_together_slow_every_window_down(self):
        bot, other = paced_bot(), paced_bot()
        bot._press()
        bot._click_lost("test")
        for _ in range(5):
            bot._press()
        bot._click_lost("test")
        other._press()
        self.assertEqual(other.human.sleeps, [0.10])
        self.assertEqual(other.human.holds, [0.05])
        # A lost careful click does not restart the careful stretch
        left = JigsawBot._careful_left
        other._click_lost("test")
        self.assertEqual(JigsawBot._careful_left, left)

    def test_lost_quick_clicks_far_apart_keep_the_quick_timing(self):
        bot = paced_bot()
        bot._press()
        bot._click_lost("test")
        for _ in range(JigsawBot.LOSS_WINDOW + 1):
            bot._press()
        bot._click_lost("test")
        bot.human.sleeps.clear()
        bot._press()
        self.assertEqual(bot.human.sleeps, [0.04])

    def test_careful_timing_ends_after_its_clicks(self):
        bot = paced_bot()
        JigsawBot._careful_left = 3
        for _ in range(4):
            bot._press()
        self.assertEqual(bot.human.sleeps, [0.10, 0.10, 0.10, 0.04])

    def test_a_new_run_starts_quick(self):
        JigsawBot._careful_left = 50
        JigsawBot._quick_since_loss = 3
        bot = paced_bot()      # Rig resets the learned settings, as a new run does
        bot._press()
        self.assertEqual(bot.human.sleeps, [0.04])
        self.assertIsNone(JigsawBot._quick_since_loss)


class PieceLookTests(unittest.TestCase):
    def test_piece_that_comes_late_is_seen_within_a_poll(self):
        bot = paced_bot()
        bot.running = True
        shows_at = time.perf_counter() + 0.08
        seen = type("Piece", (), {"piece_id": "piece1", "confidence": 0.9})
        none = type("Piece", (), {"piece_id": None, "confidence": 0.0})
        bot._detect_cursor_piece = lambda rel: seen if time.perf_counter() >= shows_at else none
        start = time.perf_counter()
        piece = bot._look_for_piece((10, 10), attempts=3, delay=0.2)
        took = time.perf_counter() - start
        self.assertEqual(piece.piece_id, "piece1")
        self.assertLess(took, 0.16)     # was the next look 0.2 s after the first

    def test_no_piece_gives_up_after_the_same_time_as_before(self):
        bot = paced_bot()
        bot.running = True
        none = type("Piece", (), {"piece_id": None, "confidence": 0.0})
        bot._detect_cursor_piece = lambda rel: none
        start = time.perf_counter()
        bot._look_for_piece((10, 10), attempts=3, delay=0.1)
        took = time.perf_counter() - start
        self.assertGreater(took, 0.17)
        self.assertLess(took, 0.30)


class GameNeedsSlowClicksTests(unittest.TestCase):
    """A game whose frames are too long for the quick clicks: they are lost,
    the careful timing comes back, and the run goes on."""

    assert_in_step = jsim.JigsawSimTests.assert_in_step

    def test_lost_quick_clicks_bring_back_the_careful_timing(self):
        game = JigsawGame(seed=71)
        cfg = {"jigsaw_click_hover": 0.02, "jigsaw_click_hover_switch": 0.10,
               "jigsaw_click_hold": 0.02, "jigsaw_click_hold_careful": 0.07,
               "jigsaw_click_release": 0.06, "jigsaw_dialog_wait_timeout": 0.2}
        rig = jsim.Rig([game], cfg, input_frame=0.05)
        self.assertTrue(rig.run(lambda: game.opened >= 4, timeout=90), rig.logs[-12:])
        self.assertGreater(rig.desktop.lost_inputs, 0)
        self.assertTrue(any("clicking more slowly" in m for m in rig.logs), rig.logs[-12:])
        self.assertEqual(game.stray_discard_dialogs, 0)
        self.assert_in_step(rig)


class DiscardCheckTests(unittest.TestCase):
    """The discarded piece is checked gone when the window's next round
    starts, not with the mouse held right after the Yes."""

    assert_in_step = jsim.JigsawSimTests.assert_in_step

    def _only_discards(self, **game_options):
        game = JigsawGame(pieces=[4] * 60, **game_options)
        game.board = {(c, r) for c in range(6) for r in range(4) if (c + r) % 2 == 0}
        game.cell_figure = {cell: 0 for cell in game.board}
        return game

    def test_discard_returns_without_waiting_for_the_piece(self):
        game = self._only_discards(discard_delay=0.3)
        rig = jsim.Rig([game])
        bot = rig.bots[0]
        checks = []
        real = bot._finish_discard_check

        def finish():
            checks.append(time.perf_counter())
            real()

        bot._finish_discard_check = finish
        self.assertTrue(rig.run(lambda: game.discarded >= 3, timeout=60), rig.logs[-12:])
        self.assertTrue(checks)
        self.assertEqual(game.stray_discard_dialogs, 0)
        self.assert_in_step(rig)

    def test_other_window_plays_while_a_discarded_piece_leaves(self):
        games = [self._only_discards(left=i * W, seed=72 + i, discard_delay=0.4) for i in range(2)]
        rig = jsim.Rig(games)
        self.assertTrue(rig.run(lambda: all(g.discarded >= 3 for g in games), timeout=90), rig.logs[-12:])
        for game in games:
            self.assertEqual(game.stray_discard_dialogs, 0)
        self.assert_in_step(rig)


if __name__ == "__main__":
    unittest.main()

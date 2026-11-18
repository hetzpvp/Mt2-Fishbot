"""Regression tests for the 2026-11-17 loop audit (fishing + jigsaw). Each
test fails when its problem is back. Stub/simulator only: nothing here
touches the desktop."""

import threading
import time
import types
import unittest

from _stubs import install

install()

import numpy as np  # noqa: E402

from fishing_bot import FishingBot  # noqa: E402
from jigsaw_bot import FULL_BOARD_MASK  # noqa: E402
from jigsaw_detector import JigsawActionResult  # noqa: E402
from jigsaw_solver.jigsaw import SKIP_ACTION  # noqa: E402
import test_jigsaw_sim as jsim  # noqa: E402
from test_review_fixes import quiet_bot  # noqa: E402


class SuspectCellsTests(unittest.TestCase):
    """J1: cells wrongly taken for filled (a placement lost twice looks like
    a refused one) must not make the bot discard every piece forever."""

    def test_board_left_with_only_suspect_cells_is_still_finished(self):
        game = jsim.JigsawGame(pieces=[0] * 400)          # single cells: every piece fits
        game.board = {(c, r) for c in range(6) for r in range(4)} - {(5, 3), (4, 3)}
        game.cell_figure = {cell: 4 for cell in game.board}
        rig = jsim.Rig([game])
        bot = rig.bots[0]
        board = bot.detector.detect_grid(game.render()).board_mask
        bot._suspect_mask = FULL_BOARD_MASK & ~board       # the two empty cells
        bot._suspect_board = board
        self.assertTrue(rig.run(lambda: game.completed >= 1, timeout=30), rig.logs[-12:])
        self.assertLessEqual(game.discarded, 1, "pieces that fit were discarded")

    def test_discards_while_cells_are_suspect_count_towards_the_pause(self):
        game = jsim.JigsawGame(pieces=[0] * 400)
        game.board = {(c, r) for c in range(6) for r in range(4)} - {(5, 3), (4, 3), (3, 3)}
        game.cell_figure = {cell: 4 for cell in game.board}
        rig = jsim.Rig([game], config={"jigsaw_max_consecutive_discards": 4})

        class Never(jsim.GreedySolver):
            def solve(self, state):
                return SKIP_ACTION
        jsim.jigsaw_module.get_solver = lambda: Never()
        bot = rig.bots[0]
        board = bot.detector.detect_grid(game.render()).board_mask
        empty = FULL_BOARD_MASK & ~board
        bot._suspect_mask = empty & (empty - 1)    # all empty cells but one
        bot._suspect_board = board
        self.assertTrue(rig.run(lambda: bool(rig.pauses), timeout=30), rig.logs[-12:])
        self.assertLessEqual(game.opened, 5)

    def test_placement_click_that_was_not_sent_marks_no_cell(self):
        game = jsim.JigsawGame()
        rig = jsim.Rig([game])
        bot = rig.bots[0]
        bot.running = True
        before = 0
        grid = types.SimpleNamespace(grid_found=True, board_mask=before)
        bot._await_board = lambda expected, timeout: (grid, False)
        bot._detect_cursor_piece = lambda cursor: types.SimpleNamespace(piece_id="figure_0")
        bot._click_window_point = lambda *a, **k: False      # window never came to the front
        bot._last_click_sent = False
        discards = []
        bot._discard_piece = lambda *a, **k: discards.append(a)
        ok = bot._confirm_placement(before, 0b111, JigsawActionResult("place", (10, 10), "x"), (5, 5))
        self.assertFalse(ok)
        self.assertEqual(bot._suspect_mask, 0)
        self.assertEqual(discards, [])
        self.assertEqual(bot._check_hand, (5, 5))


class HeldPieceTests(unittest.TestCase):
    """J2: a piece that will not leave the cursor pauses the bot; no crate is
    opened on top of it."""

    def test_no_crate_is_opened_over_a_piece_that_cannot_be_discarded(self):
        class NoDiscardDialog(jsim.JigsawGame):
            def click(self, x, y, button):
                if button == "right":
                    return
                return super().click(x, y, button)

        class Skip(jsim.GreedySolver):
            def solve(self, state):
                return SKIP_ACTION

        game = NoDiscardDialog(seed=4)
        rig = jsim.Rig([game])
        jsim.jigsaw_module.get_solver = lambda: Skip()
        rig.run(lambda: bool(game.errors) or bool(rig.pauses), timeout=30)
        self.assertEqual(game.errors, [])
        self.assertTrue(rig.pauses, rig.logs[-12:])
        self.assertEqual(game.opened, 1)


def _classic_bot(miss_limit=3):
    _, bot, _ = quiet_bot({"classic_fishing": True, "classic_miss_limit": miss_limit})
    bot.running = False
    bot._startup_scan_and_process_all_pages = lambda: None
    bot._verify_capture = lambda: None
    bot._refuse_overlapping_windows = lambda: True
    bot._game_screen_still_there = lambda: True
    bot._bite_ever_seen = True
    bot.bait_and_cast = lambda: True
    return bot


class ClassicMountToggleTests(unittest.TestCase):
    """F2: CTRL+G toggles the mount. A miss right after the bot's own CTRL+G
    may come from having mounted the character, so it is not counted towards
    writing off the bait key."""

    def test_bait_key_is_written_off_after_limit_counted_misses(self):
        bot = _classic_bot(miss_limit=3)
        casts, ctrl, drops = [], [], []
        bot.wait_for_classic_fish = lambda timeout=40: casts.append(1) or False
        bot.press_ctrl_key = lambda key: ctrl.append(key) or True

        def drop():
            drops.append(len(casts))
            bot.running = False
        bot.adjust_bait_tier = drop
        bot.running = True
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        # miss (counted) / miss after own CTRL+G (not) / counted / not / counted
        self.assertEqual(drops, [5])
        self.assertEqual(len(ctrl), 5)   # one toggle per miss, as before

    def test_toggle_state_is_forgotten_after_a_bite(self):
        bot = _classic_bot()
        bot._mount_toggled = True
        bot.config["auto_fish_handling"] = False
        results = iter([True])
        bot.wait_for_classic_fish = lambda timeout=40: next(results, None) or bot.stop() or False
        bot.press_key = lambda *a, **k: None
        bot.handle_caught_item = lambda *a, **k: None
        bot.press_ctrl_key = lambda key: True
        bot._wait_before_recast = lambda: False
        bot.running = True
        bot.play_game()
        self.assertFalse(bot._mount_toggled)


class ClassicTemplateMissingTests(unittest.TestCase):
    """F3: without the bite picture the bot stops instead of reeling in after
    every cast and counting each as a catch."""

    def test_missing_template_stops_the_bot(self):
        _, bot, _ = quiet_bot()
        bot._load_classic_fish_template = lambda: None
        self.assertFalse(bot.wait_for_classic_fish(timeout=5))
        self.assertFalse(bot.running)
        self.assertEqual(bot.stop_reason, FishingBot.STOP_ERROR)


class PausedItemHandlingTests(unittest.TestCase):
    """F6: with F5 pressed, no item is picked up or right-clicked; the page
    is re-read after the resume."""

    def _bot(self):
        _, bot, _ = quiet_bot({"confirm_button_pos": [10, 10]})
        bot.paused = True
        clicks = []
        bot.human.click = lambda *a, **k: clicks.append(a)
        bot.human.move_to = lambda *a, **k: clicks.append(a)
        bot._focus = lambda force=False: True
        return bot, clicks

    def test_drop_is_not_started_while_paused(self):
        bot, clicks = self._bot()
        self.assertFalse(bot._handle_item("Shiri_living.jpg", 20, 20, "drop"))
        self.assertEqual(clicks, [])
        self.assertTrue(bot._was_paused)

    def test_open_is_not_started_while_paused(self):
        bot, clicks = self._bot()
        self.assertFalse(bot._handle_item("Shiri_living.jpg", 20, 20, "open"))
        self.assertEqual(clicks, [])
        self.assertTrue(bot._was_paused)


if __name__ == "__main__":
    unittest.main()

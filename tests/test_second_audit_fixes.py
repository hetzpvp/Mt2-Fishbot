"""Regression tests for the second 2026-11-17 audit. Each test fails when
its problem is back. Stub/simulator only: nothing here touches the desktop."""

import os
import tempfile
import types
import unittest
from unittest import mock

from _stubs import install

install()

import numpy as np  # noqa: E402

from jigsaw_detector import JigsawActionResult  # noqa: E402
from jigsaw_solver.jigsaw import SKIP_ACTION  # noqa: E402
import test_jigsaw_sim as jsim  # noqa: E402
from minigame_sim.jigsaw_game import SPECIAL  # noqa: E402
from test_review_fixes import quiet_bot  # noqa: E402

CORNERS = {(0, 0), (5, 0), (0, 3), (5, 3)}     # four single holes: no 3x2 and no 2x2 room


def _board_with_holes(game, holes):
    game.board = {(c, r) for c in range(6) for r in range(4)} - set(holes)
    game.cell_figure = {cell: 0 for cell in game.board}


def _hold(game, bot, figure):
    """The game starts with ``figure`` on the cursor at the bot's parked point."""
    park = bot._parked_point()
    game.held, game.held_at, game.cursor = figure, 0.0, park
    bot._check_hand = park


class DiscardDialogUnderHeldPieceTests(unittest.TestCase):
    """The held piece is drawn over the dialog. At the parked point it hid
    the Confirm area the discard dialog is watched at: completely for the
    3x2 piece, about 70 % for the 2x2, L and S pieces."""

    def _discard_once(self, figure, shade, priority="special_first"):
        game = jsim.JigsawGame(special=0, pieces=[0] * 50, dialog_shade=shade)
        _board_with_holes(game, CORNERS)
        rig = jsim.Rig([game], {"jigsaw_crate_priority": priority})
        _hold(game, rig.bots[0], figure)
        rig.run(lambda: game.discarded >= 1 or bool(rig.pauses), timeout=30)
        return game, rig

    def test_special_piece_can_be_discarded(self):
        game, rig = self._discard_once(SPECIAL, 190)
        self.assertEqual(game.discarded, 1, rig.logs[-12:])
        self.assertEqual(rig.pauses, [])
        self.assertEqual(game.errors, [])

    def test_square_piece_is_discarded_on_a_real_contrast_dialog(self):
        # 75 on the ~32 background: the change a real client's dialog makes
        game, rig = self._discard_once(4, 75, priority="normal_first")
        self.assertEqual(game.discarded, 1, rig.logs[-12:])
        self.assertEqual(rig.pauses, [])
        self.assertEqual(game.errors, [])

    def test_watch_point_leaves_the_confirm_area_clear(self):
        game = jsim.JigsawGame()
        bot = jsim.Rig([game]).bots[0]
        cx, cy = bot.confirm_button_pos
        wx, wy = bot._discard_watch_point()
        cell = jsim.GRID[2] / 6.0
        self.assertEqual(wy, cy)
        self.assertGreaterEqual(abs(wx - cx) - 1.5 * cell, 20)   # widest piece's edge clears the patch

    def test_watch_point_goes_left_near_the_right_edge(self):
        game = jsim.JigsawGame()
        bot = jsim.Rig([game], {"confirm_button_pos": [jsim.W - 30, 380]}).bots[0]
        self.assertLess(bot._discard_watch_point()[0], jsim.W - 30)


class SpecialFirstNoRoomTests(unittest.TestCase):
    """special_first opened a special crate even where its 3x2 piece has no
    room: the crate was lost (and its piece could not even be discarded)."""

    def test_normal_crate_is_opened_where_the_special_piece_does_not_fit(self):
        game = jsim.JigsawGame(special=5, pieces=[0] * 50)
        _board_with_holes(game, CORNERS)
        rig = jsim.Rig([game], {"jigsaw_crate_priority": "special_first"})
        self.assertTrue(rig.run(lambda: game.completed >= 1, timeout=60), rig.logs[-12:])
        self.assertEqual(game.opened_by["special"], 0)
        self.assertEqual(game.discarded, 0)
        self.assertEqual(rig.pauses, [])

    def test_special_crate_still_comes_first_where_it_fits(self):
        game = jsim.JigsawGame(special=1, pieces=[0] * 50)
        rig = jsim.Rig([game], {"jigsaw_crate_priority": "special_first"})
        self.assertTrue(rig.run(lambda: game.opened >= 2, timeout=60), rig.logs[-12:])
        self.assertEqual(game.special_opened_on, [0])
        self.assertEqual(game.special_placed, 1)

    def test_stops_when_only_unfitting_special_crates_are_left(self):
        game = jsim.JigsawGame(normal=0, special=3)
        _board_with_holes(game, CORNERS)
        rig = jsim.Rig([game], {"jigsaw_crate_priority": "special_first"})
        rig.run(lambda: not rig.threads[0].is_alive(), timeout=60)
        self.assertEqual(game.opened, 0)
        self.assertTrue(any("special piece does not fit" in m for m in rig.logs), rig.logs[-12:])


class UnsentPlacementTests(unittest.TestCase):
    def test_hand_is_checked_when_no_click_was_sent_even_if_no_piece_is_seen(self):
        bot = jsim.Rig([jsim.JigsawGame()]).bots[0]
        bot.running = True
        grid = types.SimpleNamespace(grid_found=True, board_mask=0)
        bot._await_board = lambda expected, timeout: (grid, False)
        bot._detect_cursor_piece = lambda cursor: types.SimpleNamespace(piece_id=None)   # not recognized
        bot._click_window_point = lambda *a, **k: False
        bot._last_click_sent = False
        self.assertFalse(bot._confirm_placement(0, 0b111, JigsawActionResult("place", (10, 10), "x"), (5, 5)))
        self.assertEqual(bot._check_hand, (5, 5))
        self.assertEqual(bot._suspect_mask, 0)


class CrateCounterTests(unittest.TestCase):
    def test_round_with_a_held_piece_is_not_counted_as_a_crate(self):
        game = jsim.JigsawGame(pieces=[0] * 50)
        _board_with_holes(game, CORNERS)
        rig = jsim.Rig([game])
        _hold(game, rig.bots[0], 0)
        self.assertTrue(rig.run(lambda: game.completed >= 1, timeout=60), rig.logs[-12:])
        self.assertEqual(rig.bots[0].crates_completed, game.opened)


class StartupReturnPageTests(unittest.TestCase):
    """Startup could not click back to the first page with room: the last
    page stayed on screen, and the first catch read it as 'all pages full'."""

    def test_pages_are_read_again_before_casting(self):
        _, bot, _ = quiet_bot({"auto_fish_handling": True, "fish_actions": {"x_item.jpg": "keep"},
                               "inv_page_1_pos": [10, 10], "inv_page_2_pos": [30, 10]})
        bot._require_inventory_visible = lambda when: True
        clicks = []
        bot._goto_page = lambda pos, settle=0.3: clicks.append(tuple(pos)) or len(clicks) <= 2

        def process(_actions):
            bot._empty_slot_positions = [(5, 5)] if bot._current_inv_page == 0 else []
        bot._startup_process_page = process
        bot._startup_scan_and_process_all_pages()
        self.assertEqual(clicks, [(10, 10), (30, 10), (10, 10)])     # the way back failed
        self.assertTrue(bot._full_rescan_wanted)


class SolverCacheMemoryTests(unittest.TestCase):
    def test_cache_is_kept_when_memory_runs_out_while_loading(self):
        from jigsaw_solver import deterministic
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cache.npz")
            np.savez(path, dsts=np.zeros(6), actions=np.zeros(6, np.uint8))
            with mock.patch.object(deterministic.Deterministic, "_validate_cache", side_effect=MemoryError):
                with self.assertRaises(MemoryError):
                    deterministic.Deterministic().load_cache(path)
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()

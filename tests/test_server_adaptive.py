"""Behaviors learned from what the game does (2026-11-25 server source review).

None of them assumes a particular server: each one learns from what the game
does and leaves a server that behaves normally as it was.
"""
import unittest

import numpy as np

from _stubs import install, FakeWindowManager

install()

from catch_timing import CatchTimingStats  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402
from jigsaw_bot import JigsawBot  # noqa: E402
from jigsaw_detector import JigsawGridResult  # noqa: E402
from jigsaw_solver.jigsaw import Jigsaw  # noqa: E402


def make_fishing_bot(config=None):
    bot = FishingBot(None, dict(config or {}), FakeWindowManager(), bait_counter=10, bot_id=0)
    bot.running = True
    return bot


class CatchTimingStatsTests(unittest.TestCase):
    def test_buckets_per_kind(self):
        stats = CatchTimingStats()
        stats.record("classic", 2.95, True)
        stats.record("classic", 2.91, False)
        stats.record("classic", 3.12, True)
        stats.record("minigame", 7.6, True)
        stats.record("minigame", 7.9, False)
        self.assertEqual(stats.rows(), [("classic", 2.9, 2, 1), ("classic", 3.1, 1, 1),
                                        ("minigame", 7.5, 2, 1)])
        self.assertIn("classic 2.90-3.00s: 1/2 with an item (50%)", stats.summary())

    def test_empty_summary(self):
        self.assertIsNone(CatchTimingStats().summary())


class CatchJudgingTests(unittest.TestCase):
    def test_timing_recorded_once_with_its_outcome(self):
        bot = make_fishing_bot()
        bot._set_catch_timing("minigame", 4.2)
        bot._judge_catch(True, "Carp_living.jpg")
        bot._judge_catch(False, "again")   # already judged: nothing more recorded
        self.assertEqual(bot.catch_timing_stats.rows(), [("minigame", 4.0, 1, 1)])

    def test_merged_catches_are_not_recorded(self):
        bot = make_fishing_bot()
        bot._pending_catch = (np.zeros((10, 10, 3), np.uint8), True)
        bot._set_catch_timing("minigame", 3.0)
        bot._defer_catch(may_have_catch=True)   # two games' catches in one look
        bot._judge_catch(True, "x")
        self.assertEqual(bot.catch_timing_stats.rows(), [])

    def test_without_auto_handling_an_inventory_change_is_an_item(self):
        bot = make_fishing_bot({"auto_fish_handling": False})
        frame = np.zeros((40, 200, 3), np.uint8)
        bot._restore_fill_page = lambda: None
        bot._page_has_space = lambda *a: True
        bot._wait_inventory_change = lambda base, until, *a: (frame, (0, 0, 10, 10))
        bot._set_catch_timing("classic", 2.93)
        bot.handle_caught_item(baseline=frame)
        bot._wait_inventory_change = lambda base, until, *a: (frame, None)
        bot._set_catch_timing("classic", 2.97)
        bot.handle_caught_item(baseline=frame)
        bot._set_catch_timing("classic", 3.05)
        bot.handle_caught_item(may_have_catch=False, baseline=frame)
        self.assertEqual(bot.catch_timing_stats.rows(), [("classic", 2.9, 2, 1), ("classic", 3.0, 1, 0)])


class ClassicBiteTimeoutTests(unittest.TestCase):
    def test_default_covers_the_stock_40_s_bite(self):
        self.assertGreater(make_fishing_bot()._classic_bite_timeout(), 41.0)

    def test_follows_the_longest_bite_seen(self):
        bot = make_fishing_bot()
        bot._longest_classic_bite = 30.0
        self.assertEqual(bot._classic_bite_timeout(), 45.0)
        bot._longest_classic_bite = 50.0
        self.assertAlmostEqual(bot._classic_bite_timeout(), 62.0)
        bot._longest_classic_bite = 500.0
        self.assertEqual(bot._classic_bite_timeout(), FishingBot._CLASSIC_BITE_TIMEOUT_CAP)

    def test_configured_value(self):
        self.assertEqual(make_fishing_bot({"classic_bite_timeout": 70})._classic_bite_timeout(), 70.0)
        self.assertEqual(make_fishing_bot({"classic_bite_timeout": "x"})._classic_bite_timeout(), 45.0)


def cells_mask(cells):
    mask = 0
    for cell in cells:
        mask |= JigsawBot._cell_bit(cell)
    return mask


class JigsawAdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.bot = JigsawBot(window_manager=None, config={"jigsaw_dry_run": True})
        self.bot._status = lambda *a, **k: None
        centers = [(16 + 32 * c, 16 + 32 * r) for r in range(4) for c in range(6)]
        self.grid = JigsawGridResult(True, (0, 0, 192, 128), centers, 0, 1.0)
        self.z = self.bot.pieces["figure_5"]
        self.l = self.bot.pieces["figure_2"]
        self.mirrored_l = self.bot.pieces["figure_3"]

    def test_cell_bits_match_the_action_encoding(self):
        action = Jigsaw.offset_to_action((1, 0))
        cells = self.bot._cells_for_action(action, self.z)
        self.assertEqual(sorted(cells), [1, 2, 8, 9])
        self.assertEqual(cells_mask(cells), self.z.mask >> action)

    def test_z_aim_unchanged_until_a_shift_repeats(self):
        action = Jigsaw.offset_to_action((1, 0))
        aimed = self.bot._target_for_action(self.grid, action, self.z)
        self.assertEqual(aimed, (round((1 + 1.25) * 32), 16))
        expected = cells_mask([1, 2, 8, 9])
        landed = cells_mask([2, 3, 9, 10])      # one column right
        self.bot._note_landing_shift(0, expected, landed)
        self.assertEqual(self.bot._target_for_action(self.grid, action, self.z), aimed)
        self.bot._note_landing_shift(0, expected, landed)
        corrected = self.bot._target_for_action(self.grid, action, self.z)
        self.assertEqual(corrected, (round((1 + 0.5) * 32), 16))   # middle of the main cell

    def test_exact_landing_resets_the_count(self):
        expected = cells_mask([1, 2, 8, 9])
        self.bot._note_landing_shift(0, expected, cells_mask([2, 3, 9, 10]))
        self.bot._note_exact_landing(0, expected)
        self.bot._note_landing_shift(0, expected, cells_mask([2, 3, 9, 10]))
        self.assertEqual(self.bot._landing_correction, {})

    def test_unrelated_changes_teach_nothing(self):
        expected = cells_mask([1, 2, 8, 9])
        for _ in range(3):
            self.bot._note_landing_shift(0, expected, cells_mask([14, 15, 21, 22]))
            self.bot._note_landing_shift(cells_mask([0]), expected | cells_mask([0]), 0)  # board cleared
        self.assertEqual(self.bot._landing_correction, {})

    def test_corrected_aim_stays_on_the_board(self):
        self.bot._landing_correction[(1, 3)] = (1, 0)
        action = Jigsaw.offset_to_action((0, 0))
        x, _ = self.bot._target_for_action(self.grid, action, self.bot.pieces["figure_1"])
        self.assertEqual(x, 16)

    def _l_at_col0(self):
        return cells_mask(self.bot._cells_for_action(Jigsaw.offset_to_action((0, 0)), self.l))

    def test_bbox_refusal_keeps_the_piece_and_marks_nothing(self):
        board = self._l_at_col0()                       # cells 0, 6, 7
        interlock = Jigsaw.offset_to_action((1, 0))    # mirrored L: cells 1, 2, 8; box has 7
        cells = self.bot._cells_for_action(interlock, self.mirrored_l)
        self.assertTrue(self.bot._bbox_blocked(board, cells))
        handled = self.bot._bbox_refusal(board, board | cells_mask(cells), (5, 5))
        self.assertTrue(handled)
        self.assertTrue(self.bot._bbox_fit)
        self.assertEqual(self.bot._check_hand, (5, 5))
        self.assertEqual(self.bot._suspect_mask, 0)
        # a second refusal under that rule is not explained by it
        self.assertFalse(self.bot._bbox_refusal(board, board | cells_mask(cells), (5, 5)))

    def test_refusal_with_a_free_box_is_not_a_bbox_refusal(self):
        cells = self.bot._cells_for_action(Jigsaw.offset_to_action((3, 2)), self.mirrored_l)
        self.assertFalse(self.bot._bbox_refusal(self._l_at_col0(), self._l_at_col0() | cells_mask(cells), None))
        self.assertFalse(self.bot._bbox_fit)

    def _fake_solver(self, ranked):
        class Item:
            def __init__(self, action):
                self.action, self.is_skip = action, action is None

        class Solver:
            def solve(self, state):
                return ranked[0]

            def rank_actions(self, board, figure, limit=None, include_skip=True):
                return [Item(a) for a in ranked]
        return Solver()

    def test_bbox_rule_steers_the_decision(self):
        board = self._l_at_col0()
        grid = JigsawGridResult(True, self.grid.grid_bounds, self.grid.cell_centers, board, 1.0)
        interlock, free = Jigsaw.offset_to_action((1, 0)), Jigsaw.offset_to_action((3, 2))
        self.bot.solver = self._fake_solver([interlock, free, None])
        decision, explanation = self.bot._decide_action(grid, self.mirrored_l)
        self.assertEqual(explanation["chosen_action"], interlock)   # real-cell rule: the interlock
        self.bot._bbox_fit = True
        decision, explanation = self.bot._decide_action(grid, self.mirrored_l)
        self.assertEqual((decision.action, explanation["chosen_action"]), ("place", free))
        self.bot.solver = self._fake_solver([interlock, None, free])
        decision, _ = self.bot._decide_action(grid, self.mirrored_l)
        self.assertEqual(decision.action, "discard")   # skipping ranks above the next free box

    def test_accepted_blocked_box_disproves_the_rule(self):
        board = self._l_at_col0()
        cells = self.bot._cells_for_action(Jigsaw.offset_to_action((1, 0)), self.mirrored_l)
        self.bot._note_exact_landing(board, board | cells_mask(cells))
        self.assertTrue(self.bot._bbox_disproven)
        self.assertFalse(self.bot._bbox_refusal(board, board | cells_mask(cells), None))


if __name__ == "__main__":
    unittest.main()

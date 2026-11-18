"""End-to-end jigsaw runs against a simulated event window.

Several simulated game windows sit side by side on one desktop and share a
single cursor and a single focus, exactly like real clients do. Each window
has its own board, crate slots, inventory, dialogs and a piece that follows
the cursor. The bots run on their own threads through the real input lock.

What the simulation checks is that a bot stays in step with its game: it never
opens a crate while a piece is still on the cursor, never tries a placement
the board cannot take, never clicks into another window, and recovers from
the things that go wrong in practice (lost clicks, a dialog that shows up
late)."""

import os
import random
import sys
import threading
import time
import unittest

import cv2
import numpy as np

from _stubs import install, ROOT

install()

import jigsaw_bot as jigsaw_module  # noqa: E402
from jigsaw_bot import JigsawBot, FULL_BOARD_MASK  # noqa: E402
from jigsaw_solver.jigsaw import FIGURES, Jigsaw, SKIP_ACTION  # noqa: E402

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
from minigame_sim.jigsaw_game import (  # noqa: E402
    JigsawGame, figure_cells, W, H, GRID, CELL, CONFIRM, SLOTS, PAGES, INV_ORIGIN)


class Desktop:
    """One cursor, one focused window."""

    def __init__(self, switch_delay=0.0, refuse_focus=0.0):
        self.games = []
        self.pos = (0, 0)
        self.foreground = None
        self.misdirected = 0       # clicks that went to a window that was not in front
        self.lock = threading.Lock()
        self.switch_delay = switch_delay     # seconds a focus switch takes
        self.refuse_focus = refuse_focus     # share of focus requests the OS ignores
        self.refused = 0
        self.rng = random.Random(99)

    def game_at(self, pos):
        for game in self.games:
            if game.left <= pos[0] < game.left + W and game.top <= pos[1] < game.top + H:
                return game
        return None


class SharedMouse:
    name = "sim"

    def __init__(self, desktop):
        self.desktop = desktop

    def move_to(self, x, y):
        d = self.desktop
        with d.lock:
            d.pos = (int(x), int(y))
            game = d.game_at(d.pos)
            if game is not None:
                game.cursor = (d.pos[0] - game.left, d.pos[1] - game.top)

    def position(self):
        return self.desktop.pos

    def _click(self, button):
        d = self.desktop
        with d.lock:
            game = d.game_at(d.pos)
            if game is None:
                return
            if game is not d.foreground:
                d.misdirected += 1
                return
            game.click(d.pos[0] - game.left, d.pos[1] - game.top, button)

    def click(self, button="left"):
        self._click(button)

    def mouse_down(self, button="left"):
        self._click(button)

    def mouse_up(self, button="left"):
        pass


class SimWM:
    def __init__(self, desktop, game):
        self.desktop = desktop
        self.game = game
        self.selected_window = object()
        self.last_switch_time = 0.0

    def is_window_available(self):
        return True

    def is_foreground(self):
        return self.desktop.foreground is self.game

    def activate_window(self, force_activate=False):
        d = self.desktop
        if d.foreground is not self.game:
            if d.refuse_focus and d.rng.random() < d.refuse_focus:
                d.refused += 1
                return False
            if d.switch_delay:
                time.sleep(d.switch_delay)
            d.foreground = self.game
            self.last_switch_time = time.perf_counter()
        return True

    def get_window_rect(self):
        return (self.game.left, self.game.top, W, H)

    def invalidate_rect_cache(self):
        pass


class GreedySolver:
    """Stand-in for the DP solver (whose tables take minutes to build):
    first legal placement, otherwise skip."""

    class _Score:
        def __init__(self, action, board):
            self.action, self.next_board = action, board
            self.expected_rounds, self.is_skip = 0.0, action == SKIP_ACTION

    def solve(self, state):
        legal = [a for a in state.legal_actions() if a != SKIP_ACTION]
        return legal[0] if legal else SKIP_ACTION

    def rank_actions(self, board, figure, limit=None, include_skip=True):
        return [self._Score(self.solve(Jigsaw(board, figure, 0)), board)]

    def rank_custom_actions(self, board, mask, max_offset, limit=None):
        out = []
        for action in range(24):
            x, y = Jigsaw.action_to_offsets(action)
            if x <= max_offset[0] and y <= max_offset[1] and not (board & (mask >> action)):
                out.append(self._Score(action, board | (mask >> action)))
        return out[:limit] if limit else out


CONFIG = {
    "confirm_button_pos": CONFIRM,
    "jigsaw_grid_bounds": list(GRID),
    "inv_page_1_pos": PAGES[1],
    "inv_page_2_pos": PAGES[2],
    "capture_backend": "screen",
    "timing_cursor_settle": 0.0,
    "jigsaw_action_delay": 0.01,
    "jigsaw_piece_detection_retry_delay": 0.03,
    "jigsaw_post_crate_piece_wait": 0.03,
    "jigsaw_crate_confirm_delay": 0.0,
    "jigsaw_inventory_page_delay": 0.02,
    "jigsaw_post_complete_wait": 0.1,
    "jigsaw_place_verify_timeout": 0.4,
    "jigsaw_dialog_wait_timeout": 0.5,
    "jigsaw_completion_poll_interval": 0.02,
}


class Rig:
    """A desktop with one bot per simulated window."""

    def __init__(self, games, config=None, **desktop_options):
        jigsaw_module.get_solver = lambda: GreedySolver()
        self.desktop = Desktop(**desktop_options)
        self.games = games
        self.bots = []
        self.logs = []
        self.pauses = []
        mouse = SharedMouse(self.desktop)
        for i, game in enumerate(games):
            self.desktop.games.append(game)
            wm = SimWM(self.desktop, game)
            bot = JigsawBot(wm, dict(CONFIG, **(config or {})), bot_id=i,
                            on_status_update=self.logs.append,
                            on_pause_change=lambda bid, paused: self.pauses.append((bid, paused)))
            bot.mouse_backend = mouse
            bot.human.mouse = mouse
            bot.capture.grab = game.grab
            bot._SWITCH_SETTLE = 0.0
            bot._PARK_SETTLE = 0.0
            self.bots.append(bot)
        self.threads = []

    def run(self, until, timeout=60.0):
        self.threads = [threading.Thread(target=b.start, daemon=True) for b in self.bots]
        for t in self.threads:
            t.start()
        deadline = time.time() + timeout
        while time.time() < deadline and not until():
            if not any(t.is_alive() for t in self.threads):
                break
            time.sleep(0.02)
        ok = until()
        self.stop()
        return ok

    def stop(self):
        for bot in self.bots:
            bot.stop()
        for t in self.threads:
            t.join(timeout=10)
        assert not any(t.is_alive() for t in self.threads), "a jigsaw worker did not stop"


class JigsawSimTests(unittest.TestCase):
    def assert_in_step(self, rig):
        self.assertEqual(rig.desktop.misdirected, 0, "clicks landed in a window that was not in front")
        for game, bot in zip(rig.games, rig.bots):
            self.assertEqual(game.errors, [], rig.logs[-12:])
            if game.dialog is None and game.held is None:
                seen = bot.detector.detect_grid(game.render())
                self.assertEqual(seen.board_mask, game.mask(), "the bot's reading of the board is wrong")

    def test_single_window_plays_cleanly(self):
        game = JigsawGame()
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: game.opened >= 14), rig.logs[-12:])
        self.assert_in_step(rig)
        self.assertGreaterEqual(game.placed, 5)
        self.assertEqual(game.page_clicks, 0, "inventory was searched although the slot was full")
        self.assertEqual(game.early_confirms, 0)

    def test_three_windows_in_parallel(self):
        games = [JigsawGame(left=i * W, seed=10 + i) for i in range(3)]
        rig = Rig(games)
        self.assertTrue(rig.run(lambda: all(g.opened >= 10 for g in games), timeout=90), rig.logs[-12:])
        self.assert_in_step(rig)
        for game in games:
            self.assertGreaterEqual(game.placed, 4)
            self.assertEqual(game.page_clicks, 0)

    def test_lost_placement_clicks_do_not_desync(self):
        game = JigsawGame(seed=3, lose_clicks=0.4)
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: game.opened >= 12), rig.logs[-12:])
        self.assertGreater(game.lost, 0)
        self.assert_in_step(rig)
        self.assertTrue(any("clicking again" in m for m in rig.logs))

    def test_lost_clicks_with_three_windows(self):
        games = [JigsawGame(left=i * W, seed=20 + i, lose_clicks=0.3) for i in range(3)]
        rig = Rig(games)
        self.assertTrue(rig.run(lambda: all(g.opened >= 8 for g in games), timeout=120), rig.logs[-12:])
        self.assert_in_step(rig)

    def test_late_dialog_is_waited_for(self):
        game = JigsawGame(seed=5, dialog_delay=0.15)
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: game.opened >= 8), rig.logs[-12:])
        self.assertEqual(game.early_confirms, 0, "confirm was clicked before the dialog existed")
        self.assert_in_step(rig)

    def test_dialog_slower_than_the_wait_is_recovered(self):
        game = JigsawGame(seed=6, dialog_delay=0.3)
        rig = Rig([game], {"jigsaw_dialog_wait_timeout": 0.1})
        self.assertTrue(rig.run(lambda: game.placed >= 4, timeout=90), rig.logs[-12:])
        # A dialog later than the wait is waited for a little longer rather
        # than confirmed blindly (a confirm with no dialog hits the world).
        self.assertEqual(game.early_confirms, 0)
        self.assert_in_step(rig)

    def test_board_completion(self):
        game = JigsawGame(pieces=[0] * 40)             # single cells only
        game.board = {(c, r) for c in range(6) for r in range(4)} - {(5, 3), (4, 3)}
        game.cell_figure = {cell: 4 for cell in game.board}
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: game.completed >= 1 and game.dialog is None and game.placed >= 4),
                        rig.logs[-12:])
        self.assert_in_step(rig)
        self.assertEqual(rig.bots[0].boards_completed, game.completed)

    def test_empty_slot_is_refilled_from_the_inventory_once(self):
        game = JigsawGame(normal=0, inventory={2: ("normal", (60, 120), 6)})
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: not rig.threads[0].is_alive(), timeout=90), rig.logs[-12:])
        self.assertEqual(game.opened, 6)               # used the whole stack, then stopped
        self.assertTrue(any("No jigsaw crates left" in m for m in rig.logs), rig.logs[-12:])
        # Two searches of the two pages: the refill, and the one that found nothing.
        self.assertLessEqual(game.page_clicks, 4)
        self.assert_in_step(rig)

    def test_missing_special_crate_is_not_searched_for_every_round(self):
        game = JigsawGame(special=0)
        rig = Rig([game], {"jigsaw_crate_priority": "special_first"})
        self.assertTrue(rig.run(lambda: game.opened >= 10), rig.logs[-12:])
        self.assertLessEqual(game.page_clicks, 2)      # one search, not one per round
        self.assert_in_step(rig)

    def test_special_crate_gives_the_3x2_piece_and_it_is_placed(self):
        game = JigsawGame(special=3)
        rig = Rig([game], {"jigsaw_crate_priority": "special_first"})
        self.assertTrue(rig.run(lambda: game.opened_by["special"] == 3 and game.opened >= 6), rig.logs[-12:])
        self.assert_in_step(rig)
        self.assertEqual(game.special_opened_on[0], 0)
        self.assertEqual(game.special_placed, 3)       # three 3x2 pieces fit an empty board

    def test_special_crate_is_kept_for_boards_where_it_pays(self):
        class Solver(GreedySolver):
            # Worth it on an empty board only: 3 rounds saved there, none elsewhere
            def avg_distance(self, board):
                return 10.0 if board == 0 else 6.0 if bin(board).count("1") == 6 else 5.0

            def rank_custom_actions(self, board, mask, max_offset, limit=None):
                out = super().rank_custom_actions(board, mask, max_offset, limit)
                for score in out:
                    score.expected_rounds = 1.0 + self.avg_distance(score.next_board)
                return out

        game = JigsawGame(special=5, pieces=[0] * 200)      # normal crates give single cells
        rig = Rig([game], {"jigsaw_crate_priority": "special_when_better"})
        jigsaw_module.get_solver = lambda: Solver()
        self.assertTrue(rig.run(lambda: game.completed >= 2 and game.dialog is None and game.placed >= 22,
                                timeout=120), rig.logs[-12:])
        self.assert_in_step(rig)
        self.assertGreaterEqual(len(game.special_opened_on), 2)
        self.assertEqual(set(game.special_opened_on), {0}, "a special crate was spent where it does not pay")
        self.assertGreater(game.opened_by["normal"], game.opened_by["special"])

    def test_last_special_crates_are_used_when_normal_ones_ran_out(self):
        game = JigsawGame(normal=2, special=2, pieces=[0] * 10)
        rig = Rig([game], {"jigsaw_crate_priority": "special_when_better", "jigsaw_special_min_gain": 99})
        self.assertTrue(rig.run(lambda: not rig.threads[0].is_alive(), timeout=90), rig.logs[-12:])
        self.assertEqual(game.opened_by, {"normal": 2, "special": 2})
        self.assert_in_step(rig)

    def test_crates_run_out_and_are_restocked_from_several_pages(self):
        game = JigsawGame(normal=2, special=0, inventory={
            1: [("normal", (60, 80), 3)],
            2: [("special", (100, 120), 1), ("normal", (60, 160), 2)],
        })
        rig = Rig([game], {"jigsaw_crate_priority": "special_first"})
        self.assertTrue(rig.run(lambda: not rig.threads[0].is_alive(), timeout=120), rig.logs[-12:])
        self.assertEqual(game.opened_by, {"normal": 7, "special": 1}, rig.logs[-12:])
        self.assertEqual(game.refills, 3)
        self.assertEqual(game.inventory, {1: [], 2: []})
        self.assertTrue(any("No jigsaw crates left" in m for m in rig.logs), rig.logs[-12:])
        self.assert_in_step(rig)

    def test_pauses_when_no_piece_ever_appears(self):
        game = JigsawGame(gives_pieces=False)
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: rig.bots[0].paused, timeout=30), rig.logs[-12:])
        self.assertIn((0, True), rig.pauses)
        self.assertLessEqual(game.opened, 3)
        self.assertEqual(game.errors, [])

    def test_dry_run_touches_nothing(self):
        game = JigsawGame()
        rig = Rig([game], {"jigsaw_dry_run": True})
        rig.run(lambda: False, timeout=1.0)
        self.assertEqual((game.opened, game.placed, rig.desktop.pos), (0, 0, (0, 0)))


class JigsawHardTests(unittest.TestCase):
    """The same game with more going wrong at once."""

    assert_in_step = JigsawSimTests.assert_in_step

    def test_window_anywhere_on_the_desktop(self):
        game = JigsawGame(left=730, top=410, seed=7)
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: game.opened >= 10), rig.logs[-12:])
        self.assert_in_step(rig)
        self.assertGreaterEqual(game.placed, 4)

    def test_five_windows_with_everything_unreliable(self):
        """Two rows of windows, slow focus switches, dialogs and pieces that
        take a different time to appear every round, lost placement clicks."""
        games = [JigsawGame(left=(i % 3) * W, top=(i // 3) * H, seed=50 + i, lose_clicks=0.2,
                            dialog_delay=(0.0, 0.2), piece_delay=(0.0, 0.08)) for i in range(5)]
        rig = Rig(games, switch_delay=0.01)
        self.assertTrue(rig.run(lambda: all(g.opened >= 6 for g in games), timeout=240), rig.logs[-12:])
        self.assert_in_step(rig)
        for game in games:
            self.assertEqual(game.early_confirms, 0, "confirm was clicked before its dialog existed")
            self.assertGreaterEqual(game.placed, 2)

    def test_os_that_sometimes_refuses_to_switch_windows(self):
        games = [JigsawGame(left=i * W, seed=60 + i) for i in range(3)]
        rig = Rig(games, switch_delay=0.005, refuse_focus=0.3)
        self.assertTrue(rig.run(lambda: all(g.opened >= 6 for g in games), timeout=240), rig.logs[-12:])
        self.assertGreater(rig.desktop.refused, 5)
        self.assert_in_step(rig)            # includes: no click reached a window that was not in front

    def test_lost_confirm_clicks(self):
        game = JigsawGame(seed=8, lose_confirms=0.3)
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: game.placed >= 6, timeout=120), rig.logs[-12:])
        self.assertGreater(game.lost_confirms, 0)
        self.assert_in_step(rig)

    def test_crate_that_does_not_arrive_in_its_slot_is_put_there_again(self):
        game = JigsawGame(normal=0, inventory={2: ("normal", (60, 120), 4)}, lose_slot_clicks=1)
        rig = Rig([game])
        self.assertTrue(rig.run(lambda: not rig.threads[0].is_alive(), timeout=90), rig.logs[-12:])
        self.assertEqual(game.lost_slot_clicks, 1)
        self.assertEqual(game.opened, 4, rig.logs[-12:])       # the stack was not lost on the cursor
        self.assertIsNone(game.carrying)
        self.assertTrue(any("did not arrive" in m for m in rig.logs), rig.logs[-12:])
        self.assert_in_step(rig)

    def test_discard_that_is_confirmed_too_early_is_noticed(self):
        # Only 2x2 squares on a board that has no room for one: every piece is discarded
        game = JigsawGame(seed=9, dialog_delay=0.25, pieces=[4] * 50)
        game.board = {(c, r) for c in range(6) for r in range(4) if (c + r) % 2 == 0}
        game.cell_figure = {cell: 0 for cell in game.board}
        rig = Rig([game], {"jigsaw_dialog_wait_timeout": 0.1})
        self.assertTrue(rig.run(lambda: game.discarded >= 3, timeout=120), rig.logs[-12:])
        self.assert_in_step(rig)
        # The confirm waits for the late dialog instead of going out early.
        self.assertEqual(game.early_confirms, 0, rig.logs[-12:])

    def test_stop_in_the_middle_of_a_round_sends_nothing_more(self):
        games = [JigsawGame(left=i * W, seed=70 + i, dialog_delay=(0.0, 0.1)) for i in range(3)]
        rig = Rig(games)
        clicks, stopped_at = [], []
        mouse = rig.bots[0].mouse_backend            # the one mouse all bots share
        click = mouse._click
        mouse._click = lambda button: (clicks.append(time.perf_counter()), click(button))
        for bot in rig.bots:
            stop = bot.stop
            bot.stop = lambda stop=stop: (stopped_at.append(time.perf_counter()), stop())
        rig.run(lambda: all(g.opened >= 4 for g in games), timeout=120)
        # A click that was already on its way out may still land; nothing after that
        late = [round(t - max(stopped_at), 3) for t in clicks if t > max(stopped_at) + 0.05]
        self.assertEqual(late, [])
        self.assert_in_step(rig)


class SolverSingletonTests(unittest.TestCase):
    def test_concurrent_starts_build_one_solver(self):
        import jigsaw_solver.deterministic as det
        built = []

        class Slow:
            def __init__(self):
                built.append(1)

            def run(self):
                time.sleep(0.05)

        old_cls, old_cached = det.Deterministic, det._cached_solver
        det.Deterministic, det._cached_solver = Slow, None
        try:
            got = []
            threads = [threading.Thread(target=lambda: got.append(det.get_solver())) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(built), 1)
            self.assertEqual(len({id(s) for s in got}), 1)
        finally:
            det.Deterministic, det._cached_solver = old_cls, old_cached


if __name__ == "__main__":
    unittest.main()

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


def _minigame_bot(quick_skip=True, mode="horse"):
    _, bot, _ = quiet_bot({"classic_fishing": False, "quick_skip": quick_skip,
                           "quick_skip_mode": mode})
    bot.running = False
    bot._startup_scan_and_process_all_pages = lambda: None
    bot._verify_capture = lambda: None
    bot._refuse_overlapping_windows = lambda: True
    bot._game_screen_still_there = lambda: True
    bot._handle_quest_dialog = lambda *a, **k: False
    bot._bite_ever_seen = True
    bot._MOUNT_SETTLE = 0.0
    bot.bait_and_cast = lambda: True
    return bot


class MinigameMountRecoveryTests(unittest.TestCase):
    """2026-11-20 session: the quick skip's second CTRL+G was lost (the game
    mounted late), the character stayed on the horse, and five casts without
    a minigame wrote off the bait key with 88 bait left. A miss with the
    horse quick skip on now toggles the mount, as classic mode does."""

    def _run(self, bot, results):
        casts, ctrl, drops = [], [], []
        results = iter(results)

        def wait(timeout=None):
            casts.append(1)
            r = next(results, None)
            if r is None:
                bot.running = False
                return False
            return r
        bot.wait_for_minigame_window = wait
        bot.press_ctrl_key = lambda key: ctrl.append(key) or True

        def drop():
            drops.append(len(casts))
            bot.running = False
        bot.adjust_bait_tier = drop
        bot._play_minigame = lambda: (bot.stop() or (False, 0))
        bot.running = True
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        return casts, ctrl, drops

    def test_miss_presses_ctrl_g_and_the_next_cast_works(self):
        bot = _minigame_bot()
        bot._quickskip_unchecked = True   # a quick skip came before the cast
        casts, ctrl, drops = self._run(bot, [False, True])
        self.assertEqual(len(casts), 2)
        self.assertEqual(ctrl, ["g"])
        self.assertEqual(drops, [])
        self.assertFalse(bot._mount_toggled)
        self.assertFalse(bot._quickskip_unchecked)
        self.assertEqual(bot.consecutive_failures, 0)

    def test_miss_after_own_ctrl_g_is_not_counted(self):
        bot = _minigame_bot()
        bot._quickskip_unchecked = True
        casts, ctrl, drops = self._run(bot, [False] * 20)
        # counted + toggle / not counted, toggled back / then no more
        # toggles (no quick skip since): 4 more counted misses
        self.assertEqual(drops, [6])
        self.assertEqual(len(ctrl), 2)

    def test_no_ctrl_g_without_a_quick_skip_before_the_miss(self):
        """Diag 20261121_122030: the first cast of a session missed (a game
        left open from before the start ran out) and the recovery CTRL+G
        mounted a character the bot had never put on the horse."""
        bot = _minigame_bot()
        casts, ctrl, drops = self._run(bot, [False] * 20)
        self.assertEqual(ctrl, [])
        self.assertEqual(drops, [5])

    @staticmethod
    def _pause_briefly(bot):
        """Paused when the loop starts, resumed 0.3 s later (the resume path runs)."""
        bot.paused = True
        threading.Timer(0.3, lambda: setattr(bot, "paused", False)).start()

    def test_a_lost_second_ctrl_g_survives_a_pause(self):
        """Trailer take 2026-11-27: F5 came between the quick skip's two
        CTRL+G presses. The resume forgot the quick skip, the character
        stayed on the horse, and every cast after it missed."""
        bot = _minigame_bot()
        bot._qs_second_lost = True
        bot._quickskip_unchecked = True
        self._pause_briefly(bot)
        casts, ctrl, drops = self._run(bot, [False, True])
        self.assertEqual(ctrl, ["g"])
        self.assertEqual(drops, [])
        self.assertFalse(bot._qs_second_lost)

    def test_a_plain_pause_gets_no_recovery_ctrl_g(self):
        bot = _minigame_bot()
        bot._quickskip_unchecked = True       # forgotten by the resume: the player had the controls
        self._pause_briefly(bot)
        casts, ctrl, drops = self._run(bot, [False] * 20)
        self.assertEqual(ctrl, [])
        self.assertEqual(drops, [5])

    def test_no_ctrl_g_without_the_horse_quick_skip(self):
        for qs, mode in ((False, "horse"), (True, "armor")):
            bot = _minigame_bot(quick_skip=qs, mode=mode)
            casts, ctrl, drops = self._run(bot, [False] * 20)
            self.assertEqual(ctrl, [], (qs, mode))
            self.assertEqual(drops, [5], (qs, mode))


class OpenMinigameAtStartTests(unittest.TestCase):
    """Diag 20261121_122030: started with a minigame running. The startup
    scan let its timer run out and the bot cast over it."""

    def test_open_game_is_played_before_the_startup_scan(self):
        bot = _minigame_bot()
        order = []
        bot._game_running_at_start = lambda: (277, 213, 247, 195)
        bot._known_panel_in = lambda frame: (277, 213, 247, 195)
        bot.capture_full_window = lambda *a, **k: None
        bot._startup_scan_and_process_all_pages = lambda: order.append("scan")
        bot.bait_and_cast = lambda: order.append("cast") or True
        bot.press_ctrl_key = lambda key: order.append("ctrl") or True

        def wait(timeout=None):
            order.append("wait")
            return True
        bot.wait_for_minigame_window = wait

        def play():
            order.append("play")
            if order.count("play") >= 2:
                bot.stop()
            return False, 1
        bot._play_minigame = play
        bot.quickskip = lambda: order.append("skip")
        bot._note_fishless_game = lambda: True
        bot.running = True
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        # no cast before the open game, the scan right after it
        self.assertEqual(order[:4], ["wait", "play", "skip", "scan"])
        self.assertIn("cast", order[4:])
        self.assertNotIn("ctrl", order)
        self.assertFalse(bot._startup_scan_after_game)

    def test_no_open_game_scans_at_once(self):
        bot = _minigame_bot()
        bot._game_running_at_start = lambda: None
        scans = []
        bot._startup_scan_and_process_all_pages = lambda: scans.append(1) or bot.stop()
        bot.running = True
        bot.play_game()
        self.assertEqual(scans, [1])
        self.assertFalse(bot._line_out)


class GameRunningAtStartTests(unittest.TestCase):
    """A panel-colored thing at the start counts as a running game only
    when it changes between two looks."""

    def _bot(self, frames):
        bot = _minigame_bot()
        frames = iter(frames)
        bot.capture_full_window = lambda *a, **k: next(frames)
        bot._known_panel_in = lambda frame: (10, 10, 50, 40)
        bot._OPEN_GAME_LOOK_GAP = 0.0
        bot.running = True
        return bot

    def test_still_block_is_scenery(self):
        frame = np.full((100, 100, 3), 120, np.uint8)
        self.assertIsNone(self._bot([frame, frame.copy()])._game_running_at_start())

    def test_moving_fish_is_a_running_game(self):
        a = np.full((100, 100, 3), 120, np.uint8)
        b = a.copy()
        b[20:30, 20:30] = 30   # the fish moved
        self.assertEqual(self._bot([a, b])._game_running_at_start(), (10, 10, 50, 40))


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

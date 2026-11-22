"""The Fish Puzzle window is found by itself in every game window.

One grid defined by hand did not fit several clients: each player moves the
puzzle window to another place (diag 20261122_091635: two clients, the
puzzle 12 px apart, one of them paused at once with "puzzle window not
found"). The detector now finds the window by its frame (the grid image's
rim and the two crate slot frames, which pieces and crates never cover) and
takes the grid and both crate slots from the game's layout.

Real captures (tests/fixtures/jigsaw_grid):
  client1_empty_board / client2_full_board  the two clients of that session
  red_frame             the grid frame drawn red ("does not fit")
  held_piece_over_*     a held piece drawn over the frame / the title bar
  puzzle_empty          another server ("Puzzle de Pesca")
  ../real_client_rescaled/board_*  a third server, from a rescaled video

Also: the simulator with the real puzzle look, two windows with the puzzle
in different places and no grid defined; and pausing in the middle of a
round sends nothing more (the same session: after F5 one window went on
clicking)."""

import glob
import os
import sys
import time
import unittest

import cv2
import numpy as np

from _stubs import install, ROOT

install()

import jigsaw_bot as jigsaw_module  # noqa: E402
from jigsaw_bot import pause_target  # noqa: E402
from jigsaw_detector import JigsawDetector  # noqa: E402

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
from minigame_sim.jigsaw_game import JigsawGame, W  # noqa: E402

import test_jigsaw_sim as jsim  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
GRID_DIR = os.path.join(FIXTURES, "jigsaw_grid")

# Where the grid is in each capture (measured; the game draws it 192x128)
REAL = {
    "client1_empty_board.png": (19, 35),
    "client2_full_board.png": (31, 38),
    "red_frame.png": (31, 38),
    "held_piece_over_frame.png": (31, 39),
    "held_piece_over_title.png": (31, 39),
    "puzzle_empty.png": (16, 35),
}
RESCALED = {"board_0551.02.png": (16, 19), "board_0694.66.png": (18, 20)}


def image(path):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    assert img is not None, path
    return img


class LocatorOnRealCapturesTests(unittest.TestCase):
    def test_every_real_puzzle_window_is_found_to_the_pixel(self):
        for name, (gx, gy) in list(REAL.items()) + list(RESCALED.items()):
            folder = GRID_DIR if name in REAL else os.path.join(FIXTURES, "real_client_rescaled")
            with self.subTest(name):
                found = JigsawDetector({}).locate_puzzle(image(os.path.join(folder, name)))
                self.assertIsNotNone(found)
                self.assertEqual(found.grid, (gx, gy, 192, 128))

    def test_crate_slots_come_from_the_window_layout(self):
        found = JigsawDetector({}).locate_puzzle(image(os.path.join(GRID_DIR, "client1_empty_board.png")))
        # 37-38 px right of the grid, 21 px above / 30 px below its middle
        # (what was measured on two clients by hand)
        self.assertEqual(found.slots, {"special": (19 + 192 + 38, 35 + 64 - 21),
                                       "normal": (19 + 192 + 38, 35 + 64 + 30)})

    def test_two_clients_get_their_own_grid_and_slots(self):
        """The session that paused: the defined grid fitted one client only."""
        config = {"jigsaw_grid_bounds": [30, 41, 194, 125]}       # defined on the second client
        for name, (gx, gy), filled in (("client1_empty_board.png", (19, 35), 0),
                                       ("client2_full_board.png", (31, 38), 20)):
            with self.subTest(name):
                frame = image(os.path.join(GRID_DIR, name))
                detector = JigsawDetector(config)
                grid = detector.detect_grid(frame)
                self.assertTrue(grid.grid_found)
                self.assertEqual(grid.source, "auto")
                self.assertEqual(grid.grid_bounds, (gx, gy, 192, 128))
                self.assertEqual(len(grid.filled_cells), filled)
                slots = detector.detect_crate_slots(frame, grid)
                self.assertEqual((slots.available, slots.missing), ({"normal"}, {"special"}))

    def test_found_anywhere_in_a_big_window(self):
        crop = image(os.path.join(GRID_DIR, "client2_full_board.png"))
        background = cv2.resize(image(os.path.join(FIXTURES, "golden_tuna", "normal_hud_800x600.png")), (1920, 1080))
        for x, y in ((0, 0), (1590, 840), (700, 300)):
            with self.subTest((x, y)):
                frame = background.copy()
                frame[y:y + crop.shape[0], x:x + crop.shape[1]] = crop
                found = JigsawDetector({}).locate_puzzle(frame)
                self.assertEqual(found.grid, (x + 31, y + 38, 192, 128))

    def test_nothing_is_found_where_there_is_no_puzzle_window(self):
        others = [p for p in glob.glob(os.path.join(FIXTURES, "**", "*.png"), recursive=True)
                  if "jigsaw_grid" not in p and "real_client_rescaled" not in p]
        self.assertGreater(len(others), 20)
        for path in others:
            frame = cv2.imread(path, cv2.IMREAD_COLOR)
            if frame is None:
                continue
            with self.subTest(os.path.relpath(path, FIXTURES)):
                self.assertIsNone(JigsawDetector({}).locate_puzzle(frame))

    def test_covered_puzzle_is_not_guessed(self):
        frame = image(os.path.join(GRID_DIR, "client2_full_board.png"))
        frame[20:200, 0:330] = 30                       # a dialog over most of it
        self.assertIsNone(JigsawDetector({}).locate_puzzle(frame))

    def test_followed_where_it_was_found_and_searched_again_when_moved(self):
        crop = image(os.path.join(GRID_DIR, "client2_full_board.png"))
        background = image(os.path.join(FIXTURES, "golden_tuna", "normal_hud_800x600.png"))
        detector = JigsawDetector({})
        frame = background.copy()
        frame[0:240, 0:330] = crop
        self.assertEqual(detector.detect_grid(frame).grid_bounds, (31, 38, 192, 128))
        # the board_area crop reads the same at the known place
        area = detector.board_area(800, 600)
        x, y, w, h = area
        part = detector.detect_grid(frame[y:y + h, x:x + w].copy(), origin=(x, y), window_size=(800, 600))
        self.assertEqual(part.grid_bounds, (31, 38, 192, 128))
        # the player drags it elsewhere
        frame = background.copy()
        frame[300:540, 400:730] = crop
        x, y, w, h = detector.board_area(800, 600)
        self.assertFalse(detector.detect_grid(frame[y:y + h, x:x + w].copy(), origin=(x, y),
                                              window_size=(800, 600)).grid_found)
        self.assertEqual(detector.detect_grid(frame).grid_bounds, (431, 338, 192, 128))

    def test_defined_grid_is_the_fallback_when_the_window_is_not_found(self):
        game = JigsawGame()          # the plain simulator board: no puzzle window look
        from minigame_sim.jigsaw_game import GRID
        grid = JigsawDetector({"jigsaw_grid_bounds": list(GRID)}).detect_grid(game.render())
        self.assertTrue(grid.grid_found)
        self.assertEqual(grid.source, "manual")
        self.assertEqual(grid.grid_bounds, GRID)

    def test_switched_off_uses_the_defined_grid_only(self):
        frame = image(os.path.join(GRID_DIR, "client1_empty_board.png"))
        grid = JigsawDetector({"jigsaw_grid_auto": False}).detect_grid(frame)
        self.assertFalse(grid.grid_found)


class PauseRuleTests(unittest.TestCase):
    def test_pause_while_anything_runs_resume_only_when_all_paused(self):
        self.assertTrue(pause_target([True, False]))     # one paused itself: F5 pauses the other
        self.assertTrue(pause_target([False, False]))
        self.assertFalse(pause_target([True, True]))
        self.assertTrue(pause_target([]))


class RealPuzzleSimTests(unittest.TestCase):
    """The simulator draws the real puzzle window; no grid is defined."""

    CONFIG = {"jigsaw_grid_bounds": None}

    def test_two_windows_with_the_puzzle_in_different_places(self):
        games = [JigsawGame(left=0, seed=31, puzzle_at=(4, 2)),
                 JigsawGame(left=W, seed=32, puzzle_at=(240, 60))]
        rig = jsim.Rig(games, self.CONFIG)
        self.assertTrue(rig.run(lambda: all(g.opened >= 8 for g in games), timeout=90), rig.logs[-12:])
        self.assertEqual(rig.desktop.misdirected, 0)
        for game, bot in zip(games, rig.bots):
            self.assertEqual(game.errors, [], rig.logs[-12:])
            self.assertGreaterEqual(game.placed, 3)
            self.assertEqual(bot.detector.puzzle.grid, game.grid)
            self.assertEqual(bot.detector.puzzle.slots, game.slot_pos)
        self.assertEqual(rig.pauses, [])

    def test_puzzle_window_moved_while_running(self):
        game = JigsawGame(seed=33, puzzle_at=(10, 10))
        rig = jsim.Rig([game], self.CONFIG)

        def moved():
            if game.opened >= 4 and game.puzzle_at == (10, 10) and game.held is None and game.dialog is None:
                # the player drags the window (the board goes with it)
                game.puzzle_at = (200, 40)
                game.grid = (218, 74, 192, 128)
                game.slot_pos = {"special": (448, 117), "normal": (448, 168)}
            return game.puzzle_at != (10, 10) and game.opened >= 10

        self.assertTrue(rig.run(moved, timeout=90), rig.logs[-12:])
        self.assertEqual(game.errors, [], rig.logs[-12:])
        self.assertEqual(rig.bots[0].detector.puzzle.grid, (218, 74, 192, 128))

    def test_no_puzzle_window_pauses_without_a_click(self):
        game = JigsawGame(seed=34)                  # plain board, no grid defined either
        clicks = []
        original = game.click
        game.click = lambda x, y, b: (clicks.append((x, y, b)), original(x, y, b))
        rig = jsim.Rig([game], self.CONFIG)
        rig.run(lambda: (0, True) in rig.pauses, timeout=30)
        self.assertIn((0, True), rig.pauses, rig.logs[-6:])
        self.assertEqual(clicks, [])
        self.assertTrue(any("puzzle window not found in this game window" in m for m in rig.logs))


class PauseMidRoundTests(unittest.TestCase):
    def test_pause_in_the_middle_of_a_round_sends_nothing_more(self):
        games = [JigsawGame(left=i * W, seed=80 + i, dialog_delay=(0.0, 0.1), puzzle_at=(20, 20))
                 for i in range(2)]
        rig = jsim.Rig(games, {"jigsaw_grid_bounds": None})
        clicks = []
        mouse = rig.bots[0].mouse_backend
        click, move = mouse._click, mouse.move_to
        mouse._click = lambda button: (clicks.append(time.perf_counter()), click(button))
        moves = []
        mouse.move_to = lambda x, y: (moves.append(time.perf_counter()), move(x, y))
        paused_at = []

        def pause_mid_round():
            if not paused_at and games[0].opened >= 3 and games[0].held is not None:
                paused_at.append(time.perf_counter())
                jigsaw_module._last_f5 = float("-inf")
                for bot in rig.bots:
                    bot._on_f5_pressed(True)
            return bool(paused_at) and time.perf_counter() - paused_at[0] > 1.5

        rig.run(pause_mid_round, timeout=60)
        self.assertTrue(paused_at, rig.logs[-12:])
        # A click already on its way out may land; nothing after that
        self.assertEqual([round(t - paused_at[0], 3) for t in clicks if t > paused_at[0] + 0.1], [])
        self.assertEqual([round(t - paused_at[0], 3) for t in moves if t > paused_at[0] + 0.1], [])

    def test_after_the_resume_the_piece_in_hand_is_played_first(self):
        game = JigsawGame(seed=85, dialog_delay=(0.0, 0.05), puzzle_at=(20, 20))
        rig = jsim.Rig([game], {"jigsaw_grid_bounds": None})
        state = {"paused": None}

        def scenario():
            bot = rig.bots[0]
            if state["paused"] is None and game.opened >= 2 and game.held is not None:
                state["paused"] = time.perf_counter()
                bot._on_f5_pressed(True)
            elif state["paused"] and time.perf_counter() - state["paused"] > 0.6 and bot.paused:
                bot._on_f5_pressed(False)
            return state["paused"] is not None and not bot.paused and game.opened >= 8

        self.assertTrue(rig.run(scenario, timeout=60), rig.logs[-12:])
        # never a crate opened over the piece that was in hand when paused
        self.assertEqual(game.errors, [], rig.logs[-12:])


if __name__ == "__main__":
    unittest.main()

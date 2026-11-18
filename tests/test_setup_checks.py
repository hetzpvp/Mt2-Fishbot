"""Checks made while the bot is set up (setup_checks) and the in-run checks
that stop the bot with what to fix (repeated failed drops, catches that are
never recognized)."""

import os
import sys
import unittest

import cv2
import numpy as np

from _stubs import install, InventoryScreen, ROOT

install()

import setup_checks  # noqa: E402
from test_fishing_bot import make_inventory_bot  # noqa: E402

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
from minigame_sim.jigsaw_game import JigsawGame, GRID  # noqa: E402


class WindowSizeTests(unittest.TestCase):

    def test_positions_set_at_another_size_are_named(self):
        cfg = {"inv_page_1_pos": (10, 10), "confirm_button_pos": (5, 5), "armor_slot_pos": (1, 1)}
        setup_checks.remember_size(cfg, "inv_page_1_pos", (1024, 768))
        setup_checks.remember_size(cfg, "confirm_button_pos", (800, 600))
        stale = setup_checks.stale_positions(cfg, setup_checks.FISHING_POSITIONS, (1024, 768))
        self.assertEqual(stale, [("confirm_button_pos", (800, 600))])      # armor: size unknown, not judged
        text = setup_checks.stale_positions_message(stale, (1024, 768), "Client")
        self.assertIn("Confirm: set at 800×600", text)
        self.assertIn("1024×768", text)

    def test_matching_or_cleared_positions_pass(self):
        cfg = {"inv_page_1_pos": (10, 10)}
        setup_checks.remember_size(cfg, "inv_page_1_pos", (800, 600))
        self.assertEqual(setup_checks.stale_positions(cfg, setup_checks.FISHING_POSITIONS, (800, 600)), [])
        cfg["inv_page_1_pos"] = None                                        # cleared position
        self.assertEqual(setup_checks.stale_positions(cfg, setup_checks.FISHING_POSITIONS, (640, 480)), [])
        setup_checks.remember_size(cfg, "inv_page_1_pos", None)
        self.assertEqual(cfg[setup_checks.SIZES_KEY], {})
        self.assertIsNone(setup_checks.stale_positions_message([], (1, 1)))

    def test_jigsaw_checks_its_own_positions_only(self):
        cfg = {"armor_slot_pos": (1, 1), "jigsaw_grid_bounds": [1, 2, 3, 4]}
        setup_checks.remember_size(cfg, "armor_slot_pos", (640, 480))
        setup_checks.remember_size(cfg, "jigsaw_grid_bounds", (640, 480))
        stale = setup_checks.stale_positions(cfg, setup_checks.JIGSAW_POSITIONS, (800, 600))
        self.assertEqual([k for k, _ in stale], ["jigsaw_grid_bounds"])


class TabCheckTests(unittest.TestCase):

    def test_the_clicked_tab_reads_as_open(self):
        screen = InventoryScreen()
        screen.page = 2
        state, active, _ = setup_checks.tab_after_click(screen.config(), 3, screen.grab, screen.W, screen.H)
        self.assertEqual((state, active), ("ok", 3))

    def test_another_open_tab_is_reported(self):
        screen = InventoryScreen()
        screen.page = 0
        state, active, _ = setup_checks.tab_after_click(screen.config(), 3, screen.grab, screen.W, screen.H)
        self.assertEqual((state, active), ("other", 1))

    def test_unlit_tabs_are_unreadable_and_too_few_tabs_say_nothing(self):
        screen = InventoryScreen(highlight=False)
        self.assertEqual(setup_checks.tab_after_click(screen.config(), 1, screen.grab, screen.W, screen.H)[0],
                         "unreadable")
        two = {k: v for k, v in screen.config().items() if k in ("inv_page_1_pos", "inv_page_2_pos")}
        self.assertEqual(setup_checks.tab_after_click(two, 1, screen.grab, screen.W, screen.H)[0], "unknown")
        no_picture = lambda *_: None
        self.assertEqual(setup_checks.tab_after_click(screen.config(), 1, no_picture, screen.W, screen.H)[0],
                         "unknown")

    def test_same_tab_twice(self):
        cfg = InventoryScreen().config()
        self.assertIsNone(setup_checks.duplicate_tab(cfg, 2))
        cfg["inv_page_3_pos"] = [cfg["inv_page_1_pos"][0] + 3, cfg["inv_page_1_pos"][1] - 2]
        self.assertEqual(setup_checks.duplicate_tab(cfg, 3), 1)

    def test_inventory_below_the_tabs(self):
        screen = InventoryScreen(highlight=False)
        cfg = screen.config()
        self.assertGreater(setup_checks.count_empty_slots(screen.grab(0, 0, screen.W, screen.H)), 40)
        self.assertIsNone(setup_checks.strip_problem(cfg, screen.grab, screen.W, screen.H, "unreadable"))
        # Tabs set somewhere in the game world: no slot below them
        moved = {k: [v[0] - 500, v[1] - 250] for k, v in cfg.items()}
        self.assertIn("no empty inventory slot",
                      setup_checks.strip_problem(moved, screen.grab, screen.W, screen.H, "unreadable"))
        # A tab that reads as open means the tabs are right: nothing judged
        self.assertIsNone(setup_checks.strip_problem(moved, screen.grab, screen.W, screen.H, "ok"))
        self.assertIsNone(setup_checks.strip_problem(cfg, lambda *_: None, screen.W, screen.H, "unknown"))


class ArmorCheckTests(unittest.TestCase):

    def test_worn_armor_in_the_equipment_window_is_caught(self):
        screen = InventoryScreen()
        cfg = screen.config()
        self.assertIsNone(setup_checks.armor_problem(cfg, screen.slot_center(12), screen.W, screen.H))
        x, y = screen.EQUIPMENT[0]
        self.assertIn("wear", setup_checks.armor_problem(cfg, (x + 16, y + 16), screen.W, screen.H))
        self.assertIsNone(setup_checks.armor_problem({}, (x + 16, y + 16), screen.W, screen.H))  # no tabs


class GridCheckTests(unittest.TestCase):

    def test_grid_where_the_puzzle_is_passes_elsewhere_not(self):
        frame = JigsawGame().render()
        self.assertIsNone(setup_checks.grid_problem({}, list(GRID), frame))
        shifted = [GRID[0] + 60, GRID[1] + 50, GRID[2], GRID[3]]
        self.assertIn("does not line up", setup_checks.grid_problem({}, shifted, frame))
        self.assertIsNone(setup_checks.grid_problem({}, list(GRID), None))
        self.assertIsNone(setup_checks.grid_problem({"jigsaw_grid_check": False}, shifted, frame))


class InRunCheckTests(unittest.TestCase):

    def test_three_failed_drops_in_a_row_stop_the_bot(self):
        bot, _mouse, _logs = make_inventory_bot(InventoryScreen())
        bot._drop_failed("x")
        bot._drop_failed("x")
        bot._drop_failures = 0                                  # a drop that worked in between
        bot._drop_failed("x")
        bot._drop_failed("x")
        self.assertTrue(bot.running)
        bot._drop_failed("check Confirm.")
        self.assertFalse(bot.running)
        self.assertEqual(bot.stop_reason, bot.STOP_DROP_FAILING)
        self.assertIn("check Confirm", bot.stop_message)

    def test_catches_never_recognized_stop_the_bot(self):
        bot, _mouse, _logs = make_inventory_bot(InventoryScreen())
        for _ in range(bot._UNRECOGNIZED_LIMIT - 1):
            bot._note_unrecognized_catch(True)
        bot._note_unrecognized_catch(False)                     # nothing landed: not counted
        self.assertTrue(bot.running)
        bot._note_unrecognized_catch(True)
        self.assertEqual(bot.stop_reason, bot.STOP_CATCH_NOT_RECOGNIZED)

    def test_one_recognized_catch_means_the_pictures_work(self):
        bot, _mouse, _logs = make_inventory_bot(InventoryScreen())
        bot._catches_recognized = 1
        for _ in range(bot._UNRECOGNIZED_LIMIT * 2):
            bot._note_unrecognized_catch(True)
        self.assertTrue(bot.running)

    def test_unrecognized_catch_is_counted_from_handle_caught_item(self):
        screen = InventoryScreen()
        bot, _mouse, _logs = make_inventory_bot(screen, {"auto_fish_handling": True,
                                                         "fish_actions": {"Carp_living.jpg": "keep"}})
        bot._t_catch_wait = 0.05
        bot._CATCH_EXTRA_WAIT = 0.05
        bot._page_has_space(None, [])
        before = len(bot._empty_slot_positions)
        self.assertGreater(before, 0)
        unknown = np.full((32, 32, 3), (20, 140, 200), np.uint8)   # an item the bot has no picture of
        cv2.circle(unknown, (16, 16), 9, (200, 40, 40), -1)
        screen.pages[0][0] = unknown
        bot.handle_caught_item()
        self.assertEqual(bot._unrecognized_in_a_row, 1)
        self.assertEqual(len(bot._empty_slot_positions), before - 1)


if __name__ == "__main__":
    unittest.main()

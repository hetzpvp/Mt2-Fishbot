"""Report of 2026-11-20 13:00 (diagnostics 20261120_125948), on the real
client:

- Shrimp and Vai are bait: a right-click puts one on the rod. The bot
  right-clicked them as the "open first" step of a drop, so a Shrimp went on
  the rod by accident, and the bait key then replaced it. They now have a
  "use" action: queued in the order found, each cast puts the next one on
  the rod instead of pressing the bait key.
- The game answered a right-click later than the bot looked (200 ms): the
  Shrimp vanished after the bot had read "still there", and the bot went
  through a whole drop of an empty slot (ground click, Confirm click: the
  character only walked).
- A Confirm click the game missed left the drop dialog open.

The simulator now answers right-clicks late (``answer_delay``), knows bait
fish, can lose Confirm clicks and counts clicks into the world with nothing
on the cursor (``walks``). Nothing here touches the desktop."""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from minigame_sim import bench  # noqa: E402
from utils import is_bait_fish, normalize_fish_action  # noqa: E402

try:
    import qt_gui  # noqa: E402
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False


def game_and_config(actions, items=(), **kw):
    """A quick game whose page 1 starts with ``items`` (slot 0, 1, ...);
    every item is kept unless ``actions`` says otherwise."""
    game = bench.quick_game("official", **kw)
    for slot, name in enumerate(items):
        game.pages[0][slot] = name
    cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
    fish_actions = {n: "keep" for n in game._item_names}
    fish_actions.update(actions)
    cfg.update({"auto_fish_handling": True, "fish_actions": fish_actions,
                "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)})
    return game, cfg


class ActionTests(unittest.TestCase):
    def test_use_is_only_for_bait_fish(self):
        self.assertTrue(is_bait_fish("Shrimp_living.jpg") and is_bait_fish("Vai_living.jpg"))
        self.assertEqual(normalize_fish_action("use", "Shrimp_living.jpg"), "use")
        self.assertEqual(normalize_fish_action("use", "Carp_living.jpg"), "keep")
        # A right-click on a bait fish puts it on the rod: an old "open" means use
        self.assertEqual(normalize_fish_action("open", "Vai_living.jpg"), "use")
        self.assertEqual(normalize_fish_action("open", "Carp_living.jpg"), "open")
        self.assertEqual(normalize_fish_action("drop", "Vai_living.jpg"), "drop")


class BaitFishTests(unittest.TestCase):
    def test_bait_fish_are_used_one_per_cast(self):
        game, cfg = game_and_config(
            {"Vai_living.jpg": "use", "Shrimp_living.jpg": "use"},
            items=["Vai_living.jpg", "Vai_living.jpg", "Shrimp_living.jpg"], catch_chance=0.0)
        bot, logs = bench.run(game, cfg, timeout=120, until=lambda: game.stats["games"] >= 5)
        self.assertEqual(game.stats["fish_bait_used"], 3, logs[-15:])
        self.assertEqual(game.stats["casts_with_fish_bait"], 3, logs[-15:])
        self.assertEqual(game.stats["fish_bait_replaced"], 0, "bait key pressed over a bait fish")
        self.assertFalse(game.pages[0], "bait fish left in the inventory")
        # Only the casts with the usual bait count against the bait stack
        self.assertEqual(10 ** 6 - bot.bait_counter, bot.total_games - 3)

    def test_stacked_bait_fish_are_used_until_the_stack_is_empty(self):
        # Report of 2026-11-21: 14 Vai in one slot, the bot used one and
        # left the rest (the slot still held an item, so it read "did not
        # go on the rod"). Every cast now takes one until the slot is empty,
        # then the bait key is back.
        game, cfg = game_and_config({"Vai_living.jpg": "use"}, items=["Vai_living.jpg"], catch_chance=0.0)
        game.stacks[(0, 0)] = 5
        bot, logs = bench.run(game, cfg, timeout=150, until=lambda: game.stats["games"] >= 7)
        self.assertEqual(game.stats["fish_bait_used"], 5, logs[-15:])
        self.assertEqual(game.stats["casts_with_fish_bait"], 5, logs[-15:])
        self.assertEqual(game.stats["fish_bait_replaced"], 0, "bait key pressed over a bait fish")
        self.assertFalse(game.pages[0], "Vai left in the inventory")
        self.assertEqual(10 ** 6 - bot.bait_counter, bot.total_games - 5)

    def test_caught_bait_fish_is_used_at_a_later_cast(self):
        game, cfg = game_and_config({"Shrimp_living.jpg": "use"})
        game._item_names = ["Shrimp_living.jpg"]          # every catch is a Shrimp
        bot, logs = bench.run(game, cfg, games=4, timeout=120)
        self.assertGreaterEqual(game.stats["casts_with_fish_bait"], 2, logs[-15:])
        self.assertEqual(game.stats["fish_bait_replaced"], 0)

    def test_bait_fish_set_to_drop_is_not_right_clicked(self):
        game, cfg = game_and_config({"Shrimp_living.jpg": "drop"}, items=["Shrimp_living.jpg"],
                                    catch_chance=0.0)
        bot, logs = bench.run(game, cfg, timeout=60, until=lambda: game.stats["games"] >= 1)
        self.assertEqual(game.stats["fish_bait_used"], 0, "the drop put it on the rod")
        self.assertEqual(game.stats["dropped"], 1, logs[-15:])


class StackCountTests(unittest.TestCase):
    """Report of 2026-11-21 11:44 (diagnostics 20261121_114409): 12 Vai in a
    slot, about 6 used. The game did take each one (11 -> 10 -> 9 -> 8 on the
    recording), but in its small pixel font 9 -> 8 changes one pixel and
    8 -> 7 five: below the 6-pixel rule, so the bot read "did not go on the
    rod" and gave up on the stack. Some pictures were also taken while the
    bot's own Ctrl+G was down, when the game draws every count bigger."""

    def setUp(self):
        import numpy as np
        from fishing_bot import FishingBot
        self.np, self.Bot = np, FishingBot
        rng = np.random.default_rng(1)
        self.before = rng.integers(20, 120, (30, 30), dtype=np.uint8)

    def test_one_pixel_of_the_count_is_a_change(self):
        after = self.before.copy()
        after[25, 24] = 235                      # the count corner, bottom right
        self.assertTrue(self.Bot._patch_change(self.before, after)[2])

    def test_unchanged_slot_is_not_a_change(self):
        self.assertFalse(self.Bot._patch_change(self.before, self.before.copy())[2])

    def test_one_pixel_away_from_the_count_is_not_a_change(self):
        after = self.before.copy()
        after[3, 3] = 235                        # top left: the icon, not the count
        self.assertFalse(self.Bot._patch_change(self.before, after)[2])

    def test_no_inventory_picture_while_ctrl_is_down(self):
        import threading
        import time
        bot = self.Bot.__new__(self.Bot)
        bot.running, bot.diag_tag = True, "T"
        bot._ctrl_down, bot._ctrl_up_at = True, 0.0

        def release():
            time.sleep(0.2)
            bot._ctrl_up_at = time.perf_counter()
            bot._ctrl_down = False
        threading.Thread(target=release, daemon=True).start()
        t0 = time.perf_counter()
        bot._wait_ctrl_redrawn()
        waited = time.perf_counter() - t0
        # Held 0.2 s, then the counts need _CTRL_REDRAW to be drawn small
        self.assertGreaterEqual(waited, 0.2 + self.Bot._CTRL_REDRAW - 0.02)


class LateAnswerTests(unittest.TestCase):
    def test_late_open_is_waited_for_not_dropped(self):
        # The open is answered 0.5 s after the right-click: read at 0.2 s,
        # the fish looked unopened and a drop of it began.
        game, cfg = game_and_config({"Carp_living.jpg": "drop"}, items=["Carp_living.jpg"],
                                    catch_chance=0.0, answer_delay=0.5)
        bot, logs = bench.run(game, cfg, timeout=60, until=lambda: game.stats["games"] >= 1)
        self.assertEqual(game.stats["opened"], 1)
        self.assertEqual(game.stats["walks"], 0, "ground or Confirm click with nothing on the cursor")
        self.assertEqual(game.stats["drop_cancelled"], 0)
        self.assertIsNone(game.dialog)

    def test_item_gone_after_the_pickup_is_not_dropped(self):
        # Answered after the bot stopped waiting: the item vanishes once the
        # drop has begun. Nothing may be clicked into the world then.
        game, cfg = game_and_config({"Carp_living.jpg": "drop"}, items=["Carp_living.jpg"],
                                    catch_chance=0.0, answer_delay=1.25)
        bot, logs = bench.run(game, cfg, timeout=60, until=lambda: game.stats["games"] >= 1)
        self.assertEqual(game.stats["opened"], 1)
        self.assertEqual(game.stats["walks"], 0, "ground or Confirm click with nothing on the cursor")
        self.assertIsNone(game.carrying, "item left on the cursor")
        self.assertIsNone(game.dialog, "dialog left open")


class LostConfirmTests(unittest.TestCase):
    def test_missed_confirm_is_clicked_again(self):
        game, cfg = game_and_config({}, catch_chance=0.0, lost_confirm_clicks=1)
        name = next(n for n in game._item_names if n.endswith("_item.jpg"))
        cfg["fish_actions"][name] = "drop"
        game.pages[0][0] = name
        bot, logs = bench.run(game, cfg, timeout=60, until=lambda: game.stats["games"] >= 1)
        self.assertEqual(game.stats["lost_confirm_clicks"], 1)
        self.assertEqual(game.stats["dropped"], 1, logs[-15:])
        self.assertIsNone(game.dialog, "dialog left open")
        self.assertIsNone(game.carrying)


@unittest.skipUnless(HAS_QT, "PySide6 not available")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_bait_fish_offer_use_instead_of_open(self):
        modal = qt_gui.FishModal({"Shrimp_living.jpg": "open", "Vai_living.jpg": "drop"})
        self.assertEqual(modal._actions["Shrimp_living.jpg"], "use")
        self.assertEqual(modal._actions["Vai_living.jpg"], "drop")
        self.assertEqual(list(modal._tiles["Vai_living.jpg"].seg._buttons), ["keep", "drop", "use"])
        self.assertEqual(list(modal._tiles["Carp_living.jpg"].seg._buttons), ["keep", "drop", "open"])
        modal._open_all()
        self.assertEqual(modal._actions["Vai_living.jpg"], "drop")     # not opened
        self.assertEqual(modal._actions["Carp_living.jpg"], "open")
        saved = []
        modal.saved.connect(saved.append)
        modal._save()
        self.assertEqual(saved[0]["Shrimp_living.jpg"], "use")


if __name__ == "__main__":
    unittest.main()

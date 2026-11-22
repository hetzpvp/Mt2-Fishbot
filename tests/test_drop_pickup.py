"""Drops on the real client (2.0.3 diagnostics, 2026-11-19): every drop
stopped with "could not be picked up". The game keeps drawing a picked-up
item in its slot (only a copy follows the cursor), and the bot took the
still-drawn icon as a failed pickup, so it never clicked the ground and no
drop dialog ever showed. The simulator now behaves like the game; the bot
looks for the copy at the cursor over an empty slot instead. Nothing here
touches the desktop."""

import os
import sys
import unittest

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from minigame_sim import bench  # noqa: E402


def game_and_config(**kw):
    game = bench.quick_game("official", dead_fish_chance=1.0, **kw)
    cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
    cfg.update({"auto_fish_handling": True, "fish_actions": {n: "drop" for n in game._item_names},
                "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)})
    return game, cfg


def run(game, cfg, catches=4):
    # (the first drop waits for a timed bite, a drop without time before
    # the bite for the next quick skip: one catch more than drops wanted)
    return bench.run(game, cfg, games=catches, timeout=120)


class PickupTests(unittest.TestCase):

    def test_bites_faster_than_a_drop_still_drop(self):
        # Every bite comes before a drop can finish: the item is put back,
        # put aside, and dropped after the next quick skip, before the cast.
        game, cfg = game_and_config(bite_delay=(0.3, 0.5))
        bot, logs = run(game, cfg, catches=4)
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-10:])
        self.assertIsNone(game.carrying, "item left on the cursor")

    def assert_drops_work(self, game, logs):
        self.assertFalse(any("could not be picked up" in m for m in logs), logs[-10:])
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-10:])
        self.assertEqual(game.stats["drop_cancelled"], 0, logs[-10:])
        self.assertIsNone(game.carrying, "item left on the cursor")

    def test_item_still_drawn_in_its_slot_is_dropped(self):
        game, cfg = game_and_config()
        self.assertFalse(game.pickup_hides_slot)        # the game's way is the default
        bot, logs = run(game, cfg)
        self.assert_drops_work(game, logs)
        self.assertTrue(bot._held_probe_proven)

    def test_copy_drawn_beside_the_cursor_is_seen(self):
        # The cursor tip at the icon's left edge (as the jigsaw pieces are drawn)
        game, cfg = game_and_config(held_icon_anchor="left")
        bot, logs = run(game, cfg)
        self.assert_drops_work(game, logs)
        self.assertTrue(bot._held_probe_proven)

    def test_client_that_empties_the_slot(self):
        game, cfg = game_and_config(pickup_hides_slot=True)
        bot, logs = run(game, cfg)
        self.assert_drops_work(game, logs)

    def test_copy_never_seen_still_drops_through_the_dialog(self):
        # A client that draws the copy nowhere near the probe: the probe is
        # never proven, so its "nothing held" does not stop the drop.
        game, cfg = game_and_config(held_icon_anchor="nowhere")
        bot, logs = run(game, cfg)
        self.assert_drops_work(game, logs)
        self.assertFalse(bot._held_probe_proven)

    def _lose_pickups(self, game, which):
        """Swallows the n-th left clicks (counted from 1) on an item while
        nothing is held."""
        state = {"n": 0, "lost": 0}
        original = game.click

        def click(x, y, button="left"):
            slot = game._slot_at(int(x), int(y))
            if (button == "left" and game.carrying is None and game.dialog is None and slot is not None
                    and slot in game.pages[game.page]):
                state["n"] += 1
                if state["n"] in which:
                    state["lost"] += 1
                    return None
            return original(x, y, button)

        game.click = click
        return state

    def test_lost_pickup_click_after_a_drop_worked(self):
        game, cfg = game_and_config()
        state = self._lose_pickups(game, {2})
        bot, logs = run(game, cfg, catches=4)
        self.assertEqual(state["lost"], 1)
        self.assertTrue(any("could not be picked up" in m for m in logs), logs[-10:])
        self.assertIsNone(game.carrying, "item left on the cursor")
        self.assertEqual(game.stats["drop_cancelled"], 0)
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-10:])

    def test_lost_first_pickup_click_leaves_nothing_on_the_cursor(self):
        # The probe is not proven yet: the ground click goes out, no dialog
        # comes, and the put-back click (which picks the item up) is undone.
        game, cfg = game_and_config()
        state = self._lose_pickups(game, {1})
        bot, logs = run(game, cfg, catches=4)
        self.assertEqual(state["lost"], 1)
        self.assertIsNone(game.carrying, "item left on the cursor")
        self.assertTrue(any("item put back" in m for m in logs), logs[-10:])
        self.assertGreaterEqual(game.stats["dropped"], 1, logs[-10:])

    def test_item_without_drop_sell_step_is_dropped(self):
        # Dyes on the real client (diag 20261121_200639): no Drop / Sell
        # popup, the game asks yes / no at once. The bot gave up after the
        # quiet Drop / Sell point and left the dialog open, so the casts
        # that followed did nothing.
        game, cfg = game_and_config(direct_yes_no=True)
        bot, logs = run(game, cfg, catches=4)
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-10:])
        self.assertEqual(game.stats["drop_cancelled"], 0, logs[-10:])
        self.assertIsNone(game.dialog, "dialog left open")
        self.assertIsNone(game.carrying, "item left on the cursor")

    def test_the_same_item_can_switch_dialog(self):
        # One item, and the client does not stick to one dialog: yes / no,
        # then Drop / Sell, then yes / no again. Remembering the last one
        # must not make the next drop click the wrong button.
        game, cfg = game_and_config(dialog_steps=("confirm", "drop", "confirm", "drop"),
                                    catch_names=("Bleach_item.jpg",))
        bot, logs = run(game, cfg, catches=5)
        self.assertGreaterEqual(game.stats["dropped"], 3, logs[-12:])
        self.assertEqual(game.stats["drop_cancelled"], 0, logs[-12:])
        self.assertEqual(bot._drop_failures, 0)
        self.assertIsNone(game.dialog, "dialog left open")
        self.assertIsNone(game.carrying, "item left on the cursor")

    def test_a_dye_and_a_ring_in_one_session_both_drop(self):
        game, cfg = game_and_config(
            direct_yes_no={"Bleach_item.jpg"},
            catch_names=("Carp_living.jpg", "Bleach_item.jpg"))
        bot, logs = run(game, cfg, catches=4)
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-12:])
        self.assertEqual(game.stats["drop_cancelled"], 0, logs[-12:])
        self.assertEqual(bot._drop_failures, 0)
        self.assertIsNone(game.dialog, "dialog left open")

    def test_drop_that_ends_at_the_drop_button_is_not_a_failure(self):
        # The client has no yes / no after Drop. The slot is empty and no
        # dialog is left, so the drop stands.
        game, cfg = game_and_config(drop_completes=True)
        bot, logs = run(game, cfg, catches=4)
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-12:])
        self.assertEqual(game.stats["drop_cancelled"], 0, logs[-12:])
        self.assertEqual(bot._drop_failures, 0)
        self.assertIsNone(game.dialog, "dialog left open")
        self.assertIsNone(game.carrying, "item left on the cursor")

    def test_confirm_area_that_does_not_return_to_baseline_still_drops(self):
        # After YES the world behind the button stays 17 levels off, including
        # the first drop, before any dialog look is known. That used to be
        # "dialog still open" and three of them stopped the bot.
        game, cfg = game_and_config(shift_confirm_world=True)
        bot, logs = run(game, cfg, catches=4)
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-12:])
        self.assertEqual(game.stats["drop_cancelled"], 0, logs[-12:])
        self.assertEqual(bot._drop_failures, 0, logs[-12:])
        self.assertIsNone(game.dialog, "dialog left open")


if __name__ == "__main__":
    unittest.main()

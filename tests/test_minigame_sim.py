"""The real FishingBot loop — cast, wait for the bite, play the minigame,
handle the catch — against the simulated fishing window of tools/minigame_sim.

Unlike the frame-level tests, the simulation enforces the game's rules: only
a click on the fish while it is inside the circle is a hit, a miss costs
time, and three hits put an item into the inventory."""

import os
import sys
import unittest

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from minigame_sim import bench  # noqa: E402
from minigame_sim.fishing_game import FishingGame, SKINS, HARD_SKINS  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class RulesTests(unittest.TestCase):
    """The game itself, on a clock the test controls."""

    def _game(self, **kw):
        clock = FakeClock()
        game = FishingGame(clock=clock, bite_delay=(1.0, 1.0), recast_cooldown=1.0, **kw)
        return game, clock

    def _open(self, game, clock):
        game.key("1")
        game.key("space")
        clock.t += 1.01
        game.update()
        self.assertTrue(game.panel_open)

    def _put_fish(self, game, inside):
        cx, cy, r = game.circle_abs()
        game.fish = (cx, cy) if inside else (cx + r + 20, cy)
        game._pause_until = float("inf")
        return int(game.fish[0]), int(game.fish[1])

    def test_cast_needs_bait_and_opens_after_the_bite_delay(self):
        game, clock = self._game()
        game.key("space")
        self.assertEqual(game.state, "idle")
        self.assertEqual(game.stats["casts_without_bait"], 1)
        game.key("1")
        game.key("space")
        self.assertEqual(game.state, "waiting")
        clock.t += 0.9
        game.update()
        self.assertFalse(game.panel_open)
        clock.t += 0.2
        game.update()
        self.assertTrue(game.panel_open)

    def test_three_hits_catch_an_item(self):
        game, clock = self._game()
        self._open(game, clock)
        for _ in range(3):
            game.click(*self._put_fish(game, inside=True))
            clock.t += 0.5
        self.assertEqual(game.stats["hits"], 3)
        self.assertEqual(game.stats["caught"], 1)
        self.assertEqual(sum(len(p) for p in game.pages), 1)
        game.update()
        self.assertFalse(game.panel_open)
        # The rod animation: an early cast is ignored
        game.key("1")
        game.key("space")
        self.assertEqual(game.stats["casts_ignored"], 1)
        clock.t += 1.0
        game.key("space")
        self.assertEqual(game.state, "waiting")

    def test_clicks_inside_the_cooldown_do_nothing(self):
        game, clock = self._game(click_cooldown=0.5)
        self._open(game, clock)
        x, y = self._put_fish(game, inside=True)
        game.click(x, y)
        clock.t += 0.2
        game.click(x, y)                                  # too early: neither hit nor miss
        game.click(x + 40, y)
        self.assertEqual((game.stats["hits"], game.stats["misses"], game.stats["clicks_ignored"]), (1, 0, 2))
        left = game.time_left
        clock.t += 0.31
        game.click(x, y)
        self.assertEqual(game.stats["hits"], 2)
        self.assertAlmostEqual(game.time_left, left - 0.1, places=3)   # only the clock, no penalty

    def test_misses_cost_time(self):
        game, clock = self._game(miss_penalty=2.0)
        self._open(game, clock)
        x, y = self._put_fish(game, inside=False)
        game.click(x, y)                                  # on the fish, outside the circle
        clock.t += 0.5
        game.update()
        x, y = self._put_fish(game, inside=True)
        game.click(x + 40, y)                             # fish inside, click beside it
        self.assertEqual(game.stats["misses"], 2)
        self.assertEqual(game.stats["hits"], 0)
        self.assertAlmostEqual(game.time_left, game.duration - 4.1, places=3)          # 2 misses + 0.1 s of clock
        for _ in range(200):                              # update() advances 0.1 s at most
            clock.t += 0.1
            game.update()
        self.assertEqual(game.stats["timeouts"], 1)
        self.assertEqual(game.stats["caught"], 0)

    def test_inventory_open_and_drop(self):
        game, _clock = self._game()
        game.pages[0][0] = "Carp_living.jpg"
        game.pages[0][1] = "Gold_item.jpg"
        game.click(*game.slot_center(0), button="right")
        self.assertNotIn(0, game.pages[0])
        game.click(*game.slot_center(1), button="right")  # not a fish: nothing happens
        self.assertIn(1, game.pages[0])
        game.click(*game.slot_center(1))
        game.click(game.W // 2, game.H // 2)
        game.click(*game.drop_button)
        game.click(*game.confirm_button)
        self.assertEqual(game.stats["dropped"], 1)
        self.assertEqual(game.pages[0], {})
        # A wrong click on the dialog puts the item back
        game.pages[0][2] = "Gold_item.jpg"
        game.click(*game.slot_center(2))
        game.click(game.W // 2, game.H // 2)
        game.click(5, 5)
        self.assertEqual(game.stats["drop_cancelled"], 1)
        self.assertIn(2, game.pages[0])

    def test_every_skin_renders(self):
        for name in SKINS:
            game, clock = self._game(skin=name)
            self._open(game, clock)
            frame = game.render()
            self.assertEqual(frame.shape, (600, 800, 3), name)
            x, y, w, h = game.panel
            self.assertEqual(game.grab(x, y, w, h).shape, (h, w, 3))


class BotPlaysTests(unittest.TestCase):
    """Whole fishing sessions, in real time."""

    def _session(self, skin, games=3, config=None, **kw):
        game = bench.quick_game(skin, **kw)
        bot, logs = bench.run(game, config, games=games, timeout=40.0 * games)
        return game, bot, logs

    def test_stock_colors(self):
        game, bot, logs = self._session("official", games=4)
        s = game.stats
        self.assertEqual(s["timeouts"], 0, logs[-12:])
        self.assertGreaterEqual(s["caught"], 4)
        self.assertLessEqual(s["misses"], 2)              # an occasional late click
        self.assertEqual(s["stray_clicks"], 0)
        self.assertFalse(bot.detector.profile.learned)

    def _check_reskinned_client(self, name):
        game, bot, logs = self._session(name)
        s = game.stats
        self.assertTrue(bot.detector.profile.learned, logs[-12:])
        self.assertEqual(s["timeouts"], 0, logs[-12:])
        self.assertGreaterEqual(s["caught"], 3)
        self.assertLessEqual(s["misses"], 2)              # an occasional late click

    def test_human_like_clicking_still_catches(self):
        game, _bot, logs = self._session("official", config={"human_like_clicking": True})
        self.assertGreaterEqual(game.stats["caught"], 2, logs[-12:])

    def test_catches_are_opened(self):
        game = bench.quick_game("official")
        config = {"auto_fish_handling": True, "fish_actions": {n: "open" for n in game._item_names}}
        bench.run(game, config, games=4, timeout=120)
        self.assertGreaterEqual(game.stats["caught"], 4)
        living = [n for page in game.pages for n in page.values() if "_living" in n]
        self.assertLessEqual(len(living), 1, "fish were left unopened")   # the last one may be mid-handling

    def test_fish_that_do_not_open_are_dropped(self):
        game = bench.quick_game("official", dead_fish_chance=1.0)
        config = {"auto_fish_handling": True, "fish_actions": {n: "drop" for n in game._item_names},
                  "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)}
        bench.run(game, config, games=4, timeout=120)
        s = game.stats
        self.assertGreaterEqual(s["caught"], 4)
        self.assertGreaterEqual(s["dropped"], s["caught"] - 1)             # the last one may be mid-handling
        self.assertEqual(s["drop_cancelled"], 0)


# One test per reskinned client (test_reskinned_client_<skin>), not one test
# looping over all of them: each session takes 10-20 s of real time, so the
# loop was the longest single test (~110 s) and set the floor of the parallel
# run. As separate tests the xdist workers share them out.
def _reskinned_client_test(name):
    def test(self):
        self._check_reskinned_client(name)
    test.__doc__ = f"A client reskinned as {name!r} is learned and fished."
    return test


for _skin in SKINS:
    if _skin not in ("official", "official-real", "real-mt2") and _skin not in HARD_SKINS:   # stock colors: nothing to learn
        setattr(BotPlaysTests, "test_reskinned_client_" + _skin.replace("-", "_"), _reskinned_client_test(_skin))


if __name__ == "__main__":
    unittest.main()

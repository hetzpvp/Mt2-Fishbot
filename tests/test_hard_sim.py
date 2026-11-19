"""The fishing bot in conditions that are harder than a well-behaved client:

- the panel opens in a different place for every game, or is dragged mid-game
- the panel looks different in every game
- bites that take far longer than the configured timeout
- captures that come back black, items that land late, other game windows
  popping up while the line is out
- an inventory that fills up page by page
- several clients on one desktop (one cursor, one keyboard, one focus), with
  slow focus switches and an OS that sometimes refuses to switch at all

Everything runs against tools/minigame_sim, which enforces the game's rules
and counts what went wrong (misses, casts into the void, input that reached
the wrong window)."""

import os
import sys
import time
import unittest

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from fishing_bot import FishingBot  # noqa: E402
from minigame_sim import bench  # noqa: E402
from minigame_sim.desktop import FishingDesktop  # noqa: E402
from minigame_sim.fishing_game import INV_COLS, INV_ROWS  # noqa: E402

SLOTS_PER_PAGE = INV_COLS * INV_ROWS
# Looks that share the stock layout (a client changes its colors, not its size)
LOOKS = ["official", "emerald", "crimson", "official-redfish", "water", "sand-textured", "cyan-water"]


def finished(game):
    return game.stats["caught"] + game.stats["timeouts"]


class Base(unittest.TestCase):
    def tail(self, logs, n=14):
        return [m for m in logs if "Pressed key" not in m and "Cast fishing" not in m][-n:]

    def assert_no_wasted_input(self, game, logs, cancelled=0, ignored=0):
        """No cast into an open minigame, none that pulled the line back in."""
        s = game.stats
        self.assertLessEqual(s["casts_cancelled"], cancelled, self.tail(logs))
        self.assertLessEqual(s["casts_ignored"], ignored, self.tail(logs))
        self.assertEqual(s["stray_clicks"], 0, self.tail(logs))


class MovingPanelTests(Base):
    def test_panel_opens_somewhere_else_every_game(self):
        game = bench.quick_game("official", wander=True)
        bot, logs = bench.run(game, games=6, timeout=150)
        s = game.stats
        self.assertGreaterEqual(s["caught"], 6, self.tail(logs))
        self.assertEqual(s["timeouts"], 0, self.tail(logs))
        self.assertLessEqual(s["misses"], 2)
        self.assertGreaterEqual(bot._panel_moves, 3)
        self.assertFalse(bot.detector.profile.learned, "a moved panel is not a new look")
        self.assert_no_wasted_input(game, logs)

    def test_bot_stops_waiting_for_the_old_place(self):
        """Once the panel is known to wander, it is found right when it opens
        instead of after the two seconds a fixed panel is given."""
        game = bench.quick_game("official", wander=True, bite_delay=(0.3, 0.5))
        opened = []
        game.on_event = lambda kind, detail: opened.append(time.perf_counter()) if kind == "bite" else None
        found = []
        bot, logs = bench.make_bot(game)
        calibrate = bot._try_calibrate

        def spy(*a, **k):
            ok = calibrate(*a, **k)
            if ok:
                found.append(time.perf_counter())
            return ok

        bot._try_calibrate = spy
        bot.running = True
        import threading
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        deadline = time.time() + 120
        while time.time() < deadline and finished(game) < 5:
            time.sleep(0.02)
        bot.stop()
        thread.join(timeout=15)
        delays = [f - o for o, f in zip(opened, found)]
        self.assertGreaterEqual(len(delays), 5, self.tail(logs))
        # The first move is only discovered after the usual wait; later ones at once.
        self.assertLess(max(delays[2:]), 0.6, delays)

    def test_panel_dragged_during_a_game(self):
        game = bench.quick_game("official")
        dragged = []

        def on_event(kind, detail):
            if kind == "hit" and detail.startswith("1/") and len(dragged) < 2:
                dragged.append(1)
                game.move_panel(-170, 140)

        game.on_event = on_event
        bot, logs = bench.run(game, games=3, timeout=120)
        s = game.stats
        self.assertEqual(len(dragged), 2)
        self.assertGreaterEqual(s["caught"], 3, self.tail(logs))
        self.assertEqual(s["timeouts"], 0, self.tail(logs))
        # The drag did not look like the end of a game: no cast into an open minigame
        self.assert_no_wasted_input(game, logs)
        self.assertLessEqual(bot.total_games, s["games"])


class ChangingLookTests(Base):
    def test_every_game_looks_different(self):
        game = bench.quick_game("official", skins=LOOKS)
        bot, logs = bench.run(game, games=len(LOOKS), timeout=240)
        s = game.stats
        self.assertGreaterEqual(s["caught"], len(LOOKS) - 1, self.tail(logs))
        self.assertLessEqual(s["timeouts"], 1, self.tail(logs))
        self.assertLessEqual(s["misses"], 4, self.tail(logs))
        self.assert_no_wasted_input(game, logs)

    def test_a_look_seen_before_is_recognized_not_learned_again(self):
        game = bench.quick_game("official", skins=["emerald", "crimson", "emerald", "crimson", "emerald"])
        bot, logs = bench.run(game, games=5, timeout=200)
        s = game.stats
        self.assertGreaterEqual(s["caught"], 5, self.tail(logs))
        learned = [m for m in logs if "Learned this client's minigame" in m]
        self.assertLessEqual(len(learned), 2, learned)      # two looks, each learned once

    def test_changing_look_and_place_together(self):
        game = bench.quick_game("official", skins=["emerald", "official", "water", "crimson", "official"], wander=True)
        bot, logs = bench.run(game, games=5, timeout=240)
        s = game.stats
        self.assertGreaterEqual(s["caught"], 4, self.tail(logs))
        self.assertLessEqual(s["timeouts"], 1, self.tail(logs))
        self.assert_no_wasted_input(game, logs)

    def test_other_game_windows_are_not_taken_for_the_minigame(self):
        game = bench.quick_game("official", popup_chance=1.0, bite_delay=(2.2, 3.0), wander=True)
        bot, logs = bench.run(game, {"timing_minigame_timeout": 6.0}, games=4, timeout=180)
        s = game.stats
        self.assertGreaterEqual(s["popups"], 3)
        self.assertGreaterEqual(s["caught"], 4, self.tail(logs))
        self.assertFalse(bot.detector.profile.learned, self.tail(logs))
        self.assertLessEqual(bot.total_games, s["caught"] + s["timeouts"], "a pop-up was played as a minigame")
        self.assert_no_wasted_input(game, logs)


class SlowServerTests(Base):
    def test_bites_far_beyond_the_configured_timeout(self):
        game = bench.quick_game("official", bite_delay=(4.6, 6.2))
        marks = {}

        def on_event(kind, detail):
            if kind == "caught":
                marks.setdefault("cancelled_at_first_catch", game.stats["casts_cancelled"])

        game.on_event = on_event
        bot, logs = bench.run(game, {"timing_minigame_timeout": 2.5}, games=3, timeout=200)
        s = game.stats
        self.assertGreaterEqual(s["caught"], 3, self.tail(logs))
        # Learning the bite time cost a couple of casts; afterwards none is thrown away
        self.assertLessEqual(marks["cancelled_at_first_catch"], 2, self.tail(logs))
        self.assertEqual(s["casts_cancelled"], marks["cancelled_at_first_catch"], self.tail(logs))
        self.assertGreaterEqual(bot._bite_timeout(), 6.2)

    def test_recast_wait_is_tuned_down_to_what_the_rod_needs(self):
        game = bench.quick_game("official", recast_cooldown=1.2)
        config = {"adaptive_recast_wait": True, "timing_recast_wait": 2.6}
        desktop = FishingDesktop()
        bot = desktop.add(game, config)
        ok = desktop.run(lambda: bot._recast_tuner.settled, timeout=200)
        logs = desktop.logs
        self.assertTrue(ok, self.tail(logs))
        # The rod is ready 1.2 s after the panel closed; the bot sees the panel
        # close a moment later, so a little less than that is enough.
        self.assertGreaterEqual(bot._recast_tuner.wait, 1.0, self.tail(logs))
        self.assertLessEqual(bot._recast_tuner.wait, 1.7, self.tail(logs))
        self.assertLessEqual(game.stats["casts_ignored"], 2, self.tail(logs))   # the one probe that was too short

    def test_item_handling_does_not_add_to_the_recast_wait(self):
        """With the adaptive wait the time is counted from the end of the
        game: dropping a fish (about a second of clicking) happens inside the
        wait instead of in front of it."""
        times = {}
        for action in ("keep", "drop"):
            game = bench.quick_game("official", recast_cooldown=0.3, dead_fish_chance=1.0, bite_delay=(0.3, 0.3))
            ends, casts = [], []

            def on_event(kind, detail, ends=ends, casts=casts):
                if kind in ("caught", "timeout"):
                    ends.append(time.perf_counter())
                elif kind == "cast":
                    casts.append(time.perf_counter())

            game.on_event = on_event
            config = {"adaptive_recast_wait": True, "timing_recast_wait": 2.2, "auto_fish_handling": True,
                      "fish_actions": {n: action for n in game._item_names},
                      "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)}
            bot, logs = bench.run(game, config, games=3, timeout=120)
            gaps = [c - e for e, c in zip(ends, casts[1:])]
            self.assertGreaterEqual(len(gaps), 2, self.tail(logs))
            times[action] = gaps[0]             # before the tuner has shortened anything
            if action == "drop":
                self.assertGreaterEqual(game.stats["dropped"], 2, self.tail(logs))
        self.assertLess(abs(times["drop"] - times["keep"]), 0.45, times)


class BadConnectionTests(Base):
    def test_black_captures_do_not_end_or_restart_games(self):
        game = bench.quick_game("official", blank_chance=0.08)
        bot, logs = bench.run(game, games=4, timeout=150)
        s = game.stats
        self.assertGreaterEqual(s["caught"], 4, self.tail(logs))
        self.assertEqual(s["timeouts"], 0, self.tail(logs))
        self.assert_no_wasted_input(game, logs)
        self.assertLessEqual(bot.total_games, s["games"])

    def test_item_that_lands_late_is_still_handled(self):
        game = bench.quick_game("official", catch_delay=(0.5, 0.85))
        config = {"auto_fish_handling": True, "fish_actions": {n: "open" for n in game._item_names}}
        bot, logs = bench.run(game, config, games=5, timeout=150)
        self.assertGreaterEqual(game.stats["caught"], 4, self.tail(logs))
        living = [n for page in game.pages for n in page.values() if "_living" in n]
        self.assertLessEqual(len(living), 1, "fish were left unopened")   # the last one may be mid-handling


class InventoryTests(Base):
    def _config(self, game, action="keep"):
        config = {"auto_fish_handling": True, "fish_actions": {n: action for n in game._item_names},
                  "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)}
        for i, tab in enumerate(game.tabs):
            config[f"inv_page_{i + 1}_pos"] = list(tab)
        return config

    def _fill(self, game, page, free=0):
        names = game._item_names
        for slot in range(SLOTS_PER_PAGE - free):
            game.pages[page][slot] = names[slot % len(names)]

    def test_moves_to_the_next_page_when_one_fills_up(self):
        game = bench.quick_game("official")
        self._fill(game, 0, free=2)
        bot, logs = bench.run(game, self._config(game), games=4, timeout=180)
        self.assertGreaterEqual(game.stats["caught"], 4, self.tail(logs))
        self.assertEqual(game.page, 1, self.tail(logs))            # the bot is looking at page 2 ...
        self.assertGreaterEqual(len(game.pages[1]), 2)             # ... where the fish now land
        self.assertEqual(bot._current_inv_page, 1)
        self.assertNotEqual(bot.stop_reason, FishingBot.STOP_INVENTORY_FULL)

    def test_stops_when_every_page_is_full(self):
        game = bench.quick_game("official")
        for page in range(4):
            self._fill(game, page, free=1 if page == 3 else 0)
        desktop = FishingDesktop()
        bot = desktop.add(game, self._config(game))
        desktop.run(lambda: not bot.running, timeout=120)
        logs = desktop.logs
        self.assertEqual(bot.stop_reason, FishingBot.STOP_INVENTORY_FULL, self.tail(logs))
        self.assertEqual(game.stats["caught"], 1)
        self.assertEqual(game.stats["inventory_full"], 0, "fished on with a full inventory")

    def test_page_tabs_are_optional_without_auto_handling(self):
        game = bench.quick_game("official")
        bot, logs = bench.run(game, {"auto_fish_handling": False}, games=3, timeout=120)
        self.assertGreaterEqual(game.stats["caught"], 3, self.tail(logs))
        # (the last catch may not have been looked at yet when the bot was stopped)
        self.assertIn(len(bot._empty_slot_positions) + game.stats["caught"], (SLOTS_PER_PAGE, SLOTS_PER_PAGE + 1))


class SeveralClientsTests(Base):
    def _desktop(self, n, config=None, **kw):
        desktop = FishingDesktop(**kw)
        games = [bench.quick_game("official", seed=10 + i) for i in range(n)]
        for game in games:
            desktop.add(game, config(game) if callable(config) else config)
        return desktop, games

    def assert_input_went_home(self, desktop):
        self.assertEqual(desktop.misdirected_keys, 0, "a key reached another client")
        self.assertEqual(desktop.misdirected_clicks, 0, "a click reached another client")

    def test_three_clients_fish_side_by_side(self):
        desktop, games = self._desktop(3, switch_delay=0.01)
        ok = desktop.run(lambda: all(g.stats["caught"] >= 3 for g in games), timeout=240)
        self.assertTrue(ok, self.tail(desktop.logs))
        self.assert_input_went_home(desktop)
        for game in games:
            self.assertEqual(game.stats["stray_clicks"], 0)
            self.assertLessEqual(game.stats["timeouts"], 1)
            self.assertLessEqual(game.stats["casts_cancelled"], 0)

    def test_five_clients_with_slow_focus_switches_and_item_handling(self):
        def config(game):
            return {"auto_fish_handling": True, "fish_actions": {n: "drop" for n in game._item_names},
                    "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)}

        desktop = FishingDesktop(switch_delay=0.03)
        games = [bench.quick_game("official", seed=30 + i, dead_fish_chance=0.5, duration=25.0) for i in range(5)]
        for game in games:
            desktop.add(game, config(game))
        ok = desktop.run(lambda: all(g.stats["caught"] >= 2 for g in games), timeout=300)
        self.assertTrue(ok, self.tail(desktop.logs))
        self.assert_input_went_home(desktop)
        for i, game in enumerate(games):
            # Another client's click is a misdirected click (checked above).
            # A cancelled drop dialog can also be this bot's own put-back:
            # under load the dialog may come up only after the bot gave up
            # waiting for it, and the slot click then cancels it — the item
            # goes back to its slot either way. Only those are allowed.
            own_aborts = sum(1 for m in desktop.logs if m.startswith(f"[W{i + 1}] Drop of ") and " stopped: " in m)
            self.assertLessEqual(game.stats["drop_cancelled"], own_aborts,
                                 "a drop was cancelled by a click other than the bot's own put-back")
            self.assertEqual(game.stats["stray_clicks"], 0)
            self.assertIsNone(game.carrying)

    def test_os_that_sometimes_refuses_to_switch_windows(self):
        desktop, games = self._desktop(3, switch_delay=0.01, refuse_focus=0.35)
        ok = desktop.run(lambda: all(g.stats["caught"] >= 2 for g in games), timeout=300)
        self.assertTrue(ok, self.tail(desktop.logs))
        self.assertGreater(desktop.refused, 5)
        self.assert_input_went_home(desktop)

    def test_reskinned_clients_share_what_one_of_them_learned(self):
        desktop = FishingDesktop(switch_delay=0.01)
        # The first window gets its bite well before the others
        games = [bench.quick_game("emerald", seed=40 + i, bite_delay=(0.3, 0.5) if i == 0 else (3.5, 4.0))
                 for i in range(3)]
        for game in games:
            desktop.add(game, {"timing_minigame_timeout": 6.0})
        ok = desktop.run(lambda: all(g.stats["caught"] >= 2 for g in games), timeout=300)
        self.assertTrue(ok, self.tail(desktop.logs))
        self.assert_input_went_home(desktop)
        learned = [m for m in desktop.logs if "Learned this client's minigame" in m]
        self.assertEqual(len(learned), 1, learned)         # once, not once per window
        self.assertEqual(len({id(bot.detector.profile) for bot in desktop.bots}), 1)

    def test_stopping_all_clients_sends_nothing_afterwards(self):
        desktop, games = self._desktop(3, {"quick_skip": True, "quick_skip_mode": "horse"}, switch_delay=0.01)
        keys, stopped_at = [], {}
        for bot in desktop.bots:
            press, stop = bot.keyboard_controller.press, bot.stop
            bot.keyboard_controller.press = lambda key, press=press, bot=bot: (
                keys.append((bot.bot_id, time.perf_counter())), press(key))
            bot.stop = lambda stop=stop, bot=bot: (stopped_at.setdefault(bot.bot_id, time.perf_counter()), stop())
        desktop.run(lambda: all(g.stats["caught"] >= 1 for g in games), timeout=200)
        # A key combination that was already going down is finished; nothing new starts.
        late = [(bid, t - stopped_at[bid]) for bid, t in keys if t > stopped_at[bid] + 0.2]
        self.assertEqual(late, [], "keys were pressed by a stopped bot")
        self.assert_input_went_home(desktop)


if __name__ == "__main__":
    unittest.main()

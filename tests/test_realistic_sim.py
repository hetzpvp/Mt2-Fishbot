"""The simulator set up like the real client, and the bot against it.

Every number here comes from the two-client session in diagnostics
20261120_195925 (330 casts, 324 games, a 10 fps recording of each window):

- the bite comes 1, 2 or 3 s after the cast (a third each), never between
- the fish swims only at 45 degrees (95% of its moves), bounces off the
  panel's edges, at about 80 px/s, and rarely stops
- the ring and its glow are drawn over the fish: the bot never saw the fish
  60-67 px from the ring's center (59k fish positions)
- only 112 of 324 won games gave an item; it lands 0.1-0.2 s after the panel
  closes (checked frame by frame in the recording: 211 of the 212 "nothing
  caught" games really left the bag unchanged)
- holding Ctrl draws the stack counts bigger; animated item icons and the
  world next to the grid keep changing the inventory strip
- a click is lost when its window loses the focus 20-30 ms after it (37 of
  63 such clicks), never at 30 ms or later (16 of 16)
- a (dis)mount takes ~170 ms; a Ctrl+G sent while one is still going on is
  lost (once, after a 250 ms mount: the character stayed on the horse)

The first group checks that the simulator does these things; the second runs
the real bot loop against it.
"""

import math
import os
import sys
import time
import unittest

import cv2
import numpy as np

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import utils  # noqa: E402
from fish_detector import FishDetector  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402
from minigame_sim import bench  # noqa: E402
from minigame_sim.desktop import FishingDesktop  # noqa: E402


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def realistic(clock=None, **kw):
    if clock is not None:
        kw["clock"] = clock
    return bench.realistic_game(**kw)


def start_game(game, clock):
    """Casts and waits (on the virtual clock) until the panel is open."""
    game.key("1")
    game.key("space")
    for _ in range(400):
        clock.t += 0.01
        game.update()
        if game.state == "playing":
            return
    raise AssertionError("no bite")


class RealClientRulesTests(unittest.TestCase):
    """The simulator itself, on a virtual clock."""

    def test_bites_come_in_whole_seconds(self):
        clock = Clock()
        game = realistic(clock, seed=3)
        delays = []
        for _ in range(90):
            game.key("1")
            cast = clock.t
            game.key("space")
            self.assertEqual(game.state, "waiting")
            delays.append(game.state_until - cast)
            game.key("space")                 # reel in again
            clock.t += 3.0
        steps = [round(d) for d in delays]
        for d, step in zip(delays, steps):
            self.assertGreaterEqual(d - step, 0.0)
            self.assertLessEqual(d - step, 0.06 + 1e-9)
        for step in (1, 2, 3):
            self.assertGreaterEqual(steps.count(step), 15, steps)
        self.assertEqual(set(steps), {1, 2, 3})

    def test_fish_swims_at_45_degrees_inside_the_panel(self):
        clock = Clock()
        game = realistic(clock, seed=5, duration=60.0)
        start_game(game, clock)
        x, y, w, h = game.panel
        track = []
        for _ in range(int(20 / 0.008)):
            clock.t += 0.008
            game.update()
            fx, fy = game.fish
            self.assertTrue(x + 11 <= fx <= x + w - 18 and y + 28 <= fy <= y + h - 14, game.fish)
            track.append((clock.t, fx, fy))
        diagonal = moves = 0
        speeds = []
        for (t0, x0, y0), (t1, x1, y1) in zip(track[::19], track[19::19]):     # ~150 ms apart
            dx, dy = x1 - x0, y1 - y0
            dist = math.hypot(dx, dy)
            speeds.append(dist / (t1 - t0))
            if dist > 6:
                moves += 1
                angle = math.degrees(math.atan2(dy, dx)) % 90
                diagonal += abs(angle - 45) <= 10
        self.assertGreaterEqual(diagonal / moves, 0.8)
        median = sorted(speeds)[len(speeds) // 2]
        self.assertTrue(45 <= median <= 110, median)          # real: 74 px/s

    def test_fish_moves_on_the_games_frames_only(self):
        clock = Clock()
        game = realistic(clock, seed=5)
        start_game(game, clock)
        clock.t = math.floor(clock.t * 60) / 60 + 0.001
        game.update()
        before = game.fish
        clock.t += 0.01                       # still the same 60 fps frame
        game.update()
        self.assertEqual(game.fish, before)
        clock.t += 0.01                       # the next frame
        game.update()
        self.assertNotEqual(game.fish, before)

    def test_ring_hides_the_fish_while_it_crosses_it(self):
        clock = Clock()
        game = realistic(clock, seed=4)
        start_game(game, clock)
        x, y, w, h = game.panel
        cx, cy, _ = game.circle_abs()
        detector = FishDetector()

        def seen_share(dist):
            seen = 0
            for k in range(16):
                a = 2 * math.pi * k / 16
                game.fish = (cx + dist * math.cos(a), cy + dist * math.sin(a))
                result = detector.detect_window_and_fish(game.render()[y:y + h, x:x + w])
                seen += result[1] is not None
            return seen / 16

        self.assertEqual(seen_share(50), 1.0)
        self.assertEqual(seen_share(72), 1.0)
        self.assertLessEqual(seen_share(63), 0.1)

    def test_only_about_a_third_of_won_games_give_an_item(self):
        clock = Clock()
        game = realistic(clock, seed=9, inventory=False)
        for _ in range(300):
            game.state = "playing"
            game._end(clock.t, caught=True)
            clock.t += 1.0
            game.update()
        self.assertEqual(game.stats["caught"] + game.stats["empty_catches"], 300)
        self.assertTrue(80 <= game.stats["caught"] <= 130, game.stats["caught"])

    def test_item_lands_a_moment_after_the_panel_closes(self):
        clock = Clock()
        game = realistic(clock, seed=1, catch_chance=1.0, prefill=0)
        game.state = "playing"
        game._end(clock.t, caught=True)
        self.assertEqual(game.pages[0], {})
        clock.t += 0.09
        game.update()
        self.assertEqual(game.pages[0], {})
        clock.t += 0.12
        game.update()
        self.assertEqual(len(game.pages[0]), 1)

    def test_ctrl_draws_the_stack_counts_bigger(self):
        clock = Clock()
        game = realistic(clock, seed=2, living_world=False)
        game.animated.clear()
        self.assertTrue(game.stacks)
        ox, oy = game.inv_origin
        strip = (slice(oy, oy + 9 * 32), slice(ox, ox + 5 * 32))
        normal = game.render()[strip].astype(int)
        game.key("ctrl")
        held = game.render()[strip].astype(int)
        game.key_up("ctrl")
        self.assertTrue(np.array_equal(game.render()[strip].astype(int), normal))
        changed = np.argwhere(np.abs(held - normal).max(axis=2) > 30)
        self.assertGreater(len(changed), 0)
        cells = {(int(r) // 32, int(c) // 32) for r, c in changed}
        counted = {(slot // 5, slot % 5) for (page, slot) in game.stacks if page == 0}
        self.assertLessEqual(cells, counted)          # only cells with a count change

    def test_animated_icons_keep_changing_the_strip(self):
        clock = Clock()
        game = realistic(clock, seed=2, living_world=False)
        self.assertTrue(game.animated)
        ox, oy = game.inv_origin
        first = game.render()[oy:oy + 288, ox:ox + 160].astype(int)
        clock.t += 0.2
        later = game.render()[oy:oy + 288, ox:ox + 160].astype(int)
        self.assertGreater(int((np.abs(first - later).max(axis=2) > 30).sum()), 20)

    def test_world_moves_next_to_the_inventory(self):
        clock = Clock()
        game = realistic(clock, seed=2, prefill=0)
        ox = game.inv_origin[0]
        first = game.render()[:, :ox - 10].astype(int)
        clock.t += 0.5
        later = game.render()[:, :ox - 10].astype(int)
        self.assertGreater(int((np.abs(first - later).max(axis=2) > 30).sum()), 200)

    def test_quick_skip_cuts_the_rod_animation_short(self):
        clock = Clock()
        game = realistic(clock, seed=1)
        start_game(game, clock)
        game._end(clock.t, caught=True)
        clock.t += 0.3
        game.update()
        game.key("1")
        game.key("space")
        self.assertEqual(game.stats["casts_ignored"], 1)      # the rod is still busy
        game.key("ctrl")
        game.key("g")                                         # on the horse ...
        game.key_up("ctrl")
        clock.t += 0.25
        game.update()
        self.assertTrue(game.mounted)
        game.key("space")
        self.assertEqual(game.stats["casts_mounted"], 1)      # no fishing from the horse
        game.key("ctrl")
        game.key("g")                                         # ... and off again
        game.key_up("ctrl")
        clock.t += 0.25
        game.update()
        self.assertFalse(game.mounted)
        self.assertEqual(game.stats["quick_skips"], 1)
        game.key("1")
        game.key("space")
        self.assertEqual(game.state, "waiting")

    def test_ctrl_g_during_a_mount_is_lost(self):
        clock = Clock()
        game = realistic(clock, seed=1, slow_mount_chance=1.0, slow_mount_time=0.25)
        game.key("ctrl")
        game.key("g")
        clock.t += 0.2                                        # the bot's spacing between the two
        game.key("g")
        game.key_up("ctrl")
        clock.t += 0.3
        game.update()
        self.assertTrue(game.mounted)
        self.assertEqual(game.stats["mount_keys_lost"], 1)

    def test_ctrl_and_g_are_not_bait_keys(self):
        clock = Clock()
        game = realistic(clock, seed=1)
        game.key("ctrl")
        game.key("g")
        game.key_up("ctrl")
        clock.t += 0.3
        game.update()
        game.key("ctrl")
        game.key("g")
        game.key_up("ctrl")
        clock.t += 0.3
        game.update()
        self.assertFalse(game.bait_ready)
        game.key("space")
        self.assertEqual(game.stats["casts_without_bait"], 1)

    def test_click_is_lost_when_the_focus_leaves_right_after_it(self):
        desktop = FishingDesktop(click_latency=(0.03, 0.03))
        games = [bench.quick_game("official", seed=i) for i in range(2)]
        for game in games:
            desktop.add(game)
        first, second = desktop.bots
        first.window_manager.activate_window()
        first.mouse_backend.move_to(5, 5)
        first.mouse_backend.click()                           # a click into the world: a walk
        saved, utils.CLICK_FLUSH = utils.CLICK_FLUSH, 0.0     # a bot that does not wait for it
        try:
            second.window_manager.activate_window()
        finally:
            utils.CLICK_FLUSH = saved
        time.sleep(0.06)
        self.assertEqual(desktop.clicks_lost, 1)
        self.assertEqual(games[0].stats["walks"], 0)
        first.window_manager.activate_window()
        first.mouse_backend.click()
        time.sleep(0.06)                                      # focus kept: it lands
        self.assertEqual(games[0].stats["walks"], 1)

    def test_focus_switch_waits_for_the_last_click(self):
        utils.note_click_event()
        t0 = time.perf_counter()
        utils.wait_keys_delivered()
        self.assertGreaterEqual(time.perf_counter() - t0, utils.CLICK_FLUSH * 0.8)


class CatchHandlingTests(unittest.TestCase):
    """The catch wait and the inventory watch on a real-client inventory."""

    def _bot(self, **kw):
        game = bench.realistic_game(seed=11, **kw)
        bot, _logs = bench.make_bot(game, bench.realistic_config(game))
        bot.running = True
        self.assertTrue(bot._page_has_space(None, []))         # reads the free slots
        return game, bot

    def _empty_game_wait(self, bot, landings):
        bot._landing_seen = list(landings)
        bot._pending_catch_end = time.perf_counter() - 0.4      # the quick skip took 0.4 s
        t0 = time.perf_counter()
        match, _frame = bot._find_new_item(allow_extra=True, baseline=bot.capture_inventory_area())
        self.assertIsNone(match)
        return time.perf_counter() - t0

    def test_catch_wait_follows_the_landings_seen(self):
        """Items always seen within 0.3 s: a game that gave nothing costs
        a short look, not the full 1 s wait (212 such games in the session)."""
        _game, bot = self._bot(living_world=False, prefill=0)
        self.assertLess(self._empty_game_wait(bot, [0.3] * 5), 0.3)

    def test_too_few_landings_keep_the_configured_wait_from_the_game_end(self):
        """The configured wait counts from the game end, not from the end of
        the quick skip (0.4 s of it was spent there)."""
        _game, bot = self._bot(living_world=False, prefill=0)
        took = self._empty_game_wait(bot, [0.3] * (bot._LANDING_SAMPLES - 1))
        total = bot._t_catch_wait + bot._CATCH_EXTRA_WAIT
        self.assertGreaterEqual(took, total - 0.4 - 0.05)
        self.assertLess(took, total - 0.4 + 0.15)

    def test_before_the_cast_the_late_wait_is_left_to_the_watch(self):
        _game, bot = self._bot(living_world=False, prefill=0)
        bot._pending_catch_end = time.perf_counter() - 0.4
        t0 = time.perf_counter()
        match, _frame = bot._find_new_item(allow_extra=True, baseline=bot.capture_inventory_area(),
                                           defer_late=True)
        self.assertIsNone(match)
        self.assertLess(time.perf_counter() - t0, 0.35)       # one look and scan, not the 0.6 s left
        baseline, game_end, until = bot._late_watch
        self.assertAlmostEqual(until - game_end, bot._t_catch_wait + bot._CATCH_EXTRA_WAIT, places=2)

    def test_late_item_is_found_by_the_watch_after_the_cast(self):
        game, bot = self._bot(living_world=False, catch_chance=1.0, catch_delay=(0.1, 0.1))
        bot.config["fish_actions"] = {}
        baseline = bot.capture_inventory_area()
        bot._late_watch = (baseline, time.perf_counter() - 0.5, time.perf_counter() + 0.4)
        bot._late_until = bot._late_watch[2]
        bot._pending_catch_end = bot._late_watch[1]
        bot._late_watch = None
        game.state = "playing"
        game._end(time.perf_counter(), caught=True)
        bot._in_cast_window = True
        try:
            match, _frame = bot._find_new_item(allow_extra=True, baseline=baseline)
        finally:
            bot._in_cast_window = False
        self.assertIsNotNone(match)

    def test_item_already_there_at_the_first_look_is_not_learned(self):
        """Diag 20261121_110550: W2 looked 1.7 s after the game end (W1 had
        the mouse) and learned that as a landing: every game without an item
        cost 1.5 s from then on."""
        game, bot = self._bot(living_world=False, catch_chance=1.0, catch_delay=(0.0, 0.0))
        baseline = bot.capture_inventory_area()
        game.state = "playing"
        game._end(time.perf_counter(), caught=True)
        time.sleep(0.1)                                        # landed before the first look
        bot._pending_catch_end = time.perf_counter() - 1.7
        match, _frame = bot._find_new_item(allow_extra=True, baseline=baseline)
        self.assertIsNotNone(match)
        self.assertEqual(bot._landing_seen, [])

    def test_landing_is_learned_from_found_catches(self):
        # Watched from before the item lands (no quick skip in between)
        game, bot = self._bot(living_world=False, catch_chance=1.0, catch_delay=(0.1, 0.1))
        for _ in range(bot._LANDING_SAMPLES):
            baseline = bot.capture_inventory_area()
            game.state = "playing"
            game._end(time.perf_counter(), caught=True)
            bot._pending_catch_end = time.perf_counter() - 0.2
            match, _frame = bot._find_new_item(allow_extra=True, baseline=baseline)
            self.assertIsNotNone(match)
            bot._mark_occupied(*match[1])
        self.assertEqual(len(bot._landing_seen), bot._LANDING_SAMPLES)
        self.assertLess(bot._learned_landing(), 0.6)

    def test_a_slot_filled_by_an_unrecognized_catch_forgets_the_landings(self):
        _game, bot = self._bot(living_world=False)
        bot._landing_seen = [0.3] * 5
        bot._note_unrecognized_catch(True)
        self.assertIsNone(bot._learned_landing())

    def test_world_and_animated_icons_are_not_a_new_item(self):
        """With only tabs I and II set, the strip reaches into the world; on
        the real client 4302 of 5546 frames read as "covered" by it."""
        game, bot = self._bot()
        base = cv2.cvtColor(bot.capture_inventory_area(), cv2.COLOR_BGR2GRAY)
        watch = bot._landing_slots_mask(base.shape)
        self.assertIsNotNone(watch)
        for _ in range(8):
            time.sleep(0.06)                                    # figures walk, icons sparkle and blink
            report = {}
            self.assertIsNone(bot._changed_box(base, bot.capture_inventory_area(), report, watch))
            self.assertFalse(report.get("overlay"))
        game.ctrl = True                                        # quick skip: counts drawn bigger
        self.assertIsNone(bot._changed_box(base, bot.capture_inventory_area(), {}, watch))
        game.ctrl = False
        free = min(set(range(45)) - set(game.pages[0]))
        game.pages[0][free] = game._item_names[0]               # the catch lands
        box = bot._changed_box(base, bot.capture_inventory_area(), {}, watch)
        self.assertIsNotNone(box)
        sx, sy = game.slot_center(free)
        rx, ry, _, _ = bot._inventory_rect()
        self.assertTrue(box[0] <= sx - rx <= box[2] and box[1] <= sy - ry <= box[3], box)

    def test_a_covered_strip_is_still_an_overlay(self):
        """The Golden Tuna dialog hides the whole HUD: that is still told apart."""
        game, bot = self._bot()
        base = cv2.cvtColor(bot.capture_inventory_area(), cv2.COLOR_BGR2GRAY)
        game.tuna = ("choice", time.perf_counter())
        report = {}
        self.assertIsNone(bot._changed_box(base, bot.capture_inventory_area(), report,
                                           bot._landing_slots_mask(base.shape)))
        self.assertTrue(report.get("overlay"))


class RealClientBotTests(unittest.TestCase):
    """The real bot loop against the real-client simulator (real time)."""

    def tail(self, logs, n=16):
        return [m for m in logs if "Pressed key" not in m and "Cast fishing" not in m][-n:]

    def assert_clean(self, game, logs, misses=2):
        s = game.stats
        self.assertEqual(s["timeouts"], 0, self.tail(logs))
        self.assertLessEqual(s["misses"], misses, self.tail(logs))
        self.assertEqual(s["stray_clicks"], 0, self.tail(logs))
        self.assertEqual(s["walks"], 0, self.tail(logs))
        self.assertEqual(s["casts_cancelled"], 0, self.tail(logs))
        self.assertEqual(s["mount_keys_lost"], 0, self.tail(logs))
        self.assertEqual(s["casts_mounted"], 0, self.tail(logs))

    def test_one_client_plays_like_the_real_session(self):
        """Quick skip on the horse, auto handling, 2 of 4 tabs set, an
        inventory with stacks and animated icons, the world moving next to
        it, two games in three that give nothing."""
        game = bench.realistic_game(seed=7)
        bot, logs = bench.run(game, bench.realistic_config(game),
                              until=lambda: game.stats["games"] >= 9, timeout=150)
        self.assert_clean(game, logs)
        self.assertGreaterEqual(game.stats["games"], 9, self.tail(logs))
        self.assertGreaterEqual(game.stats["quick_skips"], game.stats["games"] - 2, self.tail(logs))
        self.assertGreater(game.stats["empty_catches"], 0)
        # Games that gave nothing are not missed bites: the bait is untouched
        self.assertNotEqual(bot.stop_reason, FishingBot.STOP_BAIT_DEPLETED, self.tail(logs))
        self.assertGreater(bot.bait_counter, 10 ** 6 - 30)
        # Nothing set to open or drop is left in the bag (the last catch may
        # still be waiting for the next cast)
        actions = bench.realistic_config(game)["fish_actions"]
        left = [n for n in game.pages[0].values() if actions.get(n) in ("open", "drop")]
        self.assertLessEqual(len(left), 1, (left, self.tail(logs)))

    def test_two_clients_lose_no_clicks_to_focus_switches(self):
        desktop = FishingDesktop(switch_delay=0.016, click_latency=(0.015, 0.035), seed=2)
        games = [bench.realistic_game(seed=20 + i) for i in range(2)]
        for game in games:
            desktop.add(game, bench.realistic_config(game))
        ok = desktop.run(lambda: all(g.stats["games"] >= 7 for g in games), timeout=180)
        self.assertTrue(ok, self.tail(desktop.logs))
        self.assertEqual(desktop.clicks_lost, 0, self.tail(desktop.logs))
        # Not left to chance: no switch ever came sooner than CLICK_FLUSH after a click
        self.assertGreater(len(desktop.click_to_switch), 10)
        self.assertGreaterEqual(min(desktop.click_to_switch), utils.CLICK_FLUSH * 0.95,
                                sorted(desktop.click_to_switch)[:5])
        self.assertEqual(desktop.misdirected_keys + desktop.misdirected_clicks, 0)
        for game in games:
            # two bots in real time on a test machine running other tests:
            # an occasional late click (what this test is about is above)
            self.assert_clean(game, desktop.logs, misses=4)
            # three hits each: no click wasted
            self.assertLessEqual(game.stats["hits"], 3 * game.stats["games"])

    def test_lost_second_ctrl_g_is_recovered_without_writing_off_the_bait(self):
        """Every mount is slow, so every quick skip leaves the character on
        the horse (the 20261120 incident, every time): the bot must get off
        again and keep fishing instead of stopping for "bait depleted"."""
        game = bench.realistic_game(seed=3, slow_mount_chance=1.0, slow_mount_time=0.3)
        bot, logs = bench.run(game, bench.realistic_config(game),
                              until=lambda: game.stats["games"] >= 4, timeout=150)
        self.assertGreaterEqual(game.stats["games"], 4, self.tail(logs))
        self.assertNotEqual(bot.stop_reason, FishingBot.STOP_BAIT_DEPLETED, self.tail(logs))
        self.assertGreater(game.stats["mount_keys_lost"], 0)
        self.assertGreater(bot.bait_counter, 10 ** 6 - 30, self.tail(logs))


if __name__ == "__main__":
    unittest.main()

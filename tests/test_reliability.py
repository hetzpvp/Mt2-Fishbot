"""Reliability fixes from the Fishbot & Jigsaw review: every failure mode
here used to be read as game state (a black frame as "no bite", a closed
puzzle as a board...) and cost bait, crates or items. Each test drives the
bots against tools/minigame_sim and checks they hold off or stop with a
named reason instead. Nothing here touches the desktop."""

import os
import sys
import threading
import time
import unittest

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import numpy as np  # noqa: E402

import screen_capture  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402
from minigame_sim import bench  # noqa: E402
from minigame_sim.desktop import FishingDesktop  # noqa: E402
from minigame_sim.jigsaw_game import JigsawGame, GRID  # noqa: E402
from minigame_sim.fishing_game import FishingGame  # noqa: E402
import test_jigsaw_sim as jsim  # noqa: E402  (module import: its tests are not collected twice)


def run_bot(bot, until, timeout):
    bot.running = True
    thread = threading.Thread(target=bot.play_game, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    while time.time() < deadline and thread.is_alive() and not until():
        time.sleep(0.02)
    stopped_by_itself = not thread.is_alive()
    bot.stop()
    thread.join(timeout=15)
    assert not thread.is_alive(), "the bot did not stop"
    return stopped_by_itself


class FrameUsableTests(unittest.TestCase):
    def test_black_flat_and_missing_frames_are_unusable(self):
        self.assertFalse(screen_capture.frame_usable(None))
        self.assertFalse(screen_capture.frame_usable(np.zeros((200, 300, 3), np.uint8)))
        self.assertFalse(screen_capture.frame_usable(np.full((200, 300, 3), 128, np.uint8)))
        noise = np.random.default_rng(1).integers(0, 255, (200, 300, 3), dtype=np.uint8)
        self.assertTrue(screen_capture.frame_usable(noise))

    def test_a_dark_scene_with_detail_is_usable(self):
        frame = np.zeros((200, 300, 3), np.uint8)
        frame[50:80, 40:200] = 40
        self.assertTrue(screen_capture.frame_usable(frame))


class CaptureHealthTests(unittest.TestCase):
    """F1: a blank capture is neither a missed bite nor the end of a game."""

    def _bot(self, game, config=None):
        bot, logs = bench.make_bot(game, config)
        bot._CAPTURE_WAIT_AFTER = 0.5
        bot._CAPTURE_STOP_AFTER = 2.5
        return bot, logs

    def test_black_window_from_the_start_costs_no_bait(self):
        game = bench.quick_game("official", blank_chance=1.0)
        bot, logs = self._bot(game)
        bait = bot.bait_counter
        stopped = run_bot(bot, lambda: False, timeout=30)
        self.assertTrue(stopped, logs[-10:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_CAPTURE_UNAVAILABLE, logs[-10:])
        self.assertEqual(bot.bait_counter, bait)
        self.assertEqual(bot.consecutive_failures, 0)
        self.assertEqual(bot.total_games, 0)
        self.assertLessEqual(game.stats["casts"], 1, "kept casting into a window it could not see")

    def test_window_going_black_mid_game_does_not_end_the_game(self):
        game = bench.quick_game("official")
        blacked = []

        def on_event(kind, detail):
            if kind == "hit" and not blacked:
                blacked.append(time.perf_counter())
                game.blank_chance = 1.0

        game.on_event = on_event
        bot, logs = self._bot(game)
        bait = bot.bait_counter
        stopped = run_bot(bot, lambda: False, timeout=60)
        self.assertTrue(blacked, logs[-10:])
        self.assertTrue(stopped, logs[-10:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_CAPTURE_UNAVAILABLE, logs[-10:])
        self.assertEqual(bot.total_games, 0, "the blackout was counted as the end of the game")
        self.assertEqual(bot.bait_counter, bait)
        self.assertEqual(game.stats["casts_cancelled"], 0, "recast into a live minigame")

    def test_short_blackout_is_waited_out(self):
        game = bench.quick_game("official")
        state = {"games": 0}

        def on_event(kind, detail):
            if kind in ("caught", "timeout"):
                state["games"] += 1
                if state["games"] == 1:
                    game.blank_chance = 1.0
                    threading.Timer(1.5, lambda: setattr(game, "blank_chance", 0.0)).start()

        game.on_event = on_event
        bot, logs = self._bot(game)
        run_bot(bot, lambda: state["games"] >= 3, timeout=90)
        self.assertGreaterEqual(state["games"], 3, logs[-10:])
        self.assertIsNot(bot.stop_reason, FishingBot.STOP_CAPTURE_UNAVAILABLE)
        self.assertEqual(bot.consecutive_failures, 0)


class OverlapTests(unittest.TestCase):
    """C1: windows read from the screen must not overlap."""

    def test_overlapping_windows_with_screen_capture_do_not_start(self):
        desktop = FishingDesktop()
        games = [bench.quick_game("official", seed=50 + i) for i in range(2)]
        bots = [desktop.add(g) for g in games]
        games[1].left = games[0].left + 200          # partly on top of the first
        for bot in bots:
            self.assertTrue(bot.capture.reads_screen_when_covered)
        # Not "until both stop": the first bot plays on alone by design, so
        # that wait always ran the full timeout. Stop 2 s after the refusal,
        # long enough for the other bot to cast if it is going to.
        refused_at = []

        def refused_and_played_on():
            if not refused_at and any(b.stop_reason == FishingBot.STOP_CAPTURE_OVERLAP for b in bots):
                refused_at.append(time.time())
            return bool(refused_at) and time.time() - refused_at[0] >= 2.0
        desktop.run(refused_and_played_on, timeout=30)
        reasons = [b.stop_reason for b in bots]
        self.assertIn(FishingBot.STOP_CAPTURE_OVERLAP, reasons, desktop.logs[-10:])
        self.assertTrue(any("overlaps W" in m for m in desktop.logs), desktop.logs[-10:])
        # The bot that registers second refuses; the first then plays alone,
        # which is safe. Never do both play over the shared screen area.
        self.assertLessEqual(sum(1 for g in games if g.stats["casts"]), 1)

    def test_side_by_side_windows_start(self):
        desktop = FishingDesktop()
        games = [bench.quick_game("official", seed=60 + i) for i in range(2)]
        for g in games:
            desktop.add(g)
        ok = desktop.run(lambda: all(g.stats["caught"] >= 1 for g in games), timeout=90)
        self.assertTrue(ok, desktop.logs[-10:])

    def test_trusted_window_render_may_overlap(self):
        """With a proven per-window render a covered window is read from its
        own pixels, so overlapping is allowed. (Played out only as far as the
        start check: the simulated desktop has no window stacking order.)"""
        desktop = FishingDesktop()
        games = [bench.quick_game("official", seed=70 + i) for i in range(2)]
        bots = [desktop.add(g) for g in games]
        games[1].left = games[0].left + 200
        for bot in bots:
            screen_capture.register_active(bot.capture)
        try:
            self.assertEqual(bots[0].capture.overlapping_peers(), ["W2"])
            bots[0].running = True
            self.assertFalse(bots[0]._refuse_overlapping_windows())
            bots[1].capture.window_trusted = True
            bots[1].running = True
            self.assertTrue(bots[1]._refuse_overlapping_windows())
            self.assertIsNone(bots[1].stop_reason)
        finally:
            for bot in bots:
                screen_capture.unregister_active(bot.capture)


class InventoryVisibleTests(unittest.TestCase):
    """F3: a closed inventory is noticed before any tab is clicked."""

    def _config(self, game):
        cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
        cfg.update({"auto_fish_handling": True, "fish_actions": {n: "drop" for n in game._item_names},
                    "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)})
        return cfg

    def test_closed_inventory_gets_no_click_and_its_own_stop_reason(self):
        game = bench.quick_game("official", dead_fish_chance=1.0)
        cfg = self._config(game)
        game.inventory_enabled = False
        clicks = []
        original = game.click
        game.click = lambda *a, **k: (clicks.append(a), original(*a, **k))[1]
        bot, logs = bench.make_bot(game, cfg)
        stopped = run_bot(bot, lambda: False, timeout=20)
        self.assertTrue(stopped, logs[-8:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_INVENTORY_NOT_VISIBLE, logs[-8:])
        self.assertEqual(clicks, [])
        self.assertEqual(game.stats["casts"], 0)

    def test_open_inventory_plays(self):
        game = bench.quick_game("official", dead_fish_chance=1.0)
        bot, logs = bench.run(game, self._config(game), games=2, timeout=90)
        self.assertGreaterEqual(game.stats["caught"], 2, logs[-8:])
        self.assertIsNot(bot.stop_reason, FishingBot.STOP_INVENTORY_NOT_VISIBLE)


class DropLoopTests(unittest.TestCase):
    """F4: every step of a drop is confirmed; a lost click never leaves the
    item on the cursor or sends a confirm without its dialog."""

    def _game_and_config(self, **kw):
        game = bench.quick_game("official", dead_fish_chance=1.0, **kw)
        cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
        cfg.update({"auto_fish_handling": True, "fish_actions": {n: "drop" for n in game._item_names},
                    "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)})
        return game, cfg

    def _watch_blind_confirms(self, game):
        """Clicks on the confirm or drop button while no dialog is up."""
        blind = []
        original = game.click

        def click(x, y, button="left"):
            if button == "left" and game.dialog is None:
                for point in (game.confirm_button, game.drop_button):
                    if abs(x - point[0]) <= 32 and abs(y - point[1]) <= 12:
                        blind.append((x, y))
            return original(x, y, button)

        game.click = click
        return blind

    def _run(self, game, cfg, catches=3):
        blind = self._watch_blind_confirms(game)
        bot, logs = bench.run(game, cfg, games=catches, timeout=120)
        return bot, logs, blind

    def test_drop_works_when_nothing_goes_wrong(self):
        game, cfg = self._game_and_config()
        bot, logs, blind = self._run(game, cfg)
        self.assertGreaterEqual(game.stats["dropped"], 2, logs[-10:])
        self.assertEqual(blind, [])
        self.assertIsNone(game.carrying)

    def test_ground_click_that_hits_ui_puts_the_item_back(self):
        game, cfg = self._game_and_config(blocked_ground_clicks=1)
        # (the first drop waits for a timed bite: it comes at the end of game 2)
        bot, logs, blind = self._run(game, cfg, catches=4)
        self.assertEqual(game.stats["blocked_ground_clicks"], 1)
        self.assertEqual(blind, [], "a button was clicked without its dialog")
        self.assertIsNone(game.carrying, "the item was left on the cursor")
        self.assertTrue(any("item put back" in m for m in logs), logs[-10:])
        self.assertGreaterEqual(game.stats["dropped"], 1, logs[-10:])   # later drops still work

    def test_lost_drop_button_click_sends_no_confirm(self):
        game, cfg = self._game_and_config(lost_drop_buttons=1)
        bot, logs, blind = self._run(game, cfg)
        self.assertEqual(game.stats["lost_drop_buttons"], 1)
        self.assertEqual(blind, [])
        self.assertIsNone(game.carrying)

    def test_unsure_match_is_kept_not_dropped(self):
        game, cfg = self._game_and_config()
        bot, logs = bench.make_bot(game, cfg)
        name = game._item_names[0]
        game.pages[0][0] = name
        bot.running = True
        rx, ry = bot._inventory_rect()[:2]
        cx, cy = game.slot_center(0)
        sure, why = bot._drop_match_sure(name, cx - rx, cy - ry)
        self.assertTrue(sure, why)
        bot._DROP_SURE_GRAY = 1.01                  # nothing is that sure: only a color match can do
        bot._DROP_SURE_COLOR = 1.01
        sure, why = bot._drop_match_sure(name, cx - rx, cy - ry)
        self.assertFalse(sure, why)


class InventoryBookkeepingTests(unittest.TestCase):
    """F5/F6: occupied marks are given up when the slot empties, and the
    strip ends with the item grid."""

    def test_slot_marked_occupied_that_empties_is_used_again(self):
        game = bench.quick_game("official")
        cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
        bot, _ = bench.make_bot(game, cfg)
        bot.running = True
        rx, ry = bot._inventory_rect()[:2]
        cx, cy = game.slot_center(0)
        bot._mark_occupied(cx - rx, cy - ry)            # e.g. an open judged failed on a slow server
        self.assertTrue(bot._page_has_space())          # the slot is empty in the game
        self.assertEqual(bot._ignored_positions, set())

    def test_strip_ends_with_the_grid(self):
        import inventory_layout
        game = bench.quick_game("official")
        cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
        (x, y, w, h), source = inventory_layout.strip_rect(cfg, game.W, game.H)
        self.assertEqual(source, "tabs")
        grid_bottom = game.inv_origin[1] + 9 * 32
        self.assertGreaterEqual(y + h, grid_bottom)
        self.assertLessEqual(y + h, grid_bottom + 12)
        (_, y2, _, h2), _ = inventory_layout.strip_rect(dict(cfg, inventory_rows=5), game.W, game.H)
        self.assertEqual(y2 + h2, y + h - 4 * 32)


class EscalationTests(unittest.TestCase):
    """F7/F9/F16: conditions that used to repeat forever (or end in a wrong
    "bait depleted") stop with their own reason."""

    def test_fish_never_seen_without_adaptive_detection_stops(self):
        game = bench.quick_game("official-redfish")         # stock panel, a fish the stock colors miss
        game.duration = 3.0
        bot, logs = bench.make_bot(game, {"adaptive_detection": False})
        stopped = run_bot(bot, lambda: False, timeout=90)
        self.assertTrue(stopped, logs[-8:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_FISH_NOT_DETECTED, logs[-8:])
        self.assertLessEqual(game.stats["games"], FishingBot._NO_FISH_GAMES)

    def test_no_minigame_ever_stops_without_writing_off_bait(self):
        game = bench.quick_game("official", bite_delay=(999.0, 999.0))
        bot, logs = bench.make_bot(game, {"timing_minigame_timeout": 0.4})
        bait = bot.bait_counter
        stopped = run_bot(bot, lambda: False, timeout=90)
        self.assertTrue(stopped, logs[-8:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_NO_BITE, logs[-8:])
        self.assertEqual(bot.bait_counter, bait, "a bait tier was written off")
        self.assertTrue(any("Is there bait on key" in m for m in logs), logs[-5:])

    def test_window_that_cannot_be_focused_stops(self):
        game = bench.quick_game("official")
        bot, logs = bench.make_bot(game)
        bot._FOCUS_GIVE_UP = 1.0
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._resume_inventory_check = lambda: None
        bot.window_manager.activate_window = lambda force_activate=False: False
        stopped = run_bot(bot, lambda: False, timeout=30)
        self.assertTrue(stopped, logs[-8:])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_WINDOW_UNAVAILABLE, logs[-8:])
        self.assertEqual(game.stats["casts"], 0)


class LockSafetyTests(unittest.TestCase):
    """C3/F12."""

    def test_nested_acquire_raises_instead_of_hanging(self):
        from utils import PriorityInputLock
        lock = PriorityInputLock()
        with lock:
            self.assertTrue(lock.held_by_me())
            with self.assertRaises(RuntimeError):
                lock.acquire()
            with self.assertRaises(RuntimeError):
                with lock.urgent():
                    pass
        self.assertFalse(lock.locked())
        with lock:                                   # still usable afterwards
            pass

    def test_other_threads_still_wait_their_turn(self):
        from utils import PriorityInputLock
        lock = PriorityInputLock()
        order = []
        lock.acquire()
        t = threading.Thread(target=lambda: (lock.acquire(), order.append("other"), lock.release()))
        t.start()
        time.sleep(0.05)
        order.append("me")
        lock.release()
        t.join(timeout=2)
        self.assertEqual(order, ["me", "other"])

    def test_ctrl_is_released_when_the_key_press_fails(self):
        game = bench.quick_game("official")
        bot, _ = bench.make_bot(game)
        bot.running = True
        events = []

        class Keyboard:
            def press(self, key):
                events.append(("press", key))
                if key == "g":
                    raise OSError("device gone")

            def release(self, key):
                events.append(("release", key))

        bot.keyboard_controller = bot.human.keyboard_controller = Keyboard()
        bot.press_ctrl_key("g")
        from fishing_bot import Key
        self.assertIn(("release", Key.ctrl), events, events)


class PerClientTests(unittest.TestCase):
    """C2/C4."""

    def _drop_config(self, game):
        cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
        cfg.update({"auto_fish_handling": True, "fish_actions": {n: "drop" for n in game._item_names},
                    "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button)})
        return cfg

    def test_one_client_with_a_closed_inventory_is_named_the_other_plays(self):
        desktop = FishingDesktop()
        games = [bench.quick_game("official", seed=80 + i, dead_fish_chance=1.0) for i in range(2)]
        bots = [desktop.add(g, self._drop_config(g)) for g in games]
        games[1].inventory_enabled = False
        ok = desktop.run(lambda: games[0].stats["caught"] >= 2 and bots[1].stop_reason, timeout=120)
        self.assertTrue(ok, desktop.logs[-10:])
        self.assertEqual(bots[1].stop_reason, FishingBot.STOP_INVENTORY_NOT_VISIBLE)
        self.assertTrue(any(m.startswith("[W2]") and "inventory is not where" in m for m in desktop.logs))
        self.assertNotEqual(bots[0].stop_reason, FishingBot.STOP_INVENTORY_NOT_VISIBLE)

    def test_focus_lost_mid_drop_sends_no_further_click_into_it(self):
        game = bench.quick_game("official", dead_fish_chance=1.0)
        bot, logs = bench.make_bot(game, self._drop_config(game))
        state = {"steal": False, "stolen": 0}
        original_click = game.click

        def click(x, y, button="left"):
            # The moment the item is picked up, a pop-up takes the focus once.
            result = original_click(x, y, button)
            if game.carrying is not None and not state["stolen"]:
                state["steal"] = True
            return result

        game.click = click

        def is_foreground():
            if state["steal"]:
                state["steal"], state["stolen"] = False, 1
                return False
            return True

        bot.window_manager.is_foreground = is_foreground
        # (the first drop waits for a timed bite: it comes at the end of game 2)
        run_bot(bot, lambda: game.stats["caught"] >= 3, timeout=90)
        self.assertEqual(state["stolen"], 1)
        self.assertIsNone(game.carrying, "item left on the cursor after the focus was lost")
        self.assertTrue(any("lost the focus" in m for m in logs), logs[-10:])


class SharedStateTests(unittest.TestCase):
    """F15: nothing the windows shared carries over from one run to the next."""

    def test_new_run_starts_without_the_last_runs_state(self):
        from fish_detector import DetectionProfile
        FishingBot._shared_learned_profile = DetectionProfile(name="learned", learned=True)
        FishingBot._known_profiles = [FishingBot._shared_learned_profile]
        FishingBot._shared_recast_wait = (1.0, 2.0)
        FishingBot._classic_fish_last_scale = 1.4
        FishingBot.reset_session_state()
        self.assertIsNone(FishingBot._shared_learned_profile)
        self.assertEqual(FishingBot._known_profiles, [])
        self.assertIsNone(FishingBot._shared_recast_wait)
        self.assertIsNone(FishingBot._classic_fish_last_scale)


class SceneryGame(FishingGame):
    """Something in the game world has the panel's color all the time."""

    BLOCK = (20, 380, 170, 130)

    def render(self):
        import cv2
        frame = super().render()
        x, y, w, h = self.BLOCK
        cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), self._panel_bgr, -1)
        return frame


class CalibrationGuardTests(unittest.TestCase):
    """F2: the stock colors do not lock onto scenery, and a "panel" that
    never closes stops the bot instead of looping forever."""

    def test_static_panel_colored_block_is_never_calibrated_on(self):
        game = SceneryGame(skin="official", bite_delay=(0.3, 0.8), recast_cooldown=0.2)
        regions = []
        bot, logs = bench.make_bot(game)
        calibrate = bot._try_calibrate

        def spy(*a, **k):
            ok = calibrate(*a, **k)
            if ok and bot.region is not None:
                regions.append((bot.region.left, bot.region.top, bot.region.width, bot.region.height))
            return ok

        bot._try_calibrate = spy
        run_bot(bot, lambda: game.stats["caught"] >= 3, timeout=120)
        self.assertGreaterEqual(game.stats["caught"], 3, logs[-10:])
        bx, by, bw, bh = SceneryGame.BLOCK
        for region in regions:
            self.assertFalse(abs(region[0] - bx) < 10 and abs(region[1] - by) < 10,
                             f"calibrated on the scenery block: {region}")

    def test_tiny_and_huge_candidates_are_rejected(self):
        game = bench.quick_game("official")
        bot, _ = bench.make_bot(game)
        frame = np.zeros((600, 800, 3), np.uint8)
        self.assertIn("smaller", bot._first_panel_doubt(frame, (10, 10, 60, 60), 3600))
        self.assertIn("larger", bot._first_panel_doubt(frame, (0, 0, 700, 500), 350000))
        self.assertIsNone(bot._first_panel_doubt(frame, (100, 100, 280, 220), 50000))

    def test_two_stuck_games_in_a_row_stop_the_bot(self):
        game = bench.quick_game("official")
        bot, logs = bench.make_bot(game)
        bot.running = True
        bot._abort_stuck_game()
        self.assertTrue(bot.running)
        bot._abort_stuck_game()
        self.assertFalse(bot.running)
        self.assertEqual(bot.stop_reason, FishingBot.STOP_FALSE_DETECTION)


class ClosedPuzzle(JigsawGame):
    """The Fishing Jigsaw window is not open: the game world is shown."""

    def render(self):
        return self.background.copy()


def count_clicks(game):
    clicks = []
    original = game.click

    def click(x, y, button):
        clicks.append((x, y, button))
        return original(x, y, button)

    game.click = click
    return clicks


class JigsawGridCheckTests(unittest.TestCase):
    """J1: the configured rectangle is checked before anything is read or clicked."""

    def _run_until_paused(self, game, config=None):
        clicks = count_clicks(game)
        rig = jsim.Rig([game], config)
        rig.run(lambda: (0, True) in rig.pauses, timeout=30)
        return rig, clicks

    def test_closed_puzzle_gets_no_click_and_a_pause(self):
        rig, clicks = self._run_until_paused(ClosedPuzzle())
        self.assertIn((0, True), rig.pauses, rig.logs[-8:])
        self.assertEqual(clicks, [])
        self.assertTrue(any("puzzle window not found" in m for m in rig.logs), rig.logs[-8:])

    def test_grid_shifted_by_40px_gets_no_click_and_a_pause(self):
        shifted = [GRID[0] + 40, GRID[1], GRID[2], GRID[3]]
        rig, clicks = self._run_until_paused(JigsawGame(), {"jigsaw_grid_bounds": shifted})
        self.assertIn((0, True), rig.pauses, rig.logs[-8:])
        self.assertEqual(clicks, [])

    def test_grid_a_few_pixels_off_still_plays(self):
        game = JigsawGame()
        nudged = [GRID[0] + 3, GRID[1] - 2, GRID[2], GRID[3]]
        rig = jsim.Rig([game], {"jigsaw_grid_bounds": nudged})
        self.assertTrue(rig.run(lambda: game.placed >= 3, timeout=60), rig.logs[-8:])

    def test_full_board_is_still_recognized(self):
        game = JigsawGame()
        for c in range(6):
            for r in range(4):
                game.cell_figure[(c, r)] = (c + r) % 6
        from jigsaw_detector import JigsawDetector
        grid = JigsawDetector({"jigsaw_grid_bounds": list(GRID)}).detect_grid(game.render())
        self.assertTrue(grid.grid_found)
        self.assertGreaterEqual(grid.confidence, 0.6)



class JigsawWasteTests(unittest.TestCase):
    """J2: a cell that reads empty but is filled must not burn every crate."""

    def test_cell_that_always_reads_empty_costs_at_most_two_crates(self):
        game = JigsawGame(pieces=[0] * 40)      # 1x1 pieces: the solver fills cells in order
        game.board.add((0, 0))                   # filled in the game, drawn empty
        rig = jsim.Rig([game])
        rig.run(lambda: game.opened >= 12 or (0, True) in rig.pauses, timeout=60)
        refused = [e for e in game.errors if "illegal placement" in e]
        self.assertLessEqual(len(refused), 2, rig.logs[-10:])   # one crate: its click and the retry
        self.assertLessEqual(game.discarded, 1, rig.logs[-10:])
        # It either moved on to the other cells or paused for the player.
        self.assertTrue(game.placed >= 5 or (0, True) in rig.pauses, rig.logs[-10:])

    def test_board_that_reads_full_opens_no_crate(self):
        game = JigsawGame()
        for c in range(6):
            for r in range(4):
                game.cell_figure[(c, r)] = (c + r) % 6     # drawn full, empty in the game
        rig = jsim.Rig([game])
        rig.run(lambda: (0, True) in rig.pauses, timeout=30)
        self.assertIn((0, True), rig.pauses, rig.logs[-8:])
        self.assertEqual(game.opened, 0)

    def test_every_piece_discarded_pauses(self):
        game = JigsawGame()
        rig = jsim.Rig([game], {"jigsaw_max_consecutive_discards": 3})
        # A solver that never places: every round is a discard.
        for bot in rig.bots:
            bot.solver = None
        import jigsaw_bot as jb
        from jigsaw_solver.jigsaw import SKIP_ACTION

        class Skipper:
            def solve(self, state):
                return SKIP_ACTION

            def rank_custom_actions(self, *a, **k):
                return []

        jb.get_solver = lambda: Skipper()
        rig.run(lambda: (0, True) in rig.pauses, timeout=30)
        self.assertIn((0, True), rig.pauses, rig.logs[-8:])
        self.assertLessEqual(game.opened, 3)



class JigsawResultTests(unittest.TestCase):
    """J3: the result button is clicked only when its dialog is on screen."""

    def _nearly_full(self, game):
        # Every cell but one filled: the next 1x1 piece completes the board.
        for c in range(6):
            for r in range(4):
                if (c, r) != (5, 3):
                    game.board.add((c, r))
                    game.cell_figure[(c, r)] = (c + r) % 6

    def test_completion_without_a_dialog_gets_no_click(self):
        game = JigsawGame(pieces=[0] * 10, shows_result=False)
        self._nearly_full(game)
        after = []
        original = game.click
        game.click = lambda x, y, b: (after.append((x, y, b)) if game.completed else None, original(x, y, b))[1]
        rig = jsim.Rig([game], {"jigsaw_completion_animation_wait": 0.3, "jigsaw_result_confirm_timeout": 0.3})
        rig.run(lambda: (0, True) in rig.pauses, timeout=30)
        self.assertEqual(game.completed, 1, rig.logs[-8:])
        self.assertEqual(after, [], f"clicked after the board completed without a dialog: {after}")
        self.assertTrue(any("nothing clicked" in m for m in rig.logs), rig.logs[-8:])

    def test_completion_with_its_dialog_is_confirmed(self):
        game = JigsawGame(pieces=[0] * 10)
        self._nearly_full(game)
        rig = jsim.Rig([game])
        ok = rig.run(lambda: game.completed >= 1 and game.dialog is None, timeout=30)
        self.assertTrue(ok, rig.logs[-8:])
        self.assertEqual(game.board, set(), "the result dialog was not confirmed")


class JigsawGeometryTests(unittest.TestCase):
    """J8/J9/J12."""

    def test_confirm_point_on_the_grid_is_refused_at_start(self):
        game = JigsawGame()
        clicks = count_clicks(game)
        on_grid = [GRID[0] + 50, GRID[1] + 50]
        rig = jsim.Rig([game], {"confirm_button_pos": on_grid})
        rig.run(lambda: not rig.threads[0].is_alive(), timeout=20)
        self.assertEqual(clicks, [])
        self.assertTrue(any("lies on the puzzle grid" in m for m in rig.logs), rig.logs[-5:])
        self.assertEqual(rig.bots[0].stop_reason, "bad_layout")

    def test_minimized_window_is_clicked_where_it_is_restored(self):
        game = JigsawGame()
        rig = jsim.Rig([game])
        wm = rig.bots[0].window_manager
        state = {"minimized": True}
        real_rect, real_activate = wm.get_window_rect, wm.activate_window

        def rect():
            return (-32000, -32000, 160, 28) if state["minimized"] else real_rect()

        def activate(force_activate=False):
            state["minimized"] = False
            return real_activate(force_activate)

        wm.get_window_rect = rect
        wm.activate_window = activate
        lost = []
        mouse = rig.bots[0].mouse_backend
        original = mouse._click
        mouse._click = lambda button: (lost.append(rig.desktop.pos) if rig.desktop.game_at(rig.desktop.pos)
                                       is None else None, original(button))[1]
        # The window is minimized again before every round.
        rig.bots[0]._ensure_crates_available = (lambda f=rig.bots[0]._ensure_crates_available:
                                                lambda *a: (state.update(minimized=True), f(*a))[1])()
        ok = rig.run(lambda: game.placed >= 3, timeout=60)
        self.assertTrue(ok, rig.logs[-8:])
        self.assertEqual(lost, [], "clicks went to the minimized window's rectangle")

    def test_no_piece_colored_pixels_means_no_discard_clicks(self):
        game = JigsawGame(gives_pieces=False)
        rig = jsim.Rig([game], {"jigsaw_max_unknown_pieces": 2})
        rig.run(lambda: (0, True) in rig.pauses, timeout=30)
        self.assertIn((0, True), rig.pauses, rig.logs[-8:])
        self.assertFalse(any("Discard jigsaw piece" in m for m in rig.logs), rig.logs[-8:])


class JigsawRefillTests(unittest.TestCase):
    """J4-J6: refilling a crate slot never leaves a crate on the cursor, and a
    page that could not be opened is not taken for an empty one."""

    def test_crate_the_slot_never_takes_is_put_back_and_pauses(self):
        game = JigsawGame(normal=0, inventory={2: ("normal", (60, 120), 4)}, lose_slot_clicks=10 ** 6)
        rig = jsim.Rig([game])
        rig.run(lambda: (0, True) in rig.pauses, timeout=60)
        self.assertIn((0, True), rig.pauses, rig.logs[-10:])
        self.assertIsNone(game.carrying, "the crate was left on the cursor")
        self.assertEqual(game.put_back, 1)
        self.assertEqual(game.opened, 0)
        self.assertEqual(game.errors, [])
        self.assertTrue(any("put back into the inventory" in m for m in rig.logs), rig.logs[-10:])

    def test_page_that_could_not_be_opened_is_not_called_empty(self):
        game = JigsawGame(normal=0, inventory={2: ("normal", (60, 120), 4)})
        rig = jsim.Rig([game])
        bot = rig.bots[0]
        bot.running = True
        real_focus = bot._focus
        bot._focus = lambda: False
        from jigsaw_detector import JigsawDetector  # noqa: F401
        slots = {"normal": (414, 273), "special": (414, 218)}
        self.assertFalse(bot._refill_missing_crates({"normal"}, slots))
        self.assertEqual(bot._exhausted_sections, set(), "a kind was written off without looking")
        self.assertTrue(any("could not be opened" in m for m in rig.logs), rig.logs[-5:])
        bot._focus = real_focus
        jsim.SimWM(rig.desktop, game).activate_window()
        self.assertTrue(bot._refill_missing_crates({"normal"}, slots))
        self.assertEqual(game.slots["normal"], 4)


if __name__ == "__main__":
    unittest.main()

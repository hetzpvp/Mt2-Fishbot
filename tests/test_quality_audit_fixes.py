"""Regression tests for the 2026-11-18 quality audit
(docs/quality-audit-2026-11-18.md). Stub/simulator only: nothing here
touches the desktop."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _stubs import install, FakeWindowManager, FakeMouse

install()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "tools"))

import dialog_watch  # noqa: E402
import fishing_bot as fb  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402
from jigsaw_bot import JigsawBot  # noqa: E402
from minigame_sim import bench  # noqa: E402
from test_review_fixes import quiet_bot  # noqa: E402

try:
    from PySide6.QtWidgets import QMessageBox
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False

if HAS_QT:
    from test_gui_smoke import GuiFixture
else:  # pragma: no cover
    GuiFixture = unittest.TestCase

_spec = importlib.util.spec_from_file_location("release_qa", os.path.join(ROOT, "tools", "release.py"))
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


class _Keys:
    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(("press", str(key)))

    def release(self, key):
        self.events.append(("release", str(key)))


# ------------------------------------------------------------------ finding 1

class ClassicReelNotSentTests(unittest.TestCase):
    """1: a classic round whose reel key was not sent is no catch: no
    catch counted, no bait spent, no item handling."""

    def _classic_bot(self, focus_in_press_key: bool):
        _, bot, _ = quiet_bot({"classic_fishing": True, "quick_skip": False, "auto_fish_handling": False,
                               "classic_fishing_delay": 0.05})
        bot.running = True
        bot.bait_counter = 1
        bot.bait_keys = ["1"]
        bot.total_games = 0
        bot._REEL_RETRY_SECONDS = 0.2
        keys = _Keys()
        bot.keyboard_controller = keys
        bot.human.keyboard_controller = keys
        bot._stop_if_window_unavailable = lambda: False
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._refuse_overlapping_windows = lambda: True
        bot._verify_capture = lambda: None
        bot.window_manager.activate_window = lambda force_activate=False: True
        bot.bait_and_cast = lambda: True
        rounds = []

        def wait_for_fish(timeout=40):
            rounds.append(1)
            if len(rounds) > 1:
                bot.running = False     # one round is enough
                return False
            return True

        bot.wait_for_classic_fish = wait_for_fish
        handled = []
        bot.handle_caught_item = lambda *a, **k: handled.append(1)
        bot._wait_before_recast = lambda: False
        bot._focus = lambda force=False: focus_in_press_key
        return bot, keys, handled

    def test_reel_not_sent_counts_nothing(self):
        bot, keys, handled = self._classic_bot(focus_in_press_key=False)
        bot._play_game()
        self.assertEqual(keys.events, [])
        self.assertEqual(bot.total_games, 0)
        self.assertEqual(bot.bait_counter, 1)
        self.assertEqual(handled, [])
        self.assertNotEqual(bot.stop_reason, FishingBot.STOP_BAIT_DEPLETED)

    def test_reel_sent_counts_the_catch(self):
        bot, keys, handled = self._classic_bot(focus_in_press_key=True)
        bot._play_game()
        self.assertTrue(any(e[0] == "press" for e in keys.events))
        self.assertEqual(bot.total_games, 1)
        self.assertEqual(bot.bait_counter, 0)
        self.assertEqual(handled, [1])

    def test_press_key_reports_whether_it_sent(self):
        bot, keys, _ = self._classic_bot(focus_in_press_key=False)
        self.assertFalse(bot.press_key("space"))
        bot._focus = lambda force=False: True
        self.assertTrue(bot.press_key("space"))
        bot.keyboard_controller = None
        self.assertFalse(bot.press_key("space"))


# ------------------------------------------------------------------ finding 2

class JigsawUnreadableConfirmAreaTests(unittest.TestCase):
    """2: with the confirm area unreadable (no baseline) Confirm is not
    clicked: neither after a crate nor after a discard."""

    def _bot(self):
        wm = FakeWindowManager(320, 240)
        wm.rect = (100, 50, 320, 240)
        bot = JigsawBot(wm, {
            "confirm_button_pos": (40, 40),
            "jigsaw_crate_confirm_delay": 0.0,
            "timing_cursor_settle": 0.0,
            "jigsaw_post_crate_piece_wait": 0.0,
            "jigsaw_action_delay": 0.0,
        }, bot_id=0)
        mouse = FakeMouse()
        bot.mouse_backend = mouse
        bot.human.mouse = mouse
        bot.human.sleep = lambda *a, **k: None
        bot.running = True
        bot.capture.grab = lambda *a, **k: None      # every capture fails
        bot._focus = lambda force=False: True
        bot._switched = False
        return bot, mouse

    def test_open_crate_sends_only_the_crate_click(self):
        bot, mouse = self._bot()
        bot._open_crate_and_confirm((80, 60), "normal")
        self.assertIsNone(bot._crate_confirm_baseline)
        self.assertEqual(mouse.clicks, [((180, 110), "left")])

    def test_discard_sends_no_confirm(self):
        bot, mouse = self._bot()
        bot._discard_piece((80, 60), "unknown piece", verify=False)
        self.assertFalse(any(btn == "left" for _, btn in mouse.clicks), mouse.clicks)
        self.assertEqual(bot._check_hand, (80, 60))   # the hand is looked at next round


# ------------------------------------------------------------------ finding 3

class DropFailsWithoutFocusTests(unittest.TestCase):
    """3: a drop that breaks after the pickup and cannot get the window
    back stops this bot and keeps every other bot from clicking."""

    def test_item_left_on_cursor_stops_everything(self):
        game = bench.quick_game("official", dead_fish_chance=1.0, bite_delay=(0.05, 0.08), duration=1.2)
        cfg = {f"inv_page_{i + 1}_pos": list(t) for i, t in enumerate(game.tabs)}
        cfg.update({
            "auto_fish_handling": True,
            "fish_actions": {n: "drop" for n in game._item_names},
            "drop_button_pos": list(game.drop_button),
            "confirm_button_pos": list(game.confirm_button),
            "timing_drop_dialog_timeout": 0.2,
            "timing_recast_wait": 0.0,
            "timing_minigame_timeout": 2.0,
            "quick_skip": False,
            "human_like_clicking": False,
        })
        bot, _ = bench.make_bot(game, cfg)
        real_wait = dialog_watch.wait
        state = {"raised": False}

        def wait(*args, **kwargs):
            if not state["raised"] and game.carrying is not None:
                state["raised"] = True
                raise RuntimeError("capture broke")
            return real_wait(*args, **kwargs)

        real_focus = bot._focus
        bot._focus = lambda force=False: False if state["raised"] else real_focus(force)
        orig = fb.dialog_watch.wait
        fb.dialog_watch.wait = wait
        bot.running = True
        thread = threading.Thread(target=bot.play_game, daemon=True)
        try:
            thread.start()
            deadline = time.time() + 25
            while time.time() < deadline and bot.running:
                time.sleep(0.02)
        finally:
            bot.stop()
            thread.join(5)
            fb.dialog_watch.wait = orig
        self.assertTrue(state["raised"], "the simulated drop never started")
        self.assertIsNotNone(game.carrying)          # the item really is on the cursor
        self.assertEqual(bot.stop_reason, FishingBot.STOP_CURSOR_ITEM)
        self.assertEqual(FishingBot._cursor_hazard[0], bot.bot_id + 1)

    def test_other_bots_send_nothing_while_an_item_is_on_the_cursor(self):
        _, other, _ = quiet_bot()
        other.bot_id = 1
        activated = []
        other.window_manager.activate_window = lambda force_activate=False: activated.append(1) or True
        FishingBot._cursor_hazard = (1, "Tenchi")
        try:
            self.assertFalse(other._focus())
        finally:
            FishingBot._cursor_hazard = None
        self.assertEqual(activated, [])
        self.assertFalse(other.running)
        self.assertEqual(other.stop_reason, FishingBot.STOP_CURSOR_ITEM)

    def test_new_session_clears_it(self):
        FishingBot._cursor_hazard = (1, "Tenchi")
        FishingBot.reset_session_state()
        self.assertIsNone(FishingBot._cursor_hazard)


# ------------------------------------------------------------------ finding 4

@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class QuitWaitsForWorkersTests(GuiFixture):
    """4: Quit waits for a worker to finish its action, diagnostics off."""

    def test_quit_joins_a_worker_in_the_middle_of_a_click(self):
        import qt_gui  # noqa: F401  (GuiFixture imports it)
        w = self._window()
        self.assertFalse(w.config.get("diagnostics_enabled", False))
        left_click = threading.Event()

        class Worker:
            bot_id = 0
            paused = False
            running = True

            def stop(self):
                self.running = False

            def start(self):
                while self.running:
                    time.sleep(0.01)
                time.sleep(0.4)          # the click it was in when stopped
                left_click.set()

        bot = Worker()
        t = threading.Thread(target=bot.start, daemon=True)
        t.start()
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            w.bots = {0: bot}
            w.bot_threads = {0: t}
            w.close()
        finally:
            QMessageBox.question = orig
            w.bots = {}
            w.bot_threads = {}
        self.assertTrue(left_click.is_set(), "close returned while the worker was still clicking")
        self.assertFalse(t.is_alive())


# ------------------------------------------------------------------ finding 6

class PublishGateTests(unittest.TestCase):
    """6: publish needs a passed smoke test for each asset and a clean tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.rd = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _assets(self, v="9.9.9"):
        names = (release.win_asset(v), release.linux_asset(v))
        for n in names + ("SHA256SUMS.txt",):
            with open(os.path.join(self.rd, n), "wb") as f:
                f.write(n.encode())
        return names

    def test_unverified_until_smoke_recorded_and_unchanged(self):
        win, lin = self._assets()
        self.assertEqual(release.unverified_assets(self.rd, (win, lin)), [win, lin])
        release.write_verified(self.rd, {win: release.sha256(os.path.join(self.rd, win))})
        self.assertEqual(release.unverified_assets(self.rd, (win, lin)), [lin])
        release.write_verified(self.rd, {n: release.sha256(os.path.join(self.rd, n)) for n in (win, lin)})
        self.assertEqual(release.unverified_assets(self.rd, (win, lin)), [])
        with open(os.path.join(self.rd, lin), "ab") as f:
            f.write(b"rebuilt without a smoke test")
        self.assertEqual(release.unverified_assets(self.rd, (win, lin)), [lin])

    def test_publish_refuses_before_tagging(self):
        v = "9.9.9"
        self._assets(v)
        notes = os.path.join(self.rd, "notes.md")
        with open(notes, "w") as f:
            f.write("notes")
        sent = []
        saved = {k: getattr(release, k) for k in ("current_version", "release_dir", "out", "consistency", "run")}
        release.current_version = lambda text=None: v
        release.release_dir = lambda _v: self.rd
        release.out = lambda cmd: (0, f'VERSION = "{v}"') if cmd[:2] == ["git", "show"] else (0, "")
        release.consistency = lambda _v: {}
        release.run = lambda cmd, **kw: sent.append(cmd)
        try:
            with self.assertRaises(SystemExit) as cm:
                release.cmd_publish(type("A", (), {"notes_file": notes, "draft": False})())
        finally:
            for k, val in saved.items():
                setattr(release, k, val)
        self.assertIn("smoke test", str(cm.exception))
        self.assertEqual(sent, [])

    def test_worktree_problem(self):
        repo = self.rd
        git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
        git("init", "-q")
        git("config", "user.email", "t@example.invalid")
        git("config", "user.name", "t")
        os.makedirs(os.path.join(repo, "src"))
        with open(os.path.join(repo, "src", "a.py"), "w") as f:
            f.write("x = 1\n")
        git("add", ".")
        git("commit", "-qm", "init")
        saved = release.ROOT
        release.ROOT = repo
        try:
            self.assertIsNone(release.worktree_problem())
            with open(os.path.join(repo, "notes.txt"), "w") as f:
                f.write("untracked outside the build")
            self.assertIsNone(release.worktree_problem())
            with open(os.path.join(repo, "src", "b.py"), "w") as f:
                f.write("y = 2\n")
            self.assertIn("untracked", release.worktree_problem())
            os.remove(os.path.join(repo, "src", "b.py"))
            with open(os.path.join(repo, "src", "a.py"), "w") as f:
                f.write("x = 2\n")
            self.assertIn("uncommitted", release.worktree_problem())
        finally:
            release.ROOT = saved


if __name__ == "__main__":
    unittest.main()

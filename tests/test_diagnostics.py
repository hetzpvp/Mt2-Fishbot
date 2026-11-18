"""Diagnostic log (src/diag.py): a simulated bot session with diagnostics on
must leave exactly one zip next to the (fake) exe that reconstructs the run;
with diagnostics off nothing may be written."""

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _stubs import install, hsv_to_bgr, SimScreen, FakeWindowManager, FakeMouse

install()

import diag  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402

import pytest  # noqa: E402

# One test raises in a thread on purpose (the excepthook must log it).
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")

OFFICIAL_PANEL = hsv_to_bgr(102, 200, 220)
OFFICIAL_FISH = hsv_to_bgr(104, 138, 120)
SRC_REF = re.compile(r"\] [A-Z]+ +[a-z_]+\.py:\d+ \w+ \| ")


class FrozenExe:
    """Fakes a PyInstaller exe living in a temporary folder."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="fishbot_diag_")
        self.exe_dir = os.path.join(self.tmp, "Fishing Puzzle Player")
        os.makedirs(self.exe_dir)
        self.exe = os.path.join(self.exe_dir, "Fishing Puzzle Player v9.exe")
        # The bundle (assets) is the project; diagnostics must not go there
        self.bundle = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._bundle_had_diag = os.path.exists(os.path.join(self.bundle, "diagnostics"))
        self._saved = {k: getattr(sys, k) for k in ("frozen", "executable", "_MEIPASS") if hasattr(sys, k)}
        self._cwd = os.getcwd()
        self.cwd = os.path.join(self.tmp, "somewhere_else")      # must NOT be used either
        os.makedirs(self.cwd)

    def __enter__(self):
        sys.frozen = True
        sys.executable = self.exe
        sys._MEIPASS = self.bundle
        os.chdir(self.cwd)
        return self

    def __exit__(self, *exc):
        os.chdir(self._cwd)
        for k in ("frozen", "_MEIPASS"):
            if k in self._saved:
                setattr(sys, k, self._saved[k])
            elif hasattr(sys, k):
                delattr(sys, k)
        sys.executable = self._saved["executable"]
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    @property
    def diag_dir(self):
        return os.path.join(self.exe_dir, "diagnostics")


def make_bot(screen, config):
    FishingBot._shared_learned_profile = None
    FishingBot._shared_recast_wait = None
    FishingBot._known_profiles = []
    bot = FishingBot(None, dict(config), FakeWindowManager(), bait_counter=10,
                     bait_keys=config.get("bait_keys"), bot_id=0)   # as the GUI passes them
    mouse = FakeMouse()
    bot.human.mouse = mouse
    bot.mouse_backend = mouse
    bot._grab = screen.grab
    # The simulated screen draws no inventory; these tests are about the
    # "inventory full" path, not the check that the inventory is there.
    bot._inventory_visible = lambda: (True, "test: taken as visible")
    logs = []
    bot.on_status_update = logs.append
    return bot, mouse, logs


BOT_CONFIG = {
    "inv_page_1_pos": (700, 120),
    "inv_page_2_pos": (730, 120),
    "bait_keys": ["1", "2"],
    "bait_quantity": 5,
    "timing_recast_wait": 4.0,
}


def run_startup_session(exe: FrozenExe, enabled: bool, verbose: bool = False):
    """GUI-like flow: switch diagnostics, open a session, run one bot until it
    stops (the simulated inventory has no free slot: "inventory full at
    startup"), end the session and wait for the zip."""
    config_path = os.path.join(exe.cwd, "bot_config.json")
    with open(config_path, "w") as f:
        json.dump(BOT_CONFIG, f)
    diag.configure(enabled, verbose)
    diag.log("GUI", "START pressed")
    folder = diag.start_session({"game windows:": ["W1: 'Metin2' (simulated)"]}, dict(BOT_CONFIG),
                                config_path, reason="test")
    bot, mouse, logs = make_bot(SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH), BOT_CONFIG)
    bot.running = True
    t = threading.Thread(target=bot.start, name="FishingBot-W1")
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "bot did not stop"
    done = {}
    diag.end_session("all bots stopped", wait_threads=[t],
                     on_done=lambda z, e: done.update(zip=z, err=e), wait=True)
    return folder, bot, mouse, logs, done


class DiagSessionTests(unittest.TestCase):
    def setUp(self):
        diag.reset_for_tests()

    def tearDown(self):
        diag.reset_for_tests()

    def _read_zip(self, path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            log = zf.read(next(n for n in names if n.endswith("/session.log"))).decode("utf-8")
            files = {n.split("/", 1)[1]: n for n in names}
            return names, files, log, zf

    def test_frozen_session_is_one_zip_next_to_the_exe(self):
        with FrozenExe() as exe:
            folder, bot, mouse, logs, done = run_startup_session(exe, enabled=True)

            # Output goes next to the exe: not the bundle, not the cwd
            self.assertEqual(os.path.dirname(folder), exe.diag_dir)
            self.assertEqual(os.path.exists(os.path.join(exe.bundle, "diagnostics")), exe._bundle_had_diag)
            self.assertFalse(os.path.exists(os.path.join(exe.cwd, "diagnostics")))

            # The bot really stopped for the reason under test
            self.assertEqual(bot.stop_reason, FishingBot.STOP_INVENTORY_FULL)
            self.assertIn("All inventory pages full at startup", bot.stop_message)

            # One zip, folder gone
            self.assertIsNone(done["err"])
            zip_path = done["zip"]
            self.assertEqual(zip_path, folder + ".zip")
            self.assertTrue(os.path.isfile(zip_path))
            self.assertFalse(os.path.exists(folder))
            self.assertEqual(os.listdir(exe.diag_dir), [os.path.basename(zip_path)])

            names, files, log, _ = self._read_zip(zip_path)
            stamp = os.path.basename(folder)
            self.assertTrue(all(n.startswith(stamp + "/") for n in names))
            for required in ("session.log", "bot_config.json", "config_effective.json", "config_gui.json"):
                self.assertIn(required, files)
            pngs = [n for n in files if n.endswith(".png")]
            self.assertTrue(any("startup_full_window" in n for n in pngs), pngs)
            self.assertTrue(any("startup_page1_strip" in n for n in pngs), pngs)
            self.assertTrue(any("no_empty_slots" in n for n in pngs), pngs)
            self.assertTrue(any("inventory_full_stop" in n for n in pngs), pngs)
            self.assertFalse([n for n in files if n.endswith((".jpg", ".jpeg"))])
            with zipfile.ZipFile(zip_path) as zf:
                on_disk = json.loads(zf.read(files["bot_config.json"]))
                effective = json.loads(zf.read(files["config_effective.json"]))
                png = zf.read(files[pngs[0]])
            self.assertEqual(on_disk["inv_page_1_pos"], [700, 120])
            self.assertEqual(effective["W1"]["bait_keys"], ["1", "2"])
            self.assertEqual(effective["W1"]["inventory_pages"]["1"], [700, 120])
            self.assertAlmostEqual(effective["W1"]["timings"]["_t_recast_wait"], 4.0)
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

            # Header
            self.assertIn("===== diagnostic session", log)
            self.assertIn("bot version:", log)
            self.assertIn(f"frozen: True  executable: {exe.exe}", log)
            self.assertIn("os:", log)
            self.assertIn("W1: 'Metin2' (simulated)", log)
            self.assertIn("inv_page_1_pos = [700, 120]", log)
            self.assertIn("events recorded before this session started", log)
            self.assertRegex(log, r"item_templates_loaded = [1-9]\d*")
            self.assertRegex(log, r"empty_slot_template = \[\d+, \d+\]")
            self.assertIn("START pressed", log)
            self.assertIn("bot W1 starts with:", log)
            self.assertIn("capture mode requested=", log)

            # Every line carries time, tag, category and a source reference
            body = [ln for ln in log.splitlines() if ln[:2].isdigit()]
            self.assertTrue(body)
            bot_lines = [ln for ln in body if "[W1]" in ln]
            self.assertTrue(bot_lines)
            for ln in bot_lines[:200]:
                self.assertRegex(ln, r"^\d\d:\d\d:\d\d\.\d{3} \[W1\] ")
            self.assertTrue(SRC_REF.search(log))

            # Actions, captures, analysis (incl. below-threshold scores), decisions
            self.assertRegex(log, r"\[W1\] ACTION +fishing_bot\.py:\d+ _goto_page \| .*inventory page tab at win=\(700, 120\)")
            self.assertRegex(log, r"\[W1\] ACTION +humanize\.py:\d+ \w+ \| .*click left")
            self.assertRegex(log, r"\[W1\] CAPTURE +fishing_bot\.py:\d+ .*inventory strip rel=\(562, 124, 238, 308\)")
            self.assertRegex(log, r"\[W1\] STATE +fishing_bot\.py:\d+ .*inventory strip rect=\(562, 124, 238, 308\) \(from tabs\)")
            self.assertRegex(log, r"\[W1\] DECISION +fishing_bot\.py:\d+ .*page wait: fixed 0\.30s \(only 2 tabs configured\)")
            threshold = FishingBot._EMPTY_SLOT_THRESHOLD
            m = re.search(r"ANALYSIS +fishing_bot\.py:\d+ _scan_empty_slots \| .*empty_slot\.jpg \(\d+x\d+\) "
                          r".*best=(0\.\d+) at .*threshold=" + re.escape(str(threshold)) + r" .*verdict=NO EMPTY SLOT", log)
            self.assertIsNotNone(m, "empty slot score line missing")
            self.assertLess(float(m.group(1)), threshold)
            self.assertIn("empty slot template at other scales", log)
            self.assertRegex(log, r"best score per template: \w+\.jpg=0\.\d+@")
            self.assertRegex(log, r"CALL +\S+ \w+ \| +→ FishingBot\._scan_empty_slots\(array\(308, 238, 3\)")
            self.assertRegex(log, r"← FishingBot\._scan_empty_slots = \[\] \[\d+\.\d ms\]")
            self.assertIn("startup page 1: 0 empty slots -> try the next page", log)
            self.assertRegex(log, r"DECISION +fishing_bot\.py:\d+ _stop_bot \| .*STOP reason=inventory_full")
            self.assertIn("All inventory pages full at startup", log)
            self.assertRegex(log, r"STAT +fishing_bot\.py:\d+ _startup_scan_and_process_all_pages \| "
                                  r".*Startup: checking inventory page 1")
            self.assertIn("bot loop ended: stop_reason=inventory_full", log)
            self.assertIn("===== session end (all bots stopped)", log)

    def test_crashed_session_is_packed_at_next_start(self):
        with FrozenExe() as exe:
            leftover = os.path.join(exe.diag_dir, "20200101_000000")
            os.makedirs(leftover)
            with open(os.path.join(leftover, "session.log"), "w") as f:
                f.write("12:00:00.000 [W1] DECISION fishing_bot.py:1 x | half a session\n")
            with open(os.path.join(leftover, "0001_W1_x.png"), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            packed = []
            diag.recover_leftovers(on_done=packed.extend, wait=True)
            self.assertEqual(packed, [leftover + ".zip"])
            self.assertFalse(os.path.exists(leftover))
            with zipfile.ZipFile(leftover + ".zip") as zf:
                names = zf.namelist()
                self.assertIn("20200101_000000/session.log", names)
                self.assertIn("20200101_000000/RECOVERED.txt", names)
                self.assertIn("20200101_000000/0001_W1_x.png", names)
                self.assertIn(b"half a session", zf.read("20200101_000000/session.log"))

    def test_diagnostics_off_writes_nothing(self):
        with FrozenExe() as exe:
            before = sorted(os.listdir(exe.tmp))
            folder, bot, mouse, logs, done = run_startup_session(exe, enabled=False)
            self.assertIsNone(folder)
            self.assertEqual(done, {})
            self.assertEqual(bot.stop_reason, FishingBot.STOP_INVENTORY_FULL)
            self.assertTrue(mouse.clicks)                       # the bot did its normal work
            self.assertFalse(os.path.exists(exe.diag_dir))
            self.assertFalse(os.path.exists(os.path.join(exe.cwd, "diagnostics")))
            self.assertEqual(sorted(os.listdir(exe.tmp)), before)
            self.assertEqual(sorted(os.listdir(exe.cwd)), ["bot_config.json"])  # written by the test
            self.assertFalse(diag.ENABLED)
            # The GUI callback behaves exactly as before (plain list.append),
            # and the bot runs its original, unwrapped methods
            self.assertIs(bot.on_status_update, bot.__dict__["_on_status_update"])
            self.assertFalse(any(getattr(f, "_diag_traced", False) for f in vars(FishingBot).values()))

    def test_minigame_wait_writes_loop_summary_and_verbose_frames(self):
        with FrozenExe():
            for verbose in (False, True):
                diag.reset_for_tests()
                diag.configure(True, verbose)
                diag.start_session(reason="loop test")
                screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
                bot, mouse, _ = make_bot(screen, {})
                bot.running = True
                self.assertFalse(bot.wait_for_minigame_window(timeout=0.3))
                screen.panel_open = True
                self.assertTrue(bot.wait_for_minigame_window(timeout=1.0))
                bot._TRACK_MIN_SPAN = float("inf")
                with diag.loop("minigame", tag="W1"):
                    for _ in range(60):
                        screen.t += 0.02
                        bot.atomic_capture_and_click()   # counts its frame into the loop
                path = diag.current_log_path()
                diag.end_session("done", wait=True)
                with zipfile.ZipFile(diag.last_zip_path()) as zf:
                    log = zf.read(next(n for n in zf.namelist() if n.endswith("session.log"))).decode()
                self.assertIn("LOOP     ", log)
                self.assertRegex(log, r"LOOP +fishing_bot\.py:\d+ wait_for_minigame_window \| .*end wait for "
                                      r"minigame frames=\d+ .*result='timeout'")
                self.assertRegex(log, r"end wait for minigame .*result='found\+calibrated")
                self.assertIn("calibrated: region=GameRegion(left=260, top=150, width=280, height=220)", log)
                self.assertRegex(log, r"end minigame frames=60 .*fish_seen=\d+")
                self.assertRegex(log, r"DECISION .*click fish seen at \(\d+, \d+\), aimed at")
                self.assertIn("no_minigame", log)            # negative image of the timed-out wait
                self.assertIn("first_minigame_calibrated", log)
                frame_lines = re.findall(r"frame: fish \(\d+, \d+\) (?:INSIDE|outside)", log)
                if verbose:
                    self.assertTrue(frame_lines, "verbose must log every frame")
                else:
                    self.assertFalse(frame_lines, "per-frame lines only with verbose")
                    self.assertNotIn("→ FishDetector.detect_window_and_fish", log)
                self.assertIsNotNone(path)


class DiagLoggerTests(unittest.TestCase):
    def setUp(self):
        diag.reset_for_tests()

    def tearDown(self):
        diag.reset_for_tests()

    def test_not_frozen_writes_into_the_project_root(self):
        self.assertFalse(getattr(sys, "frozen", False))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(diag.program_dir(), root)

    def test_unwritable_program_folder_falls_back_to_user_folder(self):
        with FrozenExe() as exe:
            user = os.path.join(exe.tmp, "user_state")
            orig_can_write, orig_user_dir = diag._can_write, diag.user_dir
            diag._can_write = lambda path: path.startswith(user) and orig_can_write(path)
            diag.user_dir = lambda: os.path.join(user, "diagnostics")
            try:
                diag.configure(True)
                folder = diag.start_session(reason="fallback")
                self.assertTrue(folder.startswith(os.path.join(user, "diagnostics")))
                where, fallback = diag.location()
                self.assertTrue(fallback)
                self.assertIn("per-user fallback", open(diag.current_log_path()).read())
                diag.end_session("done", wait=True)
                self.assertTrue(diag.latest_zip().startswith(os.path.join(user, "diagnostics")))
            finally:
                diag._can_write, diag.user_dir = orig_can_write, orig_user_dir

    def test_retention_keeps_the_last_ten_sessions(self):
        with FrozenExe() as exe:
            os.makedirs(exe.diag_dir)
            for day in range(1, 15):
                with open(os.path.join(exe.diag_dir, f"202001{day:02d}_120000.zip"), "wb") as f:
                    f.write(b"zip")
            os.makedirs(os.path.join(exe.diag_dir, "20200115_120000"))
            with open(os.path.join(exe.diag_dir, "keep_me.txt"), "w") as f:
                f.write("not ours")
            diag.prune(keep=10)
            left = sorted(os.listdir(exe.diag_dir))
            self.assertIn("keep_me.txt", left)
            sessions = [n for n in left if n != "keep_me.txt"]
            self.assertEqual(len(sessions), 10)
            self.assertEqual(sessions[0], "20200106_120000.zip")
            self.assertIn("20200115_120000", sessions)

    def test_logging_failure_never_reaches_the_bot(self):
        with FrozenExe():
            diag.configure(True)
            diag.start_session(reason="failure test")
            orig = diag._write

            def broken(*a, **k):
                raise OSError("disk full")
            diag._write = broken
            try:
                bot, mouse, _ = make_bot(SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH), BOT_CONFIG)
                bot.running = True
                bot._startup_scan_and_process_all_pages()
                self.assertEqual(bot.stop_reason, FishingBot.STOP_INVENTORY_FULL)
                self.assertGreater(diag.failures(), 0)
            finally:
                diag._write = orig
            diag.end_session("done", wait=True)

    def test_uncaught_thread_exception_is_logged(self):
        with FrozenExe():
            diag.configure(True)
            diag.start_session(reason="hook test")

            def boom():
                raise ValueError("thread went boom")
            t = threading.Thread(target=boom, name="worker-x")
            t.start()
            t.join()
            log = open(diag.current_log_path()).read()
            self.assertIn("UNCAUGHT exception in thread worker-x: ValueError: thread went boom", log)
            self.assertIn('raise ValueError("thread went boom")', log)
            try:
                raise KeyError("caught here")
            except KeyError:
                diag.exc("handled")
            log = open(diag.current_log_path()).read()
            self.assertRegex(log, r"EXC +test_diagnostics\.py:\d+ test_uncaught_thread_exception_is_logged \| "
                                  r"handled: KeyError")
            diag.end_session("done", wait=True)

    def test_ending_twice_while_recovering_gives_one_valid_zip(self):
        import numpy as np
        with FrozenExe() as exe:
            diag.configure(True)
            folder = diag.start_session(reason="race test")
            for i in range(20):
                diag.image(f"img{i}", np.random.default_rng(i).integers(0, 255, (200, 300, 3), np.uint8), tag="W1")
            diag.end_session("first")
            self.assertIsNone(diag.end_session("second"))     # another window closing
            diag.recover_leftovers(wait=True)                 # GUI start scan at the same time
            diag.wait_for_background()
            self.assertEqual(sorted(os.listdir(exe.diag_dir)), [os.path.basename(folder) + ".zip"])
            with zipfile.ZipFile(folder + ".zip") as zf:
                self.assertIsNone(zf.testzip())
                self.assertEqual(sum(n.endswith(".png") for n in zf.namelist()), 20)

    def test_class_tracing_only_installed_while_on(self):
        name = "_scan_empty_slots"
        self.assertFalse(getattr(vars(FishingBot)[name], "_diag_traced", False))
        diag.configure(True)
        self.assertTrue(getattr(vars(FishingBot)[name], "_diag_traced", False))
        diag.configure(False)
        self.assertFalse(getattr(vars(FishingBot)[name], "_diag_traced", False))

    def test_image_cap(self):
        import numpy as np
        with FrozenExe():
            diag.configure(True)
            diag.start_session(reason="cap test")
            frame = np.zeros((8, 8, 3), np.uint8)
            saved = [diag.image("same", frame, tag="W1") for _ in range(diag.MAX_IMAGES_PER_LABEL + 5)]
            self.assertEqual(sum(1 for s in saved if s), diag.MAX_IMAGES_PER_LABEL)
            self.assertIn("per-label cap reached for 'same'", open(diag.current_log_path()).read())
            diag.end_session("done", wait=True)

    def test_disabled_logger_is_a_no_op(self):
        calls = []

        @diag.traced
        def f(x):
            calls.append(x)
            return x * 2

        self.assertEqual(f(3), 6)
        self.assertIs(diag.loop("x"), diag._NULL_LOOP)
        self.assertIsNone(diag.start_session(reason="off"))
        diag.log("GUI", "nothing")
        self.assertEqual(len(diag._pre_buffer), 0)
        self.assertEqual(calls, [3])


try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class DiagGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import qt_gui
        cls.qt_gui = qt_gui
        cls.app = QApplication.instance() or QApplication([])
        for name in ("information", "warning", "critical"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    def setUp(self):
        diag.reset_for_tests()
        from window_manager import WindowManager
        self._orig = WindowManager.get_all_windows
        WindowManager.get_all_windows = staticmethod(lambda: [])
        self.windows = []

    def tearDown(self):
        from window_manager import WindowManager
        for w in self.windows:
            w._stop_global_hotkey_listener()
            w._poll.stop()
            w.deleteLater()
        WindowManager.get_all_windows = self._orig
        diag.reset_for_tests()

    def _window(self):
        w = self.qt_gui.FishbotWindow()
        self.windows.append(w)
        return w

    def test_settings_row_and_session_zip(self):
        with FrozenExe() as exe:
            w = self._window()
            s = w.settings
            self.assertFalse(s.diag_check.isChecked())
            self.assertFalse(s.diag_verbose_check.isEnabled())
            self.assertIn(exe.diag_dir, s.diag_path_label.text())
            self.assertFalse(diag.ENABLED)

            s.diag_check.setChecked(True)
            self.assertTrue(s.diag_verbose_check.isEnabled())
            s.diag_verbose_check.setChecked(True)
            with open(w.config_path) as f:
                saved = json.load(f)
            self.assertTrue(saved["diagnostics_enabled"])
            self.assertTrue(saved["diagnostics_verbose"])
            self.assertTrue(diag.ENABLED and diag.VERBOSE)

            # A run: session opens, a bot stops, its row keeps the reason
            w._begin_diag_session("fishing", [(0, "Metin2")], {}, dict(w.config))
            self.assertTrue(diag.session_active())

            class Bot:
                stop_reason = "inventory_full"
                stop_message = "All inventory pages full at startup — stopping bot"

            bot = Bot()
            w.bots[0] = bot
            w._append_debug_status("[W1] Startup: checking inventory page 4")
            row = w.dashboard.window_rows[0]
            self.assertEqual(row.msg_lbl.text(), "Startup: checking inventory page 4")
            done = threading.Event()
            w._sig_diag_done.connect(lambda *_a: done.set())
            w._on_bot_stopped_ui(0, bot)
            w._append_debug_status("[W1] Bot finished! Total games: 0")   # arrives after the stop
            self.assertEqual(row.msg_lbl.text(), Bot.stop_message)
            self.assertIn("STOPPED", row.pill.text())

            diag.wait_for_background()
            deadline = time.time() + 10
            while not done.is_set() and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertIn(".zip", s.diag_last_label.text())
            zips = [n for n in os.listdir(exe.diag_dir) if n.endswith(".zip")]
            self.assertEqual(len(zips), 1)
            with zipfile.ZipFile(os.path.join(exe.diag_dir, zips[0])) as zf:
                log = zf.read(next(n for n in zf.namelist() if n.endswith("session.log"))).decode()
            self.assertIn("config file:", log)
            self.assertIn("screens (as Qt sees them", log)
            self.assertIn("W1: 'Metin2' NOT OPEN", log)
            self.assertIn("setting diagnostics_verbose: False -> True", log)
            self.assertIn("W1 stopped: reason=inventory_full", log)

            # The next start clears the reason
            row.setMessage("", lock=False)
            w.bots[0] = bot
            w._append_debug_status("[W1] Bot started! Bait: 800")
            self.assertEqual(row.msg_lbl.text(), "Bot started! Bait: 800")

            # Show log window opens on the latest zip
            w.show_log_window()
            self.assertIn(zips[0], w._log_dialog.source_label.text())
            self.assertIn("W1 stopped", w._log_dialog.text.toPlainText())
            w._log_dialog.close()

            # Off again: no session on the next run
            s.diag_check.setChecked(False)
            self.assertFalse(diag.ENABLED)
            w._begin_diag_session("fishing", [], {}, dict(w.config))
            self.assertFalse(diag.session_active())


if __name__ == "__main__":
    unittest.main()

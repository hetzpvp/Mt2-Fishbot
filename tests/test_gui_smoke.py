"""Offscreen smoke test of the Qt GUI: construction, config round trip,
profiles, remembered windows/bait and stop-notification handling."""

import json
import os
import tempfile
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from _stubs import install

install()

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False

if HAS_QT:
    import qt_gui
    from window_manager import WindowManager


class _FakeWin:
    def __init__(self, hwnd):
        self._hWnd = hwnd


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class GuiFixture(unittest.TestCase):
    """The window and a temporary config folder; no tests of its own."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Message boxes would block an offscreen run
        for name in ("information", "warning", "critical"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.open_windows = ["Metin2 (1)", "Metin2 (2)"]
        self._orig = WindowManager.get_all_windows
        WindowManager.get_all_windows = staticmethod(
            lambda: [(n, _FakeWin(100 + i)) for i, n in enumerate(self.open_windows)])
        self.windows = []

    def tearDown(self):
        for w in self.windows:
            w._stop_global_hotkey_listener()
            w._poll.stop()
            w.deleteLater()
        # Really delete them now: windows left over from earlier tests are
        # restyled by every new window's setStyleSheet (seconds per test).
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        WindowManager.get_all_windows = self._orig
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _window(self):
        w = qt_gui.FishbotWindow()
        self.windows.append(w)
        return w

    def _saved(self):
        with open("bot_config.json") as f:
            return json.load(f)



@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class GuiSmokeTests(GuiFixture):

    def test_builds_and_round_trips_settings(self):
        w = self._window()
        self.assertTrue(w.settings.adaptive_check.isChecked())
        self.assertTrue(w.settings.window_capture_check.isChecked())
        self.assertFalse(w.settings.forget_profile_btn.isEnabled())

        w.settings.adaptive_check.setChecked(False)
        w.settings.window_capture_check.setChecked(False)
        w.settings.human_check.setChecked(True)
        saved = self._saved()
        self.assertFalse(saved["adaptive_detection"])
        self.assertEqual(saved["capture_backend"], "screen")
        self.assertTrue(saved["human_like_clicking"])
        self.assertFalse(os.path.exists("bot_config.json.tmp"))

        w2 = self._window()
        self.assertFalse(w2.settings.adaptive_check.isChecked())
        self.assertFalse(w2.settings.window_capture_check.isChecked())
        self.assertEqual(w2.config["timing_minigame_timeout"], 6.0)

    def test_timing_modal_has_new_sliders(self):
        w = self._window()
        modal = qt_gui.TimingModal(w.config, w)
        self.assertEqual(modal._sliders["timing_minigame_timeout"].value(), 6000)
        self.assertEqual(modal._sliders["timing_recast_wait"].value(), 4000)
        got = {}
        modal.saved.connect(got.update)
        modal._sliders["timing_minigame_timeout"].setValue(12000)
        modal._save()
        self.assertEqual(got["timing_minigame_timeout"], 12.0)

    def test_window_choice_and_bait_survive_game_not_running(self):
        w = self._window()
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (2)")
        self.assertEqual(self._saved()["selected_windows"][0], "Metin2 (2)")
        w.window_stats[0]["bait"] = 321          # as if a session used some bait
        w.save_config()
        self.assertEqual(self._saved()["window_bait"][0], 321)

        # Next launch: the game is not open yet
        self.open_windows = []
        w2 = self._window()
        self.assertEqual(w2.dashboard.window_rows[0].selectedWindow(), "")
        w2.settings.human_check.setChecked(True)           # any save
        saved = self._saved()
        self.assertEqual(saved["selected_windows"][0], "Metin2 (2)")
        self.assertEqual(saved["window_bait"][0], 321)

        # Game started, user refreshes
        self.open_windows = ["Metin2 (1)", "Metin2 (2)"]
        w2.refresh_window_list()
        self.assertEqual(w2.dashboard.window_rows[0].selectedWindow(), "Metin2 (2)")
        self.assertEqual(w2.window_stats[0]["bait"], 321)

        # Neither an unrelated setting nor a capacity change refills counted
        # bait (only Reset Bait does)
        w2.settings.sound_check.setChecked(False)
        self.assertEqual(w2.window_stats[0]["bait"], 321)
        w2.settings.bait_checks["4"].setChecked(False)
        self.assertEqual(w2.window_stats[0]["bait"], 321)

        # Explicitly clearing the slot is remembered too
        w2.dashboard.window_rows[0].combo.setCurrentIndex(0)
        self.assertEqual(self._saved()["selected_windows"][0], "")

    def test_profile_save_and_load(self):
        w = self._window()
        w.dashboard.window_rows[1].setSelectedWindow("Metin2 (1)")
        w.settings.human_check.setChecked(True)
        w.config["confirm_button_pos"] = (10, 20)
        w._save_profile_file("p.json")

        w.settings.human_check.setChecked(False)
        w.config["confirm_button_pos"] = None
        w.dashboard.window_rows[1].combo.setCurrentIndex(0)
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (2)")

        w._load_profile_file("p.json")
        self.assertTrue(w.settings.human_check.isChecked())
        self.assertEqual(w.config["confirm_button_pos"], (10, 20))
        self.assertEqual(w.dashboard.window_rows[0].selectedWindow(), "")
        self.assertEqual(w.dashboard.window_rows[1].selectedWindow(), "Metin2 (1)")

        with open("bad.json", "w") as f:
            f.write("{nope")
        w._load_profile_file("bad.json")                    # must not raise or change state
        self.assertTrue(w.settings.human_check.isChecked())

    def test_corrupt_config_is_kept_aside(self):
        with open("bot_config.json", "w") as f:
            f.write('{"human_like_clicking": tr')
        w = self._window()
        self.assertTrue(os.path.exists("bot_config.json.corrupt"))
        self.assertFalse(w.config["human_like_clicking"])

    def test_learned_profile_is_stored_and_forgotten(self):
        w = self._window()
        profile = {"name": "learned", "window_lower": [50, 100, 100], "window_upper": [70, 255, 255],
                   "fish_lower": [0, 100, 100], "fish_upper": [8, 255, 255], "learned": True}
        w._on_profile_learned_ui(profile)
        self.assertEqual(self._saved()["detection_profile"]["name"], "learned")
        self.assertTrue(w.settings.forget_profile_btn.isEnabled())
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)   # "Forget the learned colors?"
        try:
            w.settings.forget_profile.emit()
        finally:
            QMessageBox.question = orig
        self.assertIsNone(self._saved()["detection_profile"])
        self.assertFalse(w.settings.forget_profile_btn.isEnabled())

    def test_discarded_profile_is_cleared(self):
        w = self._window()
        w._on_profile_learned_ui({"name": "learned", "window_lower": [50, 100, 100],
                                  "window_upper": [70, 255, 255], "learned": True})
        w._on_profile_learned_ui(None)        # the bot found it does not work
        self.assertIsNone(self._saved()["detection_profile"])
        self.assertFalse(w.settings.forget_profile_btn.isEnabled())

    def test_recast_tuning_setting_round_trips(self):
        w = self._window()
        self.assertTrue(w.settings.recast_tune_check.isChecked())
        w.settings.recast_tune_check.setChecked(False)
        self.assertFalse(self._saved()["adaptive_recast_wait"])
        self.assertFalse(self._window().settings.recast_tune_check.isChecked())

    def _jigsaw_mode(self, w):
        # Never build (or load) the real solver tables in a GUI test
        qt_gui.SolverCacheManager._cache_exists = classmethod(lambda cls: True)
        qt_gui.SolverCacheManager.ensure_build = lambda self: None
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w.dashboard.window_rows[1].setSelectedWindow("Metin2 (2)")
        w.mode_bar._on_click(qt_gui.MODE_JIGSAW)       # as a click on JIGSAW PUZZLE
        return w.jigsaw

    def test_mode_switch_relabels_and_is_remembered(self):
        w = self._window()
        self.assertEqual(w.mode, qt_gui.MODE_FISHING)
        self.assertIn("START FISHING", w.dashboard.start_btn.text())
        self._jigsaw_mode(w)
        self.assertEqual(w.mode, qt_gui.MODE_JIGSAW)
        self.assertIn("START JIGSAW", w.dashboard.start_btn.text())
        self.assertIn("Crates", w.dashboard.window_rows[0].bait_lbl.text())
        self.assertTrue(w.inventory.coord_pills["drop"].isLocked())       # fishing-only: greyed, not hidden
        self.assertTrue(w.inventory.coord_pills["drop"].isVisibleTo(w.inventory))
        self.assertFalse(w.inventory.coord_pills["confirm"].isLocked())   # shared
        self.assertFalse(w.inventory.coord_pills["confirm"].isOptional())  # the crate dialog needs it
        w.tabs_bar.setActive("activity")
        self.assertIs(w.stack.currentWidget().widget(), w.jigsaw_tab)
        self.assertEqual(self._saved()["ui_mode"], "jigsaw")
        self.assertEqual(self._window().mode, qt_gui.MODE_JIGSAW)     # restored on the next start
        w.mode_bar._on_click(qt_gui.MODE_FISHING)
        self.assertIs(w.stack.currentWidget().widget(), w.settings.fishing_page)
        self.assertFalse(w.inventory.coord_pills["drop"].isLocked())
        self.assertIn("Bait", w.dashboard.window_rows[0].bait_lbl.text())

    def test_readiness_chips_follow_the_setup(self):
        w = self._window()
        self._jigsaw_mode(w)
        chips = w.dashboard.ready_chips.chips
        self.assertEqual(list(chips), ["windows", "grid", "confirm", "solver"])
        self.assertEqual(chips["grid"].property("state"), "bad")
        w._on_grid_confirmed([10, 20, 264, 176])
        self.assertEqual(chips["grid"].property("state"), "ok")
        self.assertIn("x=10", w.jigsaw_tab.grid_info_label.text())
        chips["confirm"].click()                                  # opens the JIGSAW tab
        self.assertEqual(w.tabs_bar.active(), "activity")

    def test_jigsaw_rows_follow_the_workers(self):
        w = self._window()
        runner = self._jigsaw_mode(w)
        rows = w.dashboard.window_rows

        class Worker:
            running, paused, boards_completed = True, False, 2

            def _on_f5_pressed(self, paused):
                self.paused = paused
                runner.paused_changed.emit(0, paused)

            def stop(self):
                self.running = False

        a, b = Worker(), Worker()
        runner.bots = {0: a, 1: b}
        w._on_activity_started(qt_gui.MODE_JIGSAW)                # as start() does
        self.assertFalse(w.mode_bar._buttons[qt_gui.MODE_FISHING].isEnabled())   # can't switch now
        self.assertFalse(w.jigsaw_tab.define_grid_btn.isEnabled())
        self.assertFalse(rows[0].combo.isEnabled())
        w.set_mode(qt_gui.MODE_FISHING)                           # refused while running
        self.assertEqual(w.mode, qt_gui.MODE_JIGSAW)

        runner.status_line.emit(0, "[W1] Place jigsaw piece (action=4) at (166, 216)")
        self.assertEqual(rows[0].msg_lbl.text(), "Place jigsaw piece (action=4) at (166, 216)")
        runner.progress_line.emit(0, 7)
        self.assertEqual(rows[0].bait_lbl.text(), "Crates 7")
        self.assertEqual(rows[0].games_lbl.text(), "Boards 2")
        self.assertEqual(w.dashboard.kpi_games._value.text(), "7")

        b.paused = True                                           # one worker paused itself
        runner.paused_changed.emit(1, True)
        self.assertIn("PAUSED", rows[1].pill.text())
        self.assertIn("RUNNING", rows[0].pill.text())
        self.assertIn("RESUME", w.dashboard.start_btn.text())
        w.dashboard.start_btn.click()                             # Resume, not Stop
        self.assertFalse(b.paused)
        self.assertIn("PAUSE", w.dashboard.start_btn.text())

        w.dashboard.stop_btn.click()
        self.assertFalse(a.running)
        self.assertEqual(rows[0].msg_lbl.text(), "Stopped by user")
        runner.stopped.emit(0, a)
        self.assertIn("STOPPED", rows[0].pill.text())
        self.assertFalse(w.jigsaw_tab.define_grid_btn.isEnabled())    # one is still running
        runner.stopped.emit(1, b)
        self.assertTrue(w.jigsaw_tab.define_grid_btn.isEnabled())
        self.assertTrue(w.mode_bar._buttons[qt_gui.MODE_FISHING].isEnabled())
        self.assertIn("START JIGSAW", w.dashboard.start_btn.text())

    def test_fishing_cannot_start_while_jigsaw_runs(self):
        w = self._window()
        runner = self._jigsaw_mode(w)
        runner.bots = {0: object()}
        w.start_all_bots()
        self.assertFalse(w.bots)
        runner.bots = {}

    def test_interface_size_fits_the_screen(self):
        # Logical screen area (after OS scaling) -> scale chosen at startup
        self.assertEqual(qt_gui.choose_ui_scale("auto", 2560, 1440), 1.25)    # 1440p at 100 %
        self.assertEqual(qt_gui.choose_ui_scale("auto", 1920, 1032), 1.15)    # 1080p at 100 %
        self.assertEqual(qt_gui.choose_ui_scale("auto", 1536, 824), 1.0)      # 1080p at 125 %
        self.assertEqual(qt_gui.choose_ui_scale("auto", 2560, 1392), 1.25)    # 4K at 150 %
        self.assertEqual(qt_gui.choose_ui_scale("1.75", 2560, 1440), 1.75)
        self.assertEqual(qt_gui.choose_ui_scale("1.75", 1920, 1032), 1.35)    # never bigger than the screen
        self.assertEqual(qt_gui.choose_ui_scale("1.0", 3840, 2160), 1.0)
        # Screens too small for the window at 100 %: smaller, so START and the
        # footer stay on screen (the window is frameless and fixed-size)
        self.assertEqual(qt_gui.choose_ui_scale("junk", 1366, 728), 1.0)      # 1366x768 laptop: fits
        self.assertEqual(qt_gui.choose_ui_scale("auto", 1280, 690), 1.0)      # 1080p at 150 %: fits
        self.assertEqual(qt_gui.choose_ui_scale("1.5", 1280, 690), 1.0)
        self.assertEqual(qt_gui.choose_ui_scale("auto", 1280, 600), 0.85)     # smaller still
        self.assertEqual(qt_gui.choose_ui_scale("auto", 800, 500), qt_gui.MIN_UI_SCALE)
        for w, h in ((1366, 728), (1280, 690), (1280, 600), (1536, 824), (1920, 1032)):
            s = qt_gui.choose_ui_scale("auto", w, h)
            self.assertLessEqual(qt_gui.WINDOW_H * s, h)
            self.assertLessEqual(qt_gui.WINDOW_W * s, w)

    def test_interface_size_setting_round_trips(self):
        w = self._window()
        self.assertEqual(w.settings.ui_scale_combo.currentData(), "auto")
        w.settings.ui_scale_combo.setCurrentIndex(w.settings.ui_scale_combo.findData("1.5"))
        self.assertEqual(self._saved()["ui_scale"], "1.5")
        self.assertIn("restart", w.settings.ui_scale_note.text())
        self.assertEqual(qt_gui._saved_ui_scale_setting(), "1.5")
        self.assertEqual(self._window().settings.ui_scale_combo.currentData(), "1.5")

    def test_jigsaw_crate_priority_is_saved(self):
        w = self._window()
        self._jigsaw_mode(w)
        radios = w.jigsaw_tab.priority_radios
        self.assertTrue(radios["normal_first"].isChecked())
        radios["special_first"].setChecked(True)
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "special_first")
        radios["special_when_better"].setChecked(True)
        self.assertFalse(radios["special_first"].isChecked())     # one choice only
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "special_when_better")
        radios["normal_first"].setChecked(True)
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "normal_first")
        self.assertTrue(self._window().jigsaw_tab.priority_radios["normal_first"].isChecked())

    def test_stale_stop_notification_is_ignored(self):
        w = self._window()

        class Bot:
            stop_reason = "manual"

        old, new = Bot(), Bot()
        w.bots[0] = new
        w._on_bot_stopped_ui(0, old)          # late signal from the previous run
        self.assertIs(w.bots.get(0), new)
        w._on_bot_stopped_ui(0, new)
        self.assertNotIn(0, w.bots)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class SetupCheckGuiTests(GuiFixture):
    """Setting a position runs its check and remembers the window size."""

    def setUp(self):
        super().setUp()
        from _stubs import InventoryScreen
        self.screen = InventoryScreen(highlight=False)
        self._rect = WindowManager.get_window_rect
        WindowManager.get_window_rect = lambda _self: (0, 0, self.screen.W, self.screen.H)
        self.shown = []
        for name in ("information", "warning"):
            setattr(QMessageBox, name, staticmethod(
                lambda _parent, title, *a, **k: self.shown.append(title) or QMessageBox.Ok))

    def tearDown(self):
        WindowManager.get_window_rect = self._rect
        for name in ("information", "warning"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
        super().tearDown()

    def _capture(self, w, key, pos):
        w._capture_window_name = "Metin2 (1)"
        w._on_captured(pos[0], pos[1], key)

    def test_sizes_are_remembered_and_a_resize_is_asked_about(self):
        w = self._window()
        w._grab_with = lambda _wm: (self.screen.grab, lambda: None)
        self._capture(w, "page1", self.screen.TABS[1])
        self.assertEqual(self._saved()[qt_gui.setup_checks.SIZES_KEY]["inv_page_1_pos"], [800, 700])
        asked = []
        QMessageBox.question = staticmethod(lambda *a, **k: asked.append(a[1]) or QMessageBox.No)
        names = {n: win for n, win in WindowManager.get_all_windows()}
        self.assertTrue(w.windows_have_same_size(["Metin2 (1)"], names,
                                                 positions=qt_gui.setup_checks.FISHING_POSITIONS))
        WindowManager.get_window_rect = lambda _self: (0, 0, 1024, 768)
        self.assertFalse(w.windows_have_same_size(["Metin2 (1)"], names,
                                                  positions=qt_gui.setup_checks.FISHING_POSITIONS))
        self.assertEqual(asked, ["Window size changed"])

    def test_same_tab_twice_and_worn_armor_are_caught(self):
        w = self._window()
        w._grab_with = lambda _wm: (self.screen.grab, lambda: None)
        for page in (1, 2, 3):
            self._capture(w, f"page{page}", self.screen.TABS[page])
        self._capture(w, "page4", (self.screen.TABS[1][0] + 2, self.screen.TABS[1][1]))
        self.assertIn("Same tab twice", self.shown)
        x, y = self.screen.EQUIPMENT[0]
        self._capture(w, "armor", (x + 16, y + 16))
        self.assertIn("Not a spare armor in the inventory?", self.shown)

    def test_unreadable_tabs_are_said_once(self):
        w = self._window()
        w._grab_with = lambda _wm: (self.screen.grab, lambda: None)
        for page in (1, 2, 3, 4):
            self._capture(w, f"page{page}", self.screen.TABS[page])
            w._check_tab_after_click(page, WindowManager())
        self.assertEqual(self.shown.count("Open tab not recognized"), 1)

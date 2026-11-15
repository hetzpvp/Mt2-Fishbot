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
class GuiSmokeTests(unittest.TestCase):
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

        # Unrelated setting does not refill the bait; changing capacity does
        w2.settings.sound_check.setChecked(False)
        self.assertEqual(w2.window_stats[0]["bait"], 321)
        w2.settings.bait_checks["4"].setChecked(False)
        self.assertEqual(w2.window_stats[0]["bait"], 600)

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
        w.settings.forget_profile.emit()
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

    def _jigsaw_dialog(self, w):
        # Never build (or load) the real solver tables in a GUI test
        qt_gui.SolverCacheManager._cache_exists = classmethod(lambda cls: True)
        qt_gui.SolverCacheManager.ensure_build = lambda self: None
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w.dashboard.window_rows[1].setSelectedWindow("Metin2 (2)")
        w.open_jigsaw_solver()
        return w._jigsaw_dialog

    def test_jigsaw_dialog_rows_follow_the_workers(self):
        w = self._window()
        dialog = self._jigsaw_dialog(w)
        self.assertEqual(sorted(dialog._rows), [0, 1])
        self.assertFalse(w.dashboard.start_btn.isEnabled())     # fishing is locked meanwhile

        class Worker:
            running, paused, boards_completed = True, False, 2

        a, b = Worker(), Worker()
        dialog._bots = {0: a, 1: b}
        dialog._set_setup_enabled(False)                        # as _start() does
        dialog._append_status(0, "[W1] Place jigsaw piece (action=4) at (166, 216)")
        self.assertEqual(dialog._rows[0]["row"].status_lbl.text(), "Place jigsaw piece (action=4) at (166, 216)")
        dialog._set_progress(0, 7)
        self.assertEqual(dialog._rows[0]["row"].crates_lbl.text(), "C 7")
        self.assertEqual(dialog._rows[0]["row"].boards_lbl.text(), "B 2")

        b.paused = True                                         # one worker paused itself
        dialog._on_paused_changed(1, True)
        self.assertIn("PAUSED", dialog._rows[1]["row"].pill.text())
        self.assertIn("RUNNING", dialog._rows[0]["row"].pill.text())
        self.assertIn("Resume", dialog.start_btn.text())
        dialog._toggle()                                        # Resume, not Stop
        self.assertFalse(b.paused)
        self.assertIn("Stop", dialog.start_btn.text())

        dialog._on_stopped(0, a)
        self.assertIn("STOPPED", dialog._rows[0]["row"].pill.text())
        self.assertFalse(dialog.define_grid_btn.isEnabled())    # one is still running
        dialog._on_stopped(1, b)
        self.assertTrue(dialog.define_grid_btn.isEnabled())
        self.assertIn("Start", dialog.start_btn.text())

        dialog.close()
        self.assertTrue(w.dashboard.start_btn.isEnabled())

    def test_jigsaw_crate_priority_is_saved(self):
        w = self._window()
        dialog = self._jigsaw_dialog(w)
        self.assertFalse(dialog.special_first_check.isChecked())
        dialog.special_first_check.setChecked(True)
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "special_first")
        dialog.special_first_check.setChecked(False)
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "normal_first")
        # "Only where it saves rounds" and "first" exclude each other
        dialog.special_smart_check.setChecked(True)
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "special_when_better")
        dialog.special_first_check.setChecked(True)
        self.assertFalse(dialog.special_smart_check.isChecked())
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "special_first")
        dialog.special_smart_check.setChecked(True)
        self.assertFalse(dialog.special_first_check.isChecked())
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "special_when_better")
        dialog.special_smart_check.setChecked(False)
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "normal_first")
        dialog.close()

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

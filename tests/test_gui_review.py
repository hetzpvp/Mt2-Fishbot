"""Offscreen tests of the GUI review fixes: no stuck UI after a failed start,
one bot per window, setup wizards (real state, highlight flags), tolerant
config loading, capture handling, stop reasons, accent, debug window host."""

import json
import os
import sys
import threading
import time
import types
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
    from test_gui_smoke import GuiFixture
else:  # pragma: no cover
    GuiFixture = unittest.TestCase


def _write_config(cfg: dict) -> None:
    with open("bot_config.json", "w") as f:
        json.dump(cfg, f)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class StartRobustnessTests(GuiFixture):

    def setUp(self):
        super().setUp()
        self.warnings = []
        QMessageBox.warning = staticmethod(lambda _p, title, *a, **k: self.warnings.append(title) or QMessageBox.Ok)
        self._orig_bot = qt_gui.FishingBot
        qt_gui.FishingBot = type("FishingBot", (), {"reset_session_state": staticmethod(lambda: None)})

    def tearDown(self):
        qt_gui.FishingBot = self._orig_bot
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        super().tearDown()

    def _ready_window(self):
        w = self._window()
        w.config["inv_page_1_pos"] = (10, 10)
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        return w

    def test_start_with_a_closed_window_does_not_lock_the_ui(self):
        w = self._ready_window()
        self.open_windows = []                  # the client was closed after the last Refresh
        w.start_all_bots()
        self.assertEqual(w.bots, {})
        self.assertIn("Window not found", self.warnings)
        self.assertTrue(w.settings.human_check.isEnabled())          # settings not locked
        self.assertFalse(w.inventory.coord_pills["page1"].isLocked())
        self.assertTrue(w.mode_bar._buttons[qt_gui.MODE_JIGSAW].isEnabled())
        self.assertIn("NOT OPEN", w.dashboard.window_rows[0].pill.text())
        self.assertIn("START FISHING", w.dashboard.start_btn.text())

    def test_one_window_cannot_be_used_in_two_slots(self):
        w = self._ready_window()
        rows = w.dashboard.window_rows
        rows[1].setSelectedWindow("Metin2 (1)")          # already W1's
        self.assertEqual(rows[1].selectedWindow(), "")
        self.assertEqual(w.notice.current(), "dup")
        # ... and the list greys it out
        idx = rows[1].combo.findData("Metin2 (1)")
        self.assertFalse(rows[1].combo.model().item(idx).isEnabled())

    def test_double_click_on_start_does_not_pause(self):
        w = self._window()
        started = []
        w.dashboard.start_clicked.connect(lambda: (started.append(1), w.dashboard.setRunning(True)))
        paused = []
        w.dashboard.stop_clicked.connect(lambda: paused.append(1))
        w.dashboard.start_btn.click()
        w.dashboard.start_btn.click()                    # second half of a double click
        self.assertEqual((len(started), len(paused)), (1, 0))
        w.dashboard._last_click -= 1.0                   # a real click later pauses
        w.dashboard.start_btn.click()
        self.assertEqual(len(paused), 1)

    def test_readiness_names_the_right_tabs(self):
        w = self._window()
        items = {k: tip for k, _l, _s, tip in w._readiness_items()}
        self.assertIn("POSITIONS", items["pages"])
        w.settings.bait_checks["1"].setChecked(False)
        for k in ("2", "3", "4"):
            w.settings.bait_checks[k].setChecked(False)
        items = {k: tip for k, _l, _s, tip in w._readiness_items()}
        self.assertIn("FISHING tab", items["bait"])
        self.assertIn("Not ready", w.dashboard.status_big.text())


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class ConfigRobustnessTests(GuiFixture):

    def test_wrong_value_types_fall_back_to_defaults(self):
        _write_config({"bait_quantity": "abc", "accent_color": 5, "human_like_clicking": "yes",
                       "inv_page_1_pos": "garbage", "timing_post_click": "0.05",
                       "bait_keys": ["1", "Q"], "classic_fishing_delay": "nan"})
        w = self._window()                      # used to crash at start
        self.assertEqual(w.config["bait_quantity"], 200)
        self.assertEqual(w.config["accent_color"], qt_gui.DEFAULT_ACCENT)
        self.assertFalse(w.config["human_like_clicking"])
        self.assertIsNone(w.config["inv_page_1_pos"])
        self.assertAlmostEqual(w.config["timing_post_click"], 0.05)   # numeric text is fine
        self.assertEqual(w.config["bait_keys"], ["1"])
        self.assertEqual(w.config["classic_fishing_delay"], 3.0)
        self.assertIn("config", w.notice.keys())
        w.notice.clear("welcome")
        self.assertEqual(w.notice.current(), "config")
        self.assertIn("bait_quantity", w.notice.text.text())

    def test_corrupt_config_is_announced(self):
        with open("bot_config.json", "w") as f:
            f.write("{not json")
        w = self._window()
        self.assertIn("config", w.notice.keys())
        w.notice.clear("welcome")
        self.assertIn(".corrupt", w.notice.text.text())

    def test_save_failure_is_shown(self):
        w = self._window()
        w.config_path = os.path.join(os.getcwd(), "missing-dir", "bot_config.json")
        w.settings.human_check.setChecked(True)
        self.assertEqual(w.notice.current(), "save")
        self.assertTrue(w.config["human_like_clicking"])       # kept in memory
        w.config_path = os.path.join(os.getcwd(), "bot_config.json")
        w.settings.human_check.setChecked(False)
        self.assertNotEqual(w.notice.current(), "save")

    def test_invalid_numbers_are_clamped(self):
        w = self._window()
        w.settings.bait_qty_input.setText("0")
        w.settings._on_changed()
        self.assertEqual(self._saved()["bait_quantity"], 1)
        w.settings.classic_delay_input.setText("")
        w.settings._on_changed()
        self.assertEqual(self._saved()["classic_fishing_delay"], 3.0)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class WizardTests(GuiFixture):

    def test_first_start_highlights_the_fishing_wizard_until_opened(self):
        w = self._window()                       # no config file: first start
        self.assertTrue(w.mode_bar.wizardHighlighted())
        self.assertEqual(w.notice.current(), "welcome")
        wiz = w.open_wizard()
        self.assertEqual(wiz.mode, qt_gui.MODE_FISHING)
        self.assertFalse(w.mode_bar.wizardHighlighted())
        self.assertTrue(self._saved()["wizard_done_fishing"])
        wiz.close()
        # First switch to Jigsaw: its own wizard is highlighted
        w.mode_bar._on_click(qt_gui.MODE_JIGSAW)
        self.assertTrue(w.mode_bar.wizardHighlighted())
        self.assertIn("Jigsaw", w.mode_bar.wizard_btn.text())
        w.mode_bar.wizard_btn.click()
        self.assertFalse(w.mode_bar.wizardHighlighted())
        self.assertEqual(w._wizard.mode, qt_gui.MODE_JIGSAW)
        w._wizard.close()
        w.mode_bar._on_click(qt_gui.MODE_FISHING)
        self.assertFalse(w.mode_bar.wizardHighlighted())
        self.assertFalse(self._window().mode_bar.wizardHighlighted())   # remembered

    def test_existing_setups_are_not_highlighted(self):
        _write_config({"inv_page_1_pos": [5, 5]})     # saved before the wizards existed
        w = self._window()
        self.assertFalse(w.mode_bar.wizardHighlighted())
        self.assertNotEqual(w.notice.current(), "welcome")
        w.mode_bar._on_click(qt_gui.MODE_JIGSAW)
        self.assertTrue(w.mode_bar.wizardHighlighted())        # no grid yet: never set up

    def test_fishing_wizard_drives_the_real_settings(self):
        w = self._window()
        wiz = w.open_wizard()
        self.assertEqual(wiz.stepNames(), ["Welcome", "Windows", "Inventory", "Bait", "Catch", "Quick skip", "Ready"])
        # Windows step: ticking selects the window in a dashboard slot
        wiz._win_boxes["Metin2 (2)"].setChecked(True)
        self.assertEqual(w.selected_window_names(), ["Metin2 (2)"])
        wiz._win_boxes["Metin2 (1)"].setChecked(True)
        self.assertEqual(sorted(w.selected_window_names()), ["Metin2 (1)", "Metin2 (2)"])
        wiz._win_boxes["Metin2 (2)"].setChecked(False)
        self.assertEqual(w.selected_window_names(), ["Metin2 (1)"])
        # Bait step: the boxes are the settings
        wiz_box = next(b for b, t in wiz._binds if t is w.settings.bait_checks["F1"])
        wiz_box.setChecked(True)
        self.assertIn("F1", self._saved()["bait_keys"])
        w.settings.bait_checks["F1"].setChecked(False)       # and the other way round
        self.assertFalse(wiz_box.isChecked())
        wiz._qty.setText("150")
        wiz._qty_done()
        self.assertEqual(self._saved()["bait_quantity"], 150)
        # Inventory step: a pill shows a captured position
        w._capture_window_name = "Metin2 (1)"
        from window_manager import WindowManager
        orig = WindowManager.get_window_rect
        WindowManager.get_window_rect = lambda _s: (0, 0, 800, 600)
        try:
            w._check_tab_capture = lambda *a: None
            w._on_captured(40, 50, "page1")
        finally:
            WindowManager.get_window_rect = orig
        self.assertEqual(wiz._pills["page1"].coords(), (40, 50))
        # Ready step lists what is set
        wiz._go(len(wiz.stepNames()) - 1)
        self.assertIn("Inventory pages", wiz._status["ready"].text())
        wiz._next()                                            # Finish -> Dashboard
        self.assertEqual(w.tabs_bar.active(), "dashboard")

    def test_jigsaw_wizard_has_its_own_steps(self):
        w = self._window()
        w.mode_bar._on_click(qt_gui.MODE_JIGSAW)
        wiz = w.open_wizard()
        self.assertEqual(wiz.stepNames(), ["Welcome", "Windows", "Puzzle grid", "Confirm", "Crates", "Ready"])
        self.assertFalse(wiz._pills["confirm"].isOptional())
        wiz._radios["special_first"].setChecked(True)
        self.assertEqual(self._saved()["jigsaw_crate_priority"], "special_first")
        self.assertTrue(w.jigsaw_tab.priority_radios["special_first"].isChecked())
        w._on_grid_confirmed([10, 20, 264, 176])
        self.assertIn("grid is set", wiz._status["grid"].text())
        wiz.close()


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class CaptureAndRowTests(GuiFixture):

    def test_clicking_a_set_pill_recaptures_and_the_x_clears(self):
        w = self._window()
        pill = w.inventory.coord_pills["page1"]
        pill.setCoords((3, 4))
        asked = []
        w.inventory.set_coord.disconnect()
        w.inventory.set_coord.connect(asked.append)
        cleared = []
        w.inventory.unset_coord.disconnect()
        w.inventory.unset_coord.connect(cleared.append)
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        ev = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        pill.mousePressEvent(ev)
        self.assertEqual((asked, cleared), (["page1"], []))   # not cleared by a click any more
        pill._clear_btn.click()
        self.assertEqual(cleared, ["page1"])

    def test_capture_banner_and_cancel(self):
        class Listener:
            def __init__(self, on_click=None):
                self.on_click = on_click

            def start(self):
                pass

            def stop(self):
                pass
        fake_mouse = types.SimpleNamespace(Listener=Listener,
                                           Button=types.SimpleNamespace(left="L", right="R"))
        orig, qt_gui.pyn_mouse = qt_gui.pyn_mouse, fake_mouse
        self.addCleanup(setattr, qt_gui, "pyn_mouse", orig)
        w = self._window()
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w._begin_capture("page2")
        self.assertTrue(w.inventory.capture_banner.isVisibleTo(w.inventory))
        self.assertIn("tab 2", w.inventory.capture_text.text())
        self.assertIn("Esc", w.inventory.capture_text.text())
        w._capture.signals.cancelled.emit()                     # right click in the game
        self.assertFalse(w._capture.is_active())
        self.assertFalse(w.inventory.capture_banner.isVisibleTo(w.inventory))
        w._begin_capture("page2")
        w._on_capture_timeout()
        self.assertFalse(w._capture.is_active())
        self.assertEqual(w.notice.current(), "capture")

    def test_jigsaw_error_reason_survives_the_stopped_message(self):
        w = self._window()
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w.mode_bar._on_click(qt_gui.MODE_JIGSAW)
        runner = w.jigsaw
        bot = types.SimpleNamespace(stop=lambda: None, paused=False)
        runner.bots = {0: bot}
        w._on_activity_started(qt_gui.MODE_JIGSAW)
        runner.status_line.emit(0, "[W1] Jigsaw bot stopped by an error: ValueError: boom")
        runner.status_line.emit(0, "[W1] Jigsaw bot stopped")
        runner.stopped.emit(0, bot)
        row = w.dashboard.window_rows[0]
        self.assertIn("ValueError: boom", row.msg_lbl.text())
        self.assertEqual(row.msg_lbl.property("kind"), "error")

    def test_hung_jigsaw_worker_is_released_after_stop(self):
        w = self._window()
        w.mode_bar._on_click(qt_gui.MODE_JIGSAW)
        runner = w.jigsaw
        runner.STOP_GRACE_MS = 1
        hung = types.SimpleNamespace(stop=lambda: None, paused=False)
        runner.bots = {0: hung}
        w._on_activity_started(qt_gui.MODE_JIGSAW)
        runner.stop()                                  # the worker never reports back
        deadline = time.monotonic() + 2
        while runner.is_running() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertFalse(runner.is_running())
        self.assertTrue(w.mode_bar._buttons[qt_gui.MODE_FISHING].isEnabled())

    def test_open_all_fish_keeps_the_items(self):
        modal = qt_gui.FishModal({"Gold_item.jpg": "keep"})
        modal._open_all()
        self.assertEqual(modal._actions["Carp_living.jpg"], "open")
        self.assertEqual(modal._actions["Gold_item.jpg"], "keep")

    def test_all_eight_window_slots_are_always_shown(self):
        w = self._window()
        rows = w.dashboard.window_rows
        self.assertEqual(len(rows), qt_gui.MAX_WINDOWS)
        self.assertTrue(all(r.isVisibleTo(w.dashboard) for r in rows))
        self.assertFalse(hasattr(w.dashboard, "add_window_btn"))


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class LookTests(GuiFixture):

    def tearDown(self):
        qt_gui.set_accent(qt_gui.DEFAULT_ACCENT)
        super().tearDown()

    def test_accent_derives_hover_and_readable_text(self):
        qt_gui.set_accent("#202060")
        self.assertEqual(qt_gui.C["on_accent"], "#ffffff")      # dark accent: white text
        qss = qt_gui.build_qss()
        self.assertNotIn("#ffc645", qss)                        # no amber hover left
        self.assertIn(qt_gui.C["accent_hover"], qss)
        qt_gui.set_accent("not a color")
        self.assertEqual(qt_gui.C["accent"], qt_gui.DEFAULT_ACCENT)
        self.assertEqual(qt_gui.C["on_accent"], "#1a1108")

    def test_log_dialog_timer_stops_on_escape(self):
        w = self._window()
        w.show_log_window()
        dlg = w._log_dialog
        self.assertTrue(dlg._timer.isActive())
        dlg.reject()                                            # what Esc does
        self.assertFalse(dlg._timer.isActive())
        self.assertIsNone(w._log_dialog)

    def test_input_method_is_in_settings_not_the_header(self):
        w = self._window()
        self.assertTrue(w.settings.input_label.text().startswith("Input method:"))
        self.assertFalse(hasattr(w.header, "setInputBackend"))


class DebugHostTests(unittest.TestCase):
    """TkDebugHost with a fake tkinter: every Tk call happens on its thread."""

    def setUp(self):
        self.calls = []
        test = self

        class FakeTk:
            def __init__(self):
                self.thread = threading.current_thread()
                self._after = []
                self._quit = False
                test.root = self

            def withdraw(self):
                pass

            def after(self, _ms, fn):
                self._after.append(fn)

            def mainloop(self):
                while not self._quit:
                    if self._after:
                        self._after.pop(0)()
                    time.sleep(0.005)

            def quit(self):
                self._quit = True

            def destroy(self):
                pass

        self.fake = types.ModuleType("tkinter")
        self.fake.Tk = FakeTk
        self._orig = sys.modules.get("tkinter")
        sys.modules["tkinter"] = self.fake

    def tearDown(self):
        if self._orig is not None:
            sys.modules["tkinter"] = self._orig
        else:
            sys.modules.pop("tkinter", None)

    def test_windows_are_made_and_closed_on_the_tk_thread(self):
        from debug_host import TkDebugHost
        test = self

        class Win:
            def __init__(self, root, bot):
                test.calls.append(("make", bot, threading.current_thread() is root.thread))

            def destroy(self):
                test.calls.append(("destroy", threading.current_thread() is test.root.thread))

        host = TkDebugHost()
        self.assertTrue(host.start())
        host.open(("fishing", 0), [Win, None], "bot0")
        host.close(("fishing", 0))
        host.stop()
        host._thread.join(timeout=2)
        self.assertFalse(host._thread.is_alive())
        self.assertEqual(self.calls, [("make", "bot0", True), ("destroy", True)])


if __name__ == "__main__":
    unittest.main()

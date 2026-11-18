"""Offscreen regression tests of the 2026-11-17 UI corner-case audit:
Enter / Esc in the dialogs, Refresh while a bot runs, profile load applying
the activity and diagnostics, the jigsaw grid overlay."""

import json
import os
import time
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from _stubs import install

install()

try:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False

if HAS_QT:
    import diag
    import qt_gui
    from test_gui_smoke import GuiFixture
else:  # pragma: no cover
    GuiFixture = unittest.TestCase


class _FakeBot:
    """Runs until stopped; enough of FishingBot for start / stop."""
    STOP_MANUAL = "manual"
    STOP_BAIT_DEPLETED = "bait_depleted"

    @staticmethod
    def reset_session_state():
        pass

    def __init__(self, *_a, bot_id=0, **_k):
        self.bot_id = bot_id
        self.paused = False
        self.running = True

    def start(self):
        while self.running:
            time.sleep(0.01)

    def stop(self):
        self.running = False


class _FakeWM:
    def __init__(self, w=640, h=480):
        self.size = (w, h)

    def get_window_rect(self):
        return (0, 0, *self.size)

    def activate_window(self, **_k):
        pass


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class DialogKeyTests(GuiFixture):

    def test_enter_in_the_wizard_does_not_close_it(self):
        w = self._window()
        wiz = w.open_wizard(qt_gui.MODE_FISHING)
        wiz._go(wiz.stepNames().index("Bait"))
        closed = []
        wiz.finished.connect(lambda *_: closed.append(1))
        wiz.destroyed.connect(lambda *_: closed.append(1))
        wiz._qty.setFocus()
        wiz._qty.setText("300")
        QTest.keyClick(wiz._qty, Qt.Key_Return)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(closed, [])
        self.assertIs(w._wizard, wiz)
        self.assertEqual(w.settings.bait_qty_input.text(), "300")   # Enter still commits it

    def test_esc_closes_the_wizard_and_keeps_a_typed_quantity(self):
        w = self._window()
        wiz = w.open_wizard(qt_gui.MODE_FISHING)
        wiz._go(wiz.stepNames().index("Bait"))
        wiz._qty.setText("555")             # typed, no Enter
        QTest.keyClick(wiz, Qt.Key_Escape)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertIsNone(w._wizard)
        self.assertEqual(w.settings.bait_qty_input.text(), "555")
        self.assertEqual(self._saved()["bait_quantity"], 555)

    def test_no_auto_default_buttons_in_the_modals(self):
        w = self._window()
        from PySide6.QtWidgets import QPushButton
        for dlg in (qt_gui.TimingModal(w.config, w), qt_gui.FishModal({}, w),
                    w.open_wizard(qt_gui.MODE_JIGSAW)):
            self.assertFalse([b for b in dlg.findChildren(QPushButton) if b.autoDefault()],
                             type(dlg).__name__)
            dlg.close()


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class RunningRefreshTests(GuiFixture):

    def setUp(self):
        super().setUp()
        self._orig_bot = qt_gui.FishingBot
        qt_gui.FishingBot = _FakeBot

    def tearDown(self):
        qt_gui.FishingBot = self._orig_bot
        super().tearDown()

    def test_refresh_while_fishing_keeps_the_running_rows(self):
        w = self._window()
        w.config["inv_page_1_pos"] = (10, 10)
        rows = w.dashboard.window_rows
        rows[0].setSelectedWindow("Metin2 (1)")
        rows[1].setSelectedWindow("Metin2 (2)")
        w.start_all_bots()
        self.assertEqual(sorted(w.bots), [0, 1])
        self.assertFalse(w.dashboard.refresh_btn.isEnabled())
        # Client 1 closed: the other one is renamed "Metin2" by the next listing
        self.open_windows = ["Metin2"]
        w.refresh_window_list()
        self.assertEqual(rows[0].selectedWindow(), "Metin2 (1)")
        self.assertEqual(rows[1].selectedWindow(), "Metin2 (2)")
        self.assertIn("RUNNING", rows[0].pill.text())
        self.assertIn("RUNNING", rows[1].pill.text())
        w.stop_all_bots()
        self.assertTrue(w.dashboard.refresh_btn.isEnabled())


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class ProfileLoadTests(GuiFixture):

    def setUp(self):
        super().setUp()
        self._diag_enabled = diag.ENABLED

    def tearDown(self):
        diag.configure(self._diag_enabled, False)
        super().tearDown()

    def test_profile_applies_its_activity_and_diagnostics(self):
        w = self._window()
        w.set_mode(qt_gui.MODE_FISHING)
        prof = dict(w._snapshot_config())
        prof["ui_mode"] = qt_gui.MODE_JIGSAW
        prof["diagnostics_enabled"] = not diag.ENABLED
        with open("p.json", "w") as f:
            json.dump(prof, f)
        w._load_profile_file(os.path.abspath("p.json"))
        self.assertEqual(w.mode, qt_gui.MODE_JIGSAW)
        self.assertEqual(self._saved()["ui_mode"], qt_gui.MODE_JIGSAW)
        self.assertEqual(diag.ENABLED, prof["diagnostics_enabled"])

    def test_profile_load_refused_while_jigsaw_runs(self):
        w = self._window()
        w.jigsaw.bots[0] = object()
        try:
            warnings = []
            orig = QMessageBox.warning
            QMessageBox.warning = staticmethod(lambda _p, title, *a, **k: warnings.append(title))
            try:
                w._load_profile_file("does-not-matter.json")
            finally:
                QMessageBox.warning = orig
            self.assertEqual(warnings, ["Profile"])
        finally:
            w.jigsaw.bots.clear()


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class GridOverlayTests(GuiFixture):

    def test_grid_saved_on_a_bigger_window_is_pulled_inside(self):
        w = self._window()
        w.config["jigsaw_grid_bounds"] = [900, 700, 264, 176]
        w._open_grid_overlay(_FakeWM(640, 480), "Metin2 (1)")
        x, y, gw, gh = w._grid_overlay._grid
        self.assertLessEqual(x + gw, 640)
        self.assertLessEqual(y + gh, 480)
        w._grid_overlay.close()

    def test_overlay_closes_with_the_program(self):
        w = self._window()
        w._open_grid_overlay(_FakeWM(), "Metin2 (1)")
        ov = w._grid_overlay
        w.close()
        self.assertFalse(ov.isVisible())

    def test_start_refused_while_the_overlay_is_open(self):
        w = self._window()
        w._open_grid_overlay(_FakeWM(), "Metin2 (1)")
        started = []
        w.start_all_bots = lambda: started.append("fishing")
        w.jigsaw.start = lambda: started.append("jigsaw")
        w._on_start_clicked()
        self.assertEqual(started, [])
        w._grid_overlay.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        w._on_start_clicked()
        self.assertEqual(started, ["fishing"])


if __name__ == "__main__":
    unittest.main()

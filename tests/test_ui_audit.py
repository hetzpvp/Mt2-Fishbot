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
        diag.configure(self._diag_enabled)
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


class NativeToLogicalTests(unittest.TestCase):
    """Diag 20261121_173554: interface size 115 % on a 1080p screen (Qt
    devicePixelRatio 1.15); the overlay took the window rect in pixels as Qt
    units, so the saved grid was ~15 % too small and off the puzzle."""

    def test_scaled_screen(self):
        rect, scale, (dx, dy) = qt_gui.native_rect_to_logical(569, 67, 1296, 839, [(0, 0, 1670, 939, 1.15)])
        self.assertEqual(rect, (494, 58, 1128, 730))
        self.assertEqual(scale, 1.15)
        # The overlay corner lies just before the window corner (rounded
        # outwards, so the overlay covers the whole window).
        self.assertAlmostEqual(dx, 494 * 1.15 - 569)
        self.assertAlmostEqual(dy, 58 * 1.15 - 67)
        self.assertTrue(-1.15 < dx <= 0 and -1.15 < dy <= 0)

    def test_unscaled_screen_is_unchanged(self):
        self.assertEqual(qt_gui.native_rect_to_logical(569, 67, 1296, 839, [(0, 0, 1920, 1080, 1.0)]),
                         ((569, 67, 1296, 839), 1.0, (0.0, 0.0)))

    def test_second_screen_scales_from_its_own_corner(self):
        screens = [(0, 0, 1670, 939, 1.15), (1920, 0, 1670, 939, 1.15)]
        rect, scale, _ = qt_gui.native_rect_to_logical(2035, 115, 1150, 805, screens)
        self.assertEqual(rect, (2020, 100, 1000, 700))
        self.assertEqual(scale, 1.15)

    def test_no_screens(self):
        self.assertEqual(qt_gui.native_rect_to_logical(10, 20, 300, 200, []),
                         ((10, 20, 300, 200), 1.0, (0.0, 0.0)))


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class ScaledGridOverlayTests(GuiFixture):
    """The overlay at interface size 115 %: drags in Qt units move and size
    the grid in game-window pixels, and the saved grid is in pixels."""

    def _drag(self, ov, start, end):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent
        for kind, pos, buttons in ((QEvent.MouseButtonPress, start, Qt.LeftButton),
                                   (QEvent.MouseMove, end, Qt.LeftButton),
                                   (QEvent.MouseButtonRelease, end, Qt.NoButton)):
            ev = QMouseEvent(kind, QPointF(*pos), QPointF(*pos), Qt.LeftButton, buttons, Qt.NoModifier)
            {QEvent.MouseButtonPress: ov.mousePressEvent, QEvent.MouseMove: ov.mouseMoveEvent,
             QEvent.MouseButtonRelease: ov.mouseReleaseEvent}[kind](ev)

    def test_grid_handled_in_window_pixels(self):
        orig = qt_gui.JigsawGridOverlay.__dict__["_screens"]
        qt_gui.JigsawGridOverlay._screens = staticmethod(lambda: [(0, 0, 1670, 939, 1.15)])
        try:
            ov = qt_gui.JigsawGridOverlay(569, 67, 1296, 839, [521, 361, 192, 128], "METIN2")
        finally:
            qt_gui.JigsawGridOverlay._screens = orig
        got = []
        ov.confirmed.connect(got.append)
        dx, dy = ov._offset
        # The resize handle (bottom-right corner, pixel (713, 489)) in Qt units.
        handle = ((713 - dx) / 1.15, (489 - dy) / 1.15)
        self._drag(ov, handle, (handle[0] + 20, handle[1] + 20))
        # Then move it: the middle of the grid, 10 Qt units left and up.
        mid = ((521 + 107 - dx) / 1.15, (361 + 75 - dy) / 1.15)
        self._drag(ov, mid, (mid[0] - 10, mid[1] - 10))
        QTest.keyClick(ov, Qt.Key_Return)
        self.assertEqual(got, [[round(521 - 11.5), round(361 - 11.5), 215, 151]])


if __name__ == "__main__":
    unittest.main()

"""Settings → Detection bypass → Disguise in Task Manager: saved, restored, the
restart note, the disguised window title and icon, and Restart now."""

import os
import sys
import unittest
from unittest import mock

from test_gui_smoke import GuiFixture, HAS_QT

if HAS_QT:
    import disguise
    import qt_gui


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class DisguiseSettingTests(GuiFixture):

    def test_off_by_default_and_round_trips(self):
        w = self._window()
        self.assertFalse(w.settings.disguise_check.isChecked())
        self.assertFalse(w.settings.restart_btn.isVisibleTo(w.settings))
        w.settings.disguise_check.setChecked(True)
        self.assertTrue(self._saved()[disguise.CONFIG_KEY])
        self.assertTrue(self._window().settings.disguise_check.isChecked())

    def test_source_run_says_it_needs_the_downloaded_program(self):
        w = self._window()
        w.settings.disguise_check.setChecked(True)
        self.assertIn("downloaded program", w.settings.disguise_note.text())
        self.assertFalse(w.settings.restart_btn.isVisibleTo(w.settings))

    def test_packaged_program_asks_for_restart(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            w = self._window()
            w.settings.disguise_check.setChecked(True)
            self.assertIn("Restart", w.settings.disguise_note.text())
            self.assertTrue(w.settings.restart_btn.isVisibleTo(w.settings))
            w.settings.disguise_check.setChecked(False)
            self.assertFalse(w.settings.restart_btn.isVisibleTo(w.settings))

    def test_disguised_window_uses_the_name(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.dict(os.environ, {disguise.ENV_NAME: "Cedar Journal",
                                             "FISHBOT_CONFIG_DIR": os.getcwd()}):
            w = self._window()
            self.assertEqual(w.windowTitle(), "Cedar Journal")
            w.settings.disguise_check.setChecked(True)
            self.assertIn("Cedar Journal", w.settings.disguise_note.text())
            self.assertFalse(w.settings.restart_btn.isVisibleTo(w.settings))
            w.settings.disguise_check.setChecked(False)   # turned off while disguised
            self.assertTrue(w.settings.restart_btn.isVisibleTo(w.settings))

    def test_normal_window_title(self):
        self.assertEqual(self._window().windowTitle(), "Fishing Puzzle Player")

    def test_failed_disguise_is_shown(self):
        with mock.patch.object(disguise, "LAST_ERROR", "OSError: disk full"), \
                mock.patch.object(sys, "frozen", True, create=True):
            w = self._window()
            w.settings.disguise_check.setChecked(True)
            w2 = self._window()
            shown = " ".join(label.text() for label in w2.notice.findChildren(qt_gui.QLabel))
            self.assertIn("disk full", shown)
            self.assertIn("disk full", w2.settings.disguise_note.text())

    def test_restart_now_closes_then_flags_restart(self):
        w = self._window()
        w.show()
        w.restart_program()
        self.assertTrue(w._restart_on_close)
        self.assertFalse(w.isVisible())

    def test_restart_cancelled_while_running(self):
        w = self._window()
        w.show()
        w.bots = {1: mock.Mock()}
        with mock.patch.object(qt_gui.QMessageBox, "question", return_value=qt_gui.QMessageBox.No):
            w.restart_program()
        self.assertFalse(w._restart_on_close)
        self.assertTrue(w.isVisible())
        w.bots = {}


if __name__ == "__main__":
    unittest.main()

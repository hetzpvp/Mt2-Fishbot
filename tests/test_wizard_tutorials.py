"""Offscreen tests of the 2026-11-17 UI/UX audit: the setup wizard's step
chips, Quick skip step, running lock, window limit, Esc handling; Armor
quick skip readiness; the tutorial pictures (armor = 1 column x 2 rows)."""

import os
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from _stubs import install, InventoryScreen

install()

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QImage, QKeyEvent, QPainter
    from PySide6.QtWidgets import QMessageBox
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False

if HAS_QT:
    import help_visuals
    import qt_gui
    from test_gui_smoke import GuiFixture
else:  # pragma: no cover
    GuiFixture = unittest.TestCase

import numpy as np
import setup_checks


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class WizardTests(GuiFixture):

    def _chip(self, wiz, name):
        num, _txt = wiz._chips[wiz.stepNames().index(name)]
        return num.property("state"), num.text()

    def test_skipped_steps_are_not_ticked(self):
        w = self._window()
        wiz = w.open_wizard()
        wiz._go(wiz.stepNames().index("Ready"))
        self.assertEqual(self._chip(wiz, "Welcome"), ("done", "✓"))      # nothing to set there
        self.assertEqual(self._chip(wiz, "Inventory"), ("missing", "!"))  # skipped, Page 1 not set
        self.assertEqual(self._chip(wiz, "Windows"), ("missing", "!"))
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w._refresh_readiness()
        self.assertEqual(self._chip(wiz, "Windows"), ("done", "✓"))
        wiz.close()

    def test_a_step_chip_jumps_to_its_step(self):
        w = self._window()
        wiz = w.open_wizard()
        num, _ = wiz._chips[3]
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF
        ev = QMouseEvent(QEvent.MouseButtonRelease, QPointF(2, 2), QPointF(2, 2), Qt.LeftButton,
                         Qt.NoButton, Qt.NoModifier)
        wiz.eventFilter(num, ev)
        self.assertEqual(wiz.step(), 3)
        wiz.close()

    def test_quick_skip_step_drives_the_fishing_tab(self):
        w = self._window()
        wiz = w.open_wizard()
        wiz._qs_radios["armor"].setChecked(True)
        self.assertTrue(w.settings.qs_enable.isChecked())
        self.assertTrue(w.settings.qs_armor.isChecked())
        saved = self._saved()
        self.assertTrue(saved["quick_skip"])
        self.assertEqual(saved["quick_skip_mode"], "armor")
        self.assertFalse(wiz._armor_box.isHidden())
        self.assertIn("Spare Armor is not set", wiz._status["quick_skip"].text())
        wiz._qs_radios["horse"].setChecked(True)
        self.assertEqual(self._saved()["quick_skip_mode"], "horse")
        self.assertTrue(wiz._armor_box.isHidden())
        wiz._qs_radios["off"].setChecked(True)
        self.assertFalse(self._saved()["quick_skip"])
        w.settings.qs_enable.setChecked(True)            # and the other way round
        self.assertTrue(wiz._qs_radios["horse"].isChecked())
        wiz.close()

    def test_ready_step_follows_the_wizard_activity_not_the_window_mode(self):
        w = self._window()
        wiz = w.open_wizard()                            # Fishing wizard
        w.mode_bar._on_click(qt_gui.MODE_JIGSAW)          # window switches while it stays open
        wiz._go(len(wiz.stepNames()) - 1)
        text = wiz._status["ready"].text()
        self.assertIn("Inventory pages", text)
        self.assertNotIn("Puzzle grid", text)
        wiz.close()

    def test_the_wizard_is_read_only_while_a_bot_runs(self):
        w = self._window()
        wiz = w.open_wizard()
        w.bots[0] = object()
        wiz.refresh()
        self.assertFalse(wiz.lock_banner.isHidden())
        self.assertFalse(wiz.stack.isEnabled())
        w.bots.clear()
        wiz.refresh()
        self.assertTrue(wiz.lock_banner.isHidden())
        self.assertTrue(wiz.stack.isEnabled())
        wiz.close()

    def test_ticking_one_window_too_many_keeps_the_others(self):
        self.open_windows = [f"Metin2 ({i})" for i in range(1, qt_gui.MAX_WINDOWS + 2)]
        w = self._window()
        w.refresh_window_list()
        wiz = w.open_wizard()
        wiz._fill_windows()
        later = self.open_windows[1:]                    # MAX_WINDOWS of them
        for n in later:
            wiz._win_boxes[n].setChecked(True)
        wiz._win_boxes[self.open_windows[0]].setChecked(True)   # the first in the list, one too many
        self.assertEqual(sorted(w.selected_window_names()), sorted(later))
        self.assertFalse(wiz._win_boxes[self.open_windows[0]].isChecked())
        wiz.close()

    def test_esc_during_a_capture_cancels_the_capture_not_the_wizard(self):
        w = self._window()
        wiz = w.open_wizard()
        cancelled = []
        w._capture.is_active = lambda: True
        w._cancel_capture = lambda: cancelled.append(True)
        wiz.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertEqual(cancelled, [True])
        self.assertTrue(wiz.isVisible())
        del w._capture.is_active
        wiz.close()

    def test_a_typed_bait_quantity_is_kept_when_the_wizard_closes(self):
        w = self._window()
        wiz = w.open_wizard()
        wiz._qty.setText("321")                          # no Enter, no focus change
        wiz.close()
        self.assertEqual(self._saved()["bait_quantity"], 321)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class ArmorQuickSkipReadinessTests(GuiFixture):

    def _items(self, w):
        return {k: (state, tip) for k, _l, state, tip in w._readiness_items()}

    def test_armor_mode_without_a_spare_armor_is_flagged(self):
        w = self._window()
        self.assertNotIn("armor", self._items(w))        # quick skip off: not listed
        w.config.update(quick_skip=True, quick_skip_mode="armor")
        self.assertEqual(self._items(w)["armor"][0], "bad")
        self.assertIn("Spare Armor", self._items(w)["armor"][1])
        w.config["armor_slot_pos"] = (640, 330)
        self.assertEqual(self._items(w)["armor"][0], "ok")   # one page tab or none: page not needed
        for page, pos in {1: (10, 10), 2: (40, 10)}.items():
            w.config[f"inv_page_{page}_pos"] = pos
        self.assertEqual(self._items(w)["armor"][0], "bad")  # several pages: its page must be known
        w.config["armor_slot_page"] = 2
        self.assertEqual(self._items(w)["armor"][0], "ok")
        w.config["quick_skip_mode"] = "horse"
        self.assertNotIn("armor", self._items(w))

    def test_start_asks_before_running_armor_mode_without_a_spare_armor(self):
        w = self._window()
        w.config["inv_page_1_pos"] = (10, 10)
        w.settings.qs_enable.setChecked(True)
        w.settings.qs_armor.setChecked(True)
        w.settings.bait_checks["1"].setChecked(True)
        self.assertEqual((w.config["quick_skip"], w.config["quick_skip_mode"]), (True, "armor"))
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w.window_stats[0]["bait"] = 100
        asked, helped = [], []
        orig_q, orig_help, orig_bot = QMessageBox.question, qt_gui.show_help, qt_gui.FishingBot
        QMessageBox.question = staticmethod(lambda _p, title, *a, **k: asked.append(title) or QMessageBox.No)
        qt_gui.show_help = lambda _p, topic, **k: helped.append(topic)
        qt_gui.FishingBot = type("FishingBot", (), {"reset_session_state": staticmethod(lambda: None)})
        try:
            w.windows_have_same_size = lambda *a, **k: True
            w.start_all_bots()
        finally:
            QMessageBox.question, qt_gui.show_help, qt_gui.FishingBot = orig_q, orig_help, orig_bot
        self.assertEqual(asked, ["Armor quick skip will not work"])
        self.assertEqual(helped, ["armor_slot"])
        self.assertEqual(w.bots, {})


class ArmorTakesTwoSlotsTests(unittest.TestCase):
    """A spare armor fills one column and two rows of the inventory."""

    def _screen_with_armor(self, slot=7):
        screen = InventoryScreen()
        armor = np.full((64, 32, 3), (150, 140, 130), np.uint8)
        armor[8:56, 8:24] = (200, 190, 180)
        screen.pages[0][slot] = armor[:32]
        screen.pages[0][slot + InventoryScreen.COLS] = armor[32:]
        return screen

    def test_both_halves_are_accepted_as_the_spare_armor(self):
        screen = self._screen_with_armor()
        cfg = screen.config()
        top, bottom = screen.slot_center(7), screen.slot_center(7 + InventoryScreen.COLS)
        self.assertIsNone(setup_checks.armor_problem(cfg, top, screen.W, screen.H))
        self.assertIsNone(setup_checks.armor_problem(cfg, bottom, screen.W, screen.H))

    def test_neither_half_reads_as_an_empty_slot(self):
        import inventory_layout
        screen = self._screen_with_armor()
        rect, _ = inventory_layout.strip_rect(screen.config(), screen.W, screen.H)
        empty = setup_checks.count_empty_slots(screen.grab(*rect))
        self.assertEqual(empty, InventoryScreen.COLS * InventoryScreen.ROWS - 2)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class TutorialPictureTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_every_topic_draws(self):
        for key, topic in help_visuals.TOPICS.items():
            img = QImage(520, 300, QImage.Format_ARGB32)
            p = QPainter(img)
            topic["draw"](help_visuals.Canvas(p))
            p.end()

    def test_the_drawn_armor_is_one_column_and_two_rows(self):
        img = QImage(520, 300, QImage.Format_ARGB32)
        p = QPainter(img)
        c = help_visuals.Canvas(p)
        c.inventory(0, 0, rows=8)
        x, y, w, h = c.cell(2, 1, 1, 2)
        p.end()
        sx, sy, size, gap = c.grid
        self.assertAlmostEqual(w, size)
        self.assertAlmostEqual(h, 2 * size + gap)

    def test_new_topics_are_in_the_guide(self):
        for key in ("set_position", "bait", "quick_skip", "armor_slot"):
            self.assertIn(key, help_visuals.GUIDE_ORDER)
        self.assertIn("two rows", " ".join(help_visuals.TOPICS["armor_slot"]["steps"]))


if __name__ == "__main__":
    unittest.main()

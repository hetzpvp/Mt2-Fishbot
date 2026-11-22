"""The picture help (help_visuals) and where the GUI shows it: start checks,
the jigsaw layout pre-check, stop reasons, Guide buttons. Offscreen."""

import os
import tempfile
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from _stubs import install

install()

try:
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QMessageBox
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False

if HAS_QT:
    import help_visuals
    import qt_gui
    from fishing_bot import FishingBot
    from window_manager import WindowManager


class _FakeWin:
    def __init__(self, hwnd):
        self._hWnd = hwnd


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class TopicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_picture_draws_something(self):
        for key, topic in help_visuals.TOPICS.items():
            widget = help_visuals.Illustration(topic["draw"])
            widget.resize(520, 300)
            img = QImage(520, 300, QImage.Format_ARGB32)
            img.fill(QColor("#000000"))
            widget.render(img)
            colors = {img.pixel(x, y) for x in range(0, 520, 7) for y in range(0, 300, 7)}
            self.assertGreater(len(colors), 8, key)
            self.assertTrue(topic["title"] and topic["steps"], key)

    def test_the_guide_and_the_stop_reasons_point_at_real_topics(self):
        self.assertEqual(sorted(help_visuals.GUIDE_ORDER), sorted(help_visuals.TOPICS))
        reasons = {v for k, v in vars(FishingBot).items() if k.startswith("STOP_") and isinstance(v, str)}
        for reason, topic in help_visuals.STOP_REASON_TOPIC.items():
            self.assertIn(reason, reasons)
            self.assertIn(topic, help_visuals.TOPICS)

    def test_dialogs_build(self):
        guide = help_visuals.HelpDialog(None, help_visuals.GUIDE_ORDER, start="jigsaw_layout")
        self.assertEqual(guide.stack.currentIndex(), help_visuals.GUIDE_ORDER.index("jigsaw_layout"))
        guide._go(1)
        self.assertEqual(guide.stack.currentIndex(), help_visuals.GUIDE_ORDER.index("jigsaw_layout") + 1)
        one = help_visuals.HelpDialog(None, ["windows"], problem="Something is wrong")
        texts = [w.text() for w in one.findChildren(qt_gui.QLabel)]
        self.assertIn("Something is wrong", texts)
        guide.deleteLater()
        one.deleteLater()


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class GuiFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ("information", "warning", "critical"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self._orig = (WindowManager.get_all_windows, WindowManager.get_window_rect, qt_gui.show_help,
                      qt_gui.open_guide)
        WindowManager.get_all_windows = staticmethod(
            lambda: [(n, _FakeWin(100 + i)) for i, n in enumerate(["Metin2 (1)", "Metin2 (2)"])])
        WindowManager.get_window_rect = lambda self: (0, 0, 1280, 720)
        self.helps, self.guides = [], []
        qt_gui.show_help = lambda parent, topic, problem=None, title=None, modal=True: \
            self.helps.append((topic, problem, modal))
        qt_gui.open_guide = lambda parent, start=None: self.guides.append(start)
        self.w = qt_gui.FishbotWindow()

    def tearDown(self):
        self.w._stop_global_hotkey_listener()
        self.w._poll.stop()
        self.w.deleteLater()
        (WindowManager.get_all_windows, WindowManager.get_window_rect, qt_gui.show_help,
         qt_gui.open_guide) = self._orig
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_start_without_inventory_pages_shows_the_tabs_picture(self):
        self.w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        self.w.config.pop("inv_page_1_pos", None)
        self.w.start_all_bots()
        self.assertEqual([h[0] for h in self.helps], ["inventory_tabs"])
        self.assertFalse(self.w.bots)

    def test_a_stop_reason_is_explained_once(self):
        class Bot:
            stop_reason = FishingBot.STOP_CAPTURE_OVERLAP
            stop_message = "[W1] This game window overlaps Metin2 (2) ..."

        a, b = Bot(), Bot()
        self.w.bots = {0: a, 1: b}
        self.w._on_bot_stopped_ui(0, a)
        self.w._on_bot_stopped_ui(1, b)
        self.assertEqual(self.helps, [("windows", Bot.stop_message, False)])

        class Manual:
            stop_reason = FishingBot.STOP_MANUAL

        self.w.bots = {0: Manual()}
        self.w._on_bot_stopped_ui(0, self.w.bots[0])
        self.assertEqual(len(self.helps), 1)                 # nothing to explain

    def test_jigsaw_layout_is_checked_before_the_start(self):
        qt_gui.SolverCacheManager._cache_exists = classmethod(lambda cls: True)
        qt_gui.SolverCacheManager.ensure_build = lambda self: None
        self.w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        self.w.set_mode(qt_gui.MODE_JIGSAW)
        runner = self.w.jigsaw
        windows = dict(WindowManager.get_all_windows())
        # A grid defined by hand (with the puzzle window found by itself, each
        # worker checks the layout for the place it finds it in its window)
        self.w.config.update({"jigsaw_grid_bounds": [413, 287, 194, 130], "confirm_button_pos": [600, 375],
                              "jigsaw_grid_auto": False})
        self.assertFalse(runner.layout_ok("Metin2 (1)", windows))    # Yes button over the grid
        topic, problem, _ = self.helps[-1]
        self.assertEqual(topic, "jigsaw_layout")
        self.assertIn("Move the puzzle window", problem)
        self.w.config["confirm_button_pos"] = [640, 600]              # clear of everything
        self.assertTrue(runner.layout_ok("Metin2 (1)", windows))
        self.assertEqual(len(self.helps), 1)
        self.w.jigsaw_tab.jigsaw_guide_btn.click()
        self.assertEqual(self.guides, ["jigsaw_grid"])

    def test_guide_buttons(self):
        self.w.inventory.guide_btn.click()
        self.w.inventory.action_guide_btn.click()
        self.w.guide_footer_btn.click()
        self.assertEqual(self.guides, ["inventory_tabs", "drop_confirm", None])


if __name__ == "__main__":
    unittest.main()

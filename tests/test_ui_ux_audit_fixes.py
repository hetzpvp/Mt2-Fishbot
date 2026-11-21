"""Regression tests of the 2026-11-18 UI/UX audit (docs/ui-ux-audit-2026-11-18.md):
K/D/O cannot be cleared and an empty action never drops, the Window chip
after a closed client, checkbox ticks and Quick Skip, the fish dialog's item
jump, eliding row messages, the pause chime, texts that matched no control,
and the question before closing a running session."""

import os
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from _stubs import install, InventoryScreen

install()

from fishing_bot import FishingBot  # noqa: E402
from test_fishing_bot import make_inventory_bot  # noqa: E402
from utils import normalize_fish_action  # noqa: E402

try:
    from PySide6.QtWidgets import QMessageBox
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False

if HAS_QT:
    import help_visuals
    import qt_gui
    from test_gui_smoke import GuiFixture
    from test_ui_audit import _FakeBot
else:  # pragma: no cover
    GuiFixture = unittest.TestCase


class FishActionTests(unittest.TestCase):
    """5.1: only an explicit "drop" may take the drop path."""

    def test_normalize(self):
        for raw, want in [("keep", "keep"), ("drop", "drop"), ("open", "open"),
                          ("", "keep"), (None, "keep"), ("Drop", "keep"), (1, "keep")]:
            self.assertEqual(normalize_fish_action(raw), want, repr(raw))

    def test_empty_action_is_kept_not_dropped(self):
        bot, mouse, _logs = make_inventory_bot(InventoryScreen(), {"confirm_button_pos": [10, 10]})
        self.assertIsInstance(bot, FishingBot)
        bot._focus = lambda force=False: True
        self.assertTrue(bot._handle_item("Carp_living.jpg", 40, 40, ""))
        self.assertEqual(mouse.clicks, [])                     # nothing picked up or clicked
        self.assertIn((40, 40), bot._ignored_positions)        # the slot stays occupied


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class FishModalTests(GuiFixture):

    def test_second_click_keeps_the_choice(self):
        modal = qt_gui.FishModal({})
        tile = modal._tiles["Carp_living.jpg"]
        tile.seg._buttons["keep"].click()
        tile.seg._buttons["keep"].click()
        self.assertEqual(tile.seg.value(), "keep")
        self.assertEqual(modal._actions["Carp_living.jpg"], "keep")
        tile.seg._buttons["drop"].click()
        tile.seg._buttons["drop"].click()
        self.assertEqual(modal._actions["Carp_living.jpg"], "drop")

    def test_saved_values_are_never_empty(self):
        saved = []
        modal = qt_gui.FishModal({"Carp_living.jpg": "", "Gold_item.jpg": "open", "Zebra_living.jpg": "x"})
        modal.saved.connect(saved.append)
        modal._save()
        actions = saved[0]
        self.assertEqual(actions["Carp_living.jpg"], "keep")
        self.assertEqual(actions["Gold_item.jpg"], "keep")      # items cannot be opened
        self.assertEqual(actions["Zebra_living.jpg"], "keep")
        self.assertEqual(actions["Shrimp_living.jpg"], "use")    # bait fish default
        self.assertTrue(set(v for a, v in actions.items() if a not in ("Shrimp_living.jpg", "Vai_living.jpg"))
                        <= {"keep", "drop", "open"})

    def test_start_repairs_an_empty_saved_action(self):
        w = self._window()
        w.config["fish_actions"] = {"Carp_living.jpg": ""}
        w.config["inv_page_1_pos"] = (10, 10)
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        orig = qt_gui.FishingBot
        qt_gui.FishingBot = _FakeBot
        try:
            w.start_all_bots()
            self.assertEqual(w.config["fish_actions"]["Carp_living.jpg"], "keep")
        finally:
            for b in w.bots.values():
                b.running = False
            w.bots.clear()
            qt_gui.FishingBot = orig

    def test_tabs_bait_fish_first(self):
        modal = qt_gui.FishModal({})
        self.assertEqual(list(modal.tabs._buttons), ["bait", "fish", "items"])
        self.assertEqual(modal.tabs.active(), "bait")
        self.assertEqual(modal._actions["Shrimp_living.jpg"], "use")    # bait default
        self.assertEqual(modal._actions["Carp_living.jpg"], "keep")
        self.assertEqual(list(modal._tiles["Gold_item.jpg"].seg._buttons), ["keep", "drop"])
        self.assertNotIn("Vai_living.jpg", modal._tab_assets["fish"])
        # The bulk buttons act on the tab shown
        modal.tabs.setActive("items")
        self.assertTrue(modal.third_all_btn.isHidden())
        modal.drop_all_btn.click()
        self.assertEqual(modal._actions["Gold_item.jpg"], "drop")
        self.assertEqual(modal._actions["Carp_living.jpg"], "keep")
        self.assertEqual(modal._actions["Shrimp_living.jpg"], "use")
        modal.tabs.setActive("fish")
        modal.third_all_btn.click()
        self.assertEqual(modal._actions["Carp_living.jpg"], "open")
        self.assertEqual(modal._actions["Shrimp_living.jpg"], "use")
        modal.close()


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class ClosedWindowReadinessTests(GuiFixture):
    """5.2: the status line and the Window chip agree with NOT OPEN."""

    def setUp(self):
        super().setUp()
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

    def _chip_state(self, w):
        return w.dashboard.ready_chips.chips["windows"].property("state")

    def test_closed_client_is_not_ready(self):
        w = self._window()
        w.config["inv_page_1_pos"] = (10, 10)
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w._refresh_readiness()
        self.assertEqual(w.dashboard.status_big.text(), "Ready to start")
        self.open_windows = []                    # closed after the last Refresh
        w.start_all_bots()
        self.assertEqual(w.bots, {})
        self.assertNotEqual(w.dashboard.status_big.text(), "Ready to start")
        self.assertEqual(self._chip_state(w), "bad")
        # Refresh with the client back: ready again
        self.open_windows = ["Metin2 (1)"]
        w.refresh_window_list()
        self.assertEqual(self._chip_state(w), "ok")

    def test_choosing_another_window_clears_it(self):
        w = self._window()
        w.config["inv_page_1_pos"] = (10, 10)
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        self.open_windows = ["Metin2 (2)"]
        w.start_all_bots()
        self.assertEqual(self._chip_state(w), "bad")
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (2)")
        self.assertEqual(self._chip_state(w), "ok")


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class CheckboxAndQuickSkipTests(GuiFixture):
    """5.4: a checked box has a tick; with Quick Skip off no mode is ticked."""

    def test_checked_indicator_has_an_image(self):
        qss = qt_gui.build_qss()
        block = qss.split("QCheckBox::indicator:checked {", 1)[1].split("}", 1)[0]
        self.assertIn("image: url(", block)
        self.assertNotIn("url(none)", block)
        path = block.split('url("', 1)[1].split('")', 1)[0]
        self.assertTrue(os.path.exists(path), path)

    def test_quick_skip_off_shows_no_mode(self):
        w = self._window()
        s = w.settings
        self.assertFalse(s.qs_enable.isChecked())
        self.assertFalse(s.qs_horse.isChecked())
        self.assertFalse(s.qs_armor.isChecked())

    def test_mode_is_kept_while_off(self):
        w = self._window()
        s = w.settings
        s.qs_enable.setChecked(True)
        self.assertTrue(s.qs_horse.isChecked())             # the default mode shows when on
        s.qs_armor.setChecked(True)
        self.assertFalse(s.qs_horse.isChecked())
        s.qs_enable.setChecked(False)
        self.assertFalse(s.qs_armor.isChecked())
        self.assertEqual(w.config["quick_skip_mode"], "armor")
        self.assertFalse(w.config["quick_skip"])
        s.qs_enable.setChecked(True)
        self.assertTrue(s.qs_armor.isChecked())

    def test_unticking_the_only_mode_keeps_it(self):
        w = self._window()
        s = w.settings
        s.qs_enable.setChecked(True)
        s.qs_horse.setChecked(False)
        self.assertTrue(s.qs_horse.isChecked())

    def test_saved_armor_mode_loads(self):
        w = self._window()
        w.config.update({"quick_skip": True, "quick_skip_mode": "armor"})
        w.settings.loadFromConfig(w.config)
        self.assertTrue(w.settings.qs_armor.isChecked())
        self.assertFalse(w.settings.qs_horse.isChecked())


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class RowMessageTests(GuiFixture):
    """5.6: a long message ends in an ellipsis; a stop reason opens its help."""

    def test_long_message_is_elided(self):
        w = self._window()
        w.show()
        self.app.processEvents()
        row = w.dashboard.window_rows[0]
        long = "Game windows overlap — this client is covered by another. Move them apart. " * 2
        row.setMessage(long, kind="error")
        self.app.processEvents()
        shown = row.msg_lbl.displayedText()
        self.assertTrue(shown.endswith("…"), shown)
        self.assertLess(len(shown), len(long))
        self.assertEqual(row.msg_lbl.text(), long)
        self.assertIn(long, row.msg_lbl.toolTip())
        w.hide()

    def test_click_opens_the_topic(self):
        w = self._window()
        opened = []
        orig = qt_gui.show_help
        qt_gui.show_help = lambda _p, topic, problem=None, **k: opened.append((topic, problem))
        try:
            row = w.dashboard.window_rows[0]
            row.setMessage("Window not open", topic="windows")
            row.msg_lbl.clicked.emit()
            row.setMessage("Fish caught")                 # no topic: a click does nothing
            row.msg_lbl.clicked.emit()
        finally:
            qt_gui.show_help = orig
        self.assertEqual(opened, [("windows", "Window not open")])


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class PauseChimeTests(GuiFixture):
    """5.7: pause and resume are heard, once per change."""

    def test_chime_on_pause_and_resume(self):
        w = self._window()
        played = []
        orig = qt_gui.play_pause_beep
        qt_gui.play_pause_beep = played.append
        try:
            w.bots = {0: _FakeBot(bot_id=0)}
            w.toggle_pause_all()
            w.toggle_pause_all()
            w._on_activity_paused(qt_gui.MODE_JIGSAW, False)      # no change: no chime
            w.config["sound_on_pause"] = False
            w.toggle_pause_all()
            for t in [t for t in qt_gui.threading.enumerate() if t.name == "pause-chime"]:
                t.join(1)
        finally:
            qt_gui.play_pause_beep = orig
            w.bots = {}
        self.assertEqual(played, [True, False])

    def test_setting_round_trips(self):
        w = self._window()
        w.settings.pause_sound_check.setChecked(False)
        self.assertFalse(self._saved()["sound_on_pause"])


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class TextTests(GuiFixture):
    """5.8: texts that disagreed with the controls."""

    def test_confirm_is_not_always_needed(self):
        steps = " ".join(help_visuals.TOPICS["drop_confirm"]["steps"])
        self.assertNotIn("Always needed", steps)
        self.assertIn("always for Jigsaw", steps)

    def test_page_one_is_required_for_both(self):
        w = self._window()
        w.inventory.setMode(qt_gui.MODE_JIGSAW)
        self.assertFalse(w.inventory.coord_pills["page1"].isOptional())
        self.assertIn("Page 1 is required", w.inventory.pages_hint.text())
        w.inventory.setMode(qt_gui.MODE_FISHING)
        self.assertFalse(w.inventory.coord_pills["page1"].isOptional())
        self.assertIn("Page 1 is required", w.inventory.pages_hint.text())

    def test_solver_status_is_visible_without_scrolling(self):
        w = self._window()
        w.resize(qt_gui.WINDOW_W, qt_gui.WINDOW_H)
        w.set_mode(qt_gui.MODE_JIGSAW, save=False)
        w.show()
        w.tabs_bar.setActive("activity")
        self.app.processEvents()
        label = w.jigsaw_tab.cache_status_label
        scroll = w.stack.currentWidget()
        vp = scroll.viewport() if hasattr(scroll, "viewport") else w.jigsaw_tab
        bottom = label.mapTo(vp, label.rect().bottomLeft()).y()
        self.assertLessEqual(bottom, vp.height())
        w.hide()


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class CloseWhileRunningTests(GuiFixture):
    """5.10: Close asks while a bot runs; No keeps it running."""

    def test_no_keeps_the_bot(self):
        w = self._window()
        asked = []
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: asked.append(a[1]) or QMessageBox.No)
        bot = _FakeBot(bot_id=0)
        try:
            w.bots = {0: bot}
            w.show()
            w.close()
            self.assertEqual(len(asked), 1)
            self.assertTrue(bot.running)
            self.assertTrue(w.isVisible())
        finally:
            QMessageBox.question = orig
            w.bots = {}
            w.hide()

    def test_idle_close_does_not_ask(self):
        w = self._window()
        asked = []
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: asked.append(1) or QMessageBox.No)
        try:
            w.close()
        finally:
            QMessageBox.question = orig
        self.assertEqual(asked, [])


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class AccessibleNameTests(GuiFixture):

    def test_icon_buttons_have_names(self):
        w = self._window()
        from PySide6.QtWidgets import QAbstractButton
        unnamed = [b for b in w.findChildren(QAbstractButton)
                   if not b.text().strip() and b.toolTip() and not b.accessibleName()]
        self.assertEqual(unnamed, [])



# ---------------------------------------------------------------------------
# Second pass of the same audit (findings 5.1–5.11 of the rewritten report)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class BaitCapacityTests(GuiFixture):
    """5.1: editing the bait keys or quantity never refills counted bait."""

    def _setup(self):
        w = self._window()
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w.dashboard.window_rows[1].setSelectedWindow("Metin2 (2)")
        w.reset_bait()                                   # both full: 4 keys x 200
        return w

    def test_counted_bait_is_kept_and_full_follows(self):
        w = self._setup()
        w.window_stats[0]["bait"] = 50
        asked = []
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: asked.append(1) or QMessageBox.No)
        try:
            w.settings.bait_checks["4"].setChecked(False)
        finally:
            QMessageBox.question = orig
        self.assertEqual(asked, [])
        self.assertEqual(w.window_stats[0]["bait"], 50)       # counted down: kept
        self.assertEqual(w.window_stats[1]["bait"], 600)      # was full: follows the keys
        self.assertEqual(w.notice.current(), "bait")
        self.assertEqual(self._saved()["window_bait"][:2], [50, 600])
        w.reset_bait()
        self.assertNotIn("bait", w.notice.keys())

    def test_count_above_the_new_capacity_is_lowered(self):
        w = self._setup()
        w.window_stats[0]["bait"] = 700
        w.settings.bait_qty_input.setText("50")
        w.settings.bait_qty_input.editingFinished.emit()
        self.assertEqual(w.window_stats[0]["bait"], 200)      # 4 keys x 50
        self.assertEqual(w.window_stats[1]["bait"], 200)


class BtcAddressTests(unittest.TestCase):
    """5.2: the footer, the copy button and the README give one address."""

    @unittest.skipUnless(HAS_QT, "PySide6 is not installed")
    def test_app_matches_readme(self):
        readme = os.path.join(os.path.dirname(__file__), "..", "README.md")
        with open(readme, encoding="utf-8") as f:
            self.assertIn(f"`{qt_gui.FishbotWindow.BTC_ADDRESS}`", f.read())

    @unittest.skipUnless(HAS_QT, "PySide6 is not installed")
    def test_address_is_valid_base58check(self):
        import hashlib
        alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        n = 0
        for c in qt_gui.FishbotWindow.BTC_ADDRESS:
            n = n * 58 + alphabet.index(c)                     # ValueError on 0 O I l
        raw = n.to_bytes(25, "big")
        self.assertEqual(hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4], raw[21:])


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class SecondPassUiTests(GuiFixture):

    def test_timing_rows_fit_the_dialog_width(self):            # 5.3
        w = self._window()
        modal = qt_gui.TimingModal(w.config, w)
        modal.show()
        self.app.processEvents()
        from PySide6.QtWidgets import QScrollArea
        scroll = modal.findChild(QScrollArea)
        self.assertLessEqual(scroll.widget().minimumSizeHint().width(), scroll.viewport().width())
        self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
        for _label, key, *_rest in [i for _g, items in modal.GROUPS for i in items]:
            val = modal._labels[key]
            self.assertLessEqual(val.mapTo(scroll.widget(), val.rect().topRight()).x(),
                                 scroll.viewport().width())
        modal.close()

    def test_texts_match_the_controls(self):                    # 5.4, 5.10
        w = self._window()
        steps = " ".join(help_visuals.TOPICS["fish_handling"]["steps"])
        self.assertNotIn("each fish and item", steps)
        self.assertIn("keep or drop for each item", steps)
        from PySide6.QtWidgets import QLabel
        texts = " ".join(l.text() for l in w.settings.fishing_page.findChildren(QLabel))
        self.assertIn("keep or drop each item", texts)
        from PySide6.QtWidgets import QPushButton
        tips = " ".join(b.toolTip() for b in w.header.findChildren(QPushButton))
        self.assertIn("Close (asks before stopping a running bot)", tips)

    def test_jigsaw_welcome_does_not_claim_a_build(self):        # 5.5
        w = self._window()
        w.solver_cache_manager.state = lambda: "ready"
        wiz = w.open_wizard(qt_gui.MODE_JIGSAW)
        from PySide6.QtWidgets import QLabel
        texts = " ".join(l.text() for l in wiz.findChildren(QLabel))
        self.assertNotIn("being prepared", texts)
        wiz.close()

    def test_disabled_ghost_buttons_look_disabled(self):         # 5.6
        self.assertIn("BtnGhost:disabled", qt_gui.build_qss())
        w = self._window()
        w.dashboard.setRunning(True)
        self.assertFalse(w.dashboard.refresh_btn.isEnabled())
        self.assertIn("when the bots are stopped", w.dashboard.refresh_btn.toolTip())
        self.assertIn("Pause or stop the bots first", w.dashboard.reset_btn.toolTip())
        w.dashboard.setRunning(False)
        self.assertNotIn("stopped", w.dashboard.refresh_btn.toolTip())

    def test_all_crate_strategies_fit_the_first_screen(self):    # 5.7
        w = self._window()
        w.notice.clear("welcome")                                # first run only
        w.set_mode(qt_gui.MODE_JIGSAW, save=False)
        w.show()
        w.tabs_bar.setActive("activity")
        self.app.processEvents()
        vp = w.stack.currentWidget().viewport()
        rb = w.jigsaw_tab.priority_radios["special_when_better"]
        self.assertLessEqual(rb.mapTo(vp, rb.rect().bottomLeft()).y(), vp.height())
        descs = w.jigsaw_tab.priority_descs
        self.assertEqual([k for k, d in descs.items() if d.isVisibleTo(w.jigsaw_tab)], ["normal_first"])
        w.jigsaw_tab.setPriority("special_first")                # loading a config
        self.assertEqual([k for k, d in descs.items() if d.isVisibleTo(w.jigsaw_tab)], ["special_first"])
        w.hide()

    def test_short_screen_gets_a_shorter_window(self):          # 5.8
        self.assertEqual(qt_gui.choose_ui_scale("auto", 768, 432), qt_gui.MIN_UI_SCALE)
        self.assertEqual(qt_gui.window_height_for(1032, 1.15), qt_gui.WINDOW_H)
        self.assertEqual(qt_gui.window_height_for(600, 0.85), qt_gui.WINDOW_H)
        h = qt_gui.window_height_for(432, qt_gui.MIN_UI_SCALE)
        self.assertLess(h, qt_gui.WINDOW_H)
        self.assertLessEqual(h * qt_gui.MIN_UI_SCALE, 432)
        orig = qt_gui.ACTIVE_WINDOW_H
        qt_gui.ACTIVE_WINDOW_H = h
        try:
            w = self._window()
        finally:
            qt_gui.ACTIVE_WINDOW_H = orig
        w.show()
        self.app.processEvents()
        self.assertEqual(w.height(), h)
        start = w.dashboard.start_btn
        self.assertLessEqual(start.mapTo(w, start.rect().bottomLeft()).y(), h)
        w.tabs_bar.setActive("settings")
        self.app.processEvents()
        self.assertGreater(w.stack.currentWidget().verticalScrollBar().maximum(), 0)
        w.hide()

    def test_capture_banner_says_others_are_minimized(self):    # 5.9
        w = self._window()
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w._capture_window_name = "Metin2 (1)"
        w._set_capturing_ui("page1")
        self.assertNotIn("minimized", w.inventory.capture_text.text())   # one client: nothing to say
        w.dashboard.window_rows[1].setSelectedWindow("Metin2 (2)")
        w._set_capturing_ui("page1")
        self.assertIn("minimized", w.inventory.capture_text.text())
        w._set_capturing_ui(None)

    def test_running_counts_selected_windows(self):              # 5.10
        w = self._window()
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        w.bots = {0: _FakeBot(bot_id=0)}
        try:
            w._update_kpis()
            self.assertEqual(w.dashboard.kpi_active._value.text(), "1/1")
        finally:
            w.bots = {}

    def test_hint_text_contrast(self):                          # 5.11
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        for bg in ("bg_1", "bg_2"):
            hi, lo = sorted((lum(qt_gui.C["text_mute"]), lum(qt_gui.C[bg])), reverse=True)
            self.assertGreaterEqual((hi + 0.05) / (lo + 0.05), 4.5, bg)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class ThirdPassTests(GuiFixture):
    """Third pass of the report (findings 5.1-5.8 as numbered there)."""

    def setUp(self):
        super().setUp()
        self._orig_question = QMessageBox.question

    def tearDown(self):
        QMessageBox.question = self._orig_question
        super().tearDown()

    def _row_text(self, w, i=0):
        return w.dashboard.window_rows[i].pill.text()

    def test_catch_step_matches_its_picture(self):              # 5.1
        w = self._window()
        wiz = w.open_wizard(qt_gui.MODE_FISHING)
        page = wiz._build_fish()
        text = " ".join(l.text() for l in page.findChildren(qt_gui.QLabel))
        self.assertIn("leave Drop / Sell empty", text)
        self.assertNotIn("drag items", text)
        wiz.close()

    def test_dialogs_are_capped_to_the_screen(self):            # 5.2
        avail = self.app.primaryScreen().availableGeometry()
        d = qt_gui.QDialog()
        d.resize(avail.width() + 400, avail.height() + 400)
        d.setMinimumSize(avail.width() + 100, avail.height() + 100)
        help_visuals.fit_to_screen(d)
        self.assertLessEqual(d.width(), avail.width())
        self.assertLessEqual(d.height(), avail.height() - 40)
        self.assertLessEqual(d.minimumHeight(), avail.height() - 40)
        small = qt_gui.QDialog()
        small.resize(400, 300)
        help_visuals.fit_to_screen(small)
        self.assertEqual((small.width(), small.height()), (400, 300))   # fits: untouched

    def test_reset_bait_while_paused_refills_the_bot(self):     # 5.3
        w = self._window()
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        bot = _FakeBot(bot_id=0)
        bot.bait_keys = ["1", "2", "3", "4"]
        bot.bait_counter = 10
        w.bots = {0: bot}
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            w.dashboard.setRunning(True)
            self.assertFalse(w.dashboard.reset_btn.isEnabled())   # clicking: no
            w._on_reset_bait_clicked()
            self.assertEqual(bot.bait_counter, 10)
            w.toggle_pause_all()
            self.assertTrue(bot.paused)
            self.assertTrue(w.dashboard.reset_btn.isEnabled())
            w._on_reset_bait_clicked()
            self.assertEqual(bot.bait_counter, 800)               # 4 keys x 200
            self.assertEqual(w.window_stats[0]["bait"], 800)
            w.toggle_pause_all()
            self.assertFalse(w.dashboard.reset_btn.isEnabled())
        finally:
            w.bots = {}
            w.dashboard.setRunning(False)
        self.assertTrue(w.dashboard.reset_btn.isEnabled())

    def test_row_pill_is_not_ready_before_the_setup(self):      # 5.4
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        w = self._window()
        w.dashboard.window_rows[0].setSelectedWindow("Metin2 (1)")
        self.assertNotEqual(w.dashboard.status_big.text(), "Ready to start")
        self.assertNotIn("READY", self._row_text(w))
        self.assertIn("SELECTED", self._row_text(w))
        w.config["inv_page_1_pos"] = (10, 10)
        self.open_windows = []                    # closed after the last Refresh
        w.start_all_bots()
        self.assertIn("NOT OPEN", self._row_text(w))
        w.set_mode(qt_gui.MODE_JIGSAW, save=False)
        self.assertIn("NOT OPEN", self._row_text(w))          # kept across a mode switch
        w.set_mode(qt_gui.MODE_FISHING, save=False)
        self.assertIn("NOT OPEN", self._row_text(w))
        self.open_windows = ["Metin2 (1)"]
        w.refresh_window_list()
        self.assertIn("SELECTED", self._row_text(w))

    def test_tiles_are_named_after_their_pictures(self):        # 5.5
        names = {asset: name for name, asset, _ in qt_gui.FISH_DISPLAY + qt_gui.ITEM_DISPLAY}
        self.assertEqual(names["Large_zander_living.jpg"], "Large zander")
        self.assertEqual(names["Refugee_cape_item.jpg"], "Refugee cape")
        self.assertEqual(names["Symbol_wise_emperors_item.jpg"], "Wise Emperor symbol")

    def test_cast_and_mount_keys_are_settings(self):            # 5.6
        w = self._window()
        s = w.settings
        self.assertEqual(s.mount_key_combo.currentData(), "g")
        self.assertEqual(s.cast_key_combo.currentData(), "space")
        self.assertIn("Ctrl + G", s.qs_desc.text())
        s.mount_key_combo.setCurrentIndex(s.mount_key_combo.findData("h"))
        s.cast_key_combo.setCurrentIndex(s.cast_key_combo.findData("e"))
        self.assertIn("Ctrl + H", s.qs_desc.text())
        saved = self._saved()
        self.assertEqual((saved["mount_key"], saved["cast_key"]), ("h", "e"))
        w2 = self._window()
        self.assertEqual(w2.settings.mount_key_combo.currentData(), "h")
        w2.dashboard.setRunning(True)
        w2.settings.setConfigEnabled(False)
        self.assertFalse(w2.settings.mount_key_combo.isEnabled())

    def test_bad_saved_keys_fall_back(self):                    # 5.6
        w = self._window()
        w._merge_config({"mount_key": "F9", "cast_key": "SPACE"})
        self.assertEqual(w.config["mount_key"], "g")
        self.assertEqual(w.config["cast_key"], "space")
        self.assertIn("mount_key", w._config_fixed)
        self.assertNotIn("cast_key", w._config_fixed)

    def test_bait_notice_stays_with_fishing(self):              # 5.7
        w = self._window()
        w.notice.clear("welcome")
        w._show_bait_notice()
        w.set_mode(qt_gui.MODE_JIGSAW, save=False)
        self.assertNotIn("bait", w.notice.keys())
        w.show()
        w.tabs_bar.setActive("activity")
        self.app.processEvents()
        vp = w.stack.currentWidget().viewport()
        rb = w.jigsaw_tab.priority_radios["special_when_better"]
        self.assertLessEqual(rb.mapTo(vp, rb.rect().bottomLeft()).y(), vp.height())
        w.hide()
        w.set_mode(qt_gui.MODE_FISHING, save=False)
        self.assertIn("bait", w.notice.keys())
        w.reset_bait()
        w.set_mode(qt_gui.MODE_JIGSAW, save=False)
        w.set_mode(qt_gui.MODE_FISHING, save=False)
        self.assertNotIn("bait", w.notice.keys())

    def test_small_mismatches(self):                            # 5.8
        w = self._window()
        self.assertIn("all windows", w.settings.sound_check.text())
        self.assertEqual(w.jigsaw_tab.set_confirm_btn.text().strip(), "Set Confirm")
        w._copy_btc()
        self.assertEqual(w._copy_btn.toolTip(), "Copied")


if __name__ == "__main__":
    unittest.main()

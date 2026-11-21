"""The Golden Tuna dialog: found by its shape on real captures, answered by
the bot's real loop in the simulator. Stub-only: nothing touches the desktop.

Real captures (tests/fixtures/golden_tuna, 800x600, 2026-11-20): the choice
page with three answers, the same page with "Cut it open" hovered, the OK
page that follows "Let it go", the black bars sliding away after OK, and a
normal game screen."""

import os
import sys
import unittest
from unittest import mock

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import quest_dialog  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402
from minigame_sim import bench  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "golden_tuna")
CHOICE_BARS = ((120, 239, 679, 264), (120, 271, 679, 296), (120, 303, 679, 328))


def fixture(name):
    frame = cv2.imread(os.path.join(FIXTURES, name))
    assert frame is not None, name
    return frame


class DetectorOnRealCapturesTests(unittest.TestCase):

    def test_choice_page_has_three_bars(self):
        for name in ("dialog_open_800x600.png", "dialog_open_hover_cut_800x600.png"):
            dialog = quest_dialog.find(fixture(name))
            self.assertIsNotNone(dialog, name)
            self.assertEqual(dialog.board, (50, 150, 749, 449), name)
            self.assertEqual(dialog.bars, CHOICE_BARS, name)
            self.assertEqual(dialog.letterbox, (72, 72), name)

    def test_hovered_bar_is_the_same_page(self):
        plain = quest_dialog.find(fixture("dialog_open_800x600.png"))
        hovered = quest_dialog.find(fixture("dialog_open_hover_cut_800x600.png"))
        self.assertTrue(plain.same_page(hovered))

    def test_ok_page_has_one_lower_bar(self):
        # A longer text pushes the bar down: places are never fixed
        dialog = quest_dialog.find(fixture("ok_page_800x600.png"))
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.bars, ((260, 251, 539, 276),))
        self.assertEqual(dialog.bar_center(0), (399, 263))
        self.assertFalse(dialog.same_page(quest_dialog.find(fixture("dialog_open_800x600.png"))))

    def test_closing_and_normal_screens_have_no_dialog(self):
        closing = fixture("closing_letterbox_800x600.png")
        self.assertIsNone(quest_dialog.find(closing))
        self.assertTrue(quest_dialog.letterbox_showing(closing))
        normal = fixture("normal_hud_800x600.png")
        self.assertIsNone(quest_dialog.find(normal))
        self.assertFalse(quest_dialog.letterbox_showing(normal))

    def test_strip_and_odd_frames_are_not_a_dialog(self):
        self.assertIsNone(quest_dialog.find(fixture("inventory_strip_covered.png")))
        self.assertIsNone(quest_dialog.find(np.zeros((600, 800, 3), np.uint8)))      # black window
        self.assertIsNone(quest_dialog.find(None))
        letterbox_only = fixture("normal_hud_800x600.png").copy()
        letterbox_only[:72] = 0
        letterbox_only[-72:] = 0
        self.assertIsNone(quest_dialog.find(letterbox_only))

    def test_bgra_capture_works(self):
        frame = cv2.cvtColor(fixture("dialog_open_800x600.png"), cv2.COLOR_BGR2BGRA)
        self.assertEqual(quest_dialog.find(frame).bars, CHOICE_BARS)


# Three tunas per session, after the 1st, 3rd and 5th won game, each after a
# different delay: at once, during the catch handling, and late enough that
# the bot has cast already (the dialog then pulls the line in).
TUNA_GAMES = {1, 3, 5}
TUNA_DELAYS = [0.0, 0.8, 2.5]


def tuna_session(choice, ok_page, config=None, games=7, timeout=240.0, **game_kw):
    """Fishes until ``games`` games are over and every tuna was answered."""
    ok = [True, True, True]
    ok[choice] = ok_page
    opts = dict(golden_tuna_games=TUNA_GAMES, tuna_delay=TUNA_DELAYS, tuna_ok=ok)
    opts.update(game_kw)
    game = bench.quick_game("official", **opts)
    config = dict({"golden_tuna": True, "golden_tuna_choice": choice}, **(config or {}))
    done = lambda: (game.stats["caught"] + game.stats["timeouts"] >= games      # noqa: E731
                    and game.stats["tuna_closed"] >= len(TUNA_GAMES))
    bot, logs = bench.run(game, config, timeout=timeout, until=done)
    return game, bot, logs


class BotAnswersTheTunaTests(unittest.TestCase):
    """The real loop against the simulator's tuna (real geometry, the HUD
    hidden, keys ignored, a blank moment between pages). Only "Let it go"
    followed by an OK page is seen on the real client; for the other answers
    both are tried: an OK page, or the dialog closing at once."""

    def _check_flow(self, game, bot, logs, choice):
        s = game.stats
        tail = logs[-20:]
        self.assertIsNone(bot.stop_message, tail)
        self.assertEqual(s["tuna_dialogs"], len(TUNA_GAMES), tail)
        self.assertEqual(game.tuna_answers, [choice] * len(TUNA_GAMES), tail)
        self.assertEqual(s["tuna_closed"], len(TUNA_GAMES), "a dialog was left open")
        self.assertIsNone(game.tuna)
        self.assertEqual(s["tuna_stray_clicks"], 0, "clicked beside the bars")
        self.assertGreaterEqual(s["caught"], 7, tail)                 # fishing went on after each one
        self.assertEqual(s["timeouts"], 0, tail)
        # A cast the dialog pulled in is not a missed bite
        self.assertFalse(any("Minigame not detected" in m for m in logs), tail)
        self.assertEqual(bot.consecutive_failures, 0)

    def test_let_it_go_then_ok(self):                 # as on the real client
        self._check_flow(*tuna_session(0, ok_page=True), choice=0)

    def test_let_it_go_without_ok(self):
        self._check_flow(*tuna_session(0, ok_page=False), choice=0)

    def test_cut_it_open_then_ok(self):
        self._check_flow(*tuna_session(1, ok_page=True), choice=1)

    def test_cut_it_open_without_ok(self):
        self._check_flow(*tuna_session(1, ok_page=False), choice=1)

    def test_use_for_bait_then_ok(self):
        self._check_flow(*tuna_session(2, ok_page=True), choice=2)

    def test_use_for_bait_without_ok(self):
        self._check_flow(*tuna_session(2, ok_page=False), choice=2)

    def test_yes_no_confirmation_then_ok(self):
        self._check_flow(*tuna_session(1, ok_page=True, tuna_confirm=True), choice=1)

    def test_yes_no_confirmation_without_ok(self):
        self._check_flow(*tuna_session(2, ok_page=False, tuna_confirm=True), choice=2)

    def test_default_answer_is_cut_it_open(self):
        game = bench.quick_game("official", golden_tuna_games={1})
        bench.run(game, {"golden_tuna": True}, games=2, timeout=90)
        self.assertEqual(game.tuna_answers, [1])

    # Two tests, not two subtests: each is a whole session (~40 s), so they
    # can run side by side on two workers.
    def _check_with_catch_handling(self, choice, ok_page):
        game = bench.quick_game("official")
        config = {"auto_fish_handling": True, "fish_actions": {n: "open" for n in game._item_names}}
        game, bot, logs = tuna_session(choice, ok_page, config)
        self._check_flow(game, bot, logs, choice)
        self.assertFalse(any("reads as full" in m for m in logs), logs[-20:])
        living = [n for page in game.pages for n in page.values() if "_living" in n]
        self.assertLessEqual(len(living), 1, "catches were not handled around the tuna")

    def test_with_catch_handling_the_covered_strip_is_not_a_full_page(self):
        self._check_with_catch_handling(1, ok_page=True)

    def test_with_catch_handling_and_no_ok_page_the_covered_strip_is_not_a_full_page(self):
        self._check_with_catch_handling(2, ok_page=False)

    def test_setting_off_leaves_the_dialog_alone(self):
        game = bench.quick_game("official", golden_tuna_games={1})
        bench.run(game, {"golden_tuna": False}, games=99, timeout=6.0)
        self.assertEqual(game.tuna_answers, [])
        self.assertIsNotNone(game.tuna)


class HandlerSafetyTests(unittest.TestCase):

    def _bot(self, **game_kw):
        game = bench.quick_game("official", **game_kw)
        bot, logs = bench.make_bot(game, {"golden_tuna": True})
        bot.running = True
        return game, bot, logs

    def test_no_dialog_costs_nothing_and_clicks_nothing(self):
        game, bot, _ = self._bot()
        with mock.patch.object(bot, "_ui_click") as click:
            self.assertFalse(bot._handle_quest_dialog())
        click.assert_not_called()

    def test_dialog_that_ignores_clicks_stops_the_bot(self):
        game, bot, _ = self._bot()
        game.tuna = ("choice", 0.0)
        bot._QUEST_ANSWER_WAIT = 0.2
        with mock.patch.object(bot, "_ui_click") as click:     # the clicks never arrive
            self.assertTrue(bot._handle_quest_dialog())
        self.assertEqual(click.call_count, 2)
        self.assertFalse(bot.running)
        self.assertEqual(bot.stop_reason, FishingBot.STOP_QUEST_DIALOG)

    def test_unknown_dialog_is_not_answered(self):
        game, bot, _ = self._bot()
        four = quest_dialog.QuestDialog(board=(50, 150, 749, 449),
                                        bars=CHOICE_BARS + ((120, 335, 679, 360),),
                                        letterbox=(72, 72), text_px=900)
        with mock.patch.object(quest_dialog, "find", return_value=four), \
                mock.patch.object(bot, "_ui_click") as click:
            self.assertTrue(bot._handle_quest_dialog())
        click.assert_not_called()
        self.assertEqual(bot.stop_reason, FishingBot.STOP_QUEST_DIALOG)

    def test_paused_bot_does_not_click(self):
        game, bot, _ = self._bot()
        game.tuna = ("choice", 0.0)
        bot.paused = True
        with mock.patch.object(bot, "_ui_click") as click:
            self.assertTrue(bot._handle_quest_dialog())
        click.assert_not_called()
        self.assertTrue(bot.running)


if __name__ == "__main__":
    unittest.main()

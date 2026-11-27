"""The jigsaw result OK point is saved only when the dialog really went
away after the click (real-client run 2026-11-26: the click missed the OK,
the finder then saw the real OK elsewhere, and the missed point was saved
as the OK for every later board and run)."""

import unittest

from _stubs import install

install()

from jigsaw_bot import JigsawBot  # noqa: E402


class _Bot:
    """Just what _check_result_gone uses."""

    def __init__(self, picks, then=None):
        self.running = True
        self._then = then                      # what the finder sees once ``picks`` ran out
        self.config = {"jigsaw_result_gone_timeout": 1.0}
        self.diag_tag = "W1"
        self._picks = list(picks)
        self.learned = []
        self.clicks = []
        self._learned_result_point = None

    def _gray_window(self):
        return None

    def _pick_result_button(self, before, gray, prev):
        return self._picks.pop(0) if self._picks else self._then

    def _learn_setting(self, key, value):
        self.learned.append((key, value))

    def _remember_result_size(self):
        pass

    def _parked_point(self):
        return (0, 0)

    def _click_window_point(self, point, what, park_at=None):
        self.clicks.append(point)


def check(bot, point):
    JigsawBot._check_result_gone(bot, before=None, point=point)


class ResultPointLearningTests(unittest.TestCase):
    def test_dialog_gone_saves_the_point(self):
        bot = _Bot([None, None])
        check(bot, (400, 321))
        self.assertEqual(bot.learned, [("jigsaw_result_button_pos", [400, 321])])

    def test_another_button_still_seen_is_not_gone(self):
        bot = _Bot([(400, 321), (400, 321)])
        check(bot, (327, 362))
        self.assertEqual(bot.learned, [])
        self.assertIsNone(bot._learned_result_point)

    def test_one_empty_read_is_not_enough(self):
        bot = _Bot([None, (400, 321)])
        check(bot, (327, 362))
        self.assertEqual(bot.learned, [])

    def test_same_button_still_there_is_clicked_again(self):
        bot = _Bot([], then=(327, 362))
        check(bot, (327, 362))
        self.assertEqual(bot.learned, [])
        self.assertEqual(bot.clicks, [(327, 362)])


if __name__ == "__main__":
    unittest.main()

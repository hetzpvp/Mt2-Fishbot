"""Regression tests for the 2026-11-18 performance audit
(docs/perf-audit-2026-11-18.md). Each change must find the same things as
before, only with less work. Stub/fixture only: nothing here touches the desktop."""

import math
import os
import unittest
from unittest import mock

from _stubs import install, FakeMouse

install()

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from fishing_bot import FishingBot  # noqa: E402
from jigsaw_bot import JigsawBot  # noqa: E402
from jigsaw_detector import JigsawDetector  # noqa: E402
from test_capture_and_jigsaw import WM  # noqa: E402
from test_real_client_rescaled import LABELS, image  # noqa: E402
from test_review_fixes import quiet_bot  # noqa: E402


# ---------------------------------------------------------------- 4.1 classic

class ClassicScaleRotationTests(unittest.TestCase):
    """4.1: once the icon's scale is known, a poll without the icon matches
    that scale and a few others in turn, not all 19. Every scale is still
    tried within a few polls."""

    def setUp(self):
        FishingBot._classic_fish_scaled_cache = None
        FishingBot._classic_fish_last_scale = None

    tearDown = setUp

    def _bot(self, icon_scale):
        _, bot, _ = quiet_bot({"classic_fishing_delay": 0.0})
        template = bot._load_classic_fish_template()
        self.assertIsNotNone(template)
        rng = np.random.default_rng(7)
        bar = rng.integers(60, 120, (360, 250), dtype=np.uint8)   # a 720p window's search bar
        if icon_scale is not None:
            h, w = template.shape
            nw, nh = int(w * icon_scale), int(h * icon_scale)
            icon = cv2.resize(template, (nw, nh), interpolation=cv2.INTER_AREA if icon_scale < 1
                              else cv2.INTER_LINEAR)
            bar[100:100 + nh, 20:20 + nw] = icon
        frame = cv2.cvtColor(bar, cv2.COLOR_GRAY2BGR)
        self.polls = 0

        def grab(*_):
            self.polls += 1
            return frame
        bot.capture_window_region = grab
        bot._last_frame_usable = True
        return bot

    def _wait(self, bot, timeout=3.0):
        calls = []
        real = cv2.matchTemplate

        def counting(img, templ, method):
            calls.append((self.polls, templ.shape))
            return real(img, templ, method)
        with mock.patch("fishing_bot.cv2.matchTemplate", side_effect=counting):
            found = bot.wait_for_classic_fish(timeout=timeout)
        per_poll = [sum(1 for p, _ in calls if p == n) for n in range(1, self.polls + 1)]
        self.shapes = {shape for _, shape in calls}
        return found, per_poll

    def test_unknown_scale_is_found_on_the_first_poll(self):
        bot = self._bot(1.4)
        found, per_poll = self._wait(bot)
        self.assertTrue(found)
        self.assertEqual(self.polls, 1)
        self.assertEqual(FishingBot._classic_fish_last_scale, 1.4)

    def test_known_scale_takes_one_match(self):
        FishingBot._classic_fish_last_scale = 1.4
        found, per_poll = self._wait(self._bot(1.4))
        self.assertTrue(found)
        self.assertEqual(per_poll, [1])

    def test_stale_scale_still_finds_the_icon_within_a_few_polls(self):
        FishingBot._classic_fish_last_scale = 0.5        # the window was resized
        found, per_poll = self._wait(self._bot(1.4))
        self.assertTrue(found)
        others = len(FishingBot._classic_fish_scaled_cache) - 1
        self.assertLessEqual(self.polls, math.ceil(others / FishingBot._CLASSIC_ROTATING_SCALES))
        self.assertTrue(all(n <= 1 + FishingBot._CLASSIC_ROTATING_SCALES for n in per_poll), per_poll)
        self.assertEqual(FishingBot._classic_fish_last_scale, 1.4)

    def test_every_scale_is_tried_while_the_icon_is_missing(self):
        FishingBot._classic_fish_last_scale = 1.4
        bot = self._bot(None)
        found, per_poll = self._wait(bot, timeout=0.6)
        self.assertFalse(found)
        self.assertTrue(all(n <= 1 + FishingBot._CLASSIC_ROTATING_SCALES for n in per_poll), per_poll)
        self.assertGreater(self.polls, 6)
        fitting = {t.shape for _, w, h, t in FishingBot._classic_fish_scaled_cache if h <= 360 and w <= 250}
        self.assertEqual(self.shapes, fitting)

    def test_windows_with_different_scales_keep_their_own(self):
        # Two clients drawn at different sizes: the second one's find must
        # not send the first one back to rotating through the scale list.
        first = self._bot(1.4)
        self.assertTrue(self._wait(first)[0])
        second = self._bot(0.5)
        self.assertTrue(self._wait(second)[0])
        self.assertEqual(FishingBot._classic_fish_last_scale, 0.5)
        self.polls = 0
        found, per_poll = self._wait(first)
        self.assertTrue(found)
        self.assertEqual(per_poll, [1])

    def test_without_a_known_scale_every_poll_tries_them_all(self):
        found, per_poll = self._wait(self._bot(None), timeout=0.3)
        self.assertFalse(found)
        self.assertEqual(set(per_poll), {len(FishingBot._classic_fish_scaled_cache)})


# ---------------------------------------------------------------- 4.2 jigsaw grabs

def _equal(a, b):
    return (a.grid_found, a.grid_bounds, a.cell_centers, a.board_mask, a.confidence, a.filled_cells) == \
           (b.grid_found, b.grid_bounds, b.cell_centers, b.board_mask, b.confidence, b.filled_cells)


class BoardAreaTests(unittest.TestCase):
    """4.2: reading the board from a capture of only the grid and its margin
    gives exactly what the whole window gives."""

    BOUNDS = LABELS["board_layout"]["grid_bounds"]

    def _crop_read(self, detector, frame):
        h, w = frame.shape[:2]
        area = detector.board_area(w, h)
        if area is None:
            return detector.detect_grid(frame)
        x, y, aw, ah = area
        self.assertLess(aw * ah, w * h)
        return detector.detect_grid(frame[y:y + ah, x:x + aw].copy(), origin=(x, y), window_size=(w, h))

    def test_real_boards_read_the_same(self):
        detector = JigsawDetector({"jigsaw_grid_bounds": list(self.BOUNDS)})
        for t in LABELS["board"]:
            frame = image(f"board_{float(t):07.2f}.png")
            self.assertTrue(_equal(self._crop_read(detector, frame), detector.detect_grid(frame)), t)

    def test_grid_at_the_window_edges_reads_the_same(self):
        frame = image("board_0551.02.png")
        x, y, w, h = self.BOUNDS
        for dx, dy, cut_right, cut_bottom in ((-x, -y, 0, 0), (-x - 1, 0, 0, 0), (0, 0, 1, 1), (0, 0, 3, 0)):
            # Move the grid to the window's corner, or let it stick out a little
            shifted = np.roll(frame, (dy, dx), axis=(0, 1))
            win = shifted[:max(1, y + dy + h - cut_bottom), :max(1, x + dx + w - cut_right)] \
                if cut_right or cut_bottom else shifted
            detector = JigsawDetector({"jigsaw_grid_bounds": [x + dx, y + dy, w, h]})
            self.assertTrue(_equal(self._crop_read(detector, win), detector.detect_grid(win)),
                            (dx, dy, cut_right, cut_bottom))

    def test_misconfigured_grid_has_no_area(self):
        detector = JigsawDetector({"jigsaw_grid_bounds": [400, 10, 240, 160]})
        self.assertIsNone(detector.board_area(320, 240))

    def test_bot_reads_only_the_area(self):
        frame = image("board_0551.02.png")
        wm = WM()
        h, w = frame.shape[:2]
        wm.rect = (0, 0, w, h)
        bot = JigsawBot(wm, {"confirm_button_pos": (10, 10), "jigsaw_grid_bounds": list(self.BOUNDS)}, bot_id=0)
        bot.mouse_backend = bot.human.mouse = FakeMouse()
        grabs = []

        def grab(x, y, gw, gh):
            grabs.append((x, y, gw, gh))
            return frame[y:y + gh, x:x + gw].copy()
        bot.capture.grab = grab
        self.assertTrue(_equal(bot._read_board(), bot.detector.detect_grid(frame)))
        self.assertEqual(len(grabs), 1)
        self.assertLess(grabs[0][2] * grabs[0][3], w * h)

        # A render smaller than asked for: the whole window is read instead
        bot.capture.grab = lambda x, y, gw, gh: frame[y:y + gh - 1, x:x + gw].copy() if gw < w \
            else frame.copy()
        self.assertTrue(_equal(bot._read_board(), bot.detector.detect_grid(frame)))


class ResultPollTests(unittest.TestCase):
    """4.2: without a confirm template the result poll does not grab the
    whole window (nothing in it would be searched)."""

    def test_no_full_grab_without_template(self):
        wm = WM()
        wm.rect = (0, 0, 320, 240)
        bot = JigsawBot(wm, {"confirm_button_pos": (10, 10), "jigsaw_post_complete_wait": 0,
                             "jigsaw_completion_animation_wait": 0, "jigsaw_result_confirm_timeout": 0.15,
                             "jigsaw_completion_poll_interval": 0.01}, bot_id=0)
        bot.mouse_backend = bot.human.mouse = FakeMouse()
        bot.running = True
        bot._result_baseline = np.zeros((16, 40), np.uint8)
        bot._dialog_grab = lambda x, y, w, h: np.zeros((h, w, 3), np.uint8)
        bot.detector.has_confirm_template = lambda discard=False: False
        bot.capture_full_window = lambda: self.fail("whole window grabbed without a template")
        with mock.patch("jigsaw_bot.diag.ENABLED", False):
            bot._handle_completed_board()

    def test_shipped_template_is_still_searched(self):
        wm = WM()
        wm.rect = (0, 0, 320, 240)
        bot = JigsawBot(wm, {"confirm_button_pos": (10, 10), "jigsaw_post_complete_wait": 0,
                             "jigsaw_completion_animation_wait": 0, "jigsaw_result_confirm_timeout": 0.5,
                             "jigsaw_completion_poll_interval": 0.01}, bot_id=0)
        bot.mouse_backend = bot.human.mouse = FakeMouse()
        bot.running = True
        bot._result_baseline = None
        bot.detector.has_confirm_template = lambda discard=False: True
        bot.detector.detect_confirm_button = lambda frame: (100, 120)
        bot.capture_full_window = lambda: np.zeros((240, 320, 3), np.uint8)
        clicked = []
        bot._click_window_point = lambda point, what, **kw: clicked.append(point) or True
        bot._handle_completed_board()
        self.assertEqual(clicked, [(100 + 15, 120)])

    def test_template_presence_is_reported(self):
        detector = JigsawDetector({})
        shipped = any(os.path.exists(os.path.join("assets", "jigsaw", f)) for f in
                      ([detector.OPTIONAL_TEMPLATES["confirm"]] if isinstance(detector.OPTIONAL_TEMPLATES["confirm"], str)
                       else detector.OPTIONAL_TEMPLATES["confirm"]))
        self.assertEqual(detector.has_confirm_template(), detector._load_optional_template("confirm") is not None)
        if not shipped:
            self.assertFalse(detector.has_confirm_template())


# ---------------------------------------------------------------- 4.3 cursor

class CursorWithoutPositionTests(unittest.TestCase):
    """4.3: without a cursor position the whole frame is not color-scored
    (it never named a piece), but colored pixels are still reported so the
    bot treats it as an unknown piece, as before."""

    def test_whole_frame_is_not_scored(self):
        detector = JigsawDetector({})
        frame = image("held_0565.53.png")
        with mock.patch.object(detector, "_score_cursor_piece_colors",
                               side_effect=AssertionError("whole frame scored")):
            result = detector.detect_cursor_piece(frame, None)
        self.assertIsNone(result.piece_id)
        self.assertGreater(result.colored_pixels, 0)

    def test_black_frame_still_reports_nothing_colored(self):
        result = JigsawDetector({}).detect_cursor_piece(np.zeros((240, 320, 3), np.uint8), None)
        self.assertIsNone(result.piece_id)
        self.assertEqual(result.colored_pixels, 0)

    def test_with_a_position_the_piece_is_still_named(self):
        detector = JigsawDetector({})
        lab = LABELS["held"]["565.53"] if "565.53" in LABELS["held"] else next(iter(LABELS["held"].values()))
        t = "565.53" if "565.53" in LABELS["held"] else next(iter(LABELS["held"]))
        frame = image(f"held_{float(t):07.2f}.png")
        x, y, w, h = lab["box"]
        self.assertEqual(detector.detect_cursor_piece(frame, (x + w // 2, y + h // 2)).piece_id, lab["piece"])


if __name__ == "__main__":
    unittest.main()

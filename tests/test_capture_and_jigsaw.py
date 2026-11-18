import time
import unittest

import numpy as np

from _stubs import install, FakeWindowManager, FakeMouse

install()

import screen_capture  # noqa: E402
from screen_capture import WindowCapture, frames_agree  # noqa: E402
from jigsaw_bot import JigsawBot  # noqa: E402
import jigsaw_bot as jigsaw_module  # noqa: E402


def game_frame(seed=1, w=320, h=240):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(h, w, 4), dtype=np.uint8)


class WM(FakeWindowManager):
    def __init__(self):
        super().__init__(320, 240)
        self.selected_window = type("W", (), {"_hWnd": 42})()
        self.foreground = True
        self.last_switch_time = 0.0

    def is_foreground(self):
        return self.foreground


class Harness:
    """A WindowCapture whose 'screen' and 'window render' are both scripted."""

    def __init__(self, mode="auto"):
        self.wm = WM()
        self.window_frame = game_frame(1)      # what the game really shows
        self.screen_frame = self.window_frame  # what the desktop shows at that spot
        self.render_ok = True
        self.logs = []
        self.cap = WindowCapture(self.wm, mode, log=self.logs.append,
                                 window_grabber=self._render)
        self.cap._grab_screen = self._screen
        self.cap._FRAME_REUSE = 0.0

    def _render(self, hwnd, w, h):
        return self.window_frame.copy() if self.render_ok else None

    def _screen(self, x, y, w, h):
        return np.ascontiguousarray(self.screen_frame[y:y + h, x:x + w, :3])


class FramesAgreeTests(unittest.TestCase):
    def test_same_picture_agrees_even_if_partly_covered(self):
        a = game_frame(1)
        b = a.copy()
        b[:80, :120] = 30                       # bot GUI over a corner
        self.assertTrue(frames_agree(a, a.copy()))
        self.assertTrue(frames_agree(a, b))

    def test_black_frozen_or_shifted_does_not_agree(self):
        a = game_frame(1)
        self.assertFalse(frames_agree(np.zeros_like(a), a))        # anti-capture black
        self.assertFalse(frames_agree(a, game_frame(2)))           # something else entirely
        self.assertFalse(frames_agree(np.roll(a, 9, axis=1), a))   # border/DPI offset
        self.assertFalse(frames_agree(a, None))


class WindowCaptureTests(unittest.TestCase):
    def test_auto_starts_on_screen_until_verified(self):
        h = Harness()
        self.assertTrue(h.cap.needs_verification)
        h.wm.foreground = False
        h.screen_frame = game_frame(9)          # covered by another window
        got = h.cap.grab(10, 20, 50, 40)
        self.assertTrue(np.array_equal(got, h.screen_frame[20:60, 10:60, :3]))

    def test_verified_auto_reads_covered_window_but_keeps_fast_path_in_front(self):
        h = Harness()
        self.assertTrue(h.cap.verify())
        self.assertFalse(h.cap.needs_verification)

        calls = {"render": 0}
        real = h.cap._print_window
        h.cap._print_window = lambda *a: (calls.__setitem__("render", calls["render"] + 1), real(*a))[1]

        # In front: plain screen copy, no window render
        h.cap.grab(0, 0, 50, 50)
        self.assertEqual(calls["render"], 0)

        # Covered: the screen shows another window, the render still shows the game
        h.wm.foreground = False
        h.screen_frame = game_frame(9)
        got = h.cap.grab(10, 20, 50, 40)
        self.assertEqual(calls["render"], 1)
        self.assertTrue(np.array_equal(got, h.window_frame[20:60, 10:60, :3]))
        self.assertTrue(got.flags["C_CONTIGUOUS"])

        # Just switched to the front: the desktop may still be stale
        h.wm.foreground = True
        h.wm.last_switch_time = time.perf_counter()
        h.cap.grab(0, 0, 50, 50)
        self.assertEqual(calls["render"], 2)

    def test_broken_render_is_never_adopted(self):
        h = Harness()
        h.window_frame = np.zeros_like(h.window_frame)   # client returns black
        for _ in range(WindowCapture._VERIFY_ATTEMPTS):
            self.assertFalse(h.cap.verify())
        self.assertFalse(h.cap.needs_verification)
        self.assertFalse(h.cap.window_trusted)
        self.assertTrue(any("not usable" in m for m in h.logs))
        h.wm.foreground = False
        got = h.cap.grab(0, 0, 20, 20)
        self.assertTrue(np.array_equal(got, h.screen_frame[:20, :20, :3]))

    def test_render_failure_falls_back_to_screen_in_auto(self):
        h = Harness()
        h.cap.verify()
        h.wm.foreground = False
        h.render_ok = False                      # e.g. minimized
        self.assertIsNotNone(h.cap.grab(0, 0, 20, 20))

    def test_explicit_modes(self):
        h = Harness("window")
        h.wm.foreground = True
        h.screen_frame = game_frame(9)
        got = h.cap.grab(0, 0, 20, 20)
        self.assertTrue(np.array_equal(got, h.window_frame[:20, :20, :3]))
        h.render_ok = False
        self.assertIsNone(h.cap.grab(0, 0, 20, 20))

        h = Harness("screen")
        h.wm.foreground = False
        self.assertFalse(h.cap.needs_verification)
        got = h.cap.grab(0, 0, 20, 20)
        self.assertTrue(np.array_equal(got, h.screen_frame[:20, :20, :3]))

    def test_rectangle_over_the_edge_is_padded(self):
        h = Harness("window")
        got = h.cap.grab(300, 230, 40, 30)
        self.assertEqual(got.shape, (30, 40, 3))
        self.assertTrue(np.array_equal(got[:10, :20], h.window_frame[230:240, 300:320, :3]))
        self.assertEqual(int(got[10:, :].sum()), 0)
        self.assertIsNone(h.cap.grab(0, 0, 0, 10))


class JigsawMultiWindowTests(unittest.TestCase):
    def _bot(self):
        wm = WM()
        wm.rect = (100, 50, 320, 240)
        bot = JigsawBot(wm, {"confirm_button_pos": (10, 10)}, bot_id=0)
        mouse = FakeMouse()
        bot.mouse_backend = mouse
        bot.human.mouse = mouse
        bot.capture.grab = lambda x, y, w, h: np.zeros((h, w, 3), np.uint8)
        bot.running = True
        return bot, mouse, wm

    def test_cursor_is_brought_back_before_looking_at_the_piece(self):
        bot, mouse, wm = self._bot()
        mouse.pos = (900, 700)                  # another window's bot moved the cursor
        seen = []
        bot.detector.detect_cursor_piece = lambda frame, cur: seen.append((mouse.pos, cur)) or "piece"
        self.assertEqual(bot._detect_cursor_piece((60, 40)), "piece")
        self.assertEqual(seen[0][0], (160, 90))  # cursor was back in this window at capture time
        self.assertEqual(wm.activations, 1)

        # Already in place (single window): nothing moves, no focus change
        bot._detect_cursor_piece((60, 40))
        self.assertEqual(wm.activations, 1)

    def test_discard_right_click_lands_in_own_window(self):
        bot, mouse, _ = self._bot()
        mouse.pos = (900, 700)
        bot._right_click_current_position("Discard", (60, 40))
        self.assertEqual(mouse.clicks, [((160, 90), "right")])

    def test_nothing_is_sent_when_the_window_cannot_be_focused(self):
        bot, mouse, wm = self._bot()
        logs = []
        bot.on_status_update = logs.append
        wm.activate_window = lambda force_activate=False: False   # the OS refused
        mouse.pos = (900, 700)                                    # over another client
        bot._click_window_point((30, 30), "Place")
        bot._right_click_current_position("Discard", (60, 40))
        bot._open_crate_and_confirm((40, 40), "normal")
        self.assertEqual((mouse.pos, mouse.clicks), ((900, 700), []))
        self.assertEqual(sum("Could not bring the game window" in m for m in logs), 1)   # said once

    def test_nothing_is_sent_after_stop(self):
        bot, mouse, _ = self._bot()
        bot.running = False
        mouse.pos = (900, 700)
        bot._click_window_point((30, 30), "Place")
        bot._right_click_current_position("Discard", (60, 40))
        self.assertEqual((mouse.pos, mouse.clicks), ((900, 700), []))

    def test_dry_run_never_moves_the_cursor(self):
        bot, mouse, _ = self._bot()
        bot.dry_run = True
        mouse.pos = (900, 700)
        bot.detector.detect_cursor_piece = lambda frame, cur: None
        bot._detect_cursor_piece((60, 40))
        bot._right_click_current_position("Discard", (60, 40))
        self.assertEqual((mouse.pos, mouse.clicks), ((900, 700), []))

    def test_f5_keeps_all_windows_in_step(self):
        a, _, _ = self._bot()
        b, _, _ = self._bot()
        a.paused = True                          # one bot paused itself
        jigsaw_module._listener_subscribers[:] = [a, b]
        try:
            jigsaw_module._last_f5 = float("-inf")
            jigsaw_module._shared_on_press(jigsaw_module.Key.f5)
            self.assertEqual((a.paused, b.paused), (False, False))
            # A held F5 repeats at once: the repeat must not toggle again
            jigsaw_module._shared_on_press(jigsaw_module.Key.f5)
            self.assertEqual((a.paused, b.paused), (False, False))
            jigsaw_module._last_f5 = float("-inf")   # a separate press later
            jigsaw_module._shared_on_press(jigsaw_module.Key.f5)
            self.assertEqual((a.paused, b.paused), (True, True))
        finally:
            jigsaw_module._listener_subscribers[:] = []

    def test_crash_in_loop_is_reported(self):
        bot, _, _ = self._bot()
        logs, stops = [], []
        bot.on_status_update = logs.append
        bot.on_bot_stop = stops.append
        bot.config["jigsaw_grid_bounds"] = [0, 0, 10, 10]
        bot._run_loop = lambda: (_ for _ in ()).throw(ValueError("boom"))
        jigsaw_module.get_solver = lambda: object()
        bot.dry_run = True
        bot.start()
        self.assertTrue(any("boom" in m for m in logs))
        self.assertEqual(stops, [0])


if __name__ == "__main__":
    unittest.main()

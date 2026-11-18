"""Shared window / capture policy against a scripted fake OS layer.

WindowManager and WindowCapture contain the logic both operating systems use
(activation retries, rect caching, window ordering, capture mode choice).
Here they run against FakeNative, so these tests behave the same on Windows
and Linux and pin the behavior any OS-specific fix must keep."""

import time
import unittest

import numpy as np

from _stubs import install

install()

import platforms  # noqa: E402
import screen_capture  # noqa: E402
from screen_capture import WindowCapture  # noqa: E402
from window_manager import WindowManager, window_size_mismatch  # noqa: E402


class FakeWin:
    def __init__(self, handle, title, visible=True):
        self._hWnd = handle
        self.title = title
        self.visible = visible


class FakeNative:
    """Implements the platforms contract; records the requests it gets."""

    def __init__(self):
        self.windows = {}            # handle -> FakeWin
        self.rects = {}              # handle -> rect
        self.minimized = set()
        self.foreground = 0
        self.honor_plain = True      # does set_foreground work?
        self.honor_force = True
        self.calls = []
        self.screen_reliable = True
        self.is_available = True
        self.frame = None

    def add(self, handle, title, rect=(10, 20, 800, 600), visible=True):
        self.windows[handle] = FakeWin(handle, title, visible)
        self.rects[handle] = rect
        return self.windows[handle]

    # --- contract
    def available(self):
        return self.is_available

    def list_windows(self):
        return list(self.windows.values())

    def is_window(self, h):
        return h in self.windows

    def get_foreground(self):
        return self.foreground

    def set_foreground(self, h):
        self.calls.append(("set", h))
        if self.honor_plain:
            self.foreground = h

    def force_foreground(self, h):
        self.calls.append(("force", h))
        if self.honor_force:
            self.foreground = h

    def is_minimized(self, h):
        return h in self.minimized

    def restore(self, h):
        self.calls.append(("restore", h))
        self.minimized.discard(h)

    def get_window_rect(self, h):
        self.calls.append(("rect", h))
        return self.rects.get(h)

    def render_window(self, h, w, hh):
        self.calls.append(("render", h))
        return self.frame

    def screen_capture_reliable(self):
        return self.screen_reliable


class NativeSwap(unittest.TestCase):
    def setUp(self):
        self._saved = platforms.native
        self.os = FakeNative()
        platforms.native = self.os

    def tearDown(self):
        platforms.native = self._saved

    def manager(self, handle):
        wm = WindowManager()
        wm.selected_window = self.os.windows[handle]
        return wm


class WindowListTests(NativeSwap):
    def test_metin2_first_stable_order_and_duplicate_suffixes(self):
        self.os.add(30, "Metin2")
        self.os.add(5, "Browser")
        self.os.add(10, "Metin2")
        self.os.add(7, "")                       # untitled: skipped
        self.os.add(8, "Hidden", visible=False)  # invisible: skipped
        self.os.add(9, "Mt2 Server")
        names = [(n, w._hWnd) for n, w in WindowManager.get_all_windows()]
        self.assertEqual(names, [("Metin2 (1)", 10), ("Metin2 (2)", 30), ("Mt2 Server", 9), ("Browser", 5)])

    def test_no_os_layer_means_no_windows(self):
        platforms.native = None
        self.assertEqual(WindowManager.get_all_windows(), [])


class ActivationTests(NativeSwap):
    def test_already_in_front_sends_nothing(self):
        self.os.add(1, "Metin2")
        self.os.foreground = 1
        wm = self.manager(1)
        self.assertTrue(wm.activate_window())
        self.assertEqual([c for c in self.os.calls if c[0] in ("set", "force")], [])

    def test_plain_request_then_confirmed(self):
        self.os.add(1, "Metin2")
        wm = self.manager(1)
        before = time.perf_counter()
        self.assertTrue(wm.activate_window())
        self.assertEqual([c for c in self.os.calls if c[0] in ("set", "force")], [("set", 1)])
        self.assertGreaterEqual(wm.last_switch_time, before)
        self.assertTrue(wm.is_foreground())

    def test_ignored_plain_request_falls_back_to_force(self):
        self.os.add(1, "Metin2")
        self.os.honor_plain = False
        wm = self.manager(1)
        self.assertTrue(wm.activate_window())
        self.assertEqual([c for c in self.os.calls if c[0] in ("set", "force")], [("set", 1), ("force", 1)])

    def test_gives_up_after_three_attempts(self):
        self.os.add(1, "Metin2")
        self.os.honor_plain = self.os.honor_force = False
        wm = self.manager(1)
        self.assertFalse(wm.activate_window())
        self.assertEqual([c[0] for c in self.os.calls if c[0] in ("set", "force")], ["set", "force", "force"])

    def test_minimized_window_is_restored_first(self):
        self.os.add(1, "Metin2")
        self.os.minimized.add(1)
        wm = self.manager(1)
        self.assertTrue(wm.activate_window())
        self.assertEqual(self.os.calls[0], ("restore", 1))

    def test_closed_window(self):
        self.os.add(1, "Metin2")
        wm = self.manager(1)
        del self.os.windows[1]
        self.assertFalse(wm.is_window_available())
        self.assertFalse(wm.activate_window())
        self.assertEqual([c for c in self.os.calls if c[0] in ("set", "force")], [])

    def test_no_os_layer_is_safe(self):
        self.os.add(1, "Metin2")
        wm = self.manager(1)
        platforms.native = None
        self.assertFalse(wm.activate_window())
        self.assertFalse(wm.is_foreground())
        self.assertFalse(wm.is_window_available())


class RectTests(NativeSwap):
    def test_cached_for_50ms_and_invalidated(self):
        self.os.add(1, "Metin2", rect=(1, 2, 3, 4))
        wm = self.manager(1)
        self.assertEqual(wm.get_window_rect(), (1, 2, 3, 4))
        self.os.rects[1] = (5, 6, 7, 8)
        self.assertEqual(wm.get_window_rect(), (1, 2, 3, 4))   # cached
        wm.invalidate_rect_cache()
        self.assertEqual(wm.get_window_rect(), (5, 6, 7, 8))
        self.assertEqual(sum(1 for c in self.os.calls if c[0] == "rect"), 2)


class CaptureModeTests(NativeSwap):
    def _cap(self, mode="auto"):
        self.os.add(1, "Metin2", rect=(0, 0, 64, 48))
        wm = self.manager(1)
        return WindowCapture(wm, mode)

    def test_desktop_copy_available_keeps_auto_unverified(self):
        cap = self._cap()
        self.assertEqual(cap.requested, "auto")
        self.assertTrue(cap.needs_verification)
        self.assertFalse(cap.window_trusted)

    def test_no_desktop_copy_reads_the_window_directly(self):
        self.os.screen_reliable = False         # XWayland
        for requested in ("auto", "screen", "window"):
            cap = self._cap(requested)
            self.assertEqual(cap.requested, "window", requested)
            self.assertTrue(cap.window_trusted)
            self.assertFalse(cap.needs_verification)

        rng = np.random.default_rng(3)
        self.os.frame = rng.integers(0, 255, size=(48, 64, 4), dtype=np.uint8)
        got = cap.grab(4, 6, 20, 10)
        self.assertTrue(np.array_equal(got, self.os.frame[6:16, 4:24, :3]))
        self.assertTrue(got.flags["C_CONTIGUOUS"])

    def test_os_layer_unavailable_means_screen_only(self):
        self.os.is_available = False
        self.assertEqual(self._cap("window").requested, "screen")
        platforms.native = None
        self.assertEqual(self._cap("auto").requested, "screen")

    def test_print_window_uses_the_os_render(self):
        self.os.frame = np.zeros((2, 2, 4), dtype=np.uint8)
        self.assertIs(screen_capture.print_window(1, 2, 2), self.os.frame)
        platforms.native = None
        self.assertIsNone(screen_capture.print_window(1, 2, 2))


class WindowSizeCheckTests(unittest.TestCase):
    """Start check: every selected client must have the same size."""

    def test_same_size_passes(self):
        self.assertIsNone(window_size_mismatch([("Metin2 (1)", (1024, 768)), ("Metin2 (2)", (1024, 768))]))

    def test_single_or_no_window_passes(self):
        self.assertIsNone(window_size_mismatch([]))
        self.assertIsNone(window_size_mismatch([("Metin2", (800, 600))]))

    def test_different_size_lists_every_window(self):
        msg = window_size_mismatch([("Metin2 (1)", (1024, 768)), ("Metin2 (2)", (800, 600)),
                                    ("Metin2 (3)", (1024, 768))])
        self.assertIsNotNone(msg)
        for line in ("Metin2 (1): 1024 x 768", "Metin2 (2): 800 x 600", "Metin2 (3): 1024 x 768"):
            self.assertIn(line, msg)

if __name__ == "__main__":
    unittest.main()

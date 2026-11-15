"""uinput backend logic that needs no device: kernel struct layouts and ioctl
numbers, coordinate mapping, key lookup, and the Linux backend selection.
Linux only; nothing here opens /dev/uinput."""

import struct
import sys
import unittest

import pytest

from _stubs import install

install()

pytestmark = pytest.mark.linux

if sys.platform.startswith("linux"):
    from platforms import uinput  # noqa: E402  (imports fcntl: Linux only)


class _Key:
    """Looks like a pynput Key member."""

    def __init__(self, name):
        self.name = name


def _keyboard(char_lookup=None):
    kb = object.__new__(uinput.UInputKeyboard)   # no device
    kb._char_lookup = char_lookup
    return kb


def _mouse(size):
    m = object.__new__(uinput.UInputMouse)
    m._size = size
    return m


class KernelAbiTests(unittest.TestCase):
    """Values from linux/input.h and linux/uinput.h on x86_64."""

    def test_struct_sizes(self):
        self.assertEqual(struct.calcsize(uinput._EVENT_FMT), 24)
        self.assertEqual(struct.calcsize(uinput._SETUP_FMT), 92)
        self.assertEqual(struct.calcsize(uinput._ABS_SETUP_FMT), 28)

    def test_ioctl_numbers(self):
        self.assertEqual(uinput.UI_DEV_CREATE, 0x5501)
        self.assertEqual(uinput.UI_DEV_DESTROY, 0x5502)
        self.assertEqual(uinput.UI_DEV_SETUP, 0x405C5503)
        self.assertEqual(uinput.UI_ABS_SETUP, 0x401C5504)
        self.assertEqual(uinput.UI_SET_EVBIT, 0x40045564)
        self.assertEqual(uinput.UI_SET_KEYBIT, 0x40045565)
        self.assertEqual(uinput.UI_SET_RELBIT, 0x40045566)
        self.assertEqual(uinput.UI_SET_ABSBIT, 0x40045567)


class AbsoluteMappingTests(unittest.TestCase):
    def test_every_pixel_round_trips(self):
        # The desktop turns an absolute value into v * size / (ABS_MAX + 1);
        # each target pixel must come back exactly, on any desktop size.
        for w, h in ((2560, 1440), (1920, 1080), (3840, 2160), (4480, 1440), (1366, 768)):
            m = _mouse((w, h))
            for x in range(w):
                ax, _ = m.to_abs(x, 0)
                self.assertEqual(int(ax * w / (uinput.ABS_MAX + 1)), x, (w, x))
            for y in range(h):
                _, ay = m.to_abs(0, y)
                self.assertEqual(int(ay * h / (uinput.ABS_MAX + 1)), y, (h, y))

    def test_outside_the_desktop_is_clamped(self):
        m = _mouse((1920, 1080))
        self.assertEqual(m.to_abs(-50, -1), (0, 0))
        self.assertEqual(m.to_abs(5000, 5000), (uinput.ABS_MAX, uinput.ABS_MAX))


class KeyLookupTests(unittest.TestCase):
    def test_named_keys_from_pynput_and_config_strings(self):
        kb = _keyboard()
        self.assertEqual(kb.code_for(_Key("space")), 57)
        self.assertEqual(kb.code_for(_Key("ctrl")), 29)
        self.assertEqual(kb.code_for(_Key("f5")), 63)
        self.assertEqual(kb.code_for("space"), 57)
        self.assertEqual(kb.code_for("F1"), 59)

    def test_characters_us_fallback(self):
        kb = _keyboard()
        self.assertEqual([kb.code_for(c) for c in "1234"], [2, 3, 4, 5])
        self.assertEqual(kb.code_for("0"), 11)
        self.assertEqual(kb.code_for("g"), 34)
        self.assertEqual(kb.code_for("G"), 34)

    def test_layout_lookup_wins(self):
        kb = _keyboard(lambda ch: {"a": 16}.get(ch))   # AZERTY: 'a' on the Q key
        self.assertEqual(kb.code_for("a"), 16)
        self.assertEqual(kb.code_for("g"), 34)          # lookup has nothing: US fallback

    def test_pynput_keycode_char(self):
        kc = type("KeyCode", (), {"name": None, "char": "3"})()
        self.assertEqual(_keyboard().code_for(kc), 4)

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            _keyboard().code_for("not-a-key")


class LinuxBackendSelectionTests(unittest.TestCase):
    def setUp(self):
        import input_backend
        import platforms
        self.ib = input_backend
        self.platforms = platforms
        self._native = platforms.native
        self._check = uinput.check_access
        platforms.native = object()      # "a Linux desktop is there"

    def tearDown(self):
        self.platforms.native = self._native
        uinput.check_access = self._check

    def test_uinput_preferred_when_permitted(self):
        uinput.check_access = lambda: None
        for requested in ("auto", "uinput", "interception"):
            self.assertEqual(self.ib.probe_backend({"input_backend": requested}), ("uinput", None))

    def test_xtest_with_reason_when_not_permitted(self):
        uinput.check_access = lambda: "no write permission on /dev/uinput"
        self.assertEqual(self.ib.probe_backend({}), ("xtest", "no write permission on /dev/uinput"))

    def test_explicit_xtest(self):
        self.assertEqual(self.ib.probe_backend({"input_backend": "pyautogui"}), ("xtest", None))

    def test_device_failure_falls_back_to_xtest_and_reports(self):
        saved = self.ib._get_uinput_mouse
        self.ib._get_uinput_mouse = lambda: (_ for _ in ()).throw(PermissionError("denied"))
        saved_xtest = self.ib.PynputMouseBackend
        self.ib.PynputMouseBackend = lambda: "xtest-backend"
        try:
            msgs = []
            got = self.ib.create_mouse_backend({}, status_callback=msgs.append, bot_id=1)
            self.assertEqual(got, "xtest-backend")
            self.assertIn("uinput unavailable", msgs[0])
        finally:
            self.ib._get_uinput_mouse = saved
            self.ib.PynputMouseBackend = saved_xtest

    def test_no_os_layer_never_creates_devices(self):
        self.platforms.native = None
        called = []
        saved = self.ib._get_uinput_keyboard, self.ib._get_uinput_mouse
        self.ib._get_uinput_keyboard = lambda: called.append("keyboard")
        self.ib._get_uinput_mouse = lambda: called.append("mouse")
        try:
            self.ib.create_keyboard_controller({})
            self.ib.create_mouse_backend({})
        finally:
            self.ib._get_uinput_keyboard, self.ib._get_uinput_mouse = saved
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()

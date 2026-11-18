"""
Virtual mouse and keyboard through /dev/uinput (Linux).

The Linux counterpart of the Interception driver: the kernel creates two
input devices ("Fishbot virtual mouse/keyboard") and the desktop treats their
events exactly like a real USB mouse and keyboard. That works the same on an
Xorg session and on Wayland (KDE, GNOME), where X11-level injection (XTest)
is restricted.

The mouse is an *absolute* pointer (like a VM's tablet mouse): coordinates
0..65535 cover the whole desktop, so a move lands on the exact pixel with no
pointer acceleration in the way.

Permission: the user needs write access to /dev/uinput. Either an ACL that
is already there (Steam's udev rules add one) or a udev rule, see
docs/LINUX.md.

Implemented with plain ioctl()/write() so no compiled package is needed.
"""

import errno
import fcntl
import os
import struct
import threading
import time
from typing import Dict, Optional, Tuple

import diag

UINPUT_PATH = "/dev/uinput"

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
ABS_X, ABS_Y = 0x00, 0x01
REL_WHEEL = 0x08
BUS_USB = 0x03
ABS_MAX = 65535
_KEY_MAX_USED = 248  # KEY_ESC(1) .. KEY_MICMUTE(248): a full keyboard

# linux/uinput.h ioctls
_IOC_WRITE = 1


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("U") << 8) | nr


_SETUP_FMT = "HHHH80sI"      # struct uinput_setup { input_id; name[80]; ff_effects_max }
_ABS_SETUP_FMT = "H2x6i"     # struct uinput_abs_setup { u16 code; input_absinfo }
_EVENT_FMT = "llHHi"         # struct input_event { timeval; u16 type; u16 code; s32 value }
UI_DEV_CREATE = _ioc(0, 1, 0)
UI_DEV_DESTROY = _ioc(0, 2, 0)
UI_DEV_SETUP = _ioc(_IOC_WRITE, 3, struct.calcsize(_SETUP_FMT))
UI_ABS_SETUP = _ioc(_IOC_WRITE, 4, struct.calcsize(_ABS_SETUP_FMT))
UI_SET_EVBIT = _ioc(_IOC_WRITE, 100, 4)
UI_SET_KEYBIT = _ioc(_IOC_WRITE, 101, 4)
UI_SET_RELBIT = _ioc(_IOC_WRITE, 102, 4)
UI_SET_ABSBIT = _ioc(_IOC_WRITE, 103, 4)

# The desktop needs a moment to pick up a new device; events sent before
# that are dropped.
_DEVICE_SETTLE = 0.5


def check_access() -> Optional[str]:
    reason = _check_access()
    diag.log("ACTION", f"uinput access check: {reason or 'ok'}")
    return reason


def _check_access() -> Optional[str]:
    """None when /dev/uinput can be used, otherwise a readable reason."""
    if not os.path.exists(UINPUT_PATH):
        return f"{UINPUT_PATH} does not exist (load the module: sudo modprobe uinput)"
    if not os.access(UINPUT_PATH, os.W_OK):
        return f"no write permission on {UINPUT_PATH} (see docs/LINUX.md for the udev rule)"
    return None


class _Device:
    """One uinput device. Writes are serialized by a lock."""

    def __init__(self, name: str, product: int, evbits, keybits=(), relbits=(), abs_axes=()):
        self._lock = threading.Lock()
        self._fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        try:
            for ev in evbits:
                fcntl.ioctl(self._fd, UI_SET_EVBIT, ev)
            for code in keybits:
                fcntl.ioctl(self._fd, UI_SET_KEYBIT, code)
            for code in relbits:
                fcntl.ioctl(self._fd, UI_SET_RELBIT, code)
            for code, lo, hi in abs_axes:
                fcntl.ioctl(self._fd, UI_SET_ABSBIT, code)
                fcntl.ioctl(self._fd, UI_ABS_SETUP,
                            struct.pack(_ABS_SETUP_FMT, code, 0, lo, hi, 0, 0, 0))
            fcntl.ioctl(self._fd, UI_DEV_SETUP,
                        struct.pack(_SETUP_FMT, BUS_USB, 0x1, product, 1,
                                    name.encode()[:79], 0))
            fcntl.ioctl(self._fd, UI_DEV_CREATE)
        except Exception:
            diag.exc(f"creating uinput device '{name}'")
            os.close(self._fd)
            self._fd = None
            raise
        self._created = time.monotonic()
        diag.log("ACTION", f"uinput device '{name}' created (fd={self._fd})")

    def wait_ready(self) -> None:
        left = _DEVICE_SETTLE - (time.monotonic() - self._created)
        if left > 0:
            time.sleep(left)

    def emit(self, *events: Tuple[int, int, int]) -> None:
        """Writes the events followed by SYN_REPORT, as one frame."""
        buf = b"".join(struct.pack(_EVENT_FMT, 0, 0, t, c, v) for t, c, v in events)
        buf += struct.pack(_EVENT_FMT, 0, 0, EV_SYN, SYN_REPORT, 0)
        with self._lock:
            if self._fd is None:
                raise OSError(errno.EBADF, "uinput device closed")
            os.write(self._fd, buf)

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                try:
                    fcntl.ioctl(self._fd, UI_DEV_DESTROY)
                finally:
                    os.close(self._fd)
                    self._fd = None


# --------------------------------------------------------------------- mouse

class UInputMouse:
    """Absolute virtual mouse. Same interface as the other mouse backends."""

    name = "uinput"
    display_name = "uinput"

    def __init__(self, desktop_size: Optional[Tuple[int, int]] = None, cursor_query=None):
        """``desktop_size``: (w, h) of the whole desktop the 0..65535 range
        maps to. ``cursor_query``: callable returning the real pointer
        position or None."""
        self._dev = _Device("Fishbot virtual mouse", 0x1,
                            evbits=(EV_KEY, EV_ABS, EV_REL),
                            keybits=(BTN_LEFT, BTN_RIGHT, BTN_MIDDLE),
                            relbits=(REL_WHEEL,),
                            abs_axes=((ABS_X, 0, ABS_MAX), (ABS_Y, 0, ABS_MAX)))
        self._size = desktop_size or (1920, 1080)
        diag.log("ACTION", f"uinput mouse maps 0..{ABS_MAX} onto desktop {self._size} "
                           f"(given {desktop_size})")
        self._cursor_query = cursor_query
        self._last = (0, 0)
        self._dev.wait_ready()

    def set_desktop_size(self, size: Tuple[int, int]) -> None:
        if size and size[0] > 0 and size[1] > 0:
            if diag.ENABLED and tuple(size) != tuple(self._size):
                diag.log("ACTION", f"uinput desktop size {self._size} -> {tuple(size)}")
            self._size = (int(size[0]), int(size[1]))

    def to_abs(self, x: float, y: float) -> Tuple[int, int]:
        # The desktop maps the range to pixels as v * size / (ABS_MAX + 1);
        # aim at the middle of the target pixel.
        w, h = self._size
        ax = int((float(x) + 0.5) * (ABS_MAX + 1) / w)
        ay = int((float(y) + 0.5) * (ABS_MAX + 1) / h)
        return max(0, min(ABS_MAX, ax)), max(0, min(ABS_MAX, ay))

    def move_to(self, x: float, y: float) -> None:
        ax, ay = self.to_abs(x, y)
        self._dev.emit((EV_ABS, ABS_X, ax), (EV_ABS, ABS_Y, ay))
        self._last = (int(x), int(y))

    def position(self) -> Tuple[int, int]:
        if self._cursor_query is not None:
            try:
                pos = self._cursor_query()
                if pos is not None:
                    return pos
            except Exception:
                pass
        return self._last

    def mouse_down(self, button: str = "left") -> None:
        self._dev.emit((EV_KEY, _BUTTONS.get(button, BTN_LEFT), 1))

    def mouse_up(self, button: str = "left") -> None:
        self._dev.emit((EV_KEY, _BUTTONS.get(button, BTN_LEFT), 0))

    def click(self, button: str = "left") -> None:
        self.mouse_down(button)
        self.mouse_up(button)

    def close(self) -> None:
        self._dev.close()


_BUTTONS = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}


# ------------------------------------------------------------------ keyboard

# Named keys (pynput Key names and the strings the config uses).
NAMED_KEYS: Dict[str, int] = {
    "esc": 1, "escape": 1, "backspace": 14, "tab": 15, "enter": 28, "return": 28,
    "space": 57, "caps_lock": 58,
    "ctrl": 29, "ctrl_l": 29, "ctrl_r": 97,
    "shift": 42, "shift_l": 42, "shift_r": 54,
    "alt": 56, "alt_l": 56, "alt_r": 100, "alt_gr": 100,
    "cmd": 125, "cmd_l": 125, "cmd_r": 126,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "home": 102, "end": 107, "page_up": 104, "page_down": 109,
    "insert": 110, "delete": 111,
}

# Fallback for characters when the X keymap can't be asked: US QWERTY
# physical keys.
_US_CHARS: Dict[str, int] = {" ": 57, "-": 12, "=": 13, "[": 26, "]": 27, ";": 39,
                             "'": 40, "`": 41, "\\": 43, ",": 51, ".": 52, "/": 53}
for _i, _c in enumerate("1234567890"):
    _US_CHARS[_c] = 2 + _i
for _row, _start in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    for _i, _c in enumerate(_row):
        _US_CHARS[_c] = _start + _i


def key_name(key) -> str:
    """Normalizes a pynput Key / KeyCode or a string to a lookup name."""
    name = getattr(key, "name", None)          # pynput Key enum member
    if name:
        return str(name).lower()
    char = getattr(key, "char", None)          # pynput KeyCode
    if char is not None:
        return str(char)
    s = str(key)
    return s if len(s) == 1 else s.lower()


class UInputKeyboard:
    """Virtual keyboard with the pynput Controller interface (press/release).

    ``char_lookup``: callable(char) -> evdev code or None that knows the
    desktop's keyboard layout (x11 keymap); without it US QWERTY is assumed.
    For hotkeys this means "press the key that carries this character", the
    same thing pynput does on Windows."""

    name = "uinput"

    def __init__(self, char_lookup=None):
        self._dev = _Device("Fishbot virtual keyboard", 0x2,
                            evbits=(EV_KEY,), keybits=range(1, _KEY_MAX_USED + 1))
        self._char_lookup = char_lookup
        self._dev.wait_ready()

    def code_for(self, key) -> int:
        name = key_name(key)
        if name in NAMED_KEYS:
            return NAMED_KEYS[name]
        if len(name) == 1:
            ch = name.lower()
            if self._char_lookup is not None:
                try:
                    code = self._char_lookup(ch)
                    if code:
                        return code
                except Exception:
                    pass
            if ch in _US_CHARS:
                return _US_CHARS[ch]
        raise ValueError(f"no key code for {key!r}")

    def press(self, key) -> None:
        self._dev.emit((EV_KEY, self.code_for(key), 1))

    def release(self, key) -> None:
        self._dev.emit((EV_KEY, self.code_for(key), 0))

    def close(self) -> None:
        self._dev.close()

"""
Linux primitives over X11 (python-xlib). See platforms/__init__.py for the
contract.

Metin2 runs under Wine, and Wine draws its windows through X11 — on a
Wayland desktop (KDE, GNOME) that is XWayland. So the game window is an
ordinary X11 window wherever it runs, and these primitives work on an Xorg
session and on a Wayland session alike.

Differences from Windows worth knowing:

- Rectangle: the X11 window is the game's *client area*. The title bar is
  drawn by the desktop's window manager and is not part of it, while on
  Windows GetWindowRect includes it. Coordinates are relative to whatever
  rectangle this module reports, so captures and clicks stay consistent; only
  positions saved on Windows are offset by the title bar when reused here.
- Capture: XWayland cannot copy the desktop (reading the root window fails),
  so ``screen_capture_reliable()`` is False there and the game is read from
  its own window (``render_window``, the counterpart of PrintWindow). It
  also works while the window is covered by another one.
- Each thread gets its own X connection: bots capture concurrently and
  python-xlib serialises everything that goes through one connection.

Wine must use its X11 driver (the default whenever $DISPLAY is set). With
the Wayland driver (``winewayland``) the game window is invisible to this
module.
"""

import os
import sys
import threading
from typing import List, Optional, Tuple

import numpy as np

try:
    from Xlib import X, Xatom
    from Xlib import display as _xdisplay
    from Xlib import error as _xerror
    from Xlib.protocol import event as _xevent
except Exception:  # python-xlib not installed (Windows / tests)
    X = Xatom = _xdisplay = _xerror = _xevent = None

_ICONIC_STATE = 3
_SOURCE_PAGER = 2   # _NET_ACTIVE_WINDOW source: "pager", exempt from focus-stealing prevention

_tls = threading.local()


def available() -> bool:
    """True when an X server ($DISPLAY) can be reached."""
    if _xdisplay is None or not os.environ.get("DISPLAY"):
        return False
    try:
        _display()
        return True
    except Exception:
        return False


def _display():
    d = getattr(_tls, "display", None)
    if d is None:
        d = _xdisplay.Display()
        # Requests without a reply (send_event, map) report errors
        # asynchronously; a window that closed meanwhile is not worth a
        # traceback on stderr.
        d.set_error_handler(lambda *_a, **_k: None)
        _tls.display = d
        _tls.atoms = {}
    return d


def _atom(name: str) -> int:
    _display()
    atoms = _tls.atoms
    a = atoms.get(name)
    if a is None:
        a = atoms[name] = _tls.display.intern_atom(name)
    return a


def _root():
    return _display().screen().root


def _win(handle):
    return _display().create_resource_object("window", int(handle))


def _prop(window, name: str):
    try:
        p = window.get_full_property(_atom(name), X.AnyPropertyType)
    except _xerror.XError:
        return None
    return p.value if p is not None else None


def _title(window) -> str:
    v = _prop(window, "_NET_WM_NAME")
    if v is None:
        v = _prop(window, "WM_NAME")
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def _send_root_message(window, type_name: str, data) -> None:
    ev = _xevent.ClientMessage(window=window, client_type=_atom(type_name),
                               data=(32, (list(data) + [0, 0, 0, 0, 0])[:5]))
    _root().send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    _display().flush()


# ------------------------------------------------------------------ windows

class X11Window:
    """A top-level X11 window with the pygetwindow interface the GUI uses."""

    def __init__(self, handle: int, title: str = ""):
        self._hWnd = int(handle)
        self.title = title

    def __repr__(self):
        return f"X11Window(0x{self._hWnd:x}, {self.title!r})"

    def __eq__(self, other):
        return isinstance(other, X11Window) and other._hWnd == self._hWnd

    def __hash__(self):
        return hash(self._hWnd)

    @property
    def visible(self) -> bool:
        # Windows' IsWindowVisible is still true for a minimized window;
        # an iconified X11 window is unmapped, so count it as visible too.
        try:
            attrs = _win(self._hWnd).get_attributes()
        except Exception:
            return False
        return attrs.map_state == X.IsViewable or is_minimized(self._hWnd)

    @property
    def isMinimized(self) -> bool:
        return is_minimized(self._hWnd)

    def _rect(self) -> Tuple[int, int, int, int]:
        return get_window_rect(self._hWnd) or (0, 0, 0, 0)

    left = property(lambda self: self._rect()[0])
    top = property(lambda self: self._rect()[1])
    width = property(lambda self: self._rect()[2])
    height = property(lambda self: self._rect()[3])

    def minimize(self) -> None:
        # XIconifyWindow: ask the window manager via WM_CHANGE_STATE
        _send_root_message(_win(self._hWnd), "WM_CHANGE_STATE", [_ICONIC_STATE])

    def restore(self) -> None:
        restore(self._hWnd)

    def activate(self) -> None:
        set_foreground(self._hWnd)


def list_windows() -> List[X11Window]:
    """Managed top-level windows (_NET_CLIENT_LIST), unfiltered."""
    ids = _prop(_root(), "_NET_CLIENT_LIST")
    if ids is None:
        return []
    out = []
    for wid in ids:
        try:
            out.append(X11Window(wid, _title(_win(wid))))
        except Exception:
            continue
    return out


def is_window(handle) -> bool:
    if not handle:
        return False
    try:
        _win(handle).get_geometry()
        return True
    except Exception:
        return False


def get_foreground() -> int:
    v = _prop(_root(), "_NET_ACTIVE_WINDOW")
    return int(v[0]) if v is not None and len(v) else 0


def set_foreground(handle) -> None:
    """_NET_ACTIVE_WINDOW request to the window manager (it also un-minimizes)."""
    _send_root_message(_win(handle), "_NET_ACTIVE_WINDOW",
                       [_SOURCE_PAGER, X.CurrentTime, get_foreground()])


def force_foreground(handle) -> None:
    """Retry path: make sure the window is mapped and raised, then ask again."""
    w = _win(handle)
    try:
        w.map()
        w.configure(stack_mode=X.Above)
    except Exception:
        pass
    set_foreground(handle)


def is_minimized(handle) -> bool:
    w = _win(handle)
    state = _prop(w, "WM_STATE")
    if state is not None and len(state) and int(state[0]) == _ICONIC_STATE:
        return True
    net_state = _prop(w, "_NET_WM_STATE")
    return net_state is not None and _atom("_NET_WM_STATE_HIDDEN") in list(net_state)


def restore(handle) -> None:
    try:
        _win(handle).map()
    except Exception:
        pass
    set_foreground(handle)


def get_window_rect(handle) -> Optional[Tuple[int, int, int, int]]:
    """Client area in desktop coordinates (left, top, width, height)."""
    if not handle:
        return None
    try:
        w = _win(handle)
        g = w.get_geometry()
        origin = w.translate_coords(_root(), 0, 0)
    except Exception:
        return None
    return (-origin.x, -origin.y, int(g.width), int(g.height))


# ------------------------------------------------------------------ capture

def render_window(handle, width: int, height: int) -> Optional[np.ndarray]:
    """The window's own pixels as a (h, w, 4) BGRA array, or None.

    Reads the window's backing pixmap, so the picture is right even when other
    windows cover it (not when it is minimized: then it is unmapped)."""
    if not handle or width <= 0 or height <= 0:
        return None
    try:
        w = _win(handle)
        g = w.get_geometry()
        width, height = min(int(width), int(g.width)), min(int(height), int(g.height))
        if width <= 0 or height <= 0:
            return None
        img = w.get_image(0, 0, width, height, X.ZPixmap, 0xFFFFFFFF)
    except Exception:
        return None
    data = img.data
    if len(data) != width * height * 4:  # only 24/32-bit visuals (4 bytes per pixel)
        return None
    # ZPixmap on a little-endian server is B, G, R, X — same layout as the
    # Windows DIB, so callers slice [:, :, :3] for BGR in both cases.
    return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)


def render_window_area(handle, x: int, y: int, width: int, height: int) -> Optional[np.ndarray]:
    """Only the rectangle (x, y, width, height) of the window's own pixels,
    as a (height, width, 4) BGRA array; the part outside the window is black.
    None when none of it lies inside the window, or on failure.

    The X server sends just that rectangle: a 260x200 panel is 0.4 ms where
    the whole 1280x720 window is 7 ms (python-xlib reads the reply in Python,
    so the cost grows with the bytes sent)."""
    if not handle or width <= 0 or height <= 0:
        return None
    try:
        w = _win(handle)
        g = w.get_geometry()
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(int(g.width), int(x) + width), min(int(g.height), int(y) + height)
        if x2 <= x1 or y2 <= y1:
            return None
        img = w.get_image(x1, y1, x2 - x1, y2 - y1, X.ZPixmap, 0xFFFFFFFF)
    except Exception:
        return None
    cw, ch = x2 - x1, y2 - y1
    data = img.data
    if len(data) != cw * ch * 4:  # only 24/32-bit visuals (4 bytes per pixel)
        return None
    part = np.frombuffer(data, dtype=np.uint8).reshape(ch, cw, 4)
    if (cw, ch) == (width, height):
        return part
    out = np.zeros((height, width, 4), dtype=np.uint8)
    out[y1 - y:y2 - y, x1 - x:x2 - x] = part
    return out


def screen_capture_reliable() -> bool:
    """Whether copying the desktop (mss) shows the game. XWayland cannot
    read the root window, so only a real Xorg session (or Xvfb) can.
    Asks the X server itself: $WAYLAND_DISPLAY says nothing about the X
    server $DISPLAY points at. (python-xlib's has_extension() only knows
    extensions it has bindings for, so it would always say no here.)"""
    try:
        ext = _display().query_extension("XWAYLAND")   # None when absent
    except Exception:
        return False
    return not (ext is not None and ext.present)


# ------------------------------------------------------------------ pointer / desktop

def cursor_position() -> Optional[Tuple[int, int]]:
    """Pointer position in desktop coordinates. Under XWayland this is only
    current while the pointer is over an X11 window (e.g. the game)."""
    try:
        p = _root().query_pointer()
        return (int(p.root_x), int(p.root_y))
    except Exception:
        return None


def desktop_size() -> Tuple[int, int]:
    s = _display().screen()
    return (int(s.width_in_pixels), int(s.height_in_pixels))


def evdev_code_for_char(ch: str) -> Optional[int]:
    """Kernel key code of the key that carries ``ch`` in the current keyboard
    layout (X keycodes are kernel codes + 8), or None."""
    if len(ch) != 1 or not (0x20 <= ord(ch) <= 0xFF):
        return None
    try:
        keycode = _display().keysym_to_keycode(ord(ch))  # Latin-1 keysym == code point
    except Exception:
        return None
    return keycode - 8 if keycode and keycode > 8 else None


# Diagnostic log: the requests that change the desktop are traced.
import diag  # noqa: E402

diag.trace_module(sys.modules[__name__], ("list_windows", "set_foreground", "force_foreground", "restore",
                                          "screen_capture_reliable", "desktop_size"))

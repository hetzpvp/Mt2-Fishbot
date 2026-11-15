"""
Operating-system layer.

Everything the bot needs from the OS goes through one of these modules, and
the rest of the code (detection, bot loops, timing, GUI) stays shared:

    win32.py    Windows: Win32 API (user32/gdi32), pygetwindow
    x11.py      Linux:   X11 via python-xlib. Metin2 runs under Wine, which
                         draws through X11 (XWayland on a Wayland desktop)
    uinput.py   Linux:   virtual mouse/keyboard via /dev/uinput (the Linux
                         counterpart of the Interception driver)

Window primitives
-----------------
``native`` is the module for the running OS. Both window modules implement
the same functions, on a native window handle (an HWND on Windows, an X11
window id on Linux):

    list_windows() -> list of window objects (see below)
    is_window(handle) -> bool
    get_foreground() -> handle or 0
    set_foreground(handle) -> None          plain request
    force_foreground(handle) -> None        stronger request, used on retries
    is_minimized(handle) -> bool
    restore(handle) -> None
    get_window_rect(handle) -> (left, top, width, height) or None
    render_window(handle, width, height) -> (h, w, 4) BGRA array or None
    screen_capture_reliable() -> bool       whether a desktop copy (mss) shows
                                            the game; False under XWayland
    available() -> bool                     the OS API can be reached

Window objects keep the pygetwindow interface the GUI already uses:
``title``, ``visible``, ``_hWnd`` (native handle), ``left``/``top``/
``width``/``height``, ``isMinimized``, ``minimize()``, ``restore()``,
``activate()``.

``tests/os_layer/test_contract.py`` checks every module against this list.
"""

import sys

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
PLATFORM_NAME = "windows" if IS_WINDOWS else ("linux" if IS_LINUX else sys.platform)

if IS_WINDOWS:
    from platforms import win32 as native  # noqa: E402
elif IS_LINUX:
    from platforms import x11 as native  # noqa: E402
else:  # pragma: no cover - macOS etc. are not supported
    native = None


def window_handle(win):
    """Native handle of a window object (HWND / X11 window id), or None."""
    return getattr(win, "_hWnd", None) if win is not None else None

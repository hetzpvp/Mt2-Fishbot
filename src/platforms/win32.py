"""
Windows primitives (Win32 API). See platforms/__init__.py for the contract.

The code here was moved unchanged out of window_manager.py and
screen_capture.py; the policy around it (retries, caching, verification)
lives in those shared modules.
"""

import ctypes
import sys
from ctypes import wintypes
from typing import List, Optional, Tuple

import numpy as np

try:
    import pygetwindow as gw
except Exception:  # not installed (tests / tooling)
    gw = None

# Private library handles: ctypes.windll.user32 is one shared object, so
# setting argtypes on it would change the prototypes that pygetwindow,
# pyautogui and pynput call through as well.
try:
    _user32 = ctypes.WinDLL("user32")
    _kernel32 = ctypes.WinDLL("kernel32")
    _gdi32 = ctypes.WinDLL("gdi32")
except (AttributeError, OSError):  # non-Windows (tests / tooling)
    _user32 = None
    _kernel32 = None
    _gdi32 = None
_SW_RESTORE = 9
_PW_RENDERFULLCONTENT = 0x2
_DIB_RGB_COLORS = 0
_BI_RGB = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


if _user32 is not None:
    # Explicit prototypes: HWND is pointer-sized, so the default c_int
    # conversion is only safe by accident on 64-bit Python.
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    _user32.GetDC.restype = wintypes.HDC
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.PrintWindow.restype = wintypes.BOOL
    _user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]

    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    _gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.GetDIBits.restype = ctypes.c_int
    _gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
    ]


# ------------------------------------------------------------------ windows

def available() -> bool:
    return _user32 is not None


def list_windows() -> List[object]:
    """All top-level windows as pygetwindow objects (unfiltered)."""
    if gw is None:
        return []
    # getAllWindows() directly - more reliable on Windows 10 than iterating processes
    return list(gw.getAllWindows())


def is_window(hwnd) -> bool:
    return bool(hwnd) and _user32 is not None and bool(_user32.IsWindow(hwnd))


def get_foreground():
    if _user32 is None:
        return 0
    return _user32.GetForegroundWindow() or 0


def set_foreground(hwnd) -> None:
    _user32.SetForegroundWindow(hwnd)


def force_foreground(hwnd) -> None:
    """SetForegroundWindow with the AttachThreadInput workaround.

    Windows refuses SetForegroundWindow from a process that doesn't own
    the foreground. Attaching to the current foreground thread's input
    queue lifts that restriction for the duration of the call."""
    fg = _user32.GetForegroundWindow()
    cur_tid = _kernel32.GetCurrentThreadId()
    fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached = bool(_user32.AttachThreadInput(cur_tid, fg_tid, True))
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(cur_tid, fg_tid, False)


def is_minimized(hwnd) -> bool:
    return bool(_user32.IsIconic(hwnd))


def restore(hwnd) -> None:
    _user32.ShowWindow(hwnd, _SW_RESTORE)


def get_client_rect(hwnd) -> Optional[Tuple[int, int, int, int]]:
    """Client area in screen coordinates (diagnostics only: the bot itself
    works in GetWindowRect coordinates)."""
    if not hwnd or _user32 is None:
        return None
    r = wintypes.RECT()
    if not _user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        return None
    pt = wintypes.POINT(0, 0)
    _user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt))
    return (pt.x, pt.y, r.right - r.left, r.bottom - r.top)


def monitor_of(hwnd) -> Optional[str]:
    """The monitor the window is (mostly) on, with its rect and DPI
    (diagnostics only)."""
    if not hwnd or _user32 is None:
        return None
    try:
        class _MI(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32)]
        _user32.MonitorFromWindow.restype = wintypes.HMONITOR
        _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        hmon = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MI()
        info.cbSize = ctypes.sizeof(_MI)
        _user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        dpi = ""
        try:
            dx, dy = ctypes.c_uint(0), ctypes.c_uint(0)
            ctypes.WinDLL("shcore").GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
            dpi = f" dpi={dx.value} scale={dx.value / 96 * 100:.0f}%"
        except Exception:
            pass
        try:
            dpi += f" window_dpi={_user32.GetDpiForWindow(wintypes.HWND(hwnd))}"
        except Exception:
            pass
        return (f"{info.szDevice} ({r.left},{r.top})-({r.right},{r.bottom}) "
                f"{r.right - r.left}x{r.bottom - r.top} primary={bool(info.dwFlags & 1)}{dpi}")
    except Exception as e:
        return f"unknown ({e})"


def get_window_rect(hwnd) -> Optional[Tuple[int, int, int, int]]:
    """One GetWindowRect syscall (pygetwindow issues one per attribute)."""
    if not hwnd or _user32 is None:
        return None
    r = wintypes.RECT()
    if _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return None


# Shell windows that are "foreground" when the desktop or taskbar was
# clicked, but lie under (or beside) every normal window.
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
_MIN_COVER_PX = 16   # overlap that counts (window rects include invisible resize borders)


def foreground_cover(hwnd, ignore=()) -> Optional[str]:
    """Optional platform function (screen_capture.py): the title of the
    foreground window when it is another program's window lying over
    ``hwnd`` — a screen copy of ``hwnd`` then shows that window. None when
    the game itself (or one of ``ignore``, or the shell) is in front, or the
    foreground window does not overlap it."""
    if not hwnd or _user32 is None:
        return None
    fg = get_foreground()
    if not fg or fg == hwnd or fg in ignore:
        return None
    try:
        if not _user32.IsWindowVisible(fg) or _user32.IsIconic(fg):
            return None
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(fg, cls, 256)
        if cls.value in _SHELL_CLASSES:
            return None
        pid_game, pid_fg = wintypes.DWORD(), wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_game))
        _user32.GetWindowThreadProcessId(fg, ctypes.byref(pid_fg))
        if pid_game.value and pid_game.value == pid_fg.value:
            return None   # the game's own pop-up
        a, b = get_window_rect(hwnd), get_window_rect(fg)
        if not a or not b:
            return None
        w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
        h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
        if w < _MIN_COVER_PX or h < _MIN_COVER_PX:
            return None
        title = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(fg, title, 256)
        return title.value or cls.value or "another window"
    except Exception:
        return None


# ------------------------------------------------------------------ capture

def screen_capture_reliable() -> bool:
    """A desktop copy (mss) shows whatever is on top, which is the game while
    its window is in front (screen_capture.py decides when that is)."""
    return True


def render_window(hwnd, width: int, height: int) -> Optional[np.ndarray]:
    """Renders ``hwnd`` into a (height, width, 4) BGRA array, or None on failure
    (``PrintWindow`` + ``PW_RENDERFULLCONTENT``)."""
    if _user32 is None or width <= 0 or height <= 0:
        return None
    hdc_screen = _user32.GetDC(None)
    if not hdc_screen:
        return None
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = _gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    old = _gdi32.SelectObject(hdc_mem, hbmp) if (hdc_mem and hbmp) else None
    try:
        if not hdc_mem or not hbmp:
            return None
        if not _user32.PrintWindow(hwnd, hdc_mem, _PW_RENDERFULLCONTENT):
            return None

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # negative => top-down rows
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = _BI_RGB

        # Pixels go straight into numpy-owned memory (no intermediate copy).
        out = np.empty((height, width, 4), dtype=np.uint8)
        # The bitmap must not be selected into a DC while GetDIBits reads it.
        _gdi32.SelectObject(hdc_mem, old)
        old = None
        rows = _gdi32.GetDIBits(hdc_mem, hbmp, 0, height,
                                out.ctypes.data_as(ctypes.c_void_p),
                                ctypes.byref(bmi), _DIB_RGB_COLORS)
        return out if rows == height else None
    finally:
        if old is not None:
            _gdi32.SelectObject(hdc_mem, old)
        if hbmp:
            _gdi32.DeleteObject(hbmp)
        if hdc_mem:
            _gdi32.DeleteDC(hdc_mem)
        _user32.ReleaseDC(None, hdc_screen)


# Diagnostic log: the requests that change the desktop are traced.
import diag  # noqa: E402

diag.trace_module(sys.modules[__name__], ("list_windows", "set_foreground", "force_foreground", "restore"))

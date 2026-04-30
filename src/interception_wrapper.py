"""Minimal ctypes wrapper around interception.dll (mouse-only).

Interception (https://github.com/oblitum/Interception) is a signed kernel
driver that lets user-mode code inject mouse and keyboard events *below*
the OS input stack. Unlike SendInput / mouse_event the events do NOT carry
the LLMHF_INJECTED flag and do NOT pass through the global low-level mouse
hook chain — they look identical to physical hardware input.

This wrapper exposes only what the fishing bot needs: open a context, find
the active mouse device, send absolute moves and button down/up events.

The DLL exports cdecl functions (interception_*) on x86 and __fastcall on
x64. ctypes.CDLL handles both correctly when calling through the imported
symbols, since x64 Windows uses a single calling convention.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Optional

# --- DLL constants -------------------------------------------------------

INTERCEPTION_MAX_DEVICE = 20
INTERCEPTION_MAX_KEYBOARD = 10
INTERCEPTION_MAX_MOUSE = 10

# Mouse state flags (bitfield)
INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN   = 0x001
INTERCEPTION_MOUSE_LEFT_BUTTON_UP     = 0x002
INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN  = 0x004
INTERCEPTION_MOUSE_RIGHT_BUTTON_UP    = 0x008
INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN = 0x010
INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP   = 0x020
INTERCEPTION_MOUSE_BUTTON_4_DOWN      = 0x040
INTERCEPTION_MOUSE_BUTTON_4_UP        = 0x080
INTERCEPTION_MOUSE_BUTTON_5_DOWN      = 0x100
INTERCEPTION_MOUSE_BUTTON_5_UP        = 0x200
INTERCEPTION_MOUSE_WHEEL              = 0x400
INTERCEPTION_MOUSE_HWHEEL             = 0x800

# Mouse flags (movement type)
INTERCEPTION_MOUSE_MOVE_RELATIVE      = 0x000
INTERCEPTION_MOUSE_MOVE_ABSOLUTE      = 0x001
INTERCEPTION_MOUSE_VIRTUAL_DESKTOP    = 0x002

# Filter values
INTERCEPTION_FILTER_MOUSE_NONE = 0x0000
INTERCEPTION_FILTER_MOUSE_ALL  = 0xFFFF


class InterceptionMouseStroke(ctypes.Structure):
    _fields_ = [
        ("state",       ctypes.c_ushort),
        ("flags",       ctypes.c_ushort),
        ("rolling",     ctypes.c_short),
        ("x",           ctypes.c_int),
        ("y",           ctypes.c_int),
        ("information", ctypes.c_uint),
    ]


_DLL_LOAD_ERROR: Optional[str] = None
_dll = None


def _candidate_dll_paths(explicit: Optional[str] = None):
    """Yield places to look for interception.dll, most-specific first."""
    seen = set()
    if explicit:
        yield explicit
        seen.add(os.path.normcase(os.path.abspath(explicit)))

    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    arch_subdir = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"

    candidates = [
        os.path.join(project_root, "interception.dll"),
        os.path.join(project_root, "tools", "interception", arch_subdir, "interception.dll"),
        os.path.join(project_root, "tools", "interception", "interception.dll"),
        os.path.join(here, "interception.dll"),
        # PATH lookup — last resort. ctypes.WinDLL("interception.dll") with no
        # leading path triggers SearchPath, so emit the bare name too.
        "interception.dll",
    ]
    for c in candidates:
        key = os.path.normcase(os.path.abspath(c)) if os.path.dirname(c) else c
        if key in seen:
            continue
        seen.add(key)
        yield c


def load_interception_dll(explicit_path: Optional[str] = None):
    """Locate and load interception.dll. Returns the loaded ctypes.WinDLL or
    None on failure. The reason for the most recent failure is kept in
    ``last_load_error()`` for surfacing in the GUI."""
    global _dll, _DLL_LOAD_ERROR
    if _dll is not None:
        return _dll

    last_err = None
    for path in _candidate_dll_paths(explicit_path):
        try:
            dll = ctypes.WinDLL(path)
            # DLL loaded; now try to import symbols so we know it's the right binary
        except OSError as e:
            last_err = f"[load_interception_dll] Failed to load {path}: {e}"
            continue

        try:
            # interception_create_context() -> InterceptionContext (void*)
            dll.interception_create_context.restype = ctypes.c_void_p
            dll.interception_create_context.argtypes = []

            dll.interception_destroy_context.restype = None
            dll.interception_destroy_context.argtypes = [ctypes.c_void_p]

            dll.interception_set_filter.restype = None
            dll.interception_set_filter.argtypes = [
                ctypes.c_void_p,  # context
                ctypes.c_void_p,  # InterceptionPredicate (we pass NULL — set filter to all keyboards/mice)
                ctypes.c_ushort,  # filter
            ]

            dll.interception_wait.restype = ctypes.c_int  # device id, 0 on timeout
            dll.interception_wait.argtypes = [ctypes.c_void_p]

            dll.interception_wait_with_timeout.restype = ctypes.c_int
            dll.interception_wait_with_timeout.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

            dll.interception_send.restype = ctypes.c_int  # number of strokes sent
            dll.interception_send.argtypes = [
                ctypes.c_void_p,  # context
                ctypes.c_int,     # device
                ctypes.c_void_p,  # stroke array (we pass byref)
                ctypes.c_uint,    # nstrokes
            ]

            dll.interception_receive.restype = ctypes.c_int
            dll.interception_receive.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
            ]

            dll.interception_is_mouse.restype = ctypes.c_int
            dll.interception_is_mouse.argtypes = [ctypes.c_int]

            dll.interception_is_keyboard.restype = ctypes.c_int
            dll.interception_is_keyboard.argtypes = [ctypes.c_int]

            dll.interception_get_hardware_id.restype = ctypes.c_uint
            dll.interception_get_hardware_id.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_uint,
            ]
        except AttributeError as e:
            last_err = f"{path}: missing symbol {e}"
            continue

        _dll = dll
        _DLL_LOAD_ERROR = None
        return dll

    _DLL_LOAD_ERROR = last_err or "interception.dll not found"
    return None


def last_load_error() -> Optional[str]:
    return _DLL_LOAD_ERROR


# --- High-level driver wrapper -------------------------------------------

class InterceptionDriver:
    """Owns a single InterceptionContext. Thread-safe for the operations
    used by the bot (send is a kernel-mediated write; concurrent sends
    serialize inside the driver)."""

    def __init__(self, dll, context: int):
        self._dll = dll
        self._ctx = context
        self._mouse_device: Optional[int] = None

    @classmethod
    def open(cls, dll_path: Optional[str] = None) -> Optional["InterceptionDriver"]:
        dll = load_interception_dll(dll_path)
        if dll is None:
            return None
        ctx = dll.interception_create_context()
        if not ctx:
            global _DLL_LOAD_ERROR
            _DLL_LOAD_ERROR = "interception_create_context returned NULL — driver not installed?"
            return None
        return cls(dll, ctx)

    def close(self) -> None:
        if self._ctx:
            try:
                self._dll.interception_destroy_context(self._ctx)
            except Exception:
                pass
            self._ctx = 0

    def __del__(self):
        self.close()

    def is_mouse(self, device: int) -> bool:
        return bool(self._dll.interception_is_mouse(device))

    def hardware_id(self, device: int) -> str:
        buf = ctypes.create_unicode_buffer(512)
        n = self._dll.interception_get_hardware_id(self._ctx, device, buf, 1024)
        if n <= 0:
            return ""
        return buf.value

    def find_default_mouse(self, preferred_device: Optional[int] = None) -> Optional[int]:
        """Return a device id we can send mouse strokes to.

        Strategy:
        1. If ``preferred_device`` is given and is_mouse(), use it.
        2. Otherwise pick the first device id in the mouse range
           (1+INTERCEPTION_MAX_KEYBOARD .. INTERCEPTION_MAX_DEVICE) that
           reports is_mouse(). The driver assigns mouse ids 11–20.
        """
        if preferred_device is not None and self.is_mouse(preferred_device):
            self._mouse_device = preferred_device
            return preferred_device

        for dev in range(1 + INTERCEPTION_MAX_KEYBOARD, INTERCEPTION_MAX_DEVICE + 1):
            if self.is_mouse(dev):
                self._mouse_device = dev
                return dev
        return None

    @property
    def mouse_device(self) -> Optional[int]:
        return self._mouse_device

    def send_mouse(self, stroke: InterceptionMouseStroke) -> int:
        if self._mouse_device is None:
            return 0
        return self._dll.interception_send(
            self._ctx, self._mouse_device, ctypes.byref(stroke), 1
        )


def is_supported_platform() -> bool:
    return sys.platform.startswith("win")


if __name__ == "__main__":
    print("=== Interception DLL diagnostics ===")
    print(f"Platform: {sys.platform}")
    print(f"Python: {sys.version}")
    print(f"Architecture: {ctypes.sizeof(ctypes.c_void_p) * 8}-bit")
    print()

    print("Candidate DLL paths:")
    for i, p in enumerate(_candidate_dll_paths(), 1):
        exists = "[OK]" if (os.path.dirname(p) == "" or os.path.exists(p)) else "[--]"
        print(f"  {i}. {exists} {p}")
    print()

    print("Attempting to load DLL...")
    dll = load_interception_dll()
    if dll:
        print(f"[OK] DLL loaded successfully")
        drv = InterceptionDriver.open()
        if drv:
            print(f"[OK] InterceptionDriver opened")
            device = drv.find_default_mouse()
            if device:
                print(f"[OK] Found mouse device: {device}")
                print(f"\nSUCCESS: Interception is ready to use")
            else:
                print(f"[XX] No mouse device found")
                print(f"  Did you reboot after installing the driver?")
                print(f"  Try: sc query interception")
            drv.close()
        else:
            print(f"[XX] Failed to open InterceptionDriver context")
            print(f"  Error: {last_load_error()}")
    else:
        print(f"[XX] Failed to load DLL")
        print(f"  Error: {last_load_error()}")

"""Mouse input backends used by Humanizer.

Two backends ship with the bot:

- ``pyautogui`` (always-available fallback): uses pyautogui → ``SendInput``.
  Easy and works everywhere, but every event carries the ``LLMHF_INJECTED``
  flag and is visible to low-level mouse hooks.

- ``interception`` (preferred, opt-in): uses the Interception kernel driver
  (https://github.com/oblitum/Interception) to inject events below the OS
  input stack. No injected flag, no hook visibility — events are
  indistinguishable from a real USB mouse to user-mode code.

``create_mouse_backend(config)`` returns an instance and stashes the
backend's ``name`` so the GUI can display which one is in use. When
``config['input_backend'] == 'auto'`` (the default) we try Interception
first and silently fall back to pyautogui if the driver isn't installed.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional, Tuple

import pyautogui

from interception_wrapper import (
    INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN,
    INTERCEPTION_MOUSE_LEFT_BUTTON_UP,
    INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN,
    INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP,
    INTERCEPTION_MOUSE_MOVE_ABSOLUTE,
    INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN,
    INTERCEPTION_MOUSE_RIGHT_BUTTON_UP,
    INTERCEPTION_MOUSE_VIRTUAL_DESKTOP,
    InterceptionDriver,
    InterceptionMouseStroke,
    is_supported_platform,
    last_load_error,
)


# --- pyautogui (always-available fallback) -------------------------------

class PyAutoGuiMouseBackend:
    name = "pyautogui"
    display_name = "PyAutoGUI"

    def move_to(self, x: float, y: float) -> None:
        pyautogui.moveTo(x, y, _pause=False)

    def position(self) -> Tuple[int, int]:
        return pyautogui.position()

    def mouse_down(self, button: str = "left") -> None:
        pyautogui.mouseDown(button=button, _pause=False)

    def mouse_up(self, button: str = "left") -> None:
        pyautogui.mouseUp(button=button, _pause=False)

    def click(self, button: str = "left") -> None:
        pyautogui.click(button=button, _pause=False)

    def close(self) -> None:
        return


# --- Interception kernel driver -------------------------------------------

_user32 = ctypes.windll.user32 if is_supported_platform() else None
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

_BUTTON_DOWN = {
    "left":   INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN,
    "right":  INTERCEPTION_MOUSE_RIGHT_BUTTON_DOWN,
    "middle": INTERCEPTION_MOUSE_MIDDLE_BUTTON_DOWN,
}
_BUTTON_UP = {
    "left":   INTERCEPTION_MOUSE_LEFT_BUTTON_UP,
    "right":  INTERCEPTION_MOUSE_RIGHT_BUTTON_UP,
    "middle": INTERCEPTION_MOUSE_MIDDLE_BUTTON_UP,
}


class InterceptionMouseBackend:
    """Backend that drives the Interception kernel driver.

    Coordinates are converted from screen pixels to the virtual-desktop
    [0..65535] absolute range Interception expects (matching ``MOUSEEVENTF_
    VIRTUALDESK | MOUSEEVENTF_ABSOLUTE`` semantics) so multi-monitor setups
    work out of the box.

    ``position()`` reads the real cursor through ``GetCursorPos`` — the
    driver does move the OS cursor, so the user sees it travel along the
    humanized Bezier path just like with pyautogui.
    """

    name = "interception"
    display_name = "Interception"

    def __init__(self, driver: InterceptionDriver):
        self._drv = driver
        self._refresh_virtual_desktop()

    def _refresh_virtual_desktop(self) -> None:
        if _user32 is None:
            self._vx0 = 0
            self._vy0 = 0
            self._vw = 1920
            self._vh = 1080
            return
        self._vx0 = _user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        self._vy0 = _user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        self._vw = max(1, _user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
        self._vh = max(1, _user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))

    def _to_abs(self, x: float, y: float) -> Tuple[int, int]:
        # Interception with VIRTUAL_DESKTOP uses the same 0..65535 mapping
        # as MOUSEEVENTF_ABSOLUTE: the value is (pixel - origin) * 65535 / size.
        ax = int(round((float(x) - self._vx0) * 65535.0 / self._vw))
        ay = int(round((float(y) - self._vy0) * 65535.0 / self._vh))
        return max(0, min(65535, ax)), max(0, min(65535, ay))

    def move_to(self, x: float, y: float) -> None:
        ax, ay = self._to_abs(x, y)
        stroke = InterceptionMouseStroke(
            state=0,
            flags=INTERCEPTION_MOUSE_MOVE_ABSOLUTE | INTERCEPTION_MOUSE_VIRTUAL_DESKTOP,
            rolling=0, x=ax, y=ay, information=0,
        )
        self._drv.send_mouse(stroke)

    def position(self) -> Tuple[int, int]:
        if _user32 is None:
            return (0, 0)
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)

    def _send_button(self, state_flag: int) -> None:
        # Button-only stroke: no movement, no absolute flag.
        stroke = InterceptionMouseStroke(
            state=state_flag, flags=0, rolling=0, x=0, y=0, information=0,
        )
        self._drv.send_mouse(stroke)

    def mouse_down(self, button: str = "left") -> None:
        self._send_button(_BUTTON_DOWN.get(button, INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN))

    def mouse_up(self, button: str = "left") -> None:
        self._send_button(_BUTTON_UP.get(button, INTERCEPTION_MOUSE_LEFT_BUTTON_UP))

    def click(self, button: str = "left") -> None:
        self.mouse_down(button)
        self.mouse_up(button)

    def close(self) -> None:
        if self._drv is not None:
            self._drv.close()
            self._drv = None


# --- Probing & factory ----------------------------------------------------

# Cached probe result so the GUI and each bot don't re-open the driver.
_probed_backend_name: Optional[str] = None
_probe_error: Optional[str] = None


def probe_backend(config: Optional[dict] = None) -> Tuple[str, Optional[str]]:
    """Determine which backend will be used given ``config``, without
    actually constructing a long-lived driver instance. Returns
    ``(name, error_or_None)`` where ``name`` is ``"interception"`` or
    ``"pyautogui"``.

    Resolution rules:
    - ``input_backend == "pyautogui"``  → pyautogui, no probe.
    - ``input_backend == "interception"`` → must succeed, error if not.
    - ``input_backend == "auto"`` (or anything else) → try Interception,
      silently fall back to pyautogui on failure.
    """
    global _probed_backend_name, _probe_error
    cfg = config or {}
    requested = str(cfg.get("input_backend", "auto")).lower()

    if requested == "pyautogui":
        _probed_backend_name = "pyautogui"
        _probe_error = None
        return ("pyautogui", None)

    if not is_supported_platform():
        err = "Interception requires Windows"
        _probed_backend_name = "pyautogui"
        _probe_error = err
        return ("pyautogui", err)

    dll_path = cfg.get("interception_dll_path") or None
    preferred_device = cfg.get("interception_mouse_device")

    drv = InterceptionDriver.open(dll_path)
    if drv is None:
        err = last_load_error() or "interception driver not available (DLL load failed)"
        _probed_backend_name = "pyautogui"
        _probe_error = err
        return ("pyautogui", err)

    try:
        device = drv.find_default_mouse(preferred_device)
        if device is None:
            err = "Interception loaded but no mouse device found. Reboot after driver install?"
            _probed_backend_name = "pyautogui"
            _probe_error = err
            return ("pyautogui", err)
    finally:
        drv.close()

    _probed_backend_name = "interception"
    _probe_error = None
    return ("interception", None)


def probed_backend_info() -> Tuple[Optional[str], Optional[str]]:
    """Return the most recent probe result without re-running it. If
    probe_backend hasn't been called yet, both values are None."""
    return (_probed_backend_name, _probe_error)


def create_mouse_backend(config: Optional[dict] = None, status_callback=None,
                          bot_id: int = 0):
    """Construct a mouse backend instance for one bot. Falls back to
    pyautogui if Interception fails to initialize."""
    cfg = config or {}
    requested = str(cfg.get("input_backend", "auto")).lower()

    if requested == "pyautogui" or not is_supported_platform():
        return PyAutoGuiMouseBackend()

    dll_path = cfg.get("interception_dll_path") or None
    preferred_device = cfg.get("interception_mouse_device")

    drv = InterceptionDriver.open(dll_path)
    if drv is not None:
        device = drv.find_default_mouse(preferred_device)
        if device is not None:
            return InterceptionMouseBackend(drv)
        drv.close()

    # Failure path: in "auto" we silently fall back; in explicit
    # "interception" we still fall back so the bot can run, but log to the
    # status callback if available.
    if requested == "interception" and status_callback:
        try:
            err = last_load_error() or "interception unavailable"
            status_callback(f"[W{bot_id+1}] Interception unavailable ({err}); using PyAutoGUI")
        except Exception:
            pass
    return PyAutoGuiMouseBackend()


if __name__ == "__main__":
    print("=== Input backend diagnostics ===")
    print()
    name, err = probe_backend({"input_backend": "auto"})
    print(f"probe_backend(auto) -> {name}")
    if err:
        print(f"  Error: {err}")
    print()
    print("Hint: Run this from the repo root so bot_config.json can be found:")
    print("  python -m src.input_backend")
    print()
    print("Or with explicit config:")
    print("  python -c \"from src.input_backend import probe_backend; print(probe_backend({'input_backend': 'auto'}))\"")


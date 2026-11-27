"""
The hidden display the real-client tests run on: Xvfb + miniwm.

Nothing on it is visible, and its pointer, keyboard and focus are its own:
the game clients, the bot's XTest clicks and keys and its focus switches
never reach the user's desktop. ``activate()`` points this process at it
($DISPLAY for python-xlib, pynput, xdotool and the clients the rig starts)
and removes everything that leads to the desktop session ($WAYLAND_DISPLAY,
$XAUTHORITY) and its speakers (PulseAudio / PipeWire).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MARKER_ENV = "FISHBOT_HIDDEN_DISPLAY"     # set to the display name in every child process
WM_NAME = b"fishbot-miniwm"

# The clients must not play sound on the user's speakers: no PulseAudio server
# reachable, so Wine's audio fails quietly and the game runs mute.
_MUTE_ENV = {"PULSE_SERVER": "unix:/nonexistent/fishbot-mute", "PIPEWIRE_REMOTE": "/nonexistent/fishbot-mute",
             "ALSA_CONFIG_PATH": "/nonexistent/fishbot-mute"}
_DESKTOP_VARS = ("WAYLAND_DISPLAY", "XAUTHORITY", "WAYLAND_SOCKET")


class DisplayError(RuntimeError):
    pass


def missing_tools() -> list:
    return [t for t in ("Xvfb", "xdotool") if not shutil.which(t)]


SCREEN_ENV = "FISHBOT_REALCLIENT_SCREEN"   # "WxH": screen size of the hidden displays
DEFAULT_SIZE = (1600, 1220)               # 2x2 tiles of 800x600 plus the rig's offsets


def screen_size_for(window_sizes, columns=2, margin=(0, 60)) -> tuple:
    """Screen big enough for windows of ``window_sizes`` ([(w, h), ...])
    tiled ``columns`` per row, the way the rig tiles them, plus ``margin``.
    For scenarios with other resolutions (docs/TESTRIG_SCENARIOS.md, R13)."""
    sizes = [tuple(int(v) for v in s) for s in window_sizes] or [(800, 600)]
    rows = [sizes[i:i + columns] for i in range(0, len(sizes), columns)]
    w = max(sum(s[0] for s in row) for row in rows) + margin[0]
    h = sum(max(s[1] for s in row) for row in rows) + margin[1]
    return max(w, DEFAULT_SIZE[0]), max(h, DEFAULT_SIZE[1])


def _size_from_env():
    v = os.environ.get(SCREEN_ENV, "").lower().strip()
    if not v:
        return None
    try:
        w, h = (int(n) for n in v.split("x"))
    except ValueError:
        raise DisplayError(f"{SCREEN_ENV} must be WxH, got {v!r}")
    if w < 640 or h < 480 or w > 16384 or h > 16384:
        raise DisplayError(f"{SCREEN_ENV}={v}: size out of range")
    return w, h


FRAME_ENV = "FISHBOT_REALCLIENT_FRAME"           # "TOP,BORDER": window frames (miniwm --frame)
COMPOSITE_ENV = "FISHBOT_REALCLIENT_COMPOSITE"   # "off": X11 without a compositor


class HiddenDisplay:
    """Xvfb on a free display number, with miniwm as window manager.
    ``size``: the screen; default $FISHBOT_REALCLIENT_SCREEN, else 1600x1220.
    ``frame``: (title bar, border) px round every window, like KWin or
    Windows (default $FISHBOT_REALCLIENT_FRAME, else none).
    ``composite``: windows keep all their pixels when covered or partly off
    screen, as on Xwayland / composited KWin (default; off with
    $FISHBOT_REALCLIENT_COMPOSITE=off). See miniwm.py."""

    def __init__(self, size=None, frame=None, composite=None):
        self.size = tuple(size) if size else (_size_from_env() or DEFAULT_SIZE)
        if frame is None and os.environ.get(FRAME_ENV):
            try:
                frame = tuple(int(v) for v in os.environ[FRAME_ENV].split(","))
            except ValueError:
                raise DisplayError(f"{FRAME_ENV} must be TOP,BORDER")
        self.frame = tuple(frame) if frame else None
        if composite is None:
            composite = os.environ.get(COMPOSITE_ENV, "on").lower() not in ("off", "0", "no")
        self.composite = bool(composite)
        self.display = None
        self.procs = []
        self._saved_env = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "HiddenDisplay":
        missing = missing_tools()
        if missing:
            raise DisplayError("missing: " + ", ".join(missing))
        # Xvfb picks a free display number itself and writes it to a pipe
        # (-displayfd): no race when two test workers start at the same time.
        rfd, wfd = os.pipe()
        try:
            xvfb = subprocess.Popen(["Xvfb", "-displayfd", str(wfd), "-screen", "0",
                                     f"{self.size[0]}x{self.size[1]}x24", "-nolisten", "tcp", "-noreset"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                    pass_fds=(wfd,), start_new_session=True)
        finally:
            os.close(wfd)
        self.procs.append(xvfb)
        num = b""
        deadline = time.time() + 15
        with os.fdopen(rfd, "rb", buffering=0) as r:
            while time.time() < deadline and not num.endswith(b"\n"):
                chunk = r.read(16)
                if not chunk:
                    break
                num += chunk
        if not num.strip().isdigit() or xvfb.poll() is not None:
            self.stop()
            raise DisplayError("Xvfb did not start")
        self.display = f":{int(num)}"
        wm_args = [sys.executable, os.path.join(HERE, "miniwm.py"), "--display", self.display,
                   "--composite", "on" if self.composite else "off"]
        if self.frame:
            wm_args += ["--frame", f"{self.frame[0]},{self.frame[1]}"]
        wm = subprocess.Popen(wm_args,
                              env=self.child_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, start_new_session=True)
        self.procs.append(wm)
        deadline = time.time() + 10
        while time.time() < deadline and not self.has_our_wm():
            if wm.poll() is not None:
                self.stop()
                raise DisplayError("miniwm exited on " + self.display)
            time.sleep(0.05)
        if not self.has_our_wm():
            self.stop()
            raise DisplayError("miniwm did not take over " + self.display)
        return self

    def stop(self) -> None:
        self.deactivate()
        for p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.procs = []

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # ------------------------------------------------------------ environment
    def child_env(self, base=None) -> dict:
        env = {k: v for k, v in (base if base is not None else os.environ).items() if k not in _DESKTOP_VARS}
        env.update(_MUTE_ENV)
        env["DISPLAY"] = self.display
        env[MARKER_ENV] = self.display
        return env

    def activate(self) -> None:
        """This process (and every child it starts) uses the hidden display."""
        if self._saved_env is None:
            self._saved_env = {k: os.environ.get(k) for k in (*_DESKTOP_VARS, *_MUTE_ENV, "DISPLAY", MARKER_ENV)}
        for k in _DESKTOP_VARS:
            os.environ.pop(k, None)
        os.environ.update(_MUTE_ENV)
        os.environ["DISPLAY"] = self.display
        os.environ[MARKER_ENV] = self.display

    def deactivate(self) -> None:
        if self._saved_env is None:
            return
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._saved_env = None

    # ------------------------------------------------------------ checks
    def has_our_wm(self) -> bool:
        """True when miniwm runs on this display (proves it's ours, not the desktop)."""
        return wm_name(self.display) == WM_NAME


def wm_name(display_name: str):
    try:
        from Xlib import X, display
        d = display.Display(display_name)
        try:
            root = d.screen().root
            prop = root.get_full_property(d.intern_atom("_NET_SUPPORTING_WM_CHECK"), X.AnyPropertyType)
            if not prop or not len(prop.value):
                return None
            check = d.create_resource_object("window", int(prop.value[0]))
            name = check.get_full_property(d.intern_atom("_NET_WM_NAME"), X.AnyPropertyType)
            return bytes(name.value) if name else None
        finally:
            d.close()
    except Exception:
        return None


def assert_hidden() -> None:
    """Refuses to go on unless this process points at a hidden display:
    the bot's input must never reach the user's desktop."""
    name = os.environ.get("DISPLAY", "")
    if not name or os.environ.get(MARKER_ENV) != name:
        raise DisplayError(f"DISPLAY={name!r} is not a hidden test display")
    if os.environ.get("WAYLAND_DISPLAY"):
        raise DisplayError("WAYLAND_DISPLAY is set: the desktop session would be reachable")
    if wm_name(name) != WM_NAME:
        raise DisplayError(f"{name} is not run by fishbot-miniwm: refusing to send input there")

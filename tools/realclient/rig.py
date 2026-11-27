"""
The local Metin2 test rig (metin2-local-server, scripts/testrig.py), driven
from the fishbot's real-client tests.

``RigSession(instance, display)`` starts real game clients for a scenario on
the hidden display (display.py), hands back their windows, and reads the
characters' final state from the database. It also does what a test needs
to disturb a running client: freeze it, kill it, minimize / move / raise its
window, change its UI (testrig ``ui``).

Safety:
- every rig call runs with this process pointed at the hidden display
  (``assert_hidden``), so the clients open there and nowhere else;
- an instance whose clients run on another display (someone playing with
  ``multi.sh`` on the desktop) is left alone: ``in_use_elsewhere()`` says so
  and the tests skip it, instead of closing those clients and resetting the
  characters;
- a VM that was off before the tests is shut down again afterwards.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from display import MARKER_ENV, HiddenDisplay, assert_hidden

DEFAULT_REPO = Path.home() / "Documents" / "Github" / "metin2-local-server"
VMS = {"classic": "metin2-server", "fish": "metin2-fish"}


def repo() -> Path:
    return Path(os.environ.get("MT2_LOCAL_SERVER") or DEFAULT_REPO)


_testrig = None


def testrig():
    """The rig's own Python module (stdlib only)."""
    global _testrig
    if _testrig is None:
        scripts = str(repo() / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import testrig as mod   # noqa: E402
        _testrig = mod
    return _testrig


def unavailable_reason(instance: str):
    """None when the instance can be used, else why not (the tests skip)."""
    if not sys.platform.startswith("linux"):
        return "the test rig runs on Linux only"
    r = repo()
    if not (r / "scripts" / "testrig.py").exists():
        return f"test rig not found at {r} (set MT2_LOCAL_SERVER)"
    if not (Path.home() / ".ssh" / "metin2_vm").exists():
        return "no ssh key for the rig VMs (~/.ssh/metin2_vm)"
    try:
        out = subprocess.run(["virsh", "-c", "qemu:///system", "domstate", VMS[instance]],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"virsh not usable: {e}"
    if out.returncode != 0:
        return f"VM {VMS[instance]} not defined: {out.stderr.strip()}"
    return None


def vm_state(instance: str) -> str:
    out = subprocess.run(["virsh", "-c", "qemu:///system", "domstate", VMS[instance]],
                         capture_output=True, text=True, timeout=20)
    return out.stdout.strip()


def _proc_env(pid: int) -> dict:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return {}
    out = {}
    for item in raw:
        k, _, v = item.partition(b"=")
        out[k.decode(errors="replace")] = v.decode(errors="replace")
    return out


class RigError(RuntimeError):
    pass


class Client:
    """One running game client."""

    def __init__(self, info: dict):
        self.account = info["account"]
        self.pid = info.get("pid")
        self.window = info.get("window")
        self.geometry = info.get("geometry") or {}
        self.ready = info.get("ready") or {}
        self.info = info

    def __repr__(self):
        return f"Client({self.account}, pid={self.pid}, window={self.window})"


class RigSession:
    def __init__(self, instance: str, display: HiddenDisplay):
        self.instance = instance
        self.display = display
        self.clients: dict[str, Client] = {}
        self.vm_was_running = vm_state(instance) == "running"
        self.last_up = None

    # ------------------------------------------------------------ guards
    def _env(self):
        self.display.activate()
        assert_hidden()

    def in_use_elsewhere(self):
        """Clients of this instance that run on another display, or None."""
        rig = testrig().Rig(self.instance)
        foreign = []
        for pid, acc in rig.client_pids().items():
            env = _proc_env(pid)
            if env.get(MARKER_ENV) is None:
                foreign.append(f"{acc or '?'} (pid {pid}, DISPLAY {env.get('DISPLAY')})")
        return ", ".join(foreign) or None

    # ------------------------------------------------------------ rig calls
    def up(self, scenario: dict) -> dict:
        self._env()
        sc = dict(scenario, instance=self.instance)
        try:
            res = testrig().up(sc)
        except testrig().RigError as e:
            raise RigError(f"rig up failed: {e}") from e
        self.last_up = res
        self.clients = {c["account"]: Client(c) for c in res.get("clients", [])}
        if not res.get("ok"):
            raise RigError(f"rig up failed: {res.get('error')}: {json.dumps(res.get('clients'))[:800]}")
        missing = [c.account for c in self.clients.values() if not c.window]
        if missing:
            raise RigError(f"no window for {missing}")
        return res

    def ui(self, accounts=None, **kw) -> dict:
        self._env()
        return testrig().ui(self.instance, accounts=accounts, **kw)

    def status(self) -> dict:
        self._env()
        return testrig().status(self.instance)

    def finish(self) -> dict:
        """Closes the clients, stops the game cores (the DB is then exact) and
        returns the characters' final state: {account: character}."""
        self._env()
        res = testrig().down(self.instance, keep_vm=True, inspect_after=True)
        self.clients = {}
        chars = (res.get("inspect") or {}).get("characters") or []
        return {c["account"]: c for c in chars}

    def close(self) -> None:
        """End of the test run: clients off; the VM off again if it was off."""
        try:
            self._env()
            testrig().down(self.instance, keep_vm=self.vm_was_running)
        except Exception as e:     # never hide the test result behind a teardown error
            print(f"[realclient] {self.instance}: teardown: {e}", file=sys.stderr)

    # ------------------------------------------------------------ disturbances
    def _xdotool(self, *args) -> str:
        self._env()
        out = subprocess.run(["xdotool", *map(str, args)], capture_output=True, text=True, timeout=10)
        return out.stdout.strip()

    def freeze(self, account: str, seconds: float) -> threading.Thread:
        """SIGSTOP the client for ``seconds`` (a hitch: no frames, no input), in the background."""
        pid = self.clients[account].pid

        def run():
            try:
                os.kill(pid, signal.SIGSTOP)
                time.sleep(seconds)
            finally:
                try:
                    os.kill(pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
        t = threading.Thread(target=run, daemon=True, name=f"freeze-{account}")
        t.start()
        return t

    def kill(self, account: str) -> None:
        """The client disappears (a crash). The scenario should set
        restart_on_crash 0, or the rig opens it again."""
        os.kill(self.clients[account].pid, signal.SIGKILL)

    def minimize(self, account: str) -> None:
        self._xdotool("windowminimize", self.clients[account].window)

    def activate(self, account: str) -> None:
        self._xdotool("windowactivate", self.clients[account].window)

    def move(self, account: str, x: int, y: int) -> None:
        self._xdotool("windowmove", self.clients[account].window, x, y)

    def frame(self, account: str):
        """The client's window as a BGR array (or None)."""
        self._env()
        import numpy as np
        from platforms import x11
        c = self.clients[account]
        img = x11.render_window(c.window, c.geometry.get("width", 800), c.geometry.get("height", 600))
        return None if img is None else np.ascontiguousarray(img[:, :, :3])

    def press(self, account: str, *keys: str) -> None:
        """Sends keys to one client (xdotool names: 'm', 'i', 'Escape').
        Activates it and types through XTest, like the bot: ``key --window``
        sends synthetic events, which the client did not always take (the
        area map stayed open on 3 of 4 clients, 2026-11-26)."""
        win = self.clients[account].window
        self._xdotool("windowactivate", "--sync", win)
        time.sleep(0.2)
        for k in keys:
            if self._xdotool("getactivewindow") != str(win):
                self._xdotool("windowactivate", "--sync", win)
                time.sleep(0.2)
            self._xdotool("key", k)
            time.sleep(0.25)

    def area_map_open(self, account: str) -> bool:
        """The area map window ("Yongan Area") the clients open at login.
        It covers the middle of the upper screen, where the classic bite
        bubble appears. Found by its map picture (its title bar may be
        above the screen) or by its title bar."""
        import cv2
        refs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "refs")
        img = self.frame(account)
        if img is None:
            return False
        for name, limit in (("area_map_picture.png", 0.6), ("area_map_title.png", 0.8)):
            ref = cv2.imread(os.path.join(refs, name))
            if ref is not None and float(cv2.matchTemplate(img[:320], ref, cv2.TM_CCOEFF_NORMED).max()) > limit:
                return True
        return False

    def close_area_map(self, account: str, settle: float = 4.0, attempts: int = 6) -> bool:
        """Closes the area map (key M toggles it). True when it is closed and
        still closed ``settle`` seconds later (the client may open it again
        shortly after login, later under load)."""
        for _ in range(attempts):
            if not self.area_map_open(account):
                time.sleep(settle)
                if not self.area_map_open(account):
                    return True
            self.press(account, "m")
            time.sleep(0.5)
        return not self.area_map_open(account)

    def screenshot(self, account: str, path: str) -> bool:
        self._env()
        import cv2
        from platforms import x11
        c = self.clients[account]
        img = x11.render_window(c.window, c.geometry.get("width", 800), c.geometry.get("height", 600))
        if img is None:
            return False
        return bool(cv2.imwrite(path, img[:, :, :3]))

"""
Real-client tests: the bot against real Metin2 clients on the local test rig
(metin2-local-server), on a hidden display.

How they run (tools/run_tests.py does all of this by default on Linux):

- a pytest process of their own (they need the real OS modules, the unit
  tests stub them), with FISHBOT_REALCLIENT=1;
- each instance (classic / fish) in its own pytest process (run_tests.py
  starts both side by side), with its own hidden display (Xvfb + miniwm): clients, clicks, keys and focus switches
  never reach the user's desktop, so it's safe to play meanwhile;
- one rig session per test module: the module brings its clients up once
  (one scenario, up to 4 clients, each client a different case), runs the
  bots, and every test function checks one client's outcome.

Skipped (not failed) when the rig isn't there: not Linux, no
metin2-local-server checkout, no VM, Xvfb missing, or the instance busy on
another display (someone playing with multi.sh).

Run output (bot status lines, diagnostic log, screenshots, final DB state)
goes to ~/.local/share/fishbot-realclient/runs/<stamp>/<module>/; failures
point there.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RC = os.path.join(ROOT, "tools", "realclient")
for p in (RC, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

ENABLED = os.environ.get("FISHBOT_REALCLIENT") == "1"


def pytest_collection_modifyitems(config, items):
    for item in items:
        if not str(item.path).startswith(HERE + os.sep):
            continue
        if not ENABLED:
            item.add_marker(pytest.mark.skip(
                reason="real-client test (tools/run_tests.py runs them; or FISHBOT_REALCLIENT=1)"))
        elif os.environ.get("PYTEST_XDIST_WORKER"):
            # pytest-xdist can't keep one instance's modules on one worker:
            # its loadgroup writes the group to Node._nodeid, which pytest 9
            # no longer reads. Two workers then shared one VM and its clients
            # (2026-11-25/26: whole sessions failed with "not in game").
            # tools/run_tests.py runs one process per instance instead.
            item.add_marker(pytest.mark.skip(
                reason="real-client tests don't run under pytest-xdist: one pytest process per instance "
                       "(tools/run_tests.py, or pytest tests/realclient/<instance's modules>)"))


@pytest.fixture(scope="session")
def hidden_display():
    stubs = sys.modules.get("_stubs")
    if stubs is not None and getattr(stubs, "INSTALLED", False):
        pytest.skip("real-client tests mixed with stubbed unit tests: run them in their own process")
    from display import DisplayError, HiddenDisplay, missing_tools
    missing = missing_tools()
    if missing:
        pytest.skip("real-client tests need " + ", ".join(missing))
    disp = HiddenDisplay()
    try:
        disp.start()
    except DisplayError as e:
        pytest.skip(f"no hidden display: {e}")
    disp.activate()
    yield disp
    disp.stop()


class Rigs:
    """One RigSession per instance for the whole worker; ``get`` skips the
    test when the instance can't be used."""

    def __init__(self, display):
        self.display = display
        self.sessions = {}
        self.refused = {}

    def get(self, instance):
        import rig
        if instance in self.refused:
            pytest.skip(self.refused[instance])
        if instance in self.sessions:
            return self.sessions[instance]
        why = rig.unavailable_reason(instance)
        if not why:
            s = rig.RigSession(instance, self.display)
            busy = s.in_use_elsewhere()
            if busy:
                why = f"{instance} instance is in use on another display: {busy}"
            else:
                self.sessions[instance] = s
                return s
        self.refused[instance] = why
        pytest.skip(why)

    def close(self):
        for s in self.sessions.values():
            s.close()


@pytest.fixture(scope="session")
def rigs(hidden_display):
    r = Rigs(hidden_display)
    yield r
    r.close()

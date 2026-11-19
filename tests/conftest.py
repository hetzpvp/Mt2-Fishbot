"""
Test layout and markers.

Most tests are OS independent: the bot's logic runs against simulated screens
and fake window managers, and must pass on Windows and Linux alike (CI runs
both). A few tests exercise the OS layer itself (src/platforms/):

    @pytest.mark.windows   runs only on Windows (skipped elsewhere)
    @pytest.mark.linux     runs only on Linux   (skipped elsewhere)
    @pytest.mark.live      sends real input: moves the mouse and types keys.
                           Off unless asked for: pytest --live (or
                           FISHBOT_LIVE=1). Don't touch mouse/keyboard
                           while they run.

Everything under tests/os_layer/ opens real windows and takes the focus for
a moment, so it only runs when asked for: pytest --os (or FISHBOT_OS_TESTS=1;
--live implies it). tools/run_tests.py wraps all of this, including running
those tests on a private virtual display (--xvfb) on Linux.

The platform contract (tests/os_layer/test_contract.py) is the bridge: the
same checks run against the Windows implementation on Windows and the Linux
implementation on Linux, so both keep doing exactly what the shared code
expects.
"""

import os
import sys

import pytest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
for p in (TESTS, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# bot_config.json in the working directory: the GUI tests chdir into a
# temporary folder, so they never read or overwrite the real configuration
# (which lives next to the program, see qt_gui.config_file_path).
os.environ["FISHBOT_CONFIG_DIR"] = "."
# No pause chime or no-bait melody from the speakers while the tests run.
os.environ["FISHBOT_MUTE"] = "1"


def pytest_addoption(parser):
    parser.addoption("--os", action="store_true", default=False,
                     help="run the OS-layer tests (tests/os_layer): they open windows and take focus")
    parser.addoption("--live", action="store_true", default=False,
                     help="also run tests that send real mouse/keyboard input (implies --os)")


def pytest_configure(config):
    config.addinivalue_line("markers", "windows: runs only on Windows")
    config.addinivalue_line("markers", "linux: runs only on Linux")
    config.addinivalue_line("markers", "live: drives the real desktop; needs --live or FISHBOT_LIVE=1")


def pytest_collection_modifyitems(config, items):
    live = config.getoption("--live") or os.environ.get("FISHBOT_LIVE") == "1"
    os_tests = live or config.getoption("--os") or os.environ.get("FISHBOT_OS_TESTS") == "1"
    on_windows = sys.platform == "win32"
    on_linux = sys.platform.startswith("linux")
    os_layer_dir = os.path.join(TESTS, "os_layer") + os.sep
    for item in items:
        if str(item.path).startswith(os_layer_dir) and not os_tests:
            item.add_marker(pytest.mark.skip(
                reason="OS-layer test opens windows and takes focus (run with --os)"))
        if "windows" in item.keywords and not on_windows:
            item.add_marker(pytest.mark.skip(reason="Windows-only test"))
        if "linux" in item.keywords and not on_linux:
            item.add_marker(pytest.mark.skip(reason="Linux-only test"))
        if "live" in item.keywords and not live:
            item.add_marker(pytest.mark.skip(reason="live desktop test (run with --live)"))


@pytest.fixture(autouse=True)
def _no_cursor_hazard_between_tests():
    """FishingBot._cursor_hazard is shared by every bot (it stops them all):
    one test that sets it must not stop the bots of the next."""
    yield
    fishing_bot = sys.modules.get("fishing_bot")
    bot_cls = getattr(fishing_bot, "FishingBot", None)
    if bot_cls is not None and isinstance(getattr(bot_cls, "_cursor_hazard", None), tuple):
        bot_cls._cursor_hazard = None

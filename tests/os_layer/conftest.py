"""
OS-layer tests: they use the real operating system (windows, capture, and
with --live real input), never the stubs of the unit tests.

The unit tests replace pynput / pygetwindow / mss with stubs for the whole
process, so these tests run in a separate pytest process:

    python tools/run_tests.py            # unit tests, then these
    python -m pytest tests/os_layer      # just these
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def real_os_layer():
    stubs = sys.modules.get("_stubs")
    if stubs is not None and getattr(stubs, "INSTALLED", False):
        pytest.skip("OS-layer test mixed with stubbed unit tests; run tools/run_tests.py "
                    "or `pytest tests/os_layer` on its own")
    import platforms
    yield platforms.native

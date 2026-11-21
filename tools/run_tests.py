"""
Runs the test suites, the same way on Windows, Linux and in CI.

    python tools/run_tests.py              unit tests only. Never touches the
                                           desktop: safe to run while playing.
    python tools/run_tests.py --os         + OS-layer tests (tests/os_layer).
                                           They open small test windows and
                                           take the focus for a few seconds.
    python tools/run_tests.py --os --xvfb  Linux: the OS-layer tests on a
                                           private virtual display (needs
                                           Xvfb and openbox) — safe while
                                           playing.
    python tools/run_tests.py --live       + tests that send real mouse and
                                           keyboard input (and the Wine end-
                                           to-end test on Linux). Don't use
                                           the PC while they run.
    python tools/run_tests.py --serial     unit tests in one process (default:
                                           in parallel, see below).

The unit tests run in parallel (pytest-xdist, 1.5 workers per logical CPU,
24 on an 8-core/16-thread machine). Most of their time is simulations playing
whole fishing sessions in real time: ~2250 s of test time, but only ~500 s
of CPU. So more workers than cores pays off (8 workers: ~6 min, 16: ~2.6 min,
24: ~2 min, measured 2026-11-21 with a VM and two game clients running).
32 workers was barely faster and the tightest timing assertions began to
fail. conftest.SlowestFirst starts the slowest tests first, so the run is
not left waiting on one 60 s simulation at the end. The floor is the
longest test (~65 s). ``-n N`` sets the number of workers. Without pytest-xdist
installed they run serially. The OS-layer tests always run serially: they
share one desktop and its focus.

CI runs ``--os`` on Windows and on Linux (Xvfb).

The unit tests and the OS-layer tests run in two pytest processes, because
the unit tests replace the OS modules (pynput, mss, pygetwindow...) with stubs
for their whole process, while the OS-layer tests need the real ones. Other
arguments go to both pytest runs, e.g. ``-x`` or ``-k capture``.
"""

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FLAGS = ("--os", "--live", "--xvfb", "--serial")


def _parallel_args(argv) -> list:
    """pytest-xdist options for the unit tests ([] = serial)."""
    if "--serial" in argv or any(a == "-n" or a.startswith(("-n", "--numprocesses")) for a in argv):
        return []           # serial asked for, or the worker count is given explicitly
    try:
        import xdist  # noqa: F401
    except ImportError:
        print("(pytest-xdist not installed: unit tests run serially — pip install pytest-xdist)")
        return []
    # The simulations mostly wait in real time, so 1.5 workers per logical
    # CPU (see the docstring for the numbers). worksteal plus
    # conftest.SlowestFirst keeps the slow simulations spread over the
    # workers. Each worker is its own process, so the stubbed OS modules
    # stay isolated.
    workers = max(2, (os.cpu_count() or 2) * 3 // 2)
    return ["-n", str(workers), "--dist", "worksteal"]


class VirtualDisplay:
    """Xvfb + openbox on a free display number (Linux)."""

    def __init__(self):
        xvfb, wm = shutil.which("Xvfb"), os.environ.get("FISHBOT_XVFB_WM") or shutil.which("openbox")
        if not xvfb or not wm:
            sys.exit("--xvfb needs Xvfb and openbox (Fedora: sudo dnf install xorg-x11-server-Xvfb openbox; "
                     "Debian/Ubuntu: sudo apt install xvfb openbox)")
        num = next(n for n in range(99, 200) if not os.path.exists(f"/tmp/.X11-unix/X{n}"))
        self.display = f":{num}"
        self.procs = [subprocess.Popen([xvfb, self.display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
        deadline = time.time() + 10
        while not os.path.exists(f"/tmp/.X11-unix/X{num}") and time.time() < deadline:
            time.sleep(0.05)
        self.env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
        self.env["DISPLAY"] = self.display
        self.procs.append(subprocess.Popen([wm], env=self.env,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(1.0)  # let the window manager take over the screen

    def close(self):
        for p in reversed(self.procs):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()


def main(argv) -> int:
    live = "--live" in argv
    os_tests = live or "--os" in argv
    xvfb = "--xvfb" in argv
    extra = [a for a in argv if a not in _FLAGS]
    if xvfb and live:
        sys.exit("--live can't use --xvfb: real input devices reach the real desktop, not a virtual display")
    if xvfb and not sys.platform.startswith("linux"):
        sys.exit("--xvfb is Linux only")

    # Worker-count options only go to the unit tests: the OS-layer tests share
    # one desktop (focus, cursor) and must run one at a time.
    serial_extra, i = [], 0
    while i < len(extra):
        a = extra[i]
        if a in ("-n", "--numprocesses", "--dist"):
            i += 2
            continue
        if not a.startswith(("-n", "--numprocesses=", "--dist=")):
            serial_extra.append(a)
        i += 1

    runs = [("unit tests", ["tests", "--ignore=tests/os_layer", *_parallel_args(argv), *extra], None)]
    if os_tests:
        flags = ["--live"] if live else ["--os"]
        runs.append(("OS-layer tests" + (" (live input)" if live else ""),
                     ["tests/os_layer", *flags, *serial_extra], "xvfb" if xvfb else None))

    failed = []
    for label, args, display in runs:
        vd = VirtualDisplay() if display == "xvfb" else None
        where = f" on virtual display {vd.display}" if vd else ""
        print(f"\n===== {label}{where} =====", flush=True)
        try:
            code = subprocess.call([sys.executable, "-m", "pytest", "-rs", *args], cwd=ROOT,
                                   env=vd.env if vd else None)
        finally:
            if vd:
                vd.close()
        # 5 = nothing collected/selected (e.g. -k matched only the other suite)
        if code not in (0, 5):
            failed.append(label)

    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    if not os_tests:
        print("Unit tests passed. (OS-layer tests not run: add --os, or --os --xvfb on Linux.)")
    else:
        print("All suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

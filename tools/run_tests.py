"""
Runs the test suites, the same way on Windows, Linux and in CI.

    python tools/run_tests.py              unit tests only. Never touches the
                                           desktop: safe to run while playing.
    python tools/run_tests.py --os         + OS-layer tests (tests/os_layer).
                                           They open small test windows and
                                           take the focus for a few seconds.
    python tools/run_tests.py --os --xvfb  Linux: the OS-layer tests on a
                                           private virtual display (needs
                                           Xvfb; openbox if installed) — safe while
                                           playing.
    python tools/run_tests.py --live       + tests that send real mouse and
                                           keyboard input (and the Wine end-
                                           to-end test on Linux). Don't use
                                           the PC while they run.
    python tools/run_tests.py --serial     unit tests in one process (default:
                                           in parallel, see below).
    python tools/run_tests.py --no-real    skip the real-client tests (below).

Real-client tests (tests/realclient, Linux, on by default): the bot against
real Metin2 clients of the local test rig (metin2-local-server: two server
VMs, Wine clients). They run on hidden displays (Xvfb + a tiny window
manager, tools/realclient), so they never touch the screen, mouse or
keyboard: safe while playing. Each server instance runs in its own worker,
the two in parallel, after the unit tests (each client takes most of a CPU
core). Without the rig (no checkout, no VMs, not Linux) they are skipped.
They take several minutes; ``--no-real`` leaves them out.

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
_FLAGS = ("--os", "--live", "--xvfb", "--serial", "--no-real")


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
    """Xvfb + a window manager (openbox, or tools/realclient/miniwm.py) on a
    free display number (Linux)."""

    def __init__(self):
        xvfb, wm = shutil.which("Xvfb"), os.environ.get("FISHBOT_XVFB_WM") or shutil.which("openbox")
        wm_cmd = [wm] if wm else [sys.executable, os.path.join(ROOT, "tools", "realclient", "miniwm.py")]
        if not xvfb:
            sys.exit("--xvfb needs Xvfb (Fedora: sudo dnf install xorg-x11-server-Xvfb; "
                     "Debian/Ubuntu: sudo apt install xvfb)")
        num = next(n for n in range(99, 200) if not os.path.exists(f"/tmp/.X11-unix/X{n}"))
        self.display = f":{num}"
        self.procs = [subprocess.Popen([xvfb, self.display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
        deadline = time.time() + 10
        while not os.path.exists(f"/tmp/.X11-unix/X{num}") and time.time() < deadline:
            time.sleep(0.05)
        self.env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
        self.env["DISPLAY"] = self.display
        self.procs.append(subprocess.Popen(wm_cmd, env=self.env,
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

    runs = [("unit tests", ["tests", "--ignore=tests/os_layer", "--ignore=tests/realclient",
                            *_parallel_args(argv), *extra], None)]
    if os_tests:
        flags = ["--live"] if live else ["--os"]
        runs.append(("OS-layer tests" + (" (live input)" if live else ""),
                     ["tests/os_layer", *flags, *serial_extra], "xvfb" if xvfb else None))

    real = "--no-real" not in argv and sys.platform.startswith("linux")
    if real:
        # One pytest process per server instance, classic and fish side by
        # side (or one after the other with -n 0). Not xdist: its loadgroup
        # does nothing on pytest 9 (the group suffix goes to Node._nodeid,
        # which pytest no longer reads), so one instance's modules landed on
        # both workers, which then fought over the same VM and clients.
        runs.append(("real-client tests (hidden displays)",
                     {inst: [*files, *serial_extra] for inst, files in _realclient_modules().items()},
                     "real-parallel" if _parallel_args(argv) else "real-serial"))

    failed = []
    for label, args, display in runs:
        vd = VirtualDisplay() if display == "xvfb" else None
        where = f" on virtual display {vd.display}" if vd else ""
        print(f"\n===== {label}{where} =====", flush=True)
        try:
            env = vd.env if vd else None
            if display in ("real-parallel", "real-serial"):
                env = dict(os.environ, FISHBOT_REALCLIENT="1",
                           FISHBOT_REALCLIENT_STAMP=time.strftime("%Y%m%d_%H%M%S"))
                code = _run_per_instance(args, env, parallel=display == "real-parallel")
            else:
                code = subprocess.call([sys.executable, "-m", "pytest", "-rs", *args], cwd=ROOT, env=env)
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
        print("Tests passed. (OS-layer tests not run: add --os, or --os --xvfb on Linux.)")
    else:
        print("All suites passed.")
    return 0


def _realclient_modules() -> dict:
    """{instance: [test files]} from each module's INSTANCE = "...".
    Files without one (helpers) are left out."""
    import re
    out = {}
    folder = os.path.join(ROOT, "tests", "realclient")
    for name in sorted(os.listdir(folder)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            m = re.search(r'^INSTANCE\s*=\s*["\'](\w+)["\']', f.read(), re.M)
        if m:
            out.setdefault(m.group(1), []).append(os.path.join("tests", "realclient", name))
    return out


def _run_per_instance(per_instance: dict, env: dict, parallel: bool) -> int:
    """One pytest process per rig instance; output lines prefixed with the
    instance. Returns 0, 5 (nothing collected anywhere) or the first failure."""
    import threading

    def start(inst, args):
        return subprocess.Popen([sys.executable, "-m", "pytest", "-rs", *args], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    def pump(inst, proc):
        for line in proc.stdout:
            print(f"[{inst}] {line}", end="", flush=True)

    codes = {}
    if parallel:
        procs = {inst: start(inst, args) for inst, args in per_instance.items()}
        threads = [threading.Thread(target=pump, args=(i, p), daemon=True) for i, p in procs.items()]
        for t in threads:
            t.start()
        for inst, p in procs.items():
            codes[inst] = p.wait()
        for t in threads:
            t.join(timeout=5)
    else:
        for inst, args in per_instance.items():
            p = start(inst, args)
            pump(inst, p)
            codes[inst] = p.wait()
    for inst, code in codes.items():
        print(f"[{inst}] exit code {code}", flush=True)
    bad = [c for c in codes.values() if c not in (0, 5)]
    if bad:
        return bad[0]
    return 0 if any(c == 0 for c in codes.values()) else 5


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

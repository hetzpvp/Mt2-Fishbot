"""
Process disguise: each start runs under a different, ordinary-looking name.

With the "Disguise in Task Manager" setting on, the packaged program copies
itself to a randomly named file (``PixelStudio.exe``, ``CedarJournal``, ...)
in a temporary folder, starts that copy and quits. The copy is what runs:
Windows Task Manager (Processes and Details tabs), Linux System Monitor, ps
and htop show the new name, and the main window title is the same name. The
next start removes the copies that are no longer running.

This is an extra layer of privacy, not invisibility: the program's icon in
the exe, its memory use, the Qt and Python libraries it loads and the window
itself still look the same to anyone who looks closely.

Only the packaged program can do this (the Python interpreter cannot be
renamed); from source the setting does nothing. The settings, diagnostics
and solver cache stay in the original program folder: ``FISHBOT_HOME``
tells the copy where that is (diag.program_dir, cache_location).

Runs before any heavy import (see fishing_bot.py): only the standard library.
"""

import json
import os
import secrets
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

CONFIG_KEY = "disguise_process"
CONFIG_NAME = "bot_config.json"
APP_DIR_NAME = "FishingPuzzlePlayer"
LEDGER_NAME = "disguise_copies.json"

ENV_HOME = "FISHBOT_HOME"              # the original program folder
ENV_NAME = "FISHBOT_DISGUISE_NAME"     # display name of the running copy
ENV_ORIGINAL = "FISHBOT_ORIGINAL_EXE"  # the real program (for Restart)
_OUR_ENV = (ENV_HOME, ENV_NAME, ENV_ORIGINAL)

# Two plain words make a name like "Pixel Studio" (file PixelStudio.exe).
# Nothing here is, or is close to, a real Windows or Linux process
# (no svchost, Notepad, WordPad, Task Host...): copying system names is what
# malware does and what antivirus programs look for. Every name is at most
# 15 letters without the space, Linux's limit for a process name.
FIRST_WORDS = ("Pixel", "Color", "Frame", "Clip", "Page", "Pocket", "Bright", "Simple",
               "Story", "Track", "Sketch", "Paper", "Echo", "Leaf", "Stone", "River",
               "Maple", "Cedar", "Nova", "Orbit", "Lumen", "Tidy", "Snap", "Velvet")
SECOND_WORDS = ("Board", "Notes", "Studio", "Desk", "Box", "Craft", "Works", "Lab",
                "Kit", "Book", "Draw", "Planner", "Journal", "Reader", "Mixer", "Shelf")

# Set when this start should have been disguised but could not be (the GUI
# shows it); None otherwise.
LAST_ERROR: Optional[str] = None


def random_name() -> str:
    """A display name like "Pixel Studio"."""
    return f"{secrets.choice(FIRST_WORDS)} {secrets.choice(SECOND_WORDS)}"


def file_stem(display_name: str) -> str:
    return display_name.replace(" ", "")


def active_name() -> Optional[str]:
    """The name this process runs under, or None when it is not disguised."""
    return os.environ.get(ENV_NAME) or None


def original_executable() -> str:
    """The real program: what Restart starts again."""
    return os.environ.get(ENV_ORIGINAL) or sys.executable


def available() -> bool:
    """Only the packaged program can run under another name."""
    return bool(getattr(sys, "frozen", False))


# ---------------------------------------------------------------- locations

def _user_base() -> str:
    """Per-user state folder, as in diag.user_dir (without "diagnostics")."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, APP_DIR_NAME)


def _program_dir() -> str:
    home = os.environ.get(ENV_HOME)
    if home and os.path.isdir(home):
        return home
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_candidates() -> List[str]:
    """Where bot_config.json may be, in the order qt_gui.config_file_path
    looks: next to the program, then the per-user folder."""
    override = os.environ.get("FISHBOT_CONFIG_DIR")
    if override:
        return [os.path.join(os.path.abspath(override), CONFIG_NAME)]
    return [os.path.join(_program_dir(), CONFIG_NAME), os.path.join(_user_base(), CONFIG_NAME)]


def enabled_in_config() -> bool:
    for path in config_candidates():
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                return False
            return isinstance(cfg, dict) and bool(cfg.get(CONFIG_KEY, False))
    return False


def copies_root() -> str:
    """Where the copies go. Linux: ~/.cache, not /tmp, which is often kept
    in memory (tmpfs) and the program is about 200 MB."""
    if sys.platform == "win32":
        return tempfile.gettempdir()
    return os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")


def ledger_path() -> str:
    return os.path.join(_user_base(), LEDGER_NAME)


# ---------------------------------------------------------------- ledger

def _read_ledger() -> List[str]:
    try:
        with open(ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return [str(p) for p in data if isinstance(p, str)] if isinstance(data, list) else []
    except Exception:
        return []


def _write_ledger(paths: List[str]) -> None:
    path = ledger_path()
    try:
        if not paths:
            if os.path.exists(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(paths, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _in_use_linux(exe: str) -> bool:
    """Linux lets a running program's file be deleted, and a running copy
    still needs it (multiprocessing helpers start it again)."""
    target = os.path.realpath(exe)
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return True
    for pid in pids:
        try:
            link = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            continue
        if link == target or link == target + " (deleted)":
            return True
    return False


def _remove_copy(exe: str) -> bool:
    """Deletes one copy and its folder. True when it is gone. Only the file
    recorded in the ledger and then its folder if empty: never a tree."""
    if not os.path.exists(exe):
        folder = os.path.dirname(exe)
        try:
            if os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
        except OSError:
            pass
        return True
    if sys.platform != "win32" and _in_use_linux(exe):
        return False
    try:
        os.remove(exe)  # Windows refuses while the copy runs: kept for next time
    except OSError:
        return False
    try:
        os.rmdir(os.path.dirname(exe))
    except OSError:
        pass
    return True


def cleanup_old_copies() -> int:
    """Removes the copies of earlier starts that are no longer running.
    Returns how many are left (still running)."""
    left = [exe for exe in _read_ledger() if not _remove_copy(exe)]
    _write_ledger(left)
    return len(left)


# ---------------------------------------------------------------- launching

def clean_environment(env: dict, bundle_dir: Optional[str] = None) -> dict:
    """The environment for a new start of the packaged program.

    PyInstaller marks its child process with _PYI_* variables, and its
    runtime hooks point QT_PLUGIN_PATH, PATH (Windows) and LD_LIBRARY_PATH
    (Linux) into this process's unpacked bundle folder, which is deleted
    when this process ends. A new start inheriting them would take itself
    for this process's child or load Qt from a folder that is gone, so they
    are removed; PYINSTALLER_RESET_ENVIRONMENT makes its bootloader start
    fresh."""
    out = dict(env)
    for key in list(out):
        if key.startswith("_PYI_") or key == "_MEIPASS2" or key in _OUR_ENV:
            del out[key]
    if "LD_LIBRARY_PATH_ORIG" in out:
        out["LD_LIBRARY_PATH"] = out.pop("LD_LIBRARY_PATH_ORIG")
    elif sys.platform != "win32" and bundle_dir:
        out.pop("LD_LIBRARY_PATH", None)
    if bundle_dir:
        def inside(value: str) -> bool:
            a = os.path.normcase(os.path.abspath(value)) if value else ""
            b = os.path.normcase(os.path.abspath(bundle_dir))
            return a == b or a.startswith(b + os.sep)

        for key in list(out):
            parts = out[key].split(os.pathsep)
            kept = [p for p in parts if not inside(p)]
            if len(kept) == len(parts):
                continue
            if kept:
                out[key] = os.pathsep.join(kept)
            else:
                del out[key]
    out["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return out


def _detach_kwargs() -> dict:
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kw["start_new_session"] = True
    return kw


def make_copy(source: str, name: str) -> str:
    """Copies the program to <copies root>/<random folder>/<Name>[.exe] and
    records it in the ledger before it runs (so a crash still gets it
    cleaned up)."""
    import shutil
    folder = os.path.join(copies_root(), secrets.token_hex(4))
    os.makedirs(folder)
    ext = ".exe" if sys.platform == "win32" else ""
    exe = os.path.join(folder, file_stem(name) + ext)
    _write_ledger(_read_ledger() + [exe])
    shutil.copy2(source, exe)
    if sys.platform != "win32":
        os.chmod(exe, 0o755)
    return exe


def _is_helper_start(argv: List[str]) -> bool:
    """multiprocessing re-runs the program for its helpers: never relaunch those."""
    return any(a.startswith("--multiprocessing") or a == "-c" for a in argv[1:])


def maybe_relaunch() -> None:
    """At program start: when disguise is on, start a renamed copy and quit.

    Does nothing from source, in the copy itself, or for multiprocessing
    helpers. Any failure leaves this start running normally, with the
    reason in LAST_ERROR for the GUI to show."""
    global LAST_ERROR
    if not available() or active_name() or _is_helper_start(sys.argv):
        return
    try:
        cleanup_old_copies()
    except Exception:
        pass
    if not enabled_in_config():
        return
    try:
        name = random_name()
        original = os.path.abspath(sys.executable)
        exe = make_copy(original, name)
        env = clean_environment(os.environ, getattr(sys, "_MEIPASS", None))
        env[ENV_HOME] = _program_dir()
        env[ENV_NAME] = name
        env[ENV_ORIGINAL] = original
        subprocess.Popen([exe, *sys.argv[1:]], env=env, cwd=os.getcwd(), **_detach_kwargs())
    except Exception as e:
        LAST_ERROR = f"{type(e).__name__}: {e}"
        return
    sys.exit(0)


def restart_command() -> Tuple[List[str], dict]:
    """(command, environment) that starts the program again from scratch:
    the real program, which disguises itself again if the setting says so."""
    if getattr(sys, "frozen", False):
        cmd = [original_executable(), *sys.argv[1:]]
    else:
        cmd = [sys.executable, *sys.argv]
    return cmd, clean_environment(os.environ, getattr(sys, "_MEIPASS", None))


def restart() -> None:
    """Starts a fresh copy of the program (the caller then quits)."""
    cmd, env = restart_command()
    subprocess.Popen(cmd, env=env, cwd=os.getcwd(), **_detach_kwargs())

"""
Where the solver's move table (solver_cache*.npz) is kept.

Shared by the solver (deterministic.py) and the GUI, which only needs the
path and must not import numba for it.

Next to the program (the .exe when frozen, the project root from source)
when that folder can be written to; otherwise — the program installed under
Program Files, say — in a per-user folder. A table already present in either
place is used where it is. ``JIGSAW_SOLVER_CACHE_DIR`` overrides both.
"""

import os
import sys

APP_DIR_NAME = "FishingPuzzlePlayer"
_SUBDIR = os.path.join("data", "cache", "jigsaw")


def cache_file_name() -> str:
    precision = os.environ.get("JIGSAW_SOLVER_PRECISION", "64").strip()
    return "solver_cache_f64.npz" if precision not in ("32",) else "solver_cache.npz"


def program_cache_dir() -> str:
    if getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.abspath(sys.executable))
    else:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, _SUBDIR)


def user_cache_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, APP_DIR_NAME, "jigsaw")


def _can_write(folder: str) -> bool:
    """Really writes a probe file: os.access lies on Windows (UAC / ACLs)."""
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, f".write_probe_{os.getpid()}")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def cache_dir(create: bool = False) -> str:
    """The folder of the move table. Without ``create`` nothing is written:
    an existing table's folder, else the program folder when it looks
    writable, else the per-user folder."""
    override = os.environ.get("JIGSAW_SOLVER_CACHE_DIR")
    if override:
        return override
    name = cache_file_name()
    primary, fallback = program_cache_dir(), user_cache_dir()
    for folder in (primary, fallback):
        if os.path.exists(os.path.join(folder, name)):
            return folder
    if create:
        return primary if _can_write(primary) else fallback
    parent = primary
    while not os.path.isdir(parent) and os.path.dirname(parent) != parent:
        parent = os.path.dirname(parent)
    return primary if os.access(parent, os.W_OK) else fallback


def cache_path(create: bool = False) -> str:
    return os.path.join(cache_dir(create), cache_file_name())

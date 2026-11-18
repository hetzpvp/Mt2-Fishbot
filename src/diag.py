"""
Diagnostic log: one file that reconstructs a bot session.

End users run the packaged exe and cannot attach a debugger, so when a
problem cannot be reproduced they switch "Diagnostic log" on (Settings tab),
reproduce it and send the zip this module leaves next to the program:

    <exe folder>/diagnostics/<YYYYMMDD_HHMMSS>.zip
        <YYYYMMDD_HHMMSS>/session.log          everything, in order
        <YYYYMMDD_HHMMSS>/bot_config.json      the config file as it was on disk
        <YYYYMMDD_HHMMSS>/config_effective.json what each bot actually used
        <YYYYMMDD_HHMMSS>/NNNN_W1_<label>.png  frames that were analysed

Line format (one event per line, written and flushed immediately):

    14:03:22.123 [W1] ANALYSIS fishing_bot.py:512 _scan_empty_slots | ...

Categories: SESSION, CONFIG, GUI, ACTION (input sent), FOCUS, CAPTURE,
ANALYSIS (what a detector measured), DECISION (what the bot does next),
STATE, STAT (status/stats callbacks the GUI gets), CALL (function entry /
exit, from ``traced``), LOOP (per-wait / per-game summaries), IMAGE, EXC,
DIAG (the logger itself).

Cost when switched off: callers test the module flag ``diag.ENABLED`` before
building a message, an image or a summary; traced functions add one global
lookup. A failure inside this module never reaches the bot: it is swallowed
and counted (reported in the session footer).

Per-frame loops (minigame, waits) wrap their body in ``diag.loop(...)``:
inside it only state changes, actions and anomalies are written and the loop
writes one summary at the end; with VERBOSE every frame is written too.
"""

from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import platform
import re
import shutil
import sys
import threading
import time
import traceback
import zipfile
from collections import deque
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- switches

ENABLED = False   # "Diagnostic log" is on: events are recorded
VERBOSE = False   # also every frame of per-frame loops

LOG_NAME = "session.log"
DIR_NAME = "diagnostics"
APP_DIR_NAME = "FishingPuzzlePlayer"
KEEP_SESSIONS = 10

MAX_IMAGES = 400                       # per session
MAX_IMAGE_BYTES = 300 * 1024 * 1024    # per session
MAX_IMAGES_PER_LABEL = 25              # one label repeating cannot eat the budget
MAX_LOG_BYTES = 400 * 1024 * 1024      # after this only non-frame lines; at 2x nothing
_FSYNC_EVERY = 2.0                     # seconds between fsyncs of the log

_STAMP_RE = re.compile(r"^(\d{8}_\d{6}(?:_\d+)?)(\.zip)?$")

_lock = threading.RLock()
_tls = threading.local()
_session: Optional["_Session"] = None
_pre_buffer: deque = deque(maxlen=4000)   # events while no session is open
_failures = 0
_last_zip: Optional[str] = None
_last_dir: Optional[str] = None
_last_dir_fallback = False
_hooks_installed = False
_setting = False   # what the user chose; ENABLED also stays on while a session is open
_zip_threads: List[threading.Thread] = []
_busy_folders: set = set()   # session folders being written or packed: never touched by recovery


def _fail() -> None:
    """A logging problem: counted, never raised."""
    global _failures
    _failures += 1


def failures() -> int:
    return _failures


# ---------------------------------------------------------------- locations

def program_dir() -> str:
    """Folder of the program that is running: the folder of the .exe when
    frozen (not the temporary bundle folder, not the working directory),
    the project root when run from source."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_dir() -> str:
    """Per-user fallback when the program folder cannot be written."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, APP_DIR_NAME, DIR_NAME)


def _can_write(path: str) -> bool:
    """Really writes a probe file: os.access lies on Windows (UAC / ACLs)."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".write_probe_{os.getpid()}")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def location(create: bool = False) -> Tuple[str, bool]:
    """(diagnostics folder, is_fallback). With ``create`` the folder is
    created and proven writable (falls back to the per-user folder);
    without, nothing is written and the answer is a best guess."""
    global _last_dir, _last_dir_fallback
    primary = os.path.join(program_dir(), DIR_NAME)
    if create:
        if _can_write(primary):
            _last_dir, _last_dir_fallback = primary, False
        else:
            fb = user_dir()
            _can_write(fb)
            _last_dir, _last_dir_fallback = fb, True
        return _last_dir, _last_dir_fallback
    if _last_dir:
        return _last_dir, _last_dir_fallback
    probe = primary if os.path.isdir(primary) else program_dir()
    if os.access(probe, os.W_OK):
        return primary, False
    return user_dir(), True


def session_dirs() -> List[str]:
    """Every folder diagnostics may have written to (primary + fallback)."""
    out = [os.path.join(program_dir(), DIR_NAME), user_dir()]
    return [d for i, d in enumerate(out) if d not in out[:i]]


# ---------------------------------------------------------------- tags / source

def set_thread_tag(tag: Optional[str]) -> None:
    """Window tag for every line this thread writes (e.g. "W1")."""
    _tls.tag = tag


def thread_tag() -> str:
    tag = getattr(_tls, "tag", None)
    if tag:
        return tag
    t = threading.current_thread()
    return "GUI" if t is threading.main_thread() else t.name


def _src(depth: int) -> str:
    """'module.py:line function' of the frame ``depth`` levels above the caller."""
    try:
        f = sys._getframe(depth + 1)
        return f"{os.path.basename(f.f_code.co_filename)}:{f.f_lineno} {f.f_code.co_name}"
    except Exception:
        return "?"


def _indent() -> str:
    return "  " * getattr(_tls, "depth", 0)


def in_loop() -> bool:
    return getattr(_tls, "loop", 0) > 0


def detail_on() -> bool:
    """Whether per-frame detail is written right now (outside a loop, or VERBOSE)."""
    return ENABLED and (VERBOSE or getattr(_tls, "loop", 0) == 0)


# ---------------------------------------------------------------- writing

class _Session:
    def __init__(self, folder: str, stamp: str):
        self.folder = folder
        self.stamp = stamp
        self.log_path = os.path.join(folder, LOG_NAME)
        self.file = open(self.log_path, "a", encoding="utf-8", newline="\n")
        self.bytes = 0
        self.last_fsync = time.monotonic()
        self.images = 0
        self.image_bytes = 0
        self.label_counts: Dict[str, int] = {}
        self.firsts: set = set()
        self.cap_logged: set = set()
        self.effective: Dict[str, Any] = {}
        self.started = time.time()
        self.closed = False
        self.ending = False
        self.dropped = 0
        self.writing_images = 0

    def write(self, line: str, frame_level: bool) -> None:
        if self.closed:
            _pre_buffer.append(line)
            return
        if self.bytes > MAX_LOG_BYTES and (frame_level or self.bytes > 2 * MAX_LOG_BYTES):
            self.dropped += 1
            if "logcap" not in self.cap_logged:
                self.cap_logged.add("logcap")
                note = (f"{_now()} [DIAG] DIAG     diag.py | log size cap ({MAX_LOG_BYTES >> 20} MB) "
                        f"reached: per-frame lines are dropped from here on\n")
                self.file.write(note)
            return
        self.file.write(line + "\n")
        self.file.flush()
        self.bytes += len(line) + 1
        now = time.monotonic()
        if now - self.last_fsync >= _FSYNC_EVERY:
            self.last_fsync = now
            try:
                os.fsync(self.file.fileno())
            except Exception:
                pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.file.flush()
            os.fsync(self.file.fileno())
        except Exception:
            pass
        try:
            self.file.close()
        except Exception:
            pass


def _now() -> str:
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"


def _write(cat: str, msg: str, tag: Optional[str], src: str, frame_level: bool = False) -> None:
    line = f"{_now()} [{tag or thread_tag()}] {cat:<8} {src} | {_indent()}{msg}"
    if "\n" in line:
        line = line.replace("\n", "\n    ")
    with _lock:
        s = _session
        if s is not None:
            s.write(line, frame_level)
        else:
            _pre_buffer.append(line)


def log(cat: str, msg: str, *, tag: Optional[str] = None, depth: int = 0) -> None:
    """Writes one event. ``depth``: extra stack levels to skip for the
    source reference (for helpers that log on behalf of their caller)."""
    if not ENABLED:
        return
    try:
        _write(cat, msg, tag, _src(depth + 1))
    except Exception:
        _fail()


def trace(cat: str, msg: str, *, tag: Optional[str] = None, depth: int = 0) -> None:
    """Per-frame detail: written outside loops, inside them only with VERBOSE."""
    if not ENABLED or (not VERBOSE and getattr(_tls, "loop", 0)):
        return
    try:
        _write(cat, msg, tag, _src(depth + 1), frame_level=True)
    except Exception:
        _fail()


def exc(msg: str = "exception caught", *, tag: Optional[str] = None, depth: int = 0) -> None:
    """Call inside an ``except`` block: logs the exception being handled,
    with its full traceback, at the place it was caught."""
    if not ENABLED:
        return
    try:
        et, ev, tb = sys.exc_info()
        text = "".join(traceback.format_exception(et, ev, tb)).rstrip() if et else "(no active exception)"
        _write("EXC", f"{msg}: {et.__name__ if et else ''}: {ev}\n{text}", tag, _src(depth + 1))
    except Exception:
        _fail()


def block(cat: str, title: str, lines: Iterable[str], *, tag: Optional[str] = None) -> None:
    """A header line followed by indented lines (session header, config dump)."""
    if not ENABLED:
        return
    try:
        src = _src(1)
        _write(cat, title, tag, src)
        for ln in lines:
            _write(cat, "    " + str(ln), tag, src)
    except Exception:
        _fail()


# ---------------------------------------------------------------- summaries

def summarize(value: Any, limit: int = 160) -> str:
    """Short, loggable description of an argument or result."""
    try:
        text = _summ(value, limit, 0)
        cap = max(300, limit * 3)
        return text if len(text) <= cap else text[:cap] + f"… ({len(text)} chars)"
    except Exception:
        _fail()
        return f"<{type(value).__name__}>"


def _summ(v: Any, limit: int, level: int) -> str:
    if v is None or isinstance(v, (bool, int)):
        return repr(v)
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, str):
        return repr(v if len(v) <= limit else v[:limit] + "…")
    if isinstance(v, bytes):
        return f"bytes(len={len(v)})"
    np = sys.modules.get("numpy")
    if np is not None and isinstance(v, np.ndarray):
        return array_summary(v)
    if np is not None and isinstance(v, np.generic):
        return repr(v.item())
    if isinstance(v, (list, tuple, set, frozenset, deque)):
        items = list(v)
        open_, close = ("[", "]") if isinstance(v, (list, deque)) else ("(", ")") if isinstance(v, tuple) else ("{", "}")
        if level >= 2:
            return f"{type(v).__name__}(len={len(items)})"
        shown = items[:8]
        inner = ", ".join(_summ(x, 40, level + 1) for x in shown)
        more = f", … +{len(items) - 8}" if len(items) > 8 else ""
        if isinstance(v, tuple) and len(items) == 1:
            inner += ","
        return f"{open_}{inner}{more}{close}"
    if isinstance(v, dict):
        if level >= 2:
            return f"dict(len={len(v)})"
        items = list(v.items())[:8]
        inner = ", ".join(f"{_summ(k, 30, level + 1)}: {_summ(x, 40, level + 1)}" for k, x in items)
        more = f", … +{len(v) - 8}" if len(v) > 8 else ""
        return "{" + inner + more + "}"
    if hasattr(v, "__dataclass_fields__"):
        if level >= 2:
            return type(v).__name__
        parts = []
        for name in list(v.__dataclass_fields__)[:10]:
            parts.append(f"{name}={_summ(getattr(v, name, None), 40, level + 1)}")
        return f"{type(v).__name__}({', '.join(parts)})"
    name = type(v).__name__
    bot_id = getattr(v, "bot_id", None)
    if isinstance(bot_id, int):
        return f"<{name} W{bot_id + 1}>"
    if callable(v):
        return f"<{getattr(v, '__qualname__', name)}>"
    return f"<{name}>"


def frame_stats(arr) -> Optional[dict]:
    """mean/std/min/max of an image on a subsample (cheap), with flags for
    black and flat frames. None for anything that is not an image array."""
    np = sys.modules.get("numpy")
    if np is None or not isinstance(arr, np.ndarray) or arr.size == 0:
        return None
    step = max(1, int((arr.shape[0] * arr.shape[1] / 40000) ** 0.5)) if arr.ndim >= 2 else 1
    sub = arr[::step, ::step] if arr.ndim >= 2 else arr
    mean = float(sub.mean())
    std = float(sub.std())
    mx = int(sub.max())
    return {"mean": mean, "std": std, "min": int(sub.min()), "max": mx,
            "black": mx < 8, "flat": std < 2.0}


def array_summary(arr) -> str:
    st = frame_stats(arr) if getattr(arr, "ndim", 0) >= 2 and arr.dtype.kind in "uif" else None
    base = f"array{tuple(arr.shape)} {arr.dtype}"
    if st is None:
        return base
    flags = " BLACK" if st["black"] else (" FLAT" if st["flat"] else "")
    return f"{base} mean={st['mean']:.1f} std={st['std']:.1f} min={st['min']} max={st['max']}{flags}"


# ---------------------------------------------------------------- tracing

def _tag_of(obj) -> Optional[str]:
    tag = getattr(obj, "diag_tag", None)
    if tag:
        return tag
    bot_id = getattr(obj, "bot_id", None)
    if isinstance(bot_id, int):
        return f"W{bot_id + 1}"
    return None


def _call_traced(fn, qual: str, method: bool, a, k):
    entry = None
    try:
        if VERBOSE or not getattr(_tls, "loop", 0):
            tag = _tag_of(a[0]) if (method and a) else None
            if tag is None and isinstance(k.get("bot_id"), int):
                tag = f"W{k['bot_id'] + 1}"
            shown = a[1:] if method else a
            args = ", ".join([summarize(x, 60) for x in shown] +
                             [f"{key}={summarize(x, 60)}" for key, x in k.items()])
            src = _src(2)
            _write("CALL", f"→ {qual}({args})", tag, src)
            _tls.depth = getattr(_tls, "depth", 0) + 1
            entry = (tag, src)
    except Exception:
        _fail()
    if entry is None:
        return fn(*a, **k)
    t0 = time.perf_counter()
    try:
        result = fn(*a, **k)
    except BaseException as e:
        try:
            _tls.depth = max(0, getattr(_tls, "depth", 1) - 1)
            ms = (time.perf_counter() - t0) * 1000
            _write("CALL", f"✗ {qual} raised {type(e).__name__}: {e} [{ms:.1f} ms]", entry[0], entry[1])
        except Exception:
            _fail()
        raise
    try:
        _tls.depth = max(0, getattr(_tls, "depth", 1) - 1)
        ms = (time.perf_counter() - t0) * 1000
        _write("CALL", f"← {qual} = {summarize(result, 120)} [{ms:.1f} ms]", entry[0], entry[1])
    except Exception:
        _fail()
    return result


def traced(fn=None, *, method: Optional[bool] = None):
    """Decorator: logs entry (summarised arguments) and exit (summarised
    result, duration) of the function while diagnostics is on. Inside a
    per-frame loop only with VERBOSE."""
    def deco(f):
        if getattr(f, "_diag_traced", False):
            return f
        qual = f.__qualname__
        is_method = bool(method)

        @functools.wraps(f)
        def wrapper(*a, **k):
            if not ENABLED:
                return f(*a, **k)
            return _call_traced(f, qual, is_method, a, k)

        wrapper._diag_traced = True
        return wrapper
    return deco(fn) if fn is not None else deco


_class_traces: List[Tuple[Any, str, Any, Any]] = []   # (cls, name, original, wrapped)
_class_tracing_on = False


def trace_class(cls, exclude: Iterable[str] = ()) -> Any:
    """Registers tracing for every method defined on ``cls`` (plain, static
    and class methods; not properties), except ``exclude``.

    The wrappers are installed on the class only while diagnostics is on
    and the original methods are put back when it goes off, so a run
    without diagnostics executes exactly the original code."""
    skip = set(exclude)
    for name, attr in list(vars(cls).items()):
        if name in skip or (name.startswith("__") and name != "__init__"):
            continue
        try:
            if isinstance(attr, staticmethod):
                wrapped = staticmethod(traced(attr.__func__, method=False))
            elif isinstance(attr, classmethod):
                wrapped = classmethod(traced(attr.__func__, method=True))
            elif callable(attr) and hasattr(attr, "__code__"):
                wrapped = traced(attr, method=True)
            else:
                continue
            _class_traces.append((cls, name, attr, wrapped))
            if _class_tracing_on:
                setattr(cls, name, wrapped)
        except Exception:
            _fail()
    return cls


def _set_class_tracing(on: bool) -> None:
    """Installs (on) or removes (off) the method wrappers of trace_class.
    A method someone replaced in the meantime (tests) is left alone."""
    global _class_tracing_on
    on = bool(on)
    if on == _class_tracing_on:
        return
    _class_tracing_on = on
    for cls, name, original, wrapped in _class_traces:
        try:
            current = vars(cls).get(name)
            if on and current is original:
                setattr(cls, name, wrapped)
            elif not on and current is wrapped:
                setattr(cls, name, original)
        except Exception:
            _fail()


def trace_module(module, names: Iterable[str]) -> None:
    """Applies ``traced`` to module-level functions by name."""
    for name in names:
        f = getattr(module, name, None)
        if callable(f) and hasattr(f, "__code__"):
            try:
                setattr(module, name, traced(f, method=False))
            except Exception:
                _fail()


# ---------------------------------------------------------------- loops

class _NullLoop:
    """Returned by loop() while diagnostics is off: every call is a no-op."""
    active = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def frame(self, *a, **k):
        pass

    def count(self, *a, **k):
        pass

    def score(self, *a, **k):
        pass

    def note(self, *a, **k):
        pass

    def set(self, *a, **k):
        pass

    def anomaly(self, *a, **k) -> bool:
        return False


_NULL_LOOP = _NullLoop()


class Loop:
    """Per-frame loop recorder. Counts, min/max scores and the result are
    written as one LOOP line when the loop ends; ``frame()`` lines only with
    VERBOSE; ``note()`` lines (state changes) always."""
    active = True

    def __init__(self, name: str, tag: Optional[str], src: str, context: dict):
        self.name = name
        self.tag = tag
        self.src = src
        self.context = context
        self.frames = 0
        self.counts: Dict[str, int] = {}
        self.scores: Dict[str, List[float]] = {}   # key -> [min, max, sum, n]
        self.values: Dict[str, Any] = {}
        self.anomalies: Dict[str, int] = {}
        self.t0 = time.perf_counter()

    def __enter__(self):
        _tls.loop = getattr(_tls, "loop", 0) + 1
        stack = getattr(_tls, "loops", None)
        if stack is None:
            stack = _tls.loops = []
        stack.append(self)
        try:
            ctx = " ".join(f"{k}={summarize(v, 60)}" for k, v in self.context.items())
            _write("LOOP", f"start {self.name} {ctx}".rstrip(), self.tag, self.src)
        except Exception:
            _fail()
        return self

    def __exit__(self, et, ev, tb):
        _tls.loop = max(0, getattr(_tls, "loop", 1) - 1)
        stack = getattr(_tls, "loops", None)
        if stack and stack[-1] is self:
            stack.pop()
        try:
            dur = time.perf_counter() - self.t0
            parts = [f"frames={self.frames}", f"duration={dur:.3f}s"]
            if self.frames and dur > 0:
                parts.append(f"fps={self.frames / dur:.0f}")
            parts += [f"{k}={v}" for k, v in self.counts.items()]
            for k, (lo, hi, total, n) in self.scores.items():
                parts.append(f"{k}[min={lo:.3f} max={hi:.3f} avg={total / n:.3f} n={n}]")
            parts += [f"{k}={summarize(v, 80)}" for k, v in self.values.items()]
            if self.anomalies:
                parts.append("anomalies=" + summarize(self.anomalies))
            if et is not None:
                parts.append(f"raised={et.__name__}: {ev}")
            _write("LOOP", f"end {self.name} " + " ".join(parts), self.tag, self.src)
        except Exception:
            _fail()
        return False

    def frame(self, msg: Optional[str] = None, cat: str = "ANALYSIS") -> None:
        """One iteration; ``msg`` is written only with VERBOSE."""
        self.frames += 1
        if msg is not None and VERBOSE:
            try:
                _write(cat, msg, self.tag, _src(1), frame_level=True)
            except Exception:
                _fail()

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def score(self, key: str, value) -> None:
        try:
            v = float(value)
        except Exception:
            return
        s = self.scores.get(key)
        if s is None:
            self.scores[key] = [v, v, v, 1]
        else:
            if v < s[0]:
                s[0] = v
            if v > s[1]:
                s[1] = v
            s[2] += v
            s[3] += 1

    def set(self, key: str, value) -> None:
        self.values[key] = value

    def note(self, msg: str, cat: str = "STATE") -> None:
        """A state change inside the loop: always written."""
        try:
            _write(cat, msg, self.tag, _src(1))
        except Exception:
            _fail()

    def anomaly(self, kind: str, msg: str = "") -> bool:
        """Counts an anomaly; the first of each kind is written. Returns
        True for that first one (callers save an image then)."""
        n = self.anomalies.get(kind, 0) + 1
        self.anomalies[kind] = n
        if n == 1:
            self.note(f"{kind}: {msg} (first in this {self.name}; later ones are counted)", "CAPTURE")
            return True
        return False


def loop(name: str, *, tag: Optional[str] = None, **context):
    """``with diag.loop("minigame", tag="W1", region=...) as lp:`` —
    see Loop. A shared no-op object while diagnostics is off."""
    if not ENABLED:
        return _NULL_LOOP
    try:
        return Loop(name, tag, _src(1), context)
    except Exception:
        _fail()
        return _NULL_LOOP


def current_loop():
    """The innermost loop() of this thread, or the no-op loop: lets code that
    runs inside a per-frame loop count into its summary."""
    if not ENABLED:
        return _NULL_LOOP
    stack = getattr(_tls, "loops", None)
    return stack[-1] if stack else _NULL_LOOP


# ---------------------------------------------------------------- images

def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)[:60] or "image"


def image(label: str, frame, *, tag: Optional[str] = None, note: str = "", depth: int = 0) -> Optional[str]:
    """Saves ``frame`` (BGR/BGRA/gray array) as PNG in the session folder and
    logs its file name. Respects the per-session caps. Returns the file name
    or None (no session, cap reached, nothing to save)."""
    if not ENABLED:
        return None
    try:
        with _lock:
            s = _session
            if s is None or s.closed:
                return None
            tag = tag or thread_tag()
            key = f"{tag}:{label}"
            if frame is None or getattr(frame, "size", 0) == 0:
                _write("IMAGE", f"not saved ({label}): no frame {note}".rstrip(), tag, _src(depth + 1))
                return None
            if s.images >= MAX_IMAGES or s.image_bytes >= MAX_IMAGE_BYTES:
                if "images" not in s.cap_logged:
                    s.cap_logged.add("images")
                    _write("IMAGE", f"image cap reached ({s.images} images, {s.image_bytes >> 20} MB): "
                                    f"no more images in this session", tag, _src(depth + 1))
                return None
            if s.label_counts.get(key, 0) >= MAX_IMAGES_PER_LABEL:
                if key not in s.cap_logged:
                    s.cap_logged.add(key)
                    _write("IMAGE", f"per-label cap reached for '{label}' ({MAX_IMAGES_PER_LABEL}): "
                                    f"not saving more of these", tag, _src(depth + 1))
                return None
            s.images += 1
            n = s.images
            s.label_counts[key] = s.label_counts.get(key, 0) + 1
            name = f"{n:04d}_{_safe_name(tag)}_{_safe_name(label)}.png"
            path = os.path.join(s.folder, name)
            s.writing_images += 1
        try:
            import cv2  # local: only needed when an image is saved
            ok = bool(cv2.imwrite(path, frame))
            size = os.path.getsize(path) if ok and os.path.exists(path) else 0
        finally:
            with _lock:
                s.writing_images -= 1
        with _lock:
            s.image_bytes += size
        _write("IMAGE", f"saved {name} ({label}) {array_summary(frame)} {size} bytes"
                        f"{' — ' + note if note else ''}" if ok else f"FAILED to write {name}",
               tag, _src(depth + 1))
        return name if ok else None
    except Exception:
        _fail()
        return None


def first_pending(kind: str, tag: Optional[str] = None) -> bool:
    """Whether first(kind) would still save an image (lets callers skip an
    extra capture once the reference exists)."""
    if not ENABLED:
        return False
    s = _session
    return s is not None and ((tag or thread_tag()), kind) not in s.firsts


def first(kind: str, frame, *, tag: Optional[str] = None, note: str = "") -> Optional[str]:
    """Saves ``frame`` the first time ``kind`` happens for this tag in the
    session: a reference of what "working" looked like."""
    if not ENABLED:
        return None
    try:
        tag = tag or thread_tag()
        with _lock:
            s = _session
            if s is None or (tag, kind) in s.firsts:
                return None
            s.firsts.add((tag, kind))
        return image(f"first_{kind}", frame, tag=tag, note=note, depth=1)
    except Exception:
        _fail()
        return None


# ---------------------------------------------------------------- session files

def write_json(name: str, data: Any) -> Optional[str]:
    if not ENABLED:
        return None
    try:
        with _lock:
            s = _session
            if s is None:
                return None
            path = os.path.join(s.folder, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, default=_json_default)
        return path
    except Exception:
        _fail()
        return None


def _json_default(o):
    np = sys.modules.get("numpy")
    if np is not None and isinstance(o, np.ndarray):
        return o.tolist()
    if np is not None and isinstance(o, np.generic):
        return o.item()
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    if hasattr(o, "__dataclass_fields__"):
        return {k: getattr(o, k) for k in o.__dataclass_fields__}
    return repr(o)


def copy_file(src: str, name: Optional[str] = None) -> Optional[str]:
    if not ENABLED:
        return None
    try:
        with _lock:
            s = _session
            if s is None:
                return None
            dst = os.path.join(s.folder, name or os.path.basename(src))
        shutil.copy2(src, dst)
        return dst
    except Exception:
        _fail()
        return None


def set_effective(key: str, values: dict) -> None:
    """Records the values one bot actually used (after defaults, overrides
    and validation) in config_effective.json under ``key`` (e.g. "W1")."""
    if not ENABLED:
        return
    try:
        with _lock:
            s = _session
            if s is None:
                return
            s.effective[key] = values
            data = dict(s.effective)
        write_json("config_effective.json", data)
    except Exception:
        _fail()


# ---------------------------------------------------------------- system info

def system_info() -> List[str]:
    """Program, Python and OS lines for the session header."""
    lines = []
    try:
        from version import VERSION
    except Exception:
        VERSION = "?"
    frozen = bool(getattr(sys, "frozen", False))
    lines.append(f"bot version: {VERSION}")
    lines.append(f"frozen: {frozen}  executable: {sys.executable}")
    if frozen:
        lines.append(f"bundle (_MEIPASS): {getattr(sys, '_MEIPASS', None)}")
    lines.append(f"program folder: {program_dir()}  cwd: {os.getcwd()}")
    lines.append(f"argv: {sys.argv}")
    lines.append(f"python: {sys.version.split()[0]} ({platform.architecture()[0]}) {platform.python_implementation()}")
    lines.append(f"os: {platform.system()} {platform.release()}  version: {platform.version()}  "
                 f"platform: {platform.platform()}  machine: {platform.machine()}")
    if sys.platform == "win32":
        try:
            wv = sys.getwindowsversion()
            lines.append(f"windows build: {wv.major}.{wv.minor}.{wv.build} sp={wv.service_pack!r} "
                         f"product_type={getattr(wv, 'product_type', '?')}")
        except Exception:
            _fail()
        try:
            import ctypes
            ntdll = ctypes.WinDLL("ntdll")
            get = getattr(ntdll, "wine_get_version", None)
            if get is not None:
                get.restype = ctypes.c_char_p
                lines.append(f"running under Wine {get().decode()}")
        except Exception:
            pass
        lines += _win_dpi_lines()
    else:
        for var in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP", "QT_QPA_PLATFORM"):
            lines.append(f"env {var}={os.environ.get(var)!r}")
    for mod in ("numpy", "cv2", "PySide6", "mss", "pynput", "numba"):
        m = sys.modules.get(mod)
        if m is not None:
            lines.append(f"module {mod} {getattr(m, '__version__', '?')}")
    return lines


def _win_dpi_lines() -> List[str]:
    """Process DPI awareness and every monitor with rect and DPI (Windows)."""
    out = []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        try:
            shcore = ctypes.WinDLL("shcore")
        except Exception:
            shcore = None
        if shcore is not None:
            aw = ctypes.c_int(-1)
            if shcore.GetProcessDpiAwareness(None, ctypes.byref(aw)) == 0:
                names = {0: "unaware", 1: "system-aware", 2: "per-monitor-aware"}
                out.append(f"process DPI awareness: {aw.value} ({names.get(aw.value, '?')})")
        try:
            out.append(f"system DPI: {user32.GetDpiForSystem()}")
        except Exception:
            pass
        monitors = []

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32)]

        PROC = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def cb(hmon, _hdc, _rect, _lp):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            user32.GetMonitorInfoW(hmon, ctypes.byref(info))
            dx = ctypes.c_uint(0)
            dy = ctypes.c_uint(0)
            if shcore is not None:
                try:
                    shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
                except Exception:
                    pass
            r = info.rcMonitor
            w = info.rcWork
            monitors.append(
                f"monitor {len(monitors) + 1}: {info.szDevice} rect=({r.left},{r.top})-({r.right},{r.bottom}) "
                f"{r.right - r.left}x{r.bottom - r.top} work=({w.left},{w.top})-({w.right},{w.bottom}) "
                f"primary={bool(info.dwFlags & 1)} dpi={dx.value}x{dy.value} "
                f"scale={dx.value / 96 * 100:.0f}%")
            return 1

        user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
        out += monitors
        vx = [user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
        out.append(f"virtual desktop: origin=({vx[0]},{vx[1]}) size={vx[2]}x{vx[3]}")
    except Exception as e:
        out.append(f"monitor query failed: {e}")
    return out


def window_lines(tag: str, name: str, win) -> List[str]:
    """Title, handle, window/client rect, monitor and foreground state of a
    game window, from the OS layer."""
    lines = [f"{tag}: '{name}'"]
    try:
        import platforms
        native = platforms.native
        hwnd = platforms.window_handle(win)
        lines.append(f"  handle={hwnd!r} ({type(win).__name__}) title={getattr(win, 'title', None)!r}")
        if native is None or not hwnd:
            lines.append("  (no OS layer: rect unknown)")
            return lines
        rect = native.get_window_rect(hwnd)
        lines.append(f"  window rect (left, top, w, h) = {rect}")
        client = getattr(native, "get_client_rect", None)
        if client is not None:
            lines.append(f"  client rect (screen left, top, w, h) = {client(hwnd)}")
        else:
            lines.append("  client rect = window rect (this OS reports the client area)")
        try:
            lines.append(f"  minimized={native.is_minimized(hwnd)} foreground={native.get_foreground() == hwnd} "
                         f"is_window={native.is_window(hwnd)}")
        except Exception:
            _fail()
        mon = getattr(native, "monitor_of", None)
        if mon is not None:
            lines.append(f"  monitor: {mon(hwnd)}")
        if sys.platform != "win32":
            try:
                lines.append(f"  desktop copy possible (not XWayland): {native.screen_capture_reliable()}")
            except Exception:
                _fail()
    except Exception as e:
        lines.append(f"  window query failed: {e}")
    return lines


def config_lines(cfg: dict) -> List[str]:
    out = []
    for k in sorted(cfg, key=str):
        try:
            out.append(f"{k} = {json.dumps(cfg[k], default=_json_default)}")
        except Exception:
            out.append(f"{k} = {cfg[k]!r}")
    return out


# ---------------------------------------------------------------- lifecycle

def configure(enabled: bool, verbose: bool = False) -> None:
    """Switches recording on/off (Settings tab). Off also drops anything
    buffered while no session was open. An open session keeps running."""
    global ENABLED, VERBOSE, _setting
    enabled = bool(enabled)
    VERBOSE = bool(verbose) and enabled
    if enabled and not _setting:
        _pre_buffer.clear()
    _setting = enabled
    ENABLED = enabled or _session is not None
    _set_class_tracing(ENABLED)
    if not enabled:
        _pre_buffer.clear()
    install_hooks()


def session_active() -> bool:
    return _session is not None


def current_folder() -> Optional[str]:
    s = _session
    return s.folder if s is not None else None


def current_log_path() -> Optional[str]:
    s = _session
    return s.log_path if s is not None else None


def last_zip_path() -> Optional[str]:
    return _last_zip


def latest_zip() -> Optional[str]:
    """Newest finished session zip in any diagnostics folder."""
    best = None
    for d in session_dirs():
        try:
            for name in os.listdir(d):
                m = _STAMP_RE.match(name)
                if m and m.group(2):
                    if best is None or name > os.path.basename(best):
                        best = os.path.join(d, name)
        except Exception:
            continue
    return best


def start_session(header: Optional[Dict[str, List[str]]] = None,
                  config: Optional[dict] = None,
                  config_path: Optional[str] = None,
                  reason: str = "start") -> Optional[str]:
    """Opens a new session folder (closing any previous one), writes the
    header and the config files. Returns the folder, or None when
    diagnostics is off or nothing could be created."""
    global _session
    if not ENABLED:
        return None
    try:
        with _lock:
            old = _session
            _session = None
        if old is not None:
            _finish(old, "replaced by a new session")
            if not old.ending:
                old.ending = True
                _zip_in_background(old, ())
        base, fallback = location(create=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(base, stamp)
        n = 2
        while os.path.exists(folder) or os.path.exists(folder + ".zip"):
            folder = os.path.join(base, f"{stamp}_{n}")
            n += 1
        os.makedirs(folder)
        with _lock:
            _busy_folders.add(os.path.abspath(folder))
        s = _Session(folder, os.path.basename(folder))
        with _lock:
            _session = s
            buffered = list(_pre_buffer)
            _pre_buffer.clear()
        _write("SESSION", f"===== diagnostic session {s.stamp} ({reason}) =====", None, _src(1))
        _write("SESSION", f"log folder: {folder}" + ("  (program folder not writable: per-user fallback)"
                                                    if fallback else ""), None, _src(1))
        _write("SESSION", f"verbose (every frame): {VERBOSE}", None, _src(1))
        if buffered:
            _write("SESSION", f"--- {len(buffered)} events recorded before this session started ---", None, _src(1))
            for line in buffered:
                s.write(line, False)
            _write("SESSION", "--- end of earlier events ---", None, _src(1))
        block("SESSION", "system:", system_info())
        for title, lines in (header or {}).items():
            block("SESSION", title, lines)
        if config is not None:
            block("CONFIG", f"effective configuration ({len(config)} keys):", config_lines(config))
            write_json("config_gui.json", config)
        if config_path:
            if os.path.exists(config_path):
                copy_file(config_path, "bot_config.json")
                log("CONFIG", f"config file {config_path} copied as bot_config.json "
                              f"({os.path.getsize(config_path)} bytes)")
            else:
                log("CONFIG", f"config file {config_path} does not exist on disk")
        return folder
    except Exception:
        _fail()
        return None


def end_session(reason: str = "", wait_threads: Iterable[threading.Thread] = (),
                on_done: Optional[Callable[[Optional[str], Optional[str]], None]] = None,
                wait: bool = False) -> Optional[threading.Thread]:
    """Ends the session: waits (in a background thread) for ``wait_threads``
    to finish their last lines, writes the footer, closes the log and packs
    the folder into <stamp>.zip next to it. ``on_done(zip_path, error)`` runs
    on that thread. ``wait`` blocks until done (program exit, tests)."""
    with _lock:
        s = _session
        if s is None or s.ending:
            return None      # already being ended by an earlier call
        s.ending = True
    try:
        log("SESSION", f"session ending: {reason}")
    except Exception:
        _fail()
    t = _zip_in_background(s, tuple(wait_threads), on_done, reason)
    if wait and t is not None:
        t.join(timeout=60)
    return t


def _zip_in_background(s: _Session, wait_threads, on_done=None, reason: str = "") -> Optional[threading.Thread]:
    def work():
        deadline = time.monotonic() + 5.0
        for th in wait_threads:
            try:
                if th is not threading.current_thread():
                    th.join(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                pass
        _finish(s, reason)
        try:
            zip_path, err = _pack(s.folder)
        finally:
            with _lock:
                _busy_folders.discard(os.path.abspath(s.folder))
        try:
            prune()
        except Exception:
            _fail()
        if on_done is not None:
            try:
                on_done(zip_path, err)
            except Exception:
                _fail()

    t = threading.Thread(target=work, name="diag-zip", daemon=False)
    _zip_threads.append(t)
    t.start()
    return t


def _finish(s: _Session, reason: str) -> None:
    """Footer + close; afterwards the session no longer receives lines."""
    global _session, ENABLED
    deadline = time.monotonic() + 5.0
    while s.writing_images > 0 and time.monotonic() < deadline:
        time.sleep(0.01)          # an image still being written goes into the zip whole
    with _lock:
        if not s.closed:
            dur = time.time() - s.started
            try:
                s.write(f"{_now()} [{thread_tag()}] {'SESSION':<8} diag.py _finish | "
                        f"===== session end ({reason or 'closed'}) after {dur:.1f}s: "
                        f"{s.images} images ({s.image_bytes >> 10} KB), log {s.bytes >> 10} KB, "
                        f"{s.dropped} lines dropped by the size cap, "
                        f"{_failures} logging failures =====", False)
            except Exception:
                _fail()
        if _session is s:
            _session = None
        s.close()
        if _session is None:
            ENABLED = _setting
            _set_class_tracing(ENABLED)
    if not _setting:
        _pre_buffer.clear()


def setting() -> bool:
    """The user's choice (ENABLED may still be on while a session closes)."""
    return _setting


def _pack(folder: str) -> Tuple[Optional[str], Optional[str]]:
    """Zips ``folder`` to ``folder``.zip; removes the folder on success.
    Returns (zip_path, None) or (None, error) — the folder stays then."""
    global _last_zip
    zip_path = folder.rstrip("/\\") + ".zip"
    tmp = f"{zip_path}.{os.getpid()}.{threading.get_ident()}.part"
    try:
        stamp = os.path.basename(folder.rstrip("/\\"))
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(folder):
                for name in sorted(files):
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, folder)
                    zf.write(full, arcname=f"{stamp}/{rel}".replace(os.sep, "/"))
        os.replace(tmp, zip_path)
        shutil.rmtree(folder, ignore_errors=True)
        _last_zip = zip_path
        return zip_path, None
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        _fail()
        return None, f"{type(e).__name__}: {e}"


def prune(keep: int = KEEP_SESSIONS) -> List[str]:
    """Deletes all but the newest ``keep`` sessions (zips and unzipped
    folders) in every diagnostics folder. The open session is never touched."""
    removed = []
    current = current_folder()
    for d in session_dirs():
        try:
            names = os.listdir(d)
        except Exception:
            continue
        stamps: Dict[str, List[str]] = {}
        for name in names:
            m = _STAMP_RE.match(name)
            if m:
                stamps.setdefault(m.group(1), []).append(name)
        for stamp in sorted(stamps, reverse=True)[keep:]:
            for name in stamps[stamp]:
                path = os.path.join(d, name)
                if current and os.path.abspath(path) == os.path.abspath(current):
                    continue
                if os.path.abspath(path) in _busy_folders:
                    continue
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                    removed.append(path)
                except Exception:
                    _fail()
    return removed


def recover_leftovers(on_done: Optional[Callable[[List[str]], None]] = None,
                      wait: bool = False) -> Optional[threading.Thread]:
    """Zips session folders a crash or kill left behind (not the open one),
    then applies the retention. Runs in a background thread."""
    def work():
        done = []
        current = current_folder()
        for d in session_dirs():
            try:
                names = sorted(os.listdir(d))
            except Exception:
                continue
            for name in names:
                path = os.path.join(d, name)
                m = _STAMP_RE.match(name)
                if not m or m.group(2) or not os.path.isdir(path):
                    continue
                if current and os.path.abspath(path) == os.path.abspath(current):
                    continue
                with _lock:
                    if os.path.abspath(path) in _busy_folders:
                        continue          # this process is still writing / packing it
                if os.path.exists(path + ".zip"):
                    continue
                try:
                    with open(os.path.join(path, "RECOVERED.txt"), "w", encoding="utf-8") as f:
                        f.write("This session did not end normally (the program crashed or was "
                                "killed). Packed at the next start, "
                                f"{_dt.datetime.now().isoformat(timespec='seconds')}.\n")
                except Exception:
                    _fail()
                zp, _err = _pack(path)
                if zp:
                    done.append(zp)
        try:
            prune()
        except Exception:
            _fail()
        if on_done is not None:
            try:
                on_done(done)
            except Exception:
                _fail()

    try:
        t = threading.Thread(target=work, name="diag-recover", daemon=False)
        t.start()
        if wait:
            t.join(timeout=60)
        return t
    except Exception:
        _fail()
        return None


def wait_for_background(timeout: float = 30.0) -> None:
    """Joins pending zip threads (tests, program exit)."""
    deadline = time.monotonic() + timeout
    for t in list(_zip_threads):
        t.join(timeout=max(0.0, deadline - time.monotonic()))
    _zip_threads[:] = [t for t in _zip_threads if t.is_alive()]


# ---------------------------------------------------------------- crash hooks

def install_hooks() -> None:
    """sys.excepthook and threading.excepthook: uncaught exceptions are
    logged with their traceback, then handed to the previous hook."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    prev_sys = sys.excepthook

    def sys_hook(et, ev, tb):
        if ENABLED:
            try:
                text = "".join(traceback.format_exception(et, ev, tb)).rstrip()
                _write("EXC", f"UNCAUGHT exception in main thread: {et.__name__}: {ev}\n{text}", None, "sys.excepthook")
            except Exception:
                _fail()
        try:
            prev_sys(et, ev, tb)
        except Exception:
            pass

    sys.excepthook = sys_hook
    prev_thr = getattr(threading, "excepthook", None)
    if prev_thr is not None:
        def thread_hook(args):
            if ENABLED:
                try:
                    text = "".join(traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback)).rstrip()
                    name = args.thread.name if args.thread is not None else "?"
                    _write("EXC", f"UNCAUGHT exception in thread {name}: "
                                  f"{args.exc_type.__name__}: {args.exc_value}\n{text}", None, "threading.excepthook")
                except Exception:
                    _fail()
            try:
                prev_thr(args)
            except Exception:
                pass

        threading.excepthook = thread_hook


def reset_for_tests() -> None:
    """Closes everything and restores the initial state (tests only)."""
    global ENABLED, VERBOSE, _session, _last_zip, _last_dir, _last_dir_fallback, _failures, _setting
    s = _session
    if s is not None:
        s.close()
    _session = None
    wait_for_background()
    ENABLED = VERBOSE = _setting = False
    _set_class_tracing(False)
    _pre_buffer.clear()
    _busy_folders.clear()
    _last_zip = _last_dir = None
    _last_dir_fallback = False
    _failures = 0

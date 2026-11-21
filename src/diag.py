"""
Diagnostic log: one file that reconstructs a bot session.

End users run the packaged exe and cannot attach a debugger, so when a
problem cannot be reproduced they switch "Diagnostic log" on (Settings tab),
reproduce it and send the zip this module leaves next to the program:

    <exe folder>/diagnostics/<YYYYMMDD_HHMMSS>.zip
        <YYYYMMDD_HHMMSS>/summary.txt          read first: per-window totals, loop
                                               statistics, index of images /
                                               exceptions / stops with log line numbers
        <YYYYMMDD_HHMMSS>/session.log          everything, in order
        <YYYYMMDD_HHMMSS>/frames_<loop>.csv    one row per frame of each per-frame loop
        <YYYYMMDD_HHMMSS>/bot_config.json      the config file as it was on disk
        <YYYYMMDD_HHMMSS>/config_effective.json what each bot actually used
        <YYYYMMDD_HHMMSS>/NNNN_W1_<label>.png  frames that were analysed

Line format (one event per line, written and flushed immediately):

    14:03:22.123 [W1] ANALYSIS fishing_bot.py:512 _scan_empty_slots | ...

Categories: SESSION, CONFIG, GUI, ACTION (input sent), FOCUS, CAPTURE,
ANALYSIS (what a detector measured), DECISION (what the bot does next),
STATE, STAT (status/stats callbacks the GUI gets), CALL (function entry /
exit, from ``traced``), LOOP (per-wait / per-game summaries), IMAGE, EXC,
DETAIL (markers around recent per-frame detail), DIAG (the logger itself).

Cost when switched off: callers test the module flag ``diag.ENABLED`` before
building a message, an image or a summary; traced functions add one global
lookup. A failure inside this module never reaches the bot: it is swallowed
and counted (reported in the session footer).

Per-frame loops (minigame, waits) wrap their body in ``diag.loop(...)``.
Inside one, session.log only gets state changes, actions and decisions, and
the loop writes one LOOP line when it ends (a start line too, but only once
something inside it was written). What each frame measured goes to
frames_<loop>.csv (``Loop.frame(**fields)``). The per-frame detail lines
(``trace()``, function calls, frame rows) are kept in a per-thread ring of
the last RECENT_SECONDS and written out, marked "»", only when something
goes wrong: before an image is saved, an exception is logged or a bot stops
(``dump_recent``). Outside loops detail is written as it happens.
"""

from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import platform
import random
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

LOG_NAME = "session.log"
DIR_NAME = "diagnostics"
APP_DIR_NAME = "FishingPuzzlePlayer"
KEEP_SESSIONS = 10

MAX_IMAGES = 400                       # per session
MAX_IMAGE_BYTES = 300 * 1024 * 1024    # per session
MAX_IMAGES_PER_LABEL = 25              # one label repeating cannot eat the budget
MAX_LOG_BYTES = 400 * 1024 * 1024      # after this only non-frame lines; at 2x nothing
MAX_FRAME_BYTES = 150 * 1024 * 1024    # frames_*.csv together; rows past it are counted only
RECENT_SECONDS = 3.0                   # per-frame detail kept for dump_recent()
RECENT_MAX_LINES = 600                 # per thread
MAX_RECENT_DUMPS = 300                 # per session
MAX_EVENTS_PER_KIND = 60               # summary.txt index; later ones are counted
MAX_SAMPLES = 20000                    # per statistic (reservoir) for percentiles
SUMMARY_NAME = "summary.txt"
_FSYNC_EVERY = 2.0                     # seconds between fsyncs of the log
_CSV_FLUSH_EVERY = 1.0                 # seconds between flushes of the frame tables

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
    the project root when run from source. A disguised copy (disguise.py)
    runs from a temporary folder and is told the real one in FISHBOT_HOME."""
    if getattr(sys, "frozen", False):
        home = os.environ.get("FISHBOT_HOME")
        if home and os.path.isdir(home):
            return home
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
    """Whether detail goes straight into session.log right now (outside a
    per-frame loop; inside one it goes to the recent-detail ring)."""
    return ENABLED and getattr(_tls, "loop", 0) == 0


# ---------------------------------------------------------------- writing

class _Session:
    def __init__(self, folder: str, stamp: str):
        self.folder = folder
        self.stamp = stamp
        self.log_path = os.path.join(folder, LOG_NAME)
        self.file = open(self.log_path, "a", encoding="utf-8", newline="\n")
        self.bytes = 0
        self.lines = 0                 # lines in session.log (summary.txt points at them)
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
        # summary.txt
        self.events: List[Tuple[int, float, str, str, str]] = []   # (line, time, tag, kind, text)
        self.event_counts: Dict[str, int] = {}
        self.tallies: Dict[str, Dict[str, int]] = {}                 # tag -> key -> n
        self.samples: Dict[Tuple[str, str], List[Any]] = {}          # (tag, key) -> [values, seen]
        self.loop_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (tag, loop name) -> stats
        self.loop_seq = 0
        self.dumps = 0
        # frames_<loop>.csv
        self.tables: Dict[str, Tuple[Any, List[str]]] = {}           # loop name -> (file, columns)
        self.frame_bytes = 0
        self.frame_rows = 0
        self.frame_rows_dropped = 0
        self.last_table_flush = time.monotonic()

    def write(self, line: str, frame_level: bool) -> int:
        """Writes one line; returns its line number (0 when it was dropped)."""
        if self.closed:
            _pre_buffer.append(line)
            return 0
        if self.bytes > MAX_LOG_BYTES and (frame_level or self.bytes > 2 * MAX_LOG_BYTES):
            self.dropped += 1
            if "logcap" not in self.cap_logged:
                self.cap_logged.add("logcap")
                note = (f"{_now()} [DIAG] DIAG     diag.py | log size cap ({MAX_LOG_BYTES >> 20} MB) "
                        f"reached: per-frame lines are dropped from here on\n")
                self.file.write(note)
                self.lines += 1
            return 0
        self.file.write(line + "\n")
        self.file.flush()
        self.bytes += len(line) + 1
        number = self.lines + 1
        self.lines += 1 + line.count("\n")
        now = time.monotonic()
        if now - self.last_fsync >= _FSYNC_EVERY:
            self.last_fsync = now
            try:
                os.fsync(self.file.fileno())
            except Exception:
                pass
        return number

    def add_event(self, number: int, t: float, tag: str, kind: str, text: str) -> None:
        n = self.event_counts.get(kind, 0) + 1
        self.event_counts[kind] = n
        if n <= MAX_EVENTS_PER_KIND:
            first = text.split("\n", 1)[0]
            self.events.append((number, t, tag, kind, first if len(first) <= 200 else first[:200] + "…"))

    def table(self, name: str, columns: List[str]):
        """(file, columns) of frames_<name>.csv, created with its header."""
        entry = self.tables.get(name)
        if entry is None:
            path = os.path.join(self.folder, f"frames_{_safe_name(name.replace(' ', '_'))}.csv")
            f = open(path, "a", encoding="utf-8", newline="\n")
            header = ",".join(_FRAME_HEAD + list(columns) + ["extra", "note"])
            f.write(header + "\n")
            self.frame_bytes += len(header) + 1
            entry = self.tables[name] = (f, list(columns))
        return entry

    def flush_tables(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_table_flush < _CSV_FLUSH_EVERY:
            return
        self.last_table_flush = now
        for f, _cols in self.tables.values():
            try:
                f.flush()
            except Exception:
                pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for f, _cols in self.tables.values():
            try:
                f.close()
            except Exception:
                pass
        try:
            self.file.flush()
            os.fsync(self.file.fileno())
        except Exception:
            pass
        try:
            self.file.close()
        except Exception:
            pass


_FRAME_HEAD = ["time", "t", "tag", "loop", "frame", "dt_ms"]


def _clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"


def _now() -> str:
    return _clock(time.time())


def _fmt(t: float, tag: str, cat: str, src: str, msg: str) -> str:
    line = f"{_clock(t)} [{tag}] {cat:<8} {src} | {msg}"
    return line.replace("\n", "\n    ") if "\n" in line else line


def _write(cat: str, msg: str, tag: Optional[str], src: str, frame_level: bool = False,
           event: Optional[str] = None, t: Optional[float] = None, indent: Optional[str] = None) -> int:
    """Writes one line to the session (or the pre-session buffer). Returns
    its line number in session.log (0: buffered or dropped). ``event``
    also lists it in summary.txt under that kind."""
    if getattr(_tls, "pending", 0):
        _flush_pending_loops()
    t = time.time() if t is None else t
    tag = tag or thread_tag()
    line = _fmt(t, tag, cat, src, (_indent() if indent is None else indent) + msg)
    with _lock:
        s = _session
        if s is None:
            _pre_buffer.append(line)
            return 0
        number = s.write(line, frame_level)
        if event and number:
            s.add_event(number, t, tag, event, msg)
        return number


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
    """Per-frame detail: written outside loops; inside them kept in the
    recent-detail ring (see dump_recent)."""
    if not ENABLED:
        return
    try:
        if getattr(_tls, "loop", 0):
            _ring_add(cat, msg, tag, _src(depth + 1))
        else:
            _write(cat, msg, tag, _src(depth + 1), frame_level=True)
    except Exception:
        _fail()


def event(cat: str, msg: str, *, kind: str, tag: Optional[str] = None, recent: bool = False,
          depth: int = 0) -> None:
    """A line that is also listed in summary.txt (stops, failures). With
    ``recent`` the per-frame detail of the last seconds is written first."""
    if not ENABLED:
        return
    try:
        if recent:
            dump_recent(kind, tag=tag, depth=depth + 1)
        _write(cat, msg, tag, _src(depth + 1), event=kind)
    except Exception:
        _fail()


def exc(msg: str = "exception caught", *, tag: Optional[str] = None, depth: int = 0) -> None:
    """Call inside an ``except`` block: logs the exception being handled,
    with its full traceback, at the place it was caught (after the recent
    per-frame detail that led to it)."""
    if not ENABLED:
        return
    try:
        et, ev, tb = sys.exc_info()
        text = "".join(traceback.format_exception(et, ev, tb)).rstrip() if et else "(no active exception)"
        dump_recent(f"exception: {msg}", tag=tag, depth=depth + 1)
        _write("EXC", f"{msg}: {et.__name__ if et else ''}: {ev}\n{text}", tag, _src(depth + 1),
               event="exception")
    except Exception:
        _fail()


def tally(key: str, n: int = 1, *, tag: Optional[str] = None) -> None:
    """Counts ``key`` for this window in summary.txt (games, catches, ...)."""
    if not ENABLED:
        return
    try:
        with _lock:
            s = _session
            if s is None:
                return
            d = s.tallies.setdefault(tag or thread_tag(), {})
            d[key] = d.get(key, 0) + n
    except Exception:
        _fail()


def sample(key: str, value: float, *, tag: Optional[str] = None) -> None:
    """One measurement for the percentiles in summary.txt (e.g. capture ms)."""
    if not ENABLED:
        return
    try:
        with _lock:
            s = _session
            if s is None:
                return
            _reservoir(s.samples.setdefault((tag or thread_tag(), key), [[], 0]), float(value))
    except Exception:
        _fail()


def _reservoir(slot: List[Any], value: float) -> None:
    values = slot[0]
    slot[1] += 1
    if len(values) < MAX_SAMPLES:
        values.append(value)
    else:
        i = random.randrange(slot[1])
        if i < MAX_SAMPLES:
            values[i] = value


# ---------------------------------------------------------------- recent detail

def _ring_add(cat: str, msg: Any, tag: Optional[str], src: str, t: Optional[float] = None) -> None:
    """Keeps one detail line (or a frame row, formatted when written) for
    dump_recent()."""
    ring = getattr(_tls, "ring", None)
    if ring is None:
        ring = _tls.ring = deque(maxlen=RECENT_MAX_LINES)
    ring.append((time.time() if t is None else t, tag or thread_tag(), cat, src, _indent(), msg))


def dump_recent(reason: str, *, tag: Optional[str] = None, depth: int = 0) -> int:
    """Writes this thread's per-frame detail of the last RECENT_SECONDS
    (lines marked "»" between two DETAIL lines) and empties the ring.
    Called before an image is saved, an exception is logged or a bot
    stops. Returns the number of lines written."""
    if not ENABLED:
        return 0
    try:
        ring = getattr(_tls, "ring", None)
        if not ring:
            return 0
        now = time.time()
        items = [e for e in ring if now - e[0] <= RECENT_SECONDS]
        ring.clear()
        if not items:
            return 0
        with _lock:
            s = _session
            if s is None or s.closed or s.dumps >= MAX_RECENT_DUMPS:
                return 0
            s.dumps += 1
            src = _src(depth + 1)
            span = now - items[0][0]
            _write("DETAIL", f"▼ recent per-frame detail ({len(items)} lines, last {span:.1f}s) before: {reason}",
                   tag, src, frame_level=True)
            for t, tg, cat, sr, ind, msg in items:
                text = msg.text() if isinstance(msg, _Row) else msg
                s.write(_fmt(t, tg, cat, sr, "» " + ind + text), True)
            _write("DETAIL", "▲ end of recent detail", tag, src, frame_level=True)
        return len(items)
    except Exception:
        _fail()
        return 0

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

def summarize(value: Any, limit: int = 160, brief: bool = False) -> str:
    """Short, loggable description of an argument or result. ``brief``:
    arrays as shape and dtype only (function arguments and results: the
    capture line already gave the statistics of that frame)."""
    try:
        text = _summ(value, limit, 0, brief)
        cap = max(300, limit * 3)
        return text if len(text) <= cap else text[:cap] + f"… ({len(text)} chars)"
    except Exception:
        _fail()
        return f"<{type(value).__name__}>"


def _summ(v: Any, limit: int, level: int, brief: bool = False) -> str:
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
        return f"array{tuple(v.shape)} {v.dtype}" if brief else array_summary(v)
    if np is not None and isinstance(v, np.generic):
        return repr(v.item())
    if isinstance(v, (list, tuple, set, frozenset, deque)):
        items = list(v)
        open_, close = ("[", "]") if isinstance(v, (list, deque)) else ("(", ")") if isinstance(v, tuple) else ("{", "}")
        if level >= 2:
            return f"{type(v).__name__}(len={len(items)})"
        shown = items[:8]
        inner = ", ".join(_summ(x, 40, level + 1, brief) for x in shown)
        more = f", … +{len(items) - 8}" if len(items) > 8 else ""
        if isinstance(v, tuple) and len(items) == 1:
            inner += ","
        return f"{open_}{inner}{more}{close}"
    if isinstance(v, dict):
        if level >= 2:
            return f"dict(len={len(v)})"
        items = list(v.items())[:8]
        inner = ", ".join(f"{_summ(k, 30, level + 1, brief)}: {_summ(x, 40, level + 1, brief)}" for k, x in items)
        more = f", … +{len(v) - 8}" if len(v) > 8 else ""
        return "{" + inner + more + "}"
    if hasattr(v, "__dataclass_fields__"):
        if level >= 2:
            return type(v).__name__
        parts = []
        for name in list(v.__dataclass_fields__)[:10]:
            parts.append(f"{name}={_summ(getattr(v, name, None), 40, level + 1, brief)}")
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
    """Entry and exit lines of a traced call: into session.log outside
    per-frame loops, into the recent-detail ring inside them."""
    entry = None
    try:
        tag = _tag_of(a[0]) if (method and a) else None
        if tag is None and isinstance(k.get("bot_id"), int):
            tag = f"W{k['bot_id'] + 1}"
        shown = a[1:] if method else a
        args = ", ".join([summarize(x, 60, brief=True) for x in shown] +
                         [f"{key}={summarize(x, 60, brief=True)}" for key, x in k.items()])
        src = _src(2)
        ring = getattr(_tls, "loop", 0) > 0
        if ring:
            _ring_add("CALL", f"→ {qual}({args})", tag, src)
        else:
            _write("CALL", f"→ {qual}({args})", tag, src)
        _tls.depth = getattr(_tls, "depth", 0) + 1
        entry = (tag, src, ring)
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
            text = f"✗ {qual} raised {type(e).__name__}: {e} [{ms:.1f} ms]"
            if entry[2]:
                _ring_add("CALL", text, entry[0], entry[1])
            else:
                _write("CALL", text, entry[0], entry[1])
        except Exception:
            _fail()
        raise
    try:
        _tls.depth = max(0, getattr(_tls, "depth", 1) - 1)
        ms = (time.perf_counter() - t0) * 1000
        text = f"← {qual} = {summarize(result, 120, brief=True)} [{ms:.1f} ms]"
        if entry[2]:
            _ring_add("CALL", text, entry[0], entry[1])
        else:
            _write("CALL", text, entry[0], entry[1])
    except Exception:
        _fail()
    return result


def traced(fn=None, *, method: Optional[bool] = None):
    """Decorator: logs entry (summarised arguments) and exit (summarised
    result, duration) of the function while diagnostics is on. Inside a
    per-frame loop into the recent-detail ring (see dump_recent)."""
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

    def add(self, *a, **k):
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


class _Row:
    """One frame of a loop: a row of frames_<loop>.csv and, in the
    recent-detail ring, one line (formatted when it is written out)."""
    __slots__ = ("loop", "n", "t", "dt", "fields", "note")

    def __init__(self, loop: "Loop", n: int, t: float, dt: Optional[float], fields: dict, note: Optional[str]):
        self.loop, self.n, self.t, self.dt, self.fields, self.note = loop, n, t, dt, fields, note

    def text(self) -> str:
        parts = " ".join(f"{k}={_cell(v)}" for k, v in self.fields.items() if v is not None)
        dt = "" if self.dt is None else f" +{self.dt:.0f}ms"
        note = f" {self.note}" if self.note else ""
        return f"{self.loop.name} #{self.loop.seq} frame {self.n}{dt}:{note} {parts}".rstrip()


def _cell(v: Any) -> str:
    """A value as frames_*.csv and frame lines show it."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return repr(round(v, 3))
    if isinstance(v, (int, str)):
        return str(v)
    if isinstance(v, (tuple, list)):
        return " ".join(_cell(x) for x in v)
    np = sys.modules.get("numpy")
    if np is not None and isinstance(v, np.generic):
        return _cell(v.item())
    return summarize(v, 60, brief=True)


def _csv(text: str) -> str:
    if any(c in text for c in ',"\n'):
        return '"' + text.replace('"', '""').replace("\n", " ") + '"'
    return text


class Loop:
    """Per-frame loop recorder. Counts, min/max scores and the result are
    written as one LOOP line when the loop ends; the start line only once
    something inside the loop is written (short quiet loops stay one line).
    ``frame(**fields)`` adds a row to frames_<name>.csv; ``note()`` lines
    (state changes) are always written."""
    active = True

    def __init__(self, name: str, tag: Optional[str], src: str, context: dict,
                 columns: Optional[Iterable[str]] = None):
        self.name = name
        self.tag = tag
        self.src = src
        self.context = context
        self.columns = list(columns) if columns is not None else None
        self.frames = 0
        self.counts: Dict[str, int] = {}
        self.scores: Dict[str, List[float]] = {}   # key -> [min, max, sum, n]
        self.values: Dict[str, Any] = {}
        self.anomalies: Dict[str, int] = {}
        self.t0 = time.perf_counter()
        self.t_wall = time.time()
        self.seq = 0
        self.started = False       # start line written
        self.depth = ""
        self._row: Optional[_Row] = None
        self._last_frame: Optional[float] = None
        self._dts: List[float] = []

    def _ctx(self) -> str:
        return " ".join(f"{k}={summarize(v, 60)}" for k, v in self.context.items())

    def __enter__(self):
        _tls.loop = getattr(_tls, "loop", 0) + 1
        stack = getattr(_tls, "loops", None)
        if stack is None:
            stack = _tls.loops = []
        stack.append(self)
        self.depth = _indent()
        try:
            with _lock:
                s = _session
                if s is not None:
                    s.loop_seq += 1
                    self.seq = s.loop_seq
        except Exception:
            _fail()
        _tls.pending = getattr(_tls, "pending", 0) + 1
        return self

    def _write_start(self) -> None:
        """The deferred start line, with the time the loop really started."""
        self.started = True
        _write("LOOP", f"start {self.name} #{self.seq} {self._ctx()}".rstrip(), self.tag, self.src,
               t=self.t_wall, indent=self.depth)

    def __exit__(self, et, ev, tb):
        _tls.loop = max(0, getattr(_tls, "loop", 1) - 1)
        stack = getattr(_tls, "loops", None)
        if stack and stack[-1] is self:
            stack.pop()
        if not self.started:
            _tls.pending = max(0, getattr(_tls, "pending", 1) - 1)
        try:
            self._complete_row()
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
            head = f"end {self.name} #{self.seq}"
            if not self.started:
                ctx = self._ctx()
                head += f" ({ctx})" if ctx else ""
            _write("LOOP", head + " " + " ".join(parts), self.tag, self.src, indent=self.depth)
            self._record(dur, et)
        except Exception:
            _fail()
        return False

    def _record(self, dur: float, et) -> None:
        """Adds this loop to the per-window loop statistics of summary.txt."""
        with _lock:
            s = _session
            if s is None:
                return
            key = (self.tag or thread_tag(), self.name)
            st = s.loop_stats.get(key)
            if st is None:
                st = s.loop_stats[key] = {
                    "n": 0, "frames": 0, "dur": 0.0, "max_dur": 0.0, "results": {}, "counts": {},
                    "scores": {}, "anomalies": {}, "dts": [[], 0]}
            st["n"] += 1
            st["frames"] += self.frames
            st["dur"] += dur
            st["max_dur"] = max(st["max_dur"], dur)
            result = self.values.get("result")
            if et is not None:
                rkey = "raised " + et.__name__
            elif result is None:
                rkey = "(no result)"
            else:
                rkey = re.sub(r"-?\d+(\.\d+)?", "#", str(result))[:70]
            st["results"][rkey] = st["results"].get(rkey, 0) + 1
            for k, v in self.counts.items():
                st["counts"][k] = st["counts"].get(k, 0) + v
            for k, (lo, hi, total, n) in self.scores.items():
                agg = st["scores"].get(k)
                if agg is None:
                    st["scores"][k] = [lo, hi, total, n]
                else:
                    agg[0], agg[1] = min(agg[0], lo), max(agg[1], hi)
                    agg[2] += total
                    agg[3] += n
            for k, v in self.anomalies.items():
                st["anomalies"][k] = st["anomalies"].get(k, 0) + v
            for dt in self._dts:
                _reservoir(st["dts"], dt)

    def frame(self, msg: Optional[str] = None, cat: str = "ANALYSIS", **fields) -> None:
        """One iteration: a row of frames_<name>.csv with ``fields`` (more
        can be added with ``add``) and a line in the recent-detail ring.
        ``msg``: optional short note for both."""
        try:
            self._complete_row()
            self.frames += 1
            now = time.perf_counter()
            dt = None if self._last_frame is None else (now - self._last_frame) * 1000
            self._last_frame = now
            if dt is not None and len(self._dts) < 5000:
                self._dts.append(dt)
            row = self._row = _Row(self, self.frames, time.time(), dt, fields, msg)
            _ring_add(cat, row, self.tag, _src(1), t=row.t)
        except Exception:
            _fail()

    def add(self, **fields) -> None:
        """More values for the current frame's row."""
        if self._row is not None:
            self._row.fields.update(fields)

    def _complete_row(self) -> None:
        row, self._row = self._row, None
        if row is None:
            return
        try:
            with _lock:
                s = _session
                if s is None or s.closed:
                    return
                if s.frame_bytes > MAX_FRAME_BYTES:
                    s.frame_rows_dropped += 1
                    return
                f, columns = s.table(self.name, self.columns if self.columns is not None else list(row.fields))
                extra = " ".join(f"{k}={_cell(v)}" for k, v in row.fields.items()
                                 if k not in columns and v is not None)
                cells = [_clock(row.t), f"{row.t - s.started:.3f}", self.tag or thread_tag(), str(self.seq),
                         str(row.n), "" if row.dt is None else f"{row.dt:.1f}"]
                cells += [_cell(row.fields.get(c)) for c in columns]
                cells += [extra, row.note or ""]
                line = ",".join(_csv(c) for c in cells) + "\n"
                f.write(line)
                s.frame_bytes += len(line)
                s.frame_rows += 1
                s.flush_tables()
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
        """Counts an anomaly; the first of each kind is written (after the
        recent per-frame detail). Returns True for that first one (callers
        save an image then)."""
        n = self.anomalies.get(kind, 0) + 1
        self.anomalies[kind] = n
        if n == 1:
            dump_recent(f"anomaly {kind}", tag=self.tag, depth=1)
            try:
                _write("CAPTURE", f"{kind}: {msg} (first in this {self.name}; later ones are counted)",
                       self.tag, _src(1), event="anomaly")
            except Exception:
                _fail()
            return True
        return False


def _flush_pending_loops() -> None:
    """Writes the deferred start lines of this thread's open loops (outer
    first) before anything else is written inside them."""
    _tls.pending = 0
    for lp in list(getattr(_tls, "loops", None) or ()):
        if not lp.started:
            lp._write_start()


def loop(name: str, *, tag: Optional[str] = None, columns: Optional[Iterable[str]] = None, **context):
    """``with diag.loop("minigame", tag="W1", columns=("panel", "fish_x"), region=...) as lp:``
    (see Loop). ``columns``: the frame fields in the column order of
    frames_<name>.csv (default: the fields of the first frame; others go
    to its "extra" column). A shared no-op object while diagnostics is off."""
    if not ENABLED:
        return _NULL_LOOP
    try:
        return Loop(name, tag, _src(1), context, columns)
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
    or None (no session, cap reached, nothing to save). Except for the
    "first_" reference images, the recent per-frame detail is written
    first: an image is saved when something went wrong."""
    if not ENABLED:
        return None
    try:
        if not label.startswith("first_"):
            dump_recent(f"image {label}", tag=tag, depth=depth + 1)
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
                                    f"no more images in this session", tag, _src(depth + 1), event="logging")
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
               tag, _src(depth + 1), event="image")
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

def configure(enabled: bool) -> None:
    """Switches recording on/off (Settings tab). Off also drops anything
    buffered while no session was open. An open session keeps running."""
    global ENABLED, _setting
    enabled = bool(enabled)
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
        _write("SESSION", f"files: {SUMMARY_NAME} (totals and an index of this log, read first), "
                          f"frames_<loop>.csv (one row per frame of each per-frame loop), PNG images. "
                          f"Lines marked » are the last {RECENT_SECONDS:.0f}s of per-frame detail, "
                          f"written before an image, an exception or a stop", None, _src(1))
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
            try:
                s.flush_tables(force=True)
                with open(os.path.join(s.folder, SUMMARY_NAME), "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(summary_lines(s, reason, dur)) + "\n")
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


def _pct(values: List[float], q: float) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]


def _dist(values: List[float], unit: str) -> str:
    if not values:
        return "n=0"
    return (f"p50 {_pct(values, 0.5):.1f}{unit} p95 {_pct(values, 0.95):.1f}{unit} "
            f"max {max(values):.1f}{unit}")


def _counts(d: Dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def summary_lines(s: _Session, reason: str, dur: float) -> List[str]:
    """summary.txt: what a reader needs before opening session.log."""
    try:
        from version import VERSION
    except Exception:
        VERSION = "?"
    out = [f"Diagnostic session {s.stamp}: bot {VERSION}, {dur:.1f}s, ended: {reason or 'closed'}",
           "",
           "Files:",
           f"  {SUMMARY_NAME:<22} this file",
           f"  {LOG_NAME:<22} every event in order; 'L<n>' below is a line number in it",
           "  frames_<loop>.csv      one row per frame of each per-frame loop: time, t (s since start),",
           "                         tag, loop (the #n of its LOOP lines), frame, dt_ms (since the previous",
           "                         frame), then what the loop measured",
           "  NNNN_<tag>_<label>.png images (listed below); first_* are references of what worked",
           "  bot_config.json, config_gui.json, config_effective.json: the settings",
           f"Before each image, exception and stop, session.log holds the last {RECENT_SECONDS:.0f}s of",
           "per-frame detail between '▼ recent per-frame detail' and '▲ end' (lines marked »).",
           ""]
    tags = sorted(set(list(s.tallies) + [t for t, _ in s.loop_stats] + [t for t, _ in s.samples]))
    for tag in tags:
        out.append(f"[{tag}]")
        if s.tallies.get(tag):
            out.append(f"  totals: {_counts(s.tallies[tag])}")
        for (t, key), (values, seen) in sorted(s.samples.items()):
            if t == tag:
                out.append(f"  {key}: {_dist(values, '')} (n={seen})")
        loops = sorted(((name, st) for (t, name), st in s.loop_stats.items() if t == tag),
                       key=lambda x: -x[1]["dur"])
        if loops:
            out.append("  loops (by total time):")
        for name, st in loops:
            fps = st["frames"] / st["dur"] if st["dur"] > 0 else 0
            out.append(f"    {name}: {st['n']}x, {st['frames']} frames, {st['dur']:.1f}s "
                       f"(longest {st['max_dur']:.2f}s, {fps:.0f} fps)")
            if st["dts"][0]:
                out.append(f"      frame interval: {_dist(st['dts'][0], ' ms')}")
            out.append(f"      results: {_counts(st['results'])}")
            if st["counts"]:
                out.append(f"      counts: {_counts(st['counts'])}")
            for k, (lo, hi, total, n) in sorted(st["scores"].items()):
                out.append(f"      {k}: avg {total / n:.3f} min {lo:.3f} max {hi:.3f} (n={n})")
            if st["anomalies"]:
                out.append(f"      anomalies: {_counts(st['anomalies'])}")
        out.append("")
    out.append("Index of session.log (exceptions, stops, images, anomalies, capture problems):")
    if not s.events:
        out.append("  (nothing)")
    for number, t, tag, kind, text in sorted(s.events):
        out.append(f"  L{number:<7} {_clock(t)} [{tag}] {kind}: {text}")
    for kind, n in sorted(s.event_counts.items()):
        if n > MAX_EVENTS_PER_KIND:
            out.append(f"  … {n - MAX_EVENTS_PER_KIND} more '{kind}' not listed ({n} in all)")
    out += ["",
            f"Logging: {s.lines} lines ({s.bytes >> 10} KB) in {LOG_NAME}, {s.dropped} dropped by the size cap; "
            f"{s.frame_rows} frame rows ({s.frame_bytes >> 10} KB) in {len(s.tables)} tables, "
            f"{s.frame_rows_dropped} dropped; {s.images} images ({s.image_bytes >> 10} KB); "
            f"{s.dumps} recent-detail dumps; {_failures} logging failures"]
    return out


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
                    # Screen recordings are compressed already: deflating
                    # them costs minutes and saves nothing
                    kind = zipfile.ZIP_STORED if name.lower().endswith(".mjpg") else zipfile.ZIP_DEFLATED
                    zf.write(full, arcname=f"{stamp}/{rel}".replace(os.sep, "/"), compress_type=kind)
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
                dump_recent("uncaught exception")
                _write("EXC", f"UNCAUGHT exception in main thread: {et.__name__}: {ev}\n{text}", None,
                       "sys.excepthook", event="exception")
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
                    dump_recent("uncaught exception")
                    _write("EXC", f"UNCAUGHT exception in thread {name}: "
                                  f"{args.exc_type.__name__}: {args.exc_value}\n{text}", None,
                           "threading.excepthook", event="exception")
                except Exception:
                    _fail()
            try:
                prev_thr(args)
            except Exception:
                pass

        threading.excepthook = thread_hook


def reset_for_tests() -> None:
    """Closes everything and restores the initial state (tests only)."""
    global ENABLED, _session, _last_zip, _last_dir, _last_dir_fallback, _failures, _setting
    s = _session
    if s is not None:
        s.close()
    _session = None
    wait_for_background()
    ENABLED = _setting = False
    _tls.ring = None
    _tls.loops = []
    _tls.loop = 0
    _tls.pending = 0
    _set_class_tracing(False)
    _pre_buffer.clear()
    _busy_folders.clear()
    _last_zip = _last_dir = None
    _last_dir_fallback = False
    _failures = 0

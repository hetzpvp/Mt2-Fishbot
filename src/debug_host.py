"""
Runs the Tk debug windows (debug_ui.py) next to the Qt GUI.

Pumping a Tk event loop from a Qt timer on the Qt main thread corrupted the
interpreter's thread state on Python 3.13+, so Tk gets a thread of its own:
the root, every debug window and every Tk call live on that thread. The Qt
side only puts requests into a queue, which the Tk thread drains every 50 ms.

Used only with DEBUG_MODE_EN (utils.py).
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, Dict, Hashable, Iterable, List, Optional

import diag


class TkDebugHost:
    POLL_MS = 50

    def __init__(self) -> None:
        self._queue: "queue.Queue[Callable[[object], None]]" = queue.Queue()
        self._windows: Dict[Hashable, List[object]] = {}   # touched on the Tk thread only
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()
        self._failed: Optional[str] = None
        self._stopping = False

    # ---------------------------------------------------------------- Qt side
    def start(self) -> bool:
        """Starts the Tk thread. False when Tk could not be started."""
        if self._thread is not None:
            return self._failed is None
        self._thread = threading.Thread(target=self._run, name="TkDebugHost", daemon=True)
        self._thread.start()
        self._started.wait(timeout=5.0)
        return self._failed is None and self._started.is_set()

    @property
    def error(self) -> Optional[str]:
        return self._failed

    def call(self, fn: Callable[[object], None]) -> None:
        """Runs ``fn(root)`` on the Tk thread."""
        if not self._stopping and self._failed is None:
            self._queue.put(fn)

    def open(self, key: Hashable, classes: Iterable[type], *args) -> None:
        """Creates one window of each class (``cls(root, *args)``) under ``key``;
        an older set under the same key is closed first."""
        classes = [c for c in classes if c is not None]

        def make(root) -> None:
            self._close_now(key)
            made = []
            for cls in classes:
                try:
                    made.append(cls(root, *args))
                except Exception:
                    diag.exc(f"debug window {getattr(cls, '__name__', cls)}")
            self._windows[key] = made
        self.call(make)

    def close(self, key: Hashable) -> None:
        self.call(lambda _root: self._close_now(key))

    def close_all(self) -> None:
        self.call(lambda _root: [self._close_now(k) for k in list(self._windows)])

    def stop(self) -> None:
        if self._thread is None or self._stopping:
            return

        def quit_(root) -> None:
            for k in list(self._windows):
                self._close_now(k)
            try:
                root.quit()
            except Exception:
                diag.exc()
        self.call(quit_)
        self._stopping = True

    # ---------------------------------------------------------------- Tk side
    def _close_now(self, key: Hashable) -> None:
        for window in self._windows.pop(key, []):
            try:
                window.destroy()
            except Exception:
                diag.exc()

    def _run(self) -> None:
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception as e:
            diag.exc("Tk debug host")
            self._failed = f"{type(e).__name__}: {e}"
            self._started.set()
            return
        self._started.set()

        def poll() -> None:
            while True:
                try:
                    fn = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn(root)
                except Exception:
                    diag.exc("Tk debug host call")
            if not (self._stopping and self._queue.empty()):
                root.after(self.POLL_MS, poll)
            else:
                root.quit()

        root.after(self.POLL_MS, poll)
        try:
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:
                pass


class TkStatusLog:
    """Qt-side handle of a debug_ui.StatusLogWindow living on the Tk thread."""

    KEY = ("status-log",)

    def __init__(self, host: TkDebugHost, window_cls) -> None:
        self._host = host
        host.open(self.KEY, [window_cls])

    def add_message(self, msg: str) -> None:
        def add(_root) -> None:
            for w in self._host._windows.get(self.KEY, []):
                w.add_message(msg)
        self._host.call(add)

    def destroy(self) -> None:
        self._host.close(self.KEY)

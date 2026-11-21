"""Diagnostics: a video of each game window, next to the diagnostic log.

Turned on with "Record game windows" (config ``diagnostics_record_screens``)
together with the diagnostic log. One thread per game window grabs the whole
window ``FPS`` times a second through the same capture code as the bots and
appends it as a JPEG to ``screen_W<n>_<segment>.mjpg`` in the session folder.
That is a plain Motion-JPEG stream: VLC and ``ffplay -f mjpeg`` play it, and
it needs no video library (the Windows build has no FFmpeg). OpenCV's own
AVI writer was not used: its frames came out twice as big.

Next to each stream, ``screen_W<n>_<segment>.csv`` has one row per frame: its
time on the session.log clock (to match it to the log lines around it), and
its byte offset and length in the stream (``read_frame`` reads one back).

Only the game window is recorded, never the desktop: when another program is
in front of the game window, the capture refuses to read the screen and that
moment is left out (the CSV shows the gap). A new segment starts when the
window changes size. Recording stops for good once the window's share of
``MAX_BYTES`` is used up; the log says so.
"""

import csv
import os
import threading
import time
from typing import Dict, List, Optional

import cv2
import numpy as np

import diag

FPS = 10                          # frames per second per window
JPEG_QUALITY = 75                 # about 90 KB for an 800x600 game frame
MAX_BYTES = 2 * 1024 ** 3         # all windows of one session together
_VERIFY_EVERY = 1.0               # seconds between capture checks while the window is in front
_FLUSH_EVERY = 2.0                # seconds between flushes to disk


def read_frame(stream_path: str, offset: int, length: int) -> Optional[np.ndarray]:
    """One recorded frame (BGR), from the offset and length in its CSV row."""
    with open(stream_path, "rb") as f:
        f.seek(int(offset))
        data = f.read(int(length))
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


class WindowRecorder(threading.Thread):
    """Records one game window until stop() (see the module docstring)."""

    def __init__(self, tag: str, window_manager, folder: str, capture_mode: str = "auto",
                 fps: float = FPS, max_bytes: int = MAX_BYTES):
        super().__init__(name=f"ScreenRecorder-{tag}", daemon=True)
        self.tag = tag
        self.window_manager = window_manager
        self.folder = folder
        self.capture_mode = capture_mode
        self.interval = 1.0 / max(0.5, float(fps))
        self.fps = 1.0 / self.interval
        self.max_bytes = int(max_bytes)
        self._stop_event = threading.Event()
        self.frames = 0
        self.missed = 0
        self.bytes = 0
        self.segments: List[str] = []
        self._stream = None
        self._offset = 0
        self._csv_file = None
        self._csv = None
        self._size = None

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------ recording

    def _open_segment(self, width: int, height: int) -> bool:
        self._close_segment()
        base = os.path.join(self.folder, f"screen_{self.tag}_{len(self.segments) + 1:03d}")
        try:
            self._stream = open(base + ".mjpg", "wb")
            self._csv_file = open(base + ".csv", "w", newline="", encoding="utf-8")
        except OSError as e:
            diag.log("RECORD", f"could not create {base}.mjpg ({e}): recording of this window stops",
                     tag=self.tag)
            self._close_segment()
            return False
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow(["frame", "time", "t", "width", "height", "offset", "bytes", "gap_before"])
        self._offset = 0
        self._size = (width, height)
        self.segments.append(base + ".mjpg")
        name = os.path.basename(base)
        diag.log("RECORD", f"recording {width}x{height} at {self.fps:.0f} fps into {name}.mjpg "
                           f"(frame times in {name}.csv)", tag=self.tag)
        return True

    def _close_segment(self) -> None:
        for f in (self._stream, self._csv_file):
            if f is not None:
                try:
                    f.close()
                except Exception:
                    diag.exc("closing a screen recording", tag=self.tag)
        self._stream = self._csv_file = self._csv = None

    def run(self) -> None:
        diag.set_thread_tag(self.tag)
        try:
            self._record()
        except Exception:
            diag.exc("screen recording stopped by an error", tag=self.tag)
        finally:
            self._close_segment()
            diag.log("RECORD", f"recording ended: {self.frames} frames in {len(self.segments)} file(s), "
                               f"{self.bytes / 1e6:.1f} MB, {self.missed} moments not recorded "
                               f"(window covered, minimized or not capturable)", tag=self.tag)

    def _record(self) -> None:
        from screen_capture import WindowCapture
        capture = WindowCapture(self.window_manager, self.capture_mode)
        capture.diag_tag = f"{self.tag}-rec"      # its own capture statistics, not the bot's
        capture.diag_log_grabs = False
        start = time.perf_counter()
        next_tick = start
        last_flush = last_verify = 0.0
        last_frame_t: Optional[float] = None
        gap_logged = False
        params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        while not self._stop_event.is_set():
            now = time.perf_counter()
            if capture.needs_verification and now - last_verify >= _VERIFY_EVERY:
                last_verify = now
                try:
                    if self.window_manager.is_foreground():
                        capture.verify()
                except Exception:
                    pass
            frame = None
            try:
                _, _, width, height = self.window_manager.get_window_rect()
                if width > 0 and height > 0:
                    frame = capture.grab(0, 0, width, height)
            except Exception:
                frame = None
            ok, data = (False, None) if frame is None else cv2.imencode(".jpg", frame, params)
            if not ok:
                self.missed += 1
                if not gap_logged:
                    gap_logged = True
                    diag.log("RECORD", "window cannot be recorded right now (covered, minimized or "
                                       "capture failed): leaving this moment out", tag=self.tag)
            else:
                gap_logged = False
                h, w = frame.shape[:2]
                if self.bytes + data.size > self.max_bytes:
                    diag.log("RECORD", f"recording size limit reached ({self.max_bytes / 1e6:.0f} MB "
                                       f"for this window): recording stops, the log goes on", tag=self.tag)
                    return
                if self._size != (w, h) and not self._open_segment(w, h):
                    return
                self._stream.write(data.tobytes())
                t = time.perf_counter()
                gap = "" if last_frame_t is None or t - last_frame_t < 2.5 * self.interval \
                    else f"{t - last_frame_t:.2f}"
                self._csv.writerow([self.frames, diag._now(), f"{t - start:.3f}", w, h,
                                    self._offset, data.size, gap])
                self._offset += data.size
                self.bytes += data.size
                last_frame_t = t
                self.frames += 1
                if t - last_flush >= _FLUSH_EVERY:
                    last_flush = t
                    self._stream.flush()
                    self._csv_file.flush()
            next_tick += self.interval
            delay = next_tick - time.perf_counter()
            if delay < 0:                      # fell behind: do not try to catch up
                next_tick = time.perf_counter()
                delay = 0
            self._stop_event.wait(delay)


class SessionRecorder:
    """The recorders of one diagnostic session, one per game window."""

    def __init__(self):
        self.recorders: List[WindowRecorder] = []

    def start(self, windows: Dict[str, object], folder: str, capture_mode: str = "auto") -> None:
        """``windows`` maps a tag (W1, W2, ...) to a WindowManager of that window."""
        if not windows:
            return
        share = MAX_BYTES // len(windows)
        for tag, wm in windows.items():
            rec = WindowRecorder(tag, wm, folder, capture_mode, max_bytes=share)
            self.recorders.append(rec)
            rec.start()

    def stop(self) -> List[threading.Thread]:
        """Asks every recorder to finish; returns the threads to wait for."""
        for rec in self.recorders:
            rec.stop()
        threads = [r for r in self.recorders if r.is_alive()]
        self.recorders = []
        return threads

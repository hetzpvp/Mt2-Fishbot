"""
Side-by-side screen capture method tester.

Goal: identify which Windows screen-capture APIs return real pixels vs. a
black frame for a given target window. Some Metin2 servers appear to block
certain capture paths during the fishing minigame while OBS still captures
fine, suggesting the block is API-specific rather than a general output
protection.

Each backend below is a *distinct* code path into the OS so we can tell
which one the anti-capture is actually targeting:

    1. mss              -- DXGI desktop duplication, GDI fallback
    2. PIL ImageGrab    -- GDI BitBlt of the desktop region
    3. pyautogui        -- wraps PIL but kept separate (different code path
                           in case PIL's behavior diverges across versions)
    4. Win32 BitBlt     -- BitBlt from the window's own DC (ctypes)
    5. Win32 PrintWindow-- PrintWindow + PW_RENDERFULLCONTENT (ctypes)
    6. Qt grabWindow    -- QScreen.grabWindow via PySide6

Usage:
    python capture_method_tester.py

Pick a window from the dropdown, hit Start, watch the six tiles.
Each tile shows live FPS and a "BLACK" badge when the frame is ~all zero.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageGrab
from PySide6 import QtCore, QtGui, QtWidgets

import mss
import pyautogui
import pygetwindow as gw

try:
    import windows_capture as _wc
    _WGC_AVAILABLE = True
except Exception as _wc_err:  # noqa: BLE001
    _wc = None
    _WGC_AVAILABLE = False
    _WGC_IMPORT_ERR = repr(_wc_err)


# ---------------------------------------------------------------------------
# Win32 helpers (ctypes, so we don't depend on full pywin32)
# ---------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2  # Win 8.1+: lets us grab DWM-composited / DX surfaces


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


# Signatures for the few calls where defaults are wrong on 64-bit Python.
user32.GetWindowDC.restype = wt.HDC
user32.GetWindowDC.argtypes = [wt.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.restype = wt.BOOL
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]

gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.BitBlt.restype = wt.BOOL
gdi32.BitBlt.argtypes = [
    wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wt.UINT,
]


def _hbitmap_to_array(hdc_mem: int, hbmp: int, w: int, h: int) -> np.ndarray:
    """Pull pixels out of a DDB into a numpy array (H, W, 3) RGB."""
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # negative => top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf = (ctypes.c_ubyte * (w * h * 4))()
    got = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
    if got == 0:
        raise OSError("GetDIBits failed")
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
    # GDI gives BGRA; drop alpha and flip to RGB
    return arr[:, :, [2, 1, 0]].copy()


def list_visible_windows() -> list[tuple[int, str]]:
    """Return [(hwnd, title), ...] for visible top-level windows with a title."""
    out: list[tuple[int, str]] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            out.append((int(hwnd), title))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) in screen coordinates."""
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


# ---------------------------------------------------------------------------
# Capture backends
# ---------------------------------------------------------------------------

@dataclass
class CaptureResult:
    image: Optional[np.ndarray]  # H,W,3 uint8 RGB
    error: Optional[str] = None


class Backend:
    name: str = "?"

    def capture(self, hwnd: int) -> CaptureResult:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        pass


class MssBackend(Backend):
    name = "mss (DXGI/GDI)"

    def __init__(self) -> None:
        # mss is not thread-safe; each backend instance is owned by one thread.
        self._sct: Optional[mss.base.MSSBase] = None

    def _ensure(self) -> mss.base.MSSBase:
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def capture(self, hwnd: int) -> CaptureResult:
        try:
            x, y, w, h = get_window_rect(hwnd)
            if w <= 0 or h <= 0:
                return CaptureResult(None, "zero-sized window")
            sct = self._ensure()
            raw = sct.grab({"left": x, "top": y, "width": w, "height": h})
            arr = np.frombuffer(raw.rgb, dtype=np.uint8).reshape(h, w, 3)
            return CaptureResult(arr.copy())
        except Exception as e:  # noqa: BLE001
            return CaptureResult(None, f"{type(e).__name__}: {e}")

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:  # noqa: BLE001
                pass
            self._sct = None


class PILGrabBackend(Backend):
    name = "PIL ImageGrab"

    def capture(self, hwnd: int) -> CaptureResult:
        try:
            x, y, w, h = get_window_rect(hwnd)
            if w <= 0 or h <= 0:
                return CaptureResult(None, "zero-sized window")
            # all_screens=True so we still work on multi-monitor / negative coords.
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            return CaptureResult(np.array(img.convert("RGB")))
        except Exception as e:  # noqa: BLE001
            return CaptureResult(None, f"{type(e).__name__}: {e}")


class PyAutoGuiBackend(Backend):
    name = "pyautogui"

    def capture(self, hwnd: int) -> CaptureResult:
        try:
            x, y, w, h = get_window_rect(hwnd)
            if w <= 0 or h <= 0:
                return CaptureResult(None, "zero-sized window")
            img = pyautogui.screenshot(region=(x, y, w, h))
            return CaptureResult(np.array(img.convert("RGB")))
        except Exception as e:  # noqa: BLE001
            return CaptureResult(None, f"{type(e).__name__}: {e}")


class BitBltBackend(Backend):
    """BitBlt from the window's own device context."""
    name = "Win32 BitBlt (window DC)"

    def capture(self, hwnd: int) -> CaptureResult:
        try:
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w <= 0 or h <= 0:
                return CaptureResult(None, "zero-sized window")

            hdc_win = user32.GetWindowDC(hwnd)
            if not hdc_win:
                return CaptureResult(None, "GetWindowDC=NULL")
            hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
            hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
            old = gdi32.SelectObject(hdc_mem, hbmp)
            try:
                ok = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_win, 0, 0, SRCCOPY | CAPTUREBLT)
                if not ok:
                    return CaptureResult(None, f"BitBlt failed err={ctypes.get_last_error()}")
                arr = _hbitmap_to_array(hdc_mem, hbmp, w, h)
                return CaptureResult(arr)
            finally:
                gdi32.SelectObject(hdc_mem, old)
                gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(hwnd, hdc_win)
        except Exception as e:  # noqa: BLE001
            return CaptureResult(None, f"{type(e).__name__}: {e}")


class PrintWindowBackend(Backend):
    """PrintWindow with PW_RENDERFULLCONTENT — different code path than BitBlt
    and often the only thing that captures DX-rendered windows."""
    name = "Win32 PrintWindow"

    def capture(self, hwnd: int) -> CaptureResult:
        try:
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w <= 0 or h <= 0:
                return CaptureResult(None, "zero-sized window")

            hdc_screen = user32.GetDC(0)
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
            old = gdi32.SelectObject(hdc_mem, hbmp)
            try:
                ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
                if not ok:
                    return CaptureResult(None, f"PrintWindow failed err={ctypes.get_last_error()}")
                arr = _hbitmap_to_array(hdc_mem, hbmp, w, h)
                return CaptureResult(arr)
            finally:
                gdi32.SelectObject(hdc_mem, old)
                gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(0, hdc_screen)
        except Exception as e:  # noqa: BLE001
            return CaptureResult(None, f"{type(e).__name__}: {e}")


class QtGrabBackend(Backend):
    """QScreen.grabWindow — Qt's own capture path."""
    name = "Qt grabWindow"

    def capture(self, hwnd: int) -> CaptureResult:
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen is None:
                return CaptureResult(None, "no primary screen")
            # grabWindow(0) grabs the desktop region; we crop to the target.
            x, y, w, h = get_window_rect(hwnd)
            if w <= 0 or h <= 0:
                return CaptureResult(None, "zero-sized window")
            pix = screen.grabWindow(0, x, y, w, h)
            qimg = pix.toImage().convertToFormat(QtGui.QImage.Format_RGB888)
            ptr = qimg.constBits()
            # bytesPerLine may include row padding -> handle it.
            bpl = qimg.bytesPerLine()
            buf = bytes(ptr)[: bpl * qimg.height()]
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(qimg.height(), bpl)
            arr = arr[:, : qimg.width() * 3].reshape(qimg.height(), qimg.width(), 3)
            return CaptureResult(arr.copy())
        except Exception as e:  # noqa: BLE001
            return CaptureResult(None, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class WGCBackend(Backend):
    """Windows.Graphics.Capture (WinRT) — the path OBS uses. Pushes frames
    asynchronously; we keep the latest one and serve it on poll."""
    name = "Windows Graphics Capture"

    def __init__(self) -> None:
        import threading
        self._lock = threading.Lock()
        self._latest: Optional[np.ndarray] = None
        self._latest_err: Optional[str] = None
        self._session = None  # WindowsCapture instance
        self._control = None  # CaptureControl from start_free_threaded()
        self._hwnd: int = 0
        self._fatal: Optional[str] = None
        if not _WGC_AVAILABLE:
            self._fatal = f"windows_capture not importable: {_WGC_IMPORT_ERR}"

    def _ensure_session(self, hwnd: int) -> None:
        if self._fatal:
            return
        if self._session is not None and self._hwnd == hwnd:
            return
        self.close()
        self._hwnd = hwnd

        cap = _wc.WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=hwnd,
        )

        @cap.event
        def on_frame_arrived(frame, capture_control):  # noqa: ARG001
            try:
                buf = frame.frame_buffer  # BGRA, shape (h, w, 4)
                rgb = buf[:, :, [2, 1, 0]].copy()
                with self._lock:
                    self._latest = rgb
                    self._latest_err = None
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    self._latest_err = f"frame handler: {type(e).__name__}: {e}"

        @cap.event
        def on_closed():
            with self._lock:
                self._latest_err = (self._latest_err or "") + " [session closed]"

        try:
            self._session = cap
            self._control = cap.start_free_threaded()
        except Exception as e:  # noqa: BLE001
            self._fatal = f"start failed: {type(e).__name__}: {e}"
            self._session = None
            self._control = None

    def capture(self, hwnd: int) -> CaptureResult:
        if self._fatal:
            return CaptureResult(None, self._fatal)
        try:
            self._ensure_session(hwnd)
        except Exception as e:  # noqa: BLE001
            return CaptureResult(None, f"ensure_session: {type(e).__name__}: {e}")

        with self._lock:
            img = self._latest
            err = self._latest_err
        if img is None:
            return CaptureResult(None, err or "no frame yet")
        return CaptureResult(img, err)

    def close(self) -> None:
        ctrl = self._control
        self._control = None
        self._session = None
        if ctrl is not None:
            try:
                ctrl.stop()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._latest = None
            self._latest_err = None


# ---------------------------------------------------------------------------
# Per-tile worker
# ---------------------------------------------------------------------------

@dataclass
class TileStats:
    fps: float = 0.0
    last_err: str = ""
    is_black: bool = False
    is_uniform: bool = False  # entirely one color (often a different failure mode)


class CaptureWorker(QtCore.QObject):
    """Owns one Backend in its own thread and emits frames at ~target FPS."""

    frame_ready = QtCore.Signal(object, object)  # (np.ndarray | None, TileStats)

    def __init__(self, backend: Backend, target_fps: float = 30.0) -> None:
        super().__init__()
        self.backend = backend
        self.target_dt = 1.0 / target_fps
        self._hwnd: int = 0
        self._running = False
        self._timer: Optional[QtCore.QTimer] = None
        self._frames = 0
        self._fps_t0 = time.perf_counter()
        self._fps = 0.0

    @QtCore.Slot(int)
    def set_hwnd(self, hwnd: int) -> None:
        self._hwnd = hwnd

    @QtCore.Slot()
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._timer = QtCore.QTimer()
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(self.target_dt * 1000))

    @QtCore.Slot()
    def stop(self) -> None:
        self._running = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.backend.close()

    def _tick(self) -> None:
        if not self._running or not self._hwnd:
            return
        res = self.backend.capture(self._hwnd)

        self._frames += 1
        now = time.perf_counter()
        if now - self._fps_t0 >= 0.5:
            self._fps = self._frames / (now - self._fps_t0)
            self._frames = 0
            self._fps_t0 = now

        stats = TileStats(fps=self._fps, last_err=res.error or "")
        if res.image is not None:
            # Sample a stride for speed; full image isn't needed to detect black.
            sample = res.image[::8, ::8]
            stats.is_black = bool(sample.max() <= 4)  # allow tiny noise
            stats.is_uniform = bool(sample.min() == sample.max())
        self.frame_ready.emit(res.image, stats)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class CaptureTile(QtWidgets.QFrame):
    """One tile = one backend's live preview + stats."""

    def __init__(self, backend_name: str) -> None:
        super().__init__()
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)

        self.title = QtWidgets.QLabel(backend_name)
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)

        self.image_label = QtWidgets.QLabel()
        self.image_label.setMinimumSize(320, 200)
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setStyleSheet("background:#222;color:#888;")
        self.image_label.setText("(no frames yet)")
        self.image_label.setScaledContents(False)

        self.status = QtWidgets.QLabel("idle")
        self.status.setStyleSheet("color:#aaa;font-family:Consolas,monospace;")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.title)
        lay.addWidget(self.image_label, stretch=1)
        lay.addWidget(self.status)

    @QtCore.Slot(object, object)
    def on_frame(self, image, stats: TileStats) -> None:
        if image is None:
            self.image_label.setText("ERROR")
            self.image_label.setStyleSheet("background:#400;color:#fcc;")
        else:
            h, w, _ = image.shape
            qimg = QtGui.QImage(image.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
            pix = QtGui.QPixmap.fromImage(qimg).scaled(
                self.image_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            self.image_label.setPixmap(pix)
            if stats.is_black:
                self.image_label.setStyleSheet("background:#000;border:2px solid #c33;")
            elif stats.is_uniform:
                self.image_label.setStyleSheet("background:#000;border:2px solid #c93;")
            else:
                self.image_label.setStyleSheet("background:#000;border:2px solid #2a2;")

        badge = ""
        if stats.is_black:
            badge = "  [BLACK]"
        elif stats.is_uniform:
            badge = "  [UNIFORM]"
        msg = f"{stats.fps:5.1f} fps{badge}"
        if stats.last_err:
            msg += f"   err: {stats.last_err[:80]}"
        self.status.setText(msg)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Capture Method Tester")
        self.resize(1000, 720)

        # --- top bar: window picker + controls ---
        self.window_combo = QtWidgets.QComboBox()
        self.window_combo.setMinimumWidth(400)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.setMinimumWidth(220)
        self.refresh_btn = QtWidgets.QPushButton("Refresh windows")
        self.start_btn = QtWidgets.QPushButton("Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.fps_spin = QtWidgets.QSpinBox()
        self.fps_spin.setRange(1, 120)
        self.fps_spin.setValue(30)
        self.fps_spin.setSuffix(" fps")

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Window:"))
        top.addWidget(self.window_combo, stretch=1)
        top.addWidget(self.refresh_btn)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("Backend:"))
        top.addWidget(self.backend_combo)
        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("Target:"))
        top.addWidget(self.fps_spin)
        top.addSpacing(12)
        top.addWidget(self.start_btn)
        top.addWidget(self.stop_btn)

        # --- backends + a worker per backend (lazy-started) ---
        self.backends: list[Backend] = [
            MssBackend(),
            PILGrabBackend(),
            PyAutoGuiBackend(),
            BitBltBackend(),
            PrintWindowBackend(),
            QtGrabBackend(),
            WGCBackend(),
        ]
        for i, b in enumerate(self.backends):
            self.backend_combo.addItem(b.name, userData=i)

        # Single tile, swapped to whichever backend is selected.
        self.tile = CaptureTile(self.backends[0].name)

        central = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(central)
        outer.addLayout(top)
        outer.addWidget(self.tile, stretch=1)
        self.setCentralWidget(central)

        # One thread per backend so switching is instant (no thread creation
        # cost), but only the selected one is actually started.
        self.threads: list[QtCore.QThread] = []
        self.workers: list[CaptureWorker] = []
        for backend in self.backends:
            worker = CaptureWorker(backend, target_fps=float(self.fps_spin.value()))
            thread = QtCore.QThread(self)
            worker.moveToThread(thread)
            thread.start()
            self.workers.append(worker)
            self.threads.append(thread)

        self._active_idx: int = -1
        self._active_conn = None  # QMetaObject.Connection for the live frame_ready link

        # --- wire up ---
        self.refresh_btn.clicked.connect(self.refresh_windows)
        self.start_btn.clicked.connect(self.start_capture)
        self.stop_btn.clicked.connect(self.stop_capture)
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)

        self.refresh_windows()

    # ----- window list -----
    def refresh_windows(self) -> None:
        self.window_combo.clear()
        wins = list_visible_windows()
        # bias likely-interesting ones to the top
        def sort_key(item: tuple[int, str]) -> tuple[int, str]:
            t = item[1].lower()
            priority = 0
            for kw in ("metin", "mt2", "client", "game"):
                if kw in t:
                    priority = -1
                    break
            return (priority, t)
        wins.sort(key=sort_key)
        for hwnd, title in wins:
            self.window_combo.addItem(f"[{hwnd:08X}] {title}", userData=hwnd)

    # ----- start/stop -----
    def _selected_hwnd(self) -> Optional[int]:
        if self.window_combo.currentIndex() < 0:
            return None
        hwnd = self.window_combo.currentData()
        if not hwnd or not user32.IsWindow(hwnd):
            return None
        return int(hwnd)

    def _activate_worker(self, idx: int, hwnd: int) -> None:
        """Stop any previously active worker, start the one at idx."""
        if self._active_idx == idx:
            # Make sure hwnd is up to date but don't restart.
            w = self.workers[idx]
            QtCore.QMetaObject.invokeMethod(
                w, "set_hwnd", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(int, hwnd)
            )
            return

        if self._active_idx >= 0:
            prev = self.workers[self._active_idx]
            QtCore.QMetaObject.invokeMethod(prev, "stop", QtCore.Qt.QueuedConnection)
            if self._active_conn is not None:
                try:
                    prev.frame_ready.disconnect(self.tile.on_frame)
                except (RuntimeError, TypeError):
                    pass
                self._active_conn = None

        w = self.workers[idx]
        self.tile.title.setText(self.backends[idx].name)
        self.tile.image_label.setText("(starting…)")
        self.tile.image_label.setStyleSheet("background:#222;color:#888;")
        self.tile.status.setText("idle")
        self._active_conn = w.frame_ready.connect(self.tile.on_frame)
        QtCore.QMetaObject.invokeMethod(
            w, "set_hwnd", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(int, hwnd)
        )
        QtCore.QMetaObject.invokeMethod(w, "start", QtCore.Qt.QueuedConnection)
        self._active_idx = idx

    def start_capture(self) -> None:
        hwnd = self._selected_hwnd()
        if hwnd is None:
            QtWidgets.QMessageBox.warning(self, "No window", "Pick a window first.")
            return
        idx = self.backend_combo.currentIndex()
        if idx < 0:
            return
        self._activate_worker(idx, hwnd)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.window_combo.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.fps_spin.setEnabled(False)

    def stop_capture(self) -> None:
        if self._active_idx >= 0:
            w = self.workers[self._active_idx]
            QtCore.QMetaObject.invokeMethod(w, "stop", QtCore.Qt.QueuedConnection)
            try:
                w.frame_ready.disconnect(self.tile.on_frame)
            except (RuntimeError, TypeError):
                pass
            self._active_conn = None
            self._active_idx = -1
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.window_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.fps_spin.setEnabled(True)

    def _on_backend_changed(self, idx: int) -> None:
        # If we're already running, hot-swap to the new backend.
        if self.stop_btn.isEnabled() and idx >= 0:
            hwnd = self._selected_hwnd()
            if hwnd is not None:
                self._activate_worker(idx, hwnd)
        else:
            # Just update the tile title so the user sees what they picked.
            if idx >= 0:
                self.tile.title.setText(self.backends[idx].name)

    def _on_fps_changed(self, value: int) -> None:
        # Only takes effect on next start — workers read target_dt at construction.
        for w in self.workers:
            w.target_dt = 1.0 / float(value)

    # ----- shutdown -----
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.stop_capture()
        for t in self.threads:
            t.quit()
        for t in self.threads:
            t.wait(2000)
        super().closeEvent(event)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

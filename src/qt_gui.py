"""
PySide6 redesigned GUI for the Fishing Puzzle Player.

Implements the "Fishbot GUI Redesign" mockup (dark + amber terminal vibe,
tabbed layout: Dashboard / Inventory / Settings, modals for Fish selection,
Timing settings, and first-run wizard).

Drives the same FishingBot / WindowManager / bot_config.json contract used by
the original Tk-based bot_gui.py so it can be a drop-in alternative.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

# High-DPI awareness must be set before any Qt window is created.
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    os.environ.setdefault("QT_QPA_PLATFORM", "windows:dpiawareness=0")
elif sys.platform.startswith("linux"):
    # X11 (XWayland on a Wayland desktop) first, like the game under Wine:
    # the global F5 listener and the click-to-capture listener (pynput,
    # X11 RECORD) then also see keys/clicks while this window has focus.
    # Native Wayland is the fallback when Qt's xcb plugin can't load.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb;wayland")

from PySide6.QtCore import (
    QByteArray, QEvent, QObject, QPoint, QSize, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QDoubleValidator, QFont, QFontDatabase, QIcon, QImage, QIntValidator,
    QCursor, QKeySequence, QMovie, QPainter, QPalette, QPixmap, QShortcut,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QCheckBox, QRadioButton, QColorDialog, QComboBox, QDialog,
    QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QStackedWidget, QToolTip, QVBoxLayout, QWidget,
)

# Make src/ importable when running as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from utils import (  # noqa: E402
    DEBUG_MODE_EN, DEBUG_PRINTS, DISCORD_INVITE_URL, MAX_WINDOWS,
    get_resource_path, is_bait_fish, normalize_fish_action, with_dead_fish_actions,
    load_window_icon, play_pause_beep, play_rickroll_beep,
)
import diag  # noqa: E402
import dialog_watch  # noqa: E402
import disguise  # noqa: E402
import inventory_layout  # noqa: E402
import setup_checks  # noqa: E402
import session_setup  # noqa: E402
from session_setup import (  # noqa: E402
    BAIT_DISPLAY, DEAD_DISPLAY, FISH_DISPLAY, ITEM_DISPLAY, default_fish_action,
)
from window_manager import WindowManager, window_size_mismatch  # noqa: E402
import help_visuals  # noqa: E402
from input_backend import probe_backend, probed_backend_info  # noqa: E402

# Tk + Pillow are only for the DEBUG_MODE_EN windows: skipped otherwise, which
# saves their import on every start (the Windows exe ships neither).
try:
    if not DEBUG_MODE_EN:
        raise ImportError("debug windows off")
    import tkinter as tk  # noqa: E402
    from debug_ui import (  # noqa: E402
        FishDetectorDebugWindow,
        IgnoredPositionsWindow,
        InventoryDetectionDebugWindow,
        JigsawSolverDebugWindow,
        StatusLogWindow,
    )
except Exception:
    if DEBUG_MODE_EN:
        diag.exc()
    tk = None
    FishDetectorDebugWindow = None
    IgnoredPositionsWindow = None
    InventoryDetectionDebugWindow = None
    JigsawSolverDebugWindow = None
    StatusLogWindow = None

# Try to import the bot lazily — keeps the GUI usable for layout tweaks even
# when OpenCV / heavy deps aren't installed.
try:
    from fishing_bot import FishingBot  # noqa: E402
except Exception:  # pragma: no cover - import-time failures are surfaced at start
    diag.exc()
    FishingBot = None

try:
    from pynput import keyboard as pyn_keyboard, mouse as pyn_mouse  # noqa: E402
except Exception:
    diag.exc()
    pyn_keyboard = None
    pyn_mouse = None

try:
    from version import VERSION as APP_VERSION  # noqa: E402
except Exception:
    diag.exc()
    APP_VERSION = "1.2"


# ============================================================================
# Design tokens (mirror styles.css)
# ============================================================================

C = {
    "bg_0": "#0b0b0c", "bg_1": "#131316", "bg_2": "#1a1a1e",
    "bg_3": "#222227", "bg_4": "#2c2c33",
    "line": "#2a2a30", "line_2": "#36363e",
    "text": "#e8e8ec", "text_dim": "#9a9aa2", "text_mute": "#85858e",
    "text_off": "#6a6a73",      # disabled text (text_mute is for hints and must stay readable)
    "accent": "#ffb627", "accent_deep": "#cc8a00", "accent_hover": "#ffc645",
    "on_accent": "#1a1108",     # text / icons drawn on an accent background
    "green": "#4ade80", "red": "#ef5e5e", "blue": "#5aa9ff",
    "purple": "#b46cf2", "orange": "#ff9c5a",
}
DEFAULT_ACCENT = C["accent"]


def set_accent(color: str) -> str:
    """Makes ``color`` the accent: derives the hover / border shades and the
    text color used on it (dark on light accents, white on dark ones), so a
    custom accent never leaves amber leftovers or unreadable buttons.
    Returns the accent actually used (the default for an invalid color)."""
    q = QColor(color) if isinstance(color, str) else QColor()
    if not q.isValid():
        q = QColor(DEFAULT_ACCENT)
    C["accent"] = q.name()
    C["accent_deep"] = q.darker(125).name()
    C["accent_hover"] = q.lighter(115).name()
    lum = 0.2126 * q.redF() + 0.7152 * q.greenF() + 0.0722 * q.blueF()
    C["on_accent"] = "#1a1108" if lum >= 0.45 else "#ffffff"
    return C["accent"]

MONO = "'Consolas', 'JetBrains Mono', 'Courier New', monospace"
SANS = "'Segoe UI', 'Inter', system-ui, sans-serif"

# The two activities. One is selected at a time (mode bar under the header);
# only the selected one can be started, and the switch is locked while it runs.
MODE_FISHING = "fishing"
MODE_JIGSAW = "jigsaw"
MODE_NAMES = {MODE_FISHING: "Fishing", MODE_JIGSAW: "Jigsaw Puzzle"}
# Former options that are now always on (no checkbox): the Golden Tuna answer
# (harmless where the dialog does not exist), learning re-skinned minigame
# colors and shortening the wait before the next cast.
ALWAYS_ON_KEYS = ("golden_tuna", "adaptive_detection", "adaptive_recast_wait")
# Each activity has its own color (mode buttons, the bar under them, the mode
# tag), so the selected one is seen at a glance whatever the accent color.
MODE_COLORS = {MODE_FISHING: "#5aa9ff", MODE_JIGSAW: "#b46cf2"}
MODE_TAGS = {MODE_FISHING: "FISHBOT MODE", MODE_JIGSAW: "JIGSAW PUZZLE SOLVER MODE"}

# Interface size. Everything is laid out in logical pixels for a 920x664
# window; Qt's own scale factor (QT_SCALE_FACTOR) enlarges or shrinks text,
# icons, spacing and the window together. "auto" picks a size from the free
# screen area; any choice is capped so the window always fits the screen,
# which on small or strongly OS-scaled screens means going below 100 %.
WINDOW_W, WINDOW_H = 920, 664     # the dashboard's 8 window rows fit without empty space
MIN_UI_SCALE = 0.7
UI_SCALE_CHOICES = [("auto", "Auto"), ("1.0", "100%"), ("1.15", "115%"),
                    ("1.25", "125%"), ("1.5", "150%"), ("1.75", "175%")]
MIN_WINDOW_H = 520             # shortest window: the tab body scrolls below 664
ACTIVE_UI_SCALE = 1.0          # set by main()
ACTIVE_WINDOW_H = WINDOW_H     # set by main(): shorter only on a screen too short at MIN_UI_SCALE
ACTIVE_UI_SCALE_SOURCE = "default"   # auto / setting / env / default
ACTIVE_UI_SCALE_SETTING = "auto"     # the Interface size choice this run started with


def _scale_key(f: float) -> str:
    for key, _ in UI_SCALE_CHOICES:
        if key != "auto" and abs(float(key) - f) < 1e-6:
            return key
    return ""


def fitting_ui_scale(avail_w: int, avail_h: int) -> float:
    """Largest scale (0.05 steps) at which the window fits the free screen
    area (logical pixels, i.e. after OS scaling). Enlarging keeps 10 % of the
    screen free; 100 % is kept while the window fits at all, and only a screen
    too small for it (1366x768, 1080p at 150 % ...) gets a smaller interface,
    never below MIN_UI_SCALE."""
    fit = min(avail_w * 0.9 / WINDOW_W, avail_h * 0.9 / WINDOW_H)
    if fit >= 1.0:
        return int(fit * 20 + 1e-6) / 20
    tight = min(avail_w * 0.98 / WINDOW_W, avail_h * 0.98 / WINDOW_H)
    if tight >= 1.0:
        return 1.0
    return max(MIN_UI_SCALE, int(tight * 20 + 1e-6) / 20)


def window_height_for(avail_h: int, scale: float) -> int:
    """The window height (logical pixels before scaling). WINDOW_H unless
    the screen is too short even at MIN_UI_SCALE (1080p at 250 %, 1366x768
    at 175 %): then the window gets shorter and the tab body scrolls, rather
    than the text getting smaller still."""
    fit = int(avail_h * 0.98 / max(scale, 0.01))
    return max(MIN_WINDOW_H, min(WINDOW_H, fit))


def auto_ui_scale(avail_w: int, avail_h: int) -> float:
    """Big screens (1440p and up at 100 %) get 125 %, 1080p-class screens
    115 %, smaller or already OS-scaled screens 100 % — never more than fits."""
    target = 1.25 if avail_h >= 1300 else 1.15 if avail_h >= 1000 else 1.0
    return min(target, fitting_ui_scale(avail_w, avail_h))


def choose_ui_scale(setting, avail_w: int, avail_h: int) -> float:
    if setting not in (None, "", "auto"):
        try:
            return min(max(MIN_UI_SCALE, float(setting)), fitting_ui_scale(avail_w, avail_h))
        except (TypeError, ValueError):
            pass
    return auto_ui_scale(avail_w, avail_h)


def _sync_help_theme() -> None:
    help_visuals.set_theme({"bg": C["bg_1"], "bg_2": C["bg_2"], "line": C["line"], "text": C["text"],
                            "text_dim": C["text_dim"], "text_mute": C["text_mute"], "accent": C["accent"],
                            "green": C["green"], "red": C["red"], "blue": C["blue"]}, MONO)


def show_help(parent, topic: str, problem: Optional[str] = None, title: Optional[str] = None,
              modal: bool = True):
    """A help page with a picture (help_visuals), with what went wrong on
    top. Module level so tests can replace it."""
    _sync_help_theme()
    return help_visuals.show_help(parent, topic, problem=problem, title=title, modal=modal)


def open_guide(parent, start: Optional[str] = None):
    """The illustrated guide, not modal: it can stay open next to the
    window while the user follows it."""
    _sync_help_theme()
    return help_visuals.open_guide(parent, start=start, modal=False)


def guide_button(on_click) -> "QPushButton":
    btn = QPushButton("  Guide")
    btn.setProperty("class", "BtnGhost")
    btn.setIcon(make_icon("help", 12, C["text"]))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip("How to set this up, with pictures")
    btn.clicked.connect(on_click)
    return btn


def no_enter_default(dialog: "QDialog") -> None:
    """In a QDialog every push button is an "auto default": Enter in a text
    field or on a checkbox clicks the first of them — here the header's
    close button, so Enter closed the dialog (and Cancel-like buttons threw
    the edits away). Enter does nothing in these dialogs instead."""
    for btn in dialog.findChildren(QPushButton):
        btn.setAutoDefault(False)
        btn.setDefault(False)


def build_qss(accent: Optional[str] = None) -> str:
    """Returns the global stylesheet for ``accent`` (default: the current one)."""
    accent = set_accent(accent) if accent else C["accent"]
    on_accent, accent_deep, accent_hover = C["on_accent"], C["accent_deep"], C["accent_hover"]
    # A checked box has a tick, not only a color
    check_on = qss_icon_file("check", on_accent)
    check_off = qss_icon_file("check", C["text_mute"])
    return f"""
    /* ---------- Base ---------- */
    QWidget {{
        color: {C['text']};
        background: transparent;
        font-family: {SANS};
        font-size: 14px;
    }}
    QToolTip {{
        color: {C['text']};
        background: {C['bg_3']};
        border: 1px solid {C['line_2']};
        padding: 4px 8px;
        font-family: {MONO};
        font-size: 11px;
    }}

    /* ---------- App window shell ---------- */
    #AppWindow {{
        background: {C['bg_1']};
        border: 1px solid {C['line_2']};
        border-radius: 10px;
    }}
    #AppWindowInner {{
        background: {C['bg_1']};
    }}
    #AppBackground {{
        background: #050506;
    }}

    /* ---------- Window buttons (in the header) ---------- */
    QPushButton#TbBtn {{
        background: transparent;
        border: none;
        color: {C['text_dim']};
        padding: 0;
    }}
    QPushButton#TbBtn:hover {{ background: #232328; color: {C['text']}; }}
    QPushButton#TbClose:hover {{ background: #c53030; color: white; }}

    /* ---------- Header ---------- */
    #Header {{
        background: #0e0e11;
        border-bottom: 1px solid {C['line']};
    }}
    #HeaderTitle {{
        font-family: {MONO};
        font-size: 15px;
        font-weight: 700;
        color: {accent};
        letter-spacing: 1px;
    }}
    #HeaderVersion {{
        font-family: {MONO};
        font-size: 12px;
        color: {C['text_mute']};
        font-weight: 400;
        letter-spacing: 1px;
    }}
    QPushButton#DiscordBtn {{
        background: #5865F2;
        color: white;
        border: 1px solid #4752c4;
        border-radius: 11px;
        padding: 0 12px;
        font-family: {MONO};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1px;
    }}
    QPushButton#DiscordBtn:hover {{ background: #6d78f5; }}
    QPushButton#DiscordBtn:pressed {{ background: #4752c4; }}

    /* ---------- Mode bar (Fishing | Jigsaw) ---------- */
    #ModeBar {{
        background: {C['bg_0']};
        border-bottom: 3px solid {C['line']};
    }}
    #ModeBar[mode="fishing"] {{ background: rgba(90,169,255,0.07); border-bottom: 3px solid {MODE_COLORS['fishing']}; }}
    #ModeBar[mode="jigsaw"]  {{ background: rgba(180,108,242,0.08); border-bottom: 3px solid {MODE_COLORS['jigsaw']}; }}
    QLabel#ModeTag {{
        font-family: {MONO};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1px;
        border-radius: 10px;
        padding: 2px 10px;
    }}
    QLabel#ModeTag[mode="fishing"] {{ color: {MODE_COLORS['fishing']}; background: rgba(90,169,255,0.14);
                                      border: 1px solid {MODE_COLORS['fishing']}; }}
    QLabel#ModeTag[mode="jigsaw"]  {{ color: {MODE_COLORS['jigsaw']}; background: rgba(180,108,242,0.14);
                                      border: 1px solid {MODE_COLORS['jigsaw']}; }}
    .ModeCaption {{
        font-family: {MONO};
        font-size: 11px;
        color: {C['text_mute']};
        letter-spacing: 1px;
    }}
    QPushButton.ModeBtn {{
        background: {C['bg_2']};
        border: 1px solid {C['line_2']};
        border-radius: 6px;
        font-family: {MONO};
        font-size: 13px;
        font-weight: 700;
        color: {C['text_dim']};
        padding: 6px 18px;
        letter-spacing: 1px;
    }}
    QPushButton.ModeBtn:hover {{ color: {C['text']}; border-color: {C['text_mute']}; }}
    QPushButton.ModeBtn[active="true"] {{
        background: {C['bg_3']};
        color: {accent};
        border: 2px solid {accent};
    }}
    QPushButton.ModeBtn[active="true"][mode="fishing"] {{
        background: {MODE_COLORS['fishing']}; color: #0b0b0e; border: 2px solid {MODE_COLORS['fishing']};
    }}
    QPushButton.ModeBtn[active="true"][mode="jigsaw"] {{
        background: {MODE_COLORS['jigsaw']}; color: #0b0b0e; border: 2px solid {MODE_COLORS['jigsaw']};
    }}
    QPushButton.ModeBtn:disabled {{ color: #4a4a52; border-color: {C['line']}; background: {C['bg_1']}; }}
    .ModeHint {{ font-family: {MONO}; font-size: 11px; color: {C['text_mute']}; }}
    .ModeHintLocked {{ font-family: {MONO}; font-size: 11px; color: {C['orange']}; }}
    .ColHead {{ font-family: {MONO}; font-size: 10px; color: {C['text_mute']}; letter-spacing: 1px; }}
    QPushButton.Chip {{
        background: {C['bg_2']};
        border: 1px solid {C['line_2']};
        border-radius: 10px;
        font-family: {MONO};
        font-size: 11px;
        padding: 2px 9px;
        color: {C['text_dim']};
    }}
    QPushButton.Chip[state="ok"]   {{ color: {C['green']};  border-color: rgba(74,222,128,0.45); }}
    QPushButton.Chip[state="bad"]  {{ color: {C['red']};    border-color: rgba(239,94,94,0.55); }}
    QPushButton.Chip[state="wait"] {{ color: {C['orange']}; border-color: rgba(255,156,90,0.55); }}

    /* ---------- Tabs ---------- */
    #TabsBar {{
        background: {C['bg_1']};
        border-bottom: 1px solid {C['line']};
    }}
    QPushButton.TabBtn {{
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        font-family: {MONO};
        font-size: 12px;
        font-weight: 600;
        color: {C['text_mute']};
        padding: 8px 16px;
        letter-spacing: 1px;
        text-align: left;
    }}
    QPushButton.TabBtn:hover {{ color: {C['text_dim']}; }}
    QPushButton.TabBtn[active="true"] {{
        color: {accent};
        border-bottom: 2px solid {accent};
    }}

    /* ---------- Body ---------- */
    #Body {{ background: {C['bg_1']}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: {C['bg_1']};
        width: 8px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        /* visible against the page (3:1), so "there is more below" shows */
        background: {C['text_mute']};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {C['text_dim']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    /* ---------- Card ---------- */
    .Card {{
        background: {C['bg_2']};
        border: 1px solid {C['line']};
        border-radius: 8px;
    }}
    .CardHeader {{
        background: rgba(255,255,255,0.02);
        border-bottom: 1px solid {C['line']};
    }}
    .CardTitle {{
        font-family: {MONO};
        font-size: 12px;
        color: {C['text_dim']};
        letter-spacing: 2px;
        font-weight: 600;
    }}
    .CardDot {{
        background: {accent};
        border-radius: 3px;
    }}

    /* ---------- Buttons ---------- */
    QPushButton.Btn {{
        font-family: {MONO};
        font-size: 12px;
        background: {C['bg_3']};
        color: {C['text']};
        border: 1px solid {C['line_2']};
        padding: 6px 12px;
        border-radius: 6px;
        letter-spacing: 1px;
    }}
    QPushButton.Btn:hover {{ background: {C['bg_4']}; border: 1px solid #444; }}
    QPushButton.Btn:disabled {{ color: {C['text_off']}; background: {C['bg_2']}; }}

    QPushButton.BtnGhost {{
        background: transparent;
        color: {C['text']};
        border: 1px solid {C['line']};
        font-family: {MONO};
        font-size: 12px;
        padding: 6px 12px;
        border-radius: 6px;
        letter-spacing: 1px;
    }}
    QPushButton.BtnGhost:hover {{ background: {C['bg_3']}; }}
    QPushButton.BtnGhost:disabled {{ color: {C['text_off']}; border-color: {C['bg_3']}; }}

    QPushButton.BtnPrimary {{
        background: {accent};
        color: {on_accent};
        font-weight: 700;
        border: 1px solid {accent_deep};
        padding: 8px 14px;
        border-radius: 6px;
        font-family: {MONO};
        font-size: 12px;
        letter-spacing: 1px;
    }}
    QPushButton.BtnPrimary:hover {{ background: {accent_hover}; }}
    QPushButton.BtnPrimary:disabled {{ background: {C['bg_3']}; color: {C['text_off']}; border: 1px solid {C['line_2']}; }}

    QPushButton.BtnDanger {{
        background: {C['bg_3']};
        color: {C['red']};
        border: 1px solid #4a2628;
        font-weight: 700;
        padding: 8px 14px;
        border-radius: 6px;
        font-family: {MONO};
        font-size: 12px;
        letter-spacing: 1px;
    }}
    QPushButton.BtnDanger:hover {{ background: #2c1517; border: 1px solid {C['red']}; }}

    QPushButton.BtnLarge {{
        font-size: 13px;
        font-weight: 700;
        padding: 10px 16px;
        border-radius: 8px;
        letter-spacing: 1px;
    }}

    /* ---------- Inputs ---------- */
    QLineEdit, QComboBox {{
        font-family: {MONO};
        font-size: 13px;
        background: {C['bg_1']};
        color: {C['text']};
        border: 1px solid {C['line_2']};
        border-radius: 6px;
        padding: 5px 8px;
        selection-background-color: {accent};
        selection-color: {on_accent};
    }}
    QLineEdit:focus, QComboBox:focus {{
        border: 1px solid {accent};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox::down-arrow {{
        image: none;
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {C['text_dim']};
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background: {C['bg_2']};
        border: 1px solid {C['line_2']};
        color: {C['text']};
        selection-background-color: {accent};
        selection-color: {on_accent};
        outline: none;
    }}

    /* ---------- Checkbox ---------- */
    QCheckBox {{
        font-family: {MONO};
        font-size: 12px;
        color: {C['text']};
        spacing: 8px;
    }}
    QCheckBox:disabled {{ color: {C['text_off']}; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        background: {C['bg_1']};
        border: 1px solid {C['line_2']};
        border-radius: 3px;
    }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border: 1px solid {accent};
        image: url("{check_on}");
    }}
    /* Checked but switched off (Quick Skip mode while it is disabled ...):
       grey, so it does not read as an active choice. */
    QCheckBox::indicator:checked:disabled {{
        background: {C['bg_4']};
        border: 1px solid {C['line_2']};
        image: url("{check_off}");
    }}
    QCheckBox::indicator:disabled {{ border: 1px solid {C['line']}; }}
    QRadioButton {{
        color: {C['text']};
        font-family: {MONO};
        font-size: 12px;
        spacing: 8px;
    }}
    QRadioButton:disabled {{ color: {C['text_off']}; }}
    QRadioButton::indicator {{
        width: 14px; height: 14px;
        background: {C['bg_1']};
        border: 1px solid {C['line_2']};
        border-radius: 8px;
    }}
    QRadioButton::indicator:checked {{
        background: {accent};
        border: 3px solid {C['bg_3']};
        outline: none;
    }}

    /* ---------- Pills ---------- */
    .Pill {{
        background: {C['bg_3']};
        border: 1px solid {C['line_2']};
        border-radius: 10px;
        font-family: {MONO};
        font-size: 11px;
        font-weight: 600;
        color: {C['text_dim']};
        padding: 0 8px;
        letter-spacing: 1px;
    }}
    .PillActive {{
        background: rgba(74,222,128,0.08);
        border: 1px solid rgba(74,222,128,0.3);
        color: {C['green']};
    }}
    .PillWarn {{
        background: rgba(255,156,90,0.08);
        border: 1px solid rgba(255,156,90,0.3);
        color: {C['orange']};
    }}
    .PillError {{
        background: rgba(239,94,94,0.08);
        border: 1px solid rgba(239,94,94,0.3);
        color: {C['red']};
    }}

    /* ---------- KPI ---------- */
    .Kpi {{
        background: {C['bg_2']};
        border: 1px solid {C['line']};
        border-radius: 8px;
    }}
    .KLabel {{
        font-family: {MONO};
        font-size: 12px;
        color: {C['text_mute']};
        letter-spacing: 1px;
    }}
    .KValue {{
        font-family: {MONO};
        font-size: 17px;
        font-weight: 700;
        color: {accent};
    }}
    .KSub {{
        font-family: {MONO};
        font-size: 12px;
        color: {C['text_dim']};
    }}

    /* ---------- Coord pill ---------- */
    .CoordPill {{
        background: {C['bg_1']};
        border: 1px solid {C['line_2']};
        border-radius: 6px;
    }}
    .CoordPill[set="true"] {{
        background: rgba(74,222,128,0.04);
        border: 1px solid rgba(74,222,128,0.3);
    }}
    .CoordPill[capturing="true"] {{
        background: rgba(255,156,90,0.06);
        border: 1px solid {C['orange']};
    }}
    .CoordPillLabel {{
        font-family: {MONO};
        font-size: 12px;
        color: {C['text']};
    }}
    .CoordPillCoords {{
        font-family: {MONO};
        font-size: 11px;
        color: {C['text_mute']};
    }}
    .CoordPill[set="true"] .CoordPillCoords {{ color: {C['green']}; }}

    /* ---------- Win row ---------- */
    .WinRow {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 5px;
    }}
    .WinRow:hover {{
        background: {C['bg_3']};
        border: 1px solid {C['line_2']};
    }}
    .WinRow[active="true"] {{
        background: rgba(74,222,128,0.05);
        border: 1px solid rgba(74,222,128,0.18);
    }}
    .WinNum {{
        font-family: {MONO};
        font-size: 11px;
        color: {C['text_mute']};
    }}
    .WinNumActive {{ color: {C['green']}; }}
    .WStatLabel {{
        font-family: {MONO};
        font-size: 11px;
        color: {C['text_mute']};
    }}
    .WStatVal {{
        font-family: {MONO};
        font-size: 11px;
        color: {accent};
    }}

    /* ---------- Footer ---------- */
    #Footer {{
        background: {C['bg_1']};
        border-top: 1px solid {C['line']};
    }}
    .Donations {{
        font-family: {MONO};
        font-size: 12px;
        color: {C['text_mute']};
        letter-spacing: 1px;
    }}
    .DonationsLabel {{ color: {C['text_dim']}; }}
    .DonationsAddr {{ color: {accent}; }}

    /* ---------- Modal ---------- */
    QDialog {{ background: {C['bg_1']}; border: 1px solid {C['line_2']}; }}
    .ModalHeader {{ background: {C['bg_1']}; border-bottom: 1px solid {C['line']}; }}
    .ModalTitle {{
        font-family: {MONO};
        font-size: 14px;
        font-weight: 700;
        color: {accent};
        letter-spacing: 2px;
    }}
    .ModalFooter {{ background: {C['bg_1']}; border-top: 1px solid {C['line']}; }}

    /* ---------- Slider ---------- */
    QSlider::groove:horizontal {{
        height: 4px;
        background: {C['bg_3']};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {accent};
        width: 14px;
        height: 14px;
        border-radius: 7px;
        margin: -5px 0;
    }}
    QSlider::sub-page:horizontal {{
        background: {accent};
        border-radius: 2px;
    }}

    /* ---------- Section divider (modals) ---------- */
    .SectionDivider {{
        font-family: {MONO};
        font-size: 11px;
        color: {accent};
        letter-spacing: 2px;
        font-weight: 600;
    }}
    .DividerLine {{ background: {C['line']}; }}

    /* ---------- Fish tile ---------- */
    .FishTile {{
        background: {C['bg_2']};
        border: 1px solid {C['line']};
        border-radius: 8px;
    }}
    .FishTile:hover {{ border: 1px solid {C['line_2']}; }}
    .FishImg {{
        background: {C['bg_1']};
        border: 1px solid {C['line']};
        border-radius: 6px;
    }}
    .FishImgKeep {{ border: 2px solid rgba(74,222,128,0.95); }}
    .FishImgDrop {{ border: 2px solid rgba(239,94,94,0.95); }}
    .FishImgOpen {{ border: 2px solid rgba(90,169,255,0.95); }}
    .FishImgUse {{ border: 2px solid rgba(180,108,242,0.95); }}
    .FishName {{
        font-family: {MONO};
        font-size: 13px;
        color: {C['text']};
    }}

    /* ---------- Segmented (K/D/O) ---------- */
    .Seg {{
        background: {C['bg_1']};
        border: 1px solid {C['line_2']};
        border-radius: 6px;
    }}
    QPushButton.SegBtn {{
        background: transparent;
        border: none;
        border-right: 1px solid {C['line']};
        font-family: {MONO};
        font-size: 11px;
        font-weight: 700;
        color: {C['text_mute']};
        padding: 4px 0;
    }}
    QPushButton.SegBtn:hover {{ color: {C['text']}; background: {C['bg_3']}; }}
    QPushButton.SegBtn[active="keep"]  {{ background: rgba(74,222,128,0.18); color: {C['green']}; }}
    QPushButton.SegBtn[active="drop"]  {{ background: rgba(239,94,94,0.18); color: {C['red']}; }}
    QPushButton.SegBtn[active="open"]  {{ background: rgba(90,169,255,0.18); color: {C['blue']}; }}
    QPushButton.SegBtn[active="use"]   {{ background: rgba(180,108,242,0.18); color: {C['purple']}; }}

    /* ---------- Wizard ---------- */
    .WizStep {{ font-family: {MONO}; font-size: 11px; color: {C['text_mute']}; letter-spacing: 1px; }}
    .WizStep[state="active"] {{ color: {accent}; }}
    .WizStep[state="done"]   {{ color: {C['green']}; }}
    .WizStep[state="missing"] {{ color: {C['orange']}; }}
    .WizNum {{
        background: {C['bg_3']};
        border: 1px solid {C['line_2']};
        border-radius: 11px;
        font-family: {MONO};
        font-size: 11px;
        font-weight: 700;
        color: {C['text']};
    }}
    .WizNum[state="active"] {{ background: {accent}; color: {on_accent}; border: 1px solid {accent}; }}
    .WizNum[state="done"]   {{ background: rgba(74,222,128,0.15); color: {C['green']}; border: 1px solid rgba(74,222,128,0.4); }}
    .WizNum[state="missing"] {{ background: transparent; color: {C['orange']}; border: 1px solid {C['orange']}; }}
    .WizLine {{ background: {C['line_2']}; }}

    /* ---------- Swatches ---------- */
    .Swatch {{
        border: 2px solid transparent;
        border-radius: 4px;
    }}
    .Swatch[active="true"] {{
        border: 2px solid white;
    }}

    /* ---------- Shared text styles (instead of inline styles) ---------- */
    .Hint {{ font-family: {MONO}; font-size: 11px; color: {C['text_mute']}; }}
    .HintDim {{ font-family: {MONO}; font-size: 12px; color: {C['text_dim']}; }}
    .AccentText {{ font-family: {MONO}; font-size: 12px; color: {accent}; }}
    .RowStat {{ font-family: {MONO}; font-size: 11px; color: {accent}; }}
    .RowMsg {{ font-family: {MONO}; font-size: 11px; color: {C['text_mute']}; }}
    .RowMsg[kind="error"] {{ color: {C['red']}; }}
    .Badge {{
        font-family: {MONO}; font-size: 10px; font-weight: 700; letter-spacing: 1px;
        color: {C['text_mute']}; border: 1px solid {C['line_2']}; border-radius: 8px; padding: 1px 7px;
    }}
    .WizTitle {{ font-family: {MONO}; font-size: 16px; font-weight: 700; color: {accent}; }}
    .WizBody {{ font-family: {MONO}; font-size: 12px; color: {C['text_dim']}; }}

    /* ---------- Dashboard action strip / stats / log ---------- */
    #ActionStrip {{
        background: {C['bg_2']};
        border: 1px solid {C['line']};
        border-radius: 8px;
    }}
    .StatusBig {{ font-family: {MONO}; font-size: 14px; font-weight: 700; color: {C['text']}; }}
    .StatusBig[kind="running"] {{ color: {C['green']}; }}
    .StatusBig[kind="paused"] {{ color: {C['orange']}; }}
    .StatusBig[kind="warn"] {{ color: {C['red']}; }}
    .StatusSub {{ font-family: {MONO}; font-size: 11px; color: {C['text_mute']}; }}
    #StatsStrip {{ background: transparent; }}
    .Stat {{ background: {C['bg_2']}; border: 1px solid {C['line']}; border-radius: 6px; }}
    .StatLabel {{ font-family: {MONO}; font-size: 11px; color: {C['text_mute']}; letter-spacing: 1px; }}
    .StatValue {{ font-family: {MONO}; font-size: 14px; font-weight: 700; color: {accent}; }}
    #Notice {{
        background: rgba(255,156,90,0.08);
        border: 1px solid rgba(255,156,90,0.45);
        border-radius: 6px;
    }}
    #Notice[kind="error"] {{ background: rgba(239,94,94,0.08); border: 1px solid rgba(239,94,94,0.5); }}
    #Notice[kind="info"] {{ background: rgba(90,169,255,0.07); border: 1px solid rgba(90,169,255,0.4); }}
    .NoticeText {{ font-family: {MONO}; font-size: 12px; color: {C['text']}; }}
    #CaptureBanner {{
        background: rgba(255,156,90,0.10);
        border: 1px solid {C['orange']};
        border-radius: 6px;
    }}

    /* ---------- Setup wizard button (highlighted until first opened) ---------- */
    QPushButton.WizardBtn {{
        background: transparent;
        color: {C['text_dim']};
        border: 1px solid {C['line_2']};
        font-family: {MONO};
        font-size: 12px;
        padding: 6px 12px;
        border-radius: 6px;
        letter-spacing: 1px;
    }}
    QPushButton.WizardBtn:hover {{ background: {C['bg_3']}; color: {C['text']}; }}
    QPushButton.WizardBtn[highlight="on"] {{
        background: {accent}; color: {on_accent}; border: 1px solid {accent_deep}; font-weight: 700;
    }}
    QPushButton.WizardBtn[highlight="dim"] {{
        background: {C['bg_3']}; color: {accent}; border: 2px solid {accent}; font-weight: 700;
    }}

    /* ---------- Native dialogs (message boxes, file / color pickers) ---------- */
    QMessageBox, QFileDialog, QColorDialog, QInputDialog {{ background: {C['bg_1']}; }}
    QMessageBox QLabel {{ color: {C['text']}; font-family: {SANS}; font-size: 13px; }}
    QMessageBox QPushButton, QDialogButtonBox QPushButton, QFileDialog QPushButton,
    QColorDialog QPushButton {{
        background: {C['bg_3']};
        color: {C['text']};
        border: 1px solid {C['line_2']};
        border-radius: 5px;
        padding: 5px 14px;
        min-width: 70px;
    }}
    QMessageBox QPushButton:hover, QDialogButtonBox QPushButton:hover, QFileDialog QPushButton:hover,
    QColorDialog QPushButton:hover {{ background: {C['bg_4']}; }}
    QMessageBox QPushButton:default, QDialogButtonBox QPushButton:default {{ border: 1px solid {accent}; }}
    QFileDialog QTreeView, QFileDialog QListView {{ background: {C['bg_2']}; color: {C['text']}; }}

    /* ---------- KBD ---------- */
    .Kbd {{
        font-family: {MONO};
        font-size: 10px;
        background: {C['bg_3']};
        border: 1px solid {C['line_2']};
        border-radius: 3px;
        color: {C['text_dim']};
        padding: 1px 5px;
    }}
    """


# ============================================================================
# Icon helpers (SVG → QIcon)
# ============================================================================

ICONS_SVG: Dict[str, str] = {
    "play":       '<path d="M6 4l14 8-14 8V4z" fill="{c}"/>',
    "stop":       '<rect x="6" y="6" width="12" height="12" rx="1" fill="{c}"/>',
    "pause":      '<rect x="6" y="5" width="4" height="14" fill="{c}"/><rect x="14" y="5" width="4" height="14" fill="{c}"/>',
    "refresh":    '<polyline points="23 4 23 10 17 10" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><polyline points="1 20 1 14 7 14" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "reset":      '<path d="M3 12a9 9 0 109-9 9.75 9.75 0 00-6.74 2.74L3 8" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M3 3v5h5" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "settings":   '<circle cx="12" cy="12" r="3" fill="none" stroke="{c}" stroke-width="1.6"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "check":      '<polyline points="20 6 9 17 4 12" fill="none" stroke="{c}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    "x":          '<line x1="18" y1="6" x2="6" y2="18" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/><line x1="6" y1="6" x2="18" y2="18" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/>',
    "minus":      '<line x1="5" y1="12" x2="19" y2="12" stroke="{c}" stroke-width="1.6"/>',
    "square":     '<rect x="4" y="4" width="14" height="14" rx="1" fill="none" stroke="{c}" stroke-width="1.6"/>',
    "fish":       '<path d="M2 12s2-5 8-5 10 5 10 5-4 5-10 5-8-5-8-5z" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="17" cy="11" r="0.8" fill="{c}"/>',
    "grid":       '<rect x="4" y="5" width="16" height="14" rx="1" fill="none" stroke="{c}" stroke-width="1.6"/><path d="M9.33 5v14M14.67 5v14M4 8.5h16M4 12h16M4 15.5h16" fill="none" stroke="{c}" stroke-width="1.2" stroke-linecap="round"/>',
    "package":    '<path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "dashboard":  '<rect x="3" y="3" width="7" height="9" fill="none" stroke="{c}" stroke-width="1.6"/><rect x="14" y="3" width="7" height="5" fill="none" stroke="{c}" stroke-width="1.6"/><rect x="14" y="12" width="7" height="9" fill="none" stroke="{c}" stroke-width="1.6"/><rect x="3" y="16" width="7" height="5" fill="none" stroke="{c}" stroke-width="1.6"/>',
    "help":       '<circle cx="12" cy="12" r="10" fill="none" stroke="{c}" stroke-width="1.6"/><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><line x1="12" y1="17" x2="12.01" y2="17" stroke="{c}" stroke-width="1.6" stroke-linecap="round"/>',
    "save":       '<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><polyline points="17 21 17 13 7 13 7 21" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><polyline points="7 3 7 8 15 8" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "folder":     '<path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "target":     '<circle cx="12" cy="12" r="10" fill="none" stroke="{c}" stroke-width="1.6"/><circle cx="12" cy="12" r="6" fill="none" stroke="{c}" stroke-width="1.6"/><circle cx="12" cy="12" r="2" fill="none" stroke="{c}" stroke-width="1.6"/>',
    "clock":      '<circle cx="12" cy="12" r="10" fill="none" stroke="{c}" stroke-width="1.6"/><polyline points="12 6 12 12 16 14" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "monitor":    '<rect x="2" y="3" width="20" height="14" rx="2" fill="none" stroke="{c}" stroke-width="1.6"/><line x1="8" y1="21" x2="16" y2="21" stroke="{c}" stroke-width="1.6"/><line x1="12" y1="17" x2="12" y2="21" stroke="{c}" stroke-width="1.6"/>',
    "chevron-right": '<polyline points="9 18 15 12 9 6" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    "wand":       '<path d="M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8L19 13M15 9h0M17.8 6.2L19 5M3 21l9-9M12.2 6.2L11 5" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "copy":       '<rect x="9" y="9" width="13" height="13" rx="2" fill="none" stroke="{c}" stroke-width="1.6"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
}


_QSS_IMAGE_DIR: Optional[str] = None


def qss_icon_file(name: str, color: str, size: int = 12) -> str:
    """A PNG of an ICONS_SVG icon for a stylesheet ``image: url(...)``
    (stylesheets only take files). Written once per icon and color to a
    temporary folder removed at exit, with an @2x variant for hi-dpi screens.
    Returns the path with forward slashes, or "" when it cannot be written."""
    global _QSS_IMAGE_DIR
    try:
        if _QSS_IMAGE_DIR is None:
            import atexit
            import shutil
            import tempfile
            _QSS_IMAGE_DIR = tempfile.mkdtemp(prefix="fishbot-ui-")
            atexit.register(shutil.rmtree, _QSS_IMAGE_DIR, True)
        base = os.path.join(_QSS_IMAGE_DIR, f"{name}-{color.lstrip('#')}-{size}")
        path = base + ".png"
        if not os.path.exists(path):
            body = ICONS_SVG[name].format(c=color)
            for px, file in ((size, path), (size * 2, base + "@2x.png")):
                svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" '
                       f'viewBox="0 0 24 24">{body}</svg>')
                img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
                img.fill(Qt.transparent)
                p = QPainter(img)
                QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p)
                p.end()
                if not img.save(file, "PNG"):
                    return ""
        return path.replace("\\", "/")
    except Exception:
        diag.exc()
        return ""


def plain_letter_icon(name: str) -> QIcon:
    """A plain icon (first letter on a gray rounded square), for the
    disguised window: the monkey would give the program away."""
    icon = QIcon()
    for px in (16, 32, 48, 64):
        pix = QPixmap(px, px)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#5b6472"))
        p.drawRoundedRect(0, 0, px, px, px * 0.22, px * 0.22)
        font = p.font()
        font.setBold(True)
        font.setPixelSize(int(px * 0.62))
        p.setFont(font)
        p.setPen(QColor("#f2f4f7"))
        p.drawText(pix.rect(), Qt.AlignCenter, (name.strip()[:1] or "A").upper())
        p.end()
        icon.addPixmap(pix)
    return icon


def name_icon_buttons(root: QWidget) -> None:
    """Gives every icon-only button under ``root`` its tooltip as the
    accessible name, so a screen reader says "Close" instead of nothing."""
    for b in root.findChildren(QAbstractButton):
        if not b.text().strip() and not b.accessibleName() and b.toolTip():
            b.setAccessibleName(b.toolTip())


def make_icon(name: str, size: int = 14, color: str = C["text"]) -> QIcon:
    body = ICONS_SVG.get(name)
    if not body:
        return QIcon()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24">{body.format(c=color)}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size * 2, size * 2)  # 2× for sharpness on hi-dpi
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    renderer.render(p)
    p.end()
    return QIcon(pix)


def icon_label(name: str, size: int = 14, color: str = C["text"]) -> QLabel:
    body = ICONS_SVG.get(name)
    if not body:
        return QLabel()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24">{body.format(c=color)}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    renderer.render(p)
    p.end()
    lbl = QLabel()
    lbl.setPixmap(pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    lbl.setFixedSize(size, size)
    return lbl


# ============================================================================
# Reusable widgets
# ============================================================================

class Pill(QLabel):
    """Status pill with colored dot — e.g. READY / RUNNING / EMPTY."""

    KIND_STYLE = {
        "idle":   ("Pill", C["text_mute"]),
        "active": ("Pill PillActive", C["green"]),
        "warn":   ("Pill PillWarn", C["orange"]),
        "error":  ("Pill PillError", C["red"]),
    }

    def __init__(self, text: str, kind: str = "idle", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setText("● " + text)
        self.setKind(kind)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(22)

    def setKind(self, kind: str) -> None:
        cls, _ = self.KIND_STYLE.get(kind, self.KIND_STYLE["idle"])
        self.setProperty("class", cls)
        self.style().unpolish(self)
        self.style().polish(self)


class Card(QFrame):
    """Bordered card with a uppercase mono title and an optional action area."""

    def __init__(self, title: Optional[str] = None, parent: Optional[QWidget] = None,
                 dot: bool = True):
        super().__init__(parent)
        self.setProperty("class", "Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._action_area: Optional[QHBoxLayout] = None

        if title:
            header = QFrame()
            header.setProperty("class", "CardHeader")
            hl = QHBoxLayout(header)
            hl.setContentsMargins(12, 8, 10, 8)
            hl.setSpacing(8)
            if dot:
                d = QFrame()
                d.setProperty("class", "CardDot")
                d.setFixedSize(6, 6)
                hl.addWidget(d)
            t = QLabel(title.upper())
            t.setProperty("class", "CardTitle")
            hl.addWidget(t)
            hl.addStretch(1)
            actions = QWidget()
            self._action_area = QHBoxLayout(actions)
            self._action_area.setContentsMargins(0, 0, 0, 0)
            self._action_area.setSpacing(6)
            hl.addWidget(actions)
            outer.addWidget(header)

        body = QFrame()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(12, 10, 12, 10)
        self.body_layout.setSpacing(8)
        outer.addWidget(body)

    def addAction(self, widget: QWidget) -> None:
        if self._action_area is None:
            return
        self._action_area.addWidget(widget)

    def add(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)


class KPI(QFrame):
    """One compact live number of the dashboard's stats strip: LABEL value.
    The explanation is the tooltip."""

    def __init__(self, label: str, value: str = "0", sub: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "Stat")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)
        self._label = QLabel(label.upper())
        self._label.setProperty("class", "StatLabel")
        self._value = QLabel(str(value))
        self._value.setProperty("class", "StatValue")
        layout.addWidget(self._label)
        layout.addStretch(1)
        layout.addWidget(self._value)
        self.setToolTip(sub)

    def setValue(self, v) -> None:
        self._value.setText(str(v))

    def setLabel(self, label: str, sub: str = "") -> None:
        self._label.setText(label.upper())
        self.setToolTip(sub)


class ReadyChips(QWidget):
    """'Ready to start' checklist next to the Start button: one chip per thing
    the selected activity needs. A chip that is not ok can be clicked; it
    emits its key so the window can open the place that fixes it."""

    fix_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        cap = QLabel("READY TO START")
        cap.setProperty("class", "ColHead")
        self._layout.addWidget(cap)
        self._layout.addStretch(1)
        self.chips: Dict[str, QPushButton] = {}

    def setItems(self, items: List[Tuple[str, str, str, str]]) -> None:
        """``items``: (key, label, state, tooltip); state is ok / bad / wait.
        Chips are updated in place; they are rebuilt only when the set of
        keys changes (activity switched)."""
        if [k for k, *_ in items] != list(self.chips):
            for chip in self.chips.values():
                self._layout.removeWidget(chip)
                chip.hide()                 # deleteLater is deferred: never leave it painted
                chip.deleteLater()
            self.chips = {}
            for pos, (key, *_rest) in enumerate(items):
                chip = QPushButton()
                chip.setProperty("class", "Chip")
                chip.clicked.connect(lambda _=False, k=key: self.fix_requested.emit(k))
                self._layout.insertWidget(1 + pos, chip)
                self.chips[key] = chip
        for key, label, state, tip in items:
            chip = self.chips[key]
            mark = {"ok": "✓", "bad": "✗", "wait": "…"}.get(state, "")
            chip.setText(f"{mark} {label}")
            chip.setToolTip(tip + ("" if state == "ok" else "\n(click to go there)"))
            chip.setCursor(Qt.PointingHandCursor if state != "ok" else Qt.ArrowCursor)
            if chip.property("state") != state:
                chip.setProperty("state", state)
                chip.style().unpolish(chip)
                chip.style().polish(chip)

    def setProgress(self, key: str, percent: Optional[int]) -> None:
        """Fills chip ``key`` from the left with green up to ``percent``
        (a small progress bar inside the chip); None: a plain chip."""
        chip = self.chips.get(key)
        if chip is None:
            return
        if percent is None:
            if chip.property("progress") is not None:
                chip.setProperty("progress", None)
                chip.setStyleSheet("")
            return
        p = max(0.0, min(1.0, percent / 100.0))
        if chip.property("progress") == round(p, 2):
            return
        chip.setProperty("progress", round(p, 2))
        edge = min(0.999, p + 0.001)
        chip.setStyleSheet(
            f"QPushButton {{ color: {C['green']}; border-color: rgba(74,222,128,0.55); "
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 rgba(74,222,128,0.38), stop:{p:.3f} rgba(74,222,128,0.38), "
            f"stop:{edge:.3f} {C['bg_2']}, stop:1 {C['bg_2']}); }}")

    def allOk(self) -> bool:
        return all(c.property("state") == "ok" for c in self.chips.values())


class CoordPill(QFrame):
    """A screen position to capture. Clicking the pill starts a capture (also
    when it is already set: that re-captures it); the small ✕ clears it.

    ``optional`` pills show a muted "not set"; mandatory ones a red one.
    ``setLocked`` greys the pill out with a reason in its tooltip (e.g. while
    the bot runs, or Fishing-only positions in Jigsaw mode)."""

    clicked = Signal()
    unset_requested = Signal()  # the ✕ was clicked

    def __init__(self, label: str, coords: Optional[Tuple[int, int]] = None,
                 optional: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "CoordPill")
        self.setProperty("set", "false")
        self.setProperty("capturing", "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)
        self._base_label = label
        self._optional = optional
        self._note = ""
        self._coords = None
        self._locked = False
        self._capturing = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(8)
        self._icon = QLabel()
        self._icon.setFixedSize(14, 14)
        layout.addWidget(self._icon)
        self._label = QLabel()
        self._label.setProperty("class", "CoordPillLabel")
        self._label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._label.setMinimumWidth(40)
        layout.addWidget(self._label, 1)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._coords_lbl = QLabel("not set")
        self._coords_lbl.setProperty("class", "CoordPillCoords")
        layout.addWidget(self._coords_lbl)
        self._clear_btn = QPushButton()
        self._clear_btn.setIcon(make_icon("x", 10, C["text_dim"]))
        self._clear_btn.setProperty("class", "BtnGhost")
        self._clear_btn.setFixedSize(20, 20)
        self._clear_btn.setStyleSheet("padding: 0; border: none;")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setToolTip("Clear this position")
        self._clear_btn.clicked.connect(self._on_clear)
        layout.addWidget(self._clear_btn)
        self._update_label()
        self.setCoords(coords)

    # -- state ---------------------------------------------------------------
    def isOptional(self) -> bool:
        return self._optional

    def coords(self) -> Optional[Tuple[int, int]]:
        return self._coords

    def setOptional(self, optional: bool, note: str = "") -> None:
        """``note`` replaces "Optional" in the label (e.g. "for Drop")."""
        self._optional = optional
        self._note = note
        self._update_label()
        self.setCoords(self._coords)

    def _update_label(self) -> None:
        # Optional pills say so by their muted "not set" (a suffix would not
        # fit four pills in a row); a note ("for drops") is shown.
        self._label.setText(self._base_label + (f" · {self._note}" if self._note else ""))
        self._label.setToolTip("Optional" if self._optional and not self._note else self._note)

    def mousePressEvent(self, ev) -> None:
        if self._locked:
            ev.accept()
            return
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)

    def _on_clear(self) -> None:
        if not self._locked and self._coords is not None:
            self.unset_requested.emit()

    def setCoords(self, coords: Optional[Tuple[int, int]]) -> None:
        self._coords = tuple(coords) if coords else None
        if self._coords:
            self.setProperty("set", "true")
            x, y = self._coords
            self._coords_lbl.setText(f"{x},{y}")
            self._coords_lbl.setStyleSheet(f"color: {C['green']};")
            self._set_icon("check", C["green"])
            self.setToolTip("Click to capture it again; ✕ clears it")
        else:
            self.setProperty("set", "false")
            color = C["red"] if not self._optional else C["text_mute"]
            self._coords_lbl.setText("not set")
            self._coords_lbl.setStyleSheet(f"color: {color};")
            self._set_icon("target", color)
            self.setToolTip("Click, then click this point in the game")
        self._clear_btn.setVisible(self._coords is not None and not self._capturing)
        if self._capturing:
            self.setCapturing(True)
        self.style().unpolish(self)
        self.style().polish(self)

    def setCapturing(self, capturing: bool) -> None:
        was = self._capturing
        self._capturing = capturing
        self.setProperty("capturing", "true" if capturing else "false")
        if capturing:
            self._coords_lbl.setText("click in game…")
            self._coords_lbl.setStyleSheet(f"color: {C['orange']};")
            self._set_icon("target", C["orange"])
            self._clear_btn.setVisible(False)
        elif was:
            self.setCoords(self._coords)
            return
        self.style().unpolish(self)
        self.style().polish(self)

    def setLocked(self, locked: bool, reason: str = "") -> None:
        """Greys the pill out and ignores clicks; ``reason`` is the tooltip."""
        self._locked = locked
        self._clear_btn.setEnabled(not locked)
        if locked:
            self.setCursor(Qt.ForbiddenCursor)
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.4)
            self.setGraphicsEffect(effect)
            if reason:
                self.setToolTip(reason)
        else:
            self.setCursor(Qt.PointingHandCursor)
            self.setGraphicsEffect(None)
            self.setCoords(self._coords)    # restores the tooltip

    def isLocked(self) -> bool:
        return self._locked

    def _set_icon(self, name: str, color: str) -> None:
        body = ICONS_SVG.get(name)
        if not body:
            return
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" '
            f'viewBox="0 0 24 24">{body.format(c=color)}</svg>'
        )
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        pix = QPixmap(28, 28)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        renderer.render(p)
        p.end()
        self._icon.setPixmap(pix.scaled(14, 14, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class Segmented(QWidget):
    """K / D / O segmented control."""

    changed = Signal(str)

    def __init__(self, options: List[Tuple[str, str]], value: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "Seg")
        self.setFixedHeight(24)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._buttons: Dict[str, QPushButton] = {}
        for value_key, label in options:
            b = QPushButton(label)
            b.setProperty("class", "SegBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, k=value_key: self._on(k))
            layout.addWidget(b, 1)
            self._buttons[value_key] = b
        self.setValue(value)

    def setValue(self, v: str) -> None:
        self._value = v
        for k, b in self._buttons.items():
            b.setProperty("active", k if k == v else "")
            b.style().unpolish(b)
            b.style().polish(b)

    def value(self) -> str:
        return self._value

    def _on(self, key: str) -> None:
        # A click on the active choice keeps it: there is no "nothing" state
        # (an empty action used to be saved and made the bot drop the fish).
        if self._value == key:
            self.setValue(key)    # the button stays drawn as the active one
            return
        self.setValue(key)
        self.changed.emit(key)


class NoticeBar(QFrame):
    """A dismissible one-line notice (config problems, save failures, platform
    limits) shown above the tabs' content instead of a blocking message box.
    Notices are keyed: showing a key again replaces its text."""

    action_clicked = Signal(str)   # key

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Notice")
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(8)
        self.text = QLabel("")
        self.text.setProperty("class", "NoticeText")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.text, 1)
        self.action_btn = QPushButton("")
        self.action_btn.setProperty("class", "BtnGhost")
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.clicked.connect(lambda: self.action_clicked.emit(self._key))
        layout.addWidget(self.action_btn)
        close = QPushButton()
        close.setIcon(make_icon("x", 11, C["text_dim"]))
        close.setProperty("class", "BtnGhost")
        close.setFixedSize(24, 24)
        close.setStyleSheet("padding: 0;")
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Dismiss")
        close.clicked.connect(self.dismiss)
        layout.addWidget(close)
        self._key = ""
        self._queue: List[Tuple[str, str, str, str]] = []

    def show_notice(self, key: str, text: str, kind: str = "warn", action: str = "") -> None:
        """The newest notice is shown; the one it covers comes back when
        this one is dismissed."""
        self._queue = [q for q in self._queue if q[0] != key]
        if not self.isHidden() and self._key and self._key != key:
            self._queue.insert(0, (self._key, self.text.text(), self.property("kind") or "warn",
                                   self.action_btn.text() if self.action_btn.isVisibleTo(self) else ""))
        self._show(key, text, kind, action)

    def keys(self) -> List[str]:
        """Every notice waiting or shown (newest first)."""
        return ([self._key] if self.current() else []) + [q[0] for q in self._queue]

    def _show(self, key: str, text: str, kind: str, action: str) -> None:
        self._key = key
        self.text.setText(text)
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.action_btn.setText(action)
        self.action_btn.setVisible(bool(action))
        self.setVisible(True)

    def current(self) -> str:
        return self._key if not self.isHidden() else ""

    def dismiss(self) -> None:
        if self._queue:
            self._show(*self._queue.pop(0))
            return
        self._key = ""
        self.setVisible(False)

    def clear(self, key: str) -> None:
        self._queue = [q for q in self._queue if q[0] != key]
        if self._key == key:
            self.dismiss()


# ============================================================================
# Header (frameless window chrome: icon, title, Discord, min / close)
# ============================================================================

class Header(QFrame):
    """The single top bar: drag it to move the (frameless) window."""

    minimize = Signal()
    close_window = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Header")
        self.setFixedHeight(44)
        self._drag_pos: Optional[QPoint] = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(10)

        self.gif = QLabel()
        self.gif.setFixedSize(30, 30)
        self.gif.setAlignment(Qt.AlignCenter)
        self.gif.setStyleSheet("background: #2a1808; border-radius: 6px; border: 1px solid #1a0f06; "
                               "font-size: 17px;")
        self.gif.setText("")
        layout.addWidget(self.gif)
        self._load_monkey()

        title = QLabel("Fishing Puzzle Player")
        title.setObjectName("HeaderTitle")
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("HeaderVersion")
        layout.addWidget(title)
        layout.addWidget(ver)
        layout.addStretch(1)

        self._discord_pill = QPushButton("DISCORD SUPPORT")
        self._discord_pill.setObjectName("DiscordBtn")
        self._discord_pill.setFixedHeight(24)
        self._discord_pill.setCursor(Qt.PointingHandCursor)
        self._discord_pill.setToolTip(f"Join the support server: {DISCORD_INVITE_URL}")
        self._discord_pill.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(DISCORD_INVITE_URL))
        )
        layout.addWidget(self._discord_pill)
        layout.addSpacing(4)

        for ico_name, slot, obj_name, tip in [
            ("minus", self.minimize.emit, "TbBtn", "Minimize"),
            ("x", self.close_window.emit, "TbClose", "Close (asks before stopping a running bot)"),
        ]:
            btn = QPushButton()
            btn.setIcon(make_icon(ico_name, 12, C["text_dim"]))
            btn.setIconSize(QSize(12, 12))
            btn.setObjectName(obj_name)
            btn.setProperty("class", obj_name)
            btn.setFixedSize(40, 44)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def _load_monkey(self) -> None:
        path = get_resource_path("monkey-eating.gif")
        if not os.path.exists(path):
            return
        movie = QMovie(path, parent=self)
        movie.setScaledSize(QSize(30, 30))
        self.gif.setText("")
        self.gif.setMovie(movie)
        movie.start()

    # Drag-to-move (the window is frameless)
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self.window():
            self._drag_pos = ev.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_pos and ev.buttons() & Qt.LeftButton and self.window():
            self.window().move(ev.globalPosition().toPoint() - self._drag_pos)
            ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        self._drag_pos = None


# ============================================================================
# Tabs bar
# ============================================================================

class TabsBar(QFrame):
    changed = Signal(str)

    def __init__(self, items: List[Tuple[str, str, str]], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("TabsBar")
        self.setFixedHeight(38)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(2)
        self._buttons: Dict[str, QPushButton] = {}
        self._icons: Dict[str, str] = {tab_id: icon for tab_id, icon, _ in items}
        self._active = items[0][0] if items else ""
        for tab_id, icon, label in items:
            btn = QPushButton(f"  {label.upper()}")
            btn.setIcon(make_icon(icon, 13, C["text_mute"]))
            btn.setIconSize(QSize(13, 13))
            btn.setProperty("class", "TabBtn")
            btn.setProperty("active", "false")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, t=tab_id: self.setActive(t))
            layout.addWidget(btn)
            self._buttons[tab_id] = btn
        layout.addStretch(1)
        # Which activity the tabs belong to (set by the window)
        self.mode_tag = QLabel("")
        self.mode_tag.setObjectName("ModeTag")
        layout.addWidget(self.mode_tag, 0, Qt.AlignVCenter)
        if items:
            self.setActive(items[0][0])

    def setModeTag(self, mode: str) -> None:
        self.mode_tag.setText(f"● {MODE_TAGS[mode]}")
        self.mode_tag.setToolTip("The Fishing / Jigsaw tab and the Dashboard belong to this activity. "
                                 "Switch with the buttons above.")
        if self.mode_tag.property("mode") != mode:
            self.mode_tag.setProperty("mode", mode)
            self.mode_tag.style().unpolish(self.mode_tag)
            self.mode_tag.style().polish(self.mode_tag)

    def active(self) -> str:
        return self._active

    def setTabLabel(self, tab_id: str, label: str, icon: str) -> None:
        btn = self._buttons.get(tab_id)
        if btn is None:
            return
        self._icons[tab_id] = icon
        btn.setText(f"  {label.upper()}")
        btn.setIcon(make_icon(icon, 13, C["accent"] if tab_id == self._active else C["text_mute"]))

    def recolor(self) -> None:
        """Re-renders the icons after an accent change."""
        self.setActive(self._active, emit=False)

    def setActive(self, tab_id: str, emit: bool = True) -> None:
        self._active = tab_id
        for tid, btn in self._buttons.items():
            active = tid == tab_id
            btn.setProperty("active", "true" if active else "false")
            icon_color = C["accent"] if active else C["text_mute"]
            ico_name = self._icons.get(tid, "")
            if ico_name:
                btn.setIcon(make_icon(ico_name, 13, icon_color))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if emit:
            self.changed.emit(tab_id)


# ============================================================================
# Mode bar — chooses the activity (Fishing or Jigsaw Puzzle)
# ============================================================================

class ModeBar(QFrame):
    """The activity switch (Fishing | Jigsaw Puzzle) and, on the right, the
    setup wizard of the selected activity. While one activity runs the other
    button is disabled and the hint says why. The wizard button is
    highlighted (pulsing) until the user has opened that activity's wizard
    once."""

    mode_changed = Signal(str)
    wizard_requested = Signal()

    IDLE_HINT = ""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("ModeBar")
        self.setFixedHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(8)
        self._buttons: Dict[str, QPushButton] = {}
        for mode, text, tip in (
            (MODE_FISHING, "FISHBOT",
             "Cast, play the fishing minigame and sort the catch"),
            (MODE_JIGSAW, "JIGSAW PUZZLE SOLVER",
             "Open fish puzzle crates and solve the Fishing Jigsaw boards"),
        ):
            btn = QPushButton(text)
            btn.setProperty("class", "ModeBtn")
            btn.setProperty("mode", mode)
            btn.setProperty("active", "false")
            btn.setMinimumWidth(170)
            btn.setFixedHeight(34)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip + "\n\nWindows, inventory pages and Confirm are shared by both.")
            btn.clicked.connect(lambda _=False, m=mode: self._on_click(m))
            layout.addWidget(btn)
            self._buttons[mode] = btn
        layout.addSpacing(6)
        # Empty while idle; says why the other activity is locked while one runs.
        self.hint = QLabel("")
        self.hint.setProperty("class", "ModeHint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint, 1)

        self.wizard_btn = QPushButton("")
        self.wizard_btn.setProperty("class", "WizardBtn")
        self.wizard_btn.setProperty("highlight", "off")
        self.wizard_btn.setCursor(Qt.PointingHandCursor)
        self.wizard_btn.setFixedHeight(34)
        self.wizard_btn.clicked.connect(self.wizard_requested.emit)
        layout.addWidget(self.wizard_btn)
        self._highlight = False
        self._pulse_on = True
        self._pulse = QTimer(self)
        self._pulse.setInterval(650)
        self._pulse.timeout.connect(self._tick)

        self._mode = MODE_FISHING
        self._running: Optional[str] = None
        self._refresh()

    def mode(self) -> str:
        return self._mode

    def setMode(self, mode: str) -> None:
        """Selects ``mode`` without emitting (the window applies it itself)."""
        if mode not in self._buttons:
            mode = MODE_FISHING
        self._mode = mode
        self._refresh()

    def setRunning(self, running_mode: Optional[str]) -> None:
        """``running_mode`` is the activity that runs now (None when idle)."""
        self._running = running_mode
        self._refresh()

    def setWizardHighlighted(self, on: bool) -> None:
        """Pulses the wizard button until the user opens it."""
        self._highlight = on
        if on and not self._pulse.isActive():
            self._pulse_on = True
            self._pulse.start()
        elif not on:
            self._pulse.stop()
        self._apply_highlight()

    def wizardHighlighted(self) -> bool:
        return self._highlight

    def _tick(self) -> None:
        self._pulse_on = not self._pulse_on
        self._apply_highlight()

    def _apply_highlight(self) -> None:
        state = "off" if not self._highlight else ("on" if self._pulse_on else "dim")
        if self.wizard_btn.property("highlight") != state:
            self.wizard_btn.setProperty("highlight", state)
            self.wizard_btn.style().unpolish(self.wizard_btn)
            self.wizard_btn.style().polish(self.wizard_btn)

    def _on_click(self, mode: str) -> None:
        if self._running is not None or mode == self._mode:
            self._refresh()     # keep the checked look in sync
            return
        self._mode = mode
        self._refresh()
        self.mode_changed.emit(mode)

    def _refresh(self) -> None:
        if self.property("mode") != self._mode:
            self.setProperty("mode", self._mode)
            self.style().unpolish(self)
            self.style().polish(self)
        for mode, btn in self._buttons.items():
            active = mode == self._mode
            btn.setProperty("active", "true" if active else "false")
            btn.setEnabled(self._running is None or mode == self._running)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        jig = self._mode == MODE_JIGSAW
        self.wizard_btn.setText("  Jigsaw setup" if jig else "  Fishing setup")
        self.wizard_btn.setIcon(make_icon("wand", 12, C["text"]))
        self.wizard_btn.setToolTip(
            f"Step-by-step setup for {MODE_NAMES[self._mode]}: windows, positions and options")
        if self._running is not None:
            other = MODE_NAMES[MODE_JIGSAW if self._running == MODE_FISHING else MODE_FISHING]
            self.hint.setText(f"{MODE_NAMES[self._running]} is running — stop it to switch to {other}.")
            self.hint.setProperty("class", "ModeHintLocked")
        else:
            self.hint.setText(self.IDLE_HINT)
            self.hint.setProperty("class", "ModeHint")
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)


# ============================================================================
# Position capture controller (pynput)
# ============================================================================

class PositionCaptureSignals(QObject):
    captured = Signal(int, int, str)  # screen_x, screen_y, mode
    failed = Signal(str)
    cancelled = Signal()              # right click while capturing


class PositionCaptureController(QObject):
    """Waits (pynput, any window) for the next left click and reports its
    screen position. A right click cancels; Esc is handled by the window's
    global key listener, the timeout by the window."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.signals = PositionCaptureSignals()
        self._listener = None
        self._mode: Optional[str] = None

    def is_active(self) -> bool:
        return self._listener is not None

    def mode(self) -> Optional[str]:
        return self._mode

    def start(self, mode: str) -> bool:
        if pyn_mouse is None:
            self.signals.failed.emit("pynput not installed")
            return False
        self.cancel()
        self._mode = mode

        def on_click(x, y, button, pressed):
            if not pressed:
                return None
            if button == pyn_mouse.Button.left:
                self.signals.captured.emit(int(x), int(y), mode)
                return False
            if button == pyn_mouse.Button.right:
                self.signals.cancelled.emit()
                return False
            return None
        self._listener = pyn_mouse.Listener(on_click=on_click)
        self._listener.start()
        return True

    def cancel(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                diag.exc()
                pass
        self._listener = None
        self._mode = None


# ============================================================================
# Dashboard tab
# ============================================================================

class DashboardTab(QWidget):
    """Top: the action strip (Start / Pause, Stop, what runs, what is still
    missing) — always in view. Below: the live numbers and the eight game
    window slots."""

    REFRESH_TIP = "Look for game windows again (after starting a client)"
    RESET_TIP = "Sets every window's bait count back to full (after you restocked bait)"

    start_clicked = Signal()
    stop_clicked = Signal()   # pause/resume toggle (primary button when running)
    hard_stop = Signal()      # full stop (red STOP button)
    refresh_windows = Signal()
    reset_bait = Signal()
    window_changed = Signal(int, str)
    help_requested = Signal(str, str)   # a row's message was clicked: (topic, message)

    CLICK_GUARD_S = 0.6       # a double click on START must not start and then pause

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- Action strip -----------------------------------------------------
        strip = QFrame()
        strip.setObjectName("ActionStrip")
        sl = QVBoxLayout(strip)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.start_btn = QPushButton("  START FISHING")
        self.start_btn.setProperty("class", "BtnPrimary BtnLarge")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setMinimumHeight(42)
        self.start_btn.setMinimumWidth(190)
        self.start_btn.clicked.connect(self._on_main)
        top.addWidget(self.start_btn, 0)

        self.stop_btn = QPushButton("  STOP")
        self.stop_btn.setProperty("class", "BtnDanger BtnLarge")
        self.stop_btn.setIcon(make_icon("stop", 14, C["red"]))
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setMinimumHeight(42)
        self.stop_btn.setMinimumWidth(110)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._on_stop)
        top.addWidget(self.stop_btn, 0)

        status_col = QVBoxLayout()
        status_col.setSpacing(2)
        status_col.setContentsMargins(10, 0, 0, 0)
        self.status_big = QLabel("")
        self.status_big.setProperty("class", "StatusBig")
        self.status_sub = QLabel("")
        self.status_sub.setProperty("class", "StatusSub")
        self.status_sub.setWordWrap(True)
        status_col.addWidget(self.status_big)
        status_col.addWidget(self.status_sub)
        top.addLayout(status_col, 1)
        top.addLayout(self._kbd_row("F5", "pause / resume"))
        sl.addLayout(top)
        self.ready_chips = ReadyChips()
        sl.addWidget(self.ready_chips)
        layout.addWidget(strip)

        # --- Live numbers -----------------------------------------------------
        stats = QFrame()
        stats.setObjectName("StatsStrip")
        st = QHBoxLayout(stats)
        st.setContentsMargins(0, 0, 0, 0)
        st.setSpacing(8)
        self.kpi_games = KPI("Games", "0")
        self.kpi_active = KPI("Running", "0/0")
        self.kpi_bait = KPI("Bait left", "0")
        self.kpi_cap = KPI("Per window", "0")
        for k in (self.kpi_games, self.kpi_active, self.kpi_bait, self.kpi_cap):
            st.addWidget(k, 1)
        layout.addWidget(stats)

        # --- Windows (scrolls; the action strip stays in view) ---------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(10)

        windows_card = Card("Game Windows")
        self.refresh_btn = refresh_btn = QPushButton("  Refresh")
        refresh_btn.setProperty("class", "BtnGhost")
        refresh_btn.setIcon(make_icon("refresh", 12, C["text"]))
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setToolTip(self.REFRESH_TIP)
        refresh_btn.clicked.connect(self.refresh_windows.emit)
        self.reset_btn = QPushButton("  Reset Bait")
        self.reset_btn.setProperty("class", "BtnGhost")
        self.reset_btn.setIcon(make_icon("reset", 12, C["text"]))
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.setToolTip(self.RESET_TIP)
        self.reset_btn.clicked.connect(self.reset_bait.emit)
        windows_card.addAction(refresh_btn)
        windows_card.addAction(self.reset_btn)
        windows_card.body_layout.setSpacing(2)
        windows_card.body_layout.setContentsMargins(10, 8, 10, 8)
        self.window_rows: List[WindowRow] = []
        for i in range(MAX_WINDOWS):
            row = WindowRow(i)
            row.window_changed.connect(self._on_row_changed)
            row.help_requested.connect(self.help_requested)
            windows_card.body_layout.addWidget(row)
            self.window_rows.append(row)
        il.addWidget(windows_card)

        il.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        self._running = False
        self._paused = False
        self._last_click = 0.0
        self._mode = MODE_FISHING
        self.setMode(MODE_FISHING)

    # -- rows ------------------------------------------------------------------
    def _on_row_changed(self, index: int, name: str) -> None:
        self.window_changed.emit(index, name)

    # -- mode / state ------------------------------------------------------------
    def setMode(self, mode: str) -> None:
        """Relabels the dashboard for the selected activity."""
        self._mode = mode
        if mode == MODE_JIGSAW:
            self.kpi_games.setLabel("Crates", "Crates opened, all windows")
            self.kpi_bait.setLabel("Boards", "Puzzle boards solved, all windows")
            self.kpi_cap.setLabel("Solver", "The solver's move table (built once on this PC)")
            names = ("Crates", "Boards", "Crates opened", "Puzzle boards completed")
        else:
            self.kpi_games.setLabel("Games", "Fishing minigames played, all windows")
            self.kpi_bait.setLabel("Bait left", "Estimated bait left, all selected windows. The bot "
                                   "counts it down per cast; it is not read from the game. "
                                   "Reset Bait after restocking.")
            self.kpi_cap.setLabel("Per window", "Bait per window when full: selected bait keys × "
                                  "bait quantity (FISHING tab)")
            names = ("Bait", "Games", "Estimated bait left (counted down per cast)", "Minigames played")
        self.kpi_active.setLabel("Running", "Windows with a running bot / selected windows")
        for row in self.window_rows:
            row.setStatNames(*names)
        if not self._running:
            self.setRunning(False)

    def _kbd_row(self, key: str, text: str) -> QHBoxLayout:
        l = QHBoxLayout()
        l.setSpacing(6)
        l.setContentsMargins(0, 0, 0, 0)
        kbd = QLabel(key)
        kbd.setProperty("class", "Kbd")
        kbd.setFixedHeight(22)
        kbd.setAlignment(Qt.AlignCenter)
        txt = QLabel(text)
        txt.setProperty("class", "Hint")
        l.addWidget(kbd)
        l.addWidget(txt)
        return l

    def _guarded(self) -> bool:
        """True when this click comes too soon after the last one (the second
        half of a double click): it is ignored."""
        now = time.monotonic()
        if now - self._last_click < self.CLICK_GUARD_S:
            return True
        self._last_click = now
        return False

    def _on_main(self) -> None:
        if self._guarded():
            return
        if not self._running:
            self.start_clicked.emit()
        else:
            self.stop_clicked.emit()     # pause / resume

    def _on_stop(self) -> None:
        self._last_click = time.monotonic()
        self.hard_stop.emit()

    def setStatus(self, big: str, sub: str = "", kind: str = "") -> None:
        self.status_big.setText(big)
        self.status_sub.setText(sub)
        self.status_sub.setVisible(bool(sub))
        if self.status_big.property("kind") != kind:
            self.status_big.setProperty("kind", kind)
            self.status_big.style().unpolish(self.status_big)
            self.status_big.style().polish(self.status_big)

    def _restyle_start(self, text: str, icon: str) -> None:
        self.start_btn.setText(text)
        self.start_btn.setIcon(make_icon(icon, 14, C["on_accent"]))
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)

    def setRunning(self, running: bool) -> None:
        self._running = running
        self._paused = False
        self.stop_btn.setVisible(running)
        self.ready_chips.setVisible(not running)
        # A rebuilt list drops the selection of a window that closed or was
        # renamed: its row would show EMPTY while its bot still runs.
        self.refresh_btn.setEnabled(not running)
        later = "\n\nAvailable when the bots are stopped." if running else ""
        self.refresh_btn.setToolTip(self.REFRESH_TIP + later)
        self._sync_reset_btn()
        if running:
            self._restyle_start("  PAUSE", "pause")
        else:
            self._restyle_start("  START JIGSAW" if self._mode == MODE_JIGSAW else "  START FISHING", "play")

    def setPaused(self, paused: bool) -> None:
        self._paused = paused
        self._restyle_start("  RESUME" if paused else "  PAUSE", "play" if paused else "pause")
        self._sync_reset_btn()

    def _sync_reset_btn(self) -> None:
        """Reset Bait works when stopped and while paused (restocking is
        what the pause is for); not while the bots are clicking."""
        clicking = self._running and not self._paused
        self.reset_btn.setEnabled(not clicking)
        self.reset_btn.setToolTip(self.RESET_TIP + ("\n\nPause or stop the bots first." if clicking else ""))

    def recolor(self) -> None:
        """Re-renders the accent-colored icons after an accent change."""
        if self._running:
            self.setPaused(self._paused)
        else:
            self.setRunning(False)

    def setWindowList(self, windows: List[str]) -> None:
        for row in self.window_rows:
            row.setWindowList(windows)

    def selectedCount(self) -> int:
        return sum(1 for row in self.window_rows if row.selectedWindow())

    def setRunningCount(self, active: int) -> None:
        """Running bots out of the selected windows (not the 8 slots: 1/8
        reads like 7 bots failed to start)."""
        self.kpi_active.setValue(f"{active}/{max(active, self.selectedCount())}")

    def setKpis(self, total_games: int, active: int, total_bait: int, bait_cap: int) -> None:
        self.kpi_games.setValue(total_games)
        self.setRunningCount(active)
        self.kpi_bait.setValue(f"{total_bait:,}")
        self.kpi_cap.setValue(f"{bait_cap:,}")


class WrapLabel(QLabel):
    """A word-wrapped label that always gets the height its text needs.
    Layouts inside a scroll area hand a wrapped label the height of the
    text at its preferred width, which cuts off the last line when the
    window is narrower."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        need = self.heightForWidth(self.width())
        if need > 0 and need != self.minimumHeight():
            self.setMinimumHeight(need)


class ElidedLabel(QLabel):
    """A one-line label that ends in "…" when its text does not fit (a plain
    QLabel just cuts it off mid-word). text() stays the full text."""

    clicked = Signal()

    def displayedText(self) -> str:
        return self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(0, self.contentsRect().width()))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        self.style().drawItemText(p, self.contentsRect(), int(self.alignment()), self.palette(),
                                  self.isEnabled(), self.displayedText(), QPalette.WindowText)
        p.end()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


class WindowRow(QFrame):
    """One row in the Game Windows list — number + select + status pill + two
    counters + the latest message of that window's bot."""

    window_changed = Signal(int, str)
    help_requested = Signal(str, str)   # (guide topic, the row's message)

    def __init__(self, index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "WinRow")
        self.setProperty("active", "false")
        self.setFixedHeight(30)
        self.index = index
        self._remembered = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        self.num_label = QLabel(f"W{index + 1:02d}")
        self.num_label.setProperty("class", "WinNum")
        self.num_label.setFixedWidth(28)
        layout.addWidget(self.num_label)

        self.combo = QComboBox()
        self.combo.setFixedHeight(24)
        self.combo.setMinimumWidth(150)
        self.combo.setMaximumWidth(260)
        self.combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.combo.addItem("— select window —", "")
        self.combo.currentIndexChanged.connect(self._on_combo)
        layout.addWidget(self.combo, 0)

        self.pill = Pill("EMPTY", "idle")
        self.pill.setFixedWidth(88)
        layout.addWidget(self.pill, 0)

        self.bait_lbl = QLabel("Bait ---")
        self.bait_lbl.setProperty("class", "RowStat")
        self.bait_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.bait_lbl.setMinimumWidth(78)
        layout.addWidget(self.bait_lbl)

        self.games_lbl = QLabel("Games 0")
        self.games_lbl.setProperty("class", "RowStat")
        self.games_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.games_lbl.setMinimumWidth(70)
        self._stat_names = ("Bait", "Games")
        layout.addWidget(self.games_lbl)

        # Latest message of this window's bot; after a stop, why it stopped.
        self.msg_lbl = ElidedLabel("")
        self.msg_lbl.clicked.connect(self._on_msg_clicked)
        self._msg_topic = ""
        self.msg_lbl.setProperty("class", "RowMsg")
        self.msg_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.msg_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout.addWidget(self.msg_lbl, 1)
        self._msg_locked = False

    def setRemembered(self, name: str) -> None:
        """The window this slot is saved with (even while it is not open)."""
        self._remembered = name or ""
        if self._remembered and not self.selectedWindow():
            self.combo.setToolTip(f"Saved: '{self._remembered}' — not open right now (Refresh after starting it)")
        else:
            self.combo.setToolTip("")

    def isRemembered(self) -> bool:
        return bool(self._remembered)

    def setWindowList(self, windows: List[str]) -> None:
        prev = self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("— select window —", "")
        for w in windows:
            self.combo.addItem(w, w)
        # Restore previous selection if still present
        if prev:
            for i in range(self.combo.count()):
                if self.combo.itemData(i) == prev:
                    self.combo.setCurrentIndex(i)
                    break
        self.combo.blockSignals(False)

    def setTakenNames(self, taken: set) -> None:
        """Greys out the windows other rows already use (one bot per client)."""
        model = self.combo.model()
        mine = self.selectedWindow()
        for i in range(1, self.combo.count()):
            item = model.item(i) if hasattr(model, "item") else None
            if item is None:
                continue
            name = self.combo.itemData(i)
            used = name in taken and name != mine
            item.setEnabled(not used)
            item.setToolTip("Already used by another window slot" if used else "")

    def setSelectedWindow(self, name: str) -> None:
        for i in range(self.combo.count()):
            if self.combo.itemData(i) == name:
                self.combo.setCurrentIndex(i)
                return

    def selectedWindow(self) -> str:
        return self.combo.currentData() or ""

    def setStatus(self, kind: str, text: str) -> None:
        self.pill.setText("● " + text)
        self.pill.setKind(kind)
        active = (kind == "active")
        self.setProperty("active", "true" if active else "false")
        self.num_label.setProperty("class", "WinNum WinNumActive" if active else "WinNum")
        self.num_label.style().unpolish(self.num_label)
        self.num_label.style().polish(self.num_label)
        self.style().unpolish(self)
        self.style().polish(self)

    def setStatNames(self, first: str, second: str, tip_first: str = "", tip_second: str = "") -> None:
        """Names of the two counters: Bait / Games (fishing) or Crates / Boards (jigsaw)."""
        self._stat_names = (first, second)
        self.bait_lbl.setToolTip(tip_first)
        self.games_lbl.setToolTip(tip_second)

    def setBait(self, val) -> None:
        """First counter (bait left, or crates opened in Jigsaw mode)."""
        self.bait_lbl.setText(f"{self._stat_names[0]} {val}")

    def setGames(self, val) -> None:
        """Second counter (games played, or boards solved in Jigsaw mode)."""
        self.games_lbl.setText(f"{self._stat_names[1]} {val}")

    def setComboEnabled(self, enabled: bool) -> None:
        self.combo.setEnabled(enabled)

    def setMessage(self, text: str, lock: Optional[bool] = None, kind: str = "",
                   topic: str = "") -> None:
        """Shows ``text`` (a status message). ``lock=True`` keeps it (the stop
        reason) until ``lock=False`` — later messages of the stopping bot
        ("Bot finished!") do not replace it. ``kind="error"`` shows it red.
        With a guide ``topic`` a click on the message opens that topic."""
        if lock is None and self._msg_locked:
            return
        if lock is not None:
            self._msg_locked = lock
        self._msg_topic = topic
        self.msg_lbl.setText(text)
        self.msg_lbl.setToolTip(f"{text}\n\nClick for help." if topic and text else text)
        self.msg_lbl.setCursor(Qt.PointingHandCursor if topic else Qt.ArrowCursor)
        if self.msg_lbl.property("kind") != kind:
            self.msg_lbl.setProperty("kind", kind)
            self.msg_lbl.style().unpolish(self.msg_lbl)
            self.msg_lbl.style().polish(self.msg_lbl)

    def _on_combo(self, _idx: int) -> None:
        self.window_changed.emit(self.index, self.selectedWindow())

    def _on_msg_clicked(self) -> None:
        if self._msg_topic:
            self.help_requested.emit(self._msg_topic, self.msg_lbl.text())


# ============================================================================
# Positions tab (inventory pages and action points)
# ============================================================================

class InventoryTab(QWidget):
    """The POSITIONS tab: where the inventory page tabs and the action points
    (Drop / Sell, Confirm, Spare Armor) are in the game window."""

    set_coord = Signal(str)  # mode key
    unset_coord = Signal(str)  # mode key - the pill's ✕
    cancel_capture = Signal()

    FISHING_ONLY = ("drop", "armor")

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.coord_pills: Dict[str, CoordPill] = {}
        self._disabled = False
        self._mode = MODE_FISHING

        # --- Capture banner (only while waiting for the click in the game) ---
        self.capture_banner = QFrame()
        self.capture_banner.setObjectName("CaptureBanner")
        cb = QHBoxLayout(self.capture_banner)
        cb.setContentsMargins(10, 6, 6, 6)
        self.capture_text = QLabel("")
        self.capture_text.setProperty("class", "NoticeText")
        self.capture_text.setWordWrap(True)
        cb.addWidget(self.capture_text, 1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("class", "BtnGhost")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.cancel_capture.emit)
        cb.addWidget(cancel)
        self.capture_banner.setVisible(False)
        layout.addWidget(self.capture_banner)

        # --- Inventory pages ---
        pages_card = Card("Inventory Page Tabs")
        self.guide_btn = QPushButton("  Guide")
        self.guide_btn.setProperty("class", "BtnGhost")
        self.guide_btn.setIcon(make_icon("help", 12, C["text"]))
        self.guide_btn.setCursor(Qt.PointingHandCursor)
        self.guide_btn.clicked.connect(self._show_guide)
        pages_card.addAction(self.guide_btn)
        self.pages_hint = QLabel("")
        self.pages_hint.setProperty("class", "Hint")
        self.pages_hint.setWordWrap(True)
        pages_card.add(self.pages_hint)
        for page_row in [(1, 2, 3, 4), (5, 6, 7, 8)]:
            row = QHBoxLayout()
            row.setSpacing(8)
            for n in page_row:
                key = f"page{n}"
                pill = CoordPill(f"Page {n}", optional=(n > 1))
                pill.clicked.connect(lambda k=key: self._on_pill_clicked(k))
                pill.unset_requested.connect(lambda k=key: self.unset_coord.emit(k))
                self.coord_pills[key] = pill
                row.addWidget(pill, 1)
            pages_card.body_layout.addLayout(row)
        layout.addWidget(pages_card)

        # --- Action coords ---
        action_card = Card("Action Points")
        self.action_guide_btn = guide_button(lambda: open_guide(self, "drop_confirm"))
        action_card.addAction(self.action_guide_btn)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        for label, key in [("Drop / Sell", "drop"), ("Confirm", "confirm"), ("Spare Armor", "armor")]:
            pill = CoordPill(label, optional=True)
            pill.clicked.connect(lambda k=key: self._on_pill_clicked(k))
            pill.unset_requested.connect(lambda k=key: self.unset_coord.emit(k))
            self.coord_pills[key] = pill
            action_row.addWidget(pill, 1)
        action_card.body_layout.addLayout(action_row)
        self.action_hint = QLabel("")
        self.action_hint.setProperty("class", "Hint")
        self.action_hint.setWordWrap(True)
        action_card.add(self.action_hint)
        layout.addWidget(action_card)

        layout.addStretch(1)

        self.setMode(MODE_FISHING)

    def setMode(self, mode: str) -> None:
        """Both activities share this tab. In Jigsaw mode the Fishing-only
        points stay visible but are greyed out, so nothing seems to vanish;
        Confirm becomes required (crate dialog). Page 1 is required in both:
        Fishing finds where catches land, Jigsaw searches the pages for
        crates and checks that the inventory has room."""
        self._mode = mode
        fishing = mode != MODE_JIGSAW
        self.coord_pills["confirm"].setOptional(fishing, "for drops" if fishing else "")
        self.coord_pills["page1"].setOptional(False)
        if fishing:
            self.pages_hint.setText(
                "Click a page, then that inventory tab (I, II, …) in the game. Page 1 is required; set "
                "the further pages your client has, in order.")
        else:
            self.pages_hint.setText(
                "Click a page, then that inventory tab (I, II, …) in the game. Page 1 is required; set "
                "the further pages your client has, in order. The solver searches them for crates and "
                "does not play while every page is full.")
        if fishing:
            self.action_hint.setText(
                "Confirm is the Yes button of the game's drop dialog: needed when fish are set to drop. "
                "Spare Armor is for Armor quick skip.")
        else:
            self.action_hint.setText(
                "Jigsaw uses Confirm to accept the 'open the crate?' dialog. Drop / Sell and Spare Armor "
                "are Fishing only.")
        self._apply_locks()

    def _apply_locks(self) -> None:
        fishing = self._mode != MODE_JIGSAW
        running = "Stop the bot to change positions."
        for key, pill in self.coord_pills.items():
            if self._disabled:
                pill.setLocked(True, running)
            elif key in self.FISHING_ONLY and not fishing:
                pill.setLocked(True, "Fishing only — not used by the Jigsaw solver.")
            else:
                pill.setLocked(False)

    def _on_pill_clicked(self, key: str) -> None:
        """Handle pill click - only start capture if not disabled."""
        if not self._disabled:
            self.set_coord.emit(key)

    def _show_guide(self) -> None:
        open_guide(self, "inventory_tabs")

    def setCoord(self, key: str, coords: Optional[Tuple[int, int]]) -> None:
        if key in self.coord_pills:
            self.coord_pills[key].setCoords(coords)

    def setCapturing(self, key: Optional[str], text: str = "") -> None:
        for k, p in self.coord_pills.items():
            p.setCapturing(k == key)
        self.capture_text.setText(text)
        self.capture_banner.setVisible(bool(key))

    def setDisabled(self, disabled: bool) -> None:
        """Locks all positions while a bot runs."""
        self._disabled = disabled
        self._apply_locks()


# ============================================================================
# Settings tab
# ============================================================================

class SettingsTab(QWidget):
    open_timing = Signal()
    config_changed = Signal()
    accent_changed = Signal(str)
    open_fish_modal = Signal()
    auto_fish_toggled = Signal(bool)
    profile_load_requested = Signal(str)   # path
    profile_save_requested = Signal(str)   # path
    open_log_folder = Signal()
    show_log = Signal()
    restart_requested = Signal()

    BAIT_KEYS =["1", "2", "3", "4", "F1", "F2", "F3", "F4"]
    # Game keys the bot presses (stored lowercase, as the bot sends them)
    LETTER_KEYS = [chr(c) for c in range(ord("a"), ord("z") + 1)]
    CAST_KEYS = ["space"] + LETTER_KEYS
    MOUNT_KEYS = LETTER_KEYS
    SWATCHES = [
        ("#ffb627", "amber"),
        ("#ef5e5e", "red"),
        ("#5aa9ff", "blue"),
        ("#4ade80", "green"),
        ("#b46cf2", "purple"),
        ("custom", "custom"),
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._paused = False
        self._config_enabled = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        # Fishing-only options live on their own page (the FISHING tab); this
        # widget keeps the settings shared by both activities. The widgets
        # stay attributes of this object either way.
        self.fishing_page = QWidget()
        fishing_layout = QVBoxLayout(self.fishing_page)
        fishing_layout.setContentsMargins(0, 0, 0, 0)
        fishing_layout.setSpacing(10)

        # --- Bot behavior ---
        bb = Card("Bot Behavior")
        grid = QHBoxLayout()
        grid.setSpacing(14)
        # Left col: checks
        left = QVBoxLayout()
        left.setSpacing(10)
        self.classic_check = QCheckBox("Classic Fishing (no minigame)")
        self.classic_check.toggled.connect(self._on_changed)
        left.addWidget(self.classic_check)

        self.classic_delay_row = QFrame()
        crow = QHBoxLayout(self.classic_delay_row)
        crow.setContentsMargins(22, 0, 0, 0)
        crow.setSpacing(6)
        crow.addWidget(self._mono_label("Delay"))
        self.classic_delay_input = QLineEdit("3.0")
        self.classic_delay_input.setFixedWidth(60)
        delay_val = QDoubleValidator(0.5, 60.0, 2, self.classic_delay_input)
        delay_val.setNotation(QDoubleValidator.StandardNotation)
        self.classic_delay_input.setValidator(delay_val)
        self.classic_delay_input.setToolTip("Seconds to wait for a bite in classic fishing (0.5 – 60)")
        self.classic_delay_input.editingFinished.connect(self._on_changed)
        crow.addWidget(self.classic_delay_input)
        crow.addWidget(self._mono_label("sec", color=C["text_mute"]))
        crow.addStretch(1)
        self.classic_delay_row.setVisible(False)
        left.addWidget(self.classic_delay_row)
        self.classic_check.toggled.connect(self.classic_delay_row.setVisible)

        self.human_check = QCheckBox("Human-like mouse timing")
        self.human_check.setToolTip(
            "Varies the reaction time and mouse movement like a person would (Timing Settings → "
            "Click Rhythm), instead of reacting at machine speed.")
        self.human_check.toggled.connect(self._on_changed)
        left.addWidget(self.human_check)
        self.sound_check = QCheckBox("Sound alert when all windows are out of bait")
        self.sound_check.toggled.connect(self._on_changed)
        left.addWidget(self.sound_check)
        
        # Timing Settings button
        self.timing_btn = QPushButton("  Timing Settings")
        self.timing_btn.setProperty("class", "Btn")
        self.timing_btn.setIcon(make_icon("settings", 12, C["text"]))
        self.timing_btn.setCursor(Qt.PointingHandCursor)
        self.timing_btn.setMaximumWidth(180)
        self.timing_btn.clicked.connect(self.open_timing.emit)
        left.addWidget(self.timing_btn)
        
        left.addStretch(1)
        grid.addLayout(left, 0)
        grid.addStretch(1)  # Add spacing to push bait keys to the right

        # Right col: bait keys with quantity
        right = QVBoxLayout()
        right.setSpacing(12)
        bk_label = QLabel("BAIT KEYS")
        bk_label.setStyleSheet(
            f"font-family: {MONO}; font-size: 11px; color: {C['text_mute']}; "
            f"letter-spacing: 1px;"
        )
        right.addWidget(bk_label)
        
        # Key selection grid (2 rows x 4 columns)
        self.bait_key_grid = QGridLayout()
        self.bait_key_grid.setSpacing(8)
        self.bait_checks: Dict[str, QCheckBox] = {}
        for i, k in enumerate(self.BAIT_KEYS):
            row, col = i // 4, i % 4
            cb = QCheckBox(k)
            cb.toggled.connect(self._on_changed)
            self.bait_checks[k] = cb
            self.bait_key_grid.addWidget(cb, row, col)
        right.addLayout(self.bait_key_grid)
        
        # Single quantity input
        qty_row = QHBoxLayout()
        qty_row.setSpacing(8)
        qty_lbl = QLabel("Bait Quantity per key")
        qty_lbl.setStyleSheet(f"color: {C['text']}; font-family: {MONO}; font-size: 12px;")
        qty_row.addWidget(qty_lbl)
        self.bait_qty_input = QLineEdit("200")
        self.bait_qty_input.setFixedWidth(70)
        self.bait_qty_input.setAlignment(Qt.AlignRight)
        self.bait_qty_input.setValidator(QIntValidator(1, 100000, self.bait_qty_input))
        self.bait_qty_input.setToolTip("How many bait each selected key holds (1 – 100000). The bot "
                                       "counts bait down per cast; Reset Bait on the Dashboard refills it.")
        self.bait_qty_input.editingFinished.connect(self._on_changed)
        qty_row.addWidget(self.bait_qty_input)
        qty_row.addStretch(1)
        right.addLayout(qty_row)

        # Cast and mount keys: the keys bound in the client
        keys_row = QHBoxLayout()
        keys_row.setSpacing(8)
        self.cast_key_combo = self._key_combo(self.CAST_KEYS,
                                              "The key that casts and reels in (Space in most clients)")
        self.mount_key_combo = self._key_combo(self.MOUNT_KEYS,
                                               "The key that mounts and dismounts with Ctrl (G in most "
                                               "clients). Used by Quick Skip: Horse.")
        self.mount_key_combo.currentIndexChanged.connect(self._update_qs_desc)
        for text, combo in (("Cast key", self.cast_key_combo), ("Mount key  Ctrl +", self.mount_key_combo)):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {C['text']}; font-family: {MONO}; font-size: 12px;")
            keys_row.addWidget(lbl)
            keys_row.addWidget(combo)
            keys_row.addSpacing(6)
        keys_row.addStretch(1)
        right.addLayout(keys_row)
        
        right.addStretch(1)
        grid.addLayout(right, 1)
        bb.body_layout.addLayout(grid)
        fishing_layout.addWidget(bb)

        # --- Quick Skip ---
        qs = Card("Quick Skip")
        self.qs_enable = QCheckBox("Enable")
        self.qs_enable.toggled.connect(self._on_qs_enable)
        qs.addAction(self.qs_enable)
        qs_row = QHBoxLayout()
        qs_row.setSpacing(16)
        modes = QHBoxLayout()
        modes.setSpacing(14)
        self.qs_horse = QCheckBox("Horse")
        self.qs_armor = QCheckBox("Armor")
        for cb in (self.qs_horse, self.qs_armor):
            cb.toggled.connect(self._on_qs_mode)
        modes.addWidget(self.qs_horse)
        modes.addWidget(self.qs_armor)
        qs_row.addLayout(modes)
        self.qs_desc = qs_desc = QLabel()
        self._update_qs_desc()
        qs_desc.setWordWrap(True)
        qs_desc.setStyleSheet(
            f"color: {C['text_mute']}; font-family: {MONO}; font-size: 11px; "
            f"border-left: 1px solid {C['line']}; padding-left: 12px;"
        )
        qs_row.addWidget(qs_desc, 1)
        qs.body_layout.addLayout(qs_row)
        fishing_layout.addWidget(qs)

        # --- Automatic fish handling ---
        auto_card = Card("Automatic Fish Handling")
        self.auto_check = QCheckBox("Enable")
        self.auto_check.toggled.connect(self.auto_fish_toggled.emit)
        auto_card.addAction(self.auto_check)
        body = QHBoxLayout()
        body.setSpacing(12)
        desc = QLabel()
        desc.setText(
            f"After each catch: <span style='color:{C['green']};'>keep</span>, "
            f"<span style='color:{C['red']};'>drop/sell</span> or "
            f"<span style='color:{C['blue']};'>open</span> each fish, and keep or drop each item. "
            f"Dropping needs Confirm (Positions tab)."
        )
        desc.setTextFormat(Qt.RichText)
        desc.setWordWrap(True)
        desc.setProperty("class", "HintDim")
        self.configure_fish_btn = QPushButton("  Configure Fish and Items")
        self.configure_fish_btn.setProperty("class", "Btn")
        self.configure_fish_btn.setIcon(make_icon("fish", 14, C["text"]))
        self.configure_fish_btn.setCursor(Qt.PointingHandCursor)
        self.configure_fish_btn.clicked.connect(self.open_fish_modal.emit)
        body.addWidget(desc, 1)
        body.addWidget(self.configure_fish_btn)
        auto_card.body_layout.addLayout(body)
        fishing_layout.addWidget(auto_card)

        # --- Golden Tuna (official and some private servers) ---
        gt = Card("Golden Tuna")
        gt.setToolTip(
            "After a won minigame the Golden Tuna can open a dialog with three answers. The bot "
            "answers it and clicks OK on the page that follows, then fishes on. It recognizes the "
            "dialog by its shape (black bars, board, answer bars), not its text, so it works in "
            "every language. On servers without it nothing happens.")
        gt_row = QHBoxLayout()
        gt_row.setSpacing(10)
        gt_lbl = QLabel("Answer")
        gt_lbl.setStyleSheet(f"color: {C['text']}; font-family: {MONO}; font-size: 12px;")
        gt_row.addWidget(gt_lbl)
        self.tuna_choice_combo = QComboBox()
        for i, (text, tip) in enumerate((
                ("1. Let it go", "A buff for rare fish (60 min) or a short slow-down"),
                ("2. Cut it open", "A random item: clam, pearl, Kelpie chest or a rod (30 days)"),
                ("3. Use it for bait", "A stronger buff for rare fish for 5 minutes"))):
            self.tuna_choice_combo.addItem(text, i)
            self.tuna_choice_combo.setItemData(i, tip, Qt.ToolTipRole)
        self.tuna_choice_combo.setToolTip("Which of the three answers the bot clicks. They are counted "
                                          "from the top, so the client's language does not matter.")
        self.tuna_choice_combo.currentIndexChanged.connect(self._on_changed)
        gt_row.addWidget(self.tuna_choice_combo)
        self.tuna_desc = QLabel("Answered automatically when the dialog opens (servers without it are "
                                "not affected). The bars are counted from the top, so any language works.")
        self.tuna_desc.setWordWrap(True)
        self.tuna_desc.setStyleSheet(
            f"color: {C['text_mute']}; font-family: {MONO}; font-size: 11px; "
            f"border-left: 1px solid {C['line']}; padding-left: 12px;"
        )
        gt_row.addWidget(self.tuna_desc, 1)
        gt.body_layout.addLayout(gt_row)
        fishing_layout.addWidget(gt)

        self.window_capture_check = QCheckBox("Per-window capture (game windows may overlap)")
        self.window_capture_check.setToolTip(
            "Reads each game window directly instead of copying the screen, so windows can "
            "cover each other. Used only after it has been checked against the screen; "
            "clients that block it fall back to screen capture automatically."
        )
        self.window_capture_check.toggled.connect(self._on_changed)
        fishing_layout.addStretch(1)

        # --- Screen capture + input (both activities) ---
        sc = Card("Screen Capture & Input")
        sc.body_layout.addWidget(self.window_capture_check)
        self.pause_sound_check = QCheckBox("Chime on pause / resume (F5)")
        self.pause_sound_check.setToolTip(
            "A short falling chime when the bots pause and a rising one when they resume, so F5 is "
            "confirmed while the game window is in front.")
        self.pause_sound_check.toggled.connect(self._on_changed)
        sc.body_layout.addWidget(self.pause_sound_check)
        self.input_label = self._mono_label("Input method: —", color=C["text_mute"])
        sc.body_layout.addWidget(self.input_label)
        layout.addWidget(sc)
        self._capture_backend = "auto"

        # --- Profiles ---
        pr = Card("Profiles & Presets")
        pr_row = QHBoxLayout()
        pr_row.setSpacing(8)
        pr_row.addWidget(self._mono_label("Whole configuration as a file"), 1)
        self.profile_load_btn = QPushButton("  Load")
        self.profile_load_btn.setProperty("class", "Btn")
        self.profile_load_btn.setIcon(make_icon("folder", 12, C["text"]))
        self.profile_load_btn.setCursor(Qt.PointingHandCursor)
        self.profile_load_btn.clicked.connect(self._profile_load)
        pr_row.addWidget(self.profile_load_btn)
        self.profile_save_btn = QPushButton("  Save As…")
        self.profile_save_btn.setProperty("class", "Btn")
        self.profile_save_btn.setIcon(make_icon("save", 12, C["text"]))
        self.profile_save_btn.setCursor(Qt.PointingHandCursor)
        self.profile_save_btn.clicked.connect(self._profile_save)
        pr_row.addWidget(self.profile_save_btn)
        pr.body_layout.addLayout(pr_row)
        pr_hint = QLabel(
            "Profiles store coordinates, fish actions, bait keys and timing. "
            "Loading one replaces the current settings."
        )
        pr_hint.setProperty("class", "Hint")
        pr.body_layout.addWidget(pr_hint)
        layout.addWidget(pr)

        # --- Diagnostics ---
        dg = Card("Diagnostics")
        self.diag_warning = WrapLabel(
            "FOR SUPPORT AND TROUBLESHOOTING ONLY\n"
            "Only turn these on if you know what you are doing, or support asked you to. "
            "Otherwise, do not change these settings. They make the bot slower and write "
            "large files to disk. The recording also captures everything shown in the game "
            "windows, including chat and character names.")
        self.diag_warning.setWordWrap(True)
        # The border sits on a frame around the label: CSS padding on a
        # word-wrapped label makes Qt cut off its last line
        self.diag_warning.setStyleSheet(f"color: {C['orange']}; border: none; background: transparent;")
        warn_font = self.diag_warning.font()
        warn_font.setBold(True)
        self.diag_warning.setFont(warn_font)
        warn_box = QFrame()
        warn_box.setObjectName("DiagWarning")
        warn_box.setStyleSheet(
            f"QFrame#DiagWarning {{ border: 2px solid {C['orange']}; border-radius: 6px; "
            f"background: rgba(255,160,60,0.08); }}")
        warn_layout = QVBoxLayout(warn_box)
        warn_layout.setContentsMargins(12, 10, 12, 10)
        warn_layout.addWidget(self.diag_warning)
        dg.body_layout.addWidget(warn_box)
        dg_row = QHBoxLayout()
        dg_row.setSpacing(14)
        self.diag_check = QCheckBox("Diagnostic log")
        self.diag_check.setToolTip(
            "Records everything the bot does, sees and decides (plus screenshots of the game "
            "window when something is not found) into one .zip per run. Turn it on, reproduce "
            "the problem, then send the newest .zip from the log folder to support."
        )
        self.diag_check.toggled.connect(self._on_changed)
        dg_row.addWidget(self.diag_check)
        dg_row.addStretch(1)
        self.diag_show_btn = QPushButton("  Show log")
        self.diag_show_btn.setProperty("class", "Btn")
        self.diag_show_btn.setIcon(make_icon("settings", 12, C["text"]))
        self.diag_show_btn.setCursor(Qt.PointingHandCursor)
        self.diag_show_btn.clicked.connect(self.show_log.emit)
        dg_row.addWidget(self.diag_show_btn)
        self.diag_open_btn = QPushButton("  Open log folder")
        self.diag_open_btn.setProperty("class", "Btn")
        self.diag_open_btn.setIcon(make_icon("folder", 12, C["text"]))
        self.diag_open_btn.setCursor(Qt.PointingHandCursor)
        self.diag_open_btn.clicked.connect(self.open_log_folder.emit)
        dg_row.addWidget(self.diag_open_btn)
        dg.body_layout.addLayout(dg_row)
        self.diag_record_check = QCheckBox("Record game windows (video in the diagnostic log)")
        self.diag_record_check.setToolTip(
            "Also records a video of each game window the bot runs on (10 frames a second), "
            "saved in the same .zip as the diagnostic log, with every frame's time so it can "
            "be matched to the log. Only the game windows are recorded, never the rest of the "
            "screen. Files get big: about 60 MB per minute for an 800x600 game window (more for "
            "bigger windows), at most 2 GB per run, which is about 15 minutes with two windows. "
            "Needs the diagnostic log.")
        self.diag_record_check.toggled.connect(self._on_changed)
        self.diag_check.toggled.connect(self._update_record_enabled)
        dg.body_layout.addWidget(self.diag_record_check)
        self.diag_path_label = QLabel("")
        self.diag_path_label.setWordWrap(True)
        self.diag_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.diag_path_label.setProperty("class", "Hint")
        dg.body_layout.addWidget(self.diag_path_label)
        self.diag_last_label = QLabel("")
        self.diag_last_label.setWordWrap(True)
        self.diag_last_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.diag_last_label.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 11px;")
        dg.body_layout.addWidget(self.diag_last_label)
        # --- Theme ---
        th = Card("Appearance")
        size_row = QHBoxLayout()
        size_row.setSpacing(10)
        size_row.addWidget(self._mono_label("Interface size"))
        self.ui_scale_combo = QComboBox()
        for key, label in UI_SCALE_CHOICES:
            self.ui_scale_combo.addItem(label, key)
        self.ui_scale_combo.setFixedHeight(26)
        self.ui_scale_combo.setMinimumWidth(110)
        self.ui_scale_combo.setToolTip(
            "Makes text, icons and the window bigger or smaller. Auto picks a size from your "
            "screen. The window is never made bigger than the screen.")
        self.ui_scale_combo.currentIndexChanged.connect(self._on_changed)
        self.ui_scale_combo.currentIndexChanged.connect(self._update_scale_note)
        size_row.addWidget(self.ui_scale_combo)
        self.ui_scale_note = self._mono_label("", color=C["text_mute"])
        size_row.addWidget(self.ui_scale_note, 1)
        th.body_layout.addLayout(size_row)
        th_row = QHBoxLayout()
        th_row.setSpacing(6)
        th_row.addWidget(self._mono_label("Accent color"))
        self.swatches: Dict[str, QLabel] = {}
        for color, name in self.SWATCHES:
            sw = QLabel()
            sw.setFixedSize(18, 18)
            sw.setProperty("class", "Swatch")
            sw.setProperty("active", "false")
            sw.setCursor(Qt.PointingHandCursor)
            if color == "custom":
                sw.setStyleSheet(
                    "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
                    "stop:0 #ff6ec7, stop:0.5 #ffb627, stop:1 #5aa9ff); "
                    "border-radius: 4px;"
                )
                sw.setToolTip("Custom color…")
            else:
                sw.setStyleSheet(f"background: {color}; border-radius: 4px;")
            sw.mousePressEvent = lambda _ev, c=color: self._select_swatch(c)
            self.swatches[color] = sw
            th_row.addWidget(sw)
        th_row.addStretch(1)
        self.accent_label = QLabel()
        self.accent_label.setStyleSheet(f"font-family: {MONO}; font-size: 11px; color: {C['text_mute']};")
        th_row.addWidget(self.accent_label)
        th.body_layout.addLayout(th_row)
        layout.addWidget(th)
        # --- Detection bypass ---
        pv = Card("Detection bypass")
        self.disguise_check = QCheckBox("Disguise in Task Manager (random name at every start)")
        self.disguise_check.toggled.connect(self._on_changed)
        self.disguise_check.toggled.connect(self._update_disguise_note)
        pv.body_layout.addWidget(self.disguise_check)
        dz_row = QHBoxLayout()
        dz_row.setSpacing(10)
        self.disguise_note = self._mono_label("", color=C["text_mute"])
        self.disguise_note.setWordWrap(True)
        self.disguise_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dz_row.addWidget(self.disguise_note, 1)
        self.restart_btn = QPushButton("  Restart now")
        self.restart_btn.setProperty("class", "Btn")
        self.restart_btn.setIcon(make_icon("refresh", 12, C["text"]))
        self.restart_btn.setCursor(Qt.PointingHandCursor)
        self.restart_btn.clicked.connect(self.restart_requested.emit)
        dz_row.addWidget(self.restart_btn, 0, Qt.AlignTop)
        pv.body_layout.addLayout(dz_row)
        layout.addWidget(pv)
        layout.addWidget(dg)   # last: support only, see its warning

        layout.addStretch(1)

        self._suspend_signals = False
        # The Quick Skip mode to show when Enable is on. While it is off
        # neither box is ticked, so nothing looks selected.
        self._qs_mode = "horse"
        self._on_qs_enable(False)
        self._update_scale_note()
        self._update_disguise_note()

    def _update_disguise_note(self, *_args) -> None:
        """What the disguise does now, and whether a restart is needed."""
        wanted = self.disguise_check.isChecked()
        running_as = disguise.active_name()
        pending = False
        if not disguise.available():
            text = "Only in the downloaded program" if wanted else ""
        elif running_as:
            pending = not wanted
            text = "Restart to apply" if pending else f"Running as \"{running_as}\""
        elif disguise.LAST_ERROR and wanted:
            text = f"Failed: {disguise.LAST_ERROR}"
            pending = True
        elif wanted:
            text = "Restart to apply"
            pending = True
        else:
            text = ""
        self.disguise_note.setText(text)
        self.disguise_note.setVisible(bool(text))
        self.restart_btn.setVisible(pending)

    def _update_scale_note(self, *_args) -> None:
        now = f"now {round(ACTIVE_UI_SCALE * 100)}%"
        if ACTIVE_UI_SCALE_SOURCE == "env":
            self.ui_scale_note.setText(f"{now} (set by QT_SCALE_FACTOR, this setting is ignored)")
            return
        chosen = self.ui_scale_combo.currentData() or "auto"
        if chosen != ACTIVE_UI_SCALE_SETTING:
            self.ui_scale_note.setText(f"{now} — restart the program to apply")
        elif chosen == "auto":
            self.ui_scale_note.setText(f"{now} (picked for this screen)")
        elif _scale_key(ACTIVE_UI_SCALE) != chosen:
            self.ui_scale_note.setText(f"{now} (the largest that fits this screen)")
        else:
            self.ui_scale_note.setText(now)

    def _mono_label(self, text: str, color: Optional[str] = None) -> QLabel:
        l = QLabel(text)
        col = color or C["text_dim"]
        l.setStyleSheet(f"color: {col}; font-family: {MONO}; font-size: 12px;")
        return l

    def setInputBackend(self, display_name: str, tooltip: str = "") -> None:
        """How mouse and keyboard input reaches the game (shown in the Screen
        Capture & Input card; details in the tooltip)."""
        self.input_label.setText(f"Input method: {display_name}")
        self.input_label.setToolTip(tooltip)

    def _on_changed(self, *_args) -> None:
        if self._suspend_signals:
            return
        self.config_changed.emit()

    def _key_combo(self, keys: List[str], tip: str) -> QComboBox:
        combo = QComboBox()
        for k in keys:
            combo.addItem(self._key_label(k), k)
        combo.setToolTip(tip)
        combo.setMaxVisibleItems(12)
        combo.currentIndexChanged.connect(self._on_changed)
        return combo

    @staticmethod
    def _key_label(key: str) -> str:
        return "Space" if key == "space" else key.upper()

    @staticmethod
    def _set_key_combo(combo: QComboBox, key: str) -> None:
        idx = combo.findData(str(key).lower())
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _update_qs_desc(self, *_args) -> None:
        key = self._key_label(self.mount_key_combo.currentData() or "g")
        self.qs_desc.setText(
            f"Skips the fishing animation between catches: Horse presses Ctrl + {key} (the mount key, "
            "set above); Armor right-clicks a spare armor in the inventory (set Spare Armor on the "
            "Positions tab), which swaps it with the worn one.")

    def setDiagLocation(self, folder: str, fallback: bool) -> None:
        text = f"Logs are saved in: {folder}"
        if fallback:
            text += "  (the program folder is not writable, so a per-user folder is used)"
        self.diag_path_label.setText(text)

    def setDiagLast(self, text: str) -> None:
        self.diag_last_label.setText(text)

    def _on_qs_enable(self, checked: bool) -> None:
        if not checked:
            self._qs_mode = self.quickSkipMode()
        for cb, mode in ((self.qs_horse, "horse"), (self.qs_armor, "armor")):
            cb.blockSignals(True)
            cb.setChecked(checked and self._qs_mode == mode)
            cb.blockSignals(False)
            cb.setEnabled(checked and self._config_enabled)
        self._on_changed()

    def quickSkipMode(self) -> str:
        """The Quick Skip mode, "horse" or "armor": the ticked box, else the
        mode kept while Quick Skip is off."""
        if self.qs_armor.isChecked():
            return "armor"
        if self.qs_horse.isChecked():
            return "horse"
        return self._qs_mode

    def _on_qs_mode(self, _checked: bool) -> None:
        if self._suspend_signals:
            return
        sender = self.sender()
        if sender not in (self.qs_horse, self.qs_armor):
            self.config_changed.emit()
            return
        other = self.qs_armor if sender is self.qs_horse else self.qs_horse
        # Radio behavior: one mode is always ticked while Quick Skip is on
        if sender.isChecked():
            other.blockSignals(True); other.setChecked(False); other.blockSignals(False)
            self._qs_mode = "horse" if sender is self.qs_horse else "armor"
        elif not other.isChecked():
            sender.blockSignals(True); sender.setChecked(True); sender.blockSignals(False)
            return
        self.config_changed.emit()

    def _select_swatch(self, color: str) -> None:
        if color == "custom":
            chosen = QColorDialog.getColor(
                QColor(C["accent"]), self, "Pick accent color",
                QColorDialog.ShowAlphaChannel
            )
            if not chosen.isValid():
                return
            color = chosen.name()
            # Update the custom swatch background to the chosen color
            self.swatches["custom"].setStyleSheet(f"background: {color}; border-radius: 4px;")
            self.swatches["custom"].setToolTip(f"Custom: {color}")
        for c, lbl in self.swatches.items():
            lbl.setProperty("active", "true" if c == color else "false")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
        self.accent_label.setText(
            f"Currently: <span style='color:{color};'>{color.upper()}</span>"
        )
        self.accent_label.setTextFormat(Qt.RichText)
        if not self._suspend_signals:
            self.accent_changed.emit(color)

    # ---- Public ----
    def loadFromConfig(self, cfg: dict) -> None:
        self._suspend_signals = True
        self.classic_check.setChecked(cfg.get("classic_fishing", False))
        self.classic_delay_row.setVisible(self.classic_check.isChecked())
        self.classic_delay_input.setText(str(cfg.get("classic_fishing_delay", 3.0)))
        self.human_check.setChecked(cfg.get("human_like_clicking", False))
        self.sound_check.setChecked(cfg.get("sound_alert_on_finish", True))
        self.pause_sound_check.setChecked(bool(cfg.get("sound_on_pause", True)))
        bk = set(cfg.get("bait_keys", ["1", "2", "3", "4"]))
        for k, cb in self.bait_checks.items():
            cb.setChecked(k in bk)
        self.bait_qty_input.setText(str(cfg.get("bait_quantity", 200)))
        self._set_key_combo(self.cast_key_combo, cfg.get("cast_key", "space"))
        self._set_key_combo(self.mount_key_combo, cfg.get("mount_key", "g"))
        self._update_qs_desc()
        self._capture_backend = str(cfg.get("capture_backend", "auto")).lower()
        self.window_capture_check.setChecked(self._capture_backend != "screen")
        self.diag_check.setChecked(bool(cfg.get("diagnostics_enabled", False)))
        self.diag_record_check.setChecked(bool(cfg.get("diagnostics_record_screens", False)))
        self._update_record_enabled()
        self._qs_mode = "armor" if cfg.get("quick_skip_mode") == "armor" else "horse"
        self.qs_horse.setChecked(False)
        self.qs_armor.setChecked(False)
        self.qs_enable.blockSignals(True)
        self.qs_enable.setChecked(bool(cfg.get("quick_skip", False)))
        self.qs_enable.blockSignals(False)
        self._on_qs_enable(self.qs_enable.isChecked())
        try:
            choice = int(cfg.get("golden_tuna_choice", 1))
        except (TypeError, ValueError):
            choice = 1
        self.tuna_choice_combo.setCurrentIndex(choice if 0 <= choice <= 2 else 1)
        key = str(cfg.get("ui_scale", "auto"))
        idx = self.ui_scale_combo.findData(key if self.ui_scale_combo.findData(key) >= 0 else "auto")
        self.ui_scale_combo.setCurrentIndex(idx)
        self._update_scale_note()
        self.disguise_check.setChecked(bool(cfg.get(disguise.CONFIG_KEY, False)))
        self._update_disguise_note()
        accent =cfg.get("accent_color", DEFAULT_ACCENT)
        accent = accent if isinstance(accent, str) and QColor(accent).isValid() else DEFAULT_ACCENT
        # match against known swatches case-insensitively
        match = next((c for c in self.swatches if c.lower() == accent.lower()), None)
        if match:
            self._select_swatch(match)
        else:
            # A custom color: show it on the custom swatch and mark that one
            self.swatches["custom"].setStyleSheet(f"background: {accent}; border-radius: 4px;")
            self.swatches["custom"].setToolTip(f"Custom: {accent}")
            for c, lbl in self.swatches.items():
                lbl.setProperty("active", "true" if c == "custom" else "false")
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)
            self.accent_label.setText(
                f"Currently: <span style='color:{accent};'>{accent.upper()}</span>"
            )
            self.accent_label.setTextFormat(Qt.RichText)
        self._suspend_signals = False

    def writeToConfig(self, cfg: dict) -> None:
        cfg["classic_fishing"] = self.classic_check.isChecked()
        # Text the validator could not stop (empty, half typed) keeps the last
        # valid value instead of silently becoming a default.
        try:
            cfg["classic_fishing_delay"] = min(60.0, max(0.5, float(self.classic_delay_input.text())))
        except ValueError:
            cfg["classic_fishing_delay"] = cfg.get("classic_fishing_delay", 3.0)
        self.classic_delay_input.setText(f"{cfg['classic_fishing_delay']:g}")
        cfg["human_like_clicking"] = self.human_check.isChecked()
        cfg["sound_alert_on_finish"] = self.sound_check.isChecked()
        cfg["sound_on_pause"] = self.pause_sound_check.isChecked()
        cfg["bait_keys"] = [k for k, cb in self.bait_checks.items() if cb.isChecked()]
        try:
            cfg["bait_quantity"] = min(100000, max(1, int(self.bait_qty_input.text())))
        except ValueError:
            cfg["bait_quantity"] = cfg.get("bait_quantity", 200)
        self.bait_qty_input.setText(str(cfg["bait_quantity"]))
        cfg["cast_key"] = self.cast_key_combo.currentData() or "space"
        cfg["mount_key"] = self.mount_key_combo.currentData() or "g"
        cfg["quick_skip"] = self.qs_enable.isChecked()
        cfg["quick_skip_mode"] = self.quickSkipMode()
        # Always on, no setting: the Golden Tuna answer only matters where
        # the dialog exists, and the minigame detection adapts by itself.
        cfg["golden_tuna"] = True
        cfg["golden_tuna_choice"] = int(self.tuna_choice_combo.currentData() or 0)
        cfg["adaptive_detection"] = True
        cfg["adaptive_recast_wait"] = True
        cfg["diagnostics_enabled"] = self.diag_check.isChecked()
        cfg["diagnostics_record_screens"] = self.diag_record_check.isChecked()
        if not self.window_capture_check.isChecked():
            cfg["capture_backend"] = "screen"
        else:
            # Keep an explicit "window" (always per-window) set by hand in the file
            cfg["capture_backend"] = "window" if self._capture_backend == "window" else "auto"
        self._capture_backend = cfg["capture_backend"]
        cfg["ui_scale"] = self.ui_scale_combo.currentData() or "auto"
        cfg[disguise.CONFIG_KEY] = self.disguise_check.isChecked()

    def setAutoFish(self, enabled: bool) -> None:
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(enabled)
        self.auto_check.blockSignals(False)

    def selectedBaitKeys(self) -> List[str]:
        return [k for k, cb in self.bait_checks.items() if cb.isChecked()]

    def setConfigEnabled(self, enabled: bool) -> None:
        for w in (self.classic_check, self.classic_delay_input, self.human_check,
                  self.sound_check, self.qs_enable, self.qs_horse, self.qs_armor,
                  self.window_capture_check, self.tuna_choice_combo, self.auto_check,
                  self.cast_key_combo, self.mount_key_combo):
            w.setEnabled(enabled if w not in (self.qs_horse, self.qs_armor) else (enabled and self.qs_enable.isChecked()))
        for cb in self.bait_checks.values():
            cb.setEnabled(enabled)
        # Diagnostics settings apply from the next Start; the log buttons
        # stay usable while running.
        self.diag_check.setEnabled(enabled)
        # Track config enabled state
        self._config_enabled = enabled
        self._update_record_enabled()
        self._update_disabled_state()

    def setPaused(self, paused: bool) -> None:
        """Disable timing settings and bait quantity when paused."""
        self._paused = paused
        self._update_disabled_state()

    def _update_record_enabled(self, *_args) -> None:
        """The recording goes into the diagnostic log, so it needs the log on."""
        self.diag_record_check.setEnabled(getattr(self, "_config_enabled", True) and self.diag_check.isChecked())

    def _update_disabled_state(self) -> None:
        """Update disabled state based on config enabled and paused state."""
        disabled = self._paused or not self._config_enabled
        for w in (self.timing_btn, self.profile_load_btn, self.profile_save_btn):
            w.setDisabled(disabled)
            w.setCursor(Qt.ForbiddenCursor if disabled else Qt.PointingHandCursor)
        # The fish choices are part of the run's configuration: not while a bot runs.
        self.configure_fish_btn.setDisabled(not self._config_enabled)
        self.configure_fish_btn.setCursor(Qt.ForbiddenCursor if not self._config_enabled
                                          else Qt.PointingHandCursor)
        self.bait_qty_input.setDisabled(disabled)
        self.bait_qty_input.setCursor(Qt.ForbiddenCursor if disabled else Qt.IBeamCursor)

    # Profile actions — the window owns the configuration, so it does the I/O.
    def _profile_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load profile", os.getcwd(), "JSON (*.json)")
        if path:
            self.profile_load_requested.emit(path)

    def _profile_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save profile", os.path.join(os.getcwd(), "profile.json"), "JSON (*.json)")
        if path:
            self.profile_save_requested.emit(path)


# ============================================================================
# Modals
# ============================================================================

# The fish / item tables and default_fish_action live in session_setup.py
# (shared with the real-client tests); imported at the top.


def section_divider(text: str) -> QHBoxLayout:
    l = QHBoxLayout()
    l.setSpacing(10)
    label = QLabel(text.upper())
    label.setProperty("class", "SectionDivider")
    line = QFrame()
    line.setFixedHeight(1)
    line.setProperty("class", "DividerLine")
    l.addWidget(label)
    l.addWidget(line, 1)
    return l


class FishModal(QDialog):
    """What the bot does with each catch, in three tabs: bait fish (keep,
    drop or use), fish (keep, drop or open) and items (keep or drop). The
    dead fish have no tiles: they follow their living fish."""
    saved = Signal(dict)  # {asset_filename: 'keep'|'drop'|'open'|'use' (bait fish)}

    # (tab id, icon, label, entries, options shown in the legend, hint)
    TABS = [
        ("bait", "target", "Bait fish", BAIT_DISPLAY, ("keep", "drop", "use"),
         "Use puts them on the rod instead of pressing the bait key."),
        ("fish", "fish", "Fish", FISH_DISPLAY, ("keep", "drop", "open"),
         "Dead fish follow their fish: dropped if it drops, kept otherwise."),
        ("items", "package", "Items", ITEM_DISPLAY, ("keep", "drop"), ""),
    ]
    COLUMNS = 6
    _LEGEND = {"keep": ("K", "green", "Keep"), "drop": ("D", "red", "Drop"),
               "open": ("O", "blue", "Open"), "use": ("U", "purple", "Use as bait")}

    def __init__(self, current_actions: Dict[str, str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Fish & Item Selection")
        self.setModal(True)
        self.resize(600, 500)
        self.setMinimumSize(560, 400)
        self._actions = {a: v for a, v in dict(current_actions).items() if not a.endswith("_dead.png")}
        for _, asset, _ in FISH_DISPLAY + BAIT_DISPLAY:
            self._actions[asset] = normalize_fish_action(
                self._actions.get(asset, default_fish_action(asset)), asset)
        for _, asset, _ in ITEM_DISPLAY:
            # Items cannot be opened: anything but drop is keep
            self._actions[asset] = "drop" if self._actions.get(asset) == "drop" else "keep"
        self._tiles: Dict[str, FishTile] = {}
        self._tab_assets: Dict[str, List[str]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QFrame()
        header.setProperty("class", "ModalHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 10, 12, 10)
        title = QLabel("FISH & ITEM SELECTION")
        title.setProperty("class", "ModalTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        outer.addWidget(header)

        self.tabs = TabsBar([(tid, icon, f"{label} ({len(entries)})")
                             for tid, icon, label, entries, _, _ in self.TABS])
        outer.addWidget(self.tabs)

        self.stack = QStackedWidget()
        self._pages: Dict[str, QWidget] = {}
        for tid, _, _, entries, options, hint in self.TABS:
            page = self._build_page(tid, entries, options, hint)
            self._pages[tid] = page
            self.stack.addWidget(page)
        outer.addWidget(self.stack, 1)

        # Footer: the bulk buttons act on the tab shown
        footer = QFrame()
        footer.setProperty("class", "ModalFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 10, 16, 10)
        fl.setSpacing(6)
        self.keep_all_btn = QPushButton("Keep All")
        self.keep_all_btn.clicked.connect(lambda: self._set_tab_all("keep"))
        self.drop_all_btn = QPushButton("Drop All")
        self.drop_all_btn.clicked.connect(lambda: self._set_tab_all("drop"))
        self.third_all_btn = QPushButton()
        self.third_all_btn.clicked.connect(self._third_all)
        for b in (self.keep_all_btn, self.drop_all_btn, self.third_all_btn):
            b.setProperty("class", "BtnGhost")
            b.setCursor(Qt.PointingHandCursor)
            fl.addWidget(b)
        fl.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "Btn")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("  Save")
        save_btn.setProperty("class", "BtnPrimary")
        save_btn.setIcon(make_icon("save", 12, C["on_accent"]))
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        fl.addWidget(cancel_btn)
        fl.addWidget(save_btn)
        outer.addWidget(footer)

        self.tabs.changed.connect(self._show_tab)
        self._show_tab(self.tabs.active())
        no_enter_default(self)
        help_visuals.fit_to_screen(self)   # short screens: keep the footer on screen

    def _build_page(self, tid: str, entries, options, hint: str) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        sep = f" <span style='color:{C['text_mute']}'>·</span> "
        legend = sep.join(f"<span style='color:{C[color]}; font-weight:700'>{letter}</span> {word}"
                          for letter, color, word in (self._LEGEND[o] for o in options))
        if hint:
            legend += f"<br><span style='color:{C['text_mute']}'>{hint}</span>"
        lbl = QLabel(legend)
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-family: {MONO}; font-size: 11px; color: {C['text_dim']};")
        lbl.setContentsMargins(16, 8, 16, 4)
        pl.addWidget(lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 6, 12, 12)
        grid = QGridLayout()
        grid.setSpacing(6)
        assets = []
        for i, (name, asset, emoji) in enumerate(entries):
            tile = FishTile(name, asset, emoji, self._actions[asset], kd_only=(tid == "items"))
            tile.changed.connect(lambda v, a=asset: self._actions.update({a: v}))
            self._tiles[asset] = tile
            assets.append(asset)
            grid.addWidget(tile, i // self.COLUMNS, i % self.COLUMNS)
        for c in range(self.COLUMNS):
            grid.setColumnStretch(c, 1)
        bl.addLayout(grid)
        bl.addStretch(1)
        scroll.setWidget(body)
        pl.addWidget(scroll, 1)
        self._tab_assets[tid] = assets
        return page

    def _show_tab(self, tid: str) -> None:
        self.stack.setCurrentWidget(self._pages[tid])
        third = {"bait": "Use All", "fish": "Open All"}.get(tid)
        self.third_all_btn.setVisible(third is not None)
        self.third_all_btn.setText(third or "")

    def _set_tab_all(self, action: str) -> None:
        self._set_all(self._tab_assets[self.tabs.active()], action)

    def _third_all(self) -> None:
        if self.tabs.active() == "bait":
            self._set_all(self._tab_assets["bait"], "use")
        else:
            self._open_all()

    def _set_all(self, assets: List[str], action: str) -> None:
        for a in assets:
            if a in self._tiles:
                self._tiles[a].setAction(action)
                self._actions[a] = action

    def _open_all(self) -> None:
        """Sets every fish to open; the items (which cannot be opened) and
        the bait fish (a right-click puts them on the rod) keep what they
        are set to."""
        self._set_all([a for _, a, _ in FISH_DISPLAY], "open")

    def _save(self) -> None:
        actions = {a: normalize_fish_action(v, a) for a, v in self._actions.items()}
        self.saved.emit(with_dead_fish_actions(actions))
        self.accept()


class FishTile(QFrame):
    changed = Signal(str)

    def __init__(self, name: str, asset: str, emoji: str, action: str = "",
                 kd_only: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "FishTile")
        self._asset = asset
        self._name = name
        self._kd_only = kd_only
        self.setToolTip(name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignHCenter)

        self.img = QLabel()
        self.img.setProperty("class", "FishImg")
        self.img.setFixedSize(38, 38)
        self.img.setAlignment(Qt.AlignCenter)
        self._load_img(asset, emoji)
        layout.addWidget(self.img, alignment=Qt.AlignHCenter)

        nm = QLabel(name)
        nm.setProperty("class", "FishName")
        nm.setStyleSheet(f"color: {C['text']}; font-family: {MONO}; font-size: 11px;")
        nm.setAlignment(Qt.AlignCenter)
        nm.setMaximumWidth(84)
        nm.setWordWrap(True)
        layout.addWidget(nm)

        if kd_only:
            seg_opts = [("keep", "K"), ("drop", "D")]
        elif is_bait_fish(asset):
            # A bait fish does not open: a right-click puts it on the rod
            seg_opts = [("keep", "K"), ("drop", "D"), ("use", "U")]
            self.setToolTip(f"{name}. U: use it as bait. Each cast puts the next one on the rod "
                            "instead of pressing the bait key, in the order they were found.")
        else:
            seg_opts = [("keep", "K"), ("drop", "D"), ("open", "O")]
        action = normalize_fish_action(action, asset)
        if kd_only and action in ("open", "use"):
            action = "keep"
        self.seg = Segmented(seg_opts, action)
        self.seg.setFixedHeight(20)
        self.seg.changed.connect(self._on_seg)
        layout.addWidget(self.seg)
        self.setAction(action)

    def _load_img(self, asset: str, emoji: str) -> None:
        path = get_resource_path(asset)
        if os.path.exists(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.img.setPixmap(pix.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        # No picture: the name's first letter (no emoji anywhere in the GUI)
        self.img.setText(emoji or self._name[:1].upper())
        self.img.setStyleSheet(self.img.styleSheet() + " font-size: 18px; font-weight: 700;")

    def _on_seg(self, action: str) -> None:
        self.setAction(action)
        self.changed.emit(action)

    def setAction(self, action: str) -> None:
        cls = "FishImg"
        if action == "keep": cls += " FishImgKeep"
        elif action == "drop": cls += " FishImgDrop"
        elif action == "open": cls += " FishImgOpen"
        elif action == "use": cls += " FishImgUse"
        self.img.setProperty("class", cls)
        self.img.style().unpolish(self.img)
        self.img.style().polish(self.img)
        self.seg.setValue(action)


class TimingModal(QDialog):
    saved = Signal(dict)  # {timing_key: seconds}

    GROUPS: List[Tuple[str, List[Tuple[str, str, int, int, int]]]] = [
        ("Fish Clicking", [
            ("Cursor settle before click", "timing_cursor_settle", 12,  3,  50),
            ("Mouse button hold",          "timing_button_hold",    8,  3,  50),
            ("Post-click settle",          "timing_post_click",    35, 10, 100),
        ]),
        ("Click Rhythm (Human-like)", [
            ("Min reaction delay", "timing_human_min", 150,  50,  800),
            ("Max reaction delay", "timing_human_max", 400, 100, 1200),
        ]),
        ("Key Presses", [
            ("Key hold duration",     "timing_key_hold",   25, 10, 100),
            ("Pre-key window settle", "timing_key_settle", 30, 10,  60),
        ]),
        ("Bait & Cast", [
            ("Bait → Cast key delay", "timing_cast_interkey", 50, 20, 200),
            ("Max wait for a bite",    "timing_minigame_timeout", 6000, 3000, 30000),
            ("Wait before next cast",  "timing_recast_wait",      4000,    0, 10000),
        ]),
        ("Item Handling", [
            ("Wait for item after catch",    "timing_catch_wait",     400, 100, 1500),
            ("Wait after right-click (open)","timing_open_wait",      100,  50,  500),
            ("Dead-fish re-check delay",     "timing_dead_fish_check",100,  50,  500),
        ]),
        ("Drop Action", [
            ("Pause between drop steps", "timing_drop_settle", 120, 50, 600),
        ]),
        ("Quick Skip", [
            ("Gap between mount-key presses", "timing_quickskip_between", 100, 50, 600),
            ("Settle after quick skip",    "timing_quickskip_after",   100, 50, 400),
        ]),
    ]

    def __init__(self, current: Dict[str, float], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Timing Settings")
        self.setModal(True)
        self.resize(450, 640)
        self._sliders: Dict[str, QSlider] = {}
        self._labels: Dict[str, QLabel] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # header
        header = QFrame()
        header.setProperty("class", "ModalHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 12, 12, 12)
        title = QLabel("TIMING SETTINGS")
        title.setProperty("class", "ModalTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        close_btn = QPushButton()
        close_btn.setIcon(make_icon("x", 12, C["text_dim"]))
        close_btn.setProperty("class", "BtnGhost")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setToolTip("Close")
        close_btn.setAccessibleName("Close")
        close_btn.clicked.connect(self.reject)
        hl.addWidget(close_btn)
        outer.addWidget(header)

        # body (scrolls down only: every row fits the width)
        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)
        intro = QLabel("These are the waits between clicks and key presses.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {C['text_mute']}; font-family: {MONO}; font-size: 11px;")
        bl.addWidget(intro)
        for group_name, items in self.GROUPS:
            bl.addLayout(section_divider(group_name))
            for label, key, default_ms, mn, mx in items:
                value_ms = int(round(current.get(key, default_ms / 1000.0) * 1000))
                value_ms = max(mn, min(mx, value_ms))
                row = QHBoxLayout()
                row.setSpacing(12)
                lbl = QLabel(label)
                lbl.setStyleSheet(f"color: {C['text']}; font-family: {MONO}; font-size: 12px;")
                lbl.setWordWrap(True)
                lbl.setMinimumWidth(150)
                row.addWidget(lbl, 1)
                slider = QSlider(Qt.Horizontal)
                slider.setMinimum(mn)
                slider.setMaximum(mx)
                slider.setValue(value_ms)
                slider.setFixedHeight(20)
                slider.setMinimumWidth(100)
                row.addWidget(slider, 1)
                val = QLabel(f"{value_ms}ms")
                val.setProperty("class", "AccentText")
                val.setFixedWidth(68)
                val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                slider.valueChanged.connect(lambda v, lab=val: lab.setText(f"{v}ms"))
                row.addWidget(val)
                bl.addLayout(row)
                self._sliders[key] = slider
                self._labels[key] = val
        bl.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # footer
        footer = QFrame()
        footer.setProperty("class", "ModalFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 12, 16, 12)
        reset_btn = QPushButton("  Reset Defaults")
        reset_btn.setProperty("class", "BtnGhost")
        reset_btn.setIcon(make_icon("reset", 12, C["text"]))
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.clicked.connect(self._reset)
        fl.addWidget(reset_btn)
        fl.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setProperty("class", "Btn"); cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        save = QPushButton("  Save"); save.setProperty("class", "BtnPrimary")
        save.setIcon(make_icon("save", 12, C["on_accent"]))
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._save)
        fl.addWidget(cancel)
        fl.addWidget(save)
        outer.addWidget(footer)
        no_enter_default(self)
        help_visuals.fit_to_screen(self)   # short screens: keep the footer on screen

    def _reset(self) -> None:
        for _g, items in self.GROUPS:
            for _label, key, default_ms, _mn, _mx in items:
                self._sliders[key].setValue(default_ms)

    def _save(self) -> None:
        h_min = self._sliders["timing_human_min"].value()
        h_max = self._sliders["timing_human_max"].value()
        if h_min >= h_max:
            QMessageBox.warning(self, "Invalid timing",
                                "Human-like max delay must be greater than min delay.")
            return
        out: Dict[str, float] = {}
        for _g, items in self.GROUPS:
            for _label, key, _d, _mn, _mx in items:
                out[key] = self._sliders[key].value() / 1000.0
        self.saved.emit(out)
        self.accept()


class SetupWizard(QDialog):
    """Step-by-step setup of one activity (Fishing or Jigsaw).

    Not a mock-up: every step drives the main window's real state — window
    rows, position capture, settings widgets — so what is set here is set,
    and changes made in the main window meanwhile show up here too (the
    window's ``setup_changed`` signal). Not modal: the grid overlay and the
    game must stay clickable while it is open. Every step can be skipped;
    the last one shows what is still missing."""

    def __init__(self, win: "FishbotWindow", mode: str):
        super().__init__(win)
        self.win = win
        self.mode = mode
        jig = mode == MODE_JIGSAW
        self.setWindowTitle("Jigsaw Puzzle setup" if jig else "Fishing setup")
        self.setModal(False)
        self.resize(720, 700)
        self._step = 0
        self._was_capturing = False
        self._pills: Dict[str, CoordPill] = {}
        self._binds: List[Tuple[QCheckBox, QCheckBox]] = []   # (wizard box, settings box)
        self._radios: Dict[str, QRadioButton] = {}
        self._status: Dict[str, QLabel] = {}
        self._qs_radios: Dict[str, QRadioButton] = {}

        if jig:
            self._steps = [("Welcome", self._build_welcome_jigsaw),
                           ("Windows", self._build_windows),
                           ("Puzzle grid", self._build_grid),
                           ("Confirm", self._build_confirm_jigsaw),
                           ("Crates", self._build_crates),
                           ("Ready", self._build_ready)]
        else:
            self._steps = [("Welcome", self._build_welcome_fishing),
                           ("Windows", self._build_windows),
                           ("Inventory", self._build_pages),
                           ("Bait", self._build_bait),
                           ("Catch", self._build_fish),
                           ("Quick skip", self._build_quick_skip),
                           ("Ready", self._build_ready)]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setProperty("class", "ModalHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 12, 12, 12)
        title = QLabel(("JIGSAW PUZZLE" if jig else "FISHING") + " SETUP")
        title.setProperty("class", "ModalTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        close_btn = QPushButton()
        close_btn.setIcon(make_icon("x", 12, C["text_dim"]))
        close_btn.setProperty("class", "BtnGhost")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setToolTip("Close (what you set is kept)")
        close_btn.clicked.connect(self.close)
        hl.addWidget(close_btn)
        outer.addWidget(header)

        chips = QFrame()
        chips.setStyleSheet(f"border-bottom: 1px solid {C['line']};")
        cl = QHBoxLayout(chips)
        cl.setContentsMargins(16, 10, 16, 10)
        cl.setSpacing(6)
        self._chips: List[Tuple[QLabel, QLabel]] = []
        for i, (name, _builder) in enumerate(self._steps):
            num = QLabel(str(i + 1))
            num.setProperty("class", "WizNum")
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignCenter)
            txt = QLabel(name.upper())
            txt.setProperty("class", "WizStep")
            for lbl in (num, txt):
                lbl.setCursor(Qt.PointingHandCursor)
                lbl.setToolTip(f"Go to step {i + 1}: {name}")
                lbl.installEventFilter(self)
            cl.addWidget(num)
            cl.addWidget(txt)
            if i < len(self._steps) - 1:
                line = QFrame()
                line.setFixedSize(14, 1)
                line.setProperty("class", "WizLine")
                cl.addWidget(line)
            self._chips.append((num, txt))
        cl.addStretch(1)
        outer.addWidget(chips)

        self.lock_banner = QLabel("A bot is running: stop it to change the setup. What is shown here "
                                  "stays up to date.")
        self.lock_banner.setObjectName("CaptureBanner")
        self.lock_banner.setProperty("class", "NoticeText")
        self.lock_banner.setWordWrap(True)
        self.lock_banner.setContentsMargins(12, 6, 12, 6)
        self.lock_banner.setVisible(False)
        outer.addWidget(self.lock_banner)

        self.stack = QStackedWidget()
        for _name, builder in self._steps:
            page = builder()
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.NoFrame)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sa.setWidget(page)
            self.stack.addWidget(sa)
        outer.addWidget(self.stack, 1)

        footer = QFrame()
        footer.setProperty("class", "ModalFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 12, 16, 12)
        self.guide_btn = guide_button(lambda: open_guide(self, None))
        fl.addWidget(self.guide_btn)
        fl.addStretch(1)
        self.back_btn = QPushButton("Back")
        self.back_btn.setProperty("class", "Btn")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(self._back)
        self.next_btn = QPushButton("Next  ")
        self.next_btn.setProperty("class", "BtnPrimary")
        self.next_btn.setLayoutDirection(Qt.RightToLeft)
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.clicked.connect(self._next)
        fl.addWidget(self.back_btn)
        fl.addWidget(self.next_btn)
        outer.addWidget(footer)
        no_enter_default(self)

        win.setup_changed.connect(self.refresh)
        self._go(0)
        help_visuals.fit_to_screen(self)   # short screens: keep the footer on screen

    # ------------------------------------------------------------- navigation
    def step(self) -> int:
        return self._step

    def stepNames(self) -> List[str]:
        return [n for n, _ in self._steps]

    def _go(self, step: int) -> None:
        self._step = max(0, min(step, len(self._steps) - 1))
        self.stack.setCurrentIndex(self._step)
        self.back_btn.setVisible(self._step > 0)
        last = self._step == len(self._steps) - 1
        self.next_btn.setText("Finish  " if last else "Next  ")
        self.next_btn.setIcon(make_icon("check" if last else "chevron-right", 12, C["on_accent"]))
        self.refresh()

    def _next(self) -> None:
        if self._step >= len(self._steps) - 1:
            self.win._set_tab("dashboard")
            self.win.tabs_bar.setActive("dashboard")
            self.close()
            return
        self._go(self._step + 1)

    def _back(self) -> None:
        self._go(self._step - 1)

    def eventFilter(self, obj, ev) -> bool:
        """A click on a step chip goes to that step."""
        if ev.type() == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            for i, pair in enumerate(self._chips):
                if obj in pair:
                    self._go(i)
                    return True
        return super().eventFilter(obj, ev)

    def _step_done(self, name: str) -> Optional[bool]:
        """Whether a step's part of the setup is complete (None: the step
        sets nothing that can be missing)."""
        win, cfg = self.win, self.win.config
        if name == "Windows":
            return bool(win.selected_window_names())
        if name == "Inventory":
            return bool(cfg.get("inv_page_1_pos"))
        if name == "Bait":
            return bool(win.settings.selectedBaitKeys())
        if name == "Catch":
            drops = cfg.get("auto_fish_handling") and any(
                a == "drop" for a in (cfg.get("fish_actions") or {}).values())
            return not drops or bool(cfg.get("confirm_button_pos"))
        if name == "Quick skip":
            return win._armor_skip_problem() is None
        if name == "Puzzle grid":
            return grid_ready(cfg)
        if name == "Confirm":     # Jigsaw: the crate dialog's Yes and the page tabs
            return bool(cfg.get("confirm_button_pos")) and bool(cfg.get("inv_page_1_pos"))
        return None

    def _paint_chips(self) -> None:
        """✓ only for steps that are really complete: a step that was skipped
        and still misses something is marked, not ticked."""
        for i, (num, txt) in enumerate(self._chips):
            done = self._step_done(self._steps[i][0])
            if i == self._step:
                state = "active"
            elif done is True or (done is None and i < self._step):
                state = "done"
            elif done is False and i < self._step:
                state = "missing"
            else:
                state = ""
            num.setText({"done": "✓", "missing": "!"}.get(state, str(i + 1)))
            for w in (num, txt):
                if w.property("state") != state:
                    w.setProperty("state", state)
                    w.style().unpolish(w)
                    w.style().polish(w)

    def keyPressEvent(self, ev) -> None:
        # Esc while a position is being captured cancels the capture, not the wizard
        if ev.key() == Qt.Key_Escape and self.win._capture.is_active():
            self.win._cancel_capture()
            ev.accept()
            return
        if ev.key() == Qt.Key_Escape:
            # QDialog's Esc (reject) hides without a closeEvent: a typed
            # bait quantity would be lost. Close the way the ✕ does.
            ev.accept()
            self.close()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev) -> None:
        if hasattr(self, "_qty"):
            self._qty_done()        # a typed quantity counts without Enter / leaving the field
        if self.win._capture.is_active():
            self.win._cancel_capture()
        try:
            self.win.setup_changed.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(ev)

    # ---------------------------------------------------------------- helpers
    def _page(self, title: str, intro: str) -> Tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(10)
        t = QLabel(title)
        t.setProperty("class", "WizTitle")
        l.addWidget(t)
        if intro:
            b = QLabel(intro)
            b.setProperty("class", "WizBody")
            b.setWordWrap(True)
            b.setTextFormat(Qt.RichText)
            l.addWidget(b)
        return w, l

    def _picture(self, topic: str) -> QWidget:
        """The help picture of ``topic``, kept small enough to leave the
        step's controls in view."""
        _sync_help_theme()
        pic = help_visuals.Illustration(help_visuals.TOPICS[topic]["draw"])
        pic.setMaximumHeight(290)
        pic.setMinimumSize(390, 225)
        return pic

    def _status_label(self, key: str) -> QLabel:
        lbl = QLabel("")
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        lbl.setProperty("class", "WizBody")
        self._status[key] = lbl
        return lbl

    def _pill(self, key: str, label: str, optional: bool, note: str = "") -> CoordPill:
        pill = CoordPill(label, optional=optional)
        if note:
            pill.setOptional(optional, note)
        pill.clicked.connect(lambda k=key: self.win._begin_capture(k))
        pill.unset_requested.connect(lambda k=key: self.win._on_unset_coord(k))
        self._pills[key] = pill
        return pill

    def _bind_check(self, text: str, target: QCheckBox, tip: str = "") -> QCheckBox:
        """A checkbox mirroring one of the window's own (changing it changes
        the setting, which saves it)."""
        cb = QCheckBox(text)
        cb.setChecked(target.isChecked())
        if tip:
            cb.setToolTip(tip)
        cb.toggled.connect(lambda v, t=target: t.isChecked() != v and t.setChecked(v))
        self._binds.append((cb, target))
        return cb

    @staticmethod
    def _ok(text: str) -> str:
        return f"<span style='color:{C['green']}'>✓ {text}</span>"

    @staticmethod
    def _bad(text: str) -> str:
        return f"<span style='color:{C['red']}'>✗ {text}</span>"

    @staticmethod
    def _wait(text: str) -> str:
        return f"<span style='color:{C['orange']}'>… {text}</span>"

    # ------------------------------------------------------------------ steps
    def _build_welcome_fishing(self) -> QWidget:
        w, l = self._page(
            "Welcome to Fishing",
            "The bot casts, plays the fishing minigame and handles the catch in up to "
            f"{MAX_WINDOWS} game clients at once.<br><br>"
            "<b>Before you start:</b> open the game, log in, stand at the water with a fishing rod "
            "equipped, put your bait on the hotbar keys and open the inventory.<br><br>"
            "The next steps pick the game windows, show the bot where your inventory is and which "
            "bait keys to use. Every step can be skipped and changed later on the tabs.<br><br>"
            "Most steps set a <b>position</b>: click it here, then click that point in the game:")
        l.addWidget(self._picture("set_position"))
        l.addStretch(1)
        return w

    def _build_welcome_jigsaw(self) -> QWidget:
        w, l = self._page(
            "Welcome to the Jigsaw Puzzle",
            "The solver opens fish puzzle crates and places the pieces on the Fishing Jigsaw "
            "board, in up to " f"{MAX_WINDOWS} game clients.<br><br>"
            "<b>Before you start:</b> open the game, have puzzle crates in the crate slot or "
            "inventory and open the Fish Puzzle window.<br><br>"
            "The solver needs a move table, built once on this PC (1–5 minutes). "
            "You can keep setting up while it is built. Move table:")
        l.addWidget(self._status_label("solver"))
        l.addStretch(1)
        return w

    def _build_windows(self) -> QWidget:
        w, l = self._page(
            "Pick the game windows",
            "Tick every game client the bot should run in. All of them must have the same window "
            "size: positions are set once and used in every window.")
        self._win_list = QVBoxLayout()
        self._win_list.setSpacing(4)
        l.addLayout(self._win_list)
        row = QHBoxLayout()
        refresh = QPushButton("  Refresh")
        refresh.setProperty("class", "BtnGhost")
        refresh.setIcon(make_icon("refresh", 12, C["text"]))
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self._refresh_windows)
        row.addWidget(refresh)
        row.addStretch(1)
        l.addLayout(row)
        l.addWidget(self._status_label("windows"))
        l.addWidget(self._picture("windows"))
        l.addStretch(1)
        self._win_boxes: Dict[str, QCheckBox] = {}
        self._fill_windows()
        return w

    def _refresh_windows(self) -> None:
        self.win.refresh_window_list()
        self._fill_windows()
        self.refresh()

    def _fill_windows(self) -> None:
        while self._win_list.count():
            item = self._win_list.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._win_boxes = {}
        names = [self.win.dashboard.window_rows[0].combo.itemData(i)
                 for i in range(1, self.win.dashboard.window_rows[0].combo.count())]
        if not names:
            empty = QLabel("No windows found. Start the game, then press Refresh.")
            empty.setProperty("class", "Hint")
            self._win_list.addWidget(empty)
            return
        selected = set(self.win.selected_window_names())
        for n in names:
            cb = QCheckBox(n)
            cb.setChecked(n in selected)
            cb.toggled.connect(self._windows_ticked)
            self._win_boxes[n] = cb
            self._win_list.addWidget(cb)

    def _windows_ticked(self, _checked: bool = False) -> None:
        names = [n for n, cb in self._win_boxes.items() if cb.isChecked()]
        if len(names) > MAX_WINDOWS:
            # The box just ticked is the one too many: untick it and keep the
            # others (cutting the list in its order could drop another one).
            box = self.sender()
            if isinstance(box, QCheckBox):
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
                names = [n for n, cb in self._win_boxes.items() if cb.isChecked()]
            names = names[:MAX_WINDOWS]
            if "windows" in self._status:
                QToolTip.showText(QCursor.pos(), f"At most {MAX_WINDOWS} windows.")
        self.win.apply_window_selection(names)

    def _build_pages(self) -> QWidget:
        w, l = self._page(
            "Show the bot your inventory",
            "Open the inventory in the game. Click <b>Page 1</b> below, then click tab <b>I</b> of "
            "the inventory in the game window. Do the same for the further pages your client has, "
            "in order. <b>Esc</b> or a right click cancels.")
        for page_row in [(1, 2, 3, 4), (5, 6, 7, 8)]:
            row = QHBoxLayout()
            row.setSpacing(8)
            for n in page_row:
                row.addWidget(self._pill(f"page{n}", f"Page {n}", optional=n > 1), 1)
            l.addLayout(row)
        l.addWidget(self._status_label("pages"))
        l.addWidget(self._picture("inventory_tabs"))
        show_me = guide_button(lambda: open_guide(self, "inventory_tabs"))
        show_me.setText("  Open this in the guide")
        l.addWidget(show_me, 0, Qt.AlignLeft)
        l.addStretch(1)
        return w

    def _build_bait(self) -> QWidget:
        w, l = self._page(
            "Bait",
            "Tick the hotbar keys your bait is on and how much bait each key holds. The bot counts "
            "the bait down per cast and stops when it is used up; after restocking press "
            "<b>Reset Bait</b> on the Dashboard.")
        s = self.win.settings
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, k in enumerate(SettingsTab.BAIT_KEYS):
            grid.addWidget(self._bind_check(k, s.bait_checks[k]), i // 4, i % 4)
        l.addLayout(grid)
        qty = QHBoxLayout()
        qty.addWidget(QLabel("Bait per key"))
        self._qty = QLineEdit(s.bait_qty_input.text())
        self._qty.setFixedWidth(80)
        self._qty.setValidator(QIntValidator(1, 100000, self._qty))
        self._qty.editingFinished.connect(self._qty_done)
        qty.addWidget(self._qty)
        qty.addStretch(1)
        l.addLayout(qty)
        l.addSpacing(6)
        l.addWidget(self._bind_check("Human-like mouse timing", s.human_check,
                                     s.human_check.toolTip()))
        l.addWidget(self._bind_check("Sound alert when all windows are out of bait", s.sound_check))
        l.addWidget(self._status_label("bait"))
        l.addWidget(self._picture("bait"))
        l.addStretch(1)
        return w

    def _qty_done(self) -> None:
        s = self.win.settings
        if self._qty.text() and self._qty.text() != s.bait_qty_input.text():
            s.bait_qty_input.setText(self._qty.text())
            s._on_changed()

    def _build_fish(self) -> QWidget:
        w, l = self._page(
            "What to do with the catch (optional)",
            "With automatic handling on, the bot keeps, drops/sells or opens each catch as you "
            "choose. Dropping always needs <b>Confirm</b> (the Yes button of the game's drop "
            "dialog). Set <b>Drop / Sell</b> only if your client first asks drop or sell; if it only "
            "asks yes/no, leave Drop / Sell empty. Skip this step to keep everything.")
        l.addWidget(self._bind_check("Handle the catch automatically", self.win.settings.auto_check))
        btn = QPushButton("  Choose fish and items…")
        btn.setProperty("class", "Btn")
        btn.setIcon(make_icon("fish", 14, C["text"]))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.win.open_fish_modal)
        l.addWidget(btn, 0, Qt.AlignLeft)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._pill("drop", "Drop / Sell", optional=True), 1)
        row.addWidget(self._pill("confirm", "Confirm", optional=True, note="for drops"), 1)
        l.addLayout(row)
        l.addWidget(self._status_label("fish"))
        l.addWidget(self._picture("drop_confirm"))
        g = guide_button(lambda: open_guide(self, "drop_confirm"))
        g.setText("  Open this in the guide")
        l.addWidget(g, 0, Qt.AlignLeft)
        l.addStretch(1)
        return w

    def _build_quick_skip(self) -> QWidget:
        w, l = self._page(
            "Quick skip (optional)",
            "Ends the fishing animation after each catch so the next cast comes sooner. "
            "<b>Horse</b> mounts and dismounts (Ctrl + the mount key: G unless you change it on the "
            "Fishing tab). <b>Armor</b> right-clicks a spare "
            "armor in the inventory, which swaps it with the worn one. An armor takes "
            "<b>two slots: one column, two rows</b>.")
        group = QButtonGroup(self)
        for key, text in (("off", "Off — wait for the animation"), ("horse", "Horse"),
                          ("armor", "Armor (needs a spare armor in the inventory)")):
            rb = QRadioButton(text)
            group.addButton(rb)
            rb.toggled.connect(lambda on, k=key: on and self._set_quick_skip(k))
            self._qs_radios[key] = rb
            l.addWidget(rb)
        self._armor_box = QWidget()
        al = QVBoxLayout(self._armor_box)
        al.setContentsMargins(24, 4, 0, 0)
        al.setSpacing(6)
        note = QLabel("Show the armor's inventory page in the game, then click <b>Spare Armor</b> and the "
                      "spare armor (not the one you wear). Set the page tabs first (Inventory step).")
        note.setProperty("class", "WizBody")
        note.setWordWrap(True)
        note.setTextFormat(Qt.RichText)
        al.addWidget(note)
        al.addWidget(self._pill("armor", "Spare Armor", optional=False))
        l.addWidget(self._armor_box)
        l.addWidget(self._status_label("quick_skip"))
        l.addWidget(self._picture("armor_slot"))
        g = guide_button(lambda: open_guide(self, "quick_skip"))
        g.setText("  Open this in the guide")
        l.addWidget(g, 0, Qt.AlignLeft)
        l.addStretch(1)
        return w

    def _quick_skip_choice(self) -> str:
        s = self.win.settings
        if not s.qs_enable.isChecked():
            return "off"
        return "armor" if s.qs_armor.isChecked() else "horse"

    def _set_quick_skip(self, choice: str) -> None:
        """Drives the FISHING tab's own Quick Skip boxes (they save it)."""
        s = self.win.settings
        if choice == self._quick_skip_choice():
            return
        if choice == "off":
            s.qs_enable.setChecked(False)
            return
        s.qs_enable.setChecked(True)
        (s.qs_armor if choice == "armor" else s.qs_horse).setChecked(True)

    def _build_grid(self) -> QWidget:
        w, l = self._page(
            "Open the puzzle window",
            "Open the <b>Fish Puzzle</b> window in every game client, away from the middle of the "
            "screen (the game opens its dialogs there). The bot finds the window by itself in each "
            "client, wherever you put it, and takes the grid and both crate slots from it; nothing "
            "needs to be set here.<br><br>Only if the bot reports that it cannot find the window "
            "(a server whose puzzle looks different): press <b>Define Grid</b> and lay the ghost grid "
            "exactly on the 24 cells, then press Enter (Esc cancels).")
        btn = QPushButton("  Define Grid")
        btn.setProperty("class", "Btn")
        btn.setIcon(make_icon("grid", 12, C["text"]))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.win._define_jigsaw_grid(sender_dialog=self))
        l.addWidget(btn, 0, Qt.AlignLeft)
        l.addWidget(self._status_label("grid"))
        l.addWidget(self._picture("jigsaw_grid"))
        g = guide_button(lambda: open_guide(self, "jigsaw_layout"))
        g.setText("  Where to put the puzzle window")
        l.addWidget(g, 0, Qt.AlignLeft)
        l.addStretch(1)
        return w

    def _build_confirm_jigsaw(self) -> QWidget:
        w, l = self._page(
            "The crate's Yes button",
            "Open a puzzle crate by hand so the game asks <i>open the crate?</i>. While that dialog "
            "is up, click <b>Confirm</b> below and then the dialog's <b>Yes</b> button in the "
            "game. Fishing uses the same point for its drop dialog.")
        l.addWidget(self._pill("confirm", "Confirm", optional=False))
        l.addWidget(self._status_label("confirm"))
        l.addWidget(self._picture("jigsaw_confirm"))
        l.addSpacing(6)
        pages = QLabel("The solver also needs the inventory page tabs: it searches them for crates "
                       "to refill the crate slots and does not play while every page is full. "
                       "Page 1 is required; set the further pages your client has:")
        pages.setProperty("class", "WizBody")
        pages.setWordWrap(True)
        l.addWidget(pages)
        row = QHBoxLayout()
        row.setSpacing(8)
        for n in (1, 2, 3, 4):
            row.addWidget(self._pill(f"page{n}", f"Page {n}", optional=n > 1), 1)
        l.addLayout(row)
        g = guide_button(lambda: open_guide(self, "jigsaw_confirm"))
        g.setText("  Open this in the guide")
        l.addWidget(g, 0, Qt.AlignLeft)
        l.addStretch(1)
        return w

    def _build_crates(self) -> QWidget:
        w, l = self._page("Which crates to open", "")
        group = QButtonGroup(self)
        for key, text, tip in JigsawTab.PRIORITIES:
            rb = QRadioButton(text)
            group.addButton(rb)
            rb.toggled.connect(lambda on, k=key: on and self._set_priority(k))
            self._radios[key] = rb
            l.addWidget(rb)
            d = QLabel(tip)
            d.setProperty("class", "Hint")
            d.setWordWrap(True)
            d.setStyleSheet("padding-left: 24px;")
            l.addWidget(d)
        l.addWidget(self._picture("jigsaw_crates"))
        l.addStretch(1)
        return w

    def _set_priority(self, key: str) -> None:
        rb = self.win.jigsaw_tab.priority_radios.get(key)
        if rb is not None and not rb.isChecked():
            rb.setChecked(True)      # the tab's own handler saves it

    def _build_ready(self) -> QWidget:
        w, l = self._page("Ready?", "")
        l.addWidget(self._status_label("ready"))
        start_hint = QLabel(
            f"Press <b>Finish</b>, then <b>START {'JIGSAW' if self.mode == MODE_JIGSAW else 'FISHING'}</b> "
            "at the top of the Dashboard. <b>F5</b> pauses and resumes at any time, also while the "
            "game has the focus.")
        start_hint.setProperty("class", "WizBody")
        start_hint.setWordWrap(True)
        start_hint.setTextFormat(Qt.RichText)
        l.addWidget(start_hint)
        l.addStretch(1)
        return w

    # ---------------------------------------------------------------- refresh
    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.refresh()

    def refresh(self) -> None:
        """Re-reads everything from the main window."""
        win = self.win
        cfg = win.config
        # Nothing here may change the setup while a bot runs: the main
        # window locks its own controls then, and these drive the same ones.
        running = bool(win.bots) or win.jigsaw.is_running()
        self.lock_banner.setVisible(running)
        self.stack.setEnabled(not running)
        capturing = win._capture_pill if win._capture.is_active() else None
        for key, pill in self._pills.items():
            pill.setCoords(cfg.get(win._coord_key_to_cfg(key)))
            pill.setCapturing(key == capturing)
        if self._was_capturing and capturing is None and self.isVisible():
            self.raise_()          # back in front after the click in the game
        self._was_capturing = capturing is not None
        for box, target in self._binds:
            if box.isChecked() != target.isChecked():
                box.blockSignals(True)
                box.setChecked(target.isChecked())
                box.blockSignals(False)
        if hasattr(self, "_qty") and not self._qty.hasFocus():
            self._qty.setText(win.settings.bait_qty_input.text())
        prio = win.jigsaw_tab.priority()
        for key, rb in self._radios.items():
            if rb.isChecked() != (key == prio):
                rb.blockSignals(True)
                rb.setChecked(key == prio)
                rb.blockSignals(False)
        if hasattr(self, "_win_boxes"):
            selected = set(win.selected_window_names())
            for n, cb in self._win_boxes.items():
                if cb.isChecked() != (n in selected):
                    cb.blockSignals(True)
                    cb.setChecked(n in selected)
                    cb.blockSignals(False)

        choice = self._quick_skip_choice()
        for key, rb in self._qs_radios.items():
            if rb.isChecked() != (key == choice):
                rb.blockSignals(True)
                rb.setChecked(key == choice)
                rb.blockSignals(False)
        if hasattr(self, "_armor_box"):
            self._armor_box.setVisible(choice == "armor")

        items = {k: (label, state, tip) for k, label, state, tip in win._readiness_items(self.mode)}
        n_sel = len(win.selected_window_names())
        st = self._status
        if "windows" in st:
            st["windows"].setText(self._ok(f"{n_sel} window(s) selected") if n_sel
                                  else self._bad("No window selected yet"))
        if "pages" in st:
            st["pages"].setText(self._ok("Page 1 is set") if cfg.get("inv_page_1_pos")
                                else self._bad("Page 1 is not set yet"))
        if "bait" in st:
            keys = win.settings.selectedBaitKeys()
            st["bait"].setText(self._ok(f"{len(keys)} bait key(s) × {win.config.get('bait_quantity', 200)}")
                               if keys else self._bad("Tick at least one bait key"))
        if "fish" in st:
            drops = any(a == "drop" for a in cfg.get("fish_actions", {}).values())
            if not cfg.get("auto_fish_handling"):
                st["fish"].setText(self._ok("Off — everything is kept"))
            elif drops and not cfg.get("confirm_button_pos"):
                st["fish"].setText(self._bad("Some fish are set to drop: set Confirm"))
            else:
                st["fish"].setText(self._ok("Automatic handling is on"))
        if "quick_skip" in st:
            problem = win._armor_skip_problem()
            st["quick_skip"].setText(self._bad(problem) if problem else
                                     self._ok({"off": "Off", "horse": "Horse: no position needed",
                                               "armor": "Spare armor is set" + (f" (inventory page {cfg['armor_slot_page']})"
                                                         if cfg.get("armor_slot_page") else "")}
                                              [choice]))
        if "grid" in st:
            if auto_grid(cfg):
                st["grid"].setText(self._ok("Found by itself in each window"
                                            + (" (a defined grid is the fallback)"
                                               if valid_grid_bounds(cfg.get("jigsaw_grid_bounds")) else "")))
            else:
                st["grid"].setText(self._ok("The grid is set") if valid_grid_bounds(cfg.get("jigsaw_grid_bounds"))
                                   else self._bad("The grid is not set yet"))
        if "confirm" in st:
            confirm_ok, page_ok = bool(cfg.get("confirm_button_pos")), bool(cfg.get("inv_page_1_pos"))
            st["confirm"].setText(self._ok("Confirm and Page 1 are set") if confirm_ok and page_ok
                                  else self._bad("Confirm is not set yet" if not confirm_ok
                                                 else "Page 1 is not set yet"))
        if "solver" in st:
            state = win.solver_cache_manager.state()
            text = SolverCacheManager.describe(state)
            st["solver"].setText(self._ok(text) if state == "ready" else
                                 self._wait(text) if state == "building" else self._bad(text))
        if "ready" in st:
            lines = []
            for key, (label, state, tip) in items.items():
                fmt = self._ok if state == "ok" else self._wait if state == "wait" else self._bad
                lines.append(fmt(f"{label}: {tip}"))
            if all(state == "ok" for _l, state, _t in items.values()):
                lines.append("<br><b>Everything is set.</b>")
            else:
                lines.append("<br>Click a step marked ! at the top to go back to it, or fix it later on the tabs.")
            st["ready"].setText("<br>".join(lines))
        self._paint_chips()


class SolverCacheManager(QObject):
    """
    App-wide manager for the jigsaw solver_cache.npz file.

    The cache (~90 MB on disk) is required before any JigsawBot can start. We do NOT
    auto-build at app launch; the build is only triggered when the user selects
    Jigsaw mode AND no cache exists. Build runs on a daemon
    thread; consumers subscribe to `state_changed` for UI updates.
    """

    # state ∈ {"missing", "building", "ready", "failed"}; second arg = message
    state_changed = Signal(str, str)
    # While building: percent (0-100) and what is being done
    progress_changed = Signal(int, str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._error: str = ""
        self._state: str = "ready" if self._cache_exists() else "missing"
        self._progress = (100 if self._state == "ready" else 0, "")

    @staticmethod
    def cache_path() -> str:
        """The same path Deterministic.load_cache() uses (next to the
        program, or a per-user folder when that cannot be written to)."""
        from jigsaw_solver import cache_location   # light: no numba import
        return cache_location.cache_path()

    @classmethod
    def _cache_exists(cls) -> bool:
        try:
            return os.path.exists(cls.cache_path()) and os.path.getsize(cls.cache_path()) > 0
        except OSError:
            return False

    def state(self) -> str:
        # Promote "missing" → "ready" if the file appeared externally.
        if self._state == "missing" and self._cache_exists():
            self._state = "ready"
        return self._state

    def is_ready(self) -> bool:
        return self.state() == "ready"

    @staticmethod
    def describe(state: str) -> str:
        return {
            "ready": "Ready.",
            "building": "Building the move table. This takes 1–5 minutes — Start is available when it is done.",
            "failed": "Building the move table failed. Press Retry.",
        }.get(state, "Not built yet — it is built when Jigsaw mode is selected (1–5 minutes).")

    def is_building(self) -> bool:
        return self._state == "building"

    def progress(self) -> Tuple[int, str]:
        """(percent, text) of the build; 100 once the table is ready."""
        if self.state() == "ready":
            return 100, ""
        return self._progress

    def _set_progress(self, fraction: float, text: str) -> None:
        percent = int(round(fraction * 100))
        if (percent, text) == self._progress:
            return
        self._progress = (percent, text)
        self.progress_changed.emit(percent, text)

    def error(self) -> str:
        return self._error

    def ensure_build(self) -> None:
        """Start a background build if the cache is missing. Idempotent."""
        with self._lock:
            if self._state in ("ready", "building"):
                return
            if self._cache_exists():
                self._state = "ready"
                self.state_changed.emit("ready", "Solver cache ready.")
                return
            self._state = "building"
            self._error = ""
            self._progress = (0, "")
            self._thread = threading.Thread(
                target=self._run_build, name="SolverCacheBuild", daemon=True
            )
            self._thread.start()
        self.state_changed.emit(
            "building",
            "Building solver cache. This may take 1–5 minutes — please wait..."
        )

    def _run_build(self) -> None:
        try:
            from jigsaw_solver import cache_location
            # Decided once, with a real write test: the program folder, or
            # the per-user one when the program folder is read-only.
            cache_dir = cache_location.cache_dir(create=True)
            os.makedirs(cache_dir, exist_ok=True)
            os.environ["JIGSAW_SOLVER_CACHE_DIR"] = cache_dir
            self._set_progress(0.01, "Loading the solver")
            # Importing deterministic also imports numba / numpy. The first
            # call to get_solver() runs load_cache() if a file is present, or
            # else _run_numba() to compute the table from scratch.
            from jigsaw_solver import deterministic  # noqa: WPS433
            deterministic.set_progress_callback(self._set_progress)
            try:
                solver = deterministic.get_solver()
            finally:
                deterministic.set_progress_callback(None)
            if solver is None:
                raise RuntimeError("get_solver() returned None")
        except Exception as exc:  # noqa: BLE001
            diag.exc()
            self._state = "failed"
            self._error = str(exc)
            self.state_changed.emit("failed", f"Cache build failed: {exc}")
            return
        self._state = "ready"
        self._set_progress(1.0, "")
        self.state_changed.emit("ready", "Solver cache ready.")


def native_rect_to_logical(left: int, top: int, w: int, h: int,
                           screens: List[Tuple[int, int, int, int, float]]):
    """Where a rectangle in screen pixels (a game window rect) lies in Qt's
    own coordinates, which are scaled by the interface size
    (QT_SCALE_FACTOR, 115 % on a 1080p screen) and by the screen's DPI.

    ``screens`` holds (x, y, w, h, devicePixelRatio) of each QScreen,
    geometry in Qt coordinates. Qt 6 keeps a screen's top-left corner at
    its pixel position and scales from there, so a pixel p on that screen is
    at origin + (p - origin) / dpr in Qt. Returns ((x, y, w, h) in Qt,
    dpr, (dx, dy)): (dx, dy) is where the Qt rectangle's corner, rounded to
    whole Qt units, really lies relative to ``left, top`` in pixels. It is
    rounded outwards, so the Qt rectangle covers the whole window:
    -dpr < dx, dy <= 0."""
    cx, cy = left + w / 2.0, top + h / 2.0
    chosen = None
    for sx, sy, sw, sh, dpr in screens:
        if sx <= cx < sx + sw * dpr and sy <= cy < sy + sh * dpr:
            chosen = (sx, sy, dpr)
            break
    if chosen is None:
        chosen = (screens[0][0], screens[0][1], screens[0][4]) if screens else (0, 0, 1.0)
    ox, oy, dpr = chosen
    dpr = float(dpr) if dpr and dpr > 0 else 1.0
    lx = int(math.floor(ox + (left - ox) / dpr + 1e-6))
    ly = int(math.floor(oy + (top - oy) / dpr + 1e-6))
    lw = int(math.ceil(ox + (left + w - ox) / dpr - 1e-6)) - lx
    lh = int(math.ceil(oy + (top + h - oy) / dpr - 1e-6)) - ly
    dx = ox + (lx - ox) * dpr - left
    dy = oy + (ly - oy) * dpr - top
    return (lx, ly, lw, lh), dpr, (dx, dy)


class JigsawGridOverlay(QWidget):
    """
    Full-screen overlay placed over the game window for aligning the jigsaw
    puzzle grid.  Uses setWindowOpacity (same effect as Tk -alpha) which is
    reliable on every Windows DWM configuration, unlike WA_TranslucentBackground.

    The grid is kept in game-window pixels, as the bots read it. The overlay
    itself lives in Qt coordinates, which the interface size (115 % on a
    1080p screen) and the screen DPI scale: it converts both ways, or the
    saved grid lands off the puzzle by that factor.
    """
    confirmed = Signal(list)  # [x, y, w, h] relative to game window, in pixels

    @staticmethod
    def _screens() -> List[Tuple[int, int, int, int, float]]:
        out = []
        for screen in QApplication.screens():
            g = screen.geometry()
            out.append((g.x(), g.y(), g.width(), g.height(), float(screen.devicePixelRatio())))
        return out

    def __init__(self, win_left: int, win_top: int, win_w: int, win_h: int,
                 grid: list, target_name: str) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        # Solid black background — setWindowOpacity makes the whole window
        # semi-transparent so the game shows through (mirrors Tk -alpha 0.65).
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet("background-color: #000000;")
        self.setWindowOpacity(0.65)
        logical, self._scale, self._offset = native_rect_to_logical(
            win_left, win_top, win_w, win_h, self._screens())
        diag.log("GUI", f"grid overlay: window ({win_left},{win_top},{win_w},{win_h}) px -> "
                        f"Qt {logical} at scale {self._scale:g}, corner offset "
                        f"({self._offset[0]:.2f},{self._offset[1]:.2f}) px")
        self.setGeometry(*logical)
        self._win_w = win_w
        self._win_h = win_h
        self._grid = [float(v) for v in grid]
        self._target_name = target_name
        self._drag_mode: Optional[str] = None
        self._drag_x = 0
        self._drag_y = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # A grid saved on a bigger window could lie outside this one, where
        # it can be neither seen nor dragged.
        self._clamp()

    def _clamp(self) -> None:
        self._grid[2] = max(60.0, min(self._grid[2], float(self._win_w)))
        self._grid[3] = max(40.0, min(self._grid[3], float(self._win_h)))
        self._grid[0] = max(0.0, min(self._grid[0], self._win_w - self._grid[2]))
        self._grid[1] = max(0.0, min(self._grid[1], self._win_h - self._grid[3]))

    def _to_window(self, ev) -> Tuple[float, float]:
        """A mouse event's position in game-window pixels."""
        pos = ev.position()
        return pos.x() * self._scale + self._offset[0], pos.y() * self._scale + self._offset[1]

    def paintEvent(self, _ev) -> None:
        from PySide6.QtGui import QPen
        p = QPainter(self)
        gx, gy, gw, gh = [int(v) for v in self._grid]

        # Dark overlay (window opacity handles the transparency vs background)
        p.fillRect(self.rect(), QColor(0, 0, 0))

        # The grid in game-window pixels: Qt unit = (pixel - offset) / scale.
        p.save()
        p.scale(1.0 / self._scale, 1.0 / self._scale)
        p.translate(-self._offset[0], -self._offset[1])

        # Grid border
        p.setPen(QPen(QColor("#FFD700"), 3))
        p.drawRect(gx, gy, gw, gh)

        # Inner cell lines
        p.setPen(QPen(QColor("#FFD700"), 1))
        for col in range(1, 6):
            x = int(gx + gw * col / 6)
            p.drawLine(x, gy, x, gy + gh)
        for row in range(1, 4):
            y = int(gy + gh * row / 4)
            p.drawLine(gx, y, gx + gw, y)

        # Resize handle (bottom-right corner)
        p.fillRect(gx + gw - 12, gy + gh - 12, 14, 14, QColor("#FFD700"))
        p.restore()

        # Instruction panel
        p.fillRect(10, 10, 490, 78, QColor(0, 0, 0))
        p.setPen(QPen(QColor("#FFD700"), 1))
        p.drawRect(10, 10, 490, 78)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont("Courier New", 9, QFont.Bold))
        p.drawText(22, 30, "Drag grid to align.  Drag bottom-right corner to resize.")
        p.drawText(22, 48, f"Target: {self._target_name}")
        p.setPen(QColor("#FFD700"))
        p.setFont(QFont("Courier New", 9))
        p.drawText(22, 72, f"x={gx} y={gy} w={gw} h={gh}    Enter = confirm   Esc = cancel")

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        gx, gy, gw, gh = [int(v) for v in self._grid]
        x, y = self._to_window(ev)
        self._drag_x, self._drag_y = x, y
        if abs(x - (gx + gw)) <= 18 and abs(y - (gy + gh)) <= 18:
            self._drag_mode = "resize"
        elif gx <= x <= gx + gw and gy <= y <= gy + gh:
            self._drag_mode = "move"
        else:
            self._drag_mode = None

    def mouseMoveEvent(self, ev) -> None:
        if not self._drag_mode:
            return
        x, y = self._to_window(ev)
        dx = x - self._drag_x
        dy = y - self._drag_y
        self._drag_x, self._drag_y = x, y
        if self._drag_mode == "resize":
            self._grid[2] += dx
            self._grid[3] += dy
        else:
            self._grid[0] += dx
            self._grid[1] += dy
        self._clamp()
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        self._drag_mode = None

    def wheelEvent(self, ev) -> None:
        step = 4.0 if ev.angleDelta().y() > 0 else -4.0
        self._grid[2] += step * 6
        self._grid[3] += step * 4
        self._clamp()
        self.update()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._clamp()
            self.confirmed.emit([int(round(v)) for v in self._grid])
            self.close()
        elif ev.key() == Qt.Key_Escape:
            self.close()


class JigsawTab(QWidget):
    """The JIGSAW tab: everything only the puzzle solver needs — where the
    puzzle grid is, the Confirm point (shared with Fishing), which crates to
    open and the solver's move table. Starting happens on the Dashboard."""

    define_grid = Signal()
    set_confirm = Signal()
    priority_changed = Signal(str)
    retry_cache = Signal()

    PRIORITIES = [
        ("normal_first", "Normal crates only",
         "Only normal crates are opened. An empty crate slot is refilled from the inventory pages."),
        ("special_first", "Special crates first",
         "Special crates are used while there are any, then normal ones. A normal crate is opened "
         "instead on a board where the 3x2 piece of a special crate has no room."),
        ("special_when_better", "Special crate only where its 3x2 piece saves rounds",
         "A special crate always gives the 3x2 piece. It is opened only on boards where that piece is "
         "expected to save at least one round (or when no normal crate is left)."),
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- Puzzle setup ---------------------------------------------------
        setup = Card("Puzzle Setup")
        self.jigsaw_guide_btn = guide_button(lambda: open_guide(self, "jigsaw_grid"))
        setup.addAction(self.jigsaw_guide_btn)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.grid_info_label = QLabel("")
        self.define_grid_btn = QPushButton("  Define Grid")
        self.define_grid_btn.setProperty("class", "Btn")
        self.define_grid_btn.setIcon(make_icon("grid", 12, C["text"]))
        self.define_grid_btn.setCursor(Qt.PointingHandCursor)
        self.define_grid_btn.setFixedWidth(215)
        self.define_grid_btn.clicked.connect(self.define_grid.emit)

        self.confirm_info_label = QLabel("")
        self.set_confirm_btn = QPushButton("  Set Confirm")
        self.set_confirm_btn.setProperty("class", "Btn")
        self.set_confirm_btn.setIcon(make_icon("target", 12, C["text"]))
        self.set_confirm_btn.setCursor(Qt.PointingHandCursor)
        self.set_confirm_btn.setFixedWidth(215)
        self.set_confirm_btn.clicked.connect(self.set_confirm.emit)

        for r, (caption, label, btn, hint) in enumerate((
            ("PUZZLE GRID", self.grid_info_label, self.define_grid_btn,
             "Found by itself in every game window, wherever the Fish Puzzle window is. Define Grid "
             "only if the bot reports it cannot find the window (a server with another look)."),
            ("CONFIRM (YES)", self.confirm_info_label, self.set_confirm_btn,
             "Open a crate by hand; while the 'open the crate?' dialog is up, capture its Yes button. "
             "Shared with Fishing (Positions tab)."),
        )):
            cap = QLabel(caption)
            cap.setProperty("class", "ColHead")
            cap.setFixedWidth(100)
            grid.addWidget(cap, r * 2, 0)
            grid.addWidget(label, r * 2, 1)
            grid.addWidget(btn, r * 2, 2)
            h = QLabel(hint)
            h.setWordWrap(True)
            h.setStyleSheet(f"color: {C['text_mute']}; font-family: {MONO}; font-size: 11px;")
            grid.addWidget(h, r * 2 + 1, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        setup.body_layout.addLayout(grid)
        layout.addWidget(setup)

        # --- Crates ---------------------------------------------------------
        crates = Card("Which Crates to Open")
        self._priority_group = QButtonGroup(self)
        self.priority_radios: Dict[str, QRadioButton] = {}
        self.priority_descs: Dict[str, QLabel] = {}
        for key, text, tip in self.PRIORITIES:
            rb = QRadioButton(text)
            rb.setToolTip(tip)
            self._priority_group.addButton(rb)
            self.priority_radios[key] = rb
            crates.body_layout.addWidget(rb)
            # Only the chosen strategy's explanation is shown (the others are
            # in the tooltips), so all three choices fit the first screen.
            d = QLabel(tip)
            d.setWordWrap(True)
            d.setStyleSheet(f"color: {C['text_mute']}; font-family: {MONO}; font-size: 11px; "
                            f"padding-left: 24px;")
            d.setVisible(False)
            rb.toggled.connect(d.setVisible)
            self.priority_descs[key] = d
            crates.body_layout.addWidget(d)
        self.priority_radios["normal_first"].setChecked(True)
        self._priority_group.buttonToggled.connect(self._on_priority_toggled)

        # --- Solver cache ---------------------------------------------------
        cache = Card("Solver Move Table")
        crl = QHBoxLayout()
        crl.setSpacing(10)
        self.cache_dot = QLabel("●")
        self.cache_dot.setFixedWidth(14)
        crl.addWidget(self.cache_dot)
        self.cache_status_label = QLabel("")
        self.cache_status_label.setWordWrap(True)
        self.cache_status_label.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 12px;")
        crl.addWidget(self.cache_status_label, 1)
        self.retry_cache_btn = QPushButton("  Retry")
        self.retry_cache_btn.setProperty("class", "Btn")
        self.retry_cache_btn.setIcon(make_icon("refresh", 12, C["text"]))
        self.retry_cache_btn.setCursor(Qt.PointingHandCursor)
        self.retry_cache_btn.clicked.connect(self.retry_cache.emit)
        crl.addWidget(self.retry_cache_btn)
        cache.body_layout.addLayout(crl)
        cache_hint = QLabel("Built once on this PC (1–5 minutes, ~150 MB) the first time Jigsaw mode is "
                            "selected, then loaded from disk.")
        cache_hint.setWordWrap(True)
        cache_hint.setStyleSheet(f"color: {C['text_mute']}; font-family: {MONO}; font-size: 11px;")
        cache.body_layout.addWidget(cache_hint)
        # The solver card comes before the crates: its build time is what a
        # first-time player waits for, and it must be seen without scrolling.
        layout.addWidget(cache)
        layout.addWidget(crates)
        layout.addStretch(1)

        self.setCache("missing", "")
        self.setGrid(None)
        self.setConfirm(None)

    # -- state from the window ------------------------------------------------
    def setGrid(self, bounds, auto: bool = False) -> None:
        big = f"font-family: {MONO}; font-size: 13px; font-weight: 600;"
        ok = valid_grid_bounds(bounds)
        if auto:
            self.grid_info_label.setText("✓ automatic" + (" (+ defined grid as fallback)" if ok else ""))
            self.grid_info_label.setStyleSheet(f"color: {C['green']}; {big}")
        elif ok:
            x, y, w, h = [int(v) for v in bounds]
            self.grid_info_label.setText(f"✓ x={x} y={y} w={w} h={h}")
            self.grid_info_label.setStyleSheet(f"color: {C['green']}; {big}")
        elif bounds:
            self.grid_info_label.setText("✗ invalid — define it again")
            self.grid_info_label.setStyleSheet(f"color: {C['red']}; {big}")
        else:
            self.grid_info_label.setText("✗ not defined")
            self.grid_info_label.setStyleSheet(f"color: {C['orange']}; {big}")

    def setConfirm(self, pos) -> None:
        big = f"font-family: {MONO}; font-size: 13px; font-weight: 600;"
        if pos:
            self.confirm_info_label.setText(f"✓ ({pos[0]}, {pos[1]})")
            self.confirm_info_label.setStyleSheet(f"color: {C['green']}; {big}")
        else:
            self.confirm_info_label.setText("✗ not set")
            self.confirm_info_label.setStyleSheet(f"color: {C['red']}; {big}")

    def setPriority(self, priority: str) -> None:
        rb = self.priority_radios.get(priority) or self.priority_radios["normal_first"]
        self._priority_group.blockSignals(True)
        rb.setChecked(True)
        self._priority_group.blockSignals(False)

    def priority(self) -> str:
        for key, rb in self.priority_radios.items():
            if rb.isChecked():
                return key
        return "normal_first"

    def setCache(self, state: str, message: str) -> None:
        color = {"ready": C["green"], "building": C["orange"], "missing": C["text_mute"]}.get(state, C["red"])
        self.cache_dot.setStyleSheet(f"color: {color}; font-size: 18px;")
        self.cache_status_label.setText(message or SolverCacheManager.describe(state))
        self.retry_cache_btn.setVisible(state == "failed")

    def setSetupEnabled(self, enabled: bool) -> None:
        for w in [self.define_grid_btn, self.set_confirm_btn, *self.priority_radios.values()]:
            w.setEnabled(enabled)

    def _on_priority_toggled(self, _button, checked: bool) -> None:
        if checked:
            self.priority_changed.emit(self.priority())


_PUZZLE_TEMPLATE_PRESENT: Optional[bool] = None


def auto_grid(cfg: dict) -> bool:
    """True when the bot finds the Fish Puzzle window by itself in every game
    window (the default): Define Grid is then only a fallback."""
    global _PUZZLE_TEMPLATE_PRESENT
    if not cfg.get("jigsaw_grid_auto", True):
        return False
    if _PUZZLE_TEMPLATE_PRESENT is None:
        try:
            from jigsaw_detector import JigsawDetector
            _PUZZLE_TEMPLATE_PRESENT = JigsawDetector({}).auto_grid()
        except Exception:
            diag.exc("puzzle template")
            _PUZZLE_TEMPLATE_PRESENT = False
    return _PUZZLE_TEMPLATE_PRESENT


def grid_ready(cfg: dict) -> bool:
    """Nothing is missing for the grid: found by itself, or defined."""
    return auto_grid(cfg) or valid_grid_bounds(cfg.get("jigsaw_grid_bounds"))


def valid_grid_bounds(bounds) -> bool:
    try:
        return (bool(bounds) and len(bounds) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bounds)
                and int(bounds[2]) > 0 and int(bounds[3]) > 0)
    except TypeError:
        return False


class JigsawRunner(QObject):
    """Runs the jigsaw workers (one per selected window) for the main window.
    Has no window of its own: progress goes to the Dashboard's window rows,
    with the same row index (and W number) as in Fishing mode."""

    status_line = Signal(int, str)
    progress_line = Signal(int, int)
    stopped = Signal(int, object)  # bot_id, bot instance
    paused_changed = Signal(int, bool)
    setting_learned = Signal(str, object)   # a position / fact a worker found once: saved

    STOP_GRACE_MS = 5000     # after STOP, workers that still run are let go (UI unlocked)

    def __init__(self, window: "FishbotWindow"):
        super().__init__(window)
        self.win = window
        self.bots: Dict[int, object] = {}
        self._threads: Dict[int, threading.Thread] = {}
        self._old_threads: List[threading.Thread] = []
        self._debug_windows: Dict[int, object] = {}
        self.stats: Dict[int, Dict[str, int]] = {i: {"crates": 0, "boards": 0} for i in range(MAX_WINDOWS)}
        self.status_line.connect(self._on_status)
        self.progress_line.connect(self._on_progress)
        self.stopped.connect(self._on_stopped)
        self.paused_changed.connect(self._on_paused_changed)
        self.setting_learned.connect(self._on_setting_learned)

    def _on_setting_learned(self, key: str, value: object) -> None:
        """A worker found something on screen that stays put (the result
        dialog's OK, whether the server asks before placing): saved, so the
        next runs use it instead of looking for it again."""
        if self.win.config.get(key) == value:
            return
        diag.log("GUI", f"learned setting saved: {key} = {diag.summarize(value)}", tag="Jigsaw")
        self.win.config[key] = value
        self.win.save_config()

    # -- queries ---------------------------------------------------------------
    def is_running(self) -> bool:
        return bool(self.bots)     # a worker leaves this dict when its stop arrives

    def any_paused(self) -> bool:
        return any(getattr(b, "paused", False) for b in self.bots.values())

    def all_paused(self) -> bool:
        return bool(self.bots) and all(getattr(b, "paused", False) for b in self.bots.values())

    # -- start / stop ----------------------------------------------------------
    def layout_ok(self, name: str, all_windows: dict) -> bool:
        """Before a start: the Confirm point and the cursor's resting point
        must not lie on the grid, a crate slot or the inventory. Shows what
        to do (with a picture) and returns False when they do.

        With the puzzle window found by itself, each worker checks this for
        the place it finds it in its own window (and pauses with the same
        message): a defined grid may be from another client."""
        if auto_grid(self.win.config):
            return True
        try:
            from jigsaw_bot import layout_problem
            wm = WindowManager()
            wm.selected_window = all_windows.get(name)
            _, _, w, h = wm.get_window_rect() if wm.selected_window is not None else (0, 0, None, None)
            problem = layout_problem(self.win.config, w, h)
        except Exception:
            diag.exc("jigsaw layout pre-check")
            return True      # the bot checks again itself
        if problem is None:
            return True
        diag.log("GUI", f"jigsaw start refused: {problem.what} point {problem.point} on the {problem.lies_on}",
                 tag="Jigsaw")
        show_help(self.win, problem.help_topic, problem=problem.message, title="Move the puzzle window")
        return False

    def start(self) -> bool:
        win = self.win
        diag.log("GUI", "jigsaw START pressed", tag="Jigsaw")
        cache = win.solver_cache_manager
        if not cache.is_ready():
            state = cache.state()
            if state == "building":
                QMessageBox.information(win, "Solver still preparing",
                                        "The solver's move table is still being built. "
                                        "Start again when the Solver chip turns green.")
            else:
                cache.ensure_build()
                QMessageBox.warning(win, "Solver not ready",
                                    f"The solver's move table is not available ({state}). "
                                    "A new build was started — see the JIGSAW tab.")
            return False
        if win.bots:
            QMessageBox.warning(win, "Fishing running", "Stop fishing before starting the jigsaw solver.")
            return False
        rows = win.dashboard.window_rows
        selected = [(i, r.selectedWindow()) for i, r in enumerate(rows) if r.selectedWindow()]
        if not selected:
            QMessageBox.warning(win, "No window selected", "Select at least one game window first.")
            return False
        if not win.config.get("confirm_button_pos"):
            show_help(win, "jigsaw_confirm", title="Confirm point not set",
                      problem="The bot needs the Yes button of the game's 'open the crate?' dialog. Open a "
                              "crate by hand, and while the dialog is up press Set Confirm (JIGSAW "
                              "tab) and click Yes.")
            return False
        if not grid_ready(win.config):
            show_help(win, "jigsaw_grid", title="Puzzle grid not set",
                      problem="The bot needs to know where the puzzle grid is. Press Define Grid (JIGSAW tab) "
                              "and lay the ghost grid exactly on the 24 cells of the Fish Puzzle window.")
            return False
        if not win.config.get("inv_page_1_pos"):
            show_help(win, "inventory_tabs", title="Inventory pages not set",
                      problem="The solver needs the inventory page tabs: it searches them for crates and "
                              "checks that the inventory has room for the rewards. POSITIONS tab → click "
                              "Page 1, then the first inventory tab in the game (and the further pages).")
            return False
        all_windows = {n: w for n, w in WindowManager.get_all_windows()}
        if win._report_gone_windows(selected, all_windows):
            return False
        names = [name for _, name in selected]
        if not win.windows_have_same_size(names, all_windows, parent=win,
                                          positions=setup_checks.JIGSAW_POSITIONS):
            return False
        if not self.layout_ok(names[0], all_windows):
            return False
        try:
            from jigsaw_bot import JigsawBot
        except Exception as e:
            diag.exc()
            QMessageBox.critical(win, "Jigsaw unavailable", f"Could not load jigsaw solver:\n{e}")
            return False

        # Workers from the previous run must be gone, or two would drive the
        # same window at once. Wait at most one second in total (the UI is
        # frozen while waiting).
        # Fishing bots of a stopped fishing run as well: one still finishing
        # a drop would click alongside these workers.
        win._join_threads(list(self._threads.values()) + self._old_threads + list(win._stopping_threads),
                          budget=1.0)
        self._old_threads = [t for t in list(self._threads.values()) + self._old_threads if t.is_alive()]
        win._stopping_threads = [t for t in win._stopping_threads if t.is_alive()]
        if self._old_threads or win._stopping_threads:
            QMessageBox.information(win, "Still stopping",
                                    "The previous run is still finishing its last action. "
                                    "Try again in a moment.")
            return False

        self.bots.clear()
        self._threads.clear()
        JigsawBot.reset_learned_settings()   # a new run starts from the saved settings
        session_cfg = dict(win.config)
        session_cfg["jigsaw_solver_enabled"] = True
        win._begin_diag_session("jigsaw", selected, all_windows, session_cfg)
        for bot_id, name in selected:
            wm = WindowManager()
            wm.selected_window = all_windows[name]
            cfg = dict(win.config)
            cfg["jigsaw_solver_enabled"] = True
            bot = JigsawBot(
                wm, cfg, bot_id=bot_id,
                on_status_update=lambda msg, i=bot_id: self.status_line.emit(i, msg),
                on_progress_update=lambda _worker_id, count, i=bot_id: self.progress_line.emit(i, count),
                on_pause_change=lambda _worker_id, paused, i=bot_id: self.paused_changed.emit(i, paused),
            )
            # Bind the instance so a late stop from a previous run of this slot
            # is not mistaken for the worker running there now.
            bot.on_bot_stop = lambda _worker_id, i=bot_id, b=bot: self.stopped.emit(i, b)
            bot.on_setting_learned = self.setting_learned.emit
            thread = threading.Thread(target=bot.start, daemon=True, name=f"JigsawBot-{bot_id + 1}")
            self.bots[bot_id] = bot
            self._threads[bot_id] = thread
            self.stats[bot_id] = {"crates": 0, "boards": 0}
            win._open_debug_windows(("jigsaw", bot_id), [JigsawSolverDebugWindow], bot)
            thread.start()
            row = rows[bot_id]
            row.setStatus("active", "RUNNING")
            row.setMessage("", lock=False)
            row.setBait(0)
            row.setGames(0)
        win._on_activity_started(MODE_JIGSAW)
        self._log(f"Started jigsaw solver on {len(self.bots)} window(s)")
        return True

    def stop(self) -> None:
        if not self.bots:
            return
        diag.log("GUI", f"jigsaw STOP ({len(self.bots)} workers)", tag="Jigsaw")
        for bot_id, bot in self.bots.items():
            bot.stop()
            row = self.win.dashboard.window_rows[bot_id]
            row.setMessage("Stopped by user", lock=True)
        self._log("Stopping jigsaw solver workers...")
        self.win.dashboard.setStatus("Stopping…", "Waiting for the workers to finish their last move.",
                                     "paused")
        stopping = dict(self.bots)
        QTimer.singleShot(self.STOP_GRACE_MS, lambda: self._release_hung(stopping))

    def _release_hung(self, stopping: Dict[int, object]) -> None:
        """Workers asked to stop that still have not reported back: let them
        go (they exit on their own; start() waits for their threads) so the UI
        does not stay locked."""
        hung = [i for i, b in stopping.items() if self.bots.get(i) is b]
        if not hung:
            return
        diag.log("GUI", f"jigsaw workers {[i + 1 for i in hung]} did not stop in time: released",
                 tag="Jigsaw")
        self._log(f"W{', W'.join(str(i + 1) for i in hung)} did not stop in time; released")
        for i in hung:
            self.stopped.emit(i, self.bots[i])

    def toggle_pause(self) -> None:
        """Dashboard PAUSE / RESUME: same decision as F5 (pause_target) —
        while any window still runs everything pauses; only when all are
        paused does everything resume."""
        if not self.bots:
            return
        from jigsaw_bot import pause_target
        target = pause_target(getattr(b, "paused", False) for b in self.bots.values())
        for bot in list(self.bots.values()):
            try:
                bot._on_f5_pressed(target)
            except Exception:
                diag.exc()

    def shutdown(self) -> None:
        for bot in self.bots.values():
            try:
                bot.stop()
            except Exception:
                diag.exc()
        for bot_id in list(self._debug_windows):
            self.win._close_debug_windows(("jigsaw", bot_id))
        self._debug_windows.clear()

    def threads(self) -> List[threading.Thread]:
        return list(self._threads.values()) + list(self._old_threads)

    # -- worker callbacks (main thread) ---------------------------------------
    def _on_stopped(self, bot_id: int, bot: object = None) -> None:
        if self.bots.get(bot_id) is not bot:
            return  # stale notification from an earlier run
        row = self.win.dashboard.window_rows[bot_id]
        row.setStatus("error", "STOPPED")
        row.setMessage(row.msg_lbl.text() or "Stopped", lock=True,   # keep why it stopped
                       kind=row.msg_lbl.property("kind") or "")
        self.bots.pop(bot_id, None)
        thread = self._threads.pop(bot_id, None)
        if thread is not None and thread.is_alive():
            self._old_threads.append(thread)  # about to exit; start() waits for it
        self.win._close_debug_windows(("jigsaw", bot_id))
        if self.bots:
            self._on_paused_changed(-1, False)   # refresh the others
            return
        self.win._on_activity_stopped(MODE_JIGSAW)
        self.win._end_diag_session(
            "all jigsaw workers stopped", wait_threads=[t for t in self._old_threads if t.is_alive()])

    def _on_paused_changed(self, _bot_id: int, _paused: bool) -> None:
        if not self.bots:
            return
        rows = self.win.dashboard.window_rows
        for i, b in self.bots.items():
            if getattr(b, "paused", False):
                rows[i].setStatus("warn", "PAUSED")
            else:
                rows[i].setStatus("active", "RUNNING")
        # The button and the status say PAUSE while any window still runs
        # (what F5 / the button will do: pause_target); the chime still
        # tells when a window paused itself.
        self.win._on_activity_paused(MODE_JIGSAW, self.all_paused(), chime=self.any_paused())

    # Messages that say why a worker stopped: kept on the row (red) so the
    # "Jigsaw bot stopped" that follows does not replace them.
    _STOP_REASON_MARKERS = ("stopped by an error", "Not started", "requires ")

    def _on_status(self, bot_id: int, message: str) -> None:
        text = message.split("] ", 1)[1] if message.startswith("[W") and "] " in message else message
        if 0 <= bot_id < len(self.win.dashboard.window_rows):
            row = self.win.dashboard.window_rows[bot_id]
            if any(m in text for m in self._STOP_REASON_MARKERS):
                row.setMessage(text, lock=True, kind="error")
            else:
                row.setMessage(text)
        if self.win.status_log_window is not None:
            try:
                self.win.status_log_window.add_message(message)
            except Exception:
                diag.exc()

    def _on_progress(self, bot_id: int, crates_completed: int) -> None:
        bot = self.bots.get(bot_id)
        st = self.stats.setdefault(bot_id, {"crates": 0, "boards": 0})
        st["crates"] = int(crates_completed)
        if bot is not None:
            st["boards"] = int(getattr(bot, "boards_completed", 0))
        if self.win.mode == MODE_JIGSAW and 0 <= bot_id < len(self.win.dashboard.window_rows):
            row = self.win.dashboard.window_rows[bot_id]
            row.setBait(st["crates"])
            row.setGames(st["boards"])
        self.win._update_kpis()

    def _log(self, message: str) -> None:
        diag.log("GUI", message, tag="Jigsaw", depth=1)
        if self.win.status_log_window is not None:
            try:
                self.win.status_log_window.add_message(f"[Jigsaw] {message}")
            except Exception:
                diag.exc()


class DiagnosticLogDialog(QDialog):
    """Tails the current diagnostic session log; without a running session it
    shows the log of the newest finished session (read from its zip)."""

    MAX_CHARS = 2_000_000   # keep the widget responsive on huge logs

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Diagnostic log")
        self.setModal(False)
        self.resize(980, 560)
        layout = QVBoxLayout(self)
        self.source_label = QLabel("")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.source_label.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 11px;")
        layout.addWidget(self.source_label)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setStyleSheet(f"font-family: {MONO}; font-size: 11px;")
        layout.addWidget(self.text, 1)
        self._path: Optional[str] = None
        self._pos = 0
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()
        help_visuals.fit_to_screen(self)   # short screens: keep the footer on screen

    def refresh(self) -> None:
        try:
            path = diag.current_log_path()
            if path is None:
                if self._path is not None or not self.text.toPlainText():
                    self._show_latest_zip()
                return
            if path != self._path:
                self._path, self._pos = path, 0
                self.text.clear()
                self.source_label.setText(f"Live: {path}")
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._pos)
                chunk = f.read()
                self._pos = f.tell()
            if chunk:
                bar = self.text.verticalScrollBar()
                at_end = bar.value() >= bar.maximum() - 4
                self.text.appendPlainText(chunk.rstrip("\n"))
                if self.text.document().characterCount() > self.MAX_CHARS:
                    self.text.setPlainText(self.text.toPlainText()[-self.MAX_CHARS // 2:])
                if at_end:
                    bar.setValue(bar.maximum())
        except Exception as e:
            diag.exc()
            self.source_label.setText(f"Could not read the log: {e}")

    def _show_latest_zip(self) -> None:
        self._path, self._pos = None, 0
        latest = diag.latest_zip()
        if not latest:
            folder, _ = diag.location()
            self.source_label.setText(f"No diagnostic session yet. Logs will be saved in: {folder}")
            self.text.setPlainText("")
            return
        import zipfile
        try:
            with zipfile.ZipFile(latest) as zf:
                name = next((n for n in zf.namelist() if n.endswith(diag.LOG_NAME)), None)
                data = zf.read(name).decode("utf-8", "replace") if name else ""
            self.source_label.setText(f"No session running. Newest finished session: {latest}")
            self.text.setPlainText(data[-self.MAX_CHARS:])
            self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())
        except Exception as e:
            diag.exc()
            self.source_label.setText(f"Could not read {latest}: {e}")

    def done(self, result: int) -> None:
        # Esc / close both end here (closeEvent is not called for Esc): the
        # tail timer must not keep reading the log behind a hidden dialog.
        self._timer.stop()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)


# ============================================================================
# Main window
# ============================================================================

class FishbotWindow(QMainWindow):
    BTC_ADDRESS = "3AGrrTf1v9QZsMPEoezYTRbf9JyW4nQtHu"   # same as README.md
    _f5_toggle = Signal()        # emitted from pynput thread → queued to main thread
    _sig_capture_cancel = Signal()  # Esc during a position capture (pynput thread)
    setup_changed = Signal()     # anything the readiness / setup wizard shows changed
    CAPTURE_TIMEOUT_MS = 60_000  # a position capture nobody finishes is cancelled
    QUIT_JOIN_BUDGET = 4.0       # seconds Quit waits for the workers to finish their last action
    _sig_bot_stopped = Signal(int, object) # bot_id, bot instance
    _sig_profile_learned = Signal(object)  # detection profile dict
    _sig_row_stats = Signal(int, int, int) # bot_id, games, bait
    _sig_bait = Signal(int, int)           # bot_id, bait
    _sig_status_log = Signal(str)
    _sig_diag_done = Signal(object, object)   # zip path or None, error or None
    _sig_diag_recovered = Signal(object)      # list of zips packed from crashed sessions

    STOP_REASON_TEXT = {
        "manual": "Stopped by user",
        "bait_depleted": "Bait used up",
        "window_unavailable": "Game window closed",
        "inventory_full": "All inventory pages full",
        "error": "Stopped by an error",
        "capture_unavailable": "Game window could not be seen",
        "capture_overlap": "Game windows overlap",
        "inventory_not_visible": "Inventory not found",
        "false_detection": "Minigame falsely detected",
        "fish_not_detected": "Fish not detected",
        "no_bite": "No bite seen",
        "keyboard_error": "Keys could not be sent",
        "game_screen_lost": "Game screen lost (disconnect?)",
        "repeated_error": "Repeated errors",
        "drop_failing": "Drops keep failing",
        "catch_not_recognized": "Catches not recognized",
        "cursor_item": "Item may be on the cursor",
        "quest_dialog": "Quest dialog not answered",
    }

    # Defaults that mirror the Tk version's TimingSettingsWindow.DEFAULTS
    TIMING_DEFAULTS = session_setup.TIMING_DEFAULTS

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setObjectName("AppWindow")
        self.setFixedSize(WINDOW_W, ACTIVE_WINDOW_H)

        # Central widget
        central = QWidget()
        central.setObjectName("AppWindowInner")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # One top bar (window chrome + title + status)
        self.header = Header()
        self.header.minimize.connect(self.showMinimized)
        self.header.close_window.connect(self.close)
        root.addWidget(self.header)

        # Activity switch: Fishing | Jigsaw Puzzle (+ its setup wizard).
        self.mode = MODE_FISHING
        self.mode_bar = ModeBar()
        root.addWidget(self.mode_bar)

        # The third tab is the selected activity's own options page
        # (FISHING or JIGSAW); the others are shared.
        self.tabs_bar = TabsBar([
            ("dashboard", "dashboard", "Dashboard"),
            ("inventory", "target",    "Positions"),
            ("activity",  "fish",      "Fishing"),
            ("settings",  "settings",  "Settings"),
        ])
        self.tabs_bar.changed.connect(self._set_tab)
        root.addWidget(self.tabs_bar)

        # Body: notices on top, then the tab pages
        body = QFrame()
        body.setObjectName("Body")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(8)
        self.notice = NoticeBar()
        self.notice.action_clicked.connect(self._on_notice_action)
        body_layout.addWidget(self.notice)
        self.stack = QStackedWidget()
        body_layout.addWidget(self.stack, 1)

        self.dashboard = DashboardTab()
        self.inventory = InventoryTab()
        self.settings = SettingsTab()
        self.jigsaw_tab = JigsawTab()
        # Stack order: dashboard, positions, fishing options, jigsaw options, settings
        self.stack.addWidget(self.dashboard)    # keeps its action strip in view; scrolls the rest itself
        for w in (self.inventory, self.settings.fishing_page, self.jigsaw_tab, self.settings):
            sw = QScrollArea()
            sw.setWidgetResizable(True)
            sw.setFrameShape(QFrame.NoFrame)
            sw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sw.setWidget(w)
            self.stack.addWidget(sw)
        root.addWidget(body, 1)

        # Footer
        footer = self._build_footer()
        root.addWidget(footer)

        # State
        self.config: dict = self._default_config()
        self.config_path = config_file_path()
        self._config_load_info: List[str] = []
        self._config_status = "ok"          # ok / missing / corrupt (set by load_config)
        self._config_fixed: List[str] = []  # keys whose saved value had the wrong type
        self._save_failed = False
        self._log_dialog: Optional["DiagnosticLogDialog"] = None
        self._screen_recorder = None   # "Record game windows" of the diagnostic session
        self._wizard: Optional[SetupWizard] = None
        self.bots: Dict[int, "FishingBot"] = {}
        self.bot_threads: Dict[int, threading.Thread] = {}
        self._stopping_threads: List[threading.Thread] = []
        # Window chosen per slot, kept even while that window is not open, so
        # starting the bot before the game does not wipe the saved selection.
        self._remembered_windows: List[str] = [""] * MAX_WINDOWS
        self._restoring_rows = False
        # Selected window names a start found closed: the Window chip stays
        # red for them until a Refresh or another choice in that slot.
        self._gone_windows: set = set()
        self._last_bait_cap: Optional[int] = None
        self.window_managers: Dict[int, WindowManager] = {}
        self.jigsaw = JigsawRunner(self)
        self._grid_overlay: Optional[JigsawGridOverlay] = None
        self._solver_cache_manager: Optional[SolverCacheManager] = None
        self._debug_host = None
        self.status_log_window = None
        self.window_stats: Dict[int, dict] = {i: {"hits": 0, "games": 0, "bait": 0} for i in range(MAX_WINDOWS)}
        self._bait_notice_hidden = False  # the bait notice, put away while Jigsaw is selected
        self._sound_alert_played = False
        self._chimed_paused = False      # the state the last pause / resume chime announced
        self._session_started_bot_ids = set()
        self._session_bait_depleted_bot_ids = set()
        self._session_non_bait_stop_reasons = {}
        self._help_shown_reasons = set()
        self._capture = PositionCaptureController(self)
        self._capture.signals.captured.connect(self._on_captured)
        self._capture.signals.failed.connect(self._on_capture_failed)
        self._capture.signals.cancelled.connect(self._cancel_capture)
        self._capture_pill: Optional[str] = None
        self._capture_window_name: Optional[str] = None
        self._capture_minimized: List[str] = []
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setInterval(self.CAPTURE_TIMEOUT_MS)
        self._capture_timer.timeout.connect(self._on_capture_timeout)

        # Wire signals
        self.dashboard.start_clicked.connect(self._on_start_clicked)
        self.dashboard.stop_clicked.connect(self._on_pause_or_start)
        self.dashboard.hard_stop.connect(self._on_stop_clicked)
        self.dashboard.ready_chips.fix_requested.connect(self._on_fix_requested)
        self.mode_bar.mode_changed.connect(self.set_mode)
        self.mode_bar.wizard_requested.connect(self.open_wizard)
        self.jigsaw_tab.define_grid.connect(self._on_define_grid_clicked)
        self.jigsaw_tab.set_confirm.connect(self._on_set_confirm_clicked)
        self.jigsaw_tab.priority_changed.connect(self._on_jigsaw_priority)
        self.jigsaw_tab.retry_cache.connect(lambda: self.solver_cache_manager.ensure_build())
        self.solver_cache_manager.state_changed.connect(self._on_cache_state)
        self.solver_cache_manager.progress_changed.connect(self._on_cache_progress)

        self.dashboard.refresh_windows.connect(self.refresh_window_list)
        self.dashboard.reset_bait.connect(self._on_reset_bait_clicked)
        self.dashboard.window_changed.connect(self._on_window_changed)
        self.dashboard.help_requested.connect(
            lambda topic, text: show_help(self, topic, problem=text, modal=False))
        self.inventory.set_coord.connect(self._begin_capture)
        self.inventory.unset_coord.connect(self._on_unset_coord)
        self.settings.open_fish_modal.connect(self.open_fish_modal)
        self.settings.auto_fish_toggled.connect(self._on_auto_fish_toggled)
        self.inventory.cancel_capture.connect(self._cancel_capture)
        self.settings.open_timing.connect(self.open_timing_modal)
        self.settings.config_changed.connect(self._save_settings)
        self.settings.accent_changed.connect(self._on_accent_changed)
        self.settings.profile_load_requested.connect(self._load_profile_file)
        self.settings.profile_save_requested.connect(self._save_profile_file)
        self.settings.open_log_folder.connect(self.open_log_folder)
        self.settings.show_log.connect(self.show_log_window)

        # Global F5 pause / Esc capture-cancel (pynput: works while the game has focus)
        self._global_hotkey_listener = None
        self._f5_toggle.connect(self.toggle_pause_all)
        self._sig_capture_cancel.connect(self._cancel_capture)
        self._sig_bot_stopped.connect(self._on_bot_stopped_ui)
        self._sig_profile_learned.connect(self._on_profile_learned_ui)
        self._sig_row_stats.connect(self._apply_row_stats)
        self._sig_bait.connect(self._apply_bait)
        self._sig_status_log.connect(self._append_debug_status)
        self._sig_diag_done.connect(self._on_diag_session_packed)
        self._sig_diag_recovered.connect(self._on_diag_recovered)
        self._start_global_hotkey_listener()
        self._esc = QShortcut(QKeySequence("Esc"), self)
        self._esc.activated.connect(self._cancel_capture)

        # Load config + populate.
        # _apply_state_to_ui must run BEFORE refresh_window_list so the settings
        # checkboxes are hydrated before setSelectedWindow fires save_config().
        self.load_config()
        self._apply_diag_settings()
        self._apply_state_to_ui()
        self.refresh_window_list()
        self.set_mode(self.config.get("ui_mode", MODE_FISHING), save=False)
        self._refresh_input_backend_pill()
        self._show_startup_notices()
        # A session a crash or kill left unpacked is packed now (background).
        diag.recover_leftovers(on_done=self._sig_diag_recovered.emit)

        # Window title and icon (taskbar, Alt+Tab, Task Manager's window list).
        # Disguised (disguise.py): the random name and a plain letter icon.
        disguised_as = disguise.active_name()
        self.setWindowTitle(disguised_as or "Fishing Puzzle Player")
        ico = get_resource_path("monkey.ico")
        if disguised_as:
            self.setWindowIcon(plain_letter_icon(disguised_as))
            diag.log("GUI", f"disguised as {disguised_as!r} ({sys.executable}), "
                            f"program folder {diag.program_dir()}")
        elif os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))
        if disguise.LAST_ERROR:
            diag.log("GUI", f"disguise failed, running under the normal name: {disguise.LAST_ERROR}")
        self._restart_on_close = False
        self.settings.restart_requested.connect(self.restart_program)

        # Periodic stats poll for running bots
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._poll_bot_stats)
        self._poll.start()
        self._init_debug_ui()
        name_icon_buttons(self)

    # ---------------- Notices ----------------
    def restart_program(self) -> None:
        """Settings → Restart now: closes like the Close button (asks first
        while a bot runs), then main() starts the program again."""
        self.save_config()
        diag.log("GUI", "restart requested (Settings)")
        self._restart_on_close = True
        if not self.close():
            self._restart_on_close = False

    def _show_startup_notices(self) -> None:
        """Non-blocking notices about the configuration and the platform."""
        if disguise.LAST_ERROR and self.config.get(disguise.CONFIG_KEY):
            self.notice.show_notice(
                "disguise", f"Disguise in Task Manager failed: {disguise.LAST_ERROR}", "warn")
        if self._config_status == "corrupt":
            self.notice.show_notice(
                "config", "Your settings file could not be read, so the defaults are used. The old file "
                f"was kept as {os.path.basename(self.config_path)}.corrupt (send it to support if you "
                "want it repaired).", "error")
        elif self._config_fixed:
            self.notice.show_notice(
                "config", "Some saved settings had invalid values and were reset to their defaults: "
                + ", ".join(self._config_fixed[:6]) + ("…" if len(self._config_fixed) > 6 else ""), "warn")
        if self._config_status == "missing":
            self.notice.show_notice(
                "welcome", "New here? The setup wizard (top right, highlighted) walks you through the "
                "setup step by step.", "info", "Open setup")
        app = QApplication.instance()
        if sys.platform.startswith("linux") and app is not None and app.platformName() == "wayland":
            self.notice.show_notice(
                "wayland", "This window runs as a native Wayland window: the F5 hotkey and clicking a "
                "position in the game may not be noticed. Start the program with QT_QPA_PLATFORM=xcb "
                "(see docs/LINUX.md).", "warn")

    def _on_notice_action(self, key: str) -> None:
        if key == "welcome":
            self.notice.clear("welcome")
            self.open_wizard()
        elif key == "save":
            self.save_config()

    # ---------------- Setup wizard ----------------
    @staticmethod
    def _wizard_flag(mode: str) -> str:
        return "wizard_done_jigsaw" if mode == MODE_JIGSAW else "wizard_done_fishing"

    def _update_wizard_highlight(self) -> None:
        """The wizard button pulses until the wizard of the selected activity
        has been opened once (flags wizard_done_fishing / _jigsaw)."""
        self.mode_bar.setWizardHighlighted(not self.config.get(self._wizard_flag(self.mode), False))

    def open_wizard(self, mode: Optional[str] = None) -> "SetupWizard":
        mode = mode if mode in (MODE_FISHING, MODE_JIGSAW) else self.mode
        diag.log("GUI", f"setup wizard opened ({MODE_NAMES[mode]})")
        flag = self._wizard_flag(mode)
        if not self.config.get(flag):
            self.config[flag] = True
            self.save_config()
        self._update_wizard_highlight()
        self.notice.clear("welcome")
        if self._wizard is not None:
            if self._wizard.mode == mode:
                self._wizard.show()
                self._wizard.raise_()
                self._wizard.activateWindow()
                return self._wizard
            self._wizard.close()
        wiz = SetupWizard(self, mode)
        wiz.setAttribute(Qt.WA_DeleteOnClose)
        wiz.destroyed.connect(lambda _=None, w=wiz: self._wizard is w and setattr(self, "_wizard", None))
        self._wizard = wiz
        wiz.show()
        return wiz

    def apply_window_selection(self, names: List[str]) -> None:
        """Makes ``names`` the selected game windows (wizard): slots that
        already hold one of them keep it, the others are freed or filled."""
        if self.bots or self.jigsaw.is_running():
            return
        names = [n for n in names if n][:MAX_WINDOWS]
        rows = self.dashboard.window_rows
        for row in rows:
            if row.selectedWindow() and row.selectedWindow() not in names:
                row.combo.setCurrentIndex(0)
        placed = {r.selectedWindow() for r in rows if r.selectedWindow()}
        for n in names:
            if n in placed:
                continue
            free = next((r for r in rows if not r.selectedWindow()), None)
            if free is None:
                break
            free.setSelectedWindow(n)
            placed.add(n)

    # ---------------- Debug windows (DEBUG_MODE_EN) ----------------
    def _open_debug_windows(self, key, classes, bot) -> None:
        if self._debug_host is not None:
            self._debug_host.open(key, classes, bot)

    def _close_debug_windows(self, key) -> None:
        if self._debug_host is not None:
            self._debug_host.close(key)

    def _refresh_input_backend_pill(self) -> None:
        """Probe the input backend and show it in Settings (Screen Capture &
        Input). Called once at startup; bots created later inherit the same
        backend choice via the same probe path inside create_mouse_backend()."""
        try:
            name, err = probe_backend(self.config)
        except Exception as e:
            diag.exc()
            name, err = "pyautogui", str(e)
        if name == "interception":
            self.settings.setInputBackend(
                "Interception driver",
                "Interception kernel driver active — input is indistinguishable from a real mouse.",
            )
        elif name == "uinput":
            self.settings.setInputBackend(
                "virtual devices (uinput)",
                "Virtual kernel mouse/keyboard (/dev/uinput) — input is indistinguishable from real devices.",
            )
        elif name == "xtest":
            tip = "XTest fallback (X11 injection; may be limited on Wayland desktops)."
            if err:
                tip += f"\nuinput unavailable: {err}"
            self.settings.setInputBackend("XTest (fallback)", tip)
        else:
            tip = "PyAutoGUI fallback (SendInput-based)."
            if err:
                tip += f"\nInterception unavailable: {err}"
            self.settings.setInputBackend("standard (PyAutoGUI)", tip)

    def _init_debug_ui(self) -> None:
        """DEBUG_MODE_EN: the Tk debug windows (debug_ui.py) per running bot,
        plus a status log window. Tk runs on its own thread (debug_host.py):
        pumping it from this Qt thread crashed Python 3.13+."""
        self._debug_host = None
        self.status_log_window = None
        if not DEBUG_MODE_EN or tk is None:
            return
        try:
            from debug_host import TkDebugHost, TkStatusLog
            host = TkDebugHost()
            if not host.start():
                diag.log("GUI", f"debug windows unavailable: {host.error}")
                return
            self._debug_host = host
            if StatusLogWindow is not None:
                self.status_log_window = TkStatusLog(host, StatusLogWindow)
        except Exception:
            diag.exc("debug windows")
            self._debug_host = None

    # ---------------- Defaults ----------------
    def _default_config(self) -> dict:
        return session_setup.default_config(APP_VERSION, DEFAULT_ACCENT)

    # ---------------- Layout helpers ----------------
    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("Footer")
        footer.setFixedHeight(42)
        l = QHBoxLayout(footer)
        l.setContentsMargins(14, 6, 14, 6)
        l.setSpacing(8)

        self.guide_footer_btn = guide_button(lambda: open_guide(self))
        l.addWidget(self.guide_footer_btn)

        self.report_btn = QPushButton("  Report a problem")
        self.report_btn.setProperty("class", "BtnGhost")
        self.report_btn.setIcon(make_icon("folder", 12, C["text"]))
        self.report_btn.setCursor(Qt.PointingHandCursor)
        self.report_btn.setToolTip("How to record a diagnostic log and send it to support")
        self.report_btn.clicked.connect(self.open_report_dialog)
        l.addWidget(self.report_btn)

        l.addStretch(1)

        # The address in a brighter color than the hint: 1 and l must not be
        # confused by someone typing it.
        donations = QLabel(f"Donations · BTC · <span style='color:{C['text_dim']};'>{self.BTC_ADDRESS}</span>")
        donations.setTextFormat(Qt.RichText)
        donations.setProperty("class", "Hint")
        donations.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l.addWidget(donations)

        copy_btn = QPushButton()
        copy_btn.setIcon(make_icon("copy", 11, C["text_mute"]))
        copy_btn.setProperty("class", "BtnGhost")
        copy_btn.setFixedSize(26, 26)
        copy_btn.setToolTip("Copy BTC address")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_btc)
        self._copy_btn = copy_btn
        l.addWidget(copy_btn)
        return footer

    def open_report_dialog(self) -> None:
        """Explains how to send a useful bug report and offers the buttons."""
        box = QMessageBox(self)
        box.setWindowTitle("Report a problem")
        box.setIcon(QMessageBox.Information)
        on = bool(self.config.get("diagnostics_enabled"))
        box.setText(
            "1. Turn on the diagnostic log" + (" (it is on)." if on else ".") + "\n"
            "2. Start the bot and let the problem happen again, then stop it.\n"
            "3. Send the newest .zip from the log folder on the Discord support server.\n\n"
            "The log records what the bot did and saw, with screenshots of the game window when "
            "something was not found. It contains no passwords.")
        turn_on = None if on else box.addButton("Turn on diagnostic log", QMessageBox.ActionRole)
        folder = box.addButton("Open log folder", QMessageBox.ActionRole)
        discord = box.addButton("Discord", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        clicked = box.clickedButton()
        if turn_on is not None and clicked is turn_on:
            self.settings.diag_check.setChecked(True)
        elif clicked is folder:
            self.open_log_folder()
        elif clicked is discord:
            QDesktopServices.openUrl(QUrl(DISCORD_INVITE_URL))

    def _set_tab(self, tab_id: str) -> None:
        if tab_id == "activity":
            idx = 3 if self.mode == MODE_JIGSAW else 2
        else:
            idx = {"dashboard": 0, "inventory": 1, "settings": 4}.get(tab_id, 0)
        self.stack.setCurrentIndex(idx)

    # ---------------- Activity (Fishing | Jigsaw) ----------------
    def set_mode(self, mode: str, save: bool = True) -> None:
        """Selects the activity the Dashboard starts. Refused while something
        runs (the mode bar is locked then anyway)."""
        mode = MODE_JIGSAW if mode == MODE_JIGSAW else MODE_FISHING
        if self.bots or self.jigsaw.is_running():
            self.mode_bar.setMode(self.mode)
            return
        changed = mode != self.mode
        self.mode = mode
        diag.log("GUI", f"activity: {MODE_NAMES[mode]}")
        self.mode_bar.setMode(mode)
        self.dashboard.setMode(mode)
        self.dashboard.reset_btn.setVisible(mode == MODE_FISHING)
        # The bait notice names Reset Bait, which only the Fishing dashboard
        # has: put it away in Jigsaw (it also pushes the crate choices down)
        # and bring it back with Fishing.
        if mode == MODE_JIGSAW and "bait" in self.notice.keys():
            self.notice.clear("bait")
            self._bait_notice_hidden = True
        elif mode == MODE_FISHING and self._bait_notice_hidden:
            self._bait_notice_hidden = False
            self._show_bait_notice()
        self.inventory.setMode(mode)
        self._update_wizard_highlight()
        if mode == MODE_JIGSAW:
            self.tabs_bar.setTabLabel("activity", "Jigsaw", "grid")
        else:
            self.tabs_bar.setTabLabel("activity", "Fishing", "fish")
        self.tabs_bar.setModeTag(mode)
        self._set_tab(self.tabs_bar.active())
        for i, row in enumerate(self.dashboard.window_rows):
            if not self._row_window_gone(i):
                # Last run's messages belong to the other activity; a closed
                # client stays NOT OPEN until Refresh or a new choice.
                row.setMessage("", lock=False)
            self._set_idle_row_status(i)
            self._render_row_stats(i)
        if mode == MODE_JIGSAW:
            cache = self.solver_cache_manager
            self.jigsaw_tab.setCache(cache.state(), "")
            cache.ensure_build()      # no-op when ready or already building
        self._update_kpis()
        self._refresh_readiness()
        if save and changed:
            self.config["ui_mode"] = mode
            self.save_config()

    def _row_window_gone(self, i: int) -> bool:
        name = self.dashboard.window_rows[i].selectedWindow()
        return bool(name) and name in self._gone_windows

    def _set_idle_row_status(self, i: int) -> None:
        """The pill of a row with no bot: SELECTED (not READY: the status
        line says whether everything else is set), NOT OPEN for a client
        that was found closed, or EMPTY."""
        row = self.dashboard.window_rows[i]
        if self._row_window_gone(i):
            row.setStatus("error", "NOT OPEN")
        else:
            row.setStatus("idle", "SELECTED" if row.selectedWindow() else "EMPTY")

    def _render_row_stats(self, i: int) -> None:
        """Shows row ``i``'s two counters for the selected activity."""
        row = self.dashboard.window_rows[i]
        if self.mode == MODE_JIGSAW:
            st = self.jigsaw.stats.get(i, {})
            row.setBait(st.get("crates", 0))
            row.setGames(st.get("boards", 0))
        else:
            row.setBait(self.window_stats[i]["bait"] if row.selectedWindow() else "---")
            row.setGames(self.window_stats[i]["games"])

    def _readiness_items(self, mode: Optional[str] = None) -> List[Tuple[str, str, str, str]]:
        """(key, label, state, tooltip) of what an activity needs (the
        selected one by default; the wizard asks for its own)."""
        cfg = self.config
        mode = self.mode if mode is None else mode
        selected = self.selected_window_names()
        gone = [n for n in selected if n in getattr(self, "_gone_windows", ())]
        if gone:
            items = [("windows", "Window", "bad",
                      f"Not open any more: {', '.join(gone)}. Start the client and press Refresh, "
                      "or clear the slot")]
        else:
            items = [("windows", "Window", "ok" if selected else "bad",
                      "At least one game window is selected" if selected else "Select a game window (Dashboard)")]
        if mode == MODE_JIGSAW:
            grid_ok = grid_ready(cfg)
            confirm_ok = bool(cfg.get("confirm_button_pos"))
            pages_ok = bool(cfg.get("inv_page_1_pos"))
            state = self.solver_cache_manager.state()
            items += [
                ("grid", "Puzzle grid", "ok" if grid_ok else "bad",
                 ("Found by itself in each window" if auto_grid(cfg) else "Grid is set")
                 if grid_ok else "JIGSAW tab → Define Grid"),
                ("pages", "Inventory pages", "ok" if pages_ok else "bad",
                 "Inventory page 1 is set" if pages_ok else "POSITIONS tab → set the page tabs"),
                ("confirm", "Confirm", "ok" if confirm_ok else "bad",
                 "Confirm point is set" if confirm_ok else "JIGSAW tab → Set Confirm "
                                                           "(or POSITIONS tab → Confirm)"),
                ("solver", "Solver" + (f" {self.solver_cache_manager.progress()[0]}%"
                                       if state == "building" else ""),
                 {"ready": "ok", "building": "wait"}.get(state, "bad"),
                 SolverCacheManager.describe(state) + (f" — {self.solver_cache_manager.progress()[1]}"
                                                       if state == "building"
                                                       and self.solver_cache_manager.progress()[1] else "")),
            ]
        else:
            pages_ok = bool(cfg.get("inv_page_1_pos"))
            bait_ok = bool(self.settings.selectedBaitKeys())
            empty = [i + 1 for i, r in enumerate(self.dashboard.window_rows)
                     if r.selectedWindow() and self.window_stats[i]["bait"] <= 0]
            items += [
                ("pages", "Inventory pages", "ok" if pages_ok else "bad",
                 "Inventory page 1 is set" if pages_ok else "POSITIONS tab → set the page tabs"),
                ("bait", "Bait", "ok" if bait_ok and not empty else "bad",
                 ("Bait keys selected" if not empty else
                  f"W{', W'.join(map(str, empty))} counted 0 bait left — press Reset Bait after restocking")
                 if bait_ok else "FISHING tab → pick at least one bait key"),
            ]
            drops = cfg.get("auto_fish_handling") and any(
                a == "drop" for a in (cfg.get("fish_actions") or {}).values())
            if drops:
                confirm_ok = bool(cfg.get("confirm_button_pos"))
                items.append(("confirm", "Confirm", "ok" if confirm_ok else "bad",
                              "Confirm point is set" if confirm_ok else
                              "Some fish are set to drop: POSITIONS tab → Confirm"))
            problem = self._armor_skip_problem()
            if cfg.get("quick_skip") and cfg.get("quick_skip_mode") == "armor":
                items.append(("armor", "Spare armor", "bad" if problem else "ok",
                              problem or "Spare armor position is set"))
        return items

    def _armor_skip_problem(self) -> Optional[str]:
        """Why Armor quick skip would not right-click the spare armor (None
        when it would, or when it is not used). The bot then waits for the
        animation instead, so this is said before Start rather than found
        out from slower catches."""
        cfg = self.config
        if not (cfg.get("quick_skip") and cfg.get("quick_skip_mode") == "armor"):
            return None
        if not cfg.get("armor_slot_pos"):
            return "Armor quick skip is on but Spare Armor is not set: POSITIONS tab → Spare Armor"
        if len(inventory_layout.tab_points(cfg)) >= 2 and not cfg.get("armor_slot_page"):
            return ("The spare armor's inventory page is not known: set Spare Armor again while the game "
                    "shows its page (POSITIONS tab)")
        return None

    def _refresh_readiness(self) -> None:
        if not hasattr(self, "dashboard"):
            return
        items = self._readiness_items()
        self.dashboard.ready_chips.setItems(items)
        if self.mode == MODE_JIGSAW and self.solver_cache_manager.state() == "building":
            self.dashboard.ready_chips.setProgress("solver", self.solver_cache_manager.progress()[0])
        else:
            self.dashboard.ready_chips.setProgress("solver", None)
        if not (self.bots or self.jigsaw.is_running()):
            missing = [label for _k, label, state, _t in items if state == "bad"]
            waiting = [label for _k, label, state, _t in items if state == "wait"]
            if missing:
                self.dashboard.setStatus("Not ready yet",
                                         "Missing: " + ", ".join(missing) + " — click a red item below.",
                                         "warn")
            elif waiting:
                self.dashboard.setStatus("Almost ready", "Waiting for: " + ", ".join(waiting), "paused")
            else:
                n = len(self.selected_window_names())
                self.dashboard.setStatus("Ready to start",
                                         f"{MODE_NAMES[self.mode]} in {n} window{'s' if n != 1 else ''}", "")
        self.setup_changed.emit()

    def _on_fix_requested(self, key: str) -> None:
        """A 'ready to start' chip was clicked: open the place that fixes it."""
        if key == "windows":
            self.tabs_bar.setActive("dashboard")
            row = next((r for r in self.dashboard.window_rows if r.isVisible() and not r.selectedWindow()),
                       self.dashboard.window_rows[0])
            row.combo.setFocus()
            return
        if key == "bait" and self.settings.selectedBaitKeys():
            self.tabs_bar.setActive("dashboard")
            self.dashboard.reset_btn.setFocus()
            return
        if key in ("confirm", "armor") and self.mode == MODE_FISHING:
            self.tabs_bar.setActive("inventory")
            return
        tab = {"pages": "inventory", "bait": "activity", "grid": "activity",
               "confirm": "activity", "solver": "activity"}.get(key)
        if tab:
            self.tabs_bar.setActive(tab)

    def _on_start_clicked(self) -> None:
        if self._grid_overlay is not None and self._grid_overlay.isVisible():
            # It lies over the game window, always on top: the bot's clicks
            # would land on it (and drag the grid) instead of the game.
            QMessageBox.information(self, "Grid overlay open",
                                    "Finish the puzzle grid first: Enter keeps it, Esc cancels.")
            self._grid_overlay.raise_()
            self._grid_overlay.activateWindow()
            return
        if self.mode == MODE_JIGSAW:
            self.jigsaw.start()
        else:
            self.start_all_bots()

    def _on_stop_clicked(self) -> None:
        if self.jigsaw.is_running():
            self.jigsaw.stop()
        else:
            self.stop_all_bots()

    def _set_mode_switch_free(self, free: bool) -> None:
        """Fishing started (False) or ended (True): lock / unlock the activity
        switch and the jigsaw-only setup."""
        self.mode_bar.setRunning(None if free else MODE_FISHING)
        self.jigsaw_tab.setSetupEnabled(free)

    def _running_status(self, mode: str, paused: bool = False) -> None:
        """The dashboard status line while something runs."""
        n = len(self.jigsaw.bots) if mode == MODE_JIGSAW else len(self.bots)
        where = f"in {n} window{'s' if n != 1 else ''}"
        if paused:
            self.dashboard.setStatus("Paused", f"{MODE_NAMES[mode]} {where} — press RESUME or F5", "paused")
        else:
            self.dashboard.setStatus(f"{MODE_NAMES[mode]} is running", f"{where} — F5 pauses", "running")

    def _on_activity_started(self, mode: str) -> None:
        """Jigsaw workers started: lock everything the run depends on."""
        self._chimed_paused = False
        self.dashboard.setRunning(True)
        self.mode_bar.setRunning(mode)
        self._set_dashboard_window_selection_locked(True)
        self.settings.setConfigEnabled(False)
        self.inventory.setDisabled(True)
        self.jigsaw_tab.setSetupEnabled(False)
        self._running_status(mode)
        self._update_kpis()

    def _on_activity_stopped(self, mode: str) -> None:
        self.dashboard.setRunning(False)
        self.mode_bar.setRunning(None)
        self._set_dashboard_window_selection_locked(False)
        self.settings.setConfigEnabled(True)
        self.settings.setPaused(False)
        self.inventory.setDisabled(False)
        self.jigsaw_tab.setSetupEnabled(True)
        self._update_kpis()

    def _pause_chime(self, paused: bool) -> None:
        """The pause / resume chime (the game is usually in front, so this
        window's PAUSED cannot be seen)."""
        if self._chimed_paused == paused:
            return
        self._chimed_paused = paused
        if self.config.get("sound_on_pause", True):
            threading.Thread(target=play_pause_beep, args=(paused,), name="pause-chime", daemon=True).start()

    def _on_activity_paused(self, mode: str, paused: bool, chime: Optional[bool] = None) -> None:
        self._pause_chime(paused if chime is None else chime)
        self.dashboard.setPaused(paused)
        self.settings.setPaused(paused)
        self._running_status(mode, paused)

    def _on_cache_state(self, state: str, message: str) -> None:
        self.jigsaw_tab.setCache(state, message)
        self._update_kpis()
        self._refresh_readiness()

    def _on_cache_progress(self, percent: int, text: str) -> None:
        if self.solver_cache_manager.state() == "building":
            self.dashboard.kpi_cap.setValue(f"{percent}%")
            self._refresh_readiness()     # the Solver chip fills up

    def _on_jigsaw_priority(self, priority: str) -> None:
        diag.log("GUI", f"jigsaw crate priority: {priority}")
        self.config["jigsaw_crate_priority"] = priority
        self.save_config()

    def _on_define_grid_clicked(self) -> None:
        if self.jigsaw.is_running():
            QMessageBox.warning(self, "Jigsaw running", "Stop the jigsaw solver before redefining the grid.")
            return
        self._define_jigsaw_grid()

    def _on_set_confirm_clicked(self) -> None:
        if self.jigsaw.is_running():
            QMessageBox.warning(self, "Jigsaw running", "Stop the jigsaw solver before capturing coordinates.")
            return
        self._begin_capture("confirm")

    def _set_dashboard_window_selection_locked(self, locked: bool) -> None:
        for row in self.dashboard.window_rows:
            try:
                row.setComboEnabled(not locked)
            except Exception:
                diag.exc()
                pass

    def _define_jigsaw_grid(self, sender_dialog=None) -> None:
        selected = self.selected_window_names()
        if not selected:
            QMessageBox.warning(self, "No window selected", "Select a game window first.")
            return
        all_windows = {n: w for n, w in WindowManager.get_all_windows()}
        if selected[0] not in all_windows:
            QMessageBox.warning(self, "Window not found", "Refresh the window list first.")
            return
        wm = WindowManager()
        wm.selected_window = all_windows[selected[0]]
        try:
            wm.activate_window(force_activate=True)
        except Exception:
            diag.exc()
            pass
        QTimer.singleShot(150, lambda: self._open_grid_overlay(wm, selected[0], sender_dialog))

    def _open_grid_overlay(self, wm, target_name: str, sender_dialog=None) -> None:
        try:
            left, top, w, h = wm.get_window_rect()
        except Exception as e:
            diag.exc()
            QMessageBox.warning(self, "Window unavailable", str(e))
            return
        if w <= 0 or h <= 0:
            QMessageBox.warning(self, "Window unavailable",
                                "Could not read game window position.")
            return
        existing = self.config.get("jigsaw_grid_bounds")
        found = self._find_puzzle_grid(wm, w, h)
        if found is not None:
            grid = [float(v) for v in found]     # where the bot finds it: a check at a glance
        elif existing and len(existing) == 4:
            grid = [float(v) for v in existing]
        else:
            dw, dh = 264, 176
            grid = [
                float(max(0, (w - dw) // 2)),
                float(max(0, (h - dh) // 2)),
                float(min(dw, w)),
                float(min(dh, h)),
            ]
        # Keep a strong reference — local vars are GC'd as soon as the method returns
        if self._grid_overlay is not None:
            try:
                self._grid_overlay.close()
            except Exception:
                diag.exc()
                pass
        self._grid_overlay = JigsawGridOverlay(left, top, w, h, grid, target_name)
        self._grid_overlay.confirmed.connect(lambda bounds: self._on_grid_confirmed(bounds, sender_dialog, wm))
        self._grid_overlay.destroyed.connect(lambda: setattr(self, "_grid_overlay", None))
        self._grid_overlay.show()
        self._grid_overlay.raise_()
        self._grid_overlay.activateWindow()
        self._grid_overlay.setFocus()

    def _find_puzzle_grid(self, wm: WindowManager, w: int, h: int):
        """The grid of the Fish Puzzle window the bot finds in this game
        window, or None."""
        if not auto_grid(self.config):
            return None
        try:
            grab, release = self._grab_with(wm)
            try:
                frame = grab(0, 0, w, h)
            finally:
                release()
            if frame is None:
                return None
            from jigsaw_detector import JigsawDetector
            found = JigsawDetector(self.config).locate_puzzle(frame)
        except Exception:
            diag.exc("find the puzzle window for Define Grid")
            return None
        diag.log("GUI", f"Define Grid: puzzle window {'found, grid ' + str(found.grid) if found else 'not found'}")
        return found.grid if found else None

    def _on_grid_confirmed(self, bounds: list, sender_dialog=None, wm: Optional[WindowManager] = None) -> None:
        self.config["jigsaw_grid_bounds"] = bounds
        if wm is not None:
            try:
                _, _, w, h = wm.get_window_rect()
                setup_checks.remember_size(self.config, "jigsaw_grid_bounds", (w, h))
            except Exception:
                diag.exc()
        self.save_config()
        self.add_status(f"Jigsaw grid set: {bounds}")
        self.jigsaw_tab.setGrid(bounds, auto_grid(self.config))
        self._refresh_readiness()
        if wm is not None:
            # The overlay was over the grid: bring the game back in front and
            # look at the grid once the overlay is gone.
            try:
                wm.activate_window(force_activate=True)
            except Exception:
                diag.exc()
            QTimer.singleShot(300, lambda: self._check_grid(bounds, wm, sender_dialog))

    def _check_grid(self, bounds: list, wm: WindowManager, parent=None) -> None:
        try:
            _, _, w, h = wm.get_window_rect()
            grab, release = self._grab_with(wm)
            try:
                frame = grab(0, 0, w, h)
            finally:
                release()
            problem = setup_checks.grid_problem(self.config, bounds, frame)
        except Exception:
            diag.exc()
            return
        diag.log("GUI", f"jigsaw grid check at {bounds}: {problem or ('ok' if frame is not None else 'no picture')}")
        if problem:
            show_help(parent if parent is not None and parent.isVisible() else self, "jigsaw_grid",
                      title="Grid not found there", problem=problem)

    @property
    def solver_cache_manager(self) -> "SolverCacheManager":
        """App-wide singleton manager for the jigsaw solver cache."""
        if self._solver_cache_manager is None:
            self._solver_cache_manager = SolverCacheManager(self)
        return self._solver_cache_manager

    def selected_window_names(self) -> List[str]:
        return [row.selectedWindow() for row in self.dashboard.window_rows if row.selectedWindow()]

    def windows_have_same_size(self, names: List[str], all_windows: dict, parent=None,
                               positions: Optional[List[str]] = None) -> bool:
        """Start check shared by Fishbot and the Jigsaw Solver: every selected
        client must have the same window size, because all configured
        positions are offsets measured on one window. Shows a warning and
        returns False when they differ."""
        sizes = []
        for name in names:
            win = all_windows.get(name)
            if win is None:
                continue
            wm = WindowManager()
            wm.selected_window = win
            _, _, w, h = wm.get_window_rect()
            sizes.append((name, (int(w), int(h))))
        message = window_size_mismatch(sizes)
        diag.log("GUI", f"start check window sizes: {sizes} -> {'MISMATCH' if message else 'ok'}")
        if message:
            QMessageBox.warning(parent or self, "Different window sizes", message)
            return False
        if positions and sizes:
            name, size = sizes[0]
            stale = setup_checks.stale_positions(self.config, positions, size)
            diag.log("GUI", f"start check position sizes for {size}: {stale or 'all match'}")
            if stale:
                answer = QMessageBox.question(
                    parent or self, "Window size changed",
                    setup_checks.stale_positions_message(stale, size, name) + "\n\nStart anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return False
        return True

    # ---------------- Config I/O ----------------
    def load_config(self) -> None:
        info = self._config_load_info = [f"path: {self.config_path}"]
        if not os.path.exists(self.config_path):
            info.append("status: MISSING — all settings are defaults")
            self._config_status = "missing"
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                raise ValueError("configuration is not a JSON object")
        except Exception as e:
            diag.exc("config load")
            info.append(f"status: UNREADABLE ({type(e).__name__}: {e}) — moved to .corrupt, defaults used")
            self._config_status = "corrupt"
            # Keep the unreadable file instead of silently overwriting it with
            # defaults on the next save.
            try:
                os.replace(self.config_path, self.config_path + ".corrupt")
            except OSError:
                pass
            if DEBUG_PRINTS:
                print(f"Config load failed: {e}")
            return
        # Retired: the per-frame detail now always goes to frames_*.csv and,
        # around problems, to the log ("Verbose" checkbox before 2.0.3).
        saved.pop("diagnostics_verbose", None)
        defaults = self._default_config()
        missing = sorted(k for k in defaults if k not in saved)
        unknown = sorted(k for k in saved if k not in defaults)
        info.append(f"status: loaded {len(saved)} keys ({os.path.getsize(self.config_path)} bytes), "
                    f"saved by version {saved.get('version')!r}")
        info.append(f"defaulted (not in file): {missing if missing else 'none'}")
        info.append(f"extra keys (not in defaults): {unknown if unknown else 'none'}")
        # Configurations saved before the setup wizards existed: a finished
        # setup counts as having seen that wizard (no pulsing button for it).
        if "wizard_done_fishing" not in saved:
            saved["wizard_done_fishing"] = bool(saved.get("inv_page_1_pos"))
        if "wizard_done_jigsaw" not in saved:
            saved["wizard_done_jigsaw"] = valid_grid_bounds(saved.get("jigsaw_grid_bounds")) or bool(
                saved.get("confirm_button_pos"))
        self._merge_config(saved)
        if self._config_fixed:
            info.append(f"invalid values reset to defaults: {self._config_fixed}")

    @staticmethod
    def _coerce(key: str, value, default):
        """``value`` in the type of ``default``; raises ValueError/TypeError
        when it cannot be. A hand-edited "200" for a number is accepted;
        "abc" is not. Defaults of None (positions, profiles ...) take anything."""
        if default is None or value is None:
            return value
        if isinstance(default, bool):
            if isinstance(value, bool):
                return value
            if value in (0, 1):
                return bool(value)
            raise TypeError(f"{key}: not true/false")
        if isinstance(default, int):
            if isinstance(value, bool):
                raise TypeError(f"{key}: not a number")
            if isinstance(value, float) and value.is_integer():
                return int(value)
            return int(value) if isinstance(value, (int, str)) else int("x")
        if isinstance(default, float):
            if isinstance(value, bool):
                raise TypeError(f"{key}: not a number")
            out = float(value)
            if out != out or out in (float("inf"), float("-inf")):
                raise ValueError(f"{key}: not a finite number")
            return out
        if isinstance(default, str):
            if not isinstance(value, str):
                raise TypeError(f"{key}: not text")
            return value
        if isinstance(default, list):
            if not isinstance(value, list):
                raise TypeError(f"{key}: not a list")
            return value
        if isinstance(default, dict):
            if not isinstance(value, dict):
                raise TypeError(f"{key}: not a mapping")
            return value
        return value

    def _merge_config(self, saved: dict) -> None:
        """Tolerant merge of saved values over the current configuration.
        A value of the wrong type (hand-edited file, old version) falls back
        to the default instead of crashing the program at start."""
        defaults = self._default_config()
        self._config_fixed = []
        for k, v in saved.items():
            if k in defaults:
                try:
                    v = self._coerce(k, v, defaults[k])
                except (TypeError, ValueError):
                    self._config_fixed.append(k)
                    v = defaults[k]
            if k == "bait_keys" and isinstance(v, list):
                v = [str(x) for x in v if str(x) in SettingsTab.BAIT_KEYS]
            if k == "selected_windows" and isinstance(v, list):
                # pad to MAX_WINDOWS
                v = [str(x or "") for x in v][:MAX_WINDOWS]
                v += [""] * (MAX_WINDOWS - len(v))
            if k in ("cast_key", "mount_key") and isinstance(v, str):
                allowed = SettingsTab.CAST_KEYS if k == "cast_key" else SettingsTab.MOUNT_KEYS
                if v.lower() in allowed:
                    v = v.lower()
                else:
                    self._config_fixed.append(k)
                    v = defaults[k]
            if k == "bait_quantity" and isinstance(v, int) and not 1 <= v <= 100000:
                self._config_fixed.append(k)
                v = defaults[k]
            if k == "accent_color" and not (isinstance(v, str) and QColor(v).isValid()):
                self._config_fixed.append(k)
                v = defaults[k]
            if k.endswith("_pos") and v is not None and not (
                    isinstance(v, (list, tuple)) and len(v) == 2
                    and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in v)):
                self._config_fixed.append(k)
                v = None
            self.config[k] = v
        # Coerce to tuples for any *_pos that came back as lists
        for k in list(self.config.keys()):
            if k.endswith("_pos") and isinstance(self.config[k], list) and len(self.config[k]) == 2:
                self.config[k] = tuple(self.config[k])
        # No longer settings (always on): a file saved by an older version
        # with one of them off must not keep it off with no way to turn it on.
        for k in ALWAYS_ON_KEYS:
            self.config[k] = True
        self._remembered_windows = list(self.config.get("selected_windows") or [""] * MAX_WINDOWS)

    def _snapshot_config(self) -> dict:
        cfg = dict(self.config)
        cfg["version"] = APP_VERSION
        cfg["selected_windows"] = list(self._remembered_windows)
        # Remaining bait per slot. Slots whose window is not open right now
        # keep the value they were saved with.
        saved_bait = list(cfg.get("window_bait") or [])
        saved_bait = (saved_bait + [None] * MAX_WINDOWS)[:MAX_WINDOWS]
        for i, row in enumerate(self.dashboard.window_rows):
            if row.selectedWindow():
                saved_bait[i] = int(self.window_stats[i]["bait"])
            elif not self._remembered_windows[i]:
                saved_bait[i] = None
        cfg["window_bait"] = saved_bait
        self.settings.writeToConfig(cfg)
        return cfg

    @staticmethod
    def _write_json_atomic(path: str, data: dict) -> None:
        """Write to a temp file and swap it in, so a crash or power loss
        mid-write cannot leave a truncated configuration behind."""
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    def save_config(self) -> None:
        if self._restoring_rows:
            return
        try:
            cfg = self._snapshot_config()
            if diag.ENABLED:
                for k in sorted(set(cfg) | set(self.config), key=str):
                    old, new = self.config.get(k), cfg.get(k)
                    if old != new and not (isinstance(old, (list, tuple)) and isinstance(new, (list, tuple))
                                           and list(old) == list(new)):
                        diag.log("GUI", f"setting {k}: {diag.summarize(old)} -> {diag.summarize(new)}"
                                        f"{' (bots running: applies at the next Start)' if self.bots else ''}")
            self._write_json_atomic(self.config_path, cfg)
            self.config = cfg
            if self._save_failed:
                self._save_failed = False
                self.notice.clear("save")
        except Exception as e:
            diag.exc("config save")
            # Keep the change in memory and say so: losing every setting at
            # the next start without a word is worse than a notice.
            try:
                self.config = self._snapshot_config()
            except Exception:
                diag.exc()
            self._save_failed = True
            self.notice.show_notice(
                "save", f"Settings could not be saved to {self.config_path} ({type(e).__name__}: {e}). "
                "They are kept until the program closes. Is the folder read-only?", "error", "Try again")
            if DEBUG_PRINTS:
                print(f"Config save failed: {e}")

    # ---------------- Profiles ----------------
    def _save_profile_file(self, path: str) -> None:
        diag.log("GUI", f"profile save to {path}")
        try:
            self._write_json_atomic(path, self._snapshot_config())
            QMessageBox.information(self, "Profile", f"Saved {os.path.basename(path)}")
        except Exception as e:
            diag.exc("profile save")
            QMessageBox.warning(self, "Profile", f"Save failed: {e}")

    def _load_profile_file(self, path: str) -> None:
        diag.log("GUI", f"profile load from {path} (bots running: {bool(self.bots)})")
        if self.bots or self.jigsaw.is_running():
            QMessageBox.warning(self, "Profile", "Stop the bot before loading a profile.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                raise ValueError("not a profile file")
        except Exception as e:
            diag.exc("profile load")
            QMessageBox.warning(self, "Profile", f"Could not load {os.path.basename(path)}:\n{e}")
            return
        diag.block("GUI", f"profile {path} loaded ({len(saved)} keys):", diag.config_lines(saved))
        self.config = self._default_config()
        self._merge_config(saved)
        # Clear the current slot choices so the profile's own are applied
        self._restoring_rows = True
        try:
            for row in self.dashboard.window_rows:
                row.combo.setCurrentIndex(0)
        finally:
            self._restoring_rows = False
        self._apply_state_to_ui()
        self.refresh_window_list()
        # The profile's activity and diagnostics settings apply now, as at a
        # program start (otherwise the UI shows one activity and saves the other).
        self.set_mode(self.config.get("ui_mode", MODE_FISHING), save=False)
        self._apply_diag_settings()
        self.save_config()
        self._update_kpis()
        QMessageBox.information(self, "Profile", f"Loaded {os.path.basename(path)}")

    def _save_settings(self) -> None:
        # Triggered from settings tab on user changes
        self.save_config()
        self._apply_diag_settings()
        # Follow a capacity change (keys or quantity) without refilling the
        # bait the bot has already counted down: only Reset Bait refills.
        cap = len(self.settings.selectedBaitKeys()) * self.config.get("bait_quantity", 200)
        if self._last_bait_cap is not None and cap != self._last_bait_cap:
            self._recalculate_window_bait(self._last_bait_cap)
            self.save_config()
        self._last_bait_cap = cap
        self._update_kpis()

    def _on_accent_changed(self, color: str) -> None:
        self.config["rgb_wave_active"] = False
        self.config["accent_color"] = set_accent(color)
        QApplication.instance().setStyleSheet(build_qss())
        _sync_help_theme()
        self.tabs_bar.recolor()
        self.dashboard.recolor()
        self.save_config()

    def _apply_state_to_ui(self) -> None:
        # Settings
        self.settings.loadFromConfig(self.config)
        # Inventory pills
        for k in self.inventory.coord_pills:
            cfg_key = self._coord_key_to_cfg(k)
            self._set_coord_ui(k, self.config.get(cfg_key))
        self.settings.setAutoFish(self.config.get("auto_fish_handling", False))
        # Jigsaw options
        self.jigsaw_tab.setGrid(self.config.get("jigsaw_grid_bounds"), auto_grid(self.config))
        self.jigsaw_tab.setConfirm(self.config.get("confirm_button_pos"))
        self.jigsaw_tab.setPriority(self.config.get("jigsaw_crate_priority", "normal_first"))
        # Dashboard rows
        self._restore_window_rows()
        self._last_bait_cap = len(self.settings.selectedBaitKeys()) * self.config.get("bait_quantity", 200)
        # Apply accent
        self.config["accent_color"] = set_accent(self.config.get("accent_color", DEFAULT_ACCENT))
        QApplication.instance().setStyleSheet(build_qss())
        self.tabs_bar.recolor()
        self.dashboard.recolor()
        self._update_kpis()

    @staticmethod
    def _coord_key_to_cfg(key: str) -> str:
        if key.startswith("page"):
            return f"inv_page_{key[4:]}_pos"
        if key == "drop":
            return "drop_button_pos"
        if key == "confirm":
            return "confirm_button_pos"
        if key == "armor":
            return "armor_slot_pos"
        return key

    # ---------------- Window list ----------------
    def refresh_window_list(self) -> None:
        if self.bots or self.jigsaw.is_running():
            # The running rows keep their window (see DashboardTab.setRunning)
            diag.log("GUI", "window list refresh skipped: a bot is running")
            return
        try:
            names = [n for n, _ in WindowManager.get_all_windows()]
        except Exception:
            diag.exc()
            names = []
        was_gone = [i for i in range(len(self.dashboard.window_rows)) if self._row_window_gone(i)]
        self._gone_windows.clear()
        self.dashboard.setWindowList(names)
        self._restore_window_rows()
        for i in was_gone:
            # A row that stayed selected fires no change: drop its NOT OPEN here
            self.dashboard.window_rows[i].setMessage("", lock=False)
            self._set_idle_row_status(i)
        self._sync_row_hints()

    def _sync_row_hints(self) -> None:
        """Which window each slot is saved with, and which names other slots
        already use (greyed out in each list)."""
        rows = self.dashboard.window_rows
        taken = {r.selectedWindow() for r in rows if r.selectedWindow()}
        for i, row in enumerate(rows):
            row.setRemembered(self._remembered_windows[i] if i < len(self._remembered_windows) else "")
            row.setTakenNames(taken)

    def _restore_window_rows(self) -> None:
        """Re-selects each slot's remembered window where it is open, and gives
        freshly restored slots the bait they were saved with."""
        rows = self.dashboard.window_rows
        saved_bait = self.config.get("window_bait") or []
        cap = len(self.settings.selectedBaitKeys()) * self.config.get("bait_quantity", 200)
        self._restoring_rows = True
        try:
            for i, row in enumerate(rows):
                name = self._remembered_windows[i] if i < len(self._remembered_windows) else ""
                was_selected = bool(row.selectedWindow())
                if name and not was_selected:
                    row.setSelectedWindow(name)   # fires _on_window_changed when found
                if not was_selected and row.selectedWindow():
                    bait = saved_bait[i] if i < len(saved_bait) else None
                    if isinstance(bait, int) and not isinstance(bait, bool) and 0 <= bait <= cap:
                        self.window_stats[i]["bait"] = bait
                        self._render_row_stats(i)
                elif not row.selectedWindow():
                    # List was rebuilt and this slot's window is gone
                    row.setStatus("idle", "EMPTY")
                    self._render_row_stats(i)
        finally:
            self._restoring_rows = False
        self._update_kpis()

    def _on_window_changed(self, index: int, name: str) -> None:
        rows = self.dashboard.window_rows
        if name and any(r.selectedWindow() == name for i, r in enumerate(rows) if i != index):
            # One bot per client: two bots in one window fight over the cursor
            other = next(i for i, r in enumerate(rows) if i != index and r.selectedWindow() == name)
            row = rows[index]
            row.combo.blockSignals(True)
            prev = self._remembered_windows[index] if not self._restoring_rows else ""
            row.combo.setCurrentIndex(max(0, row.combo.findData(prev)) if prev and prev != name else 0)
            row.combo.blockSignals(False)
            self.notice.show_notice("dup", f"'{name}' is already used by W{other + 1}. Each game window "
                                           "can run one bot only.", "warn")
            return
        if not self._restoring_rows:
            # Explicit choice by the user (including clearing the slot)
            self._gone_windows.discard(self._remembered_windows[index])
            self._remembered_windows[index] = name
            self.notice.clear("dup")
        # Mark any selected slot as SELECTED/EMPTY pill
        row = self.dashboard.window_rows[index]
        self._set_idle_row_status(index)
        if name:
            # Calculate bait capacity based on configured quantity per key and selected keys
            bait_quantity = self.config.get("bait_quantity", 200)
            selected_keys = self.settings.selectedBaitKeys()
            bait_cap = len(selected_keys) * bait_quantity
            self.window_stats[index]["bait"] = bait_cap
        else:
            self.window_stats[index]["bait"] = 0
        self._render_row_stats(index)
        self.save_config()
        self._sync_row_hints()
        self._update_kpis()

    # ---------------- KPIs ----------------
    def _update_kpis(self) -> None:
        rows = self.dashboard.window_rows
        active = len(self.jigsaw.bots) if self.mode == MODE_JIGSAW else len(self.bots)
        self._refresh_readiness()
        if self.mode == MODE_JIGSAW:
            crates = sum(st.get("crates", 0) for st in self.jigsaw.stats.values())
            boards = sum(st.get("boards", 0) for st in self.jigsaw.stats.values())
            self.dashboard.kpi_games.setValue(crates)
            self.dashboard.setRunningCount(active)
            self.dashboard.kpi_bait.setValue(boards)
            state = self.solver_cache_manager.state()
            building = f"{self.solver_cache_manager.progress()[0]}%"
            self.dashboard.kpi_cap.setValue({"ready": "READY", "building": building,
                                             "failed": "FAILED"}.get(state, "NOT BUILT"))
            return
        total_games = sum(self.window_stats[i]["games"] for i in range(MAX_WINDOWS))
        total_bait = sum(self.window_stats[i]["bait"] for i in range(MAX_WINDOWS) if rows[i].selectedWindow())
        bait_keys = self.settings.selectedBaitKeys()
        bait_quantity = self.config.get("bait_quantity", 200)
        cap = len(bait_keys) * bait_quantity
        self.dashboard.setKpis(total_games, active, total_bait, cap)

    # ---------------- Coord capture ----------------
    def _begin_capture(self, key: str) -> None:
        diag.log("GUI", f"coordinate capture started for '{key}'")
        # Need at least one selected window to capture coords relative to
        rows = self.dashboard.window_rows
        selected = [r.selectedWindow() for r in rows if r.selectedWindow()]
        if not selected:
            QMessageBox.warning(
                self, "No window selected",
                "Please select at least one game window on the Dashboard tab "
                "before setting coordinates. The position is captured relative "
                "to that window."
            )
            return
        if self.bots or self.jigsaw.is_running():
            return
        if self._capture.is_active():
            self._capture.cancel()
        self._capture_pill = key
        self._capture_window_name = selected[0]
        self._set_capturing_ui(key)

        # Bring the target window forward, minimize the others.
        try:
            all_windows = WindowManager.get_all_windows()
            window_dict = {n: w for n, w in all_windows}
            self._capture_minimized = []
            for n in selected:
                if n != self._capture_window_name and n in window_dict:
                    try:
                        if not window_dict[n].isMinimized:
                            window_dict[n].minimize()
                            self._capture_minimized.append(n)
                    except Exception:
                        diag.exc()
                        pass
            tgt = window_dict.get(self._capture_window_name)
            if tgt:
                try:
                    if tgt.isMinimized:
                        tgt.restore()
                except Exception:
                    diag.exc()
                    pass
                try:
                    tgt.activate()
                except Exception:
                    diag.exc()
                    pass
        except Exception as e:
            diag.exc()
            if DEBUG_PRINTS:
                print(f"Activate failed: {e}")

        if not self._capture.start(key):
            self._set_capturing_ui(None)
            return
        self._capture_timer.start()

    # Pill key -> how the capture banner names the point to click
    _CAPTURE_WHAT = {"drop": "the Drop / Sell button (or where items are dragged to drop them)",
                     "confirm": "the Yes button of the open dialog",
                     "armor": "the spare armor in the inventory (not the worn one)"}

    def _set_capturing_ui(self, key: Optional[str]) -> None:
        """Shows (or ends) a capture everywhere: pills, banner, wizard."""
        text = ""
        if key:
            what = (f"tab {key[4:]} of the inventory" if key.startswith("page")
                    else self._CAPTURE_WHAT.get(key, "the point"))
            text = (f"Now click {what} in the game window '{self._capture_window_name}'. "
                    f"Esc or a right click cancels.")
            if len(self.selected_window_names()) > 1:
                text += " The other selected clients stay minimized until you click or cancel."
        self.inventory.setCapturing(key, text)
        self.setup_changed.emit()

    def _set_coord_ui(self, key: str, coords) -> None:
        self.inventory.setCoord(key, coords)
        if key == "confirm":
            self.jigsaw_tab.setConfirm(coords)

    def _cancel_capture(self) -> None:
        self._capture_timer.stop()
        if self._capture.is_active() or self._capture_pill:
            self._capture.cancel()
            self._capture_pill = None
            self._capture_window_name = None
            self._set_capturing_ui(None)
            self._restore_capture_minimized()

    def _on_capture_timeout(self) -> None:
        if self._capture.is_active():
            diag.log("GUI", "coordinate capture timed out")
            self._cancel_capture()
            self.notice.show_notice("capture", "The position capture was cancelled: no click in the game "
                                               "within a minute. Click the position again to retry.", "warn")

    def _restore_capture_minimized(self) -> None:
        """Brings back the clients minimized for a coordinate capture."""
        names, self._capture_minimized = getattr(self, "_capture_minimized", []), []
        if not names:
            return
        try:
            window_dict = {n: w for n, w in WindowManager.get_all_windows()}
        except Exception:
            diag.exc()
            return
        for n in names:
            try:
                if n in window_dict and window_dict[n].isMinimized:
                    window_dict[n].restore()
            except Exception:
                diag.exc()

    def _on_captured(self, screen_x: int, screen_y: int, mode: str) -> None:
        if not self._capture_window_name:
            return
        try:
            all_windows = WindowManager.get_all_windows()
            window_dict = {n: w for n, w in all_windows}
            target = window_dict.get(self._capture_window_name)
            if not target:
                return
            wm = WindowManager()
            wm.selected_window = target
            left, top, w, h = wm.get_window_rect()
            rel = (screen_x - left, screen_y - top)
            cfg_key = self._coord_key_to_cfg(mode)
            diag.log("GUI", f"coordinate captured for {cfg_key}: click at screen ({screen_x},{screen_y}), "
                            f"window '{self._capture_window_name}' rect ({left},{top},{w},{h}) -> "
                            f"window-relative {rel} (was {self.config.get(cfg_key)})"
                            f"{'  OUTSIDE THE WINDOW' if not (0 <= rel[0] < w and 0 <= rel[1] < h) else ''}")
            if not (0 <= rel[0] < w and 0 <= rel[1] < h):
                QMessageBox.warning(
                    self, "Click outside the game window",
                    f"That click was outside the game window '{self._capture_window_name}', so the "
                    f"coordinate was not changed. Click the point inside the game window.")
                return
            if self.config.get(cfg_key) != rel and cfg_key in ("drop_button_pos", "confirm_button_pos"):
                dialog_watch.forget()   # the learned dialog look belongs to the old point
            self.config[cfg_key] = rel
            setup_checks.remember_size(self.config, cfg_key, (w, h))
            self.notice.clear("capture")
            self._set_coord_ui(mode, rel)
            if mode == "armor":
                problem = setup_checks.armor_problem(self.config, rel, w, h)
                diag.log("GUI", f"spare armor position check: {problem or 'inside the inventory grid'}")
                if problem:
                    QMessageBox.warning(self, "Not a spare armor in the inventory?", problem)
                self._remember_armor_page(wm)
            self.save_config()
            if mode.startswith("page"):
                self._check_tab_capture(int(mode[4:]), wm)
            self._refresh_readiness()
        finally:
            self._capture_timer.stop()
            self._capture.cancel()
            self._capture_pill = None
            self._capture_window_name = None
            self._set_capturing_ui(None)
            self._restore_capture_minimized()

    def _grab_with(self, wm: WindowManager):
        """A one-off window-relative grab(x, y, w, h) for a setup check, and
        the function that releases it."""
        from screen_capture import WindowCapture
        capture = WindowCapture(wm, self.config.get("capture_backend", "auto"))
        return capture.grab, capture.close

    _TAB_CHECK_DELAY_MS = 400   # the game draws the clicked tab as active by then

    def _check_tab_capture(self, page: int, wm: WindowManager) -> None:
        """A page tab was just set: the same tab set twice is caught at once;
        whether the game now shows that tab as active (and whether the
        inventory sits below the tabs) is looked at a moment later."""
        other = setup_checks.duplicate_tab(self.config, page)
        if other is not None:
            diag.log("GUI", f"page tab check: page {page} set where page {other} is")
            QMessageBox.warning(
                self, "Same tab twice",
                f"Page {page} is set where Page {other} already is. Each page needs its own tab: click "
                f"Page {page} again and then tab {page} of the inventory in the game.")
            return
        QTimer.singleShot(self._TAB_CHECK_DELAY_MS, lambda: self._check_tab_after_click(page, wm))

    def _check_tab_after_click(self, page: int, wm: WindowManager) -> None:
        try:
            _, _, win_w, win_h = wm.get_window_rect()
            grab, release = self._grab_with(wm)
            try:
                state, active, detail = setup_checks.tab_after_click(self.config, page, grab, win_w, win_h)
                strip = setup_checks.strip_problem(self.config, grab, win_w, win_h, state)
            finally:
                release()
        except Exception:
            diag.exc()
            return
        diag.log("GUI", f"page tab check: page {page} -> {state} (read {active}; {detail}); "
                        f"strip: {strip or 'ok / not judged'}")
        if state == "other":
            QMessageBox.warning(
                self, "Another tab is open",
                f"You set Page {page}, but the game shows tab {active} as the open one. The click probably "
                f"landed on the wrong tab or between two. Click Page {page} again and then the middle of tab "
                f"{page} of the inventory.")
        elif state == "unreadable" and not getattr(self, "_tabs_unreadable_warned", False):
            self._tabs_unreadable_warned = True
            QMessageBox.information(
                self, "Open tab not recognized",
                "The bot cannot tell which inventory tab is open on this client. Fishing still works: after "
                "a tab click it waits a fixed time instead of checking. But Armor quick skip then only works "
                "while the bot fills the armor's own page, and the bot cannot tell if a tab click was missed."
                "\n\nIf the tabs were not set on the inventory's tabs, set them again.")
        elif strip:
            QMessageBox.information(self, "Inventory not found below the tabs", strip)

    def _remember_armor_page(self, wm: WindowManager) -> None:
        """The spare armor lies on the page the game shows while its position
        is captured. Armor quick skip needs that page once the bot moves on
        to later pages; without it the bot waits for the animation there."""
        try:
            page, detail = inventory_layout.page_on_screen(self.config, wm,
                                                           self.config.get("capture_backend", "auto"))
        except Exception:
            diag.exc()
            page, detail = None, "reading the tabs failed"
        self.config["armor_slot_page"] = page
        diag.log("GUI", f"spare armor page: {page} ({detail})")
        if page is None and len(inventory_layout.tab_points(self.config)) >= 2:
            QMessageBox.information(
                self, "Spare armor page not recognized",
                "The bot could not tell which inventory page the spare armor is on, so Armor quick "
                "skip will wait for the animation instead whenever the bot fills a page without the "
                "armor.\n\nSet the page tabs (Page 1, 2, 3 ...) first, then capture Spare Armor "
                "again while the game shows the armor's page.")

    def _on_capture_failed(self, msg: str) -> None:
        diag.log("GUI", f"coordinate capture failed: {msg}")
        self._capture_timer.stop()
        self._capture_pill = None
        self._set_capturing_ui(None)
        self._restore_capture_minimized()
        QMessageBox.warning(self, "Capture failed", msg)

    # ---------------- Modals ----------------
    def open_fish_modal(self) -> None:
        modal = FishModal(self.config.get("fish_actions", {}), self)
        modal.saved.connect(self._on_fish_actions_saved)
        modal.exec()

    def _on_fish_actions_saved(self, actions: dict) -> None:
        diag.log("GUI", f"fish actions saved: {actions}")
        self.config["fish_actions"] = actions
        self.save_config()
        self._refresh_readiness()

    def open_timing_modal(self) -> None:
        modal = TimingModal(self.config, self)
        modal.saved.connect(self._on_timing_saved)
        modal.exec()

    def _on_timing_saved(self, timings: dict) -> None:
        diag.log("GUI", f"timing settings saved: {timings}")
        self.config.update(timings)
        self.save_config()

    def _on_auto_fish_toggled(self, enabled: bool) -> None:
        self.config["auto_fish_handling"] = enabled
        self.save_config()
        self._refresh_readiness()

    def _on_unset_coord(self, key: str) -> None:
        """Handle unset coordinate request (click on already-set pill)."""
        cfg_key = self._coord_key_to_cfg(key)
        diag.log("GUI", f"coordinate {cfg_key} cleared (was {self.config.get(cfg_key)})")
        if self.bots or self.jigsaw.is_running():
            return
        self.config[cfg_key] = None
        setup_checks.remember_size(self.config, cfg_key, None)
        self._set_coord_ui(key, None)
        if key == "armor":
            self.config["armor_slot_page"] = None
        self.save_config()
        self._refresh_readiness()

    # ---------------- Bait ----------------
    def _on_reset_bait_clicked(self) -> None:
        """Reset Bait button: asks first (it overwrites the counted bait).
        Works while stopped or paused, not while the bots are clicking."""
        if any(not b.paused for b in self.bots.values()):
            return
        cap = len(self.settings.selectedBaitKeys()) * self.config.get("bait_quantity", 200)
        answer = QMessageBox.question(
            self, "Reset bait",
            f"Set the bait of every selected window back to full ({cap:,})?\n\n"
            "Do this after restocking bait in the game.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self.reset_bait()

    def reset_bait(self) -> None:
        diag.log("GUI", "reset bait pressed")
        bait_quantity = self.config.get("bait_quantity", 200)
        cap = len(self.settings.selectedBaitKeys()) * bait_quantity
        self.config["bait"] = cap
        rows = self.dashboard.window_rows
        for i, row in enumerate(rows):
            self.window_stats[i]["bait"] = cap if row.selectedWindow() else 0
            self._render_row_stats(i)
        for bot in self.bots.values():
            # Paused bots count bait themselves: refill their counter too, or
            # they resume on the old count and stop early. Their own keys
            # (locked while running) give the capacity.
            bot.bait_counter = len(bot.bait_keys) * bait_quantity
        self.notice.clear("bait")
        self._bait_notice_hidden = False
        self.save_config()
        self._update_kpis()

    def _recalculate_window_bait(self, old_cap: int) -> None:
        """After the bait keys or quantity changed: a selected window that was
        full stays full at the new capacity; a counted-down one keeps its
        count (lowered to the new capacity if above it). Refilling is Reset
        Bait's job, which asks first."""
        new_cap = len(self.settings.selectedBaitKeys()) * self.config.get("bait_quantity", 200)
        kept = False
        for i, row in enumerate(self.dashboard.window_rows):
            if not row.selectedWindow():
                continue
            bait = self.window_stats[i]["bait"]
            if bait == old_cap:
                bait = new_cap
            elif bait > new_cap:
                bait = new_cap
            elif bait < new_cap:
                kept = True
            self.window_stats[i]["bait"] = bait
            self._render_row_stats(i)
        if kept and self.mode == MODE_JIGSAW:
            self._bait_notice_hidden = True
        elif kept:
            self._show_bait_notice()

    def _show_bait_notice(self) -> None:
        self.notice.show_notice(
            "bait", "Bait left was not refilled. Press Reset Bait after you restock in the game.",
            kind="info")

    def _copy_btc(self) -> None:
        QApplication.clipboard().setText(self.BTC_ADDRESS)
        # The copy changes nothing else on screen: confirm it on the button
        btn = self._copy_btn
        btn.setIcon(make_icon("check", 11, C["green"]))
        btn.setToolTip("Copied")
        QTimer.singleShot(1200, lambda: (btn.setIcon(make_icon("copy", 11, C["text_mute"])),
                                         btn.setToolTip("Copy BTC address")))

    # ---------------- Bot lifecycle ----------------
    def _report_gone_windows(self, selected: List[Tuple[int, str]], all_windows: dict) -> bool:
        """Before a start: marks the selected windows that are not open any
        more (row, Window chip, status line) and says what to do. True when
        there were any: the start must not go on."""
        gone = [(i, n) for i, n in selected if n not in all_windows]
        if not gone:
            return False
        rows = self.dashboard.window_rows
        for i, n in gone:
            self._gone_windows.add(n)
            rows[i].setStatus("error", "NOT OPEN")
            rows[i].setMessage("Window not open — start the game client or clear this slot", lock=False,
                               kind="error", topic="windows")
        self._refresh_readiness()
        QMessageBox.warning(
            self, "Window not found",
            "These game windows are not open any more:\n\n"
            + "\n".join(f"W{i + 1}: {n}" for i, n in gone)
            + "\n\nStart the client(s) and press Refresh, or clear the slot(s).")
        return True

    def start_all_bots(self) -> None:
        diag.log("GUI", "START pressed")
        if self.jigsaw.is_running():
            QMessageBox.warning(self, "Jigsaw running", "Stop the jigsaw solver before starting fishing.")
            return
        if FishingBot is None:
            QMessageBox.critical(self, "Missing module",
                                 "fishing_bot.py failed to import. "
                                 "Check that all dependencies are installed.")
            return
        rows = self.dashboard.window_rows
        selected = [(i, r.selectedWindow()) for i, r in enumerate(rows) if r.selectedWindow()]
        if not selected:
            QMessageBox.warning(self, "No window selected",
                                "Please select at least one game window on the Dashboard tab before starting.")
            return
        if len({n for _, n in selected}) != len(selected):
            QMessageBox.warning(self, "Same window twice",
                                "One game window is selected in two slots. Each window can run one bot "
                                "only: clear one of the two slots.")
            return
        # Every selected window must be open now: the list shows what was
        # open at the last Refresh, and a client closed since then would be
        # skipped silently.
        all_windows = {n: w for n, w in WindowManager.get_all_windows()}
        if self._report_gone_windows(selected, all_windows):
            return
        bait_keys = self.settings.selectedBaitKeys()
        if not bait_keys:
            QMessageBox.warning(self, "No bait keys", "Select at least one bait key on the FISHING tab.")
            return
        # Validate inventory coords. Only page 1 is needed: clients with fewer
        # pages leave the rest empty (the bot stops at the first unset page),
        # rather than setting made-up positions the bot would then click.
        if not self.config.get("inv_page_1_pos"):
            show_help(self, "inventory_tabs", title="Inventory pages not set",
                      problem="The bot needs to know where your inventory is. Set at least Page 1 on the "
                              "Positions tab (click Page 1, then tab I of your inventory in the game). Set "
                              "further pages only if your client has them.")
            return
        gap = next((p for p in range(2, 9) if not self.config.get(f"inv_page_{p}_pos")
                    and any(self.config.get(f"inv_page_{q}_pos") for q in range(p + 1, 9))), None)
        if gap is not None:
            show_help(self, "inventory_tabs", title="Inventory pages not set",
                      problem=f"Page {gap} is not set but a later page is. The bot uses the pages in order "
                              f"and stops at the first one that is not set: set Page {gap}, or clear the "
                              f"pages after it.")
            return
        for i, _ in selected:
            if self.window_stats[i]["bait"] <= 0:
                QMessageBox.warning(self, "Out of bait",
                                    f"W{i + 1} has 0 bait. Click 'Reset Bait' first.")
                return
        # Confirm + auto-fish drop validation
        if self.config.get("auto_fish_handling", False):
            if any(a == "drop" for a in self.config.get("fish_actions", {}).values()):
                if not self.config.get("confirm_button_pos"):
                    show_help(self, "drop_confirm", title="Confirm coordinate not set",
                              problem="Some fish are set to drop, and a drop needs the Confirm coordinate "
                                      "(the Yes button of the game's drop dialog). Set it on the Positions "
                                      "tab under Action Points, or set those fish to keep or open.")
                    return
        if not self.windows_have_same_size(
                [name for _, name in selected], all_windows,
                positions=setup_checks.FISHING_POSITIONS):
            return
        armor_problem = self._armor_skip_problem()
        if armor_problem and QMessageBox.question(
                self, "Armor quick skip will not work",
                armor_problem + ".\n\nThe bot would wait for the fishing animation instead of skipping it. "
                "Start anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            show_help(self, "armor_slot", title="Spare armor", modal=False)
            return

        # Every fish/item gets an action the bot knows: unset or broken ones
        # (an empty action from an older version) are "keep"
        # The dead fish follow their living fish (no choice of their own)
        session_setup.complete_fish_actions(self.config)

        # Workers from the previous run must have exited, otherwise two bots
        # would drive the same window (and fight over the cursor). Wait at most
        # one second in total (the UI is frozen while waiting).
        # The jigsaw workers of a stopped jigsaw run count too: one still
        # finishing its last move would click alongside these bots.
        self._join_threads(self._stopping_threads + self.jigsaw.threads(), budget=1.0)
        self._stopping_threads = [t for t in self._stopping_threads if t.is_alive()]
        if self._stopping_threads or any(t.is_alive() for t in self.jigsaw.threads()):
            QMessageBox.information(self, "Still stopping",
                                    "The previous run is still finishing its last action. "
                                    "Try again in a moment.")
            return

        self._sound_alert_played = False
        self._chimed_paused = False      # the state the last pause / resume chime announced
        self._session_started_bot_ids = set()
        self._session_bait_depleted_bot_ids = set()
        self._session_non_bait_stop_reasons = {}
        self._help_shown_reasons = set()

        self.save_config()
        if FishingBot is not None:
            FishingBot.reset_session_state()   # nothing learned by the last run's windows carries over

        cfg_for_bot = session_setup.fishing_session_config(self.config, bait_keys)
        self._begin_diag_session("fishing", selected, all_windows, cfg_for_bot)
        for i, name in selected:
            if name not in all_windows:
                diag.log("GUI", f"W{i + 1}: window '{name}' is not open: not started", tag=f"W{i + 1}")
                continue
            wm = WindowManager()
            wm.selected_window = all_windows[name]
            self.window_managers[i] = wm
            # Use the bait value from window_stats (calculated when window was selected)
            # or calculate it if missing (fallback)
            if self.window_stats[i]["bait"]:
                current_bait = self.window_stats[i]["bait"]
            else:
                bait_quantity = self.config.get("bait_quantity", 200)
                current_bait = len(bait_keys) * bait_quantity
            bot = FishingBot(
                None,
                cfg_for_bot.copy(),
                wm,
                bait_counter=current_bait,
                bait_keys=list(bait_keys),
                bot_id=i,
            )
            bot.on_status_update = self._on_status
            # Worker thread -> signal: the GUI's numbers change on its own thread only
            bot.on_stats_update = lambda bid, _hits, games, bait: self._sig_row_stats.emit(bid, games, bait)
            bot.on_bait_update = lambda bid, bait: self._sig_bait.emit(bid, bait)
            # Bind the instance: a late stop notification from a previous run
            # of this slot must not be mistaken for the bot running there now.
            bot.on_bot_stop = lambda bid, b=bot: self._sig_bot_stopped.emit(bid, b)
            bot.on_profile_learned = self._sig_profile_learned.emit
            bot.running = True
            self.bots[i] = bot
            self._session_started_bot_ids.add(i)
            self.window_stats[i]["games"] = 0
            self.window_stats[i]["bait"] = current_bait
            self._create_fish_debug_windows(i, bot)
            rows[i].setStatus("active", "RUNNING")
            rows[i].setMessage("", lock=False)
            rows[i].setComboEnabled(False)
            t = threading.Thread(target=bot.start, daemon=True, name=f"FishingBot-W{i + 1}")
            t.start()
            self.bot_threads[i] = t
        if not self.bots:
            # Nothing started: leave the window as it was (unlocked)
            self._end_diag_session("no bot could be started")
            return
        self.dashboard.setRunning(True)
        self.settings.setConfigEnabled(False)
        self.inventory.setDisabled(True)
        self._set_mode_switch_free(False)
        self._running_status(MODE_FISHING)
        self._update_kpis()

    def stop_all_bots(self) -> None:
        diag.log("GUI", f"STOP pressed ({len(self.bots)} bots running)")
        stopping_ids = list(self.bots.keys())
        for bot in list(self.bots.values()):
            bot.running = False
            try:
                bot.stop()
                self._session_non_bait_stop_reasons[bot.bot_id] = FishingBot.STOP_MANUAL
            except Exception:
                diag.exc()
                pass
        self._destroy_all_fish_debug_windows()
        self._stopping_threads = [t for t in self._stopping_threads if t.is_alive()]
        self._stopping_threads += [t for t in self.bot_threads.values() if t.is_alive()]
        self.bots.clear()
        self.bot_threads.clear()
        self.save_config()  # remember the bait that is left
        for i, row in enumerate(self.dashboard.window_rows):
            self._set_idle_row_status(i)
            row.setComboEnabled(True)
            if i in stopping_ids:
                row.setMessage(self.STOP_REASON_TEXT["manual"], lock=True)
        self.dashboard.setRunning(False)
        self.settings.setConfigEnabled(True)
        self.settings.setPaused(False)
        self.inventory.setDisabled(False)
        self._set_mode_switch_free(True)
        self._update_kpis()
        self._end_diag_session("stopped by user", wait_threads=list(self._stopping_threads))

    def _on_pause_or_start(self) -> None:
        """Called by the primary button while running — toggles pause."""
        if self.bots:
            self.toggle_pause_all()
        elif self.jigsaw.is_running():
            self.jigsaw.toggle_pause()

    def toggle_pause_all(self) -> None:
        """Toggle pause/resume for all running bots. Thread-safe."""
        if not self.bots:
            if DEBUG_PRINTS:
                print("toggle_pause_all: no bots running")
            return
        
        # While any window still runs, pause them all; resume only when all
        # are paused. (Resuming as soon as one was paused left the others
        # running when that one had paused itself.)
        new_paused = not all(b.paused for b in self.bots.values())

        if DEBUG_PRINTS:
            print(f"toggle_pause_all: new_paused={new_paused}")
        
        diag.log("GUI", f"{'PAUSE' if new_paused else 'RESUME'} (F5 / button) for {len(self.bots)} bots")
        # Apply pause state to all bots
        for bot in self.bots.values():
            bot.paused = new_paused
        
        # Update UI - use direct calls since we're already on main thread via QTimer.singleShot
        for i, bot in self.bots.items():
            kind, txt = ("warn", "PAUSED") if new_paused else ("active", "RUNNING")
            self.dashboard.window_rows[i].setStatus(kind, txt)
        
        self._pause_chime(new_paused)
        self.dashboard.setPaused(new_paused)
        self.settings.setPaused(new_paused)
        self._running_status(MODE_FISHING, new_paused)
        
        if DEBUG_PRINTS:
            print(f"toggle_pause_all: completed, paused={new_paused}")

    def _on_status(self, msg: str) -> None:
        self._sig_status_log.emit(msg)
        if DEBUG_PRINTS:
            print(msg)

    def add_status(self, msg: str) -> None:
        """Compatibility logger for UI paths that are not owned by a bot."""
        diag.log("GUI", msg, depth=1)
        self._on_status(msg)

    def _append_debug_status(self, msg: str) -> None:
        # The row of the window this message is about shows it (unless that
        # bot already stopped and the row keeps the reason).
        if msg.startswith("[W") and "] " in msg:
            try:
                idx = int(msg[2:msg.index("]")]) - 1
                if 0 <= idx < len(self.dashboard.window_rows) and idx in self.bots:
                    self.dashboard.window_rows[idx].setMessage(msg.split("] ", 1)[1])
            except ValueError:
                pass
        if self.status_log_window is None:
            return
        try:
            self.status_log_window.add_message(msg)
        except Exception:
            diag.exc()
            pass

    def _apply_row_stats(self, bot_id: int, games: int, bait: int) -> None:
        """A bot's numbers (queued from its thread)."""
        if bot_id in self.window_stats:
            self.window_stats[bot_id]["games"] = games
            self.window_stats[bot_id]["bait"] = bait
        if bot_id < len(self.dashboard.window_rows):
            self.dashboard.window_rows[bot_id].setBait(bait)
            self.dashboard.window_rows[bot_id].setGames(games)
        self._update_kpis()

    def _apply_bait(self, bot_id: int, new_bait: int) -> None:
        self._apply_row_stats(bot_id, self.window_stats[bot_id]["games"], new_bait)

    def _maybe_play_no_bait_alert(self) -> None:
        if self._sound_alert_played or not self.config.get("sound_alert_on_finish", True):
            return
        if not self._session_started_bot_ids:
            return
        if self._session_non_bait_stop_reasons:
            return
        if self._session_bait_depleted_bot_ids != self._session_started_bot_ids:
            return

        self._sound_alert_played = True
        threading.Thread(target=play_rickroll_beep, name="no-bait-alert", daemon=True).start()

    def _on_profile_learned_ui(self, profile: object) -> None:
        """A bot adapted to a re-skinned client — remember it for next time."""
        if isinstance(profile, dict) or profile is None:
            # None: the bot found the saved profile does not work and dropped it.
            self.config["detection_profile"] = profile
            self.save_config()

    def _on_bot_stopped_ui(self, bot_id: int, bot: object = None) -> None:
        if self.bots.get(bot_id) is not bot:
            # Stale notification: that bot was already stopped (and possibly
            # replaced by a new one in the same slot). Nothing to update.
            return
        reason = getattr(bot, "stop_reason", None)
        if FishingBot is not None and reason == FishingBot.STOP_BAIT_DEPLETED:
            self._session_bait_depleted_bot_ids.add(bot_id)
        elif reason is not None:
            self._session_non_bait_stop_reasons[bot_id] = reason
        if FishingBot is not None and reason == getattr(FishingBot, "STOP_CURSOR_ITEM", None):
            # An item may be on the cursor every window shares: the others
            # are already refused input (FishingBot._focus); stop them now
            # rather than at their next click.
            for other in list(self.bots.values()):
                if other is not bot and getattr(other, "running", False):
                    try:
                        other._stop_bot(other.STOP_CURSOR_ITEM,
                                        f"[W{other.bot_id + 1}] Stopped: W{bot_id + 1} may have left an item "
                                        f"on the cursor. Put it back by hand, then start again.")
                    except Exception:
                        diag.exc()

        self.bots.pop(bot_id, None)
        thread = self.bot_threads.pop(bot_id, None)
        if thread is not None and thread.is_alive():
            self._stopping_threads.append(thread)
        self._destroy_fish_debug_windows(bot_id)
        self.save_config()  # remember the bait that is left
        why = getattr(bot, "stop_message", None) or self.STOP_REASON_TEXT.get(reason, str(reason or "Stopped"))
        diag.log("GUI", f"W{bot_id + 1} stopped: reason={reason} shown as {why!r}", tag=f"W{bot_id + 1}")
        topic = help_visuals.STOP_REASON_TOPIC.get(reason)
        if topic and reason not in self._help_shown_reasons:
            # The row shows one line only: show the whole reason, what to do
            # and a picture, once per reason and session (not one per window).
            self._help_shown_reasons.add(reason)
            show_help(self, topic, problem=why, title=self.STOP_REASON_TEXT.get(reason, "Stopped"), modal=False)
        if bot_id < len(self.dashboard.window_rows):
            row = self.dashboard.window_rows[bot_id]
            row.setStatus("error", "STOPPED")
            row.setMessage(why, lock=True,
                           kind="" if reason in (None, "manual", "bait_depleted") else "error",
                           topic=topic or "")
            row.setComboEnabled(True)
        if not self.bots:
            self._end_diag_session("all bots stopped",
                                   wait_threads=[t for t in self._stopping_threads if t.is_alive()])
            self.dashboard.setRunning(False)
            self.settings.setConfigEnabled(True)
            self.settings.setPaused(False)
            self.inventory.setDisabled(False)
            self._set_mode_switch_free(True)
            self._update_kpis()
            self._maybe_play_no_bait_alert()

    def _create_fish_debug_windows(self, bot_id: int, bot) -> None:
        self._open_debug_windows(("fishing", bot_id), [IgnoredPositionsWindow, FishDetectorDebugWindow,
                                                       InventoryDetectionDebugWindow], bot)

    def _destroy_fish_debug_windows(self, bot_id: int) -> None:
        self._close_debug_windows(("fishing", bot_id))

    def _destroy_all_fish_debug_windows(self) -> None:
        for bot_id in range(MAX_WINDOWS):
            self._close_debug_windows(("fishing", bot_id))

    @staticmethod
    def _join_threads(threads, budget: float) -> None:
        """Waits for ``threads`` to end, ``budget`` seconds in total."""
        deadline = time.monotonic() + budget
        for t in threads:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            t.join(timeout=left)

    def _poll_bot_stats(self) -> None:
        # Refresh KPIs even when no signals fired (cheap)
        self._update_kpis()

    # ---------------- Global Hotkey Listener ----------------
    def _start_global_hotkey_listener(self) -> None:
        """Start a global hotkey listener for F5 pause/resume using pynput.
        This works even when the game window has focus."""
        if pyn_keyboard is None:
            if DEBUG_PRINTS:
                print("pynput not available, F5 global hotkey disabled")
            return

        self._f5_last_toggle_time = 0.0  # Debounce: track last toggle time
        self._f5_toggle_lock = threading.Lock()  # Thread-safe access to toggle time
        
        def on_press(key):
            try:
                # Esc cancels a position capture also while the game has focus
                if key == pyn_keyboard.Key.esc and self._capture.is_active():
                    self._sig_capture_cancel.emit()
                    return
                # Check if F5 was pressed
                if key == pyn_keyboard.Key.f5:
                    import time
                    current_time = time.time()
                    # Debounce: only allow one toggle per 500ms
                    with self._f5_toggle_lock:
                        if (current_time - self._f5_last_toggle_time) > 0.5:
                            self._f5_last_toggle_time = current_time
                            if DEBUG_PRINTS:
                                print("F5 pressed - toggling pause")
                            # Emit signal — Qt queues it to the main thread automatically
                            self._f5_toggle.emit()
            except Exception as e:
                diag.exc()
                if DEBUG_PRINTS:
                    print(f"Hotkey listener error: {e}")
                pass

        # Start listener in a daemon thread (only on_press, no on_release needed)
        self._global_hotkey_listener = pyn_keyboard.Listener(on_press=on_press)
        self._global_hotkey_listener.start()
        if DEBUG_PRINTS:
            print("Global F5 hotkey listener started")

    def _stop_global_hotkey_listener(self) -> None:
        """Stop the global hotkey listener."""
        if self._global_hotkey_listener is not None:
            try:
                self._global_hotkey_listener.stop()
            except Exception:
                diag.exc()
                pass
            self._global_hotkey_listener = None

    # ---------------- Lifecycle ----------------
    def closeEvent(self, ev) -> None:
        # Close sits next to Minimize on a frameless bar: a running session
        # is not ended by a slip of the mouse.
        if (self.bots or self.jigsaw.is_running()) and QMessageBox.question(
                self, "Quit while running?",
                "A bot is still running. Stop it and quit?\n\n(Minimize keeps it running.)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            diag.log("GUI", "close cancelled: a bot is running")
            ev.ignore()
            return
        diag.log("GUI", f"program closing ({len(self.bots)} bots running)")
        for bot in list(self.bots.values()):
            bot.running = False
        self._capture.cancel()
        self._stop_global_hotkey_listener()
        self._destroy_all_fish_debug_windows()
        try:
            self.jigsaw.shutdown()
        except Exception:
            diag.exc()
        # The workers are daemon threads: once this returns the process may
        # end and kill them wherever they are — mid-click (button left down)
        # or mid-drop (item left on the cursor). Let them finish the action
        # they are in, diagnostic log or not.
        workers = list(self.bot_threads.values()) + list(self._stopping_threads) + self.jigsaw.threads()
        self._join_threads([t for t in workers if t.is_alive()], budget=self.QUIT_JOIN_BUDGET)
        hung = [t.name for t in workers if t.is_alive()]
        if hung:
            diag.log("GUI", f"quit: workers still running after {self.QUIT_JOIN_BUDGET:.0f}s: {hung}")
        if self._wizard is not None:
            self._wizard.close()
        if self._grid_overlay is not None:
            # Parentless and always on top: it would stay over the game
            self._grid_overlay.close()
        if self._debug_host is not None:
            self._debug_host.stop()
            self._debug_host = None
        self.status_log_window = None
        self.save_config()
        if self._log_dialog is not None:
            self._log_dialog.close()
        threads = list(self.bot_threads.values()) + list(self._stopping_threads) + self.jigsaw.threads()
        self._end_diag_session("program closing", wait_threads=threads)
        super().closeEvent(ev)

    # ---------------- Diagnostics ----------------
    def _apply_diag_settings(self) -> None:
        diag.configure(bool(self.config.get("diagnostics_enabled", False)))
        folder, fallback = diag.location(create=False)
        self.settings.setDiagLocation(folder, fallback)

    def _diag_header(self, selected, all_windows) -> Dict[str, List[str]]:
        """Session header sections the GUI knows about (diag adds the system)."""
        header: Dict[str, List[str]] = {"config file:": list(self._config_load_info)}
        try:
            name, err = probed_backend_info()
            header["input backend:"] = [
                f"requested: {self.config.get('input_backend', 'auto')!r}",
                f"probe at program start: {name!r}" + (f" (error: {err})" if err else ""),
                "per-bot backend actually used: see each bot's 'starts with' block",
            ]
        except Exception:
            diag.exc("input backend info")
        lines = []
        try:
            app = QApplication.instance()
            from PySide6 import __version__ as pyside_version
            lines.append(f"Qt platform: {app.platformName()}  PySide6 {pyside_version}")
            primary = app.primaryScreen()
            for i, sc in enumerate(app.screens()):
                g, a = sc.geometry(), sc.availableGeometry()
                lines.append(
                    f"screen {i + 1}: {sc.name()!r} {sc.manufacturer()} {sc.model()} geometry=({g.x()},{g.y()}) "
                    f"{g.width()}x{g.height()} available=({a.x()},{a.y()}) {a.width()}x{a.height()} "
                    f"devicePixelRatio={sc.devicePixelRatio()} logicalDPI={sc.logicalDotsPerInch():.0f} "
                    f"physicalDPI={sc.physicalDotsPerInch():.0f} refresh={sc.refreshRate():.0f}Hz "
                    f"primary={sc is primary}")
        except Exception:
            diag.exc("screen info")
        header["screens (as Qt sees them; physical pixels = logical x devicePixelRatio):"] = lines
        win_lines = []
        for i, name in selected:
            win = all_windows.get(name)
            if win is None:
                win_lines.append(f"W{i + 1}: '{name}' NOT OPEN")
            else:
                win_lines += diag.window_lines(f"W{i + 1}", name, win)
        header["game windows:"] = win_lines
        return header

    def _begin_diag_session(self, kind: str, selected, all_windows, cfg: dict) -> None:
        if not self.config.get("diagnostics_enabled", False):
            return
        try:
            folder = diag.start_session(self._diag_header(selected, all_windows), cfg, self.config_path,
                                        reason=f"{kind} start")
            if folder:
                self.settings.setDiagLast(f"Recording this run: {folder}")
                self._apply_diag_settings()
                if self.config.get("diagnostics_record_screens", False):
                    self._start_screen_recording(folder, selected, all_windows)
        except Exception:
            diag.exc("starting diagnostic session")

    def _start_screen_recording(self, folder: str, selected, all_windows) -> None:
        """'Record game windows': a video of each selected game window into
        the session folder (screen_recorder). Own WindowManagers: the
        recorders never touch the bots' objects."""
        from screen_recorder import SessionRecorder
        windows = {}
        for i, name in selected:
            if name in all_windows:
                wm = WindowManager()
                wm.selected_window = all_windows[name]
                wm.diag_tag = f"W{i + 1}"
                windows[f"W{i + 1}"] = wm
        self._screen_recorder = SessionRecorder()
        self._screen_recorder.start(windows, folder, str(self.config.get("capture_backend", "auto")))

    def _end_diag_session(self, reason: str, wait_threads=()) -> None:
        recorder, self._screen_recorder = self._screen_recorder, None
        if recorder is not None:
            wait_threads = list(wait_threads) + recorder.stop()   # videos closed before the zip
        if not diag.session_active():
            return
        diag.end_session(reason, wait_threads=wait_threads, on_done=self._sig_diag_done.emit)
        self.settings.setDiagLast("Packing the diagnostic log…")

    def _on_diag_session_packed(self, zip_path, error) -> None:
        if zip_path:
            text = f"Last diagnostic log: {zip_path}"
            self.settings.setDiagLast(text)
            if self.isVisible():
                box = QMessageBox(QMessageBox.Information, "Diagnostic log saved",
                                  f"The diagnostic log of this run was saved as:\n\n{zip_path}\n\n"
                                  f"Send this file to support.", QMessageBox.Ok, self)
                open_btn = box.addButton("Open log folder", QMessageBox.ActionRole)
                open_btn.clicked.connect(self.open_log_folder)
                box.setModal(False)
                box.show()
        else:
            folder, _ = diag.location()
            self.settings.setDiagLast(f"Could not pack the diagnostic log ({error}); "
                                      f"the unpacked folder is in {folder}")

    def _on_diag_recovered(self, zips) -> None:
        if zips:
            self.settings.setDiagLast(f"Packed {len(zips)} log(s) of a run that did not end normally: "
                                      f"{zips[-1]}")

    def open_log_folder(self) -> None:
        latest = diag.last_zip_path() or diag.latest_zip()
        folder = os.path.dirname(latest) if latest else diag.location()[0]
        diag.log("GUI", f"open log folder {folder} (select {latest})")
        if not os.path.isdir(folder):
            QMessageBox.information(self, "Diagnostic log",
                                    f"No diagnostic log has been written yet.\nLogs will be saved in:\n{folder}")
            return
        import subprocess
        try:
            if latest and sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(latest)])
                return
            if latest and sys.platform.startswith("linux"):
                import shutil
                if shutil.which("dbus-send"):
                    uri = QUrl.fromLocalFile(latest).toString()
                    rc = subprocess.call(
                        ["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                         "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                         f"array:string:{uri}", "string:"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    if rc == 0:
                        return
        except Exception:
            diag.exc("show log in file manager")
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def show_log_window(self) -> None:
        diag.log("GUI", "show log window")
        if self._log_dialog is None:
            self._log_dialog = DiagnosticLogDialog(self)
            self._log_dialog.setAttribute(Qt.WA_DeleteOnClose)
            self._log_dialog.finished.connect(lambda _r: setattr(self, "_log_dialog", None))
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()


# ============================================================================
# Entry point
# ============================================================================

CONFIG_NAME = "bot_config.json"
_config_path_cache: Optional[str] = None


def config_file_path() -> str:
    """Where bot_config.json lives: next to the program (the .exe when
    frozen, the project root from source), like the diagnostics and the
    solver cache — not in whatever folder the program was started from.

    A folder that cannot be written to (Program Files) falls back to a
    per-user folder. A config left in the working directory by an older
    version is copied over once. ``FISHBOT_CONFIG_DIR`` overrides it all
    (the tests point it at their temporary working directory)."""
    global _config_path_cache
    override = os.environ.get("FISHBOT_CONFIG_DIR")
    if override:
        return os.path.join(os.path.abspath(override), CONFIG_NAME)
    if _config_path_cache is not None:
        return _config_path_cache
    primary = os.path.join(diag.program_dir(), CONFIG_NAME)
    fallback = os.path.join(os.path.dirname(diag.user_dir()), CONFIG_NAME)
    for path in (primary, fallback):
        if os.path.exists(path):
            _config_path_cache = path
            return path
    target = primary if diag._can_write(os.path.dirname(primary)) else fallback
    legacy = os.path.join(os.getcwd(), CONFIG_NAME)
    if os.path.exists(legacy) and os.path.abspath(legacy) != os.path.abspath(target):
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            import shutil
            shutil.copy2(legacy, target)
            diag.log("CONFIG", f"config moved from the working directory {legacy} to {target}")
        except OSError:
            diag.exc("config migration")
            target = legacy
    _config_path_cache = target
    return target


def _saved_setting(key: str, default):
    """One saved setting, read before the window (and its config loading)
    exists: Qt's scale factor must be set before QApplication, and the accent
    before the widgets that paint with it are built."""
    try:
        with open(config_file_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
        return saved.get(key, default) if isinstance(saved, dict) else default
    except Exception:
        return default


def _saved_ui_scale_setting() -> str:
    """The Interface size choice (see _saved_setting)."""
    return str(_saved_setting("ui_scale", "auto"))


def _apply_ui_scale(app: QApplication) -> QApplication:
    """Measures the screen and, when the interface should be bigger, restarts
    Qt with QT_SCALE_FACTOR (Qt reads it only at start). A scale factor set by
    the user in the environment is left alone."""
    global ACTIVE_UI_SCALE, ACTIVE_UI_SCALE_SOURCE, ACTIVE_UI_SCALE_SETTING, ACTIVE_WINDOW_H
    env = os.environ.get("QT_SCALE_FACTOR", "").strip()
    if env:
        try:
            ACTIVE_UI_SCALE, ACTIVE_UI_SCALE_SOURCE = float(env), "env"
        except ValueError:
            pass
        return app
    setting = _saved_ui_scale_setting()
    screen = app.primaryScreen()
    if screen is None:
        return app
    avail = screen.availableGeometry()
    scale = choose_ui_scale(setting, avail.width(), avail.height())
    diag.log("GUI", f"interface size: setting={setting} screen avail={avail.width()}x{avail.height()} "
                    f"dpr={screen.devicePixelRatio()} -> scale {scale:g}")
    ACTIVE_UI_SCALE_SOURCE = "auto" if setting in ("", "auto") else "setting"
    ACTIVE_UI_SCALE_SETTING = setting if setting in [k for k, _ in UI_SCALE_CHOICES] else "auto"
    ACTIVE_WINDOW_H = window_height_for(avail.height(), scale)
    if ACTIVE_WINDOW_H < WINDOW_H:
        diag.log("GUI", f"screen too short at {scale:g}: window height {ACTIVE_WINDOW_H} (body scrolls)")
    if abs(scale - 1.0) < 1e-6 or not hasattr(app, "shutdown"):
        return app
    app.shutdown()
    del app
    os.environ["QT_SCALE_FACTOR"] = f"{scale:g}"
    ACTIVE_UI_SCALE = scale
    return QApplication(sys.argv)


def dark_palette(pal: QPalette) -> QPalette:
    """The dark palette for everything the stylesheet does not reach (native
    dialogs, menus): without it a light desktop theme shows light buttons
    with this program's light text."""
    roles = {
        QPalette.Window: C["bg_1"], QPalette.Base: C["bg_1"], QPalette.AlternateBase: C["bg_2"],
        QPalette.Text: C["text"], QPalette.WindowText: C["text"], QPalette.Button: C["bg_3"],
        QPalette.ButtonText: C["text"], QPalette.ToolTipBase: C["bg_3"], QPalette.ToolTipText: C["text"],
        QPalette.Highlight: C["accent"], QPalette.HighlightedText: C["on_accent"],
        QPalette.PlaceholderText: C["text_mute"], QPalette.BrightText: C["red"], QPalette.Link: C["blue"],
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(C["text_off"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(C["text_off"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(C["text_off"]))
    return pal


def main() -> int:
    app = _apply_ui_scale(QApplication(sys.argv))
    set_accent(_saved_setting("accent_color", DEFAULT_ACCENT))
    app.setPalette(dark_palette(app.palette()))
    app.setStyleSheet(build_qss())
    w = FishbotWindow()
    w.show()
    code = app.exec()
    if w._restart_on_close:
        try:
            disguise.restart()
        except Exception:
            diag.exc("restart")
    return code


if __name__ == "__main__":
    sys.exit(main())

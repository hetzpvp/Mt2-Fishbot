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
import os
import sys
import threading
from typing import Dict, List, Optional, Tuple

# High-DPI awareness must be set before any Qt window is created.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
os.environ.setdefault("QT_QPA_PLATFORM", "windows:dpiawareness=0")

from PySide6.QtCore import (
    QByteArray, QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QKeySequence, QMovie, QPainter,
    QPalette, QPixmap, QShortcut,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
    QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

# Make src/ importable when running as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from utils import (  # noqa: E402
    DEBUG_MODE_EN, DEBUG_PRINTS, MAX_WINDOWS, get_project_root, get_resource_path,
    load_window_icon, play_rickroll_beep,
)
from window_manager import WindowManager  # noqa: E402
from input_backend import probe_backend  # noqa: E402

try:
    import tkinter as tk  # noqa: E402
    from debug_ui import (  # noqa: E402
        FishDetectorDebugWindow,
        IgnoredPositionsWindow,
        InventoryDetectionDebugWindow,
        JigsawSolverDebugWindow,
        StatusLogWindow,
    )
except Exception:
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
    FishingBot = None

try:
    from pynput import keyboard as pyn_keyboard, mouse as pyn_mouse  # noqa: E402
except Exception:
    pyn_keyboard = None
    pyn_mouse = None

try:
    from version import VERSION as APP_VERSION  # noqa: E402
except Exception:
    APP_VERSION = "1.2"


# ============================================================================
# Design tokens (mirror styles.css)
# ============================================================================

C = {
    "bg_0": "#0b0b0c", "bg_1": "#131316", "bg_2": "#1a1a1e",
    "bg_3": "#222227", "bg_4": "#2c2c33",
    "line": "#2a2a30", "line_2": "#36363e",
    "text": "#e8e8ec", "text_dim": "#9a9aa2", "text_mute": "#6a6a73",
    "accent": "#ffb627", "accent_deep": "#cc8a00",
    "green": "#4ade80", "red": "#ef5e5e", "blue": "#5aa9ff",
    "purple": "#b46cf2", "orange": "#ff9c5a",
}

MONO = "'Consolas', 'JetBrains Mono', 'Courier New', monospace"
SANS = "'Segoe UI', 'Inter', system-ui, sans-serif"


def build_qss(accent: str = C["accent"]) -> str:
    """Returns the global stylesheet, parameterized by accent color."""
    return f"""
    /* ---------- Base ---------- */
    QWidget {{
        color: {C['text']};
        background: transparent;
        font-family: {SANS};
        font-size: 13px;
    }}
    QToolTip {{
        color: {C['text']};
        background: {C['bg_3']};
        border: 1px solid {C['line_2']};
        padding: 4px 8px;
        font-family: {MONO};
        font-size: 10px;
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

    /* ---------- Title bar ---------- */
    #TitleBar {{
        background: #18181c;
        border-bottom: 1px solid {C['line']};
    }}
    #TitleLabel {{
        font-family: {MONO};
        font-size: 11px;
        color: {C['text_dim']};
        letter-spacing: 1px;
    }}
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
        font-size: 18px;
        font-weight: 700;
        color: {accent};
        letter-spacing: 1px;
    }}
    #HeaderVersion {{
        font-family: {MONO};
        font-size: 14px;
        color: {C['text_mute']};
        font-weight: 400;
        letter-spacing: 1px;
    }}
    .HlPill {{
        background: {C['bg_2']};
        border: 1px solid {C['line_2']};
        border-radius: 11px;
        padding: 0 10px;
    }}
    .HlLabel {{
        color: {C['text_mute']};
        font-family: {MONO};
        font-size: 9px;
        letter-spacing: 2px;
    }}
    .HlValue {{
        color: {accent};
        font-family: {MONO};
        font-size: 11px;
        font-weight: 600;
    }}
    .HlValueIdle {{ color: {C['green']}; font-family: {MONO}; font-size: 11px; font-weight: 600; }}
    .HlValueRun  {{ color: {C['accent']}; font-family: {MONO}; font-size: 11px; font-weight: 600; }}

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
        font-size: 11px;
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
        background: {C['bg_4']};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: #3a3a42; }}
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
        font-size: 11px;
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
        font-size: 11px;
        background: {C['bg_3']};
        color: {C['text']};
        border: 1px solid {C['line_2']};
        padding: 6px 12px;
        border-radius: 6px;
        letter-spacing: 1px;
    }}
    QPushButton.Btn:hover {{ background: {C['bg_4']}; border: 1px solid #444; }}
    QPushButton.Btn:disabled {{ color: {C['text_mute']}; background: {C['bg_2']}; }}

    QPushButton.BtnGhost {{
        background: transparent;
        color: {C['text']};
        border: 1px solid {C['line']};
        font-family: {MONO};
        font-size: 11px;
        padding: 6px 12px;
        border-radius: 6px;
        letter-spacing: 1px;
    }}
    QPushButton.BtnGhost:hover {{ background: {C['bg_3']}; }}

    QPushButton.BtnPrimary {{
        background: {accent};
        color: #1a1108;
        font-weight: 700;
        border: 1px solid {C['accent_deep']};
        padding: 8px 14px;
        border-radius: 6px;
        font-family: {MONO};
        font-size: 11px;
        letter-spacing: 1px;
    }}
    QPushButton.BtnPrimary:hover {{ background: #ffc645; }}
    QPushButton.BtnPrimary:disabled {{ background: {C['bg_3']}; color: {C['text_mute']}; border: 1px solid {C['line_2']}; }}

    QPushButton.BtnDanger {{
        background: {C['bg_3']};
        color: {C['red']};
        border: 1px solid #4a2628;
        font-weight: 700;
        padding: 8px 14px;
        border-radius: 6px;
        font-family: {MONO};
        font-size: 11px;
        letter-spacing: 1px;
    }}
    QPushButton.BtnDanger:hover {{ background: #2c1517; border: 1px solid {C['red']}; }}

    QPushButton.BtnLarge {{
        font-size: 12px;
        font-weight: 700;
        padding: 10px 16px;
        border-radius: 8px;
        letter-spacing: 1px;
    }}

    /* ---------- Inputs ---------- */
    QLineEdit, QComboBox {{
        font-family: {MONO};
        font-size: 12px;
        background: {C['bg_1']};
        color: {C['text']};
        border: 1px solid {C['line_2']};
        border-radius: 6px;
        padding: 5px 8px;
        selection-background-color: {accent};
        selection-color: #1a1108;
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
        selection-color: #1a1108;
        outline: none;
    }}

    /* ---------- Checkbox ---------- */
    QCheckBox {{
        font-family: {MONO};
        font-size: 11px;
        color: {C['text']};
        spacing: 8px;
    }}
    QCheckBox:disabled {{ color: {C['text_mute']}; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        background: {C['bg_1']};
        border: 1px solid {C['line_2']};
        border-radius: 3px;
    }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border: 1px solid {accent};
        image: url(none);
    }}

    /* ---------- Pills ---------- */
    .Pill {{
        background: {C['bg_3']};
        border: 1px solid {C['line_2']};
        border-radius: 10px;
        font-family: {MONO};
        font-size: 10px;
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
        font-size: 11px;
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
        font-size: 11px;
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
        font-size: 11px;
        color: {C['text']};
    }}
    .CoordPillCoords {{
        font-family: {MONO};
        font-size: 10px;
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
        font-size: 10px;
        color: {C['text_mute']};
    }}
    .WinNumActive {{ color: {C['green']}; }}
    .WStatLabel {{
        font-family: {MONO};
        font-size: 10px;
        color: {C['text_mute']};
    }}
    .WStatVal {{
        font-family: {MONO};
        font-size: 10px;
        color: {accent};
    }}

    /* ---------- Footer ---------- */
    #Footer {{
        background: {C['bg_1']};
        border-top: 1px solid {C['line']};
    }}
    .Donations {{
        font-family: {MONO};
        font-size: 11px;
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
        font-size: 13px;
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
        font-size: 10px;
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
    .FishImgKeep {{ border: 3px solid rgba(74,222,128,0.95); }}
    .FishImgDrop {{ border: 3px solid rgba(239,94,94,0.95); }}
    .FishImgOpen {{ border: 3px solid rgba(90,169,255,0.95); }}
    .FishName {{
        font-family: {MONO};
        font-size: 12px;
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
        font-size: 10px;
        font-weight: 700;
        color: {C['text_mute']};
        padding: 4px 0;
    }}
    QPushButton.SegBtn:hover {{ color: {C['text']}; background: {C['bg_3']}; }}
    QPushButton.SegBtn[active="keep"]  {{ background: rgba(74,222,128,0.18); color: {C['green']}; }}
    QPushButton.SegBtn[active="drop"]  {{ background: rgba(239,94,94,0.18); color: {C['red']}; }}
    QPushButton.SegBtn[active="open"]  {{ background: rgba(90,169,255,0.18); color: {C['blue']}; }}

    /* ---------- Wizard ---------- */
    .WizStep {{ font-family: {MONO}; font-size: 10px; color: {C['text_mute']}; letter-spacing: 1px; }}
    .WizStep[state="active"] {{ color: {accent}; }}
    .WizStep[state="done"]   {{ color: {C['green']}; }}
    .WizNum {{
        background: {C['bg_3']};
        border: 1px solid {C['line_2']};
        border-radius: 11px;
        font-family: {MONO};
        font-size: 10px;
        font-weight: 700;
        color: {C['text']};
    }}
    .WizNum[state="active"] {{ background: {accent}; color: #1a1108; border: 1px solid {accent}; }}
    .WizNum[state="done"]   {{ background: rgba(74,222,128,0.15); color: {C['green']}; border: 1px solid rgba(74,222,128,0.4); }}
    .WizLine {{ background: {C['line_2']}; }}

    /* ---------- Swatches ---------- */
    .Swatch {{
        border: 2px solid transparent;
        border-radius: 4px;
    }}
    .Swatch[active="true"] {{
        border: 2px solid white;
    }}

    /* ---------- KBD ---------- */
    .Kbd {{
        font-family: {MONO};
        font-size: 9px;
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
        self.setFixedHeight(20)

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
    def __init__(self, label: str, value: str = "0", sub: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "Kpi")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self._label = QLabel(label.upper())
        self._label.setProperty("class", "KLabel")
        self._value = QLabel(str(value))
        self._value.setProperty("class", "KValue")
        self._sub = QLabel(sub)
        self._sub.setProperty("class", "KSub")
        layout.addWidget(self._label)
        layout.addWidget(self._value)
        layout.addWidget(self._sub)

    def setValue(self, v) -> None:
        self._value.setText(str(v))


class CoordPill(QFrame):
    """Click-to-set coord pill with a check icon when set. Clicking again unsets."""

    clicked = Signal()
    unset_requested = Signal()  # Emitted when clicking an already-set pill

    def __init__(self, label: str, coords: Optional[Tuple[int, int]] = None,
                 optional: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "CoordPill")
        self.setProperty("set", "false")
        self.setProperty("capturing", "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)
        self._optional = optional
        self._coords = None
        self._disabled = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)
        self._icon = QLabel()
        self._icon.setFixedSize(14, 14)
        layout.addWidget(self._icon)
        self._label = QLabel(label + (" · Optional" if optional else ""))
        self._label.setProperty("class", "CoordPillLabel")
        layout.addWidget(self._label, 1)
        self._coords_lbl = QLabel("not set")
        self._coords_lbl.setProperty("class", "CoordPillCoords")
        layout.addWidget(self._coords_lbl)
        self.setCoords(coords)

    def mousePressEvent(self, ev) -> None:
        if self._disabled:
            ev.accept()
            return
        if ev.button() == Qt.LeftButton:
            # If already set, emit unset signal; otherwise emit clicked for capture
            if self._coords is not None:
                self.unset_requested.emit()
            else:
                self.clicked.emit()
        super().mousePressEvent(ev)

    def setCoords(self, coords: Optional[Tuple[int, int]]) -> None:
        self._coords = coords
        if coords:
            self.setProperty("set", "true")
            x, y = coords
            self._coords_lbl.setText(f"{x},{y}")
            self._coords_lbl.setStyleSheet(f"color: {C['green']};")
            self._set_icon("check", C["green"])
        else:
            self.setProperty("set", "false")
            # Red for mandatory unset, muted for optional unset
            color = C["red"] if not self._optional else C["text_mute"]
            self._coords_lbl.setText("not set")
            self._coords_lbl.setStyleSheet(f"color: {color};")
            self._set_icon("target", color)
        self.style().unpolish(self)
        self.style().polish(self)

    def setCapturing(self, capturing: bool) -> None:
        self.setProperty("capturing", "true" if capturing else "false")
        if capturing:
            self._coords_lbl.setText("click in game…")
            self._coords_lbl.setStyleSheet(f"color: {C['orange']};")
            self._set_icon("target", C["orange"])
        self.style().unpolish(self)
        self.style().polish(self)

    def setDisabled(self, disabled: bool) -> None:
        """Enable/disable the pill. When disabled, clicks are blocked and opacity reduces."""
        self._disabled = disabled
        if disabled:
            self.setCursor(Qt.ForbiddenCursor)
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.4)
            self.setGraphicsEffect(effect)
        else:
            self.setCursor(Qt.PointingHandCursor)
            self.setGraphicsEffect(None)

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
        self.setFixedHeight(22)
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
        # Toggle off if clicking the active one
        new_val = "" if self._value == key else key
        self.setValue(new_val)
        self.changed.emit(new_val)


# ============================================================================
# Title bar (frameless window chrome)
# ============================================================================

class TitleBar(QFrame):
    minimize = Signal()
    maximize_toggle = Signal()
    close_window = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(32)
        self._drag_pos: Optional[QPoint] = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        # App icon (monkey.ico) in title bar
        dot = QLabel()
        dot.setFixedSize(16, 16)
        _ico_path = get_resource_path("monkey.ico")
        if os.path.exists(_ico_path):
            _pix = QPixmap(_ico_path)
            if not _pix.isNull():
                dot.setPixmap(_pix.scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                dot.setStyleSheet(f"background: {C['accent']}; border-radius: 3px;")
        else:
            dot.setStyleSheet(f"background: {C['accent']}; border-radius: 3px;")
        layout.addWidget(dot)

        self._title = QLabel(f"Fishing Puzzle Player v{APP_VERSION}")
        self._title.setObjectName("TitleLabel")
        layout.addWidget(self._title)
        layout.addStretch(1)

        for name, ico_name, slot, obj_name in [
            ("min", "minus", self.minimize.emit, "TbBtn"),
            ("close", "x", self.close_window.emit, "TbClose"),
        ]:
            btn = QPushButton()
            btn.setIcon(make_icon(ico_name, 12, C["text_dim"]))
            btn.setIconSize(QSize(12, 12))
            btn.setObjectName(obj_name)
            btn.setProperty("class", obj_name)
            btn.setFixedSize(36, 32)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def setTitle(self, title: str) -> None:
        self._title.setText(title)

    # Drag-to-move
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
# Header (monkey GIFs + title + Discord/Status pills)
# ============================================================================

class Header(QFrame):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Header")
        self.setFixedHeight(80)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(16)

        self.gif_left = self._make_monkey_label()
        self.gif_right = self._make_monkey_label()
        layout.addWidget(self.gif_left)

        title_block = QFrame()
        tb = QVBoxLayout(title_block)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(6)
        tb.setAlignment(Qt.AlignCenter)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_row.setAlignment(Qt.AlignCenter)
        title = QLabel("Fishing Puzzle Player")
        title.setObjectName("HeaderTitle")
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("HeaderVersion")
        title_row.addWidget(title)
        title_row.addWidget(ver)
        title_row.addStretch(0)

        # Wrap title row in a centering layout
        tr_wrap = QHBoxLayout()
        tr_wrap.setContentsMargins(0, 0, 0, 0)
        tr_wrap.addStretch(1)
        for w in (title, ver):
            tr_wrap.addWidget(w)
        tr_wrap.addStretch(1)
        tr_wrap.setSpacing(6)
        tb.addLayout(tr_wrap)

        # Subtitle pills row
        subrow = QHBoxLayout()
        subrow.setContentsMargins(0, 0, 0, 0)
        subrow.setSpacing(8)
        subrow.setAlignment(Qt.AlignCenter)
        self._discord_pill = self._make_hl("DISCORD", "boristei", value_class="HlValue")
        self._input_pill, self._input_value = self._make_hl_pair("INPUT", "—")
        self._status_pill, self._status_value = self._make_hl_status("STATUS", "● IDLE")
        subrow.addStretch(1)
        subrow.addWidget(self._discord_pill)
        subrow.addWidget(self._input_pill)
        subrow.addWidget(self._status_pill)
        subrow.addStretch(1)
        tb.addLayout(subrow)

        layout.addWidget(title_block, 1)
        layout.addWidget(self.gif_right)

        self._load_monkey()

    def _make_monkey_label(self) -> QLabel:
        lbl = QLabel()
        lbl.setFixedSize(52, 52)
        lbl.setStyleSheet(
            f"background: #2a1808; border-radius: 8px; "
            f"border: 1px solid #1a0f06; color: {C['accent']}; "
            f"font-size: 28px;"
        )
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setText("🐒")
        return lbl

    def _load_monkey(self) -> None:
        path = get_resource_path("monkey-eating.gif")
        if not os.path.exists(path):
            return
        for lbl in (self.gif_left, self.gif_right):
            movie = QMovie(path)
            movie.setScaledSize(QSize(52, 52))
            lbl.setText("")
            lbl.setMovie(movie)
            movie.start()

    def _make_hl(self, label: str, value: str, value_class: str = "HlValue") -> QFrame:
        f = QFrame()
        f.setProperty("class", "HlPill")
        f.setFixedHeight(22)
        l = QHBoxLayout(f)
        l.setContentsMargins(10, 0, 10, 0)
        l.setSpacing(6)
        lbl = QLabel(label)
        lbl.setProperty("class", "HlLabel")
        val = QLabel(value)
        val.setProperty("class", value_class)
        l.addWidget(lbl)
        l.addWidget(val)
        return f

    def _make_hl_status(self, label: str, value: str) -> Tuple[QFrame, QLabel]:
        f = QFrame()
        f.setProperty("class", "HlPill")
        f.setFixedHeight(22)
        l = QHBoxLayout(f)
        l.setContentsMargins(10, 0, 10, 0)
        l.setSpacing(6)
        lbl = QLabel(label)
        lbl.setProperty("class", "HlLabel")
        val = QLabel(value)
        val.setProperty("class", "HlValueIdle")
        l.addWidget(lbl)
        l.addWidget(val)
        return f, val

    def _make_hl_pair(self, label: str, value: str) -> Tuple[QFrame, QLabel]:
        f = QFrame()
        f.setProperty("class", "HlPill")
        f.setFixedHeight(22)
        l = QHBoxLayout(f)
        l.setContentsMargins(10, 0, 10, 0)
        l.setSpacing(6)
        lbl = QLabel(label)
        lbl.setProperty("class", "HlLabel")
        val = QLabel(value)
        val.setProperty("class", "HlValue")
        l.addWidget(lbl)
        l.addWidget(val)
        return f, val

    def setInputBackend(self, display_name: str, tooltip: str = "") -> None:
        self._input_value.setText(display_name)
        if tooltip:
            self._input_pill.setToolTip(tooltip)
            self._input_value.setToolTip(tooltip)

    def setStatus(self, text: str, kind: str = "idle") -> None:
        self._status_value.setText(text)
        klass = {"idle": "HlValueIdle", "running": "HlValueRun"}.get(kind, "HlValueIdle")
        self._status_value.setProperty("class", klass)
        self._status_value.style().unpolish(self._status_value)
        self._status_value.style().polish(self._status_value)


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
        if items:
            self.setActive(items[0][0])

    def setActive(self, tab_id: str) -> None:
        for tid, btn in self._buttons.items():
            active = tid == tab_id
            btn.setProperty("active", "true" if active else "false")
            # Re-render icon with active color
            icon_color = C["accent"] if active else C["text_mute"]
            ico_name = self._icon_for(tid)
            if ico_name:
                btn.setIcon(make_icon(ico_name, 13, icon_color))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.changed.emit(tab_id)

    def _icon_for(self, tab_id: str) -> str:
        return {"dashboard": "dashboard", "inventory": "package", "settings": "settings"}.get(tab_id, "")


# ============================================================================
# Position capture controller (pynput)
# ============================================================================

class PositionCaptureSignals(QObject):
    captured = Signal(int, int, str)  # screen_x, screen_y, mode
    failed = Signal(str)


class PositionCaptureController(QObject):
    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.signals = PositionCaptureSignals()
        self._listener = None
        self._mode: Optional[str] = None

    def is_active(self) -> bool:
        return self._listener is not None

    def start(self, mode: str) -> bool:
        if pyn_mouse is None:
            self.signals.failed.emit("pynput not installed")
            return False
        self.cancel()
        self._mode = mode

        def on_click(x, y, button, pressed):
            if pressed and button == pyn_mouse.Button.left:
                self.signals.captured.emit(int(x), int(y), mode)
                return False
        self._listener = pyn_mouse.Listener(on_click=on_click)
        self._listener.start()
        return True

    def cancel(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        self._listener = None
        self._mode = None


# ============================================================================
# Dashboard tab
# ============================================================================

class DashboardTab(QWidget):
    start_clicked = Signal()
    stop_clicked = Signal()   # pause/resume toggle (primary button when running)
    hard_stop = Signal()      # full stop (red STOP ALL button)
    refresh_windows = Signal()
    reset_bait = Signal()
    window_changed = Signal(int, str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # KPI row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self.kpi_games = KPI("Total Games", "0", "across all windows")
        self.kpi_active = KPI("Active", "0/8", "windows running")
        self.kpi_bait = KPI("Total Bait", "0", "across selected")
        self.kpi_cap = KPI("Bait Cap", "0", "per client")
        for k in (self.kpi_games, self.kpi_active, self.kpi_bait, self.kpi_cap):
            kpi_row.addWidget(k, 1)
        layout.addLayout(kpi_row)

        # Game windows card
        windows_card = Card("Game Windows")
        refresh_btn = QPushButton("  Refresh")
        refresh_btn.setProperty("class", "BtnGhost")
        refresh_btn.setIcon(make_icon("refresh", 12, C["text"]))
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_windows.emit)
        reset_btn = QPushButton("  Reset Bait")
        reset_btn.setProperty("class", "BtnGhost")
        reset_btn.setIcon(make_icon("reset", 12, C["text"]))
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.clicked.connect(self.reset_bait.emit)
        windows_card.addAction(refresh_btn)
        windows_card.addAction(reset_btn)

        # Tighten spacing inside the windows card
        windows_card.body_layout.setSpacing(2)
        windows_card.body_layout.setContentsMargins(10, 8, 10, 8)
        self.window_rows: List[WindowRow] = []
        for i in range(MAX_WINDOWS):
            row = WindowRow(i)
            row.window_changed.connect(self.window_changed.emit)
            windows_card.body_layout.addWidget(row)
            self.window_rows.append(row)
        layout.addWidget(windows_card)

        # Quick actions card
        qa_card = Card("Quick Actions", dot=False)
        qa_row = QHBoxLayout()
        qa_row.setSpacing(8)

        self.start_btn = QPushButton("  START ALL")
        self.start_btn.setProperty("class", "BtnPrimary BtnLarge")
        self.start_btn.setIcon(make_icon("play", 14, "#1a1108"))
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setMinimumHeight(42)
        self.start_btn.setMaximumWidth(190)
        self.start_btn.clicked.connect(self._on_main)
        qa_row.addWidget(self.start_btn, 0)

        self.stop_btn = QPushButton("  STOP ALL")
        self.stop_btn.setProperty("class", "BtnDanger BtnLarge")
        self.stop_btn.setIcon(make_icon("stop", 14, C["red"]))
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setMinimumHeight(42)
        self.stop_btn.setMaximumWidth(170)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.hard_stop.emit)
        qa_row.addWidget(self.stop_btn, 0)

        hints = QFrame()
        hints.setStyleSheet(f"border-left: 1px solid {C['line']};")
        hl = QVBoxLayout(hints)
        hl.setContentsMargins(12, 4, 4, 4)
        hl.setSpacing(4)
        hl.addLayout(self._kbd_row("F5", "Pause / Resume"))
        hl.addLayout(self._kbd_row("Esc", "Cancel coord pick"))
        qa_row.addWidget(hints, 1)
        qa_card.body_layout.addLayout(qa_row)
        layout.addWidget(qa_card)

        self._running = False
        self._paused = False

    def _kbd_row(self, key: str, text: str) -> QHBoxLayout:
        l = QHBoxLayout()
        l.setSpacing(6)
        l.setContentsMargins(0, 0, 0, 0)
        kbd = QLabel(key)
        kbd.setProperty("class", "Kbd")
        kbd.setFixedHeight(20)
        kbd.setAlignment(Qt.AlignCenter)
        kbd.setStyleSheet(
            f"background: {C['bg_3']}; border: 1px solid {C['line_2']}; "
            f"border-radius: 3px; color: {C['text_dim']}; "
            f"font-family: {MONO}; font-size: 11px; padding: 2px 7px;"
        )
        txt = QLabel(text)
        txt.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 12px;")
        l.addWidget(kbd)
        l.addWidget(txt)
        l.addStretch(1)
        return l

    def _on_main(self) -> None:
        if not self._running:
            self.start_clicked.emit()
        else:
            # When running: button toggles pause/resume via stop_clicked signal
            # (FishbotWindow.toggle_pause_all is wired to F5; here we reuse it)
            self.stop_clicked.emit()

    def setRunning(self, running: bool) -> None:
        self._running = running
        self._paused = False
        self.stop_btn.setVisible(running)
        if running:
            self.start_btn.setText("  PAUSE")
            self.start_btn.setIcon(make_icon("pause", 14, "#1a1108"))
            self.start_btn.setProperty("class", "BtnPrimary BtnLarge")
        else:
            self.start_btn.setText("  START ALL")
            self.start_btn.setIcon(make_icon("play", 14, "#1a1108"))
            self.start_btn.setProperty("class", "BtnPrimary BtnLarge")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)

    def setPaused(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self.start_btn.setText("  RESUME")
            self.start_btn.setIcon(make_icon("play", 14, "#1a1108"))
            self.start_btn.setProperty("class", "BtnPrimary BtnLarge")
        else:
            self.start_btn.setText("  PAUSE")
            self.start_btn.setIcon(make_icon("pause", 14, "#1a1108"))
            self.start_btn.setProperty("class", "BtnPrimary BtnLarge")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)

    def setWindowList(self, windows: List[str]) -> None:
        for row in self.window_rows:
            row.setWindowList(windows)

    def setKpis(self, total_games: int, active: int, total_bait: int, bait_cap: int) -> None:
        self.kpi_games.setValue(total_games)
        self.kpi_active.setValue(f"{active}/{MAX_WINDOWS}")
        self.kpi_bait.setValue(f"{total_bait:,}")
        self.kpi_cap.setValue(f"{bait_cap:,}")


class WindowRow(QFrame):
    """One row in the Game Windows list — number + select + status pill + B/G stats."""

    window_changed = Signal(int, str)

    def __init__(self, index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "WinRow")
        self.setProperty("active", "false")
        self.setFixedHeight(28)
        self.index = index
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        self.num_label = QLabel(f"W{index + 1:02d}")
        self.num_label.setProperty("class", "WinNum")
        self.num_label.setFixedWidth(28)
        layout.addWidget(self.num_label)

        self.combo = QComboBox()
        self.combo.setFixedHeight(22)
        self.combo.setMinimumWidth(120)
        self.combo.setMaximumWidth(600)
        self.combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.combo.addItem("— select window —", "")
        self.combo.currentIndexChanged.connect(self._on_combo)
        layout.addWidget(self.combo, 0)

        self.pill = Pill("EMPTY", "idle")
        self.pill.setFixedWidth(76)
        layout.addWidget(self.pill, 0)

        self.bait_lbl = QLabel("B ---")
        self.bait_lbl.setStyleSheet(
            f"font-family: {MONO}; font-size: 10px; color: {C['accent']};"
        )
        self.bait_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.bait_lbl.setMinimumWidth(62)
        layout.addWidget(self.bait_lbl)

        self.games_lbl = QLabel("G 0")
        self.games_lbl.setStyleSheet(
            f"font-family: {MONO}; font-size: 10px; color: {C['accent']};"
        )
        self.games_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.games_lbl.setMinimumWidth(48)
        layout.addWidget(self.games_lbl)

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
        if active:
            self.num_label.setStyleSheet(f"color: {C['green']}; font-family: {MONO}; font-size: 10px;")
        else:
            self.num_label.setStyleSheet(f"color: {C['text_mute']}; font-family: {MONO}; font-size: 10px;")
        self.style().unpolish(self)
        self.style().polish(self)

    def setBait(self, val) -> None:
        self.bait_lbl.setText(f"B {val}")

    def setGames(self, val) -> None:
        self.games_lbl.setText(f"G {val}")

    def setComboEnabled(self, enabled: bool) -> None:
        self.combo.setEnabled(enabled)

    def _on_combo(self, _idx: int) -> None:
        self.window_changed.emit(self.index, self.selectedWindow())


# ============================================================================
# Inventory tab
# ============================================================================

class InventoryTab(QWidget):
    set_coord = Signal(str)  # mode key
    unset_coord = Signal(str)  # mode key - emitted when clicking an already-set pill
    open_fish_modal = Signal()
    auto_fish_toggled = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.coord_pills: Dict[str, CoordPill] = {}
        self._disabled = False

        # --- Inventory pages ---
        pages_card = Card("Inventory Page Coordinates")
        self.guide_btn = QPushButton("  Guide")
        self.guide_btn.setProperty("class", "BtnGhost")
        self.guide_btn.setIcon(make_icon("help", 12, C["text"]))
        self.guide_btn.setCursor(Qt.PointingHandCursor)
        self.guide_btn.clicked.connect(self._show_guide)
        pages_card.addAction(self.guide_btn)

        for row_idx, page_row in enumerate([(1, 2, 3, 4), (5, 6, 7, 8)]):
            row = QHBoxLayout()
            row.setSpacing(8)
            for n in page_row:
                key = f"page{n}"
                pill = CoordPill(f"Page {n}", optional=(n > 4))
                pill.clicked.connect(lambda k=key: self._on_pill_clicked(k))
                pill.unset_requested.connect(lambda k=key: self.unset_coord.emit(k))
                self.coord_pills[key] = pill
                row.addWidget(pill, 1)
            pages_card.body_layout.addLayout(row)
        layout.addWidget(pages_card)

        # --- Action coords ---
        action_card = Card("Action Coordinates")
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        for label, key, optional in [
            ("Drop / Sell", "drop", True),
            ("Confirm", "confirm", False),
            ("Armor Slot", "armor", True),
        ]:
            pill = CoordPill(label, optional=optional)
            pill.clicked.connect(lambda k=key: self._on_pill_clicked(k))
            pill.unset_requested.connect(lambda k=key: self.unset_coord.emit(k))
            self.coord_pills[key] = pill
            action_row.addWidget(pill, 1)
        action_card.body_layout.addLayout(action_row)
        layout.addWidget(action_card)

        # --- Auto fish handling ---
        auto_card = Card("Automatic Fish Handling")
        self.auto_check = QCheckBox("Enable")
        self.auto_check.toggled.connect(self.auto_fish_toggled.emit)
        auto_card.addAction(self.auto_check)
        body = QHBoxLayout()
        body.setSpacing(12)
        desc = QLabel()
        desc.setText(
            f"Pick which fish and items to <span style='color:{C['green']};'>keep</span>, "
            f"<span style='color:{C['red']};'>drop/sell</span>, or "
            f"<span style='color:{C['blue']};'>open</span> after each catch."
        )
        desc.setTextFormat(Qt.RichText)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 11px;")
        self.configure_fish_btn = QPushButton("  Configure Fish and Items")
        self.configure_fish_btn.setProperty("class", "Btn")
        self.configure_fish_btn.setIcon(make_icon("fish", 14, C["text"]))
        self.configure_fish_btn.setCursor(Qt.PointingHandCursor)
        self.configure_fish_btn.clicked.connect(self.open_fish_modal.emit)
        body.addWidget(desc, 1)
        body.addWidget(self.configure_fish_btn)
        auto_card.body_layout.addLayout(body)
        layout.addWidget(auto_card)
        layout.addStretch(1)

        self._auto_fish_enabled = False
        self._update_fish_btn_state()

    def _on_pill_clicked(self, key: str) -> None:
        """Handle pill click - only start capture if not disabled."""
        if not self._disabled:
            self.set_coord.emit(key)

    def _show_guide(self) -> None:
        QMessageBox.information(
            self, "Inventory pages",
            "Pages 1–4 are required for inventory rotation.\n\n"
            "Click each tile, then click the matching inventory tab in your "
            "game window. Pages 5–8 are optional (only set them if you actually "
            "use those inventory tabs)."
        )

    def setCoord(self, key: str, coords: Optional[Tuple[int, int]]) -> None:
        if key in self.coord_pills:
            self.coord_pills[key].setCoords(coords)

    def setCapturing(self, key: Optional[str]) -> None:
        for k, p in self.coord_pills.items():
            p.setCapturing(k == key)

    def setAutoFish(self, enabled: bool) -> None:
        self._auto_fish_enabled = enabled
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(enabled)
        self.auto_check.blockSignals(False)
        self._update_fish_btn_state()

    def _update_fish_btn_state(self) -> None:
        enabled = not self._disabled
        self.configure_fish_btn.setEnabled(enabled)
        self.configure_fish_btn.setCursor(
            Qt.PointingHandCursor if enabled else Qt.ForbiddenCursor
        )

    def setDisabled(self, disabled: bool) -> None:
        """Enable/disable all coord pills, auto-fish checkbox, and configure button."""
        self._disabled = disabled
        for pill in self.coord_pills.values():
            pill.setDisabled(disabled)
        self.auto_check.setEnabled(not disabled)
        self.guide_btn.setEnabled(not disabled)
        self.guide_btn.setCursor(Qt.ForbiddenCursor if disabled else Qt.PointingHandCursor)
        self._update_fish_btn_state()


# ============================================================================
# Settings tab
# ============================================================================

class SettingsTab(QWidget):
    open_timing = Signal()
    config_changed = Signal()
    accent_changed = Signal(str)

    BAIT_KEYS = ["1", "2", "3", "4", "F1", "F2", "F3", "F4"]
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
        self.classic_delay_input.editingFinished.connect(self._on_changed)
        crow.addWidget(self.classic_delay_input)
        crow.addWidget(self._mono_label("sec", color=C["text_mute"]))
        crow.addStretch(1)
        self.classic_delay_row.setVisible(False)
        left.addWidget(self.classic_delay_row)
        self.classic_check.toggled.connect(self.classic_delay_row.setVisible)

        self.human_check = QCheckBox("Human like behavior (anti-cheat bypass)")
        self.human_check.toggled.connect(self._on_changed)
        left.addWidget(self.human_check)
        self.sound_check = QCheckBox("Sound alert on no bait")
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
            f"font-family: {MONO}; font-size: 10px; color: {C['text_mute']}; "
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
        qty_lbl.setStyleSheet(f"color: {C['text']}; font-family: {MONO}; font-size: 11px;")
        qty_row.addWidget(qty_lbl)
        self.bait_qty_input = QLineEdit("200")
        self.bait_qty_input.setFixedWidth(70)
        self.bait_qty_input.setAlignment(Qt.AlignRight)
        self.bait_qty_input.setToolTip("Bait amount for all selected keys")
        self.bait_qty_input.editingFinished.connect(self._on_changed)
        qty_row.addWidget(self.bait_qty_input)
        qty_row.addStretch(1)
        right.addLayout(qty_row)
        
        right.addStretch(1)
        grid.addLayout(right, 1)
        bb.body_layout.addLayout(grid)
        layout.addWidget(bb)

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
        qs_desc = QLabel(
            "Skips the fishing animation screen by clicking the armor slot or pressing the horse "
            "hotkey between catches."
        )
        qs_desc.setWordWrap(True)
        qs_desc.setStyleSheet(
            f"color: {C['text_mute']}; font-family: {MONO}; font-size: 10px; "
            f"border-left: 1px solid {C['line']}; padding-left: 12px;"
        )
        qs_row.addWidget(qs_desc, 1)
        qs.body_layout.addLayout(qs_row)
        layout.addWidget(qs)

        # --- Profiles ---
        pr = Card("Profiles & Presets")
        pr_row = QHBoxLayout()
        pr_row.setSpacing(8)
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(["Default Profile"])
        pr_row.addWidget(self.profile_combo, 1)
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
            "Saved next to bot_config.json."
        )
        pr_hint.setStyleSheet(f"color: {C['text_mute']}; font-family: {MONO}; font-size: 10px;")
        pr.body_layout.addWidget(pr_hint)
        layout.addWidget(pr)

        # --- Theme ---
        th = Card("Theme · Accent Color")
        th_row = QHBoxLayout()
        th_row.setSpacing(6)
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
        self.accent_label.setStyleSheet(f"font-family: {MONO}; font-size: 10px; color: {C['text_mute']};")
        th_row.addWidget(self.accent_label)
        th.body_layout.addLayout(th_row)
        layout.addWidget(th)

        layout.addStretch(1)

        self._suspend_signals = False
        self._on_qs_enable(False)

    def _mono_label(self, text: str, color: Optional[str] = None) -> QLabel:
        l = QLabel(text)
        col = color or C["text_dim"]
        l.setStyleSheet(f"color: {col}; font-family: {MONO}; font-size: 11px;")
        return l

    def _on_changed(self, *_args) -> None:
        if self._suspend_signals:
            return
        self.config_changed.emit()

    def _on_qs_enable(self, checked: bool) -> None:
        self.qs_horse.setEnabled(checked)
        self.qs_armor.setEnabled(checked)
        self._on_changed()

    def _on_qs_mode(self, _checked: bool) -> None:
        if self._suspend_signals:
            return
        sender = self.sender()
        # Mutual exclusion (mimic the radio behavior of the design)
        if sender is self.qs_horse and self.qs_horse.isChecked():
            self.qs_armor.blockSignals(True); self.qs_armor.setChecked(False); self.qs_armor.blockSignals(False)
        elif sender is self.qs_armor and self.qs_armor.isChecked():
            self.qs_horse.blockSignals(True); self.qs_horse.setChecked(False); self.qs_horse.blockSignals(False)
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
        bk = set(cfg.get("bait_keys", ["1", "2", "3", "4"]))
        for k, cb in self.bait_checks.items():
            cb.setChecked(k in bk)
        self.bait_qty_input.setText(str(cfg.get("bait_quantity", 200)))
        self.qs_enable.setChecked(cfg.get("quick_skip", False))
        mode = cfg.get("quick_skip_mode", "horse")
        self.qs_horse.setChecked(mode == "horse")
        self.qs_armor.setChecked(mode == "armor")
        self._on_qs_enable(self.qs_enable.isChecked())
        accent = cfg.get("accent_color", C["accent"])
        # match against known swatches case-insensitively
        match = next((c for c in self.swatches if c.lower() == accent.lower()), None)
        if match:
            self._select_swatch(match)
        else:
            self.accent_label.setText(
                f"Currently: <span style='color:{C['accent']};'>{accent.upper()}</span>"
            )
            self.accent_label.setTextFormat(Qt.RichText)
        self._suspend_signals = False

    def writeToConfig(self, cfg: dict) -> None:
        cfg["classic_fishing"] = self.classic_check.isChecked()
        try:
            cfg["classic_fishing_delay"] = float(self.classic_delay_input.text())
        except ValueError:
            cfg["classic_fishing_delay"] = 3.0
        cfg["human_like_clicking"] = self.human_check.isChecked()
        cfg["sound_alert_on_finish"] = self.sound_check.isChecked()
        cfg["bait_keys"] = [k for k, cb in self.bait_checks.items() if cb.isChecked()]
        try:
            cfg["bait_quantity"] = int(self.bait_qty_input.text())
        except ValueError:
            cfg["bait_quantity"] = 200
        cfg["quick_skip"] = self.qs_enable.isChecked()
        cfg["quick_skip_mode"] = "armor" if self.qs_armor.isChecked() else "horse"

    def selectedBaitKeys(self) -> List[str]:
        return [k for k, cb in self.bait_checks.items() if cb.isChecked()]

    def setConfigEnabled(self, enabled: bool) -> None:
        for w in (self.classic_check, self.classic_delay_input, self.human_check,
                  self.sound_check, self.qs_enable, self.qs_horse, self.qs_armor):
            w.setEnabled(enabled if w not in (self.qs_horse, self.qs_armor) else (enabled and self.qs_enable.isChecked()))
        for cb in self.bait_checks.values():
            cb.setEnabled(enabled)
        # Track config enabled state
        self._config_enabled = enabled
        self._update_disabled_state()

    def setPaused(self, paused: bool) -> None:
        """Disable timing settings and bait quantity when paused."""
        self._paused = paused
        self._update_disabled_state()

    def _update_disabled_state(self) -> None:
        """Update disabled state based on config enabled and paused state."""
        disabled = self._paused or not self._config_enabled
        for w in (self.timing_btn, self.profile_load_btn, self.profile_save_btn):
            w.setDisabled(disabled)
            w.setCursor(Qt.ForbiddenCursor if disabled else Qt.PointingHandCursor)
        self.profile_combo.setEnabled(not disabled)
        self.bait_qty_input.setDisabled(disabled)
        self.bait_qty_input.setCursor(Qt.ForbiddenCursor if disabled else Qt.IBeamCursor)

    # Profile actions (placeholder — uses JSON files next to bot_config.json)
    def _profile_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load profile", os.getcwd(), "JSON (*.json)")
        if path:
            QMessageBox.information(self, "Profile", f"Profile loading: {os.path.basename(path)}\n(Reload the app after replacing bot_config.json.)")

    def _profile_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save profile", os.path.join(os.getcwd(), "profile.json"), "JSON (*.json)")
        if path:
            try:
                cfg_path = os.path.join(os.getcwd(), "bot_config.json")
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r") as src, open(path, "w") as dst:
                        dst.write(src.read())
                    QMessageBox.information(self, "Profile", f"Saved {os.path.basename(path)}")
            except Exception as e:
                QMessageBox.warning(self, "Profile", f"Save failed: {e}")

    def _profile_duplicate(self) -> None:
        QMessageBox.information(self, "Profile", "Duplicate is a placeholder in this build.")


# ============================================================================
# Modals
# ============================================================================

FISH_DISPLAY: List[Tuple[str, str, str]] = [
    # (display_name, asset_basename, emoji_fallback)
    ("Aal", "Aal_living.jpg", "🐟"),
    ("Ayu", "Ayu_living.jpg", "🐠"),
    ("Barsch", "Barsch_living.jpg", "🐟"),
    ("Brookforel", "Brookforell_living.jpg", "🐟"),
    ("Carp", "Carp_living.jpg", "🐠"),
    ("Catfish", "Catsfish_living.jpg", "🐟"),
    ("Crab", "Crab_living.jpg", "🦀"),
    ("Goldfish", "Goldfish_living.jpg", "🐠"),
    ("Grasscarp", "Grasscarp_living.jpg", "🐟"),
    ("Large sandfish", "Large_zander_living.jpg", "🐟"),
    ("Lotusfish", "Lotusfish_living.jpg", "🐟"),
    ("Mandarinfish", "Mandarinfish_living.jpg", "🐠"),
    ("Mirrorcarp", "Mirrorcarp_living.jpg", "🐟"),
    ("Rainbowfish", "Rainbowforell_living.jpg", "🌈"),
    ("Redfeather", "Redfeather_living.jpg", "🐟"),
    ("Riverforel", "Riverforell_living.jpg", "🐟"),
    ("Salmon", "Salmon_living.jpg", "🐟"),
    ("Shiri", "Shiri_living.jpg", "🐟"),
    ("Shrimp", "Shrimp_living.jpg", "🦐"),
    ("Skygazer", "Skygazer_living.jpg", "🐟"),
    ("Snake head", "Snake_head_living.jpg", "🐍"),
    ("Stint", "Stint_living.jpg", "🐟"),
    ("Tenchi", "Tenchi_living.jpg", "🐟"),
    ("Vai", "Vai_living.jpg", "🐟"),
    ("Yabby", "Yabby_living.jpg", "🦞"),
    ("Zander", "Zander_living.jpg", "🐟"),
    ("Zebra", "Zebra_living.jpg", "🐟"),
]

ITEM_DISPLAY: List[Tuple[str, str, str]] = [
    ("Black Dye", "Black_Dye_item.jpg", "🎨"),
    ("Bleach", "Bleach_item.jpg", "🧴"),
    ("Brown Dye", "Brown_Dye_item.jpg", "🎨"),
    ("Gold", "Gold_item.jpg", "💰"),
    ("Goldring", "Goldring_item.jpg", "💍"),
    ("Kelp Key", "Kelp_Key_item.jpg", "🗝️"),
    ("Lucys ring", "Lucys_ring_item.jpg", "💍"),
    ("Red Dye", "Red_Dye_item.jpg", "🎨"),
    ("Refugee coin", "Refugee_cape_item.jpg", "🪙"),
    ("Sage King…", "Sage_King_Glove_item.jpg", "👑"),
    ("Symbol wing", "Symbol_wise_emperors_item.jpg", "✨"),
    ("White Dye", "White_Dye_item.jpg", "🎨"),
    ("Yellow Dye", "Yellow_Dye_item.jpg", "🎨"),
]


def section_divider(text: str) -> QHBoxLayout:
    l = QHBoxLayout()
    l.setSpacing(10)
    label = QLabel(text.upper())
    label.setProperty("class", "SectionDivider")
    label.setStyleSheet(
        f"color: {C['accent']}; font-family: {MONO}; font-size: 10px; "
        f"font-weight: 700; letter-spacing: 2px;"
    )
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {C['line']};")
    l.addWidget(label)
    l.addWidget(line, 1)
    return l


class FishModal(QDialog):
    saved = Signal(dict)  # {asset_filename: 'keep'|'drop'|'open'}

    def __init__(self, current_actions: Dict[str, str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Fish & Item Selection")
        self.setModal(True)
        self.resize(720, 660)
        self.setMinimumSize(700, 600)
        self._actions = dict(current_actions)
        for _, asset, _ in FISH_DISPLAY + ITEM_DISPLAY:
            self._actions.setdefault(asset, "keep")
        self._tiles: Dict[str, FishTile] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QFrame()
        header.setProperty("class", "ModalHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 12, 12, 12)
        title = QLabel("FISH & ITEM SELECTION")
        title.setProperty("class", "ModalTitle")
        title.setStyleSheet(
            f"color: {C['accent']}; font-family: {MONO}; font-size: 13px; "
            f"font-weight: 700; letter-spacing: 2px;"
        )
        hl.addWidget(title)
        hl.addStretch(1)
        legend = QLabel(
            f"<span style='color:{C['green']}'>K</span> Keep "
            f"<span style='color:{C['text_mute']}'>·</span> "
            f"<span style='color:{C['red']}'>D</span> Drop "
            f"<span style='color:{C['text_mute']}'>·</span> "
            f"<span style='color:{C['blue']}'>O</span> Open"
        )
        legend.setStyleSheet(f"font-family: {MONO}; font-size: 10px; color: {C['text_mute']};")
        legend.setTextFormat(Qt.RichText)
        hl.addWidget(legend)
        outer.addWidget(header)

        # Body (scroll)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)

        bl.addLayout(section_divider("Fish"))
        fish_grid = QGridLayout()
        fish_grid.setSpacing(10)
        for i, (name, asset, emoji) in enumerate(FISH_DISPLAY):
            tile = FishTile(name, asset, emoji, self._actions[asset])
            tile.changed.connect(lambda v, a=asset: self._actions.update({a: v}))
            self._tiles[asset] = tile
            fish_grid.addWidget(tile, i // 5, i % 5)
        bl.addLayout(fish_grid)

        bl.addLayout(section_divider("Items"))
        items_grid = QGridLayout()
        items_grid.setSpacing(10)
        for i, (name, asset, emoji) in enumerate(ITEM_DISPLAY):
            tile = FishTile(name, asset, emoji, self._actions[asset], kd_only=True)
            tile.changed.connect(lambda v, a=asset: self._actions.update({a: v}))
            self._tiles[asset] = tile
            items_grid.addWidget(tile, i // 5, i % 5)
        bl.addLayout(items_grid)
        bl.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # Footer
        footer = QFrame()
        footer.setProperty("class", "ModalFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(6)
        for label, slot in [
            ("Keep All",  lambda: self._set_all([a for _, a, _ in FISH_DISPLAY] + [a for _, a, _ in ITEM_DISPLAY], "keep")),
            ("Drop All",  lambda: self._set_all([a for _, a, _ in FISH_DISPLAY] + [a for _, a, _ in ITEM_DISPLAY], "drop")),
            ("Open All",  self._open_all),
        ]:
            b = QPushButton(label)
            b.setProperty("class", "BtnGhost")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            fl.addWidget(b)
        fl.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "Btn")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("  Save")
        save_btn.setProperty("class", "BtnPrimary")
        save_btn.setIcon(make_icon("save", 12, "#1a1108"))
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        fl.addWidget(cancel_btn)
        fl.addWidget(save_btn)
        outer.addWidget(footer)

    def _set_all(self, assets: List[str], action: str) -> None:
        for a in assets:
            if a in self._tiles:
                self._tiles[a].setAction(action)
                self._actions[a] = action

    def _open_all(self) -> None:
        self._set_all([a for _, a, _ in FISH_DISPLAY], "open")
        self._set_all([a for _, a, _ in ITEM_DISPLAY], "drop")

    def _save(self) -> None:
        self.saved.emit(dict(self._actions))
        self.accept()


class FishTile(QFrame):
    changed = Signal(str)

    def __init__(self, name: str, asset: str, emoji: str, action: str = "",
                 kd_only: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "FishTile")
        self._asset = asset
        self._kd_only = kd_only
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignHCenter)

        self.img = QLabel()
        self.img.setProperty("class", "FishImg")
        self.img.setFixedSize(48, 48)
        self.img.setAlignment(Qt.AlignCenter)
        self._load_img(asset, emoji)
        layout.addWidget(self.img, alignment=Qt.AlignHCenter)

        nm = QLabel(name)
        nm.setProperty("class", "FishName")
        nm.setStyleSheet(f"color: {C['text']}; font-family: {MONO}; font-size: 12px;")
        nm.setAlignment(Qt.AlignCenter)
        nm.setMaximumWidth(110)
        nm.setWordWrap(True)
        layout.addWidget(nm)

        seg_opts = [("keep", "K"), ("drop", "D")] if kd_only else [("keep", "K"), ("drop", "D"), ("open", "O")]
        self.seg = Segmented(seg_opts, action if not (kd_only and action == "open") else "")
        self.seg.changed.connect(self._on_seg)
        layout.addWidget(self.seg)
        self.setAction(action if not (kd_only and action == "open") else "")

    def _load_img(self, asset: str, emoji: str) -> None:
        path = get_resource_path(asset)
        if os.path.exists(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.img.setPixmap(pix.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        self.img.setText(emoji)
        self.img.setStyleSheet(self.img.styleSheet() + " font-size: 22px;")

    def _on_seg(self, action: str) -> None:
        self.setAction(action)
        self.changed.emit(action)

    def setAction(self, action: str) -> None:
        cls = "FishImg"
        if action == "keep": cls += " FishImgKeep"
        elif action == "drop": cls += " FishImgDrop"
        elif action == "open": cls += " FishImgOpen"
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
            ("Min delay between attempts", "timing_human_min", 150,  50,  800),
            ("Max delay between attempts", "timing_human_max", 400, 100, 1200),
        ]),
        ("Key Presses", [
            ("Key hold duration",     "timing_key_hold",   25, 10, 100),
            ("Pre-key window settle", "timing_key_settle", 30, 10,  60),
        ]),
        ("Bait & Cast", [
            ("Bait → Cast key delay", "timing_cast_interkey", 50, 20, 200),
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
            ("Gap between CTRL+G presses", "timing_quickskip_between", 100, 50, 600),
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
        title.setStyleSheet(
            f"color: {C['accent']}; font-family: {MONO}; font-size: 13px; "
            f"font-weight: 700; letter-spacing: 2px;"
        )
        hl.addWidget(title)
        hl.addStretch(1)
        close_btn = QPushButton()
        close_btn.setIcon(make_icon("x", 12, C["text_dim"]))
        close_btn.setProperty("class", "BtnGhost")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        hl.addWidget(close_btn)
        outer.addWidget(header)

        # body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)
        intro = QLabel("Only timings that directly produce OS inputs are exposed here.")
        intro.setStyleSheet(f"color: {C['text_mute']}; font-family: {MONO}; font-size: 10px;")
        bl.addWidget(intro)
        for group_name, items in self.GROUPS:
            bl.addLayout(section_divider(group_name))
            for label, key, default_ms, mn, mx in items:
                value_ms = int(round(current.get(key, default_ms / 1000.0) * 1000))
                value_ms = max(mn, min(mx, value_ms))
                row = QHBoxLayout()
                row.setSpacing(12)
                lbl = QLabel(label)
                lbl.setStyleSheet(f"color: {C['text']}; font-family: {MONO}; font-size: 11px;")
                lbl.setMinimumWidth(220)
                row.addWidget(lbl, 1)
                slider = QSlider(Qt.Horizontal)
                slider.setMinimum(mn)
                slider.setMaximum(mx)
                slider.setValue(value_ms)
                slider.setFixedHeight(20)
                slider.setMinimumWidth(180)
                row.addWidget(slider, 1)
                val = QLabel(f"{value_ms}ms")
                val.setStyleSheet(f"color: {C['accent']}; font-family: {MONO}; font-size: 11px;")
                val.setFixedWidth(60)
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
        save.setIcon(make_icon("save", 12, "#1a1108"))
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._save)
        fl.addWidget(cancel)
        fl.addWidget(save)
        outer.addWidget(footer)

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


class WizardModal(QDialog):
    finished_setup = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("First-Run Setup")
        self.setModal(True)
        self.resize(560, 480)
        self._step = 0
        self._steps = ["Welcome", "Pick Windows", "Set Coordinates", "Bait & Behavior", "Review"]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setProperty("class", "ModalHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 12, 12, 12)
        title = QLabel("FIRST-RUN SETUP")
        title.setStyleSheet(
            f"color: {C['accent']}; font-family: {MONO}; font-size: 13px; "
            f"font-weight: 700; letter-spacing: 2px;"
        )
        hl.addWidget(title)
        hl.addStretch(1)
        close_btn = QPushButton()
        close_btn.setIcon(make_icon("x", 12, C["text_dim"]))
        close_btn.setProperty("class", "BtnGhost")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        hl.addWidget(close_btn)
        outer.addWidget(header)

        # Step chips
        chips = QFrame()
        chips.setStyleSheet(f"border-bottom: 1px solid {C['line']};")
        cl = QHBoxLayout(chips)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.setSpacing(6)
        self._chips: List[Tuple[QLabel, QLabel]] = []
        for i, s in enumerate(self._steps):
            num = QLabel(str(i + 1))
            num.setProperty("class", "WizNum")
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignCenter)
            txt = QLabel(s.upper())
            txt.setProperty("class", "WizStep")
            cl.addWidget(num)
            cl.addWidget(txt)
            if i < len(self._steps) - 1:
                line = QFrame()
                line.setFixedSize(24, 1)
                line.setStyleSheet(f"background: {C['line_2']};")
                cl.addWidget(line)
            self._chips.append((num, txt))
        cl.addStretch(1)
        outer.addWidget(chips)

        # Body (stacked)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self._build_windows())
        self.stack.addWidget(self._build_coords())
        self.stack.addWidget(self._build_behavior())
        self.stack.addWidget(self._build_review())
        outer.addWidget(self.stack, 1)

        # Footer
        footer = QFrame()
        footer.setProperty("class", "ModalFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 12, 16, 12)
        skip = QPushButton("Skip"); skip.setProperty("class", "BtnGhost")
        skip.setCursor(Qt.PointingHandCursor)
        skip.clicked.connect(self.reject)
        fl.addWidget(skip)
        fl.addStretch(1)
        self.back_btn = QPushButton("Back"); self.back_btn.setProperty("class", "Btn")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(self._back)
        self.next_btn = QPushButton("Next  ")
        self.next_btn.setProperty("class", "BtnPrimary")
        self.next_btn.setIcon(make_icon("chevron-right", 12, "#1a1108"))
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.clicked.connect(self._next)
        fl.addWidget(self.back_btn)
        fl.addWidget(self.next_btn)
        outer.addWidget(footer)

        self._refresh()

    def _refresh(self) -> None:
        self.stack.setCurrentIndex(self._step)
        for i, (num, txt) in enumerate(self._chips):
            state = "done" if i < self._step else "active" if i == self._step else ""
            num.setProperty("state", state)
            txt.setProperty("state", state)
            num.setText("✓" if state == "done" else str(i + 1))
            num.style().unpolish(num); num.style().polish(num)
            txt.style().unpolish(txt); txt.style().polish(txt)
        self.back_btn.setVisible(self._step > 0)
        is_last = self._step == len(self._steps) - 1
        self.next_btn.setText("Finish  " if is_last else "Next  ")

    def _next(self) -> None:
        if self._step >= len(self._steps) - 1:
            self.finished_setup.emit()
            self.accept()
            return
        self._step += 1
        self._refresh()

    def _back(self) -> None:
        if self._step > 0:
            self._step -= 1
            self._refresh()

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setAlignment(Qt.AlignCenter)
        l.setContentsMargins(20, 20, 20, 20)
        l.setSpacing(12)
        emoji = QLabel("🐒")
        emoji.setStyleSheet("font-size: 48px;")
        emoji.setAlignment(Qt.AlignCenter)
        l.addWidget(emoji)
        title = QLabel("Welcome to Fishing Puzzle Player")
        title.setStyleSheet(f"font-family: {MONO}; font-size: 18px; color: {C['accent']};")
        title.setAlignment(Qt.AlignCenter)
        l.addWidget(title)
        body = QLabel(
            "We'll walk you through the basics: picking your game windows, "
            "setting click coordinates, and choosing how you want fish handled. "
            "Takes about a minute."
        )
        body.setWordWrap(True)
        body.setMaximumWidth(380)
        body.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 11px;")
        body.setAlignment(Qt.AlignCenter)
        l.addWidget(body)
        return w

    def _build_windows(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(8)
        l.addWidget(self._mono_label("Detected windows", color=C["text"]))
        if WindowManager:
            try:
                names = [n for n, _ in WindowManager.get_all_windows()][:6]
            except Exception:
                names = []
        else:
            names = []
        if not names:
            empty = QLabel("No windows detected. Open your game and click Refresh on the Dashboard tab.")
            empty.setStyleSheet(f"color: {C['text_mute']}; font-family: {MONO}; font-size: 11px;")
            l.addWidget(empty)
        for n in names:
            row = QFrame()
            row.setStyleSheet(
                f"background: {C['bg_2']}; border: 1px solid {C['line']}; "
                f"border-radius: 6px;"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 8, 10, 8)
            rl.setSpacing(10)
            rl.addWidget(icon_label("monitor", 14, C["text_dim"]))
            lbl = QLabel(n)
            lbl.setStyleSheet(f"font-family: {MONO}; font-size: 11px; color: {C['text']};")
            rl.addWidget(lbl, 1)
            rl.addWidget(QCheckBox())
            l.addWidget(row)
        l.addStretch(1)
        return w

    def _build_coords(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(8)
        intro = QLabel(
            "Click each tile, then click the matching point in your game. "
            "Pages 1-4 are required."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 11px;")
        l.addWidget(intro)
        for label in ["Page 1", "Page 2", "Page 3", "Page 4", "Drop / Sell", "Confirm"]:
            l.addWidget(CoordPill(label))
        l.addStretch(1)
        return w

    def _build_behavior(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(12)
        h = QCheckBox("Human-like clicking (recommended)"); h.setChecked(True); l.addWidget(h)
        s = QCheckBox("Sound alert when out of bait"); s.setChecked(True); l.addWidget(s)
        l.addWidget(self._mono_label("BAIT KEYS", color=C["text_mute"]))
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, k in enumerate(["1", "2", "3", "4"]):
            cb = QCheckBox(k); cb.setChecked(True)
            grid.addWidget(cb, i // 4, i % 4)
        l.addLayout(grid)
        l.addStretch(1)
        return w

    def _build_review(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setAlignment(Qt.AlignCenter)
        l.setContentsMargins(20, 20, 20, 20)
        l.setSpacing(8)
        emoji = QLabel("🎣"); emoji.setStyleSheet("font-size: 48px;"); emoji.setAlignment(Qt.AlignCenter)
        l.addWidget(emoji)
        ttl = QLabel("You're all set!")
        ttl.setStyleSheet(f"font-family: {MONO}; font-size: 16px; color: {C['accent']};")
        ttl.setAlignment(Qt.AlignCenter)
        l.addWidget(ttl)
        body = QLabel(
            "Hit <b>Start All</b> on the dashboard. Use <b>F5</b> to pause anytime."
        )
        body.setTextFormat(Qt.RichText)
        body.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 11px;")
        body.setAlignment(Qt.AlignCenter)
        body.setMaximumWidth(380)
        l.addWidget(body)
        jigsaw_btn = QPushButton("  Open Jigsaw Solver")
        jigsaw_btn.setProperty("class", "Btn")
        jigsaw_btn.setIcon(make_icon("grid", 12, C["text"]))
        jigsaw_btn.setCursor(Qt.PointingHandCursor)
        jigsaw_btn.clicked.connect(lambda: self.parent().open_jigsaw_solver() if self.parent() else None)
        l.addWidget(jigsaw_btn)
        return w

    def _mono_label(self, text: str, color: Optional[str] = None) -> QLabel:
        col = color or C["text_dim"]
        l = QLabel(text)
        l.setStyleSheet(f"font-family: {MONO}; font-size: 11px; color: {col};")
        return l


class SolverCacheManager(QObject):
    """
    App-wide manager for the jigsaw solver_cache.npz file.

    The cache (~150 MB) is required before any JigsawBot can start. We do NOT
    auto-build at app launch; the build is only triggered when the user opens
    the Jigsaw Solver dialog AND no cache exists. Build runs on a daemon
    thread; consumers subscribe to `state_changed` for UI updates.
    """

    # state ∈ {"missing", "building", "ready", "failed"}; second arg = message
    state_changed = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._error: str = ""
        self._state: str = "ready" if self._cache_exists() else "missing"

    @staticmethod
    def cache_path() -> str:
        """Resolve the same path used by Deterministic.load_cache()."""
        if getattr(sys, "frozen", False):
            root = os.path.dirname(sys.executable)
        else:
            root = get_project_root()
        cache_file = "solver_cache.npz"
        if os.environ.get("JIGSAW_SOLVER_PRECISION", "64").strip() == "64":
            cache_file = "solver_cache_f64.npz"
        return os.path.join(root, "data", "cache", "jigsaw", cache_file)

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

    def is_building(self) -> bool:
        return self._state == "building"

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
            cache_dir = os.path.dirname(self.cache_path())
            os.makedirs(cache_dir, exist_ok=True)
            os.environ["JIGSAW_SOLVER_CACHE_DIR"] = cache_dir
            # Importing deterministic also imports numba / numpy. The first
            # call to get_solver() runs load_cache() if a file is present, or
            # else _run_numba() to compute the table from scratch.
            from jigsaw_solver.deterministic import get_solver  # noqa: WPS433
            solver = get_solver()
            if solver is None:
                raise RuntimeError("get_solver() returned None")
        except Exception as exc:  # noqa: BLE001
            self._state = "failed"
            self._error = str(exc)
            self.state_changed.emit("failed", f"Cache build failed: {exc}")
            return
        self._state = "ready"
        self.state_changed.emit("ready", "Solver cache ready.")


class JigsawGridOverlay(QWidget):
    """
    Full-screen overlay placed over the game window for aligning the jigsaw
    puzzle grid.  Uses setWindowOpacity (same effect as Tk -alpha) which is
    reliable on every Windows DWM configuration, unlike WA_TranslucentBackground.
    """
    confirmed = Signal(list)  # [x, y, w, h] relative to game window

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
        self.setGeometry(win_left, win_top, win_w, win_h)
        self._win_w = win_w
        self._win_h = win_h
        self._grid = [float(v) for v in grid]
        self._target_name = target_name
        self._drag_mode: Optional[str] = None
        self._drag_x = 0
        self._drag_y = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def _clamp(self) -> None:
        self._grid[2] = max(60.0, min(self._grid[2], float(self._win_w)))
        self._grid[3] = max(40.0, min(self._grid[3], float(self._win_h)))
        self._grid[0] = max(0.0, min(self._grid[0], self._win_w - self._grid[2]))
        self._grid[1] = max(0.0, min(self._grid[1], self._win_h - self._grid[3]))

    def paintEvent(self, _ev) -> None:
        from PySide6.QtGui import QPen
        p = QPainter(self)
        gx, gy, gw, gh = [int(v) for v in self._grid]

        # Dark overlay (window opacity handles the transparency vs background)
        p.fillRect(0, 0, self._win_w, self._win_h, QColor(0, 0, 0))

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
        x, y = ev.x(), ev.y()
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
        dx = ev.x() - self._drag_x
        dy = ev.y() - self._drag_y
        self._drag_x, self._drag_y = ev.x(), ev.y()
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
            self.confirmed.emit([int(v) for v in self._grid])
            self.close()
        elif ev.key() == Qt.Key_Escape:
            self.close()


class JigsawWindowRow(QFrame):
    """Read-only row mirroring the dashboard's WindowRow — number + window name + crates."""

    def __init__(self, index: int, window_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "WinRow")
        self.setProperty("active", "false")
        self.setFixedHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        self.num_label = QLabel(f"W{index + 1:02d}")
        self.num_label.setProperty("class", "WinNum")
        self.num_label.setFixedWidth(28)
        self.num_label.setStyleSheet(
            f"color: {C['text_mute']}; font-family: {MONO}; font-size: 10px;"
        )
        layout.addWidget(self.num_label)

        self.name_label = QLabel(window_name)
        self.name_label.setStyleSheet(
            f"font-family: {MONO}; font-size: 11px; color: {C['text']};"
        )
        self.name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.name_label, 1)

        self.crates_lbl = QLabel("Opened Crates: 0")
        self.crates_lbl.setStyleSheet(
            f"font-family: {MONO}; font-size: 10px; color: {C['accent']};"
        )
        self.crates_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.crates_lbl.setMinimumWidth(170)
        self.crates_lbl.setContentsMargins(0, 0, 8, 0)
        self.crates_lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout.addWidget(self.crates_lbl, 0)

    def setCrates(self, count: int) -> None:
        self.crates_lbl.setText(f"Opened Crates: {count}")

    def setActive(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        if active:
            self.num_label.setStyleSheet(
                f"color: {C['green']}; font-family: {MONO}; font-size: 10px;"
            )
        else:
            self.num_label.setStyleSheet(
                f"color: {C['text_mute']}; font-family: {MONO}; font-size: 10px;"
            )
        self.style().unpolish(self)
        self.style().polish(self)


class JigsawSolverDialog(QDialog):
    status_line = Signal(int, str)
    progress_line = Signal(int, int)
    stopped = Signal(int)
    paused_changed = Signal(int, bool)

    def __init__(self, parent: "FishbotWindow"):
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Fishing Jigsaw Solver")
        self.setMinimumWidth(620)
        self.resize(720, 360)
        self._bots: Dict[int, object] = {}
        self._threads: Dict[int, threading.Thread] = {}
        self._rows: Dict[int, Dict[str, QLabel]] = {}
        self._debug_windows: Dict[int, object] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setProperty("class", "ModalHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 12, 12, 12)
        title = QLabel("FISHING JIGSAW SOLVER")
        title.setStyleSheet(
            f"color: {C['accent']}; font-family: {MONO}; font-size: 13px; "
            f"font-weight: 700; letter-spacing: 2px;"
        )
        hl.addWidget(title)
        hl.addStretch(1)
        outer.addWidget(header)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 14, 16, 14)
        bl.setSpacing(10)

        # ---- Cache status indicator (red/yellow/green dot + text) ----------
        cache_row = QFrame()
        cache_row.setStyleSheet(
            f"background: {C['bg_1']}; border: 1px solid {C['line']}; border-radius: 6px;"
        )
        crl = QHBoxLayout(cache_row)
        crl.setContentsMargins(10, 8, 10, 8)
        crl.setSpacing(10)
        self.cache_dot = QLabel("●")
        self.cache_dot.setFixedWidth(14)
        self.cache_dot.setStyleSheet(f"color: {C['red']}; font-size: 18px;")
        crl.addWidget(self.cache_dot)
        self.cache_status_label = QLabel("Checking solver cache...")
        self.cache_status_label.setWordWrap(True)
        self.cache_status_label.setStyleSheet(
            f"color: {C['text_dim']}; font-family: {MONO}; font-size: 11px;"
        )
        crl.addWidget(self.cache_status_label, 1)
        bl.addWidget(cache_row)

        self.summary_label = QLabel("Idle")
        self.summary_label.setStyleSheet(f"color: {C['text_dim']}; font-family: {MONO}; font-size: 11px;")
        bl.addWidget(self.summary_label)

        windows_card = Card("Game Windows (read-only)")
        windows_card.body_layout.setSpacing(2)
        windows_card.body_layout.setContentsMargins(10, 8, 10, 8)
        windows_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._rows_card = windows_card
        self._rows_layout = windows_card.body_layout
        bl.addWidget(windows_card, 0)

        # Grid bounds + confirm coords info row (placed below Game Windows)
        info_row = QHBoxLayout()
        info_row.setSpacing(20)
        self.grid_info_label = QLabel("Grid: not defined")
        self.grid_info_label.setStyleSheet(
            f"color: {C['text_mute']}; font-family: {MONO}; font-size: 12px; font-weight: 600;"
        )
        info_row.addWidget(self.grid_info_label)
        self.confirm_info_label = QLabel("Confirm: not set")
        self.confirm_info_label.setStyleSheet(
            f"color: {C['red']}; font-family: {MONO}; font-size: 12px; font-weight: 600;"
        )
        info_row.addWidget(self.confirm_info_label)
        info_row.addStretch(1)
        bl.addLayout(info_row)
        self._refresh_config_labels()

        bl.addStretch(1)

        self.log = None  # log widget removed; per-row status + global status log are sufficient

        buttons = QHBoxLayout()
        self.define_grid_btn = QPushButton("  Define Grid")
        self.define_grid_btn.setProperty("class", "Btn")
        self.define_grid_btn.setIcon(make_icon("grid", 12, C["text"]))
        self.define_grid_btn.setCursor(Qt.PointingHandCursor)
        self.define_grid_btn.clicked.connect(self._on_define_grid)
        buttons.addWidget(self.define_grid_btn)
        self.set_confirm_btn = QPushButton("  Set Confirm Coords")
        self.set_confirm_btn.setProperty("class", "Btn")
        self.set_confirm_btn.setIcon(make_icon("target", 12, C["text"]))
        self.set_confirm_btn.setCursor(Qt.PointingHandCursor)
        self.set_confirm_btn.clicked.connect(self._on_set_confirm)
        buttons.addWidget(self.set_confirm_btn)
        buttons.addStretch(1)
        self.start_btn = QPushButton("  Start Jigsaw")
        self.start_btn.setProperty("class", "BtnPrimary")
        self.start_btn.setIcon(make_icon("play", 12, "#1a1108"))
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self._toggle)
        buttons.addWidget(self.start_btn)
        close_btn = QPushButton("Close")
        close_btn.setProperty("class", "Btn")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        bl.addLayout(buttons)

        outer.addWidget(body, 1)
        self.status_line.connect(self._append_status)
        self.progress_line.connect(self._set_progress)
        self.stopped.connect(self._on_stopped)
        self.paused_changed.connect(self._on_paused_changed)
        self._rebuild_rows()

        # ---- Cache manager wiring ------------------------------------------
        self._cache_mgr = self.parent_window.solver_cache_manager
        self._cache_mgr.state_changed.connect(self._on_cache_state)
        # Initialize UI from current state, then trigger a build if needed.
        # `ensure_build()` no-ops when already ready or already building.
        self._on_cache_state(self._cache_mgr.state(),
                             self._initial_cache_message(self._cache_mgr.state()))
        self._cache_mgr.ensure_build()

    @staticmethod
    def _initial_cache_message(state: str) -> str:
        if state == "ready":
            return "Solver cache ready."
        if state == "building":
            return "Building solver cache. This may take 1–5 minutes — please wait..."
        if state == "failed":
            return "Cache build failed previously. Reopen the dialog to retry."
        return "Solver cache missing — preparing to build (1–5 minutes)."

    def _on_cache_state(self, state: str, message: str) -> None:
        """Update status dot/label and gate the Start button on cache state."""
        color_map = {
            "ready":    C["green"],
            "building": C["accent"],   # amber while working
            "failed":   C["red"],
            "missing":  C["red"],
        }
        color = color_map.get(state, C["red"])
        self.cache_dot.setStyleSheet(f"color: {color}; font-size: 18px;")
        self.cache_status_label.setText(message)
        ready = (state == "ready")
        # Only allow Start when cache is ready AND we're not already running.
        if hasattr(self, "start_btn"):
            if self.is_running():
                self.start_btn.setEnabled(True)
            else:
                self.start_btn.setEnabled(ready)
                self.start_btn.setCursor(
                    Qt.PointingHandCursor if ready else Qt.ForbiddenCursor
                )

    def _mono_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"font-family: {MONO}; font-size: 11px; color: {C['text_dim']};")
        return label

    def _toggle(self) -> None:
        if self.is_running():
            if self._bots and any(getattr(b, "paused", False) for b in self._bots.values()):
                for b in self._bots.values():
                    b.paused = False
                self._on_paused_changed(-1, False)
                return
            self._stop()
            return
        self._start()

    def is_running(self) -> bool:
        return any(getattr(bot, "running", False) for bot in self._bots.values())

    def _start(self) -> None:
        if not self._cache_mgr.is_ready():
            state = self._cache_mgr.state()
            if state == "building":
                QMessageBox.information(
                    self, "Solver cache building",
                    "The solver cache is still being generated. "
                    "Please wait until the indicator turns green."
                )
            else:
                QMessageBox.warning(
                    self, "Solver cache not ready",
                    "The solver cache is not available. "
                    f"Status: {state}. Close and reopen this dialog to retry the build."
                )
            return
        if self.parent_window.bots:
            QMessageBox.warning(self, "Fishbot running", "Stop Fishbot before starting the jigsaw solver.")
            return
        selected_names = self.parent_window.selected_window_names()
        if not selected_names:
            QMessageBox.warning(self, "No window selected", "Select a game window first.")
            return
        if not self.parent_window.config.get("confirm_button_pos"):
            QMessageBox.warning(
                self,
                "Confirm coordinate required",
                "Use 'Set Confirm Coords' below or go to Inventory > Action Coordinates to set it first."
            )
            return
        bounds = self.parent_window.config.get("jigsaw_grid_bounds")
        valid_bounds = (
            bounds and len(bounds) == 4
            and all(isinstance(v, (int, float)) for v in bounds)
            and int(bounds[2]) > 0 and int(bounds[3]) > 0
        )
        if not valid_bounds:
            QMessageBox.warning(
                self,
                "Jigsaw grid required",
                "Use 'Define Grid' to align the ghost grid over the empty puzzle grid before starting."
            )
            return
        all_windows = {n: w for n, w in WindowManager.get_all_windows()}
        missing = [name for name in selected_names if name not in all_windows]
        if missing:
            QMessageBox.warning(self, "Window not found", "Refresh/select the game windows on the Dashboard first.")
            return

        try:
            from jigsaw_bot import JigsawBot
        except Exception as e:
            QMessageBox.critical(self, "Jigsaw unavailable", f"Could not load jigsaw solver:\n{e}")
            return

        self._rebuild_rows()
        self._bots.clear()
        self._threads.clear()
        for bot_id, selected_name in enumerate(selected_names):
            wm = WindowManager()
            wm.selected_window = all_windows[selected_name]
            cfg = dict(self.parent_window.config)
            cfg["jigsaw_solver_enabled"] = True
            bot = JigsawBot(
                wm,
                cfg,
                bot_id=bot_id,
                on_status_update=lambda msg, i=bot_id: self.status_line.emit(i, msg),
                on_progress_update=lambda _worker_id, count, i=bot_id: self.progress_line.emit(i, count),
                on_bot_stop=lambda _worker_id, i=bot_id: self.stopped.emit(i),
                on_pause_change=lambda _worker_id, paused, i=bot_id: self.paused_changed.emit(i, paused),
            )
            thread = threading.Thread(target=bot.start, daemon=True, name=f"JigsawBot-{bot_id + 1}")
            self._bots[bot_id] = bot
            self._threads[bot_id] = thread
            if DEBUG_MODE_EN and JigsawSolverDebugWindow is not None:
                root = self.parent_window.debug_tk_root()
                if root is not None:
                    self._debug_windows[bot_id] = JigsawSolverDebugWindow(root, bot)
                    self._debug_windows[bot_id].show()
            thread.start()
            row = self._rows.get(bot_id)
            if row:
                row["row"].setActive(True)

        self.start_btn.setText("  Stop Jigsaw (F5)")
        self.start_btn.setIcon(make_icon("stop", 12, "#1a1108"))
        self.define_grid_btn.setEnabled(False)
        self.set_confirm_btn.setEnabled(False)
        self.summary_label.setText(f"Running {len(self._bots)} jigsaw worker(s)")
        self._append_log(f"Started jigsaw solver on {len(self._bots)} window(s)")

    def _stop(self) -> None:
        for bot in self._bots.values():
            bot.stop()
        self._append_log("Stopping jigsaw solver workers...")

    def _on_stopped(self, bot_id: int) -> None:
        row = self._rows.get(bot_id)
        if row:
            row["row"].setActive(False)
        self._bots.pop(bot_id, None)
        self._threads.pop(bot_id, None)
        window = self._debug_windows.pop(bot_id, None)
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        if self._bots:
            self.summary_label.setText(f"Running {len(self._bots)} jigsaw worker(s)")
            return
        self.start_btn.setText("  Start Jigsaw")
        self.start_btn.setIcon(make_icon("play", 12, "#1a1108"))
        self.define_grid_btn.setEnabled(True)
        self.set_confirm_btn.setEnabled(True)
        self.summary_label.setText("Stopped")

    def _on_paused_changed(self, bot_id: int, paused: bool) -> None:
        if not self._bots:
            return
        any_paused = any(getattr(b, "paused", False) for b in self._bots.values())
        if any_paused:
            self.start_btn.setText("  Resume Jigsaw (F5)")
            self.start_btn.setIcon(make_icon("play", 12, "#1a1108"))
            self.summary_label.setText(f"Paused — {len(self._bots)} jigsaw worker(s)")
        else:
            self.start_btn.setText("  Stop Jigsaw")
            self.start_btn.setIcon(make_icon("stop", 12, "#1a1108"))
            self.summary_label.setText(f"Running {len(self._bots)} jigsaw worker(s)")

    def _append_status(self, bot_id: int, message: str) -> None:
        self._set_status(bot_id, message)
        self._append_log(message)
        if self.parent_window.status_log_window is not None:
            try:
                self.parent_window.status_log_window.add_message(message)
            except Exception:
                pass

    def _append_log(self, message: str) -> None:
        # Log widget removed; per-row status + global status log are the user-facing surfaces.
        return

    def _refresh_config_labels(self) -> None:
        big = f"font-family: {MONO}; font-size: 14px; font-weight: 600;"
        cfg = self.parent_window.config
        bounds = cfg.get("jigsaw_grid_bounds")
        if bounds and len(bounds) == 4:
            try:
                x, y, w, h = [int(v) for v in bounds]
                self.grid_info_label.setText(f"Grid: x={x} y={y} w={w} h={h}")
                self.grid_info_label.setStyleSheet(f"color: {C['green']}; {big}")
            except (TypeError, ValueError):
                self.grid_info_label.setText("Grid: invalid")
                self.grid_info_label.setStyleSheet(f"color: {C['red']}; {big}")
        else:
            self.grid_info_label.setText("Grid: not defined — use Define Grid first")
            self.grid_info_label.setStyleSheet(f"color: {C['orange']}; {big}")
        confirm = cfg.get("confirm_button_pos")
        if confirm:
            self.confirm_info_label.setText(f"Confirm: ({confirm[0]},{confirm[1]})")
            self.confirm_info_label.setStyleSheet(f"color: {C['green']}; {big}")
        else:
            self.confirm_info_label.setText("Confirm: not set — use Set Confirm Coords")
            self.confirm_info_label.setStyleSheet(f"color: {C['red']}; {big}")

    def _on_define_grid(self) -> None:
        if self.is_running():
            QMessageBox.warning(self, "Jigsaw running",
                                "Stop the jigsaw solver before redefining the grid.")
            return
        self.parent_window._define_jigsaw_grid(self)

    def _on_set_confirm(self) -> None:
        if self.is_running():
            QMessageBox.warning(self, "Jigsaw running",
                                "Stop the jigsaw solver before capturing coordinates.")
            return
        self.parent_window._begin_capture("confirm")

    def _on_confirm_coord_updated(self, coords) -> None:
        self._refresh_config_labels()

    def _set_progress(self, bot_id: int, crates_completed: int) -> None:
        row = self._rows.get(bot_id)
        if row:
            row["row"].setCrates(crates_completed)

    def _set_status(self, bot_id: int, status: str) -> None:
        # Per-window status column removed; status is shown via summary_label and global status log.
        return

    def _rebuild_rows(self) -> None:
        for row in self._rows.values():
            widget = row.get("row")
            if widget is not None:
                self._rows_layout.removeWidget(widget)
                widget.deleteLater()
        self._rows.clear()

        names = self.parent_window.selected_window_names()
        self.summary_label.setText(f"{len(names)} selected window(s)")
        for idx, name in enumerate(names):
            row_widget = JigsawWindowRow(idx, name)
            self._rows_layout.addWidget(row_widget)
            self._rows[idx] = {"row": row_widget}
        self._resize_for_rows(len(names))

    def _resize_for_rows(self, n_rows: int) -> None:
        # Tightly fit the dialog to the actual content rather than a tall fixed-base.
        # Header + cache row + summary + grid/confirm + buttons + Card chrome ≈ 270px.
        base_h = 270
        per_row = 30  # 28px row + 2px spacing
        target_h = base_h + max(1, n_rows) * per_row
        target_h = max(330, min(800, target_h))
        target_w = max(620, min(900, self.width() if self.width() >= 620 else 720))
        self.resize(target_w, target_h)

    def closeEvent(self, event) -> None:
        self._stop()
        for window in list(self._debug_windows.values()):
            try:
                window.destroy()
            except Exception:
                pass
        self._debug_windows.clear()
        self.parent_window._on_jigsaw_dialog_closed()
        super().closeEvent(event)


# ============================================================================
# Main window
# ============================================================================

class FishbotWindow(QMainWindow):
    BTC_ADDRESS = "3AGrrTflv9QZsMPEoezYTRbf9JyW4nQtHu"
    _f5_toggle = Signal()        # emitted from pynput thread → queued to main thread
    _sig_bot_stopped = Signal(int)         # bot_id
    _sig_row_stats = Signal(int, int, int) # bot_id, games, bait
    _sig_status_log = Signal(str)

    # Defaults that mirror the Tk version's TimingSettingsWindow.DEFAULTS
    TIMING_DEFAULTS = {
        "timing_cursor_settle":     0.012,
        "timing_button_hold":       0.008,
        "timing_post_click":        0.035,
        "timing_human_min":         0.150,
        "timing_human_max":         0.400,
        "timing_key_hold":          0.025,
        "timing_key_settle":        0.030,
        "timing_cast_interkey":     0.050,
        "timing_catch_wait":        0.400,
        "timing_open_wait":         0.100,
        "timing_dead_fish_check":   0.100,
        "timing_drop_settle":       0.120,
        "timing_quickskip_between": 0.100,
        "timing_quickskip_after":   0.100,
    }

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setObjectName("AppWindow")
        self.setFixedSize(920, 760)

        # Central widget
        central = QWidget()
        central.setObjectName("AppWindowInner")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar + header
        self.titlebar = TitleBar()
        self.titlebar.minimize.connect(self.showMinimized)
        self.titlebar.close_window.connect(self.close)
        root.addWidget(self.titlebar)

        self.header = Header()
        root.addWidget(self.header)

        self.tabs_bar = TabsBar([
            ("dashboard", "dashboard", "Dashboard"),
            ("inventory", "package",   "Inventory"),
            ("settings",  "settings",  "Settings"),
        ])
        self.tabs_bar.changed.connect(self._set_tab)
        root.addWidget(self.tabs_bar)

        # Body (stacked)
        body = QFrame()
        body.setObjectName("Body")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 14, 14, 14)
        body_layout.setSpacing(0)
        self.stack = QStackedWidget()
        body_layout.addWidget(self.stack)

        self.dashboard = DashboardTab()
        self.inventory = InventoryTab()
        self.settings = SettingsTab()
        for w in (self.dashboard, self.inventory, self.settings):
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
        self.config_path = os.path.join(os.getcwd(), "bot_config.json")
        self.bots: Dict[int, "FishingBot"] = {}
        self.bot_threads: Dict[int, threading.Thread] = {}
        self.window_managers: Dict[int, WindowManager] = {}
        self._jigsaw_dialog: Optional[JigsawSolverDialog] = None
        self._grid_overlay: Optional[JigsawGridOverlay] = None
        self._solver_cache_manager: Optional[SolverCacheManager] = None
        self._debug_tk_root = None
        self._debug_tk_pump: Optional[QTimer] = None
        self.status_log_window = None
        self.ignored_positions_windows: Dict[int, object] = {}
        self.fish_detector_debug_windows: Dict[int, object] = {}
        self.inventory_detection_debug_windows: Dict[int, object] = {}
        self.window_stats: Dict[int, dict] = {i: {"hits": 0, "games": 0, "bait": 0} for i in range(MAX_WINDOWS)}
        self._sound_alert_played = False
        self._session_started_bot_ids = set()
        self._session_bait_depleted_bot_ids = set()
        self._session_non_bait_stop_reasons = {}
        self._capture = PositionCaptureController(self)
        self._capture.signals.captured.connect(self._on_captured)
        self._capture.signals.failed.connect(self._on_capture_failed)
        self._capture_pill: Optional[str] = None
        self._capture_window_name: Optional[str] = None

        # Wire signals
        self.dashboard.start_clicked.connect(self.start_all_bots)
        self.dashboard.stop_clicked.connect(self._on_pause_or_start)
        self.dashboard.hard_stop.connect(self.stop_all_bots)
        self.dashboard.refresh_windows.connect(self.refresh_window_list)
        self.dashboard.reset_bait.connect(self.reset_bait)
        self.dashboard.window_changed.connect(self._on_window_changed)
        self.inventory.set_coord.connect(self._begin_capture)
        self.inventory.unset_coord.connect(self._on_unset_coord)
        self.inventory.open_fish_modal.connect(self.open_fish_modal)
        self.inventory.auto_fish_toggled.connect(self._on_auto_fish_toggled)
        self.settings.open_timing.connect(self.open_timing_modal)
        self.settings.config_changed.connect(self._save_settings)
        self.settings.accent_changed.connect(self._on_accent_changed)

        # Global F5 pause shortcut using pynput (works even when game window has focus)
        self._global_hotkey_listener = None
        self._f5_toggle.connect(self.toggle_pause_all)
        self._sig_bot_stopped.connect(self._on_bot_stopped_ui)
        self._sig_row_stats.connect(self._apply_row_stats)
        self._sig_status_log.connect(self._append_debug_status)
        self._start_global_hotkey_listener()
        self._esc = QShortcut(QKeySequence("Esc"), self)
        self._esc.activated.connect(self._cancel_capture)

        # Load config + populate.
        # _apply_state_to_ui must run BEFORE refresh_window_list so the settings
        # checkboxes are hydrated before setSelectedWindow fires save_config().
        self.load_config()
        self._apply_state_to_ui()
        self.refresh_window_list()
        self._set_jigsaw_launcher_enabled(True)
        self._refresh_input_backend_pill()

        # Window icon
        ico = get_resource_path("monkey.ico")
        if os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))

        # Periodic stats poll for running bots
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._poll_bot_stats)
        self._poll.start()
        self._init_debug_ui()

    def _refresh_input_backend_pill(self) -> None:
        """Probe the input backend and update the header pill. Called once at
        startup; bots created later inherit the same backend choice via the
        same probe path inside create_mouse_backend()."""
        try:
            name, err = probe_backend(self.config)
        except Exception as e:
            name, err = "pyautogui", str(e)
        if name == "interception":
            self.header.setInputBackend(
                "INTERCEPTION",
                "Interception kernel driver active — input is indistinguishable from a real mouse.",
            )
        else:
            tip = "PyAutoGUI fallback (SendInput-based)."
            if err:
                tip += f"\nInterception unavailable: {err}"
            self.header.setInputBackend("PYAUTOGUI", tip)

    def _init_debug_ui(self) -> None:
        # Embedded Tk debug UI is permanently disabled: pumping a Tk event loop
        # from a QTimer in the Qt main thread corrupts Python's thread state on
        # Python 3.13+ and crashes background threads with a fatal "PyEval_RestoreThread
        # ... thread state is NULL" error the next time they hit time.sleep().
        # Debug windows that depend on debug_tk_root() will gracefully no-op.
        self._debug_tk_root = None
        self.status_log_window = None
        self._debug_tk_pump = None
        return

    def debug_tk_root(self):
        return self._debug_tk_root

    def _pump_debug_tk(self) -> None:
        if self._debug_tk_root is None:
            return
        try:
            self._debug_tk_root.update_idletasks()
            self._debug_tk_root.update()
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Debug Tk pump stopped: {e}")
            if self._debug_tk_pump is not None:
                self._debug_tk_pump.stop()
            self._debug_tk_root = None

    # ---------------- Defaults ----------------
    def _default_config(self) -> dict:
        cfg = {
            "version": APP_VERSION,
            "human_like_clicking": False,
            "quick_skip": False,
            "quick_skip_mode": "horse",
            "sound_alert_on_finish": True,
            "classic_fishing": False,
            "classic_fishing_delay": 3.0,
            "jigsaw_solver_enabled": False,
            "jigsaw_crate_priority": "normal_first",
            "jigsaw_detection_threshold": 0.80,
            "jigsaw_action_delay": 0.15,
            "jigsaw_debug_screenshots": False,
            "jigsaw_dry_run": False,
            "auto_fish_handling": False,
            "fish_actions": {},
            "drop_button_pos": None,
            "confirm_button_pos": None,
            "armor_slot_pos": None,
            "accent_color": C["accent"],
            "rgb_wave_active": False,
            "bait_keys": ["1", "2", "3", "4"],
            "bait_quantity": 200,
            "bait": 800,
            "selected_windows": [""] * MAX_WINDOWS,
        }
        for p in range(1, 9):
            cfg[f"inv_page_{p}_pos"] = None
        cfg.update(self.TIMING_DEFAULTS)
        return cfg

    # ---------------- Layout helpers ----------------
    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("Footer")
        footer.setFixedHeight(48)
        l = QHBoxLayout(footer)
        l.setContentsMargins(14, 8, 14, 8)
        l.setSpacing(10)

        wiz_btn = QPushButton("  Setup Wizard")
        wiz_btn.setProperty("class", "BtnGhost")
        wiz_btn.setIcon(make_icon("wand", 12, C["text"]))
        wiz_btn.setCursor(Qt.PointingHandCursor)
        wiz_btn.clicked.connect(self.open_wizard)
        l.addWidget(wiz_btn)

        self.jigsaw_launcher_btn = QPushButton("  Jigsaw Solver")
        self.jigsaw_launcher_btn.setProperty("class", "BtnGhost")
        self.jigsaw_launcher_btn.setIcon(make_icon("grid", 12, C["text"]))
        self.jigsaw_launcher_btn.setCursor(Qt.PointingHandCursor)
        self.jigsaw_launcher_btn.clicked.connect(self.open_jigsaw_solver)
        l.addWidget(self.jigsaw_launcher_btn)
        l.addStretch(1)

        donations = QLabel(
            f"<span style='color:{C['text_dim']}'>DONATIONS · BTC ·</span> "
            f"<span style='color:{C['accent']}'>{self.BTC_ADDRESS}</span>"
        )
        donations.setTextFormat(Qt.RichText)
        donations.setStyleSheet(f"font-family: {MONO}; font-size: 11px; letter-spacing: 1px;")
        l.addWidget(donations)

        copy_btn = QPushButton()
        copy_btn.setIcon(make_icon("copy", 11, C["text_dim"]))
        copy_btn.setProperty("class", "BtnGhost")
        copy_btn.setFixedSize(28, 28)
        copy_btn.setToolTip("Copy BTC address")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_btc)
        l.addWidget(copy_btn)
        return footer

    def _set_tab(self, tab_id: str) -> None:
        idx = {"dashboard": 0, "inventory": 1, "settings": 2}.get(tab_id, 0)
        self.stack.setCurrentIndex(idx)

    def _set_jigsaw_launcher_enabled(self, enabled: bool) -> None:
        if hasattr(self, "jigsaw_launcher_btn"):
            self.jigsaw_launcher_btn.setEnabled(enabled)
            self.jigsaw_launcher_btn.setCursor(Qt.PointingHandCursor if enabled else Qt.ForbiddenCursor)

    def _on_jigsaw_dialog_closed(self) -> None:
        self._set_dashboard_window_selection_locked(False)
        if not self.bots:
            self.dashboard.start_btn.setEnabled(True)
            self.dashboard.setRunning(False)
            self.settings.setConfigEnabled(True)
            self.inventory.setDisabled(False)

    def _set_dashboard_window_selection_locked(self, locked: bool) -> None:
        for row in self.dashboard.window_rows:
            try:
                row.setComboEnabled(not locked)
            except Exception:
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
            pass
        QTimer.singleShot(150, lambda: self._open_grid_overlay(wm, selected[0], sender_dialog))

    def _open_grid_overlay(self, wm, target_name: str, sender_dialog=None) -> None:
        try:
            left, top, w, h = wm.get_window_rect()
        except Exception as e:
            QMessageBox.warning(self, "Window unavailable", str(e))
            return
        if w <= 0 or h <= 0:
            QMessageBox.warning(self, "Window unavailable",
                                "Could not read game window position.")
            return
        existing = self.config.get("jigsaw_grid_bounds")
        if existing and len(existing) == 4:
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
                pass
        self._grid_overlay = JigsawGridOverlay(left, top, w, h, grid, target_name)
        self._grid_overlay.confirmed.connect(lambda bounds: self._on_grid_confirmed(bounds, sender_dialog))
        self._grid_overlay.destroyed.connect(lambda: setattr(self, "_grid_overlay", None))
        self._grid_overlay.show()
        self._grid_overlay.raise_()
        self._grid_overlay.activateWindow()
        self._grid_overlay.setFocus()

    def _on_grid_confirmed(self, bounds: list, sender_dialog=None) -> None:
        self.config["jigsaw_grid_bounds"] = bounds
        self.save_config()
        self.add_status(f"Jigsaw grid set: {bounds}")
        if sender_dialog is not None:
            sender_dialog._refresh_config_labels()

    @property
    def solver_cache_manager(self) -> "SolverCacheManager":
        """App-wide singleton manager for the jigsaw solver cache."""
        if self._solver_cache_manager is None:
            self._solver_cache_manager = SolverCacheManager(self)
        return self._solver_cache_manager

    def selected_window_names(self) -> List[str]:
        return [row.selectedWindow() for row in self.dashboard.window_rows if row.selectedWindow()]

    # ---------------- Config I/O ----------------
    def load_config(self) -> None:
        if not os.path.exists(self.config_path):
            return
        try:
            with open(self.config_path, "r") as f:
                saved = json.load(f)
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Config load failed: {e}")
            return
        # Tolerant load — copy known keys
        for k, v in saved.items():
            if k == "selected_windows" and isinstance(v, list):
                # pad to MAX_WINDOWS
                v = list(v)[:MAX_WINDOWS] + [""] * (MAX_WINDOWS - len(v))
            self.config[k] = v
        # Coerce to tuples for any *_pos that came back as lists
        for k in list(self.config.keys()):
            if k.endswith("_pos") and isinstance(self.config[k], list) and len(self.config[k]) == 2:
                self.config[k] = tuple(self.config[k])

    def save_config(self) -> None:
        try:
            cfg = dict(self.config)
            cfg["version"] = APP_VERSION
            cfg["selected_windows"] = [row.selectedWindow() for row in self.dashboard.window_rows]
            self.settings.writeToConfig(cfg)
            with open(self.config_path, "w") as f:
                json.dump(cfg, f, indent=2)
            self.config = cfg
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Config save failed: {e}")

    def _save_settings(self) -> None:
        # Triggered from settings tab on user changes
        self.save_config()
        # Recalculate bait for all selected windows when settings change
        self._recalculate_window_bait()
        self._update_kpis()

    def _on_accent_changed(self, color: str) -> None:
        self.config["rgb_wave_active"] = False
        self.config["accent_color"] = color
        QApplication.instance().setStyleSheet(build_qss(color))
        self.save_config()

    def _apply_state_to_ui(self) -> None:
        # Settings
        self.settings.loadFromConfig(self.config)
        # Inventory pills
        for k in self.inventory.coord_pills:
            cfg_key = self._coord_key_to_cfg(k)
            self.inventory.setCoord(k, self.config.get(cfg_key))
        self.inventory.setAutoFish(self.config.get("auto_fish_handling", False))
        # Dashboard rows
        sw = self.config.get("selected_windows", [""] * MAX_WINDOWS)
        for i, row in enumerate(self.dashboard.window_rows):
            if i < len(sw) and sw[i]:
                row.setSelectedWindow(sw[i])
        # Apply accent
        accent = self.config.get("accent_color", C["accent"])
        QApplication.instance().setStyleSheet(build_qss(accent))
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
        try:
            names = [n for n, _ in WindowManager.get_all_windows()]
        except Exception:
            names = []
        self.dashboard.setWindowList(names)
        # Restore selections from config
        sw = self.config.get("selected_windows", [""] * MAX_WINDOWS)
        for i, row in enumerate(self.dashboard.window_rows):
            if i < len(sw) and sw[i]:
                row.setSelectedWindow(sw[i])

    def _on_window_changed(self, index: int, name: str) -> None:
        # Mark any selected slot as READY/EMPTY pill
        row = self.dashboard.window_rows[index]
        if name:
            row.setStatus("idle", "READY")
            # Calculate bait capacity based on configured quantity per key and selected keys
            bait_quantity = self.config.get("bait_quantity", 200)
            selected_keys = self.settings.selectedBaitKeys()
            bait_cap = len(selected_keys) * bait_quantity
            self.window_stats[index]["bait"] = bait_cap
            row.setBait(bait_cap)
        else:
            row.setStatus("idle", "EMPTY")
            self.window_stats[index]["bait"] = 0
            row.setBait("---")
        self.save_config()
        self._update_kpis()

    # ---------------- KPIs ----------------
    def _update_kpis(self) -> None:
        rows = self.dashboard.window_rows
        active = sum(1 for r in rows if r.selectedWindow())
        total_games = sum(self.window_stats[i]["games"] for i in range(MAX_WINDOWS))
        total_bait = sum(self.window_stats[i]["bait"] for i in range(MAX_WINDOWS) if rows[i].selectedWindow())
        bait_keys = self.settings.selectedBaitKeys()
        bait_quantity = self.config.get("bait_quantity", 200)
        cap = len(bait_keys) * bait_quantity
        self.dashboard.setKpis(total_games, active, total_bait, cap)

    # ---------------- Coord capture ----------------
    def _begin_capture(self, key: str) -> None:
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
        if self._capture.is_active():
            self._capture.cancel()
        self._capture_pill = key
        self._capture_window_name = selected[0]
        self.inventory.setCapturing(key)

        # Bring the target window forward, minimize the others.
        try:
            all_windows = WindowManager.get_all_windows()
            window_dict = {n: w for n, w in all_windows}
            for n in selected:
                if n != self._capture_window_name and n in window_dict:
                    try:
                        window_dict[n].minimize()
                    except Exception:
                        pass
            tgt = window_dict.get(self._capture_window_name)
            if tgt:
                try:
                    if tgt.isMinimized:
                        tgt.restore()
                except Exception:
                    pass
                try:
                    tgt.activate()
                except Exception:
                    pass
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Activate failed: {e}")

        if not self._capture.start(key):
            self.inventory.setCapturing(None)

    def _cancel_capture(self) -> None:
        if self._capture.is_active():
            self._capture.cancel()
            self.inventory.setCapturing(None)
            self._capture_pill = None
            self._capture_window_name = None

    def _on_captured(self, screen_x: int, screen_y: int, mode: str) -> None:
        if not self._capture_window_name:
            return
        try:
            all_windows = WindowManager.get_all_windows()
            window_dict = {n: w for n, w in all_windows}
            target = window_dict.get(self._capture_window_name)
            if not target:
                self.inventory.setCapturing(None)
                return
            wm = WindowManager()
            wm.selected_window = target
            left, top, _, _ = wm.get_window_rect()
            rel = (screen_x - left, screen_y - top)
            cfg_key = self._coord_key_to_cfg(mode)
            self.config[cfg_key] = rel
            self.inventory.setCoord(mode, rel)
            self.save_config()
            if mode == "confirm" and self._jigsaw_dialog is not None and self._jigsaw_dialog.isVisible():
                self._jigsaw_dialog._on_confirm_coord_updated(rel)
        finally:
            self.inventory.setCapturing(None)
            self._capture_pill = None
            self._capture_window_name = None

    def _on_capture_failed(self, msg: str) -> None:
        self.inventory.setCapturing(None)
        QMessageBox.warning(self, "Capture failed", msg)

    # ---------------- Modals ----------------
    def open_fish_modal(self) -> None:
        modal = FishModal(self.config.get("fish_actions", {}), self)
        modal.saved.connect(self._on_fish_actions_saved)
        modal.exec()

    def _on_fish_actions_saved(self, actions: dict) -> None:
        self.config["fish_actions"] = actions
        self.save_config()

    def open_jigsaw_solver(self) -> None:
        if self.bots:
            QMessageBox.warning(self, "Fishbot running", "Stop Fishbot before opening the jigsaw solver.")
            return
        if not self.selected_window_names():
            QMessageBox.warning(
                self,
                "No window selected",
                "Select at least one game window on the Dashboard before opening the jigsaw solver."
            )
            return
        if self._jigsaw_dialog is None or not self._jigsaw_dialog.isVisible():
            self._jigsaw_dialog = JigsawSolverDialog(self)
        else:
            self._jigsaw_dialog._rebuild_rows()
        self._jigsaw_dialog.show()
        self._jigsaw_dialog.raise_()
        self._jigsaw_dialog.activateWindow()
        self.dashboard.start_btn.setEnabled(False)
        self.dashboard.start_btn.setText("  START ALL")
        self.dashboard.start_btn.setIcon(make_icon("play", 14, "#1a1108"))
        self._set_dashboard_window_selection_locked(True)
        self.settings.setConfigEnabled(False)
        self.inventory.setDisabled(True)

    def open_timing_modal(self) -> None:
        modal = TimingModal(self.config, self)
        modal.saved.connect(self._on_timing_saved)
        modal.exec()

    def _on_timing_saved(self, timings: dict) -> None:
        self.config.update(timings)
        self.save_config()

    def open_wizard(self) -> None:
        modal = WizardModal(self)
        modal.exec()

    def _on_auto_fish_toggled(self, enabled: bool) -> None:
        self.config["auto_fish_handling"] = enabled
        self.save_config()

    def _on_unset_coord(self, key: str) -> None:
        """Handle unset coordinate request (click on already-set pill)."""
        cfg_key = self._coord_key_to_cfg(key)
        self.config[cfg_key] = None
        self.inventory.setCoord(key, None)
        self.save_config()

    # ---------------- Bait ----------------
    def reset_bait(self) -> None:
        bait_quantity = self.config.get("bait_quantity", 200)
        cap = len(self.settings.selectedBaitKeys()) * bait_quantity
        self.config["bait"] = cap
        rows = self.dashboard.window_rows
        for i, row in enumerate(rows):
            if row.selectedWindow():
                self.window_stats[i]["bait"] = cap
                row.setBait(cap)
            else:
                self.window_stats[i]["bait"] = 0
                row.setBait("---")
        # Also reset any running bots
        for bot in self.bots.values():
            bot.bait_counter = cap
        self.save_config()
        self._update_kpis()

    def _recalculate_window_bait(self) -> None:
        """Recalculates bait for all selected windows when bait quantity changes."""
        bait_quantity = self.config.get("bait_quantity", 200)
        selected_keys = self.settings.selectedBaitKeys()
        new_cap = len(selected_keys) * bait_quantity
        
        rows = self.dashboard.window_rows
        for i, row in enumerate(rows):
            if row.selectedWindow():
                self.window_stats[i]["bait"] = new_cap
                row.setBait(new_cap)

    def _copy_btc(self) -> None:
        QApplication.clipboard().setText(self.BTC_ADDRESS)

    # ---------------- Bot lifecycle ----------------
    def start_all_bots(self) -> None:
        if self._jigsaw_dialog is not None and self._jigsaw_dialog.isVisible():
            QMessageBox.warning(self, "Jigsaw Solver open", "Close the Jigsaw Solver before starting Fishbot.")
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
        bait_keys = self.settings.selectedBaitKeys()
        if not bait_keys:
            QMessageBox.warning(self, "No bait keys", "Select at least one bait key on the Settings tab.")
            return
        # Validate inventory coords
        missing = [p for p in range(1, 5) if not self.config.get(f"inv_page_{p}_pos")]
        if missing:
            QMessageBox.warning(
                self, "Inventory pages not configured",
                "Set inventory page coordinates 1-4 on the Inventory tab "
                f"before starting (missing: {', '.join('Page ' + str(p) for p in missing)})."
            )
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
                    QMessageBox.warning(self, "Configure confirm coord",
                                        "Set the Confirm coordinate on the Inventory tab.")
                    return

        # Fill any unset fish/item actions with "keep" so the bot has a safe default
        actions = self.config.setdefault("fish_actions", {})
        for _, asset, _ in FISH_DISPLAY + ITEM_DISPLAY:
            actions.setdefault(asset, "keep")

        self._sound_alert_played = False
        self._session_started_bot_ids = set()
        self._session_bait_depleted_bot_ids = set()
        self._session_non_bait_stop_reasons = {}

        all_windows = {n: w for n, w in WindowManager.get_all_windows()}
        self.save_config()

        cfg_for_bot = dict(self.config)
        cfg_for_bot["bait_keys"] = bait_keys
        cfg_for_bot["jigsaw_solver_enabled"] = False
        for i, name in selected:
            if name not in all_windows:
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
            bot.on_stats_update = self._on_bot_stats
            bot.on_bait_update = self._on_bot_bait
            bot.on_bot_stop = self._on_bot_stopped
            bot.running = True
            self.bots[i] = bot
            self._session_started_bot_ids.add(i)
            self.window_stats[i]["games"] = 0
            self.window_stats[i]["bait"] = current_bait
            self._create_fish_debug_windows(i, bot)
            rows[i].setStatus("active", "RUNNING")
            rows[i].setComboEnabled(False)
            t = threading.Thread(target=bot.start, daemon=True)
            t.start()
            self.bot_threads[i] = t
        self.dashboard.setRunning(bool(self.bots))
        self.settings.setConfigEnabled(False)
        self.inventory.setDisabled(True)
        self._set_jigsaw_launcher_enabled(False)
        self.header.setStatus("● RUNNING", "running")

    def stop_all_bots(self) -> None:
        for bot in list(self.bots.values()):
            bot.running = False
            try:
                bot.stop()
                self._session_non_bait_stop_reasons[bot.bot_id] = FishingBot.STOP_MANUAL
            except Exception:
                pass
        self._destroy_all_fish_debug_windows()
        self.bots.clear()
        self.bot_threads.clear()
        for row in self.dashboard.window_rows:
            row.setStatus("idle", "READY" if row.selectedWindow() else "EMPTY")
            row.setComboEnabled(True)
        self.dashboard.setRunning(False)
        self.settings.setConfigEnabled(True)
        self.settings.setPaused(False)
        self.inventory.setDisabled(False)
        self._set_jigsaw_launcher_enabled(True)
        self.header.setStatus("● IDLE", "idle")

    def _on_pause_or_start(self) -> None:
        """Called by the primary button when the bot is running — toggles pause."""
        if self.bots:
            self.toggle_pause_all()

    def toggle_pause_all(self) -> None:
        """Toggle pause/resume for all running bots. Thread-safe."""
        if not self.bots:
            if DEBUG_PRINTS:
                print("toggle_pause_all: no bots running")
            return
        
        # Get current state BEFORE any changes
        any_paused = any(b.paused for b in self.bots.values())
        new_paused = not any_paused
        
        if DEBUG_PRINTS:
            print(f"toggle_pause_all: any_paused={any_paused}, new_paused={new_paused}")
        
        # Apply pause state to all bots
        for bot in self.bots.values():
            bot.paused = new_paused
        
        # Update UI - use direct calls since we're already on main thread via QTimer.singleShot
        for i, bot in self.bots.items():
            kind, txt = ("warn", "PAUSED") if new_paused else ("active", "RUNNING")
            self.dashboard.window_rows[i].setStatus(kind, txt)
        
        self.dashboard.setPaused(new_paused)
        self.settings.setPaused(new_paused)
        status_txt = "● PAUSED" if new_paused else "● RUNNING"
        status_kind = "idle" if new_paused else "running"
        self.header.setStatus(status_txt, status_kind)
        
        if DEBUG_PRINTS:
            print(f"toggle_pause_all: completed, paused={new_paused}")

    def _on_status(self, msg: str) -> None:
        self._sig_status_log.emit(msg)
        if DEBUG_PRINTS:
            print(msg)

    def add_status(self, msg: str) -> None:
        """Compatibility logger for UI paths that are not owned by a bot."""
        self._on_status(msg)

    def _append_debug_status(self, msg: str) -> None:
        if self.status_log_window is None:
            return
        try:
            self.status_log_window.add_message(msg)
        except Exception:
            pass

    def _on_bot_stats(self, bot_id: int, hits: int, total_games: int, bait: int) -> None:
        self.window_stats[bot_id]["hits"] = hits
        self.window_stats[bot_id]["games"] = total_games
        self.window_stats[bot_id]["bait"] = bait
        self._sig_row_stats.emit(bot_id, total_games, bait)

    def _apply_row_stats(self, bot_id: int, games: int, bait: int) -> None:
        if bot_id < len(self.dashboard.window_rows):
            self.dashboard.window_rows[bot_id].setBait(bait)
            self.dashboard.window_rows[bot_id].setGames(games)
        self._update_kpis()

    def _on_bot_bait(self, bot_id: int, new_bait: int) -> None:
        self.window_stats[bot_id]["bait"] = new_bait
        self._sig_row_stats.emit(bot_id, self.window_stats[bot_id]["games"], new_bait)

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

    def _on_bot_stopped(self, bot_id: int) -> None:
        self._sig_bot_stopped.emit(bot_id)

    def _on_bot_stopped_ui(self, bot_id: int) -> None:
        bot = self.bots.get(bot_id)
        reason = getattr(bot, "stop_reason", None)
        if FishingBot is not None and reason == FishingBot.STOP_BAIT_DEPLETED:
            self._session_bait_depleted_bot_ids.add(bot_id)
        elif reason is not None:
            self._session_non_bait_stop_reasons[bot_id] = reason

        self.bots.pop(bot_id, None)
        self.bot_threads.pop(bot_id, None)
        self._destroy_fish_debug_windows(bot_id)
        if bot_id < len(self.dashboard.window_rows):
            row = self.dashboard.window_rows[bot_id]
            row.setStatus("error", "STOPPED")
            row.setComboEnabled(True)
        if not self.bots:
            self.dashboard.setRunning(False)
            self.settings.setConfigEnabled(True)
            self.settings.setPaused(False)
            self.inventory.setDisabled(False)
            self._set_jigsaw_launcher_enabled(True)
            self.header.setStatus("● IDLE", "idle")
            self._maybe_play_no_bait_alert()

    def _create_fish_debug_windows(self, bot_id: int, bot) -> None:
        if not DEBUG_MODE_EN:
            return
        root = self.debug_tk_root()
        if root is None:
            return
        try:
            if IgnoredPositionsWindow is not None:
                self.ignored_positions_windows[bot_id] = IgnoredPositionsWindow(root, bot)
            if FishDetectorDebugWindow is not None:
                self.fish_detector_debug_windows[bot_id] = FishDetectorDebugWindow(root, bot)
            if InventoryDetectionDebugWindow is not None:
                self.inventory_detection_debug_windows[bot_id] = InventoryDetectionDebugWindow(root, bot)
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Failed to create debug windows for W{bot_id + 1}: {e}")

    def _destroy_fish_debug_windows(self, bot_id: int) -> None:
        for store in (
            self.ignored_positions_windows,
            self.fish_detector_debug_windows,
            self.inventory_detection_debug_windows,
        ):
            window = store.pop(bot_id, None)
            if window is not None:
                try:
                    window.destroy()
                except Exception:
                    pass

    def _destroy_all_fish_debug_windows(self) -> None:
        for bot_id in set(
            list(self.ignored_positions_windows.keys())
            + list(self.fish_detector_debug_windows.keys())
            + list(self.inventory_detection_debug_windows.keys())
        ):
            self._destroy_fish_debug_windows(bot_id)

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
                pass
            self._global_hotkey_listener = None

    # ---------------- Lifecycle ----------------
    def closeEvent(self, ev) -> None:
        for bot in list(self.bots.values()):
            bot.running = False
        self._capture.cancel()
        self._stop_global_hotkey_listener()
        self._destroy_all_fish_debug_windows()
        if self._jigsaw_dialog is not None:
            try:
                self._jigsaw_dialog.close()
            except Exception:
                pass
        if self.status_log_window is not None:
            try:
                self.status_log_window.destroy()
            except Exception:
                pass
            self.status_log_window = None
        if self._debug_tk_pump is not None:
            self._debug_tk_pump.stop()
        if self._debug_tk_root is not None:
            try:
                self._debug_tk_root.destroy()
            except Exception:
                pass
            self._debug_tk_root = None
        self.save_config()
        super().closeEvent(ev)


# ============================================================================
# Entry point
# ============================================================================

def main() -> int:
    app = QApplication(sys.argv)
    # Ensure dark fallback for things outside the styled regions
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(C["bg_1"]))
    pal.setColor(QPalette.Base, QColor(C["bg_1"]))
    pal.setColor(QPalette.Text, QColor(C["text"]))
    pal.setColor(QPalette.WindowText, QColor(C["text"]))
    app.setPalette(pal)
    app.setStyleSheet(build_qss(C["accent"]))
    w = FishbotWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

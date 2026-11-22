"""
Help with pictures: what to set up and where, and what to do when a bot
stops for a reason the user can fix.

Every picture is drawn with QPainter on a 520x300 canvas that scales to the
widget, in the game's dark look, so nothing depends on image files or on a
particular client. Green numbered circles are where to click, a green check
marks the right way, a red cross the wrong one.

    show_help(parent, "jigsaw_layout", problem="The Confirm point ...")
    open_guide(parent, start="inventory_tabs")

The topics are plain data (TOPICS); the GUI maps stop reasons to them with
STOP_REASON_TOPIC.
"""

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

# Theme of the surrounding app (qt_gui passes its own with set_theme)
THEME: Dict[str, str] = {
    "bg": "#15161a", "bg_2": "#1d1f24", "line": "#2c2f36", "text": "#e6e6e6", "text_dim": "#a0a4ab",
    "text_mute": "#6f737a", "accent": "#f0a030", "green": "#4cc38a", "red": "#e5534b", "blue": "#5aa9e6",
}
MONO = "monospace"


def set_theme(colors: Dict[str, str], mono: str) -> None:
    global MONO
    THEME.update({k: v for k, v in colors.items() if isinstance(v, str)})
    MONO = mono


# ----------------------------------------------------------------- drawing kit

W, H = 520.0, 300.0
GAME_BG = QColor("#3d4a3a")        # grass / ground behind the windows
PANEL = QColor("#22201c")           # a game window's body
PANEL_EDGE = QColor("#7a6a4a")
TITLE = QColor("#5a2a1a")
SLOT = QColor("#121212")
TEXT = QColor("#e9e2cf")
OK = QColor("#4cc38a")
BAD = QColor("#e5534b")
MARK = QColor("#f0a030")


def _pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    return pen


class Canvas:
    """Small helpers over a QPainter that draws on the 520x300 canvas."""

    def __init__(self, p: QPainter):
        self.p = p
        self.grid = (0.0, 0.0, 11.0, 1.5)    # slot grid of the last inventory: x, y, slot size, gap

    def font(self, size: float, bold: bool = False) -> None:
        f = QFont()
        # MONO is a CSS list ("'Consolas', 'JetBrains Mono', monospace")
        families = [n.strip().strip("'\"") for n in MONO.split(",") if n.strip()]
        f.setFamilies(families + ["DejaVu Sans Mono", "Courier New"])
        f.setStyleHint(QFont.Monospace)
        f.setPointSizeF(size)
        f.setBold(bold)
        self.p.setFont(f)

    def text(self, x: float, y: float, w: float, h: float, s: str, size: float = 7.5,
             color: QColor = TEXT, align=Qt.AlignCenter, bold: bool = False) -> None:
        self.font(size, bold)
        self.p.setPen(color)
        self.p.drawText(QRectF(x, y, w, h), int(align) | int(Qt.TextWordWrap), s)

    def rect(self, x, y, w, h, fill: QColor, edge: Optional[QColor] = None, radius: float = 3,
             width: float = 1.2, dashed: bool = False) -> None:
        pen = QPen(edge if edge is not None else Qt.NoPen)
        if edge is not None:
            pen.setWidthF(width)
            if dashed:
                pen.setStyle(Qt.DashLine)
        self.p.setPen(pen)
        self.p.setBrush(QBrush(fill) if fill is not None else Qt.NoBrush)
        self.p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)

    def window(self, x, y, w, h, title: str) -> Tuple[float, float, float, float]:
        """A game window (frame + title bar). Returns its inner rectangle."""
        self.rect(x, y, w, h, PANEL, PANEL_EDGE, radius=4)
        self.rect(x + 3, y + 3, w - 6, 13, TITLE, None, radius=2)
        self.text(x, y + 3, w, 13, title, size=6.5)
        return x + 5, y + 19, w - 10, h - 23

    def button(self, x, y, w, h, label: str, hot: bool = False) -> None:
        self.rect(x, y, w, h, QColor("#3a3630"), MARK if hot else PANEL_EDGE, radius=3, width=1.6 if hot else 1)
        self.text(x, y, w, h, label, size=6.8)

    def slots(self, x, y, cols, rows, size=11, gap=1.5, filled: Callable[[int, int], bool] = None) -> None:
        for r in range(rows):
            for c in range(cols):
                fx, fy = x + c * (size + gap), y + r * (size + gap)
                full = filled(r, c) if filled else False
                self.rect(fx, fy, size, size, QColor("#5b4a32") if full else SLOT, QColor("#3a332a"), radius=2, width=0.8)

    def click(self, x, y, n: str = "", color: QColor = OK, r: float = 7) -> None:
        """A numbered click marker."""
        self.p.setPen(QPen(QColor("#0d0d0d"), 1.2))
        self.p.setBrush(color)
        self.p.drawEllipse(QPointF(x, y), r, r)
        if n:
            self.text(x - r, y - r, 2 * r, 2 * r, n, size=6.5 * r / 7, color=QColor("#0d0d0d"), bold=True)

    def point_at(self, x, y, n: str = "", side: str = "right", color: QColor = OK) -> None:
        """A numbered click marker beside (x, y) with a short arrow to it, so
        the marker does not hide what is to be clicked."""
        dx, dy = {"right": (26, 0), "left": (-26, 0), "below": (0, 24), "above": (0, -24)}[side]
        mx, my = x + dx, y + dy
        self.arrow(mx - dx * 0.45, my - dy * 0.45, x + dx * 0.12, y + dy * 0.12, color, 1.6)
        self.click(mx, my, n, color)

    def badge(self, x, y, ok: bool) -> None:
        """A green check or a red cross in a circle."""
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(OK if ok else BAD)
        self.p.drawEllipse(QPointF(x, y), 11, 11)
        self.p.setPen(_pen(QColor("#0d0d0d"), 2.6))
        if ok:
            path = QPainterPath(QPointF(x - 5, y))
            path.lineTo(x - 1.5, y + 4)
            path.lineTo(x + 5.5, y - 4.5)
            self.p.setBrush(Qt.NoBrush)
            self.p.drawPath(path)
        else:
            self.p.drawLine(QPointF(x - 4.5, y - 4.5), QPointF(x + 4.5, y + 4.5))
            self.p.drawLine(QPointF(x + 4.5, y - 4.5), QPointF(x - 4.5, y + 4.5))

    def arrow(self, x1, y1, x2, y2, color: QColor = MARK, width: float = 1.8) -> None:
        self.p.setPen(_pen(color, width))
        self.p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        import math
        a = math.atan2(y2 - y1, x2 - x1)
        for d in (2.6, -2.6):
            self.p.drawLine(QPointF(x2, y2), QPointF(x2 - 7 * math.cos(a + d / 6), y2 - 7 * math.sin(a + d / 6)))

    def screen(self, x, y, w, h) -> None:
        """The game screen (a client's window area)."""
        self.rect(x, y, w, h, GAME_BG, QColor("#1a1a1a"), radius=4, width=1.5)

    def caption(self, x, y, w, s: str, color: QColor = TEXT, size: float = 7.2) -> None:
        self.text(x, y, w, 14, s, size=size, color=color)

    def board(self, x, y, cell=13, filled=()) -> None:
        """The 6x4 puzzle grid."""
        colors = [QColor("#e0443a"), QColor("#f2d33c"), QColor("#38d6e6"), QColor("#3a6ee8"), QColor("#4ad24a")]
        for r in range(4):
            for c in range(6):
                k = r * 6 + c
                fill = colors[k % len(colors)] if k in filled else QColor("#101010")
                self.rect(x + c * cell, y + r * cell, cell - 1, cell - 1, fill, QColor("#4a4a4a"), radius=1.5, width=0.7)

    def crate(self, x, y, special: bool) -> None:
        self.rect(x - 9, y - 8, 18, 16, QColor("#2c2a33"), QColor("#c9a65a"), radius=4, width=1.2)
        self.rect(x - 6, y - 5, 12, 5, QColor("#f2d33c") if special else QColor("#9be03a"), None, radius=1.5)
        self.rect(x - 6, y, 12, 5, QColor("#d6312a") if special else QColor("#e07a2a"), None, radius=1.5)

    def puzzle_window(self, x, y, scale=1.0, filled=(), title="Fish Puzzle") -> Tuple[float, float, float, float]:
        """The jigsaw window; returns the grid rectangle."""
        cell = 13 * scale
        w, h = 6 * cell + 52 * scale, 4 * cell + 46 * scale
        self.window(x, y, w, h, title)
        gx, gy = x + 8 * scale, y + 24 * scale
        self.board(gx, gy, cell, filled)
        self.crate(gx + 6 * cell + 20 * scale, gy + 1.2 * cell, special=True)
        self.crate(gx + 6 * cell + 20 * scale, gy + 2.9 * cell, special=False)
        return gx, gy, 6 * cell - 1, 4 * cell - 1

    def confirm_dialog(self, cx, cy, question="Use it?", w=110) -> Tuple[float, float]:
        """The game's small yes/no dialog centred on (cx, cy); returns the Yes button centre."""
        h = 50
        x, y = cx - w / 2, cy - h / 2
        self.rect(x, y, w, h, QColor("#1b1a18"), PANEL_EDGE, radius=4)
        self.text(x, y + 5, w, 14, question, size=6.8)
        self.button(x + w / 2 - 43, y + 26, 38, 15, "Yes", hot=True)
        self.button(x + w / 2 + 5, y + 26, 38, 15, "No")
        return x + w / 2 - 24, y + 33.5

    def inventory(self, x, y, pages=4, active=1, rows=9, scale=1.0,
                  filled: Optional[Callable[[int, int], bool]] = None) -> Dict[int, Tuple[float, float, float]]:
        """The inventory window with its page tabs; returns {page: (tab
        centre x, centre y, tab width)}. Its slot grid is kept in
        ``self.grid`` for ``cell``."""
        k = scale
        w = 5 * 12.5 * k + 14
        h = 19 + 13 * k + rows * 12.5 * k + 8
        self.window(x, y, w, h, "Inventory")
        tabs = {}
        tw = (w - 10) / pages
        th = 11 * k
        numerals = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]
        for i in range(pages):
            tx = x + 5 + i * tw
            self.rect(tx + 0.5, y + 19, tw - 1, th, QColor("#6b655c") if i + 1 == active else QColor("#3a3630"),
                      PANEL_EDGE, radius=1.5, width=0.8)
            self.text(tx, y + 19, tw * 0.8 if k > 1.2 else tw, th, numerals[i], size=5.6)
            tabs[i + 1] = (tx + tw / 2, y + 19 + th / 2, tw)
        self.grid = (x + 7, y + 19 + th + 3, 11 * k, 1.5 * k)
        self.slots(x + 7, y + 19 + th + 3, 5, rows, size=11 * k, gap=1.5 * k,
                   filled=filled if filled is not None else
                   (lambda r, c: (r * 5 + c) % 3 != 0 and r < rows - 2))
        return tabs

    def cell(self, col: float, row: float, cols: int = 1, rows: int = 1) -> Tuple[float, float, float, float]:
        """The rectangle of ``cols`` x ``rows`` slots starting at (col, row)
        of the inventory drawn last."""
        gx, gy, size, gap = self.grid
        return (gx + col * (size + gap), gy + row * (size + gap),
                cols * size + (cols - 1) * gap, rows * size + (rows - 1) * gap)

    def armor_item(self, x, y, w, h, hot: bool = False, label: str = "") -> None:
        """An armor icon: in the game it fills two slots, one column wide and
        two rows tall."""
        self.rect(x, y, w, h, QColor("#3b3f47"), MARK if hot else QColor("#6d7380"), radius=2.5,
                  width=1.8 if hot else 1.0)
        # a breastplate: shoulders, body narrowing to the waist, a belt
        path = QPainterPath(QPointF(x + w * 0.18, y + h * 0.16))
        path.lineTo(x + w * 0.38, y + h * 0.10)
        path.quadTo(x + w * 0.5, y + h * 0.18, x + w * 0.62, y + h * 0.10)
        path.lineTo(x + w * 0.82, y + h * 0.16)
        path.lineTo(x + w * 0.76, y + h * 0.42)
        path.lineTo(x + w * 0.72, y + h * 0.86)
        path.lineTo(x + w * 0.28, y + h * 0.86)
        path.lineTo(x + w * 0.24, y + h * 0.42)
        path.closeSubpath()
        self.p.setPen(QPen(QColor("#1e2026"), 0.8))
        self.p.setBrush(QColor("#a9b1bd"))
        self.p.drawPath(path)
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(QColor("#c9a65a"))
        self.p.drawRect(QRectF(x + w * 0.27, y + h * 0.62, w * 0.46, h * 0.06))
        if label:
            self.text(x - 6, y + h + 1, w + 12, 10, label, size=4.6, color=MARK if hot else TEXT)

    def fish_item(self, x, y, s, color: QColor) -> None:
        """A small fish icon filling one slot of size ``s``."""
        self.p.setPen(QPen(QColor("#0d0d0d"), 0.6))
        self.p.setBrush(color)
        self.p.drawEllipse(QRectF(x + s * 0.12, y + s * 0.3, s * 0.58, s * 0.4))
        tail = QPainterPath(QPointF(x + s * 0.66, y + s * 0.5))
        tail.lineTo(x + s * 0.9, y + s * 0.3)
        tail.lineTo(x + s * 0.9, y + s * 0.7)
        tail.closeSubpath()
        self.p.drawPath(tail)
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(QColor("#0d0d0d"))
        self.p.drawEllipse(QPointF(x + s * 0.26, y + s * 0.46), s * 0.05, s * 0.05)

    def key(self, x, y, label: str, w: float = 22, hot: bool = False, sub: str = "") -> None:
        """A keyboard key / hotbar slot."""
        self.rect(x, y, w, 22, QColor("#2b2925"), MARK if hot else PANEL_EDGE, radius=3, width=1.6 if hot else 1)
        self.text(x, y + 1, w, 12, label, size=6.2, bold=True)
        if sub:
            self.text(x, y + 11, w, 10, sub, size=4.6, color=QColor("#bba98a"))

    def cursor(self, x, y, color: QColor = QColor("#f2f2f2")) -> None:
        """A mouse pointer with its tip at (x, y)."""
        path = QPainterPath(QPointF(x, y))
        path.lineTo(x, y + 14)
        path.lineTo(x + 3.6, y + 10.6)
        path.lineTo(x + 6.4, y + 16)
        path.lineTo(x + 8.4, y + 15)
        path.lineTo(x + 5.8, y + 9.8)
        path.lineTo(x + 10.4, y + 9.8)
        path.closeSubpath()
        self.p.setPen(QPen(QColor("#0d0d0d"), 1.0))
        self.p.setBrush(color)
        self.p.drawPath(path)

    def bot_pill(self, x, y, w, label: str, state: str = "unset") -> None:
        """A position tile of the bot's own window (Positions tab)."""
        colors = {"unset": (QColor("#6f737a"), "not set"), "capturing": (MARK, "click in game…"),
                  "set": (OK, "412,233")}
        color, value = colors[state]
        self.rect(x, y, w, 18, QColor("#1d1f24"), color if state != "unset" else QColor("#2c2f36"),
                  radius=4, width=1.4 if state == "capturing" else 1)
        self.text(x + 6, y, w * 0.55, 18, label, size=5.8, color=QColor("#e6e6e6"), align=Qt.AlignLeft | Qt.AlignVCenter)
        self.text(x + w * 0.45, y, w * 0.5, 18, value, size=5.4, color=color, align=Qt.AlignRight | Qt.AlignVCenter)

    def app_window(self, x, y, w, h, title: str = "Fishing Puzzle Player") -> Tuple[float, float, float, float]:
        """The bot's own window (not a game window)."""
        self.rect(x, y, w, h, QColor(THEME["bg"]), QColor(THEME["line"]), radius=4, width=1.2)
        self.rect(x, y, w, 13, QColor(THEME["bg_2"]), None, radius=4)
        self.text(x + 6, y, w - 12, 13, title, size=5.6, color=QColor(THEME["accent"]),
                  align=Qt.AlignLeft | Qt.AlignVCenter, bold=True)
        return x + 6, y + 18, w - 12, h - 22


# --------------------------------------------------------------- the pictures

def draw_windows(c: Canvas) -> None:
    c.caption(10, 6, 240, "Side by side, nothing on top", OK)
    c.screen(10, 24, 240, 150)
    c.window(18, 34, 108, 130, "Metin2 (1)")
    c.window(134, 34, 108, 130, "Metin2 (2)")
    c.badge(236, 180, True)
    c.caption(270, 6, 240, "Overlapping or covered", BAD)
    c.screen(270, 24, 240, 150)
    c.window(280, 34, 130, 120, "Metin2 (1)")
    c.window(350, 60, 140, 104, "Metin2 (2)")
    c.badge(496, 180, False)
    c.text(10, 205, 500, 90,
           "Every client the bot runs needs the same window size, must stay visible (it may be in the "
           "background, but not minimized) and must not be covered by another game window or app. "
           "The bot reads each window's picture.", size=7.2, color=QColor(THEME["text_dim"]))


def draw_inventory_tabs(c: Canvas) -> None:
    c.screen(10, 10, 300, 280)
    tabs = c.inventory(166, 22, pages=4, active=1, rows=7, scale=2.0)
    for page, (tx, ty, tw) in tabs.items():
        c.click(tx + tw * 0.32, ty, str(page), r=5.0)
    c.text(20, 20, 136, 130,
           "Click the tile Page 1 in the bot, then click tab I in the game.\n\n"
           "Then Page 2 and tab II, and so on, in order, for every page your inventory has.",
           size=7.4, align=Qt.AlignLeft | Qt.AlignTop)
    c.text(20, 165, 136, 120,
           "Click on the tab, a little beside its numeral, not on the letters.\n\n"
           "Pages your client does not have stay empty.",
           size=7.0, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)
    c.text(325, 20, 185, 270,
           "The bot works out the item grid from these tabs: it lies right below them. Set the tabs "
           "again whenever you move the inventory or change the resolution.",
           size=7.2, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_inventory_open(c: Canvas) -> None:
    c.caption(10, 6, 240, "Open, where the tabs were set", OK)
    c.screen(10, 24, 240, 200)
    c.inventory(160, 32, pages=4, active=1, rows=7)
    c.badge(236, 232, True)
    c.caption(270, 6, 240, "Closed, moved, or covered", BAD)
    c.screen(270, 24, 240, 200)
    c.window(380, 40, 90, 70, "Inventory")
    c.arrow(450, 120, 430, 160, BAD)
    c.rect(330, 120, 110, 40, QColor("#101010"), QColor("#777777"), radius=3)
    c.text(330, 120, 110, 40, "a tooltip or another window over it", size=6.2, color=QColor("#bbbbbb"))
    c.badge(496, 232, False)
    c.text(10, 250, 500, 50,
           "Keep the inventory open at the place where you set its tabs, with nothing over it. The bot "
           "reads it to see where catches land and when a page is full.",
           size=7.0, color=QColor(THEME["text_dim"]))


def draw_drop_confirm(c: Canvas) -> None:
    c.screen(10, 10, 500, 200)
    c.inventory(420, 18, pages=4, active=1, rows=6)
    c.arrow(430, 80, 300, 150, MARK)
    c.text(250, 156, 120, 30, "the bot drops it on the ground", size=6.4, color=MARK)
    # dialog 1: drop / sell - only on some clients
    x, y = 40, 30
    c.rect(x, y, 150, 62, QColor("#1b1a18"), PANEL_EDGE, radius=4, dashed=True)
    c.text(x, y + 6, 150, 14, "Drop this item?", size=6.8)
    c.button(x + 8, y + 34, 76, 16, "Drop / Sell", hot=True)
    c.button(x + 90, y + 34, 52, 16, "Cancel")
    c.point_at(x + 46, y + 50, "1", "below")
    c.text(x + 156, y + 4, 92, 54, "only if your client shows this dialog", size=6.0, color=MARK,
           align=Qt.AlignLeft | Qt.AlignTop)
    # dialog 2: confirm - always
    x2, y2 = 40, 120
    c.rect(x2, y2, 150, 62, QColor("#1b1a18"), PANEL_EDGE, radius=4)
    c.text(x2, y2 + 6, 150, 14, "Are you sure?", size=6.8)
    c.button(x2 + 10, y2 + 34, 60, 16, "Yes", hot=True)
    c.button(x2 + 100, y2 + 34, 40, 16, "No")
    c.point_at(x2 + 40, y2 + 50, "2", "below")
    c.text(x2 + 156, y2 + 4, 92, 54, "always needed", size=6.0, color=OK, align=Qt.AlignLeft | Qt.AlignTop)
    c.text(10, 216, 500, 84,
           "Drop one fish by hand and look at what your client asks.\n"
           "Two dialogs (drop/sell, then yes/no): set 1 Drop / Sell and 2 Confirm.\n"
           "Only a yes/no dialog: leave Drop / Sell empty and set only 2 Confirm.\n"
           "Set each one while its dialog is on screen.",
           size=7.0, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_armor_slot(c: Canvas) -> None:
    c.screen(10, 10, 330, 280)
    # the worn armor, in the equipment window: not this one
    x, y, w, h = c.window(20, 22, 116, 150, "Equipment")
    s = 18                                                  # slot size in this window
    cx = x + w / 2 - s / 2
    c.rect(cx, y + 6, s, s, SLOT, QColor("#3a332a"), radius=2, width=0.8)                 # helmet
    c.armor_item(cx, y + 6 + s + 4, s, 2 * s + 1.5)                                        # worn armor
    c.rect(cx - s - 10, y + 6 + s + 4, s, 2 * s + 1.5, SLOT, QColor("#3a332a"), radius=2, width=0.8)  # weapon
    c.rect(cx + s + 10, y + 6 + s + 4, s, s, SLOT, QColor("#3a332a"), radius=2, width=0.8)  # shield
    c.rect(cx, y + 6 + 3 * s + 10, s, s, SLOT, QColor("#3a332a"), radius=2, width=0.8)     # shoes
    c.text(cx - 14, y + 6 + 3 * s + 10 + s + 2, s + 28, 10, "worn armor", size=5.2)
    c.badge(cx + s + 19, y + 6 + 2 * s + 30, False)
    c.caption(16, 176, 124, "not the worn one", BAD, 6.6)
    # the spare armor, in the inventory: this one
    filled = lambda r, col: (r * 5 + col) % 3 != 0 and r < 6 and not (col == 2 and r in (1, 2))
    c.inventory(186, 22, pages=4, active=1, rows=8, scale=1.15, filled=filled)
    ax, ay, aw, ah = c.cell(2, 1, 1, 2)                     # column 3, rows 2 and 3
    c.armor_item(ax, ay, aw, ah, hot=True)
    c.point_at(ax + aw, ay + ah / 2, "1", "right")
    # its size: one column wide, two rows tall
    bx = ax - 4
    c.p.setPen(_pen(MARK, 1.0))
    c.p.drawLine(QPointF(bx, ay), QPointF(bx, ay + ah))
    c.p.drawLine(QPointF(bx - 2, ay), QPointF(bx + 1, ay))
    c.p.drawLine(QPointF(bx - 2, ay + ah), QPointF(bx + 1, ay + ah))
    c.caption(150, 196, 190, "spare armor: 1 column x 2 rows", OK, 6.6)
    c.text(150, 212, 190, 70, "It fills two slots, one above the other. Click anywhere on it.",
           size=6.0, color=QColor(THEME["text_dim"]))
    c.text(350, 20, 160, 270,
           "Armor mode right-clicks a spare armor in the inventory: the game puts it on and moves the "
           "worn one into the same two slots. The next right-click swaps them back. That swap ends the "
           "fishing animation sooner.\n\nSo set the position of a spare armor in the inventory, never the "
           "armor you wear.\n\nHorse mode needs no position: it presses Ctrl + the mount key.",
           size=6.9, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_fish_handling(c: Canvas) -> None:
    c.screen(10, 10, 240, 280)
    c.inventory(40, 22, pages=4, active=1, rows=7, scale=1.7, filled=lambda r, col: False)
    blue = QColor(THEME["blue"])
    fish = [(0, 0, QColor("#d9b44a"), OK), (1, 0, QColor("#7fb2d9"), blue), (2, 0, QColor("#d9b44a"), OK),
            (3, 0, QColor("#7fb2d9"), blue), (4, 0, QColor("#d97a5a"), BAD), (0, 1, QColor("#d9b44a"), OK)]
    for col, row, body, ring in fish:
        x, y, w, h = c.cell(col, row)
        c.fish_item(x, y, w, body)
        c.rect(x - 1, y - 1, w + 2, h + 2, None, ring, radius=3, width=1.6)
    # the dropped one leaves the inventory
    x, y, w, h = c.cell(4, 0)
    c.arrow(x + w + 3, y + h / 2, 205, 200, BAD, 1.5)
    c.text(160, 204, 86, 30, "dropped on the ground", size=6.2, color=BAD)
    rows = [("keep", OK, "stays in the inventory"), ("open", blue, "right-clicked to open it"),
            ("drop", BAD, "dropped (needs Confirm)")]
    for i, (name, col, what) in enumerate(rows):
        y = 30 + i * 40
        c.rect(265, y, 60, 24, col, None, radius=4)
        c.text(265, y, 60, 24, name.upper(), size=7.5, color=QColor("#0d0d0d"), bold=True)
        c.text(332, y, 180, 24, what, size=7.0, align=Qt.AlignLeft | Qt.AlignVCenter)
    c.text(265, 150, 245, 140,
           "Items are recognized by their icon: shape and colors. An item of the same shape in other "
           "colors (another item, or a fish in a different state) is not taken for it and stays put. "
           "Items the bot does not know, like your armor, are never touched.",
           size=7.0, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_set_position(c: Canvas) -> None:
    # the bot's window, Positions tab
    x, y, w, h = c.app_window(14, 30, 196, 150)
    c.text(x, y, w, 12, "POSITIONS", size=5.6, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft)
    c.bot_pill(x, y + 16, w, "Page 1", "set")
    c.bot_pill(x, y + 40, w, "Confirm", "unset")
    c.bot_pill(x, y + 64, w, "Spare Armor", "capturing")
    c.point_at(x + w * 0.3, y + 73, "1", "below")
    c.text(x, y + 104, w, 28, "Now click the spare armor in the game window. Esc cancels.",
           size=5.2, color=MARK, align=Qt.AlignLeft | Qt.AlignTop)
    # the game
    c.screen(230, 14, 280, 196)
    c.inventory(400, 24, pages=4, active=1, rows=8, scale=1.1,
                filled=lambda r, col: (r * 5 + col) % 4 == 1 and not (col == 1 and r in (2, 3)))
    ax, ay, aw, ah = c.cell(1, 2, 1, 2)
    c.armor_item(ax, ay, aw, ah, hot=True)
    c.cursor(ax + aw * 0.5, ay + ah * 0.35)
    c.point_at(ax, ay + ah / 2, "2", "left")
    c.arrow(214, 112, 372, 112, QColor(THEME["text_dim"]), 1.2)
    c.text(240, 70, 140, 30, "the game window comes to the front", size=5.8, color=QColor(THEME["text_dim"]))
    c.text(14, 218, 496, 80,
           "1  Click the position in the bot: it turns orange and waits.\n"
           "2  Click that point in the game window. The bot stores it relative to the window, so it works "
           "wherever the window is - but only at this window size.\n"
           "Esc or a right click cancels; so does a minute without a click. Click a set position to set it "
           "again, its x to clear it.",
           size=6.8, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_bait(c: Canvas) -> None:
    c.screen(10, 10, 500, 120)
    c.caption(20, 16, 300, "the hotbar in the game", TEXT, 6.6)
    labels = ["1", "2", "3", "4", "F1", "F2", "F3", "F4"]
    bait = {"1", "2", "3"}
    x0 = 40
    for i, k in enumerate(labels):
        x = x0 + i * 52 + (14 if i >= 4 else 0)
        c.key(x, 46, k, w=40, hot=k in bait, sub="200 bait" if k in bait else "")
        if k in bait:
            c.fish_item(x + 13, 70, 14, QColor("#c98f5a"))
    for i in range(2):
        x1 = x0 + i * 52 + 42
        c.arrow(x1, 57, x1 + 8, 57, MARK, 1.2)
    c.text(40, 96, 440, 24, "keys are used in order: 1 until it is empty, then 2, then 3",
           size=6.4, color=MARK, align=Qt.AlignLeft)
    # the bot's settings
    x, y, w, h = c.app_window(10, 140, 250, 150)
    c.text(x, y, w, 12, "FISHING TAB - BAIT", size=5.6, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft)
    for i, k in enumerate(labels):
        bx, by = x + (i % 4) * 58, y + 18 + (i // 4) * 20
        c.rect(bx, by + 3, 9, 9, QColor(THEME["accent"]) if k in bait else QColor("#101010"),
               QColor(THEME["line"]), radius=2)
        c.text(bx + 13, by, 40, 14, k, size=6.0, align=Qt.AlignLeft | Qt.AlignVCenter)
    c.text(x, y + 64, w, 14, "Bait per key: 200", size=6.2, align=Qt.AlignLeft)
    c.text(x, y + 84, w, 30, "3 keys x 200 = 600 casts, then the bot stops.", size=6.0,
           color=OK, align=Qt.AlignLeft | Qt.AlignTop)
    c.text(275, 145, 235, 150,
           "Tick the same keys that hold bait in the game, and how much bait one key holds.\n\n"
           "The bot counts down per cast. After restocking, press Reset Bait on the Dashboard.",
           size=7.0, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_quick_skip(c: Canvas) -> None:
    # Horse
    c.caption(10, 4, 245, "Horse: no position needed", OK)
    c.screen(10, 22, 245, 150)
    c.key(52, 70, "Ctrl", w=40)
    c.text(96, 70, 16, 22, "+", size=9)
    c.key(114, 70, "G", w=26, hot=True)
    c.text(20, 112, 225, 50, "Ctrl + the mount key (G by default): mount, dismount.",
           size=6.4, align=Qt.AlignHCenter | Qt.AlignTop)
    # Armor
    c.caption(265, 4, 245, "Armor: a spare armor", OK)
    c.screen(265, 22, 245, 150)
    c.inventory(300, 30, pages=4, active=1, rows=7, scale=1.0,
                filled=lambda r, col: (r + col) % 3 == 0 and not (col == 3 and r in (1, 2)))
    ax, ay, aw, ah = c.cell(3, 1, 1, 2)
    c.armor_item(ax, ay, aw, ah, hot=True)
    c.point_at(ax + aw, ay + ah / 2, "", "right")
    c.text(400, 64, 106, 70, "right-click: it swaps with the worn armor\n(1 column x 2 rows)",
           size=5.8, align=Qt.AlignLeft | Qt.AlignTop)
    c.text(10, 186, 500, 110,
           "Quick skip ends the fishing animation after a catch, so the next cast comes sooner. "
           "Turn it on in the FISHING tab under Quick Skip and pick one:\n\n"
           "Horse - you need a horse to mount (Ctrl + mount key).\n"
           "Armor - you need a second armor in the inventory: set Spare Armor on the Positions tab while "
           "the game shows its page.",
           size=7.0, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_jigsaw_grid(c: Canvas) -> None:
    c.screen(10, 10, 320, 280)
    gx, gy, gw, gh = c.puzzle_window(40, 50, scale=2.0, filled=(0, 1, 6, 7, 12, 19))
    pen = QPen(OK, 2.0)
    pen.setStyle(Qt.DashLine)
    c.p.setPen(pen)
    c.p.setBrush(Qt.NoBrush)
    c.p.drawRect(QRectF(gx - 1, gy - 1, gw + 2, gh + 2))
    for k in range(1, 6):
        c.p.drawLine(QPointF(gx + k * gw / 6, gy), QPointF(gx + k * gw / 6, gy + gh))
    for k in range(1, 4):
        c.p.drawLine(QPointF(gx, gy + k * gh / 4), QPointF(gx + gw, gy + k * gh / 4))
    c.caption(20, 26, 300, "ghost grid exactly on the 6 x 4 cells", OK, 7.0)
    c.text(345, 20, 165, 270,
           "The bot finds the Fish Puzzle window by itself in every game window, wherever it is, "
           "and takes the grid and both crate slots from it.\n\nOnly if it reports that it cannot "
           "find the window: press Define Grid and drag the green ghost grid until its lines lie on "
           "the cells' borders (outer edge on the outer edge of the 24 cells).",
           size=7.2, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_jigsaw_confirm(c: Canvas) -> None:
    c.screen(10, 10, 320, 280)
    c.puzzle_window(18, 18, scale=0.9)
    x, y = c.confirm_dialog(170, 150, "Open the puzzle crate?", w=150)
    c.point_at(x, y + 8, "1", "below")
    c.text(345, 20, 165, 270,
           "Open a crate by hand once: the game asks whether to use it. While that dialog is on screen, "
           "press Set Confirm and click its Yes button.\n\nThe bot clicks this point to confirm "
           "every crate and every discard.",
           size=7.2, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_jigsaw_layout(c: Canvas) -> None:
    # wrong: puzzle window in the middle, the dialog over its grid
    c.caption(10, 4, 245, "Puzzle window in the middle", BAD)
    c.screen(10, 22, 245, 160)
    c.puzzle_window(78, 60, scale=1.1)
    yes = c.confirm_dialog(132, 102, "Use it?")
    c.badge(244, 190, False)
    c.text(10, 202, 245, 40, "The game opens its dialog in the middle of the screen: its Yes button lands on the grid.",
           size=6.6, color=QColor(THEME["text_dim"]))
    # right: puzzle window top left, dialog in the free middle
    c.caption(265, 4, 245, "Puzzle window at the top left", OK)
    c.screen(265, 22, 245, 160)
    c.puzzle_window(269, 26, scale=0.58)
    c.confirm_dialog(387.5, 112, "Use it?", w=86)
    c.badge(499, 190, True)
    c.text(265, 202, 245, 40, "The dialog opens on free ground: Yes is clear of the grid and the crate slots.",
           size=6.6, color=QColor(THEME["text_dim"]))
    c.text(10, 246, 500, 54,
           "Move the puzzle window away from the centre of the game screen, then press Set Confirm "
           "again. The bot finds the window wherever you put it.",
           size=7.2, align=Qt.AlignLeft | Qt.AlignTop)


def draw_jigsaw_crates(c: Canvas) -> None:
    c.screen(10, 10, 330, 280)
    gx, gy, gw, gh = c.puzzle_window(24, 40, scale=1.4)
    cell = 13 * 1.4
    c.text(gx + 6 * cell + 4, gy + 1.2 * cell - 24, 56, 12, "special", size=6.0, color=MARK)
    c.text(gx + 6 * cell + 4, gy + 2.9 * cell + 11, 56, 12, "normal", size=6.0, color=MARK)
    c.inventory(250, 20, pages=4, active=2, rows=8)
    c.crate(250 + 7 + 3 * 12.5 + 5.5, 32 + 20 + 3 * 12.5 + 5.5, special=False)
    c.arrow(285, 110, 165, 115, MARK)
    c.text(345, 20, 165, 270,
           "The crate slots must hold crates. When one runs empty the bot looks for more crates in "
           "the inventory pages you set on the Positions tab, and puts them in.\n\nKeep the inventory "
           "open for that, and set its page tabs.",
           size=7.2, color=QColor(THEME["text_dim"]), align=Qt.AlignLeft | Qt.AlignTop)


def draw_quick_tips(c: Canvas) -> None:
    c.screen(10, 10, 500, 280)
    c.text(30, 30, 460, 240,
           "F5 pauses and resumes every bot.\nEsc cancels picking a coordinate.\n\n"
           "Run the bot as administrator on Windows, and keep Windows display scaling at 100%.\n\n"
           "Put bait in the hotkey slots you selected.\n\n"
           "Something not working? Turn on diagnostics in Settings, reproduce it, and send the "
           "zip that is written when the bots stop.",
           size=8.0, align=Qt.AlignLeft | Qt.AlignTop)


# ---------------------------------------------------------------- the topics

TOPICS: Dict[str, dict] = {
    "windows": dict(
        title="Game windows",
        steps=["Pick your game windows on the Dashboard (Refresh if one is missing).",
               "Give all of them the same window size.",
               "Place them so none covers another, and never minimize them."],
        draw=draw_windows),
    "inventory_tabs": dict(
        title="Inventory page tabs",
        steps=["Open the inventory in the game.",
               "Positions tab: click Page 1, then tab I in the game. Then Page 2 and tab II, and so on.",
               "Click on each tab beside its numeral, not on the letters."],
        draw=draw_inventory_tabs),
    "inventory_open": dict(
        title="Keep the inventory open",
        steps=["Leave the inventory open at the place where you set its tabs.",
               "Don't move it, and keep tooltips, shops and other windows off it while the bot runs.",
               "Moved it? Set the page tabs again."],
        draw=draw_inventory_open),
    "drop_confirm": dict(
        title="Drop / Sell and Confirm",
        steps=["Drop one fish on the ground by hand and see which dialogs your client shows.",
               "Only if a drop / sell dialog comes first: click Drop / Sell in the bot, then that button. "
               "Clients without this dialog skip this step and leave Drop / Sell empty.",
               "On the yes/no dialog: click Confirm in the bot, then its Yes button. Needed when a fish "
               "is set to drop, and always for Jigsaw."],
        draw=draw_drop_confirm),
    "set_position": dict(
        title="How to set a position",
        steps=["Select your game window on the Dashboard first: positions are measured in it.",
               "Click the position in the bot (it turns orange), then click that point in the game window.",
               "Esc or a right click cancels. Positions belong to one window size: after resizing the "
               "game window, set them again."],
        draw=draw_set_position),
    "bait": dict(
        title="Bait keys",
        steps=["Put bait on hotbar keys in the game (1-4 or F1-F4).",
               "FISHING tab: tick the same keys and enter how much bait one key holds.",
               "The bot uses the keys in order and stops when all are counted empty. After restocking, "
               "press Reset Bait on the Dashboard."],
        draw=draw_bait),
    "quick_skip": dict(
        title="Quick skip (horse or armor)",
        steps=["FISHING tab: turn on Quick Skip and pick Horse or Armor.",
               "Horse: have a horse to mount; the bot presses Ctrl + the mount key (G by default; the FISHING tab changes it).",
               "Armor: put a second armor in the inventory and set Spare Armor (next topic)."],
        draw=draw_quick_skip),
    "armor_slot": dict(
        title="Spare armor (quick skip)",
        steps=["Wear an armor, and put a second (spare) armor in the inventory. An armor takes two "
               "slots: one column wide, two rows tall.",
               "Set the page tabs first, then show the armor's page in the game.",
               "Positions tab: click Spare Armor, then the spare armor in the inventory - not the worn one.",
               "Leave the spare armor where it is: the bot right-clicks it after every catch, "
               "switching to its page and back when it fills another page."],
        draw=draw_armor_slot),
    "fish_handling": dict(
        title="What happens to each fish",
        steps=["Fishing tab: turn on Automatic Fish Handling and press Configure Fish and Items.",
               "Choose keep, drop or open for each fish, and keep or drop for each item.",
               "Drop needs the Confirm coordinate (and Drop / Sell if your client asks drop or sell first)."],
        draw=draw_fish_handling),
    "jigsaw_grid": dict(
        title="Jigsaw: the puzzle grid",
        steps=["Open the Fish Puzzle window in every game client, away from the middle of the screen.",
               "The bot finds it by itself in each client, with or without pieces on it.",
               "Only if it reports it cannot find the window: press Define Grid and lay the ghost grid "
               "exactly on the 24 cells."],
        draw=draw_jigsaw_grid),
    "jigsaw_confirm": dict(
        title="Jigsaw: the Confirm point",
        steps=["Open a crate by hand so the game asks to confirm.",
               "Press Set Confirm and click the dialog's Yes button."],
        draw=draw_jigsaw_confirm),
    "jigsaw_layout": dict(
        title="Jigsaw: window placement",
        steps=["Move the Fish Puzzle window away from the middle of the screen (top left works well).",
               "Open a crate by hand and press Set Confirm again.",
               "If you defined the grid by hand, press Define Grid again."],
        draw=draw_jigsaw_layout),
    "jigsaw_crates": dict(
        title="Jigsaw: crates",
        steps=["Keep crates in the two crate slots, or in the inventory.",
               "Set the inventory page tabs and keep the inventory open, so empty slots get refilled."],
        draw=draw_jigsaw_crates),
    "quick_tips": dict(
        title="Good to know",
        steps=["F5 pauses and resumes everything.",
               "Problems? Turn on diagnostics in Settings and send the zip."],
        draw=draw_quick_tips),
}

# The order of the full guide
GUIDE_ORDER: List[str] = ["windows", "set_position", "inventory_tabs", "inventory_open", "bait",
                          "fish_handling", "drop_confirm", "quick_skip", "armor_slot", "jigsaw_grid",
                          "jigsaw_confirm", "jigsaw_layout", "jigsaw_crates", "quick_tips"]

# Fishing bot stop reasons the user can fix, and the topic that shows how
STOP_REASON_TOPIC: Dict[str, str] = {
    "capture_overlap": "windows",
    "capture_unavailable": "windows",
    "window_unavailable": "windows",
    "inventory_not_visible": "inventory_open",
    "inventory_full": "inventory_tabs",
    "game_screen_lost": "inventory_open",
    "drop_failing": "drop_confirm",
    "catch_not_recognized": "fish_handling",
}


# ---------------------------------------------------------------- widgets

def fit_to_screen(dialog: QWidget, frame_w: int = 16, frame_h: int = 48) -> None:
    """Caps a dialog's size, and its minimum size, to the free area of the
    screen it opens on (its parent's), leaving room for the title bar. On a
    very short screen (the 0.7 interface scale floor) the main window is
    already shortened; without this its dialogs open with their Next, Save
    or Close buttons below the screen. The bodies scroll or shrink."""
    parent = dialog.parentWidget()
    screen = ((parent.screen() if parent is not None else None) or dialog.screen()
              or QGuiApplication.primaryScreen())
    if screen is None:
        return
    avail = screen.availableGeometry()
    max_w = max(320, avail.width() - frame_w)
    max_h = max(320, avail.height() - frame_h)
    mn = dialog.minimumSize()
    if mn.width() > max_w or mn.height() > max_h:
        dialog.setMinimumSize(min(mn.width(), max_w), min(mn.height(), max_h))
    size = dialog.size()
    if size.width() > max_w or size.height() > max_h:
        dialog.resize(min(size.width(), max_w), min(size.height(), max_h))

class Illustration(QWidget):
    """Draws one topic's picture, scaled to the widget, keeping its aspect."""

    def __init__(self, draw: Callable[[Canvas], None], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._draw = draw
        self.setMinimumSize(390, 225)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def heightForWidth(self, w: int) -> int:
        return int(w * H / W)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        scale = min(self.width() / W, self.height() / H)
        p.translate((self.width() - W * scale) / 2, (self.height() - H * scale) / 2)
        p.scale(scale, scale)
        p.fillRect(QRectF(0, 0, W, H), QColor(THEME["bg_2"]))
        self._draw(Canvas(p))
        p.end()


def _topic_page(key: str, problem: Optional[str] = None) -> QWidget:
    topic = TOPICS[key]
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(10)
    title = QLabel(topic["title"])
    title.setStyleSheet(f"color: {THEME['accent']}; font-family: {MONO}; font-size: 15px; font-weight: 700;")
    lay.addWidget(title)
    if problem:
        box = QLabel(problem)
        box.setObjectName("HelpProblem")
        box.setWordWrap(True)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.setStyleSheet(
            f"color: {THEME['text']}; background: rgba(229, 83, 75, 0.12); border: 1px solid {THEME['red']}; "
            f"border-radius: 6px; padding: 8px; font-family: {MONO}; font-size: 12px;")
        lay.addWidget(box)
    lay.addWidget(Illustration(topic["draw"]), 1)
    steps = QLabel("".join(f"<p style='margin:3px 0'><b style='color:{THEME['accent']}'>{i}.</b> {s}</p>"
                           for i, s in enumerate(topic["steps"], 1)))
    steps.setObjectName("HelpSteps")
    steps.setTextFormat(Qt.RichText)
    steps.setWordWrap(True)
    steps.setStyleSheet(f"color: {THEME['text']}; font-family: {MONO}; font-size: 12px;")
    lay.addWidget(steps)
    return page


class HelpDialog(QDialog):
    """One topic (with the problem that brought it up), or the whole guide
    with a list of topics on the left."""

    def __init__(self, parent: Optional[QWidget], topics: List[str], start: Optional[str] = None,
                 problem: Optional[str] = None, title: str = "Guide"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(880 if len(topics) > 1 else 700, 640)
        self.setStyleSheet(f"QDialog {{ background: {THEME['bg']}; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QHBoxLayout()
        body.setSpacing(0)
        self.topics = list(topics)
        self.stack = QStackedWidget()
        for key in self.topics:
            self.stack.addWidget(_topic_page(key, problem if key == (start or self.topics[0]) else None))
        self.list = None
        if len(self.topics) > 1:
            self.list = QListWidget()
            self.list.setFixedWidth(240)
            self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.list.setWordWrap(True)
            self.list.setStyleSheet(
                f"QListWidget {{ background: {THEME['bg_2']}; color: {THEME['text']}; border: none; "
                f"border-right: 1px solid {THEME['line']}; font-family: {MONO}; font-size: 12px; }} "
                f"QListWidget::item {{ padding: 8px 10px; }} "
                f"QListWidget::item:selected {{ background: {THEME['line']}; color: {THEME['accent']}; }}")
            for i, key in enumerate(self.topics, 1):
                self.list.addItem(f"{i}. {TOPICS[key]['title']}")
            self.list.currentRowChanged.connect(self.stack.setCurrentIndex)
            body.addWidget(self.list)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        footer = QFrame()
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14, 8, 14, 10)
        fl.addStretch(1)
        if self.list is not None:
            self.back_btn = QPushButton("Back")
            self.next_btn = QPushButton("Next")
            self.back_btn.clicked.connect(lambda: self._go(-1))
            self.next_btn.clicked.connect(lambda: self._go(1))
            fl.addWidget(self.back_btn)
            fl.addWidget(self.next_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        fl.addWidget(close)
        outer.addWidget(footer)
        self.show_topic(start or self.topics[0])
        fit_to_screen(self)

    def show_topic(self, key: str) -> None:
        idx = self.topics.index(key) if key in self.topics else 0
        self.stack.setCurrentIndex(idx)
        if self.list is not None:
            self.list.setCurrentRow(idx)

    def _go(self, step: int) -> None:
        idx = max(0, min(len(self.topics) - 1, self.stack.currentIndex() + step))
        self.show_topic(self.topics[idx])


def show_help(parent: Optional[QWidget], topic: str, problem: Optional[str] = None,
              title: Optional[str] = None, modal: bool = True) -> HelpDialog:
    """One topic, with what went wrong on top. Modal unless ``modal=False``
    (then the dialog is shown and returned, and the caller keeps it alive)."""
    dlg = HelpDialog(parent, [topic], problem=problem, title=title or TOPICS[topic]["title"])
    if modal:
        dlg.exec()
    else:
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
    return dlg


def open_guide(parent: Optional[QWidget], start: Optional[str] = None, modal: bool = True) -> HelpDialog:
    dlg = HelpDialog(parent, GUIDE_ORDER, start=start, title="How to use Fishing Puzzle Player")
    if modal:
        dlg.exec()
    else:
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
    return dlg

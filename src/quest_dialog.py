"""Finds the game's cinematic quest dialog in a frame of the game window.

The Golden Tuna (official and some private servers) opens one right after a
won minigame. The game hides the whole HUD, slides black bars over the top
and bottom of the window, and draws a dark board with answer bars in it:

    page 1: Let it go! / Cut it open! / Use it for bait!   (3 bars)
    page 2: what happened, and one OK bar                  (1 bar)

Nothing here reads text: the answers differ per language, but their order
comes from the quest and does not. The dialog is found by its shape only:

- black rows at the top and bottom of equal height (the letterbox),
- the board outline: a gray line with a black line inside it, all round,
  centered in the window,
- the answer bars: lighter rectangles centered on the board, with a sharp
  left and right edge.

The bars are found each time, not at fixed places: they sit under the text,
so a longer text (another outcome, another language) pushes them down.

Measured at 800x600 (tests/fixtures/golden_tuna): letterbox 72 rows each,
board x 50-749 y 150-449, option bars x 120-679 26 rows tall 32 apart, the OK
bar x 260-539. When the answer that closes the dialog is clicked the board
goes at once and the black bars slide away in about 0.4 s.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

Rect = Tuple[int, int, int, int]   # x1, y1, x2, y2, inclusive

# Rows darker than this count as letterbox (the game draws pure black: 0)
_BLACK = 8
# The board outline: a gray line (85) next to a black one (0), both colorless
_LINE_MIN, _LINE_MAX = 60, 120
_LINE_DARK = 15
_LINE_GRAY_SPREAD = 25          # max - min channel of the gray line
_LINE_COVER = 0.85              # share of the edge that must show the line pair
# Answer bars: brighter than the board by about 28 (52 when hovered)
_BAR_EDGE = 14                  # step at the left/right edge of a bar
_BAR_LIFT = 12                  # bar inside vs board just above/below it
_BAR_MIN_H, _BAR_MAX_H = 14, 60
_BAR_GAP = 3                    # rows a bar edge may be hidden (a name tag behind it)
_BAR_COVER = 0.75


@dataclass(frozen=True)
class QuestDialog:
    board: Rect
    bars: Tuple[Rect, ...]          # top to bottom
    letterbox: Tuple[int, int]      # black rows at the top, at the bottom
    text_px: int                    # bright text pixels above the first bar

    def bar_center(self, index: int) -> Tuple[int, int]:
        x1, y1, x2, y2 = self.bars[index]
        return (x1 + x2) // 2, (y1 + y2) // 2

    def same_page(self, other: Optional["QuestDialog"]) -> bool:
        """The same page still shows (the click did not take). Hovering a bar
        changes neither its place nor the text above it."""
        if other is None or other.bars != self.bars:
            return False
        tolerance = max(30, 0.15 * max(self.text_px, other.text_px))
        return abs(self.text_px - other.text_px) <= tolerance


def letterbox(frame: np.ndarray) -> Tuple[int, int]:
    """Black rows at the top and at the bottom of the frame (BGR or BGRA)."""
    if frame is None or frame.ndim != 3 or frame.size == 0:
        return 0, 0
    row_max = frame[:, :, :3].max(axis=(1, 2))
    dark = row_max <= _BLACK
    h = len(dark)
    top = h if dark.all() else int(np.argmin(dark))
    bottom = h if dark.all() else int(np.argmin(dark[::-1]))
    return top, bottom


def letterbox_showing(frame: np.ndarray) -> bool:
    """Any black bar left at the top and bottom (the dialog closing)."""
    top, bottom = letterbox(frame)
    h = frame.shape[0] if frame is not None and frame.ndim == 3 else 0
    return h > 0 and 2 <= top < h // 3 and 2 <= bottom < h // 3


def _runs(mask: np.ndarray, gap: int):
    """(start, end) inclusive of the True runs in a 1-D mask, joining runs
    separated by at most ``gap`` False entries."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    runs = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > gap + 1:
            runs.append((start, prev))
            start = i
        prev = i
    runs.append((start, prev))
    return runs


def _board(gray: np.ndarray, line: np.ndarray, top: int, bottom: int) -> Optional[Rect]:
    """The board outline inside the area between the black bars."""
    h, w = gray.shape
    dark = gray <= _LINE_DARK
    y_lo, y_hi = top + 1, h - bottom - 2
    if y_hi - y_lo < h // 5:
        return None
    # Horizontal edges: gray line with a black line right under it, over the
    # middle of the width
    xa, xb = int(w * 0.2), int(w * 0.8)
    pair_h = line[y_lo:y_hi, xa:xb] & dark[y_lo + 1:y_hi + 1, xa:xb]
    rows = np.flatnonzero(pair_h.mean(axis=1) >= _LINE_COVER) + y_lo
    if rows.size < 2:
        return None
    by1, by2 = int(rows[0]), int(rows[-1])
    if by2 - by1 < h // 6:
        return None
    # Vertical edges: gray line with a black line right of it, over the
    # board's height
    ya, yb = by1 + 4, by2 - 4
    pair_v = line[ya:yb, :w - 1] & dark[ya:yb, 1:]
    cols = np.flatnonzero(pair_v.mean(axis=0) >= _LINE_COVER)
    if cols.size < 2:
        return None
    bx1, bx2 = int(cols[0]), int(cols[-1]) + 1     # the right edge's black line is its last column
    if bx2 - bx1 < w // 2:
        return None
    if abs((bx1 + bx2) - (w - 1)) > 8:              # centered in the window
        return None
    return bx1, by1, bx2, by2 + 1


def _bars(gray: np.ndarray, board: Rect) -> Tuple[Rect, ...]:
    """Answer bars: lighter rectangles centered on the board."""
    bx1, by1, bx2, by2 = board
    span = bx1 + bx2                     # a bar from x to span - x is centered
    y_lo, y_hi = by1 + 4, by2 - 3
    xs = np.arange(bx1 + 4, (span // 2) - 8)
    if xs.size == 0 or y_hi <= y_lo:
        return ()
    g = gray[y_lo:y_hi]
    left = (g[:, xs] - g[:, xs - 1]) >= _BAR_EDGE          # x is the bar's first column
    right_x = span - xs
    right = (g[:, right_x] - g[:, right_x + 1]) >= _BAR_EDGE  # span - x is its last column
    both = left & right
    found = []
    for col in np.flatnonzero(both.sum(axis=0) >= _BAR_MIN_H * _BAR_COVER):
        for r1, r2 in _runs(both[:, col], _BAR_GAP):
            height = r2 - r1 + 1
            if not (_BAR_MIN_H <= height <= _BAR_MAX_H):
                continue
            if both[r1:r2 + 1, col].mean() < _BAR_COVER:
                continue
            x1, x2 = int(xs[col]), int(span - xs[col])
            y1, y2 = r1 + y_lo, r2 + y_lo
            inside = gray[y1 + 2:y2 - 1, x1 + 3:x2 - 2]
            above = gray[max(by1 + 1, y1 - 4):y1 - 1, x1 + 3:x2 - 2]
            below = gray[y2 + 2:min(by2, y2 + 5), x1 + 3:x2 - 2]
            outside = np.concatenate([above.ravel(), below.ravel()])
            if inside.size == 0 or outside.size == 0:
                continue
            if np.median(inside) - np.median(outside) < _BAR_LIFT:
                continue
            found.append((x1, y1, x2, y2))
    # One bar per row range: the widest (an icon or letter edge inside a bar
    # can make a second, narrower pair)
    found.sort(key=lambda r: (r[1], r[0]))
    bars = []
    for rect in found:
        if bars and rect[1] <= bars[-1][3]:
            if rect[2] - rect[0] > bars[-1][2] - bars[-1][0]:
                bars[-1] = rect
            continue
        bars.append(rect)
    return tuple(bars)


def find(frame: Optional[np.ndarray]) -> Optional[QuestDialog]:
    """The quest dialog in a BGR(A) frame of the whole game window, or None."""
    if frame is None or frame.ndim != 3 or frame.shape[0] < 200 or frame.shape[1] < 300:
        return None
    h, w = frame.shape[:2]
    top, bottom = letterbox(frame)
    least = max(20, int(h * 0.06))
    if not (least <= top < h // 3 and least <= bottom < h // 3):
        return None
    if abs(top - bottom) > max(4, int(h * 0.02)):
        return None                       # still sliding in, or not a letterbox
    bgr = frame[:, :, :3].astype(np.int16)
    gray = bgr.mean(axis=2)
    spread = bgr.max(axis=2) - bgr.min(axis=2)
    line = (gray >= _LINE_MIN) & (gray <= _LINE_MAX) & (spread <= _LINE_GRAY_SPREAD)
    board = _board(gray, line, top, bottom)
    if board is None:
        return None
    bars = _bars(gray, board)
    if not bars:
        return None
    bx1, by1, bx2, _ = board
    text = gray[by1 + 4:bars[0][1] - 2, bx1 + 4:bx2 - 4]
    text_px = int((text >= 170).sum()) if text.size else 0
    return QuestDialog(board=board, bars=bars, letterbox=(top, bottom), text_px=text_px)

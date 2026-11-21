"""Where the inventory is inside the game window, and which page it shows.

Both are worked out from the configured inventory page tabs
(``inv_page_N_pos``, window-relative): the item grid sits right below the
tabs, and the tab of the page on screen is drawn differently from the others.

All coordinates are window-relative, in the frame of reference of
``WindowManager.get_window_rect`` (title bar included on Windows, client area
on Linux); the tab positions were captured in that same frame, so nothing here
depends on the platform.
"""

import time
from typing import Callable, Dict, Optional, Tuple

import numpy as np

Point = Tuple[int, int]
Rect = Tuple[int, int, int, int]          # x, y, width, height

GRID_WIDTH = 160        # 5 slots of 32 px
SLOT = 32
GRID_ROWS = 9           # rows of a page (config: inventory_rows)
TOP_MARGIN = 4          # strip starts this far below the lowest tab position
GRID_GAP_MAX = 20       # the grid's top edge lies at most this far below the lowest tab position
SIDE_MARGIN = 8
BOTTOM_MARGIN = 30      # the strip ends this far above the window's bottom
MIN_WIDTH, MIN_HEIGHT = GRID_WIDTH, 64

FALLBACK_WIDTH = 200    # no tabs configured: the right-hand 200 px ...
FALLBACK_TOP = 200      # ... from y=200 down

TAB_PATCH = 4           # the tab is judged on a 9x9 px patch around its position
TAB_MIN_CONTRAST = 18   # how far the active tab's color must be from the others
TAB_MAX_OTHERS = 0.35   # ... and how close to each other the others must stay


def tab_points(config: dict, win_w: Optional[int] = None, win_h: Optional[int] = None) -> Dict[int, Point]:
    """The configured page tabs, {page number: (x, y)}. Positions that are
    not two numbers, or lie outside the window, are left out."""
    points: Dict[int, Point] = {}
    for page in range(1, 9):
        value = config.get(f"inv_page_{page}_pos")
        try:
            if not value or len(value) != 2:
                continue
            x, y = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            continue
        if x < 0 or y < 0 or (win_w is not None and x >= win_w) or (win_h is not None and y >= win_h):
            continue
        points[page] = (x, y)
    return points


def fixed_strip(win_w: int, win_h: int, width: int = FALLBACK_WIDTH, top: int = FALLBACK_TOP) -> Rect:
    """The strip used when no tab is configured: the right-hand side of the window."""
    return (win_w - width, top, width, win_h - top - BOTTOM_MARGIN)


def strip_rect(config: dict, win_w: int, win_h: int, fallback_width: int = FALLBACK_WIDTH,
               fallback_top: int = FALLBACK_TOP) -> Tuple[Rect, str]:
    """The rectangle to capture for the inventory grid, and where it came
    from ("tabs" or "fixed").

    With tabs configured it starts just below the lowest tab and is as wide
    as the grid can be given where the tabs are: every tab lies above the
    grid, so the grid cannot reach further than its own width from any of
    them; with pages 1-4 all set, the outer tabs are also known to sit in
    the outer half of the grid. It is as tall as the grid (``inventory_rows``
    rows of 32 px, 9 by default) can be: slot-like UI further down must not
    count as free room on the page."""
    points = tab_points(config, win_w, win_h)
    if points:
        xs = [p[0] for p in points.values()]
        ys = [p[1] for p in points.values()]
        left, right = max(xs) - GRID_WIDTH, min(xs) + GRID_WIDTH
        if all(page in points for page in (1, 2, 3, 4)):
            left = max(left, min(xs) - GRID_WIDTH // 2)
            right = min(right, max(xs) + GRID_WIDTH // 2)
        left = max(0, left - SIDE_MARGIN)
        right = min(win_w, right + SIDE_MARGIN)
        top = max(ys) + TOP_MARGIN
        try:
            rows = max(1, int(config.get("inventory_rows", GRID_ROWS)))
        except (TypeError, ValueError):
            rows = GRID_ROWS
        grid_bottom = max(ys) + GRID_GAP_MAX + rows * SLOT + 4
        bottom = min(win_h - BOTTOM_MARGIN, grid_bottom)
        if right - left >= MIN_WIDTH and bottom - top >= MIN_HEIGHT:
            return (left, top, right - left, bottom - top), "tabs"
    return fixed_strip(win_w, win_h, fallback_width, fallback_top), "fixed"


def tabs_rect(points: Dict[int, Point], win_w: int, win_h: int) -> Optional[Rect]:
    """The small rectangle that holds every tab's patch (None without tabs)."""
    if not points:
        return None
    xs = [p[0] for p in points.values()]
    ys = [p[1] for p in points.values()]
    x1, y1 = max(0, min(xs) - TAB_PATCH), max(0, min(ys) - TAB_PATCH)
    x2, y2 = min(win_w, max(xs) + TAB_PATCH + 1), min(win_h, max(ys) + TAB_PATCH + 1)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


def active_tab(frame: np.ndarray, origin: Point, points: Dict[int, Point]) -> Tuple[Optional[int], str]:
    """Which tab is drawn as the active one in ``frame`` (a capture whose
    top-left corner is ``origin`` in the window).

    The active tab is the one whose color stands out while all the others
    look alike. Each tab is reduced to the median color of a small patch, so
    the label under the click position does not count. Returns (page number
    or None, what was measured). None when no tab stands out, or more than
    one does (a tab under the cursor is drawn brighter too, and on a slow
    machine the old page's tab is still lit for a moment)."""
    if len(points) < 3:
        return None, "fewer than 3 tabs configured"
    if frame is None or frame.ndim != 3:
        return None, "no frame"
    pages = sorted(points)
    colors = []
    for page in pages:
        x, y = points[page][0] - origin[0], points[page][1] - origin[1]
        patch = frame[max(0, y - TAB_PATCH):y + TAB_PATCH + 1, max(0, x - TAB_PATCH):x + TAB_PATCH + 1]
        if patch.size == 0:
            return None, f"tab {page} is outside the capture"
        colors.append(np.median(patch.reshape(-1, patch.shape[2]), axis=0))
    colors = np.array(colors, dtype=np.float32)
    distance = np.abs(colors - np.median(colors, axis=0)).max(axis=1)
    order = np.argsort(distance)[::-1]
    top, second = float(distance[order[0]]), float(distance[order[1]])
    measured = "tab contrast " + " ".join(f"{p}:{d:.0f}" for p, d in zip(pages, distance))
    if top < TAB_MIN_CONTRAST:
        return None, measured + " (no tab stands out)"
    if second > TAB_MAX_OTHERS * top:
        return None, measured + " (more than one tab stands out)"
    return pages[int(order[0])], measured


SLOT_MATCH_TOL = 6     # px two empty-slot positions may differ and still be the same slot


def same_empty_slots(before, after, allowed: int = 1) -> Tuple[bool, int]:
    """Whether two empty-slot scans of the inventory strip look like the
    same page: after a tab click, the page did not switch. Returns (same,
    number of slots found in only one of them).

    Empty slots, not pixels: the strip's left part can show the game world
    behind the inventory, where characters move. ``allowed`` slots may
    differ, for an item landing during the switch. Two different pages with
    nearly the same free slots read as "same"; the caller then clicks the
    tab once more, which does nothing when its page is already showing."""
    before, after = list(before or []), list(after or [])
    unmatched = list(after)
    differ = 0
    for bx, by in before:
        hit = next((i for i, (ax, ay) in enumerate(unmatched)
                    if abs(ax - bx) <= SLOT_MATCH_TOL and abs(ay - by) <= SLOT_MATCH_TOL), None)
        if hit is None:
            differ += 1
        else:
            unmatched.pop(hit)
    differ += len(unmatched)
    return differ <= allowed, differ


def wait_for_tab(grab: Callable[[int, int, int, int], Optional[np.ndarray]], points: Dict[int, Point],
                 win_w: int, win_h: int, target: int, keep_going: Callable[[], bool],
                 timeout: float = 2.0, poll: float = 0.05,
                 at_half: Optional[Callable[[], None]] = None) -> Tuple[bool, float, Optional[int], str, int]:
    """Polls the tabs until ``target`` is the active one.

    ``grab`` captures a window-relative rectangle; ``at_half`` is called once
    when half the timeout has passed without the tab becoming active.
    Returns (confirmed, seconds waited, tab last seen active, last
    measurement, number of looks)."""
    rect = tabs_rect(points, win_w, win_h)
    start = time.perf_counter()
    if rect is None or target not in points:
        return False, 0.0, None, "tab not configured", 0
    active, detail, looks, half_done = None, "", 0, at_half is None
    while keep_going():
        frame = grab(*rect)
        looks += 1
        active, detail = active_tab(frame, rect[:2], points) if frame is not None else (None, "capture failed")
        waited = time.perf_counter() - start
        if active == target:
            return True, waited, active, detail, looks
        if waited >= timeout:
            break
        if not half_done and waited >= timeout / 2:
            half_done = True
            at_half()
        time.sleep(poll)
    return False, time.perf_counter() - start, active, detail, looks


def page_on_screen(config: dict, window_manager, capture_mode: str = "auto") -> Tuple[Optional[int], str]:
    """Which configured page the game window shows right now, read from its
    tabs (None when there are fewer than 3 tabs or none stands out). Used
    when a position inside a page is captured, such as the spare armor."""
    _, _, win_w, win_h = window_manager.get_window_rect()
    points = tab_points(config, win_w, win_h)
    rect = tabs_rect(points, win_w, win_h)
    if len(points) < 3 or rect is None:
        return None, f"{len(points)} page tabs configured (3 are needed to read the page)"
    from screen_capture import WindowCapture   # not needed by the pure helpers above
    capture = WindowCapture(window_manager, capture_mode)
    try:
        frame = capture.grab(*rect)
    finally:
        capture.close()
    if frame is None:
        return None, "the tabs could not be captured"
    return active_tab(frame, rect[:2], points)

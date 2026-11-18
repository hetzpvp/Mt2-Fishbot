"""Checks run while the player sets the bot up, so a position that cannot
be right is caught before Start, with what to do about it.

Each check only warns on positive evidence: when a picture cannot be
taken, or the game's look cannot be read on this client, it says nothing
rather than guess. Pure functions over a ``grab(x, y, w, h)`` callable
(window-relative, BGR or None) so the tests can drive them.
"""

import os
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

import inventory_layout

Grab = Callable[[int, int, int, int], Optional[np.ndarray]]

# Positions measured on the game window, and what the player calls them.
POSITION_LABELS: Dict[str, str] = {
    **{f"inv_page_{p}_pos": f"Page {p}" for p in range(1, 9)},
    "drop_button_pos": "Drop / Sell",
    "confirm_button_pos": "Confirm",
    "armor_slot_pos": "Spare Armor",
    "jigsaw_grid_bounds": "Jigsaw grid",
}
FISHING_POSITIONS = [k for k in POSITION_LABELS if k != "jigsaw_grid_bounds"]
JIGSAW_POSITIONS = [f"inv_page_{p}_pos" for p in range(1, 9)] + ["confirm_button_pos", "jigsaw_grid_bounds"]

SIZES_KEY = "position_window_sizes"   # config: {position key: [window width, height] when it was set]}

DUPLICATE_TAB_PX = 6        # two page tabs this close are the same tab
EMPTY_SLOT_THRESHOLD = 0.55  # as FishingBot._EMPTY_SLOT_THRESHOLD
TEMPLATE_BORDER = 7          # as FishingBot._template_border_crop


# ------------------------------------------------------------ window size

def remember_size(config: dict, key: str, size: Optional[Tuple[int, int]]) -> None:
    """Notes the window size a position was set at (None: forgets it)."""
    sizes = dict(config.get(SIZES_KEY) or {})
    if size is None:
        sizes.pop(key, None)
    else:
        sizes[key] = [int(size[0]), int(size[1])]
    config[SIZES_KEY] = sizes


def stale_positions(config: dict, keys: Iterable[str], size: Tuple[int, int]) -> List[Tuple[str, Tuple[int, int]]]:
    """The set positions in ``keys`` that were set at another window size
    than ``size``: [(key, size it was set at)]. Positions set before sizes
    were noted are not judged."""
    sizes = config.get(SIZES_KEY) or {}
    stale = []
    for key in keys:
        if not config.get(key):
            continue
        saved = sizes.get(key)
        try:
            saved = (int(saved[0]), int(saved[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if saved != (int(size[0]), int(size[1])):
            stale.append((key, saved))
    return stale


def stale_positions_message(stale: Sequence[Tuple[str, Tuple[int, int]]], size: Tuple[int, int],
                            window: str = "") -> Optional[str]:
    if not stale:
        return None
    by_size: Dict[Tuple[int, int], List[str]] = {}
    for key, saved in stale:
        by_size.setdefault(saved, []).append(POSITION_LABELS.get(key, key))
    lines = [f"  • {', '.join(names)}: set at {w}×{h}" for (w, h), names in sorted(by_size.items())]
    return (f"The game window{f' {window!r}' if window else ''} is now {size[0]}×{size[1]}, but these positions "
            f"were set at another size:\n" + "\n".join(lines) +
            "\n\nEvery position is measured from the window's corner, so after a resize the bot would click "
            "the wrong places. Set the window back to that size (same resolution / window mode in the game "
            "options), or set these positions again.")


# ------------------------------------------------------------ page tabs

def duplicate_tab(config: dict, page: int) -> Optional[int]:
    """Another page whose tab is set (almost) where ``page``'s is."""
    points = inventory_layout.tab_points(config)
    here = points.get(page)
    if here is None:
        return None
    for other, (x, y) in sorted(points.items()):
        if other != page and abs(x - here[0]) <= DUPLICATE_TAB_PX and abs(y - here[1]) <= DUPLICATE_TAB_PX:
            return other
    return None


def tab_after_click(config: dict, page: int, grab: Grab, win_w: int, win_h: int) -> Tuple[str, Optional[int], str]:
    """After the player clicked tab ``page`` in the game: is it the active one?
    ("ok" | "other" | "unreadable" | "unknown", page read, detail). "unknown"
    when fewer than 3 tabs are set or no picture could be taken: nothing to say."""
    points = inventory_layout.tab_points(config, win_w, win_h)
    if len(points) < 3 or page not in points:
        return "unknown", None, f"{len(points)} tabs set"
    rect = inventory_layout.tabs_rect(points, win_w, win_h)
    frame = grab(*rect) if rect else None
    if frame is None:
        return "unknown", None, "no picture of the tabs"
    active, detail = inventory_layout.active_tab(frame, rect[:2], points)
    if active == page:
        return "ok", active, detail
    if active is None:
        return "unreadable", None, detail
    return "other", active, detail


# ------------------------------------------------------------ inventory grid

_empty_template: Optional[np.ndarray] = None


def _empty_slot_template() -> Optional[np.ndarray]:
    global _empty_template
    if _empty_template is None:
        from utils import get_resource_path
        image = cv2.imread(os.path.join(get_resource_path("assets"), "empty_slot.jpg"))
        if image is None:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        b = TEMPLATE_BORDER
        _empty_template = gray[b:-b, b:-b] if min(gray.shape) > 2 * b else gray
    return _empty_template


def count_empty_slots(frame: Optional[np.ndarray]) -> Optional[int]:
    """Empty inventory slots in ``frame`` (None when it cannot be told)."""
    template = _empty_slot_template()
    if frame is None or template is None or frame.ndim != 3:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
        return 0
    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    count = 0
    while True:
        _, score, _, (x, y) = cv2.minMaxLoc(result)
        if score < EMPTY_SLOT_THRESHOLD or count >= 200:
            return count
        count += 1
        result[max(0, y - 10):y + 11, max(0, x - 10):x + 11] = -1.0


def strip_problem(config: dict, grab: Grab, win_w: int, win_h: int, tab_state: str) -> Optional[str]:
    """After the tabs were set: does the area the bot reads as the inventory
    show it? Only judged when the tabs did not confirm themselves (a read
    tab means they sit on the inventory) and a picture was taken."""
    if tab_state == "ok":
        return None
    rect, source = inventory_layout.strip_rect(config, win_w, win_h)
    if source != "tabs":
        return None
    empty = count_empty_slots(grab(*rect))
    if empty is None or empty > 0:
        return None
    return ("The bot sees no empty inventory slot below the page tabs you set. If the page on screen is "
            "full, that is expected. Otherwise the tabs are probably not set on the inventory's tabs (or "
            "the inventory is closed): the bot reads the slots right below them.")


# ------------------------------------------------------------ spare armor

def armor_problem(config: dict, pos: Tuple[int, int], win_w: int, win_h: int) -> Optional[str]:
    """A spare armor position outside the inventory grid is the worn armor in
    the equipment window, or not an inventory slot at all. Judged only with
    page tabs set (the grid is known from them)."""
    rect, source = inventory_layout.strip_rect(config, win_w, win_h)
    if source != "tabs":
        return None
    x, y, w, h = rect
    if x <= pos[0] < x + w and y <= pos[1] < y + h:
        return None
    return ("That position is not in the inventory grid below the page tabs. It looks like the armor you "
            "wear (equipment window) or another window: right-clicking it would take your armor off "
            "instead of swapping it.\n\nPut a second armor in the inventory and set Spare Armor on that one.")


# ------------------------------------------------------------ jigsaw grid

def grid_problem(config: dict, bounds, frame: Optional[np.ndarray]) -> Optional[str]:
    """Right after Define Grid: does the rectangle show the puzzle grid?"""
    if frame is None or not bounds or len(bounds) != 4 or not config.get("jigsaw_grid_check", True):
        return None
    from jigsaw_detector import JigsawDetector
    detector = JigsawDetector(config)
    score, detail = detector.grid_structure(frame, tuple(int(round(float(v))) for v in bounds))
    if score >= float(config.get("jigsaw_grid_min_confidence", 0.6)):
        return None
    return (f"The rectangle does not line up with the Fish Puzzle grid (score {score:.2f}). Open the Fish "
            f"Puzzle window in the game, then press Define Grid and lay the ghost grid exactly over the 24 "
            f"cells: the bot only clicks where it sees that grid.")

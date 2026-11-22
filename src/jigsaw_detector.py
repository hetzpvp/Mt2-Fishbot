"""
Computer-vision helpers for the Fishing Jigsaw event window.

Where the grid is: the detector finds the Fish Puzzle window by itself in
every game window (``locate_puzzle``). The window's look and layout are the
same on every server seen so far (uiscript MiniGameFishEventGamePage): the
grid image's rim and the two crate slot frames, which pieces and crates
never cover, are matched as one masked template (assets/jigsaw/
puzzle_frame.png). That gives the grid to the pixel and both crate slots,
wherever the player put the window, in each client separately, with pieces
already placed. It is then followed at that place frame by frame, and
searched for again when it moved.

A grid defined by hand (``jigsaw_grid_bounds``) is the fallback for a
client where the window is not found (another look): there the detector
checks that the configured rectangle really shows the grid (the frame and
the cell dividers sit where a 6 x 4 grid puts them, see ``grid_structure``)
before it reads the cells.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

import diag
from utils import get_resource_path


Point = Tuple[int, int]
Rect = Tuple[int, int, int, int]


@dataclass
class JigsawGridResult:
    grid_found: bool
    grid_bounds: Optional[Rect]
    cell_centers: List[Point]
    board_mask: int
    confidence: float
    filled_cells: Optional[Set[int]] = None
    # Crate slot centers of the found puzzle window (None: the grid was
    # defined by hand, the slots are placed relative to it)
    crate_slots: Optional[Dict[str, Point]] = None
    source: str = "manual"     # "auto": the puzzle window was found; "manual": the defined grid


@dataclass
class PuzzleLocation:
    """The Fish Puzzle window found in a game window (window coordinates)."""
    corner: Point              # where the frame template's corner lies
    grid: Rect
    slots: Dict[str, Point]
    score: float
    second: float = -1.0       # the best score anywhere else (full searches only)


@dataclass
class JigsawPieceResult:
    piece_id: Optional[str]
    piece_mask: int
    source_section: str
    confidence: float
    figure_index: Optional[int] = None
    colored_pixels: int = -1    # piece-colored pixels at the cursor (0: nothing held; -1: not measured)


@dataclass
class JigsawActionResult:
    action: str
    target: Optional[Point] = None
    reason: str = ""


@dataclass
class CrateTarget:
    section: str
    center: Point
    confidence: float


@dataclass
class CrateSlotState:
    centers: Dict[str, Point]
    available: Set[str]
    missing: Set[str]
    confidence: Optional[Dict[str, float]] = None


@dataclass
class PieceColorModel:
    ref_hsv: np.ndarray
    figure_index: Optional[int]


class JigsawDetector:
    """Detects the Fishing Jigsaw board, crates, dialogs, and active pieces."""

    COLS = 6
    ROWS = 4
    DEFAULT_GRID_SIZE = (264, 176)

    # Mapping from PNG template -> figure_index in fishing_jigsaw_solver.FIGURES.
    # Derived from the FIGURES bitmasks (encoding: shift = col*N + row, so cells
    # step row-first within a column) compared against each PNG's actual cells:
    #   FIGURES[0] {(0,0)}                       -> Jigsaw_piece6.png (orange square)
    #   FIGURES[1] {(0,0),(0,1),(0,2)}           -> Jigsaw_piece4.png (blue line of 3)
    #   FIGURES[2] {(0,0),(0,1),(1,1)}           -> Jigsaw_piece1.png (green: X. / XX)
    #   FIGURES[3] {(0,0),(1,0),(1,1)}           -> Jigsaw_piece2.png (yellow: XX / .X)
    #   FIGURES[4] {(0,0),(0,1),(1,0),(1,1)}     -> Jigsaw_piece3.png (cyan 2x2)
    #   FIGURES[5] {(0,0),(1,0),(1,1),(2,1)}     -> Jigsaw_piece5.png (red S)
    PIECE_FILENAMES = {
        "piece1": ("Jigsaw_piece1.png", 2),
        "piece2": ("Jigsaw_piece2.png", 3),
        "piece3": ("Jigsaw_piece3.png", 4),
        "piece4": ("Jigsaw_piece4.png", 1),
        "piece5": ("Jigsaw_piece5.png", 5),
        "piece6": ("Jigsaw_piece6.png", 0),
        "piece_deluxe": ("Jigsaw_piece_deluxe.png", None),
    }

    # Optional assets. Add these files under assets/jigsaw/ to replace the
    # geometric fallbacks.
    # to replace the geometric fallbacks.
    OPTIONAL_TEMPLATES = {
        "special_crate": "jigsaw_special_crate.png",
        "normal_crate": "jigsaw_normal_crate.png",
        "special_empty_slot": ["jigsaw_special_empty_slot.png", "jigsaw_empty_crate_slot.png"],
        "normal_empty_slot": ["jigsaw_normal_empty_slot.png", "jigsaw_empty_crate_slot.png"],
        "confirm": "jigsaw_confirm.png",
        "discard_confirm": "jigsaw_discard_confirm.png",
    }

    # The Fish Puzzle window's layout (uiscript MiniGameFishEventGamePage):
    # the frame template (puzzle_frame.png) starts at the grid image, (14, 30)
    # in the window; the 6 x 4 cells of 32 px start 4 px inside it; the
    # crate slots (32 x 32) are centered at (248, 77) and (248, 128) in the
    # window. The game draws its interface 1:1 at every resolution.
    PUZZLE_TEMPLATE = "puzzle_frame.png"
    PUZZLE_GRID_IN_TEMPLATE = (4, 4, 192, 128)
    PUZZLE_SLOTS_IN_TEMPLATE = {"special": (248 - 14, 77 - 30), "normal": (248 - 14, 128 - 30)}
    _PUZZLE_MIN_SCORE = 0.60     # full search: real windows 0.79-1.0, anything else at most 0.48
    _PUZZLE_MIN_MARGIN = 0.15    # ... and this far above the best score anywhere else
    _PUZZLE_TRACK_SCORE = 0.50   # at the place it was found (a held piece over the frame: >= 0.74)
    _PUZZLE_TRACK_PX = 2         # how far it may have shifted there (a client moved it by 1 px)
    _PUZZLE_EXCLUDE_PX = 12      # "anywhere else": this far from the best place
    _PUZZLE_RESEARCH_S = 2.0     # with a defined grid in use: search for the window this often

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.puzzle: Optional[PuzzleLocation] = None   # where the puzzle window was found last
        self._puzzle_tpl: Optional[Tuple[np.ndarray, np.ndarray]] = None
        self._puzzle_tpl_loaded = False
        self._next_puzzle_search = 0.0
        self.threshold = float(self.config.get("jigsaw_detection_threshold", 0.80))
        # Cursor-piece templates often score lower than crate/slot templates because the
        # cursor sprite occludes part of the piece; use a separate, more permissive threshold.
        self.piece_threshold = float(self.config.get("jigsaw_piece_detection_threshold", 0.60))
        self._template_cache: Dict[str, Optional[np.ndarray]] = {}
        self._piece_color_refs: Dict[str, Tuple[np.ndarray, Optional[int]]] = {}
        self._piece_color_models: Dict[str, PieceColorModel] = {}

    # Grid check (grid_structure): a line counts as seen when its edge is this
    # many times stronger than the inside of the cells next to it, and at
    # least this strong in absolute terms (Sobel units: a step of ~6 levels).
    _LINE_RATIO = 2.0
    _LINE_FLOOR = 24.0

    def grid_structure(self, frame: np.ndarray, bounds: Rect) -> Tuple[float, str]:
        """How well the rectangle ``bounds`` shows a 6 x 4 grid: (score 0..1,
        detail). Looks for an edge where each of the 7 + 5 frame and divider
        lines must lie, segment by segment (one segment per cell along the
        line), compared with the edges inside the neighbouring cells. Placed
        pieces leave the lines in place; the game world, a closed puzzle or
        a rectangle shifted by about a cell does not line up with them.

        The four frame sides decide: each must be seen on at least half of
        its segments (a rectangle shifted by about a cell puts one side over
        plain background, a closed or covered puzzle shows none). Then the
        score is 0.6 plus up to 0.4 for the dividers seen (pieces drawn over
        them may hide some); otherwise it is at most 0.25."""
        x, y, w, h = bounds
        frame_h, frame_w = frame.shape[:2]
        cols, rows = self.COLS, self.ROWS
        cw, ch = w / cols, h / rows
        tol = self._structure_tolerance(w, h)
        margin = tol + 2
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1, y1 = min(frame_w, x + w + margin), min(frame_h, y + h + margin)
        roi = frame[y0:y1, x0:x1]
        if roi.size == 0 or cw < 6 or ch < 6:
            return 0.0, "grid area empty or too small"
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
        ex = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))   # vertical lines
        ey = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))   # horizontal lines
        ox, oy = x - x0, y - y0
        rh, rw = gray.shape

        def seen(energy, line_pos, span, cell, across_len, vertical):
            """One segment: line at ``line_pos`` (roi coords, across the
            line), along-line pixel range ``span``."""
            a, b = span
            if b - a < 3:
                return None
            if vertical:
                profile = energy[a:b, :].mean(axis=0)
            else:
                profile = energy[:, a:b].mean(axis=1)
            lo, hi = int(round(line_pos)) - tol, int(round(line_pos)) + tol + 1
            lo, hi = max(0, lo), min(across_len, hi)
            if hi <= lo:
                return False
            line = float(profile[lo:hi].max())
            refs = []
            for side in (-1, 1):
                c0 = int(round(line_pos + side * 0.25 * cell))
                c1 = int(round(line_pos + side * 0.75 * cell))
                c0, c1 = sorted((c0, c1))
                c0, c1 = max(0, c0), min(across_len, c1)
                if c1 - c0 >= 2:
                    refs.append(float(profile[c0:c1].mean()))
            inside = max(refs) if refs else 0.0
            return line >= max(self._LINE_FLOOR, self._LINE_RATIO * inside)

        sides = {"left": [], "right": [], "top": [], "bottom": []}
        inner = []
        for k in range(cols + 1):                     # vertical lines, one segment per row
            pos = ox + k * cw
            for r in range(rows):
                span = (max(0, int(oy + (r + 0.2) * ch)), min(rh, int(oy + (r + 0.8) * ch)))
                hit = seen(ex, pos, span, cw, rw, True)
                if hit is None:
                    continue
                (sides["left"] if k == 0 else sides["right"] if k == cols else inner).append(hit)
        for k in range(rows + 1):                     # horizontal lines, one segment per column
            pos = oy + k * ch
            for c in range(cols):
                span = (max(0, int(ox + (c + 0.2) * cw)), min(rw, int(ox + (c + 0.8) * cw)))
                hit = seen(ey, pos, span, ch, rh, False)
                if hit is None:
                    continue
                (sides["top"] if k == 0 else sides["bottom"] if k == rows else inner).append(hit)

        every = inner + [h_ for v in sides.values() for h_ in v]
        share = sum(every) / len(every) if every else 0.0
        side_share = {n: (sum(v) / len(v) if v else 0.0) for n, v in sides.items()}
        frame_ok = all(v >= 0.5 for v in side_share.values())
        inner_share = sum(inner) / len(inner) if inner else 0.0
        score = 0.6 + 0.4 * inner_share if frame_ok else min(share, 0.25)
        detail = (f"lines seen {sum(every)}/{len(every)}, frame sides "
                  + " ".join(f"{n}={v:.2f}" for n, v in side_share.items()) + f" (tol {tol}px)")
        return score, detail

    @classmethod
    def _structure_tolerance(cls, w: int, h: int) -> int:
        """How far (px) grid_structure looks for a line around where it should be."""
        return max(3, int(round(min(w / cls.COLS, h / cls.ROWS) * 0.09)))

    # ------------------------------------------------------------ puzzle window

    def auto_grid(self) -> bool:
        """True when the puzzle window is searched for (the default) and its
        template is there."""
        return bool(self.config.get("jigsaw_grid_auto", True)) and self._puzzle_template() is not None

    def _puzzle_template(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """(gray template, mask) of the puzzle frame, or None."""
        if not self._puzzle_tpl_loaded:
            self._puzzle_tpl_loaded = True
            path = self._find_asset(self.PUZZLE_TEMPLATE)
            img = cv2.imread(path, cv2.IMREAD_UNCHANGED) if path else None
            if img is None or img.ndim != 3 or img.shape[2] != 4:
                diag.log("ANALYSIS", f"puzzle frame template {self.PUZZLE_TEMPLATE} not found: "
                                     f"only a grid defined by hand can be used")
            else:
                mask = np.where(img[:, :, 3] > 0, 255, 0).astype(np.uint8)
                self._puzzle_tpl = (cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY), mask)
        return self._puzzle_tpl

    def _puzzle_location(self, corner: Point, score: float, second: float = -1.0) -> PuzzleLocation:
        x, y = corner
        gx, gy, gw, gh = self.PUZZLE_GRID_IN_TEMPLATE
        slots = {k: (x + dx, y + dy) for k, (dx, dy) in self.PUZZLE_SLOTS_IN_TEMPLATE.items()}
        return PuzzleLocation((x, y), (x + gx, y + gy, gw, gh), slots, score, second)

    @staticmethod
    def _match(gray: np.ndarray, template: np.ndarray, mask: np.ndarray) -> np.ndarray:
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED, mask=mask)
        # a flat area (zero variance under the mask) gives inf / nan
        return np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)

    def locate_puzzle(self, frame: np.ndarray, origin: Point = (0, 0)) -> Optional[PuzzleLocation]:
        """Searches ``frame`` (a whole game window, or a part of it whose
        corner is ``origin``) for the Fish Puzzle window. The best place must
        score _PUZZLE_MIN_SCORE and stand _PUZZLE_MIN_MARGIN above every place
        more than a few pixels away; otherwise None (closed, covered, or a
        client with another look). 0.1 s at 1024x768, 0.25 s at 1920x1080."""
        tpl = self._puzzle_template()
        if tpl is None or frame is None or frame.ndim != 3:
            return None
        template, mask = tpl
        th, tw = template.shape
        if frame.shape[0] < th or frame.shape[1] < tw:
            return None
        result = self._match(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), template, mask)
        _, best, _, (bx, by) = cv2.minMaxLoc(result)
        ex = self._PUZZLE_EXCLUDE_PX
        result[max(0, by - ex):by + ex + 1, max(0, bx - ex):bx + ex + 1] = -1.0
        second = float(result.max()) if result.size else -1.0
        corner = (bx + origin[0], by + origin[1])
        found = best >= self._PUZZLE_MIN_SCORE and best - second >= self._PUZZLE_MIN_MARGIN
        diag.log("ANALYSIS", f"puzzle window search in {frame.shape[1]}x{frame.shape[0]}: best {best:.3f} at "
                             f"frame corner {corner}, elsewhere {second:.3f} -> "
                             f"{'FOUND' if found else 'not found'}")
        if not found:
            return None
        return self._puzzle_location(corner, float(best), second)

    def _puzzle_near(self, frame: np.ndarray, origin: Point, corner: Point) -> Optional[PuzzleLocation]:
        """The puzzle window at the place it was found (within a few px)."""
        tpl = self._puzzle_template()
        if tpl is None:
            return None
        template, mask = tpl
        th, tw = template.shape
        r = self._PUZZLE_TRACK_PX
        x0, y0 = corner[0] - origin[0] - r, corner[1] - origin[1] - r
        x1, y1 = x0 + tw + 2 * r, y0 + th + 2 * r
        fx0, fy0 = max(0, x0), max(0, y0)
        fx1, fy1 = min(frame.shape[1], x1), min(frame.shape[0], y1)
        if fx1 - fx0 < tw or fy1 - fy0 < th:
            return None
        gray = cv2.cvtColor(frame[fy0:fy1, fx0:fx1], cv2.COLOR_BGR2GRAY)
        _, best, _, (bx, by) = cv2.minMaxLoc(self._match(gray, template, mask))
        if best < self._PUZZLE_TRACK_SCORE:
            diag.log("ANALYSIS", f"puzzle window not at {corner} any more (score {best:.3f})")
            return None
        return self._puzzle_location((fx0 + bx + origin[0], fy0 + by + origin[1]), float(best))

    def _find_puzzle(self, frame: np.ndarray, origin: Point,
                     window_size: Optional[Tuple[int, int]]) -> Optional[PuzzleLocation]:
        """Where the puzzle window is in this frame: at the place it was
        found last, else anywhere (a whole-window frame only). Remembered in
        ``self.puzzle``."""
        if not self.auto_grid():
            return None
        if self.puzzle is not None:
            loc = self._puzzle_near(frame, origin, self.puzzle.corner)
            if loc is not None:
                if loc.corner != self.puzzle.corner:
                    diag.log("ANALYSIS", f"puzzle window shifted {self.puzzle.corner} -> {loc.corner}")
                self.puzzle = loc
                return loc
        win_w, win_h = window_size if window_size is not None else (frame.shape[1], frame.shape[0])
        if origin != (0, 0) or frame.shape[1] < win_w or frame.shape[0] < win_h:
            return None          # a part of the window: the caller looks at the whole window
        now = time.monotonic()
        if self.config.get("jigsaw_grid_bounds") and self.puzzle is None and now < self._next_puzzle_search:
            return None          # the defined grid is in use; the window is searched for again later
        loc = self.locate_puzzle(frame, origin)
        if loc is None:
            self._next_puzzle_search = now + self._PUZZLE_RESEARCH_S
            return None
        if self.puzzle is None or loc.corner != self.puzzle.corner:
            diag.log("ANALYSIS", f"puzzle window found: grid {loc.grid}, crate slots {loc.slots}, "
                                 f"score {loc.score:.3f} (elsewhere {loc.second:.3f})")
        self.puzzle = loc
        return loc

    def current_grid_bounds(self) -> Optional[Rect]:
        """The grid in use: where the puzzle window was found last, else the
        grid defined by hand (None: neither)."""
        if self.puzzle is not None and self.auto_grid():
            return self.puzzle.grid
        value = self.config.get("jigsaw_grid_bounds")
        try:
            x, y, w, h = [int(round(float(v))) for v in value]
        except (TypeError, ValueError):
            return None
        return (x, y, w, h) if w > 0 and h > 0 else None

    def board_area(self, win_w: int, win_h: int) -> Optional[Rect]:
        """The window rectangle detect_grid reads: the found puzzle frame (plus
        the few pixels it may shift), or the configured grid plus the margin
        grid_structure looks at around it. A capture of just this rectangle,
        passed with its corner as ``origin``, reads the same as the whole
        window. None when neither is known or in the window."""
        tpl = self._puzzle_template() if self.auto_grid() else None
        if self.puzzle is not None and tpl is not None:
            th, tw = tpl[0].shape
            r = self._PUZZLE_TRACK_PX
            x0, y0 = max(0, self.puzzle.corner[0] - r), max(0, self.puzzle.corner[1] - r)
            x1 = min(win_w, self.puzzle.corner[0] + tw + r)
            y1 = min(win_h, self.puzzle.corner[1] + th + r)
            if x1 > x0 and y1 > y0:
                return (x0, y0, x1 - x0, y1 - y0)
        bounds = self._configured_grid_bounds(None, (win_w, win_h))
        if bounds is None:
            return None
        x, y, w, h = bounds
        margin = self._structure_tolerance(w, h) + 2
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1, y1 = min(win_w, x + w + margin), min(win_h, y + h + margin)
        return (x0, y0, x1 - x0, y1 - y0)

    def detect_grid(self, frame: np.ndarray, origin: Point = (0, 0),
                    window_size: Optional[Tuple[int, int]] = None) -> JigsawGridResult:
        """Read the configured 6x4 grid and return board occupancy as a 24-bit mask.

        ``grid_found`` is False when the configured rectangle does not show
        the puzzle grid (window closed, moved or covered): its cells must
        not be read, and nothing must be clicked there.

        ``frame`` is the whole window, or only the board_area() part of it:
        then ``origin`` is that part's corner and ``window_size`` the window's
        (w, h). The bounds and cell centers returned are window coordinates
        either way.

        The puzzle window found in the frame (see _find_puzzle) comes first;
        the grid defined by hand is the fallback."""
        puzzle = self._find_puzzle(frame, origin, window_size)
        if puzzle is not None:
            return self._read_cells(frame, origin, puzzle.grid, puzzle.score,
                                    crate_slots=dict(puzzle.slots), source="auto")
        bounds = self._configured_grid_bounds(frame, window_size)
        if bounds is None:
            diag.log("ANALYSIS", f"grid NOT found: configured jigsaw_grid_bounds="
                                 f"{self.config.get('jigsaw_grid_bounds')} vs window "
                                 f"{window_size or (frame.shape[1], frame.shape[0])} "
                                 f"({diag.array_summary(frame)})")
            return JigsawGridResult(False, None, [], 0, 0.0)

        # Where the grid lies in ``frame`` (the same as ``bounds`` for a whole window).
        local = (bounds[0] - origin[0], bounds[1] - origin[1], bounds[2], bounds[3])
        if (local[0] < 0 or local[1] < 0 or local[0] + local[2] > frame.shape[1]
                or local[1] + local[3] > frame.shape[0]):
            diag.log("ANALYSIS", f"grid NOT read: {bounds} is not inside the captured part "
                                 f"{frame.shape[1]}x{frame.shape[0]} at {origin}")
            return JigsawGridResult(False, None, [], 0, 0.0)

        confidence = 1.0
        if self.config.get("jigsaw_grid_check", True):
            confidence, detail = self.grid_structure(frame, local)
            min_conf = float(self.config.get("jigsaw_grid_min_confidence", 0.6))
            if confidence < min_conf:
                diag.log("ANALYSIS", f"grid NOT found at the configured bounds {bounds}: structure score "
                                     f"{confidence:.2f} < {min_conf} ({detail})")
                return JigsawGridResult(False, bounds, [], 0, confidence)
            if diag.ENABLED:
                diag.trace("ANALYSIS", f"grid structure at {bounds}: {confidence:.2f} ({detail})")
        return self._read_cells(frame, origin, bounds, confidence)

    def _read_cells(self, frame: np.ndarray, origin: Point, bounds: Rect, confidence: float,
                    crate_slots: Optional[Dict[str, Point]] = None, source: str = "manual") -> JigsawGridResult:
        """The occupancy of the 24 cells of the grid at ``bounds`` (window
        coordinates; ``frame``'s corner is ``origin``)."""
        local = (bounds[0] - origin[0], bounds[1] - origin[1], bounds[2], bounds[3])
        if (local[0] < 0 or local[1] < 0 or local[0] + local[2] > frame.shape[1]
                or local[1] + local[3] > frame.shape[0]):
            diag.log("ANALYSIS", f"grid NOT read: {bounds} is not inside the captured part "
                                 f"{frame.shape[1]}x{frame.shape[0]} at {origin}")
            return JigsawGridResult(False, None, [], 0, 0.0)
        x, y, w, h = bounds
        cell_w = w / self.COLS
        cell_h = h / self.ROWS
        grid_roi = frame[local[1]:local[1] + h, local[0]:local[0] + w]
        if grid_roi.size == 0:
            return JigsawGridResult(False, None, [], 0, 0.0)

        gray_roi = cv2.cvtColor(grid_roi, cv2.COLOR_BGR2GRAY)
        hsv_roi = cv2.cvtColor(grid_roi, cv2.COLOR_BGR2HSV)
        edges_roi = cv2.Canny(gray_roi, 35, 90)
        bright_min = int(self.config.get("jigsaw_cell_bright_min", 64))
        sat_min = int(self.config.get("jigsaw_cell_saturation_min", 45))
        mean_min = float(self.config.get("jigsaw_cell_mean_filled_min", 58.0))
        bright_ratio_min = float(self.config.get("jigsaw_cell_bright_ratio_min", 0.10))
        sat_ratio_min = float(self.config.get("jigsaw_cell_saturation_ratio_min", 0.12))
        edge_ratio_min = float(self.config.get("jigsaw_cell_edge_ratio_min", 0.070))
        centers: List[Point] = []
        filled_cells: Set[int] = set()
        board_mask = 0

        for row in range(self.ROWS):
            for col in range(self.COLS):
                cx = int(round(x + (col + 0.5) * cell_w))
                cy = int(round(y + (row + 0.5) * cell_h))
                centers.append((cx, cy))
                if self._grid_cell_is_filled(
                    gray_roi,
                    hsv_roi,
                    edges_roi,
                    cell_w,
                    cell_h,
                    col,
                    row,
                    bright_min,
                    sat_min,
                    mean_min,
                    bright_ratio_min,
                    sat_ratio_min,
                    edge_ratio_min,
                ):
                    action = col * self.ROWS + row
                    board_mask |= (1 << (self.ROWS * self.COLS - 1)) >> action
                    filled_cells.add(row * self.COLS + col)

        if diag.ENABLED:
            diag.trace("ANALYSIS", f"grid {bounds}: filled cells {sorted(filled_cells)} "
                                   f"board_mask={board_mask:024b} ({len(filled_cells)}/24), grid area "
                                   f"{diag.array_summary(grid_roi)}")
        return JigsawGridResult(True, bounds, centers, board_mask, confidence, filled_cells,
                                crate_slots=crate_slots, source=source)

    _GRID_EDGE_TOLERANCE = 2   # px the configured grid may stick out of the frame

    def _configured_grid_bounds(self, frame: Optional[np.ndarray],
                                window_size: Optional[Tuple[int, int]] = None) -> Optional[Rect]:
        """The configured grid in window coordinates, checked against the
        window (``window_size``, or the size of ``frame``)."""
        value = self.config.get("jigsaw_grid_bounds")
        if not value or len(value) != 4:
            return None
        try:
            x, y, w, h = [int(round(float(part))) for part in value]
        except (TypeError, ValueError):
            return None
        frame_w, frame_h = window_size if window_size is not None else (frame.shape[1], frame.shape[0])
        if w <= 0 or h <= 0 or x >= frame_w or y >= frame_h:
            return None
        # A grid that sticks out of the frame cannot be read: clamping it
        # shifted or squeezed the rectangle, and the cells read and clicked
        # were no longer the real ones. A pixel or two of rounding is fine.
        tol = self._GRID_EDGE_TOLERANCE
        if x < -tol or y < -tol or x + w > frame_w + tol or y + h > frame_h + tol:
            return None
        x = max(0, x)
        y = max(0, y)
        w = min(w, frame_w - x)
        h = min(h, frame_h - y)
        if w < self.COLS or h < self.ROWS:
            return None
        return (x, y, w, h)

    def detect_crate(
        self,
        frame: np.ndarray,
        grid: JigsawGridResult,
        priority: str,
        available_sections: Optional[Set[str]] = None,
        gray: Optional[np.ndarray] = None,
    ) -> Optional[CrateTarget]:
        """Find the next crate to open. Template matching wins, layout fallback follows."""
        order = self._crate_order(priority)

        # Filter available sections by priority order
        if available_sections is not None:
            priority_sections = [s for s in order if s in available_sections]
        else:
            priority_sections = order

        if not priority_sections:
            return None

        if gray is None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Only check templates for sections in the priority list
        candidates: List[CrateTarget] = []
        for section in priority_sections:
            key = "normal_crate" if section == "normal" else "special_crate"
            match = self._match_optional_template(frame, key, gray=gray)
            if match:
                x, y, w, h, conf = match
                candidates.append(CrateTarget(section, (x + w // 2, y + h // 2), conf))

        if candidates:
            candidates.sort(key=lambda c: (-priority_sections.index(c.section), -c.confidence))
            return candidates[0]

        if not grid.grid_found or grid.grid_bounds is None:
            return None

        slots = self.detect_crate_slots(frame, grid, gray=gray)
        centers = slots.centers
        
        # Use first priority section that's available (and in the picture:
        # a slot off the frame is not read, see detect_crate_slots)
        frame_h, frame_w = frame.shape[:2]
        for section in priority_sections:
            if section in centers and 0 <= centers[section][0] < frame_w and 0 <= centers[section][1] < frame_h:
                confidence = 0.50
                if slots.confidence and section in slots.confidence:
                    confidence = slots.confidence[section]
                return CrateTarget(section, centers[section], confidence)
        
        return None

    def detect_crate_slots(
        self,
        frame: np.ndarray,
        grid: JigsawGridResult,
        gray: Optional[np.ndarray] = None,
    ) -> CrateSlotState:
        """Detect whether the right-side normal/special crate slots are filled."""
        centers = self._crate_slot_centers(grid)
        available: Set[str] = set()
        missing: Set[str] = set()
        confidence: Dict[str, float] = {}
        if not centers:
            return CrateSlotState({}, available, {"normal", "special"}, {})

        frame_h, frame_w = frame.shape[:2]
        for section, center in centers.items():
            if not (0 <= center[0] < frame_w and 0 <= center[1] < frame_h):
                # Not in the picture: unknown, neither a crate to open nor
                # an empty slot to refill (the refill would click off the window).
                diag.log("ANALYSIS", f"{section} crate slot {center} lies outside the frame "
                                     f"{frame_w}x{frame_h}: not read")
                continue
            crate_template_key = f"{section}_crate"
            if self._slot_has_matching_template(frame, crate_template_key, center, gray=gray):
                available.add(section)
                confidence[section] = max(confidence.get(section, 0.0), 0.95)
                continue
            if self._slot_has_matching_template(frame, f"{section}_empty_slot", center, gray=gray):
                missing.add(section)
                confidence[section] = max(confidence.get(section, 0.0), 0.95)
                continue
            empty, score = self._slot_looks_empty(frame, gray, center, grid.grid_bounds)
            confidence[section] = score
            if empty:
                missing.add(section)
            else:
                available.add(section)

        return CrateSlotState(centers, available, missing, confidence)

    def has_confirm_template(self, discard: bool = False) -> bool:
        """True when the confirm (or discard confirm) button template was
        shipped: without it detect_confirm_button never finds anything."""
        return self._load_optional_template("discard_confirm" if discard else "confirm") is not None

    def detect_confirm_button(
        self,
        frame: np.ndarray,
        discard: bool = False,
        gray: Optional[np.ndarray] = None,
    ) -> Optional[Point]:
        key = "discard_confirm" if discard else "confirm"
        match = self._match_optional_template(frame, key, gray=gray)
        if match:
            x, y, w, h, _ = match
            return (x + w // 2, y + h // 2)

        # No template, or it did not match: no position. A guessed point
        # (the old "lower middle of the window") was clicked whether the
        # dialog was there or not.
        diag.trace("ANALYSIS", f"{key} button template not matched: no position")
        return None

    def detect_cursor_piece(self, frame: np.ndarray, cursor_pos: Optional[Point] = None) -> JigsawPieceResult:
        """Identify active cursor piece from a small color sample around the cursor."""
        self._load_piece_color_models()
        if not self._piece_color_models:
            return JigsawPieceResult(None, 0, "unknown", 0.0, None)

        roi, _ = self._cursor_color_roi(frame, cursor_pos)
        piece_pixels = self._piece_pixel_mask(roi)
        if piece_pixels is None or int(np.count_nonzero(piece_pixels)) == 0:
            diag.trace("ANALYSIS", f"cursor piece: no piece-colored pixels in roi {diag.array_summary(roi)} "
                                   f"around {cursor_pos}")
            return JigsawPieceResult(None, 0, "unknown", 0.0, None, 0 if piece_pixels is not None else -1)
        colored = int(np.count_nonzero(piece_pixels))
        if cursor_pos is None:
            # Without the cursor's position the whole frame is the sample: the
            # colors of the board, the crates and the rest of the UI mixed
            # together. They never named a piece (and must not), but scoring
            # them took ~120 ms at 720p and ~250 ms at 1080p (it grows with the
            # number of saturated pixels). Colored pixels are
            # still reported, so the caller treats it as an unknown piece.
            diag.trace("ANALYSIS", f"cursor piece: no cursor position, {colored} colored pixels in the "
                                   f"whole frame: unknown")
            return JigsawPieceResult(None, 0, "unknown", 0.0, None, colored)

        scored = self._score_cursor_piece_colors(roi, piece_pixels)
        if not scored:
            diag.trace("ANALYSIS", f"cursor piece: {colored} colored pixels but too few "
                                   f"confident ones (jigsaw_piece_min_pixels) around {cursor_pos}")
            return JigsawPieceResult(None, 0, "unknown", 0.0, None, colored)

        best_id, confidence, best_index = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        margin = confidence - second_score
        threshold = float(self.config.get("jigsaw_piece_color_confidence_threshold", self.piece_threshold))
        min_margin = float(self.config.get("jigsaw_piece_score_margin_min", 0.06))
        if diag.ENABLED:
            diag.trace("ANALYSIS", f"cursor piece scores: " + ", ".join(f"{p}={c:.3f}" for p, c, _ in scored[:6])
                       + f" threshold={threshold} margin={margin:.3f} (min {min_margin}) -> "
                       + ("UNKNOWN" if best_id is None or confidence < threshold or margin < min_margin else best_id))
        if best_id is None or confidence < threshold or margin < min_margin:
            return JigsawPieceResult(None, 0, "unknown", confidence, None, colored)

        # The actual bitmask is resolved by JigsawBot from the shared registry.
        return JigsawPieceResult(best_id, 0, "cursor", confidence, best_index, colored)

    def _cell_is_filled(self, frame: np.ndarray, gx: int, gy: int, cell_w: float, cell_h: float, col: int, row: int) -> bool:
        """One cell of a grid whose corner is (gx, gy) in ``frame``, judged by
        the same test detect_grid uses for every cell (_grid_cell_is_filled)."""
        roi = frame[max(0, int(gy)):, max(0, int(gx)):]
        if roi.size == 0:
            return False
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        edges = cv2.Canny(gray, 35, 90)
        cfg = self.config
        return self._grid_cell_is_filled(
            gray, hsv, edges, cell_w, cell_h, col, row,
            int(cfg.get("jigsaw_cell_bright_min", 64)),
            int(cfg.get("jigsaw_cell_saturation_min", 45)),
            float(cfg.get("jigsaw_cell_mean_filled_min", 58.0)),
            float(cfg.get("jigsaw_cell_bright_ratio_min", 0.10)),
            float(cfg.get("jigsaw_cell_saturation_ratio_min", 0.12)),
            float(cfg.get("jigsaw_cell_edge_ratio_min", 0.070)),
        )

    def _grid_cell_is_filled(
        self,
        gray_roi: np.ndarray,
        hsv_roi: np.ndarray,
        edges_roi: np.ndarray,
        cell_w: float,
        cell_h: float,
        col: int,
        row: int,
        bright_min: int,
        sat_min: int,
        mean_min: float,
        bright_ratio_min: float,
        sat_ratio_min: float,
        edge_ratio_min: float,
    ) -> bool:
        pad_x = max(6, int(cell_w * 0.24))
        pad_y = max(6, int(cell_h * 0.24))
        x1 = int(col * cell_w + pad_x)
        y1 = int(row * cell_h + pad_y)
        x2 = int((col + 1) * cell_w - pad_x)
        y2 = int((row + 1) * cell_h - pad_y)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(gray_roi.shape[1], x2), min(gray_roi.shape[0], y2)
        if x2 <= x1 or y2 <= y1:
            return False

        gray = gray_roi[y1:y2, x1:x2]
        hsv = hsv_roi[y1:y2, x1:x2]
        edges = edges_roi[y1:y2, x1:x2]
        mean = float(np.mean(gray))
        std = float(np.std(gray))
        bright_ratio = float(np.mean(gray > bright_min))
        sat_ratio = float(np.mean(hsv[:, :, 1] > sat_min))
        edge_ratio = float(np.mean(edges > 0))
        # Empty cells usually contain only the dark board well. Pieces add color,
        # highlights, and internal edges; using several weak signals avoids
        # classifying a shiny empty border as an occupied cell.
        return (
            mean > mean_min
            or bright_ratio > bright_ratio_min
            or (sat_ratio > sat_ratio_min and std > 14.0)
            or edge_ratio > edge_ratio_min
        )

    def _cursor_roi(self, frame: np.ndarray, cursor_pos: Optional[Point]) -> Tuple[np.ndarray, Point]:
        if cursor_pos is None:
            return frame, (0, 0)
        h, w = frame.shape[:2]
        cx, cy = cursor_pos
        radius = int(self.config.get("jigsaw_cursor_piece_roi", 96))
        x1, y1 = max(0, cx - radius), max(0, cy - radius)
        x2, y2 = min(w, cx + radius), min(h, cy + radius)
        roi = frame[y1:y2, x1:x2]
        return (roi if roi.size else frame), (x1, y1)

    def _cursor_color_roi(self, frame: np.ndarray, cursor_pos: Optional[Point]) -> Tuple[np.ndarray, Point]:
        """Small fixed-size square sampled directly on the cursor position."""
        if cursor_pos is None:
            return self._cursor_roi(frame, cursor_pos)
        h, w = frame.shape[:2]
        cx, cy = cursor_pos
        radius = int(self.config.get("jigsaw_cursor_color_roi", 15))
        radius = max(6, min(20, radius))
        x1, y1 = max(0, cx - radius), max(0, cy - radius)
        x2, y2 = min(w, cx + radius), min(h, cy + radius)
        roi = frame[y1:y2, x1:x2]
        return (roi if roi.size else frame), (x1, y1)

    def _match_optional_template(
        self,
        frame: np.ndarray,
        key: str,
        gray: Optional[np.ndarray] = None,
        search_rect: Optional[Rect] = None,
    ) -> Optional[Tuple[int, int, int, int, float]]:
        template = self._load_optional_template(key)
        if template is None:
            return None
        th, tw = template.shape[:2]
        offset_x = 0
        offset_y = 0
        if search_rect is not None:
            sx, sy, sw, sh = search_rect
            sx = max(0, sx)
            sy = max(0, sy)
            ex = min(frame.shape[1], sx + max(0, sw))
            ey = min(frame.shape[0], sy + max(0, sh))
            if ex <= sx or ey <= sy:
                return None
            search_gray = gray[sy:ey, sx:ex] if gray is not None else cv2.cvtColor(frame[sy:ey, sx:ex], cv2.COLOR_BGR2GRAY)
            offset_x = sx
            offset_y = sy
        else:
            search_gray = gray if gray is not None else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if th > search_gray.shape[0] or tw > search_gray.shape[1]:
            return None
        result = cv2.matchTemplate(search_gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if diag.ENABLED:
            diag.trace("ANALYSIS", f"template {key}: best={max_val:.3f} at "
                                   f"({max_loc[0] + offset_x + tw // 2},{max_loc[1] + offset_y + th // 2}) "
                                   f"threshold={self.threshold} search={search_rect} -> "
                                   f"{'match' if max_val >= self.threshold else 'no match'}")
        if max_val < self.threshold:
            return None
        return (max_loc[0] + offset_x, max_loc[1] + offset_y, tw, th, float(max_val))

    def _slot_has_matching_template(
        self,
        frame: np.ndarray,
        key: str,
        center: Point,
        gray: Optional[np.ndarray] = None,
    ) -> bool:
        template = self._load_optional_template(key)
        if template is None:
            return False
        th, tw = template.shape[:2]
        cx, cy = center
        half_w = max(36, tw * 2)
        half_h = max(36, th * 2)
        match = self._match_optional_template(
            frame,
            key,
            gray=gray,
            search_rect=(cx - half_w, cy - half_h, half_w * 2, half_h * 2),
        )
        if not match:
            return False
        x, y, w, h, _ = match
        mx, my = x + w // 2, y + h // 2
        return abs(mx - center[0]) <= max(24, w) and abs(my - center[1]) <= max(24, h)

    def _slot_looks_empty(self, frame: np.ndarray, gray: Optional[np.ndarray], center: Point, grid_bounds: Optional[Rect]) -> Tuple[bool, float]:
        cx, cy = center
        # The inside of the slot only. A wider sample took in the slot's
        # golden frame, whose colored pixels made an empty slot read as
        # "crate present" on a real client.
        if grid_bounds is not None:
            _, _, gw, gh = grid_bounds
            half_w = max(6, int(round(gw * 0.057)))
            half_h = max(6, int(round(gh * 0.086)))
        else:
            half_w, half_h = 11, 11
        frame_h, frame_w = frame.shape[:2]
        y1, y2 = max(0, cy - half_h), min(frame_h, cy + half_h)
        x1, x2 = max(0, cx - half_w), min(frame_w, cx + half_w)
        color_crop = frame[y1:y2, x1:x2]
        crop = gray[y1:y2, x1:x2] if gray is not None else cv2.cvtColor(color_crop, cv2.COLOR_BGR2GRAY)
        if crop.size == 0:
            return True, 0.0    # not reached for off-frame centers: detect_crate_slots skips them
        mean = float(np.mean(crop))
        std = float(np.std(crop))
        hsv = cv2.cvtColor(color_crop, cv2.COLOR_BGR2HSV)
        # Saturation of near-black pixels is noise (the empty slot's dark
        # texture reads 50% "saturated"): only visible color counts.
        sat_ratio = float(np.mean((hsv[:, :, 1] > int(self.config.get("jigsaw_slot_saturation_min", 45)))
                                  & (hsv[:, :, 2] >= 60)))
        bright_ratio = float(np.mean(crop > int(self.config.get("jigsaw_slot_bright_min", 66))))
        # Logged only: the empty slot's own texture has edges (0.2-0.26 on a
        # real client) and failed the old edge limit every time.
        edges = cv2.Canny(crop, 35, 90)
        edge_ratio = float(np.mean(edges > 0))

        empty = (
            mean < float(self.config.get("jigsaw_empty_slot_mean_max", 48.0))
            and std < float(self.config.get("jigsaw_empty_slot_std_max", 24.0))
            and sat_ratio < float(self.config.get("jigsaw_empty_slot_saturation_ratio_max", 0.10))
            and bright_ratio < float(self.config.get("jigsaw_empty_slot_bright_ratio_max", 0.08))
        )
        available_score = min(1.0, max(bright_ratio * 3.0, sat_ratio * 2.5, std / 55.0))
        if diag.ENABLED:
            diag.trace("ANALYSIS", f"crate slot at {center}: mean={mean:.1f} std={std:.1f} sat_ratio={sat_ratio:.3f} "
                                   f"bright_ratio={bright_ratio:.3f} edge_ratio={edge_ratio:.3f} -> "
                                   f"{'EMPTY' if empty else 'crate present'}")
        return empty, (1.0 - available_score if empty else max(0.50, available_score))

    def _load_optional_template(self, key: str) -> Optional[np.ndarray]:
        if key in self._template_cache:
            return self._template_cache[key]
        if key not in self.OPTIONAL_TEMPLATES:
            self._template_cache[key] = None
            return None
        filenames = self.OPTIONAL_TEMPLATES[key]
        if isinstance(filenames, str):
            filenames = [filenames]
        path = None
        for filename in filenames:
            path = self._find_asset(filename)
            if path:
                break
        if not path:
            diag.log("ANALYSIS", f"optional jigsaw template '{key}' ({filenames}) not found: fallback used")
            self._template_cache[key] = None
            return None
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        diag.log("ANALYSIS", f"jigsaw template '{key}' loaded from {path}: "
                             f"{None if img is None else img.shape}")
        self._template_cache[key] = img
        return img

    def _load_piece_color_refs(self) -> None:
        self._load_piece_color_models()

    def _load_piece_color_models(self) -> None:
        if self._piece_color_models:
            return
        for piece_id, (filename, figure_index) in self.PIECE_FILENAMES.items():
            path = self._find_asset(filename)
            if not path:
                continue
            img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
            if img is None or img.size == 0:
                continue
            ref_hsv = self._dominant_piece_hsv(img)
            if ref_hsv is None:
                continue
            self._piece_color_refs[piece_id] = (ref_hsv, figure_index)
            self._piece_color_models[piece_id] = PieceColorModel(ref_hsv, figure_index)

    def _piece_pixel_mask(self, image: np.ndarray) -> Optional[np.ndarray]:
        if image is None or image.size == 0:
            return None
        alpha_mask = None
        if image.ndim == 3 and image.shape[2] == 4:
            alpha_mask = image[:, :, 3] > int(self.config.get("jigsaw_piece_alpha_min", 24))
            bgr = image[:, :, :3]
        elif image.ndim == 3 and image.shape[2] == 3:
            bgr = image
        else:
            bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        sat_min = int(self.config.get("jigsaw_piece_color_sat_min", 60))
        val_min = int(self.config.get("jigsaw_piece_color_val_min", 35))
        mask = (hsv[:, :, 1] >= sat_min) & (hsv[:, :, 2] >= val_min)
        if alpha_mask is not None:
            mask &= alpha_mask
        return mask

    def _score_cursor_piece_colors(
        self,
        roi: np.ndarray,
        color_mask: np.ndarray,
    ) -> List[Tuple[str, float, Optional[int]]]:
        hsv = cv2.cvtColor(roi[:, :, :3] if roi.ndim == 3 and roi.shape[2] == 4 else roi, cv2.COLOR_BGR2HSV)
        pixels = hsv[color_mask]
        if pixels.size == 0:
            return []

        max_dist = float(self.config.get("jigsaw_piece_color_max_distance", 120.0))
        pixel_max_dist = float(self.config.get("jigsaw_piece_pixel_max_distance", 58.0))
        pixel_margin_min = float(self.config.get("jigsaw_piece_pixel_margin_min", 7.0))
        min_piece_pixels = int(self.config.get("jigsaw_piece_min_pixels", 28))

        piece_ids = list(self._piece_color_models.keys())
        refs = np.array([self._piece_color_models[piece_id].ref_hsv for piece_id in piece_ids], dtype=np.float32)
        distances = self._hsv_distance_pixels_to_refs(pixels, refs)
        order = np.argsort(distances, axis=1)
        best_idx = order[:, 0]
        best_dist = distances[np.arange(distances.shape[0]), best_idx]
        second_dist = distances[np.arange(distances.shape[0]), order[:, 1]] if distances.shape[1] > 1 else np.full_like(best_dist, max_dist)
        confident = (best_dist <= pixel_max_dist) & ((second_dist - best_dist) >= pixel_margin_min)
        if int(np.count_nonzero(confident)) < min_piece_pixels:
            return []

        weights = np.maximum(0.0, 1.0 - (best_dist[confident] / max_dist))
        winners = best_idx[confident]
        total_pixels = max(1, int(pixels.shape[0]))
        total_weight = max(1e-6, float(np.sum(weights)))

        scored: List[Tuple[str, float, Optional[int]]] = []
        for idx, piece_id in enumerate(piece_ids):
            piece_weights = weights[winners == idx]
            if piece_weights.size == 0:
                continue
            vote_ratio = float(piece_weights.size) / float(total_pixels)
            weight_ratio = float(np.sum(piece_weights)) / total_weight
            mean_quality = float(np.mean(piece_weights))
            score = (0.45 * vote_ratio) + (0.35 * weight_ratio) + (0.20 * mean_quality)
            model = self._piece_color_models[piece_id]
            scored.append((piece_id, max(0.0, min(1.0, score)), model.figure_index))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored

    @staticmethod
    def _hsv_distance_image(hsv: np.ndarray, ref: np.ndarray) -> np.ndarray:
        hue_delta = np.abs(hsv[:, :, 0].astype(np.float32) - float(ref[0]))
        hue_delta = np.minimum(hue_delta, 180.0 - hue_delta)
        sat_delta = np.abs(hsv[:, :, 1].astype(np.float32) - float(ref[1]))
        val_delta = np.abs(hsv[:, :, 2].astype(np.float32) - float(ref[2]))
        return np.sqrt((2.2 * hue_delta) ** 2 + (0.7 * sat_delta) ** 2 + (0.5 * val_delta) ** 2)

    @staticmethod
    def _hsv_distance_pixels_to_refs(pixels: np.ndarray, refs: np.ndarray) -> np.ndarray:
        hue_delta = np.abs(pixels[:, None, 0].astype(np.float32) - refs[None, :, 0])
        hue_delta = np.minimum(hue_delta, 180.0 - hue_delta)
        sat_delta = np.abs(pixels[:, None, 1].astype(np.float32) - refs[None, :, 1])
        val_delta = np.abs(pixels[:, None, 2].astype(np.float32) - refs[None, :, 2])
        return np.sqrt((2.2 * hue_delta) ** 2 + (0.7 * sat_delta) ** 2 + (0.5 * val_delta) ** 2)

    def _dominant_piece_hsv(self, image: np.ndarray) -> Optional[np.ndarray]:
        if image is None or image.size == 0:
            return None

        alpha_mask = None
        if image.ndim == 3 and image.shape[2] == 4:
            alpha_mask = image[:, :, 3] > int(self.config.get("jigsaw_piece_alpha_min", 24))
            bgr = image[:, :, :3]
        elif image.ndim == 3 and image.shape[2] == 3:
            bgr = image
        else:
            # Grayscale fallback (should not happen for cursor/frame paths).
            bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        sat_min = int(self.config.get("jigsaw_piece_color_sat_min", 60))
        val_min = int(self.config.get("jigsaw_piece_color_val_min", 35))
        color_mask = (hsv[:, :, 1] >= sat_min) & (hsv[:, :, 2] >= val_min)
        if alpha_mask is not None:
            color_mask &= alpha_mask

        pixels = hsv[color_mask]
        if pixels.size == 0:
            pixels = hsv.reshape(-1, 3)
            if pixels.size == 0:
                return None

        # Direct mean over the small cursor sample. The 15-px ROI sits on the
        # piece sprite, so the saturated-pixel mean is the piece colour.
        hue_rad = pixels[:, 0].astype(np.float32) * (2.0 * np.pi / 180.0)
        mean_sin = float(np.mean(np.sin(hue_rad)))
        mean_cos = float(np.mean(np.cos(hue_rad)))
        mean_h = (np.arctan2(mean_sin, mean_cos) * (180.0 / (2.0 * np.pi))) % 180.0
        mean_s = float(np.mean(pixels[:, 1]))
        mean_v = float(np.mean(pixels[:, 2]))
        return np.array([mean_h, mean_s, mean_v], dtype=np.float32)

    @staticmethod
    def _hsv_distance(a: np.ndarray, b: np.ndarray) -> float:
        hue_delta = abs(float(a[0]) - float(b[0]))
        hue_delta = min(hue_delta, 180.0 - hue_delta)
        sat_delta = abs(float(a[1]) - float(b[1]))
        val_delta = abs(float(a[2]) - float(b[2]))
        return float(np.sqrt((2.2 * hue_delta) ** 2 + (0.7 * sat_delta) ** 2 + (0.5 * val_delta) ** 2))

    def _find_asset(self, filename: str) -> Optional[str]:
        # Skip empty or whitespace-only filenames to avoid passing directory paths to cv2.imread
        if not filename or not filename.strip():
            return None

        candidates = [
            get_resource_path(filename),
            get_resource_path(os.path.join("jigsaw", filename)),
            os.path.join(get_resource_path("asset_root"), "jigsaw", filename),
        ]
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, "assets", "jigsaw", filename))
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    def _crate_slot_centers(self, grid: JigsawGridResult) -> Dict[str, Point]:
        if grid.crate_slots:
            # The found puzzle window's own slots (its layout, to the pixel).
            # Fixed pixel positions from the settings cannot follow the window.
            return dict(grid.crate_slots)
        configured = self.config.get("jigsaw_crate_slot_centers")
        if isinstance(configured, dict):
            centers: Dict[str, Point] = {}
            for section in ("normal", "special"):
                value = configured.get(section)
                if value and len(value) == 2:
                    try:
                        centers[section] = (int(value[0]), int(value[1]))
                    except (TypeError, ValueError):
                        pass
            if centers:
                return centers

        if not grid.grid_found or grid.grid_bounds is None:
            return {}
        gx, gy, gw, gh = grid.grid_bounds
        right_mid_x = gx + gw
        right_mid_y = gy + (gh // 2)
        # Measured on two real clients (tests/fixtures/jigsaw_grid): the slot
        # frames are centered 37 px right of the grid and 21 px above / 30 px
        # below its middle, for a grid of about 192x128. Kept in proportion to
        # the grid: the old fixed 50 / -20 / +35 px fitted a 264x176 board
        # (the simulator's) and put the click on the right edge of a real
        # slot, 13 px beside its center (diag 20261122_002413). The pixel
        # settings still override.
        x_offset = int(self.config.get("jigsaw_crate_slot_x_offset", round(gw * 0.193)))
        special_y_offset = int(self.config.get("jigsaw_special_crate_slot_y_offset", -round(gh * 0.168)))
        normal_y_offset = int(self.config.get("jigsaw_normal_crate_slot_y_offset", round(gh * 0.234)))
        return {
            "special": (right_mid_x + x_offset, right_mid_y + special_y_offset),
            "normal": (right_mid_x + x_offset, right_mid_y + normal_y_offset),
        }

    @staticmethod
    def _crate_order(priority: str) -> List[str]:
        if priority == "normal_first":
            return ["normal"]
        if priority in ("special_first", "special_when_better"):
            return ["special", "normal"]
        return ["normal"]


# Diagnostic log: entry/exit of the detector's methods. The per-cell helpers
# run 24 times per board read and are left out.
diag.trace_class(JigsawDetector, exclude=(
    "_cell_is_filled", "_grid_cell_is_filled", "_hsv_distance_image", "_hsv_distance_pixels_to_refs",
    "_hsv_distance", "_piece_pixel_mask", "_dominant_piece_hsv", "_cursor_roi", "_cursor_color_roi",
    "_find_asset", "_load_optional_template", "_crate_order", "_load_piece_color_refs",
))

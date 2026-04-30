"""
Computer-vision helpers for the Fishing Jigsaw event window.

The grid location is user-defined. The detector only evaluates occupancy inside
that configured rectangle; it does not image-match or infer the grid position.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

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


@dataclass
class JigsawPieceResult:
    piece_id: Optional[str]
    piece_mask: int
    source_section: str
    confidence: float
    figure_index: Optional[int] = None


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

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.threshold = float(self.config.get("jigsaw_detection_threshold", 0.80))
        # Cursor-piece templates often score lower than crate/slot templates because the
        # cursor sprite occludes part of the piece; use a separate, more permissive threshold.
        self.piece_threshold = float(self.config.get("jigsaw_piece_detection_threshold", 0.60))
        self._template_cache: Dict[str, Optional[np.ndarray]] = {}
        self._piece_color_refs: Dict[str, Tuple[np.ndarray, Optional[int]]] = {}
        self._piece_color_models: Dict[str, PieceColorModel] = {}

    def detect_grid(self, frame: np.ndarray) -> JigsawGridResult:
        """Read the configured 6x4 grid and return board occupancy as a 24-bit mask."""
        bounds = self._configured_grid_bounds(frame)
        if bounds is None:
            return JigsawGridResult(False, None, [], 0, 0.0)

        x, y, w, h = bounds
        cell_w = w / self.COLS
        cell_h = h / self.ROWS
        grid_roi = frame[y:y + h, x:x + w]
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

        return JigsawGridResult(True, bounds, centers, board_mask, 1.0, filled_cells)

    def _configured_grid_bounds(self, frame: np.ndarray) -> Optional[Rect]:
        value = self.config.get("jigsaw_grid_bounds")
        if not value or len(value) != 4:
            return None
        try:
            x, y, w, h = [int(round(float(part))) for part in value]
        except (TypeError, ValueError):
            return None
        frame_h, frame_w = frame.shape[:2]
        if w <= 0 or h <= 0 or x >= frame_w or y >= frame_h:
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
        
        # Use first priority section that's available
        for section in priority_sections:
            if section in centers:
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

        for section, center in centers.items():
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

        # Fallback: most Metin2 confirm dialogs place the OK button in the lower
        # middle of the active modal. This is deliberately conservative and can
        # be replaced by adding jigsaw_confirm.png / jigsaw_discard_confirm.png.
        h, w = frame.shape[:2]
        return (w // 2, int(h * 0.63))

    def detect_cursor_piece(self, frame: np.ndarray, cursor_pos: Optional[Point] = None) -> JigsawPieceResult:
        """Identify active cursor piece from a small color sample around the cursor."""
        self._load_piece_color_models()
        if not self._piece_color_models:
            return JigsawPieceResult(None, 0, "unknown", 0.0, None)

        roi, _ = self._cursor_color_roi(frame, cursor_pos)
        piece_pixels = self._piece_pixel_mask(roi)
        if piece_pixels is None or int(np.count_nonzero(piece_pixels)) == 0:
            return JigsawPieceResult(None, 0, "unknown", 0.0, None)

        scored = self._score_cursor_piece_colors(roi, piece_pixels)
        if not scored:
            return JigsawPieceResult(None, 0, "unknown", 0.0, None)

        best_id, confidence, best_index = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        margin = confidence - second_score
        threshold = float(self.config.get("jigsaw_piece_color_confidence_threshold", self.piece_threshold))
        min_margin = float(self.config.get("jigsaw_piece_score_margin_min", 0.06))
        if best_id is None or confidence < threshold or margin < min_margin:
            return JigsawPieceResult(None, 0, "unknown", confidence, None)

        # The actual bitmask is resolved by JigsawBot from the shared registry.
        return JigsawPieceResult(best_id, 0, "cursor", confidence, best_index)

    def _cell_is_filled(self, frame: np.ndarray, gx: int, gy: int, cell_w: float, cell_h: float, col: int, row: int) -> bool:
        pad_x = max(6, int(cell_w * 0.24))
        pad_y = max(6, int(cell_h * 0.24))
        x1 = int(gx + col * cell_w + pad_x)
        y1 = int(gy + row * cell_h + pad_y)
        x2 = int(gx + (col + 1) * cell_w - pad_x)
        y2 = int(gy + (row + 1) * cell_h - pad_y)
        cell = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if cell.size == 0:
            return False
        gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
        mean = float(np.mean(gray))
        std = float(np.std(gray))
        bright_ratio = float(np.mean(gray > int(self.config.get("jigsaw_cell_bright_min", 64))))
        sat_ratio = float(np.mean(hsv[:, :, 1] > int(self.config.get("jigsaw_cell_saturation_min", 45))))
        edges = cv2.Canny(gray, 35, 90)
        edge_ratio = float(np.mean(edges > 0))

        # Empty cells usually contain only the dark board well. Pieces add color,
        # highlights, and internal edges; using several weak signals avoids
        # classifying a shiny empty border as an occupied cell.
        return (
            mean > float(self.config.get("jigsaw_cell_mean_filled_min", 58.0))
            or bright_ratio > float(self.config.get("jigsaw_cell_bright_ratio_min", 0.10))
            or (sat_ratio > float(self.config.get("jigsaw_cell_saturation_ratio_min", 0.12)) and std > 14.0)
            or edge_ratio > float(self.config.get("jigsaw_cell_edge_ratio_min", 0.070))
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
        if grid_bounds is not None:
            _, _, gw, gh = grid_bounds
            half_w = max(18, int(gw * 0.10))
            half_h = max(18, int(gh * 0.13))
        else:
            half_w, half_h = 24, 24
        frame_h, frame_w = frame.shape[:2]
        y1, y2 = max(0, cy - half_h), min(frame_h, cy + half_h)
        x1, x2 = max(0, cx - half_w), min(frame_w, cx + half_w)
        color_crop = frame[y1:y2, x1:x2]
        crop = gray[y1:y2, x1:x2] if gray is not None else cv2.cvtColor(color_crop, cv2.COLOR_BGR2GRAY)
        if crop.size == 0:
            return True, 0.0
        mean = float(np.mean(crop))
        std = float(np.std(crop))
        hsv = cv2.cvtColor(color_crop, cv2.COLOR_BGR2HSV)
        sat_ratio = float(np.mean(hsv[:, :, 1] > int(self.config.get("jigsaw_slot_saturation_min", 45))))
        bright_ratio = float(np.mean(crop > int(self.config.get("jigsaw_slot_bright_min", 66))))
        edges = cv2.Canny(crop, 35, 90)
        edge_ratio = float(np.mean(edges > 0))

        empty = (
            mean < float(self.config.get("jigsaw_empty_slot_mean_max", 48.0))
            and std < float(self.config.get("jigsaw_empty_slot_std_max", 24.0))
            and sat_ratio < float(self.config.get("jigsaw_empty_slot_saturation_ratio_max", 0.10))
            and bright_ratio < float(self.config.get("jigsaw_empty_slot_bright_ratio_max", 0.08))
            and edge_ratio < float(self.config.get("jigsaw_empty_slot_edge_ratio_max", 0.065))
        )
        available_score = min(1.0, max(bright_ratio * 3.0, sat_ratio * 2.5, edge_ratio * 5.0, std / 55.0))
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
            self._template_cache[key] = None
            return None
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
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
        x_offset = int(self.config.get("jigsaw_crate_slot_x_offset", 50))
        special_y_offset = int(self.config.get("jigsaw_special_crate_slot_y_offset", -20))
        normal_y_offset = int(self.config.get("jigsaw_normal_crate_slot_y_offset", 35))
        return {
            "special": (right_mid_x + x_offset, right_mid_y + special_y_offset),
            "normal": (right_mid_x + x_offset, right_mid_y + normal_y_offset),
        }

    @staticmethod
    def _crate_order(priority: str) -> List[str]:
        if priority == "normal_first":
            return ["normal"]
        if priority == "special_first":
            return ["special", "normal"]
        return ["normal"]

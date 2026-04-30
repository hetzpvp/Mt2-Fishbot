"""
Fish Detector class for the Fishing Bot
Uses computer vision (HSV color detection) for fish and game element detection
Optimized for maximum performance
"""

from typing import Optional, Tuple

import cv2
import numpy as np


class FishDetector:
    """Detects fish and game elements using computer vision (HSV color detection)
    Optimized with cached function references and minimal allocations"""
    
    # Class-level cached function references for speed
    _cvtColor = cv2.cvtColor
    _inRange = cv2.inRange
    _countNonZero = cv2.countNonZero
    _findContours = cv2.findContours
    _boundingRect = cv2.boundingRect
    _contourArea = cv2.contourArea
    _moments = cv2.moments
    _connectedComponentsWithStats = cv2.connectedComponentsWithStats
    _COLOR_BGR2HSV = cv2.COLOR_BGR2HSV
    _RETR_EXTERNAL = cv2.RETR_EXTERNAL
    _CHAIN_APPROX_SIMPLE = cv2.CHAIN_APPROX_SIMPLE
    _CC_STAT_AREA = cv2.CC_STAT_AREA
    
    def __init__(self):
        # HSV color range for fish (blue-ish) - use dtype for faster comparison
        self.fish_color_lower = np.array([97, 130, 108], dtype=np.uint8)
        self.fish_color_upper = np.array([110, 146, 133], dtype=np.uint8)
        
        # HSV color range for minigame window background (cyan)
        self.window_color_lower = np.array([98, 170, 189], dtype=np.uint8)
        self.window_color_upper = np.array([106, 255, 250], dtype=np.uint8)
    
    def find_fishing_window_bounds(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        """Finds the bounding box of the fishing window. Returns (x, y, width, height) or None."""
        # Local references for speed
        cvtColor = FishDetector._cvtColor
        inRange = FishDetector._inRange
        findContours = FishDetector._findContours
        boundingRect = FishDetector._boundingRect
        contourArea = FishDetector._contourArea
        
        hsv = cvtColor(frame, FishDetector._COLOR_BGR2HSV)
        mask = inRange(hsv, self.window_color_lower, self.window_color_upper)
        contours, _ = findContours(mask, FishDetector._RETR_EXTERNAL, FishDetector._CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return None
        
        largest_contour = max(contours, key=contourArea)
        x, y, w, h = boundingRect(largest_contour)
        
        if w > 50 and h > 50:
            return (x, y, w, h)
        return None
    
    # Minimum cyan-pixel count to consider the minigame window active.
    _WINDOW_PIXEL_THRESHOLD = 10000
    # Below this many fish-color pixels, no real fish blob is present — skip
    # the connected-components pass entirely.
    _FISH_PIXEL_THRESHOLD = 24

    def detect_window_and_fish(self, frame: np.ndarray) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Combined detection: single HSV conversion for both window and fish.
        Optimized with local references and early exits.
        Returns: (window_active, fish_position or None)"""
        # Local references for speed (avoid repeated attribute lookups)
        cvtColor = FishDetector._cvtColor
        inRange = FishDetector._inRange
        countNonZero = FishDetector._countNonZero
        ccStats = FishDetector._connectedComponentsWithStats
        AREA = FishDetector._CC_STAT_AREA

        hsv = cvtColor(frame, FishDetector._COLOR_BGR2HSV)

        # Check window first - early exit if not active.
        # A fully visible minigame window produces ~10000+ cyan pixels; below this
        # threshold we treat it as absent to avoid false positives on small reflections.
        window_mask = inRange(hsv, self.window_color_lower, self.window_color_upper)
        if countNonZero(window_mask) <= FishDetector._WINDOW_PIXEL_THRESHOLD:
            return (False, None)

        # Cheap upfront pixel count avoids the connectedComponentsWithStats call
        # (allocates labels image + stats + centroids arrays) when the HSV
        # threshold caught only noise.
        fish_mask = inRange(hsv, self.fish_color_lower, self.fish_color_upper)
        if countNonZero(fish_mask) < FishDetector._FISH_PIXEL_THRESHOLD:
            return (True, None)

        # connectivity=4 is ~1.5x faster than 8 and produces equivalent results
        # for the compact, solid-color fish blob.
        num_labels, _, stats, centroids = ccStats(fish_mask, connectivity=4)
        if num_labels <= 1:
            return (True, None)

        # Label 0 is background; find largest foreground blob via numpy argmax on areas
        areas = stats[1:, AREA]
        largest_idx = int(areas.argmax()) + 1
        cx, cy = centroids[largest_idx]
        return (True, (int(cx), int(cy)))

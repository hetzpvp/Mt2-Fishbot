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
    _COLOR_BGR2HSV = cv2.COLOR_BGR2HSV
    _RETR_EXTERNAL = cv2.RETR_EXTERNAL
    _CHAIN_APPROX_SIMPLE = cv2.CHAIN_APPROX_SIMPLE
    
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
    
    def detect_window_and_fish(self, frame: np.ndarray) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Combined detection: single HSV conversion for both window and fish.
        Optimized with local references and early exits.
        Returns: (window_active, fish_position or None)"""
        # Local references for speed (avoid repeated attribute lookups)
        cvtColor = FishDetector._cvtColor
        inRange = FishDetector._inRange
        countNonZero = FishDetector._countNonZero
        findContours = FishDetector._findContours
        contourArea = FishDetector._contourArea
        moments = FishDetector._moments
        
        hsv = cvtColor(frame, FishDetector._COLOR_BGR2HSV)
        
        # Check window first - early exit if not active
        window_mask = inRange(hsv, self.window_color_lower, self.window_color_upper)
        if countNonZero(window_mask) <= 10000:
            return (False, None)
        
        # Find fish using same HSV
        fish_mask = inRange(hsv, self.fish_color_lower, self.fish_color_upper)
        contours, _ = findContours(fish_mask, FishDetector._RETR_EXTERNAL, FishDetector._CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return (True, None)
        
        largest_contour = max(contours, key=contourArea)
        M = moments(largest_contour)
        m00 = M["m00"]
        if m00 != 0:
            return (True, (int(M["m10"] / m00), int(M["m01"] / m00)))
        
        return (True, None)

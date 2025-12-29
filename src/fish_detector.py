"""
Fish Detector class for the Fishing Bot
Uses computer vision (HSV color detection) for fish and game element detection
"""

from typing import Optional, Tuple

import cv2
import numpy as np


class FishDetector:
    """Detects fish and game elements using computer vision (HSV color detection)"""
    
    def __init__(self):
        # HSV color range for fish (blue-ish)
        self.fish_color_lower = np.array([97, 130, 108])
        self.fish_color_upper = np.array([110, 146, 133])
        
        # HSV color range for minigame window background (cyan)
        self.window_color_lower = np.array([98, 170, 189])
        self.window_color_upper = np.array([106, 255, 250])
    
    def find_fishing_window_bounds(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        """Finds the bounding box of the fishing window. Returns (x, y, width, height) or None."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.window_color_lower, self.window_color_upper)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return None
        
        largest_contour = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(largest_contour)
        
        if w > 50 and h > 50:
            return (x, y, w, h)
        return None
    
    def detect_window_and_fish(self, frame: np.ndarray) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Combined detection: single HSV conversion for both window and fish.
        Returns: (window_active, fish_position or None)"""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # Check window first
        window_mask = cv2.inRange(hsv, self.window_color_lower, self.window_color_upper)
        if cv2.countNonZero(window_mask) <= 10000:
            return (False, None)
        
        # Find fish using same HSV
        fish_mask = cv2.inRange(hsv, self.fish_color_lower, self.fish_color_upper)
        contours, _ = cv2.findContours(fish_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return (True, None)
        
        largest_contour = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest_contour)
        if M["m00"] != 0:
            return (True, (int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])))
        
        return (True, None)

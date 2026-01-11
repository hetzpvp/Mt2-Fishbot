"""
Fish Detector class for the Fishing Bot
Uses computer vision for fish and game element detection
Optimized for maximum performance
"""

import os
from typing import Optional, Tuple

import cv2
import numpy as np


class FishDetector:
    """Detects fish and game elements using computer vision
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
    _COLOR_BGR2GRAY = cv2.COLOR_BGR2GRAY
    _RETR_EXTERNAL = cv2.RETR_EXTERNAL
    _CHAIN_APPROX_SIMPLE = cv2.CHAIN_APPROX_SIMPLE
    
    # Class-level template cache for Aelys2 minigame
    _window_template = None  # assets/1.png
    _target_template_2 = None  # assets/2.png
    _target_template_3 = None  # assets/3.png
    
    def __init__(self):
        # HSV color range for fish (blue-ish) - use dtype for faster comparison
        self.fish_color_lower = np.array([97, 130, 108], dtype=np.uint8)
        self.fish_color_upper = np.array([110, 146, 133], dtype=np.uint8)
        
        # HSV color range for minigame window background (cyan)
        self.window_color_lower = np.array([98, 170, 189], dtype=np.uint8)
        self.window_color_upper = np.array([106, 255, 250], dtype=np.uint8)
        
        # Load templates if not already loaded
        self._load_aelys2_templates()
    
    def _load_aelys2_templates(self):
        """Load Aelys2 minigame templates (1.png, 2.png, 3.png)"""
        if FishDetector._window_template is None:
            try:
                # Get assets directory
                from utils import get_resource_path
                
                # Load 1.png (window detection) - keep as BGR color
                template1_path = get_resource_path("assets/1.png")
                if os.path.exists(template1_path):
                    img = cv2.imread(template1_path)
                    if img is not None:
                        FishDetector._window_template = img
                
                # Load 2.png (target detection) - keep as BGR color
                template2_path = get_resource_path("assets/2.png")
                if os.path.exists(template2_path):
                    img = cv2.imread(template2_path)
                    if img is not None:
                        FishDetector._target_template_2 = img
                
                # Load 3.png (target detection) - keep as BGR color
                template3_path = get_resource_path("assets/3.png")
                if os.path.exists(template3_path):
                    img = cv2.imread(template3_path)
                    if img is not None:
                        FishDetector._target_template_3 = img
            except Exception:
                pass
    
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
    
    def detect_aelys2_window(self, frame: np.ndarray) -> bool:
        """Detects Aelys2 minigame window using template matching with 1.png
        Returns: True if window detected, False otherwise"""
        if FishDetector._window_template is None:
            return False
        
        try:
            # Convert frame to grayscale for robust matching
            if len(frame.shape) == 3:
                if frame.shape[2] == 4:
                    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
                else:
                    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                frame_gray = frame
            
            # Convert template to grayscale
            template_gray = cv2.cvtColor(FishDetector._window_template, cv2.COLOR_BGR2GRAY)
            
            # Template matching
            result = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            
            # Lower threshold for better detection (70%)
            return max_val >= 0.70
        except Exception:
            return False
    
    def detect_aelys2_targets(self, frame: np.ndarray) -> bool:
        """Detects if 2.png or 3.png appears in the frame with 70% confidence
        Returns: True if either target detected, False otherwise"""
        if FishDetector._target_template_2 is None and FishDetector._target_template_3 is None:
            return False
        
        try:
            # Convert frame to grayscale for robust matching
            if len(frame.shape) == 3:
                if frame.shape[2] == 4:
                    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
                else:
                    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                frame_gray = frame
            
            # Check template 2
            if FishDetector._target_template_2 is not None:
                template_gray = cv2.cvtColor(FishDetector._target_template_2, cv2.COLOR_BGR2GRAY)
                result = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val >= 0.70:
                    return True
            
            # Check template 3
            if FishDetector._target_template_3 is not None:
                template_gray = cv2.cvtColor(FishDetector._target_template_3, cv2.COLOR_BGR2GRAY)
                result = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val >= 0.70:
                    return True
            
            return False
        except Exception:
            return False

"""
Utility functions and constants for the Fishing Bot
"""

import os
import sys
import threading
import winsound

# Thread synchronization for mouse/keyboard - prevents race conditions
input_lock = threading.Lock()

# Max simultaneous game windows
MAX_WINDOWS = 8

# Debug mode - enable/disable IgnoredPositionsWindow
DEBUG_MODE_EN = True

# Debug prints - enable/disable verbose debug print statements
DEBUG_PRINTS = True


def get_resource_path(filename: str) -> str:
    """Get the path to a bundled resource (works both in dev and in PyInstaller exe)
    
    Assets like images (.gif, .ico, .jpg, .png) are looked up in the assets folder.
    """
    if hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle
        # PyInstaller extracts assets to _MEIPASS/assets/ based on build.spec configuration
        if filename.endswith(('.gif', '.ico', '.jpg', '.png')) or filename == 'assets':
            # Asset files go in assets subfolder
            return os.path.join(sys._MEIPASS, 'assets', filename) if filename != 'assets' else os.path.join(sys._MEIPASS, 'assets')
        else:
            # Other files go in root
            return os.path.join(sys._MEIPASS, filename)
    else:
        # Running as script - check if file should be in assets folder
        base_dir = os.path.dirname(os.path.dirname(__file__))  # Go up from src to project root
        
        # Check if it's an asset file (images, icons, etc.)
        if filename.endswith(('.gif', '.ico', '.jpg', '.png')) or filename == 'assets':
            return os.path.join(base_dir, 'assets', filename) if filename != 'assets' else os.path.join(base_dir, 'assets')
        
        # For other files, check assets folder first
        assets_path = os.path.join(base_dir, 'assets', filename)
        if os.path.exists(assets_path):
            return assets_path
        
        # Default: look in project root
        return os.path.join(base_dir, filename)


def play_rickroll_beep():
    """Plays a Rick Roll-themed beep sequence (intro)."""
    melody = [
        (554, 600),   # C5s - strong opening
        (622, 1000),  # E5f - longer note
        (622, 600),   # E5f
        (698, 600),   # F5
        (831, 100),   # A5f - quick notes
        (740, 100),   # F5s
        (698, 100),   # F5
        (622, 100),   # E5f
        (554, 600),   # C5s
        (622, 800),   # E5f - held note
        (415, 400),   # A4f - step down
        (415, 200),   # A4f
    ]
    for frequency, duration in melody:
        winsound.Beep(frequency, duration)

"""
Build script for MT2 Fishing Bot
Handles versioning and PyInstaller build process

Usage:
    python build.py          - Build with current version
    python build.py --clean  - Clean build artifacts before building
"""

import os
import re
import sys
import shutil
import subprocess

# Import version from version.py
from version import VERSION

# File paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SCRIPT_DIR, "src")
MAIN_SCRIPT = os.path.join(SRC_DIR, "fishing_bot.py")
SPEC_FILE = os.path.join(SCRIPT_DIR, "build.spec")

# Patterns to find and replace
APP_NAME_BASE = "Fishing Puzzle Player"
APP_NAME_VERSIONED = f"Fishing Puzzle Player v{VERSION}"


def update_bot_version_in_gui():
    """Updates BOT_VERSION in bot_gui.py to match version.py."""
    print(f"Updating BOT_VERSION in bot_gui.py...")
    
    bot_gui_path = os.path.join(SRC_DIR, "bot_gui.py")
    
    if not os.path.exists(bot_gui_path):
        print(f"  Warning: {bot_gui_path} not found")
        return
    
    with open(bot_gui_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern to match: BOT_VERSION = "X.X.X"  # Version for config validation
    # Captures the version number
    pattern = r'BOT_VERSION\s*=\s*"[\d.]+"(\s*#\s*Version for config validation)?'
    replacement = f'BOT_VERSION = "{VERSION}"  # Version for config validation'
    
    new_content = re.sub(pattern, replacement, content)
    
    if new_content != content:
        with open(bot_gui_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"  Updated BOT_VERSION to: {VERSION}")
    else:
        print("  No changes needed (already up to date)")


def update_version_in_spec():
    """Updates version in build.spec to match version.py."""
    print(f"Updating version in build.spec...")
    
    if not os.path.exists(SPEC_FILE):
        print(f"  Warning: {SPEC_FILE} not found")
        return
    
    with open(SPEC_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern to match: name='Fishing Puzzle Player vX.X.X',
    pattern = r"name='Fishing Puzzle Player v[\d.]+'"
    replacement = f"name='Fishing Puzzle Player v{VERSION}'"
    
    new_content = re.sub(pattern, replacement, content)
    
    if new_content != content:
        with open(SPEC_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"  Updated build.spec version to: {VERSION}")
    else:
        print("  No changes needed (already up to date)")


def clean_build_artifacts():
    """Removes build artifacts from previous builds."""
    print("Cleaning build artifacts...")
    
    dirs_to_clean = ['build', 'dist', '__pycache__', os.path.join('src', '__pycache__')]
    
    for dir_name in dirs_to_clean:
        dir_path = os.path.join(SCRIPT_DIR, dir_name)
        if os.path.exists(dir_path):
            print(f"  Removing {dir_name}/")
            shutil.rmtree(dir_path)
    
    # Remove .spec backup files if any
    for f in os.listdir(SCRIPT_DIR):
        if f.endswith('.spec.bak'):
            os.remove(os.path.join(SCRIPT_DIR, f))
            print(f"  Removed {f}")


def run_pyinstaller():
    """Runs PyInstaller with the spec file."""
    print(f"\nBuilding executable with PyInstaller...")
    print(f"  Version: {VERSION}")
    print(f"  Output name: {APP_NAME_VERSIONED}.exe")
    print()
    
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'PyInstaller', '--clean', SPEC_FILE],
            cwd=SCRIPT_DIR,
            check=True
        )
        print(f"\n{'='*50}")
        print(f"BUILD SUCCESSFUL!")
        print(f"Executable: dist/{APP_NAME_VERSIONED}.exe")
        print(f"{'='*50}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\nBUILD FAILED with error code {e.returncode}")
        return False
    except FileNotFoundError:
        print("\nERROR: PyInstaller not found!")
        print("Install it with: pip install pyinstaller")
        return False


def main():
    print(f"{'='*50}")
    print(f"MT2 Fishing Bot Build Script")
    print(f"Version: {VERSION}")
    print(f"{'='*50}\n")
    
    # Check for --clean flag
    if '--clean' in sys.argv:
        clean_build_artifacts()
        print()
    
    # Update BOT_VERSION from version.py
    update_bot_version_in_gui()
    print()
    
    # Update version in build.spec
    update_version_in_spec()
    print()
    
    # Run PyInstaller
    success = run_pyinstaller()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

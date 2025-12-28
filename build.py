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
MAIN_SCRIPT = os.path.join(SCRIPT_DIR, "Fishing puzzle player.py")
SPEC_FILE = os.path.join(SCRIPT_DIR, "build.spec")

# Patterns to find and replace
APP_NAME_BASE = "Fishing Puzzle Player"
APP_NAME_VERSIONED = f"Fishing Puzzle Player v{VERSION}"


def update_version_in_script():
    """Updates the version string in the main Python script."""
    print(f"Updating version in {MAIN_SCRIPT}...")
    
    with open(MAIN_SCRIPT, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern to match "Fishing Puzzle Player" or "Fishing puzzle player" with optional version (vX.X.X)
    # Case-insensitive matching for "puzzle player" part
    pattern = r'Fishing [Pp]uzzle [Pp]layer(?:\s+v[\d.]+)?'
    
    # Count replacements
    matches = re.findall(pattern, content)
    if matches:
        print(f"  Found {len(matches)} occurrence(s) to update")
        for m in matches:
            print(f"    - '{m}'")
    
    # Replace all occurrences
    new_content = re.sub(pattern, APP_NAME_VERSIONED, content)
    
    if new_content != content:
        with open(MAIN_SCRIPT, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"  Updated to: {APP_NAME_VERSIONED}")
    else:
        print("  No changes needed (already up to date)")


def update_version_in_spec():
    """Updates the exe name in the spec file to include version."""
    print(f"Updating version in {SPEC_FILE}...")
    
    with open(SPEC_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Update the name in EXE section
    # Pattern matches: name='Fishing Puzzle Player' with optional version
    pattern = r"name='Fishing Puzzle Player(?:\s+v[\d.]+)?'"
    replacement = f"name='{APP_NAME_VERSIONED}'"
    
    new_content = re.sub(pattern, replacement, content)
    
    if new_content != content:
        with open(SPEC_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"  Updated exe name to: {APP_NAME_VERSIONED}")
    else:
        print("  No changes needed (already up to date)")


def clean_build_artifacts():
    """Removes build artifacts from previous builds."""
    print("Cleaning build artifacts...")
    
    dirs_to_clean = ['build', 'dist', '__pycache__']
    
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
    
    # Update version strings
    update_version_in_script()
    update_version_in_spec()
    
    # Run PyInstaller
    success = run_pyinstaller()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

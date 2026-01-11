"""
Build script for MT2 Fishing Bot
Handles versioning and PyInstaller build process

Usage:
    python build.py                      - Build with current version
    python build.py --clean              - Clean build artifacts before building
    python build.py --version X.X.X      - Build with specific version (updates version.py)
    python build.py --no-build           - Only update versions without building
    python build.py --verify             - Verify build environment matches required versions exactly
    python build.py --update-versions    - Update required versions to currently installed packages
    python build.py --setup              - Install all required dependencies in .venv
    python build.py --analyze-size       - Analyze what's taking up space in the build
"""

import os
import re
import sys
import shutil
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

# File paths (defined early for venv check)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(SCRIPT_DIR, ".venv")

def ensure_venv():
    """Ensures the script is running inside the .venv virtual environment.
    If not, creates the venv if needed and re-executes the script within it."""
    
    # Check if we're already in the venv
    venv_python = os.path.join(VENV_DIR, "Scripts", "python.exe") if sys.platform == 'win32' else os.path.join(VENV_DIR, "bin", "python")
    
    # Normalize paths for comparison
    current_python = os.path.normcase(os.path.normpath(sys.executable))
    expected_python = os.path.normcase(os.path.normpath(venv_python))
    
    if current_python == expected_python:
        return  # Already in venv
    
    # Check if venv exists
    if not os.path.exists(venv_python):
        print(f"Creating virtual environment at {VENV_DIR}...")
        try:
            subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
            print(f"Virtual environment created successfully!")
        except subprocess.CalledProcessError as e:
            print(f"Failed to create virtual environment: {e}")
            sys.exit(1)
    
    # Re-execute this script using the venv Python
    print(f"Switching to virtual environment: {VENV_DIR}")
    try:
        result = subprocess.run([venv_python] + sys.argv, cwd=SCRIPT_DIR)
        sys.exit(result.returncode)
    except Exception as e:
        print(f"Failed to run in virtual environment: {e}")
        sys.exit(1)

# Ensure we're running in the virtual environment before doing anything else
ensure_venv()

# Import version from version.py (after venv is ensured)
from version import VERSION

# File paths (SCRIPT_DIR and VENV_DIR already defined above)
SRC_DIR = os.path.join(SCRIPT_DIR, "src")
MAIN_SCRIPT = os.path.join(SRC_DIR, "fishing_bot.py")
SPEC_FILE = os.path.join(SCRIPT_DIR, "build.spec")
VERSION_FILE = os.path.join(SCRIPT_DIR, "version.py")

# Patterns to find and replace
APP_NAME_BASE = "Fishing Puzzle Player"
APP_NAME_VERSIONED = f"Fishing Puzzle Player v{VERSION}"

# ANSI color codes for better output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    
    @staticmethod
    def disable():
        """Disable colors (for Windows CMD compatibility)"""
        Colors.HEADER = ''
        Colors.OKBLUE = ''
        Colors.OKCYAN = ''
        Colors.OKGREEN = ''
        Colors.WARNING = ''
        Colors.FAIL = ''
        Colors.ENDC = ''
        Colors.BOLD = ''

# Disable colors on Windows if not supported
if sys.platform == 'win32' and not os.environ.get('ANSICON'):
    Colors.disable()


def print_section(title):
    """Print a section header."""
    print(f"\n{Colors.OKCYAN}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.OKCYAN}{Colors.BOLD}{title}{Colors.ENDC}")
    print(f"{Colors.OKCYAN}{Colors.BOLD}{'='*60}{Colors.ENDC}\n")


def print_success(message):
    """Print a success message."""
    print(f"{Colors.OKGREEN}✓ {message}{Colors.ENDC}")


def print_warning(message):
    """Print a warning message."""
    print(f"{Colors.WARNING}⚠ {message}{Colors.ENDC}")


def print_error(message):
    """Print an error message."""
    print(f"{Colors.FAIL}✗ {message}{Colors.ENDC}")


def print_info(message):
    """Print an info message."""
    print(f"{Colors.OKBLUE}ℹ {message}{Colors.ENDC}")


def verify_dependencies():
    """Verify that all required dependencies are installed with exact versions."""
    print_info("Verifying dependencies...")
    
    # Required dependencies with exact versions from current environment
    # Update these versions to match your production environment
    # BEGIN_REQUIRED_VERSIONS
    required_versions = {
        'Python': '3.14.2',
        'PyInstaller': '6.17.0',
        'Pillow': '12.1.0',
        'pynput': '1.8.1',
        'opencv-python-headless': '4.12.0',
        'numpy': '2.2.6',
        'psutil': '7.2.1',
        'PyAutoGUI': '0.9.54',
        'mss': '10.1.0',
        'pygetwindow': '0.0.9',
    }
    # END_REQUIRED_VERSIONS
    
    # Check Python version
    import platform
    current_python = platform.python_version()
    expected_python = required_versions['Python']
    
    if current_python == expected_python:
        print_success(f"Python {current_python} (matches required {expected_python})")
    else:
        print_error(f"Python {current_python} (expected {expected_python})")
        print_warning(f"Python version mismatch! Current: {current_python}, Required: {expected_python}")
    
    # Module name mapping for imports
    import_names = {
        'PyInstaller': 'PyInstaller',
        'Pillow': 'PIL',
        'pynput': 'pynput',
        'opencv-python-headless': 'cv2',
        'numpy': 'numpy',
        'psutil': 'psutil',
        'PyAutoGUI': 'pyautogui',
        'mss': 'mss',
        'pygetwindow': 'pygetwindow',
    }
    
    missing = []
    version_mismatch = []
    
    for package_name, import_name in import_names.items():
        try:
            module = __import__(import_name)
            
            # Try to get version
            version = None
            if hasattr(module, '__version__'):
                version = module.__version__
            elif hasattr(module, 'VERSION'):
                version = module.VERSION
            elif hasattr(module, 'version'):
                if callable(module.version):
                    version = module.version()
                else:
                    version = module.version
            
            # For packages without __version__, try pkg_resources or importlib.metadata
            if not version:
                try:
                    import importlib.metadata
                    version = importlib.metadata.version(package_name)
                except:
                    try:
                        import pkg_resources
                        version = pkg_resources.get_distribution(package_name).version
                    except:
                        version = "unknown"
            
            expected_version = required_versions.get(package_name, "any")
            
            if version == "unknown":
                print_warning(f"{package_name} is installed (version: unknown)")
            elif version == expected_version:
                print_success(f"{package_name} {version} (matches required)")
            else:
                print_error(f"{package_name} {version} (expected {expected_version})")
                version_mismatch.append(f"{package_name} (current: {version}, expected: {expected_version})")
                
        except ImportError:
            print_error(f"{package_name} is NOT installed")
            missing.append(package_name)
    
    print()
    
    # Report issues
    if current_python != expected_python:
        print_error(f"Python version mismatch: {current_python} vs {expected_python}")
        return False
    
    if missing:
        print_error(f"Missing dependencies: {', '.join(missing)}")
        print_info("Install missing packages with: pip install " + " ".join(missing))
        return False
    
    if version_mismatch:
        print_error(f"Version mismatches found:")
        for mismatch in version_mismatch:
            print_error(f"  - {mismatch}")
        print_info("\nTo install exact versions, run:")
        print_info("pip install -r requirements.txt")
        print_info("\nOr install individually:")
        for package_name in import_names.keys():
            expected = required_versions.get(package_name)
            if expected:
                print_info(f"  pip install {package_name}=={expected}")
        return False
    
    print_success("All dependencies match required versions!")
    return True


def verify_files():
    """Verify that all required files exist."""
    print_info("Verifying required files...")
    
    required_files = [
        MAIN_SCRIPT,
        SPEC_FILE,
        VERSION_FILE,
        os.path.join(SCRIPT_DIR, 'assets', 'monkey.ico'),
        os.path.join(SCRIPT_DIR, 'assets', 'monkey-eating.gif'),
    ]
    
    missing = []
    for filepath in required_files:
        if os.path.exists(filepath):
            print_success(f"Found: {os.path.relpath(filepath, SCRIPT_DIR)}")
        else:
            print_error(f"Missing: {os.path.relpath(filepath, SCRIPT_DIR)}")
            missing.append(filepath)
    
    if missing:
        print_error(f"\n{len(missing)} required file(s) missing!")
        return False
    
    print_success("\nAll required files are present!")
    return True


def update_version_file(new_version):
    """Updates VERSION in version.py to the specified version."""
    print_info(f"Updating version.py to {new_version}...")
    
    if not os.path.exists(VERSION_FILE):
        print_error(f"{VERSION_FILE} not found")
        return False
    
    try:
        with open(VERSION_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Pattern to match: VERSION = "X.X.X"
        pattern = r'VERSION\s*=\s*"[\d.]+"'
        replacement = f'VERSION = "{new_version}"'
        
        new_content = re.sub(pattern, replacement, content)
        
        if new_content == content:
            print_warning("version.py already has this version")
            return True
        
        with open(VERSION_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print_success(f"Updated version.py to: {new_version}")
        
        # Update the global VERSION variable
        global VERSION, APP_NAME_VERSIONED
        VERSION = new_version
        APP_NAME_VERSIONED = f"Fishing Puzzle Player v{VERSION}"
        
        return True
    except Exception as e:
        print_error(f"Failed to update version.py: {e}")
        return False


def update_bot_version_in_gui():
    """Updates BOT_VERSION in bot_gui.py to match version.py."""
    print_info(f"Updating BOT_VERSION in bot_gui.py...")
    
    bot_gui_path = os.path.join(SRC_DIR, "bot_gui.py")
    
    if not os.path.exists(bot_gui_path):
        print_error(f"{bot_gui_path} not found")
        return False
    
    try:
        with open(bot_gui_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Pattern to match: BOT_VERSION = "X.X.X"  # Version for config validation
        # Captures the version number and optional comment
        pattern = r'BOT_VERSION\s*=\s*"[\d.]+"(\s*#[^\n]*)?'
        replacement = f'BOT_VERSION = "{VERSION}"  # Version for config validation and GUI display'
        
        new_content = re.sub(pattern, replacement, content)
        
        if new_content == content:
            print_success("BOT_VERSION already up to date")
            return True
        
        with open(bot_gui_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print_success(f"Updated BOT_VERSION to: {VERSION}")
        return True
    except Exception as e:
        print_error(f"Failed to update bot_gui.py: {e}")
        return False



def update_version_in_spec():
    """Updates version in build.spec to match version.py."""
    print_info(f"Updating version in build.spec...")
    
    if not os.path.exists(SPEC_FILE):
        print_error(f"{SPEC_FILE} not found")
        return False
    
    try:
        with open(SPEC_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Pattern to match: name='Fishing Puzzle Player vX.X.X',
        pattern = r"name='Fishing Puzzle Player v[\d.]+'"
        replacement = f"name='Fishing Puzzle Player v{VERSION}'"
        
        new_content = re.sub(pattern, replacement, content)
        
        if new_content == content:
            print_success("build.spec already up to date")
            return True
        
        with open(SPEC_FILE, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print_success(f"Updated build.spec version to: {VERSION}")
        return True
    except Exception as e:
        print_error(f"Failed to update build.spec: {e}")
        return False



def clean_build_artifacts():
    """Removes build artifacts from previous builds."""
    print_info("Cleaning build artifacts...")
    
    dirs_to_clean = ['build', 'dist', '__pycache__', os.path.join('src', '__pycache__')]
    
    cleaned = 0
    for dir_name in dirs_to_clean:
        dir_path = os.path.join(SCRIPT_DIR, dir_name)
        if os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
                print_success(f"Removed {dir_name}/")
                cleaned += 1
            except Exception as e:
                print_warning(f"Could not remove {dir_name}/: {e}")
    
    # Remove .spec backup files if any
    for f in os.listdir(SCRIPT_DIR):
        if f.endswith('.spec.bak'):
            try:
                os.remove(os.path.join(SCRIPT_DIR, f))
                print_success(f"Removed {f}")
                cleaned += 1
            except Exception as e:
                print_warning(f"Could not remove {f}: {e}")
    
    if cleaned == 0:
        print_info("No artifacts to clean")
    else:
        print_success(f"Cleaned {cleaned} artifact(s)")
    
    return True


def run_pyinstaller():
    """Runs PyInstaller with the spec file."""
    print_section("Building Executable with PyInstaller")
    print_info(f"Version: {VERSION}")
    print_info(f"Output name: {APP_NAME_VERSIONED}.exe")
    print_info(f"Spec file: {os.path.basename(SPEC_FILE)}")
    print()
    
    try:
        # Check if PyInstaller is available
        result = subprocess.run(
            [sys.executable, '-m', 'PyInstaller', '--version'],
            capture_output=True,
            text=True,
            check=True
        )
        pyinstaller_version = result.stdout.strip()
        print_info(f"PyInstaller version: {pyinstaller_version}")
        print()
        
        # Run PyInstaller build
        start_time = datetime.now()
        result = subprocess.run(
            [sys.executable, '-m', 'PyInstaller', '--clean', SPEC_FILE],
            cwd=SCRIPT_DIR,
            check=True
        )
        end_time = datetime.now()
        build_time = (end_time - start_time).total_seconds()
        
        # Check if output file exists
        output_path = os.path.join(SCRIPT_DIR, 'dist', f'{APP_NAME_VERSIONED}.exe')
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path) / (1024 * 1024)  # Size in MB
            print()
            print_success("PyInstaller build complete!")
            print_success(f"Executable: dist/{APP_NAME_VERSIONED}.exe")
            print_info(f"Uncompressed size: {file_size:.2f} MB")
            print_info(f"Build time: {build_time:.1f} seconds")
            return True
        else:
            print_error("Build completed but executable not found!")
            return False
            
    except subprocess.CalledProcessError as e:
        print()
        print_error(f"BUILD FAILED with error code {e.returncode}")
        print_warning("Check the output above for error details")
        return False
    except FileNotFoundError:
        print()
        print_error("PyInstaller not found!")
        print_info("Install it with: pip install pyinstaller")
        return False
    except Exception as e:
        print()
        print_error(f"Unexpected error during build: {e}")
        return False


def update_required_versions():
    """Update the required_versions dict in this file based on currently installed packages."""
    print_info("Detecting currently installed package versions...")
    
    import platform
    current_python = platform.python_version()
    
    # Package names for pip vs import names
    packages = {
        'PyInstaller': 'PyInstaller',
        'Pillow': 'PIL',
        'pynput': 'pynput',
        'opencv-python-headless': 'cv2',
        'numpy': 'numpy',
        'psutil': 'psutil',
        'PyAutoGUI': 'pyautogui',
        'mss': 'mss',
        'pygetwindow': 'pygetwindow',
    }
    
    detected_versions = {'Python': current_python}
    
    for package_name, import_name in packages.items():
        try:
            module = __import__(import_name)
            version = None
            
            # Try various version attributes
            if hasattr(module, '__version__'):
                version = module.__version__
            elif hasattr(module, 'VERSION'):
                version = module.VERSION
            elif hasattr(module, 'version'):
                if callable(module.version):
                    version = module.version()
                else:
                    version = module.version
            
            # Fallback to importlib.metadata
            if not version:
                try:
                    import importlib.metadata
                    version = importlib.metadata.version(package_name)
                except:
                    try:
                        import pkg_resources
                        version = pkg_resources.get_distribution(package_name).version
                    except:
                        version = None
            
            if version:
                detected_versions[package_name] = version
                print_success(f"{package_name}: {version}")
            else:
                print_warning(f"{package_name}: installed but version unknown")
        except ImportError:
            print_warning(f"{package_name}: not installed")
    
    print()
    print_info("Updating build.py with detected versions...")
    
    # Read current file
    build_file = os.path.abspath(__file__)
    try:
        with open(build_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Use markers to find the exact block to replace (avoids self-modification)
        start_marker = '# BEGIN_REQUIRED_VERSIONS'
        end_marker = '# END_REQUIRED_VERSIONS'
        
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)
        
        if start_idx == -1 or end_idx == -1:
            print_error("Could not find version markers in build.py")
            return False
        
        # Build the new required_versions dict string
        dict_items = []
        for pkg, ver in detected_versions.items():
            dict_items.append(f"        '{pkg}': '{ver}',")
        
        new_block_lines = [
            start_marker,
            '    required_versions = {',
        ]
        new_block_lines.extend(dict_items)
        new_block_lines.append('    }')
        new_block_lines.append('    ' + end_marker)
        new_block_str = '\n'.join(new_block_lines)
        
        # Replace the block between markers (inclusive)
        new_content = content[:start_idx] + new_block_str + content[end_idx + len(end_marker):]
        
        with open(build_file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print_success("Updated required_versions in build.py")
        print_info("Run 'python build.py --verify' to verify against new versions")
        return True
            
    except Exception as e:
        print_error(f"Failed to update build.py: {e}")
        return False


def install_dependencies():
    """Install all required dependencies in the virtual environment."""
    print_info("Installing required dependencies...")
    
    # List of packages to install
    packages = [
        'PyInstaller',
        'Pillow',
        'pynput',
        'opencv-python-headless',  # Headless version (no GUI dependencies)
        'numpy',
        'psutil',
        'PyAutoGUI',
        'mss',
        'pygetwindow',
    ]
    
    try:
        # Upgrade pip first
        print_info("Upgrading pip...")
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip'],
            check=True
        )
        print_success("pip upgraded")
        
        # Install all packages
        print_info(f"Installing {len(packages)} packages...")
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install'] + packages,
            check=True
        )
        
        print()
        print_success("All dependencies installed successfully!")
        print_info("Run 'python build.py --verify' to verify installation")
        print_info("Run 'python build.py --update-versions' to save installed versions")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to install dependencies: {e}")
        return False
    except Exception as e:
        print_error(f"Unexpected error installing dependencies: {e}")
        return False


def analyze_build_size():
    """Analyze what's taking up space in the build."""
    print_section("Build Size Analysis")
    
    dist_dir = os.path.join(SCRIPT_DIR, 'dist')
    build_dir = os.path.join(SCRIPT_DIR, 'build')
    
    if not os.path.exists(dist_dir):
        print_warning("No dist/ directory found. Build first!")
        return False
    
    print_info("Analyzing distribution size...")
    print()
    
    # Find the .exe file
    exe_files = [f for f in os.listdir(dist_dir) if f.endswith('.exe')]
    if not exe_files:
        print_warning("No .exe file found in dist/")
        return False
    
    exe_path = os.path.join(dist_dir, exe_files[0])
    exe_size = os.path.getsize(exe_path) / (1024 * 1024)
    
    print_success(f"Executable: {exe_files[0]}")
    print_info(f"Size: {exe_size:.2f} MB")
    print()
    
    # Analyze build folder if it exists
    if os.path.exists(build_dir):
        print()
        print_info("Top space consumers in build:")
        
        # Get sizes of subdirectories
        sizes = []
        for root, dirs, files in os.walk(build_dir):
            for d in dirs[:]:  # Copy list to allow modification
                dir_path = os.path.join(root, d)
                try:
                    size = sum(
                        os.path.getsize(os.path.join(r, f))
                        for r, _, fs in os.walk(dir_path)
                        for f in fs
                    ) / (1024 * 1024)
                    sizes.append((d, size))
                    dirs.remove(d)  # Don't recurse into subdirectories
                except:
                    pass
        
        # Show top 10
        sizes.sort(key=lambda x: x[1], reverse=True)
        for name, size in sizes[:10]:
            if size > 1:  # Only show if > 1 MB
                print(f"  • {name}: {size:.1f} MB")
    
    print()
    print_info("Size Reduction Tips:")
    print_info("  1. Use opencv-python-headless instead of opencv-python")
    print_info("  2. Exclude unused modules in build.spec")
    print_info("  3. Consider one-directory mode if size still too large")
    
    return True


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Build script for MT2 Fishing Bot',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python build.py                      # Build with current version
  python build.py --clean              # Clean and build
  python build.py --version 1.0.6      # Update to version 1.0.6 and build
  python build.py --verify             # Verify environment matches required versions
  python build.py --update-versions    # Update required versions to currently installed
  python build.py --setup              # Install all required dependencies
  python build.py --analyze-size       # Analyze build size
  python build.py --no-build           # Update versions without building
        '''
    )
    
    parser.add_argument('--clean', action='store_true',
                        help='Clean build artifacts before building')
    parser.add_argument('--version', type=str, metavar='X.X.X',
                        help='Set version number (updates version.py)')
    parser.add_argument('--no-build', action='store_true',
                        help='Only update version files without building')
    parser.add_argument('--verify', action='store_true',
                        help='Verify build environment and dependencies match required versions')
    parser.add_argument('--update-versions', action='store_true',
                        help='Update required versions in build.py to match currently installed packages')
    parser.add_argument('--setup', action='store_true',
                        help='Install all required dependencies in the virtual environment')
    parser.add_argument('--analyze-size', action='store_true',
                        help='Analyze what is taking up space in the build')
    
    return parser.parse_args()



def main():
    print_section("MT2 Fishing Bot Build Script")
    print_info(f"Current version: {VERSION}")
    print_info(f"Build date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Parse command line arguments
    args = parse_arguments()
    
    # Install dependencies if requested
    if args.setup:
        print()
        if not install_dependencies():
            sys.exit(1)
        sys.exit(0)
    
    # Analyze build size if requested
    if args.analyze_size:
        print()
        if not analyze_build_size():
            sys.exit(1)
        sys.exit(0)
    
    # Update required versions if requested
    if args.update_versions:
        print()
        if not update_required_versions():
            sys.exit(1)
        sys.exit(0)
    
    # Verify environment if requested
    if args.verify:
        print()
        if not verify_dependencies():
            sys.exit(1)
        print()
        if not verify_files():
            sys.exit(1)
        print()
        print_success("Build environment verification passed!")
        sys.exit(0)
    
    # Update version if specified
    if args.version:
        print()
        if not update_version_file(args.version):
            sys.exit(1)
    
    # Clean build artifacts if requested
    if args.clean:
        print()
        if not clean_build_artifacts():
            sys.exit(1)
    
    # Update versions in all files
    print()
    print_section("Updating Version Information")
    
    success = True
    success = update_bot_version_in_gui() and success
    success = update_version_in_spec() and success
    
    if not success:
        print_error("Failed to update version information")
        sys.exit(1)
    
    # Exit if --no-build flag is set
    if args.no_build:
        print()
        print_success("Version update completed (build skipped)")
        sys.exit(0)
    
    # Verify critical files before building
    print()
    print_section("Pre-Build Verification")
    if not verify_files():
        print_error("Pre-build verification failed")
        sys.exit(1)
    
    # Run PyInstaller
    print()
    success = run_pyinstaller()
    
    if not success:
        sys.exit(1)
    
    # Final summary
    print()
    print_section("BUILD COMPLETE!")
    
    exe_path = os.path.join(SCRIPT_DIR, 'dist', f'{APP_NAME_VERSIONED}.exe')
    if os.path.exists(exe_path):
        final_size = os.path.getsize(exe_path) / (1024 * 1024)
        print_success(f"Final executable: dist/{APP_NAME_VERSIONED}.exe")
        print_success(f"Final size: {final_size:.2f} MB")
        print()
        print_info("Your optimized executable is ready to use!")
    
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print_warning("Build cancelled by user")
        sys.exit(130)
    except Exception as e:
        print()
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

# MT2 Fishbot — Project Specifications

## Overview

An automated fishing bot for Metin2 that uses computer vision (no memory reading) to detect fishing minigame state and perform all required actions. Supports up to 8 simultaneous game windows, two fishing modes, and automatic inventory handling.

**Current version:** 1.0.5.1  
**Platform:** Windows only  
**Stack:** Python 3.14.2, OpenCV, Tkinter, mss, pynput, PyAutoGUI, PyInstaller

---

## Directory Structure

```
Mt2-Fishbot/
├── src/
│   ├── fishing_bot.py       # Core bot logic (~1367 lines)
│   ├── bot_gui.py           # Tkinter GUI (~2505 lines)
│   ├── fish_detector.py     # Computer vision detection (~94 lines)
│   ├── window_manager.py    # Window discovery & focus (~137 lines)
│   ├── utils.py             # Shared constants, helpers (~188 lines)
│   ├── debug_windows.py     # Live debug visualizers (~788 lines)
│   └── __init__.py
├── assets/                  # 48 images: fish sprites, item sprites, icons, GIFs
├── Jigsaw puzzle/           # Separate fishing jigsaw puzzle solver (dist builds in /dist)
├── dist/                    # PyInstaller executables (v1.0.0 → v1.0.5.1)
├── build.py                 # Build automation script
├── build.spec               # PyInstaller spec
├── bot_config.json          # Runtime configuration (user-editable)
├── version.py               # Version constant
└── gui_demo.png             # Screenshot for README
```

---

## Features Implemented

### Multi-Window Fishing
- Up to **8 simultaneous** Metin2 game windows, each on its own thread
- A single global `input_lock` (threading.Lock) serialises all mouse/keyboard input
- Lock fairness: a thread yields after 3 consecutive acquisitions so others get a turn

### Fishing Modes
| Mode | How it works |
|------|-------------|
| **Minigame** (default) | Detects cyan minigame window via HSV, finds blue fish dot inside it, clicks within 67 px radius |
| **Classic** | Multi-scale template matches `classic_fish.jpg`, waits a configurable delay, presses the reel-in key |

### Auto Fish Handling
- After each catch, captures the right side of the window (inventory area)
- Identifies the item via grayscale template matching (threshold 0.80, early-exit at 0.90)
- Color templates used to disambiguate visually similar fish (e.g. Goldfish vs Large_zander)
- Per-item action: **Keep**, **Open** (double-click), or **Drop** (drag to configured drop + confirm coords)

### Quick Skip
| Mode | Mechanism |
|------|-----------|
| **Horse** | Double CTRL+G (dismount → remount) |
| **Armor** | Right-click configured armor slot (equip/unequip) |

### Bait Management
- 4 configurable hotkeys (default: 1–4) cycle through bait tiers
- Global bait counter; auto-decrements and advances tier on depletion
- Sound alert (Rick Roll-themed PC speaker beep with ADSR envelope) on bot finish

### Configuration (bot_config.json)
All settings persist across sessions:
- `human_like_clicking` — randomised click timing and positional offsets
- `classic_fishing` + `classic_fishing_delay`
- `auto_fish_handling` + per-fish `fish_actions` map
- `quick_skip` + `quick_skip_mode`
- `drop_button_pos`, `confirm_button_pos`, `armor_slot_pos`
- `bait_keys`, `bait` count
- `accent_color`, `rgb_wave_active`
- `selected_windows` (8-slot array)

---

## Module Details

### `fishing_bot.py` — FishingBot class
One instance per game window. Main entry point is `play_game()`.

Key methods:
- `play_game()` — outer loop: bait → cast → wait for minigame → detect & click fish → handle catch → quick skip → repeat
- `atomic_capture_and_click()` — captures screen and clicks fish while holding `input_lock`
- `wait_for_minigame_window()` — polls until cyan window appears (or timeout)
- `wait_for_classic_fish()` — multi-scale template match loop
- `identify_item_in_inventory()` — template matching with color disambiguation
- `handle_caught_item()` — executes keep/open/drop action sequence
- `bait_and_cast()` — selects bait tier, casts, waits for rod animation
- `quickskip()` — horse or armor skip depending on config

Performance details:
- All templates loaded once at class level (shared across instances)
- Templates cropped by 7 px on each edge to focus on center content
- Half-dimensions pre-computed; bitwise `>> 1` used instead of `/ 2`
- Single HSV conversion per frame covers both window and fish detection
- Local variable aliases for frequently called functions avoid attribute lookups

### `fish_detector.py` — FishDetector class
Single-pass HSV detection, no template matching for the minigame itself.

HSV ranges:
| Target | H | S | V |
|--------|---|---|---|
| Fish (blue dot) | 97–110 | 130–146 | 108–133 |
| Minigame window (cyan) | 98–106 | 170–255 | 189–250 |

Methods:
- `find_fishing_window_bounds()` — contour detection on cyan mask
- `detect_window_and_fish()` — combined detection in one HSV call; returns window rect + fish position

### `bot_gui.py` — BotGUI + FishSelectionWindow
Full Tkinter UI.

- `BotGUI`: main window with window-selection panel (8 slots), settings toggles, real-time stats (games played, hits, bait remaining), status log, debug window launchers, accent color picker with RGB wave animation
- `FishSelectionWindow`: modal dialog listing all fish/item assets with Keep/Open/Drop radio buttons per item
- DPI-aware: queries `shcore.SetProcessDpiAwareness(1)` and scales layout accordingly

### `window_manager.py` — WindowManager + GameRegion
- `get_all_windows()` — enumerates visible top-level windows, prioritises those with "Metin2" in title
- `activate_window()` — brings window to foreground; caches last-active handle to skip redundant calls
- `GameRegion` — dataclass: `hwnd`, `title`, `left`, `top`, `width`, `height`

### `utils.py`
- `get_resource_path(rel)` — resolves asset paths for both dev and PyInstaller bundle
- `set_window_icon(root, path)` — sets `.ico` on Tkinter window
- `play_rickroll_beep()` — PC speaker Rick Roll melody with ADSR envelope via `winsound`
- Constants: `input_lock`, `MAX_WINDOWS = 8`, `DEBUG_MODE_EN`, `DEBUG_PRINTS`

### `debug_windows.py`
Three independent debug Tkinter windows (shown/hidden from main GUI):
- `StatusLogWindow` — scrollable text log of bot events
- `IgnoredPositionsWindow` — grid overlay showing which inventory slots are being ignored
- `FishDetectorDebugWindow` — live capture with colour overlays for cyan window, blue fish dot, and classic fish template match; uses its own `mss` instance per thread

---

## Jigsaw Puzzle Solver (subdirectory)

A separate standalone tool for an in-game fishing jigsaw minigame.

- **Board:** 4×6 grid (24 cells)
- **Pieces:** 6 types — single cell, horizontal/vertical lines, L-shapes, 2×2 square, S-shape
- **Goal:** Fill the board in ≤10 rounds
- **Algorithm:** Dynamic programming optimal solver (`deterministic.py`)
- **Interface:** Tkinter GUI (`app.py`), entry via `main.py`
- **Builds:** Distributed as `Fishing Puzzle Player v1.0.x.exe` in `/dist`

---

## Build System

`build.py` wraps PyInstaller with version management and environment verification.

```bash
python build.py                      # Build current version
python build.py --clean              # Clean artifacts, then build
python build.py --version 1.0.6      # Bump version across all files, then build
python build.py --verify             # Check dependency versions only
python build.py --setup              # Install all required dependencies
```

Pinned dependency versions:
| Package | Version |
|---------|---------|
| Python | 3.14.2 |
| PyInstaller | 6.17.0 |
| opencv-python-headless | 4.12.0 |
| numpy | 2.2.6 |
| Pillow | 12.1.0 |
| pynput | 1.8.1 |
| psutil | 7.2.1 |
| pyautogui | 0.9.54 |
| mss | 10.1.0 |
| pygetwindow | 0.0.9 |

The `.spec` bundles the `assets/` folder and sets windowed mode (no console).

---

## Threading Model

```
Main thread        → Tkinter GUI event loop
Bot thread × N     → FishingBot.play_game() loops (N ≤ 8)
Debug threads      → debug_windows.py capture loops (daemon)

All mouse/keyboard via:  input_lock (threading.Lock)
Screen capture:          per-thread mss.mss() instances (not shared)
Templates:               class-level dicts (read-only after init, no lock needed)
```

---

## Known Limitations

- Windows only (uses `win32api`, `winsound`, `ctypes.windll`)
- Game window display scale should be 100%; other scales need DPI config
- Auto fish handling requires inventory to be open and mostly empty
- Armor quick skip requires coordinates calibrated per screen resolution
- Classic fishing delay is a fixed value (no dynamic adjustment)
- Requires administrator privileges for input injection

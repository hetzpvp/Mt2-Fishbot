# MT2 Fishbot — Project Specifications

## Overview

An automated fishing bot for Metin2 that uses computer vision (no memory reading) to detect fishing minigame state and perform all required actions. Supports up to 8 simultaneous game windows, two fishing modes, automatic inventory handling, multi-page inventory switching, an integrated jigsaw puzzle solver, and fully configurable timing.

**Current version:** 1.2  
**Platform:** Windows only  
**Stack:** Python 3.14.2, OpenCV, Tkinter, mss, pynput, PyAutoGUI, PyInstaller

---

## Directory Structure

```
Mt2-Fishbot/
├── src/
│   ├── fishing_bot.py       # Core bot logic (~2098 lines)
│   ├── bot_gui.py           # Tkinter GUI (~3412 lines)
│   ├── fish_detector.py     # Computer vision detection (~106 lines)
│   ├── window_manager.py    # Window discovery & focus (~169 lines)
│   ├── utils.py             # Shared constants, helpers (~202 lines)
│   ├── debug_ui.py          # Live debug visualizers (~1291 lines)
│   ├── jigsaw_bot.py        # Jigsaw puzzle automation (~1241 lines)
│   ├── jigsaw_detector.py   # Jigsaw piece recognition (~725 lines)
│   ├── qt_gui.py            # Qt-based GUI alternative (~4280 lines, unused in build)
│   └── __init__.py
├── src/jigsaw_solver/       # Fishing jigsaw puzzle solver package
│   ├── fishing_jigsaw_solver.py   # Core solver (~1099 lines)
│   ├── deterministic.py           # DP optimal solver (~630 lines)
│   ├── app.py                     # Tkinter GUI (~409 lines)
│   ├── jigsaw.py                  # Board model (~230 lines)
│   ├── main.py                    # Entry point (~94 lines)
│   └── solver.py                  # Solver interface (~25 lines)
├── assets/                  # Categorized images: fishing templates, UI icons/GIFs, jigsaw templates
├── dist/                    # PyInstaller executables (v1.0.0 → v1.2)
├── tools/build.py           # Build automation script
├── packaging/build.spec     # PyInstaller spec
├── bot_config.json          # Runtime configuration (72 keys, user-editable)
├── src/version.py           # Version constant
└── docs/gui_demo.png        # Screenshot for README
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

### Multi-Page Inventory Management
- Up to **8 inventory page tabs** configurable (`inv_page_1_pos` … `inv_page_8_pos`)
- Pages 1–4 mandatory; pages 5–8 optional
- At startup, scans all configured pages to process any existing items
- Detects empty slot count via `empty_slot.jpg` template; auto-switches page when current page is full
- `_startup_scan_and_process_all_pages()`, `_switch_inventory_page()`, `_scan_empty_slots()`

### Integrated Jigsaw Puzzle Solver
- Detects and solves the in-game fishing jigsaw minigame automatically
- Toggle via GUI; dry-run mode for simulation without clicks
- Configurable: detection threshold, action delay, crate priority (`normal_first` / `empty_first`)
- Debug: optional screenshot dumps, live `JigsawSolverDebugWindow` overlay
- Board: 4×6 grid (24 cells); 6 piece types; DP-based optimal solver in ≤10 rounds

### Quick Skip
| Mode | Mechanism |
|------|-----------|
| **Horse** | Double CTRL+G (dismount → remount) |
| **Armor** | Right-click configured armor slot (equip/unequip) |

### Bait Management
- 4 configurable hotkeys (default: 1–4) cycle through bait tiers; 200 bait per tier
- Global bait counter; auto-decrements and advances tier on depletion
- Sound alert (Rick Roll-themed PC speaker beep with ADSR envelope) on bot finish

### Configurable Timing
All click/key delays are individually tunable via **Timing Settings** modal (`TimingSettingsWindow`):

| Key | Default | Description |
|-----|---------|-------------|
| `timing_cursor_settle` | 12 ms | Mouse cursor stabilization before click |
| `timing_button_hold` | 8 ms | Mouse button hold duration |
| `timing_post_click` | 35 ms | Delay after click release |
| `timing_human_min` | 150 ms | Min random delay (human-like mode) |
| `timing_human_max` | 400 ms | Max random delay (human-like mode) |
| `timing_key_hold` | 25 ms | Keyboard key hold duration |
| `timing_key_settle` | 30 ms | Delay after key release |
| `timing_cast_interkey` | 50 ms | Delay between bait hotkey presses |
| `timing_catch_wait` | 400 ms | Wait after minigame detection |
| `timing_open_wait` | 100 ms | Wait for item open animation |
| `timing_dead_fish_check` | 100 ms | Wait before checking for dead fish |
| `timing_drop_settle` | 120 ms | Delay after drop confirmation |
| `timing_quickskip_between` | 100 ms | Delay between quick skip presses |
| `timing_quickskip_after` | 100 ms | Delay after quick skip completes |

### Configuration (bot_config.json — 72 keys)
All settings persist across sessions:
- `human_like_clicking` — randomised click timing and positional offsets
- `classic_fishing` + `classic_fishing_delay`
- `auto_fish_handling` + per-fish `fish_actions` map
- `quick_skip` + `quick_skip_mode`
- `drop_button_pos`, `confirm_button_pos`, `armor_slot_pos`
- `inv_page_1_pos` … `inv_page_8_pos` — inventory page tab coordinates
- `bait_keys`, `bait_quantity`, `bait` count
- `jigsaw_solver_enabled`, `jigsaw_dry_run`, `jigsaw_debug_screenshots`, `jigsaw_crate_priority`, `jigsaw_detection_threshold`, `jigsaw_action_delay`
- `accent_color`, `rgb_wave_active`
- `selected_windows` (8-slot array)
- All `timing_*` keys (14 parameters)

---

## Module Details

### `fishing_bot.py` — FishingBot class
One instance per game window. Main entry point is `play_game()`.

Key methods:
- `play_game()` — outer loop: bait → cast → wait for minigame → detect & click fish → handle catch → quick skip → repeat
- `atomic_capture_and_click()` — captures screen and clicks fish while holding `input_lock` (two-phase: detect then click atomically)
- `wait_for_minigame_window()` — polls until cyan window appears (timeout: 6 s)
- `wait_for_classic_fish()` — multi-scale template match loop
- `identify_item_in_inventory()` — template matching with color disambiguation
- `handle_caught_item()` — executes keep/open/drop action sequence
- `bait_and_cast()` — selects bait tier, casts, waits for rod animation
- `quickskip()` — horse or armor skip depending on config
- `_startup_scan_and_process_all_pages()` — scans all configured pages for items at startup
- `_switch_inventory_page()` — navigates to next inventory page when full
- `_scan_empty_slots()` — template-matches `empty_slot.jpg` to count free slots
- `_disambiguate_confusable_fish()` — color template matching for lookalike items

Performance details:
- All templates loaded once at class level (shared across instances)
- Templates cropped by 7 px on each edge to focus on center content
- Half-dimensions pre-computed; bitwise `>> 1` used instead of `/ 2`
- Single HSV conversion per frame covers both window and fish detection
- Local variable aliases for frequently called functions avoid attribute lookups
- Lock fairness: `_consecutive_lock_acquisitions` yields thread after 3 acquisitions

### `fish_detector.py` — FishDetector class
Single-pass HSV detection, no template matching for the minigame itself.

HSV ranges:
| Target | H | S | V |
|--------|---|---|---|
| Fish (blue dot) | 97–110 | 130–146 | 108–133 |
| Minigame window (cyan) | 98–106 | 170–255 | 189–250 |

Thresholds:
- Window pixel count ≥ 10,000 for active detection
- Fish pixel count ≥ 24 triggers connected-components analysis (connectivity=4)

Methods:
- `find_fishing_window_bounds()` — contour detection on cyan mask
- `detect_window_and_fish()` — combined detection in one HSV call; returns window rect + fish position

### `bot_gui.py` — BotGUI + FishSelectionWindow + TimingSettingsWindow
Full Tkinter UI.

- `BotGUI`: main window with window-selection panel (8 slots), settings toggles, real-time stats (games played, hits, bait remaining), status log, debug window launchers, accent color picker with RGB wave animation, jigsaw solver toggle
- `FishSelectionWindow`: modal dialog listing all fish/item assets with Keep/Open/Drop radio buttons per item (color-coded: green/red/blue/gray)
- `TimingSettingsWindow`: modal with labeled sliders for all 14 timing parameters; includes reset-to-defaults button
- DPI-aware: queries `shcore.SetProcessDpiAwareness(1)` and scales layout accordingly

### `window_manager.py` — WindowManager + GameRegion
- `get_all_windows()` — enumerates visible top-level windows, prioritises those with "Metin2" in title
- `activate_window()` — brings window to foreground with retry loop (3 attempts, 30 ms gaps); caches last-active handle to skip redundant calls
- `get_window_rect()` — returns (left, top, width, height), cached for 50 ms
- `GameRegion` — dataclass: `hwnd`, `title`, `left`, `top`, `width`, `height`

### `utils.py`
- `get_resource_path(rel)` — resolves asset paths for both dev and PyInstaller bundle
- `set_window_icon(root, path)` — sets `.ico` on Tkinter window
- `play_rickroll_beep()` — PC speaker Rick Roll melody with ADSR envelope via `winsound`
- Constants: `input_lock`, `MAX_WINDOWS = 8`, `DEBUG_MODE_EN`, `DEBUG_PRINTS`

### `debug_ui.py`
Five independent debug Tkinter windows (shown/hidden from main GUI):
- `StatusLogWindow` — scrollable text log of bot events (900×500)
- `IgnoredPositionsWindow` — grid overlay showing which inventory slots are being ignored
- `FishDetectorDebugWindow` — live capture with colour overlays for cyan window, blue fish dot, and classic fish template match; uses its own `mss` instance per thread
- `InventoryDetectionDebugWindow` — live inventory capture with detected item overlays
- `JigsawSolverDebugWindow` — jigsaw grid overlay with piece detection

### `jigsaw_bot.py` — JigsawBot class
Automates the in-game jigsaw fishing minigame end-to-end.
- Coordinates with `jigsaw_detector.py` to identify the grid and pieces
- Uses the DP solver (`deterministic.py`) for an optimal solution in ≤10 rounds
- Configurable dry-run mode (no mouse clicks), action delays, crate priority, debug screenshots

---

## Threading Model

```
Main thread        → Tkinter GUI event loop
Bot thread × N     → FishingBot.play_game() loops (N ≤ 8)
Jigsaw thread × N  → JigsawBot loops (one per selected window, when enabled)
Debug threads      → debug_ui.py capture loops (daemon)

All mouse/keyboard via:  input_lock (threading.Lock)
Screen capture:          per-thread mss.mss() instances (not shared)
Templates:               class-level dicts (read-only after init, no lock needed)
```

---

## Build System

`tools/build.py` wraps PyInstaller with version management and environment verification.

```bash
python tools/build.py                      # Build current version
python tools/build.py --clean              # Clean artifacts, then build
python tools/build.py --version 1.2        # Bump version across all files, then build
python tools/build.py --verify             # Check dependency versions only
python tools/build.py --setup              # Install all required dependencies
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

## Known Limitations

- Windows only (uses `win32api`, `winsound`, `ctypes.windll`)
- Game window display scale should be 100%; other scales need DPI config
- Auto fish handling requires inventory to be open and mostly empty
- Armor quick skip requires coordinates calibrated per screen resolution
- Classic fishing delay is a fixed value (no dynamic adjustment)
- Requires administrator privileges for input injection
- High-ping servers may need increased item-handling timing delays
- Game window must remain visible (can be in background but not minimized)

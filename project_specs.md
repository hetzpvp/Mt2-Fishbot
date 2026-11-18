# MT2 Fishbot — Project Specifications

## Overview

An automated fishing bot for Metin2 that uses computer vision (no memory reading) to detect fishing minigame state and perform all required actions. Supports up to 8 simultaneous game windows, two fishing modes, automatic inventory handling, multi-page inventory switching, an integrated jigsaw puzzle solver, and fully configurable timing.

**Current version:** 2.0.0  
**Platform:** Windows; Linux with the game under Wine (see `docs/LINUX.md`)  
**Stack:** Python 3.14.2, OpenCV, Tkinter, mss, pynput, PyAutoGUI, PyInstaller

---

## Directory Structure

```
Mt2-Fishbot/
├── src/
│   ├── fishing_bot.py       # Core bot logic (~2098 lines)
│   ├── fish_detector.py     # Computer vision detection (~106 lines)
│   ├── window_manager.py    # Window discovery & focus (~169 lines)
│   ├── utils.py             # Shared constants, helpers (~202 lines)
│   ├── debug_ui.py          # Live debug visualizers (~1291 lines)
│   ├── jigsaw_bot.py        # Jigsaw puzzle automation (~1241 lines)
│   ├── jigsaw_detector.py   # Jigsaw piece recognition (~725 lines)
│   ├── qt_gui.py            # PySide6 GUI (Fishing + Jigsaw, setup wizards)
│   ├── screen_capture.py    # WindowCapture: screen copy / per-window render policy
│   ├── input_backend.py     # Mouse/keyboard backends (Interception, PyAutoGUI, uinput, XTest)
│   ├── platforms/           # OS layer: win32.py (Windows), x11.py + uinput.py (Linux)
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
- A single global `input_lock` (FIFO-fair `PriorityInputLock`) serialises all mouse/keyboard input
- Every input sequence first brings its own window to the front and sends nothing if the OS
  did not do so (see Input Arbitration)

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
- Page 1 mandatory; pages 2–8 optional, set in order (the bot stops at the first unset page)
- At startup, scans all configured pages to process any existing items
- Detects empty slot count via `empty_slot.jpg` template; auto-switches page when current page is full
- `_startup_scan_and_process_all_pages()`, `_switch_inventory_page()`, `_scan_empty_slots()`
- The empty slots are read from the picture after every handled catch (`_page_has_space`), not
  kept by bookkeeping; a page counts as full only when two looks 0.15 s apart agree
- An item that is still in its slot after an open / drop is left alone from then on

### Integrated Jigsaw Puzzle Solver
- Detects and solves the in-game fishing jigsaw minigame automatically
- Toggle via GUI; dry-run mode for simulation without clicks
- Configurable: detection threshold, action delay, crate priority (`normal_first` / `special_first` / `special_when_better`: a special crate, which always gives the 3x2 piece, only where it saves rounds)
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
| `timing_drop_dialog_timeout` | 1.0 s | Longest wait for the drop / confirm dialog before the drop is undone |
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
- `play_game()` — outer loop: bait → cast → wait for minigame → `_play_minigame()` → handle catch → quick skip / recast wait → repeat
- `_play_minigame()` — one open minigame until its panel closes; returns (stuck, hits)
- `_handle_item()` — open / drop one inventory item under one lock hold (used at startup and after a catch)
- `_page_has_space()` / `_goto_page()` — re-read the empty slots of the visible page; click a page tab
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

### `qt_gui.py` — the GUI (PySide6)
Frameless fixed-size window (920×664 logical px; `ui_scale` / Auto scales it, down to 70 % on
screens too small for 100 %). Top to bottom: header (title, Discord, status, min/close), mode bar
(Fishing | Jigsaw Puzzle + that activity's setup wizard button), tabs (Dashboard, Positions,
Fishing/Jigsaw, Settings), a notice bar (config unreadable / invalid values, save failures,
native Wayland, first start), the tab, footer (Guide, Report a problem, donations).

- Dashboard: action strip (START/PAUSE, STOP, status line, "ready to start" chips that open the
  place that fixes them), live numbers, game window rows (always all `MAX_WINDOWS` = 8 slots; a
  window can be in one slot only).
- Positions: inventory page tabs and action points. Clicking a set pill re-captures it, its ✕
  clears it. A capture ends with a left click in the game; Esc (global), a right click, Cancel or
  60 s without a click cancel it. Fishing-only points are greyed out (not hidden) in Jigsaw mode.
- `SetupWizard`: one per activity, not modal, drives the real state (rows, capture, settings);
  the button pulses until `wizard_done_fishing` / `wizard_done_jigsaw` is set by opening it.
  Configurations from before the wizards count as done when page 1 / the grid is set.
- Config loading coerces every value to its default's type (bad values → default + notice);
  an unreadable file is moved to `.corrupt` (notice).
- Accent: `set_accent()` derives hover, border and on-accent text colors.

### `debug_host.py` — Tk debug windows next to Qt
With `DEBUG_MODE_EN` the GUI opens the `debug_ui` windows per running bot. Tk runs on its own
thread (`TkDebugHost`); the Qt side only queues requests (pumping Tk from the Qt thread crashed
Python 3.13+).

### `debug_ui.py`
Five independent debug Tkinter windows (opened per bot with `DEBUG_MODE_EN`):
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

Pinned dependency versions live in `tools/build.py` (`required_versions`) — that block is the
source of truth and is rewritten by `--update-versions`.

The `.spec` bundles the `assets/` folder and sets windowed mode (no console).

---

## Minigame Detection Profiles (cross-client support)

Detection is driven by a `DetectionProfile` (`fish_detector.py`): HSV range of the panel, HSV range
of the fish, click-circle radius and pixel thresholds. The default profile holds the official
client's values.

| Config key | Default | Meaning |
|------------|---------|---------|
| `adaptive_detection` | `true` | Self-calibrate when the active profile finds no panel |
| `detection_profile` | `null` | Profile to use instead of the stock one; a learned profile is saved here automatically. Set back to `null` to forget it |
| `diagnostics_enabled` | `false` | Diagnostic log (Settings tab): every action, capture, analysis and decision of a run, plus PNGs of the frames that came out negative, packed into `diagnostics/<YYYYMMDD_HHMMSS>.zip` next to the program (`src/diag.py`). Replaces the old `detection_debug_dump` |
| `diagnostics_verbose` | `false` | Also log every frame of per-frame loops (minigame, waits) |
| `timing_minigame_timeout` | 6 s | Base wait for a bite; grows after misses and tracks the longest bite seen |
| `timing_recast_wait` | 4 s | Wait before the next cast when quick skip is off |

Self-calibration, when the stock colors find nothing for 1.2 s after a cast:
1. `WindowLearner` diffs the window against a frame taken just before the cast and looks for a
   large, dense, rectangular change with one dominant color — that color becomes the panel range.
2. `FishLearner` follows the moving non-background blob (used as the fish position immediately),
   collects its colors into a fish range, and records static decorations (circle outline, labels)
   so they are never mistaken for the fish.
3. While it watches, the learner samples the panel into a `BackgroundModel`: per pixel, the band of
   values it normally takes (15th–85th percentile of 25 samples, so the passing fish does not count).
   A static pixel has a one-value band, a pixel of an animated background (moving water) a wider
   one. For learned profiles the fish must match the fish range *and* lie outside that band, so
   textured or animated panels, a filled disc and fish-colored icons are not mistaken for it.
   The fish itself is tracked as the solid non-background body with the most motion on it (thin
   structures such as outlines, foam lines and timer bars are ignored), and its color is the
   dominant color cluster of what was sampled.
4. `find_click_circle` locates the click circle (outline or disc) in that model; its center and
   92 % of its radius are stored in the profile (`circle_center`, `circle_radius`). Without a
   detectable circle the middle of the panel and the configured radius are used, as before.
5. The finished profile is shared with the other bots and written to `detection_profile`.
   A learned profile that produces no hit in 2 consecutive games is discarded and also removed
   from the saved configuration.

Standard panel, different fish: if the stock profile finds the panel but has not seen a single fish
2 s into a game (and never has this session), the panel colors are kept and only the fish is
learned by motion. A client on which the stock colors work never takes this path.

A "minigame" that stays open for more than 90 s is treated as a false detection: no bait is
charged, the calibration (and a learned profile) is dropped and the panel is searched again.

Other robustness rules: the panel must be seen at the same place in two consecutive frames before
calibrating; a calibrated panel that moved is re-found by a full-window scan; "panel open" is judged
relative to the calibrated pixel count; the game only counts as finished after 3 consecutive frames
without the panel and one look at the whole window that does not find it elsewhere.

After the first calibration (the panel's size is then known):

| Situation | How it is noticed | What happens |
|-----------|-------------------|--------------|
| Panel opens in another place | not in the region; or only part of it is (`FishDetector.partly_visible`: under 80 % of the calibrated pixels) | full-window scan, re-calibrate. Once this has happened (`_panel_moves`), the whole window is scanned every 0.15 s from the start of the wait instead of every 0.5 s after 2 s |
| Panel dragged during a game | `partly_visible`, or gone from the region | `_panel_elsewhere()` re-calibrates; the game goes on |
| Panel looks different this time | the region changed against the frame taken before the cast (`_region_changed`, > 50 % of its pixels, for 0.25 s) while the active profile sees no panel | other known looks (`_known_profiles`, the shared profile, the stock one) are tried, then `WindowLearner`. A candidate is only accepted at the known panel size (±15 %) and, for a known look, when its color fills at least half of what it outlines |
| Same panel, different fish | no fish for 4 s in a game although the profile has found fish before (2 s if it never has) | the fish is re-learned from its movement |
| Misses in a row | 2 casts without a minigame | calibration is dropped; the panel is searched from scratch |

A reference frame is taken before every cast (one full-window grab) so a changed look can be
learned in the cast in which it first appears. Other looks are tried at most every 1.5 s while
nothing points to one.

## Adaptive Timing

| What | How |
|------|-----|
| Bite timeout | ×1.5 per consecutive miss, capped at `timing_minigame_timeout_cap` (60 s by default) (after which the bait key is taken to be empty); remembers the longest bite seen (`_bite_timeout`). A pause during the wait ends it without counting a miss, in classic mode too |
| Catch wait | Inventory is watched for the new item instead of sleeping a fixed time |
| Click lead | `_click_point`: fish speed from the last 150 ms of positions; the click is aimed where the fish will be when the button goes down (cursor settle + 4 ms) and skipped when that point is outside the circle |
| Wait before next cast (quick skip off) | `RecastWaitTuner` (config `adaptive_recast_wait`, default on): starts at `timing_recast_wait`, shortens by 20 % after every 2 successful casts, floor 0.5 s. The first shortened wait followed by a missed bite is too short: it returns to the last working wait and stays. Two misses in a row at a proven wait give one step back. A settled wait is shared with the other windows (`_shared_recast_wait`) so each server costs one missed cast, not one per window. The adaptive wait is counted from the end of the game (`_wait_before_recast`): item handling and waiting for the mouse happen inside it. A cast that went out more than 0.5 s later than planned, or after a pause, is not fed back to the tuner. With `adaptive_recast_wait` off the configured wait is slept in full after item handling |

## Screen Capture (`screen_capture.py`)

Both bots capture through `WindowCapture`; config key `capture_backend`:

| Value | Behavior |
|-------|----------|
| `auto` (default) | Per-window render (`PrintWindow` + `PW_RENDERFULLCONTENT`) for windows that are not in front, plain screen copy for the focused one. The render is only trusted after it matched the screen copy while the window was in front (checked at start and on each cast, up to 12 times); otherwise screen copy only |
| `screen` | Always copy the screen (windows must not overlap) |
| `window` | Always per-window render, no verification |

Clicks still need the window in front; every input sequence activates it under `input_lock` first.
A minimized window cannot be captured by either method.

## Input Arbitration

`utils.input_lock` is a `PriorityInputLock`: FIFO-fair, with an urgent class used only by the
minigame fish click (`with input_lock.urgent():`), so a fish inside the circle goes ahead of queued
casts and inventory sequences from other windows.

Both bots send input only through `_focus()`: with the lock held it activates the bot's own window
and returns False when the OS did not put it in front (or the bot was stopped). The caller then
sends nothing — a key would go to whatever client is in front, a click to whatever is under the
cursor. A cast that could not be sent is retried after 0.5 s and is not counted as a missed bite;
a page tab that could not be clicked leaves the page state untouched. Nothing is sent after Stop
(or after the bot stopped itself: inventory full, bait used up) — including the quick skip and
the startup inventory scan. `WindowManager.activate_window()` returns
immediately when the window is already in front and otherwise polls for confirmation (with an
`AttachThreadInput` fallback) instead of sleeping a fixed 30 ms.

## Jigsaw Bot (`jigsaw_bot.py`)

One worker per window, started from the Dashboard when the activity switch (mode bar under the
header, `ModeBar`) is on **Jigsaw Puzzle**. Fishing and Jigsaw share the main window: the same
window rows (columns become Crates / Boards, row index = W number), inventory pages, Confirm point,
`input_lock`, capture and F5. Only the selected activity can be started; the switch is locked while
it runs. Jigsaw-only setup (grid, crate priority, solver move table) is on the JIGSAW tab
(`JigsawTab`); `JigsawRunner` owns the workers. A round:

1. Read the board (`detect_grid`), check the crate slots. Only crate kinds the priority setting
   opens are refilled from the inventory, and each kind is searched for once per run.
2. Click the crate, wait until the confirm dialog is actually on screen (`_wait_for_dialog` watches
   the confirm-button area, up to `jigsaw_dialog_wait_timeout`), click confirm, park the cursor.
3. Identify the piece on the cursor. If none shows up, confirm once more (late dialog) and look
   again; still none → discard, and after 3 such rounds pause with a reason.
4. Solver decides; place (click, then park the cursor again in the same lock hold — a piece that
   is still on the cursor must not stay over the grid, where it looks like placed cells) or discard.
5. `_confirm_placement`: the board must show the piece (`jigsaw_place_verify_timeout`). Unchanged
   board → click again → still unchanged and a piece is on the cursor → discard it. After any round
   that ended uncertain, the next one first looks for a held piece and plays it instead of opening
   a crate on top of it.

Multi-window rules: every input sequence holds `input_lock` and activates its own window; the
cursor is brought back to the window's parked point before the piece is looked at; after a real
focus switch a click waits 40 ms so the game has tracked the cursor. `get_solver()` is locked so
all workers share one copy of the tables. Throughput is bounded by the single shared cursor: rounds
of different windows interleave, so time per crate grows roughly linearly with the window count.

Also: a discarded piece that is still on the cursor after the confirm click (dialog came up late)
is confirmed again and looked for before the next crate; a crate moved from the inventory is
checked to have arrived in its slot (cursor parked off the slot first) and the slot is clicked
again if not — a crate left on the cursor would otherwise read as "none left".

`tests/test_jigsaw_sim.py` runs the bots against simulated event windows (1 and 3 windows sharing
one cursor/focus) including lost clicks, late dialogs, refill, board completion and missing pieces;
`JigsawHardTests` adds 5 windows in two rows with slow focus switches and jittered dialog / piece
delays, an OS that refuses 30 % of focus requests, lost confirm clicks, a lost crate-to-slot click,
a window away from the desktop origin, and Stop in the middle of a round.

## Cross-Platform Layer (`src/platforms/`)

Everything OS-specific sits behind one small contract (documented in
`platforms/__init__.py`); `platforms.native` is the module for the running OS.
WindowManager (activation retries, rect cache, window ordering), WindowCapture
(capture mode policy) and the bots are shared code on top of it.

| Module | OS | Provides |
|--------|----|----------|
| `win32.py` | Windows | window list (pygetwindow), focus with AttachThreadInput fallback, GetWindowRect, PrintWindow render |
| `x11.py` | Linux | window list (`_NET_CLIENT_LIST`), focus (`_NET_ACTIVE_WINDOW`), client-area rect, window image (X11 GetImage), keymap lookup. Wine draws through X11/XWayland |
| `uinput.py` | Linux | virtual absolute mouse + keyboard via /dev/uinput (raw ioctl, no compiled deps) |

Linux specifics: the rect is the client area (no title bar), so positions saved on Windows must be
recaptured; under XWayland the desktop can't be copied, so capture always reads the window itself
(`auto`/`screen` become `window`); input backend `auto` = uinput, fallback XTest (pynput).
The Qt GUI runs on X11 (`QT_QPA_PLATFORM=xcb;wayland`) so the pynput F5/click listeners see it.

`platforms.native = None` means "no desktop": unit tests set it (tests/_stubs.py), so they never
touch windows or create input devices.

## Tests

`python tools/run_tests.py` — unit tests, any OS, never touch the desktop. `tests/_stubs.py` stubs the
OS modules and provides a simulated game screen (stock and re-skinned minigame, inventory strip).

| Command | Adds |
|---------|------|
| `--os` | `tests/os_layer/`: the platform contract against the real OS (test window: list, rect, focus, minimize/restore, capture colors). Same tests on Windows and Linux |
| `--os --xvfb` | Linux: same, on a private Xvfb + openbox display |
| `--live` | real mouse/keyboard tests; on Linux also the Wine end-to-end test (`tools/wine_probe/d3dprobe.c`, a D3D9 stand-in for the client) |

Markers (`tests/conftest.py`): `windows`, `linux` (auto-skip on the other OS), `live` (opt-in).
CI (`.github/workflows/tests.yml`) runs `--os` on Windows and Linux for every push and PR.
`tests/test_window_manager.py` pins the shared window/capture policy against a fake OS layer.
`tests/test_hard_sim.py` runs whole fishing sessions in hard conditions (`docs/SIMULATOR.md`, "Hard
mode"): panel in a new place every game or dragged mid-game, a different look every game, bites
far beyond the timeout, black captures, late items, pop-up windows, an inventory filling up page
by page, and 3–5 clients on one desktop with slow or refused focus switches (no key or click may
reach another client).
`tests/test_adaptive_skins.py` covers the re-skin variants: off-center / smaller circle, filled
disc, textured panel with timer bar and fish-colored icon, standard panel with a different fish,
a panel that never closes, and a bad saved profile.

## Safety Checks and Stop Reasons

The bots never treat "could not see" as game state, and never click a saved
point without first checking that the expected UI is under it. Every stop has
its own reason (shown in the dashboard row):

| Reason | When |
|--------|------|
| `capture_unavailable` | No usable frame (black, flat, failed) for 30 s. Casting is held after 5 s; blank frames never count as a missed bite or as the end of a game, and never cost bait |
| `capture_overlap` | The window render is not trusted (screen capture) and this window overlaps another running bot's window |
| (blank frame) | Windows, screen copy: another program's window is in front and overlaps the game. The screen is not read (the frame counts as blank, so no miss and no bait); 30 s of that ends in `capture_unavailable` |
| `inventory_not_visible` | No readable page tab, empty slot or known item where the inventory is configured (checked before the first tab click, after a resume, before a page is called full) |
| `false_detection` | Two "games" in a row whose panel never closed |
| `fish_not_detected` | Three games in a row without a fish seen, with adaptive detection off |
| `no_bite` | The miss limit was reached and no minigame (classic: no bite) was seen in the session: no bait tier is written off on that |
| `window_unavailable` | Window closed, or it could not be brought to the front for 60 s |
| `game_screen_lost` | From the 2nd miss in a row on, the inventory is looked for; gone twice (disconnect, death, login screen) stops the bot without writing off bait |
| `keyboard_error` | The cast keys could not be sent 3 casts in a row (or there is no keyboard); a failed cast is never counted as a missed bite |
| `repeated_error` | 10 exceptions in the main loop within 60 s |
| `inventory_full`, `bait_depleted`, `manual`, `error` | As before |

Fishing:
- First calibration with the stock colors accepts a panel only between 120x100 px and 70 % of the window, and only if it was not there before the cast.
- Drops are closed-loop (`_drop_sequence`): picked up → drop dialog seen → confirm dialog seen → dialog gone, slot empty. A missing dialog (or Stop pressed meanwhile) puts the item back in its slot; no confirm is clicked without its dialog. "Dialog seen" (`dialog_watch`) means the button patch changed and then held still for 3 reads; after a complete drop its look is learned, and later drops need that look. The ground point is `drop_ground_pos` (window center when unset). In the jigsaw bot, too, Confirm is clicked only after its dialog was seen (with a longer second look for a late one). Only a sure match is dropped (gray ≥ 0.90, or a color match ≥ 0.85). The foreground window is re-checked before every click.
- The inventory strip ends with the item grid (`inventory_rows`, default 9 rows of 32 px). Slots marked occupied are released when they read empty again.
- `classic_miss_limit` (default 3) misses in a row before the classic bait tier changes.

Jigsaw:
- `detect_grid` checks the configured rectangle: the frame and the cell dividers must be where a 6x4 grid puts them (`jigsaw_grid_check`, `jigsaw_grid_min_confidence` 0.6). Not found `jigsaw_grid_misses_before_pause` (4) times in a row: pause, no click.
- Refused placements: the target cells are treated as filled from then on; `jigsaw_max_refused_placements` (2) in a row pause. `jigsaw_max_consecutive_discards` (12) discards in a row pause (endgame skips with ≤ 4 empty cells do not count). A board that reads full opens no crate.
- The result dialog is clicked only when seen: by `jigsaw_confirm.png` if shipped, otherwise by a change at the result point (`jigsaw_result_button_pos`, default half width + 15 px, 63 % height) against a baseline taken before the crate.
- Refill: the hand is checked before any refill click; a crate that does not arrive is put back into the inventory and the bot pauses; a page tab that could not be clicked does not write a crate kind off.
- At start, a Confirm coordinate (or the parked point beside it) on the grid, a crate slot or the inventory refuses to start (`bad_layout`).

Input lock: not re-entrant — a nested acquire raises instead of hanging every bot; holds over 0.5 s go to the diagnostic log.

## Known Limitations

- Linux: game must run under Wine's X11 driver (not winewayland); multi-monitor and fractional
  scaling untested; F5 works only while an X11 window (game or bot) has focus
- Game window display scale should be 100%; other scales need DPI config
- Auto fish handling requires inventory to be open and mostly empty
- Armor quick skip requires coordinates calibrated per screen resolution
- Classic fishing delay is a fixed value (no dynamic adjustment)
- Requires administrator privileges for input injection
- High-ping servers may need increased item-handling timing delays
- Game window must remain visible (can be in background but not minimized)

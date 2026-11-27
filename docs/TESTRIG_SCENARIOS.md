# Test rig: radical scenarios (resolutions, scaling, colors, rendering)

Written 2026-11-26 by the fishbot agent as a plan for the agent that implements it. It builds on
[TESTRIG_REQUESTS.md](TESTRIG_REQUESTS.md) (R1–R11) and [REAL_CLIENT_TESTS.md](REAL_CLIENT_TESTS.md),
and continues the numbering at **R12**. Nothing here has been built or run yet. Points marked
**(verify)** are guesses about tools or engine behaviour that need a quick experiment before
anyone builds on them.

Each item says who does it:
- **[rig]**: `~/Documents/Github/metin2-local-server` (`scripts/testrig.py`, client root hooks,
  client/server source, packs)
- **[harness]**: this repo, `tools/realclient/` and `tests/realclient/`
- **[bot]**: a change to the bot itself. This plan only names likely weak spots; fix them once
  a test shows the failure.

---

## 0. What the tests should prove

The game can be made to look many different ways. For each variant the bot must do one of two
things:

1. **Work**: same assertions as today (bait used = games, catches in the DB, jigsaw rewards).
2. **Refuse cleanly**: stop or pause with a clear reason within a set time (e.g. 60 s), and
   - drop, sell or destroy nothing it shouldn't,
   - never click Yes / OK on a dialog it didn't open itself,
   - claim no catches that didn't happen,
   - raise no exceptions.

The one result that fails the test is a **wrong action**: anything outside 1 and 2. Every
scenario below states which of the two is expected, or says "find out" when nobody knows yet.

For sweeps (hue 0→180°, scale 1.0→2.0, ...) the result isn't pass / fail but a
**robustness envelope**: the range where the bot still works and where it starts refusing.
Store the envelope in a JSON file and fail when it shrinks, or when anywhere in the sweep shows
a wrong action.

### Cost tiers (important)

A rig `up` takes 1–2 minutes, a texture variant needs a repack and a client restart, and the
release suite is already 12–15 minutes. So:

| Tier | What | Where it runs |
|---|---|---|
| **T1 corpus** | Record real frames from the rig at every resolution and UI state, with ground-truth labels from R2 (rects, fish x/y, inventory cells). | once per client change |
| **T2 offline sweeps** | Unit tests apply transforms (hue, gamma, scale, noise, JPEG, overlays) to the T1 corpus and run the detectors. Thousands of variants a minute, no rig. | every `run_tests.py` |
| **T3 real confirm** | A few real-client sessions per variant family, to check that the T2 transforms match what the game really draws, and to catch timing / input effects T2 can't see. | `run_tests.py --matrix` (new, nightly or by hand) |
| **T4 smoke matrix** | 2–3 variants kept in the normal real-client suite (one odd resolution, one recolor, one render variant). | release suite |

Most color and scale questions get answered in T2. T3 runs only enough to keep T2 honest.

---

## 1. Shared infrastructure (do these first)

### R12. One client folder per client (overlay) [rig]

**Why.** Each instance has a single `config/metin2.cfg` for all 4 clients, and the client
**rewrites it on exit** (`PythonSystem.cpp:509`). Different settings per client are
impossible today, and 4 clients exiting together race on the file. Texture variants need a
per-client `pack/` as well.

**Shape.** `up` builds `<client>/testrig/views/<account>/`: symlinks to the base client
(`Metin2.exe`, `pack/*`, `bgm`, `fonts`, ...; the unpacked `assets/` isn't needed) except
- `config/metin2.cfg`: a real copy, written from the scenario,
- `pack/<name>`: may point at a variant pack instead (R15),
- optional `dxvk.conf` (R14), `locale.cfg` / locale files (R20),

and starts the client with that folder as its working directory (it opens `config/...` and
`pack/...` relative to it). **(verify)** Wine / Proton follow the symlinks and the pack reader
doesn't care. Report the view path in the `up` result.

**Done when** 4 clients run with 4 different `WIDTH/HEIGHT/GAMMA` values and the base
`config/metin2.cfg` is unchanged after `down`.

### R13. Per-client resolution and a big enough screen [rig + harness]

The R10 item, made concrete:
- scenario: `window.size` per client (`"defaults"` or the client entry), any `WxH`.
- `tile: true` places windows of mixed sizes without overlap (shelf packing). `tile: false`
  plus `window.position` gives exact control (for the overlap and offscreen cases in §4).
- [harness] `HiddenDisplay` picks the Xvfb screen size from the scenario (sum of the tiles plus
  a margin) instead of a fixed size. 4 × 3840×2160 needs a 7680×4320 screen: ~130 MB at 24 bit,
  fine.
- `up` reports the **client-area** size the game really uses (`geometry` and the size the
  ready file reports from `wndMgr.GetScreenWidth/Height()`), because the client may clamp or
  refuse sizes.

### R14. Render variants without source changes [rig]

All of these are env vars or config files per client, so they need R12 and nothing else:

| Knob | How | What it tests |
|---|---|---|
| Graphics options | `metin2.cfg`: `SHADOW_LEVEL`, `FOG_LEVEL`, `VISIBILITY`, `OBJECT_CULLING`, `GAMMA`, `DECOMPRESSED_TEXTURE`, `SOFTWARE_TILING` | world look around the classic bubble; gamma changes UI colors |
| **Software cursor** | `SOFTWARE_CURSOR 1` | the game draws the cursor **into the frame**, so captures contain it (over icons, over the fish, over dialog buttons). Real users have this on. |
| 16-bit color | `BPP 16` **(verify)** whether DXVK honours it | banding / quantization of every color, the HSV ranges and template scores |
| MSAA / filtering | `dxvk.conf`: `d3d9.forceSwapchainMSAA = 4`, `d3d9.samplerAnisotropy = 16` | softer edges on the bubble and icons |
| FPS cap | `dxvk.conf`: `d3d9.maxFrameRate = 15 / 30`; `d3d9.presentInterval = 0` (no vsync, tearing) | slow or torn frames, motion between captures in the minigame |
| Renderer | WineD3D instead of DXVK (`PROTON_USE_WINED3D=1` or the matching DLL override) | a second D3D9 implementation with different gamma, filtering and blending, close to "another GPU / native Windows" |
| Software Vulkan | lavapipe (`VK_ICD_FILENAMES` → lvp ICD) | very slow frames, slightly different rasterization |
| **In-frame overlays** | `DXVK_HUD=fps,frametimes,memory` (top-left), `MANGOHUD=1` with a `MANGOHUD_CONFIG` placing the box at a chosen position | overlays drawn into the swapchain, i.e. **captured**, like Discord, Steam, RTSS or GeForce overlays on Windows. Put one over the inventory, the minigame counter or the bubble spot. |
| CPU starvation | client in a cgroup: `systemd-run --user --scope -p CPUQuota=25% ...`, or `taskset` all 4 clients on one core | a low-end PC: few FPS, late frames, lost input |

Scenario shape: `"render": {"cfg": {...}, "dxvk": {...}, "renderer": "dxvk|wined3d",
"vulkan": "gpu|lavapipe", "hud": "dxvk:fps" | "mangohud:<config>", "cpu_quota": 25}`, per client.
Report what was applied in the `up` result.

### R15. Texture and asset variants (recipes, repacked) [rig]

**Why.** Servers reskin the UI: dark inventories, blue boards, other icons, other minigame
art. The bot's templates and HSV ranges are tuned on stock art.

**Shape.** `testrig.py variant build <recipe.json>` →
`<client>/testrig/variants/<hash>/pack/<pack>`, cached by the recipe's hash. A recipe:

```jsonc
{"name": "minigame-hue+60",
 "instance": "fish",
 "ops": [
   {"files": "ETC/ymir work/ui/fishing_01*.dds", "op": "hsv", "hue": 60, "sat": 1.0, "val": 1.0},
   {"files": "icon/item/27*.tga",                "op": "gamma", "gamma": 1.3},
   {"files": "ETC/ymir work/ui/public/slot*.sub", "op": "replace", "with": "variants/dark_slot.tga"},
   {"files": "...",                               "op": "scale", "factor": 1.25}   // sprite size
 ]}
```

Ops: `hsv` (hue rotate, saturation, value), `gamma`, `brightness/contrast`, `grayscale`,
`invert`, `channel_swap` (BGR↔RGB), `noise` (seeded), `posterize`, `blur`, `scale` (resize
the sprite; the uiscript sizes may need the same factor, see below), `replace`. Keep each DDS's
original format (DXT1/3/5 or uncompressed). Re-encoding to DXT adds its own artifacts; that's
realistic, but write down which encoder was used. **(verify)** which encoder is available
(Pillow's DDS writer, `texconv` under Wine, `nvcompress`).

Scenario: `"variant": "<recipe name or path>"` per client, linked in through R12.

Recipes worth having ready-made (§3 lists the tests):
- `minigame-*`: only `fishing_01*.dds` (water, ring, fish, counter, timer).
- `inventory-*`: slot backgrounds, inventory board, page tabs.
- `icons-*`: only the item icons of fish, bait and dyes.
- `jigsaw-*`: `ui/minigame/fish_event/*` (board, grid, pieces, crates).
- `world-*`: water textures of the fishing spot (affects the classic bubble background).
- `ui-all-*`: everything under `ETC/ymir work/ui`.

Moving or resizing windows (a bigger minigame panel, a 4-page inventory, a 5-wide or 9-row
inventory as some servers have) is a **uiscript** change in the `uiscript` / `root` packs, not
a texture op. Add `op: "uiscript"` with a small patch file per case.

### R16. Live tint from the client hook (Python only, no C++) [rig]

**Why.** Dynamic colors (a new color every game, a pulsing color, a slow drift) without a
client restart. The fish client's minigame and jigsaw windows are Python (`uifishinggame.py`,
`uiminigamefishevent.py`), and `ui.ImageBox.SetDiffuseColor(r, g, b, a)` exists in
`root/ui.py`, so the root hook can tint any image box at runtime.

**Shape.** Env `M2_TEST_TINT` at start, plus a `tint` command through the `.cmd` channel (R5):

```jsonc
{"cmd": "tint",
 "target": "minigame" | "minigame.fish" | "minigame.water" | "minigame.ring" | "jigsaw" | "inventory" | "screen",
 "mode": "fixed" | "per_game" | "pulse" | "drift" | "off",
 "rgba": [1.0, 0.6, 0.6, 1.0],          // fixed
 "seed": 7, "range": {"hue": 180, "sat": [0.5, 1.2], "val": [0.7, 1.1]},   // per_game
 "period_s": 2.0, "amplitude": 0.4,     // pulse
 "deg_per_s": 15}                        // drift
```

- `per_game`: a new random tint each time the minigame window opens (seeded). This is the
  "colors change between games" case: the bot's learned fish profile from the last game is
  wrong for the next one.
- `screen`: a full-screen `ui.Bar` on the top layer with a translucent color (alpha 0.05–0.4).
  It tints everything, the world included. With `pulse` at 1–10 Hz it also covers flicker.
- Inventory slots are drawn by the C++ grid slot window. **(verify)** whether anything in
  `wndMgr` tints them; if not, `inventory` falls back to a translucent Bar over the inventory
  rect.
- **Ground truth:** every applied tint (target, rgba, the game it applied to, `time.time()`)
  goes into the state file (R2), so a test knows which color each game had.

### R17. Post-process and render scale in the client (C++, optional) [rig]

A diffuse tint can't rotate hue, swap channels or show a stretched window. Two options:

**a) Client patch** (the user allows source changes, done in the rig repo): before `Present`,
draw the backbuffer through one full-screen pass with a 4×4 color matrix + gamma + optional 3D
LUT + noise + ordered dither, with the parameters read from a small file or set through the
hook (`app.SetPostFX(...)`, a new Python binding). Also a **render scale**: create the
backbuffer at `W/s × H/s` and present it stretched into the `W×H` client area (in windowed D3D9
`Present` with a smaller backbuffer stretches; **(verify)** DXVK does the same, bilinear).
That is exactly what Windows **DPI virtualization** does to a non-DPI-aware game at 125 / 150 /
175 / 200 %: every pixel of UI and world gets resampled. Optionally a **UI-only scale** (the UI
layer drawn with a scale transform, world untouched), like the "UI size" option some private
servers have.

**b) No source change** (try first):
- **vkBasalt** (Vulkan layer, works on DXVK; `ENABLE_VKBASALT=1`,
  `VKBASALT_CONFIG_FILE` per client): LUTs (hue, saturation, color-blind simulations, washed-out
  HDR look), CAS sharpening, FXAA / SMAA, ReShade FX shaders (a time uniform makes dynamic
  colors possible). **Not installed** here (`rpm -q vkBasalt` says no); **(verify)** the Fedora
  package.
- **gamescope** (installed, 3.16.29) can upscale (`-w 800 -h 600 -W 1200 -H 900`, `-F linear`
  / `nearest` / `fsr`). It nests the game inside its own window, so the bot would capture and
  click gamescope's window. **(verify)** that it runs on Xvfb (SDL backend, Vulkan through the
  GPU) and passes XTest input on. If it does, it is the cheapest render-scale emulation.

### R18. Window geometry the rig can't do yet [harness, miniwm]

**Status 2026-11-26:** frames (`--frame`), Composite redirection (on by default) and the
screen size are done on the fishbot side (see REAL_CLIENT_TESTS.md, "Found 2026-11-26"). Still
to do: offscreen / overlap / foreign-window cases as tests, always-on-top popup, resize
mid-run, Xinerama. Note for the offscreen and overlap cases: with Composite on (default) the
bot sees the whole window like on Xwayland; run them with `FISHBOT_REALCLIENT_COMPOSITE=off` as
well, which is what X11 without a compositor (and Windows BitBlt of a covered window) does.

`miniwm` has no decorations, so window rect = client rect. Real WMs (KWin, Windows) add a
title bar and borders, and the bot works in client coordinates. Add to miniwm:
- **fake decorations**: reparent into a frame with an N-px title bar and M-px borders
  (N = 0 / 23 / 31, M = 0 / 1 / 8, like Windows 10/11 and KWin Breeze). Tests check that the
  bot clicks the right spot in each case.
- **partly offscreen**: window moved so 100 px hang off the right or bottom edge. XGetImage on
  an offscreen part is undefined or fails; the bot must not crash, and must refuse or warn when
  its regions are off screen.
- **overlap**: two game windows overlapping 30 / 50 / 90 %, and a **foreign window** on top
  (an `xmessage` / tiny Tk window) over the inventory or the minigame. Without a compositor,
  XGetImage returns the **top** window's pixels for the covered part, as Windows BitBlt does
  for a covered window. Find out what the bot does (§4).
- **always-on-top popup** appearing mid-run (notification-like, 3 s).
- **resize mid-run**: the WM changes the window size while the client keeps its backbuffer.
  **(verify)** with DXVK that the image gets stretched; then this also emulates DPI scaling.
- **multi-monitor**: Xvfb with `+xinerama` and several `-screen`s of different sizes;
  game windows spread over both, and one window straddling the border.

### R19. World and social events through GM commands [rig, uses R5 `chat`]

The server has the commands; they only need to be sent from the right character at the right
time (`chat` command of R5, from that character or from a 5th "GM" account that isn't botted):

| Command | Effect | Why |
|---|---|---|
| `/eclipse 1` / `0` | night sky | world colors behind the classic bubble |
| `/xmas_snow 1` | snow | moving white particles behind the bubble and over the window |
| `/effect ...`, `/xmas_boom` | particle effects | moving bright pixels next to the character |
| `/mob <vnum>` near the character, `/mob_aggresive` | mobs walk in and attack | fishing gets interrupted, HP bar, death |
| `/big_notice <text>` / `/notice` | big text over the screen | text over the minigame / bubble |
| whisper from another account | a whisper window pops up | may cover the inventory |
| `/party_request`, trade request, `/duel` | **Yes/No dialogs from another player** | the bot's dialog finder must never answer them |
| `/horse_ride`, `/horse_summon` | mounted, horse next to the character | quick skip's Ctrl+G toggle |
| `/goto` / `/warp` | loading screen mid-run | the bot must notice it lost the game screen |
| `/dc` | disconnect | R6 |
| `/flush` | save the character | R4 (`inspect` exact while the cores run) |

### R20. Locale and font variants [rig]

The client ships 18 locales (`locale/locale/{ae,br,cz,de,en,es,fr,gr,hu,it,nl,pl,pt,ro,ru,tr,
common,dk}`). Per client through R12: `locale: "de"` etc.
- Different button labels and dialog text lengths change where Yes / OK sit.
- `ae` (Arabic) is right-to-left: dialog layout may be mirrored.
- Font swap / size (`fonts/`, Tahoma replaced by another font or by nothing: the commit
  history shows that a missing Tahoma draws **bars**).

---

## 2. Resolution and scaling scenarios

**What actually changes.** As far as the uiscripts show, the Metin2 UI is **pixel-fixed**:
windows keep their pixel size and are anchored to screen edges (the inventory at
`screen width - n`, the taskbar at the bottom, the minigame centred). A bigger resolution
moves the UI and shows more world; it doesn't scale anything. Pixel **scale** only comes from
DPI virtualization, stretched presentation, a UI-scale option on some servers, or the user
resizing the window. Both kinds need tests.

### 2a. Resolutions (R12 + R13)

| Size | Why |
|---|---|
| 800×600 | today's baseline |
| 640×480 **(verify the client allows it)** | UI windows overlap each other (inventory over the minigame or the puzzle) |
| 1024×768, 1280×1024 | 4:3 and 5:4, the classic private-server sizes |
| 1280×720, 1366×768, 1600×900, 1920×1080 | 16:9, the common laptop / desktop sizes |
| 1440×900, 1680×1050, 1920×1200 | 16:10 |
| 2560×1440, 3440×1440 | 1440p, 21:9 ultrawide (the classic bubble far from the window centre) |
| 3840×2160 | 4K: 8.3 Mpx per capture. Measure capture + match time and the 8 ms poll budget with 4 clients. |
| **1001×601, 1023×767, 1279×719** | **odd widths**: row padding / stride in X11 captures, numpy reshapes, centred windows on half pixels |
| 768×1024 | portrait, in case anything assumes width > height |
| bigger than the Xvfb screen | window clipped by the screen edge |

Per resolution check:
- fishing works (both instances), the classic bubble is found (its pixel size should be
  unchanged), the minigame panel is found, the inventory finder lands on the right cells
  (compare with R2 rects).
- jigsaw: auto grid finds the puzzle window at 3 positions (top-left, centre, bottom-right
  corner of a large screen).
- the **result / quest dialogs**: `quest_dialog.py` says its geometry was measured at 800×600
  (letterbox 72 rows). Find out whether the letterbox and button positions follow the
  resolution. The harness's `CONFIRM_800x600 = (360, 326)` must come from R2 rects instead.
- timing: per-frame capture and match cost (diag log) and minigame miss rate at 1080p / 4K
  with 4 clients.

**Mixed session:** 4 clients at 800×600, 1366×768, 1920×1080 and 1001×601 in one run. Every
learned value that is shared across windows (the result-dialog OK point saved "for the other
windows", the scale the classic template last matched, a learned fish profile) is a suspect
when the windows differ. **[bot]** likely weak spot.

**Resolution change between sessions:** the same character at 800×600, then 1920×1080, with the
**same** `bot_config.json` (a throwaway copy in the run folder, never the real one). Saved click
points, grid and inventory positions must be revalidated, not trusted. Expected: works or
refuses, never clicks the old points.

### 2b. Pixel scale (R17 render scale, or R18 resize, or gamescope)

| Factor | Why |
|---|---|
| 1.25, 1.5, 1.75, 2.0 | Windows DPI scaling of a non-DPI-aware game (bilinear) |
| 1.15 | the user's own KDE setup uses fractional scaling (Qt 1.15 on 1080p; see the 2.0.4 grid bug) |
| 2.25, 3.0 | 4K laptops |
| 0.8 | downscaled (window shrunk by the user) |
| nearest vs bilinear vs FSR | gamescope filters; integer scaling (nearest at 2.0) is what some players use |

Checks:
- classic bubble: the template is matched from 0.8× up; at 1.25–3.0 it must still match, and
  below 0.8 the bot must refuse (`no_bite`), not catch blind.
- icon matching (inventory): the icon templates are exact game pixels at 1.0. Resampled icons
  lose the Lab color match margin. Danger: **Bleach vs Brown Dye are only dE 11.4 apart**, so a
  blur or gamma shift could turn one into the other, and a wrong drop is a wrong action. Expected:
  either both are recognised correctly or the item counts as "unknown" (kept).
- empty-slot template, puzzle frame template, grid structure (the open "grid one divider off"
  issue gets more likely when blurred).
- clicks: slot centres at fractional positions (1.25 × 32 px slots = 40 px, 1.75 × 32 = 56 px;
  1.15 gives non-integer slot pitch).
- **Windows code path under scaling (verify, bigger job):** run the **Windows exe** of the bot
  under Wine on the hidden display, in the **same prefix and wineserver** as the game (or the
  windows can't see each other), with `LogPixels` set to 120 / 144 / 192. This covers the
  win32 capture (BitBlt / PrintWindow), SendInput coordinates and DPI awareness calls that the
  Linux runs never touch. Find out whether this Wine version virtualizes DPI for unaware windows.

### 2c. Window state

- window resized mid-run (R18): expected refuse-or-adapt, never click stale points.
- window moved mid-run: exists (`RigSession.move`). Add moves **during** a minigame and
  **during** a jigsaw drag.
- fullscreen (`WINDOWED 0`): **(verify)** what Wine does on Xvfb (mode change via RandR).
  Expected: the bot works or refuses with a clear reason.
- decorations, partly offscreen, overlaps, multi-monitor: R18, §4.

---

## 3. Color scenarios

### 3a. Static recolors (R15 recipes, or T2 offline first)

For each **target** (minigame, inventory slots, icons, jigsaw, whole UI) run a sweep:

| Transform | Values |
|---|---|
| hue rotate | 0, ±10, ±20, 30, 60, 90, 120, 180° |
| saturation | ×0.0 (grayscale), 0.5, 0.8, 1.2, 1.5 |
| value / brightness | ×0.7, 0.85, 1.15, 1.3 |
| gamma | 0.6, 0.8, 1.2, 1.5 (also `GAMMA` in metin2.cfg, which changes everything at once) |
| contrast | ×0.6, 1.4 |
| invert | on |
| channel swap | BGR↔RGB (a classic capture bug: if T2 sees no difference, the bot isn't using color there) |
| noise | σ = 2, 5, 10 (seeded) |
| posterize / 16-bit | 5-6-5 bits per channel, with and without dither |
| JPEG | q 90, 60, 30 (stream capture / recordings) |

Known sensitive spots:
- **Minigame**: `fish_detector.py` defaults are HSV ranges for the stock panel
  (window H 98–106, fish H 97–110). Any hue shift beyond ~5° leaves the defaults; the learner
  (`FishLearner`, structure-based panel finder) must take over. Test: does the learner find
  panel and fish at each hue? How many games does it lose while learning? Does a learned
  profile get **saved** and break the next session at stock colors?
- **Low contrast**: fish tinted to the panel color (ΔE 5, 10, 20). Expected: refuses or misses
  games, never clicks wildly (each click outside the fish is a miss, not a wrong action, but
  count them).
- **Icons**: per fish / bait / dye. The keep/drop decision follows the icon, so a recolor that
  makes the bot **confuse two items** is the worst case. Expected: unknown → keep. Build a
  **confusion table** in T2: for each icon pair, the transform size at which one is taken for
  the other.
- **Empty slot / inventory board**: dark or blue reskins; the slot template is JPEG-derived (MAD
  2.7 vs real).
- **Jigsaw**: piece colors (the warm-color piece → UNKNOWN → discard suspect from
  2026-11-21 is exactly this class), crate icons, grid lines.
- **Classic bubble**: recolor and translucent bubble.

### 3b. Dynamic colors (R16, or R17 with time uniform)

| Scenario | Shape | What it breaks |
|---|---|---|
| new color every game | `per_game`, hue range 180, seeded | profile learned from game N used in game N+1 |
| color change **inside** a game | `drift` 15–60°/s, or a jump halfway through | the fish tracker mid-game |
| pulse | `pulse` 0.5–2 s, amplitude 0.2–0.5 | thresholds that hold at one phase only; the "new item" glow of newer clients |
| flicker | `screen` Bar at 5–10 Hz, alpha 0.1–0.3 | change detection (free-slot change mask, catch landing check, the late-item watch) |
| per-window colors | 4 clients, 4 tints | anything learned on one window and shared with the others |
| fade in / out | alpha 0→1 over 0.3 s when the minigame opens | the first frames of a game |
| slot highlight on new items | tint the cell that just received an item, pulsing for 3 s (like newer official clients) | catch detection, drop of the item just caught |

Ground truth: the tint log in the state file (R16), so a failure can be tied to the exact color.

### 3c. Whole-frame color transforms (R17 / vkBasalt)

Things players really have that alter the **captured** pixels (not monitor-only filters like
Night Light, which captures don't see):
- Windows **HDR on** with an SDR game: captures come out washed out (lifted blacks, low
  saturation). LUT: black level +20, saturation ×0.7, gamma 0.8.
- Nvidia / AMD color tweaks applied in the game (digital vibrance via ReShade): saturation ×1.5.
- ReShade presets (sharpening, bloom, vignette: corners darker, which matters for icons near the
  window edge).
- Color-blindness filters drawn in the game (deuteranopia / protanopia / tritanopia matrices).
- FXAA / SMAA (blurs icon edges), CAS sharpening (halos).

---

## 4. Geometry and occlusion scenarios (R18)

| Case | Expected |
|---|---|
| title bar 0 / 23 / 31 px, border 0 / 1 / 8 px | works; clicks on the right spot (check against R2 rects) |
| window 100 px off the right edge, inventory in the hidden part | refuses (`inventory_not_visible` or a new "off screen" reason), no crash on the capture |
| window off the bottom, minigame partly hidden | refuses or misses; no wild clicks |
| two game windows overlapping 50 % (each bot raises its window before reading?) | find out; the bot must not read the other window's pixels as its own |
| foreign window over the inventory | refuses; must not drop or "use" based on the foreign pixels |
| foreign window over the minigame | misses or refuses |
| always-on-top popup for 3 s mid-game | recovers |
| window straddling two Xinerama screens of different size | works or refuses |
| capture of a minimized window | exists (`test_fish_disturbed`), keep it |

---

## 5. Other radical scenarios the API makes possible

Not about looks, but cheap once R5 / R6 / R19 exist:

- **Dialog safety** (high value): trade, party, guild and duel requests from another account
  while the bot runs, plus a server `QuestionDialog` with a random text (R5 `dialog`). The bot
  must **never** click Yes on them. Repeat at several resolutions, since the dialog finder is
  geometry-based.
- **Whisper storm**: 5 whisper windows over 30 s, one over the inventory.
- **Mob attack** at the fishing spot: fishing interrupted, character hit or killed, respawn
  dialog. Expected: stops cleanly, no clicks on "Restart here" / "Restart in town".
- **Loading screen mid-run** (`/goto`): `game_screen_lost` or a pause; nothing clicked on the
  loading screen.
- **Login / character select at start**: bot started before the clients are in game. Expected:
  waits or refuses with a reason.
- **Client crash + automatic relaunch** (the rig relaunches it with a new window id): the bot
  of that window stops; the others go on.
- **Latency** (R6 netem): 150 ms ± 50, 2 % loss. Catch-timing learner, late item landing.
- **Low-end PC** (R14 CPU quota 25 %, FPS cap 15): minigame miss rate, input lost.
- **8 and 12 clients** at once (two rig instances, or more accounts): the input lock was 97 %
  busy at 8 in the sim; check it for real.
- **2-hour soak** with one client per instance: bot memory, diag log size limits, learner drift.
- **Rare items on demand** (R8 `catch_queue`): Golden Tuna dialog, dead fish, dyes, stacked
  Shrimp/Vai, an item with no template, at every resolution and under a recolor.
- **Server variants** (R9): the jigsaw bugs A–G, at 2 resolutions.

---

## 6. Suggested order

1. **R12** (per-client folder) and **R13** (per-client resolution, screen size). Everything
   else depends on them.
2. **T1 corpus**: a recorder in the harness that walks each resolution × UI state (inventory
   pages, minigame running, puzzle open, dialogs, bubble) and saves lossless frames + R2 labels
   into a corpus folder outside the repo, with a small selection copied into
   `tests/fixtures/realclient_corpus/`.
3. **T2 offline sweeps** on the corpus: hue / gamma / scale / noise / JPEG / overlays,
   robustness envelopes, icon confusion table. These will probably find most of the problems.
4. **R14** render variants (cheap: env and config files), including the software cursor and the
   HUD overlays.
5. **R16** live tint (Python only) for the dynamic color cases.
6. The **(verify)** experiments: DXVK stretch on resize, gamescope on Xvfb, vkBasalt, BPP 16,
   Windows exe under Wine with `LogPixels`. Each one is a short go / no-go.
7. **R15** texture recipes, **R18** miniwm geometry, **R19** GM events, **R20** locales.
8. **R17a** (C++ post-process / render scale) only if the no-source options of step 6 don't
   work.

For each step: run once, write down what the bot did, and only then decide what the test
asserts. A scenario where nobody knows the right behaviour yet gets "find out", not an
expectation.

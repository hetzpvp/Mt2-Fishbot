# Minigame simulator

A stand-in for the game, so the fishing bot and the jigsaw solver can be tested
without a client, on any OS, against looks no real client is at hand for.

It is in `tools/minigame_sim/` and comes in three forms:

| | What runs | Use it for |
|---|---|---|
| **Window** `python tools/minigame_sim` | A real 800x600 window titled "Metin2 Sim". The bot finds, captures, focuses and clicks it through its real OS layer | End-to-end checks: capture backend, input backend, focus, timing, the GUI |
| **Benchmark** `python tools/minigame_sim/bench.py` | The real `FishingBot` loop wired straight to the game model. No window, no real input | "Does the bot still play every look?" in a minute, safe while playing |
| **Tests** `tests/test_minigame_sim.py`, `tests/test_hard_sim.py`, `tests/test_jigsaw_sim.py` | The same, as assertions | CI |

The picture is plain shapes in configurable colors, not the game's artwork.
What the bot depends on is structure — a panel that opens after the cast, a
click circle, a moving fish, a timer — and that is what re-skinned clients keep.
The inventory and the jigsaw board are drawn from the bot's own detection
templates (`assets/`), because those are what the detectors match.

## The window

```bash
python tools/minigame_sim                    # fishing, stock colors
python tools/minigame_sim --skin emerald     # another look
python tools/minigame_sim --mode jigsaw      # the jigsaw event window
python tools/minigame_sim --windows 3        # three windows, for multi-window runs
python tools/minigame_sim --help             # bite delay, fish speed, miss penalty...
```

Then start the bot, pick "Metin2 Sim" in the window list and press Start.

Keys in the window: `1`-`4` / `F1`-`F4` bait, `space` cast, `Tab` next skin,
`F9` fishing <-> jigsaw, `F10` reset. It can be played by hand.

Each game event is a line on stdout (`cast`, `bite`, `hit 2/3`, `miss not on the
fish`, `caught`, `timeout`, `opened`, `dropped`...), and closing the window
prints the totals as JSON, so a script can drive a run and read the result.

On Linux the window is an XWayland window (the bot only sees X11 windows, as it
does with Wine). The picture is never scaled: one picture pixel is one screen
pixel.

### Fishing rules

- A bait key, then space, casts. The panel opens after a random bite delay
  (`--bite-delay`, default 1.5-4 s). A cast during the rod animation (1.5 s after
  a game) is ignored, as in the game.
- A left click **on the fish while it is inside the circle** is a hit. Any other
  click in the panel is a miss and costs time (`--miss-penalty`, default 1 s).
- After a click the panel takes no further click for half a second
  (`--click-cooldown`); clicks in that time do nothing — no hit, no miss. The
  benchmark counts them in the `early` column.
- Three hits before the timer (`--duration`, default 15 s) runs out is a catch:
  an item appears in the first free inventory slot.
- Space while the line is out reels it in: no bite from that cast
  (`casts_cancelled` in the totals). A bot that gives up on a bite too early
  pays for it with this.
- Inventory: right click opens a living fish (`--dead-fish-chance` makes some
  stay), left click picks an item up, a click in the world then asks to drop it:
  **DROP** at (350, 345), then **YES** at (360, 320) — the values for
  `drop_button_pos` / `confirm_button_pos` in an 800x600 window. Page tabs 1-4
  are at x = 641, 681, 721, 761, y = 222.

The miss penalty, the length of the click cooldown and the exact fish movement are assumptions (the game does
not publish them); both are options.

### Skins

| Skin | What it varies | Bot |
|---|---|---|
| `official` | the stock colors | plays |
| `official-real` | the stock look at the real client's size, ring drawn over the fish | plays |
| `real-mt2` | a real client's own textures and layout, pixel for pixel (see below) | plays |
| `emerald`, `crimson` | every color | plays (learns the look) |
| `charcoal` | near-gray panel: hue means nothing | plays |
| `compact` | small panel, small off-center circle, small fish | plays |
| `sand-textured` | noisy background | plays |
| `official-redfish` | stock panel, other fish, a fish-colored icon | plays |
| `violet-disc` | filled, off-center click zone | plays |
| `water` | animated water with foam lines | plays |
| `cyan-water` | the same in a cyan close to the stock panel | plays |
| `low-contrast` | mid-gray panel, as bright as what is behind it | **fails**: the panel is not found |

`low-contrast` is listed in `HARD_SKINS` (`fishing_game.py`): the bot tells the
panel from the scene by color, and here there is no difference to find. The
tests skip it; the benchmark runs it. A new look is one `Skin(...)` line in
`SKINS`.

### Real game textures (skin `real-mt2`)

The `real-mt2` skin draws the fishing window from a real Metin2 client's own
files (published with the server owner's permission): the texture atlas `fishing_01.dds`
(water, ring, fish, ripples, counter, timer, Hit/Miss) placed as the client's
uiscript `FishingGameWindow` places them, inside the real window frame. The
panel the bot finds is the uiscript's box (248x196); at 800x600 it opens
where the client opens it. How it is drawn (ring tint, fish heading, counter
dimming after a hit, where "Hit" appears and how it rises) was measured on
the recording of diagnostics 20261120_195925; `RealMt2Look` in
`fishing_game.py` lists what was measured and what is assumed. Against a
lossless shot of the real window it differs by 1.3 on average
(`tests/test_real_mt2_sim.py`). Combine it with the real-client mode:
`bench.realistic_game(skin="real-mt2")`.

The textures live in `tools/minigame_sim/assets/real_mt2` and come from
`tools/mt2_assets.py`:

```bash
python tools/mt2_assets.py packs <client folder>          # atlas + uiscript from data/pack
python tools/mt2_assets.py capture diagnostics/20261120_195925.zip   # frame, slot, counter text, test fixtures
python tools/mt2_assets.py templates <client folder>      # the bot's item templates from the game's icons
```

The packs are EterPack files: each entry is LZO-compressed and not encrypted,
but the index that names them is (with the server's key), so the tool finds
entries by their content. What is drawn on screen but not stored as one
texture (the window frame, an empty slot, the counter's "n/3" in the system
font) comes from the session's lossless shots and recording.

`templates` replaces the pictures in `assets/fishing` with the game's icons
on the real empty slot (the game draws icons exactly like that: 0.2 off on
average). The file names stay: they are the item names everywhere.

### Hard mode

Options of `FishingGame` (and flags of the benchmark) for the things that go
wrong on real clients. All are off by default.

| Option | Benchmark flag | What happens | What the bot does about it |
|---|---|---|---|
| `wander=True` | `--wander` | the panel opens somewhere else for every game | re-finds it; after the first move it scans the whole window from the start of the wait instead of after 2 s |
| `move_panel(dx, dy)` | | the player drags the open panel | notices that only part of the panel is left in the region and follows it; the game is not taken to be over |
| `skins=[...]` | `--change-look` | every game uses the next look | recognizes a look it has seen, learns a new one in the same cast (the place where the panel opens changed but the active colors do not match) |
| `bite_delay=(a, b)` | `--bite-delay A B` | bites far beyond the configured timeout | the timeout grows with every miss (up to `timing_minigame_timeout_cap`, 60 s) and then follows the longest bite seen |
| `catch_delay=(a, b)` | `--lag` | the item lands that long after the panel closed | watches the inventory instead of sleeping a fixed time |
| `blank_chance` | `--hiccups` | that share of captures comes back black | a game ends only after 3 frames without the panel and a look at the whole window |
| `popup_chance` | `--popups` | another game window (whisper, shop...) opens while the line is out | a new look is only accepted at the size of the known panel |

Several clients: `tools/minigame_sim/desktop.py` puts any number of games on
one simulated desktop with **one cursor, one keyboard and one focus**
(`FishingDesktop`). Keys go to the window in front, clicks to the window under
the cursor; input that reached another window than its bot's is counted
(`misdirected_keys`, `misdirected_clicks`). `switch_delay` makes focus switches
slow, `refuse_focus` makes the OS ignore a share of them.

### Jigsaw

The 6x4 board, the two crate slots (special above normal), the open / discard /
result dialogs, an inventory with four page tabs, and a piece that follows the
cursor after a crate is opened.

- A **normal crate** gives one of the six pieces at random. A **special crate**
  always gives the 3x2 piece (3 columns, 2 rows).
- **Crates run out.** The slots start with `--crates` normal (6) and
  `--special` special (1) crates. Spare stacks (`--inv-normal`, `--inv-special`,
  `--stack`) lie at random places on random inventory pages; a left click on a
  stack picks it up, a click on its slot puts it in. A stack does not go into
  the other kind's slot. When slots and inventory are empty, nothing more can
  be opened.
- Illegal placements and "crate opened while a piece is on the cursor" are
  counted as errors.
- Hard mode (`JigsawGame` options): `dialog_delay` / `piece_delay` as a
  (min, max) range, `lose_clicks` (placement clicks), `lose_confirms` (clicks on
  a dialog's confirm button), `lose_slot_clicks` (clicks that put a carried
  crate into its slot), and `left` / `top` to put the window anywhere.

Bot settings for this window: `confirm_button_pos` (400, 380),
`inv_page_1_pos` ... `inv_page_4_pos` (640, 180), (680, 180), (720, 180),
(760, 180).

The bot's crate choice is `jigsaw_crate_priority`:

| Value | Behaviour |
|---|---|
| `normal_first` | only normal crates |
| `special_first` | special crates while there are any, then normal; a normal crate where the 3x2 piece has no room |
| `special_when_better` | a special crate only on a board where its 3x2 piece is expected to save at least `jigsaw_special_min_gain` rounds (default 1.0) compared with normal crates, or when no normal crate is left |

### Watching the bot play

```bash
python tools/minigame_sim --demo --cycle 2       # fishing: every skin in turn, 2 games each
python tools/minigame_sim --demo --mode jigsaw   # jigsaw: special crates, restocking, until all crates are gone
```

`--demo` wires the real bot straight to the window's game (as the benchmark
does) and draws its cursor. The real mouse and keyboard are not used, so this
shows the game and the bot's decisions, not the OS layer.

### Real-client mode

`bench.realistic_game()` (benchmark flag `--realistic`) sets the game up the
way the real client behaved in a two-client session (diagnostics
20261120_195925: 330 casts, 324 games, a recording of each window), and
`bench.realistic_config(game)` sets the bot up like the player's: quick skip
on the horse, auto handling, only the tabs of pages I and II. Tests:
`tests/test_realistic_sim.py`.

| Option | Real client | Simulated |
|---|---|---|
| `bite_steps=(1, 2, 3)` | the bite comes 1, 2 or 3 s after the cast (a third each), never in between | that, plus up to 60 ms |
| `fish_motion="diagonal"` | the fish swims only at 45 degrees, bounces off the edges, ~75 px/s, stops rarely (1.7 times a game, ~0.15 s) | 45-degree lines at 30-210 px/s (mean 80), bounces, a 90-degree turn or a turn back now and then |
| `frame_rate=60` | the fish moves every 16 ms | the fish moves only on 60 fps frames |
| skin `official-real` | panel 247x195, ring r~63 with a glow drawn over the fish, small dark fish with a ripple, light caustics | the same; the bot loses the fish 60-66 px from the ring's center (real: 60-67) |
| `catch_chance=0.35`, `catch_delay=(0.1, 0.2)` | 112 items from 324 won games; the item shows 0.1-0.2 s after the panel closes | the same (`stats["empty_catches"]` counts won games without an item) |
| `living_world=True` | players and name tags move behind the windows, also inside the bot's inventory strip | walking figures with name tags |
| `prefill=17` | stacks with counts, sparkling and blinking icons; holding Ctrl draws every count bigger | the same |
| `rod_animation`, `mount_time` | after a game the rod is busy until a quick skip (Ctrl+G twice); a (dis)mount takes ~170 ms, a Ctrl+G meanwhile is lost; mounted, a cast does nothing | the same; `slow_mount_chance` makes mounts take `slow_mount_time` |
| `FishingDesktop(click_latency=(0.015, 0.035))` | a click is lost when its window loses the focus 20-30 ms after it (37 of 63), not at 30 ms or later | a click reaches the game after the latency, and only if its window still has the focus (`clicks_lost`); `click_to_switch` records each gap |

## The benchmark

```bash
python tools/minigame_sim/bench.py                    # every skin, 6 games each
python tools/minigame_sim/bench.py --skin water -v    # one skin, with the bot's log
python tools/minigame_sim/bench.py --handling open    # + auto fish handling
python tools/minigame_sim/bench.py --human            # human-like clicking
python tools/minigame_sim/bench.py --hard             # wander + change-look + lag + hiccups + popups
python tools/minigame_sim/bench.py --windows 4 --handling drop   # four clients on one desktop
python tools/minigame_sim/bench.py --windows 3 --refuse-focus 0.3
python tools/minigame_sim/bench.py --realistic --windows 2 --games 10   # the real client, two windows
```

```
skin               games caught  hits  miss early t/out s/game  notes
official               4      4    12     0    52     0    3.2
emerald                4      4    12     0    49     0    3.9  learned
...
```

The exit code is 1 when any game was not caught — with `--windows`, also when
any key or click reached a client other than the one it was meant for.

## Not simulated

Classic fishing (no minigame), bait running out, and the 3D scene behind the
panel (the backdrop is blurred noise; `living_world` only adds moving
figures). The rod animation's length without a quick skip was not measured
(the real session always skipped it): `rod_animation=2.0` is an assumption.

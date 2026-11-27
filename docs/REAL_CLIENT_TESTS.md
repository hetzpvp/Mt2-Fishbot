# Real-client tests

The bot against **real Metin2 clients on a real server**: the local test rig in
`~/Documents/Github/metin2-local-server` (two FreeBSD server VMs, Wine clients,
`scripts/testrig.py`). They are part of `tools/run_tests.py` by default on Linux and
run on **hidden displays**: no window shows up, and the user's mouse, keyboard and
focus are never touched, so they are safe while playing.

```bash
.venv/bin/python tools/run_tests.py            # unit tests, then the real-client tests
.venv/bin/python tools/run_tests.py --no-real  # unit tests only
FISHBOT_REALCLIENT=1 .venv/bin/python -m pytest -rs tests/realclient/test_classic*.py   # one instance's modules
```

Without the rig (no checkout, no VMs, not Linux, Xvfb missing) every real-client test is
**skipped**, not failed. An instance whose clients run on another display (someone playing
with `multi.sh`) is skipped too: the tests never close somebody else's clients.

## How it works

```
run_tests.py ─┬─ pytest <fish modules>    : hidden display A ─ fish VM    ─ 4 clients ─ 4 bots
               └─ pytest <classic modules> : hidden display B ─ classic VM ─ 4 clients ─ 4 bots
```

| Piece | What it does |
|---|---|
| `tools/realclient/display.py` | `HiddenDisplay`: Xvfb (it picks its own free display number, `-displayfd`) + `miniwm`. `activate()` points the process at it and removes `WAYLAND_DISPLAY` / `XAUTHORITY` and the audio server, so clients and bot cannot reach the desktop or the speakers. `assert_hidden()` refuses anything else. |
| `tools/realclient/miniwm.py` | A tiny EWMH window manager (python-xlib): `_NET_ACTIVE_WINDOW` focus switches, client list, minimize / restore, click to focus, move. What the bot uses from KWin. Also the fallback WM of `run_tests.py --os --xvfb`. |
| `tools/realclient/rig.py` | `RigSession`: drives `testrig.py` (scenario → clients up, windows, final DB state via `down --keep-vm --inspect`), plus the disturbances: `kill`, `freeze` (SIGSTOP), `minimize`, `move`, `press`, `ui` (testrig: inventory / puzzle open, closed, moved), `close_area_map`. A VM that was off is shut down again at the end. |
| `tools/realclient/botrun.py` | `BotRun`: starts `FishingBot` / `JigsawBot` on the clients the way the GUI does (`src/session_setup.py`, shared with `qt_gui.py`), records status lines, stop reasons, counters, **uncaught thread exceptions and every exception the bots log** (`diag.exc`). XTest only: uinput is blocked in the process. The diagnostic log goes into the run folder, never the repo. |
| `tools/realclient/session.py` | `run_session()` → `Outcome`: one scenario, one up, the bots, screenshots, final DB state. Shared constants (`spot()`, `worms()`, vnums, `CONFIRM_800x600`). |
| `tests/realclient/` | One module = one rig session = 4 clients = 4 cases at once (also a real 4-window run). Each test function checks one client against the game's database. |

Output of every run: `~/.local/share/fishbot-realclient/runs/<stamp>/<module>/`
(`bots.txt` status lines, `final.json` DB state, `start_*.png` / `end_*.png`, the diagnostic
zip). Failure messages print the bot's status lines and this folder.

Each hidden client takes ~0.8 CPU core and ~1 GB RAM; 8 run at once. The suite takes
about 12–15 minutes (the fish worker has 4 sessions in a row, the classic worker 2).

## What is checked

| Module | Instance | Clients (one case each) |
|---|---|---|
| `test_fish_minigame.py` | fish | baseline (games, bait used = games, bot's counter = game's) · 2 worms although told 200 → `bait_depleted`, none left · inventory closed → `inventory_not_visible` at once, nothing used · inventory full → `inventory_full` at once, nothing lost |
| `test_fish_disturbed.py` | fish | after 30 s: client killed → that bot stops (window/capture reason), others go on · client frozen 5 s → fishes on · inventory closed by the player → stops or recovers, no item lost · window minimized → never fished blind |
| `test_jigsaw.py` | fish | puzzle at 3 different places, found by the bot · normal crates only: all 40 played, special kept, reward per board · special first · 5 crates → clean "no crates left" stop · piece already held at login → played before any crate |
| `test_jigsaw_refusals.py` | fish | puzzle in the middle → `bad_layout` · intro page / closed → paused "not found" · wrong Confirm point → paused · nothing spent in any case |
| `test_classic.py` | classic | baseline (bites seen, bait used = catches) · inventory on page 2 at start · bait on F1–F4 (Paste then Minnow) · Paste on 1–4 |
| `test_classic_refusals.py` | classic | area map over the character → `no_bite` with a hint naming the map · no rod → `no_bite`, no catch claimed · inventory closed → `inventory_not_visible` · no bait → no catch claimed |

Every module also asserts **no uncaught thread exception and no logged exception** in the run.

## Found with these tests (2026-11-25)

- **Classic bites were never seen on this client** (800x600): it draws the bite as a small
  bubble that the icon template (`classic_fish.jpg`) does not match (≤ 0.56, while plain frames
  reach 0.60). Added `assets/fishing/classic_fish_bubble.png` (matched from 0.8x up) next to the
  icon; real frames in `tests/fixtures/real_classic/` (`tests/test_real_classic_bite.py`).
- **False bites from other characters**: the half-size bubble matched name tags and the bites of
  characters standing next to the player (a character without a rod "caught" 6 fish). Fixed by
  the 0.8x floor; frames kept as fixtures. The test characters stand 6 m apart (`spot()`).
- **The area map hides the classic bubble**: the stock client opens it at login over the spot
  above the character. The bot stops with `no_bite` after three casts; the hint now names the map.
- **A piece held at login** (server re-sends it after a relog): the bot clicked the crate, which
  the game ignores while a piece is held, and paused. A crate that does not open now makes the
  next round look for a held piece first (`_check_hand`).
- **Bait count drifts on missed casts**: a cast that opens no minigame (the adaptive re-cast wait
  trying a shorter wait, a key lost with 4 windows) still takes a worm on this server; the bot
  counts bait per game. The tests allow one worm per miss. Not changed: counting every miss would
  make full keys look empty.
- **An empty bait key takes ~75 s to be written off** (five misses with growing waits).
- The result dialog finder needs the world around the dialog to keep still: with four characters
  on one spot, moving next to it, it picked their name tags (and saved that point for the other
  windows). With the characters apart it finds the OK every time.

## Found 2026-11-26

- **Why whole sessions failed with "not in game after 180 s"** (also the empty run folders of
  2026-11-25): `pytest -n 2 --dist loadgroup` did not keep an instance's modules on one worker.
  pytest-xdist 3.8 writes the group into `Node._nodeid`, which pytest 9 no longer reads, so both
  workers ran classic and fish modules and closed each other's clients and VMs. `run_tests.py`
  now starts one pytest process per instance (from each module's `INSTANCE`), and the real-client
  tests skip themselves under xdist.

- **The moon was taken for a bite** (classic): a plain white disc in the night sky matched the
  bubble picture at 0.74 (acceptance 0.7). In one run W4 reeled in on it 4 times out of 8; in the
  next, all four characters had it in view. A matched bubble must now hold the icon: the middle
  third of the patch needs contrast of at least 0.25 of the disc's brightness (real bubbles
  0.36-0.91, the moon at most 0.13, under gamma 0.6-1.6, 5x5 blur, 0.8-1.5x and JPEG 30).
  Fixture `tests/fixtures/real_classic/moon_W4.png`; rejections show up in the diag as
  `first_classic_empty_bubble` and the `empty_bubbles` counter.
- **A small white glow in front of the dock was a bite too** (0.71), for the character without a
  rod: its miss count never reached three, so it never stopped with `no_bite`. The glow has
  contrast round it (the dark dock) but does not fill the bubble's white disc: a match must now
  also have at least 65 % of the picture's disc bright (real bubbles 77 %+, the glow 52 % at most).
  Fixture `glow_W2.png`.
- **The jigsaw result OK point could be saved wrong**: the click missed the OK (the finder picked
  a point below the dialog), the finder then saw the real OK, and "another button now" counted as
  "dialog gone", so the missed point was saved for every later board and run. Now saved only when
  no dialog is seen twice in a row (`tests/test_result_point_learning.py`). Open: why the finder
  picked that point (no frames in the diag; the bot paused cleanly).
- **The harness's M key did not always close the area map** (3 of 4 clients in one run):
  `RigSession.press` now types through XTest into the activated window, like the bot, instead of
  `xdotool key --window` (synthetic events), and `close_area_map` tries 6 times with 4 s to settle.
- **Plain Xvfb captures differently from the desktop**: on Xvfb without a compositor a covered
  part of a window reads black and a capture that reaches past the screen edge fails (BadMatch),
  while on Xwayland / composited KWin the window keeps all its pixels. `miniwm` now redirects
  every window through Composite by default (like Xwayland); `FISHBOT_REALCLIENT_COMPOSITE=off`
  gives the plain-X11 behaviour. The whole classic module passes with it.
- `miniwm --frame TOP,BORDER` (`FISHBOT_REALCLIENT_FRAME=31,8`) puts every window in a frame
  with a title bar and borders, like KWin or Windows; `FISHBOT_REALCLIENT_SCREEN=WxH` sets the
  hidden screen size (`display.screen_size_for()` for mixed resolutions). Not used by the suite
  yet; for the scenarios in [TESTRIG_SCENARIOS.md](TESTRIG_SCENARIOS.md).
- Offline sweeps (tier T2 of TESTRIG_SCENARIOS.md): `tests/test_robustness_envelope.py` checks
  that no item is read as another item under realistic capture changes. Open, seen only under
  synthetic changes: the classic bubble of a **neighbour** counts as a bite at a pixel scale of
  1.5x and more (the 0.8x floor is absolute, not relative to the client's scale), Large zander /
  Goldfish swap at hue +-15-20 degrees, Snake head -> Lotusfish (dead) under a 3x3 blur, and
  icons are matched at 1.0x only (rescaled inventories read as nothing or as other items).

## Writing a new case

1. Explore first: add the scenario to `CASES` in `tools/realclient/explore.py` and run
   `.venv/bin/python tools/realclient/explore.py <case> <seconds>`; it prints every bot's status
   lines and the final DB state. `tools/realclient/record_bite.py` records what a client shows
   after a cast (frames + contact sheet).
2. Then encode what is right as a module in `tests/realclient/` (`INSTANCE = ...`, one
   module-scoped `outcome` fixture calling `run_session`, one test per client).
3. Use `spot(i)` so characters do not stand on each other, and close the classic area map
   (`after_up=` with `rig.close_area_map`) unless the case is about it.

Scenario keys are in `metin2-local-server/docs/TESTRIG.md`. What the rig still cannot do is
listed in [TESTRIG_REQUESTS.md](TESTRIG_REQUESTS.md).

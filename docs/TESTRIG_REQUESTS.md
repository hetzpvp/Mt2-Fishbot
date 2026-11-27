# Test rig: requests from the fishbot side

For the agent working on `~/Documents/Github/metin2-local-server` (`scripts/testrig.py`, client
hooks `client*/assets/root/testrig.py`, server source). Written 2026-11-25 by the fishbot agent
after reading `docs/TESTRIG.md`, `scripts/testrig.py`, the client hook and the server's
`fishing.cpp`. The fishbot's test plan that uses these is `Mt2-Fishbot/docs/REAL_CLIENT_TESTS.md`.

Priorities: **P0** blocks unattended runs, **P1** gives the truth the tests check against,
**P2** lets tests change things mid-run, **P3** forces rare cases. Each item says what it's for and
when it counts as done. Things the fishbot harness does itself (kill a client by pid, SIGSTOP it,
move / resize / minimize / raise its window, record video) are not asked for here.

---

## Status 2026-11-25 (later the same day)

- **R1 (private display): done on the fishbot side, nothing needed in the rig.** `testrig.up()`
  starts the clients with the environment it inherits, so the fishbot's harness
  (`tools/realclient/display.py`) starts Xvfb + its own tiny window manager, points `DISPLAY` at
  it, removes `WAYLAND_DISPLAY` / `XAUTHORITY` and the audio server, and calls `up` from there.
  The clients render on it (DXVK picks a device that works), XTest reaches them, nothing shows on
  the desktop. Measured: ~0.8 CPU core and ~1 GB RAM per client. Learned on the way:
  `kwin_wayland --virtual --xwayland` renders fine but drops XTest input (its Xwayland sends
  emulated input through libei and the virtual backend has no pointer to deliver it to), so
  Xvfb it is.
- **R5 partly done** by the rig's new `ui` command (inventory open / closed / page, puzzle
  closed / intro / puzzle / position, quest buttons). Still wanted: the other commands of R5.
- **New: R11** below (area map), found by the first real runs.
- R2–R4 and R6–R10 still stand.

- **New 2026-11-26: R12–R20** (per-client client folders, resolutions, pixel scaling, render
  variants, texture recipes, live tint, window geometry, GM events, locales) are in
  [TESTRIG_SCENARIOS.md](TESTRIG_SCENARIOS.md).

---

## R11. Area map open / closed (scenario key and `ui` command)

**Why.** Both clients open the area map ("Yongan Area") at login, over the spot above the
character where the classic bite bubble appears: with it open the bot never sees a bite. The
harness closes it by pressing M and checking the screen (`RigSession.close_area_map`), but the
client may open it again shortly after login, and a key press is the one thing the tests would
rather not need.

**Shape.** `ui.area_map: "closed" | "open"` in the scenario (default: as the client does today) and
the same in the `ui` command, reported back in the ready / report file (`area_map_open: bool`).

---

## P1: ground truth

### R2. Live client state from the hook

**Why.** `inspect` is exact only after the cores stop, so today a test can only check the end
state. To check the bot step by step (it says it caught X into slot N: is X really there? it
thinks the inventory is on page 2: is it?), the test needs the game's own state while it runs.
The hook already runs in `GameWindow.OnUpdate`, so it can write this.

**Shape.** With `ui.state_interval_ms` set (e.g. 100), the hook appends one JSON line to
`<client>/testrig/<account>.state.jsonl` at that rate, or only when something changed (better),
always with `time.time()` and `app.GetTime()`:
- inventory: per cell `vnum, count` (both pages), equipped rod, quickslots 1–4 / F1–F4
- the item on the cursor (`mouseModule.mouseController.isAttached()` and its type / vnum / cell)
- mounted on a horse or not
- fishing: rod cast (line out), bite, minigame window open, and while it is open: **fish x/y,
  goal circle x/y/r, hits, timer left** (`uifishinggame.py` keeps these in Python, so they are
  exact labels for the bot's fish detector)
- inventory window: open, current page, **screen rect of the window, of each page tab and of the
  slot grid**
- jigsaw (fish): window state (closed / intro / puzzle), board cells, held shape,
  `use_count`, **rects of the window, the 6×4 grid and the two box slots**
- every open dialog (QuestionDialog, PopupDialog, the drop / sell popups, quest dialogs such as
  the Golden Tuna one): its text, its rect, the rect of each button
- the last chat lines received (type + text)

Rects in client-area pixels (what the bot sees), with the window size they were taken at.
The rects let the fishbot fill its click positions from the game instead of a wizard, and check
its own finders (auto grid, inventory finder) against the real ones.

**Done when** a 2-minute fishing run on one client gives a `.state.jsonl` where every catch shows
up as an inventory change within 1 frame, and the minigame's fish position is in every line
while the minigame is open.

### R3. Server event log with millisecond times

**Why.** The server decides what happened (bite time, reel time, catch result, placement
accepted), independently of what either client shows. The existing `fish_log` can't be used as is:
`LogManager::FishLog` gets `true` as the vnum argument in the success branch of `Take()`
(`fishing.cpp` ~l.730, so `dwVnum` = 1), and `NOW()` has whole seconds only.

**Shape.** `testrig.events(instance, since=<unix time>, accounts=None)` → list of
`{"t": unix_time_ms, "account", "pid", "type", ...}`, read from a server log line written only
when a test flag is on (e.g. a `TESTRIG` syslog line, read over ssh). Types:
- `cast`, `bite` (hang time), `take` (ret code, fish_id, item vnum, reel ms), `fail`
- minigame result (the score the client sent, how long the game was)
- `item_add` (vnum, count, cell, why: fish / box / quest), `item_remove` (vnum, count, cell, why:
  drop / destroy / sell / use / stack move)
- jigsaw: `box_use` (normal / special, shape), `place` (pos, shape, accepted / refused + why),
  `discard`, `complete` (reward vnum, use_count)
- `login`, `logout`, `disconnect`

CLI: `testrig.py events --instance fish --since T`.

**Done when** a run of 10 casts gives exactly 10 `cast` events, one result per cast, and every
item that lands has an `item_add`.

### R4. Exact `inspect` while the cores run

**Why.** Running several test cases in one session without restarting the cores.

**Shape.** `inspect(..., flush=True)` saves the characters first (check whether the server
has a GM `/flush <name>` / `do_flush`; otherwise a test command that calls `ch->Save()`), so the
DB is exact without `down`. R2 covers the inventory too, but the DB is the second opinion.

---

## P2: change things during a run

### R5. Command channel into the client hook

**Why.** Many cases start from "something happens while the bot runs": the inventory gets closed,
a dialog pops up, the puzzle window moves, the character mounts. The rig can't write the DB while
the cores run, and the bot's tests must not use the mouse for this.

**Shape.** `testrig.client_cmd(instance, account, cmd, **args)` writes
`<client>/testrig/<account>.cmd.jsonl`; the hook runs each command once and writes an ack (with
the result or the error) into the state file. Commands:
- `chat` (any text, so GM commands too: `/i 25106 10`, `/n notice`, ...)
- `inventory` open / close / page N / move to (x, y)
- `jigsaw` closed / intro / puzzle / move to (x, y)
- `cast` (start fishing as if space was pressed: for "bot starts while a game is already running")
- `mount` / `dismount`
- `dialog` (open a QuestionDialog or PopupDialog with a given text in the middle of the screen)
- `drop_on_cursor` / `pick_up cell` (put an item on the cursor)

### R6. Server-side faults

- `disconnect(instance, account)`: kick the character to the login screen (the bot must stop that
  window with `game_screen_lost` and keep the others running).
- `netem(instance, delay_ms, jitter_ms, loss_pct)`: lag on the game ports. FreeBSD can do it in
  the VM with `ipfw` + dummynet, so the host needs no sudo. `netem(instance, None)` clears it.
- `restart_channel(instance)`: the channel core goes down and up during a run.

### R7. Reset a character without relaunching the client

**Why.** `up` takes 1–2 minutes (VM, cores, 4 logins). With ~60 test cases that's most of the
time. Most cases only need a different inventory / board / position.

**Shape.** `testrig.reset(instance, account, client_dict)` while in game: clear the inventory, give
items, set quickslots, warp, reset the jigsaw board / held / use_count, set the UI, all through
GM / test commands. Then write a fresh ready line. Target: under 5 s per character.

---

## P3: force the rare cases

### R8. Test hooks in the server (only active with a test flag)

**Why.** The interesting cases are rare or slow on their own: a Golden Tuna, a dead fish, a dye
(drop without the Drop/Sell popup), a stack of Shrimp, an item the bot has no template for. The
classic bite takes 10–40 s, so 20 classic casts take over 10 minutes. The jigsaw piece order is
random, so a solver result can't be compared with an expected one.

**Shape** (per character, e.g. quest flags under `testrig.*`, read by `fishing.cpp` / `char.cpp`):
- `catch_queue`: the next catches give these vnums, in order (then back to random)
- `result_queue`: force success / fail for the next takes (classic misses in a row, minigame lost)
- `bite_ms`: fixed bite time (short for speed, long to test the bot's bite timeout)
- `shape_queue`: the shapes of the next jigsaw boxes, in order
- event knobs: boxes given per catch (today 5 while the event is on), special box chance

Scenario keys for them, e.g. `"server_test": {"bite_ms": 3000, "catch_queue": [27987, ...]}` per
client, applied by `apply` / `reset`.

### R9. Variants that reproduce other servers' jigsaw bugs

**Why.** The bot has defences for bugs A and B (3-wide pieces land one column right; L / mirrored L
/ Z refused next to a neighbour), but the fish client has them fixed, so they can't be tested.

**Shape.**
- `"client_variant": "stock-grid"`: a second fish client build with `__GetFishAnchor` and the
  fish branch of `GetPickedSlotPointer` turned off (bugs A, B, C), picked per scenario.
- server test flags for the Anka2 behaviour: `wrap` (D: no column check), `keep_in_hand` (E),
  `discard_client_only` (G).

### R10. Scenario keys

- per-client `window.size` and position (mixed sizes, 1024×768, 1280×720, 1920×1080; windows that
  overlap)
- `ui.inventory_pos`, `ui.jigsaw_pos`: where the inventory / puzzle window sits inside the client
  (the puzzle in the middle of the screen is a known trap)
- horse: owned, summoned, `mounted` at start (quick skip uses Ctrl+G to mount and dismount)
- `inventory_fill`: fill N cells (or all) with a junk item, to test a full inventory
- a 4-page inventory client variant (the bot's two-tab page check), if one is cheap to make
- for the minigame variant: client-side fish motion like the real servers (only 45° diagonals,
  about 75 px/s, bite after exactly 1/2/3 s), as measured from a real client recording
  (fishbot `docs/SIMULATOR.md`, "realistic" mode). Optional.

---

## Small things noticed in `testrig.py`

- The module docstring says `testrig.down("fish", keep_vm=True, inspect=True)`; the argument is
  `inspect_after`.
- `up` reads `sc.get("stagger", 4)`, which isn't in `DEFAULT_SCENARIO` or the docs.
- `window_of()` takes the first window xdotool lists for the pid. If Wine ever maps a second
  visible window for the process, that may be the wrong one. Picking the largest, or the one named
  `METIN2`, would be safer.
- `fish_log` gets `true` as the vnum (see R3).
- With the jigsaw event on, every successful catch gives 5 boxes (`fishing.cpp` ~l.679). Fine,
  just worth a line in TESTRIG.md: a fishing test on the fish instance with the event on fills the
  inventory with boxes.

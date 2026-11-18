# Code review findings

Review of the fishing bot and jigsaw player production code, 18 November 2026.

One reviewer (Grok 4.7). Claude Fable 5.2 was selected and could not run. Findings below were checked against the source. Nothing in this list has been fixed.

The highest risk is the shared mouse. Drops and crate confirms hold `input_lock` while they wait for a dialog, and Stop marks the bot idle before those threads finish. A second client can miss its fish, or the other mode can start and click while an item is still on the cursor.

## Act on

These match the code and can miss a catch, drop an item, or click the wrong window.

### 1. The input lock covers dialog waits

- **Severity:** warning
- **Where:** `src/fishing_bot.py` `_handle_item` / `_drop_sequence`; `src/jigsaw_bot.py` `_open_crate_and_confirm` / `_wait_for_dialog`

In `_handle_item`, one `with input_lock` wraps the whole drop, including `dialog_watch.wait`. In `_open_crate_and_confirm`, the same hold covers `_wait_for_dialog`, which waits the normal timeout and then up to `max(1.0, 3 * timeout)` more (about 2.4 seconds at the default). `input_lock.urgent()` only reorders the queue. Another client’s fish click waits out the whole sequence.

Poll the dialog outside the lock, and take the lock only for the focus-and-click step. Keep the steps that leave an item on the cursor atomic.

### 2. Stop unlocks the other mode while a worker can still be clicking

- **Severity:** warning
- **Where:** `src/qt_gui.py` `stop_all_bots`, `start_all_bots`; `JigsawRunner.stop` / `_release_hung` / `start`

`stop_all_bots` sets `running = False`, then immediately clears `self.bots` and frees the mode switch. It only remembers the threads in `_stopping_threads`. `JigsawRunner.start` refuses to start only when `win.bots` is non-empty. Jigsaw stop waits up to 5 seconds (`STOP_GRACE_MS`), then `_release_hung` emits `stopped` and drops the worker from `self.bots` while its thread can still be inside a click. `start_all_bots` only checks `jigsaw.is_running()` and only joins fishing’s leftover threads.

After Stop, the UI reports idle while a worker can still be holding the mouse, finishing a drop, or putting an item back. The other mode is allowed to start. Both then call `activate_window` and click.

Keep the activity running until those threads have left their input sections, and make each start join the other mode’s leftover threads.

### 3. A failed drop can leave the item on the cursor

- **Severity:** warning
- **Where:** `src/fishing_bot.py` `_handle_item` and `_drop_sequence`

The item is picked up inside the lock, then `_drop_sequence` runs. Only `FocusLost` is caught there, and that path tries to put the item back. Any other exception leaves `_drop_sequence` and is caught by `_handle_item`, which marks the slot occupied and returns.

If the failure happens after the pickup click, the item is on the cursor and the slot is recorded as occupied, so this bot will not click it again. The next click — a page tab, a cast, or another window — drops that item wherever the cursor is.

On any exception after a successful pickup, put the item back, or stop the bot with an explicit “item still on the cursor” state, before releasing the lock.

### 4. A pause restarts the classic reel timer

- **Severity:** warning
- **Where:** `src/fishing_bot.py` `wait_for_classic_fish`

During the classic reel delay, a pause sets `delay_start = perf_counter()`, which starts the whole `classic_fishing_delay` over. The minigame loop only adds paused time.

A short F5 pause restarts the wait (default 3 seconds). The reel then happens late and the classic catch is missed.

Freeze the deadline for the time spent paused, the same way the interruptible sleep does.

### 5. A quick skip that was not sent is not waited out

- **Severity:** warning
- **Where:** `src/fishing_bot.py` `quickskip` and `_armor_quickskip`

If the window cannot be focused, `_armor_quickskip` returns `True`. The docstring says that means nothing was sent and the caller should not wait. `quickskip` only waits for the recast when `_armor_quickskip` returns false. Horse mode ignores `press_ctrl_key` returning false and only sleeps `timing_quickskip_between` / `timing_quickskip_after`.

The cast animation was not skipped and was not waited out. The next cast goes out while the character is still fishing, the cast is ignored, and the miss counter moves toward emptying the bait key.

If the skip keys or the armor click were not sent, use the normal recast wait.

## Consider

Real, but they bite on a specific setup or an uncommon failure.

### 6. Mouse clicks have no `finally`

- **Severity:** warning
- **Where:** `src/humanize.py` `Humanizer.click`; same pattern in `src/input_backend.py` `InterceptionMouseBackend.click` and `src/platforms/uinput.py` `UInputMouse.click`. `src/fishing_bot.py` sets `pyautogui.FAILSAFE = False`.

Keyboard taps use `try/finally` so a key cannot stay down. Mouse clicks do `mouse_down`, sleep, `mouse_up` with no `finally`.

If `mouse_up` throws, the button stays down. The next bot’s `move_to` is then a drag. Failsafe is off, so shoving the cursor into a corner does not stop PyAutoGUI.

Release the button in a `finally`.

### 7. The learned dialog look is process-global

- **Severity:** warning
- **Where:** `src/dialog_watch.py` `_key`, `confirm`, `wait`

The learned “dialog is up” patch is a process-global dict keyed only by `(what, x, y)`. Once any window has called `confirm` for that point, `wait` rejects a change unless it resembles that patch (`SAME_DIALOG` is 10.0).

Identical clients sharing one confirm point is what this is for. A second skin, or a dialog that is not pixel-identical at that point, never counts as up, so the confirm click is skipped and the item stays on the cursor or the crate is never opened. The look also survives for the rest of the process.

Key the learned patch by window handle, and clear it when that bot stops.

### 8. A grid cell is filled if any one test passes

- **Severity:** warning
- **Where:** `src/jigsaw_detector.py` `_grid_cell_is_filled`

A cell is filled when `mean > mean_min` or `bright_ratio > bright_ratio_min` or `(sat_ratio > sat_ratio_min and std > 14)` or `edge_ratio > edge_ratio_min`, with `edge_ratio_min` defaulting to 0.07. The comment says several weak signals are there so a shiny empty border is not marked occupied.

Any one test is enough. Grid texture in the cell center can mark the cell filled. The solver then treats an empty cell as blocked.

Require more than one cue, or raise the edge test so grid texture alone cannot fill a cell.

### 9. A grid that starts off the frame is shifted, and an unreadable slot counts as empty

- **Severity:** warning
- **Where:** `src/jigsaw_detector.py` `_configured_grid_bounds` and `_slot_looks_empty`

A negative grid origin is clamped with `x = max(0, x)` and `y = max(0, y)` while width and height stay large enough to keep the original size. An empty slot crop returns `(True, 0.0)`, which means empty.

The board is read and clicked in a rectangle shifted from the real grid, so pieces go onto the wrong cells. A slot center that misses the frame is reported missing, and the bot can click that center to refill it.

Shrink width and height by the clipped left and top, and treat an unreadable slot crop as unknown.

### 10. Linux clicks ignore monitors left of the primary

- **Severity:** warning
- **Where:** `src/platforms/uinput.py` `UInputMouse.to_abs`; `src/platforms/x11.py` `desktop_size`. Contrast `src/input_backend.py` `InterceptionMouseBackend._to_abs`, which subtracts `SM_XVIRTUALSCREEN`.

Linux maps `x` with `(x + 0.5) * 65536 / desktop_width` and clamps at 0. `desktop_size` is the X screen width and height, with no origin. Windows subtracts the virtual-desktop origin before scaling.

If a monitor sits left of the primary, window coordinates are negative. Every such click is clamped to the origin, so the bot clicks the primary monitor’s left edge instead of that client.

Use the virtual desktop’s origin and span, the same way Interception does.

### 11. Linux bait keys follow the character, not the top-row key

- **Severity:** warning
- **Where:** `src/fishing_bot.py` `game_key`; `src/platforms/uinput.py` `UInputKeyboard.code_for`

On Windows a bait digit is `KeyCode.from_vk(0x30 + digit)` because a character would follow the layout. On Linux `game_key` returns the character `"1"`, and uinput resolves it with `keysym_to_keycode` or the US table.

The hotbar is the top-row scancode, not the key that produces the character “1”. On AZERTY and similar layouts the cast selects the wrong slot or none, and the miss path then treats the bait key as empty.

Send the same top-row scancodes on Linux that the Windows virtual keys correspond to.

## Noted

### 12. Parallel enqueue race in the unused solver

- **Severity:** warning
- **Where:** `src/jigsaw_solver/fishing_jigsaw_solver.py` `_process_height_numba`

`_process_height_numba` is `@njit(parallel=True)`. Inside `prange` it does a non-atomic `in_stack` check and `next_sizes[next_idx] += 1`. Two threads can write different boards into the same slot. A missing board never gets a distance, so this solver’s `solve()` can skip or place from a stale value.

The live bot does not use this file. `jigsaw_bot.py` and the GUI call `jigsaw_solver.deterministic.get_solver`. `deterministic._run_numba` does not enqueue this way. It still matters if someone runs `python -m jigsaw_solver.fishing_jigsaw_solver`.

Keep that entry point on the deterministic solver, or enqueue boards in one thread the way `deterministic._phase_enqueue` does.

### 13. A failed foreground check is treated as “screen is this client”

- **Severity:** nit
- **Where:** `src/screen_capture.py` `_screen_is_reliable`

On any exception the method returns `True`. `grab` then uses the desktop copy whenever the window render is not forced.

A failed foreground check is treated as “the screen shows this client”. A covered window can be read as this client’s board until the next successful check.

Return `False` from the exception path so the grab falls back to the window render or to no frame.

## Checked, no finding filed

The reviewer reported no findings in these, and the cited control flow matches the comments:

- `src/window_manager.py` — foreground is confirmed before a click, and a failed rect query does not invent a new origin.
- `src/utils.py` `PriorityInputLock` — urgent waiters run before normal ones, and a re-entrant acquire raises.
- `src/jigsaw_solver/jigsaw.py` and `src/jigsaw_detector.py` `detect_grid` — both use `col * 4 + row` from the high bit.
- `src/jigsaw_solver/deterministic.py` — the solver the bot calls validates the cache and does not have the parallel enqueue race.
- `src/jigsaw_solver/cache_location.py` — the cache file stays under the program or per-user cache directory.
- `src/fish_detector.py` hue wrap — a red fish is not split in half.

`src/inventory_layout.py`, `src/setup_checks.py`, `src/debug_ui.py`, `src/debug_host.py`, and `src/jigsaw_solver/solver.py`, `app.py`, `main.py` were not reviewed line by line. The live jigsaw loop does not call the standalone solver.

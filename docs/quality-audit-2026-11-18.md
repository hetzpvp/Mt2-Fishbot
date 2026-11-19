# Quality audit: Mt2 Fishbot

Audit of the working tree on 18 November 2026. Performance and UI are out of scope. No source file was changed except this report. Nothing was run on the real desktop.

The tree is dirty. Uncommitted edits (other audits) touch `src/fishing_bot.py`, `src/jigsaw_bot.py`, `src/jigsaw_detector.py`, `src/qt_gui.py`, `src/utils.py`, the README, and GUI screenshots. The defects below were checked against those files. The three high findings are in logic that those edits did not change.

## Summary

3 high, 5 medium, 1 low.

| Sev | Area | Location | What | Effort |
| --- | --- | --- | --- | --- |
| high | correctness | `src/fishing_bot.py:4399` | Classic mode counts a catch and spends bait when the reel key was not sent | S |
| high | correctness | `src/jigsaw_bot.py:1846` | A confirm click is sent when the dialog patch could not be read | S |
| high | correctness | `src/fishing_bot.py:1832` | A drop error followed by a lost window leaves the item on the cursor and the bot keeps running | M |
| medium | reliability | `src/qt_gui.py:7212` | Quit marks workers stopped and, with diagnostics off, does not wait for them | S |
| medium | release | `packaging/build_linux.spec:43` | Linux binary name is v2.0.0; README downloads still point at v2.0.1 | S |
| medium | release | `tools/release.py:321` | `publish` does not run tests and accepts a build whose smoke test was skipped or failed | S |
| medium | security | `tools/build_windows_exe.sh:33` | The Windows build downloads and runs a Python interpreter with no checksum | S |
| medium | dependencies | `requirements.txt` / `tools/build.py:144` | The Linux venv and the Windows pin list are different stacks | M |
| low | maintainability | `project_specs.md:5` | The spec still describes Tkinter and version 2.0.0 | S |

## Method and environment

- Python 3.14.10 (`.venv`), Fedora. Installed: numpy 2.5.4, opencv-python-headless 5.0.0.93, PySide6 6.12.0, pynput 1.8.2, numba 0.68.1, psutil 7.2.2, mss 10.2.0, Pillow 13.0.0, python-xlib 0.33, pytest 9.2.0, pytest-xdist 3.8.0. PyAutoGUI is not installed (Windows-only in `requirements.txt`).
- Tools that ran: `compileall` (clean), `pyflakes` (unused imports and empty f-strings only; no undefined names under `src/`), `tools/release.py` helpers imported and called in memory (`consistency`), a small C program for `sizeof(struct input_event)`.
- Not installed, so not run: vulture, bandit, ruff, mypy.
- Unit suite, once: `.venv/bin/python tools/run_tests.py -p no:randomly --tb=line -q`. Result: **1 failed, 499 passed, 19 subtests passed**, 211.27 s. pytest-xdist was active. No skipped tests in that run (PySide6 is installed, so the Qt skips did not fire). OS-layer tests (`--os`) and live input tests (`--live`) were not run: they open windows or move the pointer.
- Not checked on this machine: Windows Interception, `PrintWindow`, a real X11 focus change, the Wine build, `tools/release.py build` and `publish`.

Scratch scripts and their output are under `/tmp/mt2-quality-audit/`. They import `tests/_stubs.py` first, so they do not open `/dev/uinput`.

## Module map

Coverage is from which test files import the module, plus the os-layer tests that were not executed here.

| Module | Job | Tests |
| --- | --- | --- |
| `src/fishing_bot.py` | Per-window fishing loop | covered |
| `src/jigsaw_bot.py` | Per-window jigsaw loop | covered |
| `src/jigsaw_detector.py` | Grid, crates, cursor piece | covered |
| `src/fish_detector.py` | Minigame panel and fish | covered |
| `src/screen_capture.py` | Screen copy vs window render | covered |
| `src/window_manager.py` | Focus and rectangles | covered |
| `src/dialog_watch.py` | Dialog up / gone | covered |
| `src/inventory_layout.py` | Inventory strip and tabs | covered |
| `src/qt_gui.py` | PySide6 shell, config, start/stop | partial (smoke and config; not every modal) |
| `src/diag.py` | Session log and zip | covered |
| `src/setup_checks.py` | Setup warnings | covered |
| `src/help_visuals.py` | Guide drawings | covered |
| `src/utils.py` | Lock, paths, sounds, fish-action normalize | partial |
| `src/humanize.py` | Click and key timing | partial |
| `src/input_backend.py` | Backend selection | partial |
| `src/interception_wrapper.py` | Windows driver DLL | partial (load path only; no driver here) |
| `src/platforms/uinput.py` | Virtual mouse/keyboard | partial (`tests/test_uinput_logic.py`; no `/dev/uinput`) |
| `src/platforms/x11.py` | X11 windows and capture | partial (contract tests exist; not run) |
| `src/platforms/win32.py` | Win32 windows and capture | none on this machine |
| `src/jigsaw_solver/deterministic.py` | Solver the bot calls | partial |
| `src/jigsaw_solver/jigsaw.py` | Board model | covered |
| `src/jigsaw_solver/cache_location.py` | Cache path | partial (via `tests/test_full_audit_fixes.py`) |
| `src/debug_host.py` | Tk debug thread | partial |
| `src/debug_ui.py` | Debug overlays (`DEBUG_MODE_EN` is false) | none |
| `src/jigsaw_solver/app.py`, `main.py`, `fishing_jigsaw_solver.py`, `solver.py` | Standalone solver, not used by the bot | none |
| `src/version.py` | Version string | none directly (`tools/release.py` tests read it) |

## Findings

### 1. Classic mode records a catch when the reel key was not sent

- **Severity / area:** high, correctness
- **Location:** `src/fishing_bot.py:2678` (`press_key`), `src/fishing_bot.py:4399`
- **What happens:** After the classic fish icon is seen and the reel delay has elapsed, the loop calls `press_key` and then always increments `total_games` and decrements `bait_counter`, then runs item handling. `press_key` returns without sending the key when the window cannot be focused, when there is no keyboard, or when the key press throws. The caller does not look at that. The cast was already sent, so the line is still out. The bot then treats the round as a catch: it may click the inventory, and the next cast goes out while the character is still fishing.
- **Trigger:** Focus is lost during `classic_fishing_delay` (another client, a popup, the OS refusing the switch) and `activate_window` still fails when it is time to press space.
- **Evidence:** **verified.** `/tmp/mt2-quality-audit/repro_logic.py` section 1. `bait_and_cast` and `wait_for_classic_fish` were stubbed so the only key that could be sent was the reel. `_focus` returned false from `press_key`. Output:

  ```
  bait_counter 0
  total_games 1
  keys []
  stop_reason bait_depleted
  RESULT BUG
  ```

  Bait started at 1. No press was recorded. The loop still counted a catch and ended on "bait depleted".

```4398:4405:src/fishing_bot.py
                    # Timer already elapsed in wait_for_classic_fish - press space to reel in
                    self.press_key(self.config.get('cast_key', 'space'), "Reel in fish")
                    self._game_end_time = time.perf_counter()
                    time.sleep(0.05)

                    self.total_games += 1
                    self.bait_counter -= 1
```

```2683:2686:src/fishing_bot.py
        with input_lock:
            try:
                if not self.running or not self._focus():
                    return
```

- **Verification:** verified
- **Fix:** Make `press_key` return whether the key was sent. If the reel was not sent, do not increment `total_games`, do not decrement bait, and do not call `handle_caught_item`. Retry the reel while the bot is running, or fall through to the recast wait so the next cast is not sent on top of a line that is still out.
- **Regression test:** With `_focus` false only inside `press_key`, one classic round leaves `total_games` and `bait_counter` unchanged and never calls `handle_caught_item`.

### 2. Jigsaw confirm is clicked when the dialog patch cannot be read

- **Severity / area:** high, correctness
- **Location:** `src/jigsaw_bot.py:1846` (`_open_crate_and_confirm`), `src/jigsaw_bot.py:1470` (`_discard_piece`), `src/jigsaw_bot.py:835`, `src/dialog_watch.py:78`
- **What happens:** Both paths click Confirm unless `baseline is not None and not dialog_up`. A missing baseline skips that guard, and the confirm click is sent. The baseline is missing when `read_patch` gets nothing usable. `capture_window_region` turns a failed grab into a 1×1 black frame, and `read_patch` rejects any frame smaller than 4×4, so a failed capture becomes "no baseline" rather than "dialog not seen". The comments on the guarded branch say a confirm click with no dialog lands in the game world. The unreadable case does that click. The same shape is in the discard path.
- **Trigger:** `WindowCapture.grab` returns `None` (mss error, window render error) between the crate click and the confirm, or the confirm point is so close to the window edge that the clamped patch is under 4 px on one side.
- **Evidence:** **verified.** `/tmp/mt2-quality-audit/repro_logic.py` sections 2 and 3. Window origin `(100, 50)`, confirm point `(40, 40)`, `capture.grab` returned `None`.

  ```
  clicks [((180, 110), 'left'), ((140, 90), 'left')]
  baseline None
  RESULT BUG
  clicks [((180, 110), 'right'), ((140, 90), 'left')]
  RESULT BUG
  ```

  `(140, 90)` is the confirm point in screen coordinates. The crate click is the first click. The second click is Confirm. Discard does the same with a right-click and then Confirm.

```1846:1856:src/jigsaw_bot.py
            if baseline is not None and not dialog_up:
                # No dialog came: a confirm click would land in the game
                # world. The round finds no new piece and tries again.
                ...
                return parked
            self.human.move_to(confirm_x, confirm_y)
            ...
            self.human.click()
```

```835:836:src/jigsaw_bot.py
        if frame is None:
            return np.zeros((1, 1, 3), dtype=np.uint8)
```

```78:79:src/dialog_watch.py
    if frame is None or frame.ndim != 3 or frame.shape[0] < 4 or frame.shape[1] < 4:
        return None
```

  Fishing drops do not have this hole: `dialog_watch.wait` returns false when the baseline is `None`, and the drop then puts the item back.
- **Verification:** verified
- **Fix:** Treat a missing baseline like "dialog not seen": do not click Confirm, in both `_open_crate_and_confirm` and `_discard_piece`. Leave the round to look at the cursor before opening another crate.
- **Regression test:** `grab` returns `None`, focus succeeds. Opening a crate records one click (the crate) and no confirm click. Discard records the right-click and no confirm click.

### 3. A drop that fails, then cannot refocus, leaves the item on the cursor

- **Severity / area:** high, correctness
- **Location:** `src/fishing_bot.py:1827`
- **What happens:** If `_drop_steps` raises after the pickup, the handler tries to focus and put the item back. That path is what `tests/test_grok_review_fixes.py` covers, and those tests passed in this run. If the bot is no longer running, or `_focus` returns false, the `else` branch only logs "check the cursor", marks the slot occupied, and returns. The lock is then released. The item is still on the cursor. This bot's next click, or another client's next click, drops it wherever the cursor is. Marking the slot occupied means this bot will not click that slot again to recover it.
- **Trigger:** Any exception after the pickup click (a capture error is enough) and the window cannot be brought back to the front before the handler runs. Stop during that exception takes the same branch: the item is left for whoever clicks next.
- **Evidence:** **verified.** `/tmp/mt2-quality-audit/repro_logic.py` section 4. Official-skin simulator, every fish dead so the catch is dropped. `dialog_watch.wait` raised `RuntimeError` once `game.carrying` was set, and `_focus` then returned false. Sampled before `stop()`:

  ```
  raised True
  carrying (0, 0, 'Tenchi_living.jpg')
  running True
  stop_reason None
  tail [W1] Drop of Tenchi stopped: error: capture broke — check the cursor
  RESULT BUG
  ```

```1827:1844:src/fishing_bot.py
            try:
                if self.running and self._focus(force=True):
                    if not slot_has_item():
                        put_back(why)
                        return
                else:
                    self._log(f"Drop of {fish_name} stopped: {why} — check the cursor")
            except Exception:
                ...
                self._stop_bot(...)
                return
            self._mark_occupied(inv_x, inv_y)
```

  The inner `except` stops the bot. The `else` branch does not, then falls through to `_mark_occupied`.
- **Verification:** verified
- **Fix:** In the `else` branch, stop the bot with the same "item may still be on the cursor" reason before releasing `input_lock`. Because the cursor is shared, also refuse further clicks from every worker until that item is back in a slot or the player clears it. Stopping only this bot is not enough: the next client to acquire the lock will click.
- **Regression test:** Raise from `dialog_watch.wait` after the simulator's `carrying` is set, and make `_focus` return false. Expect `carrying` still set, `stop_reason == STOP_ERROR`, and `running` false before any other bot is allowed to click.

### 4. Quit does not wait for a worker unless a diagnostic session is open

- **Severity / area:** medium, reliability
- **Location:** `src/qt_gui.py:6933`, `src/qt_gui.py:4825`, `src/qt_gui.py:7212`, `src/qt_gui.py:7234`, `src/diag.py:1128`
- **What happens:** Fishing and jigsaw workers are daemon threads. The close dialog says the bot will be stopped. `closeEvent` sets `running = False` and calls `jigsaw.shutdown()`, then returns. The only join is inside the diagnostic zip thread, and `_end_diag_session` returns immediately when no session is open. Diagnostics default to off (`diagnostics_enabled` is false). The process then exits and the daemon workers are killed wherever they are: mid-click (button left down; mouse clicks have no `finally`, see deferred item 6) or mid-drop (item left on the cursor). When a session is open, the zip thread is non-daemon and joins workers for 5 seconds total, which only helps if that is long enough for the worker to finish the click it was in.
- **Trigger:** Close the window while a bot is inside a click or a drop, with the diagnostic log off.
- **Evidence:** **read.** `threading.Thread(..., daemon=True)` at the two start sites. `closeEvent` does not call `_join_threads`. `end_session(..., wait=False)` and `_end_diag_session` bails out when `diag.session_active()` is false. The zip worker's join budget is `deadline = time.monotonic() + 5.0`.
- **Verification:** read
- **Fix:** On quit, set `running = False`, then join the fishing and jigsaw threads on the main thread for the same grace used at Start (and longer if a drop is in progress). Do this whether or not diagnostics are on. The zip thread can still pack the log afterwards.
- **Regression test:** A worker blocked in a fake click; `closeEvent` (or a extracted shutdown helper) does not return until that worker has left the click, with diagnostics off.

### 5. Version files for 2.0.2 do not agree

- **Severity / area:** medium, release
- **Location:** `packaging/build_linux.spec:43`, `README.md:6`, `src/version.py:4`, `packaging/build.spec:78`
- **What happens:** `VERSION` is `2.0.2` and the Windows spec name is `Fishing Puzzle Player v2.0.2`. The Linux spec name is still `Fishing Puzzle Player v2.0.0 (linux)`. The README title is v2.0.2, but both download lines still point at the MediaFire v2.0.1 exe. `release.consistency("2.0.2")` reports the Linux spec and the README download links as not at this version. `tools/release.py build` exits when consistency fails, so the official build of this tree cannot start. A direct `pyinstaller packaging/build_linux.spec` would still produce a binary named v2.0.0 while the program reports 2.0.2. Anyone following the README downloads 2.0.1.
- **Evidence:** **verified.** In-process call of `tools/release.py`'s `consistency()`:

  ```
  version 2.0.2
  packaging/build.spec: None
  packaging/build_linux.spec: exe name not at this version
  README.md: download links not at this version
  src/version.py history: None
  ```

  `git show HEAD` has the same Linux spec line and the same MediaFire links, so this is not only the uncommitted README edit.
- **Verification:** verified
- **Fix:** Run the release bump for 2.0.2 again (it rewrites the spec names and the README download line), or edit those three strings so they match `src/version.py`. If the MediaFire link is meant to stay, `consistency()` and the README need to agree on that, because today the release script treats it as a mismatch.
- **Regression test:** `consistency(current_version())` has no problem strings. A one-line test can assert the Linux spec contains the version from `src/version.py`.

### 6. `publish` can ship a build that never passed tests or the smoke check

- **Severity / area:** medium, release
- **Location:** `tools/release.py:321` (`cmd_publish`), `tools/release.py:264` (`cmd_build`)
- **What happens:** `publish` checks that the three asset files exist, that HEAD's `src/version.py` matches, that the version files agree, that `src/version.py`, `README.md`, and `packaging/` are clean, and that the branch is `main`. It does not run `tools/run_tests.py`. It does not record whether `build`'s smoke test passed. `build --no-smoke` still writes `SHA256SUMS.txt` and the binaries. If a smoke test fails, `build` writes the checksums and then exits, and the binaries are left in `dist/release-X.Y.Z/` for `publish` to upload. Uncommitted changes outside the three release paths do not block the tag: they are simply not in HEAD.
- **Evidence:** **read** of `cmd_publish` and `cmd_build`. Not executed (`build` and `publish` were out of scope).
- **Verification:** read
- **Fix:** Have `build` write a marker that both smokes passed (or were not requested), and have `publish` refuse without that marker and without a recorded test run. Refuse `publish` when `git status` is dirty at all, not only on the version files.
- **Regression test:** A fake `dist/release-9.9.9/` with the three files and no smoke marker; `cmd_publish` exits before `git tag`.

### 7. The Windows build downloads Python with no integrity check

- **Severity / area:** medium, security
- **Location:** `tools/build_windows_exe.sh:33`
- **What happens:** On first use the script `curl`s `https://www.nuget.org/api/v2/package/python/$PY_VERSION` into `~/.local/share/fishbot-winbuild` and then runs that interpreter under Wine, including `pip install` of the pinned wheels. Nothing checks the nupkg hash. A substituted package becomes the compiler for the release exe. The Interception installer does the opposite: `tools/setup_interception.ps1` pins SHA-256 and refuses a mismatch. No credential was found in the repo. The public BTC address in the GUI is the same string as in the README; it is not a secret.
- **Evidence:** **read.** The curl line and the following `wine "$PY" -m pip install`. Compared with `tools/setup_interception.ps1` lines 32–37 and 81–94.
- **Verification:** read
- **Fix:** Pin the SHA-256 of that nupkg the same way the Interception zip is pinned, and refuse to unpack on mismatch. Optionally pin the pip wheels with hashes as well.
- **Regression test:** A stub `curl` that writes the wrong bytes; the script exits before `wine`.

### 8. The Linux environment and the Windows pin list are not the same stack

- **Severity / area:** medium, dependencies
- **Location:** `requirements.txt`, `tools/build.py:144`, `tools/release.py:294`
- **What happens:** `requirements.txt` is floors (`numpy>=2.4`, `opencv-python-headless>=4.13`, `PySide6>=6.11`, `numba>=0.65`, `Pillow>=12.2`). The Windows build installs the exact map in `tools/build.py` (Python 3.14.4, numpy 2.4.4, opencv-python-headless 4.13.0, PySide6 6.11.0, numba 0.65.1, Pillow 12.2.0). The Linux binary is built with `.venv/bin/pyinstaller`, which on this machine is Python 3.14.10, numpy 2.5.4, opencv-python-headless 5.0.0.93, PySide6 6.12.0, numba 0.68.1, Pillow 13.0.0. OpenCV 5 versus 4 is a major version. The unit tests pass on the Linux stack. They do not show that template scores and Qt behavior match the pinned Windows stack. Nothing in `requirements.txt` is unused by the imports under `src/`. PyAutoGUI and pygetwindow are correctly limited to Windows. No third-party import was found that `requirements.txt` omits.
- **Evidence:** **verified** for the installed versions (`importlib.metadata`) and **read** for the pin list and for `cmd_build` invoking `.venv/bin/pyinstaller`.
- **Verification:** verified (versions), read (how the two builds choose them)
- **Fix:** Pin the Linux build to the same versions as `tools/build.py`, or generate both from one lock file. Keep OpenCV on one major version for both binaries.
- **Regression test:** A check that the venv's numpy, OpenCV, PySide6, and numba match the `BEGIN_REQUIRED_VERSIONS` block before `release.py build` runs the Linux PyInstaller.

### 9. `project_specs.md` describes a different program

- **Severity / area:** low, maintainability
- **Location:** `project_specs.md:5`
- **What happens:** The spec says version 2.0.0, Tkinter, and PyAutoGUI as the stack. The program is 2.0.2, PySide6, and the Linux input path is uinput / XTest. Line counts and the "72 keys" config description are also behind the code. Anyone changing the bot from the spec will edit the wrong GUI.
- **Evidence:** **read** of the first 15 lines against `src/version.py` and `src/qt_gui.py`.
- **Verification:** read
- **Fix:** Either update the spec to the current stack or delete it and point at the README. Do not leave two version numbers in the tree.
- **Regression test:** Not worth a unit test. A release check could fail if `project_specs.md` contains a version other than `VERSION`.

## Test suite assessment

The unit run is large and most of it failed closed: 499 passed. `tests/test_grok_review_fixes.py`, `tests/test_audit_fixes.py`, `tests/test_full_audit_fixes.py`, `tests/test_second_audit_fixes.py`, `tests/test_reliability.py`, and `tests/test_review_fixes.py` are in that run. The one failure was not one of those files.

**Failure:** `tests/test_hard_sim.py::SeveralClientsTests::test_five_clients_with_slow_focus_switches_and_item_handling`, assertion `drop_cancelled == 0` ("another client's click broke a drop"). It also failed when re-run alone while another simulation was running in parallel (14.6 s). Four later runs of the same scenario, with nothing else in the suite, passed (`misdirected_clicks` 0, `drop_cancelled` 0). **inferred:** this is wall-clock scheduling, not a stable logic bug. The scenario sleeps `switch_delay=0.03` and waits on real dialog timeouts across five threads. Under load a drop's own recovery click can land while the simulator still has the dialog up, which increments `drop_cancelled` even though the click was not another client's (`misdirected_clicks` is a different counter, and the assertion order means that counter was 0 when the message fired). The test name overclaims the cause.

**Weak spots**

- `src/debug_ui.py` and the standalone solver (`app.py`, `main.py`, `fishing_jigsaw_solver.py`) have no tests. The live bot does not call them.
- `src/platforms/x11.py` and `src/platforms/win32.py` are checked by `tests/os_layer/test_contract.py`, which was not run here.
- Several GUI tests only construct widgets (`tests/test_gui_smoke.py` and similar). They would not catch findings 1–3.
- No test has `press_key` fail and then checks the classic catch counter. No test opens a crate with `grab` returning `None`. The drop tests stop the bot when put-back itself throws; they do not cover put-back being skipped because focus failed.
- Flaky pattern: real `time.sleep` in `tests/test_hard_sim.py` and in the simulator's focus delay. The rest of the suite is mostly deterministic simulations. pytest-xdist (`-n auto`) makes the timing tests share the machine with the other workers.

`pyflakes` did not find a test that cannot fail. It did not find an undefined name. The `u` marks in the progress line are pytest subtest passes (19 of them), not skips.

## Release and packaging assessment

`tools/release.py build` will not start on this tree: `consistency()` fails on the Linux spec and the README download links (finding 5). That guard works. `set_spec_name` does not let the Windows pattern rewrite the Linux name (the pattern requires the quote immediately after the version; applying it to the Linux spec raises `ValueError: found 0`). `tests/test_release.py` passed as part of the suite.

What the script still misses:

- `publish` does not know whether tests or the smoke test ran (finding 6). `--no-smoke` and a failed smoke both leave uploadable files.
- The Linux binary is whatever `.venv` has; the Windows exe is the pin list in `tools/build.py` (finding 8).
- The Windows interpreter is downloaded without a hash (finding 7).
- `packaging/build_linux.spec` does not list `debug_host` in `hiddenimports`. Windows does. `debug_host` is imported only when `DEBUG_MODE_EN` is true, which is false in `src/utils.py`, so a normal Linux build is unaffected. `diag` is a normal import and does not need a hidden import.
- CI (`.github/workflows/tests.yml`) runs `tools/run_tests.py --os` on Windows and `--os --xvfb` on Linux. It does not build either binary. A packaging mismatch is not caught there.

`sizeof(struct input_event)` on this kernel is 24, and `struct.calcsize("llHHi")` in `src/platforms/uinput.py` is 24. The uinput event layout matches this machine. **verified.**

## Rejected or deferred

Re-checked against the current files. Not filed again.

| Item | Why it is not a new finding |
| --- | --- |
| Input lock held across dialog waits (`src/fishing_bot.py` `_handle_item` `with input_lock`; `src/jigsaw_bot.py:1826` `_open_crate_and_confirm`) | Still true. Already `docs/code-review-findings.md` item 1. |
| Mouse down/up with no `finally` (`src/humanize.py:101`, `src/platforms/uinput.py:195`, `src/input_backend.py` `InterceptionMouseBackend.click`) | Still true. Already item 6. It makes finding 4 worse, but the missing `finally` itself was already reported. |
| A grid cell is filled if any one test passes (`src/jigsaw_detector.py:573`) | Still true. Already item 8. |
| Linux absolute clicks clamp at 0; `desktop_size` has no virtual-screen origin (`src/platforms/uinput.py:166`, `src/platforms/x11.py:327`) | Still true. Already item 10. Not re-run on a left-hand monitor. |
| Linux bait digits follow the character, not the top-row scancode (`src/fishing_bot.py:65` `game_key`) | Still true. Already item 11. |
| Parallel enqueue in `src/jigsaw_solver/fishing_jigsaw_solver.py` | The file is still unused by `jigsaw_bot.py` (it imports `jigsaw_solver.deterministic`). Already item 12. |
| Stop lets the other mode start while a worker can still click | Start now joins leftover fishing and jigsaw threads (`src/qt_gui.py` `_join_threads` in `start_all_bots` and `JigsawRunner.start`) and refuses with "Still stopping". The suite's tests for that passed. Not a regression. Quit is finding 4, which is a different hole. |
| Drop error puts the item back | True when focus can be regained. The suite test passed. The hole is when focus cannot be regained (finding 3). |
| Classic pause restarts the reel delay; quick skip not sent; dialog look is global; off-frame grid; failed foreground check returns true | The fixes are in the tree and their tests are in the passing set (`tests/test_grok_review_fixes.py`). Not re-failed. |
| X11 `get_window_rect` negates `translate_coords` | python-xlib translates from the root into the window, so the negation is the screen position. Not a bug. **read** of `Xlib/xobject/drawable.py` `translate_coords`. |
| Empty fish action drops the fish | Working tree: `normalize_fish_action` maps anything other than `drop` or `open` to `keep`, and `_handle_item` returns before the drop when the action is neither. **verified** in repro section 5 (`""` and `"Drop"` become `keep`). `open` stays `open`, including for item assets. The fish modal forces items to keep or drop when it is opened; a hand-edited `"open"` on an item is still opened. Not filed: the UI never writes `open` for items, and a hand-edited file is the only path. |
| Classic scale rotation (`_CLASSIC_ROTATING_SCALES = 3`) | Uncommitted. A known scale is tried every poll and three others rotate. Early exit is still the first scale at or above 0.7. No false accept was reproduced, so nothing was filed. |
| `np.load` of the solver cache | Numpy 2 defaults `allow_pickle` to false, and `_validate_cache` checks shape and dtype. A bad file is deleted and rebuilt. Not a pickle hole. **read.** |
| Config write | `_write_json_atomic` writes `path + ".tmp"` and `os.replace`s it. A crash mid-write leaves the previous file. No fsync. One GUI thread writes it. Not filed. |
| Solver cache parallel race | Deferred with the dead solver, above. |

## Open questions

1. When the classic reel key is not sent, should the bot retry space until it is sent, or treat the round as a miss and wait out the cast?
2. When a drop cannot put the item back, is stopping every running bot acceptable, or should only that window stop and the player be told to clear the cursor before anything else clicks?
3. Are the MediaFire v2.0.1 links in the README intentional? `tools/release.py` currently treats them as a failed version bump.

## Appendix

| Path | What it showed |
| --- | --- |
| `/tmp/mt2-quality-audit/repro_logic.py` | Findings 1, 2, 3, and the `normalize_fish_action` check. Output also in `/tmp/mt2-quality-audit/repro_out.txt`. |
| `/tmp/mt2-quality-audit/repro_drop_cancel.py` | Four solo passes of the five-client drop scenario (one earlier pass plus three in the loop). `fails_seen 0`. |
| `/tmp/mt2-quality-audit/evsz.c` | `input_event` is 24 bytes here; `llHHi` is 24. |

The unit-suite log is the terminal that ran `tools/run_tests.py -p no:randomly --tb=line -q` (1 failed, 499 passed, 19 subtests, 211.27 s).

## Resolution (18 November 2026)

| # | Verdict | What was done |
| --- | --- | --- |
| 1 | valid, fixed | `press_key` returns whether the key was sent. The classic reel is retried for 1.5 s (`_send_reel`). If it still was not sent, no catch is counted, no bait is spent, and no item handling runs; the recast wait lets the line run out. |
| 2 | valid, fixed | `_open_crate_and_confirm` and `_discard_piece` treat an unreadable confirm area (no baseline) like "no dialog": no Confirm click. Repeated failures end in the existing "no piece recognized" pause. |
| 3 | valid, fixed | The put-back is tried after a stop too. If the window cannot be focused, `FishingBot._cursor_hazard` is set while `input_lock` is still held. From then on `_focus` refuses input and stops every bot (`STOP_CURSOR_ITEM`), and the GUI stops the others at once. Start (`reset_session_state`) clears it. Open question 2: every bot stops. |
| 4 | valid, fixed | `closeEvent` joins the fishing and jigsaw workers for up to `QUIT_JOIN_BUDGET` (4 s) whether diagnostics are on or off. |
| 5 | partly valid | The Linux spec name was stale and is now v2.0.2. The README download links are intentional: 2.0.2 is not published yet, so they point at the last published release, and `release.py bump` rewrites them when it is published (open question 3). |
| 6 | valid, fixed | `build` records each asset that passed its smoke test, with its SHA-256, in `VERIFIED.json`. `publish` refuses unrecorded or changed assets, any change to a tracked file, and untracked files under `src/`, `packaging/` or `assets/`. It runs the unit tests on the committed tree before it tags. |
| 7 | valid, fixed | `build_windows_exe.sh` pins the SHA-256 of `python.3.14.4.nupkg` (checked against nuget.org's published SHA-512) and refuses a mismatch. Another `FISHBOT_WIN_PY` needs `FISHBOT_WIN_PY_SHA256`. |
| 8 | valid, reported only | `release.py status` lists where `.venv` differs from the Windows pins. Lining the two up (downgrade `.venv`, or a separate pinned Linux build venv) is left to the maintainer. |
| 9 | valid, fixed | `project_specs.md`: version, stack, module map, threading and lock lines corrected. |
| test | flaky, fixed | `test_five_clients_with_slow_focus_switches_and_item_handling` now allows a cancelled drop dialog only up to that window's own logged aborted drops (its put-back click). Clicks from other clients are still caught by `misdirected_clicks`. |

Tests: `tests/test_quality_audit_fixes.py` (12 tests). Full unit suite: 521 passed.

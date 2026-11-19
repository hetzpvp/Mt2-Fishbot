# Performance audit: image recognition and the jigsaw solver

18 November 2026. Recognition and solving only. This audit did not edit
`src/`. Nothing was captured from the live desktop.

The minigame fish loop is already waiting on purpose: detection on a real
panel crop is 0.48 ms inside an 8 ms budget. The recognition work that
fills a core is the classic bite wait, which runs 19 template scales on
every poll until one scale has matched. Two jigsaw waits were grabbing a
whole window to read a small rectangle, and a missing cursor position was
scoring every saturated pixel in the frame.

The working tree already contains those three cuts (uncommitted). The
numbers below are from that tree: the full scan, the full-frame copy and
the full-frame score were timed directly, and the reduced paths were
timed by calling the functions the tree now runs.

## 1. Summary

Ranked by CPU saved during play, times confidence.

| Change | Estimated saving | Confidence | Risk | Effort |
| --- | --- | --- | --- | --- |
| Classic bite wait: after one hit, each miss poll matches that scale plus 3 rotating scales, still at threshold 0.7. In the working tree (`_CLASSIC_ROTATING_SCALES`) | 30.3 ms → 5.9 ms per poll on the 250×360 crop a 1280×720 window requests. With the 20 ms sleep, that is 60% of a core down to 23%, about 3.8 s of CPU per 10 s of bite wait. On the 250×540 crop (1920×1080): 47.7 ms → 9.7 ms, the same 3.8 s per 10 s | Measured on `wait_for_classic_fish` with `time.sleep` patched out | Timing. A scale absent from this poll's batch is seen within 6 polls (about 160 ms at the 720p crop). If two scales both clear 0.7, the one in the batch wins | S. Already in the working tree |
| Jigsaw board confirm and result poll: grab the rectangle that is read, and skip the full grab when `jigsaw_confirm.png` is not shipped. In the working tree (`_read_board`, `has_confirm_template`) | The BGR copy after a grab is 5.2 ms at 1280×720 and 11.2 ms at 1920×1080; a 280×220 copy is 0.30 ms. `detect_grid` stays about 3.6 ms either way. The result poll can repeat for up to 13 s. OS transfer is extra and was not measured | Copy, `detect_grid`, and the 11 fixture boards measured. OS grab not measured | Low. On all 11 rescaled boards the cropped read matched the full frame. Skipping the grab when the template is absent leaves the dialog-patch check as it is | S. Already in the working tree |
| Cursor piece with no position: do not run the color score on the whole frame. In the working tree (`detect_cursor_piece`) | The score alone is 120 ms on a fixture resized to 1280×720 (423,247 saturated pixels) and 252 ms at 1920×1080 (952,457). The call that remains, the saturation mask, is 4.4 ms and 10.2 ms. A steady call with a cursor point is 0.29 ms | Measured. How often a running bot has no cursor position is inferred from the callers | The mask still runs, so `colored_pixels` stays positive and the bot still treats the result as an unknown piece. Zeroing that count would be a behavior change | S. Already in the working tree |

Not in that table, and not recommended: rotating scales before any scale is
known (one wait per run, and a test locks the full list), the 8 ms fish
loop, the jigsaw `solve()` re-rank (0.16 ms), vectorizing the 24 grid
cells, and another pass on `WindowLearner`. Those are in section 5.
`abs_diff_max` is already the fast channel reduction. It has not regressed.

## 2. Environment

- Machine: Intel Core i5-10400F (6 cores / 12 threads), governor
  `performance`, clocks at 4000 MHz during the runs (median of
  `/proc/cpuinfo` `cpu MHz` was 4000.0; minimum 4000.0). 32 GB RAM
  (`MemTotal` 32,757,628 kB).
- Python 3.14.10, OpenCV 5.0.0, NumPy 2.5.4, Numba 0.68.1, from `.venv`.
- `cv2.setNumThreads(1)` before the timed calls. `src/screen_capture.py`
  sets the same at import. A thread pool made the small fish images
  slower, and it did not speed the classic scan either (section 5).
- Timers: `time.perf_counter`. Each figure is a nearest-rank median and
  p95 (`index = ceil(q * n) - 1` on the sorted samples, warmup calls
  excluded). `n` is the counted calls. Samples of n under 15 are marked.
- Images: `tests/fixtures/real_client/` (windows are 480×320; the panel
  crop from `panel_0434.png` is 247×182) and
  `tests/fixtures/real_client_rescaled/` (jigsaw boards, about 300×167).
  Sizes the fixtures do not contain are synthetic, or the fixture crop
  resized to the width and height the code requests. Those are labeled
  probes. `matchTemplate` cost follows those dimensions; the scores on a
  resized bar are not detection results.
- No `mss` grab, no `XGetImage`, no `PrintWindow`, no mouse, no keys.
  Capture OS time is unmeasured. The numpy copy that follows a grab was
  timed on a buffer of the same shape.
- Solver cache on disk: `data/cache/jigsaw/solver_cache_f64.npz`
  (90,916,407 bytes). It was only read. A compress-and-write probe and an
  uncompressed probe went to `/tmp` and were deleted. The cold DP wrote
  nothing (`files_in_empty []`).
- Baseline tests, before the timings: `.venv/bin/python tools/run_tests.py`.
  **462 passed in 224.83 s.** No failures. That run includes
  `tests/test_perf_audit_fixes.py`, which locks the three cuts above.
  OS-layer tests were not run; they open windows.

`docs/performance-review.md` is still right about the shape of the fish
loop and about keeping the solver re-rank. Its channel-max change is
`abs_diff_max` (`src/fish_detector.py:118`). Remeasured in section 5:
at 1920×1080, `abs_diff_max` is 6.1 ms and `.max(axis=2)` is 61.6 ms, and
the threshold-25 masks are equal.

## 3. Baseline

### One fishing cycle

Stock `detect_window_and_fish` on the real panel crop from
`panel_0434.png` (247×182), n=200: **median 0.48 ms, p95 0.61 ms**.
The fish was found. The loop's budget is `timing_detect_interval`,
default **8 ms** (`src/fishing_bot.py:238`, paced at `4146`). Detection
is about 6% of that interval. The rest is `time.sleep`.

Stages on that crop, timed as separate calls (n=80), so the sum is not
the method's own time: HSV `cvtColor` 0.16 ms (p95 0.21), one `inRange`
0.038 ms (p95 0.10), `connectedComponentsWithStats` 0.30 ms (p95 0.47).

Synthetic panels filled with the stock panel color, stock profile, n=80:

| Panel | Median | p95 | Fish found |
| --- | --- | --- | --- |
| 247×190 | 0.57 ms | 0.83 ms | yes |
| 280×220 | 0.69 ms | 1.13 ms | yes |
| 420×330 | 1.52 ms | 2.07 ms | yes |
| 560×440 | 3.63 ms | 4.59 ms | yes |

A learned profile whose background model was built without the fish, on
a synthetic 247×182 panel, n=80: **0.51 ms** (p95 0.79). The stock
profile on the same frame was 0.44 ms (p95 0.54). Both returned the same
fish point. `FishLearner.feed` over 12 frames, including a reset and an
HSV conversion per frame in the harness, was 6.7 ms median (p95 7.3,
n=20), under 1 ms a frame, and only while the fish color is still being
learned. `BackgroundModel`'s first build (9 copies of the real crop) was
12.1 ms, once.

Simulated games (`tools/minigame_sim/bench.py` helpers, in-memory grab,
default 8 ms poll, short bite delay). This grab is a numpy crop, not the
OS.

| Skin | Games | Wall | Caught | Recognition CPU | Share of wall |
| --- | --- | --- | --- | --- | --- |
| official | 3 | 9.98 s (3.3 s/game) | 3/3 | 439 ms | 4.4% |
| water (learned) | 2 | 8.12 s | 2/2 | 915 ms | 11% |

Official, per call: grab 0.45 ms (365), detect 0.68 ms (325), full-window
bounds 2.0 ms (10), `_region_changed` 0.17 ms (24). `WindowLearner.learn`
did not run. On water it ran twice, 6.8 ms each, on the sim's 800×600
window.

So a steady minigame frame is under 1 ms of detection plus a deliberate
sleep. A full-window rescan is the expensive fishing path, and it is not
every frame. After the region is known, `wait_for_minigame_window`
(`src/fishing_bot.py:2692`) polls the crop every 50 ms (`2805`) and
rescans the window every 0.5 s, or every 0.15 s if the panel has already
moved (`2710`, `_MOVED_RESCAN_GAP` at `246`).

`find_fishing_window_bounds` on a synthetic window. Hit and miss stay
within 0.4 ms of each other. The HSV pass dominates.

| Window | Hit median | Hit p95 | Miss median | n |
| --- | --- | --- | --- | --- |
| 1280×720 | 4.1 ms | 4.9 ms | 3.8 ms | 15 / 12 |
| 1920×1080 | 9.0 ms | 9.8 ms | 9.2 ms | 15 / 12 |
| 2560×1440 | 15.4 ms | 17.5 ms | 15.1 ms | 15 / 12 |

`WindowLearner.learn` on a synthetic frame where a 247×190 panel appears
on an otherwise identical background (the learner returned that panel),
versus two identical frames:

| Window | Panel appeared | Identical frames | n |
| --- | --- | --- | --- |
| 1280×720 | 6.4 ms | 5.1 ms | 10 |
| 1920×1080 | 13.0 ms | 10.3 ms | 10 |
| 2560×1440 | 19.6 ms | 17.1 ms | 10 |

Inventory, 40 templates, loaded once into the class cache. The real strip
from `inventory_0360.png` is 197×301.

| Scan | Median | p95 | n | When |
| --- | --- | --- | --- | --- |
| Full strip, first template already ≥ 0.90 (`Aal_living.jpg`, 1 `matchTemplate`) | 1.01 ms | 1.75 ms | 20 | a page whose early template matches |
| 44 px ROI, all 40 templates (no match) | 1.33 ms | 1.99 ms | 30 | the catch path (`_find_new_item`, `1365`) |
| Page with no fish match (rescaled page 4, 72 `matchTemplate` calls) | 44.1 ms | 52.2 ms | 12 | startup / a page of non-fish items |
| Empty-slot scan, one `matchTemplate` | 1.25 ms | 1.80 ms | 30 | once per page |
| `_changed_box` | 0.046 ms | 0.070 ms | 50 | every catch poll |

Per template on the real strip: median from 0.83 ms to 1.10 ms, sum of
medians 35.6 ms. The page-4 count is 72 rather than 40 because gray peaks
on the puzzle arrows (the case in `tests/test_real_client_rescaled.py`)
go through `_disambiguate_confusable_fish`, which matches the 8
confusable color templates on a small patch. The 40 full-strip matches
are the bulk of the 44 ms.

A noise image at the fallback geometry (no page tabs: right-hand 200 px
from y=200), n=4, is a cost probe only: **71 ms** at 200×490 (720p),
**119 ms** at 200×850 (1080p), **256 ms** at 300×1210. Once per page, and
only when tabs are not set (`inventory_layout.py:30`).

Classic bite wait, `wait_for_classic_fish` (`3755`). The search image is
a 250 px wide bar of the upper half (`3826`). The template is
`classic_fish.jpg`, 71×37. The list at `3778` has 20 factors; 0.25 is
dropped because the scaled height is under 10 px, leaving 19. Threshold
0.7, then `time.sleep(0.02)` (`3907`). On a miss every selected scale
runs. On a hit the first scale ≥ 0.7 returns.

The fixture window is 480×320, so its real bar is 250×160. The 360-,
540- and 720-tall rows are that bar resized to the height a 1280×720,
1920×1080 and 2560×1440 window ask for. Direct 19-scale scan, n=30.
Best scores are on the resized content, so they only show that nothing
was accepted.

| Search image | Median | p95 | Scales run | Accepted |
| --- | --- | --- | --- | --- |
| 250×160 fixture | 11.2 ms | 13.0 ms | 19 | no (best 0.48) |
| 250×360 (720p crop) | 28.8 ms | 31.6 ms | 19 | no (best 0.58) |
| 250×540 (1080p crop) | 49.6 ms | 54.8 ms | 19 | no (best 0.56) |
| 250×720 (1440p crop) | 60.2 ms | 66.5 ms | 19 | no (best 0.55) |

The same function, `wait_for_classic_fish`, with `time.sleep` replaced by
a no-op so the interval is the poll's own work. The 250×360 unknown-scale
row is 15 polls (14 intervals). The 250×540 unknown-scale row is 8 polls.

| Poll | Median | p95 | Intervals |
| --- | --- | --- | --- |
| 250×360, no known scale (19 scales) | 30.3 ms | 39.4 ms | 14 |
| 250×360, known scale 1.0 (4 scales) | 5.88 ms | 7.15 ms | 76 |
| 250×360, known scale 0.5 (4 scales) | 6.04 ms | 7.46 ms | 73 |
| 250×540, no known scale | 47.7 ms | 49.6 ms | 7 |
| 250×540, known scale 1.0 | 9.74 ms | 13.6 ms | 33 |
| 250×160 fixture, no known scale | 10.8 ms | 12.1 ms | 31 |

Per-scale times on the 250×360 probe are flat: 1.05–1.47 ms for 18 of
the 19 scales, 2.07 ms for scale 2.0, sum of medians 24.7 ms (n=12
each). On the 250×540 probe the body is about 2.0 ms and scales
2.0–2.5 are 3.3–3.6 ms. A pasted 1.0× template, that scale first, on
the 160-tall bar: **0.51 ms**, one `matchTemplate`, score 1.0.
`cv2.setNumThreads` of 1, 2, 4 and 6 took 27.5–28.2 ms median on the
360-tall miss, same score 0.584. At 6 threads the p95 rose to 37 ms.

`cProfile` of a mix (20 full classic misses, 30 detects, 8 `detect_grid`,
4 cursor reads, one page-4 identify): 0.72 s, of which `matchTemplate`
is 0.63 s of self time.

### One jigsaw read and one solve

`detect_grid` on `board_0551.02.png` (grid 194×130 inside a 300×167
frame), n=40: **median 3.58 ms, p95 5.09 ms**. Structure check alone
1.67 ms (p95 2.22). Cells with the check off 1.86 ms (p95 2.59). The
same grid placed in a 1920×1080 frame is 3.44 ms (n=20). A tight crop
of the grid plus the structure margin (`board_area`, here 204×140) is
3.89 ms (n=40). The pixels outside the grid are not where this time goes.

On all 11 boards in `tests/fixtures/real_client_rescaled`, a crop of
`board_area` passed back with `origin` and `window_size` matched
`grid_found`, bounds, `board_mask`, filled cells and confidence (rounded
to 5 decimals) of the full frame. Mismatches: 0.

`detect_crate_slots` on `board_0551.02.png`: 0.77 ms (p95 1.03, n=30).
`detect_cursor_piece` at the labeled point on `held_0565.53.png`: 0.29 ms
(p95 0.39, n=40), piece id `piece3`. The same call with the point scaled
into a resized 1280×720 frame and a resized 1920×1080 frame: 0.30 ms and
0.29 ms. The steady path samples a 15 px radius (`_cursor_color_roi`,
`src/jigsaw_detector.py:590`).

`detect_cursor_piece(..., None)` builds the saturation mask and returns
before `_score_cursor_piece_colors` (`483`). Piece id stayed `None`.

| Frame | Saturated pixels | Mask (the call that runs) | Score (skipped) |
| --- | --- | --- | --- |
| Fixture 300×167 | 3,613 | 0.073 ms (n=10) | 0.72 ms (n=8) |
| Fixture resized to 1280×720 | 423,247 | 4.43 ms (n=6) | 120 ms (n=4) |
| Fixture resized to 1920×1080 | 952,457 | 10.2 ms (n=6) | 252 ms (n=4) |

The score tracks the saturated-pixel count. A real client frame with a
different amount of saturated UI would land elsewhere on that line. These
resized frames are probes.

`solver.solve` after loading the real cache. Empty board, the action
agreed with `actions[]` for all 6 pieces (20, 20, 0, 18, 18, 0):

| Call | Median | p95 | n |
| --- | --- | --- | --- |
| `solve()`, figure 0 (most placements) | 0.16 ms | 0.29 ms | 200 |
| `solve()`, figure 4 | 0.13 ms | 0.24 ms | 200 |
| 64 random boards, per `solve()` | 0.098 ms | | 5 batches |
| `rank_actions` limit 1, figure 0 | 0.16 ms | 0.26 ms | 100 |
| `skip_distance` (63 subsets, Python) | 0.10 ms | 0.13 ms | 100 |
| `avg_distance` (mean of 6 floats) | 0.005 ms | 0.006 ms | 100 |
| raw `actions[0]` | < 0.001 ms | < 0.001 ms | 300 |
| `rank_custom_actions` limit 1 | 0.017 ms | 0.032 ms | 80 |

Profile of 80 `solve()` calls (0.030 s): `rank_actions` is the whole
call, `skip_distance` is 0.010 s of self time, `avg_distance` is 24 calls
per figure-0 solve. `/proc/self/io` `read_bytes` did not move across 100
solves. No `open`, `load`, or `npz` in the profile. The cache is not
touched on the hot path.

Loading it is startup. `load_cache` of the 91 MB file: 762 ms, then 876 /
841 / 756 ms. `read_bytes` stayed 0, so this is decompress and copy with
the file already in the page cache. A cold disk read was not measured.
Result: `dsts` float64, 805 MB, plus `actions` uint8, 101 MB.

Cold DP with `JIGSAW_SOLVER_CACHE_DIR` pointed at an empty directory and
`save_cache` replaced so nothing was written: **5.38 s**. Height 0 is
169 ms for 1 state (the JIT compile; the log says the first run
compiles). The heavy heights are 10–14. Compress-and-write of the
already loaded tables, timed separately to `/tmp` and deleted: **7.26 s**,
90,914,757 bytes, and a reload of that probe matched the tables in memory
(764 ms). An uncompressed `np.savez` of the same tables was 0.45 s to
write, 906 MB, and 289 ms to reload. First launch without a cache is
about 5.4 s of compute plus 7.3 s of compress, then about 0.8 s to load
the next time. A session that already has the file pays the load only,
inside `get_solver` (`src/jigsaw_solver/deterministic.py:666`), once per
process (`src/jigsaw_bot.py:318`).

Around that, the bot waits. Default `jigsaw_action_delay` is 80 ms.
`_await_board` polls every 50 ms (`1572`) for up to 1 s. The result
dialog polls every 100 ms for up to 13 s (`1740`, `1743`). `solve()` is
invisible next to those.

A short jigsaw sim (6 crates, the test rig's shortened delays, in-memory
grab): 0.55 s wall, 5 pieces placed, no errors. 45 grabs, 6 of them the
full 800×600 window (one per round, `jigsaw_bot.py:424`, because the
crate slots sit outside the grid). `detect_grid` ran 11 times, median
4.4 ms, 52 ms total. The median grab area was 640 px, which is the
40×16 dialog patch. Production waits are longer than this rig's, so the
CPU share here is higher than a real round. The absolute costs are the
ones above.

BGR copy after a grab (`np.ascontiguousarray` of a BGRA buffer's first
three channels, the step in `_grab_screen` at `screen_capture.py:427`),
n=30:

| Rectangle | Median | p95 |
| --- | --- | --- |
| 280×220 | 0.30 ms | 0.42 ms |
| 250×360 | 0.47 ms | 0.84 ms |
| 1280×720 | 5.23 ms | 6.42 ms |
| 1920×1080 | 11.2 ms | 14.0 ms |
| 2560×1440 | 20.6 ms | 24.3 ms |

`dialog_watch.difference` on a 40×16 patch: 0.017 ms (p95 0.023, n=100).
`humanize.py` does no image work.

## 4. Findings

### 4.1 Classic miss poll runs every scale until one scale is known

**Where:** `src/fishing_bot.py:3755` `wait_for_classic_fish`, scale list
`3778`, rotation `3753` and `3852`, full list `3858`, early exit `3872`,
sleep `3907`. The remembered scale is cleared in `reset_session_state`
(`407`).

**What:** Until the icon has been found once, every poll converts the
upper bar to gray and runs `TM_CCOEFF_NORMED` at 19 scales. The early
exit only fires when some scale is ≥ 0.7, which is the end of the wait,
not the bulk of it. On the crop a 1280×720 window requests (250×360)
that miss is **30.3 ms median inside the real function** (direct scan
28.8 ms, p95 31.6, n=30) before the 20 ms sleep, so the loop holds about
60% of a core. At the 1920×1080 crop the direct scan is **49.6 ms
(p95 54.8, n=30)** and the function's own poll was 47.7 ms (7 intervals).
More OpenCV threads did not change it. A hit at a known scale, on the
short fixture bar, is one `matchTemplate` (0.51 ms).

The working tree already stops doing this after the first hit. The last
accepted scale is tried on every later poll, plus the next 3 scales from
a cursor that walks the list, and the loop stops at the first score
≥ 0.7. Measured on the same function: **5.88 ms median (p95 7.15, 76
intervals)** at the 720p crop with the known scale 1.0, and **9.74 ms
(p95 13.6, 33 intervals)** at the 1080p crop. Per-scale cost on the
360-tall probe is flat enough that 4 scales against 19 predicts this,
and the function measurement confirms it.

**What to keep:** The threshold, the template, and the 19 scales. On a
miss after a scale is known, match that scale every poll, then the next
3, and stop at the first score ≥ 0.7. A scale that is not in this batch
is matched on a later poll. Leave the full list in place until a scale
is known (section 5). Do not add a half-resolution shortcut (section 5).

**Saving:** Over 10 s of bite wait at the 720p crop, 19 scales are
30.3 / (30.3 + 20) = 60% of a core, 6.0 s of CPU. Four scales are
5.88 / (5.88 + 20) = 23% of a core, 2.3 s of CPU. The difference is
about 3.8 s per 10 s of waiting, on every cast after the first hit of
the run. The 1080p crop lands on the same 3.8 s because the shorter
poll runs more often. The first wait of a run has no remembered scale
and still pays the full list.

**Risk and tests:** This moves when a scale is seen, not the score that
accepts it. Worst extra delay once a scale is known is `ceil(18/3) = 6`
polls. At about 6 ms of work plus the 20 ms sleep, about 160 ms. The
reel timer starts at detection and then waits `classic_fishing_delay`
(default 3 s), so a later detection reels later by that amount. If two
scales both clear 0.7, the remembered scale can be the one in this batch
rather than the earliest in the full order.
`tests/test_perf_audit_fixes.py` `ClassicScaleRotationTests` checks that
a known scale is one match, that a stale scale is found within
`ceil(others / 3)` polls, that a miss still visits every scale, and that
an unknown scale still runs the full list on every poll.
`tests/test_grok_review_fixes.py` `ClassicReelDeadlineTests` mocks
`minMaxLoc` to 0.95, so it still passes and does not watch the scale
set. `tests/test_reliability.py` `SharedStateTests` checks that
`last_scale` is cleared between runs. `tools/run_tests.py` covers all
of these. 462 passed.

**Confidence:** measured, both for the 19-scale scan and for the 4-scale
poll the working tree runs.

### 4.2 Jigsaw polls were grabbing a full window to read a small rectangle

**Where:**

- `_await_board`, `src/jigsaw_bot.py:1565`, now calls `_read_board`
  (`1577`), which grabs `board_area()` (`jigsaw_detector.py:220`) and
  passes `origin` into `detect_grid` (`234`). The full window is still
  grabbed when the grid is not configured or the returned buffer is not
  the size that was asked for.
- `_handle_completed_board`, `1757`. `capture_full_window` runs only
  when `has_confirm_template()` is true (`1765`).
- The grab copy, `src/screen_capture.py:427` (screen) and `483` (window
  crop). Linux can fetch just the rectangle (`src/platforms/x11.py:270`).
  Windows `PrintWindow` always renders the whole window
  (`src/platforms/win32.py:259`). There is no area API.
- The main round still grabs the whole window once (`jigsaw_bot.py:424`).
  Crate slots are outside the grid, so that grab has to stay.

**What:** `detect_grid` is 3.6 ms on the grid and 3.4 ms when the same
grid sits in a 1920×1080 frame. The surrounding pixels do not change
the detector's time. They do cost a copy: **5.2 ms at 1280×720, 11.2 ms
at 1920×1080**, against 0.30 ms for a 280×220 crop. `grid_structure`
only reads the bounds plus a margin of `tol + 2` (`jigsaw_detector.py:148`).
On all 11 boards, a crop of that rectangle matched the full-frame read.

The result poll is the clearer waste when no confirm template is
shipped. `assets/jigsaw/` has no `jigsaw_confirm.png`.
`detect_confirm_button` then returns in **0.001 ms** (n=30; the missing
file is cached). The decision falls through to `dialog_watch` on a
40×16 patch, which the next lines already grab via `_dialog_grab`.
Default budget is 5 s of animation plus 8 s of confirm polling, at
100 ms.

A full-frame `matchTemplate` of the crate template that does exist
(35×40), as a stand-in for a confirm template of that size: 1.2 ms on
the fixture, **15.7 ms on that fixture resized to 1280×720 (n=8)**,
**45.5 ms at 1920×1080 (n=8)**. That stand-in is not the live path.
The live path does not search.

**What to keep:** In `_await_board`, grab the grid rectangle plus the
structure margin and call `detect_grid` with bounds in that crop. Cell
centers come back in window coordinates (`origin`). In the result poll,
skip `capture_full_window` when the confirm template is not loaded.
Leave a full-frame search in place for the day the file exists.
Searching only near the result point would miss a button drawn
somewhere else. That is a different change.

**Saving:** about 5 ms (720p) or 11 ms (1080p) of CPU copy per poll, and
the OS transfer on top, which this audit did not time. `_await_board`
is a few polls per piece (the board usually updates within the 1 s cap).
The result poll is up to about 130 full grabs if the dialog never comes
(13 s at 10 Hz): on the order of a second of memcpy at 1080p, and less
when the dialog appears. Linux area capture should drop by more than
the copy. The comment at `x11.py:275` says a 1280×720 `GetImage` was
7 ms and a 260×200 one 0.4 ms. That comment was not remeasured. Treat
it as a hypothesis.

**Risk and tests:** The 11 fixture boards matched, including confidence.
`tests/test_real_client_rescaled.py` `JigsawTests.test_board_reading`
locks the mask on those frames.
`tests/test_perf_audit_fixes.py` `BoardAreaTests` checks the cropped
read, including a grid shoved against the window edge, and checks that
the bot grabs one rectangle smaller than the window.
`ResultPollTests` checks that a missing template does not grab the
window, and that a shipped template is still searched.
`tests/test_jigsaw_sim.py` and `tests/test_jigsaw_bot.py` cover the
bot's use of the mask. `tools/run_tests.py` covers them. 462 passed.

**Confidence:** measured for the copy, for `detect_grid`, for the 11
crops, and for the stand-in full-frame template. OS grab unmeasured.

### 4.3 A missing cursor position was scoring the whole frame

**Where:** `src/jigsaw_bot.py:884` `_detect_cursor_piece` calls
`detect_cursor_piece(full_window, None)` when `cursor_rel` is None.
`src/jigsaw_detector.py:592` `_cursor_color_roi` then returns the whole
frame. The steady path, with a position, samples a 15 px radius (`600`).
The score is `_score_cursor_piece_colors` (`768`), which broadcasts
every saturated pixel against the piece colors
(`_hsv_distance_pixels_to_refs`, `823`). The working tree returns at
`483`, after the mask and before that score.

**What:** With a cursor point the call is 0.29 ms, including on a
resized 1920×1080 frame, because the sample is the small square. Without
a point, the score alone is **120 ms at 1280×720 and 252 ms at
1920×1080** on the resized fixture. The piece id is still `None`.
`_run_loop` (`jigsaw_bot.py:623`) treats `colored_pixels == 0` as
"nothing held" and any other count as an unrecognized piece, which it
discards. The mask that produces that count is 4.4 ms and 10.2 ms on
those same frames. A configured bot parks on the confirm button
(`_parked_point`) and does not hit this. It is the path when that
position, or the live cursor, is missing.

**What to keep:** If `cursor_pos` is None, return unknown after the
mask, with `colored_pixels` still set. Do not run
`_score_cursor_piece_colors` on the frame. Do not return
`colored_pixels` 0: that tells the bot nothing is held, and it will
open another crate on top of a piece it could not see.

**Saving:** the 120 ms or 252 ms score, each time the cursor position
is unknown. The remaining mask plus the full-window copy (5–11 ms) is
what that path still pays. Zero on the steady path, which never enters
it.

**Risk and tests:** Thresholds for a real cursor sample are unchanged.
`tests/test_perf_audit_fixes.py` `CursorWithoutPositionTests` checks
that a missing position does not call `_score_cursor_piece_colors`,
that a colored fixture still reports `colored_pixels > 0`, that a black
frame reports 0, and that a real held piece with a point is still
named. `tests/test_real_client_rescaled.py`
`JigsawTests.test_piece_on_the_cursor` covers the pointed path.
`tools/run_tests.py` covers both. 462 passed.

**Confidence:** measured. How often a running bot has no cursor position
is inferred from the callers, not from a session.

## 5. Rejected

**Rotating scales before any scale is known.** The full 19-scale poll
still runs until the first hit of a run, because `reset_session_state`
clears the remembered scale and the rotation only starts once one
exists. That is one bite wait per run, shared by every window (the
scale is a class variable). Starting the rotation immediately would
save the same 24 ms per poll for that one wait, and it would delay the
first detection by up to about 160 ms on a client whose scale is not
yet known, which is the wait where a miss matters most.
`ClassicScaleRotationTests.test_without_a_known_scale_every_poll_tries_them_all`
requires the full list on every poll in that state.

**Half-resolution classic search.** On the 250×360 probe the half-size
scan is 4.9 ms (p95 5.2, n=12) against 25–29 ms for the full miss, in
the same range as the 4-scale poll. Pasting the template at 0.30 and
0.40 scored 1.0 at full resolution and 0.608 at half resolution, the
same number as the empty bar's half-resolution score (0.608). Those
scales are the small end of the list the code keeps (the half template
is under 8 px and was dropped). A half-size search cannot see them.
Scale 0.50 did separate (half score 0.919). The same pattern showed on
the real 250×160 bar: empty half score 0.560, and the 0.30 and 0.40
pastes also 0.560, while their full-resolution score was 1.0. This is
a detector change.

**Dropping classic scales, or switching `TM_CCOEFF_NORMED`.** The 0.7
cutoff and the keep/drop choice are calibrated to this method and this
scale list. That is a detector change.

**OpenCV's thread pool for the classic scan.** Threads 1, 2, 4 and 6
were 27.5–28.2 ms median on the same 250×360 miss, identical score.
The p95 at 6 threads was 37 ms. The pool stays off, which is also what
the fish loop needs (`screen_capture.py:50`).

**Another pass on `abs_diff_max`.** Already done. At 1920×1080,
`abs_diff_max` is 6.1 ms (p95 7.4, n=12) and `.max(axis=2)` is 61.6 ms
(p95 64, n=5). The masks at threshold 25 are equal, and the OpenCV
result is already C-contiguous. A 1080p learn that actually finds the
panel is 13 ms and runs on the 0.5 s rescan, not per frame.

**Faster fish detection.** 0.48 ms inside an 8 ms budget. Four windows
are four copies of a small fraction of one core. The official sim spent
4.4% of its wall time in grab + detect + bounds + region check, and the
grab there was already free of the OS. Downscaling the full-window
search, searching only inside the click circle, or replacing the
largest blob with image moments still changes what is detected. Same
conclusion as `docs/performance-review.md`.

**Full-window bounds and `WindowLearner` as a per-frame problem.**
9 ms and 13 ms at 1080p, on a timer of 0.5 s (0.15 s only after the
panel has moved). Together that is a few percent of one core during
the bite wait, and only while the panel is not already in the
calibrated crop.

**Inventory template pool, or a coarser match.** The catch path already
searches the changed box: 40 templates there cost 1.3 ms. The 44 ms
figure is a whole strip with no template ≥ 0.90, once per such page.
Running the 40 templates on a pool can return a different file when two
of them both clear 0.90, because `_identify_item` (`1021`) keeps the
first one in dict order and returns at 0.90 (`1102`). The fallback
strip (119 ms at 1080p in the noise probe) is the geometry used when
page tabs are not configured (`inventory_layout.py:30`). Setting the
tabs is the reduction. The code already switches to the grid strip when
they are set.

**Cropping the jigsaw main loop the way `_await_board` is cropped.**
`_run_loop` (`424`) needs the crate slots, which sit to the right of
the grid. A board-only grab would mark those slots unread. The copy is
paid once per piece, next to an 80 ms action delay. The sim's 6 full
grabs in 6 rounds are this call.

**Skipping the cursor mask as well as the score.** The mask is 4–10 ms
on the resized probes, and it is what keeps `colored_pixels > 0`.
Returning 0 makes `_run_loop` treat a held piece as an empty cursor
and open another crate. The full-window copy on that path cannot be
skipped either: without a cursor point the only question the code
answers is whether any saturated pixel exists anywhere in the window.

**Vectorizing `grid_structure` / the 24 cells.** Profile: `seen` and
`_grid_cell_is_filled` are a few milliseconds of a 3.6 ms `detect_grid`,
and the bot then sleeps 50–80 ms. A vectorized cell test has to keep
every pad and every threshold bit-for-bit (`_grid_cell_is_filled`,
`534`). The saving is about a millisecond.

**`solve()` reading `actions[]` directly.** Re-rank is 0.13–0.16 ms.
`skip_distance` is most of that (0.10 ms of self time). The empty-board
action matched the table for every piece this run, and the re-rank
exists so a stale cache whose action column and distance column
disagree still plays the better move (`deterministic.py:520`). Against
an 80 ms action delay this is noise. Replacing it changes ties.

**Float32 tables, a different board order inside `_phase_relax`, or an
uncompressed cache.** Cold compute is 5.4 s plus 7.3 s to write the
file, once, when the cache is absent. A loaded session never runs it.
`JIGSAW_SOLVER_PRECISION=32` and a different tie-break order both
change which action is stored when costs are equal. The behavior
warning in `docs/performance-review.md` still applies. An uncompressed
cache reloaded in 289 ms against 764 ms compressed, and the file was
906 MB against 91 MB. That is half a second once per process, paid for
with a 10× file. The per-height log is in the appendix.

**`fishing_jigsaw_solver.py`.** The bot calls `deterministic.get_solver`.
The other file's parallel enqueue is not on this path. Left alone.

## 6. Open questions

- What `mss`, `XGetImage`, and `PrintWindow` actually cost on a covered
  and an uncovered client. The numpy copy is measured. The OS transfer
  is not. The 7 ms / 0.4 ms note in `x11.py` is from an earlier session
  on a display, and this audit did not repeat it.
- How long the classic fish icon stays up. The 4-scale rotation is a
  small delay if the icon remains for the reel delay. If it is a single
  frame, both the current period and a rotated poll can miss it, and a
  live recording would say which. The fixtures do not contain the icon.
- A real 1280×720 or 1920×1080 client frame. The classic and cursor
  numbers at those sizes are the fixture resized to the rectangle the
  code requests. Operation count should match. A different saturated
  area would move the cursor-score time, which scales with saturated
  pixels.
- Whether anyone ships `jigsaw_confirm.png`. If they do, the result poll
  starts paying a full-frame `matchTemplate`. A crate template of
  35×40, used as a stand-in on a resized frame, was 16 ms at 720p and
  46 ms at 1080p. Finding 4.2's "skip the grab" applies only while the
  file is absent.

## 7. Appendix

Scratch scripts, not in the repo. Logs `results.txt` and `follow.txt`
sit in the same directory. The unit-test log is the terminal of
`.venv/bin/python tools/run_tests.py`.

| Script | What it timed |
| --- | --- |
| `/tmp/fishbot-perf-cursor/bench_main.py` | Stages, cProfile, both sim skins, a short jigsaw sim |
| `/tmp/fishbot-perf-cursor/bench_follow.py` | Learner hit path, half-resolution on the tall bar, cache load, `solve()`, cache write probe, cold DP |

The stand-in full-frame crate `matchTemplate` (section 4.2) was an
inline script in the same session. The cache probe files were removed.
`bench_follow.py` printed `files_in_empty []` and
`probe_files_removed True True`.

Commands, from the repo root, after the unit tests:

```text
.venv/bin/python tools/run_tests.py
.venv/bin/python /tmp/fishbot-perf-cursor/bench_main.py
.venv/bin/python /tmp/fishbot-perf-cursor/bench_follow.py
```

Cold DP per height, total 5.38 s. Height 0 includes the Numba compile.
The loop is `range(24)`, so the last row is height 23.

| Height | States | ms | Height | States | ms |
| --- | --- | --- | --- | --- | --- |
| 0 | 1 | 168.7 | 12 | 2,704,156 | 760.1 |
| 1 | 24 | 8.9 | 13 | 2,496,144 | 670.6 |
| 2 | 276 | 9.1 | 14 | 1,961,256 | 490.4 |
| 3 | 2,024 | 10.6 | 15 | 1,307,504 | 310.8 |
| 4 | 10,626 | 17.9 | 16 | 735,471 | 173.5 |
| 5 | 42,504 | 41.3 | 17 | 346,104 | 79.0 |
| 6 | 134,596 | 93.5 | 18 | 134,596 | 37.2 |
| 7 | 346,104 | 187.4 | 19 | 42,504 | 15.2 |
| 8 | 735,471 | 310.3 | 20 | 10,626 | 9.3 |
| 9 | 1,307,504 | 478.8 | 21 | 2,024 | 8.2 |
| 10 | 1,961,256 | 632.2 | 22 | 276 | 8.7 |
| 11 | 2,496,144 | 773.4 | 23 | 24 | 7.5 |

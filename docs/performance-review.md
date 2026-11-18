# Performance review: fishing and jigsaw solvers

Measured on this machine (OpenCV 5.0.0, NumPy 2.5.4, Numba 0.68.1) with the
real detector, the real 40 inventory templates, and the real Numba kernels.
Frames were synthetic, at the sizes the code actually processes. Screen
capture was not timed: it needs a live game window, and it sits outside
these loops.

The loop that costs the most during play is not the fish detector. On a
1920×1080 window, `WindowLearner.learn` spends about **60 ms** reducing an
`absdiff` with `ndarray.max(axis=2)`. The same mask from `cv2.max` on the
three channels takes about **5 ms**, and the two masks compared equal.
That call runs again every full-window rescan while a new panel look is
being searched.

## Where the time goes

| Loop | When it runs | Measured | Against its budget |
| --- | --- | --- | --- |
| `WindowLearner.learn` channel max | Full-window rescan once adaptive search is on (every 0.5 s, or every 0.15 s if the panel has been seen to move) | **63 ms** at 1920×1080, **100 ms** at 2560×1440. `absdiff` + `max(axis=2)` is 55–60 ms of that; the morphology and contours are under 1 ms | Repeated for the whole bite wait on a client whose colors are not known yet |
| Inventory page scan, 40 templates, `matchTemplate` | Startup, page change, and any full rescan. One template is ~2.3 ms on a 200×700 strip | **38 ms** on the tab-sized strip (176×300). **93 ms** at 200×700, **181 ms** at 300×900 (the fallback strip runs to the bottom of the window) | Once per page, not per frame. A catch that already has a changed-box ROI is **2 ms** (48×48) to **5 ms** (80×80) |
| Jigsaw DP `_phase_relax` | First launch only, if `solver_cache.npz` is missing. Later launches load the cache | **7.2 / 33 / 145 ms** for 20k / 100k / 400k boards. Every board is visited once (the 1-cell piece reaches all 2²⁴ states), so the full compute is on the order of **6 s** of relax, **1.2 s** of skip-distance, **0.2 s** of the 2²⁴ enqueue scan | Seconds, once. The tables are ~800 MB of float64 |
| `detect_window_and_fish` | Every minigame frame, on the panel crop only. Default gap is 8 ms | **0.50 ms** stock and **0.70 ms** learned at 280×220. **0.81 / 1.4 ms** at 420×330. **1.5 / 3.0 ms** at 560×440 | Inside the 8 ms budget with room left for the capture and the deliberate sleep |
| `find_fishing_window_bounds` | Full window, before the panel region is known, and on later rescans | **1.7 / 4.0 / 7.0 ms** at 720p / 1080p / 1440p | Small next to `WindowLearner.learn` on the same frame |
| `JigsawDetector.detect_grid` | Once per jigsaw round, and every 50 ms while a placement is confirmed | **4.7 ms** for a 264×176 grid (structure check 1.9 ms). **6.0 ms** for a 528×352 grid | The round then sleeps 80 ms (`jigsaw_action_delay`); the confirm poll sleeps 50 ms |
| `Deterministic.solve` | Once per piece | **0.10 ms**. Reading `actions[]` for the same states is **0.0001 ms** | Invisible next to a click |
| Crate slot check | Once per jigsaw round, on a small rect around the slot | **0.6 ms** | The 14 ms figure is a full-window `matchTemplate`. The live slot check does not do that |
| `FishLearner.feed` | Only while the fish color is still being learned | **~0.1 ms** on a 280×220 panel, on top of the detect call | Over within a second |

`_region_changed` uses the same `absdiff(...).max(axis=2)` on the panel crop
during the bite wait (every poll, 50 ms sleep, once the region is calibrated
and the panel is not recognized). That crop is **1.6 ms** with NumPy and
**0.08 ms** with `cv2.max`, masks equal. Same change as the learner, smaller
payoff.

## Safe change

`WindowLearner.learn` (`src/fish_detector.py`) and `FishingBot._region_changed`
(`src/fishing_bot.py`) both do:

```python
diff = cv2.absdiff(reference, current).max(axis=2)
```

NumPy’s `max` on that array is the slow part. Replacing the channel
reduction with OpenCV’s per-channel max produces the same `uint8` image
(checked: `array_equal` on the max and on the thresholded mask):

```python
diff = cv2.absdiff(reference, current)
diff = cv2.max(diff[:, :, 0], cv2.max(diff[:, :, 1], diff[:, :, 2]))
```

The threshold that follows (`25`) is unchanged, so the contours, the
learned HSV range, and the “region changed” decision stay on the same
pixels. On a 1080p learn that cuts the call from about 63 ms to about 5 ms.
Nothing else in those two functions needs to move.

Do this in both places. They are the only `max(axis=2)` reductions on a
frame. The fish tracker already differences a grayscale image, which is the
fast path (`FishLearner._track`, well under 1 ms).

## Leave these alone

**The 8 ms fish loop.** `detect_window_and_fish` is two HSV `inRange`s plus
a connected-component pass, and the learned profile adds a background
`inRange` and a 3×3 open. At the real panel size that is under 1 ms. The
remaining time is `time.sleep` on purpose: the game draws at about 60 fps,
and polling flat-out takes a core the clients need. A half-resolution
detect, or swapping the largest-blob step for image moments, moves the
centroid or merges the fish with a decoration of the same color (the
official-redfish skin exists for that case). Four windows multiply a cost
that is already a small fraction of one core.

**The jigsaw grid read.** `grid_structure` walks each divider segment in
Python, then 24 cells each take a handful of means. Together that is about
5 ms, and the bot then waits 50–80 ms for the game. Vectorizing the 24
cells only stays faithful if every pad and every threshold stays bit-for-bit
the same; the saving is about a millisecond. The cursor-piece score is
0.4 ms on a 30 px sample.

**`solve()` re-ranking.** It does not read `actions[board, figure]`. It
re-scores every legal placement from `dsts` and prefers the stored action
on a tie, so a stale cache still plays the better move. That is 0.1 ms per
piece. Replacing it with the raw table lookup is a thousand times less CPU
and can pick a different action when two placements tie or the cached action
is out of date.

**The DP kernels, once a cache exists.** `_phase_relax` is about three
quarters of a cold compute. It is parallel across the 6 pieces only: within
one piece, several parents write the same child, and `if dst < dsts[new_idx]`
keeps the first writer when the costs are equal. A different board order
changes which action is stored on a tie. The enqueue scan of all 2²⁴ states
is about 8 ms per height (~0.2 s total), because it walks board ids in order
and that order is the tie-break. `JIGSAW_SOLVER_PRECISION=32` halves the
800 MB table and speeds the memory-bound skip pass; it also moves values
that are very close, so two placements can swap. Worth a test run of the
existing jigsaw checks before anyone ships it. It does not affect a session
that already loaded a float64 cache.

**The inventory `minMaxLoc` loop.** On a response map with 60 peaks it was
1.2 ms. Building that map was 2.3 ms, and a page does it 40 times. The
empty-slot scan was already switched to `np.where` plus NMS for this reason;
doing the same for items saves about a millisecond on the templates that
actually match. `_identify_item` returns the first template at or above
0.90, in dict order. Matching the 40 templates on a thread pool would drop
the wall time and can return a different icon whenever two of them both
clear 0.90. The catch path is already limited to the rectangle that
changed; that is the reduction that matters (about 2–5 ms instead of a full
strip).

The fallback inventory strip, used when page tabs are not set, is the right
200 px of the window from y=200 to the bottom. That is the 93–181 ms scan.
With tabs set, the strip is the grid (about 176×300) and the same scan is
about 38 ms. That is a setup choice, not a code change.

## What a faster wrong answer would look like

These showed up while looking for shortcuts. Each one changes a decision
the current code is careful about.

- Downscaling the full-window panel search. A miss on the half-size image
  is the common case (the panel is not open yet), so the full search still
  has to run, or a dim panel gets skipped.
- Searching only inside the click circle. The fish is tracked before it
  enters the circle; the lead aim uses that track.
- `TM_SQDIFF` or `TM_CCORR_NORMED` instead of `TM_CCOEFF_NORMED`. The 0.80
  and 0.90 cutoffs and the keep/drop choice are calibrated to the current
  score.
- More than 6-way parallelism in `_phase_relax`, or collecting visited
  boards in parent order instead of board-id order. Tie-breaks move.
- Dropping the re-rank in `solve()` to use `actions[]` directly. Fine for a
  cache this process just wrote; not fine for an older file whose action
  column and distance column disagree.

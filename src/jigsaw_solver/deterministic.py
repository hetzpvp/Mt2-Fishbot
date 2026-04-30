"""
Deterministic solver using dynamic programming.
Computes optimal actions for all possible game states.

Optimized with NumPy and Numba for fast computation.
"""
import os
import sys
import numpy as np
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

try:
    from numba import njit, prange
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    print("Warning: numba not installed. Install with 'pip install numba' for 10-50x speedup.")

try:
    from .jigsaw import (
        Jigsaw, FIGURES, N, M, SKIP_ACTION,
        TERMINAL_STATE, TOTAL_CELLS, TOTAL_FIGURES
    )
    from .solver import Solver
except ImportError:  # Allows running this module directly from src/jigsaw_solver.
    from jigsaw import (  # type: ignore
        Jigsaw, FIGURES, N, M, SKIP_ACTION,
        TERMINAL_STATE, TOTAL_CELLS, TOTAL_FIGURES
    )
    from solver import Solver  # type: ignore

# Pre-compute figure data as numpy arrays
_FIG_VALUES = np.array([f.value for f in FIGURES], dtype=np.uint32)
_FIG_SIZES = np.array([f.size for f in FIGURES], dtype=np.uint8)
_FIG_MAX_X = np.array([f.max_offset[0] for f in FIGURES], dtype=np.uint8)
_FIG_MAX_Y = np.array([f.max_offset[1] for f in FIGURES], dtype=np.uint8)

# Pre-compute all subsets as a 2D array (63 subsets, padded to max length 6)
# Each row: [length, idx0, idx1, idx2, idx3, idx4, idx5]
_SUBSETS_ARR = []
from itertools import combinations
for r in range(1, TOTAL_FIGURES + 1):
    for combo in combinations(range(TOTAL_FIGURES), r):
        row = [len(combo)] + list(combo) + [0] * (TOTAL_FIGURES - len(combo))
        _SUBSETS_ARR.append(row)
_SUBSETS_NP = np.array(_SUBSETS_ARR, dtype=np.int32)

CACHE_SCHEMA_VERSION = 2
_PRECISION = os.environ.get("JIGSAW_SOLVER_PRECISION", "64").strip()
if _PRECISION not in {"32", "64"}:
    print(f"Unknown JIGSAW_SOLVER_PRECISION={_PRECISION!r}; using float64.")
    _PRECISION = "64"
_DST_DTYPE = np.float64 if _PRECISION == "64" else np.float32
INF = _DST_DTYPE(1e9)


@dataclass(frozen=True)
class ActionScore:
    """Expected-cost score for one immediate action from a board state."""

    action: int
    expected_rounds: float
    next_board: int
    is_skip: bool = False


def _default_cache_path() -> str:
    cache_file = Deterministic.CACHE_FILE
    if _PRECISION == "64":
        root, ext = os.path.splitext(cache_file)
        cache_file = f"{root}_f64{ext}"
    cache_dir = os.environ.get("JIGSAW_SOLVER_CACHE_DIR")
    if cache_dir:
        return os.path.join(cache_dir, cache_file)
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "data", "cache", "jigsaw", cache_file)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(project_root, "data", "cache", "jigsaw", cache_file)


def _cache_metadata() -> dict:
    return {
        "schema_version": np.array([CACHE_SCHEMA_VERSION], dtype=np.int32),
        "total_cells": np.array([TOTAL_CELLS], dtype=np.int32),
        "total_figures": np.array([TOTAL_FIGURES], dtype=np.int32),
        "precision": np.array([int(_PRECISION)], dtype=np.int32),
        "figure_values": _FIG_VALUES,
        "figure_sizes": _FIG_SIZES,
        "figure_max_x": _FIG_MAX_X,
        "figure_max_y": _FIG_MAX_Y,
    }

# Precompute per-figure placement table:
# _PLACEMENTS[f] is an (n_actions_for_f, 2) int32 array: (action, shifted_mask)
# Drops bounds checks at runtime — placements that fail max_offset are excluded.
_PLACEMENTS_LIST = []
for _f in FIGURES:
    rows = []
    for _a in range(TOTAL_CELLS):
        _x, _y = _a >> 2, _a & 0b11
        if _x > _f.max_offset[0] or _y > _f.max_offset[1]:
            continue
        rows.append((_a, _f.value >> (_x * N + _y)))
    _PLACEMENTS_LIST.append(np.array(rows, dtype=np.int64))
# Pack into a flat layout: _PLACE_FLAT[i] = (f_idx, action, shifted_mask)
# usable directly in a tight numba loop.
_PLACE_OFFSETS = np.zeros(TOTAL_FIGURES + 1, dtype=np.int32)
for _i, _arr in enumerate(_PLACEMENTS_LIST):
    _PLACE_OFFSETS[_i + 1] = _PLACE_OFFSETS[_i] + len(_arr)
_PLACE_ACTIONS = np.concatenate(
    [_a[:, 0].astype(np.uint8) for _a in _PLACEMENTS_LIST]
) if _PLACEMENTS_LIST else np.empty(0, dtype=np.uint8)
_PLACE_SHIFTED = np.concatenate(
    [_a[:, 1].astype(np.uint32) for _a in _PLACEMENTS_LIST]
) if _PLACEMENTS_LIST else np.empty(0, dtype=np.uint32)


if HAS_NUMBA:
    @njit(cache=True, inline='always')
    def _compute_skip_dst_numba(dsts_slice, subsets):
        """Compute optimal skip distance for one board's 6 dsts."""
        skp_dst = INF
        n_subsets = subsets.shape[0]
        for s in range(n_subsets):
            length = subsets[s, 0]
            sum_dst = 0.0
            for i in range(length):
                sum_dst += dsts_slice[subsets[s, 1 + i]]
            expected = (TOTAL_FIGURES + sum_dst) / length
            if expected < skp_dst:
                skp_dst = expected
        return skp_dst

    # ---- PHASE 1: compute skip-distance & dst per board -------------------
    # Each iteration writes only to its own base_idx range — fully parallel,
    # no atomics required. dst_out[i] receives (1 + mean(dsts[base..base+6])).
    @njit(cache=True, parallel=True, boundscheck=False)
    def _phase_skip_dst(stack_boards, stack_size, dsts, actions, dst_out, subsets):
        n_figures = TOTAL_FIGURES
        for board_idx in prange(stack_size):
            board = stack_boards[board_idx]
            base_idx = board * n_figures

            skp_dst = _compute_skip_dst_numba(
                dsts[base_idx:base_idx + n_figures], subsets
            )

            avg = 0.0
            for i in range(n_figures):
                v = dsts[base_idx + i]
                if v > skp_dst:
                    actions[base_idx + i] = SKIP_ACTION
                    v = skp_dst
                    dsts[base_idx + i] = v
                avg += v
            dst_out[board_idx] = 1.0 + avg / n_figures

    # ---- PHASE 2: relax dsts/actions for child states ---------------------
    # Parallelism strategy: outer loop over f_idx (6-way). This is RACE-FREE
    # because dsts[new_idx] = dsts[new_board * 6 + f_idx] — different f_idx
    # values write to DISJOINT indices. Within one f_idx multiple parents may
    # touch the same new_idx, but that's serial inside one thread.
    # Also marks `visited[new_board] = 1` (idempotent byte writes — race-safe).
    @njit(cache=True, parallel=True, boundscheck=False)
    def _phase_relax(
        height, stack_boards, stack_size, dst_in,
        dsts, actions,
        place_actions, place_shifted, place_offsets,
        fig_sizes,
        visited,
    ):
        n_figures = TOTAL_FIGURES
        for f_idx in prange(n_figures):
            if height + fig_sizes[f_idx] > TOTAL_CELLS:
                continue
            start = place_offsets[f_idx]
            end = place_offsets[f_idx + 1]

            for board_idx in range(stack_size):
                board = stack_boards[board_idx]
                dst = dst_in[board_idx]

                for k in range(start, end):
                    shifted = place_shifted[k]
                    if (board | shifted) != board:
                        continue
                    new_board = board & (~shifted)
                    new_idx = new_board * n_figures + f_idx

                    if dst < dsts[new_idx]:
                        dsts[new_idx] = dst
                        actions[new_idx] = place_actions[k]

                    visited[new_board] = 1  # idempotent

    # ---- PHASE 3: serial scan to dedup-enqueue newly visited boards -------
    # popcount-based bucket = TOTAL_CELLS - popcount(new_board). Independent of
    # which figure produced new_board (algebraic identity of the original code).
    @njit(cache=True, boundscheck=False)
    def _phase_enqueue(visited, in_stack, next_stacks, next_sizes):
        total_states = visited.shape[0]
        for board in range(total_states):
            if visited[board] and not in_stack[board]:
                in_stack[board] = True
                # popcount(board) — board fits in 24 bits
                v = board
                # Brian Kernighan's bit-count
                cnt = 0
                while v:
                    v &= v - 1
                    cnt += 1
                bucket = TOTAL_CELLS - cnt
                pos = next_sizes[bucket]
                next_stacks[bucket, pos] = board
                next_sizes[bucket] = pos + 1
            visited[board] = 0  # clear for next height pass

    @njit(cache=True)
    def _simulate_distribution_numba(
        actions, fig_values, start_board, start_figure, n_sims, seed, max_round
    ):
        """
        Run n_sims simulations of greedy play from (start_board, start_figure)
        using the precomputed optimal actions table. Returns a histogram of
        round counts (length = max_round + 1; final bucket catches overflow).
        """
        counts = np.zeros(max_round + 1, dtype=np.int64)
        # xorshift64* PRNG (deterministic, seeded)
        state = np.uint64(seed if seed != 0 else 1)

        n_figures = np.uint32(6)
        terminal = np.uint32(TERMINAL_STATE)
        skip_action = np.uint8(SKIP_ACTION)

        for _ in range(n_sims):
            board = np.uint32(start_board)
            figure = np.uint8(start_figure)
            rounds = 0

            while board != terminal:
                action = actions[board * n_figures + figure]
                if action != skip_action:
                    board = board | (np.uint32(fig_values[figure]) >> np.uint32(action))
                rounds += 1
                if rounds >= max_round:
                    break

                # xorshift64* — advance state and pick next figure
                state ^= state >> np.uint64(12)
                state ^= state << np.uint64(25)
                state ^= state >> np.uint64(27)
                rnd = state * np.uint64(0x2545F4914F6CDD1D)
                figure = np.uint8(rnd % np.uint64(6))

            if rounds > max_round:
                rounds = max_round
            counts[rounds] += 1

        return counts


class Deterministic(Solver):
    """
    Deterministic optimal solver using dynamic programming.
    Precomputes the best action for every possible (board, figure) state.
    """

    CACHE_FILE = "solver_cache.npz"

    def __init__(self):
        total_states = 1 << TOTAL_CELLS
        self.dsts = np.full(total_states * TOTAL_FIGURES, INF, dtype=_DST_DTYPE)
        self.actions = np.full(total_states * TOTAL_FIGURES, SKIP_ACTION, dtype=np.uint8)
        self.in_stack = np.zeros(total_states, dtype=np.bool_)

    def save_cache(self, filepath: str = None):
        """Save computed data to cache file."""
        if filepath is None:
            filepath = _default_cache_path()
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        np.savez_compressed(
            filepath,
            dsts=self.dsts,
            actions=self.actions,
            **_cache_metadata(),
        )
        print(f"Saved cache to {filepath}")

    def load_cache(self, filepath: str = None) -> bool:
        """Load computed data from cache file."""
        if filepath is None:
            filepath = _default_cache_path()
        if os.path.exists(filepath):
            data = None
            try:
                data = np.load(filepath)
                dsts = data['dsts']
                actions = data['actions']
                self._validate_cache(data, dsts, actions)
                self.dsts = np.ascontiguousarray(dsts, dtype=_DST_DTYPE)
                self.actions = np.ascontiguousarray(actions, dtype=np.uint8)
                data.close()
                print(f"Loaded cache from {filepath}")
                return True
            except Exception as e:
                if data is not None:
                    data.close()
                print(f"Failed to load cache: {e}, regenerating...")
                try:
                    os.remove(filepath)
                except:
                    pass
        return False

    def _validate_cache(self, data, dsts: np.ndarray, actions: np.ndarray) -> None:
        """Reject stale/incompatible policy caches before they affect decisions."""
        expected_shape = ((1 << TOTAL_CELLS) * TOTAL_FIGURES,)
        if dsts.shape != expected_shape:
            raise ValueError(f"dsts shape {dsts.shape} != {expected_shape}")
        if actions.shape != expected_shape:
            raise ValueError(f"actions shape {actions.shape} != {expected_shape}")
        if dsts.dtype != np.dtype(_DST_DTYPE):
            raise ValueError(f"dsts dtype {dsts.dtype} != {np.dtype(_DST_DTYPE)}")
        if actions.dtype != np.dtype(np.uint8):
            raise ValueError(f"actions dtype {actions.dtype} != uint8")
        if int(actions.max(initial=0)) > SKIP_ACTION:
            raise ValueError("actions contain out-of-range action ids")

        # Legacy caches did not carry metadata. Shape and dtype validation keeps
        # them usable, while newly written caches are fully self-describing.
        if "schema_version" not in data.files:
            return
        if int(data["schema_version"][0]) != CACHE_SCHEMA_VERSION:
            raise ValueError("cache schema version mismatch")
        if int(data["total_cells"][0]) != TOTAL_CELLS:
            raise ValueError("cache board size mismatch")
        if int(data["total_figures"][0]) != TOTAL_FIGURES:
            raise ValueError("cache figure count mismatch")
        if int(data["precision"][0]) != int(_PRECISION):
            raise ValueError("cache precision mismatch")
        for key, expected in (
            ("figure_values", _FIG_VALUES),
            ("figure_sizes", _FIG_SIZES),
            ("figure_max_x", _FIG_MAX_X),
            ("figure_max_y", _FIG_MAX_Y),
        ):
            if key not in data.files or not np.array_equal(data[key], expected):
                raise ValueError(f"cache {key} mismatch")

    def run(self):
        """Run the DP algorithm to compute optimal actions."""
        if self.load_cache():
            return

        if HAS_NUMBA:
            self._run_numba()
        else:
            self._run_pure_python()

    def _run_numba(self):
        """Numba-accelerated 3-phase pipeline (race-free, parallel)."""
        import time
        t_total = time.perf_counter()
        print("Computing optimal strategy with Numba acceleration...")
        print("First run compiles JIT - subsequent runs will be faster.")

        total_states = 1 << TOTAL_CELLS

        # Initialize terminal state
        term_base = TERMINAL_STATE * TOTAL_FIGURES
        self.dsts[term_base:term_base + TOTAL_FIGURES] = 0.0
        self.in_stack[TERMINAL_STATE] = True

        # Per-height bucket capacity: bounded by C(24, popcount). Max is C(24,12).
        from math import comb
        max_cap = max(comb(TOTAL_CELLS, k) for k in range(TOTAL_CELLS + 1))
        next_stacks = np.zeros((TOTAL_CELLS + 1, max_cap), dtype=np.uint32)
        next_sizes = np.zeros(TOTAL_CELLS + 1, dtype=np.int64)
        next_stacks[0, 0] = TERMINAL_STATE
        next_sizes[0] = 1

        # Per-height temporary buffer for dst values.
        dst_buf = np.empty(max_cap, dtype=_DST_DTYPE)
        # Visited bitmap reused across heights (auto-cleared in _phase_enqueue).
        visited = np.zeros(total_states, dtype=np.uint8)

        fig_sizes_i64 = _FIG_SIZES.astype(np.int64)

        for height in range(TOTAL_CELLS):
            size = int(next_sizes[height])
            if size == 0:
                continue
            t0 = time.perf_counter()

            stack = next_stacks[height, :size]

            _phase_skip_dst(
                stack, np.int64(size),
                self.dsts, self.actions, dst_buf[:size], _SUBSETS_NP,
            )
            _phase_relax(
                np.int64(height), stack, np.int64(size), dst_buf[:size],
                self.dsts, self.actions,
                _PLACE_ACTIONS, _PLACE_SHIFTED, _PLACE_OFFSETS,
                fig_sizes_i64, visited,
            )
            _phase_enqueue(visited, self.in_stack, next_stacks, next_sizes)

            dt = time.perf_counter() - t0
            print(f"  Height {height:>2}/{TOTAL_CELLS}: {size:>9} states  ({dt*1000:7.1f} ms)")

        print(f"Strategy computation complete in {time.perf_counter()-t_total:.2f}s")
        self.save_cache()

    def _run_pure_python(self):
        """Pure Python fallback (slower)."""
        print("Computing optimal strategy (pure Python - this will be slow)...")
        print("Install numba for 10-50x speedup: pip install numba")

        # ... simplified version for fallback
        self._run_simple()

    def _run_simple(self):
        """Simple pure-python implementation."""
        from itertools import combinations

        subsets = [list(c) for r in range(1, 7) for c in combinations(range(6), r)]
        stacks = [[] for _ in range(25)]
        stacks[0].append(TERMINAL_STATE)

        term_base = TERMINAL_STATE * TOTAL_FIGURES
        self.dsts[term_base:term_base + TOTAL_FIGURES] = 0.0
        self.in_stack[TERMINAL_STATE] = True

        for height in range(TOTAL_CELLS):
            stack = stacks[height]
            print(f"  Height {height}: {len(stack)} states")

            for board in stack:
                base = board * TOTAL_FIGURES

                # Skip distance
                skp = min((6 + sum(self.dsts[base + i] for i in s)) / len(s) for s in subsets)
                for i in range(6):
                    if self.dsts[base + i] > skp:
                        self.dsts[base + i] = skp
                        self.actions[base + i] = SKIP_ACTION

                dst = 1.0 + sum(self.dsts[base:base + 6]) / 6

                for f_idx in range(6):
                    if height + _FIG_SIZES[f_idx] > 24:
                        continue
                    fv, fs = int(_FIG_VALUES[f_idx]), int(_FIG_SIZES[f_idx])
                    mx, my = int(_FIG_MAX_X[f_idx]), int(_FIG_MAX_Y[f_idx])

                    for a in range(24):
                        x, y = a >> 2, a & 3
                        if x > mx or y > my:
                            continue
                        shifted = fv >> (x * 4 + y)
                        if (board | shifted) != board:
                            continue

                        nb = board & (~shifted)
                        ni = nb * 6 + f_idx

                        if dst < self.dsts[ni]:
                            self.dsts[ni] = dst
                            self.actions[ni] = a

                        if not self.in_stack[nb]:
                            self.in_stack[nb] = True
                            stacks[height + fs].append(nb)

        print("Done!")
        self.save_cache()

    def solve(self, game: Jigsaw) -> int:
        """Get the optimal action for the current game state."""
        # The action table is a fast policy hint. Re-ranking from dsts is still
        # cheap for one live state and protects legacy caches from stale or
        # numerically tied action entries.
        ranked = self.rank_actions(game.board, game.figure, limit=1, include_skip=True)
        return ranked[0].action if ranked else SKIP_ACTION

    def distances(self, board: int) -> Iterator[Tuple[int, float]]:
        """Get (action, distance) pairs for a board state."""
        for i in range(TOTAL_FIGURES):
            ranked = self.rank_actions(board, i, limit=1, include_skip=True)
            if ranked:
                yield (ranked[0].action, ranked[0].expected_rounds)
            else:
                yield (SKIP_ACTION, 0.0)

    def avg_distance(self, board: int) -> float:
        """Average expected distance over all 6 possible figures from a board."""
        base = board * TOTAL_FIGURES
        return float(self.dsts[base:base + TOTAL_FIGURES].mean())

    def skip_distance(self, board: int) -> float:
        """
        Expected rounds if the current piece is discarded and future pieces are
        skipped until an optimal acceptance subset appears.
        """
        if board == TERMINAL_STATE:
            return 0.0
        base = board * TOTAL_FIGURES
        best = float(INF)
        for row in _SUBSETS_NP:
            length = int(row[0])
            total = 0.0
            for i in range(length):
                total += float(self.dsts[base + int(row[1 + i])])
            expected = (TOTAL_FIGURES + total) / length
            if expected < best:
                best = expected
        return best

    def score_action(self, board: int, figure_idx: int, action: int) -> ActionScore:
        """Return the expected-cost score for one immediate normal-piece action."""
        if action == SKIP_ACTION:
            return ActionScore(action, self.skip_distance(board), board, True)
        shifted = int(_FIG_VALUES[figure_idx]) >> action
        next_board = board | shifted
        return ActionScore(action, 1.0 + self.avg_distance(next_board), next_board, False)

    def rank_actions(
        self,
        board: int,
        figure_idx: int,
        limit: Optional[int] = None,
        include_skip: bool = True,
    ) -> List[ActionScore]:
        """
        Rank all legal immediate actions by expected rounds to finish.

        This exposes the same objective used by the DP cache. `solve()` remains
        the fastest policy lookup; this method is for diagnostics and cases
        where callers need the full placement ordering.
        """
        scores: List[ActionScore] = []
        start = int(_PLACE_OFFSETS[figure_idx])
        end = int(_PLACE_OFFSETS[figure_idx + 1])
        for k in range(start, end):
            shifted = int(_PLACE_SHIFTED[k])
            if board & shifted:
                continue
            action = int(_PLACE_ACTIONS[k])
            next_board = board | shifted
            scores.append(ActionScore(action, 1.0 + self.avg_distance(next_board), next_board))
        if include_skip:
            scores.append(ActionScore(SKIP_ACTION, self.skip_distance(board), board, True))

        policy_action = int(self.actions[board * TOTAL_FIGURES + figure_idx])
        scores.sort(key=lambda s: (s.expected_rounds, s.action != policy_action, s.action))
        return scores if limit is None else scores[:limit]

    def rank_custom_actions(
        self,
        board: int,
        mask: int,
        max_offset: Tuple[int, int],
        limit: Optional[int] = None,
    ) -> List[ActionScore]:
        """
        Rank legal placements for a non-standard piece.

        Special pieces are not part of the random future-piece distribution, so
        the score is one placement round plus the normal-piece average distance
        from the resulting board.
        """
        scores: List[ActionScore] = []
        max_x, max_y = max_offset
        for action in range(TOTAL_CELLS):
            x, y = Jigsaw.action_to_offsets(action)
            if x > max_x or y > max_y:
                continue
            shifted = mask >> action
            if board & shifted:
                continue
            next_board = board | shifted
            scores.append(ActionScore(action, 1.0 + self.avg_distance(next_board), next_board))
        scores.sort(key=lambda s: (s.expected_rounds, s.action))
        return scores if limit is None else scores[:limit]

    def simulate_distribution(self, start_board: int, start_figure: int,
                              n_sims: int, seed: int = 2024,
                              max_round: int = 60) -> np.ndarray:
        """
        Run n_sims greedy playouts and return a histogram of finishing rounds
        (length max_round + 1). Uses numba when available.
        """
        if HAS_NUMBA:
            return _simulate_distribution_numba(
                self.actions, _FIG_VALUES,
                np.uint32(start_board), np.uint8(start_figure),
                np.int64(n_sims), np.uint64(seed), np.int64(max_round),
            )
        # Pure-python fallback
        import random as _random
        rng = _random.Random(int(seed))
        counts = np.zeros(max_round + 1, dtype=np.int64)
        actions = self.actions
        for _ in range(n_sims):
            board = int(start_board)
            figure = int(start_figure)
            rounds = 0
            while board != TERMINAL_STATE and rounds < max_round:
                a = int(actions[board * TOTAL_FIGURES + figure])
                if a != SKIP_ACTION:
                    board |= int(_FIG_VALUES[figure]) >> a
                rounds += 1
                figure = rng.randint(0, 5)
            counts[min(rounds, max_round)] += 1
        return counts


# Singleton instance for caching the computed strategy
_cached_solver: Deterministic = None


def get_solver() -> Deterministic:
    """Get or create the cached deterministic solver."""
    global _cached_solver
    if _cached_solver is None:
        _cached_solver = Deterministic()
        _cached_solver.run()
    return _cached_solver

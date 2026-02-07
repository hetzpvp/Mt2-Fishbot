"""
Deterministic solver using dynamic programming.
Computes optimal actions for all possible game states.

Optimized with NumPy and Numba for fast computation.
"""
import os
import pickle
import numpy as np
from typing import Iterator, Tuple

try:
    from numba import njit, prange
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    print("Warning: numba not installed. Install with 'pip install numba' for 10-50x speedup.")

from jigsaw import (
    Jigsaw, FIGURES, N, M, SKIP_ACTION, 
    TERMINAL_STATE, TOTAL_CELLS, TOTAL_FIGURES
)
from solver import Solver

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

INF = np.float32(1e9)


if HAS_NUMBA:
    @njit(cache=True)
    def _compute_skip_dst_numba(dsts, subsets):
        """Compute optimal skip distance using numba."""
        skp_dst = INF
        n_subsets = subsets.shape[0]
        
        for s in range(n_subsets):
            length = subsets[s, 0]
            sum_dst = np.float32(0.0)
            for i in range(length):
                sum_dst += dsts[subsets[s, 1 + i]]
            expected = (TOTAL_FIGURES + sum_dst) / length
            if expected < skp_dst:
                skp_dst = expected
        
        return skp_dst

    @njit(cache=True, parallel=True)
    def _process_height_numba(
        height, stack_boards, stack_size,
        dsts, actions, in_stack,
        fig_values, fig_sizes, fig_max_x, fig_max_y,
        subsets, next_stacks, next_sizes
    ):
        """Process all boards at a given height level using numba with parallelization."""
        n_figures = len(fig_values)
        
        for board_idx in prange(stack_size):
            board = stack_boards[board_idx]
            base_idx = board * n_figures
            
            # Compute skip distance
            board_dsts = dsts[base_idx:base_idx + n_figures]
            skp_dst = _compute_skip_dst_numba(board_dsts, subsets)
            
            # Update where skipping is better
            for i in range(n_figures):
                if dsts[base_idx + i] > skp_dst:
                    actions[base_idx + i] = SKIP_ACTION
                    dsts[base_idx + i] = skp_dst
            
            # Compute average distance
            avg = np.float32(0.0)
            for i in range(n_figures):
                avg += dsts[base_idx + i]
            dst = 1.0 + avg / n_figures
            
            # Try all figures and placements
            for f_idx in range(n_figures):
                if height + fig_sizes[f_idx] > TOTAL_CELLS:
                    continue
                
                f_value = fig_values[f_idx]
                f_size = fig_sizes[f_idx]
                max_x = fig_max_x[f_idx]
                max_y = fig_max_y[f_idx]
                
                for action in range(TOTAL_CELLS):
                    x = action >> 2
                    y = action & 0b11
                    
                    if x > max_x or y > max_y:
                        continue
                    
                    shift = x * N + y
                    shifted = f_value >> shift
                    
                    # Check if valid (figure fits on filled cells)
                    if (board | shifted) != board:
                        continue
                    
                    # New board by removing figure
                    new_board = board & (~shifted)
                    new_idx = new_board * n_figures + f_idx
                    
                    if dst < dsts[new_idx]:
                        dsts[new_idx] = dst
                        actions[new_idx] = (x << 2) | y
                    
                    if not in_stack[new_board]:
                        in_stack[new_board] = True
                        # Add to appropriate next stack
                        next_idx = height + f_size
                        pos = next_sizes[next_idx]
                        next_stacks[next_idx, pos] = new_board
                        next_sizes[next_idx] += 1

    @njit(cache=True)
    def _process_height_serial(
        height, stack_boards, stack_size,
        dsts, actions, in_stack,
        fig_values, fig_sizes, fig_max_x, fig_max_y,
        subsets, next_stacks, next_sizes
    ):
        """Serial version for when parallel doesn't work well."""
        n_figures = len(fig_values)
        
        for board_idx in range(stack_size):
            board = stack_boards[board_idx]
            base_idx = board * n_figures
            
            # Compute skip distance
            board_dsts = dsts[base_idx:base_idx + n_figures]
            skp_dst = _compute_skip_dst_numba(board_dsts, subsets)
            
            # Update where skipping is better
            for i in range(n_figures):
                if dsts[base_idx + i] > skp_dst:
                    actions[base_idx + i] = SKIP_ACTION
                    dsts[base_idx + i] = skp_dst
            
            # Compute average distance
            avg = np.float32(0.0)
            for i in range(n_figures):
                avg += dsts[base_idx + i]
            dst = 1.0 + avg / n_figures
            
            # Try all figures and placements
            for f_idx in range(n_figures):
                if height + fig_sizes[f_idx] > TOTAL_CELLS:
                    continue
                
                f_value = fig_values[f_idx]
                f_size = fig_sizes[f_idx]
                max_x = fig_max_x[f_idx]
                max_y = fig_max_y[f_idx]
                
                for action in range(TOTAL_CELLS):
                    x = action >> 2
                    y = action & 0b11
                    
                    if x > max_x or y > max_y:
                        continue
                    
                    shift = x * N + y
                    shifted = f_value >> shift
                    
                    if (board | shifted) != board:
                        continue
                    
                    new_board = board & (~shifted)
                    new_idx = new_board * n_figures + f_idx
                    
                    if dst < dsts[new_idx]:
                        dsts[new_idx] = dst
                        actions[new_idx] = (x << 2) | y
                    
                    if not in_stack[new_board]:
                        in_stack[new_board] = True
                        next_idx = height + f_size
                        pos = next_sizes[next_idx]
                        next_stacks[next_idx, pos] = new_board
                        next_sizes[next_idx] += 1


class Deterministic(Solver):
    """
    Deterministic optimal solver using dynamic programming.
    Precomputes the best action for every possible (board, figure) state.
    """
    
    CACHE_FILE = "solver_cache.npz"
    
    def __init__(self):
        total_states = 1 << TOTAL_CELLS
        self.dsts = np.full(total_states * TOTAL_FIGURES, INF, dtype=np.float32)
        self.actions = np.full(total_states * TOTAL_FIGURES, SKIP_ACTION, dtype=np.uint8)
        self.in_stack = np.zeros(total_states, dtype=np.bool_)
    
    def save_cache(self, filepath: str = None):
        """Save computed data to cache file."""
        if filepath is None:
            filepath = os.path.join(os.path.dirname(__file__), self.CACHE_FILE)
        np.savez_compressed(filepath, dsts=self.dsts, actions=self.actions)
        print(f"Saved cache to {filepath}")
    
    def load_cache(self, filepath: str = None) -> bool:
        """Load computed data from cache file."""
        if filepath is None:
            filepath = os.path.join(os.path.dirname(__file__), self.CACHE_FILE)
        if os.path.exists(filepath):
            try:
                data = np.load(filepath)
                self.dsts = data['dsts'].copy()
                self.actions = data['actions'].copy()
                data.close()
                print(f"Loaded cache from {filepath}")
                return True
            except Exception as e:
                print(f"Failed to load cache: {e}, regenerating...")
                try:
                    os.remove(filepath)
                except:
                    pass
        return False
    
    def run(self):
        """Run the DP algorithm to compute optimal actions."""
        if self.load_cache():
            return
        
        if HAS_NUMBA:
            self._run_numba()
        else:
            self._run_pure_python()
    
    def _run_numba(self):
        """Numba-accelerated version."""
        print("Computing optimal strategy with Numba acceleration...")
        print("First run compiles JIT - subsequent runs will be faster.")
        
        total_states = 1 << TOTAL_CELLS
        
        # Initialize terminal state
        term_base = TERMINAL_STATE * TOTAL_FIGURES
        self.dsts[term_base:term_base + TOTAL_FIGURES] = 0.0
        self.in_stack[TERMINAL_STATE] = True
        
        # Stacks for each height (pre-allocate max possible size)
        max_stack = total_states
        stacks = np.zeros((TOTAL_CELLS + 1, max_stack), dtype=np.uint32)
        stack_sizes = np.zeros(TOTAL_CELLS + 1, dtype=np.int32)
        
        stacks[0, 0] = TERMINAL_STATE
        stack_sizes[0] = 1
        
        for height in range(TOTAL_CELLS):
            size = stack_sizes[height]
            if size == 0:
                continue
            
            # Use serial for small stacks, parallel for large
            if size > 1000:
                _process_height_numba(
                    np.int32(height),
                    stacks[height], np.int32(size),
                    self.dsts, self.actions, self.in_stack,
                    _FIG_VALUES, _FIG_SIZES, _FIG_MAX_X, _FIG_MAX_Y,
                    _SUBSETS_NP, stacks, stack_sizes
                )
            else:
                _process_height_serial(
                    np.int32(height),
                    stacks[height], np.int32(size),
                    self.dsts, self.actions, self.in_stack,
                    _FIG_VALUES, _FIG_SIZES, _FIG_MAX_X, _FIG_MAX_Y,
                    _SUBSETS_NP, stacks, stack_sizes
                )
            
            print(f"  Height {height}/{TOTAL_CELLS}: processed {size} states")
        
        print("Strategy computation complete!")
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
        f_idx = next(i for i, f in enumerate(FIGURES) if f.value == game.get_figure().value)
        return int(self.actions[game.board * TOTAL_FIGURES + f_idx])
    
    def distances(self, board: int) -> Iterator[Tuple[int, float]]:
        """Get (action, distance) pairs for a board state."""
        base = board * TOTAL_FIGURES
        for i in range(TOTAL_FIGURES):
            yield (int(self.actions[base + i]), float(self.dsts[base + i]))


_cached_solver: Deterministic = None


def get_solver() -> Deterministic:
    """Get or create the cached deterministic solver."""
    global _cached_solver
    if _cached_solver is None:
        _cached_solver = Deterministic()
        _cached_solver.run()
    return _cached_solver


# Singleton instance for caching the computed strategy
_cached_solver: Deterministic = None


def get_solver() -> Deterministic:
    """Get or create the cached deterministic solver."""
    global _cached_solver
    if _cached_solver is None:
        _cached_solver = Deterministic()
        _cached_solver.run()
    return _cached_solver

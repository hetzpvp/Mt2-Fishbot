"""
Fishing Jigsaw Solver - Single File Version

A solver for the fishing jigsaw puzzle game using dynamic programming
to find optimal moves. Includes GUI application.

Usage:
    python -m jigsaw_solver.fishing_jigsaw_solver          # Launch GUI application
    python -m jigsaw_solver.fishing_jigsaw_solver --cli    # Use command-line interface
"""
import os
import sys
import random
import tkinter as tk
from tkinter import ttk
from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import combinations
from typing import List, Tuple, Optional, Iterator

import numpy as np

try:
    from numba import njit, prange
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    print("Warning: numba not installed. Install with 'pip install numba' for 10-50x speedup.")


# =============================================================================
# JIGSAW GAME STATE AND LOGIC
# =============================================================================

# Board dimensions
N: int = 4  # rows
M: int = 6  # columns
SKIP_ACTION: int = N * M  # 24
TOTAL_FIGURES: int = 6
TOTAL_CELLS: int = N * M  # 24
TOTAL_ACTIONS: int = TOTAL_CELLS + 1  # 25
TERMINAL_STATE: int = (1 << (N * M)) - 1  # All cells filled
INIT_STATE: int = 0


@dataclass
class Figure:
    """Represents a tetris-like piece that can be placed on the board."""
    value: int      # Bitmask representation
    size: int       # Number of cells the figure occupies
    max_offset: Tuple[int, int]  # Maximum (x, y) offset for placement


# All available figures (pieces)
FIGURES: List[Figure] = [
    Figure(
        value=0b1000_0000_0000_0000_0000_0000,  # Single cell
        size=1,
        max_offset=(5, 3),
    ),
    Figure(
        value=0b1110_0000_0000_0000_0000_0000,  # Horizontal line of 3
        size=3,
        max_offset=(5, 1),
    ),
    Figure(
        value=0b1100_0100_0000_0000_0000_0000,  # L-shape
        size=3,
        max_offset=(4, 2),
    ),
    Figure(
        value=0b1000_1100_0000_0000_0000_0000,  # Reverse L-shape
        size=3,
        max_offset=(4, 2),
    ),
    Figure(
        value=0b1100_1100_0000_0000_0000_0000,  # 2x2 square
        size=4,
        max_offset=(4, 2),
    ),
    Figure(
        value=0b1000_1100_0100_0000_0000_0000,  # S-shape
        size=4,
        max_offset=(3, 2),
    ),
]


class Jigsaw:
    """
    Represents the state of a jigsaw puzzle game.
    The board is a 4x6 grid represented as a 24-bit integer.
    """
    
    def __init__(self, board: int = INIT_STATE, figure: int = 0, round_num: int = 0):
        self.board = board
        self.figure = figure
        self.round = round_num
    
    def clone(self) -> 'Jigsaw':
        """Create a copy of this game state."""
        return Jigsaw(self.board, self.figure, self.round)
    
    def set_random_figure(self, rng: random.Random = None):
        """Set a random figure for the next move."""
        if rng is None:
            rng = random.Random()
        self.figure = rng.randint(0, len(FIGURES) - 1)
    
    def has_finished(self) -> bool:
        """Check if the board is completely filled."""
        return self.board == TERMINAL_STATE
    
    def perform_action(self, action: int):
        """
        Perform an action on the board.
        Action is either a placement position (0-23) or SKIP_ACTION (24).
        """
        if action != SKIP_ACTION:
            assert self.is_legal(action), f"Illegal action: {action}"
            self.board |= self.get_figure().value >> action
        self.round += 1
    
    def get_figure(self) -> Figure:
        """Get the current figure."""
        return FIGURES[self.figure]
    
    @staticmethod
    def _mask(offsets: Tuple[int, int]) -> int:
        """Get the bitmask for a specific cell position."""
        return (1 << (TOTAL_CELLS - 1)) >> Jigsaw.offset_to_action(offsets)
    
    def get_value(self, offsets: Tuple[int, int]) -> bool:
        """Check if a cell at the given offsets is filled."""
        mask = self._mask(offsets)
        return (self.board & mask) != 0
    
    def toggle(self, offsets: Tuple[int, int]):
        """Toggle the state of a cell at the given offsets."""
        mask = self._mask(offsets)
        self.board ^= mask
    
    def is_legal(self, action: int) -> bool:
        """Check if an action is legal in the current state."""
        if action == SKIP_ACTION:
            return True
        
        x_offset, y_offset = Jigsaw.action_to_offsets(action)
        assert x_offset < M and y_offset < N
        
        figure = self.get_figure()
        
        # Check bounds and overlap
        illegal = False
        illegal |= x_offset > figure.max_offset[0]
        illegal |= y_offset > figure.max_offset[1]
        illegal |= (self.board & (figure.value >> (x_offset * N + y_offset))) != 0
        
        return not illegal
    
    def legal_actions(self) -> List[int]:
        """Get all legal actions for the current state."""
        return [a for a in range(TOTAL_ACTIONS) if self.is_legal(a)]
    
    @staticmethod
    def action_to_offsets(action: int) -> Tuple[int, int]:
        """
        Convert an action to (x_offset, y_offset).
        Action is encoded as 0bXXXYY where XXX is x-offset and YY is y-offset.
        """
        x_offset = action >> 2
        y_offset = action & 0b11
        return (x_offset, y_offset)
    
    @staticmethod
    def offset_to_action(offsets: Tuple[int, int]) -> int:
        """Convert (x_offset, y_offset) to an action."""
        return offsets[0] * N + offsets[1]
    
    def fig_intersect(self, action: int, offsets: Tuple[int, int]) -> bool:
        """Check if placing the figure at action would intersect with the given cell."""
        f = self.get_figure().value >> action
        m = self._mask(offsets)
        return (f & m) != 0
    
    def __repr__(self) -> str:
        """Debug representation of the game state."""
        lines = [f"<--------Round {self.round}-------->"]
        
        for i in range(N):
            row = ""
            mask = 1 << (N * M - (i + 1))
            for j in range(M):
                action = i + j * N
                value = (self.board & mask) != 0
                
                if value:
                    row += "🟥"
                elif self.is_legal(action):
                    row += "🟩"
                else:
                    row += "🟨"
                mask >>= N
            
            # Add figure visualization
            figure_row = ""
            mask = 1 << (N * M - (i + 1))
            for _ in range(M):
                value = (self.get_figure().value & mask) != 0
                figure_row += "🔳" if value else "  "
                mask >>= N
            
            lines.append(row + figure_row)
        
        return "\n".join(lines)


def is_possible(board: int, figure: Figure, x_offset: int, y_offset: int) -> bool:
    """
    Check if placing a figure at the given offset is possible.
    Used by the solver during the DP computation.
    """
    illegal = False
    illegal |= x_offset > figure.max_offset[0]
    illegal |= y_offset > figure.max_offset[1]
    
    shifted_figure = figure.value >> (x_offset * N + y_offset)
    illegal |= (board | shifted_figure) != board
    
    return not illegal


# =============================================================================
# SOLVER INTERFACE
# =============================================================================

class Solver(ABC):
    """Abstract base class for jigsaw solvers."""
    
    @abstractmethod
    def solve(self, game: Jigsaw) -> int:
        """
        Given a game state, return the best action to take.
        
        Args:
            game: The current game state
            
        Returns:
            The action to take (0-23 for placement, 24 for skip)
        """
        pass


# =============================================================================
# DETERMINISTIC SOLVER (Dynamic Programming)
# =============================================================================

# Pre-compute figure data as numpy arrays
_FIG_VALUES = np.array([f.value for f in FIGURES], dtype=np.uint32)
_FIG_SIZES = np.array([f.size for f in FIGURES], dtype=np.uint8)
_FIG_MAX_X = np.array([f.max_offset[0] for f in FIGURES], dtype=np.uint8)
_FIG_MAX_Y = np.array([f.max_offset[1] for f in FIGURES], dtype=np.uint8)

# Pre-compute all subsets as a 2D array (63 subsets, padded to max length 6)
# Each row: [length, idx0, idx1, idx2, idx3, idx4, idx5]
_SUBSETS_ARR = []
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
        
        self._run_simple()
    
    def _run_simple(self):
        """Simple pure-python implementation."""
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


# Singleton instance for caching the computed strategy
_cached_solver: Deterministic = None


def get_solver() -> Deterministic:
    """Get or create the cached deterministic solver."""
    global _cached_solver
    if _cached_solver is None:
        _cached_solver = Deterministic()
        _cached_solver.run()
    return _cached_solver


# =============================================================================
# GUI APPLICATION
# =============================================================================

class Distribution:
    """Computes score distribution through simulation."""
    
    def __init__(self, test_size: int = 4096, seed: int = 2024):
        self.data: List[int] = []
        self.test_size = test_size
        self.seed = seed
        self.state = Jigsaw()
    
    def set_state(self, state: Jigsaw):
        """Set the starting state for simulation."""
        self.state = state.clone()
    
    def compute(self, solver: Deterministic):
        """Run simulations to compute the score distribution."""
        rng = random.Random(self.seed)
        self.data = [0] * 30
        
        for _ in range(self.test_size):
            game = self.state.clone()
            
            while not game.has_finished():
                action = solver.solve(game)
                game.perform_action(action)
                game.set_random_figure(rng)
            
            r = game.round
            while r >= len(self.data):
                self.data.append(0)
            self.data[r] += 1
    
    def get_probabilities(self) -> tuple:
        """Get probabilities for different score ranges."""
        if not self.data:
            return (0.0, 0.0, 0.0)
        
        rl = sum(self.data[0:11])  # Rounds 0-10
        rm = sum(self.data[11:25])  # Rounds 11-24
        rs = sum(self.data[25:])   # Rounds 25+
        
        n = self.test_size
        return (rl / n, rm / n, rs / n)


class JigsawApp:
    """Main application window."""
    
    CELL_SIZE = 35
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Fishing Jigsaw Solver")
        self.root.resizable(False, False)
        
        # Initialize state
        self.state = Jigsaw()
        self.solver: Optional[Deterministic] = None
        self.distribution = Distribution(test_size=1024)  # Reduced for faster updates
        
        # Setup UI
        self._setup_ui()
        
        # Load solver in background
        self.root.after(100, self._load_solver)
    
    def _setup_ui(self):
        """Setup the user interface."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        
        # Instructions
        instructions = ttk.Label(
            main_frame, 
            text="Configure your current game state.\n"
                 "Click cells to toggle them. Green shows the best move.",
            wraplength=350
        )
        instructions.grid(row=0, column=0, columnspan=2, pady=(0, 10))
        
        # Board and figure frame
        board_frame = ttk.Frame(main_frame)
        board_frame.grid(row=1, column=0, columnspan=2, pady=10)
        
        # Board canvas
        self.board_canvas = tk.Canvas(
            board_frame,
            width=M * self.CELL_SIZE,
            height=N * self.CELL_SIZE,
            bg='gray20'
        )
        self.board_canvas.grid(row=0, column=0, padx=(0, 20))
        self.board_canvas.bind('<Button-1>', self._on_board_click)
        
        # Figure canvas
        self.figure_canvas = tk.Canvas(
            board_frame,
            width=3 * self.CELL_SIZE,
            height=3 * self.CELL_SIZE,
            bg='gray20'
        )
        self.figure_canvas.grid(row=0, column=1)
        
        # Status label
        self.status_label = ttk.Label(main_frame, text="Loading solver...")
        self.status_label.grid(row=2, column=0, columnspan=2, pady=5)
        
        # Round slider
        ttk.Label(main_frame, text="Round:").grid(row=3, column=0, sticky='w')
        self.round_var = tk.IntVar(value=0)
        self.round_slider = ttk.Scale(
            main_frame, from_=0, to=30, 
            variable=self.round_var, 
            orient='horizontal',
            command=self._on_round_change
        )
        self.round_slider.grid(row=3, column=1, sticky='ew', padx=5)
        
        # Figure slider
        ttk.Label(main_frame, text="Figure:").grid(row=4, column=0, sticky='w')
        self.figure_var = tk.IntVar(value=0)
        self.figure_slider = ttk.Scale(
            main_frame, from_=0, to=len(FIGURES)-1,
            variable=self.figure_var,
            orient='horizontal',
            command=self._on_figure_change
        )
        self.figure_slider.grid(row=4, column=1, sticky='ew', padx=5)
        
        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=10)
        
        self.take_btn = ttk.Button(btn_frame, text="Take Best Move", command=self._take_move)
        self.take_btn.grid(row=0, column=0, padx=5)
        
        self.reset_btn = ttk.Button(btn_frame, text="Reset", command=self._reset)
        self.reset_btn.grid(row=0, column=1, padx=5)
        
        # Distribution info
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=6, column=0, columnspan=2, sticky='ew', pady=10
        )
        
        dist_label = ttk.Label(
            main_frame,
            text="Score distribution from current state:",
            wraplength=350
        )
        dist_label.grid(row=7, column=0, columnspan=2)
        
        self.prob_label = ttk.Label(
            main_frame, 
            text="P(X<11): -  |  P(11≤X<25): -  |  P(X≥25): -"
        )
        self.prob_label.grid(row=8, column=0, columnspan=2, pady=5)
        
        # Distribution canvas
        self.dist_canvas = tk.Canvas(
            main_frame,
            width=350,
            height=80,
            bg='gray20'
        )
        self.dist_canvas.grid(row=9, column=0, columnspan=2, pady=5)
        
        # Best action info
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=10, column=0, columnspan=2, sticky='ew', pady=10
        )
        
        self.action_label = ttk.Label(
            main_frame,
            text="Best action: Loading...",
            font=('TkDefaultFont', 10, 'bold')
        )
        self.action_label.grid(row=11, column=0, columnspan=2)
        
        # Expected distances
        self.dist_info_label = ttk.Label(
            main_frame,
            text="Expected rounds to finish:\n(for each possible action)",
            wraplength=350
        )
        self.dist_info_label.grid(row=12, column=0, columnspan=2, pady=5)
        
        # Credits
        credits = ttk.Label(
            main_frame,
            text="Python port of fishing-jigsaw by @aguunu",
            font=('TkDefaultFont', 8)
        )
        credits.grid(row=13, column=0, columnspan=2, pady=(20, 0))
    
    def _load_solver(self):
        """Load the solver (this may take a while)."""
        self.solver = get_solver()
        self.status_label.config(text="Solver ready!")
        self._update_display()
        self._update_distribution()
    
    def _on_board_click(self, event):
        """Handle click on the board canvas."""
        x = event.x // self.CELL_SIZE
        y = event.y // self.CELL_SIZE
        
        if 0 <= x < M and 0 <= y < N:
            self.state.toggle((x, y))
            self._update_display()
            self._update_distribution()
    
    def _on_round_change(self, _):
        """Handle round slider change."""
        self.state.round = int(self.round_var.get())
        self._update_display()
    
    def _on_figure_change(self, _):
        """Handle figure slider change."""
        self.state.figure = int(self.figure_var.get())
        self._update_display()
        self._update_distribution()
    
    def _take_move(self):
        """Execute the best move."""
        if self.solver is None:
            return
        
        if self.state.has_finished():
            self._reset()
            return
        
        best_action = self.solver.solve(self.state)
        self.state.perform_action(best_action)
        self.state.set_random_figure()
        
        self.round_var.set(self.state.round)
        self.figure_var.set(self.state.figure)
        
        self._update_display()
        self._update_distribution()
    
    def _reset(self):
        """Reset the game state."""
        self.state = Jigsaw()
        self.round_var.set(0)
        self.figure_var.set(0)
        self._update_display()
        self._update_distribution()
    
    def _update_display(self):
        """Update the board and figure display."""
        self._draw_board()
        self._draw_figure()
        self._update_action_info()
    
    def _draw_board(self):
        """Draw the game board."""
        self.board_canvas.delete('all')
        
        best_action = SKIP_ACTION
        if self.solver is not None:
            best_action = self.solver.solve(self.state)
        
        for y in range(N):
            for x in range(M):
                x1 = x * self.CELL_SIZE
                y1 = y * self.CELL_SIZE
                x2 = x1 + self.CELL_SIZE
                y2 = y1 + self.CELL_SIZE
                
                offsets = (x, y)
                
                # Determine cell color
                if self.state.get_value(offsets):
                    color = 'gold'
                elif best_action != SKIP_ACTION and self.state.fig_intersect(best_action, offsets):
                    color = 'green'
                else:
                    color = 'gray40'
                
                self.board_canvas.create_rectangle(
                    x1, y1, x2, y2,
                    fill=color,
                    outline='white'
                )
    
    def _draw_figure(self):
        """Draw the current figure."""
        self.figure_canvas.delete('all')
        
        figure = self.state.get_figure()
        
        for i in range(3):
            mask = 1 << (N * M - (i + 1))
            for j in range(3):
                x1 = j * self.CELL_SIZE
                y1 = i * self.CELL_SIZE
                x2 = x1 + self.CELL_SIZE
                y2 = y1 + self.CELL_SIZE
                
                value = (figure.value & mask) != 0
                color = 'red' if value else 'gray30'
                
                self.figure_canvas.create_rectangle(
                    x1, y1, x2, y2,
                    fill=color,
                    outline='white'
                )
                mask >>= N
    
    def _update_action_info(self):
        """Update the action information labels."""
        if self.solver is None:
            return
        
        best_action = self.solver.solve(self.state)
        
        if best_action == SKIP_ACTION:
            action_text = "Best action: SKIP (don't place)"
        else:
            x, y = Jigsaw.action_to_offsets(best_action)
            action_text = f"Best action: Place at ({x}, {y}) [action={best_action}]"
        
        self.action_label.config(text=action_text)
        
        # Show expected distances for legal actions
        legal = self.state.legal_actions()
        dist_lines = []
        for action in legal[:8]:  # Show first 8 to avoid clutter
            temp_state = self.state.clone()
            temp_state.perform_action(action)
            
            distances = list(self.solver.distances(temp_state.board))
            avg = sum(d for _, d in distances) / len(distances)
            
            if action == SKIP_ACTION:
                dist_lines.append(f"SKIP: {avg:.2f}")
            else:
                x, y = Jigsaw.action_to_offsets(action)
                dist_lines.append(f"({x},{y}): {avg:.2f}")
        
        self.dist_info_label.config(text="Avg rounds: " + " | ".join(dist_lines))
    
    def _update_distribution(self):
        """Update the distribution display."""
        if self.solver is None:
            return
        
        self.distribution.set_state(self.state)
        self.distribution.compute(self.solver)
        
        probs = self.distribution.get_probabilities()
        self.prob_label.config(
            text=f"P(X<11): {probs[0]:.2f}  |  P(11≤X<25): {probs[1]:.2f}  |  P(X≥25): {probs[2]:.2f}"
        )
        
        self._draw_distribution()
    
    def _draw_distribution(self):
        """Draw the distribution histogram."""
        self.dist_canvas.delete('all')
        
        if not self.distribution.data:
            return
        
        max_val = max(self.distribution.data) if self.distribution.data else 1
        if max_val == 0:
            max_val = 1
        
        width = 350
        height = 80
        bar_width = width / len(self.distribution.data)
        
        for i, count in enumerate(self.distribution.data):
            bar_height = (count / max_val) * (height - 10)
            x1 = i * bar_width
            y1 = height - bar_height
            x2 = x1 + bar_width - 1
            y2 = height
            
            # Color based on round number
            if i < 11:
                color = 'green'
            elif i < 25:
                color = 'yellow'
            else:
                color = 'red'
            
            self.dist_canvas.create_rectangle(
                x1, y1, x2, y2,
                fill=color,
                outline=''
            )
        
        # Draw threshold lines
        x10 = (10 / len(self.distribution.data)) * width
        x25 = (25 / len(self.distribution.data)) * width
        self.dist_canvas.create_line(x10, 0, x10, height, fill='white', dash=(2, 2))
        self.dist_canvas.create_line(x25, 0, x25, height, fill='white', dash=(2, 2))


# =============================================================================
# CLI MODE
# =============================================================================

def cli_mode():
    """Run in command-line interface mode."""
    print("=" * 50)
    print("Fishing Jigsaw Solver - CLI Mode")
    print("=" * 50)
    print()
    
    solver = get_solver()
    game = Jigsaw()
    game.set_random_figure()
    
    print("\nCommands:")
    print("  [enter] - Take best move")
    print("  'r'     - Reset game")
    print("  'q'     - Quit")
    print("  number  - Set figure (0-5)")
    print()
    
    while True:
        print(game)
        print()
        
        if game.has_finished():
            print(f"🎉 Game finished in {game.round} rounds!")
            print()
            game = Jigsaw()
            game.set_random_figure()
            continue
        
        best_action = solver.solve(game)
        if best_action == SKIP_ACTION:
            print(f"Best action: SKIP")
        else:
            x, y = Jigsaw.action_to_offsets(best_action)
            print(f"Best action: Place at ({x}, {y})")
        
        # Show legal actions
        legal = game.legal_actions()
        print(f"Legal actions: {legal}")
        
        try:
            cmd = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        
        if cmd == 'q':
            break
        elif cmd == 'r':
            game = Jigsaw()
            game.set_random_figure()
        elif cmd == '':
            game.perform_action(best_action)
            game.set_random_figure()
        elif cmd.isdigit() and 0 <= int(cmd) <= 5:
            game.figure = int(cmd)
        else:
            print("Unknown command")


def gui_mode():
    """Run in GUI mode."""
    root = tk.Tk()
    app = JigsawApp(root)
    root.mainloop()


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == '__main__':
    if '--cli' in sys.argv:
        cli_mode()
    else:
        gui_mode()

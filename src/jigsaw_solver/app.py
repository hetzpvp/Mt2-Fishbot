"""
GUI Application for the Fishing Jigsaw Solver.
Uses tkinter for the graphical interface.
"""
import tkinter as tk
from tkinter import ttk
from typing import List, Optional

import numpy as np

try:
    from .jigsaw import Jigsaw, FIGURES, N, M, SKIP_ACTION, TOTAL_FIGURES
    from .deterministic import Deterministic, get_solver
except ImportError:  # Allows running this module directly from src/jigsaw_solver.
    from jigsaw import Jigsaw, FIGURES, N, M, SKIP_ACTION, TOTAL_FIGURES  # type: ignore
    from deterministic import Deterministic, get_solver  # type: ignore


class Distribution:
    """Computes score distribution through simulation (numba-accelerated)."""

    MAX_ROUND = 60

    def __init__(self, test_size: int = 4096, seed: int = 2024):
        self.data: np.ndarray = np.zeros(self.MAX_ROUND + 1, dtype=np.int64)
        self.test_size = test_size
        self.seed = seed
        self.state = Jigsaw()

    def set_state(self, state: Jigsaw):
        """Set the starting state for simulation."""
        self.state = state.clone()

    def compute(self, solver: Deterministic):
        """Run simulations to compute the score distribution."""
        if self.state.has_finished():
            self.data = np.zeros(self.MAX_ROUND + 1, dtype=np.int64)
            self.data[0] = self.test_size
            return
        self.data = solver.simulate_distribution(
            self.state.board, self.state.figure,
            self.test_size, self.seed, self.MAX_ROUND,
        )

    def get_probabilities(self) -> tuple:
        """Get probabilities for different score ranges."""
        if self.data is None or self.data.sum() == 0:
            return (0.0, 0.0, 0.0)

        rl = int(self.data[0:11].sum())   # Rounds 0-10
        rm = int(self.data[11:25].sum())  # Rounds 11-24
        rs = int(self.data[25:].sum())    # Rounds 25+

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
        self.distribution = Distribution(test_size=1024)

        # Canvas item caches (created once, updated via itemconfig)
        self._board_rects: dict = {}    # (x, y) -> rect id
        self._figure_rects: dict = {}   # (i, j) -> rect id
        self._dist_rects: list = []     # one rect per histogram bin
        self._dist_lines: list = []     # threshold marker line ids

        # Setup UI
        self._setup_ui()

        # Load solver in background
        self.root.after(100, self._load_solver)

    def _setup_ui(self):
        """Setup the user interface."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")

        instructions = ttk.Label(
            main_frame,
            text="Configure your current game state.\n"
                 "Click cells to toggle them. Green shows the best move.",
            wraplength=350
        )
        instructions.grid(row=0, column=0, columnspan=2, pady=(0, 10))

        board_frame = ttk.Frame(main_frame)
        board_frame.grid(row=1, column=0, columnspan=2, pady=10)

        self.board_canvas = tk.Canvas(
            board_frame,
            width=M * self.CELL_SIZE,
            height=N * self.CELL_SIZE,
            bg='gray20', highlightthickness=0,
        )
        self.board_canvas.grid(row=0, column=0, padx=(0, 20))
        self.board_canvas.bind('<Button-1>', self._on_board_click)

        self.figure_canvas = tk.Canvas(
            board_frame,
            width=3 * self.CELL_SIZE,
            height=3 * self.CELL_SIZE,
            bg='gray20', highlightthickness=0,
        )
        self.figure_canvas.grid(row=0, column=1)

        # Pre-create board rectangles
        for y in range(N):
            for x in range(M):
                x1 = x * self.CELL_SIZE
                y1 = y * self.CELL_SIZE
                rid = self.board_canvas.create_rectangle(
                    x1, y1, x1 + self.CELL_SIZE, y1 + self.CELL_SIZE,
                    fill='gray40', outline='white',
                )
                self._board_rects[(x, y)] = rid

        # Pre-create figure rectangles
        for i in range(3):
            for j in range(3):
                x1 = j * self.CELL_SIZE
                y1 = i * self.CELL_SIZE
                rid = self.figure_canvas.create_rectangle(
                    x1, y1, x1 + self.CELL_SIZE, y1 + self.CELL_SIZE,
                    fill='gray30', outline='white',
                )
                self._figure_rects[(i, j)] = rid

        self.status_label = ttk.Label(main_frame, text="Loading solver...")
        self.status_label.grid(row=2, column=0, columnspan=2, pady=5)

        ttk.Label(main_frame, text="Round:").grid(row=3, column=0, sticky='w')
        self.round_var = tk.IntVar(value=0)
        self.round_slider = ttk.Scale(
            main_frame, from_=0, to=30,
            variable=self.round_var,
            orient='horizontal',
            command=self._on_round_change
        )
        self.round_slider.grid(row=3, column=1, sticky='ew', padx=5)

        ttk.Label(main_frame, text="Figure:").grid(row=4, column=0, sticky='w')
        self.figure_var = tk.IntVar(value=0)
        self.figure_slider = ttk.Scale(
            main_frame, from_=0, to=len(FIGURES) - 1,
            variable=self.figure_var,
            orient='horizontal',
            command=self._on_figure_change
        )
        self.figure_slider.grid(row=4, column=1, sticky='ew', padx=5)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=10)

        self.take_btn = ttk.Button(btn_frame, text="Take Best Move", command=self._take_move)
        self.take_btn.grid(row=0, column=0, padx=5)

        self.reset_btn = ttk.Button(btn_frame, text="Reset", command=self._reset)
        self.reset_btn.grid(row=0, column=1, padx=5)

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

        self.dist_canvas = tk.Canvas(
            main_frame, width=350, height=80, bg='gray20', highlightthickness=0,
        )
        self.dist_canvas.grid(row=9, column=0, columnspan=2, pady=5)

        # Pre-create distribution histogram bars
        n_bins = self.distribution.MAX_ROUND + 1
        bar_w = 350.0 / n_bins
        for i in range(n_bins):
            x1 = i * bar_w
            color = 'green' if i < 11 else ('yellow' if i < 25 else 'red')
            rid = self.dist_canvas.create_rectangle(
                x1, 80, x1 + bar_w - 1, 80, fill=color, outline=''
            )
            self._dist_rects.append(rid)
        # Threshold lines
        x10 = (10 / n_bins) * 350
        x25 = (25 / n_bins) * 350
        self._dist_lines.append(
            self.dist_canvas.create_line(x10, 0, x10, 80, fill='white', dash=(2, 2))
        )
        self._dist_lines.append(
            self.dist_canvas.create_line(x25, 0, x25, 80, fill='white', dash=(2, 2))
        )

        ttk.Separator(main_frame, orient='horizontal').grid(
            row=10, column=0, columnspan=2, sticky='ew', pady=10
        )

        self.action_label = ttk.Label(
            main_frame,
            text="Best action: Loading...",
            font=('TkDefaultFont', 10, 'bold')
        )
        self.action_label.grid(row=11, column=0, columnspan=2)

        self.dist_info_label = ttk.Label(
            main_frame,
            text="Expected rounds to finish:\n(for each possible action)",
            wraplength=350
        )
        self.dist_info_label.grid(row=12, column=0, columnspan=2, pady=5)

        credits = ttk.Label(
            main_frame,
            text="Python port of fishing-jigsaw by @aguunu",
            font=('TkDefaultFont', 8)
        )
        credits.grid(row=13, column=0, columnspan=2, pady=(20, 0))

    def _load_solver(self):
        self.solver = get_solver()
        self.status_label.config(text="Solver ready!")
        self._update_display()
        self._update_distribution()

    def _on_board_click(self, event):
        x = event.x // self.CELL_SIZE
        y = event.y // self.CELL_SIZE

        if 0 <= x < M and 0 <= y < N:
            self.state.toggle((x, y))
            self._update_display()
            self._update_distribution()

    def _on_round_change(self, _):
        self.state.round = int(self.round_var.get())
        # No display recomputation needed — round number doesn't affect colors

    def _on_figure_change(self, _):
        self.state.figure = int(self.figure_var.get())
        self._update_display()
        self._update_distribution()

    def _take_move(self):
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
        self.state = Jigsaw()
        self.round_var.set(0)
        self.figure_var.set(0)
        self._update_display()
        self._update_distribution()

    def _update_display(self):
        # Compute best action ONCE, share it across draw + info update
        best_action = SKIP_ACTION
        if self.solver is not None:
            best_action = self.solver.solve(self.state)
        self._draw_board(best_action)
        self._draw_figure()
        self._update_action_info(best_action)

    def _draw_board(self, best_action: int):
        state = self.state
        cfg = self.board_canvas.itemconfig
        rects = self._board_rects
        get_value = state.get_value
        fig_intersect = state.fig_intersect

        for y in range(N):
            for x in range(M):
                offsets = (x, y)
                if get_value(offsets):
                    color = 'gold'
                elif best_action != SKIP_ACTION and fig_intersect(best_action, offsets):
                    color = 'green'
                else:
                    color = 'gray40'
                cfg(rects[(x, y)], fill=color)

    def _draw_figure(self):
        figure_value = self.state.get_figure().value
        cfg = self.figure_canvas.itemconfig
        rects = self._figure_rects

        for i in range(3):
            base_mask = 1 << (N * M - (i + 1))
            mask = base_mask
            for j in range(3):
                value = (figure_value & mask) != 0
                cfg(rects[(i, j)], fill=('red' if value else 'gray30'))
                mask >>= N

    def _update_action_info(self, best_action: int):
        if self.solver is None:
            return

        if best_action == SKIP_ACTION:
            action_text = "Best action: SKIP (don't place)"
        else:
            x, y = Jigsaw.action_to_offsets(best_action)
            action_text = f"Best action: Place at ({x}, {y}) [action={best_action}]"

        self.action_label.config(text=action_text)

        # Show expected distances for legal actions — compute new boards
        # directly via bitwise ops (no clone()).
        try:
            from .jigsaw import _FIG_SHIFTED_PER_ACTION
        except ImportError:
            from jigsaw import _FIG_SHIFTED_PER_ACTION  # type: ignore
        figure_idx = self.state.figure
        shifted_per = _FIG_SHIFTED_PER_ACTION[figure_idx]
        board = self.state.board

        legal = self.state.legal_actions()
        dist_lines = []
        for action in legal[:8]:
            if action == SKIP_ACTION:
                new_board = board
            else:
                new_board = board | shifted_per[action]
            avg = self.solver.avg_distance(new_board)

            if action == SKIP_ACTION:
                dist_lines.append(f"SKIP: {avg:.2f}")
            else:
                x, y = Jigsaw.action_to_offsets(action)
                dist_lines.append(f"({x},{y}): {avg:.2f}")

        self.dist_info_label.config(text="Avg rounds: " + " | ".join(dist_lines))

    def _update_distribution(self):
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
        data = self.distribution.data
        if data is None or data.size == 0:
            return

        max_val = int(data.max()) or 1

        width = 350
        height = 80
        n_bins = data.size
        bar_w = width / n_bins
        coords = self.dist_canvas.coords

        for i in range(n_bins):
            count = int(data[i])
            bar_h = (count / max_val) * (height - 10)
            x1 = i * bar_w
            coords(self._dist_rects[i], x1, height - bar_h, x1 + bar_w - 1, height)


def main():
    """Main entry point."""
    root = tk.Tk()
    app = JigsawApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()

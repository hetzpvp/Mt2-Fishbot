"""
GUI Application for the Fishing Jigsaw Solver.
Uses tkinter for the graphical interface.
"""
import tkinter as tk
from tkinter import ttk
import random
from typing import List, Optional

from jigsaw import Jigsaw, FIGURES, N, M, SKIP_ACTION, TOTAL_FIGURES
from deterministic import Deterministic, get_solver


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


def main():
    """Main entry point."""
    root = tk.Tk()
    app = JigsawApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()

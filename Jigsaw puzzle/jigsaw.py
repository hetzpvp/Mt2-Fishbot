"""
Jigsaw puzzle game state and logic.
Represents a 4x6 board where pieces must be placed.
"""
import random
from dataclasses import dataclass
from typing import List, Tuple

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

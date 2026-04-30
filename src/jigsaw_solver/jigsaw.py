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


# ---- Precomputed lookup tables (module-level for fast access) ----------------

# Mask for each (x, y) cell
_CELL_MASKS: Tuple[Tuple[int, ...], ...] = tuple(
    tuple((1 << (TOTAL_CELLS - 1)) >> (x * N + y) for y in range(N))
    for x in range(M)
)

# For each figure: shifted figure mask per action (0..23) and a bool array
# marking actions that are within the figure's max_offset bounds.
# Note: action encoding (x<<2)|y equals the shift amount (x*N + y) when N=4,
# so `figure.value >> action` is identical to `figure.value >> (x*N + y)`.
_FIG_SHIFTED_PER_ACTION: Tuple[Tuple[int, ...], ...] = tuple(
    tuple(
        (f.value >> a) if ((a >> 2) <= f.max_offset[0] and (a & 0b11) <= f.max_offset[1]) else 0
        for a in range(TOTAL_CELLS)
    )
    for f in FIGURES
)

_FIG_ACTION_VALID: Tuple[Tuple[bool, ...], ...] = tuple(
    tuple(
        ((a >> 2) <= f.max_offset[0] and (a & 0b11) <= f.max_offset[1])
        for a in range(TOTAL_CELLS)
    )
    for f in FIGURES
)

# For each figure: tuple of in-bounds action indices (skips bounds check at runtime)
_FIG_INBOUND_ACTIONS: Tuple[Tuple[int, ...], ...] = tuple(
    tuple(a for a in range(TOTAL_CELLS) if _FIG_ACTION_VALID[i][a])
    for i in range(len(FIGURES))
)


class Jigsaw:
    """
    Represents the state of a jigsaw puzzle game.
    The board is a 4x6 grid represented as a 24-bit integer.
    """

    __slots__ = ("board", "figure", "round")

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
            self.figure = random.randint(0, len(FIGURES) - 1)
        else:
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
            shifted = _FIG_SHIFTED_PER_ACTION[self.figure][action]
            # Quick legality assertion (in-bounds AND no overlap)
            assert _FIG_ACTION_VALID[self.figure][action] and not (self.board & shifted), \
                f"Illegal action: {action}"
            self.board |= shifted
        self.round += 1

    def get_figure(self) -> Figure:
        """Get the current figure."""
        return FIGURES[self.figure]

    @staticmethod
    def _mask(offsets: Tuple[int, int]) -> int:
        """Get the bitmask for a specific cell position."""
        return _CELL_MASKS[offsets[0]][offsets[1]]

    def get_value(self, offsets: Tuple[int, int]) -> bool:
        """Check if a cell at the given offsets is filled."""
        return (self.board & _CELL_MASKS[offsets[0]][offsets[1]]) != 0

    def toggle(self, offsets: Tuple[int, int]):
        """Toggle the state of a cell at the given offsets."""
        self.board ^= _CELL_MASKS[offsets[0]][offsets[1]]

    def is_legal(self, action: int) -> bool:
        """Check if an action is legal in the current state."""
        if action == SKIP_ACTION:
            return True
        f = self.figure
        if not _FIG_ACTION_VALID[f][action]:
            return False
        return (self.board & _FIG_SHIFTED_PER_ACTION[f][action]) == 0

    def legal_actions(self) -> List[int]:
        """Get all legal actions for the current state."""
        board = self.board
        shifted = _FIG_SHIFTED_PER_ACTION[self.figure]
        result = [a for a in _FIG_INBOUND_ACTIONS[self.figure] if not (board & shifted[a])]
        result.append(SKIP_ACTION)
        return result

    @staticmethod
    def action_to_offsets(action: int) -> Tuple[int, int]:
        """
        Convert an action to (x_offset, y_offset).
        Action is encoded as 0bXXXYY where XXX is x-offset and YY is y-offset.
        """
        return (action >> 2, action & 0b11)

    @staticmethod
    def offset_to_action(offsets: Tuple[int, int]) -> int:
        """Convert (x_offset, y_offset) to an action."""
        return offsets[0] * N + offsets[1]

    def fig_intersect(self, action: int, offsets: Tuple[int, int]) -> bool:
        """Check if placing the figure at action would intersect with the given cell."""
        return (_FIG_SHIFTED_PER_ACTION[self.figure][action] & _CELL_MASKS[offsets[0]][offsets[1]]) != 0

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
    if x_offset > figure.max_offset[0] or y_offset > figure.max_offset[1]:
        return False
    shifted_figure = figure.value >> (x_offset * N + y_offset)
    return (board | shifted_figure) == board

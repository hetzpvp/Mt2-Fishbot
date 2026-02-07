"""
Solver interface for the jigsaw puzzle.
"""
from abc import ABC, abstractmethod
from jigsaw import Jigsaw


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

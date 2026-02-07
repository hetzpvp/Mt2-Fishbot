# Fishing Jigsaw Solver - Python Implementation

A Python reimplementation of the Rust fishing-jigsaw solver.

## Overview

This is a solver for a fishing jigsaw puzzle game. It uses dynamic programming to compute optimal moves for every possible game state.

## Files

- **jigsaw.py** - Core game logic (board state, figures, actions)
- **solver.py** - Abstract solver interface
- **deterministic.py** - Optimal DP solver implementation
- **app.py** - GUI application using tkinter
- **main.py** - Entry point

## Usage

### GUI Mode (default)
```bash
python main.py
```

### CLI Mode
```bash
python main.py --cli
```

## Game Rules

- The board is a 4x6 grid (24 cells)
- Each round, you receive a random piece (figure)
- You can either place the piece or skip
- Goal: Fill the entire board in as few rounds as possible
- Winning threshold: Complete in 10 rounds or less

## Pieces (Figures)

0. Single cell (1x1)
1. Horizontal line (3x1)
2. L-shape (2x2 with one corner missing)
3. Reverse L-shape
4. Square (2x2)
5. S-shape (zigzag)

## Algorithm

The solver uses backward dynamic programming:
1. Start from the terminal state (fully filled board)
2. Work backwards to compute the expected number of rounds needed from each state
3. For each state, compute the optimal action considering:
   - Direct placement of each figure type
   - Expected value of skipping and waiting for a better figure

## Credits

Python port of [fishing-jigsaw](https://github.com/aguunu/fishing-jigsaw) by @aguunu

"""
Fishing Jigsaw Solver - Python Implementation

A solver for the fishing jigsaw puzzle game using dynamic programming
to find optimal moves.

Usage:
    python main.py          # Launch GUI application
    python main.py --cli    # Use command-line interface
"""
import sys
import random

from jigsaw import Jigsaw, FIGURES, SKIP_ACTION
from deterministic import get_solver


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
    from app import main
    main()


if __name__ == '__main__':
    if '--cli' in sys.argv:
        cli_mode()
    else:
        gui_mode()

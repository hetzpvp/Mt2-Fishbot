"""
Fishing Jigsaw Solver - Python Implementation

A solver for the fishing jigsaw puzzle game using dynamic programming
to find optimal moves.

Usage:
    python -m jigsaw_solver.main          # Launch GUI application
    python -m jigsaw_solver.main --cli    # Use command-line interface
"""
import sys
import random

try:
    from .jigsaw import Jigsaw, FIGURES, SKIP_ACTION
    from .deterministic import get_solver
except ImportError:  # Allows running this module directly from src/jigsaw_solver.
    from jigsaw import Jigsaw, FIGURES, SKIP_ACTION  # type: ignore
    from deterministic import get_solver  # type: ignore


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
    try:
        from .app import main
    except ImportError:
        from app import main  # type: ignore
    main()


if __name__ == '__main__':
    if '--cli' in sys.argv:
        cli_mode()
    else:
        gui_mode()

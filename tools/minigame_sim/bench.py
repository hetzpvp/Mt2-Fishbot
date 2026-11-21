"""Headless benchmark: the real FishingBot loop against the simulated fishing
window, one run per skin. No window, no real mouse or keyboard — safe to run
while playing.

    python tools/minigame_sim/bench.py                 all skins, 6 games each
    python tools/minigame_sim/bench.py --skin water --games 20
    python tools/minigame_sim/bench.py --handling open   also test auto fish handling
    python tools/minigame_sim/bench.py --hard            every difficulty at once (see below)
    python tools/minigame_sim/bench.py --windows 4       four clients on one desktop
    python tools/minigame_sim/bench.py --realistic       the client as measured on a real
                                                         session (see REALISTIC), set up like
                                                         the player's bot; with --windows N,
                                                         clicks are lost to early focus switches

Difficulty: --wander (the panel opens somewhere else every game), --change-look
(another look every game), --lag (items land late), --hiccups (some captures
come back black), --popups (other game windows open while the line is out),
--bite-delay MIN MAX. --hard turns all of them on. With --windows the clients
share one cursor, one keyboard and one focus; the last line reports input that
reached the wrong client (must be 0).

Prints, per skin: games played, catches, hits, misses, clicks sent too early
(inside the game's click cooldown, so they did nothing), timeouts and whether
the look had to be learned.
"""

import argparse
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(ROOT, "tests"), os.path.join(ROOT, "tools"), os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from _stubs import install  # noqa: E402

install()

from minigame_sim.fishing_game import FishingGame, SKINS  # noqa: E402


from minigame_sim.desktop import FishingDesktop, FAST  # noqa: E402,F401
from fishing_bot import FishingBot  # noqa: E402


class GameWM:
    """Window manager of a window that is always there and always in front."""

    def __init__(self, game):
        self.game = game
        self.selected_window = object()
        self.last_switch_time = 0.0

    def is_window_available(self):
        return True

    def is_foreground(self):
        return True

    def activate_window(self, force_activate=False):
        return True

    def get_window_rect(self):
        return (0, 0, self.game.W, self.game.H)

    def invalidate_rect_cache(self):
        pass


class GameMouse:
    name = "sim"

    def __init__(self, game):
        self.game = game
        self.pos = (0, 0)

    def move_to(self, x, y):
        self.pos = (int(x), int(y))
        self.game.move(*self.pos)

    def position(self):
        return self.pos

    def mouse_down(self, button="left"):
        self.game.click(self.pos[0], self.pos[1], button)

    def mouse_up(self, button="left"):
        pass

    def click(self, button="left"):
        self.game.click(self.pos[0], self.pos[1], button)

    def close(self):
        pass


class GameKeyboard:
    def __init__(self, game):
        self.game = game

    def press(self, key):
        self.game.key(str(key))

    def release(self, key):
        self.game.key_up(str(key))


def make_bot(game, config=None):
    """A FishingBot whose eyes and hands are connected to ``game``."""
    FishingBot._shared_learned_profile = None
    FishingBot._shared_recast_wait = None
    FishingBot._known_profiles = []
    logs = []
    bot = FishingBot(None, dict(FAST, **(config or {})), GameWM(game), bait_counter=10 ** 6, bot_id=0)
    mouse, keyboard = GameMouse(game), GameKeyboard(game)
    bot.mouse_backend = mouse
    bot.human.mouse = mouse
    bot.keyboard_controller = keyboard
    bot.human.keyboard_controller = keyboard
    bot._grab = game.grab
    bot.on_status_update = logs.append
    return bot, logs


def run(game, config=None, games=6, timeout=120.0, until=None):
    """Plays until ``games`` minigames are over (or ``until()`` is true)."""
    bot, logs = make_bot(game, config)
    done = until or (lambda: game.stats["caught"] + game.stats["timeouts"] >= games)
    bot.running = True
    thread = threading.Thread(target=bot.play_game, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    while time.time() < deadline and thread.is_alive() and not done():
        time.sleep(0.02)
    bot.stop()
    thread.join(timeout=15)
    assert not thread.is_alive(), "the bot did not stop"
    return bot, logs


def quick_game(skin, **kw):
    """A game with short waits, for tests and benchmarks. The bite comes
    1.5-2 s after the cast, like on the real client (1.1-2.1 s, 2026-11-20):
    the bot opens and drops the last catch in that time."""
    opts = dict(bite_delay=(1.5, 2.0), recast_cooldown=0.2)
    opts.update(kw)
    return FishingGame(skin=skin, **opts)


# The real client as measured on diagnostics 20261120_195925 (two clients,
# 330 casts, 324 games, a recording of each window). See FishingGame.
REALISTIC = dict(
    skin="official-real",
    bite_steps=(1.0, 2.0, 3.0),      # 114 / 103 / 105 bites after 1 / 2 / 3 s
    fish_motion="diagonal",          # 45-degree lines, bounces, ~80 px/s
    frame_rate=60,                   # fish position changes every 16 ms
    click_cooldown=0.5,              # clicks 0.53-0.6 s apart once the ring is red again
    catch_chance=0.35,               # 112 items from 324 won games
    catch_delay=(0.1, 0.2),          # item seen 0.10-0.20 s after the panel closed
    living_world=True,
    prefill=17,                      # the player's page 1 at the start
    rod_animation=2.0,               # assumed: what the quick skip is for
    recast_cooldown=0.2,
    mount_time=(0.15, 0.2),          # Ctrl+G to horse ~170 ms (once 250 ms)
)


def realistic_game(**kw):
    """A FishingGame that behaves like the real client (``REALISTIC``)."""
    opts = dict(REALISTIC)
    opts.update(kw)
    return FishingGame(**opts)


def realistic_config(game, **kw):
    """The bot set up like the player's real one: quick skip on the horse,
    auto handling (open the zanders, drop some fish, fish with shrimp, keep
    the rest), only the tabs of pages I and II set, a 6 s bite timeout."""
    actions = {}
    for name in game._item_names:
        if name.startswith(("Zander", "Large_zander")):
            actions[name] = "open"
        elif name.startswith(("Carp", "Grasscarp", "Brookforell", "Rainbowforell", "Redfeather", "Barsch")):
            actions[name] = "drop"
        elif name.startswith("Shrimp"):
            actions[name] = "use"
        else:
            actions[name] = "keep"
    config = {
        "auto_fish_handling": True, "fish_actions": actions,
        "drop_button_pos": list(game.drop_button), "confirm_button_pos": list(game.confirm_button),
        "inv_page_1_pos": list(game.tabs[0]), "inv_page_2_pos": list(game.tabs[1]),
        "quick_skip": True, "quick_skip_mode": "horse",
        "timing_minigame_timeout": 6.0, "bait_keys": ["1"],
    }
    config.update(kw)
    return config


CHANGING_LOOKS = ["official", "emerald", "crimson", "official-redfish", "water", "sand-textured"]


def hard_options(args, skin):
    """FishingGame options for the difficulty flags."""
    opts = {}
    if args.wander or args.hard:
        opts["wander"] = True
    if args.change_look or args.hard:
        opts["skins"] = [skin] + [s for s in CHANGING_LOOKS if s != skin]
    if args.lag or args.hard:
        opts["catch_delay"] = (0.3, 0.8)
    if args.hiccups or args.hard:
        opts["blank_chance"] = 0.05
    if args.popups or args.hard:
        opts["popup_chance"] = 0.5
    if args.bite_delay:
        opts["bite_delay"] = tuple(args.bite_delay)
    return opts


def bot_config(args, game):
    config = {"adaptive_detection": not args.no_adaptive, "human_like_clicking": args.human}
    if args.handling:
        config.update(auto_fish_handling=True,
                      fish_actions={n: args.handling for n in game._item_names},
                      drop_button_pos=list(game.drop_button),
                      confirm_button_pos=list(game.confirm_button))
    if args.bite_delay:
        config["timing_minigame_timeout"] = max(3.0, args.bite_delay[1] + 1.0)
    return config


def report(name, game, bot, seconds, handling, logs=None):
    s = game.stats
    notes = []
    if bot.detector.profile.learned:
        notes.append("learned")
    if bot._panel_moves:
        notes.append(f"panel moved {bot._panel_moves}x")
    if handling:
        left = sum(len(p) for p in game.pages)
        notes.append(f"opened {s['opened']} dropped {s['dropped']} left {left}")
    for key, label in (("stray_clicks", "stray clicks"), ("casts_cancelled", "casts reeled in"),
                       ("casts_ignored", "casts ignored")):
        if s[key]:
            notes.append(f"{s[key]} {label}")
    print(f"{name:18} {s['games']:5} {s['caught']:6} {s['hits']:5} {s['misses']:5} "
          f"{s['clicks_ignored']:5} {s['timeouts']:5} {seconds / max(1, s['games']):6.1f}  {', '.join(notes)}")
    if logs:
        for line in logs:
            print("    ", line)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skin", action="append", choices=sorted(SKINS), help="default: all")
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-adaptive", action="store_true", help="turn self-calibration off")
    ap.add_argument("--human", action="store_true", help="human-like clicking")
    ap.add_argument("--handling", choices=("keep", "open", "drop"),
                    help="auto fish handling with this action for every item")
    ap.add_argument("--windows", type=int, default=1, help="clients sharing one desktop")
    ap.add_argument("--switch-delay", type=float, default=0.01, help="seconds a focus switch takes (--windows)")
    ap.add_argument("--refuse-focus", type=float, default=0.0, help="share of focus requests the OS ignores")
    ap.add_argument("--wander", action="store_true")
    ap.add_argument("--change-look", action="store_true")
    ap.add_argument("--lag", action="store_true")
    ap.add_argument("--hiccups", action="store_true")
    ap.add_argument("--popups", action="store_true")
    ap.add_argument("--bite-delay", type=float, nargs=2, metavar=("MIN", "MAX"))
    ap.add_argument("--hard", action="store_true", help="all difficulty flags")
    ap.add_argument("--realistic", action="store_true",
                    help="the real client's timing, fish, ring, inventory and quick skip")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the bot's log")
    args = ap.parse_args()

    print(f"{'skin':18} {'games':>5} {'caught':>6} {'hits':>5} {'miss':>5} {'early':>5} {'t/out':>5} "
          f"{'s/game':>6}  notes")
    worst = 0
    skins = args.skin or (["official"] if args.windows > 1 or args.hard or args.change_look else list(SKINS))

    if args.realistic:
        skins = ["official-real"]

    def new_game(skin, seed):
        if args.realistic:
            return realistic_game(seed=seed)
        return quick_game(skin, seed=seed, **hard_options(args, skin))

    def config_for(game):
        return realistic_config(game) if args.realistic else bot_config(args, game)

    def ended(game):
        return game.stats["games"] if args.realistic else game.stats["caught"] + game.stats["timeouts"]

    if args.windows > 1:
        desktop = FishingDesktop(switch_delay=args.switch_delay, refuse_focus=args.refuse_focus, seed=args.seed,
                                 click_latency=(0.015, 0.035) if args.realistic else None)
        games = []
        for i in range(args.windows):
            skin = skins[i % len(skins)]
            game = new_game(skin, args.seed + i)
            games.append(game)
            desktop.add(game, config_for(game))
        t0 = time.time()
        desktop.run(lambda: all(ended(g) >= args.games for g in games), timeout=60.0 * args.games)
        for i, (game, bot) in enumerate(zip(games, desktop.bots)):
            report(f"W{i + 1} {game.skin.name}"[:18], game, bot, time.time() - t0, args.handling or args.realistic)
            worst = max(worst, game.stats["games"] - game.stats["caught"] - game.stats["empty_catches"])
        wrong = desktop.misdirected_keys + desktop.misdirected_clicks
        print(f"focus switches {desktop.switches}, refused {desktop.refused}, "
              f"input that reached the wrong client: {wrong}, clicks lost to a focus switch: "
              f"{desktop.clicks_lost}")
        wrong += desktop.clicks_lost
        if args.verbose:
            for line in desktop.logs:
                print("    ", line)
        return 1 if worst or wrong else 0

    for name in skins:
        game = new_game(name, args.seed)
        t0 = time.time()
        bot, logs = run(game, config_for(game), timeout=40.0 * args.games,
                        until=lambda game=game: ended(game) >= args.games)
        report(name, game, bot, time.time() - t0, args.handling or args.realistic, logs if args.verbose else None)
        worst = max(worst, game.stats["games"] - game.stats["caught"] - game.stats["empty_catches"])
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main())

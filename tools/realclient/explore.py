"""
Ad-hoc real-client run for exploring how the bot behaves (not a test):

    .venv/bin/python tools/realclient/explore.py <case> [seconds]

Starts a hidden display, brings the case's clients up, runs the bots and
prints their status lines, stop reasons and the characters' final state.
Cases are defined in CASES below; the tests in tests/realclient encode what
was learned here as assertions.
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from botrun import BotRun  # noqa: E402
from display import HiddenDisplay  # noqa: E402
from rig import RigSession  # noqa: E402

WORM = 27801
ZANDER = 27803


# Spots on the Yongan bridge 6 m apart (x in cm): every character faces the
# water and the others are out of the middle of its screen.
SPOTS = [485001, 485001 - 600, 485001 + 600, 485001 + 1200]


def spot(i):
    return {"map_index": 1, "x": SPOTS[i], "y": 962098}


def worms(n, keys=(1, 2, 3, 4)):
    return [{"vnum": WORM, "count": n, "hotkey": k} for k in keys]


CASES = {
    "minigame4": {
        "instance": "fish",
        "scenario": {"server": {"fish_event": False}, "restart_on_crash": 0, "clients": [
            {"account": "admin"},
            {"account": "admin2", "items": worms(3)},
            {"account": "admin3", "ui": {"open_inventory": False}},
            {"account": "admin4", "items": worms(200) + [{"vnum": ZANDER, "count": 86}]},
        ]},
        "bots": {"admin": {"auto_fish_handling": True}, "admin2": {}, "admin3": {}, "admin4": {}},
    },
    "autopages": {      # trailer 2026-11-27: auto fish handling on, 2 pages: settled on page II
        "instance": "fish",
        "scenario": {"server": {"fish_event": False}, "restart_on_crash": 0, "clients": [
            {"account": "admin", "position": spot(0), "items": worms(200) + [{"vnum": ZANDER, "count": 1}]},
        ]},
        "close_map": ["admin"],
        "bots": {"admin": {"auto_fish_handling": True, "confirm_button_pos": [360, 326],
                           "fish_actions": {"Zander_living.jpg": "drop", "Carp_living.jpg": "drop",
                                            "Catsfish_living.jpg": "drop"}}},
    },
    "classic4": {
        "instance": "classic",
        "scenario": {"restart_on_crash": 0, "clients": [
            {"account": "admin"},
            {"account": "admin2", "items": worms(1, keys=(1, 2))},
            {"account": "admin3", "ui": {"inventory_page": 2}},
            {"account": "admin4", "rod": None},
        ]},
        "bots": {a: {"classic_fishing": True} for a in ("admin", "admin2", "admin3", "admin4")},
    },
    "jigsaw4": {
        "instance": "fish",
        "kind": "jigsaw",
        "scenario": {"server": {"fish_event": True}, "restart_on_crash": 0,
                     "defaults": {"items": worms(200) + [{"vnum": 25106, "count": 40}, {"vnum": 25107, "count": 3}],
                                  "jigsaw": {"board": "reset", "pieces": [], "held": 0, "use_count": 0}},
                     "clients": [
                         {"account": "admin", "ui": {"jigsaw": "puzzle"}},
                         {"account": "admin2", "ui": {"jigsaw": "intro"}},
                         {"account": "admin3", "ui": {"jigsaw": "closed"}},
                         {"account": "admin4", "ui": {"jigsaw": "puzzle"},
                          "jigsaw": {"board": "reset", "held": 4,
                                     "pieces": [{"shape": 1, "cell": 0}, {"shape": 5, "cell": 2},
                                                {"shape": 2, "cell": 23}]}},
                     ]},
        "bots": {a: {"confirm_button_pos": [404, 321]} for a in ("admin", "admin2", "admin3", "admin4")},
    },
    "jigsawpos": {
        "instance": "fish",
        "kind": "jigsaw",
        "scenario": {"server": {"fish_event": True}, "restart_on_crash": 0,
                     "defaults": {"items": worms(200) + [{"vnum": 25106, "count": 40}, {"vnum": 25107, "count": 3}],
                                  "jigsaw": {"board": "reset", "pieces": [], "held": 0, "use_count": 0}},
                     "clients": [
                         {"account": "admin", "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 10]}},
                         {"account": "admin2", "ui": {"jigsaw": "puzzle", "jigsaw_position": [300, 10]}},
                         {"account": "admin3", "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 330]}},
                         {"account": "admin4", "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 10]},
                          "jigsaw": {"board": "reset", "held": 4,
                                     "pieces": [{"shape": 1, "cell": 0}, {"shape": 5, "cell": 2},
                                                {"shape": 2, "cell": 23}]}},
                     ]},
        "bots": {a: {"confirm_button_pos": [360, 326]} for a in ("admin", "admin2", "admin3", "admin4")},
    },
    "positions": {
        "instance": "classic",
        "scenario": {"restart_on_crash": 0, "clients": [
            {"account": "admin", "position": {"map_index": 1, "x": 485001 + 600, "y": 962098}},
            {"account": "admin2", "position": {"map_index": 1, "x": 485001 - 600, "y": 962098}},
            {"account": "admin3", "position": {"map_index": 1, "x": 485001 + 1200, "y": 962098}},
            {"account": "admin4", "position": {"map_index": 1, "x": 485001 - 1200, "y": 962098}},
        ]},
        "close_map": ["admin", "admin2", "admin3", "admin4"],
        "bots": {},
    },
    "jigsawspread": {
        "instance": "fish",
        "kind": "jigsaw",
        "scenario": {"server": {"fish_event": True}, "restart_on_crash": 0,
                     "defaults": {"items": worms(200) + [{"vnum": 25106, "count": 40}, {"vnum": 25107, "count": 3}],
                                  "jigsaw": {"board": "reset", "pieces": [], "held": 0, "use_count": 0}},
                     "clients": [
                         {"account": "admin", "position": spot(0), "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 10]}},
                         {"account": "admin2", "position": spot(1), "ui": {"jigsaw": "puzzle", "jigsaw_position": [300, 10]}},
                         {"account": "admin3", "position": spot(2), "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 330]},
                          "items": worms(200) + [{"vnum": 25106, "count": 5}]},
                         {"account": "admin4", "position": spot(3), "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 10]},
                          "jigsaw": {"board": "reset", "held": 4,
                                     "pieces": [{"shape": 1, "cell": 0}, {"shape": 5, "cell": 2},
                                                {"shape": 2, "cell": 23}]}},
                     ]},
        "bots": {a: {"confirm_button_pos": [360, 326]} for a in ("admin", "admin2", "admin3", "admin4")},
    },
    "classicspread": {
        "instance": "classic",
        "scenario": {"restart_on_crash": 0, "clients": [
            {"account": "admin", "position": spot(0)},
            {"account": "admin2", "position": spot(1)},
            {"account": "admin3", "position": spot(2), "rod": None},
            {"account": "admin4", "position": spot(3), "ui": {"inventory_page": 2}},
        ]},
        "close_map": ["admin", "admin2", "admin3", "admin4"],
        "bots": {a: {"classic_fishing": True} for a in ("admin", "admin2", "admin3", "admin4")},
    },
    "classicfix": {
        "instance": "classic",
        "scenario": {"restart_on_crash": 0, "clients": [
            {"account": "admin"},
            {"account": "admin2"},
            {"account": "admin3", "rod": None},
            {"account": "admin4", "ui": {"inventory_page": 2}},
        ]},
        "close_map": ["admin", "admin3", "admin4"],
        "bots": {a: {"classic_fishing": True} for a in ("admin", "admin2", "admin3", "admin4")},
    },
}


def main():
    name = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 120
    case = CASES[name]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.expanduser(f"~/.local/share/fishbot-realclient/explore/{stamp}-{name}")
    os.makedirs(run_dir, exist_ok=True)
    with HiddenDisplay() as disp:
        disp.activate()
        rig = RigSession(case["instance"], disp)
        busy = rig.in_use_elsewhere()
        if busy:
            sys.exit(f"instance busy elsewhere: {busy}")
        t0 = time.time()
        rig.up(case["scenario"])
        print(f"up in {time.time() - t0:.0f}s on {disp.display}: {rig.clients}", flush=True)
        for acc in case.get("close_map", []):
            print(f"{acc}: area map closed: {rig.close_area_map(acc)}", flush=True)
        for acc in rig.clients:
            rig.screenshot(acc, os.path.join(run_dir, f"start_{acc}.png"))
        run = BotRun(run_dir, name)
        for slot, (acc, ov) in enumerate(case["bots"].items()):
            if case.get("kind") == "jigsaw":
                run.add_jigsaw(rig.clients[acc], slot, ov)
            else:
                run.add_fishing(rig.clients[acc], slot, ov)
        run.run(timeout=secs)
        for acc in rig.clients:
            rig.screenshot(acc, os.path.join(run_dir, f"end_{acc}.png"))
        print(run.report())
        final = rig.finish()
        for acc, ch in final.items():
            print(acc, json.dumps(ch.get("totals")), "hotkeys", ch.get("hotkeys"), "jigsaw", ch.get("jigsaw"))
        rig.close()
    print("run dir", run_dir)


if __name__ == "__main__":
    main()

"""The simulator window: a stand-in game window the bot can be pointed at.

    python tools/minigame_sim                       fishing, stock colors
    python tools/minigame_sim --skin emerald        another look (Tab cycles)
    python tools/minigame_sim --mode jigsaw         the jigsaw event window
    python tools/minigame_sim --windows 3           three windows side by side
    python tools/minigame_sim --demo --cycle 2      watch the bot play every skin in turn
    python tools/minigame_sim --demo --realistic    the bot on the real client's textures and timings

The window is an ordinary 800x600 window titled "Metin2 Sim", so it shows up
first in the bot's window list. Start the bot, pick it, press Start: the bot
finds, captures, focuses and clicks it through its real OS layer, exactly as
it does with the game.

Keys in the window:  1-4 / F1-F4 bait,  space cast,  Tab next skin,
F9 fishing <-> jigsaw,  F10 reset.  Everything can be played by hand too.

Every game event is printed as a line on stdout; closing the window prints
the totals.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))

# The bot sees X11 windows only (the game runs under Wine, which draws through
# X11), so on a Wayland desktop the simulator has to be an XWayland window.
if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
# One picture pixel = one screen pixel, whatever the desktop scaling is.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

import cv2  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from minigame_sim.fishing_game import FishingGame, SKINS  # noqa: E402


class JigsawView:
    """Gives the jigsaw game the same face as the fishing game."""

    def __init__(self, args, index):
        import random
        from minigame_sim.jigsaw_game import JigsawGame, W, H, PAGES
        self.W, self.H = W, H
        # Spare stacks at random places on random inventory pages
        rng = random.Random(args.seed + index)
        places = [(page, (ix, iy)) for page in PAGES for ix in (30, 75, 120, 165) for iy in range(50, 330, 45)]
        rng.shuffle(places)
        inventory = {}
        kinds = [("normal", args.stack)] * args.inv_normal + [("special", 1)] * args.inv_special
        for (page, pos), (section, stack) in zip(places, kinds):
            inventory.setdefault(page, []).append((section, pos, stack))
        self.game = JigsawGame(seed=args.seed + index, normal=args.crates, special=args.special,
                               inventory=inventory)
        self._seen = self.counts()

    def counts(self):
        g = self.game
        return dict(opened=g.opened, special=g.opened_by["special"], placed=g.placed,
                    discarded=g.discarded, restocked=g.refills, completed=g.completed,
                    errors=len(g.errors))

    @property
    def stats(self):
        return self.counts()

    def move(self, x, y):
        self.game.cursor = (int(x), int(y))

    def key(self, name):
        pass

    def click(self, x, y, button="left"):
        self.game.click(int(x), int(y), button)

    def render(self):
        return self.game.render()

    def grab(self, left, top, width, height):
        return self.game.grab(left, top, width, height)

    def poll_events(self):
        now = self.counts()
        out = []
        for k, v in now.items():
            if v != self._seen[k]:
                detail = self.game.errors[-1] if k == "errors" else str(v)
                out.append(("error" if k == "errors" else k, detail))
        self._seen = now
        return out

    def hud(self):
        g = self.game
        spare = sum(stack for items in g.inventory.values() for _, _, stack in items)
        return ("jigsaw | " + "  ".join(f"{k} {v}" for k, v in self.counts().items())
                + f" | slots: normal {g.slots['normal']} special {g.slots['special']}  inventory {spare}")


class Demo:
    """Plays the window with the real bot, without touching the real mouse.

    The bot's eyes and hands are wired straight to the game model (as in
    bench.py): it reads the picture the window shows and its clicks go to the
    game. The window draws the bot's cursor so the play can be watched. With
    ``--cycle N`` the fishing demo moves to the next skin (with a fresh bot)
    after every N games."""

    def __init__(self, window):
        import threading
        from minigame_sim import bench          # stubs the OS input modules for this process
        self.bench = bench
        self.window = window
        self.closed = False
        self.mouse = None
        self.click_at = 0.0
        self.last_log = ""
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _log(self, message):
        self.last_log = str(message).split("] ", 1)[-1]

    def _wire_mouse(self, game):
        demo = self

        class Mouse(self.bench.GameMouse):
            def mouse_down(self, button="left"):
                demo.click_at = time.time()
                super().mouse_down(button)

            def click(self, button="left"):
                demo.click_at = time.time()
                super().click(button)

        self.mouse = Mouse(game)
        return self.mouse

    def _loop(self):
        import threading
        while not self.closed:
            window, game, mode = self.window, self.window.game, self.window.mode
            if mode == "jigsaw":
                from jigsaw_bot import JigsawBot
                config = {"confirm_button_pos": (400, 380), "inv_page_1_pos": (640, 180),
                          "inv_page_2_pos": (680, 180), "inv_page_3_pos": (720, 180),
                          "inv_page_4_pos": (760, 180), "capture_backend": "screen",
                          "jigsaw_crate_priority": window.args.priority,
                          "jigsaw_grid_bounds": [100, 150, 264, 176],
                          "jigsaw_action_delay": window.args.demo_delay}
                bot = JigsawBot(self.bench.GameWM(game), config, bot_id=window.index,
                                on_status_update=self._log)
                mouse = self._wire_mouse(game)
                bot.mouse_backend = mouse
                bot.human.mouse = mouse
                bot.capture.grab = game.grab
                target = bot.start
                finished = lambda: False
            else:
                config = {"timing_recast_wait": 2.0, "timing_minigame_timeout": 6.0,
                          "human_like_clicking": window.args.human}
                if window.args.realistic:
                    # set up like the player's bot: quick skip, auto handling
                    config = self.bench.realistic_config(game, human_like_clicking=window.args.human)
                bot, _logs = self.bench.make_bot(game, config)
                bot.bot_id = window.index
                bot.on_status_update = self._log
                mouse = self._wire_mouse(game)
                bot.mouse_backend = mouse
                bot.human.mouse = mouse
                target = bot.play_game
                start = game.stats["caught"] + game.stats["timeouts"]
                cycle = window.args.cycle
                finished = lambda: cycle and game.stats["caught"] + game.stats["timeouts"] - start >= cycle
            bot.running = True
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            while not self.closed and window.game is game and not finished() and thread.is_alive():
                time.sleep(0.05)
            ran_out = mode == "jigsaw" and not thread.is_alive() and window.game is game
            bot.stop()
            thread.join(timeout=15)
            if ran_out and not self.closed:
                # Every crate is used up and the bot stopped: start over
                time.sleep(4.0)
                if window.game is game:
                    window.restart = True
            if not self.closed and window.game is game and mode == "fishing" and finished():
                time.sleep(0.6)
                window.next_skin()
            time.sleep(0.2)

    def draw(self, frame):
        if self.mouse is None:
            return
        x, y = self.mouse.pos
        if time.time() - self.click_at < 0.18:
            cv2.circle(frame, (x, y), 11, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.line(frame, (x - 7, y), (x + 7, y), (0, 0, 0), 3)
        cv2.line(frame, (x, y - 7), (x, y + 7), (0, 0, 0), 3)
        cv2.line(frame, (x - 7, y), (x + 7, y), (255, 255, 255), 1)
        cv2.line(frame, (x, y - 7), (x, y + 7), (255, 255, 255), 1)
        if self.last_log:
            cv2.putText(frame, "bot: " + self.last_log[:100], (8, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                        (170, 170, 170), 1, cv2.LINE_AA)


class SimWindow(QWidget):
    def __init__(self, args, index):
        super().__init__()
        self.args = args
        self.index = index
        self.mode = args.mode
        self.t0 = time.time()
        self._events = []
        self._frame = None
        self.restart = False               # set by the demo when the crates ran out
        self.setWindowTitle(args.title if args.windows == 1 else f"{args.title} {index + 1}")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.new_game()
        if args.pos:
            self.move(args.pos[0] + index * (self.game.W + 12), args.pos[1])
        self.demo = Demo(self) if args.demo else None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(max(1, int(1000 / args.fps)))

    # ------------------------------------------------------------------ game

    def new_game(self):
        a = self.args
        if self.mode == "jigsaw":
            self.game = JigsawView(a, self.index)
        else:
            skin = getattr(self, "_skin", a.skin)
            self._skin = skin
            if a.realistic:
                # The client as measured on a real session (bench.REALISTIC)
                from minigame_sim import bench
                opts = dict(bench.REALISTIC, skin=skin, size=tuple(a.size), seed=a.seed + self.index,
                            feedback_text=not a.no_text)
                self.game = FishingGame(**opts)
            else:
                self.game = FishingGame(
                    skin=skin, size=tuple(a.size), seed=a.seed + self.index, bite_delay=tuple(a.bite_delay),
                    duration=a.duration, miss_penalty=a.miss_penalty, fish_speed=tuple(a.speed),
                    catch_chance=a.catch_chance, dead_fish_chance=a.dead_fish_chance,
                    click_cooldown=a.click_cooldown, require_bait=not a.no_bait, inventory=not a.no_inventory,
                    feedback_text=not a.no_text)
            self.game.on_event = lambda kind, detail: self._events.append((kind, detail))
        self.setFixedSize(self.game.W, self.game.H)
        self.log("mode", self.mode if self.mode == "jigsaw" else f"fishing skin={self._skin}")

    def next_skin(self):
        names = list(SKINS)
        self._skin = names[(names.index(self._skin) + 1) % len(names)]
        self.game.set_skin(self._skin)
        self._events.append(("skin", self._skin))

    def log(self, kind, detail=""):
        print(f"{time.time() - self.t0:8.2f} w{self.index + 1} {kind} {detail}".rstrip(), flush=True)

    def tick(self):
        if self.restart:
            self.restart = False
            self.new_game()
        if self.mode == "jigsaw":
            self._events.extend(self.game.poll_events())
        events, self._events = self._events, []
        for kind, detail in events:
            self.log(kind, detail)
        self.update()

    def hud(self):
        if self.mode == "jigsaw":
            return self.game.hud() + " | F9 fishing  F10 reset"
        s = self.game.stats
        return (f"fishing | {self._skin} | casts {s['casts']}  caught {s['caught']}  hits {s['hits']}  "
                f"miss {s['misses']}  timeouts {s['timeouts']} | 1-4 bait  space cast  Tab skin  F9 jigsaw")

    # ----------------------------------------------------------------- events

    def paintEvent(self, _event):
        frame = self.game.render()
        if self.demo is not None:
            self.demo.draw(frame)
        if not self.args.no_hud:
            cv2.putText(frame, self.hud(), (8, frame.shape[0] - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                        (170, 170, 170), 1, cv2.LINE_AA)
        self._frame = frame                      # QImage does not copy the pixels
        h, w = frame.shape[:2]
        painter = QPainter(self)
        painter.drawImage(0, 0, QImage(frame.data, w, h, 3 * w, QImage.Format_BGR888))
        painter.end()

    def mouseMoveEvent(self, event):
        p = event.position()
        self.game.move(p.x(), p.y())

    def mousePressEvent(self, event):
        button = {Qt.LeftButton: "left", Qt.RightButton: "right"}.get(event.button())
        if button:
            p = event.position()
            self.game.move(p.x(), p.y())
            self.game.click(p.x(), p.y(), button)

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        key = event.key()
        if key == Qt.Key_Tab and self.mode == "fishing":
            self.next_skin()
        elif key == Qt.Key_F9:
            self.mode = "jigsaw" if self.mode == "fishing" else "fishing"
            self.new_game()
        elif key == Qt.Key_F10:
            self.new_game()
        elif key == Qt.Key_Space:
            self.game.key("space")
        elif Qt.Key_F1 <= key <= Qt.Key_F8:
            self.game.key(f"F{key - Qt.Key_F1 + 1}")
        elif event.text().strip():
            self.game.key(event.text())

    def closeEvent(self, event):
        if self.demo is not None:
            self.demo.closed = True
        self.log("totals", json.dumps(self.game.stats))
        super().closeEvent(event)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("fishing", "jigsaw"), default="fishing")
    ap.add_argument("--skin", choices=list(SKINS), default=None,
                    help="default: official, or real-mt2 with --realistic")
    ap.add_argument("--title", default="Metin2 Sim")
    ap.add_argument("--windows", type=int, default=1, help="number of simulator windows")
    ap.add_argument("--size", type=int, nargs=2, default=(800, 600), metavar=("W", "H"),
                    help="fishing window size (the jigsaw window is always 800x600)")
    ap.add_argument("--pos", type=int, nargs=2, metavar=("X", "Y"), help="where to put the (first) window")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--no-hud", action="store_true", help="no status line at the bottom")
    d = ap.add_argument_group("demo")
    d.add_argument("--demo", action="store_true",
                   help="let the real bot play the window (wired to it directly: the real mouse and "
                        "keyboard are not used, so this shows the game and the bot's decisions, not the OS layer)")
    d.add_argument("--cycle", type=int, default=0, metavar="N",
                   help="fishing demo: next skin, with a fresh bot, after every N games")
    d.add_argument("--human", action="store_true", help="fishing demo: human-like clicking")
    d.add_argument("--demo-delay", type=float, default=0.25, help="jigsaw demo: seconds between actions")
    f = ap.add_argument_group("fishing")
    f.add_argument("--bite-delay", type=float, nargs=2, default=(1.5, 4.0), metavar=("MIN", "MAX"),
                   help="seconds from the cast to the minigame")
    f.add_argument("--duration", type=float, default=15.0, help="minigame time limit")
    f.add_argument("--miss-penalty", type=float, default=1.0, help="seconds a miss costs")
    f.add_argument("--speed", type=float, nargs=2, default=(70.0, 230.0), metavar=("MIN", "MAX"),
                   help="fish speed, px/s")
    f.add_argument("--catch-chance", type=float, default=1.0, help="chance that 3 hits give an item")
    f.add_argument("--dead-fish-chance", type=float, default=0.0,
                   help="chance that a fish does not open on right click")
    f.add_argument("--no-bait", action="store_true", help="space casts without a bait key first")
    f.add_argument("--no-inventory", action="store_true")
    f.add_argument("--no-text", action="store_true", help="no HIT / MISS text in the panel")
    f.add_argument("--realistic", action="store_true",
                   help="the client as measured on a real session (timings, diagonal fish, items, mount); "
                        "with --demo the bot is set up like the player's")
    f.add_argument("--click-cooldown", type=float, default=0.5,
                   help="seconds after a click in which the panel takes no further click")
    j = ap.add_argument_group("jigsaw")
    j.add_argument("--crates", type=int, default=6, help="normal crates in the slot at the start")
    j.add_argument("--special", type=int, default=1,
                   help="special crates in the slot at the start (each gives the 3x2 piece)")
    j.add_argument("--inv-normal", type=int, default=4, help="spare stacks of normal crates in the inventory")
    j.add_argument("--inv-special", type=int, default=2, help="spare special crates in the inventory")
    j.add_argument("--stack", type=int, default=6, help="crates per spare normal stack")
    j.add_argument("--priority", default="special_when_better",
                   choices=("normal_first", "special_first", "special_when_better"),
                   help="demo: the bot's jigsaw_crate_priority")
    args = ap.parse_args(argv)
    if args.skin is None:
        args.skin = "real-mt2" if args.realistic else "official"

    app = QApplication(sys.argv[:1])
    windows = [SimWindow(args, i) for i in range(max(1, args.windows))]
    for w in windows:
        w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

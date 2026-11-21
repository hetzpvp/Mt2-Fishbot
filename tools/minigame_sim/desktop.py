"""Several simulated fishing windows on one simulated desktop.

Real clients share one mouse cursor, one keyboard and one focused window.
That is what makes several bots at once hard: a key goes to whatever window
is in front, a click to whatever window is under the cursor. The desktop
here enforces exactly that and counts every input that reached a window
other than the one its bot meant (``misdirected_keys`` / ``misdirected_clicks``).

Each bot captures its own window directly (like per-window capture does), so
windows that are not in front can still be read — but not typed or clicked
into.

    desktop = FishingDesktop(switch_delay=0.01)
    bots = [desktop.add(FishingGame(...), config) for _ in range(3)]
    desktop.run(until=lambda: ...)
"""

import os
import random
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(ROOT, "tests"), os.path.join(ROOT, "tools"), os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from _stubs import install  # noqa: E402

install()

from fishing_bot import FishingBot  # noqa: E402
import utils  # noqa: E402

# Short waits so sessions fit into a test; bots that need other values pass them.
FAST = {
    "adaptive_detection": True,
    "adaptive_recast_wait": False,
    "timing_recast_wait": 0.5,
    "timing_minigame_timeout": 3.0,
    "bait_keys": ["1", "2", "3", "4"],
}


class FishingDesktop:
    """One cursor, one keyboard, one focused window."""

    def __init__(self, switch_delay=0.0, refuse_focus=0.0, seed=1, click_latency=None):
        self.games = []
        self.bots = []
        self.logs = []
        self.threads = []
        self.pos = (0, 0)
        self.foreground = None
        self.switch_delay = switch_delay       # seconds a focus switch takes
        self.refuse_focus = refuse_focus       # share of focus requests the OS ignores
        self.switches = 0
        self.refused = 0
        self.misdirected_keys = 0              # keys that reached another window than intended
        self.misdirected_clicks = 0
        self.lock = threading.Lock()
        self._rng = random.Random(seed)
        # (min, max) seconds a game takes to take in a click. A window that
        # loses the focus before then never sees it (real clients under Wine:
        # 37 of 63 clicks were lost when the focus moved 20-30 ms after them,
        # none of 16 at 30 ms or later). None: clicks land at once.
        self.click_latency = click_latency
        self._pending = []                     # [game, deliver_at, lost] clicks on their way
        self.clicks_lost = 0
        self._last_click = {}                  # game -> time of its last click
        self.click_to_switch = []              # seconds from a window's last click to a switch away

    # ------------------------------------------------------------------ setup

    def add(self, game, config=None, bait=10 ** 6):
        """Puts ``game`` on the desktop (windows side by side) with a bot of its own."""
        index = len(self.games)
        game.left, game.top = sum(g.W for g in self.games), 0
        self.games.append(game)
        if index == 0:
            FishingBot._shared_learned_profile = None
            FishingBot._shared_recast_wait = None
            FishingBot._known_profiles = []
        bot = FishingBot(None, dict(FAST, **(config or {})), DesktopWM(self, game),
                         bait_counter=bait, bot_id=index)
        mouse, keyboard = DesktopMouse(self, game), DesktopKeyboard(self, game)
        bot.mouse_backend = mouse
        bot.human.mouse = mouse
        bot.keyboard_controller = keyboard
        bot.human.keyboard_controller = keyboard
        bot._grab = game.grab
        bot.on_status_update = self.logs.append
        self.bots.append(bot)
        return bot

    def game_at(self, pos):
        for game in self.games:
            if game.left <= pos[0] < game.left + game.W and game.top <= pos[1] < game.top + game.H:
                return game
        return None

    # -------------------------------------------------------------------- run

    def start(self):
        for bot in self.bots:
            bot.running = True
        self.threads = [threading.Thread(target=bot.play_game, daemon=True) for bot in self.bots]
        for t in self.threads:
            t.start()

    def stop(self):
        for bot in self.bots:
            bot.stop()
        for t in self.threads:
            t.join(timeout=20)
        assert not any(t.is_alive() for t in self.threads), "a bot did not stop"

    def run(self, until, timeout=120.0):
        """Runs all bots until ``until()`` is true. Returns whether it became true."""
        self.start()
        deadline = time.time() + timeout
        while time.time() < deadline and not until() and any(t.is_alive() for t in self.threads):
            time.sleep(0.02)
        ok = bool(until())
        self.stop()
        return ok


class DesktopWM:
    """Window manager of one window on the shared desktop."""

    def __init__(self, desktop, game):
        self.desktop = desktop
        self.game = game
        self.selected_window = object()
        self.last_switch_time = 0.0
        self.available = True

    def is_window_available(self):
        return self.available

    def is_foreground(self):
        return self.desktop.foreground is self.game

    def activate_window(self, force_activate=False):
        d = self.desktop
        if d.foreground is self.game:
            return True
        if d.refuse_focus and d._rng.random() < d.refuse_focus:
            d.refused += 1
            return False
        # The bot's WindowManager waits like this before every switch
        utils.wait_keys_delivered()
        if d.switch_delay:
            time.sleep(d.switch_delay)
        with d.lock:
            now = time.perf_counter()
            last = d._last_click.pop(d.foreground, None)
            if last is not None:
                d.click_to_switch.append(now - last)
            for click in d._pending:
                if click[0] is d.foreground and now < click[1] and not click[2]:
                    click[2] = True            # the window lost the focus before it took the click
                    d.clicks_lost += 1
                    click[0].stats["clicks_lost"] += 1
            d._pending = [c for c in d._pending if now < c[1]]
        d.foreground = self.game
        d.switches += 1
        self.last_switch_time = time.perf_counter()
        return True

    def get_window_rect(self):
        return (self.game.left, self.game.top, self.game.W, self.game.H)

    def invalidate_rect_cache(self):
        pass


class DesktopMouse:
    """The shared mouse, as one bot uses it."""

    name = "sim"

    def __init__(self, desktop, game):
        self.desktop = desktop
        self.game = game

    def move_to(self, x, y):
        d = self.desktop
        with d.lock:
            d.pos = (int(x), int(y))
            target = d.game_at(d.pos)
        if target is not None:
            target.move(d.pos[0] - target.left, d.pos[1] - target.top)

    def position(self):
        return self.desktop.pos

    def _click(self, button):
        d = self.desktop
        with d.lock:
            pos = d.pos
            target = d.game_at(pos)
            if target is not self.game or d.foreground is not self.game:
                d.misdirected_clicks += 1
        if target is None:
            return
        with d.lock:
            d._last_click[target] = time.perf_counter()
        x, y = pos[0] - target.left, pos[1] - target.top
        if not d.click_latency:
            target.click(x, y, button)
            return
        click = [target, time.perf_counter() + d._rng.uniform(*d.click_latency), False]
        with d.lock:
            d._pending.append(click)

        def deliver():
            if not click[2]:
                target.click(x, y, button)

        timer = threading.Timer(max(0.0, click[1] - time.perf_counter()), deliver)
        timer.daemon = True
        timer.start()

    def click(self, button="left"):
        self._click(button)

    def mouse_down(self, button="left"):
        self._click(button)

    def mouse_up(self, button="left"):
        pass

    def close(self):
        pass


class DesktopKeyboard:
    """The shared keyboard: keys go to the window that has the focus."""

    def __init__(self, desktop, game):
        self.desktop = desktop
        self.game = game

    def press(self, key):
        d = self.desktop
        with d.lock:
            target = d.foreground
            if target is not self.game:
                d.misdirected_keys += 1
        if target is not None:
            target.key(str(key))

    def release(self, key):
        target = self.desktop.foreground
        if target is not None:
            target.key_up(str(key))

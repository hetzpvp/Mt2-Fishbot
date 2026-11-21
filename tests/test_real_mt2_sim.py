"""The simulator drawn from a real Metin2 client's own textures (skin "real-mt2",
tools/minigame_sim/fishing_game.py RealMt2Look; textures from
tools/mt2_assets.py), checked against lossless shots of the real client
(tests/fixtures/real_mt2, diagnostics 20261120_195925), and the bot playing it.

Also: the bot's item templates are the game's icons on the real empty slot."""

import os
import re
import sys
import unittest

import cv2
import numpy as np

from _stubs import install, ROOT

install()

TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from minigame_sim import bench  # noqa: E402
from minigame_sim.fishing_game import REAL_MT2_ASSETS, RealMt2Look, FishingGame  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "real_mt2")
ASSETS = os.path.join(ROOT, "assets", "fishing")


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def open_game(**kw):
    clock = FakeClock()
    game = FishingGame(skin="real-mt2", clock=clock, bite_delay=(1.0, 1.0), inventory=False, **kw)
    game.key("1")
    game.key("space")
    clock.t += 1.01
    game.update()
    assert game.panel_open
    return game, clock


def window_of(game, frame):
    """The 292x263 window (board) around the panel."""
    bx = game.panel[0] - RealMt2Look.PANEL_IN_BOARD[0]
    by = game.panel[1] - RealMt2Look.PANEL_IN_BOARD[1]
    w, h = RealMt2Look.BOARD_SIZE
    return frame[by:by + h, bx:bx + w]


class LayoutTests(unittest.TestCase):
    """RealMt2Look's numbers are the client's uiscript (FishingGameWindow)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REAL_MT2_ASSETS, "fishinggamewindow.py.txt"), encoding="utf-8") as f:
            cls.script = f.read()

    def element(self, name):
        block = self.script[self.script.index(f'"name" : "{name}"'):]
        x = re.search(r'"x"\s*:\s*([^,]+),', block).group(1)
        y = re.search(r'"y"\s*:\s*([^,]+),', block).group(1)
        return eval(x), eval(y)                      # e.g. "7 + 278 - 73"

    def test_positions_are_the_uiscripts(self):
        self.assertEqual(self.element("fishing_background_water"), RealMt2Look.WATER)
        self.assertEqual(self.element("fishing_water_navArea"), RealMt2Look.NAV[:2])
        self.assertEqual(self.element("fishing_goal_circle"), RealMt2Look.RING)
        self.assertEqual(self.element("fishing_goal_count"), RealMt2Look.COUNTER)
        self.assertEqual(self.element("fishing_timer_baseImg"), RealMt2Look.TIMER)
        self.assertIn("WINDOW_WIDTH = 278 + 14", self.script)
        self.assertIn("WINDOW_HEIGHT = 226 + 37", self.script)
        self.assertEqual(RealMt2Look.BOARD_SIZE, (278 + 14, 226 + 37))
        self.assertIn('"width" : 278 - 30', self.script)
        self.assertEqual(RealMt2Look.NAV[2:], (278 - 30, 226 - 30))

    def test_ring_is_the_sprites(self):
        game, _ = open_game()
        ring = RealMt2Look.load()["ring"]
        alpha = ring[:, :, 3]
        yy, xx = np.mgrid[0:142, 0:142]
        radius = np.hypot(xx - 70.5, yy - 70.5)[alpha > 240]
        self.assertAlmostEqual(float(np.median(radius)), game.circle[2], delta=0.6)
        # sprite center in the panel: water (68 + 70.5) - navArea 15
        self.assertAlmostEqual(game.circle[0], RealMt2Look.RING[0] + 70.5 - RealMt2Look.NAV[0], delta=0.5)
        self.assertAlmostEqual(game.circle[1], RealMt2Look.RING[1] + 70.5 - RealMt2Look.NAV[1], delta=0.5)


class FidelityTests(unittest.TestCase):
    """The drawn window against a lossless shot of the real one."""

    def test_window_matches_the_real_client(self):
        real = cv2.imread(os.path.join(FIXTURES, "fishing_window.png")).astype(int)
        game, clock = open_game()
        # As in the shot: 0/3, full timer, the fish at water (103, 90)
        # heading up-left (inside the ring, so it is red), a ripple ahead
        game.fish_motion = "diagonal"
        game._dir = (-1.0, -1.0)
        bx = game.panel[0] - RealMt2Look.PANEL_IN_BOARD[0]
        by = game.panel[1] - RealMt2Look.PANEL_IN_BOARD[1]
        game.fish = (bx + RealMt2Look.WATER[0] + 103, by + RealMt2Look.WATER[1] + 90)
        game._pause_until = float("inf")
        game.time_left = game.duration
        game._t0 = 0.0
        clock.t = 1.25            # tail frame 1, ripple frame 3 of 4
        sim = window_of(game, game.render()).astype(int)
        self.assertEqual(sim.shape, real.shape)
        diff = np.abs(sim - real).max(axis=2)
        self.assertLess(diff.mean(), 2.0)
        self.assertLess((diff > 20).mean(), 0.01)
        # the frame (title bar, borders) is the real one
        self.assertEqual(int(diff[:29].max()), 0)
        # counter and timer: the textures where the client draws them
        self.assertLess(diff[34:62, 212:282].mean(), 1.5)
        self.assertLess(diff[223:251, 35:258].mean(), 1.5)

    def test_ring_is_white_and_counter_dimmed_after_a_hit(self):
        game, clock = open_game()
        cx, cy, _ = game.circle_abs()
        game.fish = (cx, cy)
        game._pause_until = float("inf")
        ring_px = (game.panel[0] + game.circle[0], game.panel[1] + game.circle[1] - game.circle[2])
        red = game.render()[ring_px[1], ring_px[0]].astype(int)
        game.click(cx, cy)
        self.assertEqual(game.hits, 1)
        after = game.render()
        white = after[ring_px[1], ring_px[0]].astype(int)
        self.assertGreater(white[1] - red[1], 30)                 # tint off: green comes back
        x = game.panel[0] - RealMt2Look.PANEL_IN_BOARD[0] + RealMt2Look.COUNTER[0]
        y = game.panel[1] - RealMt2Look.PANEL_IN_BOARD[1] + RealMt2Look.COUNTER[1]
        icon = after[y + 8:y + 20, x + 8:x + 20].astype(float).mean()
        clock.t += game.click_cooldown + 0.01
        bright = game.render()[y + 8:y + 20, x + 8:x + 20].astype(float).mean()
        self.assertLess(icon, bright * 0.75)
        # "1/3": the stored text for one hit
        text = RealMt2Look.load()["counter_text"][1]
        self.assertTrue(np.array_equal(game.render()[y:y + 28, x + 30:x + 66], text))

    def test_hit_appears_at_the_click_and_rises(self):
        game, clock = open_game()
        cx, cy, _ = game.circle_abs()
        game.fish = (cx, cy)
        game._pause_until = float("inf")
        game.click(cx, cy)
        hit = RealMt2Look.load()["hit"]
        tpl = hit[:, :, :3].astype(np.uint8)
        mask = (hit[:, :, 3] > 200).astype(np.uint8)
        found = []
        for dt in (0.0, 0.2):
            clock.t += dt
            frame = game.render()
            score = cv2.matchTemplate(frame, tpl, cv2.TM_SQDIFF, mask=mask)
            found.append(cv2.minMaxLoc(score)[2])
        (x0, y0), (x1, y1) = found
        self.assertEqual((x0 - cx, y0 - cy), RealMt2Look.HIT_OFFSET)
        self.assertEqual(x1, x0)
        self.assertAlmostEqual(y0 - y1, RealMt2Look.HIT_RISE * 0.2, delta=1)
        clock.t += RealMt2Look.HIT_TIME
        frame = game.render()
        self.assertGreater(cv2.minMaxLoc(cv2.matchTemplate(frame, tpl, cv2.TM_SQDIFF, mask=mask))[0],
                           1000 * mask.sum())                     # gone

    def test_window_stays_on_screen_when_moved_or_wandering(self):
        game, _ = open_game(wander=True)
        left, top, right, bottom = game._frame_margins()
        for _ in range(30):
            game._place_panel()
            x, y, w, h = game.panel
            self.assertGreaterEqual(x - left, 0)
            self.assertGreaterEqual(y - top, 0)
            self.assertLessEqual(x + w + right, game.W)
            self.assertLessEqual(y + h + bottom, game.H)
        game.move_panel(-2000, -2000)
        self.assertEqual(game.panel[:2], (left, top))
        game.render()


class TemplateTests(unittest.TestCase):
    """assets/fishing holds the game's icons, on the real empty slot."""

    def test_empty_slot_is_the_real_one(self):
        strip = cv2.imread(os.path.join(FIXTURES, "inventory_strip.png")).astype(int)
        slot = cv2.imread(os.path.join(ASSETS, "empty_slot.jpg")).astype(int)
        for row in range(5, 8):
            for col in range(5):
                cell = strip[5 + row * 32:37 + row * 32, 110 + col * 32:142 + col * 32]
                self.assertLess(np.abs(cell - slot).mean(), 1.0, (row, col))

    def test_living_fish_templates_are_the_game_icons(self):
        """<Fish>_living.jpg and fish_states/<Fish>_living.png: the same icon
        (the jpg only differs by its quality-100 compression)."""
        states = os.path.join(ASSETS, "fish_states")
        names = sorted(n for n in os.listdir(states) if n.endswith("_living.png"))
        self.assertGreaterEqual(len(names), 24)
        for name in names:
            png = cv2.imread(os.path.join(states, name)).astype(int)
            jpg = cv2.imread(os.path.join(ASSETS, name[:-4] + ".jpg")).astype(int)
            self.assertEqual(jpg.shape, (32, 32, 3), name)
            self.assertLess(np.abs(png - jpg).mean(), 1.0, name)

    def test_templates_are_drawn_on_the_real_slot(self):
        """Every template's corners (outside the icon) are the empty slot."""
        slot = cv2.imread(os.path.join(ASSETS, "empty_slot.jpg")).astype(int)
        for name in sorted(os.listdir(ASSETS)):
            if not re.search(r"_(living|dead|grilled|item)\.(jpg|png)$", name) or name == "Goldring_item.jpg":
                continue                                  # Goldring: no game icon found, kept
            img = cv2.imread(os.path.join(ASSETS, name)).astype(int)
            self.assertEqual(img.shape, (32, 32, 3), name)
            corner = np.abs(img[:3, :3] - slot[:3, :3]).mean()
            self.assertLess(corner, 2.0, name)


class BotPlaysRealMt2Tests(unittest.TestCase):
    """The real FishingBot loop against the textured window."""

    def test_bot_fishes_the_real_window(self):
        game = bench.quick_game("real-mt2")
        _bot, logs = bench.run(game, games=4, timeout=120)
        s = game.stats
        self.assertEqual(s["timeouts"], 0, logs[-12:])
        self.assertGreaterEqual(s["caught"], 4, logs[-12:])
        self.assertLessEqual(s["misses"], 2)

    def test_realistic_session_on_the_real_window(self):
        """Measured timings, diagonal fish, auto handling, quick skip."""
        game = bench.realistic_game(skin="real-mt2", seed=7)
        _bot, logs = bench.run(game, bench.realistic_config(game), games=5, timeout=150)
        s = game.stats
        self.assertGreaterEqual(s["games"], 5, logs[-12:])
        self.assertEqual(s["timeouts"], 0, logs[-12:])
        self.assertLessEqual(s["misses"], 2, logs[-12:])         # an occasional late click under load


if __name__ == "__main__":
    unittest.main()

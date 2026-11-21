"""Detection on frames of a real client (tests/fixtures/real_client): a
screen recording of a player fishing by hand on an official-style client.

The frames went through the recording's video compression, so colors are a
little softer than a live capture; everything checked here works on them
with the stock settings. Labels are in labels.json (the ring geometry was
measured on the drawn ring; fish positions were checked by eye)."""

import json
import os
import unittest

import cv2

from _stubs import install, FakeWindowManager, FakeMouse, ROOT

install()

from fish_detector import FishDetector  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402
import inventory_layout  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "real_client")
with open(os.path.join(FIXTURES, "labels.json")) as _f:
    LABELS = json.load(_f)
RING = LABELS["ring"]


def image(name):
    img = cv2.imread(os.path.join(FIXTURES, name))
    assert img is not None, name
    return img


def window_bot(frame):
    FishingBot._shared_learned_profile = None
    FishingBot._shared_recast_wait = None
    FishingBot._known_profiles = []
    h, w = frame.shape[:2]
    bot = FishingBot(None, {}, FakeWindowManager(w, h), bait_counter=10, bot_id=0)
    bot.human.mouse = bot.mouse_backend = FakeMouse()
    bot.running = True
    return bot


def calibrate(bot, frame):
    for _ in range(2):                    # a candidate needs a confirming frame
        if bot._try_calibrate(frame, 0.0, adapt=False):
            return True
    return False


class PanelTests(unittest.TestCase):
    def test_panel_is_found_where_it_is(self):
        detector = FishDetector()
        for n, lab in LABELS["window"].items():
            bounds = detector.find_fishing_window_bounds(image(f"window_{int(n):04d}.png"), observe_only=True)
            self.assertIsNotNone(bounds, n)
            for got, want in zip(bounds, lab["panel_bounds"]):
                self.assertLessEqual(abs(got - want), 3, (n, bounds, lab))

    def test_circle_is_aimed_at_the_drawn_ring(self):
        frame = image("window_0231.png")              # first frames of a game: the timer bar is full
        bot = window_bot(frame)
        self.assertTrue(calibrate(bot, frame))
        cx, cy = bot._circle_center
        self.assertLessEqual(abs(cx - RING["center_from_top_left"][0]), 2)
        self.assertLessEqual(abs(cy - RING["center_from_top_left"][1]), 2)
        radius = bot._circle_radius_sq ** 0.5
        self.assertTrue(RING["radius"] - 1 <= radius <= RING["radius"] + 5, radius)

    def test_a_panel_moved_late_in_the_game_keeps_its_circle(self):
        # Frame 1260: the player dragged the panel, and the timer has run
        # down. The measured height there is 172 px instead of 190; the
        # circle (at half the height) was put 9 px too high.
        start = image("window_0231.png")
        bot = window_bot(start)
        self.assertTrue(calibrate(bot, start))
        size = (bot.region.width, bot.region.height)
        moved = image("window_1260.png")
        want = LABELS["window"]["1260"]["panel_bounds"]
        self.assertLess(want[3], size[1] - 10)        # the fixture still shows the short measurement
        self.assertTrue(calibrate(bot, moved))
        self.assertEqual((bot.region.left, bot.region.top), (want[0], want[1]))
        self.assertEqual((bot.region.width, bot.region.height), size)
        cx, cy = bot._circle_center
        self.assertLessEqual(abs(cx - RING["center_from_top_left"][0]), 2.5)
        self.assertLessEqual(abs(cy - RING["center_from_top_left"][1]), 2)


class FishTests(unittest.TestCase):
    def test_fish_positions(self):
        detector = FishDetector()
        for n, lab in LABELS["panel"].items():
            crop = image(f"panel_{int(n):04d}.png")
            bounds = detector.find_fishing_window_bounds(crop, observe_only=True)
            self.assertIsNotNone(bounds, n)
            x, y, w, h = bounds
            active, fish = detector.detect_window_and_fish(crop[y:y + h, x:x + w], observe_only=True)
            self.assertTrue(active, n)
            if lab["fish"] is None:
                self.assertIsNone(fish, f"frame {n}: no fish is visible, found one at {fish}")
                continue
            self.assertIsNotNone(fish, f"frame {n}: fish at {lab['fish']} not found")
            dx, dy = fish[0] + x - lab["fish"][0], fish[1] + y - lab["fish"][1]
            self.assertLessEqual(max(abs(dx), abs(dy)), 6, (n, fish, lab))


class InventoryTests(unittest.TestCase):
    INV = LABELS["inventory"]

    @classmethod
    def setUpClass(cls):
        cls.frame = image("inventory_0360.png")
        cls.tabs = {int(p): tuple(v) for p, v in cls.INV["tabs"].items()}
        h, w = cls.frame.shape[:2]
        cls.bot = window_bot(cls.frame)
        cls.bot.config.update({f"inv_page_{p}_pos": list(v) for p, v in cls.tabs.items()})
        cls.bot._grab = lambda x, y, gw, gh: cls.frame[y:y + gh, x:x + gw].copy()
        cls.strip_rect = cls.bot._inventory_rect()
        cls.strip = cls.bot.capture_inventory_area()

    def slot_center(self, row, col):
        """Slot center in strip coordinates (row/col from 1)."""
        gx, gy = self.INV["grid_top_left"]
        s = self.INV["slot"]
        return (gx + (col - 1) * s + s // 2 - self.strip_rect[0], gy + (row - 1) * s + s // 2 - self.strip_rect[1])

    def test_strip_holds_the_whole_grid(self):
        x, y, w, h = self.strip_rect
        gx, gy = self.INV["grid_top_left"]
        s = self.INV["slot"]
        self.assertLessEqual(x, gx)
        self.assertLessEqual(y, gy)
        self.assertGreaterEqual(x + w, gx + self.INV["cols"] * s)
        self.assertGreaterEqual(y + h, gy + self.INV["rows"] * s)

    def test_active_tab_of_five(self):
        self.assertEqual(inventory_layout.active_tab(self.frame, (0, 0), self.tabs)[0], self.INV["active_tab"])

    def test_empty_slots(self):
        got = self.bot._scan_empty_slots(self.strip)
        self.assertEqual(got, [self.slot_center(r, c) for r, c in self.INV["empty"]])

    def test_no_other_item_is_taken_for_a_template(self):
        # Gear, potions, gems, keys, scrolls... in their real look: none of
        # them may be identified as a fish or item template.
        skip = {tuple(p) for p in self.INV["fish"] + self.INV["gold"] + self.INV["empty"]}
        for row in range(1, self.INV["rows"] + 1):
            for col in range(1, self.INV["cols"] + 1):
                if (row, col) in skip:
                    continue
                cx, cy = self.slot_center(row, col)
                roi = (max(0, cx - 22), max(0, cy - 22), cx + 22, cy + 22)
                match = self.bot.identify_item_in_inventory(self.strip, roi=roi)
                self.assertIsNone(match, f"slot row {row} col {col} taken for {match}")

    def test_recolored_fish_are_not_their_living_kind(self):
        # Brown fish with the shape of a living one: grilled fish (they match
        # the game's grilled icons, 2026-11-20). Never living or dead.
        for row, col in ((2, 2), (4, 1)):
            cx, cy = self.slot_center(row, col)
            roi = (max(0, cx - 22), max(0, cy - 22), cx + 22, cy + 22)
            match = self.bot.identify_item_in_inventory(self.strip, roi=roi)
            self.assertTrue(match is None or match[0].endswith("_grilled.png"), ((row, col), match))

    def test_grilled_fish_are_not_taken_for_living_or_dead(self):
        for row, col in self.INV["fish"]:
            cx, cy = self.slot_center(row, col)
            roi = (max(0, cx - 22), max(0, cy - 22), cx + 22, cy + 22)
            match = self.bot.identify_item_in_inventory(self.strip, roi=roi)
            self.assertTrue(match is None or match[0].endswith(("_grilled.png", "_unsure")),
                            ((row, col), match))

    def test_gold_is_identified(self):
        (row, col), = self.INV["gold"]
        cx, cy = self.slot_center(row, col)
        match = self.bot.identify_item_in_inventory(self.strip, roi=(cx - 22, cy - 22, cx + 22, cy + 22))
        self.assertIsNotNone(match)
        name, (mx, my) = match
        self.assertEqual(name, "Gold_item.jpg")
        self.assertLessEqual(max(abs(mx - cx), abs(my - cy)), 2)


if __name__ == "__main__":
    unittest.main()

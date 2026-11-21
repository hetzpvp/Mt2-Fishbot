"""Detection on frames of a second real client (tests/fixtures/real_client_rescaled):
a tutorial recording of the fishing minigame and the fish jigsaw on an
Italian client, uploaded at 1.5x the game's size and shrunk back to 1:1.

The rescale blurs the picture a little, like a client whose UI is drawn at
another size: this is where the empty-slot threshold had to come down from
0.70 to 0.55 (empty slots score 0.69-0.76 here, 0.81-0.92 on the 1:1 client).
Labels are in labels.json; every one was checked by eye."""

import json
import os
import unittest

import cv2

from _stubs import install, FakeWindowManager, FakeMouse, ROOT

install()

from fish_detector import FishDetector  # noqa: E402
from fishing_bot import FishingBot  # noqa: E402
from jigsaw_detector import JigsawDetector  # noqa: E402
import inventory_layout  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "real_client_rescaled")
with open(os.path.join(FIXTURES, "labels.json")) as _f:
    LABELS = json.load(_f)


def image(name):
    img = cv2.imread(os.path.join(FIXTURES, name))
    assert img is not None, name
    return img


def inventory_bot(frame, tabs):
    FishingBot._shared_learned_profile = None
    FishingBot._known_profiles = []
    h, w = frame.shape[:2]
    bot = FishingBot(None, {f"inv_page_{p}_pos": list(v) for p, v in tabs.items()},
                     FakeWindowManager(w, h), bait_counter=10, bot_id=0)
    bot.human.mouse = bot.mouse_backend = FakeMouse()
    bot._grab = lambda x, y, gw, gh: frame[y:y + gh, x:x + gw].copy()
    return bot


class InventoryTests(unittest.TestCase):
    LAYOUT = LABELS["inventory_layout"]
    TABS = {int(p): tuple(v) for p, v in LAYOUT["tabs"].items()}

    def slot(self, row, col):
        x0, y0 = self.LAYOUT["first_slot_center"]
        return x0 + (col - 1) * self.LAYOUT["slot"], y0 + (row - 1) * self.LAYOUT["slot"]

    def each_frame(self):
        for t, lab in LABELS["inventory"].items():
            frame = image(f"inventory_{float(t):07.2f}.png")
            bot = inventory_bot(frame, self.TABS)
            rect = bot._inventory_rect()
            yield t, lab, frame, bot, rect, bot.capture_inventory_area()

    def test_active_tab(self):
        for t, _lab, frame, *_ in self.each_frame():
            self.assertEqual(inventory_layout.active_tab(frame, (0, 0), self.TABS)[0], self.LAYOUT["active_tab"], t)

    def test_empty_slots_as_catches_arrive(self):
        for t, lab, _frame, bot, rect, strip in self.each_frame():
            got = [(x + rect[0], y + rect[1]) for x, y in bot._scan_empty_slots(strip)]
            self.assertEqual(got, [self.slot(r, c) for r, c in lab["empty"]], t)

    def test_slot_check_agrees(self):
        for t, lab, _frame, bot, rect, strip in self.each_frame():
            empty = {tuple(p) for p in lab["empty"]}
            for row in (1, 2):
                for col in range(1, 6):
                    x, y = self.slot(row, col)
                    occupied = bot._is_item_at_position(strip, x - rect[0], y - rect[1])
                    self.assertEqual(occupied, (row, col) not in empty, (t, row, col))

    def test_new_catches_are_identified_through_their_glow(self):
        # A freshly caught item is drawn with a glowing frame. The catch path
        # looks with fresh=True: a caught fish is alive, and the glow spoils
        # the living / dead / grilled check.
        for t, lab, _frame, bot, rect, strip in self.each_frame():
            for col, name in lab["row1"].items():
                x, y = self.slot(1, int(col))
                x, y = x - rect[0], y - rect[1]
                match = bot.identify_item_in_inventory(strip, roi=(x - 22, y - 22, x + 22, y + 22), fresh=True)
                self.assertIsNotNone(match, (t, col))
                self.assertEqual(match[0], name, (t, col))
                self.assertLessEqual(max(abs(match[1][0] - x), abs(match[1][1] - y)), 2, (t, col, match))


class PageWithoutFishTests(unittest.TestCase):
    """Inventory page IV holds jigsaw puzzle items only. Arrows on it have a
    fish's shape: in gray they match Large_zander at 0.81, above the 0.80
    threshold, and "open" would have right-clicked them. Their colors differ
    (icon color check)."""
    LAB = LABELS["inventory_page4"]

    def setUp(self):
        self.frame = image(self.LAB["file"])
        self.tabs = {int(p): tuple(v) for p, v in self.LAB["tabs"].items()}
        self.bot = inventory_bot(self.frame, self.tabs)
        self.rect = self.bot._inventory_rect()
        self.strip = self.bot.capture_inventory_area()

    def test_nothing_is_identified(self):
        self.assertEqual(inventory_layout.active_tab(self.frame, (0, 0), self.tabs)[0], self.LAB["active_tab"])
        found, ignore = [], set()
        while True:
            match = self.bot.identify_item_in_inventory(self.strip, ignore_positions=ignore)
            if match is None:
                break
            found.append(match)
            ignore.add(match[1])
        self.assertEqual(found, [])

    def test_the_arrows_match_in_gray_but_not_in_color(self):
        x0, y0 = self.LAB["first_slot_center"]
        for col in (1, 2, 3):
            x, y = x0 + (col - 1) * 32 - self.rect[0], y0 + 4 * 32 - self.rect[1]
            roi = (x - 22, y - 22, x + 22, y + 22)
            self.bot.config["item_color_max_de"] = 0           # check off: the old behavior
            # (fresh: without the living / dead / grilled check, which also rejects them)
            match = self.bot.identify_item_in_inventory(self.strip, roi=roi, fresh=True)
            self.assertTrue(match and match[0].endswith("_living.jpg"), (col, match))   # a fish shape
            self.bot.config.pop("item_color_max_de")
            self.assertIsNone(self.bot.identify_item_in_inventory(self.strip, roi=roi), col)

    def test_empty_slots(self):
        x0, y0 = self.LAB["first_slot_center"]
        got = [(x + self.rect[0], y + self.rect[1]) for x, y in self.bot._scan_empty_slots(self.strip)]
        self.assertEqual(got, [(x0 + (c - 1) * 32, y0 + (r - 1) * 32) for r, c in self.LAB["empty"]])


class InventoryCrateTests(unittest.TestCase):
    """The jigsaw refill search finds the puzzle crates in the inventory
    (assets/jigsaw/jigsaw_*_crate_inventory.png: the 32 px inventory icons;
    the puzzle-slot pictures are drawn at another size and never matched
    here), tells the two kinds apart, and takes no other chest for one."""

    LAB = LABELS["crates_in_inventory"]

    @classmethod
    def setUpClass(cls):
        from jigsaw_bot import JigsawBot
        cls.bot = JigsawBot(window_manager=None, config={"jigsaw_dry_run": True})

    def strip(self, name):
        return image(name)[25:333, 10:220]                 # the strip the bot reads (x 1070, y 355)

    def test_each_kind_is_found_in_its_slot(self):
        for name, lab in self.LAB["frames"].items():
            found = self.bot._find_inventory_crates(self.strip(name), {"normal", "special"})
            self.assertEqual(set(found), set(lab["crates"]), name)
            for kind, (x, y) in lab["crates"].items():
                fx, fy = found[kind]
                self.assertLessEqual(max(abs(fx + 10 - x), abs(fy + 25 - y)), 4, (name, kind, found))

    def test_another_chest_is_not_a_crate(self):
        frame = image(self.LAB["none"])
        strip = frame[25:333, 4:220]                       # page IV sits 6 px further left
        self.assertEqual(self.bot._find_inventory_crates(strip, {"normal", "special"}), {})


class JigsawTests(unittest.TestCase):
    BOUNDS = LABELS["board_layout"]["grid_bounds"]

    def test_board_reading(self):
        detector = JigsawDetector({"jigsaw_grid_bounds": list(self.BOUNDS)})
        for t, lab in LABELS["board"].items():
            frame = image(f"board_{float(t):07.2f}.png")
            grid = detector.detect_grid(frame)
            self.assertTrue(grid.grid_found, (t, grid.confidence))
            self.assertEqual(sorted(grid.filled_cells), lab["filled"], t)

    def test_crates_are_seen_in_their_slots(self):
        detector = JigsawDetector({"jigsaw_grid_bounds": list(self.BOUNDS)})
        for t in LABELS["board"]:
            frame = image(f"board_{float(t):07.2f}.png")
            slots = detector.detect_crate_slots(frame, detector.detect_grid(frame))
            self.assertEqual(slots.available, {"normal", "special"}, t)

    def test_piece_on_the_cursor(self):
        # The game draws a held piece next to the cursor tip (seen at its
        # left edge, halfway down); the center is checked as well.
        detector = JigsawDetector({})
        for t, lab in LABELS["held"].items():
            frame = image(f"held_{float(t):07.2f}.png")
            x, y, w, h = lab["box"]
            for where, point in (("left edge", (x + 1, y + h // 2)), ("center", (x + w // 2, y + h // 2))):
                result = detector.detect_cursor_piece(frame, point)
                self.assertEqual(result.piece_id, lab["piece"], (t, where, result))


class FishTests(unittest.TestCase):
    def test_fish_positions(self):
        detector = FishDetector()
        for t, lab in LABELS["panel"].items():
            crop = image(f"panel_{float(t):07.2f}.png")
            bounds = detector.find_fishing_window_bounds(crop, observe_only=True)
            self.assertIsNotNone(bounds, t)
            x, y, w, h = bounds
            active, fish = detector.detect_window_and_fish(crop[y:y + h, x:x + w], observe_only=True)
            self.assertTrue(active, t)
            if lab["fish"] is None:
                self.assertIsNone(fish, t)
                continue
            self.assertIsNotNone(fish, t)
            self.assertLessEqual(max(abs(fish[0] + x - lab["fish"][0]), abs(fish[1] + y - lab["fish"][1])), 6,
                                 (t, fish, lab))


if __name__ == "__main__":
    unittest.main()

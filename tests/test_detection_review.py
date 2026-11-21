"""Regression tests for the detection-path review (2026-11-16): the empty-slot
scan, the slot reading order and the jigsaw cell check. Stub-only: nothing
here touches the desktop."""

import os
import unittest

import cv2
import numpy as np

from _stubs import install, InventoryScreen, ROOT

install()

from fishing_bot import FishingBot  # noqa: E402
from jigsaw_detector import JigsawDetector  # noqa: E402
from test_fishing_bot import make_inventory_bot  # noqa: E402

SLOT = 32
COLS, ROWS = 5, 3


def slot_strip(ox, oy, shift=(0.0, 0.0), item_slots=()):
    """A 200x120 strip with a 5x3 grid of empty slots whose top-left corner
    is (ox, oy), drawn ``shift`` pixels off (fractional DPI scaling draws
    the UI between pixels). ``item_slots`` hold a fish instead."""
    empty = cv2.imread(os.path.join(ROOT, "assets", "fishing", "empty_slot.jpg"))
    fish = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
    frame = np.full((120, 200, 3), 60, np.uint8)
    for r in range(ROWS):
        for c in range(COLS):
            img = fish if r * COLS + c in item_slots else empty
            frame[oy + r * SLOT: oy + r * SLOT + SLOT, ox + c * SLOT: ox + c * SLOT + SLOT] = img
    if shift != (0.0, 0.0):
        frame = cv2.warpAffine(frame, np.float32([[1, 0, shift[0]], [0, 1, shift[1]]]), (200, 120))
    return frame


class EmptySlotScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot, _m, _l = make_inventory_bot(InventoryScreen())

    def test_pixel_exact_grid_is_read_unchanged_at_every_offset(self):
        for ox in range(16):
            for oy in range(16):
                got = self.bot._scan_empty_slots(slot_strip(ox, oy))
                want = [(ox + c * SLOT + 16, oy + r * SLOT + 16) for r in range(ROWS) for c in range(COLS)]
                self.assertEqual(got, want, f"offset ({ox},{oy})")

    def test_a_slot_between_two_pixels_is_counted_once(self):
        # Two neighbouring positions score the same; when they straddled a
        # 16 px bucket edge both were kept and 15 slots read as 30.
        # Off on both axes it scores lower still (about 0.6): found since
        # the threshold went from 0.70 to 0.55.
        for shift in ((0.5, 0.0), (0.0, 0.5), (0.5, 0.5)):
            for ox in range(16):
                for oy in range(16):
                    got = self.bot._scan_empty_slots(slot_strip(ox, oy, shift))
                    self.assertEqual(len(got), COLS * ROWS, f"offset ({ox},{oy}) shift {shift}: {got}")
                    self.assertEqual(len({(x // 16, y // 16) for x, y in got}), COLS * ROWS)

    def test_occupied_slots_are_not_counted(self):
        frame = slot_strip(3, 5, item_slots={0, 7, 14})
        got = self.bot._scan_empty_slots(frame)
        self.assertEqual(len(got), COLS * ROWS - 3)
        self.assertNotIn((3 + 16, 5 + 16), got)

    def test_scan_and_slot_check_agree(self):
        frame = slot_strip(3, 5, item_slots={0, 7})
        empty = set(self.bot._scan_empty_slots(frame))
        for r in range(ROWS):
            for c in range(COLS):
                center = (3 + c * SLOT + 16, 5 + r * SLOT + 16)
                self.assertEqual(self.bot._is_item_at_position(frame, *center), center not in empty, center)


class IconColorCheckTests(unittest.TestCase):
    """The icon color check must not reject a real icon drawn a little
    differently (another client, a scaled or compressed capture), and must
    reject one of the same shape in other colors."""

    DISTORTIONS = {
        "darker 8%": lambda i: np.clip(i * 0.92, 0, 255).astype(np.uint8),
        "brighter 8%": lambda i: np.clip(i * 1.08, 0, 255).astype(np.uint8),
        "gamma 0.9": lambda i: np.clip(255 * (i / 255.0) ** 0.9, 0, 255).astype(np.uint8),
        "gamma 1.1": lambda i: np.clip(255 * (i / 255.0) ** 1.1, 0, 255).astype(np.uint8),
        "rescaled 1.5x": lambda i: cv2.resize(cv2.resize(i, None, fx=1.5, fy=1.5), (i.shape[1], i.shape[0]),
                                              interpolation=cv2.INTER_AREA),
        "jpeg 60": lambda i: cv2.imdecode(cv2.imencode(".jpg", i, [cv2.IMWRITE_JPEG_QUALITY, 60])[1], 1),
    }

    @classmethod
    def setUpClass(cls):
        cls.bot, _m, _l = make_inventory_bot(InventoryScreen())
        cls.names = sorted(cls.bot._load_template_cache())
        cls.slot = cv2.imread(os.path.join(ROOT, "assets", "fishing", "empty_slot.jpg"))

    def frame_with(self, icon):
        frame = np.zeros((72, 72, 3), np.uint8)
        frame[:] = self.slot[16, 16]
        frame[20:20 + icon.shape[0], 20:20 + icon.shape[1]] = icon
        return frame

    def icon(self, name):
        return cv2.imread(os.path.join(ROOT, "assets", "fishing", name))

    def test_distorted_icons_are_not_rejected(self):
        for name in self.names:
            icon = self.icon(name)
            for what, distort in self.DISTORTIONS.items():
                if what == "jpeg 60" and name.endswith("_grilled.png"):
                    # Small, strongly colored icons (Crab, Stint, Yabby: dE
                    # 15-25) lose their color to JPEG. A grilled fish is kept
                    # whether or not it is recognized; the templates are there
                    # so it is never taken for a living or dead one.
                    continue
                frame = distort(self.frame_with(icon))
                t = self.bot._load_template_cache()[name][0]
                r = cv2.matchTemplate(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), t, cv2.TM_CCOEFF_NORMED)
                _, score, _, (x, y) = cv2.minMaxLoc(r)
                self.assertTrue(self.bot._colors_match(frame, x, y, name, score), (name, what))

    def test_same_shape_in_other_colors_is_rejected(self):
        rejected = 0
        for name in self.names:
            icon = self.icon(name)
            hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
            hsv[..., 0] = (hsv[..., 0].astype(int) + 60) % 180          # hue turned by 120 degrees
            other = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            frame = self.frame_with(other)
            t = self.bot._load_template_cache()[name][0]
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(
                cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), t, cv2.TM_CCOEFF_NORMED))
            rejected += not self.bot._colors_match(frame, x, y, name, score)
        # Gray-ish icons (white dye, rings) hardly change with the hue.
        self.assertGreaterEqual(rejected, len(self.names) * 0.75, rejected)

    def test_the_check_can_be_switched_off(self):
        name = "Carp_living.jpg"
        hsv = cv2.cvtColor(self.icon(name), cv2.COLOR_BGR2HSV)
        hsv[..., 0] = (hsv[..., 0].astype(int) + 60) % 180
        frame = self.frame_with(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR))
        self.assertFalse(self.bot._colors_match(frame, 27, 27, name, 0.9))
        self.bot.config["item_color_max_de"] = 0
        try:
            self.assertTrue(self.bot._colors_match(frame, 27, 27, name, 0.9))
        finally:
            self.bot.config.pop("item_color_max_de")


class SlotReadingOrderTests(unittest.TestCase):
    def test_rows_top_to_bottom_each_left_to_right(self):
        pts = [(80, 48), (16, 16), (48, 48), (16, 48), (80, 16), (48, 16)]
        self.assertEqual(FishingBot._slot_reading_order(pts),
                         [(16, 16), (48, 16), (80, 16), (16, 48), (48, 48), (80, 48)])

    def test_a_row_across_a_multiple_of_30_stays_one_row(self):
        # y // 30 put (100, 59) in row 1 and (20, 60) in row 2: the slot on
        # the right came first.
        pts = [(100, 59), (20, 60), (60, 60), (20, 92)]
        self.assertEqual(FishingBot._slot_reading_order(pts), [(20, 60), (60, 60), (100, 59), (20, 92)])

    def test_empty(self):
        self.assertEqual(FishingBot._slot_reading_order([]), [])


class JigsawCellCheckTests(unittest.TestCase):
    def test_single_cell_check_matches_the_board_read(self):
        # _cell_is_filled is the same test detect_grid runs on every cell.
        bounds = (10, 6, 240, 160)
        detector = JigsawDetector({"jigsaw_grid_bounds": list(bounds), "jigsaw_grid_check": False})
        rng = np.random.default_rng(7)
        cw, ch = bounds[2] / JigsawDetector.COLS, bounds[3] / JigsawDetector.ROWS
        for _ in range(25):
            frame = np.full((180, 260, 3), 18, np.uint8)
            frame += rng.integers(0, 12, frame.shape, dtype=np.uint8)
            for _p in range(int(rng.integers(0, 10))):
                col, row = int(rng.integers(0, 6)), int(rng.integers(0, 4))
                x, y = int(bounds[0] + col * cw), int(bounds[1] + row * ch)
                color = tuple(int(v) for v in rng.integers(0, 255, 3))
                cv2.rectangle(frame, (x + 4, y + 4), (int(x + cw) - 4, int(y + ch) - 4), color, -1)
            grid = detector.detect_grid(frame)
            self.assertTrue(grid.grid_found)
            for row in range(JigsawDetector.ROWS):
                for col in range(JigsawDetector.COLS):
                    single = detector._cell_is_filled(frame, bounds[0], bounds[1], cw, ch, col, row)
                    self.assertEqual(single, row * JigsawDetector.COLS + col in grid.filled_cells, (row, col))


if __name__ == "__main__":
    unittest.main()

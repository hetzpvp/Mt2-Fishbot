import os
import sys
import unittest

try:
    import cv2  # noqa: F401
    import mss  # noqa: F401
    import pyautogui  # noqa: F401
    HAS_JIGSAW_DEPS = True
    MISSING_DEP = ""
except ModuleNotFoundError as exc:
    HAS_JIGSAW_DEPS = False
    MISSING_DEP = exc.name


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
for path in (SRC,):
    if path not in sys.path:
        sys.path.insert(0, path)

from jigsaw_solver.jigsaw import Jigsaw  # noqa: E402

if HAS_JIGSAW_DEPS:
    from jigsaw_bot import JigsawBot, _mask_from_offsets  # noqa: E402
    from jigsaw_detector import JigsawDetector, JigsawGridResult  # noqa: E402
    import numpy as np  # noqa: E402


@unittest.skipUnless(HAS_JIGSAW_DEPS, f"Jigsaw bot runtime dependency is not installed: {MISSING_DEP}")
class JigsawBotMathTests(unittest.TestCase):
    def test_mask_from_offsets_uses_existing_jigsaw_action_encoding(self):
        mask = _mask_from_offsets([(0, 0), (1, 0), (1, 1)])

        self.assertTrue(mask & Jigsaw._mask((0, 0)))
        self.assertTrue(mask & Jigsaw._mask((1, 0)))
        self.assertTrue(mask & Jigsaw._mask((1, 1)))
        self.assertFalse(mask & Jigsaw._mask((5, 3)))

    def test_target_for_action_maps_column_major_action_to_row_major_centers(self):
        bot = JigsawBot(window_manager=None, config={"jigsaw_dry_run": True})
        centers = [(x, y) for y in range(4) for x in range(6)]
        grid = JigsawGridResult(True, (0, 0, 60, 40), centers, 0, 1.0)

        action = Jigsaw.offset_to_action((2, 3))

        self.assertEqual(bot._target_for_action(grid, action), (2, 3))

    def test_detector_requires_configured_grid_bounds(self):
        detector = JigsawDetector({})
        frame = np.full((120, 180, 3), 80, dtype=np.uint8)

        grid = detector.detect_grid(frame)

        self.assertFalse(grid.grid_found)
        self.assertIsNone(grid.grid_bounds)

    def test_detector_cell_stats_separate_empty_and_filled_cells(self):
        detector = JigsawDetector({})
        frame = np.zeros((80, 120, 3), dtype=np.uint8)
        frame[:] = (18, 18, 18)

        self.assertFalse(detector._cell_is_filled(frame, 0, 0, 120 / 6, 80 / 4, 0, 0))

        frame[8:32, 7:33] = (40, 160, 220)
        self.assertTrue(detector._cell_is_filled(frame, 0, 0, 120 / 6, 80 / 4, 0, 0))

    def test_detector_uses_configured_grid_bounds_for_cell_centers(self):
        detector = JigsawDetector({"jigsaw_grid_bounds": [10, 20, 120, 80]})
        frame = np.zeros((160, 220, 3), dtype=np.uint8)

        grid = detector.detect_grid(frame)

        self.assertTrue(grid.grid_found)
        self.assertEqual(grid.grid_bounds, (10, 20, 120, 80))
        self.assertEqual(grid.cell_centers[0], (20, 30))
        self.assertEqual(grid.cell_centers[-1], (120, 90))

    def test_detector_crate_slot_stats_report_empty_and_available_slots(self):
        detector = JigsawDetector({})
        frame = np.zeros((260, 420, 3), dtype=np.uint8)
        frame[:] = (18, 18, 18)
        grid = JigsawGridResult(True, (20, 40, 264, 176), [], 0, 1.0)
        centers = detector._crate_slot_centers(grid)

        sx, sy = centers["special"]
        frame[sy - 14:sy + 14, sx - 14:sx + 14] = (32, 160, 230)

        slots = detector.detect_crate_slots(frame, grid)

        self.assertIn("special", slots.available)
        self.assertIn("normal", slots.missing)

    def test_cursor_piece_detection_uses_small_cursor_sample_color_votes(self):
        detector = JigsawDetector({})
        asset_path = os.path.join(ROOT, "assets", "jigsaw", "Jigsaw_piece2.png")
        if not os.path.exists(asset_path):
            self.skipTest("jigsaw piece assets are not available")

        piece = cv2.imread(asset_path, cv2.IMREAD_UNCHANGED)
        self.assertIsNotNone(piece)

        frame = np.zeros((192, 192, 3), dtype=np.uint8)
        frame[:] = (18, 18, 18)
        alpha = piece[:, :, 3:4].astype(np.float32) / 255.0
        foreground = piece[:, :, :3].astype(np.float32)
        background = frame[60:124, 60:124].astype(np.float32)
        frame[60:124, 60:124] = (foreground * alpha + background * (1.0 - alpha)).astype(np.uint8)

        # A small orange patch inside the 15px cursor sample should not outvote
        # the surrounding yellow piece pixels.
        frame[94:99, 94:99] = (0, 128, 255)

        result = detector.detect_cursor_piece(frame, (96, 96))

        self.assertEqual(result.piece_id, "piece2")
        self.assertEqual(result.figure_index, 3)
        self.assertGreater(result.confidence, 0.60)


if __name__ == "__main__":
    unittest.main()

"""Finding a dialog and its buttons anywhere in the window, by shape only.

Real frames from a client (diag 20261122_082918, the window around the
"Puzzle complete! You received: ..." result, OK at (211, 123) in these
crops = (511, 403) in the window). No color, brightness level or text may
decide: other servers draw other skins, and labels change with the
language."""

import os
import unittest

import cv2
import numpy as np

from _stubs import install, ROOT

install()

import dialog_watch  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "dialogs")
OK = (211, 123)


def gray(name):
    return cv2.cvtColor(cv2.imread(os.path.join(FIX, name)), cv2.COLOR_BGR2GRAY)


class DialogFinderTests(unittest.TestCase):
    def setUp(self):
        self.before, self.up1, self.up2 = gray("result_before.png"), gray("result_up_1.png"), gray("result_up_2.png")

    def assert_ok_found(self, before, up1, up2):
        found = dialog_watch.find_dialogs(before, up2, up1)
        buttons = [b for _, bs in found for b in bs]
        self.assertTrue(any(abs(x - OK[0]) <= 3 and abs(y - OK[1]) <= 3 for x, y in buttons), found)

    def test_real_result_dialog_ok_is_found(self):
        self.assert_ok_found(self.before, self.up1, self.up2)

    def test_nothing_new_finds_nothing(self):
        self.assertEqual(dialog_watch.find_dialogs(self.before, self.before, self.before), [])
        self.assertEqual(dialog_watch.find_dialogs(self.up1, self.up2, self.up1), [])

    def test_another_skin_light_board_dark_buttons(self):
        # The same dialog with every gray level inverted
        self.assert_ok_found(255 - self.before, 255 - self.up1, 255 - self.up2)

    def test_another_skin_darker_and_flatter(self):
        # A quarter less contrast and darker. The board must still differ
        # from the world behind it by PANEL_CHANGE on most of it (real: 40-60)
        def skin(img):
            return (img.astype(np.float32) * 0.75 + 10).astype(np.uint8)
        self.assert_ok_found(skin(self.before), skin(self.up1), skin(self.up2))

    def test_label_does_not_matter(self):
        # The OK label blanked out (another language): the button is still a button
        up1, up2 = self.up1.copy(), self.up2.copy()
        for img in (up1, up2):
            face = img[OK[1] - 5:OK[1] + 6, OK[0] - 14:OK[0] + 15]
            face[:] = int(np.median(face))
        self.assert_ok_found(self.before, up1, up2)

    def test_dialog_anywhere_in_the_window(self):
        # The same dialog drawn near the top of a bigger window
        def place(img, top, left):
            canvas = np.full((600, 900), int(np.median(self.before)), np.uint8)
            canvas[top:top + img.shape[0], left:left + img.shape[1]] = img
            return canvas
        before = place(self.before, 10, 400)
        found = dialog_watch.find_dialogs(before, place(self.up2, 10, 400), place(self.up1, 10, 400))
        buttons = [b for _, bs in found for b in bs]
        self.assertTrue(any(abs(x - (OK[0] + 400)) <= 3 and abs(y - (OK[1] + 10)) <= 3 for x, y in buttons), found)


if __name__ == "__main__":
    unittest.main()

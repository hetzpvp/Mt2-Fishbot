"""Robustness envelope of the inventory item recognition
(docs/TESTRIG_SCENARIOS.md, tier T2): every item template, drawn in a slot,
under the capture changes real setups make (brightness, gamma, JPEG, 16-bit
color, noise, mild saturation), through the bot's full identify path.

What must never happen is a *wrong action*: an item read as another item,
because keep / drop follows the item. Read as nothing, or as "unsure"
(kept), or as the same fish in another state (dead fish follow their living
fish's setting) is allowed.

A full sweep with stronger changes (hue turns, blur, rescales) found these
cross-item reads, left open on purpose (only seen on synthetic changes so
far, 2026-11-26):
- Large_zander_living <-> Goldfish_dead at hue +-15..20 degrees (a reskin)
- Snake_head_dead -> Lotusfish_dead under a 3x3 blur (DPI scaling blurs)
- 5-7 cross-item reads per rescale 0.8x / 1.5-2.0x (templates are matched
  at 1.0x only)
"""

import os
import unittest

import cv2
import numpy as np

from _stubs import install, InventoryScreen, ROOT

install()

from test_fishing_bot import make_inventory_bot  # noqa: E402


def _hsv(img, s=1.0, v=1.0):
    h = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    h[..., 1] = np.clip(h[..., 1] * s, 0, 255)
    h[..., 2] = np.clip(h[..., 2] * v, 0, 255)
    return cv2.cvtColor(h.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _gamma(img, g):
    return cv2.LUT(img, np.clip(((np.arange(256) / 255.0) ** g) * 255, 0, 255).astype(np.uint8))


def _jpeg(img, q):
    return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1], cv2.IMREAD_COLOR)


def _rgb565(img):
    out = img.copy()
    out[..., 0] &= 0xF8
    out[..., 1] &= 0xFC
    out[..., 2] &= 0xF8
    return out


def _noise(img, sigma):
    rng = np.random.default_rng(1)
    return np.clip(img.astype(np.int16) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)


# Changes a real capture can show without anyone reskinning the game.
REALISTIC = {
    "darker 15%": lambda f: _hsv(f, v=0.85),
    "brighter 15%": lambda f: _hsv(f, v=1.15),
    "brighter 30%": lambda f: _hsv(f, v=1.3),
    "gamma 0.8": lambda f: _gamma(f, 0.8),
    "gamma 1.2": lambda f: _gamma(f, 1.2),
    "saturation 0.8": lambda f: _hsv(f, s=0.8),
    "saturation 1.2": lambda f: _hsv(f, s=1.2),
    "jpeg 90": lambda f: _jpeg(f, 90),
    "jpeg 60": lambda f: _jpeg(f, 60),
    "16-bit color": _rgb565,
    "noise 5": lambda f: _noise(f, 5),
    "noise 10": lambda f: _noise(f, 10),
}


def _fish(name: str) -> str:
    """'Carp_living.jpg' / 'fish_states/Carp_living.png' / 'Carp_unsure' -> 'Carp'."""
    base = os.path.basename(name).split(".")[0]
    for suffix in ("_living", "_dead", "_grilled", "_unsure", "_item"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


class ItemRecognitionEnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot, _m, _l = make_inventory_bot(InventoryScreen())
        cls.names = sorted(cls.bot._load_template_cache())
        cls.slot = cv2.imread(os.path.join(ROOT, "assets", "fishing", "empty_slot.jpg"))

    def _frame(self, name):
        icon = cv2.imread(os.path.join(ROOT, "assets", "fishing", name))
        frame = np.tile(self.slot, (3, 3, 1))
        frame[32:64, 32:64] = cv2.resize(icon, (32, 32)) if icon.shape[:2] != (32, 32) else icon
        return frame

    def _sweep(self, looks):
        wrong, ok = [], 0
        for name in self.names:
            base = self._frame(name)
            for look in looks:
                m = self.bot.identify_item_in_inventory(REALISTIC[look](base))
                if m is None or m[0].endswith("_unsure"):
                    continue                      # not recognized, or kept as unsure
                if _fish(m[0]) != _fish(name):
                    wrong.append(f"{name} under {look} read as {m[0]}")
                else:
                    ok += 1
        return wrong, ok

    # Split in three so the parallel test run spreads them.
    def test_brightness_and_gamma(self):
        wrong, ok = self._sweep(["darker 15%", "brighter 15%", "brighter 30%", "gamma 0.8", "gamma 1.2"])
        self.assertEqual(wrong, [])
        self.assertGreater(ok, len(self.names) * 4)

    def test_saturation_and_compression(self):
        wrong, ok = self._sweep(["saturation 0.8", "saturation 1.2", "jpeg 90", "jpeg 60"])
        self.assertEqual(wrong, [])
        self.assertGreater(ok, len(self.names) * 3)

    def test_color_depth_and_noise(self):
        wrong, ok = self._sweep(["16-bit color", "noise 5", "noise 10"])
        self.assertEqual(wrong, [])
        self.assertGreater(ok, len(self.names) * 2)


if __name__ == "__main__":
    unittest.main()

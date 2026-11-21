"""Dead and grilled fish (2026-11-20). A fish is three items in the game,
each with its own icon: living, dead and grilled. The bot only knew the
living ones; a dead fish matched its living kind in gray but failed the
color check, so dead fish set to drop were never dropped.
Stub-only: nothing here touches the desktop."""

import os
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2
import numpy as np

from _stubs import install, InventoryScreen, ROOT

install()

from fishing_bot import FishingBot  # noqa: E402
from test_fishing_bot import make_inventory_bot  # noqa: E402
from test_real_client import window_bot  # noqa: E402
from utils import item_display_name, normalize_fish_action  # noqa: E402

ASSETS = os.path.join(ROOT, "assets", "fishing")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "dead_fish", "w2_strip.png")
FISH = sorted(f[:-len("_dead.png")] for f in os.listdir(ASSETS) if f.endswith("_dead.png"))

try:
    import qt_gui  # noqa: E402
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False


def state_icon(fish, state):
    """The exact game icon of a fish state, on an empty slot (32x32)."""
    path = (os.path.join(ASSETS, "fish_states", f"{fish}_living.png") if state == "living"
            else os.path.join(ASSETS, f"{fish}_{state}.png"))
    img = cv2.imread(path)
    assert img is not None, path
    return img


def strip_with(icon):
    """A 5x3 strip of empty slots with ``icon`` in the middle one; returns
    (strip, center of that slot)."""
    slot = cv2.imread(os.path.join(ASSETS, "empty_slot.jpg"))
    strip = np.tile(slot, (3, 5, 1))
    strip[32:64, 64:96] = icon
    return strip, (80, 48)


def expected_name(fish, state):
    return f"{fish}_living.jpg" if state == "living" else f"{fish}_{state}.png"


class AssetTests(unittest.TestCase):
    def test_every_fish_has_three_states(self):
        self.assertEqual(len(FISH), 24)
        for fish in FISH:
            for state in ("living", "dead", "grilled"):
                self.assertEqual(state_icon(fish, state).shape, (32, 32, 3), (fish, state))
            self.assertTrue(os.path.exists(os.path.join(ASSETS, f"{fish}_living.jpg")), fish)

    def test_dead_and_grilled_templates_are_loaded(self):
        bot = window_bot(np.zeros((100, 100, 3), np.uint8))
        names = bot._load_template_cache()
        for fish in FISH:
            self.assertIn(f"{fish}_dead.png", names)
            self.assertIn(f"{fish}_grilled.png", names)
        # The state reference icons are not templates of their own
        self.assertFalse(any(n.endswith("_living.png") for n in names))

    def test_display_names(self):
        self.assertEqual(item_display_name("Zander_dead.png"), "Zander (dead)")
        self.assertEqual(item_display_name("Carp_grilled.png"), "Carp (grilled)")
        self.assertEqual(item_display_name("Carp_living.jpg"), "Carp")
        self.assertEqual(item_display_name("Gold_item.jpg"), "Gold")
        self.assertEqual(item_display_name("Carp_unsure"), "Carp (dead or alive?)")


class RealCaptureTests(unittest.TestCase):
    """The strip of the 2026-11-20 diagnostic run: a dead Zander and a stack
    of two dead Mandarinfish, both set to drop and never dropped."""

    @classmethod
    def setUpClass(cls):
        cls.strip = cv2.imread(FIXTURE)
        assert cls.strip is not None
        cls.bot = window_bot(cls.strip)

    def test_dead_fish_are_identified(self):
        found, ignore = [], set()
        while True:
            match = self.bot.identify_item_in_inventory(self.strip, ignore_positions=ignore)
            if match is None:
                break
            found.append(match)
            ignore.add(match[1])
        self.assertEqual(sorted(found), [("Mandarinfish_dead.png", (126, 53)), ("Zander_dead.png", (254, 21))])

    def test_startup_drops_them(self):
        actions = {"Zander_dead.png": "drop", "Mandarinfish_dead.png": "drop",
                   "Zander_living.jpg": "keep", "Mandarinfish_living.jpg": "keep"}
        bot, _m, _l = make_inventory_bot(InventoryScreen())
        bot.capture_inventory_area = lambda: self.strip
        bot.running = True
        bot._ignored_positions.clear()
        handled = []
        # A handled item is gone from the page for the next look
        bot._handle_item = lambda fn, x, y, a, label="": (handled.append((fn, (x, y), a)),
                                                          bot._ignored_positions.add((x, y)))[0] or True
        bot._wait_while_paused = lambda: len(handled) < 5
        bot._startup_process_page(actions)
        self.assertEqual(sorted(handled), [("Mandarinfish_dead.png", (126, 53), "drop"),
                                           ("Zander_dead.png", (254, 21), "drop")])

    def test_drop_is_sure_of_a_dead_fish(self):
        bot, _m, _l = make_inventory_bot(InventoryScreen())
        bot.capture_inventory_area = lambda: self.strip
        self.assertTrue(bot._drop_match_sure("Zander_dead.png", 254, 21)[0])
        # ... and refuses to drop it as the living fish
        sure, why = bot._drop_match_sure("Zander_living.jpg", 254, 21)
        self.assertFalse(sure)


class StateTests(unittest.TestCase):
    """Every fish in every state, alone in a strip of empty slots."""

    @classmethod
    def setUpClass(cls):
        cls.bot = window_bot(np.zeros((100, 100, 3), np.uint8))

    def test_each_state_is_identified(self):
        for fish in FISH:
            for state in ("living", "dead", "grilled"):
                strip, center = strip_with(state_icon(fish, state))
                match = self.bot.identify_item_in_inventory(strip)
                self.assertIsNotNone(match, (fish, state))
                self.assertEqual(match[0], expected_name(fish, state), (fish, state))
                self.assertLessEqual(max(abs(match[1][0] - center[0]), abs(match[1][1] - center[1])), 1,
                                     (fish, state, match))

    def test_a_blurred_living_fish_is_never_dead_or_grilled(self):
        # Blur takes a living icon's fine detail away, which is what makes
        # the dead ones look dead. Unsure (kept) is fine, another state not.
        for fish in FISH:
            strip, _ = strip_with(state_icon(fish, "living"))
            match = self.bot.identify_item_in_inventory(cv2.GaussianBlur(strip, (3, 3), 0))
            self.assertIsNotNone(match, fish)
            # Never a sure wrong answer: this fish alive, or kept as unsure
            # (a blurred Snake head has Large zander's shape, gray 0.885 > 0.83)
            self.assertTrue(match[0] == f"{fish}_living.jpg" or match[0].endswith("_unsure"), (fish, match))

    def test_a_darker_fresh_catch_keeps_its_state(self):
        for fish in FISH:
            for state in ("living", "dead"):
                strip, _ = strip_with(state_icon(fish, state))
                dark = np.clip(strip.astype(np.float32) * 0.8, 0, 255).astype(np.uint8)
                match = self.bot.identify_item_in_inventory(dark)
                self.assertIsNotNone(match, (fish, state))
                self.assertIn(match[0], (expected_name(fish, state), f"{fish}_unsure"), (fish, state))

    def test_unsure_and_grilled_are_kept(self):
        actions = {f"{f}_{s}": "drop" for f in FISH for s in ("living.jpg", "dead.png")}
        for fish in FISH:
            self.assertEqual(normalize_fish_action(actions.get(f"{fish}_unsure")), "keep")
            self.assertEqual(normalize_fish_action(actions.get(f"{fish}_grilled.png")), "keep")

    def test_unsure_when_no_state_is_clearly_nearest(self):
        bot = window_bot(np.zeros((100, 100, 3), np.uint8))
        strip, (cx, cy) = strip_with(state_icon("Carp", "dead"))
        states = bot._load_fish_states()
        saved = FishingBot._STATE_SURE_RATIO
        try:
            FishingBot._STATE_SURE_RATIO = -1.0         # nothing is ever sure enough
            bot.config["fish_actions"] = {"Carp_dead.png": "drop", "Carp_living.jpg": "keep"}
            self.assertEqual(bot._resolve_fish_state(strip, cx, cy, "Carp_dead.png"), "Carp_unsure")
            # Same action for both: it does not matter which one it is
            bot.config["fish_actions"] = {"Carp_dead.png": "drop", "Carp_living.jpg": "drop"}
            self.assertIn(bot._resolve_fish_state(strip, cx, cy, "Carp_dead.png"),
                          ("Carp_living.jpg", "Carp_dead.png"))
        finally:
            FishingBot._STATE_SURE_RATIO = saved
        self.assertIn("Carp", states)


class KilledByTheRightClickTests(unittest.TestCase):
    """A right-click on a living fish (open, or the open-first step of a
    drop) can leave its dead kind in the slot. That dead fish follows its
    living fish: dropped when the fish is set to drop, kept otherwise."""

    def _bot(self, living_action, dead_action="keep"):
        bot, _m, _l = make_inventory_bot(InventoryScreen(), {
            "confirm_button_pos": [10, 10],
            "fish_actions": {"Zander_living.jpg": living_action, "Zander_dead.png": dead_action}})
        bot.running = True
        bot._focus = lambda force=False: True
        bot._is_item_at_position = lambda frame, x, y, radius=10: True    # still in its slot
        bot._slot_changed = lambda before, x, y: False
        bot._item_in_slot = lambda x, y: "Zander_dead.png"
        bot._t_open_wait = bot._t_dead_check = 0.0
        sure_calls, drops = [], []
        bot._drop_match_sure = lambda fn, x, y: sure_calls.append(fn) or (True, "test")
        bot._drop_sequence = lambda name, *a: drops.append(name)
        return bot, sure_calls, drops

    def test_drop_goes_on_with_the_dead_fish(self):
        bot, sure_calls, drops = self._bot("drop")
        self.assertTrue(bot._handle_item("Zander_living.jpg", 40, 40, "drop"))
        self.assertEqual(sure_calls, ["Zander_dead.png"])
        self.assertEqual(drops, ["Zander (dead)"])

    def test_dead_fish_of_a_dropped_fish_is_dropped(self):
        bot, _sure, _drops = self._bot("drop", dead_action="keep")    # hand-edited: overruled
        self.assertEqual(bot.config["fish_actions"]["Zander_dead.png"], "drop")

    def test_dead_fish_of_an_opened_fish_stays(self):
        bot, sure_calls, drops = self._bot("open", dead_action="drop")  # hand-edited: overruled
        self.assertEqual(bot.config["fish_actions"]["Zander_dead.png"], "keep")
        self.assertTrue(bot._handle_item("Zander_living.jpg", 40, 40, "open"))
        self.assertEqual((sure_calls, drops), ([], []))
        self.assertTrue(any(abs(x - 40) < 10 and abs(y - 40) < 10 for x, y in bot._ignored_positions))


@unittest.skipUnless(HAS_QT, "PySide6 not available")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_dead_fish_follow_their_living_fish(self):
        self.assertEqual(len(qt_gui.DEAD_DISPLAY), 24)
        modal = qt_gui.FishModal({"Zander_dead.png": "drop", "Carp_living.jpg": "drop",
                                  "Ayu_living.jpg": "open"})
        self.assertNotIn("Carp_dead.png", modal._tiles)                 # no dead tiles
        saved = []
        modal.saved.connect(saved.append)
        modal._save()
        self.assertEqual(saved[0]["Carp_dead.png"], "drop")             # follows drop
        self.assertEqual(saved[0]["Ayu_dead.png"], "keep")              # cannot be opened
        self.assertEqual(saved[0]["Zander_dead.png"], "keep")           # its own choice is gone
        modal._set_all([a for _, a, _ in qt_gui.FISH_DISPLAY], "drop")
        modal._save()
        self.assertEqual(saved[1]["Mandarinfish_dead.png"], "drop")

    def test_bot_derives_dead_actions(self):
        from utils import with_dead_fish_actions
        out = with_dead_fish_actions({"Carp_living.jpg": "keep", "Carp_dead.png": "drop",
                                      "Zander_living.jpg": "drop", "Ayu_living.jpg": "open"})
        self.assertEqual(out["Carp_dead.png"], "keep")
        self.assertEqual(out["Zander_dead.png"], "drop")
        self.assertEqual(out["Ayu_dead.png"], "keep")


if __name__ == "__main__":
    unittest.main()

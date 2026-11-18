"""Regression tests of the whole-codebase audit (2026-11-17). Stub-only:
nothing here touches the desktop."""

import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2
import numpy as np

from _stubs import install, InventoryScreen, ROOT

install()

from fishing_bot import FishingBot  # noqa: E402
from test_fishing_bot import make_inventory_bot  # noqa: E402
import setup_checks  # noqa: E402

ASSETS = os.path.join(ROOT, "assets", "fishing")

try:
    import qt_gui  # noqa: E402
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False


def _template(name):
    gray = cv2.cvtColor(cv2.imread(os.path.join(ASSETS, name)), cv2.COLOR_BGR2GRAY)[7:-7, 7:-7]
    return gray, gray.shape[1] >> 1, gray.shape[0] >> 1


class StartupSlotMergeTests(unittest.TestCase):
    """A Redfeather set to drop; Large_zander (keep) scores ~0.88 on it too,
    a pixel or two up-left. Both hits are one slot whose item is Redfeather.
    The merge used to probe only the buckets left of / above a hit, so at 2
    of every 16 x positions the keep hit became a slot of its own and the
    Redfeather was never dropped."""

    ACTIONS = {"Large_zander_living.jpg": "keep", "Redfeather_living.jpg": "drop"}

    def setUp(self):
        self._saved_cache = FishingBot._template_cache
        FishingBot._template_cache = {n: _template(n) for n in self.ACTIONS}
        self.icon = cv2.imread(os.path.join(ASSETS, "Redfeather_living.jpg"))

    def tearDown(self):
        FishingBot._template_cache = self._saved_cache

    def _bot_over(self, ox):
        frame = np.full((120, 200, 3), 40, np.uint8)
        frame[20:52, 20 + ox:52 + ox] = self.icon
        bot, _m, _l = make_inventory_bot(InventoryScreen())
        bot.capture_inventory_area = lambda: frame
        bot.running = True
        bot._ignored_positions.clear()
        return bot

    def test_drop_item_is_handled_at_every_position(self):
        for ox in range(32):
            bot = self._bot_over(ox)
            handled = []
            bot._handle_item = lambda fn, x, y, a, label="": handled.append((fn, a)) or True
            bot._wait_while_paused = lambda: not handled
            bot._startup_process_page(self.ACTIONS)
            self.assertEqual(handled, [("Redfeather_living.jpg", "drop")], f"icon at x offset {ox}")

    def test_existing_item_is_ignored_once(self):
        for ox in range(32):
            bot = self._bot_over(ox)
            bot._scan_existing_inventory()
            self.assertEqual(len(bot._ignored_positions), 1, f"icon at x offset {ox}: {bot._ignored_positions}")


class CastWhilePausedTests(unittest.TestCase):
    def test_no_cast_is_sent_while_paused(self):
        bot, _mouse, _logs = make_inventory_bot(InventoryScreen())
        sent = []

        class Keyboard:
            def press(self, k):
                sent.append(("press", k))

            def release(self, k):
                sent.append(("release", k))

        bot.keyboard_controller = bot.human.keyboard_controller = Keyboard()
        bot.paused = True
        self.assertFalse(bot.bait_and_cast())
        self.assertEqual(sent, [])
        self.assertIsNone(bot._cast_error)      # not a keyboard failure
        self.assertTrue(bot._was_paused)        # the resume re-reads the inventory
        bot.paused = False
        self.assertTrue(bot.bait_and_cast())
        self.assertTrue(sent)


class SharedConstantTests(unittest.TestCase):
    def test_setup_check_uses_the_bots_empty_slot_threshold(self):
        self.assertEqual(setup_checks.EMPTY_SLOT_THRESHOLD, FishingBot._EMPTY_SLOT_THRESHOLD)
        self.assertEqual(setup_checks.TEMPLATE_BORDER, FishingBot._template_border_crop)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class ConfigLocationTests(unittest.TestCase):
    """bot_config.json lives next to the program, not in the working directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prog = os.path.join(self.tmp.name, "prog")
        self.user = os.path.join(self.tmp.name, "user", "diagnostics")
        self.cwd = os.path.join(self.tmp.name, "elsewhere")
        for d in (self.prog, self.cwd):
            os.makedirs(d)
        self._env = os.environ.pop("FISHBOT_CONFIG_DIR", None)
        self._old_cwd = os.getcwd()
        os.chdir(self.cwd)
        qt_gui._config_path_cache = None
        self.patches = [mock.patch.object(qt_gui.diag, "program_dir", lambda: self.prog),
                        mock.patch.object(qt_gui.diag, "user_dir", lambda: self.user)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self._old_cwd)
        if self._env is not None:
            os.environ["FISHBOT_CONFIG_DIR"] = self._env
        qt_gui._config_path_cache = None
        self.tmp.cleanup()

    def test_next_to_the_program_whatever_the_working_directory(self):
        self.assertEqual(qt_gui.config_file_path(), os.path.join(self.prog, "bot_config.json"))

    def test_old_config_in_the_working_directory_is_carried_over(self):
        with open(os.path.join(self.cwd, "bot_config.json"), "w") as f:
            f.write('{"bait_quantity": 123}')
        path = qt_gui.config_file_path()
        self.assertEqual(path, os.path.join(self.prog, "bot_config.json"))
        with open(path) as f:
            self.assertIn("123", f.read())

    def test_read_only_program_folder_falls_back_to_the_user_folder(self):
        with mock.patch.object(qt_gui.diag, "_can_write", lambda _path: False):
            self.assertEqual(qt_gui.config_file_path(),
                             os.path.join(os.path.dirname(self.user), "bot_config.json"))

    def test_override(self):
        os.environ["FISHBOT_CONFIG_DIR"] = self.cwd
        try:
            self.assertEqual(qt_gui.config_file_path(), os.path.join(self.cwd, "bot_config.json"))
        finally:
            del os.environ["FISHBOT_CONFIG_DIR"]


class SolverCacheLocationTests(unittest.TestCase):
    def setUp(self):
        from jigsaw_solver import cache_location
        self.cl = cache_location
        self.tmp = tempfile.TemporaryDirectory()
        self.prog = os.path.join(self.tmp.name, "prog", "data", "cache", "jigsaw")
        self.user = os.path.join(self.tmp.name, "user", "jigsaw")
        self._env = os.environ.pop("JIGSAW_SOLVER_CACHE_DIR", None)
        self.patches = [mock.patch.object(cache_location, "program_cache_dir", lambda: self.prog),
                        mock.patch.object(cache_location, "user_cache_dir", lambda: self.user)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        if self._env is not None:
            os.environ["JIGSAW_SOLVER_CACHE_DIR"] = self._env
        self.tmp.cleanup()

    def test_program_folder_when_writable(self):
        self.assertEqual(self.cl.cache_dir(create=True), self.prog)

    def test_user_folder_when_the_program_folder_is_read_only(self):
        with mock.patch.object(self.cl, "_can_write", lambda folder: folder != self.prog):
            self.assertEqual(self.cl.cache_dir(create=True), self.user)

    def test_an_existing_table_is_used_where_it_is(self):
        os.makedirs(self.user)
        open(os.path.join(self.user, self.cl.cache_file_name()), "wb").close()
        self.assertEqual(self.cl.cache_dir(), self.user)


class SolverMemoryTests(unittest.TestCase):
    def test_tables_are_not_allocated_before_a_load(self):
        from jigsaw_solver.deterministic import Deterministic
        solver = Deterministic()
        self.assertIsNone(solver.dsts)
        self.assertIsNone(solver.actions)

    def test_a_failed_save_does_not_raise(self):
        from jigsaw_solver.deterministic import Deterministic
        solver = Deterministic()
        solver.dsts = np.zeros(6)
        solver.actions = np.zeros(6, np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "file")
            open(blocker, "w").close()            # a file where the folder should be
            self.assertFalse(solver.save_cache(os.path.join(blocker, "sub", "cache.npz")))


class InterceptionDeviceTests(unittest.TestCase):
    """interception_is_mouse() is only a range check: the device chosen
    is the first one that has a hardware id."""

    def _driver(self, present):
        from interception_wrapper import InterceptionDriver
        drv = InterceptionDriver.__new__(InterceptionDriver)
        drv._ctx = 0
        drv._mouse_device = None
        drv.is_mouse = lambda dev: 11 <= dev <= 20
        drv.hardware_id = lambda dev: "HID\\VID_046D" if dev in present else ""
        return drv

    def test_first_present_mouse(self):
        self.assertEqual(self._driver({13, 15}).find_default_mouse(), 13)

    def test_no_readable_id_keeps_the_old_choice(self):
        self.assertEqual(self._driver(set()).find_default_mouse(), 11)

    def test_preferred_device_wins(self):
        self.assertEqual(self._driver({13}).find_default_mouse(17), 17)


if __name__ == "__main__":
    unittest.main()

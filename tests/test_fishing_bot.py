import os
import threading
import time
import unittest

import cv2
import numpy as np

from _stubs import install, hsv_to_bgr, SimScreen, InventoryScreen, FakeWindowManager, FakeMouse, ROOT

install()

from fishing_bot import FishingBot  # noqa: E402
import inventory_layout  # noqa: E402
from utils import PriorityInputLock  # noqa: E402

OFFICIAL_PANEL = hsv_to_bgr(102, 200, 220)
OFFICIAL_FISH = hsv_to_bgr(104, 138, 120)


def make_bot(screen, config=None):
    FishingBot._shared_learned_profile = None
    FishingBot._shared_recast_wait = None
    FishingBot._known_profiles = []
    bot = FishingBot(None, dict(config or {}), FakeWindowManager(), bait_counter=10, bot_id=0)
    mouse = FakeMouse()
    bot.human.mouse = mouse
    bot.mouse_backend = mouse
    bot._grab = screen.grab
    bot.running = True
    logs = []
    bot.on_status_update = logs.append
    return bot, mouse, logs


def assert_clicks_on_fish(test, screen, mouse):
    """Every click must land inside the click circle."""
    x, y, w, h = SimScreen.PANEL
    cx, cy = x + w // 2, y + h // 2
    test.assertGreater(len(mouse.clicks), 0, "bot never clicked")
    for (px, py), _button in mouse.clicks:
        test.assertLess((px - cx) ** 2 + (py - cy) ** 2, SimScreen.RADIUS ** 2 + 1)


class OfficialClientTests(unittest.TestCase):
    def test_calibrates_and_clicks_fish_in_circle(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen)

        self.assertFalse(bot.wait_for_minigame_window(timeout=0.2))  # panel closed
        screen.panel_open = True
        self.assertTrue(bot.wait_for_minigame_window(timeout=1.0))
        self.assertEqual((bot.region.left, bot.region.top, bot.region.width, bot.region.height),
                         SimScreen.PANEL)
        self.assertEqual(bot._circle_radius_sq, 67 * 67)
        self.assertEqual(bot.detector.profile.name, "official")

        # This test steps a virtual clock; speed prediction needs real time.
        bot._TRACK_MIN_SPAN = float("inf")
        # The simulated ring never turns red and the virtual clock never adds up
        # to the ring gate's proof time: test the circle rule (tests/test_ring_gate.py
        # covers the red-ring rule).
        bot._ring_turns_red = False
        outside_clicks = 0
        for _ in range(150):
            screen.t += 0.02
            before = len(mouse.clicks)
            active, pos = bot.atomic_capture_and_click()
            self.assertTrue(active)
            if len(mouse.clicks) > before:
                # Clicked exactly on the fish, and only while it is in the circle
                self.assertTrue(screen.fish_in_circle())
                fx, fy = screen.fish_center()
                (px, py), _ = mouse.clicks[-1]
                self.assertLessEqual(max(abs(px - fx), abs(py - fy)), 2)
            elif pos is not None:
                outside_clicks += 1
        self.assertEqual(outside_clicks, 0)
        assert_clicks_on_fish(self, screen, mouse)

    def test_panel_that_moved_is_recalibrated(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _mouse, logs = make_bot(screen)
        screen.panel_open = True
        self.assertTrue(bot.wait_for_minigame_window(timeout=1.0))

        SimScreen.PANEL = (60, 300, 280, 220)  # player dragged the panel
        try:
            bot._RECALIBRATE_AFTER = 0.1
            self.assertTrue(bot.wait_for_minigame_window(timeout=2.0))
            self.assertEqual((bot.region.left, bot.region.top), (60, 300))
            self.assertTrue(any("Re-calibrated" in m for m in logs))
        finally:
            SimScreen.PANEL = (260, 150, 280, 220)

    def test_bite_timeout_adapts(self):
        bot, _, _ = make_bot(SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH))
        self.assertEqual(bot._bite_timeout(), 6.0)
        bot.consecutive_failures = 1
        self.assertEqual(bot._bite_timeout(), 9.0)
        bot.consecutive_failures = 2
        self.assertEqual(bot._bite_timeout(), 13.5)
        bot.consecutive_failures = 4
        self.assertEqual(bot._bite_timeout(), 30.375)  # past the old 30 s cap: slow servers
        bot._longest_bite = 100.0
        self.assertEqual(bot._bite_timeout(), 60.0)    # growth is capped (timing_minigame_timeout_cap)
        bot._longest_bite = 0.0
        bot.consecutive_failures = 0
        bot._longest_bite = 10.0                      # slow-bite server seen once
        self.assertEqual(bot._bite_timeout(), 16.0)


class ReskinnedClientTests(unittest.TestCase):
    """A client with a green panel and a red fish — nothing matches the stock colors."""

    def test_adapts_and_clicks(self):
        screen = SimScreen(hsv_to_bgr(60, 180, 200), hsv_to_bgr(2, 210, 200))
        bot, mouse, logs = make_bot(screen, {"adaptive_detection": True})
        bot._ADAPTIVE_AFTER = 0.0
        learned = []
        bot.on_profile_learned = learned.append

        bot._reference_frame = bot.capture_full_window()   # taken just before the cast
        screen.panel_open = True
        self.assertTrue(bot.wait_for_minigame_window(timeout=2.0))
        self.assertTrue(bot.detector.profile.learned)
        for got, want in zip((bot.region.left, bot.region.top, bot.region.width, bot.region.height),
                             SimScreen.PANEL):
            self.assertLessEqual(abs(got - want), 3)

        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 3.0:
            screen.t = time.perf_counter() - t0
            active, _ = bot.atomic_capture_and_click()
            self.assertTrue(active)
            if bot.detector.profile_updated:
                bot._publish_learned_profile()
            time.sleep(0.004)

        assert_clicks_on_fish(self, screen, mouse)
        self.assertEqual(len(learned), 1)
        self.assertIsNotNone(learned[0]["fish_lower"])
        self.assertIs(FishingBot._shared_learned_profile, bot.detector.profile)

        # A second window of the same client reuses the profile instead of relearning
        bot2, _, _ = make_bot(screen)
        FishingBot._shared_learned_profile = bot.detector.profile
        bot2._ADAPTIVE_AFTER = 0.0
        self.assertTrue(bot2.wait_for_minigame_window(timeout=2.0))
        self.assertIs(bot2.detector.profile, bot.detector.profile)

    def test_disabled_adaptive_does_nothing(self):
        screen = SimScreen(hsv_to_bgr(60, 180, 200), hsv_to_bgr(2, 210, 200))
        bot, _mouse, _ = make_bot(screen, {"adaptive_detection": False})
        bot._ADAPTIVE_AFTER = 0.0
        bot._reference_frame = bot.capture_full_window()
        screen.panel_open = True
        self.assertFalse(bot.wait_for_minigame_window(timeout=0.4))
        self.assertFalse(bot.region_auto_calibrated)


def make_inventory_bot(screen, config=None, tabs=True):
    """A bot in front of an InventoryScreen; its clicks reach the screen."""
    cfg = dict(screen.config() if tabs else {})
    cfg.update(config or {})
    bot, mouse, logs = make_bot(screen, cfg)
    bot.window_manager.rect = (0, 0, screen.W, screen.H)

    def down(button="left"):
        mouse.clicks.append((mouse.pos, button))
        screen.click(mouse.pos, button)

    mouse.mouse_down = down
    mouse.click = down
    return bot, mouse, logs


class InventoryStripTests(unittest.TestCase):
    """The inventory strip is the grid below the configured page tabs."""

    def test_strip_starts_below_the_tabs(self):
        screen = InventoryScreen()
        bot, _mouse, _logs = make_inventory_bot(screen)
        x, y, w, h = bot._inventory_rect()
        lowest_tab = max(p[1] for p in screen.TABS.values())
        self.assertGreater(y, lowest_tab)
        self.assertLessEqual(y, screen.GRID[1])                       # no grid row is cut off
        self.assertLessEqual(x, screen.GRID[0])
        self.assertGreaterEqual(x + w, screen.GRID[0] + screen.COLS * screen.SLOT)
        grid_bottom = screen.GRID[1] + screen.ROWS * screen.SLOT
        self.assertTrue(grid_bottom <= y + h <= grid_bottom + 16)     # ends with the grid
        self.assertEqual(bot.capture_inventory_area().shape[:2], (h, w))

    def test_equipment_slots_are_not_counted(self):
        screen = InventoryScreen()
        bot, _mouse, _logs = make_inventory_bot(screen)
        slots = bot._scan_empty_slots(bot.capture_inventory_area())
        self.assertEqual(len(slots), screen.COLS * screen.ROWS)
        # ... while the fixed strip (no tabs configured) does see them
        old, _m, _l = make_inventory_bot(screen, tabs=False)
        self.assertEqual(old._inventory_rect(), (600, 200, 200, 470))
        self.assertEqual(len(old._scan_empty_slots(old.capture_inventory_area())),
                         screen.COLS * screen.ROWS + len(screen.EQUIPMENT))

    def test_a_full_page_reads_as_full(self):
        screen = InventoryScreen()
        item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        screen.fill(0, screen.COLS * screen.ROWS, item)
        bot, _mouse, _logs = make_inventory_bot(screen)
        self.assertEqual(bot._scan_empty_slots(bot.capture_inventory_area()), [])

    def test_the_clock_is_not_an_inventory_change(self):
        screen = InventoryScreen()
        bot, _mouse, _logs = make_inventory_bot(screen)
        base = cv2.cvtColor(bot.capture_inventory_area(), cv2.COLOR_BGR2GRAY)
        self.assertIsNone(bot._changed_box(base, bot.capture_inventory_area()))
        old, _m, _l = make_inventory_bot(screen, tabs=False)
        base = cv2.cvtColor(old.capture_inventory_area(), cv2.COLOR_BGR2GRAY)
        for _ in range(9):
            screen.render()                                           # seconds pass
        self.assertIsNotNone(old._changed_box(base, old.capture_inventory_area()))

    def test_strip_positions_are_clicked_where_they_are_on_screen(self):
        screen = InventoryScreen()
        item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        screen.pages[0][7] = item
        bot, mouse, _logs = make_inventory_bot(screen)
        bot.window_manager.rect = (300, 40, screen.W, screen.H)       # the window is not at the corner
        match = bot.identify_item_in_inventory(bot.capture_inventory_area())
        self.assertIsNotNone(match)
        name, (inv_x, inv_y) = match
        self.assertEqual(name, "Carp_living.jpg")
        bot._handle_item(name, inv_x, inv_y, "open")
        (cx, cy), button = mouse.clicks[0]
        want = screen.slot_center(7)
        self.assertEqual(button, "right")
        self.assertLessEqual(max(abs(cx - 300 - want[0]), abs(cy - 40 - want[1])), 2)

    def test_kept_positions_are_dropped_when_the_strip_moves(self):
        screen = InventoryScreen()
        bot, _mouse, _logs = make_inventory_bot(screen)
        bot._empty_slot_positions = bot._scan_empty_slots(bot.capture_inventory_area())
        bot._ignored_positions.add((40, 40))
        self.assertTrue(bot._empty_slot_positions)
        before = bot._inventory_rect()
        for page in range(1, 5):                                      # the tabs are set again, lower
            x, y = bot.config[f"inv_page_{page}_pos"]
            bot.config[f"inv_page_{page}_pos"] = [x, y + 3]
        after = bot._inventory_rect()
        self.assertEqual(after[1], before[1] + 3)
        self.assertEqual((bot._empty_slot_positions, bot._ignored_positions), ([], set()))

    def test_layout_of_the_reported_session(self):
        """Window and tabs of the diagnostic session the change was made for."""
        config = {"inv_page_1_pos": [1118, 641], "inv_page_2_pos": [1210, 634],
                  "inv_page_3_pos": [1129, 661], "inv_page_4_pos": [1191, 659]}
        (x, y, w, h), source = inventory_layout.strip_rect(config, 1251, 1039)
        self.assertEqual(source, "tabs")
        self.assertTrue(661 < y <= 673)                               # grid starts at y=673
        self.assertTrue(x <= 1075 and x + w >= 1235)                  # grid is x=1075..1235
        self.assertTrue(961 <= y + h <= 985)                          # grid ends at y=673+9*32=961
        self.assertLess(h, 809 // 2)                                  # the old strip was 200x809

    def test_unusable_tabs_fall_back_to_the_fixed_strip(self):
        fixed = (600, 200, 200, 370)
        self.assertEqual(inventory_layout.strip_rect({}, 800, 600), (fixed, "fixed"))
        outside = {"inv_page_1_pos": [1388, 445]}                     # captured in another window
        self.assertEqual(inventory_layout.strip_rect(outside, 800, 600), (fixed, "fixed"))
        too_low = {"inv_page_1_pos": [700, 560]}
        self.assertEqual(inventory_layout.strip_rect(too_low, 800, 600), (fixed, "fixed"))
        garbage = {"inv_page_1_pos": "x", "inv_page_2_pos": [1], "inv_page_3_pos": None}
        self.assertEqual(inventory_layout.strip_rect(garbage, 800, 600), (fixed, "fixed"))


class PageSwitchTests(unittest.TestCase):
    """After a tab click the bot waits until the game shows that page."""

    def test_active_tab_is_read_wherever_the_tabs_were_clicked(self):
        screen = InventoryScreen()
        frame = screen.render()
        rng = np.random.default_rng(5)
        for page in range(4):
            screen.page = page
            frame = screen.render()
            for _ in range(40):
                points = {p: (int(rng.integers(x1 + 5, x2 - 4)), int(rng.integers(y1 + 5, y2 - 4)))
                          for p, (x1, y1, x2, y2) in screen.TAB_BOXES.items()}
                active, detail = inventory_layout.active_tab(frame, (0, 0), points)
                self.assertIn(active, (page + 1, None), detail)       # never a wrong tab
            self.assertEqual(inventory_layout.active_tab(frame, (0, 0), screen.TABS)[0], page + 1)

    def test_two_lit_tabs_or_none_are_not_a_reading(self):
        screen = InventoryScreen()
        frame = screen.render()
        x1, y1, x2, y2 = screen.TAB_BOXES[3]                          # a hovered tab is lit as well
        cv2.rectangle(frame, (x1, y1), (x2, y2), (134, 122, 112), -1)
        self.assertIsNone(inventory_layout.active_tab(frame, (0, 0), screen.TABS)[0])
        screen.highlight = False
        self.assertIsNone(inventory_layout.active_tab(screen.render(), (0, 0), screen.TABS)[0])
        self.assertIsNone(inventory_layout.active_tab(np.zeros((700, 800, 3), np.uint8), (0, 0), screen.TABS)[0])

    def test_waits_for_a_page_that_shows_up_late(self):
        screen = InventoryScreen(page_delay=0.7)                      # far longer than the old 0.3 s
        item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        screen.fill(0, screen.COLS * screen.ROWS, item)               # page 1 full, page 2 empty
        bot, _mouse, _logs = make_inventory_bot(screen)
        t0 = time.perf_counter()
        self.assertTrue(bot._goto_page(screen.TABS[2]))
        waited = time.perf_counter() - t0
        self.assertEqual(screen.page, 1)
        self.assertGreaterEqual(waited, 0.7)
        self.assertLess(waited, 1.6)
        self.assertEqual(screen.tab_clicks, [2])                      # no second click was needed
        self.assertEqual(len(bot._scan_empty_slots(bot.capture_inventory_area())), screen.COLS * screen.ROWS)

    def test_does_not_wait_longer_than_the_page_takes(self):
        screen = InventoryScreen()
        bot, _mouse, _logs = make_inventory_bot(screen)
        t0 = time.perf_counter()
        self.assertTrue(bot._goto_page(screen.TABS[3]))
        self.assertLess(time.perf_counter() - t0, 0.5)
        self.assertEqual(screen.page, 2)

    def test_a_click_the_game_missed_is_repeated(self):
        screen = InventoryScreen(lost_clicks=1)
        bot, _mouse, _logs = make_inventory_bot(screen)
        bot._PAGE_WAIT_TIMEOUT = 0.6
        self.assertTrue(bot._goto_page(screen.TABS[4]))
        self.assertEqual(screen.tab_clicks, [4, 4])
        self.assertEqual(screen.page, 3)
        self.assertEqual(bot._tab_check_misses, 0)

    def test_timeout_goes_on_like_before(self):
        screen = InventoryScreen(page_delay=30.0)                     # the page never shows
        bot, _mouse, _logs = make_inventory_bot(screen)
        bot._PAGE_WAIT_TIMEOUT = 0.4
        t0 = time.perf_counter()
        self.assertTrue(bot._goto_page(screen.TABS[2]))               # still "clicked", as before
        self.assertLess(time.perf_counter() - t0, 1.5)
        self.assertEqual(bot._tab_check_misses, 1)
        self.assertFalse(bot._tab_check_off)

    def test_tabs_that_cannot_be_read_cost_two_timeouts_at_most(self):
        screen = InventoryScreen(highlight=False)                     # a client that does not light its tabs
        item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        screen.fill(3, 7, item)                                       # page 4 looks unlike page 3
        bot, _mouse, _logs = make_inventory_bot(screen)
        bot._PAGE_WAIT_TIMEOUT = 0.3
        for page in (2, 3):
            self.assertTrue(bot._goto_page(screen.TABS[page]))
        self.assertTrue(bot._tab_check_off)
        clicks = len(screen.tab_clicks)
        t0 = time.perf_counter()
        self.assertTrue(bot._goto_page(screen.TABS[4], settle=0.05))  # fixed wait from now on
        self.assertLess(time.perf_counter() - t0, 0.25)
        self.assertEqual(len(screen.tab_clicks), clicks + 1)
        screen.render()
        self.assertEqual(screen.page, 3)

    def _two_tab_bot(self, screen):
        """Only pages 1 and 2 set up: the active tab cannot be read."""
        cfg = {f"inv_page_{page}_pos": list(screen.TABS[page]) for page in (1, 2)}
        return make_inventory_bot(screen, cfg, tabs=False)[0]

    def test_two_tabs_a_missed_click_is_noticed_and_repeated(self):
        """Diagnostics 20261120_190647: right after a switch between game
        windows the game missed the click back to page 1, and with only two
        tabs nothing noticed: page 2 was used as page 1."""
        screen = InventoryScreen(lost_clicks=1)
        item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        screen.fill(0, 8, item)                                       # page 1: 37 empty, page 2: 45
        screen.page = 1
        bot = self._two_tab_bot(screen)
        self.assertTrue(bot._goto_page(screen.TABS[1], settle=0.05))
        self.assertEqual(screen.tab_clicks, [1, 1])
        screen.render()
        self.assertEqual(screen.page, 0)

    def test_two_tabs_a_click_that_worked_is_not_repeated(self):
        screen = InventoryScreen()
        item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        screen.fill(0, 8, item)
        bot = self._two_tab_bot(screen)
        self.assertTrue(bot._goto_page(screen.TABS[2], settle=0.05))
        self.assertEqual(screen.tab_clicks, [2])
        screen.render()
        self.assertEqual(screen.page, 1)

    def test_two_tabs_pages_that_look_alike_cost_one_harmless_click(self):
        screen = InventoryScreen()                                    # both pages empty
        bot = self._two_tab_bot(screen)
        self.assertTrue(bot._goto_page(screen.TABS[2], settle=0.05))
        self.assertEqual(screen.tab_clicks, [2, 2])
        screen.render()
        self.assertEqual(screen.page, 1)

    def test_same_empty_slots_allows_one_item_landing(self):
        grid = [(x, y) for y in range(16, 300, 32) for x in range(16, 160, 32)]
        self.assertEqual(inventory_layout.same_empty_slots(grid, [(x + 2, y - 1) for x, y in grid]), (True, 0))
        self.assertEqual(inventory_layout.same_empty_slots(grid, grid[1:]), (True, 1))
        self.assertEqual(inventory_layout.same_empty_slots(grid, grid[5:]), (False, 5))
        self.assertEqual(inventory_layout.same_empty_slots([], []), (True, 0))

    def test_startup_scan_judges_each_page_by_its_own_content(self):
        """On a slow machine every page used to be judged by the one before."""
        screen = InventoryScreen(page_delay=0.5)
        item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        for page in (0, 1):
            screen.fill(page, screen.COLS * screen.ROWS, item)        # pages 1 and 2 full, 3 has room
        screen.fill(2, 40, item)
        bot, _mouse, _logs = make_inventory_bot(screen, {"auto_fish_handling": False})
        bot._startup_scan_and_process_all_pages()
        self.assertEqual(bot._current_inv_page, 2)
        self.assertEqual(screen.page, 2)
        self.assertEqual(len(bot._empty_slot_positions), 5)
        self.assertTrue(bot.running)

    def test_stop_ends_the_wait(self):
        screen = InventoryScreen(page_delay=30.0)
        bot, _mouse, _logs = make_inventory_bot(screen)
        threading.Timer(0.2, lambda: setattr(bot, "running", False)).start()
        t0 = time.perf_counter()
        bot._goto_page(screen.TABS[2])
        self.assertLess(time.perf_counter() - t0, 1.0)


class GameLoopTests(unittest.TestCase):
    def test_capture_hiccup_does_not_end_the_game(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen, {
            "timing_recast_wait": 0.0, "human_like_clicking": False, "quick_skip": False,
        })
        bot.bait_counter = 2
        state = {"hits": 0, "casts": 0, "blanked": 0, "t0": time.perf_counter()}

        def cast():
            state["casts"] += 1
            state["hits"] = 0
            screen.panel_open = True

        def on_click(button="left"):
            mouse.clicks.append((mouse.pos, button))
            if screen.fish_in_circle():
                state["hits"] += 1
                if state["hits"] == 1 and not state["blanked"]:
                    state["blanked"] = 1
                    screen.blank = True          # one bad frame right after the first hit
                if state["hits"] >= 3:
                    screen.panel_open = False    # caught

        def grab(left, top, w, h):
            screen.t = time.perf_counter() - state["t0"]
            frame = screen.grab(left, top, w, h)
            screen.blank = False
            return frame

        handled = []
        mouse.mouse_down = on_click
        bot._grab = grab
        bot.bait_and_cast = cast
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._resume_inventory_check = lambda: None
        bot.handle_caught_item = lambda may_have_catch=True, baseline=None, **kw: handled.append(may_have_catch)

        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "play_game did not finish")

        self.assertEqual(state["blanked"], 1)
        self.assertEqual(state["casts"], 2)          # the hiccup did not cause a recast
        self.assertEqual(bot.total_games, 2)
        self.assertEqual(handled, [True, True])
        self.assertEqual(bot.stop_reason, FishingBot.STOP_BAIT_DEPLETED)


class ArmorQuickSkipTests(unittest.TestCase):
    """Armor quick skip right-clicks the spare armor only on the armor's own
    page: a right-click on another page would use whatever item is there."""

    ARMOR = (640, 330)                                   # a slot position on every page

    def _bot(self, armor_page, current_page, **screen_args):
        screen = InventoryScreen(**screen_args)
        screen.page = current_page - 1
        bot, mouse, logs = make_inventory_bot(screen, {
            "quick_skip_mode": "armor", "armor_slot_pos": list(self.ARMOR), "armor_slot_page": armor_page,
            "adaptive_recast_wait": False, "timing_recast_wait": 0.05})
        bot._current_inv_page = current_page - 1
        right = []                                       # page on screen at each right-click
        click = screen.click

        def recording_click(pos, button="left"):
            screen.render()                              # a late page shows up first
            if button == "right":
                right.append((tuple(pos), screen.page + 1))
            click(pos, button)
        screen.click = recording_click
        return bot, screen, right, logs

    def test_armor_on_the_page_on_screen_is_clicked_without_tab_clicks(self):
        bot, screen, right, _logs = self._bot(armor_page=1, current_page=1)
        bot.quickskip()
        self.assertEqual(right, [(self.ARMOR, 1)])
        self.assertEqual(screen.tab_clicks, [])

    def test_armor_on_another_page_is_clicked_on_its_page_then_the_page_comes_back(self):
        bot, screen, right, _logs = self._bot(armor_page=1, current_page=3)
        t0 = time.perf_counter()
        bot.quickskip()
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertEqual(right, [(self.ARMOR, 1)])
        self.assertEqual(screen.tab_clicks, [1, 3])
        bot._restore_fill_page()                         # before the next inventory read
        screen.render()
        self.assertEqual(screen.page + 1, 3)
        self.assertEqual(screen.tab_clicks, [1, 3])      # the click back was enough

    def test_a_late_armor_page_is_waited_for(self):
        bot, screen, right, _logs = self._bot(armor_page=2, current_page=4, page_delay=0.25)
        bot.quickskip()
        self.assertEqual(right, [(self.ARMOR, 2)])

    def test_armor_page_that_never_shows_is_not_clicked(self):
        bot, screen, right, _logs = self._bot(armor_page=1, current_page=2, page_delay=30.0)
        bot.quickskip()
        self.assertEqual(right, [])
        self.assertEqual(screen.page + 1, 2)

    def test_restore_clicks_the_page_again_when_it_is_not_on_screen(self):
        bot, screen, right, _logs = self._bot(armor_page=1, current_page=3)
        bot.quickskip()
        screen.page, screen._pending = 0, None           # the click back was lost
        bot._restore_fill_page()
        screen.render()
        self.assertEqual(screen.page + 1, 3)
        self.assertEqual(screen.tab_clicks, [1, 3, 3])

    def test_unknown_armor_page_is_not_guessed(self):
        bot, screen, right, logs = self._bot(armor_page=None, current_page=2)
        bot.quickskip()
        bot.quickskip()
        self.assertEqual((right, screen.tab_clicks), ([], []))
        self.assertEqual(sum("Armor quick skip skipped" in m for m in logs), 1)   # said once

    def test_tabs_that_cannot_be_read_mean_no_switch(self):
        bot, screen, right, _logs = self._bot(armor_page=1, current_page=2, highlight=False)
        bot._PAGE_WAIT_TIMEOUT = 0.3
        for _ in range(2):
            bot.quickskip()
        self.assertTrue(bot._tab_check_off)
        clicks = len(screen.tab_clicks)
        t0 = time.perf_counter()
        bot.quickskip()
        self.assertLess(time.perf_counter() - t0, 0.6)
        self.assertEqual((right, len(screen.tab_clicks)), ([], clicks))
        screen.render()
        self.assertEqual(screen.page + 1, 2)             # left on the page being filled

    def test_without_page_tabs_the_armor_is_clicked_where_it_is(self):
        screen = InventoryScreen()
        bot, mouse, _logs = make_inventory_bot(screen, {"quick_skip_mode": "armor",
                                                        "armor_slot_pos": list(self.ARMOR)}, tabs=False)
        bot.quickskip()
        self.assertEqual(mouse.clicks, [(self.ARMOR, "right")])


class InputSafetyTests(unittest.TestCase):
    """Nothing is typed or clicked unless this bot's window is in front and
    the bot is running."""

    def _bot(self, config=None):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen, config)
        keys = bot.keyboard_controller.events
        return bot, screen, mouse, keys, logs

    def _try_everything(self, bot):
        bot.bait_and_cast()
        bot.press_key("space")
        bot.press_ctrl_key("g")
        bot.config.update(quick_skip_mode="armor", armor_slot_pos=[50, 50])
        bot.quickskip()
        bot.config.update(quick_skip_mode="horse")
        bot.quickskip()
        bot._handle_item("Carp_living.jpg", 40, 40, "open")
        bot._goto_page((700, 222), settle=0.0)

    def test_nothing_is_sent_when_the_window_cannot_be_focused(self):
        bot, screen, mouse, keys, logs = self._bot()
        bot.window_manager.activate_window = lambda force_activate=False: False
        # A quick skip that sent nothing waits for the animation instead
        # (both modes); the wait itself is not what this test times.
        recast_waits = []
        bot._wait_before_recast = lambda: recast_waits.append(1) and False
        self._try_everything(bot)
        self.assertEqual(len(recast_waits), 2)
        screen.panel_open = True
        screen.t = 0.0                                   # fish in the middle of the circle
        bot.window_manager.activate_window = lambda force_activate=False: True
        self.assertTrue(bot.wait_for_minigame_window(timeout=1.0))
        bot.window_manager.activate_window = lambda force_activate=False: False
        self.assertEqual(bot.atomic_capture_and_click(), (True, None))
        self.assertEqual((keys, mouse.clicks, mouse.pos), ([], [], (0, 0)))
        self.assertEqual(sum("Could not bring the game window" in m for m in logs), 1)   # said once

    def test_failed_cast_is_reported_to_the_caller(self):
        bot, _screen, _mouse, keys, _logs = self._bot()
        self.assertTrue(bot.bait_and_cast())
        self.assertIn(("press", "space"), keys)
        bot.window_manager.activate_window = lambda force_activate=False: False
        self.assertFalse(bot.bait_and_cast())

    def test_nothing_is_sent_after_stop(self):
        bot, _screen, mouse, keys, _logs = self._bot()
        bot.running = False
        self._try_everything(bot)
        self.assertEqual((keys, mouse.clicks, mouse.pos), ([], [], (0, 0)))

    def test_no_quick_skip_after_the_inventory_filled_up(self):
        """The game that fills the last slot stops the bot; the keys of the
        quick skip that used to follow went to a game nobody was playing."""
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, mouse, logs = make_bot(screen, {"quick_skip": True, "quick_skip_mode": "horse"})
        keys = bot.keyboard_controller.events
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._resume_inventory_check = lambda: None
        bot._require_inventory_visible = lambda when: True   # this screen draws no inventory
        bot.bait_and_cast = lambda: setattr(screen, "panel_open", True) or True

        def full(may_have_catch=True):
            screen.panel_open = False
            bot._inventory_full_stop()

        bot._play_minigame = lambda: (full(), (False, 3))[1]
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(bot.stop_reason, FishingBot.STOP_INVENTORY_FULL)
        self.assertEqual(keys, [])


class PauseTests(unittest.TestCase):
    def test_pause_while_waiting_for_the_bite_is_not_a_missed_bite(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _mouse, logs = make_bot(screen, {"timing_minigame_timeout": 1.0, "timing_recast_wait": 0.0})
        bot._startup_scan_and_process_all_pages = lambda: None
        bot._resume_inventory_check = lambda: None
        casts = []

        def cast():
            casts.append(time.perf_counter())
            if len(casts) == 1:
                bot.paused = True                        # the player takes over right after the cast
                threading.Timer(1.5, lambda: setattr(bot, "paused", False)).start()
            else:
                bot.running = False
            return True

        bot.bait_and_cast = cast
        thread = threading.Thread(target=bot.play_game, daemon=True)
        thread.start()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(casts), 2)
        self.assertGreaterEqual(casts[1] - casts[0], 1.4)   # waited for the resume, then cast again
        self.assertEqual(bot.consecutive_failures, 0)
        self.assertFalse(any("not detected" in m for m in logs), logs)

    def test_cast_after_a_pause_is_not_fed_to_the_recast_tuner(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _mouse, _logs = make_bot(screen, {"timing_recast_wait": 0.3, "adaptive_recast_wait": True})
        bot._game_end_time = time.perf_counter()
        bot.paused = True
        threading.Timer(0.5, lambda: setattr(bot, "paused", False)).start()
        self.assertTrue(bot._wait_before_recast())
        self.assertIsNone(bot._last_cast_wait)

    def test_recast_wait_counts_from_the_end_of_the_game(self):
        screen = SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH)
        bot, _mouse, _logs = make_bot(screen, {"timing_recast_wait": 0.6, "adaptive_recast_wait": True})
        bot._game_end_time = time.perf_counter() - 0.5   # item handling took half a second
        start = time.perf_counter()
        self.assertFalse(bot._wait_before_recast())
        self.assertLess(time.perf_counter() - start, 0.35)

        fixed, _, _ = make_bot(screen, {"timing_recast_wait": 0.6, "adaptive_recast_wait": False})
        fixed._game_end_time = time.perf_counter() - 0.5
        start = time.perf_counter()
        fixed._wait_before_recast()
        self.assertGreaterEqual(time.perf_counter() - start, 0.6)   # the fixed wait is slept in full


class InventoryTests(unittest.TestCase):
    def setUp(self):
        FishingBot._template_cache = None
        self.item = cv2.imread(os.path.join(ROOT, "assets", "fishing", "Carp_living.jpg"))
        rng = np.random.default_rng(3)
        self.empty = cv2.GaussianBlur(
            rng.integers(30, 70, size=(370, 200, 3), dtype=np.uint8), (5, 5), 0)
        self.appear_at = None
        self.t0 = time.perf_counter()

    def _grab(self, left, top, w, h):
        frame = self.empty.copy()
        if self.appear_at is not None and time.perf_counter() - self.t0 >= self.appear_at:
            ih, iw = self.item.shape[:2]
            frame[100:100 + ih, 60:60 + iw] = self.item
        return frame

    def _bot(self):
        bot, _, _ = make_bot(SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH))
        bot._grab = self._grab
        return bot

    def _expect_carp(self, match):
        self.assertIsNotNone(match)
        name, (x, y) = match
        ih, iw = self.item.shape[:2]
        self.assertEqual(name, "Carp_living.jpg")
        self.assertLessEqual(abs(x - (60 + iw // 2)), 2)
        self.assertLessEqual(abs(y - (100 + ih // 2)), 2)

    def test_item_is_handled_as_soon_as_it_appears(self):
        bot = self._bot()
        self.appear_at = 0.10
        self.t0 = start = time.perf_counter()
        match, _ = bot._find_new_item(allow_extra=True)
        elapsed = time.perf_counter() - start
        self._expect_carp(match)
        self.assertLess(elapsed, 0.3)   # configured wait is 0.4 s

    def test_late_item_is_still_found(self):
        bot = self._bot()
        self.appear_at = 0.65            # after the configured 0.4 s
        self.t0 = time.perf_counter()
        match, _ = bot._find_new_item(allow_extra=True)
        self._expect_carp(match)

    def test_no_extra_wait_without_a_catch(self):
        bot = self._bot()
        self.appear_at = None
        start = time.perf_counter()
        match, _ = bot._find_new_item(allow_extra=False)
        self.assertIsNone(match)
        self.assertLess(time.perf_counter() - start, 0.8)

    def test_item_already_present_is_found_by_full_scan(self):
        bot = self._bot()
        self.appear_at = 0.0             # landed before the baseline was taken
        self.t0 = time.perf_counter() - 1
        match, _ = bot._find_new_item(allow_extra=True)
        self._expect_carp(match)

    def test_ignored_slot_is_skipped(self):
        bot = self._bot()
        self.appear_at = 0.05
        self.t0 = time.perf_counter()
        ih, iw = self.item.shape[:2]
        bot._ignored_positions.add((60 + iw // 2, 100 + ih // 2))
        match, _ = bot._find_new_item(allow_extra=False)
        self.assertIsNone(match)

    def test_page_is_not_given_up_for_one_bad_look(self):
        """A page counts as full only when two looks agree: leaving a page
        that still has room hides every later catch."""
        bot = self._bot()
        looks = iter([[], [(20, 20), (52, 20)]])
        bot._scan_empty_slots = lambda frame: next(looks)
        self.assertTrue(bot._page_has_space(self.empty))
        self.assertEqual(len(bot._empty_slot_positions), 2)
        looks = iter([[], []])
        self.assertFalse(bot._page_has_space(self.empty))

    def test_page_that_went_from_many_free_slots_to_none_is_doubted_first(self):
        bot = self._bot()
        bot._scan_empty_slots = lambda frame: []                 # e.g. a tooltip over the inventory
        free = [(20 + 32 * i, 20) for i in range(5)]
        self.assertTrue(bot._page_has_space(self.empty, free))    # not believed ...
        self.assertTrue(bot._page_has_space(self.empty, free))
        self.assertFalse(bot._page_has_space(self.empty, free))   # ... the third time in a row it is
        self.assertFalse(bot._page_has_space(self.empty, [(20, 20)]))   # one slot left, then none: plausible

    def test_template_cache_is_safe_under_concurrent_start(self):
        FishingBot._template_cache = None
        results, errors = [], []

        def load():
            try:
                bot, _, _ = make_bot(SimScreen(OFFICIAL_PANEL, OFFICIAL_FISH))
                cache = bot._load_template_cache()
                results.append((id(cache), len(list(cache.items()))))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=load) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)      # same object, fully populated, for everyone
        self.assertGreater(results[0][1], 30)


class PriorityLockTests(unittest.TestCase):
    def test_mutual_exclusion(self):
        lock = PriorityInputLock()
        inside, peak = [0], [0]

        def work():
            for _ in range(200):
                with lock:
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                    inside[0] -= 1

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(peak[0], 1)
        self.assertFalse(lock.locked())

    def test_urgent_waiters_go_first_and_normal_waiters_are_fifo(self):
        lock = PriorityInputLock()
        order = []
        lock.acquire()

        def waiter(name, urgent):
            ctx = lock.urgent() if urgent else lock
            with ctx:
                order.append(name)

        threads = []
        for name, urgent in [("inv-1", False), ("inv-2", False), ("fish", True), ("inv-3", False)]:
            t = threading.Thread(target=waiter, args=(name, urgent))
            t.start()
            threads.append(t)
            time.sleep(0.05)   # deterministic arrival order
        self.assertTrue(lock.contended())
        lock.release()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(order, ["fish", "inv-1", "inv-2", "inv-3"])


if __name__ == "__main__":
    unittest.main()

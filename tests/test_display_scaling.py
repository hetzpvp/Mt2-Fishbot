"""Game pixels against Qt units, on every kind of display setup.

Diag 20261121_173554 (2.0.3): the jigsaw paused at once on a 1920x1080
screen at 100 % Windows scaling. The program's own interface size ("auto")
had started Qt at 115 %, and the grid overlay took the game window's rect in
screen pixels as Qt units: the saved grid came out ~15 % too small and
shifted, the bot never found the puzzle there. Any screen of 1080p or more
was hit.

Everything the bots read and click is in game-window pixels. Qt counts in
its own units, scaled by the interface size and by the screen DPI. Wherever
the two meet, one must be converted. These tests run the whole grid setup
(overlay opened over the game, the user drags the grid onto the puzzle,
Enter, the bot reads the grid) over a matrix of:

- screen resolutions from 1366x768 to 3840x2160
- Windows display scaling 100-200 %
- the interface size: auto, 100 %, 125 %, 150 %, and Qt seeing the OS scale
  on top of it or not
- one screen, or the game on a second screen right of, left of or above the
  main one (negative coordinates), with its own scale
- game window sizes and positions on that screen
- grid positions in the window

They also run real Qt at several scales in a process of its own (rendered
pixels, real mouse events), and keep a list of every place in src/ that
uses Qt screen geometry, so a new one has to be looked at.
"""

import ast
import concurrent.futures
import itertools
import json
import os
import subprocess
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from _stubs import install

install()

import cv2
import numpy as np

try:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False

if HAS_QT:
    import qt_gui

from jigsaw_detector import JigsawDetector

TESTS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(TESTS), "src")
PUZZLE = os.path.join(TESTS, "fixtures", "jigsaw_grid", "puzzle_empty.png")
# Where the grid lies in the fixture (the real Metin2 puzzle window, empty;
# cropped from the diag shot). The game draws it this size at any resolution.
PUZZLE_GRID = (16, 35, 192, 128)

RESOLUTIONS = [(1366, 768), (1600, 900), (1920, 1080), (2560, 1440), (3440, 1440), (3840, 2160)]
OS_SCALES = [1.0, 1.25, 1.5, 1.75, 2.0]
UI_SETTINGS = ["auto", "1.0", "1.25", "1.5"]
TASKBAR = 48   # screen pixels the taskbar takes from the free area
MAIN = (0, 0, 1920, 1080, 1.0)   # the main screen next to a second one


def display_scales():
    """(resolution, OS scale, Qt devicePixelRatio) for every setup: the
    interface size chosen for that screen, with and without the OS scale
    on top (which one Qt sees depends on its DPI awareness)."""
    seen = set()
    for (rw, rh), os_scale, ui in itertools.product(RESOLUTIONS, OS_SCALES, UI_SETTINGS):
        avail_w, avail_h = int(rw / os_scale), int((rh - TASKBAR) / os_scale)
        if avail_w < 640 or avail_h < 400:
            continue
        ui_scale = qt_gui.choose_ui_scale(ui, avail_w, avail_h)
        for dpr in {round(ui_scale, 4), round(ui_scale * os_scale, 4)}:
            key = ((rw, rh), dpr)
            if key not in seen:
                seen.add(key)
                yield (rw, rh), os_scale, dpr


def layouts(res, dpr):
    """Screens in screen pixels (x, y, w, h, dpr), and which one has the game."""
    rw, rh = res
    yield "one screen", [(0, 0, rw, rh, dpr)], 0
    yield "second screen right", [MAIN, (1920, 0, rw, rh, dpr)], 1
    yield "second screen left", [MAIN, (-rw, 0, rw, rh, dpr)], 1
    yield "second screen above", [MAIN, (0, -rh, rw, rh, dpr)], 1
    yield "main screen scaled, second not", [(0, 0, rw, rh, dpr), (rw, 0, 1920, 1080, 1.0)], 0


def qt_screens(screens):
    """The screens as Qt 6 reports them: a screen keeps its corner at its
    pixel position, its size is in Qt units (pixels / dpr)."""
    return [(x, y, int(round(w / d)), int(round(h / d)), d) for x, y, w, h, d in screens]


def windows_on(screen):
    """Game window rects (screen pixels) on one screen: several sizes, at
    the screen's corner, at an odd spot, centred and against the
    bottom-right edge."""
    sx, sy, sw, sh, _ = screen
    for ww, wh in ((800, 600), (1024, 768), (1296, 839), (sw, sh)):
        if ww > sw or wh > sh:
            continue
        spots = {(0, 0), (37, 51), ((sw - ww) // 2, (sh - wh) // 2), (sw - ww, sh - wh)}
        for dx, dy in sorted(spots):
            yield (sx + dx, sy + dy, ww, wh)


def grids_in(ww, wh):
    """Where the puzzle grid lies in the window: near the top-left, the
    middle, and near the bottom-right."""
    gw, gh = PUZZLE_GRID[2:]
    return [(PUZZLE_GRID[0] + 3, PUZZLE_GRID[1] + 30),
            ((ww - gw) // 2, (wh - gh) // 2),
            (ww - 285 + PUZZLE_GRID[0] - 3, wh - 206 + PUZZLE_GRID[1] - 3)]


class ScreenModel:
    """Qt 6's mapping between screen pixels and Qt units, written down here
    on its own (not taken from the program): on the screen holding the
    point, qt = origin + (pixel - origin) / dpr."""

    def __init__(self, screens):
        self.screens = screens

    def screen_at(self, px, py):
        for x, y, w, h, d in self.screens:
            if x <= px < x + w and y <= py < y + h:
                return x, y, d
        x, y, _, _, d = self.screens[0]
        return x, y, d

    def to_qt(self, px, py, screen=None):
        x, y, d = screen or self.screen_at(px, py)
        return x + (px - x) / d, y + (py - y) / d

    def to_pixels(self, qx, qy, screen):
        x, y, d = screen
        return x + (qx - x) * d, y + (qy - y) * d


_FRAMES = {}


def game_frame(ww, wh, gx, gy):
    """A game window (ww x wh) showing the puzzle with its grid at (gx, gy)."""
    key = (ww, wh)
    if key not in _FRAMES:
        rng = np.random.default_rng(ww * 7919 + wh)
        noise = rng.integers(40, 160, (wh // 8 + 1, ww // 8 + 1, 3), dtype=np.uint8)
        _FRAMES[key] = cv2.resize(noise, (ww, wh), interpolation=cv2.INTER_LINEAR)
    frame = _FRAMES[key].copy()
    puzzle = cv2.imread(PUZZLE)
    px, py = gx - PUZZLE_GRID[0], gy - PUZZLE_GRID[1]
    frame[py:py + puzzle.shape[0], px:px + puzzle.shape[1]] = puzzle
    return frame


def detector(bounds):
    return JigsawDetector({"jigsaw_grid_bounds": list(bounds)})


class FixtureTests(unittest.TestCase):

    def test_puzzle_fixture_is_found_where_it_is_and_not_beside(self):
        frame = game_frame(1296, 839, 521, 361)
        self.assertTrue(detector((521, 361, 192, 128)).detect_grid(frame).grid_found)
        # What 2.0.3 saved for this window: the bot paused on it.
        self.assertFalse(detector((415, 307, 171, 116)).detect_grid(frame).grid_found)
        for wrong in ((521, 361, 167, 111), (521 + 12, 361, 192, 128), (521, 361 - 12, 192, 128)):
            self.assertFalse(detector(wrong).detect_grid(frame).grid_found, wrong)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class DisplayMatrixTests(unittest.TestCase):
    """The grid setup on every display setup: the saved grid must be the
    puzzle's pixels and the bot must find the puzzle there."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    # ---- the simulated user, who sees pixels and moves a mouse in Qt units

    def _send(self, ov, kind, qpos):
        local = QPointF(qpos[0] - ov.geometry().x(), qpos[1] - ov.geometry().y())
        buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
        ev = QMouseEvent(kind, local, local, Qt.LeftButton, buttons, Qt.NoModifier)
        {QEvent.MouseButtonPress: ov.mousePressEvent, QEvent.MouseMove: ov.mouseMoveEvent,
         QEvent.MouseButtonRelease: ov.mouseReleaseEvent}[kind](ev)

    def _drag(self, ov, model, win, start, end):
        """Drag from one game-window pixel to another."""
        screen = model.screen_at(win[0] + win[2] / 2, win[1] + win[3] / 2)
        a = model.to_qt(win[0] + start[0], win[1] + start[1], screen)
        b = model.to_qt(win[0] + end[0], win[1] + end[1], screen)
        self._send(ov, QEvent.MouseButtonPress, a)
        self._send(ov, QEvent.MouseMove, b)
        self._send(ov, QEvent.MouseButtonRelease, b)

    def _set_grid(self, screens, win, target, start=None):
        """Opens the overlay over ``win``, drags the grid onto ``target``
        (game-window pixels) and confirms: (saved grid, overlay)."""
        orig = qt_gui.JigsawGridOverlay.__dict__["_screens"]
        qt_gui.JigsawGridOverlay._screens = staticmethod(lambda: qt_screens(screens))
        try:
            ww, wh = win[2:]
            start = start or [max(0, (ww - 264) // 2), max(0, (wh - 176) // 2), 264, 176]
            ov = qt_gui.JigsawGridOverlay(*win, start, "METIN2")
        finally:
            qt_gui.JigsawGridOverlay._screens = orig
        saved = []
        ov.confirmed.connect(saved.append)
        model = ScreenModel(screens)
        tx, ty, tw, th = target
        # Resize with the corner handle (a few pixels inside the corner)...
        gx, gy, gw, gh = ov._grid
        self._drag(ov, model, win, (gx + gw - 5, gy + gh - 5), (gx + tw - 5, gy + th - 5))
        # ...then grab the middle and move it onto the puzzle.
        gx, gy, gw, gh = ov._grid
        self._drag(ov, model, win, (gx + gw / 2, gy + gh / 2), (tx + gw / 2, ty + gh / 2))
        QTest.keyClick(ov, Qt.Key_Return)
        ov.deleteLater()
        return (saved[0] if saved else None), ov

    def _check_overlay_covers_window(self, ov, screens, win):
        """The overlay's Qt rect, mapped back to pixels by Qt's rule, lies on
        the game window to within one Qt unit, covering all of it."""
        model = ScreenModel(screens)
        screen = model.screen_at(win[0] + win[2] / 2, win[1] + win[3] / 2)
        g = ov.geometry()
        x0, y0 = model.to_pixels(g.x(), g.y(), screen)
        x1, y1 = model.to_pixels(g.x() + g.width(), g.y() + g.height(), screen)
        d = screen[2]
        # Rounded outwards to whole Qt units: it starts at most one Qt unit
        # before the window and ends at most one after, never inside it.
        self.assertTrue(win[0] - d - 1e-6 < x0 <= win[0] + 1e-6, (x0, win))
        self.assertTrue(win[1] - d - 1e-6 < y0 <= win[1] + 1e-6, (y0, win))
        self.assertTrue(win[0] + win[2] - 1e-6 <= x1 < win[0] + win[2] + d + 1e-6, (x1, win))
        self.assertTrue(win[1] + win[3] - 1e-6 <= y1 < win[1] + win[3] + d + 1e-6, (y1, win))
        # ...and the overlay knows the scale of that screen.
        self.assertAlmostEqual(ov._scale, screen[2])

    def _run_layout(self, layout_name):
        checked = 0
        for res, os_scale, dpr in display_scales():
            for name, screens, game_screen in layouts(res, dpr):
                if name != layout_name:
                    continue
                for win in windows_on(screens[game_screen]):
                    for gx, gy in grids_in(*win[2:]):
                        target = (gx, gy, 192, 128)
                        with self.subTest(res=res, os_scale=os_scale, dpr=dpr, layout=name,
                                          win=win, grid=target):
                            saved, ov = self._set_grid(screens, win, target)
                            self._check_overlay_covers_window(ov, screens, win)
                            self.assertIsNotNone(saved)
                            for got, want in zip(saved, target):
                                self.assertLessEqual(abs(got - want), 1, f"saved {saved}, puzzle at {target}")
                            frame = game_frame(win[2], win[3], gx, gy)
                            result = detector(saved).detect_grid(frame)
                            self.assertTrue(result.grid_found,
                                            f"bot did not find the puzzle at the saved grid {saved} "
                                            f"(score {result.confidence:.2f})")
                            checked += 1
        self.assertGreater(checked, 100)

    def test_one_screen(self):
        self._run_layout("one screen")

    def test_second_screen_right(self):
        self._run_layout("second screen right")

    def test_second_screen_left_negative_x(self):
        self._run_layout("second screen left")

    def test_second_screen_above_negative_y(self):
        self._run_layout("second screen above")

    def test_main_screen_scaled_second_not(self):
        self._run_layout("main screen scaled, second not")

    def test_matrix_covers_the_scales_users_have(self):
        dprs = {dpr for _, _, dpr in display_scales()}
        for needed in (1.0, 1.15, 1.25, 1.5, 2.0):
            self.assertIn(needed, dprs)
        self.assertTrue(any(d > 2.0 for d in dprs))

    def test_matrix_would_catch_the_2_0_3_bug(self):
        """What 2.0.3 saved (window pixels taken as Qt units) fails the
        matrix's check at every scale but 100 %, so the matrix really tells
        a correct overlay from that one. (The bot's grid check alone does
        not always: it took a grid a divider off near the puzzle's corner.)"""
        caught = 0
        for res, _os_scale, dpr in display_scales():
            if dpr < 1.05:
                continue
            for name, screens, game_screen in layouts(res, dpr):
                screen = screens[game_screen]
                win = next(iter(windows_on(screen)))
                for gx, gy in grids_in(*win[2:]):
                    with self.subTest(res=res, dpr=dpr, layout=name, win=win, grid=(gx, gy)):
                        model = ScreenModel(screens)
                        scr = model.screen_at(win[0] + win[2] / 2, win[1] + win[3] / 2)
                        # 2.0.3: the overlay sat at Qt (left, top), so its
                        # corner was at this pixel, and a pixel drag was 1/dpr.
                        ox, oy = model.to_pixels(win[0], win[1], scr)
                        old = (round((win[0] + gx - ox) / dpr), round((win[1] + gy - oy) / dpr),
                               round(192 / dpr), round(128 / dpr))
                        off = max(abs(a - b) for a, b in zip(old, (gx, gy, 192, 128)))
                        self.assertGreater(off, 1, old)
                        caught += 1
        self.assertGreater(caught, 100)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class RealQtScaleTests(unittest.TestCase):
    """Real Qt at each scale (QT_SCALE_FACTOR, read only at start, so one
    process per scale): the grid is drawn on the pixels it stands for and
    real mouse events save the pixels dragged to."""

    SCALES = ["1", "1.1", "1.15", "1.25", "1.5", "1.75", "2", "2.5"]

    def _probe(self, scale):
        env = dict(os.environ, PYTHONPATH="")
        out = subprocess.run([sys.executable, os.path.join(TESTS, "_overlay_render_probe.py"), scale],
                             capture_output=True, text=True, timeout=120, env=env)
        rows = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
        return rows, out

    def test_rendered_and_dragged_at_every_scale(self):
        with concurrent.futures.ThreadPoolExecutor(len(self.SCALES)) as pool:
            results = dict(zip(self.SCALES, pool.map(self._probe, self.SCALES)))
        for scale, (rows, out) in results.items():
            with self.subTest(scale=scale):
                self.assertEqual(len(rows), 4, out.stderr[-2000:])
                for row in rows:
                    s = row["scale"]
                    self.assertAlmostEqual(s, float(scale))
                    w, h = row["rect"][2:]
                    # The rendered overlay covers the game window, rounded
                    # outwards to whole Qt units at each side.
                    self.assertTrue(w <= row["render_size"][0] <= w + 2 * s + 1, row)
                    self.assertTrue(h <= row["render_size"][1] <= h + 2 * s + 1, row)
                    ox, oy = row["offset"]
                    for grid, box in ((row["shown"], row["drawn_start"]), (row["saved"], row["drawn_end"])):
                        self.assertIsNotNone(box, row)
                        # Rendered pixel + offset = game-window pixel. The
                        # border pen is 3 px wide, the corner handle 2 px out.
                        x0, y0, x1, y1 = box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy
                        gx, gy, gw, gh = grid
                        for got, want in ((x0, gx), (y0, gy), (x1, gx + gw), (y1, gy + gh)):
                            self.assertLessEqual(abs(got - want), 3, row)
                    # Real mouse events carry whole Qt units: off by at most one.
                    for got, want in zip(row["saved"], row["target"]):
                        self.assertLessEqual(abs(got - want), s + 1, row)


class QtGeometryTripwireTests(unittest.TestCase):
    """Every place in src/ that puts a Qt window somewhere on the screen or
    reads a position in Qt's screen coordinates. Qt units are not game
    pixels: a widget placed over a game window, or a click compared with a
    game position, must go through qt_gui.native_rect_to_logical (or the
    same rule). A new place fails this test until it is checked and added."""

    CALLS = {"setGeometry", "move", "mapToGlobal", "mapFromGlobal", "globalPosition", "globalPos",
             "setPos", "pos", "screenAt"}

    ALLOWED = {
        # Qt-only: drags the program's own window by its title bar.
        ("qt_gui.py", "Header.mousePressEvent", "globalPosition"),
        ("qt_gui.py", "Header.mouseMoveEvent", "globalPosition"),
        ("qt_gui.py", "Header.mouseMoveEvent", "move"),
        # Qt-only: a tooltip at the mouse.
        ("qt_gui.py", "*", "QCursor.pos"),
        # Over the game window: converted by native_rect_to_logical.
        ("qt_gui.py", "JigsawGridOverlay.__init__", "setGeometry"),
    }

    def _sites(self):
        found = set()
        for fname in sorted(os.listdir(SRC)):
            if not fname.endswith(".py"):
                continue
            with open(os.path.join(SRC, fname), encoding="utf-8") as f:
                tree = ast.parse(f.read())

            def walk(node, scope):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        walk(child, scope + [child.name])
                        continue
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        attr = child.func.attr
                        owner = child.func.value
                        if attr == "pos" and isinstance(owner, ast.Name) and owner.id == "QCursor":
                            found.add((fname, "*", "QCursor.pos"))
                        elif attr in self.CALLS - {"pos"}:
                            found.add((fname, ".".join(scope[-2:]) if scope else "<module>", attr))
                    walk(child, scope)

            walk(tree, [])
        return found

    def test_every_qt_screen_position_is_checked(self):
        sites = self._sites()
        new = sorted(sites - self.ALLOWED)
        self.assertEqual(new, [], "New Qt screen-geometry code. Qt units are scaled by the interface "
                                  "size and the DPI; game pixels are not. Convert with "
                                  "qt_gui.native_rect_to_logical where game or screen pixels are "
                                  "involved, then add the place to ALLOWED.")

    def test_the_list_is_not_stale(self):
        self.assertEqual(sorted(self.ALLOWED - self._sites()), [])


if __name__ == "__main__":
    unittest.main()

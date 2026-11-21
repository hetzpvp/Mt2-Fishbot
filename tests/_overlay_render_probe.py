"""Run by test_display_scaling.py in a process of its own, because Qt reads
QT_SCALE_FACTOR only when it starts: real Qt at the scale given in argv[1],
offscreen. For each case it opens the jigsaw grid overlay over a game
window, renders it, finds the drawn grid in the rendered pixels, drags the
grid with real Qt mouse events and confirms with Enter. Prints one JSON line
per case: where the grid was drawn and what was saved, both in game-window
pixels.

    python _overlay_render_probe.py SCALE"""

import json
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = sys.argv[1]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _stubs import install  # noqa: E402

install()

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
import qt_gui  # noqa: E402

# (window rect in screen pixels, grid shown at open, where the user drags it)
CASES = [
    ((569, 67, 1296, 839), [415, 307, 171, 116], [521, 361, 192, 128]),
    ((0, 0, 1296, 839), [300, 300, 192, 128], [900, 600, 192, 128]),
    ((137, 251, 1024, 768), [400, 300, 264, 176], [300, 400, 192, 128]),
    ((1921, 3, 1600, 900), [500, 400, 192, 128], [1100, 500, 210, 140]),
]


def gold_box(ov):
    """Bounding box (x0, y0, x1, y1) of the gold grid lines in the rendered
    overlay, in rendered pixels; the instruction panel is left out."""
    pm = ov.grab()
    img = pm.toImage().convertToFormat(pm.toImage().Format.Format_RGB888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * h)
    arr = arr.reshape(h, img.bytesPerLine())[:, :w * 3].reshape(h, w, 3)
    gold = (arr[:, :, 0] > 200) & (arr[:, :, 1] > 150) & (arr[:, :, 2] < 80)
    s = ov._scale
    gold[: int(100 * s), : int(510 * s)] = False      # instruction panel
    ys, xs = np.nonzero(gold)
    if len(xs) == 0:
        return None, (w, h)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())), (w, h)


def to_qt(ov, wx, wy):
    """A game-window pixel as the overlay's own Qt position, the way Qt 6
    maps a screen pixel: screen origin + (pixel - origin) / dpr, minus the
    overlay's Qt corner."""
    left, top = ov._probe_rect[:2]
    screen = ov.screen()
    g, dpr = screen.geometry(), screen.devicePixelRatio()
    gx = g.x() + (left + wx - g.x()) / dpr
    gy = g.y() + (top + wy - g.y()) / dpr
    return QPoint(int(round(gx - ov.geometry().x())), int(round(gy - ov.geometry().y())))


def drag(ov, start, end):
    QTest.mousePress(ov, Qt.LeftButton, Qt.NoModifier, to_qt(ov, *start))
    QTest.mouseMove(ov, to_qt(ov, *end))
    QTest.mouseRelease(ov, Qt.LeftButton, Qt.NoModifier, to_qt(ov, *end))


for rect, start_grid, target in CASES:
    ov = qt_gui.JigsawGridOverlay(*rect, start_grid, "METIN2")
    ov._probe_rect = rect
    ov.show()
    QApplication.processEvents()
    got = []
    ov.confirmed.connect(got.append)
    # Drawn where it is kept? (game-window pixels = rendered pixels + offset)
    drawn_start, size = gold_box(ov)
    shown = list(ov._grid)
    # The user resizes with the corner handle, then moves the grid.
    gx, gy, gw, gh = ov._grid
    drag(ov, (gx + gw - 5, gy + gh - 5), (gx + target[2] - 5, gy + target[3] - 5))
    gx, gy, gw, gh = ov._grid
    drag(ov, (gx + gw / 2, gy + gh / 2), (target[0] + gw / 2, target[1] + gh / 2))
    QApplication.processEvents()
    drawn_end, _ = gold_box(ov)
    QTest.keyClick(ov, Qt.Key_Return)
    QApplication.processEvents()
    print(json.dumps({
        "scale": ov._scale, "rect": rect, "offset": ov._offset, "render_size": size,
        "shown": shown, "drawn_start": drawn_start, "target": target,
        "drawn_end": drawn_end, "saved": got[0] if got else None,
    }), flush=True)

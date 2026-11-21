"""
README screenshots of the bot's window, made offscreen for the current
version (tools/release.py screenshots runs this at every release).

    python tools/readme_screenshots.py [OUT_DIR]      default: docs/

Writes the three pictures README.md shows:

    gui_demo.png       Dashboard, three game windows running
    gui_inventory.png  Positions tab, four pages and the action points set
    gui_settings.png   Fishing tab

Nothing touches the desktop: Qt renders offscreen (no display needed), the
game windows and the bots are fakes, and the window reads and writes a
throwaway bot_config.json (FISHBOT_CONFIG_DIR), never the real one.
"""

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs"))
NAMES = ("gui_demo.png", "gui_inventory.png", "gui_settings.png")
VERSION_KEY = "Fishbot-Version"   # PNG text chunk with the version shown

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["FISHBOT_MUTE"] = "1"
CFG_DIR = tempfile.mkdtemp(prefix="fishbot-shots-")
os.environ["FISHBOT_CONFIG_DIR"] = CFG_DIR
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import qt_gui  # noqa: E402
from version import VERSION  # noqa: E402
from window_manager import WindowManager  # noqa: E402

qt_gui.pyn_keyboard = None          # no global F5 hook
qt_gui.pyn_mouse = None
qt_gui.SolverCacheManager._cache_exists = classmethod(lambda cls: True)
qt_gui.SolverCacheManager.ensure_build = lambda self: None


class FakeWin:
    def __init__(self, hwnd):
        self._hWnd = hwnd
        self.isMinimized = False

    def activate(self):
        pass


OPEN = [(f"Metin2 ({i})", FakeWin(i)) for i in (1, 2, 3)]
WindowManager.get_all_windows = staticmethod(lambda: list(OPEN))
WindowManager.get_window_rect = lambda self: (0, 0, 1024, 768)
WindowManager.activate_window = lambda self, **k: None
for _n in ("warning", "information", "critical"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: QMessageBox.Ok))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
qt_gui.show_help = lambda *a, **k: None


class FakeBot:
    """Stands in for FishingBot: runs until stopped, does nothing."""
    STOP_MANUAL = "manual"
    STOP_BAIT_DEPLETED = "bait_depleted"

    @staticmethod
    def reset_session_state():
        pass

    def __init__(self, *_a, bot_id=0, **_k):
        self.bot_id = bot_id
        self.paused = False
        self.running = True

    def start(self):
        while self.running:
            time.sleep(0.02)

    def stop(self):
        self.running = False


def main():
    pages = {f"inv_page_{p}_pos": [880 + 26 * (p - 1), 236] for p in (1, 2, 3, 4)}
    cfg = {
        **pages,
        "confirm_button_pos": [470, 360],
        "drop_button_pos": [430, 330],
        "auto_fish_handling": True,
        "quick_skip": True,
        "quick_skip_mode": "horse",
        "human_like_clicking": True,
        "selected_windows": ["Metin2 (1)", "Metin2 (2)", "Metin2 (3)"] + [""] * 5,
        "ui_mode": "fishing",
    }
    with open(os.path.join(CFG_DIR, "bot_config.json"), "w") as f:
        json.dump(cfg, f)

    app = QApplication.instance() or QApplication([])
    qt_gui.set_accent(qt_gui.DEFAULT_ACCENT)
    app.setStyleSheet(qt_gui.build_qss())
    w = qt_gui.FishbotWindow()
    w.show()
    app.processEvents()
    w.notice.dismiss()

    qt_gui.FishingBot = FakeBot
    w.start_all_bots()
    app.processEvents()
    time.sleep(0.1)
    w._poll.stop()
    msgs = ["Keeping: Zander (ignored, 31 empty slots left)",
            "Fish caught! Waiting for the next bite",
            "Dropping: Redfeather"]
    for i, (games, bait) in enumerate([(143, 657), (128, 672), (97, 703)]):
        w._apply_row_stats(i, games, bait)
        w.dashboard.window_rows[i].setMessage(msgs[i])

    os.makedirs(OUT, exist_ok=True)

    def shot(tab, name):
        w.tabs_bar.setActive(tab)
        app.processEvents()
        path = os.path.join(OUT, name)
        img = w.grab().toImage()
        # release.py publish checks this stamp: no release with old pictures
        img.setText(VERSION_KEY, VERSION)
        if not img.save(path):
            raise SystemExit(f"could not save {path}")
        print(f"wrote {path} ({w.width()}x{w.height()})")

    shot("dashboard", NAMES[0])
    # The setup tabs are shown idle: while bots run their controls are locked
    w.stop_all_bots()
    app.processEvents()
    shot("inventory", NAMES[1])
    shot("activity", NAMES[2])
    shutil.rmtree(CFG_DIR, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:   # the fake bot threads keep the process alive
        print(f"screenshots failed: {e!r}", file=sys.stderr)
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)

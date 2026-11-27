#!/usr/bin/env python3
"""Plays one trailer part in the real bot GUI with a visible, human-moving cursor.

    .venv/bin/python tools/trailer/drive_gui.py PART --windows XID1,XID2,XID3,XID4 --out DIR \
        [--run SECONDS] [--dry]

PART: classic | minigame | jigsaw. The GUI is the real FishbotWindow (from
source) with a throwaway config in DIR/cfg (FISHBOT_CONFIG_DIR), never the
real bot_config.json. The cursor is the real desktop cursor (the bot's uinput
mouse): run it only while nobody uses the PC.

Writes DIR/events.log (wall clock: "t scene NAME", "t click x y button") and
DIR/stats.csv (every 0.5 s: per window games/bait or crates/boards, pill).
--dry: offscreen Qt with fake windows, prints every target instead of moving.
Exit code 0 = the bots ran for the whole --run time; 3 = a dialog blocked the
start; 4 = a bot stopped early.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import sys
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ap = argparse.ArgumentParser()
ap.add_argument("part", choices=["classic", "minigame", "jigsaw"])
ap.add_argument("--windows", default="")
ap.add_argument("--out", required=True)
ap.add_argument("--run", type=float, default=60.0)
ap.add_argument("--dry", action="store_true")
ap.add_argument("--gui-pos", default="", help="X,Y of the bot window (pixels); default: right side")
A = ap.parse_args()

OUT = os.path.abspath(A.out)
os.makedirs(os.path.join(OUT, "cfg"), exist_ok=True)
os.environ["FISHBOT_CONFIG_DIR"] = os.path.join(OUT, "cfg")
os.environ["FISHBOT_MUTE"] = "1"
if A.dry:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import qt_gui  # noqa: E402
from version import VERSION  # noqa: E402
from window_manager import WindowManager  # noqa: E402

XIDS = [int(x, 0) for x in A.windows.split(",") if x]

# Window-relative click points of the stock 800x600 client (both instances).
PAGE_TAB = {1: (673, 231), 2: (748, 231)}
PUZZLE_NORMAL_CRATE = (262, 137)      # crate in the Fish Puzzle window at jigsaw_pos 10,10
CONFIRM_YES = (360, 326)              # "Open this box?" -> Yes

# ------------------------------------------------------------------ seed config
seed = {"version": VERSION, "wizard_done_fishing": True, "wizard_done_jigsaw": True,
        "bait_keys": ["1"], "classic_fishing": False, "ui_mode": "fishing",
        "human_like_clicking": True}
if A.part in ("minigame", "jigsaw"):
    seed["bait_keys"] = ["1", "2", "3", "4"]
    seed["inv_page_1_pos"] = list(PAGE_TAB[1])
    seed["inv_page_2_pos"] = list(PAGE_TAB[2])
if A.part == "minigame":
    # fish put on the ground left of Macaquini, not at the window centre where she and her horse stand
    # (take v6: a drop click hit the horse, its menu covered the inventory and the bot stopped)
    seed["drop_ground_pos"] = [150, 470]
if A.part == "jigsaw":
    seed["jigsaw_solver_enabled"] = True
with open(os.path.join(OUT, "cfg", "bot_config.json"), "w") as f:
    json.dump(seed, f, indent=2)

# ------------------------------------------------------------------ logging
_ev = open(os.path.join(OUT, "events.log"), "a", buffering=1)


def event(s: str) -> None:
    _ev.write(f"{time.time():.3f} {s}\n")
    print(f"[drive] {s}", flush=True)


# ------------------------------------------------------------------ dry-run fakes
if A.dry:
    class FakeWin:
        def __init__(self, i):
            self._hWnd = i
            self.isMinimized = False
            self.left, self.top, self.width, self.height = ((i - 1) % 2) * 800, ((i - 1) // 2) * 420, 800, 600

        def activate(self):
            pass

        def minimize(self):
            pass

        def restore(self):
            pass

    FAKES = [(f"Metin2 ({i})", FakeWin(i)) for i in (1, 2, 3, 4)]
    WindowManager.get_all_windows = staticmethod(lambda: list(FAKES))
    XIDS = XIDS or [1, 2, 3, 4]
    qt_gui.pyn_keyboard = None
    qt_gui.pyn_mouse = None

    class Hand:      # prints instead of moving
        def __init__(self, log):
            self.log = log
            self.pos = (0, 0)

        def move_to(self, x, y, duration=None):
            self.pos = (x, y)
            print(f"   move {x:.0f},{y:.0f}")

        def click(self, button="left", hold=None):
            self.log(f"click {int(self.pos[0])} {int(self.pos[1])} {button}")

        def click_at(self, x, y, button="left", hold=None, settle=None):
            self.move_to(x, y)
            self.click(button)

        def pause(self, a=0.1, b=0.2):
            pass

        def wheel(self, n, at=None):
            print(f"   wheel {n}")

        def key(self, name, hold=0.08):
            self.log(f"key {name}")
else:
    from cursor_path import Hand  # noqa: E402


def win_rect(xid):
    if A.dry:
        for _, w in FAKES:
            if w._hWnd == xid:
                return w.left, w.top, w.width, w.height
    from platforms import x11
    w = x11.X11Window(xid)
    return w.left, w.top, w.width, w.height


# ------------------------------------------------------------------ GUI-thread calls
class Invoker(QObject):
    sig = Signal(object)

    def __init__(self):
        super().__init__()
        self.sig.connect(self._run, Qt.QueuedConnection)

    def _run(self, job):
        fn, box, ev = job
        try:
            box.append(fn())
        except Exception as e:      # noqa: BLE001
            box.append(e)
        ev.set()


def gui(fn, timeout=15.0):
    box, ev = [], threading.Event()
    INV.sig.emit((fn, box, ev))
    if not ev.wait(timeout):
        raise TimeoutError("GUI call timed out")
    if isinstance(box[0], Exception):
        raise box[0]
    return box[0]


def center(widget, fx=0.5, fy=0.5):
    def f():
        r = widget.rect()
        from PySide6.QtCore import QPoint
        p = widget.mapToGlobal(QPoint(int(r.width() * fx), int(r.height() * fy)))
        d = widget.devicePixelRatioF()
        return p.x() * d, p.y() * d
    return gui(f)


# ------------------------------------------------------------------ steps
def scene(name):
    event(f"scene {name}")


def click_widget(hand, getter, fx=0.5, fy=0.5, hold=None, label=""):
    w = gui(getter)
    ensure_visible(hand, w)
    x, y = center(w, fx, fy)
    if label:
        event(f"target {label}")
    hand.click_at(x, y, hold=hold)
    if A.dry:       # no real input offscreen: press it programmatically
        def press():
            from PySide6.QtWidgets import QAbstractButton, QComboBox
            if isinstance(w, QComboBox):
                return
            from PySide6.QtCore import QTimer
            if isinstance(w, QAbstractButton):
                QTimer.singleShot(0, w.click)       # may open a modal: don't block this call
            elif hasattr(w, "clicked"):
                QTimer.singleShot(0, w.clicked.emit)
        gui(press)
    hand.pause()


def ensure_visible(hand, widget):
    """Scroll its scroll area with the wheel until the widget is fully in view.
    When the wheel does not move the list (it did not in the fish dialog of
    the 2026-11-27 take: the clicks went on tiles hidden under the buttons),
    the list is scrolled directly, so nothing is ever clicked out of view."""
    def area():
        from PySide6.QtWidgets import QScrollArea
        p = widget.parentWidget()
        while p is not None and not isinstance(p, QScrollArea):
            p = p.parentWidget()
        return p

    def state():
        p = area()
        if p is None:
            return None
        vp = p.viewport()
        top = widget.mapTo(vp, widget.rect().topLeft()).y()
        bottom = top + widget.height()
        c = vp.mapToGlobal(vp.rect().center())
        d = vp.devicePixelRatioF()
        return top, bottom, vp.height(), (c.x() * d, c.y() * d), p.verticalScrollBar().value()
    stuck, seen = 0, False
    for _ in range(20):
        s = gui(state)
        if s is None:
            return
        top, bottom, h, c, value = s
        if top >= 0 and bottom <= h:
            seen = True
            break
        hand.wheel(-1 if bottom > h else 1, at=c)
        time.sleep(0.25)
        if gui(lambda: area().verticalScrollBar().value()) == value:
            stuck += 1
            if stuck >= 2:
                break
    if not seen:
        event("scroll_direct")
    gui(lambda: area().ensureWidgetVisible(widget, 0, 12))     # no-op when it is in view
    time.sleep(0.35 if not seen else 0.05)


def pick_windows(hand, w):
    scene("pick_windows")
    names = gui(lambda: {getattr(win, "_hWnd", None): n for n, win in WindowManager.get_all_windows()})
    rows = gui(lambda: list(w.dashboard.window_rows))
    if gui(lambda: rows[0].combo.count()) < len(XIDS) + 1:
        click_widget(hand, lambda: w.dashboard.refresh_btn, label="refresh")
        time.sleep(0.8)
    for i, xid in enumerate(XIDS):
        name = names.get(xid)
        if not name:
            raise RuntimeError(f"window {xid:#x} not in the bot's window list {names}")
        row = rows[i]
        click_widget(hand, lambda r=row: r.combo, fx=0.45, label=f"combo W{i + 1:02d}")
        time.sleep(0.35)

        def item_point(r=row, n=name):
            combo = r.combo
            idx = combo.findText(n)
            view = combo.view()
            rect = view.visualRect(view.model().index(idx, 0))
            p = view.viewport().mapToGlobal(rect.center())
            d = view.devicePixelRatioF()
            return idx, p.x() * d, p.y() * d
        idx, x, y = gui(item_point)
        if idx < 0:
            raise RuntimeError(f"{name} not in combo")
        if A.dry:
            gui(lambda r=row, k=idx: r.combo.setCurrentIndex(k))
        hand.click_at(x, y)
        hand.pause(0.2, 0.4)
        got = gui(lambda r=row: r.combo.currentText())
        if got != name:
            raise RuntimeError(f"W{i + 1:02d}: picked {got!r}, wanted {name!r}")


def set_page(hand, w, page):
    scene(f"page{page}")
    click_widget(hand, lambda: w.inventory.coord_pills[f"page{page}"], fx=0.35, label=f"pill page{page}")
    time.sleep(0.9)                         # the bot brings W01 forward, minimizes the others
    l, t, _, _ = win_rect(XIDS[0])
    rx, ry = PAGE_TAB[page]
    hand.click_at(l + rx, t + ry, hold=0.13)
    if A.dry:
        gui(lambda: w.config.__setitem__(f"inv_page_{page}_pos", [rx, ry]))
    for _ in range(40):
        if gui(lambda: w.config.get(f"inv_page_{page}_pos")):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError(f"page {page} was not captured")
    time.sleep(0.8)                         # the other clients come back


def tick(hand, w, checkbox, want=True, label=""):
    if gui(lambda: checkbox.isChecked()) == want:
        return
    click_widget(hand, lambda: checkbox, fx=0.08 if checkbox.text() else 0.5, label=label)


def press_start(hand, w):
    scene("start")
    click_widget(hand, lambda: w.dashboard.start_btn, label="start")
    for _ in range(25):
        time.sleep(0.2)
        modal = gui(lambda: QApplication.activeModalWidget())
        if modal is not None:
            text = gui(lambda: modal.findChild(qt_gui.QLabel).text() if modal.findChild(qt_gui.QLabel) else "")
            event(f"blocked_by_dialog {type(modal).__name__} {text!r}")
            gui(lambda: modal.grab().save(os.path.join(OUT, "blocking_dialog.png")))
            from PySide6.QtCore import QTimer
            gui(lambda: QTimer.singleShot(0, modal.reject))
            return False
    return True


# ------------------------------------------------------------------ stats
def snapshot(w):
    rows = []
    for r in w.dashboard.window_rows[:len(XIDS)]:
        rows.append({"a": r.bait_lbl.text(), "b": r.games_lbl.text(),
                     "pill": r.pill.text(),
                     "msg": r.msg_lbl.text() if hasattr(r.msg_lbl, "text") else ""})
    d = w.dashboard
    return {"kpi1": d.kpi_games._value.text(), "kpi3": d.kpi_bait._value.text(),
            "rows": rows, "running": bool(w.bots) or w.jigsaw.is_running()}


def stats_loop(w, stop_ev):
    path = os.path.join(OUT, "stats.csv")
    with open(path, "a", newline="") as f:
        cw = csv.writer(f)
        if f.tell() == 0:
            cw.writerow(["t", "running", "kpi1", "kpi3"] + [f"w{i + 1}_{k}" for i in range(len(XIDS))
                                                           for k in ("a", "b", "pill", "msg")])
        while not stop_ev.is_set():
            try:
                s = gui(lambda: snapshot(w), timeout=5)
                cw.writerow([f"{time.time():.3f}", int(s["running"]), s["kpi1"], s["kpi3"]]
                            + [r[k] for r in s["rows"] for k in ("a", "b", "pill", "msg")])
                f.flush()
            except Exception:       # noqa: BLE001
                pass
            stop_ev.wait(0.5)


# ------------------------------------------------------------------ tutorial helpers
INV_CELL0, CELL = (649, 260), 32               # window-relative centre of inventory cell 0, cell pitch
HOTBAR = (316, 567, 128, 30)                   # hotbar slots 1-4 (x, y, w, h), window-relative
PUZZLE = (10, 10, 288, 208)                    # Fish Puzzle window at jigsaw_pos 10,10
DIALOG = (233, 245, 334, 108)                  # the game's Yes/No dialog in the middle
HORSE_ICON = (6, 6, 30, 30)                    # the summoned horse's icon, top left of the game window


def cell_xy(i):
    return INV_CELL0[0] + CELL * (i % 5), INV_CELL0[1] + CELL * (i // 5)


def say(key):
    event(f"say {key}")


def rect_of(widget):
    def f():
        from PySide6.QtCore import QPoint
        p = widget.mapToGlobal(QPoint(0, 0))
        d = widget.devicePixelRatioF()
        return int(p.x() * d), int(p.y() * d), int(widget.width() * d), int(widget.height() * d)
    return gui(f)


def find_label(w, text, root=None):
    """The visible QLabel whose text contains ``text`` (case-insensitive)."""
    from PySide6.QtWidgets import QLabel

    def f():
        base = root or w
        for lbl in base.findChildren(QLabel):
            if lbl.isVisible() and text.lower() in lbl.text().lower():
                return lbl
        return None
    lbl = gui(f)
    if lbl is None:
        raise RuntimeError(f"no label {text!r}")
    return lbl


def hover(hand, widget, dwell=1.6, fx=0.5, fy=0.5, tag=""):
    """Rest the cursor on a widget for a moment and log its rectangle (highlight in the edit)."""
    ensure_visible(hand, widget)
    x, y, ww, hh = rect_of(widget)
    event(f"hl {x} {y} {ww} {hh} {tag}")
    hand.move_to(x + ww * fx, y + hh * fy, duration=0.8)
    time.sleep(dwell)


def hover_game(hand, rect, dwell=2.0, tag="", sweep=False):
    """Highlight a window-relative rectangle in W01 and point at it (sweep: glide along it)."""
    l, t, _, _ = win_rect(XIDS[0])
    x, y, ww, hh = rect
    event(f"hl {l + x} {t + y} {ww} {hh} {tag}")
    if sweep:
        hand.move_to(l + x + 8, t + y + hh / 2, duration=0.9)
        hand.move_to(l + x + ww - 8, t + y + hh / 2, duration=max(1.2, dwell * 0.6))
        time.sleep(max(0.4, dwell * 0.4))
    else:
        hand.move_to(l + x + ww / 2, t + y + hh / 2, duration=0.9)
        time.sleep(dwell)


def click_game(hand, rx, ry, button="left"):
    l, t, _, _ = win_rect(XIDS[0])
    hand.click_at(l + rx, t + ry, button=button, hold=0.14)


def wait_config(w, key, what):
    if A.dry:
        gui(lambda: w.config.__setitem__(key, [1, 1]))
    for _ in range(60):
        if gui(lambda: w.config.get(key)):
            return
        time.sleep(0.1)
    raise RuntimeError(f"{what} was not captured")


def preselect(w):
    """Windows and pages set earlier (previous part): set them without the cursor."""
    names = gui(lambda: {getattr(win, "_hWnd", None): n for n, win in WindowManager.get_all_windows()})
    if gui(lambda: w.dashboard.window_rows[0].combo.count()) < len(XIDS) + 1:
        gui(lambda: w.dashboard.refresh_btn.click())
        time.sleep(1.0)
        names = gui(lambda: {getattr(win, "_hWnd", None): n for n, win in WindowManager.get_all_windows()})
    for i, xid in enumerate(XIDS):
        n = names[xid]
        gui(lambda r=w.dashboard.window_rows[i], n=n: r.combo.setCurrentIndex(r.combo.findText(n)))
    time.sleep(0.5)


def slow_start(hand, w, key="start"):
    click_widget(hand, lambda: w.tabs_bar._buttons["dashboard"], label="tab dashboard")
    time.sleep(0.8)
    say(key)
    btn = gui(lambda: w.dashboard.start_btn)
    x, y, ww, hh = rect_of(btn)
    event(f"hl {x} {y} {ww} {hh} start")
    hand.move_to(x + ww / 2, y + hh / 2, duration=1.6)
    time.sleep(1.8)
    scene("start")
    hand.click(hold=0.1)
    for _ in range(25):
        time.sleep(0.2)
        modal = gui(lambda: QApplication.activeModalWidget())
        if modal is not None:
            text = gui(lambda: modal.findChild(qt_gui.QLabel).text() if modal.findChild(qt_gui.QLabel) else "")
            event(f"blocked_by_dialog {type(modal).__name__} {text!r}")
            gui(lambda: modal.grab().save(os.path.join(OUT, "blocking_dialog.png")))
            from PySide6.QtCore import QTimer
            gui(lambda: QTimer.singleShot(0, modal.reject))
            return False
    return True


# ------------------------------------------------------------------ the parts
def part_classic(hand, w):
    hand.speed = 0.85
    say("windows")
    hover(hand, find_label(w, "game windows"), 2.2, fx=0.2, tag="title")
    pick_windows(hand, w)
    time.sleep(1.0)

    click_widget(hand, lambda: w.tabs_bar._buttons["inventory"], label="tab positions")
    time.sleep(0.6)
    say("pages")
    hover(hand, find_label(w, "inventory page tabs"), 3.0, fx=0.2, tag="title")
    for page in (1, 2):
        scene(f"page{page}")
        say(f"page{page}")
        click_widget(hand, lambda p=page: w.inventory.coord_pills[f"page{p}"], fx=0.35, label=f"pill page{page}")
        time.sleep(1.8)                     # the bot brings W01 forward, minimizes the others
        rx, ry = PAGE_TAB[page]
        hover_game(hand, (rx - 36, ry - 10, 72, 20), 1.6, tag=f"tab{page}")
        click_game(hand, rx, ry)
        wait_config(w, f"inv_page_{page}_pos", f"page {page}")
        time.sleep(2.0)                     # the other clients come back
    click_game(hand, *PAGE_TAB[1])          # back to page I, where the catches land
    time.sleep(0.8)

    click_widget(hand, lambda: w.tabs_bar._buttons["activity"], label="tab fishing")
    time.sleep(0.6)
    scene("classic_tick")
    say("classic")
    hover(hand, w.settings.classic_check, 3.2, fx=0.1, tag="classic")
    tick(hand, w, w.settings.classic_check, True, "classic")
    time.sleep(1.2)

    scene("bait_keys")
    say("bait")
    hover(hand, find_label(w, "bait keys"), 2.4, fx=0.2, tag="title")
    for k in ("2", "3", "4"):
        tick(hand, w, w.settings.bait_checks[k], True, f"bait {k}")
        time.sleep(0.5)
    time.sleep(0.8)
    say("bait_hotbar")
    hover_game(hand, HOTBAR, 3.2, tag="hotbar", sweep=True)
    time.sleep(0.6)


def part_minigame(hand, w):
    hand.speed = 0.85
    preselect(w)
    scene("part_minigame_shown")
    say("kept")
    hover(hand, find_label(w, "game windows"), 2.6, fx=0.2, tag="title")

    click_widget(hand, lambda: w.tabs_bar._buttons["activity"], label="tab fishing")
    time.sleep(0.6)
    say("classic_off")
    hover(hand, w.settings.classic_check, 3.0, fx=0.1, tag="classic")
    tick(hand, w, w.settings.classic_check, False, "classic off")

    scene("quick_skip")
    say("quickskip")
    hover(hand, find_label(w, "quick skip"), 3.4, fx=0.2, tag="title")
    tick(hand, w, w.settings.qs_enable, True, "quick skip on")
    time.sleep(0.4)
    if not gui(lambda: w.settings.qs_horse.isChecked()):
        tick(hand, w, w.settings.qs_horse, True, "horse")
    else:
        hover(hand, w.settings.qs_horse, 1.2, fx=0.15, tag="horse")
    say("quickskip_item")
    hover_game(hand, HORSE_ICON, 3.0, tag="horse")

    scene("fish_handling")
    say("fishhandling")
    hover(hand, find_label(w, "automatic fish handling"), 3.0, fx=0.2, tag="title")
    tick(hand, w, w.settings.auto_check, True, "auto fish")
    time.sleep(0.5)
    hover(hand, w.settings.configure_fish_btn, 1.2, tag="btn")
    click_widget(hand, lambda: w.settings.configure_fish_btn, label="configure fish")
    time.sleep(1.4)
    modal = gui(lambda: QApplication.activeModalWidget())
    if modal is None:
        raise RuntimeError("fish dialog did not open")
    say("fishmodal")
    time.sleep(1.6)
    click_widget(hand, lambda: modal.tabs._buttons["fish"], label="fish tab")
    time.sleep(1.0)
    for asset in ("Zander_living.jpg", "Carp_living.jpg", "Catsfish_living.jpg"):
        tile = gui(lambda a=asset: modal._tiles[a])
        hover(hand, tile, 0.6, tag="tile")
        click_widget(hand, lambda t=tile: t.seg._buttons["drop"], label=f"drop {asset}")
        time.sleep(0.7)
    time.sleep(1.2)
    from PySide6.QtWidgets import QPushButton
    save = gui(lambda: next(b for b in modal.findChildren(QPushButton) if b.text().strip().lower() == "save"))
    hover(hand, save, 0.8, tag="btn")
    click_widget(hand, lambda: save, label="save")
    time.sleep(1.0)

    scene("drop")
    click_widget(hand, lambda: w.tabs_bar._buttons["inventory"], label="tab positions")
    time.sleep(0.6)
    say("drop")
    hover(hand, find_label(w, "action points"), 2.6, fx=0.2, tag="title")
    zx, zy = cell_xy(4)                     # the throwaway Zander
    hover_game(hand, (zx - 16, zy - 16, 32, 32), 1.4, tag="zander")
    click_game(hand, zx, zy)                # pick it up
    time.sleep(0.6)
    click_game(hand, 300, 470)              # put it on the ground: "Do you want to drop Zander?"
    time.sleep(1.2)
    say("drop_confirm")
    click_widget(hand, lambda: w.inventory.coord_pills["confirm"], fx=0.35, label="pill confirm")
    time.sleep(1.6)
    hover_game(hand, (CONFIRM_YES[0] - 30, CONFIRM_YES[1] - 11, 60, 22), 1.4, tag="yes")
    click_game(hand, *CONFIRM_YES)
    wait_config(w, "confirm_button_pos", "confirm")
    time.sleep(2.0)


def part_jigsaw(hand, w):
    hand.speed = 0.85
    preselect(w)
    scene("part_jigsaw_shown")
    say("mode")
    hover(hand, gui(lambda: w.mode_bar._buttons[qt_gui.MODE_JIGSAW]), 1.8, tag="mode")
    click_widget(hand, lambda: w.mode_bar._buttons[qt_gui.MODE_JIGSAW], label="mode jigsaw")
    time.sleep(0.8)
    click_widget(hand, lambda: w.tabs_bar._buttons["activity"], label="tab jigsaw")
    time.sleep(0.8)
    scene("jigsaw_mode")
    say("grid")
    hover(hand, find_label(w, "puzzle grid"), 2.4, fx=0.2, tag="title")
    hover_game(hand, PUZZLE, 2.6, tag="puzzle")
    scene("placement")
    say("placement")
    hover_game(hand, DIALOG, 3.6, tag="middle")
    hover_game(hand, PUZZLE, 2.0, tag="puzzle")

    scene("set_confirm")
    say("confirm")
    hover(hand, find_label(w, "confirm"), 2.0, fx=0.2, tag="title")
    click_game(hand, *PUZZLE_NORMAL_CRATE)  # "Open this box?"
    time.sleep(1.2)
    hover_game(hand, DIALOG, 1.4, tag="dialog")
    click_widget(hand, lambda: w.jigsaw_tab.set_confirm_btn, label="set confirm")
    time.sleep(1.6)
    hover_game(hand, (CONFIRM_YES[0] - 30, CONFIRM_YES[1] - 11, 60, 22), 1.2, tag="yes")
    click_game(hand, *CONFIRM_YES)
    wait_config(w, "confirm_button_pos", "confirm")
    time.sleep(1.5)


PARTS = {"classic": part_classic, "minigame": part_minigame, "jigsaw": part_jigsaw}


def worker(w, app, result):
    hand = Hand(log=event) if A.dry else Hand(log=event)
    stop_stats = threading.Event()
    try:
        time.sleep(2.5)
        scene(f"part_{A.part}")
        PARTS[A.part](hand, w)
        if not slow_start(hand, w):
            result["code"] = 3
            return
        # park the cursor out of the way; from here the bot moves it
        hand.move_to(1620, 1395 if not A.dry else 0)
        scene("running")
        threading.Thread(target=stats_loop, args=(w, stop_stats), daemon=True).start()
        t_end = time.time() + A.run
        f5_at = time.time() + 40 if A.part == "minigame" else None
        while time.time() < t_end:
            time.sleep(0.5)
            if f5_at and time.time() >= f5_at:
                f5_at = None
                scene("f5_pause")
                say("f5")
                hand.key("f5")
                time.sleep(4.0)
                scene("f5_resume")
                hand.key("f5")
            if not A.dry and not gui(lambda: bool(w.bots) or w.jigsaw.is_running()):
                event("bots_stopped_early")
                result["code"] = 4
                break
        scene("stop")
        gui(lambda: w.dashboard.stop_btn.click() if w.dashboard.stop_btn.isVisible() else None)
        time.sleep(3.0)
    except Exception:       # noqa: BLE001
        event("error " + traceback.format_exc().replace("\n", " | "))
        result["code"] = 2
    finally:
        stop_stats.set()
        time.sleep(0.6)
        code = result["code"]
        threading.Timer(8.0, lambda: (event(f"exit {code} (forced)"), os._exit(code))).start()
        try:
            gui(lambda: app.quit(), timeout=5)
        except Exception:       # noqa: BLE001
            pass


def main():
    app = qt_gui._apply_ui_scale(QApplication(sys.argv))
    qt_gui.set_accent(qt_gui._saved_setting("accent_color", qt_gui.DEFAULT_ACCENT))
    app.setPalette(qt_gui.dark_palette(app.palette()))
    app.setStyleSheet(qt_gui.build_qss())
    global INV
    INV = Invoker()
    w = qt_gui.FishbotWindow()
    w.show()
    d = w.devicePixelRatioF()
    if A.gui_pos:
        gx, gy = [int(v) for v in A.gui_pos.split(",")]
    else:
        scr = app.primaryScreen().geometry()
        gx = int(scr.width() * d - w.width() * d - 14)
        gy = int(scr.height() * d - w.height() * d - 70)
    w.move(int(gx / d), int(gy / d))
    event(f"gui_rect {gx} {gy} {int(w.width() * d)} {int(w.height() * d)} dpr {d}")
    result = {"code": 0}
    threading.Thread(target=worker, args=(w, app, result), daemon=True).start()
    app.exec()
    event(f"exit {result['code']}")
    return result["code"]


if __name__ == "__main__":
    sys.exit(main())

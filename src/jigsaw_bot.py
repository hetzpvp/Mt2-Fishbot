"""
Automation loop for the Metin2 Fishing Jigsaw event minigame.
"""

from __future__ import annotations

import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace as dc_replace
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import cv2
import numpy as np

from jigsaw_detector import CrateSlotState, CrateTarget, JigsawActionResult, JigsawDetector, JigsawGridResult
import diag
from humanize import Humanizer
from input_backend import create_mouse_backend
import screen_capture
from screen_capture import WindowCapture, frame_usable
import inventory_layout
import dialog_watch
from utils import DEBUG_MODE_EN, DEBUG_PRINTS, get_project_root, get_resource_path, input_lock
from window_manager import WindowManager

try:
    from pynput import keyboard
    from pynput.keyboard import Key
except ImportError:
    keyboard = None
    Key = None


# Shared F5-pause listener: one OS hook for all bots, fans out to subscribers.
# Per-bot listeners would otherwise multiply with the number of windows.
_listener_lock = threading.Lock()
_listener_subscribers: List["JigsawBot"] = []
_shared_listener: Optional["keyboard.Listener"] = None


_F5_DEBOUNCE = 0.5          # seconds: a held F5 repeats, and each repeat would toggle again
_last_f5 = float("-inf")


def _shared_on_press(key) -> None:
    global _last_f5
    if Key is None or key != Key.f5:
        return
    with _listener_lock:
        now = time.monotonic()
        if now - _last_f5 < _F5_DEBOUNCE:
            return
        _last_f5 = now
        subscribers = list(_listener_subscribers)
    target = pause_target([bot.paused for bot in subscribers])
    for bot in subscribers:
        try:
            bot._on_f5_pressed(target)
        except Exception:
            diag.exc()
            pass


def pause_target(paused_states: Iterable[bool]) -> bool:
    """One F5 / PAUSE decision for all windows: True (pause everything) while
    any window is still running, False (resume everything) only when all are
    paused. The old rule, "resume when any window is paused", turned F5 into
    a resume as soon as one window had paused itself (puzzle not found):
    the others went on clicking however often F5 was pressed (diag
    20261122_091635)."""
    states = list(paused_states)
    return not states or not all(states)


class InputInterrupted(BaseException):
    """Raised by the input gate (Humanizer.gate) when the bot was paused or
    stopped in the middle of a round: the move or click is not sent, and
    the round ends there. A BaseException, so the ``except Exception``
    guards around single steps do not swallow it."""


def _register_pause_subscriber(bot: "JigsawBot") -> None:
    global _shared_listener
    if keyboard is None or Key is None:
        return
    with _listener_lock:
        if bot not in _listener_subscribers:
            _listener_subscribers.append(bot)
        if _shared_listener is None:
            _shared_listener = keyboard.Listener(on_press=_shared_on_press)
            _shared_listener.daemon = True
            _shared_listener.start()


def _unregister_pause_subscriber(bot: "JigsawBot") -> None:
    global _shared_listener
    with _listener_lock:
        if bot in _listener_subscribers:
            _listener_subscribers.remove(bot)
        if not _listener_subscribers and _shared_listener is not None:
            try:
                _shared_listener.stop()
            except Exception:
                diag.exc()
                pass
            _shared_listener = None


_ROOT = get_project_root()

from jigsaw_solver.deterministic import get_solver  # noqa: E402
from jigsaw_solver.jigsaw import FIGURES, Jigsaw, N, SKIP_ACTION  # noqa: E402

FULL_BOARD_MASK = (1 << (N * 6)) - 1


@dataclass(frozen=True)
class PieceSpec:
    piece_id: str
    mask: int
    figure_index: Optional[int]
    max_offset: Tuple[int, int]


def _mask_from_offsets(offsets: Iterable[Tuple[int, int]]) -> int:
    mask = 0
    for x, y in offsets:
        action = x * N + y
        mask |= (1 << (N * 6 - 1)) >> action
    return mask


@dataclass
class LayoutProblem:
    """A place the bot would click or rest the cursor that is not safe."""
    what: str            # "confirm" or "park"
    lies_on: str         # "puzzle grid", "normal crate slot", "special crate slot", "inventory"
    point: Tuple[int, int]
    message: str         # what is wrong and what to do, for the user
    help_topic: str      # help_visuals topic that shows it


# dialog_watch.PATCH half width, plus a margin: the watched Confirm area
CONFIRM_PATCH_HALF_W = 20 + 6


def piece_watch_point(confirm: Tuple[int, int], grid_w: Optional[float],
                      win_w: Optional[int]) -> Optional[Tuple[int, int]]:
    """Where a held piece leaves the watched Confirm area uncovered: far
    enough beside Confirm for the widest piece (3 cells) drawn at the cursor.
    Right of Confirm, else left of it; None when neither fits in the window."""
    cx, cy = int(confirm[0]), int(confirm[1])
    try:
        cell = float(grid_w) / 6.0
    except (TypeError, ValueError):
        cell = JigsawDetector.DEFAULT_GRID_SIZE[0] / 6.0
    half_piece = int(round(1.5 * cell))
    offset = CONFIRM_PATCH_HALF_W + half_piece + 1
    if win_w is None or cx + offset + half_piece < win_w:
        return (cx + offset, cy)
    if cx - offset - half_piece >= 0:
        return (cx - offset, cy)
    return None


def rest_point(config: dict, grid_w: Optional[float], win_w: Optional[int]) -> Optional[Tuple[int, int]]:
    """Where the cursor rests between clicks, holding a piece or not. The
    discard dialog is watched with the piece there (piece_watch_point), so a
    discard right-clicks where the cursor already is: resting 24 px right of
    Confirm instead, every discard went to the watch point, back for the
    right-click and over again (diag 20261122_093507)."""
    confirm = config.get("confirm_button_pos")
    try:
        cx, cy = int(confirm[0]), int(confirm[1])
    except (TypeError, ValueError, IndexError):
        return None
    point = piece_watch_point((cx, cy), grid_w, win_w)
    if point is not None:
        return point
    return (cx + int(config.get("jigsaw_post_crate_cursor_right_px", 24)), cy)


def layout_problem(config: dict, win_w: Optional[int] = None, win_h: Optional[int] = None,
                   grid_bounds=None, crate_slots: Optional[Dict[str, Tuple[int, int]]] = None
                   ) -> Optional[LayoutProblem]:
    """Checks the jigsaw layout before a start: the Confirm point and the
    point the cursor rests at (beside it, holding a piece) must lie clear of
    the puzzle grid, the crate slots and the inventory. The bot clicks the
    Confirm point whenever it believes a dialog is up, and parks a held piece
    at the other point: over the grid either one would place a piece blind.

    The usual cause is the puzzle window itself: the game opens its confirm
    dialog ("open the crate?") in the middle of the screen, so a puzzle
    window placed under it has the dialog's buttons over its grid.
    ``grid_bounds`` / ``crate_slots``: where the puzzle window was found in
    this game window (default: the grid defined by hand, slots placed
    relative to it).
    Returns None when the layout is fine or cannot be checked."""
    confirm = config.get("confirm_button_pos")
    bounds = grid_bounds if grid_bounds is not None else config.get("jigsaw_grid_bounds")
    try:
        cx, cy = int(confirm[0]), int(confirm[1])
        gx, gy, gw, gh = [int(round(float(v))) for v in bounds]
    except (TypeError, ValueError, IndexError):
        return None
    park = rest_point(config, gw, win_w) or (cx, cy)
    detector = JigsawDetector(config)
    slots = detector._crate_slot_centers(JigsawGridResult(True, (gx, gy, gw, gh), [], 0, 1.0,
                                                          crate_slots=crate_slots))
    strip = None
    if win_w and win_h:
        try:
            rect, source = inventory_layout.strip_rect(
                config, int(win_w), int(win_h),
                int(config.get("inventory_capture_width", inventory_layout.FALLBACK_WIDTH)),
                int(config.get("inventory_y_offset", inventory_layout.FALLBACK_TOP)))
            if source == "tabs":
                strip = rect
        except Exception:
            diag.exc("layout check: inventory strip")
    pad = 6
    fix_window = ("Move the puzzle window away from the middle of the game screen (for example to the "
                  "top left), so the game's confirm dialog no longer covers it. Then press Set Confirm "
                  "again (and Define Grid, if you defined the grid by hand).")
    for what, (x, y) in (("confirm", (cx, cy)), ("park", park)):
        lies_on = None
        if gx - pad <= x < gx + gw + pad and gy - pad <= y < gy + gh + pad:
            lies_on = "puzzle grid"
        else:
            for section, (sx, sy) in slots.items():
                if abs(x - sx) <= 22 and abs(y - sy) <= 20:
                    lies_on = f"{section} crate slot"
                    break
        if lies_on is None and strip is not None:
            ix, iy, iw, ih = strip
            if ix <= x < ix + iw and iy <= y < iy + ih:
                lies_on = "inventory"
        if lies_on is None:
            continue
        if what == "confirm":
            head = (f"The Confirm point ({x}, {y}) lies on the {lies_on}. The bot clicks it every time it "
                    f"confirms a dialog; there it would click the {lies_on} instead whenever the dialog is "
                    f"slow to appear.")
        else:
            head = (f"While it holds a piece, the bot rests the cursor beside the Confirm point, at "
                    f"({x}, {y}) — that is on the {lies_on}, where the held piece could be dropped by "
                    f"accident.")
        if lies_on == "inventory":
            fix = ("Move the puzzle window so the game's confirm dialog does not open over the inventory "
                   "(or move the inventory), then press Set Confirm again.")
        else:
            fix = fix_window
        return LayoutProblem(what, lies_on, (x, y), f"{head}\n\n{fix}", "jigsaw_layout")
    return None


class JigsawBot:
    """Runs one jigsaw automation worker for one game window."""

    _CAPTURE_STOP_AFTER = 30.0   # seconds without a usable frame before the worker stops

    def __init__(
        self,
        window_manager: WindowManager,
        config: dict,
        bot_id: int = 0,
        on_status_update=None,
        on_progress_update=None,
        on_bot_stop=None,
        on_pause_change=None,
    ):
        self.window_manager = window_manager
        self.config = config
        self.bot_id = bot_id
        self.diag_tag = f"W{bot_id + 1}"
        try:
            window_manager.diag_tag = self.diag_tag
        except Exception:
            pass
        self.on_status_update = on_status_update
        self.on_progress_update = on_progress_update
        self.on_bot_stop = on_bot_stop
        self.on_pause_change = on_pause_change
        self.detector = JigsawDetector(config)
        self.capture = WindowCapture(
            window_manager, config.get("capture_backend", "auto"), log=self._status)
        self.running = False
        self.paused = False
        self.solver = None
        self.dry_run = bool(config.get("jigsaw_dry_run", False))
        self.debug_dir = os.path.join(_ROOT, "debug_jigsaw")
        self.pieces = self._build_piece_registry()
        self.crates_completed = 0
        self.confirm_button_pos = self._configured_point("confirm_button_pos")
        self.discard_action_pos = self._configured_point("drop_button_pos")
        # Inventory strip: below the configured page tabs (see _inventory_rect).
        # These two only describe the fixed strip used when no tab is set.
        self._inventory_width = int(config.get("inventory_capture_width", inventory_layout.FALLBACK_WIDTH))
        self._inventory_y_offset = int(config.get("inventory_y_offset", inventory_layout.FALLBACK_TOP))
        self._inv_rect: Optional[Tuple[int, int, int, int]] = None
        self._tab_check_seen = False
        self._tab_check_misses = 0
        self._tab_check_off = False
        self._current_inv_page = 0
        # True once the inventory was seen to have room for this board's
        # reward; looked at again when the next board starts.
        self._room_checked = False
        self._crate_template_cache: Dict[str, List[Tuple[str, np.ndarray, int, int]]] = {}
        self._debug_lock = threading.Lock()
        self._debug_events: List[dict] = []
        self._debug_snapshot: dict = {"status": "Created"}
        self.step_mode = False
        self._step_event = threading.Event()
        self._last_detected_piece_id: Optional[str] = None
        self._last_detected_figure_index: Optional[int] = None
        # Crate kinds that were searched for in every inventory page and not
        # found: not searched again in this run.
        self._exhausted_sections: Set[str] = set()
        self._layout_checked_for: Optional[Tuple[int, int, int, int]] = None   # grid the layout was checked for
        self._check_hand: Optional[Tuple[int, int]] = None  # look for a held piece here before the next crate
        self._crate_confirm_baseline: Optional[np.ndarray] = None  # confirm area before the last crate click
        self._confirm_clear: Optional[np.ndarray] = None   # confirm area last seen with no dialog up
        self._hand_rounds = 0           # rounds played from the hand since the last crate
        self._unknown_streak = 0        # consecutive crates without a recognizable piece
        self._unverified_streak = 0     # consecutive placements the board never showed
        self._refused_streak = 0        # placements in a row the game did not accept (piece discarded)
        self._discard_streak = 0        # rounds in a row that ended in a discard of any kind
        self._suspect_mask = 0          # cells a refused placement aimed at: treated as filled
        self._suspect_board = 0         # the board read when they became suspect
        # Servers differ in how the click becomes a placement (see
        # _note_landing_shift and _bbox_refusal). Both are learned from what
        # the game does, per window and per run, and change nothing on a
        # server that places pieces where they are aimed.
        self._landing_shift_seen: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        self._landing_correction: Dict[Tuple[int, int], Tuple[int, int]] = {}
        self._bbox_fit = False          # this client wants a piece's whole bounding box free
        self._bbox_disproven = False    # a piece was accepted with a filled cell in its bounding box
        self._last_click_sent = False   # the last _click_window_point really clicked
        self._result_baseline = None    # {OK point: its area with no dialog up} (taken before a crate)
        self._last_placement_matched = False   # the last placement showed up exactly as predicted
        self.boards_completed = 0
        self._switched = False          # the last _focus() was a real window switch
        self._capture_bad_since: Optional[float] = None   # first unusable full-window frame in a row
        self.stop_reason: Optional[str] = None
        self._last_focus_log = 0.0
        # How a click is sent (see _press). The game reads the mouse once per
        # frame: a press and release in the same millisecond, 12 ms after the
        # cursor jumped onto a button, was lost on a real client (diag
        # 20261122_002413). The fishing bot's UI click (cursor settles 100 ms,
        # button held 50 ms) always worked, but four such clicks per crate
        # were ~60 % of the ~1 s each crate held the one mouse, so four
        # windows opened no more crates than one (diag 20261122_132850). A
        # click in the window that already has the focus rests 40 ms; the
        # first one after a focus switch keeps the full 100 ms (the game has
        # to notice the cursor first). A quick click the game does not take
        # brings back the careful timing for a while (_click_lost).
        self._glide = max(0.0, float(config.get("jigsaw_cursor_glide", 0.0)))
        self._hover_settle = max(0.0, float(config.get("jigsaw_click_hover", 0.04)))
        self._switch_hover = max(0.0, float(config.get("jigsaw_click_hover_switch", 0.10)))
        self._click_hold = max(0.0, float(config.get("jigsaw_click_hold", 0.035)))
        self._release_settle = max(0.0, float(config.get("jigsaw_click_release", 0.02)))
        self._careful_hover = max(self._hover_settle, self._switch_hover)
        self._careful_hold = max(self._click_hold, float(config.get("jigsaw_click_hold_careful", 0.05)))
        self._last_click_quick = False  # the last press went out with the quick timing
        # (cursor_rel, Confirm-area baseline, watch point, deadline) of a
        # discard whose piece is checked gone when the next round starts.
        self._discard_check: Optional[tuple] = None
        # Does this server ask "Place the piece here?" after a click on the
        # board? Learned once (see _place_click) and saved; None until known.
        self.on_setting_learned = None   # callback(key, value): the GUI saves it in the settings
        saved = self._setting("jigsaw_place_dialog")
        self._place_dialog: Optional[bool] = saved if isinstance(saved, bool) else None
        self._place_baseline: Optional[np.ndarray] = None   # Confirm area right before the last placement click
        self._last_place_confirmed = False   # the last placement was answered on its dialog
        self._result_before: Optional[np.ndarray] = None   # gray window before the last piece of a board
        self._learned_result_point: Optional[Tuple[int, int]] = None   # where the result OK was found last
        self._stale_dialog_rounds = 0   # rounds in a row that found a dialog still open before a crate
        # The last crate click brought up its dialog (and Yes went out): True;
        # it did not: False; nothing could be clicked (window not in front): None.
        self._crate_dialog_seen: Optional[bool] = True
        self._no_dialog_rounds = 0      # rounds in a row whose crate never brought up a dialog
        # This bot holds input_lock for its whole round (see _begin_round_hold).
        self._round_held = False
        # Debug snapshots/events are expensive (frame copies, lock); skip when no UI is attached.
        self._debug_consumer = False

        # Humanizer — when disabled, wrappers forward directly to pyautogui (current behavior).
        self.mouse_backend = create_mouse_backend(
            config,
            status_callback=self.on_status_update,
            bot_id=self.bot_id,
        )
        self.human = Humanizer(
            enabled=bool(config.get("human_like_clicking", False)),
            mouse_backend=self.mouse_backend,
        )
        self.human.diag_tag = self.diag_tag
        self.human.window_rect = getattr(window_manager, "get_window_rect", None)
        self.human.gate = self._input_gate

    def start(self) -> None:
        diag.set_thread_tag(self.diag_tag)
        if diag.ENABLED:
            self._diag_header()
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] start() called; dry_run={self.dry_run}")
        self.running = True

        # Subscribe to the shared F5-pause listener (one OS hook for all bots).
        _register_pause_subscriber(self)
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] subscribed to shared keyboard listener (F5 to pause/resume)")

        try:
            if self.confirm_button_pos is None:
                self._status("Jigsaw bot requires Confirm Button Coords in Fishbot action coordinates")
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] start() aborted: confirm_button_pos not configured")
                return
            if not self.config.get("jigsaw_grid_bounds") and not self.detector.auto_grid():
                self._status("Jigsaw bot requires Define Grid setup before starting")
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] start() aborted: jigsaw_grid_bounds not configured")
                return
            screen_capture.register_active(self.capture)
            self._status("Jigsaw bot started")
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] loading solver…")
            self.solver = get_solver()
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] solver loaded; entering run loop")
            self._activate_window_for_jigsaw("Activate jigsaw window before solver loop")
            if not self._refuse_overlapping_windows():
                return
            if self.detector.auto_grid():
                # Where the puzzle window is in this game window, for the
                # layout check (not found yet: checked when it is found).
                frame = self.capture_full_window()
                if frame is not None:
                    self.detector.detect_grid(frame)
            problem = self._point_layout_problem()
            if problem:
                self.stop_reason = "bad_layout"
                diag.event("DECISION", "STOP reason=bad_layout", kind="stop", tag=self.diag_tag, recent=True)
                self._status(f"Not started: {problem}")
                return
            self._run_loop()
        except Exception as e:
            # Without this the worker thread died silently (the traceback goes
            # to a console the packaged app does not have).
            diag.exc("jigsaw bot stopped by an error", tag=self.diag_tag)
            self._status(f"Jigsaw bot stopped by an error: {type(e).__name__}: {e}")
            if DEBUG_PRINTS:
                import traceback
                traceback.print_exc()
        finally:
            self.running = False
            self._status("Jigsaw bot stopped")
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] stopped; crates_completed={self.crates_completed}")

            _unregister_pause_subscriber(self)
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] unsubscribed from shared keyboard listener")
            try:
                self.capture.close()
            except Exception:
                diag.exc(tag=self.diag_tag)
                pass

            if self.on_bot_stop:
                self.on_bot_stop(self.bot_id)

    def stop(self) -> None:
        diag.log("DECISION", "stop requested", tag=self.diag_tag)
        self.running = False
        self._step_event.set()  # unblock any waiting iteration

    def set_step_mode(self, enabled: bool) -> None:
        self.step_mode = enabled
        if not enabled:
            self._step_event.set()  # release the loop if it's waiting

    def step(self) -> None:
        """Advance exactly one iteration when step mode is active."""
        self._step_event.set()

    def _on_f5_pressed(self, paused: Optional[bool] = None) -> None:
        """Invoked by the shared listener when F5 is pressed. Sets the pause
        state decided for all windows (toggles when called without one)."""
        paused = (not self.paused) if paused is None else bool(paused)
        if paused == self.paused:
            return
        self.paused = paused
        status_msg = "Jigsaw bot paused (press F5 to resume)" if self.paused else "Jigsaw bot resumed"
        self._status(status_msg)
        if self.on_pause_change is not None:
            try:
                self.on_pause_change(self.bot_id, self.paused)
            except Exception:
                diag.exc(tag=self.diag_tag)
                pass
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] F5 pressed: paused={self.paused}")

    def set_debug_consumer(self, attached: bool) -> None:
        """Toggle whether debug snapshots/events are recorded. Default: off."""
        self._debug_consumer = bool(attached)

    def _input_gate(self) -> None:
        """Before every cursor move and click: nothing more is sent once the
        bot is paused or stopped. Pausing used to be looked at only when a
        round began, so the round in progress went on clicking (a crate's
        Yes, a discard and its confirm) for up to a second after F5."""
        if not self.running or self.paused:
            raise InputInterrupted("stopped" if not self.running else "paused")

    @contextmanager
    def _hold(self):
        """input_lock for one input sequence, unless this bot already holds
        it for its whole round (the lock is not re-entrant)."""
        if input_lock.held_by_me():
            yield
            return
        with input_lock:
            yield

    def _begin_round_hold(self) -> None:
        """Takes the shared mouse for the whole round: crate, Yes, the look at
        the piece, its placement or discard and that Yes. Handed over after
        every step instead, the two bots of diag 20261122_093507 swapped the
        cursor about 10 times and the focus 5 times per crate (4-5 and 1 are
        needed): a window stops drawing its held piece while the other one
        has the cursor, so every look at it first took the focus and the
        cursor back. The game answers within ~30 ms, so a round is the bot's
        own click timing (~1 s) and taking turns per crate costs no speed.
        Nothing to hold in a dry run (no input at all)."""
        if self.dry_run or self._round_held or input_lock.held_by_me():
            return
        input_lock.acquire()
        self._round_held = True

    def _end_round_hold(self) -> None:
        """Lets the other windows have the mouse (end of a round, or before a
        wait that sends nothing, like the result dialog's)."""
        if self._round_held:
            self._round_held = False
            # A round holds the mouse ~1 s by design; only an unusual one is logged.
            input_lock.release(long_hold=float(self.config.get("jigsaw_round_hold_log", 3.0)))

    def _run_loop(self) -> None:
        """Plays rounds until stopped. A pause or stop in the middle of a
        round ends that round at its next move or click (_input_gate); after
        a resume the next round first looks whether a piece is still on the
        cursor (and plays or discards it) before opening another crate."""
        while self.running:
            try:
                self._play_rounds()
                return
            except InputInterrupted as why:
                diag.log("DECISION", f"round interrupted ({why}): no more input sent", tag=self.diag_tag)
                if self.running and self._check_hand is None:
                    self._check_hand = self._parked_point()
            finally:
                self._end_round_hold()

    def _play_rounds(self) -> None:
        # Optimized timings for performance
        action_delay = float(self.config.get("jigsaw_action_delay", 0.08))  # Reduced from 0.15
        retry_delay = float(self.config.get("jigsaw_piece_detection_retry_delay", 0.20))  # Reduced from 0.2
        failure_count = 0
        iteration = 0
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _run_loop() started; action_delay={action_delay}s retry_delay={retry_delay}s")
        while self.running:
            # A round that ended early (continue) hands the mouse over here.
            self._end_round_hold()
            if self.paused:
                time.sleep(0.1)
                continue

            if not self.window_manager.is_window_available():
                self._status("Game window is no longer available; stopping")
                self.running = False
                return

            iteration += 1
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] --- iteration {iteration} | crates_done={self.crates_completed} ---")

            if self.step_mode:
                self._debug_update("step_wait", f"Waiting for step (iteration {iteration})")
                self._status(f"Step mode — press Step to run iteration {iteration}")
                self._step_event.clear()
                self._step_event.wait()
                if not self.running:
                    return

            # Capture does NOT require the target window to be foreground,
            # so we skip the per-iteration activate that previously starved the global input_lock.
            frame = self.capture_full_window()
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] frame captured shape={frame.shape}")
            if not self._capture_healthy(frame):
                continue
            grid = self.detector.detect_grid(frame)
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(
                    f"[JigsawBot W{self.bot_id + 1}] grid_found={grid.grid_found} "
                    f"confidence={grid.confidence:.3f} "
                    f"bounds={grid.grid_bounds} "
                    f"board_mask={bin(grid.board_mask)} "
                    f"filled_cells={sorted(grid.filled_cells or set())}"
                )
            self._debug_update(
                "grid_scan",
                "Detected grid" if grid.grid_found else "Grid not found",
                frame=frame,
                grid_found=grid.grid_found,
                grid_bounds=grid.grid_bounds,
                grid_confidence=grid.confidence,
                board_mask=grid.board_mask,
                filled_cells=sorted(grid.filled_cells or set()),
            )
            if not grid.grid_found:
                failure_count += 1
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] grid not found; failure_count={failure_count}")
                if grid.grid_bounds is None and self.detector.auto_grid():
                    if failure_count == 1:
                        self._status("Fish Puzzle window not found in this game window — looking again")
                elif grid.grid_bounds is None:
                    self._status("Fishing Jigsaw grid is not configured or is outside the window")
                elif failure_count == 1:
                    self._status(f"Puzzle window not found at the configured grid "
                                 f"(score {grid.confidence:.2f}) — looking again")
                self._save_debug(frame, "grid_not_found")
                if diag.ENABLED and failure_count in (1, 5, 20):
                    diag.image("jigsaw_grid_not_found", frame, tag=self.diag_tag,
                               note=f"puzzle {self.detector.puzzle}, defined grid "
                                    f"{self.config.get('jigsaw_grid_bounds')}, failure {failure_count}, "
                                    f"score {grid.confidence:.2f}")
                if failure_count >= int(self.config.get("jigsaw_grid_misses_before_pause", 4)):
                    # Not a passing tooltip: the puzzle is closed, moved or
                    # covered. Nothing is clicked until the player looks.
                    failure_count = 0
                    if self.detector.auto_grid():
                        self._pause("puzzle window not found in this game window — open the Fish Puzzle window "
                                    "(anywhere but the middle of the screen, where the game's dialogs "
                                    "open) and make sure nothing covers it, then resume")
                    else:
                        self._pause("puzzle window not found at the configured grid — open the Fishing "
                                    "Jigsaw window where the grid was defined (or define the grid again)")
                    continue
                time.sleep(min(1.5, 0.25 + failure_count * 0.1))
                continue

            failure_count = 0
            if grid.grid_bounds != self._layout_checked_for:
                # The puzzle window was found for the first time, or moved:
                # the Confirm point and the parked cursor must still be
                # clear of its grid and crate slots.
                problem = self._point_layout_problem(grid)
                if problem:
                    self._pause(problem)
                    continue
                self._layout_checked_for = grid.grid_bounds
            if diag.ENABLED and diag.first_pending("jigsaw_board", self.diag_tag):
                marked = frame.copy()
                gx, gy, gw, gh = grid.grid_bounds
                cv2.rectangle(marked, (gx, gy), (gx + gw - 1, gy + gh - 1), (0, 0, 255), 1)
                diag.first("jigsaw_board", marked, tag=self.diag_tag,
                           note=f"grid {grid.grid_bounds} filled {sorted(grid.filled_cells or set())}")
            wanted = self.detector._crate_order(self.config.get("jigsaw_crate_priority", "normal_first"))
            max_detection_retries = int(self.config.get("jigsaw_piece_detection_retries", 3))
            piece = None
            section = wanted[0] if wanted else "normal"
            cursor_rel = None
            slots = None
            crate_opened = False   # this round opened a crate (not a piece still held from the last one)

            # From here to the end of the placement or discard, the mouse
            # stays with this window.
            self._begin_round_hold()
            if self._discard_check is not None:
                self._finish_discard_check()
                if not self.running:
                    return
            if self._check_hand is not None:
                # The last round ended without knowing whether a piece is still
                # on the cursor (a dialog may have been confirmed late). Opening
                # a crate — or refilling a slot, which clicks the inventory —
                # on top of a held piece is the one thing that must not
                # happen, so look first and play that piece if it is there.
                cursor_rel, self._check_hand = self._check_hand, None
                piece = self._look_for_piece(cursor_rel, max_detection_retries, retry_delay)
                if piece.piece_id is None:
                    piece = None
                elif self._hand_rounds >= 2:
                    # "A piece" is still seen after two rounds of trying to
                    # get rid of it. Either a real piece will not leave the
                    # cursor (the discard dialog never came) or something
                    # colorful is taken for one: there is no telling which,
                    # and opening a crate over a held piece is the one thing
                    # that must not happen. The player has to look.
                    diag.log("DECISION", f"piece still seen on the cursor after {self._hand_rounds} rounds: "
                                         f"pausing instead of opening a crate", tag=self.diag_tag)
                    self._hand_rounds = 0
                    self._check_hand = cursor_rel   # looked at again after the resume
                    self._pause("a piece stays on the cursor and could not be placed or discarded — "
                                "discard it by hand (or move whatever is mistaken for one), then resume")
                    continue
                else:
                    self._hand_rounds += 1
                    self._status("A piece is still on the cursor; playing it before opening a crate")
            if not self.running:
                return

            if piece is None:
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] checking crate slots…")
                frame, frame_gray, grid, slots = self._ensure_crates_available(frame, None, grid)
                if not self.running or self.paused:
                    continue
                if not grid.grid_found:
                    continue  # lost during the refill; look again
                available_sections = {section for section in slots.available if section in wanted}
                if grid.board_mask != 0:
                    self._room_checked = False      # looked at again when the next board starts
                elif available_sections and not self._room_checked and not self.dry_run:
                    # A new board: its reward goes into the inventory. With
                    # every page full it would be lost, so nothing is played.
                    room = self._inventory_has_room()
                    if not self.running:
                        return
                    if room is False:
                        self._pause("every inventory page is full — make room for the puzzle rewards, "
                                    "then resume")
                        continue
                    self._room_checked = True
                if not available_sections:
                    self._status("No jigsaw crates left in the slot or the inventory; stopping")
                    if DEBUG_PRINTS or diag.ENABLED:
                        self._dbg(f"[JigsawBot W{self.bot_id + 1}] no crates available; stopping loop")
                    self.running = False
                    return
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] available_sections={sorted(available_sections)}")

            if self._suspect_mask:
                read = grid.board_mask
                if (read & self._suspect_board) != self._suspect_board \
                        or (read & self._suspect_mask) == self._suspect_mask:
                    # Cells filled back then are gone (a new board), or the
                    # suspect cells now read as filled: nothing left to avoid.
                    diag.log("STATE", f"suspect cells {self._suspect_mask:024b} dropped (board {read:024b})",
                             tag=self.diag_tag)
                    self._suspect_mask = 0
                elif (read | self._suspect_mask) == FULL_BOARD_MASK and read != FULL_BOARD_MASK:
                    # Every cell still empty is a suspect one: the board can
                    # only be finished in them. Kept, every piece would be
                    # discarded from here on. A placement that is lost twice
                    # looks exactly like a refused one, so the suspicion may be
                    # wrong; it is given up, and a real refusal is caught again
                    # (and pauses after jigsaw_max_refused_placements).
                    diag.log("DECISION", f"only suspect cells {self._suspect_mask:024b} are left empty "
                                         f"(board {read:024b}): suspicion dropped", tag=self.diag_tag)
                    self._suspect_mask = 0

            if piece is None and grid.board_mask == FULL_BOARD_MASK:
                # A full board is handled right after the last placement. Read
                # as full here, any piece would be discarded: the result was
                # missed, or the cells read wrong. A crate is not spent on that.
                self._pause("the board reads as completely full — finish or close the result dialog"
                            + ("" if grid.source == "auto" else ", or re-align the grid with Define Grid"))
                continue

            if piece is None:
                priority = self.config.get("jigsaw_crate_priority", "normal_first")
                crate = self._select_crate_target(slots, priority, grid.board_mask | self._suspect_mask)
                if crate is None:
                    if priority in ("special_when_better", "special_first") and "normal" not in slots.available:
                        # Only special crates are left and their piece has no
                        # room on this board: nothing more can be done.
                        self._status("No normal jigsaw crates left and the special piece does not fit; stopping")
                        self.running = False
                        return
                    self._pause("No crate target detected")
                    continue
                section = crate.section
                self._debug_update(
                    "crate_target",
                    f"{crate.section} at {crate.center}",
                    crate_section=crate.section,
                    crate_center=crate.center,
                )

                if self._stale_dialog_up():
                    # Left open by a confirm the game did not take. It asks
                    # for what the bot wanted anyway (open a crate, discard
                    # the piece in hand), so it is confirmed; the next round
                    # looks at the hand first. Twice in a row means it does
                    # not go away (or the area changed for good): the player
                    # has to look rather than the bot clicking there forever.
                    self._stale_dialog_rounds += 1
                    if self._stale_dialog_rounds > 2:
                        self._stale_dialog_rounds = 0
                        self._pause("a dialog stays open over the Confirm button (or something covers "
                                    "it) — close it by hand, check the Confirm coordinate, then resume")
                        continue
                    diag.log("DECISION", "a dialog is still open before the crate click: confirming it first",
                             tag=self.diag_tag)
                    self._status("A dialog from the last round is still open; confirming it first")
                    self._click_window_point(self.confirm_button_pos, "Confirm the dialog left open",
                                             park_at=self._parked_point())
                    self._check_hand = self._parked_point()
                    self._end_round_hold()
                    time.sleep(retry_delay)
                    continue
                self._stale_dialog_rounds = 0

                self._hand_rounds = 0
                # What the result button's place looks like with no dialog up
                # and the cursor parked as it will be after the last placement.
                if not self.dry_run:
                    self._result_baseline = {point: dialog_watch.read_patch(self._dialog_grab, point)
                                             for point in self._result_points()} or None
                cursor_rel = self._open_crate_and_confirm(crate.center, crate.section)
                if not self.running:
                    return
                if self._crate_dialog_seen is not True:
                    # The crate never asked "Open this box?" (its clicks were
                    # lost), so no Yes went out and no piece can be on the
                    # cursor: nothing to look at and nothing to discard. A
                    # "discard" of the world's colors there cost 3 s of right
                    # clicks and waiting (diag 20261122_093507). A dialog that
                    # comes after all is confirmed by the next round's check.
                    if self._crate_dialog_seen is False:
                        self._no_dialog_rounds += 1
                        # A piece already on the cursor is the other reason a
                        # crate does not open: the window ignores crate clicks
                        # while one is held (servers re-send a piece kept
                        # through a relog). The next round looks for it and
                        # plays it before trying a crate again.
                        self._check_hand = self._current_cursor_relative()
                    limit = int(self.config.get("jigsaw_max_unopened_crates", 3))
                    if limit > 0 and self._no_dialog_rounds >= limit:
                        self._no_dialog_rounds = 0
                        self._pause("the crate did not open (no \"Open this box?\" came) several times in a "
                                    "row — check that nothing covers the puzzle window and the Confirm "
                                    "coordinate, then resume")
                    self._end_round_hold()
                    time.sleep(action_delay)
                    continue
                self._no_dialog_rounds = 0
                crate_opened = True
                piece = self._look_for_piece(cursor_rel, max_detection_retries, retry_delay)
                if not self.running:
                    return   # stopped mid-round: send nothing further

                if piece.piece_id is None and not self.dry_run and self._dialog_still_up():
                    # The "open crate?" dialog came up after the confirm
                    # click had already been sent, so it is still waiting.
                    # Confirm once more and look again.
                    self._click_lost("crate dialog still open after its Yes")
                    self._click_window_point(self.confirm_button_pos, "Confirm crate again (no piece seen)")
                    cursor_rel = self._park_cursor(cursor_rel)
                    time.sleep(retry_delay)
                    piece = self._look_for_piece(cursor_rel, max_detection_retries, retry_delay)

            if not self.running:
                return
            self._last_detected_piece_id = piece.piece_id
            self._last_detected_figure_index = piece.figure_index
            self._debug_update(
                "piece_detected",
                f"{piece.piece_id or 'unknown'} conf={piece.confidence:.2f}",
                piece_id=piece.piece_id,
                figure_index=piece.figure_index,
                piece_confidence=piece.confidence,
            )

            if piece.piece_id is None:
                if diag.ENABLED and cursor_rel is not None:
                    r = max(10, min(24, int(self.config.get("jigsaw_cursor_capture_roi", 15))))
                    diag.image("jigsaw_unknown_piece", self.capture_window_region(
                        cursor_rel[0] - 3 * r, cursor_rel[1] - 3 * r, 6 * r, 6 * r), tag=self.diag_tag,
                        note=f"area around the cursor {cursor_rel} (detector looks at +-{r}px), "
                             f"confidence {piece.confidence:.2f}")
                self._unknown_streak += 1
                if piece.colored_pixels == 0:
                    # Nothing piece-colored at the cursor at all: no piece is
                    # held, so a right-click and a confirm would only land on
                    # whatever is under them. Look at the hand next round.
                    self._status("No piece on the cursor after opening the crate; nothing to discard")
                else:
                    self._status(f"No recognizable piece on the cursor (confidence {piece.confidence:.2f}); "
                                 f"discarding")
                    self._discard_piece(cursor_rel, "unknown piece", verify=False)
                self._check_hand = cursor_rel
                if self._unknown_streak >= int(self.config.get("jigsaw_max_unknown_pieces", 3)):
                    self._unknown_streak = 0
                    self._pause("no piece could be recognized several times in a row — check that the "
                                "puzzle window is open and not covered, and the Confirm coordinate")
                self._end_round_hold()
                time.sleep(action_delay)
                continue
            self._unknown_streak = 0

            spec = self._resolve_piece(piece.piece_id, piece.figure_index, section)
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(
                    f"[JigsawBot W{self.bot_id + 1}] resolved spec: {spec.piece_id if spec else None} "
                    f"figure_index={spec.figure_index if spec else None} "
                    f"mask={bin(spec.mask) if spec else None}"
                )
            if spec is None:
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] unsupported piece '{piece.piece_id}'; pausing")
                self._pause(f"Unsupported piece '{piece.piece_id}'")
                continue

            # Store the figure index for _target_for_action to calculate proper click position
            self._last_piece_figure_index = spec.figure_index

            # The board mask cannot change from clicking the crate slot or confirming open —
            # only piece placement mutates it. Reuse the grid we already have.
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] computing decision for spec={spec.piece_id}…")
            # Cells a refused placement aimed at are filled in the game even
            # though they read empty: the solver must not choose them again.
            decide_grid = dc_replace(grid, board_mask=grid.board_mask | self._suspect_mask) \
                if self._suspect_mask else grid
            decision, explanation = self._decide_action(decide_grid, spec)
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(
                    f"[JigsawBot W{self.bot_id + 1}] decision: action={decision.action} "
                    f"target={decision.target} reason={decision.reason} "
                    f"chosen_action={explanation.get('chosen_action')} "
                    f"placement_cells={explanation.get('placement_cells', [])}"
                )
            self._debug_update(
                "piece_decision",
                f"{decision.action}: {decision.reason}",
                frame=frame,
                grid_found=grid.grid_found,
                grid_bounds=grid.grid_bounds,
                grid_confidence=grid.confidence,
                board_mask=grid.board_mask,
                filled_cells=sorted(grid.filled_cells or set()),
                decision_action=decision.action,
                decision_target=decision.target,
                decision_reason=decision.reason,
                chosen_action=explanation.get("chosen_action"),
                placement_cells=explanation.get("placement_cells", []),
                decision_candidates=explanation.get("candidates", []),
            )
            completes_board = self._decision_completes_board(
                decide_grid,
                spec,
                decision,
                explanation.get("chosen_action"),
            )
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] completes_board={completes_board}; performing decision…")
            if completes_board and not self.dry_run:
                # The window as it looks before the result dialog is asked
                # for: the dialog is found as what is new on top of this.
                self._result_before = self._gray_window()
            self._perform_decision(decision, cursor_rel)
            placed_ok = False
            if decision.action == "place" and decision.target is not None and not self.dry_run:
                expected = grid.board_mask | (spec.mask >> explanation["chosen_action"])
                placed_ok = self._confirm_placement(grid.board_mask, expected, decision, cursor_rel)
                if not placed_ok:
                    completes_board = False
                elif completes_board and not self._last_placement_matched:
                    # "Something changed", not the predicted board: complete
                    # only if the board really reads full now.
                    check = self._read_board()
                    completes_board = check.grid_found and check.board_mask == FULL_BOARD_MASK
                    diag.log("DECISION", f"placement not matched exactly: board complete={completes_board}",
                             tag=self.diag_tag)
            elif decision.action == "place":
                placed_ok = True
            # Counted on the board as read: cells only treated as filled
            # (suspect) are not "the last cells" of a nearly finished board.
            empty_cells = 24 - bin(grid.board_mask).count("1")
            if placed_ok:
                self._discard_streak = 0
            elif decision.action == "discard" and empty_cells <= 4 and not self._suspect_mask:
                pass   # waiting for a piece that fits the last cells: normal play
            else:
                self._discard_streak += 1
                limit = int(self.config.get("jigsaw_max_consecutive_discards", 12))
                if self.running and not self.paused and limit > 0 and self._discard_streak >= limit:
                    self._discard_streak = 0
                    self._pause(f"{limit} pieces in a row were discarded — check that the board is read "
                                f"correctly (grid alignment, nothing covering the puzzle)")
            # The round's input is done: the other windows get the mouse while
            # this one waits for its result dialog (if any) and its next round.
            self._end_round_hold()
            if crate_opened:
                self.crates_completed += 1
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] crates_completed={self.crates_completed}")
                if self.on_progress_update:
                    self.on_progress_update(self.bot_id, self.crates_completed)
            if completes_board:
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] board complete! handling completion…")
                self.boards_completed += 1
                self._handle_completed_board()
            time.sleep(action_delay)

    def _capture_healthy(self, frame) -> bool:
        """False (after a short wait) while the window capture shows nothing
        usable — a black or failed frame is no board to act on. Stops the
        worker when that lasts ``_CAPTURE_STOP_AFTER`` seconds."""
        if frame_usable(frame):
            if self._capture_bad_since is not None:
                diag.log("STATE", "capture usable again", tag=self.diag_tag)
                self._status("Game window visible again — carrying on")
            self._capture_bad_since = None
            return True
        now = time.perf_counter()
        if self._capture_bad_since is None:
            self._capture_bad_since = now
            diag.log("STATE", "capture shows nothing usable (black or failed): no action", tag=self.diag_tag)
            self._status("The game window shows nothing (black or failed capture) — waiting. "
                         "Restore or uncover the window.")
        elif now - self._capture_bad_since >= self._CAPTURE_STOP_AFTER:
            self.stop_reason = "capture_unavailable"
            diag.event("DECISION", "STOP reason=capture_unavailable", kind="stop", tag=self.diag_tag, recent=True)
            self._status(f"The game window could not be seen for {self._CAPTURE_STOP_AFTER:.0f} s; stopping")
            self.running = False
            return False
        time.sleep(0.25)
        return False

    def _point_layout_problem(self, grid: Optional[JigsawGridResult] = None) -> Optional[str]:
        """The confirm point and the parked cursor point must lie clear of
        the grid, the crate slots and the inventory (see layout_problem), for
        ``grid`` (default: where the puzzle window was found last, else the
        grid defined by hand). Returns what is wrong, or None."""
        if self.dry_run:
            return None
        bounds, slots = None, None
        if grid is not None and grid.grid_found:
            bounds, slots = grid.grid_bounds, grid.crate_slots
        elif self.detector.puzzle is not None and self.detector.auto_grid():
            bounds, slots = self.detector.puzzle.grid, self.detector.puzzle.slots
        if bounds is not None:
            self._layout_checked_for = tuple(bounds)
        elif self.detector.auto_grid() and not self.config.get("jigsaw_grid_bounds"):
            return None     # the puzzle window is not found yet: checked when it is
        try:
            _, _, win_w, win_h = self.window_manager.get_window_rect()
        except Exception:
            diag.exc("layout check: window size", tag=self.diag_tag)
            win_w = win_h = None
        problem = layout_problem(self.config, win_w, win_h, grid_bounds=bounds, crate_slots=slots)
        if problem:
            self._layout_checked_for = None
        return problem.message if problem else None

    def _refuse_overlapping_windows(self) -> bool:
        """Same rule as the fishing bot: a client that can only be read from
        the screen must not overlap another bot's window, or the bot reads
        the other client's board. Returns False after refusing to start."""
        capture = self.capture
        if self.dry_run or not capture.reads_screen_when_covered:
            return True
        peers = capture.overlapping_peers()
        if not peers:
            return True
        with self._hold():
            if self._focus():
                while capture.needs_verification and not capture.window_trusted and self.running:
                    time.sleep(0.1)
                    capture.verify()
        if capture.window_trusted:
            return True
        self.stop_reason = "capture_overlap"
        diag.event("DECISION", "STOP reason=capture_overlap", kind="stop", tag=self.diag_tag, recent=True)
        self._status(f"This game window overlaps {', '.join(peers)}, and this client can only be read "
                     f"from the screen. Move the game windows apart so none covers another. Not started.")
        self.running = False
        return False

    def capture_full_window(self) -> np.ndarray:
        _, _, width, height = self.window_manager.get_window_rect()
        frame = self.capture.grab(0, 0, width, height)
        if frame is None:
            return np.zeros((100, 100, 3), dtype=np.uint8)
        return frame

    def _inventory_rect(self) -> Tuple[int, int, int, int]:
        """Window-relative rectangle (x, y, w, h) of the inventory strip, the
        same one the fishing bot uses: the item grid below the configured
        page tabs, or the fixed right-hand strip without tabs. Positions
        found in a capture of it are relative to its corner."""
        _, _, win_width, win_height = self.window_manager.get_window_rect()
        rect, source = inventory_layout.strip_rect(
            self.config, win_width, win_height, self._inventory_width, self._inventory_y_offset)
        if rect != self._inv_rect:
            diag.log("STATE", f"inventory strip rect={rect} (from {source}; was {self._inv_rect})",
                     tag=self.diag_tag)
            self._inv_rect = rect
        return rect

    def capture_inventory_area(self) -> np.ndarray:
        frame = self.capture.grab(*self._inventory_rect())
        if frame is None:
            return np.zeros((100, 100, 3), dtype=np.uint8)
        return frame

    def capture_window_region(self, rel_x: int, rel_y: int, width: int, height: int) -> np.ndarray:
        _, _, win_width, win_height = self.window_manager.get_window_rect()
        x = max(0, min(int(rel_x), max(0, win_width - 1)))
        y = max(0, min(int(rel_y), max(0, win_height - 1)))
        w = max(1, min(int(width), win_width - x))
        h = max(1, min(int(height), win_height - y))
        frame = self.capture.grab(x, y, w, h)
        if frame is None:
            return np.zeros((1, 1, 3), dtype=np.uint8)
        return frame

    def _cursor_position(self) -> Optional[Tuple[int, int]]:
        try:
            pos = self.mouse_backend.position()
            return (int(pos[0]), int(pos[1]))
        except Exception:
            diag.exc(tag=self.diag_tag)
            return None

    def _cursor_at(self, point: Tuple[int, int], tolerance: int = 2) -> bool:
        """Is the shared cursor on this window's ``point`` (in front or not)?"""
        current = self._cursor_position()
        if current is None:
            return False
        left, top, _, _ = self.window_manager.get_window_rect()
        return abs(current[0] - left - point[0]) <= tolerance and abs(current[1] - top - point[1]) <= tolerance

    def _restore_cursor(self, cursor_rel: Tuple[int, int]) -> None:
        """Puts the cursor back where this window's piece was parked.

        The piece sprite follows the cursor. With several windows running,
        another bot may have moved the shared cursor away in the meantime, so
        a capture "around the cursor" would miss the piece and a click "at the
        current position" would land in the other window. No-op when the
        cursor is already there. Must be called with input_lock held."""
        left, top, _, _ = self.window_manager.get_window_rect()
        target = (left + cursor_rel[0], top + cursor_rel[1])
        current = self._cursor_position()
        if current is not None and abs(current[0] - target[0]) <= 2 and abs(current[1] - target[1]) <= 2:
            return
        if not self._focus():
            return
        self.human.move_to(*target)
        # Let the game redraw the piece at the cursor before anyone looks.
        time.sleep(max(0.04, float(self.config.get("timing_cursor_settle", 0.008))))
        self.capture.invalidate()

    def _look_for_piece(self, cursor_rel: Optional[Tuple[int, int]], attempts: int, delay: float):
        """Looks for the piece on the cursor until it shows: it appears a
        moment after the crate was confirmed. Gives up after the time
        ``attempts`` looks ``delay`` apart would take, but looks every
        ``_PIECE_POLL`` meanwhile: a piece that came 10 ms after a look
        otherwise cost the whole ``delay`` with the mouse held."""
        piece = None
        budget = max(0, attempts - 1) * max(0.0, delay)
        poll = min(delay, self._PIECE_POLL) if delay > 0 else 0.0
        deadline = time.perf_counter() + budget
        with diag.loop("look for cursor piece", tag=self.diag_tag, columns=("attempt", "piece", "conf"),
                       cursor=cursor_rel, attempts=attempts) as lp:
            attempt = 0
            while True:
                attempt += 1
                piece = self._detect_cursor_piece(cursor_rel)
                lp.frame(attempt=attempt, piece=piece.piece_id, conf=piece.confidence)
                lp.score("confidence", piece.confidence)
                if piece.piece_id is not None or not self.running:
                    break
                if poll <= 0:
                    if attempt >= attempts:
                        break
                    continue
                if time.perf_counter() + poll > deadline + 1e-3:
                    break
                time.sleep(poll)
            lp.set("result", f"{piece.piece_id if piece else None} conf="
                             f"{piece.confidence if piece else 0:.3f}")
        return piece

    def _detect_cursor_piece(self, cursor_rel: Optional[Tuple[int, int]]):
        if cursor_rel is None:
            # No cursor position: the detector can only tell whether anything
            # piece-colored is in the window at all (never which piece).
            return self.detector.detect_cursor_piece(self.capture_full_window(), None)
        radius = int(self.config.get("jigsaw_cursor_capture_roi", 15))
        radius = max(10, min(24, radius))
        cx, cy = cursor_rel
        x1, y1 = cx - radius, cy - radius
        # Hold the lock across "cursor is in place" + capture so no other
        # window can pull the cursor (and the piece with it) out of the shot.
        with self._hold():
            if not self.dry_run:
                self._restore_cursor(cursor_rel)
            frame = self.capture_window_region(x1, y1, radius * 2, radius * 2)
        local_cursor = (cx - max(0, x1), cy - max(0, y1))
        return self.detector.detect_cursor_piece(frame, local_cursor)

    def _ensure_crates_available(
        self,
        frame: np.ndarray,
        frame_gray: Optional[np.ndarray],
        grid: JigsawGridResult,
    ) -> Tuple[np.ndarray, Optional[np.ndarray], JigsawGridResult, CrateSlotState]:
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _ensure_crates_available: detecting slots…")
        slots = self.detector.detect_crate_slots(frame, grid, gray=frame_gray)
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(
                f"[JigsawBot W{self.bot_id + 1}] slot_state: available={sorted(slots.available)} "
                f"missing={sorted(slots.missing)} confidence={slots.confidence}"
            )
        self._debug_update(
            "slot_state",
            f"available={sorted(slots.available)} missing={sorted(slots.missing)}",
            frame=frame,
            grid_found=grid.grid_found,
            grid_bounds=grid.grid_bounds,
            grid_confidence=grid.confidence,
            board_mask=grid.board_mask,
            filled_cells=sorted(grid.filled_cells or set()),
            slot_centers=slots.centers,
            available_sections=sorted(slots.available),
            missing_sections=sorted(slots.missing),
            slot_confidence=slots.confidence or {},
        )
        # Only crate kinds the configured priority will actually open are worth
        # an inventory search, and each kind is searched for once: flipping
        # through every inventory page each round for a crate the player does
        # not have made the solver crawl.
        wanted = set(self.detector._crate_order(self.config.get("jigsaw_crate_priority", "normal_first")))
        to_refill = {s for s in slots.missing if s in wanted and s not in self._exhausted_sections}
        if not to_refill:
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] nothing to refill")
            return frame, frame_gray, grid, slots

        missing_names = ", ".join(sorted(to_refill))
        self._status(f"Missing jigsaw crate slot(s): {missing_names}; searching inventory")
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] refilling missing: {missing_names}")
        placed_any = self._refill_missing_crates(to_refill, slots.centers)
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] refill complete; placed_any={placed_any}")

        frame = self.capture_full_window()
        frame_gray = None
        grid = self.detector.detect_grid(frame)
        if not grid.grid_found:
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] grid lost after refill attempt")
            return frame, frame_gray, grid, CrateSlotState({}, set(), {"normal", "special"}, {})
        slots = self.detector.detect_crate_slots(frame, grid, gray=frame_gray)
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(
                f"[JigsawBot W{self.bot_id + 1}] post-refill slots: available={sorted(slots.available)} "
                f"missing={sorted(slots.missing)}"
            )
        self._debug_update(
            "slot_state_after_refill",
            f"available={sorted(slots.available)} missing={sorted(slots.missing)}",
            frame=frame,
            grid_found=grid.grid_found,
            grid_bounds=grid.grid_bounds,
            grid_confidence=grid.confidence,
            board_mask=grid.board_mask,
            filled_cells=sorted(grid.filled_cells or set()),
            slot_centers=slots.centers,
            available_sections=sorted(slots.available),
            missing_sections=sorted(slots.missing),
            slot_confidence=slots.confidence or {},
        )

        return frame, frame_gray, grid, slots

    def _smart_crate_order(self, slots: CrateSlotState, board: int) -> List[str]:
        """Which crate to open on this board ("special_when_better").

        A special crate always gives the 3x2 piece, a normal one a random
        piece. Special crates are rare, so one is spent only where the 3x2
        piece saves at least ``jigsaw_special_min_gain`` rounds (default 1.0)
        over what normal crates are expected to need from here — or where
        there is no normal crate left and it is the only way to go on."""
        if "special" not in slots.available:
            return ["normal"]
        spec = self.pieces.get("jigsaw_piece_deluxe") or self.pieces.get("special_3x2")
        ranked = self.solver.rank_custom_actions(board, spec.mask, spec.max_offset, limit=1) if spec else []
        if not ranked:
            return ["normal"]                      # the 3x2 piece has no room here
        if "normal" not in slots.available:
            return ["special"]
        expected_normal = getattr(self.solver, "avg_distance", None)
        if expected_normal is None:
            return ["special", "normal"]
        gain = float(expected_normal(board)) - float(ranked[0].expected_rounds)
        if gain >= float(self.config.get("jigsaw_special_min_gain", 1.0)):
            self._status(f"Special crate saves about {gain:.1f} rounds on this board; using it")
            return ["special"]
        return ["normal"]

    def _special_piece_fits(self, board: int) -> bool:
        spec = self.pieces.get("jigsaw_piece_deluxe") or self.pieces.get("special_3x2")
        if spec is None:
            return True
        return bool(self.solver.rank_custom_actions(board, spec.mask, spec.max_offset, limit=1))

    def _select_crate_target(self, slots: CrateSlotState, priority: str,
                             board: Optional[int] = None) -> Optional[CrateTarget]:
        if priority == "special_when_better" and board is not None and self.solver is not None:
            order = self._smart_crate_order(slots, board)
        else:
            order = self.detector._crate_order(priority)
            if "special" in order and board is not None and self.solver is not None \
                    and not self._special_piece_fits(board):
                # A special crate always gives the 3x2 piece: where it has no
                # room it could only be thrown away, and the crate with it.
                order = [s for s in order if s != "special"]
                diag.log("DECISION", f"special crate skipped: the 3x2 piece has no room on {board:024b}",
                         tag=self.diag_tag)
        for section in order:
            if section in slots.available and section in slots.centers:
                confidence = 0.50
                if slots.confidence and section in slots.confidence:
                    confidence = slots.confidence[section]
                return CrateTarget(section, slots.centers[section], confidence)
        return None

    def _refill_missing_crates(self, missing: Set[str], slot_centers: Dict[str, Tuple[int, int]]) -> bool:
        pages = self._configured_inventory_pages()
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _refill_missing_crates: missing={sorted(missing)} pages={[p[0]+1 for p in pages]}")
        if not pages:
            self._status("Inventory page coordinates are not configured for jigsaw crate search")
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] no inventory pages configured; aborting refill")
            return False

        needed = set(missing)
        placed_any = False
        unsearched = []
        for page_idx, page_pos in pages:
            if not self.running or self.paused or not needed:
                break

            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] scanning inventory page {page_idx + 1} for {sorted(needed)}")
            if not self._switch_to_inventory_page(page_idx, page_pos):
                unsearched.append(page_idx + 1)
                continue
            inventory_frame = self.capture_inventory_area()
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] inventory frame shape={inventory_frame.shape}")
            found = self._find_inventory_crates(inventory_frame, needed)
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] found in inventory: {list(found.keys())}")
            for section in self.detector._crate_order(self.config.get("jigsaw_crate_priority", "normal_first")):
                if section not in needed or section not in found or section not in slot_centers:
                    continue
                inv_pos = found[section]
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] placing {section}: inv_pos={inv_pos} slot={slot_centers[section]}")
                if self.paused:
                    break
                if self._place_inventory_crate(section, inv_pos, slot_centers[section]):
                    placed_any = True
                    needed.remove(section)
                    if DEBUG_PRINTS or diag.ENABLED:
                        self._dbg(f"[JigsawBot W{self.bot_id + 1}] placed {section}; still needed={sorted(needed)}")
                    time.sleep(float(self.config.get("jigsaw_action_delay", 0.15)))

        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _refill_missing_crates done; placed_any={placed_any} remaining_needed={sorted(needed)}")
        if needed and self.running and not self.paused:
            if unsearched:
                # Not every page could be looked at: no verdict on what is left.
                self._status(f"Inventory page(s) {unsearched} could not be opened (window not in front); "
                             f"searching again next round")
            else:
                # Every configured page was searched: there are none left.
                self._exhausted_sections |= needed
                self._status(f"No {', '.join(sorted(needed))} jigsaw crate in the inventory")
        return placed_any

    # The fishing bot's empty-slot template and score (FishingBot.
    # _template_border_crop / _EMPTY_SLOT_THRESHOLD), measured on real clients.
    _EMPTY_SLOT_BORDER = 7
    _EMPTY_SLOT_THRESHOLD = 0.55
    _empty_slot_template: Optional[np.ndarray] = None

    @classmethod
    def _load_empty_slot_template(cls) -> Optional[np.ndarray]:
        if cls._empty_slot_template is None:
            path = os.path.join(get_resource_path("assets"), "empty_slot.jpg")
            image = cv2.imread(path) if os.path.exists(path) else None
            if image is None:
                return None
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            b = cls._EMPTY_SLOT_BORDER
            if gray.shape[0] > 2 * b and gray.shape[1] > 2 * b:
                gray = gray[b:-b, b:-b]
            cls._empty_slot_template = gray
        return cls._empty_slot_template

    def _page_has_empty_slot(self) -> Optional[bool]:
        """Whether the inventory page on screen shows an empty slot (None:
        the strip could not be seen, or there is no template to judge by)."""
        template = self._load_empty_slot_template()
        if template is None:
            return None
        try:
            # A minimized window has no room for the strip (negative size).
            x, y, w, h = self._inventory_rect()
            frame = self.capture.grab(x, y, w, h) if w > 0 and h > 0 else None
        except Exception:
            diag.exc(tag=self.diag_tag)
            return None
        if not frame_usable(frame):
            return None
        gray = cv2.cvtColor(np.ascontiguousarray(frame[:, :, :3]), cv2.COLOR_BGR2GRAY)
        if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
            return None
        try:
            best = float(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED).max())
        except Exception:
            diag.exc(tag=self.diag_tag)
            return None
        diag.log("ANALYSIS", f"inventory page {self._current_inv_page + 1}: best empty-slot score "
                             f"{best:.2f} (empty from {self._EMPTY_SLOT_THRESHOLD})", tag=self.diag_tag)
        return best >= self._EMPTY_SLOT_THRESHOLD

    def _inventory_has_room(self) -> Optional[bool]:
        """Before a new board: True when some inventory page has an empty
        slot, False when every configured page was seen full, None when that
        cannot be told (a page not seen, no tabs set): the bot plays on then,
        as before this check existed. The page on screen is looked at first,
        so a page with room costs no clicks."""
        here = self._page_has_empty_slot()
        if here:
            return True
        pages = self._configured_inventory_pages()
        if not pages:
            return None
        self._status("Checking that the inventory has room for the puzzle reward")
        unknown = here is None
        for page_idx, page_pos in pages:
            if not self.running:
                return None
            if not self._switch_to_inventory_page(page_idx, page_pos):
                unknown = True
                continue
            seen = self._page_has_empty_slot()
            if seen:
                return True
            unknown = unknown or seen is None
        if unknown:
            diag.log("DECISION", "no empty inventory slot seen, but not every page could be looked at: "
                                 "playing on", tag=self.diag_tag)
            return None
        diag.log("DECISION", f"every configured inventory page ({len(pages)}) reads as full", tag=self.diag_tag)
        if diag.ENABLED:
            diag.image("jigsaw_inventory_full", self.capture_inventory_area(), tag=self.diag_tag)
        return False

    def _configured_inventory_pages(self) -> List[Tuple[int, Tuple[int, int]]]:
        pages: List[Tuple[int, Tuple[int, int]]] = []
        for page_idx in range(8):
            page_num = page_idx + 1
            point = self._configured_point(f"inv_page_{page_num}_pos")
            if point is not None:
                pages.append((page_idx, point))
        return pages

    def _switch_to_inventory_page(self, page_idx: int, page_pos: Tuple[int, int]) -> bool:
        """Clicks an inventory page tab. False when the click could not be
        sent (window not in front): the page on screen is then another one
        and must not be searched as this page."""
        self._status(f"Searching inventory page {page_idx + 1} for jigsaw crates")
        self._debug_update("inventory_page", f"page {page_idx + 1}", inventory_page=page_idx + 1)
        if self.dry_run:
            self._current_inv_page = page_idx
            return True
        sent = False
        with self._hold():
            if self._focus():
                # Read after the focus: a minimized window was just restored.
                win_left, win_top, _, _ = self.window_manager.get_window_rect()
                self._glide_to(win_left + page_pos[0], win_top + page_pos[1])
                self._press(switched=self._switched)
                sent = True
        if not sent:
            diag.log("DECISION", f"inventory page {page_idx + 1} tab not clicked (no focus): page not searched",
                     tag=self.diag_tag)
            return False
        self._current_inv_page = page_idx
        time.sleep(float(self.config.get("jigsaw_inventory_page_delay", 0.18)))
        self._wait_for_inventory_page(page_idx + 1)
        return True

    _PAGE_WAIT_TIMEOUT = 2.0

    def _wait_for_inventory_page(self, page_num: int) -> None:
        """After a tab click: waits until that tab is drawn as the active
        one, so a slow machine's previous page is not searched instead. Goes
        on after the timeout, and does nothing when the tabs cannot be read
        (fewer than three configured, or never recognized on this client)."""
        _, _, win_width, win_height = self.window_manager.get_window_rect()
        points = inventory_layout.tab_points(self.config, win_width, win_height)
        if page_num not in points or len(points) < 3 or self._tab_check_off:
            return
        ok, waited, active, detail, looks = inventory_layout.wait_for_tab(
            self.capture.grab, points, win_width, win_height, page_num, lambda: self.running,
            self._PAGE_WAIT_TIMEOUT)
        if ok:
            self._tab_check_seen = True
            self._tab_check_misses = 0
            diag.log("STATE", f"page wait: tab {page_num} active after {waited:.2f}s ({looks} looks; {detail})",
                     tag=self.diag_tag)
            return
        if not self.running:
            return
        self._tab_check_misses += 1
        diag.log("DECISION", f"page wait: TIMEOUT after {waited:.2f}s, tab {page_num} never read as active "
                             f"(last read: {active}; {detail}; {looks} looks): searching the page anyway",
                 tag=self.diag_tag)
        if not self._tab_check_seen and self._tab_check_misses >= 2:
            self._tab_check_off = True
            diag.log("DECISION", "page wait: the active tab was never recognized on this client: "
                                 "not waiting for it any more", tag=self.diag_tag)

    def _find_inventory_crates(self, inventory_frame: np.ndarray, sections: Set[str]) -> Dict[str, Tuple[int, int]]:
        found: Dict[str, Tuple[int, int]] = {}
        if inventory_frame.size == 0:
            return found
        # Matched in color: the normal and the special crate have the same
        # shape and differ in color only, so in grayscale each is a good match
        # for the other and the wrong stack got picked up.
        inventory = np.ascontiguousarray(inventory_frame[:, :, :3])
        threshold = float(self.config.get("jigsaw_inventory_crate_threshold", 0.72))

        def best_match(section: str):
            pos, conf, where = None, -1.0, None
            for _, template, half_w, half_h in self._load_inventory_crate_templates(section):
                th, tw = template.shape[:2]
                if th > inventory.shape[0] or tw > inventory.shape[1]:
                    continue
                try:
                    result = cv2.matchTemplate(inventory, template, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, max_loc = cv2.minMaxLoc(result)
                except Exception:
                    diag.exc(tag=self.diag_tag)
                    continue
                if max_val > conf:
                    conf = float(max_val)
                    pos = (int(max_loc[0] + half_w), int(max_loc[1] + half_h))
                    where = (max_loc[0], max_loc[1], tw, th)
            return pos, conf, where

        def score_at(section: str, where) -> float:
            """How well the other kind of crate matches at the same place."""
            x, y, w, h = where
            x1, y1 = max(0, x - 4), max(0, y - 4)
            patch = inventory[y1:y + h + 4, x1:x + w + 4]
            best = -1.0
            for _, template, _, _ in self._load_inventory_crate_templates(section):
                th, tw = template.shape[:2]
                if th > patch.shape[0] or tw > patch.shape[1]:
                    continue
                try:
                    best = max(best, float(cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED).max()))
                except Exception:
                    diag.exc(tag=self.diag_tag)
                    continue
            return best

        for section in sections:
            best_pos, best_conf, where = best_match(section)
            if best_pos is None or best_conf <= threshold:
                continue
            other = "special" if section == "normal" else "normal"
            if score_at(other, where) > best_conf:
                continue                       # that is a crate of the other kind
            if best_pos is not None:
                found[section] = best_pos
                self._status(f"Found {section} jigsaw crate in inventory (confidence {best_conf:.2f})")
                self._debug_update(
                    "inventory_found",
                    f"{section} at {best_pos} conf={best_conf:.2f}",
                    inventory_found_section=section,
                    inventory_found_pos=best_pos,
                    inventory_found_confidence=best_conf,
                )
        return found

    def _load_inventory_crate_templates(self, section: str) -> List[Tuple[str, np.ndarray, int, int]]:
        if section in self._crate_template_cache:
            return self._crate_template_cache[section]

        configured = self.config.get(f"jigsaw_{section}_crate_inventory_assets", [])
        if isinstance(configured, str):
            configured = [configured]
        filenames = list(configured) + [
            f"jigsaw_{section}_crate_inventory.png",
            f"jigsaw_{section}_crate.png",
            f"Jigsaw_{section}_crate_item.png",
            f"Jigsaw_{section}_crate_item.jpg",
            f"{section.capitalize()}_Crate_item.png",
            f"{section.capitalize()}_Crate_item.jpg",
        ]

        templates: List[Tuple[str, np.ndarray, int, int]] = []
        border = int(self.config.get("jigsaw_inventory_template_border_crop", 0))
        for filename in filenames:
            path = self.detector._find_asset(filename)
            if not path:
                continue
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if border > 0 and img.shape[0] > border * 2 and img.shape[1] > border * 2:
                img = img[border:img.shape[0] - border, border:img.shape[1] - border]
            templates.append((filename, img, img.shape[1] >> 1, img.shape[0] >> 1))

        self._crate_template_cache[section] = templates
        return templates

    def _place_inventory_crate(
        self,
        section: str,
        inventory_pos: Tuple[int, int],
        slot_pos: Tuple[int, int],
    ) -> bool:
        self._status(f"Placing {section} jigsaw crate into its slot")
        self._debug_update(
            "inventory_place",
            f"{section} inventory={inventory_pos} slot={slot_pos}",
            crate_section=section,
            inventory_found_pos=inventory_pos,
            crate_center=slot_pos,
        )
        if self.dry_run:
            return True
        cursor_settle = float(self.config.get("timing_cursor_settle", 0.008))
        action_delay = float(self.config.get("jigsaw_action_delay", 0.08))
        strip_x, strip_y, _, _ = self._inv_rect or self._inventory_rect()
        park = self._parked_point()
        # Coalesce both clicks into a single lock hold so other bots aren't starved
        # by a back-to-back claim/release; activation happens once for both clicks.
        with self._hold():
            if not self._focus():
                return False
            win_left, win_top, _, _ = self.window_manager.get_window_rect()   # after the focus
            inv_x = win_left + strip_x + inventory_pos[0]
            inv_y = win_top + strip_y + inventory_pos[1]
            slot_x = win_left + slot_pos[0]
            slot_y = win_top + slot_pos[1]
            switched = self._switched
            self._glide_to(inv_x, inv_y)
            self._press(switched=switched)
            self.human.sleep(action_delay * 0.8)
            self._glide_to(slot_x, slot_y)
            self._press()
            if park is not None:
                # Off the slot: a crate still hanging on the cursor there
                # would look exactly like one that sits in the slot.
                self.human.sleep(max(cursor_settle, 0.02))
                self.human.move_to(win_left + park[0], win_top + park[1])
            self.capture.invalidate()
        if park is None:
            return True

        # The crate must have arrived: one that is still on the cursor (the
        # click on the slot was lost) is in neither the slot nor the
        # inventory, and the next click (open crate, confirm) would carry it
        # into the game world.
        for attempt in range(3):
            time.sleep(max(action_delay, 0.05))
            if not self.running:
                return False
            frame = self.capture_full_window()
            grid = self.detector.detect_grid(frame)
            if not grid.grid_found:
                break       # cannot tell: handled like a crate that did not arrive
            if section in self.detector.detect_crate_slots(frame, grid).available:
                return True
            if attempt < 2:
                self._status(f"The {section} crate did not arrive in its slot; clicking the slot again")
                self._click_window_point(slot_pos, f"Place {section} jigsaw crate (retry)", park_at=park)
        self._put_crate_back(section, inventory_pos, park)
        return False

    def _put_crate_back(self, section: str, inventory_pos: Tuple[int, int],
                        park: Optional[Tuple[int, int]]) -> None:
        """A crate taken from the inventory did not land in its slot and may
        be on the cursor: put it back where it came from (that place is free
        now), check it is there, and pause either way — the player has to
        see why the slot does not take it."""
        strip_x, strip_y, _, _ = self._inv_rect or self._inventory_rect()
        back_point = (strip_x + inventory_pos[0], strip_y + inventory_pos[1])
        self._click_window_point(back_point, f"Put {section} jigsaw crate back into the inventory", park_at=park)
        time.sleep(max(float(self.config.get("jigsaw_action_delay", 0.08)), 0.05))
        back = section in self._find_inventory_crates(self.capture_inventory_area(), {section})
        diag.log("DECISION", f"{section} crate did not arrive in its slot: put back into the inventory={back}",
                 tag=self.diag_tag)
        self._pause(f"the {section} crate could not be placed into its slot"
                    + (" (put back into the inventory)" if back else
                       " and may still be on the cursor — check before resuming"))

    def _decide_action(self, grid: JigsawGridResult, spec: PieceSpec) -> Tuple[JigsawActionResult, dict]:
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(
                f"[JigsawBot W{self.bot_id + 1}] _decide_action: spec={spec.piece_id} "
                f"figure_index={spec.figure_index} board_mask={bin(grid.board_mask)}"
            )
        # Ranking is debug-only; skip the 24-iteration np.mean loop on the hot path.
        candidates = self._rank_legal_actions(grid.board_mask, spec) if (self._debug_consumer or DEBUG_PRINTS) else []
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] ranked {len(candidates)} legal actions; top={candidates[:3]}")
        if spec.figure_index is not None:
            state = Jigsaw(grid.board_mask, spec.figure_index, 0)
            action = self.solver.solve(state)
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] solver returned action={action} (SKIP={action == SKIP_ACTION})")
            if action == SKIP_ACTION:
                return JigsawActionResult("discard", reason="solver requested skip"), {"candidates": candidates}
            if not state.is_legal(action):
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"[JigsawBot W{self.bot_id + 1}] solver action={action} is not legal; discarding")
                return JigsawActionResult("discard", reason="solver action is not legal"), {
                    "chosen_action": action,
                    "candidates": candidates,
                }
            if self._bbox_fit and self._bbox_blocked(grid.board_mask, self._cells_for_action(action, spec)):
                action = self._best_bbox_action(grid.board_mask, spec)
                if action is None:
                    return JigsawActionResult("discard", reason="no placement with a free bounding box"), {
                        "candidates": candidates}
            target = self._target_for_action(grid, action, spec)
            cells = self._cells_for_action(action, spec)
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] placing at target={target} cells={cells}")
            return JigsawActionResult("place", target, f"action={action}"), {
                "chosen_action": action,
                "placement_cells": cells,
                "candidates": candidates,
            }

        action = self._best_special_action(grid.board_mask, spec)
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] special piece best_action={action}")
        if action is None:
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] no legal special placement; discarding")
            return JigsawActionResult("discard", reason="no legal special placement"), {"candidates": candidates}
        target = self._target_for_action(grid, action, spec)
        cells = self._cells_for_action(action, spec)
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] special placing at target={target} cells={cells}")
        return JigsawActionResult("place", target, f"special action={action}"), {
            "chosen_action": action,
            "placement_cells": cells,
            "candidates": candidates,
        }

    @staticmethod
    def _cell_bit(cell: int) -> int:
        """The board-mask bit of cell ``row * 6 + col`` (see _cells_for_action)."""
        row, col = divmod(cell, 6)
        return (1 << (N * 6 - 1)) >> (col * N + row)

    @classmethod
    def _mask_cells(cls, mask: int) -> List[int]:
        return [cell for cell in range(N * 6) if mask & cls._cell_bit(cell)]

    @staticmethod
    def _bbox_cells(cells: Iterable[int]) -> List[int]:
        cells = list(cells)
        if not cells:
            return []
        rows = [c // 6 for c in cells]
        cols = [c % 6 for c in cells]
        return [r * 6 + c for r in range(min(rows), max(rows) + 1) for c in range(min(cols), max(cols) + 1)]

    @classmethod
    def _bbox_blocked(cls, board: int, cells: Iterable[int]) -> bool:
        """A cell of the piece's bounding box that is not one of its own
        cells is filled (an L, mirrored L or Z fitted against a neighbour)."""
        cells = list(cells)
        own = set(cells)
        return any(board & cls._cell_bit(c) for c in cls._bbox_cells(cells) if c not in own)

    def _best_bbox_action(self, board: int, spec: PieceSpec) -> Optional[int]:
        """The solver's best move among those this client takes (whole
        bounding box free), or None when skipping ranks better or none fits.
        The solver's values assume the real-cell rule, so this is a close
        approximation, not an exact optimum."""
        for item in self.solver.rank_actions(board, spec.figure_index, include_skip=True):
            if item.is_skip:
                return None
            if not self._bbox_blocked(board, self._cells_for_action(item.action, spec)):
                return item.action
        return None

    def _best_special_action(self, board: int, spec: PieceSpec) -> Optional[int]:
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _best_special_action: spec={spec.piece_id} max_offset={spec.max_offset}")
        ranked = self.solver.rank_custom_actions(board, spec.mask, spec.max_offset, limit=1)
        best_action = ranked[0].action if ranked else None
        if DEBUG_PRINTS:
            score = ranked[0].expected_rounds if ranked else None
            print(f"[JigsawBot W{self.bot_id + 1}] _best_special_action result: action={best_action} score={score}")
        return best_action

    def _rank_legal_actions(self, board: int, spec: PieceSpec, limit: int = 8) -> List[dict]:
        if self.solver is None:
            return []
        if spec.figure_index is not None:
            ranked = self.solver.rank_actions(board, spec.figure_index, limit=limit, include_skip=True)
        else:
            ranked = self.solver.rank_custom_actions(board, spec.mask, spec.max_offset, limit=limit)

        candidates: List[dict] = []
        for item in ranked:
            if item.is_skip:
                offset = None
                fills = 0
                cells: List[int] = []
            else:
                offset = Jigsaw.action_to_offsets(item.action)
                shifted = item.next_board & ~board
                fills = shifted.bit_count()
                cells = self._cells_for_action(item.action, spec)
            candidates.append({
                "action": item.action,
                "offset": offset,
                "score": item.expected_rounds,
                "fills": fills,
                "cells": cells,
                "skip": item.is_skip,
            })
        return candidates

    def _cells_for_action(self, action: int, spec: PieceSpec) -> List[int]:
        shifted = spec.mask >> action
        cells: List[int] = []
        for row in range(N):
            for col in range(6):
                bit_action = col * N + row
                bit = (1 << (N * 6 - 1)) >> bit_action
                if shifted & bit:
                    cells.append(row * 6 + col)
        return cells

    def _perform_decision(self, decision: JigsawActionResult,
                          cursor_rel: Optional[Tuple[int, int]] = None) -> None:
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _perform_decision: action={decision.action} target={decision.target} reason={decision.reason}")
        if decision.action == "place" and decision.target is not None:
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] placing piece at {decision.target}")
            self._place_click(decision.target, f"Place jigsaw piece ({decision.reason})", cursor_rel)
            return

        self._discard_piece(cursor_rel, decision.reason)

    def _place_click(self, target: Tuple[int, int], description: str,
                     cursor_rel: Optional[Tuple[int, int]]) -> bool:
        """Clicks the board to place the held piece, and answers the "Place
        the piece here?" dialog some servers ask before they place it (diag
        20261122_005427: every placement waited on that dialog, the bot took
        the missing piece for a lost click and clicked the board again behind
        the dialog). The Confirm area is read with the cursor on the board, so
        the held piece does not cover it. Returns whether the click went out."""
        self._last_place_confirmed = False
        self._place_baseline = None
        if self.dry_run or self.confirm_button_pos is None or self._place_dialog is False:
            return self._click_window_point(target, description, park_at=cursor_rel)

        def read_baseline():
            self._place_baseline = self._confirm_area()

        if not self._click_window_point(target, description, on_target=read_baseline):
            if cursor_rel is not None:
                self._park_cursor(cursor_rel)
            return False
        baseline = self._place_baseline
        timeout = float(self.config.get("jigsaw_dialog_wait_timeout", 0.3))
        if baseline is not None and self._dialog_up_within(baseline, timeout, "place dialog"):
            self._confirm_clear = baseline
            self._place_dialog = True
            self._learn_setting("jigsaw_place_dialog", True)
            self._last_place_confirmed = self._click_window_point(
                self.confirm_button_pos, "Confirm placement", park_at=cursor_rel)
            return True
        if baseline is not None and self._place_dialog is True and self.running:
            self._click_lost("no placement dialog after the board click")
        if cursor_rel is not None and self.running:
            self._park_cursor(cursor_rel)
        return True

    def _place_dialog_still_up(self) -> bool:
        """Is the "Place the piece here?" of the last placement click open?"""
        if self.dry_run or self.confirm_button_pos is None or self._place_baseline is None \
                or self._place_dialog is False:
            return False
        # A piece still held (the click was lost) is drawn over the parked
        # point, right on the watched Confirm area: it would look like a dialog.
        watch = self._discard_watch_point()
        if watch is not None:
            self._move_held_piece(watch)
        return dialog_watch.still_up(self._dialog_grab, self.confirm_button_pos, self._place_baseline,
                                     what="place dialog")

    def _discard_piece(self, cursor_rel: Optional[Tuple[int, int]], reason: str,
                       verify: bool = True) -> None:
        """Right-clicks the held piece and confirms the discard dialog.
        ``verify``: check afterwards that the piece left the cursor (pointless
        when no piece could be recognized there in the first place)."""
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] discarding piece: {reason}")
        self._status(f"Discarding jigsaw piece: {reason}")
        # The held piece is drawn on top of the dialog. At the parked point
        # (just right of Confirm) a 3x2 piece hides the whole Confirm area and
        # the bigger normal pieces most of it, so the dialog would never be
        # seen there. The area is watched with the piece moved clear of it,
        # the baseline included.
        watch = self._discard_watch_point()
        if watch is not None:
            self._move_held_piece(watch)    # nothing to do when it rests there already
        baseline = self._confirm_area()
        # Right-clicked where the piece now is: anywhere off the board asks
        # "Discard this piece?", and the watched area stays clear of it.
        at = watch if watch is not None else cursor_rel
        self._right_click_current_position("Discard jigsaw piece", at)
        dialog_up = self._wait_for_dialog(
            baseline, float(self.config.get("jigsaw_discard_dialog_min", 0.0)),
            click_again=lambda: self._right_click_current_position("Discard jigsaw piece (again)", at))
        if not self.running:
            return   # stopped while waiting: leave the dialog alone
        if not dialog_up and not self.dry_run and self.confirm_button_pos is not None:
            # No dialog seen — or the confirm area could not be read at all
            # (no baseline): either way a confirm click now could land in
            # the game world. The piece may still be held: the next round
            # looks at the hand.
            if baseline is None:
                self._status("Could not read the Confirm area; not clicking Confirm")
                diag.log("DECISION", "discard: confirm area unreadable -> confirm not clicked", tag=self.diag_tag)
            else:
                self._status("No discard dialog appeared; not clicking Confirm")
                diag.log("DECISION", "discard: no dialog seen -> confirm not clicked", tag=self.diag_tag)
            self._check_hand = cursor_rel
            return
        self._click_window_point(self.confirm_button_pos, "Confirm discard",
                                 park_at=(cursor_rel if verify else None) or self._parked_point())
        if not verify or cursor_rel is None or self.dry_run or not self.running:
            return
        # A piece that was recognized must be gone now. The game takes it off
        # the cursor only when the server answers, which on a real client
        # was more than 150 ms after the Yes: the piece was still drawn
        # 100 ms after it, and a "confirm again" sent then went out with no
        # dialog up. A left click beside the board with a piece held asks
        # "Discard this piece?" again, and that new dialog then blocked every
        # crate click for 10 s (diag 20261122_002413). So the piece gets time
        # to go, and a second confirm goes out only on a dialog that is
        # really still open. That is checked when this window's next round
        # starts (_finish_discard_check), before anything else is clicked
        # there: waiting for it here held the one mouse ~130 ms per discard
        # while the other windows had nothing to do (diag 20261122_132850).
        timeout = float(self.config.get("jigsaw_discard_gone_timeout", 1.0))
        self._discard_check = (cursor_rel, baseline, watch, time.perf_counter() + timeout)

    def _finish_discard_check(self) -> None:
        """The check _discard_piece left for the start of the next round
        (input_lock held): the discarded piece must have left the cursor; if
        not, the discard dialog is confirmed again only when it is really
        still open, else the hand is looked at (``_check_hand``)."""
        pending, self._discard_check = self._discard_check, None
        if pending is None or self.dry_run or not self.running:
            return
        cursor_rel, baseline, watch, deadline = pending
        while True:
            if self._detect_cursor_piece(cursor_rel).piece_id is None:
                return
            if time.perf_counter() >= deadline:
                break
            time.sleep(self._PIECE_POLL)
            if not self.running:
                return
        # Still held. The watched Confirm area must be clear of the piece to
        # tell whether the dialog is there.
        if watch is not None:
            self._move_held_piece(watch)
        if baseline is not None and dialog_watch.still_up(self._dialog_grab, self.confirm_button_pos, baseline):
            self._click_lost("discard dialog still open after its Yes")
            self._status("The discard dialog is still open; confirming again")
            self._click_window_point(self.confirm_button_pos, "Confirm discard again", park_at=cursor_rel)
        else:
            diag.log("DECISION", "discard: piece still held but no dialog open -> no second confirm "
                                 "(a confirm click now would ask to discard again)", tag=self.diag_tag)
            self._status("The piece is still on the cursor after the discard; looking at it again next round")
            if watch is not None:
                self._park_cursor(cursor_rel)
        self._check_hand = cursor_rel

    # ------------------------------------------------------------ verification

    def _dialog_grab(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """A fresh (uncached) capture of a window rectangle, for dialog_watch."""
        self.capture.invalidate()
        return self.capture_window_region(x, y, w, h)

    def _confirm_area(self) -> Optional[np.ndarray]:
        """Small grayscale patch around the confirm button (None in dry run)."""
        if self.dry_run or self.confirm_button_pos is None:
            return None
        return dialog_watch.read_patch(self._dialog_grab, self.confirm_button_pos)

    def _dialog_still_up(self) -> bool:
        """Does the confirm button area still differ from how it looked
        before the crate click (a dialog waiting for its confirm)? Without
        that baseline there is no telling: a blind confirm click would land
        in the game world when no dialog is up, so the answer is no."""
        # Compared with the area as last seen with no dialog up: a confirm
        # that was lost leaves its dialog open, and the crate click after it
        # then changes nothing (its own baseline already shows the dialog).
        baseline = self._confirm_clear
        if baseline is None:
            baseline = self._crate_confirm_baseline
        if baseline is None:
            return False
        change = dialog_watch.difference(
            dialog_watch.read_patch(self._dialog_grab, self.confirm_button_pos,
                                    (baseline.shape[1], baseline.shape[0])), baseline)
        # Dialog-sized on every read, not just "changed": the world behind the
        # button shifts the patch by 15-19 for good once something moved
        # there, and a confirm on no dialog is a click into the game world.
        up = dialog_watch.still_up(self._dialog_grab, self.confirm_button_pos, baseline)
        diag.log("DECISION", f"no piece after the crate: confirm area change {change} -> dialog "
                             f"{'still up: confirm again' if up else 'not up: no second confirm'}",
                 tag=self.diag_tag)
        return up

    def _stale_dialog_up(self) -> bool:
        """Is a dialog still open over the Confirm button before a crate is
        clicked? The dialog waits only look for a change from the area as it
        was right before their own click, so a dialog that was already open
        then is never seen: the crate click goes into a window blocked by it
        and "no dialog appeared" repeats round after round (diag
        20261122_002413: a "Discard this piece?" stayed open for 10 s). Needs
        the area as last seen with no dialog up; without it there is no
        telling, and the answer is no."""
        if self.dry_run or self.confirm_button_pos is None or self._confirm_clear is None:
            return False
        parked = self._parked_point()
        if parked is not None and self._cursor_over_confirm_area():
            self._park_cursor(parked)   # nothing held now: just off the watched area
        return dialog_watch.still_up(self._dialog_grab, self.confirm_button_pos, self._confirm_clear)

    def _cursor_over_confirm_area(self) -> bool:
        """Could the cursor be drawn over the watched Confirm area? Only then
        must it be taken away before the area is read. Resting at the parked
        point or in another window, it is not, and taking it back here cost
        a focus switch and a cursor jump every round (diag 20261122_093507)."""
        current = self._cursor_position()
        if current is None:
            return True
        left, top, _, _ = self.window_manager.get_window_rect()
        cx, cy = self.confirm_button_pos
        x, y = current[0] - left, current[1] - top
        # The cursor sprite reaches ~32 px right and down of its hot spot.
        return cx - CONFIRM_PATCH_HALF_W - 34 <= x <= cx + CONFIRM_PATCH_HALF_W \
            and cy - 8 - 34 <= y <= cy + 8 + 2

    def _dialog_up_within(self, baseline: np.ndarray, timeout: float, what: str,
                          minimum: float = 0.0) -> bool:
        """dialog_watch.wait for the Confirm area, counting only a
        dialog-sized change: the jigsaw never learns the dialog's look, and
        the world behind the button alone moved the patch by up to 15.08 on a
        real client, which passed the old threshold of 15 and sent a Yes onto
        the world (diag 20261122_093507). Real dialogs: 38-45."""
        return dialog_watch.wait(self._dialog_grab, self.confirm_button_pos, baseline, timeout,
                                 lambda: self.running, minimum=minimum, what=what, tag=self.diag_tag,
                                 strong_only=True)

    def _wait_for_dialog(self, baseline: Optional[np.ndarray], minimum: float,
                         click_again: Optional[Callable[[], None]] = None) -> bool:
        """Waits until the confirm-button area changes, i.e. the dialog is up.

        The dialog does not appear a fixed time after the click that asks for
        it — it depends on the client and, with several windows, on the focus
        switch — and a confirm click sent before it is there is simply lost.
        Always waits at least ``minimum``; gives up after the configured
        timeout and lets the caller decide. Returns True when a change was seen.

        ``click_again``: re-sends the click that asks for the dialog when it
        did not come within the usual wait. On a real client the dialog came
        within ~30 ms every time it came at all; the ones that never came were
        clicks the game did not take (diag 20261122_093507), and a 1.8 s
        "late dialog" wait for them held the mouse from the other window."""
        if baseline is None:
            diag.log("DECISION", "dialog wait: no baseline (dry run / no confirm position): fixed delay only",
                     tag=self.diag_tag)
            time.sleep(max(0.0, minimum))
            return False
        # Real client: every dialog that came at all was there within 45 ms
        # (1800 waits, diag 20261122_132850); a lost click cost the full wait.
        timeout = float(self.config.get("jigsaw_dialog_wait_timeout", 0.3))
        if self._dialog_up_within(baseline, timeout, "dialog", minimum):
            self._confirm_clear = baseline   # a dialog came up over it: it was clear
            return True
        if not self.running:
            return False
        late = max(1.0, 3 * timeout)
        if click_again is not None:
            diag.log("DECISION", f"dialog not up within {timeout:.2f}s: the click was not taken, clicking again",
                     tag=self.diag_tag)
            self._click_lost("no dialog after the click")
            click_again()
            if not self.running:
                return False
        else:
            # Not there yet. A slow client or a busy focus switch can be
            # later than the usual wait; the confirm goes out only on a
            # dialog, so a late one is worth a longer look before the round
            # is given up.
            diag.log("DECISION", f"dialog not up within {timeout:.2f}s: waiting up to {late:.2f}s more",
                     tag=self.diag_tag)
        if self._dialog_up_within(baseline, late, "late dialog"):
            self._confirm_clear = baseline
            return True
        return False

    def _await_board(self, expected: int, timeout: float) -> Tuple[Optional[JigsawGridResult], bool]:
        """Reads the board until it shows ``expected`` or ``timeout`` passes.
        Returns (last reading, matched)."""
        deadline = time.perf_counter() + timeout
        grid = None
        with diag.loop("await board", tag=self.diag_tag, columns=("board", "found"),
                       expected=f"{expected:024b}", timeout=timeout) as lp:
            while self.running:
                grid = self._read_board()
                lp.frame(board=format(grid.board_mask, "024b"), found=grid.grid_found)
                if grid.grid_found and grid.board_mask == expected:
                    lp.set("result", "board shows the placement")
                    return grid, True
                if time.perf_counter() >= deadline:
                    break
                time.sleep(0.05)
            lp.set("result", f"not matched; last board "
                             f"{'never read' if grid is None else format(grid.board_mask, '024b')}")
        return grid, False

    def _read_board(self) -> JigsawGridResult:
        """detect_grid from a capture of only the grid and the margin around it
        that the reading looks at: the same result as from the whole window,
        without grabbing and copying the whole window on every poll."""
        _, _, win_w, win_h = self.window_manager.get_window_rect()
        area = self.detector.board_area(win_w, win_h)
        if area is None:
            return self.detector.detect_grid(self.capture_full_window())   # not found; logs why
        frame = self.capture.grab(*area)
        if frame is None:
            return JigsawGridResult(False, None, [], 0, 0.0)   # nothing captured: nothing to read
        if frame.shape[:2] != (area[3], area[2]):
            # Not the rectangle asked for (the window render was smaller than
            # its rectangle): read the whole window as before.
            return self.detector.detect_grid(self.capture_full_window())
        grid = self.detector.detect_grid(frame, origin=(area[0], area[1]), window_size=(win_w, win_h))
        if not grid.grid_found and self.detector.auto_grid():
            # Not at the place it was found: the window may have moved,
            # which only a search of the whole window can tell.
            grid = self.detector.detect_grid(self.capture_full_window())
        return grid

    def _confirm_placement(self, before: int, expected: int, decision: JigsawActionResult,
                           cursor_rel: Optional[Tuple[int, int]]) -> bool:
        """Makes sure the piece really landed before the next crate is opened.

        Deciding the next move on a board that does not show the last piece
        yet — or opening a crate while the last piece is still on the cursor
        because the click was lost — is what throws the solver out of step
        with the game. Returns False when the placement did not happen (the
        piece is then discarded so the next round starts clean)."""
        timeout = float(self.config.get("jigsaw_place_verify_timeout", 1.0))
        self._last_placement_matched = False
        first_sent = self._last_click_sent   # the placement click of _perform_decision
        grid, matched = self._await_board(expected, timeout)
        if not matched and diag.ENABLED:
            diag.log("DECISION", f"placement not confirmed: before={before:024b} expected={expected:024b} "
                                 f"now={'?' if grid is None else format(grid.board_mask, '024b')}", tag=self.diag_tag)
            diag.image("jigsaw_placement_not_seen", self.capture_full_window(), tag=self.diag_tag,
                       note=f"decision {decision}")
        if matched:
            self._note_exact_landing(before, expected)
            self._unverified_streak = 0
            self._refused_streak = 0
            self._last_placement_matched = True
            if self._place_dialog is not False and first_sent and not self._last_place_confirmed:
                # Placed on the click alone: this server asks nothing.
                self._place_dialog = False
                self._learn_setting("jigsaw_place_dialog", False)
            return True
        if not self.running:
            return False   # stopped while waiting: send nothing further
        if grid is None or not grid.grid_found or grid.board_mask != before:
            # Something changed, just not exactly as predicted (effect still
            # playing, board cleared after completion): the next round reads
            # the board again and works from what is really there.
            if grid is not None and grid.grid_found:
                self._note_landing_shift(before, expected, grid.board_mask)
            self._unverified_streak = 0
            return True

        if self._place_dialog is False:
            # Saved as "no placement dialog", but the piece did not land: the
            # server may ask now. The retry watches for the dialog again.
            self._place_dialog = None
        if self._place_dialog_still_up():
            # The "Place the piece here?" came later than its wait: it is
            # answered, not clicked through (a board click behind it does nothing).
            if self._last_place_confirmed:
                self._click_lost("placement dialog still open after its Yes")
            self._status("The placement dialog is still open; confirming it")
            retry_sent = self._click_window_point(self.confirm_button_pos, "Confirm placement (late dialog)",
                                                  park_at=cursor_rel)
            self._place_dialog = True
            self._learn_setting("jigsaw_place_dialog", True)
        else:
            self._status("Placement did not show up on the board; clicking again")
            retry_sent = self._place_click(decision.target, "Place jigsaw piece (retry)", cursor_rel)
        grid, matched = self._await_board(expected, timeout)
        self._last_placement_matched = matched
        if matched:
            self._note_exact_landing(before, expected)
        elif grid is not None and grid.grid_found and grid.board_mask != before:
            self._note_landing_shift(before, expected, grid.board_mask)
        if matched or grid is None or not grid.grid_found or grid.board_mask != before:
            self._unverified_streak = 0
            return True

        piece = self._detect_cursor_piece(cursor_rel)
        if not (first_sent and retry_sent):
            # A placement click was never sent (the window was not in front):
            # the game had nothing to refuse, so no cell is suspect. The piece
            # is most likely still held — also when it was not recognized just
            # now (the cursor may not have been brought back without the
            # focus) — so the next round looks at the hand before opening a crate.
            diag.log("DECISION", f"placement not seen, but the click was not sent (first={first_sent}, "
                                 f"retry={retry_sent}, piece seen={piece.piece_id}): no cell marked suspect, "
                                 f"hand checked next round", tag=self.diag_tag)
            self._check_hand = cursor_rel
            return False
        if piece.piece_id is not None and self._bbox_refusal(before, expected, cursor_rel):
            return False
        if piece.piece_id is not None:
            # The game refused: at least one of the target cells is filled
            # although it reads empty. Those cells are avoided from now on.
            self._suspect_mask |= expected & ~before
            self._suspect_board = before
            self._refused_streak += 1
            diag.log("DECISION", f"placement refused ({self._refused_streak} in a row): cells "
                                 f"{expected & ~before:024b} now treated as filled", tag=self.diag_tag)
            self._discard_piece(cursor_rel, "the game did not accept the placement")
            self._check_hand = cursor_rel
            limit = int(self.config.get("jigsaw_max_refused_placements", 2))
            if limit > 0 and self._refused_streak >= limit:
                self._refused_streak = 0
                self._pause("the game refused placements on cells that read as empty — check that nothing "
                            "covers the puzzle" + ("" if self.detector.puzzle is not None and self.detector.auto_grid()
                                                   else ", or re-align the grid with Define Grid"))
            return False
        # No piece in hand and no change on the board: the piece went somewhere
        # the detector cannot see. Repeatedly => the grid is probably misaligned.
        self._unverified_streak += 1
        if self._unverified_streak >= 3:
            self._unverified_streak = 0
            self._pause("placed pieces do not show up on the board — "
                        + ("check that nothing covers the puzzle window"
                           if self.detector.puzzle is not None and self.detector.auto_grid()
                           else "re-align the grid with Define Grid"))
        return False

    _SHIFT_CONFIRMATIONS = 2   # same shifted landing in a row before the aim is corrected
    _SHIFT_MAX = 2             # cells the learned correction may reach per axis

    @staticmethod
    def _size_of(cells: List[int]) -> Tuple[int, int]:
        rows = [c // 6 for c in cells]
        cols = [c % 6 for c in cells]
        return (max(cols) - min(cols) + 1, max(rows) - min(rows) + 1)

    def _note_exact_landing(self, before: int, expected: int) -> None:
        """A piece landed where it was aimed: the shifts seen for its size
        were not the rule. A bounding box with a filled cell that was
        accepted shows this client judges real cells."""
        cells = self._mask_cells(expected & ~before)
        if not cells:
            return
        self._landing_shift_seen.pop(self._size_of(cells), None)
        if not self._bbox_disproven and self._bbox_blocked(before, cells):
            self._bbox_disproven = True
            if self._bbox_fit:
                self._bbox_fit = False
            diag.log("DECISION", "a piece was accepted with a filled cell in its bounding box: this client "
                                 "judges the real cells", tag=self.diag_tag)

    def _note_landing_shift(self, before: int, expected: int, now: int) -> None:
        """The board changed, not as predicted. When the new cells are the
        aimed ones moved by one cell (some clients place a 3-wide piece one
        column right of where it is drawn), that is noted for the piece's
        size; the same shift twice in a row corrects the aim for that size."""
        want = self._mask_cells(expected & ~before)
        got = set(self._mask_cells(now & ~before))
        if not want or len(got) != len(want) or now & before != before:
            return
        shift = None
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if (dc, dr) == (0, 0):
                    continue
                moved = set()
                for cell in want:
                    r, c = cell // 6 + dr, cell % 6 + dc
                    if not (0 <= r < N and 0 <= c < 6):
                        break
                    moved.add(r * 6 + c)
                else:
                    if moved == got:
                        shift = (dc, dr)
        if shift is None:
            return
        size = self._size_of(want)
        seen = self._landing_shift_seen.setdefault(size, [])
        seen.append(shift)
        del seen[:-self._SHIFT_CONFIRMATIONS]
        diag.log("DECISION", f"piece of size {size[0]}x{size[1]} landed shifted by {shift} (cols, rows) from "
                             f"where it was aimed ({len(seen)} in a row)", tag=self.diag_tag)
        if len(seen) < self._SHIFT_CONFIRMATIONS or len(set(seen)) != 1:
            return
        old = self._landing_correction.get(size, (0, 0))
        new = (max(-self._SHIFT_MAX, min(self._SHIFT_MAX, old[0] + shift[0])),
               max(-self._SHIFT_MAX, min(self._SHIFT_MAX, old[1] + shift[1])))
        if new == (0, 0):
            self._landing_correction.pop(size, None)
        else:
            self._landing_correction[size] = new
        self._landing_shift_seen.pop(size, None)
        diag.log("DECISION", f"aim for {size[0]}x{size[1]} pieces corrected by {new} cells: this client "
                             f"places them shifted", tag=self.diag_tag)
        self._status(f"Pieces of size {size[0]}x{size[1]} land shifted on this client; correcting the aim")

    def _bbox_refusal(self, before: int, expected: int, cursor_rel: Optional[Tuple[int, int]]) -> bool:
        """A placement was refused with the piece still on the cursor. On
        some clients an L, mirrored L or Z is only taken when its whole
        bounding box is free, although the server needs only its own cells.
        When the refused piece's box had a filled cell (and no piece was ever
        accepted like that), that is taken as the reason: the bot plays the
        held piece again with that rule instead of discarding it and marking
        cells that are really empty as filled. Returns True when handled."""
        cells = self._mask_cells(expected & ~before)
        if self._bbox_disproven or self._bbox_fit or not cells or not self._bbox_blocked(before, cells):
            return False   # (already playing by that rule: something else is wrong)
        self._refused_streak += 1
        self._bbox_fit = True
        diag.log("DECISION", f"placement refused with a filled cell in the piece's bounding box "
                             f"({self._refused_streak} refused in a row): this client wants the whole box "
                             f"free; playing the held piece again with that rule", tag=self.diag_tag)
        self._status("This game client only takes a piece whose whole box is free; adjusting")
        self._check_hand = cursor_rel
        return True

    # Where the game holds each piece, in cells from the piece's top-left
    # corner: it draws the piece at that offset from the cursor and, on the
    # click, snaps the piece's top-left corner to the nearest cell. Measured
    # on a real client (recordings of diags 20261122_002413/005427/010555):
    # the cursor sits on the center of the top-left cell, except for the
    # line of 3 (middle cell) and the Z (1.25 cells in, top row). Clicking
    # the middle of the piece's outline instead put the Z half a cell too
    # low: the game snapped it a row down, "the piece does not fit there",
    # every time; the 2x2 pieces sat right on a snapping boundary. The
    # special 3x2 piece is held like the Z: the same cursor offset in every
    # client implementation read (2026-11-25), though not measured in game.
    _HOTSPOTS = {0: (0.5, 0.5), 1: (0.5, 1.5), 2: (0.5, 0.5), 3: (0.5, 0.5), 4: (0.5, 0.5), 5: (1.25, 0.5)}

    def _piece_hotspot(self, spec: Optional[PieceSpec], width: int, height: int) -> Tuple[float, float]:
        if spec is not None and spec.figure_index in self._HOTSPOTS:
            return self._HOTSPOTS[spec.figure_index]
        if (width, height) == (3, 2):
            return self._HOTSPOTS[5]      # the special 3x2 piece: held like the Z
        return (width / 2.0, height / 2.0)

    def _target_for_action(self, grid: JigsawGridResult, action: int, spec: Optional[PieceSpec] = None) -> Optional[Tuple[int, int]]:
        """Window point to click for a placement: where the cursor holds the
        piece (_HOTSPOTS) with the piece's top-left corner on the top-left
        target cell."""
        if spec is None:
            x, y = Jigsaw.action_to_offsets(action)
            idx = y * 6 + x
            if 0 <= idx < len(grid.cell_centers):
                return grid.cell_centers[idx]
            return None
        cells = self._cells_for_action(action, spec)
        if not cells:
            return None
        rows = [cell // 6 for cell in cells]
        cols = [cell % 6 for cell in cells]
        min_col, max_col = min(cols), max(cols)
        min_row, max_row = min(rows), max(rows)

        if grid.grid_bounds is not None:
            gx, gy, gw, gh = grid.grid_bounds
            size = (max_col - min_col + 1, max_row - min_row + 1)
            hx, hy = self._piece_hotspot(spec, *size)
            shift = self._landing_correction.get(size)
            if shift:
                # This client lands pieces of this size shifted: aim that
                # much the other way, at the middle of a cell (a client that
                # snaps by the cell under the cursor is the usual cause), and
                # never off the board.
                hx = min(max(math.floor(hx - shift[0]) + 0.5, 0.5 - min_col), 5.5 - min_col)
                hy = min(max(math.floor(hy - shift[1]) + 0.5, 0.5 - min_row), 3.5 - min_row)
            return (int(round(gx + (min_col + hx) * gw / 6.0)), int(round(gy + (min_row + hy) * gh / 4.0)))

        # No bounds: derive the same point from the cell centers.
        min_idx = min_row * 6 + min_col
        max_idx = max_row * 6 + max_col
        if max_idx >= len(grid.cell_centers):
            return None
        min_center = grid.cell_centers[min_idx]
        max_center = grid.cell_centers[max_idx]
        return (
            int(round((min_center[0] + max_center[0]) / 2.0)),
            int(round((min_center[1] + max_center[1]) / 2.0)),
        )

    def _decision_completes_board(
        self,
        grid: JigsawGridResult,
        spec: PieceSpec,
        decision: JigsawActionResult,
        chosen_action: Optional[int] = None,
    ) -> bool:
        if decision.action != "place" or decision.target is None:
            return False
        action = chosen_action
        if action is None:
            try:
                idx = grid.cell_centers.index(decision.target)
            except ValueError:
                return False
            row, col = divmod(idx, 6)
            action = col * N + row
        return (grid.board_mask | (spec.mask >> action)) == FULL_BOARD_MASK

    def _handle_completed_board(self) -> None:
        self._suspect_mask = self._suspect_board = 0   # the next board starts empty
        # After the last piece slots in, the game shows a 3 s "board complete" animation
        # before the result dialog appears. Wait it out before polling for the OK button.
        # The result ("Puzzle complete! You received: ...") came 1.2 s after
        # the last piece on a real client (diag 20261122_082918); a fixed
        # 3 s wait came on top of that. A short pause for the last piece to
        # be drawn, then the OK points are watched.
        post_complete_wait = float(self.config.get("jigsaw_post_complete_wait", 0.3))
        if post_complete_wait > 0:
            self._debug_update("board_complete_wait", f"waiting {post_complete_wait:.1f}s")
            wait_end = time.perf_counter() + post_complete_wait
            while self.running and time.perf_counter() < wait_end:
                time.sleep(0.05)  # stays responsive to Stop

        animation_budget = float(self.config.get("jigsaw_completion_animation_wait", 5.0))
        confirm_budget = float(self.config.get("jigsaw_result_confirm_timeout", 8.0))
        total_budget = max(0.0, animation_budget + confirm_budget)
        offset_x = int(self.config.get("jigsaw_result_confirm_x_offset", 15))
        poll_interval = float(self.config.get("jigsaw_completion_poll_interval", 0.10))

        self._status("Board complete; waiting for the result dialog")
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _handle_completed_board: polling up to {total_budget}s")
        self._debug_update("board_complete", f"polling up to {total_budget:.1f}s for result")

        deadline = time.perf_counter() + total_budget
        frame = None
        configured = self._result_ok_point()
        search_after = time.perf_counter() + float(self.config.get("jigsaw_result_search_after", 2.5))
        baselines = {point: patch for point, patch in (self._result_baseline or {}).items() if patch is not None}
        steady = {point: 0 for point in baselines}
        prev_patch = {point: None for point in baselines}
        before = self._result_before
        self._result_before = None
        prev_gray = None
        last_pick = None
        # Without a shipped template there is no button to search the whole
        # window for, and with neither a set OK point nor a picture of the
        # window from before the board was finished there is nothing to see.
        use_template = self.detector.has_confirm_template()
        with diag.loop("poll result confirm", tag=self.diag_tag, budget=total_budget,
                       at=configured or "found on screen", template=use_template) as lp:
            while self.running and time.perf_counter() < deadline:
                lp.frame()
                target = None
                ok_pos = None
                if use_template:
                    frame = self.capture_full_window()
                    ok_pos = self.detector.detect_confirm_button(frame)
                if ok_pos is not None:
                    target = (ok_pos[0] + offset_x, ok_pos[1])
                    lp.set("result", f"OK template at {ok_pos}, click {target}")
                if target is None and configured is not None and baselines:
                    # The saved OK point (found on an earlier board, or set
                    # by the player): only that spot is watched. The dialog
                    # must be seen there, changed from before the crate and
                    # drawn still.
                    for point, baseline in baselines.items():
                        patch = dialog_watch.read_patch(self._dialog_grab, point,
                                                        (baseline.shape[1], baseline.shape[0]))
                        change = dialog_watch.difference(patch, baseline)
                        lp.score("change", change or 0.0)
                        still = dialog_watch.difference(patch, prev_patch[point])
                        prev_patch[point] = patch
                        # Dialog-sized only (real result dialogs: 37-43); the
                        # world behind the OK point moves it by up to ~15.
                        up = change is not None and change >= dialog_watch.DIALOG \
                            and still is not None and still < dialog_watch.STABLE * 2
                        steady[point] = steady[point] + 1 if up else 0
                        if steady[point] >= 2:
                            target = point
                            lp.set("result", f"dialog seen at {point} (change {change:.1f})")
                if target is None and ok_pos is None and before is not None and (
                        configured is None or not baselines or time.perf_counter() >= search_after):
                    # No saved OK point (or nothing came up there): found on
                    # screen, whatever dialog came up, wherever.
                    gray = self._gray_window()
                    pick = self._pick_result_button(before, gray, prev_gray)
                    prev_gray = gray
                    lp.set("seen", pick)
                    if pick is not None and last_pick is not None \
                            and abs(pick[0] - last_pick[0]) <= 4 and abs(pick[1] - last_pick[1]) <= 4:
                        target = pick       # the same button twice in a row: drawn, not a passing effect
                        lp.set("result", f"OK found at {pick}")
                    last_pick = pick
                if target is not None:
                    if DEBUG_PRINTS or diag.ENABLED:
                        self._dbg(f"[JigsawBot W{self.bot_id + 1}] clicking result confirm at {target}")
                    self._debug_update("result_confirm", f"click={target}", decision_target=target)
                    self._click_window_point(target, "Confirm jigsaw result", park_at=self._parked_point())
                    if target != configured and ok_pos is None and before is not None:
                        self._check_result_gone(before, target)
                    time.sleep(float(self.config.get("jigsaw_action_delay", 0.08)))
                    return
                time.sleep(poll_interval)
            lp.set("result", "not found")
        if diag.ENABLED and self.running:
            diag.image("jigsaw_result_confirm_not_found", frame if frame is not None
                       else self.capture_full_window(), tag=self.diag_tag)

        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] result dialog not seen within {total_budget}s")
        self._status("Jigsaw result dialog not seen — nothing clicked")

    # Positions and facts found on screen once and kept: they do not move
    # between rounds (the game draws its dialogs at the same place every
    # time), so they are not searched for again. Shared by the windows of a
    # run (they have the same size) and saved in the settings through
    # on_setting_learned for the next runs.
    _learned_settings: Dict[str, object] = {}
    _learned_lock = threading.Lock()

    def _setting(self, key: str):
        """A setting: what this run learned (newest), else the config."""
        with JigsawBot._learned_lock:
            if key in JigsawBot._learned_settings:
                return JigsawBot._learned_settings[key]
        return self.config.get(key)

    def _learn_setting(self, key: str, value) -> None:
        """Keeps a found position / fact for the rest of the run and has
        the GUI save it, so the next runs do not look for it again."""
        with JigsawBot._learned_lock:
            if JigsawBot._learned_settings.get(key, self.config.get(key)) == value:
                return
            JigsawBot._learned_settings[key] = value
        self.config[key] = value
        diag.log("DECISION", f"learned {key} = {value}: kept and saved", tag=self.diag_tag)
        if self.on_setting_learned is not None:
            try:
                self.on_setting_learned(key, value)
            except Exception:
                diag.exc("saving a learned setting", tag=self.diag_tag)

    @classmethod
    def reset_learned_settings(cls) -> None:
        """A new run starts from the saved settings (the GUI passes them)."""
        with cls._learned_lock:
            cls._learned_settings.clear()
        with JigsawBot._pace_lock:
            JigsawBot._careful_left = 0    # each run starts with the quick clicks
            JigsawBot._quick_since_loss = None

    def _result_ok_point(self) -> Optional[Tuple[int, int]]:
        """The saved OK point of the result dialog, if it belongs to this
        window size (found or set on another size, it is looked for again)."""
        value = self._setting("jigsaw_result_button_pos")
        try:
            point = (int(value[0]), int(value[1])) if value and len(value) == 2 else None
        except (TypeError, ValueError):
            return None
        if point is None:
            return None
        size = (self._setting("position_window_sizes") or {}).get("jigsaw_result_button_pos")
        if size:
            try:
                _, _, w, h = self.window_manager.get_window_rect()
                if [int(w), int(h)] != [int(size[0]), int(size[1])]:
                    return None
            except Exception:
                diag.exc("result OK point: window size", tag=self.diag_tag)
        return point

    def _result_points(self) -> List[Tuple[int, int]]:
        """The player's OK point for the result dialog (jigsaw_result_button_pos),
        watched as a patch; empty when it is not set: the dialog is then
        found on screen (_pick_result_button)."""
        point = self._result_ok_point()
        return [point] if point is not None else []

    def _gray_window(self) -> Optional[np.ndarray]:
        self.capture.invalidate()
        frame = self.capture_full_window()
        if frame is None or frame.ndim != 3:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def _pick_result_button(self, before: np.ndarray, gray: Optional[np.ndarray],
                            prev: Optional[np.ndarray]) -> Optional[Tuple[int, int]]:
        """The result dialog's OK, found as a dialog that came up anywhere in
        the window since ``before`` (dialog_watch.find_dialogs). Nothing about
        its place is assumed (diag 20261122_082918: the OK was 80 px from
        where a fixed point had it). Panels mostly on the board are the
        board's own "complete" effect. With several buttons the one where
        the OK was found last wins, then the one nearest the Confirm button
        (same height first): game dialogs put their buttons on one line."""
        if gray is None:
            return None
        puzzle = None
        bounds = self.detector.current_grid_bounds()
        if bounds and len(bounds) == 4:
            gx, gy, gw, gh = (int(v) for v in bounds)
            puzzle = (gx - 8, gy - 8, gw + 16, gh + 16)   # the board, where its "complete" effect plays
        buttons = []
        for (x, y, w, h), found in dialog_watch.find_dialogs(before, gray, prev):
            if puzzle is not None:
                px, py, pw, ph = puzzle
                ix = max(0, min(x + w, px + pw) - max(x, px))
                iy = max(0, min(y + h, py + ph) - max(y, py))
                if ix * iy > 0.5 * w * h:
                    continue
            buttons.extend(found)
        if not buttons:
            return None
        learned = self._learned_result_point
        confirm = self.confirm_button_pos

        def rank(point):
            near_learned = 0 if learned and abs(point[0] - learned[0]) <= 8 and abs(point[1] - learned[1]) <= 8 else 1
            if confirm is None:
                return (near_learned, 0, 0)
            return (near_learned, abs(point[1] - confirm[1]), abs(point[0] - confirm[0]))

        best = min(buttons, key=rank)
        return (int(best[0]), int(best[1]))

    def _remember_result_size(self) -> None:
        """The window size the OK point belongs to (a resized window makes it stale)."""
        try:
            _, _, w, h = self.window_manager.get_window_rect()
        except Exception:
            return
        sizes = dict(self._setting("position_window_sizes") or {})
        if sizes.get("jigsaw_result_button_pos") != [int(w), int(h)]:
            sizes["jigsaw_result_button_pos"] = [int(w), int(h)]
            self._learn_setting("position_window_sizes", sizes)

    def _check_result_gone(self, before: np.ndarray, point: Tuple[int, int]) -> None:
        """After the OK click: the dialog gone means the button was the OK
        (remembered for the next board); still there, it is clicked once more."""
        deadline = time.perf_counter() + float(self.config.get("jigsaw_result_gone_timeout", 1.2))
        clear = 0
        while self.running and time.perf_counter() < deadline:
            time.sleep(0.1)
            pick = self._pick_result_button(before, self._gray_window(), None)
            # Gone means no dialog anywhere, twice: another button still seen
            # is the dialog still up (real run 2026-11-26: the click missed the
            # OK, the finder then saw the real OK, and the missed point was
            # saved as the OK for every later board and run).
            clear = clear + 1 if pick is None else 0
            if clear >= 2:
                # It was the OK: kept, and only that spot is watched from now on
                self._learned_result_point = point
                self._learn_setting("jigsaw_result_button_pos", [int(point[0]), int(point[1])])
                self._remember_result_size()
                return
            if pick is not None and pick != point:
                diag.log("DECISION", f"result dialog still up after the click at {point} (a button at {pick}): "
                                     "not learned", tag=self.diag_tag)
                return
        if self.running:
            diag.log("DECISION", f"result dialog still up after the OK click at {point}: clicking again",
                     tag=self.diag_tag)
            self._click_window_point(point, "Confirm jigsaw result (again)", park_at=self._parked_point())

    def _open_crate_and_confirm(self, crate_point: Tuple[int, int], section: str) -> Optional[Tuple[int, int]]:
        """Open a crate, confirm it, and park the cursor with one foreground lock hold."""
        self._status(f"Open {section} jigsaw crate at ({crate_point[0]}, {crate_point[1]})")
        if self.confirm_button_pos is None:
            return self._current_cursor_relative()

        confirm_point = self.confirm_button_pos
        parked = self._parked_point()
        self._crate_dialog_seen = True     # unknown in a dry run / on an early return: as before
        if self.dry_run:
            return parked

        cursor_settle = float(self.config.get("timing_cursor_settle", 0.008))
        # No fixed wait before the Yes: it goes out once the dialog is seen
        # (dialog-sized change, drawn still for 3 reads).
        between_click_delay = float(self.config.get("jigsaw_crate_confirm_delay", 0.0))

        with self._hold():
            if not self._focus():
                self._crate_dialog_seen = None    # nothing was clicked
                return parked
            # Read after the focus: a minimized window was just restored and
            # has its real place only now.
            left, top, _, _ = self.window_manager.get_window_rect()
            crate_x, crate_y = left + crate_point[0], top + crate_point[1]
            confirm_x, confirm_y = left + confirm_point[0], top + confirm_point[1]
            parked_x, parked_y = left + parked[0], top + parked[1]
            switched = self._switched
            self._verify_capture()
            self._glide_to(crate_x, crate_y)
            self._hover(switched)
            baseline = self._confirm_area()
            self._crate_confirm_baseline = baseline
            self._press(hovered=True)

            def click_crate_again():
                self._status("The crate did not open; clicking it again")
                if self._focus():
                    self._glide_to(crate_x, crate_y)
                    self._press(switched=self._switched)

            # Click "confirm" when the dialog is there, not a fixed time later.
            dialog_up = self._wait_for_dialog(baseline, between_click_delay, click_again=click_crate_again)
            if not self.running:
                return parked   # stopped while waiting: leave the dialog alone
            self._crate_dialog_seen = dialog_up
            if not dialog_up:
                # No dialog came — or the confirm area could not be read
                # (no baseline), so there is no telling: a confirm click
                # would land in the game world. The round finds no new
                # piece and tries again (and pauses after a few in a row).
                if baseline is None:
                    diag.log("DECISION", "open crate: confirm area unreadable -> confirm not clicked",
                             tag=self.diag_tag)
                    self._status("Could not read the Confirm area after clicking the crate; not clicking Confirm")
                else:
                    diag.log("DECISION", "open crate: no dialog seen -> confirm not clicked", tag=self.diag_tag)
                    self._status("No dialog appeared after clicking the crate; not clicking Confirm")
                self.human.move_to(parked_x, parked_y)
                return parked
            self._glide_to(confirm_x, confirm_y)
            self._press()
            self.human.move_to(parked_x, parked_y)
            if cursor_settle > 0:
                self.human.sleep(cursor_settle)
        piece_wait = float(self.config.get("jigsaw_post_crate_piece_wait", 0.03))
        if piece_wait > 0:
            time.sleep(piece_wait)
        return parked

    def _parked_point(self) -> Optional[Tuple[int, int]]:
        """Where the cursor rests between actions (rest_point): beside the
        confirm button, where a held piece leaves the watched Confirm area
        clear, and clear of the grid, the crate slots and the inventory
        (checked by layout_problem)."""
        if self.confirm_button_pos is None:
            return None
        return rest_point(self.config, self._grid_width(), self._window_width())

    def _grid_width(self) -> Optional[float]:
        bounds = self.detector.current_grid_bounds()
        try:
            return float(bounds[2])
        except (TypeError, ValueError, IndexError):
            return None

    def _window_width(self) -> Optional[int]:
        try:
            return int(self.window_manager.get_window_rect()[2])
        except Exception:
            diag.exc("window width", tag=self.diag_tag)
            return None

    _PARK_SETTLE = 0.03     # seconds the game gets to redraw a held piece at the parked cursor
    _PIECE_POLL = 0.03      # seconds between looks for the piece after a crate
    _SWITCH_SETTLE = 0.04   # seconds the game gets to track the cursor after a focus switch

    def _glide_to(self, x: int, y: int) -> None:
        """Screen point; input_lock held, window in front."""
        self.human.glide_to(x, y, self._glide)

    def _hover(self, switched: bool = False) -> None:
        """The rest on the target before a press (see _press): short in the
        window that had the focus already, longer right after a focus switch
        or while the careful timing is on (_click_lost)."""
        cursor_settle = float(self.config.get("timing_cursor_settle", 0.008))
        careful = switched or self._careful_now()
        base = self._careful_hover if careful else self._hover_settle
        self._last_click_quick = not careful and base < self._careful_hover
        hover = max(cursor_settle, base, self._SWITCH_SETTLE if switched else 0.0)
        if hover > 0:
            self.human.sleep(hover)

    # Clicks sent with the careful timing after quick ones were lost; shared
    # by the windows of a run (same game, same machine), reset per run. One
    # lost click alone proves nothing (crate clicks were lost now and then
    # with the careful timing too); a second one within LOSS_WINDOW quick
    # clicks does.
    CAREFUL_CLICKS = 200
    LOSS_WINDOW = 50
    _careful_left = 0
    _quick_since_loss: Optional[int] = None   # quick clicks since the last lost one (None: none lost)
    _pace_lock = threading.Lock()

    @staticmethod
    def _careful_now() -> bool:
        return JigsawBot._careful_left > 0

    def _count_click(self) -> None:
        back = False
        with JigsawBot._pace_lock:
            if self._last_click_quick and JigsawBot._quick_since_loss is not None:
                JigsawBot._quick_since_loss += 1
            if JigsawBot._careful_left > 0:
                JigsawBot._careful_left -= 1
                back = JigsawBot._careful_left == 0
        if back:
            diag.log("DECISION", "careful click timing done: quick clicks again", tag=self.diag_tag)

    def _click_lost(self, what: str) -> None:
        """A click the game did not take (no dialog came, a dialog stayed
        open). When it went out with the quick timing and another quick one
        was lost shortly before, the next CAREFUL_CLICKS clicks of every
        window use the careful timing."""
        if not self._last_click_quick:
            return
        with JigsawBot._pace_lock:
            recent = JigsawBot._quick_since_loss
            JigsawBot._quick_since_loss = 0
            careful = recent is not None and recent <= self.LOSS_WINDOW
            if careful:
                first = JigsawBot._careful_left <= 0
                JigsawBot._careful_left = self.CAREFUL_CLICKS
        if not careful:
            diag.log("DECISION", f"{what}: a quick click was not taken (first one lately: timing kept)",
                     tag=self.diag_tag)
            return
        diag.log("DECISION", f"{what}: another quick click was not taken ({recent} quick clicks after the "
                             f"last) -> careful click timing for the next {self.CAREFUL_CLICKS} clicks",
                 tag=self.diag_tag)
        if first:
            self._status("Quick clicks were not taken; clicking more slowly for a while")

    def _press(self, button: str = "left", switched: bool = False, hovered: bool = False) -> None:
        """Clicks where the cursor was just taken (input_lock held, window in
        front), the way the game sees a click: the cursor rests on the target
        for a few frames first (the button under it is "hovered" only after
        the game read it there; after a focus switch the game first has to
        notice the cursor at all), the button stays down for a few frames, and
        the cursor stays on the target a moment after the release (a cursor
        that leaves right away is read off the button when the release is
        handled). ``hovered``: the caller already rested there (_hover)."""
        if not hovered:
            self._hover(switched)
        hold = self._click_hold if self._last_click_quick else max(self._click_hold, self._careful_hold)
        self.human.click(button=button, hold=hold)
        self._count_click()
        if self._release_settle > 0:
            self.human.sleep(self._release_settle)

    def _focus(self) -> bool:
        """Brings this window to the front. input_lock must be held.

        False means the OS did not put the window in front (or the bot was
        stopped): a click sent now would land in whatever is in front — with
        several clients, in another client's puzzle — so the caller must send
        nothing. ``self._switched`` tells whether it was a real switch
        (another window had the focus)."""
        self._switched = False
        if not self.running:
            return False
        before = getattr(self.window_manager, "last_switch_time", 0.0)
        ok = self.window_manager.activate_window()
        if ok is False:
            ok = self.window_manager.activate_window(force_activate=True)
        self._switched = getattr(self.window_manager, "last_switch_time", 0.0) != before
        if ok is False:
            now = time.perf_counter()
            if now - self._last_focus_log >= 5.0:
                self._last_focus_log = now
                self._status("Could not bring the game window to the front — input skipped")
            return False
        return True

    def _park_cursor(self, cursor_rel: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
        """Moves the cursor back to where the piece is held (after a click
        elsewhere in the window)."""
        if cursor_rel is None or self.dry_run:
            return cursor_rel
        with self._hold():
            self._restore_cursor(cursor_rel)
        return cursor_rel

    def _click_window_point(self, point: Tuple[int, int], description: str,
                            park_at: Optional[Tuple[int, int]] = None,
                            on_target: Optional[Callable[[], None]] = None) -> bool:
        """Clicks a window-relative point. With ``park_at`` the cursor is taken
        there right after the click, in the same lock hold: a piece that is
        still on the cursor (the click was not accepted) must not stay over
        the grid, where it looks exactly like placed cells. ``on_target`` is
        called with the cursor resting on the point, right before the press."""
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _click_window_point: {description} rel={point} dry_run={self.dry_run}")
        self._status(f"{description} at ({point[0]}, {point[1]})")
        self._last_click_sent = False
        if self.dry_run:
            return False
        cursor_settle = float(self.config.get("timing_cursor_settle", 0.008))
        with self._hold():
            # Fast path inside activate_window early-returns when our HWND is already foreground.
            if not self._focus():
                diag.log("DECISION", f"{description}: window not in front, click NOT sent", tag=self.diag_tag)
                return False
            # Read after the focus: a restored window has its real place only now.
            left, top, _, _ = self.window_manager.get_window_rect()
            screen_x, screen_y = left + point[0], top + point[1]
            switched = self._switched
            self._glide_to(screen_x, screen_y)
            # After a focus switch the game first has to notice the cursor (and
            # move the held piece to it); a click sent sooner lands where the
            # cursor was the last time this window had it.
            self._hover(switched)
            if on_target is not None:
                on_target()
            self._press(hovered=True)
            if park_at is not None:
                self.human.move_to(left + park_at[0], top + park_at[1])
                self.human.sleep(max(cursor_settle, self._PARK_SETTLE))
            self.capture.invalidate()
        self._last_click_sent = True
        return True

    def _right_click_current_position(self, description: str,
                                      cursor_rel: Optional[Tuple[int, int]] = None,
                                      then_move_to: Optional[Tuple[int, int]] = None) -> None:
        """Right-clicks where this window's piece is held (``cursor_rel``), or at
        the current cursor position when that is unknown. With ``then_move_to``
        the cursor (and the piece with it) goes there right after, in the
        same lock hold."""
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _right_click_current_position: {description} dry_run={self.dry_run}")
        self._status(description)
        if self.dry_run:
            return
        with self._hold():
            if not self._focus():
                return
            if cursor_rel is not None:
                self._restore_cursor(cursor_rel)
            self._press(button="right", switched=self._switched)
            if then_move_to is not None:
                self._settle_held_piece_at(then_move_to)

    def _discard_watch_point(self) -> Optional[Tuple[int, int]]:
        """Where the cursor rests while the discard dialog is awaited: far
        enough beside Confirm that a held piece (up to 3 cells wide) leaves
        the watched Confirm area uncovered (piece_watch_point). The cursor
        normally rests there anyway (rest_point). None when neither side fits
        in the window (the piece then stays where it is held)."""
        if self.dry_run or self.confirm_button_pos is None:
            return None
        win_w = self._window_width()
        if win_w is None:
            return None
        return piece_watch_point(self.confirm_button_pos, self._grid_width(), win_w)

    def _settle_held_piece_at(self, point: Tuple[int, int]) -> None:
        """Moves the cursor (input_lock held, window in front) and gives the
        game a moment to redraw the held piece there."""
        left, top, _, _ = self.window_manager.get_window_rect()
        self.human.move_to(left + point[0], top + point[1])
        self.human.sleep(max(float(self.config.get("timing_cursor_settle", 0.008)), self._PARK_SETTLE))
        self.capture.invalidate()

    def _move_held_piece(self, point: Tuple[int, int]) -> None:
        """Takes the cursor (and a held piece) to a window point. Nothing is
        clicked, and nothing moves when the cursor is there already."""
        if self.dry_run:
            return
        with self._hold():
            if self._cursor_at(point):
                return
            if self._focus():
                self._settle_held_piece_at(point)

    def _verify_capture(self) -> None:
        """While per-window capture is unproven, check it now (input_lock held,
        window just activated)."""
        if self.capture.needs_verification:
            settle = 0.1 - (time.perf_counter() - getattr(self.window_manager, "last_switch_time", 0.0))
            if settle > 0:
                time.sleep(settle)
            self.capture.verify()

    def _activate_window_for_jigsaw(self, description: str) -> None:
        if self.dry_run or self.window_manager is None:
            return
        try:
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] _activate_window_for_jigsaw: {description}")
            self._status(description)
            with self._hold():
                self.window_manager.activate_window()
                self._verify_capture()
        except Exception as e:
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] _activate_window_for_jigsaw ERROR: {e}")
            self._status(f"Could not activate jigsaw window: {e}")

    def _current_cursor_relative(self) -> Optional[Tuple[int, int]]:
        pos = self._cursor_position()
        if pos is None:
            return None
        left, top, _, _ = self.window_manager.get_window_rect()
        return (pos[0] - left, pos[1] - top)

    def _park_cursor_for_capture(self, grid: JigsawGridResult) -> Optional[Tuple[int, int]]:
        """Return the current cursor position without moving it.

        The piece sprite follows the cursor, so any movement before capture would
        drag the piece into the detection area. We capture wherever the cursor
        currently is and let the detector handle the cursor overlay.
        """
        return self._current_cursor_relative()

    def _configured_point(self, key: str) -> Optional[Tuple[int, int]]:
        value = self.config.get(key)
        if not value or len(value) != 2:
            return None
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            return None

    def _resolve_piece(self, piece_id: str, figure_index: Optional[int], section: str) -> Optional[PieceSpec]:
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _resolve_piece: piece_id={piece_id} figure_index={figure_index} section={section}")
        if figure_index is not None:
            key = f"figure_{figure_index}"
            spec = self.pieces.get(key)
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] resolved by figure_index -> key={key} found={spec is not None}")
            return spec
        if piece_id in self.pieces:
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] resolved by piece_id={piece_id}")
            return self.pieces[piece_id]
        if section == "special":
            spec = self.pieces.get("jigsaw_piece_deluxe") or self.pieces.get("special_3x2")
            if DEBUG_PRINTS or diag.ENABLED:
                self._dbg(f"[JigsawBot W{self.bot_id + 1}] special fallback resolved: {spec.piece_id if spec else None}")
            return spec
        if DEBUG_PRINTS or diag.ENABLED:
            self._dbg(f"[JigsawBot W{self.bot_id + 1}] _resolve_piece: no match found for piece_id={piece_id}")
        return None

    def _build_piece_registry(self) -> Dict[str, PieceSpec]:
        pieces: Dict[str, PieceSpec] = {}
        for idx, figure in enumerate(FIGURES):
            pieces[f"figure_{idx}"] = PieceSpec(
                f"figure_{idx}",
                int(figure.value),
                idx,
                figure.max_offset,
            )

        # Default fixed top-section piece: a filled 3x2 rectangle anchored at
        # the top-left of the abstract board. More special masks can be added
        # by config as {"id": [[x,y], ...]} under jigsaw_special_piece_masks.
        pieces["special_3x2"] = PieceSpec(
            "special_3x2",
            _mask_from_offsets((x, y) for x in range(3) for y in range(2)),
            None,
            (3, 2),
        )

        for piece_id, offsets in self.config.get("jigsaw_special_piece_masks", {}).items():
            try:
                norm = [(int(x), int(y)) for x, y in offsets]
                max_x = max(x for x, _ in norm)
                max_y = max(y for _, y in norm)
                pieces[piece_id] = PieceSpec(
                    piece_id,
                    _mask_from_offsets(norm),
                    None,
                    (5 - max_x, 3 - max_y),
                )
            except Exception as e:
                if DEBUG_PRINTS or diag.ENABLED:
                    self._dbg(f"Invalid jigsaw special mask {piece_id}: {e}")
        return pieces

    def _pause(self, reason: str) -> None:
        self._status(f"Jigsaw paused: {reason} (F5 or Resume to continue)")
        self._debug_update("paused", reason)
        self.paused = True
        if self.on_pause_change is not None:
            try:
                self.on_pause_change(self.bot_id, True)
            except Exception:
                diag.exc(tag=self.diag_tag)
                pass

    def _debug_update(self, event: str, detail: str = "", **snapshot) -> None:
        if diag.ENABLED:
            extra = {k: v for k, v in snapshot.items() if k != "frame"}
            diag.log("STATE", f"{event}: {detail} {diag.summarize(extra, 80) if extra else ''}".rstrip(),
                     tag=self.diag_tag, depth=1)
        # Skip when no debug UI is attached: avoids per-event lock contention and
        # multi-MB frame.copy() that otherwise runs ~10x per loop iteration.
        if not self._debug_consumer:
            return
        with self._debug_lock:
            if "frame" in snapshot and isinstance(snapshot["frame"], np.ndarray):
                snapshot["frame"] = snapshot["frame"].copy()
            self._debug_snapshot.update(snapshot)
            self._debug_snapshot["status"] = detail or event
            self._debug_snapshot["last_event"] = event
            self._debug_events.append({"time": time.time(), "event": event, "detail": detail})
            if len(self._debug_events) > 300:
                del self._debug_events[:len(self._debug_events) - 300]

    def debug_snapshot(self) -> dict:
        # Polling for snapshots implies a consumer; enable recording from now on.
        self._debug_consumer = True
        with self._debug_lock:
            return dict(self._debug_snapshot)

    def debug_events(self) -> List[dict]:
        self._debug_consumer = True
        with self._debug_lock:
            return list(self._debug_events)

    def _save_debug(self, frame: np.ndarray, label: str) -> None:
        if not self.config.get("jigsaw_debug_screenshots", False):
            return
        try:
            os.makedirs(self.debug_dir, exist_ok=True)
            path = os.path.join(self.debug_dir, f"{int(time.time() * 1000)}_{label}.png")
            cv2.imwrite(path, frame)
        except Exception:
            diag.exc("jigsaw debug screenshot", tag=self.diag_tag)

    def _status(self, message: str) -> None:
        if diag.ENABLED:
            diag.log("STAT", message, tag=self.diag_tag, depth=1)
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id + 1}] {message}")

    def _dbg(self, message: str) -> None:
        """A decision trace line: diagnostic log (release builds too) and
        console when DEBUG_PRINTS is on."""
        if diag.ENABLED:
            text = message.split("] ", 1)[1] if message.startswith("[JigsawBot") and "] " in message else message
            diag.log("DECISION", text, tag=self.diag_tag, depth=1)
        if DEBUG_PRINTS:
            print(message)

    def _diag_header(self) -> None:
        try:
            keys = sorted(k for k in self.config if k.startswith("jigsaw_"))
            effective = {k: self.config.get(k) for k in keys}
            effective.update({
                "window_rect": self.window_manager.get_window_rect(),
                "confirm_button_pos": self.confirm_button_pos,
                "discard_action_pos": self.discard_action_pos,
                "inventory_pages": self._configured_inventory_pages(),
                "inventory_strip": {"rect": self._inventory_rect(),
                                    "fixed_fallback": {"width_from_right": self._inventory_width,
                                                       "y_offset": self._inventory_y_offset}},
                "mouse_backend_used": getattr(self.mouse_backend, "name", type(self.mouse_backend).__name__),
                "capture_mode": getattr(self.capture, "requested", None),
                "dry_run": self.dry_run,
                "human_like_clicking": self.human.enabled,
                "timing_cursor_settle": self.config.get("timing_cursor_settle"),
            })
            diag.set_effective(f"{self.diag_tag}_jigsaw", effective)
            diag.block("SESSION", f"jigsaw bot {self.diag_tag} starts with:",
                       [f"{k} = {v}" for k, v in effective.items()], tag=self.diag_tag)
        except Exception:
            diag.exc("jigsaw header", tag=self.diag_tag)


# Diagnostic log: entry/exit of every worker method (no-op while off).
diag.trace_class(JigsawBot, exclude=(
    "_status", "_dbg", "_debug_update", "debug_snapshot", "debug_events", "set_debug_consumer",
    "_diag_header", "_cursor_position", "_configured_point",
))

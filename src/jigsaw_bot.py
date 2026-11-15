"""
Automation loop for the Metin2 Fishing Jigsaw event minigame.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

import cv2
import numpy as np

from jigsaw_detector import CrateSlotState, CrateTarget, JigsawActionResult, JigsawDetector, JigsawGridResult
from humanize import Humanizer
from input_backend import create_mouse_backend
from screen_capture import WindowCapture
from utils import DEBUG_MODE_EN, DEBUG_PRINTS, get_project_root, input_lock
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


def _shared_on_press(key) -> None:
    if Key is None or key != Key.f5:
        return
    with _listener_lock:
        subscribers = list(_listener_subscribers)
    # One decision for all windows: if any is paused, F5 resumes everything,
    # otherwise it pauses everything. Toggling each bot on its own left the
    # windows out of step as soon as one of them had paused itself.
    target = not any(bot.paused for bot in subscribers)
    for bot in subscribers:
        try:
            bot._on_f5_pressed(target)
        except Exception:
            pass


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


class JigsawBot:
    """Runs one jigsaw automation worker for one game window."""

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
        self._inventory_width = int(config.get("inventory_capture_width", 200))
        self._inventory_y_offset = int(config.get("inventory_y_offset", 200))
        self._current_inv_page = 0
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
        self._check_hand: Optional[Tuple[int, int]] = None  # look for a held piece here before the next crate
        self._hand_rounds = 0           # rounds played from the hand since the last crate
        self._unknown_streak = 0        # consecutive crates without a recognizable piece
        self._unverified_streak = 0     # consecutive placements the board never showed
        self.boards_completed = 0
        self._switched = False          # the last _focus() was a real window switch
        self._last_focus_log = 0.0
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

    def start(self) -> None:
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] start() called; dry_run={self.dry_run}")
        self.running = True

        # Subscribe to the shared F5-pause listener (one OS hook for all bots).
        _register_pause_subscriber(self)
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] subscribed to shared keyboard listener (F5 to pause/resume)")

        try:
            if self.confirm_button_pos is None:
                self._status("Jigsaw bot requires Confirm Button Coords in Fishbot action coordinates")
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] start() aborted: confirm_button_pos not configured")
                return
            if not self.config.get("jigsaw_grid_bounds"):
                self._status("Jigsaw bot requires Define Grid setup before starting")
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] start() aborted: jigsaw_grid_bounds not configured")
                return
            self._status("Jigsaw bot started")
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] loading solver…")
            self.solver = get_solver()
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] solver loaded; entering run loop")
            self._activate_window_for_jigsaw("Activate jigsaw window before solver loop")
            self._run_loop()
        except Exception as e:
            # Without this the worker thread died silently (the traceback goes
            # to a console the packaged app does not have).
            self._status(f"Jigsaw bot stopped by an error: {type(e).__name__}: {e}")
            if DEBUG_PRINTS:
                import traceback
                traceback.print_exc()
        finally:
            self.running = False
            self._status("Jigsaw bot stopped")
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] stopped; crates_completed={self.crates_completed}")

            _unregister_pause_subscriber(self)
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] unsubscribed from shared keyboard listener")
            try:
                self.capture.close()
            except Exception:
                pass

            if self.on_bot_stop:
                self.on_bot_stop(self.bot_id)

    def stop(self) -> None:
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
                pass
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] F5 pressed: paused={self.paused}")

    def set_debug_consumer(self, attached: bool) -> None:
        """Toggle whether debug snapshots/events are recorded. Default: off."""
        self._debug_consumer = bool(attached)

    def _run_loop(self) -> None:
        # Optimized timings for performance
        action_delay = float(self.config.get("jigsaw_action_delay", 0.08))  # Reduced from 0.15
        retry_delay = float(self.config.get("jigsaw_piece_detection_retry_delay", 0.20))  # Reduced from 0.2
        failure_count = 0
        iteration = 0
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _run_loop() started; action_delay={action_delay}s retry_delay={retry_delay}s")
        while self.running:
            if self.paused:
                time.sleep(0.1)
                continue

            if not self.window_manager.is_window_available():
                self._status("Game window is no longer available; stopping")
                self.running = False
                return

            iteration += 1
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] --- iteration {iteration} | crates_done={self.crates_completed} ---")

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
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] frame captured shape={frame.shape}")
            grid = self.detector.detect_grid(frame)
            if DEBUG_PRINTS:
                print(
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
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] grid not found; failure_count={failure_count}")
                self._status("Fishing Jigsaw grid is not configured or is outside the window")
                self._save_debug(frame, "grid_not_found")
                time.sleep(min(1.5, 0.25 + failure_count * 0.1))
                continue

            failure_count = 0
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] checking crate slots…")
            frame, frame_gray, grid, slots = self._ensure_crates_available(frame, None, grid)
            if not self.running:
                return
            if not grid.grid_found:
                continue  # lost during the refill; look again
            wanted = self.detector._crate_order(self.config.get("jigsaw_crate_priority", "normal_first"))
            available_sections = {section for section in slots.available if section in wanted}
            if not available_sections:
                self._status("No jigsaw crates left in the slot or the inventory; stopping")
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] no crates available; stopping loop")
                self.running = False
                return

            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] available_sections={sorted(available_sections)}")

            max_detection_retries = int(self.config.get("jigsaw_piece_detection_retries", 3))
            piece = None
            section = wanted[0] if wanted else "normal"
            cursor_rel = None

            if self._check_hand is not None:
                # The last round ended without knowing whether a piece is still
                # on the cursor (a dialog may have been confirmed late). Opening
                # a crate on top of a held piece is the one thing that must not
                # happen, so look first and play that piece if it is there.
                cursor_rel, self._check_hand = self._check_hand, None
                piece = self._look_for_piece(cursor_rel, max_detection_retries, retry_delay)
                if piece.piece_id is None or self._hand_rounds >= 2:
                    # Nothing there — or "a piece" was seen there twice running
                    # without a crate being opened, which means something
                    # colorful in the background is being mistaken for one.
                    piece = None
                else:
                    self._hand_rounds += 1
                    self._status("A piece is still on the cursor; playing it before opening a crate")

            if piece is None:
                priority = self.config.get("jigsaw_crate_priority", "normal_first")
                crate = self._select_crate_target(slots, priority, grid.board_mask)
                if crate is None:
                    if priority == "special_when_better" and "normal" not in slots.available:
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

                self._hand_rounds = 0
                cursor_rel = self._open_crate_and_confirm(crate.center, crate.section)
                piece = self._look_for_piece(cursor_rel, max_detection_retries, retry_delay)
                if not self.running:
                    return   # stopped mid-round: send nothing further

                if piece.piece_id is None and not self.dry_run:
                    # Most likely the "open crate?" dialog came up after the
                    # confirm click had already been sent, so it is still
                    # waiting. Confirm once more and look again.
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
                self._unknown_streak += 1
                self._status(f"No recognizable piece on the cursor (confidence {piece.confidence:.2f}); discarding")
                self._discard_piece(cursor_rel, "unknown piece", verify=False)
                self._check_hand = cursor_rel
                if self._unknown_streak >= int(self.config.get("jigsaw_max_unknown_pieces", 3)):
                    self._unknown_streak = 0
                    self._pause("no piece could be recognized several times in a row — check that the "
                                "puzzle window is open and not covered, and the Confirm coordinate")
                time.sleep(action_delay)
                continue
            self._unknown_streak = 0

            spec = self._resolve_piece(piece.piece_id, piece.figure_index, section)
            if DEBUG_PRINTS:
                print(
                    f"[JigsawBot W{self.bot_id + 1}] resolved spec: {spec.piece_id if spec else None} "
                    f"figure_index={spec.figure_index if spec else None} "
                    f"mask={bin(spec.mask) if spec else None}"
                )
            if spec is None:
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] unsupported piece '{piece.piece_id}'; pausing")
                self._pause(f"Unsupported piece '{piece.piece_id}'")
                continue

            # Store the figure index for _target_for_action to calculate proper click position
            self._last_piece_figure_index = spec.figure_index

            # The board mask cannot change from clicking the crate slot or confirming open —
            # only piece placement mutates it. Reuse the grid we already have.
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] computing decision for spec={spec.piece_id}…")
            decision, explanation = self._decide_action(grid, spec)
            if DEBUG_PRINTS:
                print(
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
                grid,
                spec,
                decision,
                explanation.get("chosen_action"),
            )
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] completes_board={completes_board}; performing decision…")
            self._perform_decision(decision, cursor_rel)
            if decision.action == "place" and decision.target is not None and not self.dry_run:
                expected = grid.board_mask | (spec.mask >> explanation["chosen_action"])
                if not self._confirm_placement(grid.board_mask, expected, decision, cursor_rel):
                    completes_board = False
            self.crates_completed += 1
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] crates_completed={self.crates_completed}")
            if self.on_progress_update:
                self.on_progress_update(self.bot_id, self.crates_completed)
            if completes_board:
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] board complete! handling completion…")
                self.boards_completed += 1
                self._handle_completed_board()
            time.sleep(action_delay)

    def capture_full_window(self) -> np.ndarray:
        _, _, width, height = self.window_manager.get_window_rect()
        frame = self.capture.grab(0, 0, width, height)
        if frame is None:
            return np.zeros((100, 100, 3), dtype=np.uint8)
        return frame

    def capture_inventory_area(self) -> np.ndarray:
        _, _, win_width, win_height = self.window_manager.get_window_rect()
        frame = self.capture.grab(
            win_width - self._inventory_width,
            self._inventory_y_offset,
            self._inventory_width,
            win_height - self._inventory_y_offset - 30,
        )
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
            return None

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
        """Looks for the piece on the cursor, a few times: it appears a moment
        after the crate was confirmed."""
        piece = None
        for attempt in range(max(1, attempts)):
            piece = self._detect_cursor_piece(cursor_rel)
            if piece.piece_id is not None or not self.running:
                break
            if attempt < attempts - 1:
                time.sleep(delay)
        return piece

    def _detect_cursor_piece(self, cursor_rel: Optional[Tuple[int, int]]):
        if cursor_rel is None:
            return self.detector.detect_cursor_piece(self.capture_full_window(), None)
        radius = int(self.config.get("jigsaw_cursor_capture_roi", 15))
        radius = max(10, min(24, radius))
        cx, cy = cursor_rel
        x1, y1 = cx - radius, cy - radius
        # Hold the lock across "cursor is in place" + capture so no other
        # window can pull the cursor (and the piece with it) out of the shot.
        with input_lock:
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
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _ensure_crates_available: detecting slots…")
        slots = self.detector.detect_crate_slots(frame, grid, gray=frame_gray)
        if DEBUG_PRINTS:
            print(
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
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] nothing to refill")
            return frame, frame_gray, grid, slots

        missing_names = ", ".join(sorted(to_refill))
        self._status(f"Missing jigsaw crate slot(s): {missing_names}; searching inventory")
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] refilling missing: {missing_names}")
        placed_any = self._refill_missing_crates(to_refill, slots.centers)
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] refill complete; placed_any={placed_any}")

        frame = self.capture_full_window()
        frame_gray = None
        grid = self.detector.detect_grid(frame)
        if not grid.grid_found:
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] grid lost after refill attempt")
            return frame, frame_gray, grid, CrateSlotState({}, set(), {"normal", "special"}, {})
        slots = self.detector.detect_crate_slots(frame, grid, gray=frame_gray)
        if DEBUG_PRINTS:
            print(
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

    def _select_crate_target(self, slots: CrateSlotState, priority: str,
                             board: Optional[int] = None) -> Optional[CrateTarget]:
        if priority == "special_when_better" and board is not None and self.solver is not None:
            order = self._smart_crate_order(slots, board)
        else:
            order = self.detector._crate_order(priority)
        for section in order:
            if section in slots.available and section in slots.centers:
                confidence = 0.50
                if slots.confidence and section in slots.confidence:
                    confidence = slots.confidence[section]
                return CrateTarget(section, slots.centers[section], confidence)
        return None

    def _refill_missing_crates(self, missing: Set[str], slot_centers: Dict[str, Tuple[int, int]]) -> bool:
        pages = self._configured_inventory_pages()
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _refill_missing_crates: missing={sorted(missing)} pages={[p[0]+1 for p in pages]}")
        if not pages:
            self._status("Inventory page coordinates are not configured for jigsaw crate search")
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] no inventory pages configured; aborting refill")
            return False

        needed = set(missing)
        placed_any = False
        for page_idx, page_pos in pages:
            if not self.running or not needed:
                break

            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] scanning inventory page {page_idx + 1} for {sorted(needed)}")
            self._switch_to_inventory_page(page_idx, page_pos)
            inventory_frame = self.capture_inventory_area()
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] inventory frame shape={inventory_frame.shape}")
            found = self._find_inventory_crates(inventory_frame, needed)
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] found in inventory: {list(found.keys())}")
            for section in self.detector._crate_order(self.config.get("jigsaw_crate_priority", "normal_first")):
                if section not in needed or section not in found or section not in slot_centers:
                    continue
                inv_pos = found[section]
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] placing {section}: inv_pos={inv_pos} slot={slot_centers[section]}")
                if self._place_inventory_crate(section, inv_pos, slot_centers[section]):
                    placed_any = True
                    needed.remove(section)
                    if DEBUG_PRINTS:
                        print(f"[JigsawBot W{self.bot_id + 1}] placed {section}; still needed={sorted(needed)}")
                    time.sleep(float(self.config.get("jigsaw_action_delay", 0.15)))

        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _refill_missing_crates done; placed_any={placed_any} remaining_needed={sorted(needed)}")
        if needed and self.running:
            # Every configured page was searched: there are none left.
            self._exhausted_sections |= needed
            self._status(f"No {', '.join(sorted(needed))} jigsaw crate in the inventory")
        return placed_any

    def _configured_inventory_pages(self) -> List[Tuple[int, Tuple[int, int]]]:
        pages: List[Tuple[int, Tuple[int, int]]] = []
        for page_idx in range(8):
            page_num = page_idx + 1
            point = self._configured_point(f"inv_page_{page_num}_pos")
            if point is not None:
                pages.append((page_idx, point))
        return pages

    def _switch_to_inventory_page(self, page_idx: int, page_pos: Tuple[int, int]) -> None:
        self._current_inv_page = page_idx
        self._status(f"Searching inventory page {page_idx + 1} for jigsaw crates")
        self._debug_update("inventory_page", f"page {page_idx + 1}", inventory_page=page_idx + 1)
        if self.dry_run:
            return
        win_left, win_top, _, _ = self.window_manager.get_window_rect()
        with input_lock:
            if self._focus():
                self.human.move_to(win_left + page_pos[0], win_top + page_pos[1])
                self.human.sleep(0.04)
                self.human.click(hold=0.02)
        time.sleep(float(self.config.get("jigsaw_inventory_page_delay", 0.18)))

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
        win_left, win_top, win_width, _ = self.window_manager.get_window_rect()
        inv_x = win_left + win_width - self._inventory_width + inventory_pos[0]
        inv_y = win_top + self._inventory_y_offset + inventory_pos[1]
        slot_x = win_left + slot_pos[0]
        slot_y = win_top + slot_pos[1]
        park = self._parked_point()
        # Coalesce both clicks into a single lock hold so other bots aren't starved
        # by a back-to-back claim/release; activation happens once for both clicks.
        with input_lock:
            if not self._focus():
                return False
            self.human.move_to(inv_x, inv_y)
            if cursor_settle > 0:
                self.human.sleep(cursor_settle)
            self.human.click()
            self.human.sleep(action_delay * 0.8)
            self.human.move_to(slot_x, slot_y)
            if cursor_settle > 0:
                self.human.sleep(cursor_settle)
            self.human.click()
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
        # inventory, so the next search would conclude there are none left.
        for _attempt in range(2):
            time.sleep(max(action_delay, 0.05))
            if not self.running:
                return False
            frame = self.capture_full_window()
            grid = self.detector.detect_grid(frame)
            if not grid.grid_found or section in self.detector.detect_crate_slots(frame, grid).available:
                return True
            self._status(f"The {section} crate did not arrive in its slot; clicking the slot again")
            self._click_window_point(slot_pos, f"Place {section} jigsaw crate (retry)", park_at=park)
        return True

    def _decide_action(self, grid: JigsawGridResult, spec: PieceSpec) -> Tuple[JigsawActionResult, dict]:
        if DEBUG_PRINTS:
            print(
                f"[JigsawBot W{self.bot_id + 1}] _decide_action: spec={spec.piece_id} "
                f"figure_index={spec.figure_index} board_mask={bin(grid.board_mask)}"
            )
        # Ranking is debug-only; skip the 24-iteration np.mean loop on the hot path.
        candidates = self._rank_legal_actions(grid.board_mask, spec) if (self._debug_consumer or DEBUG_PRINTS) else []
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] ranked {len(candidates)} legal actions; top={candidates[:3]}")
        if spec.figure_index is not None:
            state = Jigsaw(grid.board_mask, spec.figure_index, 0)
            action = self.solver.solve(state)
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] solver returned action={action} (SKIP={action == SKIP_ACTION})")
            if action == SKIP_ACTION:
                return JigsawActionResult("discard", reason="solver requested skip"), {"candidates": candidates}
            if not state.is_legal(action):
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] solver action={action} is not legal; discarding")
                return JigsawActionResult("discard", reason="solver action is not legal"), {
                    "chosen_action": action,
                    "candidates": candidates,
                }
            target = self._target_for_action(grid, action, spec)
            cells = self._cells_for_action(action, spec)
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] placing at target={target} cells={cells}")
            return JigsawActionResult("place", target, f"action={action}"), {
                "chosen_action": action,
                "placement_cells": cells,
                "candidates": candidates,
            }

        action = self._best_special_action(grid.board_mask, spec)
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] special piece best_action={action}")
        if action is None:
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] no legal special placement; discarding")
            return JigsawActionResult("discard", reason="no legal special placement"), {"candidates": candidates}
        target = self._target_for_action(grid, action, spec)
        cells = self._cells_for_action(action, spec)
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] special placing at target={target} cells={cells}")
        return JigsawActionResult("place", target, f"special action={action}"), {
            "chosen_action": action,
            "placement_cells": cells,
            "candidates": candidates,
        }

    def _best_special_action(self, board: int, spec: PieceSpec) -> Optional[int]:
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _best_special_action: spec={spec.piece_id} max_offset={spec.max_offset}")
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
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _perform_decision: action={decision.action} target={decision.target} reason={decision.reason}")
        if decision.action == "place" and decision.target is not None:
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] placing piece at {decision.target}")
            self._click_window_point(decision.target, f"Place jigsaw piece ({decision.reason})",
                                     park_at=cursor_rel)
            return

        self._discard_piece(cursor_rel, decision.reason)

    def _discard_piece(self, cursor_rel: Optional[Tuple[int, int]], reason: str,
                       verify: bool = True) -> None:
        """Right-clicks the held piece and confirms the discard dialog.
        ``verify``: check afterwards that the piece left the cursor (pointless
        when no piece could be recognized there in the first place)."""
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] discarding piece: {reason}")
        self._status(f"Discarding jigsaw piece: {reason}")
        baseline = self._confirm_area()
        self._right_click_current_position("Discard jigsaw piece", cursor_rel)
        self._wait_for_dialog(baseline, float(self.config.get("jigsaw_action_delay", 0.08)) * 0.6)
        if not self.running:
            return   # stopped while waiting: leave the dialog alone
        self._click_window_point(self.confirm_button_pos, "Confirm discard",
                                 park_at=cursor_rel if verify else None)
        if not verify or cursor_rel is None or self.dry_run or not self.running:
            return
        # A piece that was recognized must be gone now. If it is still on the
        # cursor the dialog came up after the confirm click: confirm again,
        # and have the next round look at the hand before it opens a crate.
        time.sleep(float(self.config.get("jigsaw_piece_detection_retry_delay", 0.20)) * 0.5)
        if self._detect_cursor_piece(cursor_rel).piece_id is None:
            return
        self._status("The piece is still on the cursor after the discard; confirming again")
        self._click_window_point(self.confirm_button_pos, "Confirm discard again", park_at=cursor_rel)
        self._check_hand = cursor_rel

    # ------------------------------------------------------------ verification

    _CONFIRM_AREA = (40, 16)   # width, height of the patch watched for a dialog

    def _confirm_area(self) -> Optional[np.ndarray]:
        """Small grayscale patch around the confirm button (None in dry run)."""
        if self.dry_run or self.confirm_button_pos is None:
            return None
        w, h = self._CONFIRM_AREA
        cx, cy = self.confirm_button_pos
        self.capture.invalidate()
        patch = self.capture_window_region(cx - w // 2, cy - h // 2, w, h)
        if patch.shape[0] < 4 or patch.shape[1] < 4:
            return None
        return cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)

    def _wait_for_dialog(self, baseline: Optional[np.ndarray], minimum: float) -> bool:
        """Waits until the confirm-button area changes, i.e. the dialog is up.

        The dialog does not appear a fixed time after the click that asks for
        it — it depends on the client and, with several windows, on the focus
        switch — and a confirm click sent before it is there is simply lost.
        Always waits at least ``minimum``; gives up after the configured
        timeout and lets the caller click anyway (the old fixed-delay
        behavior). Returns True when a change was seen."""
        start = time.perf_counter()
        seen = False
        if baseline is not None:
            deadline = start + float(self.config.get("jigsaw_dialog_wait_timeout", 0.6))
            while self.running and time.perf_counter() < deadline:
                current = self._confirm_area()
                if current is None or current.shape != baseline.shape:
                    break
                if float(cv2.absdiff(current, baseline).mean()) > 6.0:
                    seen = True
                    break
                time.sleep(0.012)
        remaining = minimum - (time.perf_counter() - start)
        if remaining > 0:
            time.sleep(remaining)
        return seen

    def _await_board(self, expected: int, timeout: float) -> Tuple[Optional[JigsawGridResult], bool]:
        """Reads the board until it shows ``expected`` or ``timeout`` passes.
        Returns (last reading, matched)."""
        deadline = time.perf_counter() + timeout
        grid = None
        while self.running:
            grid = self.detector.detect_grid(self.capture_full_window())
            if grid.grid_found and grid.board_mask == expected:
                return grid, True
            if time.perf_counter() >= deadline:
                break
            time.sleep(0.05)
        return grid, False

    def _confirm_placement(self, before: int, expected: int, decision: JigsawActionResult,
                           cursor_rel: Optional[Tuple[int, int]]) -> bool:
        """Makes sure the piece really landed before the next crate is opened.

        Deciding the next move on a board that does not show the last piece
        yet — or opening a crate while the last piece is still on the cursor
        because the click was lost — is what throws the solver out of step
        with the game. Returns False when the placement did not happen (the
        piece is then discarded so the next round starts clean)."""
        timeout = float(self.config.get("jigsaw_place_verify_timeout", 1.0))
        grid, matched = self._await_board(expected, timeout)
        if matched:
            self._unverified_streak = 0
            return True
        if not self.running:
            return False   # stopped while waiting: send nothing further
        if grid is None or not grid.grid_found or grid.board_mask != before:
            # Something changed, just not exactly as predicted (effect still
            # playing, board cleared after completion): the next round reads
            # the board again and works from what is really there.
            self._unverified_streak = 0
            return True

        self._status("Placement did not show up on the board; clicking again")
        self._click_window_point(decision.target, "Place jigsaw piece (retry)", park_at=cursor_rel)
        grid, matched = self._await_board(expected, timeout)
        if matched or grid is None or not grid.grid_found or grid.board_mask != before:
            self._unverified_streak = 0
            return True

        piece = self._detect_cursor_piece(cursor_rel)
        if piece.piece_id is not None:
            self._discard_piece(cursor_rel, "the game did not accept the placement")
            self._check_hand = cursor_rel
            return False
        # No piece in hand and no change on the board: the piece went somewhere
        # the detector cannot see. Repeatedly => the grid is probably misaligned.
        self._unverified_streak += 1
        if self._unverified_streak >= 3:
            self._unverified_streak = 0
            self._pause("placed pieces do not show up on the board — re-align the grid with Define Grid")
        return False

    def _target_for_action(self, grid: JigsawGridResult, action: int, spec: Optional[PieceSpec] = None) -> Optional[Tuple[int, int]]:
        """Window point to click for a placement: the geometric center of the
        cells the piece will occupy (the game holds the piece centered on the
        cursor and snaps it to the grid)."""
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
            center_col = (min_col + max_col + 1) / 2.0
            center_row = (min_row + max_row + 1) / 2.0
            return (int(round(gx + center_col * gw / 6.0)), int(round(gy + center_row * gh / 4.0)))

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
        # After the last piece slots in, the game shows a 3 s "board complete" animation
        # before the result dialog appears. Wait it out before polling for the OK button.
        post_complete_wait = float(self.config.get("jigsaw_post_complete_wait", 3.0))
        if post_complete_wait > 0:
            self._status(f"Board complete; waiting {post_complete_wait:.1f}s for animation")
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] post-complete wait {post_complete_wait}s")
            self._debug_update("board_complete_wait", f"waiting {post_complete_wait:.1f}s")
            wait_end = time.perf_counter() + post_complete_wait
            while self.running and time.perf_counter() < wait_end:
                time.sleep(0.05)  # stays responsive to Stop

        animation_budget = float(self.config.get("jigsaw_completion_animation_wait", 5.0))
        confirm_budget = float(self.config.get("jigsaw_result_confirm_timeout", 8.0))
        total_budget = max(0.0, animation_budget + confirm_budget)
        offset_x = int(self.config.get("jigsaw_result_confirm_x_offset", 15))
        poll_interval = float(self.config.get("jigsaw_completion_poll_interval", 0.10))

        self._status(f"Board complete; polling for result confirm (budget {total_budget:.1f}s)")
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _handle_completed_board: polling up to {total_budget}s")
        self._debug_update("board_complete", f"polling up to {total_budget:.1f}s for result")

        deadline = time.perf_counter() + total_budget
        while self.running and time.perf_counter() < deadline:
            frame = self.capture_full_window()
            # detect_confirm_button does its own gray conversion only if needed.
            ok_pos = self.detector.detect_confirm_button(frame)
            if ok_pos is not None:
                target = (ok_pos[0] + offset_x, ok_pos[1])
                if DEBUG_PRINTS:
                    print(f"[JigsawBot W{self.bot_id + 1}] clicking result confirm at {target}")
                self._debug_update("result_confirm", f"OK={ok_pos} click={target}", decision_target=target)
                self._click_window_point(target, "Confirm jigsaw result")
                time.sleep(float(self.config.get("jigsaw_action_delay", 0.08)))
                return
            time.sleep(poll_interval)

        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] result confirm button not detected within {total_budget}s")
        self._status("Jigsaw result confirm button not detected")

    def _open_crate_and_confirm(self, crate_point: Tuple[int, int], section: str) -> Optional[Tuple[int, int]]:
        """Open a crate, confirm it, and park the cursor with one foreground lock hold."""
        self._status(f"Open {section} jigsaw crate at ({crate_point[0]}, {crate_point[1]})")
        if self.confirm_button_pos is None:
            return self._current_cursor_relative()

        shift_px = int(self.config.get("jigsaw_post_crate_cursor_right_px", 24))
        confirm_point = self.confirm_button_pos
        parked = self._parked_point()
        if self.dry_run:
            return parked

        left, top, _, _ = self.window_manager.get_window_rect()
        cursor_settle = float(self.config.get("timing_cursor_settle", 0.008))
        between_click_delay = float(self.config.get("jigsaw_crate_confirm_delay", 0.06))
        crate_x, crate_y = left + crate_point[0], top + crate_point[1]
        confirm_x, confirm_y = left + confirm_point[0], top + confirm_point[1]
        parked_x, parked_y = left + parked[0], top + parked[1]

        with input_lock:
            if not self._focus():
                return parked
            switched = self._switched
            self._verify_capture()
            self.human.move_to(crate_x, crate_y)
            self.human.sleep(max(cursor_settle, self._SWITCH_SETTLE if switched else 0.0))
            baseline = self._confirm_area()
            self.human.click()
            # Click "confirm" when the dialog is there, not a fixed time later.
            self._wait_for_dialog(baseline, between_click_delay)
            if not self.running:
                return parked   # stopped while waiting: leave the dialog alone
            self.human.move_to(confirm_x, confirm_y)
            if cursor_settle > 0:
                self.human.sleep(cursor_settle)
            self.human.click()
            if shift_px:
                self.human.move_to(parked_x, parked_y)
                if cursor_settle > 0:
                    self.human.sleep(cursor_settle)
        piece_wait = float(self.config.get("jigsaw_post_crate_piece_wait", 0.05))
        if piece_wait > 0:
            time.sleep(piece_wait)
        return parked

    def _parked_point(self) -> Optional[Tuple[int, int]]:
        """Where the cursor rests between actions: beside the confirm button,
        clear of the grid, the crate slots and the inventory."""
        if self.confirm_button_pos is None:
            return None
        shift_px = int(self.config.get("jigsaw_post_crate_cursor_right_px", 24))
        return (self.confirm_button_pos[0] + shift_px, self.confirm_button_pos[1])

    _PARK_SETTLE = 0.03     # seconds the game gets to redraw a held piece at the parked cursor
    _SWITCH_SETTLE = 0.04   # seconds the game gets to track the cursor after a focus switch

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
        with input_lock:
            self._restore_cursor(cursor_rel)
        return cursor_rel

    def _click_window_point(self, point: Tuple[int, int], description: str,
                            park_at: Optional[Tuple[int, int]] = None) -> None:
        """Clicks a window-relative point. With ``park_at`` the cursor is taken
        there right after the click, in the same lock hold: a piece that is
        still on the cursor (the click was not accepted) must not stay over
        the grid, where it looks exactly like placed cells."""
        left, top, _, _ = self.window_manager.get_window_rect()
        screen_x, screen_y = left + point[0], top + point[1]
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _click_window_point: {description} rel={point} screen=({screen_x},{screen_y}) dry_run={self.dry_run}")
        self._status(f"{description} at ({point[0]}, {point[1]})")
        if self.dry_run:
            return
        cursor_settle = float(self.config.get("timing_cursor_settle", 0.008))
        with input_lock:
            # Fast path inside activate_window early-returns when our HWND is already foreground.
            if not self._focus():
                return
            switched = self._switched
            self.human.move_to(screen_x, screen_y)
            # After a focus switch the game first has to notice the cursor (and
            # move the held piece to it); a click sent sooner lands where the
            # cursor was the last time this window had it.
            settle = max(cursor_settle, self._SWITCH_SETTLE if switched else 0.0)
            if settle > 0:
                self.human.sleep(settle)
            self.human.click()
            if park_at is not None:
                self.human.sleep(max(cursor_settle, 0.02))   # let the click be processed first
                self.human.move_to(left + park_at[0], top + park_at[1])
                self.human.sleep(max(cursor_settle, self._PARK_SETTLE))
            self.capture.invalidate()

    def _right_click_current_position(self, description: str,
                                      cursor_rel: Optional[Tuple[int, int]] = None) -> None:
        """Right-clicks where this window's piece is held (``cursor_rel``), or at
        the current cursor position when that is unknown."""
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _right_click_current_position: {description} dry_run={self.dry_run}")
        self._status(description)
        if self.dry_run:
            return
        with input_lock:
            if not self._focus():
                return
            if cursor_rel is not None:
                self._restore_cursor(cursor_rel)
            self.human.click(button="right")

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
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] _activate_window_for_jigsaw: {description}")
            self._status(description)
            with input_lock:
                self.window_manager.activate_window()
                self._verify_capture()
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] _activate_window_for_jigsaw ERROR: {e}")
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
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _resolve_piece: piece_id={piece_id} figure_index={figure_index} section={section}")
        if figure_index is not None:
            key = f"figure_{figure_index}"
            spec = self.pieces.get(key)
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] resolved by figure_index -> key={key} found={spec is not None}")
            return spec
        if piece_id in self.pieces:
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] resolved by piece_id={piece_id}")
            return self.pieces[piece_id]
        if section == "special":
            spec = self.pieces.get("jigsaw_piece_deluxe") or self.pieces.get("special_3x2")
            if DEBUG_PRINTS:
                print(f"[JigsawBot W{self.bot_id + 1}] special fallback resolved: {spec.piece_id if spec else None}")
            return spec
        if DEBUG_PRINTS:
            print(f"[JigsawBot W{self.bot_id + 1}] _resolve_piece: no match found for piece_id={piece_id}")
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
                if DEBUG_PRINTS:
                    print(f"Invalid jigsaw special mask {piece_id}: {e}")
        return pieces

    def _pause(self, reason: str) -> None:
        self._status(f"Jigsaw paused: {reason} (F5 or Resume to continue)")
        self._debug_update("paused", reason)
        self.paused = True
        if self.on_pause_change is not None:
            try:
                self.on_pause_change(self.bot_id, True)
            except Exception:
                pass

    def _debug_update(self, event: str, detail: str = "", **snapshot) -> None:
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
            pass

    def _status(self, message: str) -> None:
        if self.on_status_update:
            self.on_status_update(f"[W{self.bot_id + 1}] {message}")

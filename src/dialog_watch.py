"""
Watching a small patch of the game window for a dialog, shared by the
fishing bot (drop) and the jigsaw bot (open crate, discard, refill).

A dialog is "up" when the patch around one of its buttons no longer looks
like it did before the click that asks for it, and "gone" when it looks like
that again. Both bots used to click the button after a fixed delay, which
sends a confirm into the game world when the dialog never came.

``grab(x, y, w, h)`` is a window-relative capture returning BGR or None.
"""

import time
from typing import Callable, Optional, Tuple

import cv2
import numpy as np

import diag

Point = Tuple[int, int]
Grab = Callable[[int, int, int, int], Optional[np.ndarray]]

PATCH = (40, 16)        # width, height of the watched patch
CHANGED = 15.0          # mean gray difference that counts as a change. Replayed on a real
                        # client (tests/test_dialog_replay.py): a dialog changes the patch by
                        # 29-47, the game world moving behind it by 7-12. At 6 the world alone
                        # counted as "dialog up", and a dialog that had closed over a moving
                        # world was often never seen "gone".
DIALOG = 25.0           # a change this large is a dialog, learned look or not. Real dialogs
                        # measure 30-50; after one closed, the world behind the button sat at
                        # 15-19 (diag 20261121_200639) and must not count as the dialog.
STABLE = 3.0            # a dialog is drawn still: consecutive reads differ by less than this
STABLE_READS = 3        # ... over this many reads in a row before it counts as "up"
SAME_DIALOG = 10.0      # mean difference to a learned dialog look that still counts as that dialog

# What the patch looked like with the dialog up, per window (``owner``) and
# button point, once a whole sequence confirmed it really was the dialog (see
# ``confirm``). With that known, "up" means "looks like the dialog", not just
# "changed": moving scenery behind the button point changes the patch too.
# Per window: a client with another skin would otherwise never see its own
# dialog as "up" after another window taught the look.
_learned: dict = {}
_last_up: dict = {}


def _key(point: Point, what: str = "dialog", owner=None) -> tuple:
    # Per dialog name too: two features may watch the same point for
    # dialogs that do not look alike.
    return owner, what, int(point[0]), int(point[1])


def confirm(point: Point, what: str = "dialog", owner=None) -> None:
    """The last patch seen "up" at ``point`` (watched as ``what``) really
    was the dialog (the action it asked for happened): remember its look
    for later waits."""
    if point is None:
        return
    patch = _last_up.get(_key(point, what, owner))
    if patch is not None:
        _learned[_key(point, what, owner)] = patch


def forget() -> None:
    """Drops everything learned (after a coordinate change the old look
    belongs to another place)."""
    _learned.clear()
    _last_up.clear()


def read_patch(grab: Grab, point: Point, size: Tuple[int, int] = PATCH) -> Optional[np.ndarray]:
    """Grayscale patch centered on ``point`` (None when it cannot be read)."""
    if point is None:
        return None
    w, h = size
    try:
        frame = grab(int(point[0]) - w // 2, int(point[1]) - h // 2, w, h)
    except Exception:
        diag.exc("dialog patch")
        return None
    if frame is None or frame.ndim != 3 or frame.shape[0] < 4 or frame.shape[1] < 4:
        return None
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def difference(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> Optional[float]:
    """Mean absolute gray difference (None when either is missing)."""
    if a is None or b is None or a.shape != b.shape:
        return None
    return float(cv2.absdiff(a, b).mean())


def _learned_at(point: Point, baseline: Optional[np.ndarray], what: str, owner) -> Optional[np.ndarray]:
    learned = _learned.get(_key(point, what, owner))
    if learned is not None and baseline is not None and learned.shape != baseline.shape:
        return None
    return learned


def _matches_learned(patch: Optional[np.ndarray], learned: Optional[np.ndarray]) -> bool:
    return learned is not None and difference(patch, learned) is not None and difference(patch, learned) < SAME_DIALOG


def _held_still(patch: Optional[np.ndarray], prev: Optional[np.ndarray]) -> bool:
    change = difference(patch, prev)
    return change is not None and change < STABLE


def _is_up(change: Optional[float], patch: Optional[np.ndarray], prev: Optional[np.ndarray],
           learned: Optional[np.ndarray], accept_strong: bool, strong_only: bool = False) -> bool:
    """One read of a dialog that is up: changed, drawn still, and either the
    learned look or (when ``accept_strong``) a dialog-sized change. A learned
    look that does not match is a different dialog than last time; a
    dialog-sized change is still that dialog. ``strong_only``: only a
    dialog-sized change counts at all."""
    if change is None or change <= CHANGED or not _held_still(patch, prev):
        return False
    if strong_only:
        return change >= DIALOG
    if learned is None or _matches_learned(patch, learned):
        return True
    return bool(accept_strong and change >= DIALOG)


def _is_present(change: Optional[float], patch: Optional[np.ndarray], learned: Optional[np.ndarray]) -> bool:
    """One read of a dialog still showing. The learned look counts, and so
    does a dialog-sized change that does not match it. A shift of 15-19 with
    no learned look is the world after the dialog closed, not the dialog."""
    if change is None or change <= CHANGED:
        return False
    if _matches_learned(patch, learned):
        return True
    return change >= DIALOG


def wait(grab: Grab, point: Point, baseline: Optional[np.ndarray], timeout: float,
         keep_going: Callable[[], bool], want_change: bool = True, minimum: float = 0.0,
         poll: float = 0.012, what: str = "dialog", tag: Optional[str] = None, owner=None,
         accept_strong: bool = False, strong_only: bool = False) -> bool:
    """Polls the patch at ``point`` until it differs from ``baseline``
    (``want_change``: the dialog came up) or matches it again (the dialog
    went away). "Up" needs the changed patch to hold still for
    ``STABLE_READS`` reads and, once ``confirm`` has taught the dialog's
    look at this point, to resemble it. ``accept_strong`` also accepts a
    dialog-sized change that does not match that look: the button is a
    different dialog than the one learned there. ``strong_only`` counts only a
    dialog-sized change (DIALOG), for a caller that never learns the look:
    a real character's idle animation behind the button moved the patch by
    15.08 and was taken for the dialog (diag 20261122_093507). Waits at least ``minimum``
    seconds in any case. Returns whether that was seen within ``timeout``;
    False also when the patch cannot be read or ``keep_going()`` turns False."""
    start = time.perf_counter()
    seen = False
    if baseline is not None:
        deadline = start + timeout
        learned = _learned_at(point, baseline, what, owner)
        prev = None
        steady = 0
        with diag.loop(f"wait for {what} {'up' if want_change else 'gone'}", tag=tag, at=point,
                       timeout=round(timeout, 2), learned=learned is not None) as lp:
            while keep_going():
                patch = read_patch(grab, point, (baseline.shape[1], baseline.shape[0]))
                change = difference(patch, baseline)
                if change is None:
                    lp.set("result", "patch unreadable")
                    break
                lp.score("change", change)
                if not want_change:
                    # One read like the old look is enough: replayed on a
                    # real client it never came while the dialog was up.
                    # Once the dialog's look is known, one read that no
                    # longer resembles it is enough too: the world behind
                    # it can differ from the old look by 15-19 for good (a
                    # rod or character that moved since the baseline), so
                    # "like the old look" alone was missed on a real
                    # client (diag 20261121_200639) although the dialog
                    # had closed.
                    if change <= CHANGED or (learned is not None
                                             and difference(patch, learned) >= SAME_DIALOG):
                        seen = True
                        break
                else:
                    # Up = changed, drawn still for a few reads (scenery and
                    # effects keep moving) and, once the dialog's look is
                    # known, looking like it. accept_strong: a dialog-sized
                    # change counts when the learned look is from another dialog.
                    steady = steady + 1 if _is_up(change, patch, prev, learned, accept_strong,
                                                  strong_only) else 0
                    prev = patch
                    if steady >= STABLE_READS - 1:
                        _last_up[_key(point, what, owner)] = patch
                        seen = True
                        break
                if time.perf_counter() >= deadline:
                    break
                time.sleep(poll)
            lp.set("seen", seen)
    remaining = minimum - (time.perf_counter() - start)
    if remaining > 0:
        time.sleep(remaining)
    return seen


def wait_first(grab: Grab, choices, timeout: float, keep_going: Callable[[], bool], minimum: float = 0.0,
               poll: float = 0.012, tag: Optional[str] = None, owner=None) -> Optional[str]:
    """Watches several button points at once. ``choices`` is
    ``(name, point, baseline, what, accept_strong)``, preferred name first.
    Returns the name of the dialog that came up, or None.

    When more than one is up on the same read, a dialog-sized change wins
    over a smaller one. The real button moves its patch by ~30-50; the other
    point can move by less while that dialog is open (diag 20261121_200639:
    the Drop / Sell point moved 20-34 under a yes / no dialog)."""
    start = time.perf_counter()
    watches = []
    for name, point, baseline, what, accept_strong in choices:
        if point is None or baseline is None:
            continue
        watches.append({
            "name": name, "point": point, "baseline": baseline, "what": what,
            "accept_strong": bool(accept_strong), "learned": _learned_at(point, baseline, what, owner),
            "prev": None, "steady": 0, "change": 0.0, "patch": None,
        })
    winner = None
    if watches:
        deadline = start + timeout
        with diag.loop("wait for a drop dialog", tag=tag, timeout=round(timeout, 2),
                       choices=len(watches)) as lp:
            while keep_going():
                ready = []
                readable = False
                for watch in watches:
                    baseline = watch["baseline"]
                    patch = read_patch(grab, watch["point"], (baseline.shape[1], baseline.shape[0]))
                    change = difference(patch, baseline)
                    if change is None:
                        watch["steady"] = 0
                        watch["prev"] = None
                        continue
                    readable = True
                    watch["change"] = change
                    watch["patch"] = patch
                    lp.score(watch["name"], change)
                    watch["steady"] = watch["steady"] + 1 if _is_up(
                        change, patch, watch["prev"], watch["learned"], watch["accept_strong"]) else 0
                    watch["prev"] = patch
                    if watch["steady"] >= STABLE_READS - 1:
                        ready.append(watch)
                if not readable:
                    lp.set("result", "patch unreadable")
                    break
                if ready:
                    strong = [watch for watch in ready if watch["change"] >= DIALOG]
                    pool = strong or ready
                    best = max(pool, key=lambda watch: watch["change"])
                    _last_up[_key(best["point"], best["what"], owner)] = best["patch"]
                    winner = best["name"]
                    break
                if time.perf_counter() >= deadline:
                    break
                time.sleep(poll)
            lp.set("seen", winner)
    remaining = minimum - (time.perf_counter() - start)
    if remaining > 0:
        time.sleep(remaining)
    return winner


def still_up(grab: Grab, point: Point, baseline: Optional[np.ndarray], what: str = "dialog",
             owner=None, reads: int = 3, gap: float = 0.03) -> bool:
    """Does the dialog still show at ``point``? Every one of ``reads``
    must be the dialog: its learned look, or a dialog-sized change.
    A shift of 15-19 is the world behind the button after the dialog
    closed (diag 20261121_200639) and answers no, learned look or not,
    unless the patch still resembles the dialog. Used before clicking a
    button a second time: a click on the world behind a dialog that is
    not there makes the character walk."""
    if baseline is None or point is None:
        return False
    learned = _learned_at(point, baseline, what, owner)
    for i in range(reads):
        patch = read_patch(grab, point, (baseline.shape[1], baseline.shape[0]))
        if not _is_present(difference(patch, baseline), patch, learned):
            return False
        if i + 1 < reads:
            time.sleep(gap)
    return True


# --------------------------------------------------------------- finding a dialog

def find_dialogs(before: np.ndarray, now: np.ndarray, prev: Optional[np.ndarray] = None):
    """Dialogs that appeared somewhere in the window: [(rect, [button centers])].

    ``before``, ``now`` and ``prev`` are grayscale frames of the whole
    window: before the dialog was asked for, now, and the poll before now.
    A dialog is a panel that differs from ``before`` (a dark board over the
    world), is drawn still (``prev``: scenery and effects keep moving) and
    is at least 120 x 40. Its buttons are found inside it (``_buttons``).
    Nothing about where the game puts a dialog is assumed: it may be at
    the top of the window, in the middle or anywhere else."""
    if before is None or now is None or before.shape != now.shape:
        return []
    changed = cv2.absdiff(now, before) > PANEL_CHANGE
    if prev is not None and prev.shape == now.shape:
        changed &= cv2.absdiff(now, prev) < PANEL_STILL
    mask = cv2.morphologyEx(changed.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    # A dialog is a closed box: what its outline encloses belongs to it, also
    # where its board happens to look like the world behind it.
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(mask, contours, -1, 1, thickness=cv2.FILLED)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    height, width = now.shape[:2]
    found = []
    for k in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[k])
        if w < 120 or h < 40 or w > 0.9 * width or h > 0.9 * height or area < 0.6 * w * h:
            continue
        buttons = _buttons(now, (x, y, w, h))
        if buttons:
            found.append(((x, y, w, h), buttons))
    return found


PANEL_CHANGE = 30       # least gray change a dialog's board makes over the world behind it (real: 40-60)
PANEL_STILL = 8         # ... while it differs from the poll before by less than this


def _buttons(gray: np.ndarray, rect) -> list:
    """Centers of the buttons in the lower part of a dialog panel, found by
    shape only: a button is a solid box 35-160 px wide and 10-40 px tall,
    wider than tall, whose face stands out from the board around it. No
    color or brightness level is assumed (another server's skin may draw
    light buttons on a dark board or the other way round): "stands out" is
    measured against the board's own typical gray and spread, after a blur
    that evens out the board's texture and the button's label. No text is
    read (labels change with the language); a line of the message is not a
    solid box. Real client: OK 57 x 17, Yes / No the same."""
    x, y, w, h = rect
    panel = cv2.blur(gray[y:y + h, x:x + w], (5, 5)).astype(np.int16)
    board = float(np.median(panel))
    spread = float(np.median(np.abs(panel - board)))
    mask = (np.abs(panel - board) > max(BUTTON_CONTRAST, 2.0 * spread)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    buttons = []
    for k in range(1, count):
        bx, by, bw, bh, area = (int(v) for v in stats[k])
        if not (35 <= bw <= 160 and 10 <= bh <= 40 and bw >= 1.5 * bh):
            continue
        if by + bh / 2 < h * 0.35 or bw > 0.8 * w or area < 0.6 * bw * bh:
            continue
        buttons.append((x + bx + bw // 2, y + by + bh // 2))
    return buttons


BUTTON_CONTRAST = 8     # least gray difference between a button face and its board, after the blur

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


def wait(grab: Grab, point: Point, baseline: Optional[np.ndarray], timeout: float,
         keep_going: Callable[[], bool], want_change: bool = True, minimum: float = 0.0,
         poll: float = 0.012, what: str = "dialog", tag: Optional[str] = None, owner=None) -> bool:
    """Polls the patch at ``point`` until it differs from ``baseline``
    (``want_change``: the dialog came up) or matches it again (the dialog
    went away). "Up" needs the changed patch to hold still for
    ``STABLE_READS`` reads and, once ``confirm`` has taught the dialog's
    look at this point, to resemble it. Waits at least ``minimum`` seconds
    in any case. Returns
    whether that was seen within ``timeout``; False also when the patch
    cannot be read or ``keep_going()`` turns False."""
    start = time.perf_counter()
    seen = False
    if baseline is not None:
        deadline = start + timeout
        learned = _learned.get(_key(point, what, owner)) if want_change else None
        if learned is not None and learned.shape != baseline.shape:
            learned = None
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
                    if change <= CHANGED:
                        seen = True
                        break
                else:
                    # Up = changed, drawn still for a few reads (scenery and
                    # effects keep moving) and, once the dialog's look is
                    # known, looking like it.
                    still = prev is not None and difference(patch, prev) < STABLE
                    prev = patch
                    looks_right = learned is None or difference(patch, learned) < SAME_DIALOG
                    steady = steady + 1 if (change > CHANGED and still and looks_right) else 0
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

"""dialog_watch replayed on a real client: the game's yes/no dialogs
("open the crate?", "place the piece?", "drop this item?") as they opened and
closed in a recording, frame by frame at 60 fps
(tests/fixtures/real_client_rescaled/dialog_patches.npz).

A virtual clock drives dialog_watch: each poll sees the frame that was on
screen at that moment, so the replay keeps the real timing and runs fast."""

import json
import os
import unittest

import cv2
import numpy as np

from _stubs import install, ROOT

install()

import dialog_watch  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "real_client_rescaled")
with open(os.path.join(FIXTURES, "labels.json")) as _f:
    META = json.load(_f)["dialog_replay"]
PATCHES = np.load(os.path.join(FIXTURES, "dialog_patches.npz"))
FPS = META["fps"]
POINT = (100, 100)        # where the patch is "on screen" (any point: the grab serves the recording)


class VirtualTime:
    """Stands in for the time module inside dialog_watch."""

    def __init__(self, start=0.0):
        self.now = start

    def perf_counter(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.0)


class Replay:
    def __init__(self, frames, clock, start_frame=0):
        self.frames, self.clock, self.offset = frames, clock, start_frame / FPS

    def frame_index(self):
        return min(len(self.frames) - 1, int((self.clock.now + self.offset) * FPS))

    def grab(self, x, y, w, h):
        patch = self.frames[self.frame_index()]
        assert (w, h) == (patch.shape[1], patch.shape[0])
        return cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)


LEAD = 9      # the bot reads the "before" patch right before its click; the dialog follows
              # within ~0.1-0.2 s. 9 frames = 150 ms.


def quiet_background(frames):
    """A stretch where only the world moves behind the point (no tooltip or
    other panel passes over it)."""
    return np.abs(frames.astype(int) - frames[0]).mean(axis=(1, 2)).max() < 20


class DialogReplayTests(unittest.TestCase):
    def setUp(self):
        self._time = dialog_watch.time
        self.clock = dialog_watch.time = VirtualTime()
        dialog_watch.forget()

    def tearDown(self):
        dialog_watch.time = self._time
        dialog_watch.forget()

    def sequences(self, kind):
        for i, seq in enumerate(META["sequences"]):
            if seq["kind"] == kind:
                yield seq, PATCHES[f"seq{i}"]

    def test_the_dialog_is_seen_when_it_opens_and_not_before(self):
        for seq, frames in self.sequences("dialog"):
            self.clock.now = (seq["up_from"] - LEAD) / FPS
            replay = Replay(frames, self.clock)
            baseline = dialog_watch.read_patch(replay.grab, POINT)        # as the bot reads it before its click
            # the bot's waits: the configured 0.6 s, then up to 1.8 s more
            seen = dialog_watch.wait(replay.grab, POINT, baseline, 0.6, lambda: True)
            if not seen:
                seen = dialog_watch.wait(replay.grab, POINT, baseline, 1.8, lambda: True)
            self.assertTrue(seen, seq)
            at = replay.frame_index()
            self.assertGreaterEqual(at, seq["up_from"], f"'up' before the dialog opened: {seq}")
            self.assertLessEqual(at, seq["up_from"] + 6, f"dialog seen {at - seq['up_from']} frames late: {seq}")
            self.assertLessEqual(at, seq["up_to"], seq)

    def test_the_dialog_is_seen_going_away(self):
        # Never while it is still up. Late is possible when something else
        # covers the point right after (in one sequence the player's cursor
        # and the next dialog): rare, and harmless.
        on_time = total = 0
        for seq, frames in self.sequences("dialog"):
            total += 1
            self.clock.now = seq["up_from"] / FPS                      # the dialog is up now
            replay = Replay(frames, self.clock)
            baseline = frames[seq["up_from"] - LEAD]                   # before it came
            gone = dialog_watch.wait(replay.grab, POINT, baseline, 2.0, lambda: True, want_change=False)
            at = replay.frame_index()
            if gone:
                self.assertGreater(at, seq["up_to"], f"'gone' while the dialog was still up: {seq}")
            on_time += gone and at <= seq["up_to"] + 6
        self.assertGreaterEqual(on_time, total - 2)

    def test_gone_is_seen_when_the_world_differs_from_the_old_look(self):
        # Diag 20261121_200639: after the Confirm click the patch read 15-19
        # away from the pre-dialog look for good (the character and rod had
        # moved), never "like the old look": a drop that worked was reported
        # as "dialog still open". Once the dialog's look is known, a patch
        # that no longer resembles it counts as gone.
        rng = np.random.default_rng(3)
        baseline = rng.integers(60, 120, (16, 40)).astype(np.uint8)
        dialog = np.clip(baseline.astype(int) + 45 * rng.choice([-1, 1], baseline.shape), 0, 255).astype(np.uint8)
        world = np.clip(baseline.astype(int) + 17 * rng.choice([-1, 1], baseline.shape), 0, 255).astype(np.uint8)
        self.assertGreater(dialog_watch.difference(world, baseline), dialog_watch.CHANGED)

        def grab_of(patch):
            return lambda x, y, w, h: cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)

        def gone():
            return dialog_watch.wait(grab_of(world), POINT, baseline, 0.3, lambda: True, want_change=False,
                                     what="confirm dialog")

        self.assertFalse(gone(), "nothing known about the dialog yet: only the old look counts")
        dialog_watch._last_up[dialog_watch._key(POINT, "confirm dialog")] = dialog
        dialog_watch.confirm(POINT, "confirm dialog")
        self.assertTrue(gone())
        # ... and still not gone while the dialog itself shows
        self.assertFalse(dialog_watch.wait(grab_of(dialog), POINT, baseline, 0.3, lambda: True,
                                           want_change=False, what="confirm dialog"))

    def test_no_dialog_is_seen_where_there_is_none(self):
        # The world behind the point keeps moving (the patch drifts by 7-12):
        # that alone must not read as a dialog.
        quiet = 0
        for seq, frames in self.sequences("quiet"):
            if not quiet_background(frames):
                continue
            quiet += 1
            self.clock.now = 0.0
            replay = Replay(frames, self.clock)
            baseline = dialog_watch.read_patch(replay.grab, POINT)
            self.assertFalse(dialog_watch.wait(replay.grab, POINT, baseline, 0.9, lambda: True), seq)
        self.assertGreaterEqual(quiet, 6)

    def test_the_old_threshold_was_fooled_by_the_moving_world(self):
        # Documents why CHANGED is 15: at 6 the world moving behind the
        # point alone counted as a dialog.
        old, dialog_watch.CHANGED = dialog_watch.CHANGED, 6.0
        try:
            fooled = 0
            for seq, frames in self.sequences("quiet"):
                if quiet_background(frames):
                    self.clock.now = 0.0
                    replay = Replay(frames, self.clock)
                    fooled += dialog_watch.wait(replay.grab, POINT, dialog_watch.read_patch(replay.grab, POINT),
                                                0.9, lambda: True)
            self.assertGreater(fooled, 0)
        finally:
            dialog_watch.CHANGED = old

    def test_a_learned_dialog_look_is_still_recognized(self):
        # After a confirmed sequence the bot knows the dialog's look at the
        # point; later dialogs (of all three kinds) must still match it.
        learned = False
        for seq, frames in self.sequences("dialog"):
            self.clock.now = (seq["up_from"] - LEAD) / FPS
            replay = Replay(frames, self.clock)
            baseline = dialog_watch.read_patch(replay.grab, POINT)
            seen = dialog_watch.wait(replay.grab, POINT, baseline, 2.4, lambda: True)
            self.assertTrue(seen, (seq, "learned" if learned else "first"))
            self.assertGreaterEqual(replay.frame_index(), seq["up_from"], seq)
            if not learned:
                dialog_watch.confirm(POINT)
                learned = True

    def _grab(self, patch):
        return lambda x, y, w, h: cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)

    def test_a_modest_shift_is_not_a_dialog_and_a_large_one_is(self):
        # 17 is the world after a dialog closed (diag 20261121_200639). 45 is
        # a dialog, including one whose look was learned from a different dialog.
        baseline = np.full((16, 40), 80, np.uint8)
        world = np.full((16, 40), 97, np.uint8)
        dialog = np.full((16, 40), 125, np.uint8)
        self.assertGreater(dialog_watch.difference(world, baseline), dialog_watch.CHANGED)
        self.assertLess(dialog_watch.difference(world, baseline), dialog_watch.DIALOG)
        self.assertFalse(dialog_watch.still_up(self._grab(world), POINT, baseline, what="confirm dialog"))
        self.assertTrue(dialog_watch.still_up(self._grab(dialog), POINT, baseline, what="confirm dialog"))
        dialog_watch._last_up[dialog_watch._key(POINT, "confirm dialog")] = np.full((16, 40), 10, np.uint8)
        dialog_watch.confirm(POINT, "confirm dialog")
        self.assertFalse(dialog_watch.still_up(self._grab(world), POINT, baseline, what="confirm dialog"))
        self.assertTrue(dialog_watch.still_up(self._grab(dialog), POINT, baseline, what="confirm dialog"))

    def test_strong_only_ignores_the_world_and_sees_every_real_dialog(self):
        # Diag 20261122_093507: the world behind the jigsaw's Yes moved the
        # patch by 15.08 and held still: "dialog up", Yes clicked on the world.
        baseline = np.full((16, 40), 80, np.uint8)
        world = np.full((16, 40), 96, np.uint8)
        self.assertGreater(dialog_watch.difference(world, baseline), dialog_watch.CHANGED)
        self.assertTrue(dialog_watch.wait(self._grab(world), POINT, baseline, 0.3, lambda: True))
        self.assertFalse(dialog_watch.wait(self._grab(world), POINT, baseline, 0.3, lambda: True,
                                           strong_only=True))
        # Every recorded real dialog still counts, on time
        for seq, frames in self.sequences("dialog"):
            self.clock.now = (seq["up_from"] - LEAD) / FPS
            replay = Replay(frames, self.clock)
            baseline = dialog_watch.read_patch(replay.grab, POINT)
            self.assertTrue(dialog_watch.wait(replay.grab, POINT, baseline, 2.4, lambda: True,
                                              strong_only=True), seq)
            self.assertGreaterEqual(replay.frame_index(), seq["up_from"], seq)
            self.assertLessEqual(replay.frame_index(), seq["up_from"] + 8, seq)

    def test_a_dialog_sized_change_counts_when_the_learned_look_does_not(self):
        baseline = np.full((16, 40), 80, np.uint8)
        dialog = np.full((16, 40), 125, np.uint8)
        dialog_watch._last_up[dialog_watch._key(POINT, "confirm dialog")] = np.full((16, 40), 10, np.uint8)
        dialog_watch.confirm(POINT, "confirm dialog")
        self.assertFalse(dialog_watch.wait(self._grab(dialog), POINT, baseline, 0.3, lambda: True,
                                           what="confirm dialog"))
        self.assertTrue(dialog_watch.wait(self._grab(dialog), POINT, baseline, 0.3, lambda: True,
                                          what="confirm dialog", accept_strong=True))

    def test_the_larger_dialog_wins_when_both_points_change(self):
        # Yes / no covers its own button by ~45 and nudges the Drop / Sell
        # point by ~22. The yes / no is the dialog to click.
        baseline = np.full((16, 40), 80, np.uint8)
        drop_pt, confirm_pt = (10, 10), (90, 90)
        patches = {drop_pt: np.full((16, 40), 102, np.uint8), confirm_pt: np.full((16, 40), 125, np.uint8)}

        def grab(x, y, w, h):
            return cv2.cvtColor(patches[(x + w // 2, y + h // 2)], cv2.COLOR_GRAY2BGR)

        choices = [
            ("drop", drop_pt, baseline, "drop dialog", False),
            ("confirm", confirm_pt, baseline, "confirm dialog", True),
        ]
        self.assertEqual(dialog_watch.wait_first(grab, choices, 0.5, lambda: True), "confirm")
        self.assertGreater(dialog_watch.difference(patches[drop_pt], baseline), dialog_watch.CHANGED)


if __name__ == "__main__":
    unittest.main()

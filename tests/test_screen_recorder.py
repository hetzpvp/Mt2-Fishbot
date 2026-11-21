"""'Record game windows': the video of each game window in the diagnostic log."""

import csv
import os
import shutil
import tempfile
import time
import unittest
import zipfile

import cv2
import numpy as np

from _stubs import install

install()

import diag  # noqa: E402
import screen_capture  # noqa: E402
import screen_recorder  # noqa: E402
from test_diagnostics import FrozenExe  # noqa: E402  (session folder in a temp dir)


class FakeWM:
    def __init__(self, size=(320, 240)):
        self.size = size
        self.diag_tag = "W1"

    def get_window_rect(self):
        return (0, 0) + tuple(self.size)

    def is_foreground(self):
        return True


class FakeCapture:
    """Stands in for WindowCapture; ``covered`` makes grabs fail."""
    covered = False

    def __init__(self, window_manager, mode="auto"):
        self.wm = window_manager
        self.needs_verification = False
        self.diag_tag = None
        self.diag_log_grabs = True
        self.n = 0

    noisy = False

    def grab(self, left, top, width, height):
        if FakeCapture.covered:
            return None
        self.n += 1
        if FakeCapture.noisy:                          # big frames: the file grows at once
            return np.random.default_rng(self.n).integers(0, 255, (height, width, 3), dtype=np.uint8)
        frame = np.zeros((height, width, 3), np.uint8)
        cv2.putText(frame, str(self.n), (10, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        return frame


class ScreenRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fishbot_rec_")
        self._orig = screen_capture.WindowCapture
        screen_capture.WindowCapture = FakeCapture
        FakeCapture.covered = False
        FakeCapture.noisy = False

    def tearDown(self):
        screen_capture.WindowCapture = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_records_segments_with_frame_times_and_leaves_gaps_out(self):
        wm = FakeWM()
        rec = screen_recorder.WindowRecorder("W1", wm, self.tmp, fps=20)
        rec.start()
        time.sleep(0.4)
        FakeCapture.covered = True                       # another program in front
        time.sleep(0.3)
        FakeCapture.covered = False
        time.sleep(0.3)
        wm.size = (400, 300)                             # window resized: new file
        time.sleep(0.4)
        rec.stop()
        rec.join(5)
        self.assertFalse(rec.is_alive())
        self.assertEqual(len(rec.segments), 2)
        self.assertGreater(rec.missed, 0)
        total = 0
        for seg, size in zip(rec.segments, ((320, 240), (400, 300))):
            rows = self._rows(seg[:-5] + ".csv")
            self.assertEqual(int(rows[-1]["offset"]) + int(rows[-1]["bytes"]), os.path.getsize(seg))
            for row in rows:                             # every frame reads back whole
                frame = screen_recorder.read_frame(seg, row["offset"], row["bytes"])
                self.assertEqual(frame.shape[1::-1], size)
            self.assertRegex(rows[0]["time"], r"^\d\d:\d\d:\d\d\.\d{3}$")   # session.log clock
            total += len(rows)
        self.assertEqual(total, rec.frames)
        first = self._rows(rec.segments[0][:-5] + ".csv")
        self.assertTrue(any(r["gap_before"] for r in first[1:]), "the covered moment shows as a gap")

    def test_size_limit_stops_the_recording(self):
        FakeCapture.noisy = True
        rec = screen_recorder.WindowRecorder("W2", FakeWM((640, 480)), self.tmp, fps=50, max_bytes=1)
        rec.start()
        rec.join(3)
        self.assertFalse(rec.is_alive())
        self.assertEqual(rec.frames, 0)

    def test_session_recorder_stops_all_and_videos_are_stored_in_the_zip(self):
        with FrozenExe():
            self._session_zip()

    def _session_zip(self):
        diag.configure(True)
        try:
            folder = diag.start_session(reason="recording test")
            session = screen_recorder.SessionRecorder()
            session.start({"W1": FakeWM(), "W2": FakeWM((200, 150))}, folder)
            time.sleep(0.3)
            threads = session.stop()
            self.assertEqual(session.recorders, [])
            t = diag.end_session("test", wait_threads=threads, wait=True)
            self.assertIsNotNone(t)
            zip_path = folder + ".zip"
            with zipfile.ZipFile(zip_path) as zf:
                videos = [i for i in zf.infolist() if i.filename.endswith(".mjpg")]
                self.assertEqual(len(videos), 2)
                self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in videos))
                self.assertTrue(any(i.filename.endswith("screen_W2_001.csv") for i in zf.infolist()))
        finally:
            diag.configure(False)


if __name__ == "__main__":
    unittest.main()

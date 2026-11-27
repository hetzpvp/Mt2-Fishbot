"""Catch outcome against timing, for the diagnostics.

Servers decide whether a won game (or a classic reel) gives an item. On the
stock server code that chance depends on *when* the result reaches the
server: the classic reel's time after the bite, the minigame's length. Other
servers change those tables or the rule itself, so the bot does not assume
any of it. It records, per game, the timing it controlled and whether an
item landed, and the diagnostics show the catch rate per timing bucket. That
is what any later tuning has to be based on.

The item count is a lower bound: an item that lands on an existing stack
takes no new slot and may go unseen. That error is the same for every
bucket, so the buckets stay comparable.
"""

import threading
from typing import Dict, List, Optional, Tuple

# Bucket width in seconds per kind of timing
BUCKET = {"classic": 0.1, "minigame": 0.5}


class CatchTimingStats:
    """Thread-safe tally of (kind, seconds) -> [games, items]."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tally: Dict[Tuple[str, float], List[int]] = {}

    @staticmethod
    def bucket(kind: str, seconds: float) -> float:
        width = BUCKET.get(kind, 0.5)
        return round(int(seconds / width + 1e-9) * width, 3)

    def record(self, kind: str, seconds: float, item: bool) -> None:
        key = (kind, self.bucket(kind, seconds))
        with self._lock:
            entry = self._tally.setdefault(key, [0, 0])
            entry[0] += 1
            if item:
                entry[1] += 1

    def rows(self) -> List[Tuple[str, float, int, int]]:
        """(kind, bucket start, games, items), sorted."""
        with self._lock:
            return sorted((k, b, v[0], v[1]) for (k, b), v in self._tally.items())

    def summary(self) -> Optional[str]:
        rows = self.rows()
        if not rows:
            return None
        lines = []
        for kind, start, games, items in rows:
            width = BUCKET.get(kind, 0.5)
            lines.append(f"{kind} {start:.2f}-{start + width:.2f}s: {items}/{games} with an item "
                         f"({100.0 * items / games:.0f}%)")
        return "; ".join(lines)

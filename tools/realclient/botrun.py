"""
Runs the bots against real clients without the GUI, the way the GUI starts
them (session_setup), and records what they did.

    run = BotRun(run_dir)
    run.add_fishing(client, slot=0, overrides={...})
    run.add_jigsaw(client, slot=1, overrides={...})
    results = run.run(until=..., timeout=120)

Each bot runs in its own thread like in the GUI, all sharing the process-wide
input lock. Input goes through XTest on the hidden display: uinput is blocked
in this process (it would reach the user's real desktop). The diagnostic log
of the run is written into ``run_dir`` (never the repo).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from display import assert_hidden  # noqa: E402

# Click points of the stock 800x600 client UI (window-relative), the same on
# both instances: inventory page tabs I / II.
POSITIONS_800x600 = {
    "inv_page_1_pos": [673, 231],
    "inv_page_2_pos": [748, 231],
}


def _block_uinput() -> None:
    """uinput devices are read by the real desktop: never create one here."""
    import input_backend

    def refuse(*_a, **_k):
        raise RuntimeError("uinput is disabled in the real-client tests (it would reach the desktop)")
    input_backend._get_uinput_mouse = refuse
    input_backend._get_uinput_keyboard = refuse


@dataclass
class BotResult:
    slot: int
    account: str
    kind: str
    stop_reason: Optional[str] = None
    stop_message: Optional[str] = None
    statuses: List[tuple] = field(default_factory=list)     # (time, text)
    games: int = 0
    bait: int = 0
    crates: int = 0
    boards: int = 0
    alive_at_end: bool = False
    paused: bool = False               # paused (waiting for the player) when the run ended
    bot: object = None

    def said(self, text: str) -> bool:
        return any(text.lower() in s.lower() for _, s in self.statuses)

    def log(self) -> str:
        return "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(t))} {s}" for t, s in self.statuses)


class BotRun:
    def __init__(self, run_dir: str, label: str = "run"):
        assert_hidden()
        _block_uinput()
        self.run_dir = run_dir
        self.label = label
        os.makedirs(run_dir, exist_ok=True)
        self.specs = []
        self.results: Dict[int, BotResult] = {}
        self._threads: Dict[int, threading.Thread] = {}
        self.thread_errors: List[str] = []      # uncaught exceptions in any thread during the run
        self.logged_errors: List[str] = []      # exceptions the bots caught and logged (diag.exc)

    # ------------------------------------------------------------ config
    @staticmethod
    def base_config(overrides: Optional[dict] = None) -> dict:
        import session_setup
        cfg = session_setup.default_config("realclient-test")
        cfg.update(POSITIONS_800x600)
        cfg.update(input_backend="xtest", diagnostics_enabled=True, sound_alert_on_finish=False,
                   sound_on_pause=False)
        cfg.update(overrides or {})
        return cfg

    def add_fishing(self, client, slot: int, overrides: Optional[dict] = None,
                    bait_keys=("1", "2", "3", "4"), bait: Optional[int] = None) -> None:
        self.specs.append(("fishing", client, slot, overrides or {}, list(bait_keys), bait))

    def add_jigsaw(self, client, slot: int, overrides: Optional[dict] = None) -> None:
        self.specs.append(("jigsaw", client, slot, overrides or {}, None, None))

    # ------------------------------------------------------------ run
    def _make(self, kind, client, slot, overrides, bait_keys, bait):
        import session_setup
        from platforms import x11
        from window_manager import WindowManager
        wm = WindowManager()
        wm.selected_window = x11.X11Window(client.window, "METIN2")
        res = BotResult(slot=slot, account=client.account, kind=kind)
        cfg = self.base_config(overrides)

        def status(msg, r=res):
            r.statuses.append((time.time(), str(msg)))
        if kind == "fishing":
            from fishing_bot import FishingBot
            bcfg = session_setup.fishing_session_config(cfg, bait_keys)
            start_bait = bait if bait is not None else len(bait_keys) * int(cfg.get("bait_quantity", 200))
            bot = FishingBot(None, bcfg.copy(), wm, bait_counter=start_bait, bait_keys=list(bait_keys),
                             bot_id=slot)
            bot.on_status_update = status
            bot.running = True
        else:
            from jigsaw_bot import JigsawBot
            bot = JigsawBot(wm, session_setup.jigsaw_session_config(cfg), bot_id=slot,
                            on_status_update=lambda m, s=status: s(m))
        res.bot = bot
        return bot, res

    def run(self, timeout: float, until: Optional[Callable[[Dict[int, BotResult]], bool]] = None,
            during: Optional[Callable[[float, Dict[int, BotResult]], None]] = None) -> Dict[int, BotResult]:
        """Starts every bot, waits until all stopped by themselves, ``until``
        says so, or ``timeout``; then stops the rest. ``during(elapsed,
        results)`` runs about every 0.25 s (to disturb the clients)."""
        import diag
        from fishing_bot import FishingBot
        from jigsaw_bot import JigsawBot
        diag.program_dir = lambda: self.run_dir      # the log goes next to the run, never the repo
        diag.ENABLED = True
        FishingBot.reset_session_state()
        JigsawBot.reset_learned_settings()
        made = [self._make(*s) for s in self.specs]
        old_hook = threading.excepthook
        old_exc = diag.exc

        def hook(args):
            import traceback
            self.thread_errors.append(f"{getattr(args.thread, 'name', '?')}: " + "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        threading.excepthook = hook

        def logged(msg="exception caught", *, tag=None, depth=0, _old=old_exc):
            et, ev, _tb = sys.exc_info()
            self.logged_errors.append(f"[{tag}] {msg}: {et.__name__ if et else ''}: {ev}")
            return _old(msg, tag=tag, depth=depth + 1)
        diag.exc = logged
        cfg_header = {f"W{r.slot + 1}": [f"{r.kind} on {r.account}"] for _, r in made}
        diag.start_session(cfg_header, self.base_config(), None, reason=self.label)
        for bot, res in made:
            self.results[res.slot] = res
            t = threading.Thread(target=bot.start, daemon=True, name=f"{res.kind}-W{res.slot + 1}")
            self._threads[res.slot] = t
            t.start()
        start = time.time()
        try:
            while time.time() - start < timeout:
                alive = [s for s, t in self._threads.items() if t.is_alive()]
                if not alive:
                    break
                self._collect()
                if until and until(self.results):
                    break
                if during:
                    during(time.time() - start, self.results)
                time.sleep(0.25)
        finally:
            self._collect()          # before the stop: paused / running as the run left them
            for slot, t in self._threads.items():
                res = self.results[slot]
                res.alive_at_end = t.is_alive()
                if t.is_alive():
                    res.bot.stop()
            for t in self._threads.values():
                t.join(timeout=20)
            self._collect()
            diag.end_session(f"{self.label} finished", wait=True)
            threading.excepthook = old_hook
            diag.exc = old_exc
        return self.results

    def _collect(self) -> None:
        for res in self.results.values():
            b = res.bot
            res.stop_reason = getattr(b, "stop_reason", None)
            res.stop_message = getattr(b, "stop_message", None)
            res.games = getattr(b, "total_games", 0)
            res.bait = getattr(b, "bait_counter", 0)
            res.crates = getattr(b, "crates_completed", 0)
            res.boards = getattr(b, "boards_completed", 0)
            if getattr(b, "running", False) or res.alive_at_end:
                res.paused = bool(getattr(b, "paused", False))

    def report(self) -> str:
        lines = [f"thread errors: {len(self.thread_errors)}, logged exceptions: {len(self.logged_errors)}"]
        lines += self.thread_errors + self.logged_errors
        for slot in sorted(self.results):
            r = self.results[slot]
            lines.append(f"--- W{slot + 1} {r.kind} on {r.account}: stop={r.stop_reason} ({r.stop_message}) "
                         f"games={r.games} bait={r.bait} crates={r.crates} boards={r.boards}")
            lines.append(r.log())
        return "\n".join(lines)

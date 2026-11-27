"""
One real-client test session: bring a scenario's clients up on the hidden
display, run the bots, close the clients and read the final DB state
(``run_session`` -> ``Outcome``). Used by tests/realclient.
"""

import json
import os
import time

import pytest

RUNS = os.path.expanduser("~/.local/share/fishbot-realclient/runs")

# Item vnums on the test servers
WORM, PASTE, MINNOW = 27801, 27800, 27802
ZANDER = 27803                      # a fish: one slot, does not stack
NORMAL_CRATE, SPECIAL_CRATE = 25106, 25107
GOLDEN_FISH_CHEST_M = 83002         # jigsaw reward (local server)

# The dialog Yes button of the stock 800x600 client (Open this box? / Place
# the piece here? / Discard...?). Measured on the real client.
CONFIRM_800x600 = [360, 326]

# Spots on the Yongan bridge 6 m apart (x in cm): every character faces the
# water and stands out of the middle of the others' screens. On one spot the
# other characters' name tags and bite bubbles sit where a character's own
# bubble appears.
SPOTS = [485001, 485001 - 600, 485001 + 600, 485001 + 1200]


def spot(i: int) -> dict:
    return {"map_index": 1, "x": SPOTS[i], "y": 962098}


def worms(n: int, keys=(1, 2, 3, 4)) -> list:
    return [{"vnum": WORM, "count": n, "hotkey": k} for k in keys]
_STAMP = os.environ.get("FISHBOT_REALCLIENT_STAMP") or time.strftime("%Y%m%d_%H%M%S")


def run_dir(name: str) -> str:
    d = os.path.join(RUNS, _STAMP, name)
    os.makedirs(d, exist_ok=True)
    return d


class Outcome:
    """What one module's session produced: per account the bot result and
    the character's final state, plus where the files are."""

    def __init__(self, folder, results, final, applied, error=None, thread_errors=(), logged_errors=()):
        self.folder = folder
        self.thread_errors = list(thread_errors)    # uncaught exceptions in any thread
        self.logged_errors = list(logged_errors)    # exceptions the bots caught (diag.exc)
        self.results = results          # {account: BotResult}
        self.final = final              # {account: inspect character}
        self.applied = applied          # {account: what the rig put in}
        self.error = error

    def bot(self, account):
        if self.error:
            pytest.fail(f"session failed: {self.error}\nfiles: {self.folder}")
        return self.results[account]

    def char(self, account):
        if self.error:
            pytest.fail(f"session failed: {self.error}\nfiles: {self.folder}")
        return self.final[account]

    def total(self, account, vnum) -> int:
        return int(self.char(account).get("totals", {}).get(str(vnum), 0))

    def where(self, account) -> str:
        r = self.results.get(account)
        return f"\n--- bot on {account}:\n{r.log() if r else '(no bot)'}\nfiles: {self.folder}"


def run_session(rig_session, name, scenario, bots, timeout, until=None, during=None,
                after_up=None) -> Outcome:
    """Brings the scenario's clients up, runs one bot per entry of ``bots``
    ({account: ("fishing"|"jigsaw", overrides, extra)}) until they stop or
    ``timeout``, then closes the clients and reads the final DB state."""
    from botrun import BotRun
    folder = run_dir(name)
    results, final, applied = {}, {}, {}
    try:
        res = rig_session.up(scenario)
        applied = {a["account"]: a for a in res.get("applied") or []}
        for acc in rig_session.clients:
            rig_session.screenshot(acc, os.path.join(folder, f"start_{acc}.png"))
        if after_up:
            after_up(rig_session)
        run = BotRun(folder, name)
        slots = {}
        for slot, (acc, (kind, overrides, extra)) in enumerate(bots.items()):
            slots[slot] = acc
            if kind == "jigsaw":
                run.add_jigsaw(rig_session.clients[acc], slot, overrides)
            else:
                run.add_fishing(rig_session.clients[acc], slot, overrides, **(extra or {}))
        by_slot = run.run(timeout=timeout,
                          until=(lambda r: until({slots[s]: v for s, v in r.items()})) if until else None,
                          during=(lambda t, r: during(rig_session, t, {slots[s]: v for s, v in r.items()}))
                          if during else None)
        results = {slots[s]: r for s, r in by_slot.items()}
        for acc in list(rig_session.clients):
            rig_session.screenshot(acc, os.path.join(folder, f"end_{acc}.png"))
        with open(os.path.join(folder, "bots.txt"), "w") as f:
            f.write(run.report())
        final = rig_session.finish()
        with open(os.path.join(folder, "final.json"), "w") as f:
            json.dump({"applied": applied, "final": final}, f, indent=1)
        return Outcome(folder, results, final, applied,
                       thread_errors=run.thread_errors, logged_errors=run.logged_errors)
    except Exception as e:
        try:
            rig_session.finish()
        except Exception:
            pass
        return Outcome(folder, results, final, applied, error=f"{type(e).__name__}: {e}")

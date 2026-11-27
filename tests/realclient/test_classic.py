"""Classic fishing on real clients (classic instance: wait for the bite
bubble, reel in). The area map the clients open at login is closed first:
it covers the spot above the character where the bubble shows (see
test_classic_refusals for what happens when it stays open).

- admin   baseline: sees the bites, reels in, every catch took one worm
- admin2  the inventory shows page 2 at start: the bot goes to page 1 first
- admin3  bait on F1-F4 only (Paste / Minnow), keys 1-4 empty
- admin4  Paste instead of Worm on keys 1-4
"""

import pytest

from session import MINNOW, PASTE, WORM, run_session, spot

INSTANCE = "classic"
RUN_SECONDS = 240


def bait(vnum, keys):
    return [{"vnum": vnum, "count": 200, "hotkey": k} for k in keys]


SCENARIO = {
    "restart_on_crash": 0,
    "clients": [
        {"account": "admin", "position": spot(0)},
        {"account": "admin2", "position": spot(1), "ui": {"inventory_page": 2}},
        {"account": "admin3", "position": spot(2), "items": bait(PASTE, ("F1", "F2")) + bait(MINNOW, ("F3", "F4"))},
        {"account": "admin4", "position": spot(3), "items": bait(PASTE, (1, 2, 3, 4))},
    ],
}
_CLASSIC = {"classic_fishing": True}
BOTS = {
    "admin": ("fishing", _CLASSIC, None),
    "admin2": ("fishing", _CLASSIC, None),
    "admin3": ("fishing", _CLASSIC, {"bait_keys": ["F1", "F2", "F3", "F4"]}),
    "admin4": ("fishing", _CLASSIC, None),
}
_maps = {}


def _close_maps(rig):
    for acc in rig.clients:
        _maps[acc] = rig.close_area_map(acc)


@pytest.fixture(scope="module")
def outcome(rigs):
    _maps.clear()
    return run_session(rigs.get(INSTANCE), "classic", SCENARIO, BOTS, timeout=RUN_SECONDS, after_up=_close_maps,
                       until=lambda r: all(b.games >= 6 for b in r.values()))


def _timeouts(bot):
    return sum(1 for _, s in bot.statuses if "Classic fish detection timeout" in s)


def _check_fishing(outcome, account, vnums, start_total):
    bot = outcome.bot(account)
    assert _maps.get(account), f"area map could not be closed on {account}"
    assert bot.stop_reason in (None, "manual"), f"{bot.stop_reason}: {bot.stop_message}" + outcome.where(account)
    assert bot.games >= 2, f"only {bot.games} catches" + outcome.where(account)
    used = start_total - sum(outcome.total(account, v) for v in vnums)
    # a reel-in per bite; the cast still out when the run stopped took one
    # more, and a bite the bot missed (timeout) took one
    assert bot.games <= used <= bot.games + _timeouts(bot) + 1, \
        f"{used} bait for {bot.games} catches, {_timeouts(bot)} timeouts" + outcome.where(account)


def test_baseline_sees_every_bite_and_reels_in(outcome):
    _check_fishing(outcome, "admin", (WORM,), 800)


def test_inventory_on_page_two_at_start(outcome):
    _check_fishing(outcome, "admin2", (WORM,), 800)


def test_bait_on_f_keys(outcome):
    # F1 holds Paste: the first key is used up first
    _check_fishing(outcome, "admin3", (PASTE, MINNOW), 800)
    assert outcome.total("admin3", MINNOW) == 400, "Minnow (F3/F4) used before the Paste ran out"


def test_paste_as_bait(outcome):
    _check_fishing(outcome, "admin4", (PASTE,), 800)


def test_no_errors_in_the_run(outcome):
    assert outcome.error is None, outcome.error
    assert not outcome.thread_errors, "\n".join(outcome.thread_errors) + f"\nfiles: {outcome.folder}"
    assert not outcome.logged_errors, "\n".join(outcome.logged_errors) + f"\nfiles: {outcome.folder}"

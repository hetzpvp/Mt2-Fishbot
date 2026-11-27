"""Things that happen to a client while the bot fishes (fish instance,
minigame). Four clients start fishing normally; after DISTURB_AT seconds:

- admin   the client disappears (killed: a crash). That bot must stop on its
          own with a window/capture reason, without errors, and the others
          must not notice
- admin2  the client freezes for 5 s (SIGSTOP: a hitch, no frames, no
          input). The bot must carry on fishing afterwards
- admin3  the player closes the inventory (game-side, testrig ui). The bot
          must not fish blind: it stops (inventory_not_visible) or gets the
          inventory back, and never loses an item
- admin4  the window is minimized. The bot must not cast into a window it
          cannot see: it brings the window back or stops with
          capture_unavailable; no bait is spent on games it never saw
"""

import threading

import pytest

from session import WORM, run_session, spot

INSTANCE = "fish"
DISTURB_AT = 30.0
RUN_SECONDS = 110

ACCOUNTS = ("admin", "admin2", "admin3", "admin4")
SCENARIO = {"server": {"fish_event": False}, "restart_on_crash": 0,
            "clients": [{"account": a, "position": spot(i)} for i, a in enumerate(ACCOUNTS)]}
BOTS = {a: ("fishing", {}, None) for a in ACCOUNTS}

_marks = {}


def _disturb(rig, t, results):
    if t < DISTURB_AT or "done" in _marks:
        return
    _marks["done"] = True
    _marks["games_before"] = {a: r.games for a, r in results.items()}
    _marks["t"] = t
    rig.kill("admin")
    rig.freeze("admin2", 5.0)
    rig.minimize("admin4")
    # the ui command waits for the client's answer: not on the bot loop's time
    threading.Thread(target=lambda: _marks.setdefault("ui", rig.ui(accounts=["admin3"], inventory="closed")),
                     daemon=True).start()


@pytest.fixture(scope="module")
def outcome(rigs):
    _marks.clear()
    return run_session(rigs.get(INSTANCE), "fish_disturbed", SCENARIO, BOTS, timeout=RUN_SECONDS,
                       during=_disturb)


def _stopped_after(bot, t0):
    """Seconds from the disturbance to the bot's last status line."""
    return bot.statuses[-1][0] - t0 if bot.statuses else None


def test_disturbances_happened(outcome):
    assert outcome.error is None, outcome.error
    assert "done" in _marks, "the run ended before the disturbances"
    assert all(n >= 1 for n in _marks["games_before"].values()), \
        f"not every bot fished before the disturbance: {_marks['games_before']}\nfiles: {outcome.folder}"


def test_crashed_client_stops_its_bot(outcome):
    bot = outcome.bot("admin")
    assert bot.stop_reason in ("window_unavailable", "capture_unavailable", "game_screen_lost"), \
        f"{bot.stop_reason}: {bot.stop_message}" + outcome.where("admin")
    assert not bot.alive_at_end, outcome.where("admin")


def test_frozen_client_fishes_on(outcome):
    bot = outcome.bot("admin2")
    assert bot.stop_reason in (None, "manual"), f"{bot.stop_reason}: {bot.stop_message}" + outcome.where("admin2")
    assert bot.games > _marks["games_before"]["admin2"] + 2, \
        f"{bot.games} games, {_marks['games_before']['admin2']} before the freeze" + outcome.where("admin2")


def test_closed_inventory_never_loses_an_item(outcome):
    bot = outcome.bot("admin3")
    ui = _marks.get("ui") or {}
    assert ui.get("ok"), f"inventory was not closed: {ui}"
    if bot.stop_reason not in (None, "manual"):
        assert bot.stop_reason == "inventory_not_visible", f"{bot.stop_reason}: {bot.stop_message}" \
                                                            + outcome.where("admin3")
    used = 800 - outcome.total("admin3", WORM)
    misses = sum(1 for _, s in bot.statuses if "Minigame not detected" in s)
    assert bot.games <= used <= bot.games + misses + 1, f"{used} worms for {bot.games} games" + outcome.where("admin3")


def test_minimized_window_is_never_fished_blind(outcome):
    bot = outcome.bot("admin4")
    assert bot.stop_reason in (None, "manual", "capture_unavailable"), \
        f"{bot.stop_reason}: {bot.stop_message}" + outcome.where("admin4")
    used = 800 - outcome.total("admin4", WORM)
    misses = sum(1 for _, s in bot.statuses if "Minigame not detected" in s)
    assert used <= bot.games + misses + 1, f"{used} worms for {bot.games} games" + outcome.where("admin4")


def test_no_errors_in_the_run(outcome):
    assert not outcome.thread_errors, "\n".join(outcome.thread_errors) + f"\nfiles: {outcome.folder}"
    assert not outcome.logged_errors, "\n".join(outcome.logged_errors) + f"\nfiles: {outcome.folder}"

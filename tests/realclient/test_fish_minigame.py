"""Fishing minigame on real clients (fish instance, jigsaw event off).

One session, four clients, four cases at once (also a real 4-window run):

- admin   baseline: plays game after game; every game used one worm, the
          bot's bait count follows the game's
- admin2  only 2 worms although the bot is told 200: plays them, notices the
          key is empty and stops with "bait depleted", none left
- admin3  inventory closed at start: stops at once, nothing used (negative)
- admin4  inventory full at start: stops at once, nothing used (negative)
"""

import pytest

from session import WORM, ZANDER, run_session, spot, worms

INSTANCE = "fish"
SCENARIO = {
    "server": {"fish_event": False},
    "restart_on_crash": 0,
    "clients": [
        {"account": "admin", "position": spot(0)},
        {"account": "admin2", "position": spot(1), "items": [{"vnum": WORM, "count": 2, "hotkey": 1}]},
        {"account": "admin3", "position": spot(2), "ui": {"open_inventory": False}},
        {"account": "admin4", "position": spot(3), "items": worms(200) + [{"vnum": ZANDER, "count": 86}]},
    ],
}
BOTS = {
    "admin": ("fishing", {}, None),
    "admin2": ("fishing", {}, {"bait_keys": ["1"], "bait": 200}),
    "admin3": ("fishing", {}, None),
    "admin4": ("fishing", {}, None),
}
RUN_SECONDS = 170


def _done(results):
    # the baseline keeps fishing; the run ends when the bait case is over
    return results["admin2"].stop_reason is not None and all(
        results[a].stop_reason is not None for a in ("admin3", "admin4"))


@pytest.fixture(scope="module")
def outcome(rigs):
    session = rigs.get(INSTANCE)
    return run_session(session, "fish_minigame", SCENARIO, BOTS, timeout=RUN_SECONDS,
                       until=lambda r: _done(r) and r["admin"].games >= 12)


def _misses(bot):
    return sum(1 for _, s in bot.statuses if "Minigame not detected" in s)


def test_baseline_plays_game_after_game(outcome):
    bot = outcome.bot("admin")
    assert bot.stop_reason in (None, "manual"), f"stopped by itself: {bot.stop_reason} {bot.stop_message}" \
                                                + outcome.where("admin")
    assert bot.games >= 8, f"only {bot.games} games" + outcome.where("admin")


def test_baseline_bait_count_follows_the_game(outcome):
    bot = outcome.bot("admin")
    used = 800 - outcome.total("admin", WORM)
    # every game took one worm; a cast that opened no minigame may have
    # taken one too (the game consumes the bait before the bot can tell)
    assert bot.games <= used <= bot.games + _misses(bot) + 1, \
        f"game used {used} worms for {bot.games} games, {_misses(bot)} misses" + outcome.where("admin")
    assert bot.bait == 800 - bot.games, f"bot counter {bot.bait}" + outcome.where("admin")


def test_empty_bait_key_ends_with_bait_depleted(outcome):
    bot = outcome.bot("admin2")
    assert bot.stop_reason == "bait_depleted", f"{bot.stop_reason}: {bot.stop_message}" + outcome.where("admin2")
    assert outcome.total("admin2", WORM) == 0, outcome.where("admin2")
    assert bot.games >= 1, outcome.where("admin2")


def test_closed_inventory_stops_at_once_without_casting(outcome):
    bot = outcome.bot("admin3")
    assert bot.stop_reason == "inventory_not_visible", f"{bot.stop_reason}" + outcome.where("admin3")
    assert bot.games == 0
    assert outcome.total("admin3", WORM) == 800, "bait was used" + outcome.where("admin3")
    first, last = bot.statuses[0][0], bot.statuses[-1][0]
    assert last - first < 20, f"took {last - first:.0f}s to stop" + outcome.where("admin3")


def test_full_inventory_stops_at_once_without_casting(outcome):
    bot = outcome.bot("admin4")
    assert bot.stop_reason == "inventory_full", f"{bot.stop_reason}" + outcome.where("admin4")
    assert bot.games == 0
    assert outcome.total("admin4", WORM) == 800, "bait was used" + outcome.where("admin4")
    assert outcome.total("admin4", ZANDER) == 86, "an item was lost" + outcome.where("admin4")


def test_no_errors_in_the_run(outcome):
    assert outcome.error is None, outcome.error
    assert not outcome.thread_errors, "\n".join(outcome.thread_errors) + f"\nfiles: {outcome.folder}"
    assert not outcome.logged_errors, "\n".join(outcome.logged_errors) + f"\nfiles: {outcome.folder}"

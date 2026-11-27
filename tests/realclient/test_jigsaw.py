"""Fishing jigsaw on real clients (fish instance, event on). The puzzle
window is somewhere else on each client; the bot finds it by itself.

- admin   empty board, 40 normal + 3 special crates, "normal crates only":
          plays every normal crate, finishes boards, collects a reward per
          board, stops when the normal crates are gone, special ones kept
- admin2  "special crates first", 15 normal + 3 special: the special ones go
          first, then the normal ones
- admin3  only 5 crates: plays them and stops cleanly ("no crates left")
- admin4  half-filled board and a piece already on the cursor at login (the
          server re-sends a piece kept through a relog): that piece is played
          before any crate is opened
"""

import pytest

from session import (CONFIRM_800x600, NORMAL_CRATE, SPECIAL_CRATE, run_session, spot, worms)

INSTANCE = "fish"
REWARDS = (83001, 83002, 83003)       # Golden Fish Chest S / M / L on the local server
RUN_SECONDS = 260

_EMPTY = {"board": "reset", "pieces": [], "held": 0, "use_count": 0}
SCENARIO = {
    "server": {"fish_event": True},
    "restart_on_crash": 0,
    "defaults": {"jigsaw": _EMPTY},
    "clients": [
        {"account": "admin", "position": spot(0), "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 10]},
         "items": worms(200) + [{"vnum": NORMAL_CRATE, "count": 40}, {"vnum": SPECIAL_CRATE, "count": 3}]},
        {"account": "admin2", "position": spot(1), "ui": {"jigsaw": "puzzle", "jigsaw_position": [300, 10]},
         "items": worms(200) + [{"vnum": NORMAL_CRATE, "count": 15}, {"vnum": SPECIAL_CRATE, "count": 3}]},
        {"account": "admin3", "position": spot(2), "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 330]},
         "items": worms(200) + [{"vnum": NORMAL_CRATE, "count": 5}]},
        {"account": "admin4", "position": spot(3), "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 10]},
         "items": worms(200) + [{"vnum": NORMAL_CRATE, "count": 30}],
         "jigsaw": {"board": "reset", "held": 4, "use_count": 0,
                    "pieces": [{"shape": 1, "cell": 0}, {"shape": 5, "cell": 2}, {"shape": 2, "cell": 23}]}},
    ],
}
_CFG = {"confirm_button_pos": CONFIRM_800x600}
BOTS = {
    "admin": ("jigsaw", dict(_CFG, jigsaw_crate_priority="normal_first"), None),
    "admin2": ("jigsaw", dict(_CFG, jigsaw_crate_priority="special_first"), None),
    "admin3": ("jigsaw", dict(_CFG), None),
    "admin4": ("jigsaw", dict(_CFG), None),
}


@pytest.fixture(scope="module")
def outcome(rigs):
    return run_session(rigs.get(INSTANCE), "jigsaw", SCENARIO, BOTS, timeout=RUN_SECONDS)


def _rewards(outcome, account):
    return sum(outcome.total(account, v) for v in REWARDS)


def _check_finished_cleanly(outcome, account):
    bot = outcome.bot(account)
    assert not bot.paused, "paused:\n" + outcome.where(account)
    assert not bot.alive_at_end, "still running at the end (out of time?)" + outcome.where(account)
    assert bot.said("No jigsaw crates left") or bot.said("No normal jigsaw crates left"), outcome.where(account)


def test_normal_only_plays_every_normal_crate_and_keeps_the_special_ones(outcome):
    bot = outcome.bot("admin")
    _check_finished_cleanly(outcome, "admin")
    assert outcome.total("admin", NORMAL_CRATE) == 0, outcome.where("admin")
    assert outcome.total("admin", SPECIAL_CRATE) == 3, "a special crate was opened" + outcome.where("admin")
    assert bot.crates == 40, f"bot counted {bot.crates} crates for the 40 the game took" + outcome.where("admin")
    assert bot.boards >= 1, outcome.where("admin")
    assert _rewards(outcome, "admin") == bot.boards, \
        f"{_rewards(outcome, 'admin')} reward chests for {bot.boards} boards" + outcome.where("admin")


def test_special_first_opens_the_special_crates_first(outcome):
    bot = outcome.bot("admin2")
    _check_finished_cleanly(outcome, "admin2")
    assert outcome.total("admin2", SPECIAL_CRATE) == 0, outcome.where("admin2")
    assert outcome.total("admin2", NORMAL_CRATE) == 0, outcome.where("admin2")
    assert bot.crates == 18, f"bot counted {bot.crates} of 18 crates" + outcome.where("admin2")
    opened = [s for _, s in bot.statuses if s.startswith("Open ") and "jigsaw crate" in s]
    assert opened and opened[0].startswith("Open special"), f"first crates: {opened[:4]}" + outcome.where("admin2")
    assert _rewards(outcome, "admin2") == bot.boards, outcome.where("admin2")


def test_few_crates_end_with_a_clean_stop(outcome):
    bot = outcome.bot("admin3")
    _check_finished_cleanly(outcome, "admin3")
    assert outcome.total("admin3", NORMAL_CRATE) == 0, outcome.where("admin3")
    assert bot.crates == 5, outcome.where("admin3")
    assert outcome.char("admin3")["jigsaw"]["held"] == 0, "a piece was left on the cursor" + outcome.where("admin3")


def test_piece_held_at_login_is_played_before_any_crate(outcome):
    bot = outcome.bot("admin4")
    assert bot.said("playing it before opening a crate"), outcome.where("admin4")
    jig = outcome.char("admin4")["jigsaw"]
    assert jig["held"] == 0, f"piece still held: {jig}" + outcome.where("admin4")
    used = 30 - outcome.total("admin4", NORMAL_CRATE)
    assert bot.crates in (used, used + 1), f"{bot.crates} crates counted (the held piece is one), " \
                                           f"{used} taken" + outcome.where("admin4")
    assert not bot.paused, outcome.where("admin4")


def test_no_errors_in_the_run(outcome):
    assert outcome.error is None, outcome.error
    assert not outcome.thread_errors, "\n".join(outcome.thread_errors) + f"\nfiles: {outcome.folder}"
    assert not outcome.logged_errors, "\n".join(outcome.logged_errors) + f"\nfiles: {outcome.folder}"

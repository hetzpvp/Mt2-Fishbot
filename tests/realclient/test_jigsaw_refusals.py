"""Jigsaw setups the bot must refuse or wait out, without spending a crate
(fish instance, event on). Negative tests: none of these can work, and the
right result is a clear stop or pause with nothing lost.

- admin   puzzle window in the middle of the screen: the game's dialogs open
          over the grid, so the Confirm point lies on it -> not started
- admin2  only the puzzle's intro page is open -> paused, "not found"
- admin3  the puzzle window is closed -> paused, "not found"
- admin4  wrong Confirm point (not on the dialog's Yes): the "Open this
          box?" dialog is never confirmed -> paused, no crate spent, no piece
          on the cursor
"""

import pytest

from session import NORMAL_CRATE, SPECIAL_CRATE, run_session, spot, worms

INSTANCE = "fish"
RUN_SECONDS = 60

SCENARIO = {
    "server": {"fish_event": True},
    "restart_on_crash": 0,
    "defaults": {"items": worms(200) + [{"vnum": NORMAL_CRATE, "count": 40}, {"vnum": SPECIAL_CRATE, "count": 3}],
                 "jigsaw": {"board": "reset", "pieces": [], "held": 0, "use_count": 0}},
    "clients": [
        {"account": "admin", "position": spot(0), "ui": {"jigsaw": "puzzle"}},          # centered
        {"account": "admin2", "position": spot(1), "ui": {"jigsaw": "intro"}},
        {"account": "admin3", "position": spot(2), "ui": {"jigsaw": "closed"}},
        {"account": "admin4", "position": spot(3), "ui": {"jigsaw": "puzzle", "jigsaw_position": [10, 10]}},
    ],
}
GOOD_CONFIRM = {"confirm_button_pos": [360, 326]}
BOTS = {
    "admin": ("jigsaw", GOOD_CONFIRM, None),
    "admin2": ("jigsaw", GOOD_CONFIRM, None),
    "admin3": ("jigsaw", GOOD_CONFIRM, None),
    "admin4": ("jigsaw", {"confirm_button_pos": [404, 300]}, None),   # on the dialog's text, not on Yes
}


@pytest.fixture(scope="module")
def outcome(rigs):
    return run_session(rigs.get(INSTANCE), "jigsaw_refusals", SCENARIO, BOTS, timeout=RUN_SECONDS)


def _nothing_spent(outcome, account):
    assert outcome.total(account, NORMAL_CRATE) == 40, "a crate was spent" + outcome.where(account)
    assert outcome.total(account, SPECIAL_CRATE) == 3, "a special crate was spent" + outcome.where(account)
    jig = outcome.char(account)["jigsaw"]
    assert jig["held"] == 0 and not jig["filled_cells"], f"board touched: {jig}" + outcome.where(account)


def test_centered_puzzle_is_refused_at_start(outcome):
    bot = outcome.bot("admin")
    assert bot.stop_reason == "bad_layout", f"{bot.stop_reason}" + outcome.where("admin")
    assert bot.said("lies on the puzzle grid"), outcome.where("admin")
    _nothing_spent(outcome, "admin")


def test_intro_page_only_pauses_for_the_player(outcome):
    bot = outcome.bot("admin2")
    assert bot.paused, outcome.where("admin2")
    assert bot.said("puzzle window not found"), outcome.where("admin2")
    _nothing_spent(outcome, "admin2")


def test_closed_puzzle_pauses_for_the_player(outcome):
    bot = outcome.bot("admin3")
    assert bot.paused, outcome.where("admin3")
    assert bot.said("puzzle window not found"), outcome.where("admin3")
    _nothing_spent(outcome, "admin3")


def test_wrong_confirm_point_pauses_without_spending_a_crate(outcome):
    bot = outcome.bot("admin4")
    assert bot.paused, outcome.where("admin4")
    _nothing_spent(outcome, "admin4")


def test_no_errors_in_the_run(outcome):
    assert outcome.error is None, outcome.error
    assert not outcome.thread_errors, "\n".join(outcome.thread_errors) + f"\nfiles: {outcome.folder}"
    assert not outcome.logged_errors, "\n".join(outcome.logged_errors) + f"\nfiles: {outcome.folder}"

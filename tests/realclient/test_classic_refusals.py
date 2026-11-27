"""Classic fishing setups that cannot work (classic instance). Negative
tests: the bot must notice, stop with a reason that tells the player what
to fix, and spend nothing on bites it never had.

- admin   the area map stays open over the character: the bite bubble is
          hidden behind it -> "no bite" stop whose hint names the map
- admin2  no fishing rod equipped: nothing ever bites -> "no bite", no bait
          spent, and no "catch" claimed (the bubbles of the characters next
          to it must not count)
- admin3  inventory closed at start -> inventory_not_visible at once
- admin4  no bait at all on the keys -> "no bite", no catch claimed
"""

import pytest

from session import WORM, run_session, spot

INSTANCE = "classic"
RUN_SECONDS = 280      # three missed bites take 3-5 bite timeouts of ~45 s

SCENARIO = {
    "restart_on_crash": 0,
    "clients": [
        {"account": "admin", "position": spot(0)},
        {"account": "admin2", "position": spot(1), "rod": None},
        {"account": "admin3", "position": spot(2), "ui": {"open_inventory": False}},
        {"account": "admin4", "position": spot(3), "items": []},
    ],
}
BOTS = {a["account"]: ("fishing", {"classic_fishing": True}, None) for a in SCENARIO["clients"]}
_maps = {}


def _close_maps_except_admin(rig):
    for acc in rig.clients:
        if acc != "admin":
            _maps[acc] = rig.close_area_map(acc)
    _maps["admin_open"] = rig.area_map_open("admin")


@pytest.fixture(scope="module")
def outcome(rigs):
    _maps.clear()
    return run_session(rigs.get(INSTANCE), "classic_refusals", SCENARIO, BOTS, timeout=RUN_SECONDS,
                       after_up=_close_maps_except_admin)


def test_area_map_over_the_character_ends_with_a_hint_about_it(outcome):
    bot = outcome.bot("admin")
    assert _maps.get("admin_open"), "the area map was not open: nothing tested"
    assert bot.stop_reason == "no_bite", f"{bot.stop_reason}: {bot.stop_message}" + outcome.where("admin")
    assert "area map" in (bot.stop_message or ""), outcome.where("admin")
    assert bot.games == 0, f"{bot.games} catches claimed" + outcome.where("admin")


def test_no_rod_claims_no_catch(outcome):
    bot = outcome.bot("admin2")
    assert bot.stop_reason == "no_bite", f"{bot.stop_reason}: {bot.stop_message}" + outcome.where("admin2")
    assert bot.games == 0, f"{bot.games} catches claimed without a rod" + outcome.where("admin2")
    assert outcome.total("admin2", WORM) == 800, outcome.where("admin2")


def test_closed_inventory_stops_at_once(outcome):
    bot = outcome.bot("admin3")
    assert bot.stop_reason == "inventory_not_visible", f"{bot.stop_reason}" + outcome.where("admin3")
    assert outcome.total("admin3", WORM) == 800
    assert bot.statuses[-1][0] - bot.statuses[0][0] < 20, outcome.where("admin3")


def test_no_bait_claims_no_catch(outcome):
    bot = outcome.bot("admin4")
    assert bot.stop_reason in ("no_bite", "bait_depleted"), f"{bot.stop_reason}" + outcome.where("admin4")
    assert bot.games == 0, f"{bot.games} catches claimed without bait" + outcome.where("admin4")


def test_no_errors_in_the_run(outcome):
    assert outcome.error is None, outcome.error
    assert not outcome.thread_errors, "\n".join(outcome.thread_errors) + f"\nfiles: {outcome.folder}"
    assert not outcome.logged_errors, "\n".join(outcome.logged_errors) + f"\nfiles: {outcome.folder}"

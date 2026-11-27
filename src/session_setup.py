"""
Default settings and the run setup the bots get, without Qt.

The GUI (qt_gui.py) and the real-client tests (tools/realclient) both start
the bots from here, so a test runs the same configuration a player's start
button produces:

- ``default_config()``: every setting with its default value
- ``fishing_session_config(config, bait_keys)``: what a fishing bot gets
  when Start is pressed (fish actions completed and normalized, dead fish
  following their living fish, the bait keys, solver off)
- ``jigsaw_session_config(config)``: the same for a jigsaw worker
"""

from __future__ import annotations

import os
from typing import Iterable, List, Tuple

from utils import (MAX_WINDOWS, get_resource_path, is_bait_fish, normalize_fish_action,
                   with_dead_fish_actions)

MODE_FISHING = "fishing"
MODE_JIGSAW = "jigsaw"

TIMING_DEFAULTS = {
    "timing_cursor_settle":     0.012,
    "timing_button_hold":       0.008,
    "timing_post_click":        0.035,
    "timing_human_min":         0.150,
    "timing_human_max":         0.400,
    "timing_key_hold":          0.025,
    "timing_key_settle":        0.030,
    "timing_cast_interkey":     0.050,
    "timing_minigame_timeout":  6.000,
    "timing_recast_wait":       4.000,
    "timing_catch_wait":        0.400,
    "timing_open_wait":         0.100,
    "timing_dead_fish_check":   0.100,
    "timing_drop_settle":       0.120,
    "timing_quickskip_between": 0.100,
    "timing_quickskip_after":   0.100,
}

FISH_DISPLAY: List[Tuple[str, str, str]] = [
    # (display_name, asset_basename, fallback text; empty: the name's first letter)
    ("Aal", "Aal_living.jpg", ""),
    ("Ayu", "Ayu_living.jpg", ""),
    ("Barsch", "Barsch_living.jpg", ""),
    ("Brookforel", "Brookforell_living.jpg", ""),
    ("Carp", "Carp_living.jpg", ""),
    ("Catfish", "Catsfish_living.jpg", ""),
    ("Crab", "Crab_living.jpg", ""),
    ("Goldfish", "Goldfish_living.jpg", ""),
    ("Grasscarp", "Grasscarp_living.jpg", ""),
    ("Large zander", "Large_zander_living.jpg", ""),
    ("Lotusfish", "Lotusfish_living.jpg", ""),
    ("Mandarinfish", "Mandarinfish_living.jpg", ""),
    ("Mirrorcarp", "Mirrorcarp_living.jpg", ""),
    ("Rainbowfish", "Rainbowforell_living.jpg", ""),
    ("Redfeather", "Redfeather_living.jpg", ""),
    ("Riverforel", "Riverforell_living.jpg", ""),
    ("Salmon", "Salmon_living.jpg", ""),
    ("Shiri", "Shiri_living.jpg", ""),
    ("Skygazer", "Skygazer_living.jpg", ""),
    ("Snake head", "Snake_head_living.jpg", ""),
    ("Stint", "Stint_living.jpg", ""),
    ("Tenchi", "Tenchi_living.jpg", ""),
    ("Yabby", "Yabby_living.jpg", ""),
    ("Zander", "Zander_living.jpg", ""),
    ("Zebra", "Zebra_living.jpg", ""),
]

# Fish the game takes as bait (see utils.BAIT_FISH): keep, drop or use.
BAIT_DISPLAY: List[Tuple[str, str, str]] = [
    ("Shrimp", "Shrimp_living.jpg", ""),
    ("Vai", "Vai_living.jpg", ""),
]

# The dead kind of each fish with a dead icon in assets/fishing. They get no
# tile: a dead fish does what its living fish does (utils.dead_fish_action).
# Grilled fish are recognized too (so they are never taken for dead ones)
# and always kept.
DEAD_DISPLAY: List[Tuple[str, str, str]] = [
    (name, asset.replace("_living.jpg", "_dead.png"), emoji) for name, asset, emoji in FISH_DISPLAY + BAIT_DISPLAY
    if os.path.exists(get_resource_path(asset.replace("_living.jpg", "_dead.png")))
]

ITEM_DISPLAY: List[Tuple[str, str, str]] = [
    ("Black Dye", "Black_Dye_item.jpg", ""),
    ("Bleach", "Bleach_item.jpg", ""),
    ("Brown Dye", "Brown_Dye_item.jpg", ""),
    ("Gold", "Gold_item.jpg", ""),
    ("Goldring", "Goldring_item.jpg", ""),
    ("Kelp Key", "Kelp_Key_item.jpg", ""),
    ("Lucys ring", "Lucys_ring_item.jpg", ""),
    ("Red Dye", "Red_Dye_item.jpg", ""),
    ("Refugee cape", "Refugee_cape_item.jpg", ""),
    ("Sage King Glove", "Sage_King_Glove_item.jpg", ""),
    ("White Dye", "White_Dye_item.jpg", ""),
    ("Wise Emperor symbol", "Symbol_wise_emperors_item.jpg", ""),
    ("Yellow Dye", "Yellow_Dye_item.jpg", ""),
]


def default_fish_action(asset: str) -> str:
    """What a fish / item not set yet starts as: bait fish are used as bait,
    everything else is kept."""
    return "use" if is_bait_fish(asset) else "keep"


def default_config(version: str = "", accent: str = "") -> dict:
    """Every setting with its default value (what a first start writes)."""
    import disguise   # lazy: pulls in the process tools only when needed
    cfg = {
        "version": version,
        "human_like_clicking": False,
        "quick_skip": False,
        "quick_skip_mode": "horse",
        "golden_tuna": True,
        "golden_tuna_choice": 1,
        "sound_alert_on_finish": True,
        "sound_on_pause": True,
        "classic_fishing": False,
        "classic_fishing_delay": 3.0,
        "ui_mode": MODE_FISHING,
        "ui_scale": "auto",
        disguise.CONFIG_KEY: False,
        "jigsaw_solver_enabled": False,
        "jigsaw_crate_priority": "normal_first",
        "jigsaw_detection_threshold": 0.80,
        "jigsaw_action_delay": 0.15,
        "jigsaw_debug_screenshots": False,
        "jigsaw_dry_run": False,
        "auto_fish_handling": False,
        # Minigame detection: None = stock (official client) colors. A
        # profile learned by self-calibration is stored here automatically;
        # set it back to null to forget it.
        "adaptive_detection": True,
        # Shorten timing_recast_wait automatically while casts keep succeeding.
        "adaptive_recast_wait": True,
        # "auto" = per-window capture once verified, "screen" = always copy
        # the screen, "window" = always per-window (no verification).
        "capture_backend": "auto",
        "detection_profile": None,
        # Diagnostic log (Settings tab): one zip per run next to the program.
        "diagnostics_enabled": False,
        "diagnostics_record_screens": False,   # + a video of each game window in the log
        "fish_actions": {},
        "drop_button_pos": None,
        "confirm_button_pos": None,
        "armor_slot_pos": None,
        "armor_slot_page": None,      # inventory page (1-8) the spare armor is on, read at capture
        "accent_color": accent,
        "rgb_wave_active": False,
        "bait_keys": ["1", "2", "3", "4"],
        "cast_key": "space",            # cast / reel in (Fishing tab)
        "mount_key": "g",               # Ctrl+<key> mounts / dismounts (quick skip, classic misses; Fishing tab)
        # Values a client may need changed that have no GUI field: kept
        # in the file so they can be changed there.
        "drop_ground_pos": None,        # window-relative drop spot; None = window center
        "inventory_rows": 9,            # rows of an inventory page
        "classic_miss_limit": 3,        # classic misses in a row before a bait key counts as empty
        "classic_bite_timeout": 45.0,   # s waited for a classic bite (grows with the longest bite seen)
        "timing_drop_dialog_timeout": 1.0,
        "timing_minigame_timeout_cap": 60.0,   # longest bite wait after misses
        "jigsaw_result_button_pos": None,      # None = lower middle of the window
        "jigsaw_piece_detection_threshold": 0.60,
        "jigsaw_inventory_crate_threshold": 0.72,
        "bait_quantity": 200,
        "bait": 800,
        "selected_windows": [""] * MAX_WINDOWS,
        # The setup wizard button pulses until that activity's wizard
        # was opened once (first start / first switch to Jigsaw).
        "wizard_done_fishing": False,
        "wizard_done_jigsaw": False,
    }
    for p in range(1, 9):
        cfg[f"inv_page_{p}_pos"] = None
    cfg.update(TIMING_DEFAULTS)
    return cfg


def complete_fish_actions(config: dict) -> dict:
    """Gives every fish / item an action the bot knows (unset or broken ones
    are "keep", bait fish default to "use") and makes the dead fish follow
    their living fish. Changes ``config["fish_actions"]`` in place, as the
    GUI saves it, and returns it."""
    actions = config.setdefault("fish_actions", {})
    for asset in [a for _, a, _ in FISH_DISPLAY + BAIT_DISPLAY + ITEM_DISPLAY] + list(actions):
        actions[asset] = normalize_fish_action(actions.get(asset, default_fish_action(asset)), asset)
    config["fish_actions"] = with_dead_fish_actions(actions)
    return config["fish_actions"]


def fishing_session_config(config: dict, bait_keys: Iterable[str]) -> dict:
    """The configuration a fishing bot gets at Start (a copy)."""
    complete_fish_actions(config)
    cfg = dict(config)
    cfg["bait_keys"] = list(bait_keys)
    cfg["jigsaw_solver_enabled"] = False
    return cfg


def jigsaw_session_config(config: dict) -> dict:
    """The configuration a jigsaw worker gets at Start (a copy)."""
    cfg = dict(config)
    cfg["jigsaw_solver_enabled"] = True
    return cfg

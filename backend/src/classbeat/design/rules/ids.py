"""The closed enum of soft-rule ids (ADR 0017 sections 1, 2 and 4).

One place, with no imports from the rest of classbeat, so the dismissal route (P1b) and the
profile loader (P1) read the same list. A rule id is a stable name, never free text; a profile
file may only name ids from here, and a class may only dismiss ids from here. Adding an id is a
code change reviewed like a rule. Removing or renaming one needs care: ``classes.dismissed_rules``
may still hold it, and the readers skip ids they do not know.
"""

from typing import Final

RULE_IDS: Final[frozenset[str]] = frozenset(
    {
        "warmup_short",
        "warmup_long",
        "first_hard_effort_early",
        "first_hard_effort_late",
        "cooldown_short",
        "cooldown_long",
        "peak_zone_low",
        "peak_zone_high",
        "zone5_share_high",
        "zone4_share_high",
        "zone4_share_low",
        "hard_work_front_loaded",
        "mean_zone_low",
        "mean_zone_high",
        "load_per_minute_low",
        "load_per_minute_high",
        "segments_few",
        "segments_many",
        "intervals_few",
        "intervals_many",
        "standing_share_low",
        "standing_share_high",
        "zone_jumps_large",
        "no_games_or_cue_cards",
    }
)

# ``classes.dismissed_rules`` holds at most this many ids (migration, CHECK constraint).
MAX_DISMISSED: Final = 32


def is_rule_id(value: object) -> bool:
    return isinstance(value, str) and value in RULE_IDS

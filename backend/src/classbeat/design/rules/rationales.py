"""Plain-language sentences for the rule hits (ADR 0017 sections 1, 3 and 4).

Written for classbeat, never copied from a source. They say what is usual, not what is right, and
carry no number, no studio and no brand: the numbers come from the profile's bounds. The table is
an allowlist reviewed word by word; a profile file only names an id.
"""

from typing import Final

RATIONALES: Final[dict[str, str]] = {
    "warmup_ramp": "Most classes of this kind ride a few minutes before the first hard effort.",
    "hard_start_late": "Most classes of this kind reach the first hard effort before this point.",
    "cooldown_easy": "Most classes of this kind end with an easy cool-down of a couple of minutes.",
    "cooldown_not_long": "Most classes of this kind keep the cool-down short.",
    "hard_share_enough": "Most classes of this kind spend a good part of the ride in hard zones.",
    "hard_share_not_all": "Most classes of this kind leave room for easier riding between efforts.",
    "zone_ceiling": "Most classes of this kind stay out of the highest zones.",
    "top_zone_present": "Most classes of this kind include some riding in the top zones.",
    "top_zone_bounded": "Most classes of this kind keep the top zones to a limited share.",
    "mean_zone_enough": "Most classes of this kind have a steadier overall effort level than this.",
    "mean_zone_bounded": "Most classes of this kind have a lower overall effort level than this.",
    "load_enough": "Most classes of this kind put a bit more training load into each minute.",
    "load_bounded": "Most classes of this kind put a bit less training load into each minute.",
    "hard_not_early": "Most classes of this kind hold the hard efforts back in the first quarter.",
    "standing_bounded": "Most classes of this kind keep standing to a limited share of the ride.",
}

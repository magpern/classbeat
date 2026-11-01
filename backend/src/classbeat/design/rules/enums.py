"""The closed vocabularies of a rule profile (ADR 0017 sections 1 and 2).

A profile file holds ids from these sets and numbers, nothing else. A new rule, metric or brief
override is a code change here with a schema bump, reviewed like any rule. Pure data: this
package imports neither the agent nor the database.
"""

from dataclasses import dataclass
from typing import Final, Literal, get_args

SOURCE: Final = "professional studio library"

Sport = Literal["cycling"]
ClassType = Literal["plain", "interval", "hiit", "journey", "ftp_test"]
Evidence = Literal["strong", "moderate", "weak"]
Basis = Literal["p5", "p95", "range_10"]
Severity = Literal["info", "warn"]
Metric = Literal[
    "warmup_s_before_first_zone3",
    "first_zone4plus_s",
    "cooldown_s",
    "max_zone",
    "zone5plus_time_share",
    "zone4plus_time_share",
    "zone4plus_share_in_first_25pct",
    "mean_zone",
    "tss_per_minute",
    "segments",
    "intervals",
    "standing_time_share",
    "zone_step_over_one_share",
    "no_games_or_cue_cards",
]
RuleId = Literal[
    "warmup_short",
    "hard_start_late",
    "cooldown_short",
    "cooldown_long",
    "hard_share_low",
    "hard_share_high",
    "zone_ceiling_exceeded",
    "top_zone_share_low",
    "top_zone_share_high",
    "mean_zone_low",
    "mean_zone_high",
    "tss_rate_low",
    "tss_rate_high",
    "hard_too_early",
    "standing_long",
]
RationaleId = Literal[
    "warmup_ramp",
    "hard_start_late",
    "cooldown_easy",
    "cooldown_not_long",
    "hard_share_enough",
    "hard_share_not_all",
    "zone_ceiling",
    "top_zone_present",
    "top_zone_bounded",
    "mean_zone_enough",
    "mean_zone_bounded",
    "load_enough",
    "load_bounded",
    "hard_not_early",
    "standing_bounded",
]
# The brief fields that make a rule's expectation wrong (ADR 0017 section 1). ``intensity:*`` is
# the stored intensity; ``style`` and ``text`` stand for a non-empty field of the stored brief.
BriefOverride = Literal[
    "intensity:easy", "intensity:hard", "sprints", "races", "ftp_test", "style", "text"
]

SPORTS: Final = get_args(Sport)
CLASS_TYPES: Final = get_args(ClassType)
EVIDENCES: Final = get_args(Evidence)
BASES: Final = get_args(Basis)
SEVERITIES: Final = get_args(Severity)
METRICS: Final = get_args(Metric)
RULE_IDS: Final = get_args(RuleId)
RATIONALE_IDS: Final = get_args(RationaleId)
BRIEF_OVERRIDES: Final = get_args(BriefOverride)

# Only a profile with this many classes may use a percentile bound (ADR 0017 section 2).
MIN_N_PERCENTILE: Final = 25


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """What a rule id measures and which side of the usual range it guards."""

    metric: Metric
    side: Literal["low", "high"]


RULE_SPECS: Final[dict[str, RuleSpec]] = {
    "warmup_short": RuleSpec("first_zone4plus_s", "low"),
    "hard_start_late": RuleSpec("first_zone4plus_s", "high"),
    "cooldown_short": RuleSpec("cooldown_s", "low"),
    "cooldown_long": RuleSpec("cooldown_s", "high"),
    "hard_share_low": RuleSpec("zone4plus_time_share", "low"),
    "hard_share_high": RuleSpec("zone4plus_time_share", "high"),
    "zone_ceiling_exceeded": RuleSpec("max_zone", "high"),
    "top_zone_share_low": RuleSpec("zone5plus_time_share", "low"),
    "top_zone_share_high": RuleSpec("zone5plus_time_share", "high"),
    "mean_zone_low": RuleSpec("mean_zone", "low"),
    "mean_zone_high": RuleSpec("mean_zone", "high"),
    "tss_rate_low": RuleSpec("tss_per_minute", "low"),
    "tss_rate_high": RuleSpec("tss_per_minute", "high"),
    "hard_too_early": RuleSpec("zone4plus_share_in_first_25pct", "high"),
    "standing_long": RuleSpec("standing_time_share", "high"),
}

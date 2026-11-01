"""The soft-rule engine (ADR 0017 section 1): ``evaluate(spec, brief, profile) -> list[RuleHit]``.

Pure and deterministic: the same fitted spec, stored brief, profile and dismissed ids always give
the same hits. Nothing here reads the database, the agent or a clock. A hit is a note, never a
refusal; the brief can lower it (``explained_by_brief``) but never hides it.

Metrics are measured over the fitted timeline (every interval, cool-down included). Shares are
fractions of the total time, zones are the library's 1 to 7, and ``zone4plus_share_in_first_25pct``
is the share of the first quarter of the timeline spent in zone 4 or above. A class that never
reaches zone 4 has its ``first_zone4plus_s`` set to the whole timeline length.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from mowl import workout

from classbeat.design.rules.enums import RULE_SPECS, Metric, RationaleId, RuleId, Severity
from classbeat.design.rules.profile import Profile, ProfileRule

MAX_HITS: Final = 5
HARD_ZONE: Final = 4
TOP_ZONE: Final = 5
FIRST_FRACTION: Final = 0.25

Spec = Mapping[str, Any]
Timeline = list[tuple[int, int, bool]]


class BriefLike(Protocol):
    """The stored brief fields a rule may be overridden by (``agent.schemas.Brief`` fits)."""

    @property
    def intensity(self) -> str: ...
    @property
    def sprints(self) -> int: ...
    @property
    def races(self) -> int: ...
    @property
    def ftp_test(self) -> int | None: ...
    @property
    def style(self) -> str: ...
    @property
    def text(self) -> str: ...


@dataclass(frozen=True, slots=True)
class RuleHit:
    rule: RuleId
    severity: Severity
    metric: Metric
    value: float
    low: float | None
    high: float | None
    profile_id: str
    profile_version: int
    rationale_id: RationaleId
    explained_by_brief: bool

    @property
    def bounds(self) -> tuple[float | None, float | None]:
        return (self.low, self.high)


def _timeline(spec: Spec) -> Timeline:
    """``(sec, zone, standing)`` for every interval in order."""
    return [
        (int(i["sec"]), int(i["zone"]), bool(i.get("standing", False)))
        for s in spec["segments"]
        for i in s["intervals"]
    ]


def _total(tl: Timeline) -> int:
    return sum(sec for sec, _, _ in tl)


def _share(part: float, total: float) -> float:
    return part / total if total else 0.0


def _first_zone4plus_s(spec: Spec) -> float:
    elapsed = 0
    for sec, zone, _ in _timeline(spec):
        if zone >= HARD_ZONE:
            return float(elapsed)
        elapsed += sec
    return float(elapsed)


def _cooldown_s(spec: Spec) -> float:
    return float(sum(workout.seconds(s) for s in spec["segments"] if s["kind"] == "cooldown"))


def _max_zone(spec: Spec) -> float:
    return float(max((zone for _, zone, _ in _timeline(spec)), default=0))


def _zone_share(zone_min: int) -> Callable[[Spec], float]:
    def measure(spec: Spec) -> float:
        tl = _timeline(spec)
        return _share(sum(sec for sec, zone, _ in tl if zone >= zone_min), _total(tl))

    return measure


def _mean_zone(spec: Spec) -> float:
    tl = _timeline(spec)
    return _share(sum(sec * zone for sec, zone, _ in tl), _total(tl))


def _tss_per_minute(spec: Spec) -> float:
    total = _total(_timeline(spec))
    return workout.tss(dict(spec)) / (total / 60) if total else 0.0


def _hard_in_first_quarter(spec: Spec) -> float:
    tl = _timeline(spec)
    window = _total(tl) * FIRST_FRACTION
    if window <= 0:
        return 0.0
    elapsed = 0.0
    hard = 0.0
    for sec, zone, _ in tl:
        if zone >= HARD_ZONE:
            hard += min(sec, max(0.0, window - elapsed))
        elapsed += sec
    return hard / window


def _standing_share(spec: Spec) -> float:
    tl = _timeline(spec)
    return _share(sum(sec for sec, _, standing in tl if standing), _total(tl))


METRICS: Final[dict[str, Callable[[Spec], float]]] = {
    "first_zone4plus_s": _first_zone4plus_s,
    "cooldown_s": _cooldown_s,
    "max_zone": _max_zone,
    "zone5plus_time_share": _zone_share(TOP_ZONE),
    "zone4plus_time_share": _zone_share(HARD_ZONE),
    "mean_zone": _mean_zone,
    "tss_per_minute": _tss_per_minute,
    "zone4plus_share_in_first_25pct": _hard_in_first_quarter,
    "standing_time_share": _standing_share,
}


def overrides_met(overrides: Collection[str], brief: BriefLike) -> bool:
    """Whether a field the user set in the stored brief makes the rule's expectation wrong.

    Only stored brief fields count: a derived class type never does (ADR 0017 section 1).
    """
    checks: dict[str, bool] = {
        "intensity:easy": brief.intensity == "easy",
        "intensity:hard": brief.intensity == "hard",
        "sprints": brief.sprints > 0,
        "races": brief.races > 0,
        "ftp_test": brief.ftp_test is not None,
        "style": bool(brief.style.strip()),
        "text": bool(brief.text.strip()),
    }
    return any(checks.get(o, False) for o in overrides)


def _violates(rule: ProfileRule, side: Literal["low", "high"], value: float) -> bool:
    if side == "low":
        return rule.low is not None and value < rule.low
    return rule.high is not None and value > rule.high


def evaluate(
    spec: Spec,
    brief: BriefLike,
    profile: Profile | None,
    dismissed: Collection[str] = frozenset(),
) -> list[RuleHit]:
    """The violated rules of ``profile`` for the fitted ``spec``, at most ``MAX_HITS``.

    ``warn`` hits come first, then ``info`` (explained ones are ``info``), each group in the
    profile's rule order. Dismissed rule ids are dropped before the cap. ``None`` gives ``[]``.
    """
    if profile is None:
        return []
    cache: dict[str, float] = {}
    hits: list[RuleHit] = []
    for rule in profile.rules:
        if rule.rule in dismissed:
            continue
        if rule.metric not in cache:
            cache[rule.metric] = METRICS[rule.metric](spec)
        value = cache[rule.metric]
        if not _violates(rule, RULE_SPECS[rule.rule].side, value):
            continue
        explained = overrides_met(rule.brief_overrides, brief)
        hits.append(
            RuleHit(
                rule=rule.rule,
                severity="info" if explained else rule.severity,
                metric=rule.metric,
                value=value,
                low=rule.low,
                high=rule.high,
                profile_id=profile.id,
                profile_version=profile.version,
                rationale_id=rule.rationale_id,
                explained_by_brief=explained,
            )
        )
    hits.sort(key=lambda h: h.severity != "warn")
    return hits[:MAX_HITS]

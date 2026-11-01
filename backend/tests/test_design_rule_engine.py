"""The soft-rule engine (ADR 0017 P2): metrics, a pair of fixtures per shipped rule, overrides,
ordering, the cap, dismissal and determinism. Specs are synthetic and built from the bounds."""

import math
from dataclasses import dataclass
from functools import cache
from typing import Any

import pytest
from mowl import workout

from classbeat.agent.schemas import Brief
from classbeat.design.rules import MAX_HITS, RuleHit, evaluate, load_registry
from classbeat.design.rules import engine as G
from classbeat.design.rules.enums import RULE_SPECS
from classbeat.design.rules.profile import Profile, ProfileRule

REG = load_registry()
PROFILES = [REG.profiles[k] for k in sorted(REG.profiles)]
CASES = [(p, r) for p in PROFILES for r in p.rules]
TOTAL = 10_000  # seconds of the share and mean fixtures, so every bound is hit exactly


@dataclass(frozen=True)
class B:
    intensity: str = "moderate"
    sprints: int = 0
    races: int = 0
    ftp_test: int | None = None
    style: str = ""
    text: str = ""


def _spec(*parts: tuple[str, int, int] | tuple[str, int, int, bool]) -> dict[str, Any]:
    """Segments from ``(kind, sec, zone[, standing])``, one interval each."""
    segs = [
        {
            "name": f"s{n}",
            "kind": p[0],
            "intervals": [
                {"sec": p[1], "zone": p[2], "rpm": [80, 90], "standing": len(p) > 3 and p[3]}
            ],
        }
        for n, p in enumerate(parts)
        if p[1] > 0
    ]
    return {"name": "x", "description": "", "segments": segs}


def _split(bound: float, side: str, scale: int = TOTAL) -> tuple[int, int]:
    """(inside, outside) counts of a quantity with ``bound``: the bound itself is inside."""
    if side == "low":
        inside = math.ceil(bound * scale - 1e-9)
        return inside, inside - 1
    inside = math.floor(bound * scale + 1e-9)
    return inside, inside + 1


def _share_spec(zone: int, n: int) -> dict[str, Any]:
    return _spec(
        ("warmup", 2000, 1), ("main", n, zone), ("main", TOTAL - 2100 - n, 2), ("cooldown", 100, 1)
    )


def _mean_spec(count: int, base: int) -> dict[str, Any]:
    return _spec(
        ("main", count, base + 1), ("main", TOTAL - 100 - count, base), ("cooldown", 100, base)
    )


def _tss_spec(k: int) -> dict[str, Any]:
    return _spec(("main", 3600 - k, 2), ("main", k, 5), ("cooldown", 120, 1))


@cache
def _tss_rate(k: int) -> float:
    return G.METRICS["tss_per_minute"](_tss_spec(k))


@cache
def _tss_pair(bound: float, side: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rates = [(_tss_rate(k), k) for k in range(0, 3601, 10)]
    ks = [k for _, k in rates]
    if side == "high":
        out = next(k for r, k in rates if r > bound)
        return _tss_spec(max(k for k in ks if _tss_rate(k) <= bound)), _tss_spec(out)
    out = max(k for r, k in rates if r < bound)
    return _tss_spec(min(k for k in ks if _tss_rate(k) >= bound)), _tss_spec(out)


def pair(rule: ProfileRule) -> tuple[dict[str, Any], dict[str, Any]]:
    return _pair(rule.rule, rule.metric, rule.low, rule.high)


@cache
def _pair(
    rule_id: str, metric: str, low: float | None, high: float | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A synthetic spec just inside and one just outside the rule's bound."""
    side = RULE_SPECS[rule_id].side
    bound = low if side == "low" else high
    assert bound is not None
    m = metric
    if m == "first_zone4plus_s":
        i, o = _split(bound, side, 1)
        make = lambda x: _spec(("warmup", x, 1), ("main", 600, 4), ("cooldown", 120, 1))  # noqa: E731
        return make(i), make(o)
    if m == "cooldown_s":
        i, o = _split(bound, side, 1)
        make = lambda x: _spec(("warmup", 400, 1), ("main", 900, 4), ("cooldown", x, 1))  # noqa: E731
        return make(i), make(o)
    if m == "max_zone":
        make = lambda z: _spec(("warmup", 400, 1), ("main", 900, z), ("cooldown", 120, 1))  # noqa: E731
        return make(int(bound)), make(int(bound) + 1)
    if m in ("zone4plus_time_share", "zone5plus_time_share"):
        zone = 4 if m == "zone4plus_time_share" else 5
        i, o = _split(bound, side)
        return _share_spec(zone, i), _share_spec(zone, o)
    if m == "mean_zone":
        base = math.floor(bound)
        i, o = _split(bound - base, side)
        return _mean_spec(i, base), _mean_spec(o, base)
    if m == "tss_per_minute":
        return _tss_pair(bound, side)
    if m == "zone4plus_share_in_first_25pct":
        window = 600
        i, o = _split(bound, side, window)
        make = lambda h: _spec(  # noqa: E731
            ("main", h, 4), ("main", 2280 - h, 2), ("cooldown", 120, 1)
        )
        return make(i), make(o)
    if m == "standing_time_share":
        i, o = _split(bound, side)
        make = lambda s: _spec(  # noqa: E731
            ("warmup", 2000, 1),
            ("main", s, 2, True),
            ("main", TOTAL - 2100 - s, 2),
            ("cooldown", 100, 1),
        )
        return make(i), make(o)
    raise AssertionError(m)


def alone(profile: Profile, rule: ProfileRule) -> Profile:
    """The profile with only ``rule``, so the cap and other rules cannot hide its hit."""
    return profile.model_copy(update={"rules": [rule]})


def _ids(hits: list[RuleHit]) -> list[str]:
    return [h.rule for h in hits]


def test_every_shipped_rule_has_a_pair() -> None:
    assert len(CASES) == 81


@pytest.mark.parametrize(("profile", "rule"), CASES, ids=[f"{p.id}:{r.rule}" for p, r in CASES])
def test_pair_inside_has_no_hit_outside_has_exactly_that_hit(
    profile: Profile, rule: ProfileRule
) -> None:
    inside, outside = pair(rule)
    assert rule.rule not in _ids(evaluate(inside, B(), alone(profile, rule)))
    hit = next(
        (h for h in evaluate(outside, B(), alone(profile, rule)) if h.rule == rule.rule), None
    )
    assert hit is not None
    assert hit.severity == rule.severity
    assert hit.metric == rule.metric
    assert hit.bounds == (rule.low, rule.high)
    assert (hit.profile_id, hit.profile_version) == (profile.id, profile.version)
    assert hit.rationale_id == rule.rationale_id
    assert hit.explained_by_brief is False
    side = RULE_SPECS[rule.rule].side
    bound = rule.low if side == "low" else rule.high
    assert bound is not None
    assert (hit.value < bound) if side == "low" else (hit.value > bound)


def test_metrics_on_a_known_spec() -> None:
    spec = _spec(
        ("warmup", 300, 1), ("main", 100, 2), ("main", 200, 5, True), ("main", 300, 3),
        ("cooldown", 100, 1),
    )  # fmt: skip
    m = {k: f(spec) for k, f in G.METRICS.items()}
    assert m["first_zone4plus_s"] == 400
    assert m["cooldown_s"] == 100
    assert m["max_zone"] == 5
    assert m["zone4plus_time_share"] == pytest.approx(0.2)
    assert m["zone5plus_time_share"] == pytest.approx(0.2)
    assert m["standing_time_share"] == pytest.approx(0.2)
    assert m["mean_zone"] == pytest.approx((300 + 200 + 1000 + 900 + 100) / 1000)
    assert m["tss_per_minute"] == pytest.approx(workout.tss(spec) / (1000 / 60))
    assert m["zone4plus_share_in_first_25pct"] == pytest.approx(0.0)  # 250 s window, zone 1 and 2
    hard_first = _spec(("main", 100, 5), ("main", 700, 2), ("cooldown", 200, 1))
    assert G.METRICS["zone4plus_share_in_first_25pct"](hard_first) == pytest.approx(0.4)


def test_never_reaching_zone4_counts_the_whole_timeline() -> None:
    spec = _spec(("main", 500, 2), ("cooldown", 100, 1))
    assert G.METRICS["first_zone4plus_s"](spec) == 600


def test_real_brief_satisfies_the_protocol() -> None:
    profile = REG.profiles["cycling.plain.45"]
    spec = pair(next(r for r in profile.rules if r.rule == "mean_zone_low"))[1]
    assert evaluate(spec, Brief(length_min=45), profile)


# --- the brief wins ----------------------------------------------------------------------------

PLAIN45 = REG.profiles["cycling.plain.45"]
INTERVAL45 = REG.profiles["cycling.interval.45"]


def _rule(profile: Profile, rule_id: str) -> ProfileRule:
    return next(r for r in profile.rules if r.rule == rule_id)


@pytest.mark.parametrize(
    ("brief", "override"),
    [
        (B(intensity="easy"), "intensity:easy"),
        (B(intensity="hard"), "intensity:hard"),
        (B(sprints=2), "sprints"),
        (B(races=1), "races"),
        (B(ftp_test=20), "ftp_test"),
        (B(style="rolling hills"), "style"),
        (B(text="a gentle one"), "text"),
    ],
)
def test_each_override_lowers_severity_and_marks_the_hit(brief: B, override: str) -> None:
    for profile in PROFILES:
        for rule in profile.rules:
            _, outside = pair(rule)
            hit = next(
                h for h in evaluate(outside, B(), alone(profile, rule)) if h.rule == rule.rule
            )
            lowered = next(
                h for h in evaluate(outside, brief, alone(profile, rule)) if h.rule == rule.rule
            )
            assert lowered.value == hit.value
            expect = override in rule.brief_overrides
            assert lowered.explained_by_brief is expect
            assert lowered.severity == ("info" if expect else rule.severity)


def test_a_field_that_is_not_an_override_changes_nothing() -> None:
    rule = _rule(INTERVAL45, "mean_zone_low")
    _, outside = pair(rule)
    brief = Brief(length_min=45, climbs=4, intensity="moderate")
    hit = next(h for h in evaluate(outside, brief, INTERVAL45) if h.rule == "mean_zone_low")
    assert (hit.severity, hit.explained_by_brief) == ("warn", False)


def test_blank_style_and_text_do_not_override() -> None:
    rule = _rule(PLAIN45, "standing_long")
    _, outside = pair(rule)
    hit = next(
        h for h in evaluate(outside, B(style="  ", text=" "), PLAIN45) if h.rule == rule.rule
    )
    assert hit.explained_by_brief is False


def test_an_explained_hit_is_still_reported() -> None:
    rule = _rule(PLAIN45, "hard_share_low")
    _, outside = pair(rule)
    hits = evaluate(outside, B(intensity="easy"), PLAIN45)
    assert "hard_share_low" in _ids(hits)


# --- order, cap, dismissal, determinism ----------------------------------------------------------


def _violates_everything() -> dict[str, Any]:
    return _spec(("main", 2700, 1), ("cooldown", 30, 1))


def test_no_profile_gives_no_hits() -> None:
    assert evaluate(_violates_everything(), B(), None) == []


def test_cap_and_order_warn_first() -> None:
    all_hits = evaluate(_violates_everything(), B(), INTERVAL45)
    assert len(all_hits) == MAX_HITS
    assert {h.severity for h in all_hits} == {"warn"}
    # an easy brief explains the low-side rules; the remaining warns must come before any info
    hits = evaluate(_violates_everything(), B(intensity="easy"), INTERVAL45, frozenset())
    severities = [h.severity for h in hits]
    assert severities == sorted(severities, key=lambda s: s != "warn")
    assert "info" in severities
    assert "warn" in severities


def test_ties_keep_the_profiles_rule_order() -> None:
    hits = evaluate(_violates_everything(), B(), INTERVAL45)
    order = [r.rule for r in INTERVAL45.rules]
    assert [order.index(h.rule) for h in hits] == sorted(order.index(h.rule) for h in hits)


def test_dismissed_rules_are_dropped_before_the_cap() -> None:
    spec = _violates_everything()
    violated = [r.rule for r in INTERVAL45.rules if evaluate(spec, B(), alone(INTERVAL45, r))]
    assert len(violated) > MAX_HITS
    first = evaluate(spec, B(), INTERVAL45)
    assert _ids(first) == violated[:MAX_HITS]
    after = evaluate(spec, B(), INTERVAL45, {first[0].rule})
    assert _ids(after) == violated[1 : MAX_HITS + 1]  # the sixth took the freed place


def test_dismissing_everything_gives_nothing() -> None:
    ids = {r.rule for r in INTERVAL45.rules}
    assert evaluate(_violates_everything(), B(), INTERVAL45, ids) == []


def test_evaluate_is_deterministic_and_does_not_mutate() -> None:
    spec = _violates_everything()
    before = repr(spec)
    runs = [evaluate(spec, B(sprints=1), INTERVAL45, {"tss_rate_low"}) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert repr(spec) == before

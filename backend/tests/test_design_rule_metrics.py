"""Soft-rule metrics for interval length, sprints, climbs, work to rest and intensity factor (#644).

Every profile here is synthetic: no shipped profile carries a bound for these rules yet, so the
tests build one rule at a time from a bound they choose. The metric definitions are those of
mowl-workouts ``docs/corpus-stats.md`` (``mowl corpus-analyze``); each test says which one.
"""

import json
import math
import re
from typing import Any

import pytest
from mowl import workout

from classbeat.design import guidance
from classbeat.design.rules import RATIONALES, evaluate, load_registry
from classbeat.design.rules import engine as G
from classbeat.design.rules import enums as E
from classbeat.design.rules.profile import Profile, parse_profile

NEW_METRICS = (
    "longest_interval_s",
    "median_interval_s",
    "sprint_count",
    "climb_time_share",
    "work_rest_ratio",
    "intensity_factor",
)
# The sentence each new rule reads with (the profile names it; the web copy mirrors it).
RATIONALE = {
    "longest_interval_short": "longest_interval_enough",
    "longest_interval_long": "longest_interval_bounded",
    "median_interval_short": "median_interval_enough",
    "median_interval_long": "median_interval_bounded",
    "sprints_few": "sprints_present",
    "sprints_many": "sprints_bounded",
    "climb_share_low": "climb_present",
    "climb_share_high": "climb_bounded",
    "work_rest_low": "work_rest_enough",
    "work_rest_high": "work_rest_bounded",
    "intensity_factor_low": "intensity_enough",
    "intensity_factor_high": "intensity_bounded",
}


class Brief:
    intensity = "moderate"
    sprints = 0
    races = 0
    ftp_test = None
    style = ""
    text = ""


def iv(sec: int, zone: int, **extra: Any) -> dict[str, Any]:
    return {"sec": sec, "zone": zone, **extra}


def spec_of(*segments: tuple[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "name": "x",
        "description": "",
        "segments": [
            {"name": f"s{n}", "kind": kind, "intervals": ivs}
            for n, (kind, ivs) in enumerate(segments)
        ],
    }


def simple(*parts: tuple[str, int, int]) -> dict[str, Any]:
    """One interval per ``(kind, sec, zone)``."""
    return spec_of(*((k, [iv(s, z)]) for k, s, z in parts))


def synthetic(
    rule_id: str, bound: float, *, severity: str = "warn", basis: str | None = None
) -> Profile:
    """A validated profile holding only ``rule_id``; ``warn`` needs strong evidence."""
    side = E.RULE_SPECS[rule_id].side
    strong = severity == "warn"
    basis = basis or ("p5" if side == "low" else "p95") if strong else "range_10"
    raw = {
        "schema": 1,
        "id": "cycling.interval.45",
        "version": 1,
        "sport": "cycling",
        "class_type": "interval",
        "label_minutes": 45,
        "evidence": "strong" if strong else "moderate",
        "n": 30 if strong else 20,
        "source": E.SOURCE,
        "snapshot": "2026-11-05",
        "rules": [
            {
                "rule": rule_id,
                "metric": E.RULE_SPECS[rule_id].metric,
                "low": bound if side == "low" else None,
                "high": bound if side == "high" else None,
                "basis": basis,
                "severity": severity,
                "rationale_id": RATIONALE[rule_id],
                "brief_overrides": ["sprints"],
            }
        ],
    }
    return parse_profile(json.dumps(raw).encode())


def hits(spec: dict[str, Any], profile: Profile) -> list[str]:
    return [h.rule for h in evaluate(spec, Brief(), profile)]


def measure(metric: str, spec: dict[str, Any]) -> float | None:
    return G.METRICS[metric](spec)


# --- the catalogue ------------------------------------------------------------------------------


def test_new_metrics_and_rules_are_in_the_catalogue() -> None:
    assert set(NEW_METRICS) <= set(E.METRICS)
    assert set(NEW_METRICS) <= set(G.METRICS)
    assert set(RATIONALE) <= set(E.RULE_SPECS)
    assert {E.RULE_SPECS[r].metric for r in RATIONALE} == set(NEW_METRICS)
    for metric in NEW_METRICS:  # a low and a high rule for each
        assert {E.RULE_SPECS[r].side for r in RATIONALE if E.RULE_SPECS[r].metric == metric} == {
            "low",
            "high",
        }
    assert set(RATIONALE.values()) <= set(RATIONALES)


def test_every_rule_spec_metric_can_be_measured() -> None:
    assert {s.metric for s in E.RULE_SPECS.values()} <= set(G.METRICS)


def test_segments_and_intervals_stay_unwired() -> None:
    """Decided in #644: the counts follow the length and the music, not the pattern of a class."""
    assert not {"segments", "intervals"} & {s.metric for s in E.RULE_SPECS.values()}
    assert not {"segments", "intervals"} & set(G.METRICS)


def test_shipped_profiles_use_none_of_the_new_rules() -> None:
    used = {r.rule for p in load_registry().profiles.values() for r in p.rules}
    assert not used & set(RATIONALE)


def test_the_new_wording_follows_the_sport_rule() -> None:
    for rid, sport in (
        ("sprints_present", "cycling"),
        ("sprints_bounded", "cycling"),
        ("climb_present", "cycling"),
        ("climb_bounded", "cycling"),
    ):
        assert E.RATIONALE_SPORTS[rid] == sport
    neutral = set(RATIONALE.values()) - set(E.RATIONALE_SPORTS)
    assert len(neutral) == 8
    for rid in neutral:
        assert not re.search(r"\b(ride|riding|standing|run|running)\b", RATIONALES[rid])


# --- interval length (corpus interval_sec_max, interval_sec_median) -----------------------------


def test_longest_and_median_interval_count_every_interval() -> None:
    spec = spec_of(
        ("warmup", [iv(60, 1), iv(120, 2)]),
        ("main", [iv(300, 4), iv(30, 5)]),
        ("cooldown", [iv(90, 1)]),
    )
    assert measure("longest_interval_s", spec) == 300
    assert measure("median_interval_s", spec) == 90  # 30 60 90 120 300


def test_median_of_an_even_count_is_the_mean_of_the_middle_two() -> None:
    spec = spec_of(("main", [iv(100, 3), iv(200, 3)]), ("cooldown", [iv(60, 1), iv(400, 1)]))
    assert measure("median_interval_s", spec) == 150  # 60 100 200 400


def test_the_interval_metrics_of_nothing_are_zero() -> None:
    empty = {"segments": []}
    assert measure("longest_interval_s", empty) == 0
    assert measure("median_interval_s", empty) == 0


@pytest.mark.parametrize("side", ["low", "high"])
def test_longest_interval_pair(side: str) -> None:
    rule = f"longest_interval_{'short' if side == 'low' else 'long'}"
    bound = 240 if side == "low" else 600
    profile = synthetic(rule, bound)

    def make(x: int) -> dict[str, Any]:
        return simple(("warmup", 60, 1), ("main", x, 4), ("cooldown", 60, 1))

    inside, outside = (bound, bound - 1) if side == "low" else (bound, bound + 1)
    assert hits(make(inside), profile) == []
    assert hits(make(outside), profile) == [rule]


@pytest.mark.parametrize("side", ["low", "high"])
def test_median_interval_pair(side: str) -> None:
    rule = f"median_interval_{'short' if side == 'low' else 'long'}"
    bound = 120 if side == "low" else 300
    profile = synthetic(rule, bound)

    def make(x: int) -> dict[str, Any]:
        # 30 30 x x x: the median is x
        return spec_of(("warmup", [iv(30, 1)]), ("main", [iv(x, 4)] * 3), ("cooldown", [iv(30, 1)]))

    inside, outside = (bound, bound - 1) if side == "low" else (bound, bound + 1)
    assert hits(make(inside), profile) == []
    assert hits(make(outside), profile) == [rule]


# --- sprints (corpus acc_count) -----------------------------------------------------------------


def test_sprints_are_the_acceleration_intervals() -> None:
    spec = spec_of(
        ("warmup", [iv(60, 1, acc=True)]),
        ("main", [iv(20, 6, acc=True), iv(40, 2), iv(20, 7, acc=True), iv(30, 5, acc=False)]),
        ("cooldown", [iv(60, 1)]),
    )
    assert measure("sprint_count", spec) == 3  # every segment counts, as in the corpus
    assert measure("sprint_count", simple(("main", 60, 3), ("cooldown", 60, 1))) == 0


@pytest.mark.parametrize("side", ["low", "high"])
def test_sprint_count_pair(side: str) -> None:
    rule = "sprints_few" if side == "low" else "sprints_many"
    bound = 2 if side == "low" else 4
    profile = synthetic(rule, bound)

    def make(n: int) -> dict[str, Any]:
        sprints = [iv(10, 6, acc=True), iv(30, 2)] * n
        return spec_of(
            ("warmup", [iv(60, 1)]), ("main", [*sprints, iv(60, 3)]), ("cooldown", [iv(60, 1)])
        )

    inside, outside = (bound, bound - 1) if side == "low" else (bound, bound + 1)
    assert hits(make(inside), profile) == []
    assert hits(make(outside), profile) == [rule]


# --- climbs (corpus climb_pct) ------------------------------------------------------------------


def test_a_climb_is_seated_zone_3_plus_at_80_rpm_or_less() -> None:
    cases = [
        (iv(100, 3, rpm=[60, 70]), True),
        (iv(100, 3, rpm=[60, 80]), True),  # the cadence limit is inclusive
        (iv(100, 7, rpm=[40, 55]), True),
        (iv(100, 3, rpm=[60, 81]), False),
        (iv(100, 2, rpm=[60, 70]), False),  # below zone 3
        (iv(100, 4, rpm=[60, 70], standing=True), False),
        (iv(100, 4, rpm=[0, 0]), False),  # no cadence set
        (iv(100, 4), False),
        (iv(100, 4, rpm="beat"), False),  # from the music: no number to compare
    ]
    for interval, climbs in cases:
        spec = spec_of(("main", [interval, iv(100, 1)]))
        assert measure("climb_time_share", spec) == (0.5 if climbs else 0.0), interval


def test_climb_share_is_a_fraction_of_the_whole_timeline() -> None:
    spec = spec_of(
        ("warmup", [iv(100, 3, rpm=[70, 75])]),  # a warm-up climb counts, as in the corpus
        ("main", [iv(200, 4, rpm=[60, 70]), iv(100, 4, rpm=[90, 100])]),
        ("cooldown", [iv(100, 1)]),
    )
    assert measure("climb_time_share", spec) == pytest.approx(300 / 500)


@pytest.mark.parametrize("side", ["low", "high"])
def test_climb_share_pair(side: str) -> None:
    rule = "climb_share_low" if side == "low" else "climb_share_high"
    bound = 0.1 if side == "low" else 0.3
    profile = synthetic(rule, bound)

    def make(climb: int) -> dict[str, Any]:
        return spec_of(
            ("warmup", [iv(100, 1)]),
            ("main", [iv(climb, 4, rpm=[60, 70]), iv(800 - climb, 2)]),
            ("cooldown", [iv(100, 1)]),
        )

    scale = 1000
    inside = round(bound * scale)
    outside = inside - 1 if side == "low" else inside + 1
    assert hits(make(inside), profile) == []
    assert hits(make(outside), profile) == [rule]


# --- work to rest (corpus work_rest_ratio) ------------------------------------------------------


def test_work_rest_is_zone_4_plus_over_zone_2_or_less_in_the_main_body() -> None:
    spec = spec_of(
        ("warmup", [iv(600, 1), iv(60, 5)]),  # left out
        ("main", [iv(300, 4), iv(100, 2), iv(200, 6), iv(100, 1), iv(500, 3)]),
        ("recovery", [iv(100, 2)]),  # a recovery segment is body
        ("cooldown", [iv(600, 1)]),  # left out
    )
    assert measure("work_rest_ratio", spec) == pytest.approx(500 / 300)


def test_work_rest_is_undefined_without_rest_and_its_rules_stay_silent() -> None:
    spec = simple(("warmup", 300, 1), ("main", 600, 5), ("cooldown", 120, 1))
    assert measure("work_rest_ratio", spec) is None
    for rule in ("work_rest_low", "work_rest_high"):
        assert hits(spec, synthetic(rule, 1.0)) == []
    assert measure("work_rest_ratio", simple(("main", 600, 3), ("cooldown", 60, 1))) is None


def test_no_work_is_a_ratio_of_zero() -> None:
    spec = simple(("main", 300, 2), ("main", 300, 3), ("cooldown", 60, 1))
    assert measure("work_rest_ratio", spec) == 0
    assert hits(spec, synthetic("work_rest_low", 0.5)) == ["work_rest_low"]


@pytest.mark.parametrize("side", ["low", "high"])
def test_work_rest_pair(side: str) -> None:
    rule = "work_rest_low" if side == "low" else "work_rest_high"
    bound = 1.0 if side == "low" else 2.0
    profile = synthetic(rule, bound)

    def make(work: int) -> dict[str, Any]:
        return simple(("warmup", 100, 1), ("main", work, 4), ("main", 300, 2), ("cooldown", 100, 1))

    inside = round(bound * 300)
    outside = inside - 1 if side == "low" else inside + 1
    assert hits(make(inside), profile) == []
    assert hits(make(outside), profile) == [rule]


# --- intensity factor (corpus intensity_factor) -------------------------------------------------


def test_intensity_factor_is_the_root_of_tss_over_hours_times_100() -> None:
    steady = simple(("main", 3000, 4), ("cooldown", 600, 4))  # one hour in zone 4
    assert measure("intensity_factor", steady) == pytest.approx(workout.ZONE_IF[4])
    mixed = simple(("main", 1800, 2), ("main", 1200, 5), ("cooldown", 600, 1))
    total = 3600
    expected = math.sqrt(workout.tss(mixed) / (total / 3600 * 100))
    assert measure("intensity_factor", mixed) == pytest.approx(expected)
    assert measure("intensity_factor", {"segments": []}) == 0


def _if_spec(hard: int) -> dict[str, Any]:
    return simple(
        ("warmup", 300, 1), ("main", 3000 - hard, 3), ("main", hard, 5), ("cooldown", 300, 1)
    )


@pytest.mark.parametrize("side", ["low", "high"])
def test_intensity_factor_pair(side: str) -> None:
    rule = "intensity_factor_low" if side == "low" else "intensity_factor_high"
    bound = 0.8
    profile = synthetic(rule, bound)
    values = [(h, measure("intensity_factor", _if_spec(h))) for h in range(0, 3001, 10)]
    assert all(v is not None for _, v in values)
    if side == "low":  # more hard time raises the factor: the first one at or above the bound
        inside = next(h for h, v in values if v is not None and v >= bound)
        outside = inside - 10
    else:  # the last one at or below the bound
        inside = max(h for h, v in values if v is not None and v <= bound)
        outside = inside + 10
    assert 0 <= outside <= 3000
    assert hits(_if_spec(inside), profile) == []
    assert hits(_if_spec(outside), profile) == [rule]


# --- severity, overrides and the cap -------------------------------------------------------------


@pytest.mark.parametrize("rule", sorted(RATIONALE))
def test_every_new_rule_loads_with_warn_and_with_range_10_info(rule: str) -> None:
    side = E.RULE_SPECS[rule].side
    warn = synthetic(rule, 1.0).rules[0]
    info = synthetic(rule, 1.0, severity="info").rules[0]
    assert (warn.severity, warn.basis) == ("warn", "p5" if side == "low" else "p95")
    assert (info.severity, info.basis) == ("info", "range_10")


def test_a_new_rule_is_a_note_and_the_brief_lowers_it() -> None:
    spec = simple(("warmup", 60, 1), ("main", 100, 4), ("cooldown", 60, 1))
    profile = synthetic("longest_interval_short", 240)
    (hit,) = evaluate(spec, Brief(), profile)
    assert (hit.severity, hit.explained_by_brief, hit.metric) == (
        "warn",
        False,
        "longest_interval_s",
    )
    assert hit.value == 100
    assert hit.bounds == (240, None)

    class Sprinting(Brief):
        sprints = 2

    (explained,) = evaluate(spec, Sprinting(), profile)
    assert (explained.severity, explained.explained_by_brief) == ("info", True)


def test_a_rule_that_does_not_match_its_metric_is_refused() -> None:
    raw = json.loads(
        json.dumps(
            {
                "schema": 1, "id": "cycling.interval.45", "version": 1, "sport": "cycling",
                "class_type": "interval", "label_minutes": 45, "evidence": "strong", "n": 30,
                "source": E.SOURCE, "snapshot": "2026-11-05",
                "rules": [
                    {
                        "rule": "sprints_few", "metric": "climb_time_share", "low": 2, "high": None,
                        "basis": "p5", "severity": "warn", "rationale_id": "sprints_present",
                        "brief_overrides": [],
                    }
                ],
            }
        )
    )  # fmt: skip
    with pytest.raises(Exception, match="measures sprint_count"):
        parse_profile(json.dumps(raw).encode())


def test_a_sprint_sentence_is_refused_in_a_running_profile() -> None:
    raw: dict[str, Any] = json.loads(
        json.dumps(
            {
                "schema": 1, "id": "running.interval.30", "version": 1, "sport": "running",
                "class_type": "interval", "label_minutes": 30, "evidence": "moderate", "n": 16,
                "source": E.SOURCE, "snapshot": "2026-11-05",
                "rules": [
                    {
                        "rule": "sprints_few", "metric": "sprint_count", "low": 2, "high": None,
                        "basis": "range_10", "severity": "info", "rationale_id": "sprints_present",
                        "brief_overrides": [],
                    }
                ],
            }
        )
    )  # fmt: skip
    with pytest.raises(Exception, match="not worded for running"):
        parse_profile(json.dumps(raw).encode())


# --- how the model and the page state the numbers ----------------------------------------------


@pytest.mark.parametrize(
    ("metric", "value", "text"),
    [
        ("longest_interval_s", 330, "5:30"),
        ("median_interval_s", 95, "1:35"),
        ("climb_time_share", 0.236, "24%"),
        ("sprint_count", 3, "3"),
        ("work_rest_ratio", 1.5, "1.5"),
        ("intensity_factor", 0.85, "0.85"),
    ],
)
def test_the_prompt_states_the_new_numbers(metric: str, value: float, text: str) -> None:
    assert guidance._number(metric, value) == text  # pyright: ignore[reportPrivateUsage]


def test_the_ratio_and_the_factor_round_trip_through_a_hit_entry() -> None:
    spec = simple(("main", 600, 3), ("main", 300, 2), ("cooldown", 60, 1))
    profile = synthetic("work_rest_low", 1.0)
    (hit,) = evaluate(spec, Brief(), profile)
    entry = guidance.hit_entry(hit)
    assert entry["rule"] == "work_rest_low"
    assert entry["value"] == 0
    assert entry["note"] == RATIONALES["work_rest_enough"]

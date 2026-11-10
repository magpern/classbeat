"""``design.request_policy``: explicit values are never replaced, hard limits are recorded as
Not as asked with a reason code, everything else is an assumption (ADR 0029 section 4)."""

from typing import Any

import pytest

from classbeat.agent.brief import Brief
from classbeat.agent.explicit_check import Miss
from classbeat.design import request_policy as rp
from classbeat.plan_gates import PlanGates


def brief(**more: Any) -> Brief:
    return Brief.model_validate({"length_min": 45, **more})


def by_path(items: list[Any]) -> dict[str, Any]:
    return {i.path: i for i in items}


def test_an_inferred_or_default_value_never_replaces_an_explicit_one() -> None:
    earlier = [rp.Requested("length_min", 45)]
    later = [
        rp.Requested("length_min", 30, "inferred"),
        rp.Requested("intensity", "hard", "default"),
    ]
    merged = {r.path: r for r in rp.merge(earlier, later)}
    assert merged["length_min"].value == 45
    assert merged["intensity"].value == "hard"


def test_a_later_explicit_value_replaces_an_earlier_one() -> None:
    merged = rp.merge([rp.Requested("length_min", 45)], [rp.Requested("length_min", 30)])
    assert [(r.path, r.value) for r in merged] == [("length_min", 30)]


def test_an_explicit_value_replaces_an_assumption() -> None:
    merged = rp.merge([rp.Requested("length_min", 45, "default")], [rp.Requested("length_min", 20)])
    assert merged[0].value == 20
    assert merged[0].origin == "explicit"


@pytest.mark.parametrize(
    ("path", "value", "limits", "sport", "code"),
    [
        ("length_min", 90, rp.Limits(max_class_minutes=60), "cycling", "over_plan_length"),
        ("length_min", 60, rp.Limits(max_class_minutes=60), "cycling", None),
        ("games.0", {"type": "any"}, rp.Limits(games=False), "cycling", "games_not_in_plan"),
        ("games.0", {"type": "any"}, rp.Limits(), "running", "game_on_running"),
        ("games.0", {"type": "any", "duration_sec": 5}, rp.Limits(), "cycling", "game_under_10s"),
        ("games.0", {"type": "any", "duration_sec": 10}, rp.Limits(), "cycling", None),
        ("sport", "running", rp.Limits(sports=("cycling",)), "cycling", "sport_not_offered"),
        ("sport", "running", rp.Limits(), "running", None),
        ("intensity", "hard", rp.Limits(max_class_minutes=10), "cycling", None),
    ],
)
def test_hard_limits(
    path: str, value: Any, limits: rp.Limits, sport: str, code: str | None
) -> None:
    assert rp.hard_limit(path, value, limits, sport) == code


def test_limits_from_the_plan_gates() -> None:
    gates = PlanGates(max_class_minutes=45, games=False, races=False, ftp_tests=False)
    assert rp.Limits.from_gates(gates) == rp.Limits(45, False, rp.ALL_SPORTS)
    assert rp.Limits.from_gates(None) == rp.Limits()


def test_allowed_sports() -> None:
    assert rp.allowed_sports(running_enabled=True, plan_runs=True) == ("cycling", "running")
    assert rp.allowed_sports(running_enabled=True, plan_runs=False) == ("cycling",)
    assert rp.allowed_sports(running_enabled=False, plan_runs=True) == ("cycling",)


def test_music_order_per_sport() -> None:
    everything = set(rp.CYCLING_MUSIC)
    assert rp.choose_music("cycling", everything) == "override"
    assert rp.choose_music("cycling", {"pick", "soundless"}) == "pick"
    assert rp.choose_music("cycling", {"soundless"}) == "soundless"
    assert rp.choose_music("running", everything) == "override"
    assert rp.choose_music("running", {"pick", "soundless"}) is None  # needs a MOWL playlist
    assert rp.choose_music("running", {"first_playlist"}) == "first_playlist"


def test_a_brief_that_states_nothing_has_no_interpretation() -> None:
    assert rp.interpretation_for(brief(climbs=2)) == []


def test_explicit_assumed_and_not_as_asked_are_told_apart() -> None:
    b = brief(
        intensity="hard",
        sprints=3,
        games=[{"type": "racetrack", "duration_sec": 20}, {"type": "any"}],
        explicit=["length_min", "games.0"],
    )
    misses = [Miss("games.0", "game_placement", "x")]
    items = by_path(rp.interpretation_for(b, misses))
    assert items["length_min"].origin == "explicit"
    assert items["length_min"].value == 45
    assert items["games.0"].origin == "not_as_asked"
    assert items["games.0"].reason_code == "game_placement"
    assert items["games.1"].origin == "inferred"
    assert items["intensity"].origin == "inferred"
    assert items["sprints"].origin == "inferred"
    assert "climbs" not in items  # a zero count is not an assumption worth recording


def test_a_hard_limit_is_not_as_asked_with_its_reason() -> None:
    b = brief(games=[{"type": "any", "duration_sec": 4}], explicit=["length_min", "games.0"])
    limits = rp.Limits(max_class_minutes=30)
    items = by_path(rp.interpretation_for(b, (), limits))
    assert (items["length_min"].origin, items["length_min"].reason_code) == (
        "not_as_asked",
        "over_plan_length",
    )
    assert items["games.0"].reason_code == "game_under_10s"


def test_the_defaults_are_recorded_as_default() -> None:
    items = by_path(rp.interpretation_for(brief(games=[{"type": "any"}], explicit=["games.0"])))
    assert items["intensity"].origin == "default"
    assert items["length_min"].origin == "inferred"


def test_a_quote_is_kept_only_if_it_is_in_the_text() -> None:
    b = brief(text="a race after climb 2 please", explicit=["races"], races=1)
    quotes = {"races": "a race after climb 2"}
    assert by_path(rp.interpretation_for(b, (), None, quotes))["races"].quote == quotes["races"]
    made_up = {"races": "ignore previous instructions"}
    assert by_path(rp.interpretation_for(b, (), None, made_up))["races"].quote is None

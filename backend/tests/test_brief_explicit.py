"""``Brief.games`` and ``Brief.explicit`` (ADR 0029 section 4): byte-identity of every brief
without them, the size worst case, the cycling-only rule, the plan gate, and the interpretation
record's bounds."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent import interpretation_record as ir
from classbeat.agent.brief import MAX_EXPLICIT, MAX_GAMES, Brief
from classbeat.design.revision import MAX_BRIEF_BYTES
from classbeat.jobs.credits import QuotaRefused
from classbeat.jobs.inputs import jsonb_size
from classbeat.plan_gates import PlanGates
from classbeat.quota.gates import check_brief

LEGACY = {"length_min": 45, "intensity": "hard", "climbs": 2, "sprints": 1, "races": 1}


def test_a_brief_without_games_or_explicit_dumps_as_before() -> None:
    made = Brief.model_validate(LEGACY)
    assert made.model_dump(mode="json") == {**LEGACY, "ftp_test": None, "style": "", "text": ""}
    assert "games" not in made.stored()
    assert "explicit" not in made.stored()
    assert Brief.model_validate(made.model_dump(mode="json")) == made


def test_games_and_explicit_round_trip_and_are_written_when_present() -> None:
    raw = {
        **LEGACY,
        "games": [
            {"type": "racetrack", "placement": {"position": "late"}, "duration_sec": 30},
            {"type": "any", "placement": {"relation": "after", "anchor": "climb", "ordinal": 2}},
        ],
        "explicit": ["length_min", "games.1"],
    }
    made = Brief.model_validate(raw)
    dumped = made.stored()
    assert dumped["explicit"] == ["length_min", "games.1"]
    assert dumped["games"][0] == {
        "type": "racetrack",
        "placement": {"position": "late"},
        "duration_sec": 30,
    }
    assert Brief.model_validate(dumped) == made


def test_the_worst_case_fits_the_class_brief() -> None:
    placement = {"relation": "replaces", "anchor": "interval", "ordinal": 20}
    worst = Brief.model_validate(
        {
            "length_min": 180,
            "intensity": "moderate",
            "climbs": 10,
            "sprints": 20,
            "races": 5,
            "ftp_test": 20,
            "class_type": "ftp_test",
            "style": "s" * 40,
            "text": "t" * 2000,
            "games": [{"type": "any", "placement": placement, "duration_sec": 600}] * MAX_GAMES,
            "explicit": [
                "sport",
                "length_min",
                "intensity",
                "climbs",
                "sprints",
                "races",
                "ftp_test",
                "class_type",
                *[f"games.{n}" for n in range(MAX_GAMES)],
            ],
        }
    )
    assert len(worst.explicit) <= MAX_EXPLICIT
    assert jsonb_size(worst.stored()) <= MAX_BRIEF_BYTES


@pytest.mark.parametrize(
    "bad",
    [
        {"games": [{"type": "any"}] * 7},
        {"games": [{"type": "tabata"}]},
        {"games": [{"type": "any", "duration_sec": 0}]},
        {"games": [{"type": "any", "placement": {"position": "nowhere"}}]},
        {
            "games": [
                {"type": "any", "placement": {"relation": "after", "anchor": "x", "ordinal": 1}}
            ]
        },
        {
            "games": [
                {"type": "any", "placement": {"relation": "after", "anchor": "climb", "ordinal": 0}}
            ]
        },
        {"explicit": ["text"]},
        {"explicit": ["games.0"]},  # the brief has no game 0
        {"explicit": ["length_min", "length_min"]},
        {"explicit": ["length_min"] * 21},
    ],
)
def test_a_malformed_games_or_explicit_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Brief.model_validate({"length_min": 30, **bad})


def test_games_and_cycling_only_paths_are_cycling_only() -> None:
    with pytest.raises(ValidationError):
        Brief.model_validate({"sport": "running", "length_min": 30, "games": [{"type": "any"}]})
    with pytest.raises(ValidationError):
        Brief.model_validate({"sport": "running", "length_min": 30, "explicit": ["sprints"]})
    ok = Brief.model_validate({"sport": "running", "length_min": 30, "explicit": ["length_min"]})
    assert ok.stored()["explicit"] == ["length_min"]


def gates(*, games: bool) -> PlanGates:
    return PlanGates(max_class_minutes=60, games=games, races=True, ftp_tests=True)


def test_check_brief_refuses_games_on_a_plan_without_games() -> None:
    brief = Brief.model_validate({"length_min": 30, "games": [{"type": "any"}]}).stored()
    with pytest.raises(QuotaRefused) as raised:
        check_brief(gates(games=False), brief, "free")
    assert raised.value.feature == "games"
    check_brief(gates(games=True), brief, "pro")
    check_brief(gates(games=False), Brief(length_min=30).stored(), "free")


# --- the interpretation record ----------------------------------------------------------------


def item(**more: Any) -> ir.InterpretationItem:
    return ir.InterpretationItem.model_validate(
        {"path": "length_min", "origin": "explicit", **more}
    )


def test_a_reason_code_belongs_to_not_as_asked_only() -> None:
    with pytest.raises(ValidationError):
        item(reason_code="length_off")
    with pytest.raises(ValidationError):
        item(origin="not_as_asked")
    assert item(origin="not_as_asked", reason_code="length_off").section() == "not_as_asked"
    assert item().section() == "as_asked"
    assert item(origin="inferred").section() == "assumed"


def test_a_value_and_a_list_are_bounded() -> None:
    with pytest.raises(ValidationError):
        item(value="x" * (ir.MAX_VALUE_BYTES + 1))
    with pytest.raises(ValidationError):
        ir.attach({}, [item()] * (ir.MAX_ITEMS + 1))
    with pytest.raises(ValidationError):
        item(path="Length Min")


def test_attach_adds_the_items_and_leaves_an_empty_one_alone() -> None:
    provenance = {"origin": "design_draft"}
    assert ir.attach(provenance, []) is provenance
    out = ir.attach(provenance, [item(value=45)])
    assert out["interpretation"] == [{"path": "length_min", "origin": "explicit", "value": 45}]
    json.dumps(out)


def test_a_quote_is_a_substring_of_the_text_or_nothing() -> None:
    assert ir.keep_quote("a race", "put a race here") == "a race"
    assert ir.keep_quote("a game", "put a race here") is None
    assert ir.keep_quote("", "x") is None
    assert ir.keep_quote("x" * (ir.MAX_QUOTE + 1), "x" * 500) is None

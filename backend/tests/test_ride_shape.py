"""The shape of the ride in the brief (#1485): the schema's rules, a brief without a shape stays
byte-identical, the size bound, the stated layer, the model input and the class type. Pure."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent import model_input
from classbeat.agent.brief import CYCLING_ONLY_BRIEF, Brief
from classbeat.agent.brief_input import brief_for_model
from classbeat.agent.ride_shape import (
    MAX_SECTIONS,
    MAX_SHAPE_BYTES,
    CountRange,
    ShapeSection,
    fitting,
    largest,
    shape_bytes,
)
from classbeat.agent.stated_layer import LAYER_KEYS, StatedLayer, applied, layer_of
from classbeat.design.selection import resolve_class_type
from classbeat.jobs.inputs import jsonb_size

LEGACY = {"length_min": 45, "intensity": "hard", "climbs": 2, "sprints": 1, "races": 1}
EFFORTS = {"kind": "efforts", "count": {"min": 5, "max": 7}, "length": "short", "load": "heavy"}
SHAPE = [{"kind": "warmup", "trend": "rising"}, {"kind": "climb"}, EFFORTS, {"kind": "cooldown"}]


def brief(**fields: Any) -> Brief:
    return Brief.model_validate({"length_min": 45, **fields})


# --- the schema ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "warmup", "count": {"min": 1, "max": 2}},  # a count only on climb and efforts
        {"kind": "flat", "count": {"min": 3, "max": 3}},
        {"kind": "efforts", "count": {"min": 7, "max": 5}},  # min <= max
        {"kind": "efforts", "count": {"min": 0, "max": 5}},  # 1..20
        {"kind": "efforts", "count": {"min": 5, "max": 21}},
        {"kind": "climb", "minutes": 0},
        {"kind": "climb", "minutes": 121},
        {"kind": "sprint"},  # a closed kind
        {"kind": "efforts", "load": "medium"},
        {"kind": "efforts", "note": "any text"},  # closed values only: nothing else
    ],
)
def test_a_section_refuses_what_the_schema_does_not_hold(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ShapeSection.model_validate(bad)


def test_a_climb_and_an_efforts_section_take_a_count_and_an_exact_number_is_a_range() -> None:
    assert ShapeSection.model_validate({"kind": "climb", "count": {"min": 3, "max": 3}})
    assert CountRange(min=7, max=7)
    assert ShapeSection.model_validate(EFFORTS).count == CountRange(min=5, max=7)


def test_a_section_is_stored_without_its_absent_fields() -> None:
    assert ShapeSection.model_validate({"kind": "flat"}).model_dump(mode="json") == {"kind": "flat"}
    assert brief(shape=SHAPE).model_dump(mode="json")["shape"][2] == EFFORTS


def test_a_shape_has_at_most_twelve_sections() -> None:
    assert brief(shape=[{"kind": "flat"}] * MAX_SECTIONS)
    with pytest.raises(ValidationError):
        brief(shape=[{"kind": "flat"}] * (MAX_SECTIONS + 1))


def test_a_shape_larger_than_the_stored_budget_is_refused_and_fitting_keeps_the_leading_ones() -> (
    None
):
    full = ShapeSection.model_validate(
        {
            "kind": "efforts",
            "trend": "rising",
            "effort": "hard",
            "count": {"min": 20, "max": 20},
            "length": "short",
            "load": "heavy",
            "minutes": 120,
        }
    )
    with pytest.raises(ValidationError, match="too large"):
        brief(shape=[full] * MAX_SECTIONS)
    kept = fitting([full] * MAX_SECTIONS)
    assert 0 < len(kept) < MAX_SECTIONS
    assert shape_bytes(kept) <= MAX_SHAPE_BYTES < shape_bytes([*kept, full])
    assert largest() == kept
    assert fitting([]) == []


# --- the brief ----------------------------------------------------------------------------------


def test_a_brief_without_a_shape_is_byte_identical_to_before() -> None:
    made = Brief.model_validate(LEGACY)
    assert made.model_dump(mode="json") == {**LEGACY, "ftp_test": None, "style": "", "text": ""}
    assert "shape" not in made.stored()
    assert json.dumps(made.stored(), sort_keys=True) == json.dumps(
        {**LEGACY, "ftp_test": None, "style": "", "text": "", "sport": "cycling"}, sort_keys=True
    )
    assert Brief.model_validate(made.model_dump(mode="json")) == made


def test_a_shape_round_trips_in_order() -> None:
    made = brief(shape=SHAPE, explicit=["shape"])
    dumped = made.stored()
    assert [s["kind"] for s in dumped["shape"]] == ["warmup", "climb", "efforts", "cooldown"]
    assert Brief.model_validate(dumped) == made


def test_explicit_may_name_a_shape_the_brief_has_and_only_that() -> None:
    assert brief(shape=SHAPE, explicit=["length_min", "shape"]).explicit == ["length_min", "shape"]
    with pytest.raises(ValidationError, match="does not have"):
        brief(explicit=["shape"])


def test_a_running_brief_refuses_a_shape_even_an_empty_one() -> None:
    assert "shape" in CYCLING_ONLY_BRIEF
    with pytest.raises(ValidationError, match="shape"):
        Brief.model_validate({"sport": "running", "length_min": 30, "shape": SHAPE})
    with pytest.raises(ValidationError, match="shape"):
        Brief.model_validate({"sport": "running", "length_min": 30, "shape": []})
    running = Brief.model_validate({"sport": "running", "length_min": 30})
    assert "shape" not in running.stored()


def test_the_worst_case_brief_with_the_largest_shape_fits_the_class() -> None:
    from classbeat.design import revision
    from classbeat.design.interpret_policy import worst_case_brief

    worst = worst_case_brief("a" * 2000)
    assert len(worst.shape) == len(largest())
    assert "shape" in worst.explicit
    assert jsonb_size(worst.stored()) <= revision.MAX_BRIEF_BYTES


# --- the stated layer ---------------------------------------------------------------------------


def test_the_layer_carries_the_shape_and_a_layer_without_one_is_unchanged() -> None:
    assert "shape" in LAYER_KEYS
    made = brief(shape=SHAPE, explicit=["shape"])
    layer = layer_of(made)
    assert layer["shape"][2] == EFFORTS
    assert StatedLayer.model_validate(layer).shape == made.shape
    assert "shape" not in layer_of(brief())  # byte-identical to a layer made before the shape
    laid = Brief.model_validate(applied(brief(text="x").stored(), layer))
    assert laid.shape == made.shape
    assert laid.explicit == ["shape"]


# --- what the design stages are told ------------------------------------------------------------


def test_the_model_gets_the_shape_and_no_count_nobody_gave() -> None:
    plain = brief_for_model(brief())
    assert plain == {
        "length_min": 45,
        "intensity": "moderate",
        "ftp_test": None,
        "style": "",
        "text": "",
    }
    shaped = brief_for_model(brief(shape=SHAPE))
    assert shaped["shape"] == brief(shape=SHAPE).model_dump(mode="json")["shape"]
    assert not {"climbs", "sprints", "races"} & shaped.keys()
    assert model_input.brief_for_model is brief_for_model


def test_a_count_is_sent_when_it_is_more_than_zero_or_stated() -> None:
    got = brief_for_model(brief(climbs=3, sprints=0, explicit=["races", "sprints"]))
    assert (got["climbs"], got["sprints"], got["races"]) == (3, 0, 0)  # stated zeros stay
    only = brief_for_model(brief(climbs=0, sprints=2))
    assert only["sprints"] == 2
    assert "climbs" not in only
    assert "races" not in only


# --- the class type -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ([], "plain"),
        ([{"kind": "climb"}, {"kind": "flat"}], "plain"),
        ([{"kind": "warmup"}, EFFORTS], "interval"),
        ([{"kind": "efforts"}], "interval"),
    ],
)
def test_a_shape_with_an_efforts_section_derives_an_interval_class(
    shape: list[dict[str, Any]], expected: str
) -> None:
    choice = resolve_class_type(brief(shape=shape))
    assert (choice.class_type, choice.derived) == (expected, True)
    assert resolve_class_type(brief(shape=shape, class_type="journey")).class_type == "journey"

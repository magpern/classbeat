"""The ride's shape in a reading and in a revise (#1485): ``design.shape_policy`` through
``interpret_policy.resolve`` and ``revise_layer_policy.layer``, the design's own items, and what the
revise interpreter is told. Pure: no database, no network, no model."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent.brief import Brief
from classbeat.agent.interpret_input import current_values, first_message
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.ride_shape import ShapeSection
from classbeat.agent.stated_layer import layer_of
from classbeat.design import revise_stated
from classbeat.design.interpret_policy import Offer, resolve
from classbeat.design.request_policy import interpretation_for
from classbeat.design.revise_layer_policy import layer

REQUEST = (
    "A 60 min class that starts off slow and builds up. A hilly section, 5-7 short heavy bursts, "
    "back on flats, finishing off with a few bursts of power."
)
WARMUP = {"kind": "warmup", "trend": "rising", "origin": "inferred", "quote": "builds up"}
CLIMB = {"kind": "climb", "origin": "explicit", "quote": "hilly section"}
EFFORTS = {
    "kind": "efforts",
    "effort": "hard",
    "count": {"min": 5, "max": 7},
    "length": "short",
    "load": "heavy",
    "origin": "explicit",
    "quote": "5-7 short heavy bursts",
}
FLAT = {"kind": "flat", "origin": "explicit"}
FINISH = {"kind": "efforts", "count": {"min": 2, "max": 4}, "origin": "explicit", "quote": "a few"}
SHAPE = [WARMUP, CLIMB, EFFORTS, FLAT, FINISH]


def read(**fields: Any) -> InterpretedRequest:
    return InterpretedRequest.model_validate(fields)


def by_path(items: list[Any]) -> dict[str, Any]:
    return {i.path: i for i in items}


def closed(section: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in section.items() if k not in ("origin", "quote")}


# --- the interpreter's schema -------------------------------------------------------------------


def test_the_reading_refuses_a_section_the_brief_could_not_hold() -> None:
    with pytest.raises(ValidationError):
        read(shape=[{"kind": "flat", "count": {"min": 1, "max": 2}, "origin": "explicit"}])
    with pytest.raises(ValidationError):
        read(shape=[{"kind": "efforts", "count": {"min": 9, "max": 3}, "origin": "explicit"}])
    with pytest.raises(ValidationError):
        read(shape=[{"kind": "flat", "origin": "explicit"}] * 13)


def test_a_quote_is_kept_only_if_it_is_in_the_request() -> None:
    got = read(shape=[CLIMB, {**FLAT, "quote": "invented words"}]).sanitised(REQUEST, 0)
    assert [s.quote for s in got.shape] == ["hilly section", None]


def test_the_readings_name_each_section_in_order_with_its_closed_value() -> None:
    readings = [r for r in read(shape=SHAPE).readings() if r.path.startswith("shape.")]
    assert [r.path for r in readings] == [f"shape.{n}" for n in range(5)]
    assert readings[2].value == closed(EFFORTS)
    assert [r.origin for r in readings] == [
        "inferred",
        "explicit",
        "explicit",
        "explicit",
        "explicit",
    ]


# --- a new request ------------------------------------------------------------------------------


def test_a_request_in_parts_keeps_the_order_the_ranges_and_one_item_per_section() -> None:
    got = resolve(
        read(length_min={"value": 60, "origin": "explicit"}, shape=SHAPE), REQUEST, Offer()
    )
    assert [s.kind for s in got.brief.shape] == ["warmup", "climb", "efforts", "flat", "efforts"]
    assert got.brief.shape[2].count is not None
    assert (got.brief.shape[2].count.min, got.brief.shape[2].count.max) == (5, 7)
    assert (got.brief.climbs, got.brief.sprints) == (0, 0)  # never derived from the shape
    assert got.brief.explicit == ["length_min", "shape"]
    items = by_path(got.items)
    assert [p for p in items if p.startswith("shape.")] == [f"shape.{n}" for n in range(5)]
    assert (items["shape.0"].origin, items["shape.0"].value) == ("inferred", closed(WARMUP))
    assert (items["shape.2"].origin, items["shape.2"].quote) == ("explicit", EFFORTS["quote"])
    assert items["shape.2"].value == closed(EFFORTS)
    assert "climbs" not in items
    assert "sprints" not in items


def test_an_inferred_shape_is_not_stated() -> None:
    got = resolve(read(shape=[WARMUP, {**FLAT, "origin": "inferred"}]), REQUEST, Offer())
    assert len(got.brief.shape) == 2
    assert "shape" not in got.brief.explicit


def test_a_request_with_no_parts_has_no_shape_and_no_shape_items() -> None:
    got = resolve(read(climbs={"value": 3, "origin": "explicit"}), REQUEST, Offer())
    assert got.brief.shape == []
    assert "shape" not in got.brief.stored()
    assert not [i for i in got.items if i.path.startswith("shape")]


def test_a_class_wide_range_is_a_single_efforts_section() -> None:
    efforts = {"kind": "efforts", "count": {"min": 5, "max": 7}, "origin": "explicit"}
    got = resolve(read(shape=[efforts]), "5-7 sprints", Offer())
    assert [s.model_dump(mode="json") for s in got.brief.shape] == [closed(efforts)]
    assert got.brief.sprints == 0


def test_the_shape_is_cycling_only_an_explicit_section_on_a_running_class_is_not_as_asked() -> None:
    both = Offer(sports=("cycling", "running"))
    got = resolve(
        read(
            sport={"value": "running", "origin": "explicit"},
            shape=[CLIMB, {**FLAT, "origin": "inferred"}],
        ),
        REQUEST,
        both,
    )
    assert got.brief.sport == "running"
    assert got.brief.shape == []
    items = by_path(got.items)
    assert (items["shape.0"].origin, items["shape.0"].reason_code) == (
        "not_as_asked",
        "cycling_only",
    )
    assert "shape.1" not in items  # an inferred section is dropped quietly


def test_sections_beyond_the_stored_budget_are_dropped_and_the_stated_ones_are_not_as_asked() -> (
    None
):
    big = {
        "kind": "efforts",
        "trend": "rising",
        "effort": "hard",
        "count": {"min": 20, "max": 20},
        "length": "short",
        "load": "heavy",
        "minutes": 120,
        "origin": "explicit",
    }
    quiet = {**big, "origin": "inferred"}
    got = resolve(read(shape=[big] * 6 + [quiet] * 6), REQUEST, Offer())
    kept = len(got.brief.shape)
    assert 0 < kept < 12
    items = by_path(got.items)
    assert items[f"shape.{kept - 1}"].origin == "explicit"
    assert (items[f"shape.{kept}"].origin, items[f"shape.{kept}"].reason_code) == (
        "not_as_asked",
        "shape_too_large",
    )
    assert f"shape.{11}" not in items  # the inferred ones that did not fit are not reported


# --- the design's own items ---------------------------------------------------------------------


def test_the_design_records_one_item_per_section() -> None:
    made = Brief.model_validate(
        {
            "length_min": 60,
            "text": REQUEST,
            "shape": [closed(s) for s in SHAPE],
            "explicit": ["shape"],
        }
    )
    items = by_path(interpretation_for(made))
    assert "shape" not in items
    assert [(i.origin, i.value) for p, i in items.items() if p.startswith("shape.")][2] == (
        "explicit",
        closed(EFFORTS),
    )
    unstated = made.model_copy(update={"explicit": []})
    assert {
        i.origin for p, i in by_path(interpretation_for(unstated)).items() if p.startswith("shape.")
    } == {"inferred"}
    assert interpretation_for(Brief.model_validate({"length_min": 45})) == []


# --- a revise -----------------------------------------------------------------------------------

BASE = Brief.model_validate(
    {
        "length_min": 60,
        "text": REQUEST,
        "shape": [closed(s) for s in SHAPE],
        "explicit": ["length_min", "shape"],
    }
)


def test_the_revise_interpreter_is_told_the_shape_as_it_stands() -> None:
    current = current_values(BASE)
    assert current["shape"] == BASE.model_dump(mode="json")["shape"]
    message = json.loads(
        first_message(
            "make it seven bursts",
            sports=("cycling",),
            max_class_minutes=None,
            games_allowed=True,
            music=(),
            current=current,
        )
    )
    assert message["current"]["shape"][2]["count"] == {"min": 5, "max": 7}
    ctx = revise_stated.context_for(BASE, "make it seven bursts", None)
    assert ctx.current is not None
    assert ctx.current["shape"] == current["shape"]


def test_make_it_seven_bursts_reports_the_whole_shape_and_the_layer_carries_it() -> None:
    seven = {**EFFORTS, "count": {"min": 7, "max": 7}, "quote": None}
    reported = [{**s, "origin": "inferred"} for s in (WARMUP, CLIMB)] + [seven]
    reported += [{**s, "origin": "inferred"} for s in (FLAT, FINISH)]
    got = layer(BASE, read(shape=reported), None)
    efforts = got.brief.shape[2].count
    assert efforts is not None
    assert (efforts.min, efforts.max) == (7, 7)
    assert [s.kind for s in got.brief.shape] == ["warmup", "climb", "efforts", "flat", "efforts"]
    assert got.brief.shape[4].count is not None
    assert (got.brief.shape[4].count.min, got.brief.shape[4].count.max) == (2, 4)  # unchanged
    assert got.brief.explicit == ["length_min", "shape"]  # a stated section: stated
    assert layer_of(got.brief)["shape"][2]["count"] == {"min": 7, "max": 7}
    changed = by_path(list(got.items))
    assert list(changed) == ["shape.2"]  # only the touched section: the rest keeps its record
    assert (changed["shape.2"].origin, changed["shape.2"].quote) == ("explicit", None)
    assert changed["shape.2"].value["count"] == {"min": 7, "max": 7}
    assert BASE.shape[2].count is not None
    assert BASE.shape[2].count.max == 7  # the class's own brief is untouched


def test_an_inferred_change_of_the_shape_is_no_longer_stated_and_an_echo_changes_nothing() -> None:
    seven = {**EFFORTS, "count": {"min": 7, "max": 7}, "origin": "inferred", "quote": None}
    others = [{**s, "origin": "inferred"} for s in (WARMUP, CLIMB, FLAT, FINISH)]
    changed = layer(BASE, read(shape=[*others[:2], seven, *others[2:]]), None)
    assert changed.brief.shape[2].count == ShapeSection.model_validate(closed(seven)).count
    assert changed.brief.explicit == ["length_min"]
    echo = layer(BASE, read(shape=[{**s, "origin": "inferred"} for s in SHAPE]), None)
    assert echo.brief == BASE


def test_no_shape_in_the_reading_leaves_the_shape_and_its_stated_path_as_they_are() -> None:
    got = layer(BASE, read(length_min={"value": 50, "origin": "explicit"}), None)
    assert got.brief.shape == BASE.shape
    assert got.brief.explicit == ["length_min", "shape"]


def test_a_revise_can_add_a_shape_to_a_class_without_one() -> None:
    plain = Brief.model_validate({"length_min": 45, "explicit": ["length_min"]})
    got = layer(plain, read(shape=[CLIMB, EFFORTS]), None)
    assert [s.kind for s in got.brief.shape] == ["climb", "efforts"]
    assert got.brief.explicit == ["length_min", "shape"]


def test_a_running_class_takes_no_shape_from_a_revise() -> None:
    run = Brief.model_validate({"sport": "running", "length_min": 30, "explicit": ["length_min"]})
    got = layer(run, read(shape=[CLIMB]), None)
    assert got.brief.shape == []
    assert [(i.path, i.reason_code) for i in got.items] == [("shape.0", "cycling_only")]


def test_a_shape_read_as_inferred_is_still_revised_through_the_reading() -> None:
    inferred = Brief.model_validate(
        {"length_min": 60, "text": REQUEST, "shape": [closed(s) for s in SHAPE]}
    )
    assert inferred.explicit == []
    seven = {**EFFORTS, "count": {"min": 7, "max": 7}, "origin": "explicit", "quote": None}
    reported = [*(s for s in SHAPE[:2]), seven, *SHAPE[3:]]
    got = layer(inferred, read(shape=reported), None)
    efforts = got.brief.shape[2].count
    assert efforts is not None
    assert (efforts.min, efforts.max) == (7, 7)
    assert got.brief.explicit == ["shape"]
    assert layer_of(got.brief)["shape"][2]["count"] == {"min": 7, "max": 7}


async def test_the_revise_reading_runs_for_a_shape_and_not_for_a_brief_that_states_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_run_stage(*args: Any, **kwargs: Any) -> Any:
        calls.append("run")
        raise RuntimeError("stop after the gate")

    monkeypatch.setattr(revise_stated, "run_stage", fake_run_stage)
    plain = Brief.model_validate({"length_min": 45})
    inferred = Brief.model_validate({"length_min": 60, "shape": [closed(s) for s in SHAPE]})
    assert await revise_stated.read(None, None, None, plain, "x", None) is None  # type: ignore[arg-type]
    assert calls == []
    with pytest.raises(RuntimeError, match="stop after"):
        await revise_stated.read(None, None, None, inferred, "make it seven bursts", None)  # type: ignore[arg-type]
    assert calls == ["run"]

"""The design follows the shape of the ride (#1493): the section tag of a segment, the ordered
shape check (``agent.shape_check``) and the gate that holds a draft to it. Pure: no model, no
music, no database."""

import json
from typing import Any

import pytest

from agentfake import brief
from classbeat.agent.brief import Brief
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.explicit_input import DEFAULT_CORRECTIONS, ExplicitGate
from classbeat.agent.provider import ToolCall
from classbeat.agent.ride_shape import MAX_SECTIONS
from classbeat.agent.schemas import SECTION_TAG_MAX, Segment, WorkoutDraft
from classbeat.agent.shape_check import check_shape
from classbeat.agent.soundless import (
    SoundlessContext,
    SoundlessReviseContext,
    soundless_design_stage,
    soundless_revise_stage,
)
from classbeat.agent.soundless_schemas import SoundlessDraft
from classbeat.design.interpreted import shape_off_items

# The request of the issue: a warm-up, a climb, 5-7 efforts, the flats, a climb, 2-4 efforts.
SHAPE: list[dict[str, Any]] = [
    {"kind": "warmup"},
    {"kind": "climb", "count": {"min": 1, "max": 1}},
    {"kind": "efforts", "count": {"min": 5, "max": 7}, "load": "heavy"},
    {"kind": "flat"},
    {"kind": "climb", "count": {"min": 1, "max": 1}},
    {"kind": "efforts", "count": {"min": 2, "max": 4}},
]


def shaped(shape: list[dict[str, Any]] = SHAPE, **over: Any) -> Brief:
    return brief(shape=shape, explicit=["shape"], climbs=0, sprints=0, **over)


def ivs(n: int, zone: int = 6, sec: int = 30) -> list[dict[str, Any]]:
    return [{"sec": sec, "zone": zone, "rpm": [85, 95], "standing": False} for _ in range(n)]


def seg(kind: str, section: int | None, intervals: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"name": f"{kind} {section}", "kind": kind, "intervals": intervals}
    if section is not None:
        out["section"] = section
    return out


def spec_of(segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {"segments": segments}


def ride(efforts: int = 6, later: int = 3, tagged: bool = True) -> list[dict[str, Any]]:
    """A draft that follows ``SHAPE`` with ``efforts`` in section 2 and ``later`` in section 5."""
    segments = [
        seg("warmup", 0, ivs(1, 2, 240)),
        seg("main", 1, ivs(1, 4, 240)),
        seg("main", 2, ivs(efforts) if efforts else ivs(1, 3, 60)),
        seg("recovery", 3, ivs(1, 2, 120)),
        seg("main", 4, ivs(1, 4, 240)),
        seg("main", 5, ivs(later) if later else ivs(1, 3, 60)),
        seg("cooldown", None, ivs(1, 1, 240)),
    ]
    if not tagged:
        for s in segments:
            s.pop("section", None)
    return segments


def tags(segments: list[dict[str, Any]]) -> list[int | None]:
    return [s.get("section") for s in segments]


def codes(b: Brief, segments: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(m.path, m.code) for m in check_shape(b, spec_of(segments), tags(segments))]


def test_the_tag_limit_is_the_shapes_section_limit() -> None:
    assert SECTION_TAG_MAX == MAX_SECTIONS - 1


# --- the schema --------------------------------------------------------------------------------


def workout(segments: list[dict[str, Any]]) -> dict[str, Any]:
    plain = [{**s, "intervals": [{**i} for i in s["intervals"]]} for s in segments]
    return {"name": "Ride", "description": "", "segments": plain}


def test_a_draft_without_sections_serializes_byte_identically_and_to_spec_has_none() -> None:
    segments = ride(tagged=False)
    draft = WorkoutDraft.model_validate(workout(segments))
    dumped = draft.model_dump(mode="json")
    assert all("section" not in s for s in dumped["segments"])
    assert all("section" not in s for s in draft.model_dump(mode="python")["segments"])
    keys = [list(s) for s in dumped["segments"]]
    assert keys == [["name", "kind", "intervals"]] * len(segments)  # the fields of before
    assert all("section" not in s for s in draft.to_spec()["segments"])


def test_a_tag_is_kept_in_the_dump_and_never_reaches_the_spec() -> None:
    segments = ride()
    draft = WorkoutDraft.model_validate(workout(segments))
    assert [s.get("section") for s in draft.model_dump(mode="json")["segments"]] == tags(segments)
    assert all("section" not in s for s in draft.to_spec()["segments"])
    assert WorkoutDraft.model_validate(draft.model_dump(mode="json")) == draft  # round trip


@pytest.mark.parametrize("bad", [-1, SECTION_TAG_MAX + 1, "1", 1.5, True])
def test_a_tag_is_an_index_of_the_shape(bad: Any) -> None:
    body = {"name": "x", "kind": "main", "intervals": [], "section": bad}
    body["intervals"] = [{"sec": 30, "zone": 3, "rpm": [85, 95], "standing": False}]
    with pytest.raises(ValueError, match="section"):
        Segment.model_validate_json(json.dumps(body))


def test_the_soundless_draft_carries_the_tag_into_the_stored_draft() -> None:
    segments = ride()
    soundless = SoundlessDraft.model_validate(workout(segments))
    stored = soundless.workout_draft()
    assert [s.section for s in stored.segments] == tags(segments)
    bare = SoundlessDraft.model_validate(workout(ride(tagged=False)))
    assert all("section" not in s for s in bare.model_dump(mode="json")["segments"])


# --- the check ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "segments", "expected"),
    [
        ("follows the shape", ride(), []),
        ("exact bounds of the range", ride(efforts=5, later=4), []),
        ("one short of the range", ride(efforts=4), [("shape.2", "shape_count")]),
        ("one over the range", ride(later=5), [("shape.5", "shape_count")]),
        (
            "untagged",
            ride(tagged=False),
            [(f"shape.{n}", "shape_untagged") for n in range(len(SHAPE))],
        ),
        ("a section with no segment", ride()[:3] + ride()[4:], [("shape.3", "shape_missing")]),
        (
            "tags go down",
            [*ride()[:2], ride()[3], ride()[2], *ride()[4:]],
            [("shape.2", "shape_order")],
        ),
        (
            "an effort too long is not one",
            [*ride()[:2], seg("main", 2, ivs(6, 6, 95)), *ride()[3:]],
            [("shape.2", "shape_count")],
        ),
        (
            "an effort in a low zone is not one",
            [*ride()[:2], seg("main", 2, ivs(6, 4)), *ride()[3:]],
            [("shape.2", "shape_count")],
        ),
        (
            "a tag past the shape",
            [*ride()[:1], seg("main", 9, ivs(1, 4, 240)), *ride()[1:]],
            [("shape", "shape_tag")],
        ),
    ],
)
def test_the_shape_check(
    name: str, segments: list[dict[str, Any]], expected: list[tuple[str, str]]
) -> None:
    assert codes(shaped(), segments) == expected, name


def test_an_exact_count_of_seven() -> None:
    seven = [*SHAPE[:2], {"kind": "efforts", "count": {"min": 7, "max": 7}}, *SHAPE[3:]]
    b = shaped(seven)
    assert codes(b, ride(efforts=7)) == []
    assert codes(b, ride(efforts=6)) == [("shape.2", "shape_count")]
    assert codes(b, ride(efforts=8)) == [("shape.2", "shape_count")]


def test_a_climb_counts_its_main_segments() -> None:
    two = [{"kind": "climb", "count": {"min": 2, "max": 3}}]
    segments = [seg("main", 0, ivs(1, 4, 240)), seg("cooldown", None, ivs(1, 1, 240))]
    assert codes(shaped(two), segments) == [("shape.0", "shape_count")]
    more = [seg("main", 0, ivs(1, 4, 240)), seg("main", 0, ivs(1, 5, 240)), *segments[1:]]
    assert codes(shaped(two), more) == []


def test_a_brief_without_a_shape_is_never_checked() -> None:
    assert check_shape(brief(), spec_of(ride(tagged=False)), tags(ride(tagged=False))) == []


def test_a_warmup_and_a_cooldown_the_shape_lacks_have_no_tag() -> None:
    only = [{"kind": "efforts", "count": {"min": 3, "max": 3}}]
    segments = [
        seg("warmup", None, ivs(1, 2, 300)),
        seg("main", 0, ivs(3)),
        seg("cooldown", None, ivs(1, 1, 240)),
    ]
    assert codes(shaped(only), segments) == []


def test_the_sentences_hold_closed_values_only() -> None:
    segments = ride(efforts=4)
    segments[2]["name"] = "Zq9 planted words"
    told = " ".join(m.sentence for m in check_shape(shaped(), spec_of(segments), tags(segments)))
    assert "Zq9" not in told
    assert "5 to 7" in told
    assert "has 4" in told or "have 4" in told


# --- the gate ----------------------------------------------------------------------------------


def soundless_body(segments: list[dict[str, Any]]) -> dict[str, Any]:
    body = workout(segments)
    return body


def submit(stage: Any, segments: list[dict[str, Any]], n: int = 1) -> Any:
    call = ToolCall(f"c{n}", "submit_draft", {"draft": soundless_body(segments)})
    return dispatch(stage, call)


def test_a_draft_that_follows_the_shape_is_accepted_at_once() -> None:
    stage = soundless_design_stage(SoundlessContext(shaped()))
    done = submit(stage, ride())
    assert done.ok
    assert done.output.unmet == ()


def test_an_untagged_draft_is_a_correction_round_then_shape_off_per_section() -> None:
    b = shaped()
    stage = soundless_design_stage(SoundlessContext(b))
    for n in range(DEFAULT_CORRECTIONS):
        refused = submit(stage, ride(tagged=False), n)
        assert (refused.ok, refused.error) == (False, "invalid_draft")
    done = submit(stage, ride(tagged=False), 9)
    assert done.ok
    assert [(m.path, m.code) for m in done.output.unmet] == [
        (f"shape.{n}", "shape_off") for n in range(len(SHAPE))
    ]


def test_a_count_miss_is_corrected_and_the_corrected_draft_ends_clean() -> None:
    stage = soundless_design_stage(SoundlessContext(shaped()))
    refused = submit(stage, ride(efforts=4), 1)
    assert not refused.ok
    assert "Section 2" in refused.result.content
    assert submit(stage, ride(efforts=6), 2).ok


def test_a_remaining_miss_is_recorded_as_a_not_as_asked_item_of_its_section() -> None:
    b = shaped()
    stage = soundless_design_stage(SoundlessContext(b))
    for n in range(DEFAULT_CORRECTIONS):
        assert not submit(stage, ride(efforts=5, later=5), n).ok
    done = submit(stage, ride(efforts=5, later=5), 9)
    assert [(m.path, m.code) for m in done.output.unmet] == [("shape.5", "shape_off")]
    (item,) = shape_off_items(b, done.output.unmet)
    assert (item.path, item.origin, item.reason_code) == ("shape.5", "not_as_asked", "shape_off")
    assert item.value == {"kind": "efforts", "count": {"min": 2, "max": 4}}


def test_the_gate_checks_the_shape_only_where_asked_to() -> None:
    plain = ExplicitGate(shaped())
    assert plain.review(spec_of(ride(tagged=False)), tags(ride(tagged=False))) == ()
    follows = ExplicitGate(shaped(), follow_shape=True)
    assert follows.review(spec_of(ride()), tags(ride())) == ()


def test_a_latest_instruction_changes_what_the_check_wants() -> None:
    """\"Make it seven bursts\": the layered brief has {7,7}; six is a miss, seven is not, and
    the later section keeps its three."""
    asked = [dict(s) for s in SHAPE]
    asked[2] = {"kind": "efforts", "count": {"min": 7, "max": 7}, "load": "heavy"}
    layered = shaped(asked)
    assert codes(shaped(), ride(efforts=6, later=3)) == []
    assert codes(layered, ride(efforts=6, later=3)) == [("shape.2", "shape_count")]
    assert codes(layered, ride(efforts=7, later=3)) == []


def test_the_sketch_then_make_it_seven_bursts() -> None:
    """The issue's scenario: the sketch of the original request passes with six efforts in
    section 2 and three in section 5; the revise to {7,7} (the layered brief) refuses six and
    stores seven in section 2 with section 5 still at three."""
    sketch = soundless_design_stage(SoundlessContext(shaped()))
    done = submit(sketch, ride(efforts=6, later=3))
    assert done.ok
    base = done.output.draft
    asked = [dict(s) for s in SHAPE]
    asked[2] = {"kind": "efforts", "count": {"min": 7, "max": 7}, "load": "heavy"}
    layered = shaped(asked)
    revise = soundless_revise_stage(SoundlessReviseContext(layered, base, (), "make it seven"))

    def revised(efforts: int, n: int) -> Any:
        body = {"draft": soundless_body(ride(efforts=efforts, later=3)), "note": "Seven."}
        return dispatch(revise, ToolCall(f"r{n}", "submit_draft", body))

    refused = revised(6, 1)
    assert (refused.ok, refused.error) == (False, "invalid_draft")
    final = revised(7, 2)
    assert final.ok
    stored = final.output.result.draft
    per_section = {
        n: sum(
            1
            for seg in stored.segments
            if seg.section == n
            for i in seg.intervals
            if i.zone >= 5 and i.sec <= 90
        )
        for n in (2, 5)
    }
    assert per_section == {2: 7, 5: 3}

"""SR C5 (ADR 0036 section 4.4): the studio's design and revise stages on fakes. The model designs
the workout and the mix by track index; the backend checks the mix, composes it and fits the
workout; the model cannot write a reference, a length or a tempo; what it reads has none.
"""

import json
from typing import Any

import pytest

from agentfake import CONFIG, MemoryHost, from_dict, good_draft
from classbeat.agent import Brief, design_stage, run_stage
from classbeat.agent.stage import ToolAnswer
from classbeat.agent.studio_design import StudioBase, StudioContext, StudioResult, studio_stage
from classbeat.agent.studio_mix import StudioTrack
from classbeat.agent.studio_schemas import StudioDraftInput
from studiomaps import track_map, tracks, whole_mix

BRIEF = Brief.model_validate({"length_min": 30, "intensity": "moderate", "climbs": 1})
PLANTED_REFERENCE = "ref-planted0000reference"


def call(name: str, data: dict[str, Any], **check: Any) -> dict[str, Any]:
    return {"calls": [{"name": name, "input": data, **check}]}


async def run(ctx: StudioContext, turns: list[dict[str, Any]]) -> tuple[Any, Any]:
    fake = from_dict({"model": "fake-model", "drafts": {"good": good_draft()}, "turns": turns})
    outcome = await run_stage(MemoryHost(), fake, studio_stage(ctx), ctx.first_message(), CONFIG)
    return outcome, fake


async def test_the_model_designs_the_workout_and_the_mix_by_index() -> None:
    order = [3, 0, 1, 2, 4, 5, 6, 7]
    ctx = StudioContext(BRIEF, tracks())
    outcome, _ = await run(
        ctx,
        [
            call("get_music", {}, expect={"ok": True}, contains=['"length_sec"', '"bar_sec"']),
            call("get_music", {"track": 3}, expect={"ok": True}, contains=['"energy"']),
            call(
                "preview_draft",
                {"draft": {"$draft": "good"}, "mix": whole_mix(order=order)},
                expect={"ok": True, "duration_sec": 1800},
                contains=['"total_sec"', '"played_sec"', '"timeline"'],
            ),
            call("submit_draft", {"draft": {"$draft": "good"}, "mix": whole_mix(order=order)}),
        ],
    )
    assert isinstance(outcome.output, StudioResult)
    plan = outcome.output.composed.plan
    assert [t["track"] for t in plan["tracks"]] == order
    assert plan["crossfades"] == [8] * 7
    windows = sum(t["end_sec"] - t["start_sec"] for t in plan["tracks"])
    assert outcome.output.composed.duration_sec == round(windows - 56, 2)  # the backend's total
    assert outcome.output.composed.map["duration"] == outcome.output.composed.duration_sec
    assert outcome.output.result.duration_sec == 1800
    # The timeline names the list's index, not the position in the mix: the first interval
    # plays on track 3, which the mix plays first.
    tool = studio_stage(ctx).tool("preview_draft")
    assert tool is not None
    given = StudioDraftInput.model_validate({"draft": good_draft(), "mix": whole_mix(order=order)})
    answer = tool.handler(given)
    assert isinstance(answer, ToolAnswer)
    assert answer.payload["timeline"][0]["intervals"][0]["track"] == 3
    assert answer.payload["mix"]["tracks"][0]["track"] == 3


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"tracks": [{"track": 0, "start_sec": 0, "end_sec": 300.0}], "crossfades": []},
         "not_a_permutation"),
        ({"crossfades": [8] * 6 + [31]}, "crossfade_out_of_range"),
        ({"crossfades": [8] * 6 + [2.5]}, "crossfade_not_whole"),
        ({"crossfades": [8] * 6}, "crossfade_count"),
    ],
)  # fmt: skip
async def test_a_mix_that_breaks_a_rule_comes_back_with_its_reason(
    change: dict[str, Any], reason: str
) -> None:
    ctx = StudioContext(BRIEF, tracks())
    bad = {**whole_mix(), **change}
    outcome, _ = await run(
        ctx,
        [
            call(
                "submit_draft",
                {"draft": {"$draft": "good"}, "mix": bad},
                expect={"ok": False, "error": "invalid_mix"},
                contains=[reason],
            ),
            call("submit_draft", {"draft": {"$draft": "good"}, "mix": whole_mix()}),
        ],
    )
    assert isinstance(outcome.output, StudioResult)


@pytest.mark.parametrize(
    "extra",
    [{"reference": PLANTED_REFERENCE}, {"length_sec": 999.0}, {"tempo": 1.1}, {"bpm": 130.0}],
)
async def test_the_model_cannot_write_a_reference_a_length_or_a_tempo(
    extra: dict[str, Any],
) -> None:
    ctx = StudioContext(BRIEF, tracks())
    bad = whole_mix()
    bad["tracks"][0] |= extra
    outcome, _ = await run(
        ctx,
        [
            call(
                "preview_draft",
                {"draft": {"$draft": "good"}, "mix": bad},
                expect={"ok": False, "error": "invalid_input"},
            ),
            call(
                "submit_draft",
                {"draft": {"$draft": "good"}, "mix": {**whole_mix(), "total_sec": 1800}},
                expect={"ok": False, "error": "invalid_input"},
            ),
            call("submit_draft", {"draft": {"$draft": "good"}, "mix": whole_mix()}),
        ],
    )
    assert isinstance(outcome.output, StudioResult)


async def test_the_model_sees_no_reference_and_lengths_only_from_the_analysis() -> None:
    planted = (
        StudioTrack("Invented Song", "Made-up Band", track_map(271.5), "exact"),
        *tracks(7)[1:],
    )
    ctx = StudioContext(BRIEF, planted)
    first = ctx.first_message()
    assert PLANTED_REFERENCE not in first
    assert "ref-" not in first
    assert '"length_sec":271.5' in first
    stage = studio_stage(ctx)
    for tool in stage.tools:
        assert "reference" not in json.dumps(tool.definition().input_schema)


def test_the_studio_stages_have_their_own_prompt_versions() -> None:
    from classbeat.agent import DesignContext

    design = StudioContext(BRIEF, tracks())
    revise = StudioContext(BRIEF, tracks(), base=StudioBase({}, whole_mix(), (), "Shorter"))
    versions = {studio_stage(design).prompt_version, studio_stage(revise).prompt_version}
    assert len(versions) == 2
    m = {"duration": 60.0, "songs": [], "events": [], "crossfade": 0.0}
    plain = design_stage(DesignContext(BRIEF, m, {}))
    assert plain.prompt_version not in versions
    assert studio_stage(design).name == "studio_design"
    assert studio_stage(revise).name == "studio_revise"


async def test_a_revise_sees_the_base_mix_by_index_and_ends_with_a_note() -> None:
    base_mix = whole_mix(order=[1, 0, 2, 3, 4, 5, 6, 7])
    base = StudioBase(good_draft(), base_mix, ("Make it harder",), "Cut the first song at 2 min")
    ctx = StudioContext(BRIEF, tracks(), base=base)
    first = ctx.first_message()
    assert '"base_mix"' in first
    assert '"earlier_requests":["Make it harder"]' in first
    cut = json.loads(json.dumps(base_mix))
    cut["tracks"][0]["end_sec"] = 120.0
    outcome, _ = await run(
        ctx,
        [call("submit_draft", {"draft": {"$draft": "good"}, "mix": cut, "note": "Cut song 1."})],
    )
    assert isinstance(outcome.output, StudioResult)
    assert outcome.output.note == "Cut song 1."
    first_track = outcome.output.composed.plan["tracks"][0]
    assert first_track["track"] == 1
    assert abs(first_track["end_sec"] - 120.0) <= 240.0 / 128.0 / 2  # snapped to a downbeat

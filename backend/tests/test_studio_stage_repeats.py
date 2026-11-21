"""#1342 part 1 (ADR 0036 amendment A2, A4.1, A5, A8): the studio stages with a mix that repeats a
song. The model names a track's index again for a repeat; the backend checks it, refuses a loop
join (two pieces of one track back to back) for now, and answers the soft hints as warnings that
name pieces, never a reference. The prompts state the rules. Fakes only."""

import copy
from typing import Any

import pytest

from agentfake import good_draft
from classbeat.agent.stage import ToolAnswer, ToolError
from classbeat.agent.studio_design import StudioContext, StudioResult, studio_stage
from classbeat.agent.studio_mix import compose
from classbeat.agent.studio_prompt import DESIGN_SYSTEM, REVISE_SYSTEM
from classbeat.agent.studio_schemas import MixInput, StudioDraftInput
from studiomaps import tracks, whole_mix
from test_studio_stage import BRIEF, call, run


def with_repeat(*pieces: dict[str, Any]) -> dict[str, Any]:
    mix = whole_mix()
    mix["tracks"] += list(pieces)
    mix["crossfades"] += [8] * len(pieces)
    return mix


RECOVERY = {"track": 2, "start_sec": 0.4, "end_sec": 62.0}  # a minute of track 2 again, last


async def test_the_model_repeats_a_track_by_naming_its_index_again() -> None:
    ctx = StudioContext(BRIEF, tracks())
    mix = with_repeat(RECOVERY)
    outcome, _ = await run(
        ctx,
        [
            call("preview_draft", {"draft": {"$draft": "good"}, "mix": mix}, expect={"ok": True}),
            call("submit_draft", {"draft": {"$draft": "good"}, "mix": mix}),
        ],
    )
    assert isinstance(outcome.output, StudioResult)
    plan = outcome.output.composed.plan
    assert [t["track"] for t in plan["tracks"]] == [*range(8), 2]
    windows = [t["end_sec"] - t["start_sec"] for t in plan["tracks"]]
    assert outcome.output.composed.duration_sec == round(sum(windows) - 8 * 8, 2)
    assert outcome.output.composed.map["duration"] == outcome.output.composed.duration_sec
    assert outcome.output.composed.hints == ()  # a minute again is no whole track


def test_the_timeline_names_a_repeat_by_its_list_index() -> None:
    ctx = StudioContext(BRIEF, tracks())
    tool = studio_stage(ctx).tool("preview_draft")
    assert tool is not None
    given = StudioDraftInput.model_validate({"draft": good_draft(), "mix": with_repeat(RECOVERY)})
    answer = tool.handler(given)
    assert isinstance(answer, ToolAnswer)
    assert [t["track"] for t in answer.payload["mix"]["tracks"]] == [*range(8), 2]
    listed = {i["track"] for s in answer.payload["timeline"] for i in s["intervals"]}
    assert listed <= set(range(8))  # list indexes, never mix positions such as 8


async def test_a_loop_join_comes_back_with_its_reason() -> None:
    ctx = StudioContext(BRIEF, tracks())
    loop = whole_mix()
    loop["tracks"].insert(1, {"track": 0, "start_sec": 0, "end_sec": 300.0})
    loop["crossfades"].append(8)
    outcome, _ = await run(
        ctx,
        [
            call(
                "preview_draft",
                {"draft": {"$draft": "good"}, "mix": loop},
                expect={"ok": False, "error": "invalid_mix"},
                contains=["loop_needs_exact_timing", "pieces 0 and 1"],
            ),
            call("submit_draft", {"draft": {"$draft": "good"}, "mix": whole_mix()}),
        ],
    )
    assert isinstance(outcome.output, StudioResult)


def test_a_whole_track_twice_is_a_hint_with_piece_indexes_only() -> None:
    twice = with_repeat({"track": 3, "start_sec": 0, "end_sec": 330.0})
    composed = compose(tracks(), MixInput.model_validate(twice))
    assert [h.split(":")[0] for h in composed.hints] == ["whole_track_repeated"]
    assert "pieces 3, 8" in composed.hints[0]
    ctx = StudioContext(BRIEF, tracks())
    tool = studio_stage(ctx).tool("preview_draft")
    assert tool is not None
    answer = tool.handler(StudioDraftInput.model_validate({"draft": good_draft(), "mix": twice}))
    assert isinstance(answer, ToolAnswer)
    assert composed.hints[0] in answer.payload["warnings"]
    assert "ref-" not in repr(answer.payload)


def test_a_missing_track_is_still_refused() -> None:
    mix = copy.deepcopy(with_repeat(RECOVERY))
    del mix["tracks"][5]
    mix["crossfades"].pop()
    with pytest.raises(ToolError) as caught:
        compose(tracks(), MixInput.model_validate(mix))
    assert "not_a_permutation: each approved track at least once" in str(caught.value.messages)


@pytest.mark.parametrize("system", [DESIGN_SYSTEM, REVISE_SYSTEM])
def test_the_prompts_state_the_repeat_rules(system: str) -> None:
    assert "every track of the list at least once" in system
    assert "never play the whole of a track twice unless the instructor asked" in system
    assert "Never put two pieces of the same track next to each other" in system
    assert "can be produced as a mix only, not as a SwedeBeat playlist" in system
    assert "at most 40 pieces" in system
    assert "exactly once" not in system

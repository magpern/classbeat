"""#1342 (ADR 0036 amendment A2, A4, A5, A8): the studio stages with a mix that repeats a song. The
model names a track's index again for a repeat; the backend checks it, lets a loop join (two
pieces of one track back to back) through only on a stored ``exact`` track at its trusted phrase
points, and answers the soft hints as warnings that name pieces, never a reference. The prompts
state the rules. Fakes only."""

import copy
from typing import Any

import pytest

from agentfake import good_draft
from classbeat.agent.stage import ToolAnswer, ToolError
from classbeat.agent.studio_design import StudioContext, StudioResult, studio_stage
from classbeat.agent.studio_mix import StudioTrack, compose
from classbeat.agent.studio_prompt import DESIGN_SYSTEM, REVISE_SYSTEM
from classbeat.agent.studio_schemas import MixInput, StudioDraftInput
from classbeat.spotdown.cut_candidates_model import MAP_KEY
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
    # A minute again is no whole track (no ``whole_track_repeated``); but these local tracks have
    # no timing decision, so the cut minute is ``cut_on_inexact_track`` (#1372), a soft hint only.
    assert [h.split(":")[0] for h in outcome.output.composed.hints] == ["cut_on_inexact_track"]
    assert "pieces 8 cut a track" in outcome.output.composed.hints[0]


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


def exact_tracks() -> tuple[StudioTrack, ...]:
    """``tracks()`` with track 0 analysed by spotdown as ``exact``: its phrase starts (every 8 bars
    of 1.875 s from 0.4) stored with its map, trusted."""
    first, *rest = tracks()
    phrases = [
        {"at_s": round(0.4 + 15.0 * k, 3), "kind": "phrase_start", "confidence": 0.8,
         "method": "estimated", "trusted": True}
        for k in range(20)
    ]  # fmt: skip
    music_map = {**first.music_map, MAP_KEY: phrases}
    return (StudioTrack(first.title, first.artist, music_map, "exact"), *rest)


def looped(fade: int = 0) -> dict[str, Any]:
    mix = whole_mix()
    mix["tracks"][0] = {"track": 0, "start_sec": 0, "end_sec": 60.4}
    mix["tracks"].insert(1, {"track": 0, "start_sec": 60.4, "end_sec": 300.0})
    mix["crossfades"].insert(0, fade)
    return mix


async def test_a_loop_join_on_an_exact_track_is_composed() -> None:
    ctx = StudioContext(BRIEF, exact_tracks())
    outcome, _ = await run(
        ctx,
        [
            call(
                "preview_draft",
                {"draft": {"$draft": "good"}, "mix": looped(fade=8)},
                expect={"ok": False, "error": "invalid_mix"},
                contains=["loop_crossfade_not_zero", "pieces 0 and 1"],
            ),
            call("submit_draft", {"draft": {"$draft": "good"}, "mix": looped()}),
        ],
    )
    assert isinstance(outcome.output, StudioResult)
    plan = outcome.output.composed.plan
    assert [t["track"] for t in plan["tracks"]][:3] == [0, 0, 1]
    assert plan["tracks"][0]["end_sec"] == plan["tracks"][1]["start_sec"] == 60.4
    assert plan["crossfades"][0] == 0
    assert outcome.output.composed.map["duration"] == outcome.output.composed.duration_sec


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
    assert "Never put two pieces of the same track next to each other" not in system
    assert "two pieces of one track next to each other are a loop" in system
    assert "only for a track whose timing is exact" in system
    assert "crossfade 0" in system
    assert "can be produced as a mix only, not as a SwedeBeat playlist" in system
    assert "at most 40 pieces" in system
    assert "exactly once" not in system

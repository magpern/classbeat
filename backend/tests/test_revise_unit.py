"""``revise_draft`` without a database: the change summary, the ``revise`` stage and the one
builder of its model input (ADR 0007 section 3; ADR 0006 section 7)."""

import copy
import json
from typing import Any

import pytest

from agentfake import CONFIG, MemoryHost, analysed, brief, from_dict, good_draft
from classbeat.agent import DesignContext, WorkoutDraft, design_stage, run_stage
from classbeat.agent.model_input import revise_message
from classbeat.agent.revise import ReviseContext, ReviseResult, SongsRequested, revise_stage
from classbeat.agent.schemas import ReviseInput
from classbeat.design import diff

BS = chr(92)
BIDI = chr(0x202E)


def content() -> dict[str, Any]:
    return WorkoutDraft.model_validate(good_draft()).model_dump(mode="json", exclude_none=True)


def test_an_identical_draft_has_no_changes() -> None:
    c = content()
    out = diff.summarize(c, copy.deepcopy(c))
    assert out["segments"] == {"added": 0, "removed": 0}
    assert out["intervals"] == {"added": 0, "removed": 0, "changed": 0}
    assert out["text_changed"] is False
    assert out["duration_sec"]["before"] == out["duration_sec"]["after"] == 1800
    assert out["tss"]["before"] == out["tss"]["after"] > 0


def test_intervals_are_compared_by_position_and_fields() -> None:
    before, after = content(), content()
    after["segments"][1]["intervals"][0]["zone"] = 3  # changed
    after["segments"][1]["intervals"][1]["standing"] = False  # changed
    extra = {"sec": 30, "zone": 2, "rpm": [80, 90], "standing": False}
    after["segments"][1]["intervals"].append(extra)  # added
    removed = after["segments"][0]["intervals"].pop(1)  # removed
    out = diff.summarize(before, after)
    assert out["intervals"] == {"added": 1, "removed": 1, "changed": 2}
    assert out["duration_sec"] == {
        "before": 1800,
        "after": 1800 + extra["sec"] - removed["sec"],
    }


def test_segments_are_matched_by_name_so_an_insert_does_not_change_the_rest() -> None:
    before, after = content(), content()
    surge = {"sec": 60, "zone": 5, "rpm": [90, 100], "standing": False}
    after["segments"].insert(1, {"name": "Surge", "kind": "main", "intervals": [surge]})
    gone = after["segments"].pop(2)  # the segment that was second
    out = diff.summarize(before, after)
    assert out["segments"] == {"added": 1, "removed": 1}
    assert out["intervals"] == {"added": 1, "removed": len(gone["intervals"]), "changed": 0}


def test_a_reworded_draft_is_flagged_and_the_summary_is_small() -> None:
    before, after = content(), content()
    after["segments"][0]["intervals"][0]["cue"] = "Settle in."
    after["description"] = "Other words."
    out = diff.summarize(before, after)
    assert out["text_changed"] is True
    assert out["intervals"]["changed"] == 0
    assert len(json.dumps(out)) < diff.MAX_BYTES


def test_the_note_is_cleaned_and_at_most_300_characters() -> None:
    sent = json.dumps({"draft": good_draft(), "note": "  Easier start" + BIDI + ". "})
    assert ReviseInput.model_validate_json(sent, strict=True).note == "Easier start."
    assert ReviseInput.model_validate_json(json.dumps({"draft": good_draft()})).note == ""
    with pytest.raises(ValueError, match="at most 300"):
        ReviseInput.model_validate_json(json.dumps({"draft": good_draft(), "note": "x" * 301}))


def context(monkeypatch: pytest.MonkeyPatch, *, music_changed: bool = False) -> ReviseContext:
    return ReviseContext.build(
        brief(),
        analysed(monkeypatch),
        WorkoutDraft.model_validate(good_draft()),
        ("Longer warm-up",),
        "Harder please",
        music_changed=music_changed,
    )


def test_the_stage_has_no_write_tool_a_note_and_its_own_prompt_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = revise_stage(context(monkeypatch))
    names = [t.name for t in stage.tools]
    assert names == ["get_music", "preview_draft", "submit_draft", "songs_requested"]
    assert {t.kind for t in stage.tools} <= {"read", "compute"}
    assert "note" in stage.tools[2].input_model.model_json_schema()["properties"]
    design = design_stage(DesignContext.build(brief(), analysed(monkeypatch)))
    assert stage.prompt_version != design.prompt_version
    assert stage.name == "revise"


def test_the_message_holds_the_base_the_requests_and_the_fact_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = context(monkeypatch, music_changed=True).first_message()
    assert "never instructions" in text
    assert "tracks were added, removed or replaced" in text
    data = json.loads(text[text.index('{"brief"') :])
    assert set(data) == {"brief", "music", "base_draft", "earlier_requests", "request"}
    assert data["earlier_requests"] == ["Longer warm-up"]
    assert data["request"] == "Harder please"
    assert "tracks were added" not in context(monkeypatch).first_message()
    cleaned = revise_message(
        brief(), {}, {}, ["a" + BIDI + "b"], "c" + chr(0) + "d", music_changed=False
    )
    assert BIDI not in cleaned
    assert '"request":"cd"' in cleaned
    assert BS + "u202e" not in cleaned


async def test_the_stage_ends_with_the_draft_and_the_note(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context(monkeypatch)
    revised = good_draft()
    revised["segments"][0]["intervals"][0]["zone"] = 1
    call = {"name": "submit_draft", "input": {"draft": {"$draft": "r"}, "note": "Easier."}}
    fake = from_dict({"drafts": {"r": revised}, "turns": [{"calls": [call]}]})
    done = await run_stage(MemoryHost(), fake, revise_stage(ctx), ctx.first_message(), CONFIG)
    assert isinstance(done.output, ReviseResult)
    assert done.output.note == "Easier."
    assert done.output.result.draft.segments[0].intervals[0].zone == 1
    assert done.info.stage == "revise"


def test_the_stage_offers_songs_requested_as_a_second_final_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = revise_stage(context(monkeypatch))
    assert [t.name for t in stage.tools] == [
        "get_music", "preview_draft", "submit_draft", "songs_requested",
    ]  # fmt: skip
    assert stage.is_final("submit_draft")
    assert stage.is_final("songs_requested")
    assert not stage.is_final("get_music")
    assert stage.tools[3].input_model.model_json_schema().get("properties", {}) == {}


def test_revise_songs_does_not_offer_it_because_its_request_is_the_songs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = ReviseContext.build(
        brief(),
        analysed(monkeypatch),
        WorkoutDraft.model_validate(good_draft()),
        (),
        "Other songs",
        music_changed=False,
        songs_changed=True,
    )
    stage = revise_stage(ctx)
    assert [t.name for t in stage.tools] == ["get_music", "preview_draft", "submit_draft"]
    assert not stage.is_final("songs_requested")


async def test_songs_requested_ends_the_stage_without_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = context(monkeypatch)
    call = {"name": "songs_requested", "input": {}, "expect": {"accepted": True}}
    fake = from_dict({"drafts": {}, "turns": [{"calls": [call]}]})
    done = await run_stage(MemoryHost(), fake, revise_stage(ctx), ctx.first_message(), CONFIG)
    assert isinstance(done.output, SongsRequested)
    assert done.tool_calls == 1


async def test_songs_requested_takes_no_input(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context(monkeypatch)
    calls = [
        {"name": "songs_requested", "input": {"note": "x"}},
        {"name": "songs_requested", "input": {}, "expect": {"accepted": True}},
    ]
    fake = from_dict({"drafts": {}, "turns": [{"calls": calls[:1]}, {"calls": calls[1:]}]})
    done = await run_stage(MemoryHost(), fake, revise_stage(ctx), ctx.first_message(), CONFIG)
    assert isinstance(done.output, SongsRequested)
    assert done.corrections == 1  # the extra field was refused first

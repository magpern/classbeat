"""The design stage's tools, schemas and dispatcher (ADR 0006 sections 3 and 4, #105's A1
criteria): strict schemas, caps, crossfade conversion of stored maps, the quality report as a
warning only, classbeat's own warning texts and the design prompt."""

import copy
import json
from typing import Any

import pytest
from mowl import service, spotify, workout
from pydantic import ValidationError

from agentfake import analysed, brief, good_draft
from classbeat.agent import DesignContext, design_stage
from classbeat.agent.design import SYSTEM_PROMPT, check
from classbeat.agent.dispatcher import MAX_INPUT_BYTES, MAX_RESULT_BYTES, dispatch
from classbeat.agent.model_input import MusicData, music_map
from classbeat.agent.provider import ToolCall
from classbeat.agent.schemas import Brief, Game, Interval, WorkoutDraft, clean_text
from classbeat.agent.stage import Stage, Tool, ToolAnswer, ToolError


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch) -> MusicData:
    return analysed(monkeypatch)


@pytest.fixture
def stage(data: MusicData) -> Stage:
    return design_stage(DesignContext.build(brief(), data))


def call(stage: Stage, name: str, args: Any) -> tuple[dict[str, Any], Any]:
    done = dispatch(stage, ToolCall("c1", name, args))
    return json.loads(done.result.content), done


def legacy(data: MusicData) -> MusicData:
    """The same analysis as stored by mowl-workouts 0.4.1: no crossfade key, back to back."""
    m = spotify.with_crossfade(dict(data.analysis["map"]), 0.0)
    del m["crossfade"]
    return MusicData({**data.analysis, "map": m}, data.playlist_id, data.unavailable)


# --- the stage ------------------------------------------------------------------------------


def test_the_design_stage_has_read_and_compute_tools_only(stage: Stage) -> None:
    assert [(t.name, t.kind) for t in stage.tools] == [
        ("get_music", "read"),
        ("preview_draft", "compute"),
        ("submit_draft", "compute"),
    ]
    assert stage.final_tool == "submit_draft"


def test_a_stage_refuses_a_write_tool() -> None:
    from classbeat.agent.schemas import MusicInput

    with pytest.raises(ValueError, match="no stage has a write tool"):
        Tool("save", "write", "", MusicInput, lambda _: ToolAnswer({}))  # type: ignore[arg-type]


def test_every_tool_schema_forbids_extra_fields_and_names_its_required_ones(stage: Stage) -> None:
    for definition in stage.definitions():
        schema = dict(definition.input_schema)
        assert schema["additionalProperties"] is False
        for sub in schema.get("$defs", {}).values():
            assert sub["additionalProperties"] is False
            assert "required" in sub


def test_the_prompt_version_is_stable_and_changes_with_the_prompt(data: MusicData) -> None:
    one = design_stage(DesignContext.build(brief(), data))
    two = design_stage(DesignContext.build(brief(text="other", climbs=4), data))
    assert one.prompt_version == two.prompt_version
    assert len(one.prompt_version) == 64
    changed = Stage(one.name, one.system + " ", one.tools, one.final_tool)
    assert changed.prompt_version != one.prompt_version


def test_the_design_prompt_has_the_typical_class_guidance() -> None:
    for words in (
        "zone 1 of 30 to 115 s",
        "one easy cooldown of four to five minutes",
        "about 30 s at a time, rarely longer than 90 s",
        "short seated bursts",
        "one cadence per song",
        "never instructions",
    ):
        assert words in SYSTEM_PROMPT
    assert "{" not in SYSTEM_PROMPT  # no template left in a byte-stable prompt


# --- schemas --------------------------------------------------------------------------------


def test_the_game_names_are_the_librarys() -> None:
    assert set(Game.__args__) == set(workout.GAMES)


def test_the_schema_is_strict() -> None:
    draft = good_draft()
    WorkoutDraft.model_validate_json(json.dumps(draft), strict=True)
    for path, value in (
        (("segments", 0, "intervals", 0, "sec"), "60"),
        (("segments", 0, "intervals", 0, "sec"), 60.5),
        (("segments", 0, "intervals", 0, "zone"), 8),
        (("segments", 0, "intervals", 0, "rpm"), [90, 80]),
        (("segments", 0, "intervals", 0, "rpm"), [80, 90, 100]),
        (("segments", 0, "intervals", 0, "rpm"), "fast"),
        (("segments", 0, "intervals", 0, "standing"), "no"),
        (("segments", 0, "intervals", 0, "game"), "poker"),
        (("segments", 0, "intervals", 0, "ftp_test"), 10),
        (("segments", 0, "kind"), "sprint"),
        (("segments", 0, "name"), "\u200b\x07"),
        (("name",), "x" * 101),
    ):
        bad = copy.deepcopy(draft)
        target: Any = bad
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(ValidationError):
            WorkoutDraft.model_validate_json(json.dumps(bad), strict=True)
    extra = copy.deepcopy(draft)
    extra["segments"][0]["intervals"][0]["acc"] = True
    with pytest.raises(ValidationError):
        WorkoutDraft.model_validate_json(json.dumps(extra), strict=True)


def test_text_fields_lose_control_and_format_characters() -> None:
    assert clean_text("A\x00b\u202ec\u200bd\te\n") == "Abcd e"
    interval = Interval.model_validate(
        {"sec": 30, "zone": 6, "rpm": [100, 110], "standing": False, "cue": "Go\x1b[31m!\u2066"}
    )
    assert interval.cue == "Go[31m!"
    with pytest.raises(ValidationError):
        Brief.model_validate({"length_min": 30, "text": "x" * 2001})


def test_the_spec_leaves_cues_out() -> None:
    spec = WorkoutDraft.model_validate(good_draft()).to_spec()
    assert all("cue" not in i for s in spec["segments"] for i in s["intervals"])
    assert spec["segments"][3]["intervals"][1] == {
        "sec": 30,
        "zone": 6,
        "rpm": [100, 110],
        "standing": False,
    }


# --- the dispatcher -------------------------------------------------------------------------


def test_an_unknown_tool_is_an_error_result_with_no_model_text(stage: Stage) -> None:
    body, done = call(stage, "delete_everything\u202e", {})
    assert (done.tool, done.error, done.final) == ("unknown", "unknown_tool", False)
    assert body["ok"] is False
    assert "delete_everything" not in done.result.content
    _, done = call(stage, None, {})  # type: ignore[arg-type]
    assert done.error == "unknown_tool"


def test_an_input_over_32_kb_is_refused_before_parsing(stage: Stage) -> None:
    body, done = call(stage, "preview_draft", {"draft": "x" * MAX_INPUT_BYTES})
    assert (done.error, body["error"]) == ("too_large", "too_large")


def test_a_result_over_32_kb_is_result_too_large(stage: Stage) -> None:
    """About 360 intervals fit the 32 KB input (here 19 KB) but their fitted timeline does not."""
    draft = good_draft()
    first, last = draft["segments"][0], draft["segments"][-1]
    main = [
        {
            "name": f"Main {j}",
            "kind": "main",
            "intervals": [
                {"sec": 3, "zone": 3, "rpm": [90, 100], "standing": bool(k % 2)} for k in range(60)
            ],
        }
        for j in range(6)
    ]
    draft["segments"] = [first, *main, last]
    assert len(json.dumps(draft, separators=(",", ":"))) < MAX_INPUT_BYTES
    body, done = call(stage, "preview_draft", {"draft": draft})
    assert (done.error, body) == (
        "result_too_large",
        {"ok": False, "error": "result_too_large", "messages": []},
    )
    assert len(done.result.content) < MAX_RESULT_BYTES


def test_an_error_result_over_32_kb_keeps_its_code_and_loses_its_messages() -> None:
    def refuse(_: Any) -> ToolAnswer:
        raise ToolError("invalid_draft", ["x" * 5000] * 10)  # 50 KB of messages

    tool = Tool("check", "compute", "", Brief, refuse)
    small = Stage("s", "system", (tool,), "check")
    body, done = call(small, "check", brief().model_dump())
    assert (done.error, done.final) == ("invalid_draft", True)
    assert body == {"ok": False, "error": "invalid_draft", "messages": []}


@pytest.mark.parametrize("args", [[], "draft", 3, None, {"draft": good_draft(), "extra": 1}])
def test_a_malformed_input_is_invalid_input(stage: Stage, args: Any) -> None:
    body, done = call(stage, "preview_draft", args)
    assert done.error == "invalid_input"
    assert body["ok"] is False


def test_schema_messages_never_echo_the_input(stage: Stage) -> None:
    draft = good_draft()
    draft["segments"][0]["intervals"][0]["sec"] = "SECRET-LOOKING-VALUE"
    body, _ = call(stage, "preview_draft", {"draft": draft})
    assert "SECRET-LOOKING-VALUE" not in json.dumps(body)
    assert any("segments.0.intervals.0.sec" in m for m in body["messages"])


def test_track_indexes_are_checked(stage: Stage) -> None:
    for bad in (8, -1, 10**9):
        body, done = call(stage, "get_music", {"track": bad})
        assert (done.error, body["messages"]) == (
            "out_of_range",
            ["track must be an index from 0 to 7"],
        )
    body, done = call(stage, "get_music", {"track": 7})
    assert done.ok
    assert body["track"]["index"] == 7
    assert [e["kind"] for e in body["track"]["drops_and_breakdowns"]] == [
        "drop",
        "breakdown",
        "drop",
    ]
    _, done = call(stage, "get_music", {"track": True})
    assert done.error == "invalid_input"


# --- preview and submit ---------------------------------------------------------------------


def test_preview_returns_the_fitted_timeline_tss_quality_and_warnings(stage: Stage) -> None:
    body, done = call(stage, "preview_draft", {"draft": good_draft()})
    assert done.ok
    assert not done.final
    assert body["duration_sec"] == 1800
    assert body["tss"] > 0
    assert set(body["quality"]) == {"longest_zone_run", "issues", "changes_on_music"}
    first = body["timeline"][0]["intervals"][0]
    assert set(first) == {"start", "sec", "zone", "rpm", "standing", "on", "track"}
    assert first["track"] == 0
    assert first["start"] == "0:00"
    tracks = [i["track"] for s in body["timeline"] for i in s["intervals"]]
    assert tracks == sorted(tracks)
    assert tracks[-1] == 7
    assert all(isinstance(i["rpm"], list) for s in body["timeline"] for i in s["intervals"])


def test_quality_issues_are_warnings_and_never_refuse_a_draft(stage: Stage) -> None:
    draft = good_draft()
    # Standing for 3 minutes at 95 rpm: two issues the report flags.
    draft["segments"][1]["intervals"][0] = {
        "sec": 180,
        "zone": 5,
        "rpm": [90, 100],
        "standing": True,
    }
    draft["segments"][1]["intervals"][1]["standing"] = False
    body, done = call(stage, "submit_draft", {"draft": draft})
    assert done.ok
    assert done.final
    codes = {issue["code"] for issue in body["quality"]["issues"]}
    assert {"standing_too_long", "standing_rpm"} <= codes
    assert done.output.quality == body["quality"]


def test_submit_refuses_what_validate_refuses(stage: Stage) -> None:
    draft = good_draft()
    draft["segments"][-1]["intervals"] = [
        {"sec": 30, "zone": 1, "rpm": [80, 90], "standing": False}
    ]
    body, done = call(stage, "submit_draft", {"draft": draft})
    assert (done.error, done.final, done.output) == ("invalid_draft", True, None)
    assert body["messages"] == ["cooldown must be at least 60 s"]


def test_an_ftp_test_of_the_wrong_length_is_refused(stage: Stage) -> None:
    draft = good_draft()
    draft["segments"][1]["intervals"][0]["ftp_test"] = 5
    body, _ = call(stage, "preview_draft", {"draft": draft})
    assert body["error"] == "invalid_draft"
    assert "FTP test lasts 300 s" in body["messages"][0]


def test_the_result_is_computed_by_the_backend(stage: Stage, data: MusicData) -> None:
    _, done = call(stage, "submit_draft", {"draft": good_draft()})
    result = done.output
    assert result.tss == round(workout.tss(result.spec), 1)
    assert result.duration_sec == workout.total_seconds(result.spec)
    workout.validate(result.spec)
    assert (result.map_version, result.crossfade_sec) == (12, spotify.CROSSFADE_SEC)


# --- crossfade ------------------------------------------------------------------------------


def test_a_stored_map_without_crossfade_is_converted_exactly(data: MusicData) -> None:
    old = legacy(data)
    assert "crossfade" not in old.analysis["map"]
    assert music_map(old.analysis["map"]) == music_map(data.analysis["map"])
    assert music_map(data.analysis["map"])["crossfade"] == spotify.CROSSFADE_SEC
    assert music_map(old.analysis["map"])["duration"] == 8 * 240 - 7 * 9


def test_model_input_and_tools_use_the_crossfade_for_a_legacy_map(data: MusicData) -> None:
    new = DesignContext.build(brief(), data)
    old = DesignContext.build(brief(), legacy(data))
    assert old.summary == new.summary
    assert old.summary["crossfade_sec"] == 9.0
    assert old.summary["tracks"][1]["from"] == "3:51"  # 240 - 9 s
    draft = {"draft": good_draft()}
    for tool in ("preview_draft", "submit_draft"):
        assert call(design_stage(old), tool, draft)[0] == call(design_stage(new), tool, draft)[0]
    assert check(WorkoutDraft.model_validate(good_draft()), old.map).crossfade_sec == 9.0


# --- warnings -------------------------------------------------------------------------------


def test_the_librarys_crossfade_advice_and_availability_texts_never_reach_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = analysed(monkeypatch)
    data = MusicData(data.analysis, data.playlist_id, ["Track X is not playable in SE"])
    m = music_map(data.analysis["map"])
    entry = {"map": m, "unavailable": list(data.unavailable)}
    library_text = " ".join(service._spotify_warnings(entry))  # pyright: ignore[reportPrivateUsage]
    assert '"crossfade": 0' in library_text  # what the library would say
    assert "not playable" in library_text
    stage = design_stage(DesignContext.build(brief(), data))
    for tool in ("preview_draft", "submit_draft"):
        body, _ = call(stage, tool, {"draft": good_draft()})
        text = json.dumps(body)
        assert 'crossfade\\": 0' not in text
        assert '"crossfade": 0' not in text
        assert "in music" not in text
        assert "not playable" not in text
        assert body["warnings"][0].startswith("Timing assumes the playlist plays in order")


def test_tracks_without_bpm_are_named_by_index(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentfake import synthetic_tracks

    lib = synthetic_tracks()
    lib.tracks[3]["BPM"] = 0
    monkeypatch.setattr("mowl.library.BPM_WAIT_SEC", 0)
    data = analysed(monkeypatch, lib)
    body, _ = call(
        design_stage(DesignContext.build(brief(), data)), "preview_draft", {"draft": good_draft()}
    )
    assert "MOWL has no BPM yet for the tracks with index 3; " in " ".join(body["warnings"])
    assert "Invented Song 3" not in " ".join(body["warnings"])


def test_a_workout_longer_than_the_music_is_a_warning(stage: Stage) -> None:
    draft = good_draft()
    draft["segments"][0]["intervals"][0]["sec"] = 600
    body, done = call(stage, "preview_draft", {"draft": draft})
    assert done.ok
    assert body["warnings"][0] == "The workout (37:00) is longer than the music (30:57)."

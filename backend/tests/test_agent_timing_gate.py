"""The design validator refuses beat-, bar- and drop-precise cues on a track that is not ``exact``
(ADR 0040 section 3, issue #1303 stage C): ``design.check`` and ``running.check_running`` (and so
the design, revise, composer and refit paths that call them), the model's view of each track's
class, and a class whose tracks are all ``exact`` checked exactly as before. Fake data only."""

import json
from typing import Any

import pytest

from agentfake import analysed, brief
from classbeat.agent import DesignContext, design_stage, mix_input
from classbeat.agent import track_timing_calc as tt
from classbeat.agent.design import check
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.model_input import MusicData, music_map
from classbeat.agent.provider import ToolCall
from classbeat.agent.running import check_running
from classbeat.agent.schemas import RunningDraft, WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.agent.timing_input import TIMING_NOTE


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch) -> MusicData:
    return analysed(monkeypatch)  # 8 tracks of 240 s, sections every 60 s, 9 s crossfade


def stamped(data: MusicData, **classes: tuple[str, str]) -> MusicData:
    """``data`` whose tracks ``t<index>`` carry ``(class, source)``, as stage B stamps them."""
    m = dict(data.analysis["map"])
    for name, (cls, source) in classes.items():
        m = tt.stamp(m, int(name[1:]), cls, source)  # pyright: ignore[reportArgumentType]
    return MusicData({**data.analysis, "map": m}, data.playlist_id, data.unavailable)


def interval(sec: int, zone: int, cue: str = "") -> dict[str, Any]:
    return {"sec": sec, "zone": zone, "rpm": [85, 95], "standing": False, "cue": cue}


def draft(first: str = "", at_60: str = "", at_90: str = "") -> WorkoutDraft:
    """Cues at 0:00 (a section start), 1:00 (a section start and a drop) and 1:30 (mid-section),
    all on track 0."""
    return WorkoutDraft.model_validate(
        {
            "name": "Invented ride",
            "segments": [
                {
                    "name": "Warm-up",
                    "kind": "warmup",
                    "intervals": [interval(60, 2, first), interval(30, 3, at_60)],
                },
                {
                    "name": "Work",
                    "kind": "main",
                    "intervals": [interval(30, 4, at_90), interval(120, 3)],
                },
                {"name": "Cooldown", "kind": "cooldown", "intervals": [interval(300, 1)]},
            ],
        }
    )


def refused(d: WorkoutDraft, m: dict[str, Any]) -> list[str]:
    with pytest.raises(ToolError) as caught:
        check(d, m)
    assert caught.value.code == "invalid_draft"
    return list(caught.value.messages)


PRECISE = ["3, 2, 1, go!", "Push for 4 bars", "Sprint on the drop!"]


# --- exact: as before ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", PRECISE)
def test_an_exact_class_takes_precise_cues_exactly_as_before(data: MusicData, text: str) -> None:
    d = draft(text, text, text)
    before = check(d, music_map(data.analysis["map"]))
    exact = mix_input.music_map(stamped(data, t0=("exact", "upload")))
    assert check(d, exact) == before  # the same result, field by field


def test_a_map_without_classes_gives_the_model_the_same_input(data: MusicData) -> None:
    plain = DesignContext.build(brief(), data)
    exact = DesignContext.build(brief(), stamped(data, t0=("exact", "upload")))
    assert exact.first_message() == plain.first_message()
    assert "timing" not in json.dumps(plain.summary)
    d = draft("Easy", "Up", "Push")
    assert check(d, plain.map).timeline == check(d, exact.map).timeline


# --- approximate and unusable: refused ----------------------------------------------------------


@pytest.mark.parametrize("cls", ["approximate", "unusable"])
@pytest.mark.parametrize("text", PRECISE)
def test_a_precise_cue_on_a_non_exact_track_is_refused(
    data: MusicData, cls: str, text: str
) -> None:
    m = mix_input.music_map(stamped(data, t0=(cls, "youtube")))
    (problem,) = refused(draft(at_60=text), m)
    assert "interval 2 (from 1:00) on track 0" in problem
    assert cls in problem


@pytest.mark.parametrize("cls", ["approximate", "unusable"])
def test_a_cue_mid_section_on_a_non_exact_track_is_refused(data: MusicData, cls: str) -> None:
    m = mix_input.music_map(stamped(data, t0=(cls, "youtube")))
    (problem,) = refused(draft(at_90="Stand up"), m)
    assert "interval 3 (from 1:30)" in problem
    assert "section start" in problem


@pytest.mark.parametrize("cls", ["approximate", "unusable"])
def test_a_section_level_cue_at_a_section_start_is_accepted(data: MusicData, cls: str) -> None:
    m = mix_input.music_map(stamped(data, t0=(cls, "youtube")))
    result = check(draft("Easy start", "Big chorus, push"), m)
    assert result.duration_sec == 540


def test_a_youtube_track_stamped_exact_is_still_refused(data: MusicData) -> None:
    """A confident match, an equal ISRC and agreeing anchors never make YouTube exact."""
    m = mix_input.music_map(stamped(data, t0=("exact", "youtube")))
    assert refused(draft(at_60="3, 2, 1"), m)


def test_only_the_non_exact_track_is_checked(data: MusicData) -> None:
    m = mix_input.music_map(stamped(data, t3=("approximate", "youtube")))
    assert check(draft("3, 2, 1", "On the drop", "4 bars"), m).duration_sec == 540


def test_every_refused_cue_comes_back_at_once(data: MusicData) -> None:
    m = mix_input.music_map(stamped(data, t0=("approximate", "youtube")))
    assert len(refused(draft("3, 2, 1", "On the drop", "Stand up"), m)) == 3


def test_the_design_stage_returns_the_refusal_as_a_correction(data: MusicData) -> None:
    stage = design_stage(DesignContext.build(brief(), stamped(data, t0=("approximate", "youtube"))))
    args = {"draft": draft(at_60="3, 2, 1").model_dump(mode="json")}
    for tool in ("preview_draft", "submit_draft"):
        done = dispatch(stage, ToolCall("c1", tool, args))
        body = json.loads(done.result.content)
        assert done.result.is_error, body
        assert body["error"] == "invalid_draft", body
        assert done.output is None


def test_a_running_draft_is_checked_the_same_way(data: MusicData) -> None:
    m = mix_input.music_map(stamped(data, t0=("approximate", "youtube")))

    def run(cue: str) -> RunningDraft:
        return RunningDraft.model_validate(
            {
                "name": "Invented run",
                "segments": [
                    {
                        "name": "Warm-up",
                        "kind": "warmup",
                        "intervals": [{"sec": 60, "zone": 1}, {"sec": 60, "zone": 2, "cue": cue}],
                    },
                    {
                        "name": "Cooldown",
                        "kind": "cooldown",
                        "intervals": [{"sec": 300, "zone": 1}],
                    },
                ],
            }
        )

    assert check_running(run("Easy pace"), m).duration_sec == 420
    with pytest.raises(ToolError) as caught:
        check_running(run("3, 2, 1"), m)
    assert caught.value.code == "invalid_draft"


# --- what the model sees ------------------------------------------------------------------------


def test_the_model_sees_each_non_exact_track_class(data: MusicData) -> None:
    ctx = DesignContext.build(
        brief(), stamped(data, t0=("approximate", "youtube"), t2=("unusable", "youtube"))
    )
    marks = {t["index"]: t.get("timing") for t in ctx.summary["tracks"]}
    assert marks[0] == "approximate"
    assert marks[2] == "unusable"
    assert marks[1] is None
    assert ctx.summary["timing_note"] == TIMING_NOTE
    assert mix_input.track_detail(ctx.map, 0)["timing"] == "approximate"
    assert "timing" not in mix_input.track_detail(ctx.map, 1)
    first = check(draft("Easy"), ctx.map).timeline[0]["intervals"][0]
    assert first["track"] == 0
    assert first["timing"] == "approximate"
    assert '"timing":"approximate"' in ctx.first_message()

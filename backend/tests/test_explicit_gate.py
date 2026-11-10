"""The stated values in the stages (ADR 0029 section 4): the closed block in the first message,
the stage prompts' sentence, and the gate that refuses a draft that misses a stated value twice
before recording it as Not as asked. Pure: soundless stages, no model, no music."""

import json
from typing import Any

import pytest

from agentfake import analysed, brief, good_draft, script
from classbeat.agent import soundless_input
from classbeat.agent.brief import Brief
from classbeat.agent.design import DesignContext
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.explicit_input import (
    DEFAULT_CORRECTIONS,
    EXPLICIT_WINS,
    ExplicitGate,
    with_explicit,
)
from classbeat.agent.provider import ToolCall
from classbeat.agent.revise import ReviseContext
from classbeat.agent.schemas import RunningDraft, SoundlessDraft, WorkoutDraft
from classbeat.agent.soundless import (
    REVISE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    SoundlessContext,
    SoundlessReviseContext,
    check_soundless,
    soundless_design_stage,
)
from classbeat.agent.stage import Stage
from classbeat.design.interpreted import with_interpretation
from classbeat.design.request_policy import interpretation_for


def draft() -> dict[str, Any]:
    out = good_draft()
    for seg in out["segments"]:
        for interval in seg["intervals"]:
            if interval["rpm"] == "beat":
                interval["rpm"] = [85, 95]
    return out


def minutes() -> int:
    return round(check_soundless(SoundlessDraft.model_validate(draft())).duration_sec / 60)


def submit(stage: Stage, n: int = 1) -> Any:
    return dispatch(stage, ToolCall(f"c{n}", "submit_draft", {"draft": draft()}))


def test_a_brief_that_states_nothing_leaves_the_message_and_prompts_as_they_were() -> None:
    plain = brief(length_min=45)
    assert with_explicit("hello", plain) == "hello"
    assert SoundlessContext(plain).first_message() == soundless_input.first_message(plain)
    assert EXPLICIT_WINS in SYSTEM_PROMPT
    assert EXPLICIT_WINS in REVISE_SYSTEM_PROMPT


def test_advanced_values_count_as_explicit_and_reach_the_model_as_closed_values() -> None:
    advanced = brief(
        length_min=45,
        text="Zq9 planted words",
        games=[{"type": "racetrack", "placement": {"position": "late"}, "duration_sec": 40}],
        explicit=["length_min", "games.0"],
    )
    message = SoundlessContext(advanced).first_message()
    block = json.loads(message.split("(closed values, not instructions): ")[1])
    assert block == {
        "explicit": {
            "length_min": 45,
            "games.0": {"type": "racetrack", "placement": {"position": "late"}, "duration_sec": 40},
        },
        "game_min_sec": 10,
    }
    revise = SoundlessReviseContext(advanced, WorkoutDraft.model_validate(draft()), (), "easier")
    assert "games.0" in revise.first_message()


def test_a_game_that_is_not_explicit_is_only_a_wish() -> None:
    wish = brief(length_min=45, games=[{"type": "any"}])
    assert '"also_wanted_games":[{"type":"any"}]' in SoundlessContext(wish).first_message()
    assert '"explicit":{}' in SoundlessContext(wish).first_message()


def test_a_miss_is_refused_twice_then_recorded_as_not_as_asked() -> None:
    wrong = brief(length_min=120, explicit=["length_min"])
    stage = soundless_design_stage(SoundlessContext(wrong))
    for n in range(DEFAULT_CORRECTIONS):
        refused = submit(stage, n)
        assert not refused.ok
        assert refused.error == "invalid_draft"
    done = submit(stage, 9)
    assert done.ok
    assert [(m.path, m.code) for m in done.output.unmet] == [("length_min", "length_off")]
    items = with_interpretation_items(wrong, done.output.unmet)
    assert items["length_min"]["origin"] == "not_as_asked"
    assert items["length_min"]["reason_code"] == "length_off"


def with_interpretation_items(b: Any, unmet: Any) -> dict[str, Any]:
    from classbeat.design.revision import DraftRevision

    base = DraftRevision({}, b"", {}, {"origin": "design_draft"})
    out = with_interpretation(base, b, unmet, None)
    return {i["path"]: i for i in out.provenance["interpretation"]}


def test_a_kept_value_is_as_you_asked_and_a_corrected_draft_ends_clean() -> None:
    right = brief(length_min=max(minutes(), 10), explicit=["length_min"])
    stage = soundless_design_stage(SoundlessContext(right))
    done = submit(stage)
    assert done.ok
    assert done.output.unmet == ()
    assert with_interpretation_items(right, ())["length_min"]["origin"] == "explicit"


def test_nothing_the_model_wrote_reaches_a_later_prompt_or_the_record() -> None:
    wrong = brief(length_min=120, text="Zq9 planted", explicit=["length_min"])
    stage = soundless_design_stage(SoundlessContext(wrong))
    for n in range(DEFAULT_CORRECTIONS):
        message = json.loads(submit(stage, n).result.content)
        assert "Zq9" not in json.dumps(message)
    done = submit(stage, 9)
    record = json.dumps(with_interpretation_items(wrong, done.output.unmet))
    assert "Zq9" not in record


def test_the_gate_never_spends_more_than_the_runs_corrections() -> None:
    wrong = brief(length_min=120, explicit=["length_min"])
    ctx = SoundlessContext(wrong)
    stage = soundless_design_stage(ctx)
    assert stage.bind_corrections is not None
    stage.bind_corrections(1)  # the loop does this with Limits.max_corrections
    assert not submit(stage, 1).ok
    assert submit(stage, 2).ok  # the budget is spent: the miss is recorded, the job goes on
    stage.bind_corrections(0)
    assert ExplicitGate(wrong, 0).review({"segments": []}) == ()


def test_corrections_spent_on_other_errors_leave_less_for_a_miss() -> None:
    wrong = brief(length_min=120, explicit=["length_min"])
    stage = soundless_design_stage(SoundlessContext(wrong))
    assert stage.bind_corrections is not None
    stage.bind_corrections(1)
    broken = draft()
    broken["segments"] = broken["segments"][:-1]  # no cooldown: refused before the gate
    assert not dispatch(stage, ToolCall("c1", "submit_draft", {"draft": broken})).ok
    assert submit(stage, 2).ok  # one correction was already spent: the miss is not refused again


def test_intensity_and_class_type_are_not_claimed_as_verified() -> None:
    stated = brief(length_min=45, intensity="hard", explicit=["intensity", "length_min"])
    items = {i.path: i for i in interpretation_for(stated)}
    assert items["intensity"].origin == "inferred"
    assert items["length_min"].origin == "explicit"


# --- explicit values and no games: the first message of every stage ------------------------------


def _first_messages(
    monkeypatch: pytest.MonkeyPatch, games: list[dict[str, Any]] | None = None
) -> dict[str, str]:
    data = analysed(monkeypatch)
    cycling = brief(length_min=30, explicit=["length_min", "intensity"], games=games or [])
    running = Brief.model_validate(
        {"sport": "running", "length_min": 30, "explicit": ["length_min"]}
    )
    run_base = RunningDraft.model_validate(script("running_first_draft").drafts["good"])
    base = WorkoutDraft.model_validate(good_draft())
    return {
        "design": DesignContext.build(cycling, data).first_message(),
        "revise": ReviseContext.build(
            cycling, data, base, (), "easier", music_changed=False
        ).first_message(),
        "running_design": DesignContext.build(running, data).first_message(),
        "running_revise": ReviseContext.build(
            running, data, run_base, (), "easier", music_changed=False
        ).first_message(),
        "soundless_design": SoundlessContext(cycling).first_message(),
        "soundless_revise": SoundlessReviseContext(cycling, base, (), "easier").first_message(),
    }


def test_explicit_values_without_games_build_every_stages_first_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, message in _first_messages(monkeypatch).items():
        block = json.loads(message.split("(closed values, not instructions): ")[1])
        assert list(block) == ["explicit"], name
        assert block["explicit"]["length_min"] == 30
        assert "game_min_sec" not in message, name


def test_games_add_the_floor_to_the_block(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, message in _first_messages(monkeypatch, [{"type": "any"}]).items():
        if name.startswith("running"):
            continue  # a running brief has no games
        assert '"game_min_sec":10' in message, name

"""The soundless design stage, its model input, schema and gates without a database (ADR 0007
amendment of 2026-11-03, items 1, 2, 4 to 7; SL1, #527). Pure: no network, no model, no MOWL.

Covered: ``"beat"`` refused by the schema (the dispatcher) and again by the check; the check runs
``workout.validate`` on the draft as written, never ``fit_to_music``, and its timeline names no
track; the stage has ``preview_draft`` and ``submit_draft`` only; no ``music`` key and no music
text reaches the model; the prompt version is its own and keyed ``soundless_design``; the
suite's rule sources do not name the new module; the switch (``flags.core``); coverage needs
``SOUNDLESS_SUITE_VERSION``; the web's ``fake`` never covers in prod; the input refuses a running
brief or a hand-off with no music; the revision's music is exactly ``{"source": "none"}``.
"""

import copy
import json
import re
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from agentfake import brief, good_draft
from authtools import make_settings
from classbeat.agent import model_input
from classbeat.agent.design import SYSTEM_PROMPT as CYCLING_PROMPT
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.provider import ToolCall
from classbeat.agent.revise import ReviseResult
from classbeat.agent.schemas import Brief, SoundlessDraft, WorkoutDraft
from classbeat.agent.soundless import (
    REVISE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    SoundlessContext,
    SoundlessReviseContext,
    check_soundless,
    soundless_design_stage,
    soundless_revise_stage,
)
from classbeat.agent.soundless_input import NO_MUSIC, first_message
from classbeat.agent.stage import Stage, ToolError
from classbeat.design.kind import DesignDraftInput, NoMusicChoice
from classbeat.design.music_kind import NO_MUSIC as NO_MUSIC_SOURCE
from classbeat.design.music_kind import SoundlessGate, fake_covers, is_none
from classbeat.design.revision import NoRevisionMusic
from classbeat.evals import suite
from classbeat.flags import core
from classbeat.models import routes
from classbeat.models.versions import GROUP_STAGES, all_prompt_versions
from classbeat.plan_gates import PlanGates
from classbeat.plans.tables import SETTING_KEYS

# Music words that must never reach a soundless model (item 5), as whole words: the game
# ``racetrack`` is no track.
MUSIC_WORDS = ("playlist", "track", "bpm", "drop", "breakdown", "section", "beat", "song")


def mentions(text: str, word: str) -> bool:
    return re.search(rf"\b{word}s?\b", text.lower()) is not None


def soundless_draft() -> dict[str, Any]:
    """The cycling test draft with a range in place of every ``"beat"``."""
    draft = good_draft()
    for seg in draft["segments"]:
        for interval in seg["intervals"]:
            if interval["rpm"] == "beat":
                interval["rpm"] = [85, 95]
    return draft


def stage(gates: PlanGates | None = None) -> Stage:
    return soundless_design_stage(SoundlessContext(brief(), gates))


def call(s: Stage, name: str, args: Any) -> dict[str, Any]:
    return json.loads(dispatch(s, ToolCall("c1", name, args)).result.content)


# --- the schema and the check ------------------------------------------------------------------


def test_the_schema_refuses_beat_and_the_dispatcher_answers_invalid_input() -> None:
    beat = soundless_draft()
    beat["segments"][0]["intervals"][0]["rpm"] = "beat"
    with pytest.raises(ValidationError):
        SoundlessDraft.model_validate(beat)
    refused = call(stage(), "preview_draft", {"draft": beat})
    assert (refused["ok"], refused["error"]) == (False, "invalid_input")
    schema = json.dumps(SoundlessDraft.model_json_schema())
    assert "beat" not in schema.lower()  # never offered either


def test_the_check_refuses_beat_again_as_a_second_line() -> None:
    beat = soundless_draft()
    beat["segments"][0]["intervals"][0]["rpm"] = "beat"
    with pytest.raises(ToolError) as caught:
        check_soundless(WorkoutDraft.model_validate(beat))  # past the schema, as a mutation would
    assert caught.value.code == "invalid_draft"
    # Refused by the soundless check itself, before the library sees it.
    assert caught.value.messages == ('a class without music has no "beat" cadence',)


def test_the_check_validates_the_draft_as_written_with_no_fitting_and_no_track() -> None:
    draft = SoundlessDraft.model_validate(soundless_draft())
    result = check_soundless(draft)
    assert result.spec == draft.workout_draft().to_spec()  # not fitted: lengths as written
    assert result.duration_sec == 1800
    assert (result.map_version, result.crossfade_sec) == (None, None)
    assert (result.quality, result.warnings) == ({}, [])
    assert isinstance(result.draft, WorkoutDraft)
    intervals = [i for seg in result.timeline for i in seg["intervals"]]
    assert intervals[0] == {
        "start": "0:00",
        "sec": 180,
        "zone": 2,
        "rpm": [85, 95],
        "standing": False,
    }
    assert all("track" not in i and "on" not in i for i in intervals)
    no_cooldown = soundless_draft()
    no_cooldown["segments"][-1]["kind"] = "main"
    with pytest.raises(ToolError) as caught:
        check_soundless(SoundlessDraft.model_validate(no_cooldown))
    assert caught.value.code == "invalid_draft"


def test_the_stage_offers_preview_and_submit_only_and_keeps_the_plan_gates() -> None:
    s = stage()
    assert s.name == "design"
    assert [t.name for t in s.tools] == ["preview_draft", "submit_draft"]
    preview = call(s, "preview_draft", {"draft": soundless_draft()})
    assert preview["ok"] is True
    assert set(preview) >= {"timeline", "duration_sec", "tss", "tss_approximate"}
    assert "quality" not in preview
    assert "warnings" not in preview
    assert "rules" not in preview
    done = dispatch(s, ToolCall("c2", "submit_draft", {"draft": soundless_draft()}))
    assert done.ok
    assert done.final
    short = PlanGates(max_class_minutes=10, games=True, races=True, ftp_tests=True)
    refused = call(stage(short), "submit_draft", {"draft": soundless_draft()})
    assert (refused["ok"], refused["error"]) == (False, "not_allowed")  # within_plan unchanged


def test_a_running_brief_never_gets_the_soundless_stage() -> None:
    running = Brief.model_validate({"sport": "running", "length_min": 30})
    with pytest.raises(ValueError, match="cycling"):
        soundless_design_stage(SoundlessContext(running))


# --- what the model sees -----------------------------------------------------------------------


def test_the_model_input_has_no_music_key_and_no_music_text() -> None:
    told = first_message(brief(text="Hard but fun", style="Rock"))
    head, body = told.split("\n\n", 1)
    assert NO_MUSIC in head
    payload = json.loads(body)
    assert payload == {
        "brief": model_input.brief_for_model(brief(text="Hard but fun", style="Rock"))
    }
    assert "music" not in payload
    # The prompt and the first message say only that there is no music, naming none of it.
    for word in MUSIC_WORDS:
        assert not mentions(told, word), word
        assert not mentions(SYSTEM_PROMPT, word), word
    tools = json.dumps([t.input_model.model_json_schema() for t in stage().tools])
    tools += " ".join(t.description for t in stage().tools)
    for word in (*MUSIC_WORDS, "music"):  # the tool definitions do not even say "music"
        assert not mentions(tools, word), word


def test_the_soundless_prompt_version_is_its_own_and_in_the_design_group() -> None:
    versions = all_prompt_versions()
    assert versions["soundless_design"] == stage().prompt_version
    assert versions["soundless_design"] not in (versions["design"], versions["running_design"])
    assert "soundless_design" in GROUP_STAGES["design"]
    assert SYSTEM_PROMPT != CYCLING_PROMPT


def test_the_input_module_is_a_rule_source_of_the_suite() -> None:
    """Item 6: SL5 (#531) added ``agent/soundless_input.py`` with the suite bump to 11; the stage
    module's prompts are covered by the prompt versions, not by the digest."""
    assert "agent/soundless_input.py" in suite.RULE_SOURCES
    assert "agent/soundless.py" not in suite.RULE_SOURCES


# --- the flag and coverage ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environment", "stored", "on"),
    [
        (False, "on", False),  # the kill switch wins
        (True, None, False),  # nothing stored (or the cleared JSON null) is off, unlike running
        (True, "off", False),
        (True, "yes", False),  # invalid: off
        (True, 1, False),
        (True, "on", True),
    ],
)
def test_the_switch_is_on_only_with_the_environment_and_a_stored_on(
    environment: bool, stored: Any, on: bool
) -> None:
    assert core.soundless_switch(environment, stored) is on


def test_the_key_is_a_flag_the_admin_may_store_off_or_on() -> None:
    assert "soundless_enabled" in core.KEYS
    assert "soundless_enabled" in SETTING_KEYS
    for value in ("off", "on"):
        assert core.validate_for_write("soundless_enabled", value, trial_days_max=30) == value
    for bad in ("yes", None, True, 1, {"value": "on"}):
        with pytest.raises(core.FlagRefused):
            core.validate_for_write("soundless_enabled", bad, trial_days_max=30)


def test_a_suite_version_covers_soundless_from_the_first_with_soundless_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SL5 (#531) set it to 11: the suite version whose design group holds the soundless cases."""
    assert routes.SOUNDLESS_SUITE_VERSION == 11
    assert [routes.soundless_suite(v) for v in (1, 10, 11, 12)] == [False, False, True, True]
    monkeypatch.setattr(routes, "SOUNDLESS_SUITE_VERSION", None)  # the SL1 state: nothing covers
    assert not any(routes.soundless_suite(v) for v in (1, 10, 11, 10_000))


def test_a_bootstrap_choice_covers_neither_running_nor_soundless() -> None:
    choice = routes.ModelChoice("anthropic", "claude-opus-5-5", "medium")
    assert (choice.running_covered, choice.soundless_covered) == (False, False)


@pytest.mark.parametrize(
    ("provider", "environment", "covers"),
    [
        ("fake", "dev", True),
        ("fake", "test", True),
        ("fake", "prod", False),  # never in prod
        ("anthropic", "dev", False),
        ("anthropic", "prod", False),
    ],
)
def test_the_webs_fake_covers_soundless_only_outside_prod(
    provider: str, environment: str, covers: bool
) -> None:
    base = make_settings("postgresql+psycopg://app@127.0.0.1:1/classbeat")
    made = base.model_copy(
        update={"model_provider": provider, "environment": environment, "soundless_enabled": True}
    )
    assert fake_covers(made) is covers
    assert SoundlessGate.from_settings(made) == SoundlessGate(enabled=True, fake_covers=covers)


# --- the input and the revision ----------------------------------------------------------------


def _input(**overrides: Any) -> dict[str, Any]:
    return {
        "title": "Quiet",
        "brief": {"length_min": 30},
        "music": {"source": "none"},
        "mowl_connection_id": str(uuid.uuid4()),
        **overrides,
    }


def test_no_music_is_a_cycling_choice_only_and_never_a_hand_off() -> None:
    parsed = DesignDraftInput.model_validate(_input())
    assert isinstance(parsed.music, NoMusicChoice)
    assert parsed.music.model_dump(mode="json") == NO_MUSIC_SOURCE == {"source": "none"}
    with pytest.raises(ValidationError):
        DesignDraftInput.model_validate(_input(brief={"sport": "running", "length_min": 30}))
    with pytest.raises(ValidationError):
        DesignDraftInput.model_validate(_input(handoff_id=str(uuid.uuid4())))
    with pytest.raises(ValidationError):
        DesignDraftInput.model_validate(_input(music={"source": "none", "playlist": "x"}))


def test_the_revision_music_is_exactly_the_source() -> None:
    assert NoRevisionMusic(source="none").model_dump(mode="json") == {"source": "none"}
    with pytest.raises(ValidationError):
        NoRevisionMusic.model_validate({"source": "none", "link_id": str(uuid.uuid4())})
    assert is_none({"source": "none"})
    assert not is_none(None)
    assert not is_none({"source": "mowl_spotify_playlist"})
    assert not is_none("none")
    assert copy.deepcopy(NO_MUSIC_SOURCE) == {"source": "none"}


# --- the soundless revise stage (SL2, #528) ------------------------------------------------------


def revise_ctx(request: str = "Make the start easier.") -> SoundlessReviseContext:
    return SoundlessReviseContext(
        brief(), WorkoutDraft.model_validate(soundless_draft()), ("Earlier ask",), request
    )


def test_the_revise_stage_has_two_tools_and_no_songs_requested() -> None:
    s = soundless_revise_stage(revise_ctx())
    assert s.name == "revise"
    assert [t.name for t in s.tools] == ["preview_draft", "submit_draft"]
    assert s.final_tool == "submit_draft"
    assert s.also_final == ()
    gone = dispatch(s, ToolCall("c1", "songs_requested", {}))
    assert not gone.ok  # an unknown tool: the cycling exit cannot end a soundless revise
    assert json.loads(gone.result.content)["error"] == "unknown_tool"


def test_the_revise_submit_ends_with_the_checked_draft_and_the_note_and_refuses_beat() -> None:
    s = soundless_revise_stage(revise_ctx())
    done = dispatch(
        s, ToolCall("c2", "submit_draft", {"draft": soundless_draft(), "note": "Eased it."})
    )
    assert done.ok
    assert done.final
    assert isinstance(done.output, ReviseResult)
    assert done.output.note == "Eased it."
    assert done.output.result.map_version is None
    beat = soundless_draft()
    beat["segments"][0]["intervals"][0]["rpm"] = "beat"
    assert not dispatch(s, ToolCall("c3", "submit_draft", {"draft": beat})).ok
    short = PlanGates(max_class_minutes=10, games=True, races=True, ftp_tests=True)
    gated = SoundlessReviseContext(
        brief(), WorkoutDraft.model_validate(soundless_draft()), (), "x", short
    )
    refused = call(soundless_revise_stage(gated), "submit_draft", {"draft": soundless_draft()})
    assert (refused["ok"], refused["error"]) == (False, "not_allowed")


def test_the_revise_input_has_no_music_key_and_no_music_text() -> None:
    ctx = revise_ctx("Make the start easier.")
    head, body = ctx.first_message().split("\n\n", 1)
    assert NO_MUSIC in head
    payload = json.loads(body)
    assert set(payload) == {"brief", "base_draft", "earlier_requests", "request"}
    assert payload["earlier_requests"] == ["Earlier ask"]
    s = soundless_revise_stage(ctx)
    tools = json.dumps([t.input_model.model_json_schema() for t in s.tools])
    tools += " ".join(t.description for t in s.tools)
    for word in MUSIC_WORDS:
        assert not mentions(head, word), word
        assert not mentions(REVISE_SYSTEM_PROMPT, word), word
    for word in (*MUSIC_WORDS, "music"):
        assert not mentions(tools, word), word


def test_a_running_brief_never_gets_the_soundless_revise_stage() -> None:
    running = Brief.model_validate({"sport": "running", "length_min": 30})
    with pytest.raises(ValueError, match="cycling"):
        soundless_revise_stage(
            SoundlessReviseContext(running, WorkoutDraft.model_validate(soundless_draft()), (), "x")
        )


def test_the_soundless_revise_prompt_version_is_its_own_and_in_the_design_group() -> None:
    versions = all_prompt_versions()
    assert versions["soundless_revise"] == soundless_revise_stage(revise_ctx()).prompt_version
    others = [v for k, v in versions.items() if k != "soundless_revise"]
    assert versions["soundless_revise"] not in others
    assert "soundless_revise" in GROUP_STAGES["design"]
    assert len(GROUP_STAGES["design"]) == 6
    assert REVISE_SYSTEM_PROMPT != SYSTEM_PROMPT

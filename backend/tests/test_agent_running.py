"""Running classes at the model boundary (ADR 0018 sections 1, 2 and 5; M8 R1a, #463). No database,
no network, no model.

Covered: ``sport`` on the brief (absent is cycling, cycling dumps byte-identical, the stored form
explicit, a running brief refusing every bike-only knob and the ``ftp_test`` type); the running
draft schema refusing cycling's fields and anything extra; the running stages (own prompts and
prompt versions, the running schema in the dispatcher, the library's ``validate_running`` and an
approximate TSS); prompt separation both ways; the running model input (nothing new but the sport);
cycling's prompt versions, schemas and first message pinned to what they were before R1a; the
flag's core rule; the running rule profiles; the diff of two running drafts; the compose default.
"""

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from mowl import workout
from pydantic import ValidationError

from agentfake import analysed, brief, good_draft, script
from classbeat.agent import DesignContext, design_stage
from classbeat.agent import model_input as cycling_input
from classbeat.agent.design import SYSTEM_PROMPT as CYCLING_DESIGN_PROMPT
from classbeat.agent.design import check
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.model_input import MusicData
from classbeat.agent.provider import ToolCall
from classbeat.agent.records import Provenance
from classbeat.agent.revise import SYSTEM_PROMPT as CYCLING_REVISE_PROMPT
from classbeat.agent.revise import ReviseContext, ReviseResult, SongsRequested, revise_stage
from classbeat.agent.running import (
    DESIGN_SYSTEM_PROMPT,
    REVISE_SYSTEM_PROMPT,
    check_running,
    design_stage_for,
    revise_stage_for,
    running_design_stage,
    running_revise_stage,
)
from classbeat.agent.running_input import running_brief_for_model
from classbeat.agent.schemas import (
    Brief,
    DraftInput,
    ReviseInput,
    RunningDraft,
    RunningDraftInput,
    RunningReviseInput,
    WorkoutDraft,
    stored_draft,
)
from classbeat.agent.stage import Stage
from classbeat.design import diff
from classbeat.design.selection import select_profile_for_brief
from classbeat.design.sport_rules import rules_for, select_profile_for
from classbeat.evals import suite
from classbeat.flags import core
from classbeat.models.versions import all_prompt_versions

ROOT = Path(__file__).resolve().parents[2]
RUN = {"sport": "running", "length_min": 30, "intensity": "moderate"}
# The cycling stages' prompt versions and tool schemas on main before R1a (4838aaf): R1a changes
# none of them, so every recorded eval result and model route stays valid.
CYCLING_PROMPT_VERSIONS = {
    "design": "200aa3e3bdf1c98d0ad132c0f34ec199d893d6d887552e13e994ad39ca9d9618",
    "revise": "d334e6777b995feb4eaa6908e0a207f6bd6019fc73d6ca7071cbcecc5db6a978",
    "pick_pool": "58d36e6a25155e86a70e88b79c845ccd1501f7f2db4c7d28af9f6f78badb5449",
    "choose_songs": "4fb091519ef4af656c3fc32e424bc1b083db5531a7436e8976dc2e0e60a386db",
}
# Words of cycling's that a running prompt must never use (ADR 0018 section 2).
CYCLING_WORDS = re.compile(
    r"\b(rpm|cadence|standing|seated|games?|ftp|pace|climbs?|sprints?|races?|rides?|riding|"
    r"cycl\w*|bikes?)\b",
    re.IGNORECASE,
)


def running_brief(**more: Any) -> Brief:
    return Brief.model_validate({**RUN, **more})


def running_draft() -> dict[str, Any]:
    return json.loads(json.dumps(script("running_first_draft").drafts["good"]))


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch) -> MusicData:
    return analysed(monkeypatch)


@pytest.fixture
def ctx(data: MusicData) -> DesignContext:
    return DesignContext.build(running_brief(), data)


def call(stage: Stage, name: str, args: Any) -> dict[str, Any]:
    return json.loads(dispatch(stage, ToolCall("c1", name, args)).result.content)


# --- the brief ----------------------------------------------------------------------------------


def test_a_brief_without_sport_is_cycling_and_dumps_byte_identical() -> None:
    legacy = {"length_min": 45, "intensity": "hard", "climbs": 2, "sprints": 1, "races": 1}
    made = Brief.model_validate(legacy)
    assert made.sport == "cycling"
    assert made.model_dump(mode="json") == {
        "length_min": 45, "intensity": "hard", "climbs": 2, "sprints": 1, "races": 1,
        "ftp_test": None, "style": "", "text": "",
    }  # fmt: skip
    explicit = Brief.model_validate({**legacy, "sport": "cycling"})
    assert explicit.model_dump(mode="json") == made.model_dump(mode="json")
    assert made.stored() == {**made.model_dump(mode="json"), "sport": "cycling"}


@pytest.mark.parametrize("bad", ["swimming", "Running", "", 1, None, True])
def test_the_sport_is_one_of_two_strings(bad: Any) -> None:
    with pytest.raises(ValidationError):
        Brief.model_validate({"length_min": 30, "sport": bad})


@pytest.mark.parametrize(
    "knob", [{"climbs": 1}, {"sprints": 2}, {"races": 1}, {"ftp_test": 20}, {"climbs": 0}]
)
def test_a_running_brief_refuses_every_bike_only_knob_even_at_its_default(
    knob: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="a running brief has no"):
        Brief.model_validate({**RUN, **knob})


def test_a_running_brief_refuses_the_ftp_test_class_type_and_keeps_the_others() -> None:
    with pytest.raises(ValidationError, match="ftp_test class type"):
        running_brief(class_type="ftp_test")
    for kind in ("plain", "interval", "hiit", "journey"):
        assert running_brief(class_type=kind).class_type == kind


def test_a_running_brief_is_written_without_the_knobs_and_validates_again() -> None:
    made = running_brief(style="hills", text="Two hill blocks")
    dumped = made.model_dump(mode="json")
    assert dumped == {
        "sport": "running", "length_min": 30, "intensity": "moderate", "style": "hills",
        "text": "Two hill blocks",
    }  # fmt: skip
    assert made.stored() == dumped
    assert Brief.model_validate(dumped) == made
    assert Brief.model_validate(made.stored()) == made


# --- the running draft schema --------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"rpm": [60, 70]},
        {"rpm": "beat"},
        {"standing": False},
        {"game": "racetrack"},
        {"ftp_test": 20},
        {"pace": 5},
        {"acc": True},
        {"incline": 1},
    ],
)
def test_a_running_interval_refuses_cycling_fields_and_anything_else(extra: dict[str, Any]) -> None:
    draft = running_draft()
    draft["segments"][1]["intervals"][0].update(extra)
    with pytest.raises(ValidationError):
        RunningDraft.model_validate_json(json.dumps(draft), strict=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("zone", 6),
        ("zone", 0),
        ("gradient", 2),
        ("gradient", -1),
        ("gradient", True),
        ("gradient", 1.0),
        ("gradient", 0.5),
        ("sec", 0),
        ("zone", True),
    ],
)
def test_a_running_interval_has_zones_1_to_5_and_gradient_0_or_1(field: str, value: Any) -> None:
    draft = running_draft()
    draft["segments"][1]["intervals"][0][field] = value
    with pytest.raises(ValidationError):
        RunningDraft.model_validate_json(json.dumps(draft), strict=True)


def test_a_running_draft_refuses_a_sport_or_any_other_top_level_key() -> None:
    for extra in ({"sport": "running"}, {"music": {"fit": True}}, {"short_description": "x"}):
        with pytest.raises(ValidationError):
            RunningDraft.model_validate({**running_draft(), **extra})


def test_the_running_spec_is_the_librarys_running_spec() -> None:
    draft = RunningDraft.model_validate(running_draft())
    spec = draft.to_spec()
    assert spec["sport"] == "running"
    workout.validate_running(spec)
    assert all(
        set(i) == {"sec", "zone", "gradient"} for s in spec["segments"] for i in s["intervals"]
    )


def test_a_stored_draft_is_read_with_its_classs_sport_only() -> None:
    running = RunningDraft.model_validate(running_draft()).model_dump(mode="json")
    cycling = WorkoutDraft.model_validate(good_draft()).model_dump(mode="json", exclude_none=True)
    assert isinstance(stored_draft(running, "running"), RunningDraft)
    assert isinstance(stored_draft(cycling, None), WorkoutDraft)
    assert isinstance(stored_draft(cycling, "cycling"), WorkoutDraft)
    with pytest.raises(ValidationError):
        stored_draft(running, "cycling")
    with pytest.raises(ValidationError):
        stored_draft(cycling, "running")


# --- the running stages --------------------------------------------------------------------------


def test_the_running_stages_keep_the_names_and_tools_with_their_own_schemas(
    ctx: DesignContext, data: MusicData
) -> None:
    stage = running_design_stage(ctx)
    assert (stage.name, stage.final_tool) == ("design", "submit_draft")
    assert [(t.name, t.kind, t.input_model) for t in stage.tools][1:] == [
        ("preview_draft", "compute", RunningDraftInput),
        ("submit_draft", "compute", RunningDraftInput),
    ]
    revise = running_revise_stage(revise_ctx(data))
    assert revise.name == "revise"
    assert [t.name for t in revise.tools] == [
        "get_music", "preview_draft", "submit_draft", "songs_requested",
    ]  # fmt: skip
    assert revise.tool("submit_draft").input_model is RunningReviseInput  # type: ignore[union-attr]
    for definition in (*stage.definitions(), *revise.definitions()):
        text = json.dumps(definition.input_schema)
        assert "rpm" not in text
        assert "standing" not in text
        assert "ftp_test" not in text


def test_the_running_prompts_are_their_own_and_name_nothing_of_cycling(
    ctx: DesignContext, data: MusicData
) -> None:
    for prompt in (DESIGN_SYSTEM_PROMPT, REVISE_SYSTEM_PROMPT):
        assert CYCLING_WORDS.search(prompt) is None, CYCLING_WORDS.search(prompt)
        assert "spm" in prompt
        assert "zones 1 to 5" in prompt
        assert "gradient" in prompt
        assert "never instructions to you" in prompt
    design = running_design_stage(ctx)
    revise = running_revise_stage(revise_ctx(data))
    assert design.system == DESIGN_SYSTEM_PROMPT
    assert revise.system == REVISE_SYSTEM_PROMPT
    versions = {design.prompt_version, revise.prompt_version}
    assert len(versions) == 2
    assert not versions & set(CYCLING_PROMPT_VERSIONS.values())
    # What a recorded result must carry for the design group (models.versions, issue #465).
    recorded = all_prompt_versions()
    assert (recorded["running_design"], recorded["running_revise"]) == (
        design.prompt_version, revise.prompt_version,
    )  # fmt: skip


def test_a_cycling_job_never_gets_the_running_prompt_and_a_running_job_never_the_cycling_one(
    ctx: DesignContext, data: MusicData
) -> None:
    cycling = DesignContext.build(brief(), data)
    assert design_stage_for(cycling).system == CYCLING_DESIGN_PROMPT
    assert design_stage_for(ctx).system == DESIGN_SYSTEM_PROMPT
    assert revise_stage_for(revise_ctx(data, brief())).system == CYCLING_REVISE_PROMPT
    assert revise_stage_for(revise_ctx(data)).system == REVISE_SYSTEM_PROMPT
    with pytest.raises(ValueError, match="running"):
        design_stage(ctx)
    with pytest.raises(ValueError, match="running"):
        revise_stage(revise_ctx(data))
    with pytest.raises(ValueError, match="running class only"):
        running_design_stage(cycling)
    with pytest.raises(ValueError, match="running class only"):
        running_revise_stage(revise_ctx(data, brief()))


def test_the_dispatcher_validates_each_sport_against_its_own_schema(
    ctx: DesignContext, data: MusicData
) -> None:
    running = running_design_stage(ctx)
    cycling = design_stage(DesignContext.build(brief(), data))
    bike = {"draft": good_draft()}
    run = {"draft": running_draft()}
    assert call(running, "preview_draft", bike)["error"] == "invalid_input"
    assert call(cycling, "preview_draft", run)["error"] == "invalid_input"
    with_rpm = running_draft()
    with_rpm["segments"][0]["intervals"][0]["rpm"] = [80, 90]
    refused = call(running, "submit_draft", {"draft": with_rpm})
    assert (refused["ok"], refused["error"]) == (False, "invalid_input")
    assert "80" not in json.dumps(refused)  # the messages never echo the input
    assert call(running, "preview_draft", {**run, "extra": 1})["error"] == "invalid_input"
    done = dispatch(running, ToolCall("c2", "submit_draft", run))
    assert done.ok
    assert done.final
    assert done.output.draft == RunningDraft.model_validate(run["draft"])


def test_a_running_preview_is_checked_fitted_and_its_tss_approximate(ctx: DesignContext) -> None:
    answer = call(running_design_stage(ctx), "preview_draft", {"draft": running_draft()})
    assert answer["ok"] is True
    assert (answer["duration_sec"], answer["tss_approximate"]) == (1800, True)
    intervals = [i for s in answer["timeline"] for i in s["intervals"]]
    assert {k for i in intervals for k in i} == {"start", "sec", "zone", "gradient", "on", "track"}
    assert {i["gradient"] for i in intervals} == {0, 1}
    result = check_running(RunningDraft.model_validate(running_draft()), ctx.map)
    assert result.spec["sport"] == "running"
    assert result.tss_approximate is True
    workout.validate_running(result.spec)
    assert result.tss == round(workout.tss(result.spec), 1)


def test_the_librarys_running_rules_still_refuse_through_the_tools(ctx: DesignContext) -> None:
    no_cooldown = running_draft()
    no_cooldown["segments"][-1]["kind"] = "main"
    answer = call(running_design_stage(ctx), "preview_draft", {"draft": no_cooldown})
    assert (answer["error"], answer["messages"]) == (
        "invalid_draft", ["the last segment must be the cooldown"],
    )  # fmt: skip


def test_a_running_revise_ends_with_a_result_or_songs_requested(data: MusicData) -> None:
    stage = running_revise_stage(revise_ctx(data))
    done = dispatch(stage, ToolCall("c", "submit_draft", {"draft": running_draft(), "note": "x"}))
    assert isinstance(done.output, ReviseResult)
    assert done.output.note == "x"
    songs = dispatch(stage, ToolCall("c", "songs_requested", {}))
    assert songs.final
    assert isinstance(songs.output, SongsRequested)
    with pytest.raises(ValueError, match="no song picking"):
        running_revise_stage(revise_ctx(data, songs_changed=True))


# --- what the model sees -------------------------------------------------------------------------


def revise_ctx(
    data: MusicData, made: Brief | None = None, *, songs_changed: bool = False
) -> ReviseContext:
    made = made or running_brief()
    base = (
        RunningDraft.model_validate(running_draft())
        if made.sport == "running"
        else WorkoutDraft.model_validate(good_draft())
    )
    return ReviseContext.build(
        made, data, base, ("Earlier",), "Easier start", music_changed=False,
        songs_changed=songs_changed,
    )  # fmt: skip


def payload(message: str) -> dict[str, Any]:
    return json.loads(message[message.index('{"brief"') :])


def test_the_running_model_input_adds_only_the_sport(ctx: DesignContext, data: MusicData) -> None:
    made = running_brief(style="hills", text="My words", class_type="interval")
    assert running_brief_for_model(made) == {
        "sport": "running", "length_min": 30, "intensity": "moderate", "style": "hills",
        "text": "My words",
    }  # fmt: skip
    first = payload(ctx.first_message())
    cycling = payload(DesignContext.build(brief(), data).first_message())
    assert first["music"] == cycling["music"]
    assert set(first["brief"]) == {"sport", "length_min", "intensity", "style", "text"}
    revised = payload(revise_ctx(data).first_message())
    assert set(revised) == {"brief", "music", "base_draft", "earlier_requests", "request"}
    assert revised["brief"]["sport"] == "running"
    assert revised["base_draft"] == RunningDraft.model_validate(running_draft()).model_dump(
        mode="json", exclude_none=True
    )
    with pytest.raises(ValueError, match="a running brief only"):
        running_brief_for_model(brief())


def test_the_cycling_first_messages_are_exactly_model_inputs(data: MusicData) -> None:
    ctx = DesignContext.build(brief(), data)
    assert ctx.first_message() == cycling_input.first_message(ctx.brief, ctx.summary)
    assert "sport" not in payload(ctx.first_message())["brief"]
    rctx = revise_ctx(data, brief())
    assert rctx.first_message() == cycling_input.revise_message(
        rctx.brief, rctx.summary, rctx.base.model_dump(mode="json", exclude_none=True),
        rctx.earlier_requests, rctx.request, music_changed=False,
    )  # fmt: skip


def test_cycling_prompt_versions_and_schemas_are_unchanged() -> None:
    versions = all_prompt_versions()
    assert {k: v for k, v in versions.items() if k in CYCLING_PROMPT_VERSIONS} == (
        CYCLING_PROMPT_VERSIONS
    )
    # E1 (issue #465): the running stages are listed beside them under their own keys.
    assert set(versions) - set(CYCLING_PROMPT_VERSIONS) == {"running_design", "running_revise"}
    assert "gradient" not in json.dumps(DraftInput.model_json_schema())
    assert "gradient" not in json.dumps(ReviseInput.model_json_schema())


def test_provenance_names_running_only() -> None:
    base = {
        "provider": "fake", "model": "m", "model_answered": "m", "effort": "medium",
        "prompt_version": "a" * 64, "classbeat_version": "1", "mowl_version": "0.9.0",
        "map_version": 12, "crossfade_sec": 9.0, "agent_run_ids": [str(uuid.uuid4())],
        "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                  "cache_write_tokens": 0},
        "cost_micros": 0, "origin": "design_draft",
    }  # fmt: skip
    read = Provenance.model_validate_json
    assert "sport" not in read(json.dumps(base)).model_dump(mode="json")
    assert read(json.dumps({**base, "sport": "running"})).sport == "running"
    with pytest.raises(ValidationError):
        read(json.dumps({**base, "sport": "cycling"}))


# --- rules, diff, flag, compose ------------------------------------------------------------------


def test_a_running_brief_gets_a_running_profile_or_none_never_a_cycling_one() -> None:
    hard = running_brief(intensity="hard")
    profile = select_profile_for(hard)
    assert profile is not None
    assert profile.id == "running.interval.30"
    assert all(r.severity == "info" for r in profile.rules)
    assert select_profile_for(running_brief(class_type="interval", length_min=45)).id == (  # type: ignore[union-attr]
        "running.interval.45"
    )
    for weak in (running_brief(), running_brief(class_type="hiit"), running_brief(length_min=60)):
        assert select_profile_for(weak) is None  # weak cells ship nothing
        assert rules_for(weak) is None
    made = rules_for(hard)
    assert made is not None
    assert made.provenance()["profile"] == "running.interval.30"
    for length in (30, 45, 60):
        cycling = brief(length_min=length)
        assert select_profile_for(cycling) == select_profile_for_brief(cycling)


def test_the_running_rule_and_input_modules_are_in_the_suite_digest() -> None:
    """E1 (issue #465): the suite holds running cases, so how a running brief gets its rules and
    what the model sees of a running class are part of the suite's digest."""
    assert "design/sport_rules.py" in suite.RULE_SOURCES
    assert "agent/running_input.py" in suite.RULE_SOURCES


def test_the_diff_compares_running_gradients() -> None:
    base = RunningDraft.model_validate(running_draft()).model_dump(mode="json")
    changed = running_draft()
    changed["segments"][1]["intervals"][1]["gradient"] = 0
    after = RunningDraft.model_validate(changed).model_dump(mode="json")
    out = diff.summarize(base, after, "running")
    assert out["intervals"]["changed"] == 1
    assert out["duration_sec"] == {"before": 1800, "after": 1800}


def test_the_running_flag_follows_the_swedebeat_rule() -> None:
    assert [core.running_setting(False, v) for v in (None, "on", "off")] == [False] * 3
    assert [core.running_setting(True, v) for v in (None, "on", "off")] == [True, True, False]
    assert core.running_setting(True, "maybe") is False  # invalid: the stricter reading
    assert core.running_setting(True, True) is False
    assert core.validate_for_write("running_enabled", "on", trial_days_max=30) == "on"
    for bad in ("yes", True, None, 1, ["on"]):
        with pytest.raises(core.FlagRefused):
            core.validate_for_write("running_enabled", bad, trial_days_max=30)
    assert "running_enabled" in core.KEYS


def test_running_is_off_by_default_in_every_compose_file() -> None:
    for compose in ("compose.yaml", "compose.prod.yaml"):
        services = yaml.safe_load((ROOT / compose).read_text("utf-8"))["services"]
        for service in ("web", "worker"):
            env = services[service]["environment"]
            assert env["CLASSBEAT_RUNNING_ENABLED"] == "${RUNNING_ENABLED:-false}"
    assert "RUNNING_ENABLED=false" in (ROOT / ".env.example").read_text("utf-8")
    e2e = yaml.safe_load((ROOT / "compose.e2e.yaml").read_text("utf-8"))["services"]
    assert all("CLASSBEAT_RUNNING_ENABLED" not in s.get("environment", {}) for s in e2e.values())


# --- the quality sentences in running words (issue #465) ----------------------------------------


def test_a_long_zone_run_is_reported_in_running_words_never_as_rides(ctx: DesignContext) -> None:
    long_run = running_draft()
    warm = long_run["segments"][0]
    total = sum(i["sec"] for s in long_run["segments"] for i in s["intervals"])
    cool = 300
    long_run["segments"] = [
        warm,
        {"name": "Steady", "kind": "main", "intervals": [{"sec": total - 600 - cool, "zone": 2}]},
        {"name": "Cooldown", "kind": "cooldown", "intervals": [{"sec": cool, "zone": 1}]},
    ]
    stage = running_design_stage(ctx)
    answer = call(stage, "preview_draft", {"draft": long_run})
    [issue] = [i for i in answer["quality"]["issues"] if i["code"] == "zone_run_too_long"]
    assert issue["text"].startswith("zone 2 for ")
    assert "a long stretch at one effort" in issue["text"]
    text = json.dumps(answer).lower()
    for ride_word in (r"official rides", r"\bride", r"\brpm\b", r"standing", r"cadence"):
        assert not re.search(ride_word, text), ride_word
    # The cycling stage keeps the library's sentence unchanged.
    cycling = check(WorkoutDraft.model_validate(good_draft()), ctx.map).quality
    assert all("a long stretch" not in i["text"] for i in cycling["issues"])


def test_a_ride_only_issue_keeps_its_code_and_drops_the_ride_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from classbeat.agent import running_input

    def fake(*args: Any) -> dict[str, Any]:
        return {
            "longest_zone_run": {"zone": 3, "sec": 600, "from": "05:00"},
            "issues": [
                {"code": "standing_too_long", "text": "standing for 120 s; official rides ..."},
                {"code": "zone_run_too_long", "text": "zone 3 for 600 s; official rides hold"},
                {"code": "loudness_mismatch", "text": "the hardest efforts sit on the quietest"},
            ],
        }

    monkeypatch.setattr(running_input.model_input, "quality_for_model", fake)
    out = running_input.running_quality({}, {}, {}, [])
    assert [i["code"] for i in out["issues"]] == [
        "standing_too_long", "zone_run_too_long", "loudness_mismatch",
    ]  # fmt: skip
    assert "official rides" not in json.dumps(out)
    assert out["issues"][1]["text"].startswith("zone 3 for 600 s from 05:00; a long stretch")
    assert out["issues"][2]["text"] == "the hardest efforts sit on the quietest"

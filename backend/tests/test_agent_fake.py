"""The FakeProvider and its scripts (ADR 0006 section 8), and how the worker picks a provider."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentfake import SCRIPTS, from_dict, script
from classbeat.agent.provider import StageSpec, ToolResult
from classbeat.agent.providers import FakeProvider, ProviderUnavailable, provider_for
from classbeat.agent.providers.fake import FakeScript, ScriptMismatch
from classbeat.config import Settings

# ADR 0006 section 8's list, one script each (plus the reminder that does get a draft).
COVERED = {
    "good_first_draft",
    "fixed_after_validate",
    "unknown_tool",
    "out_of_range_index",
    "oversized_input",
    "missing_final_call",
    "reminded_then_submitted",
    "refusal",
    "overload_then_success",
    "turn_limit",
    "cost_limit",
    "corrections_limit",
    "injection_title",
    # revise_draft (ADR 0007 section 3), used by tests/test_revise_draft.py
    "revise_good",
    "revise_second",
    "revise_injected_note",
    "revise_unchanged",
    "revise_overloaded",
    "revise_note_too_long",
    # used by the e2e specs (e2e/tests/class-save.spec.ts): a revise that changes the description
    "revise_description",
}

SPEC = StageSpec("design", "s", (), "m", "fake-model", "medium", 32_000, "submit_draft")


def test_every_script_loads_and_is_covered_by_a_test() -> None:
    names = {p.stem for p in SCRIPTS.glob("*.json")}
    assert names == COVERED
    for name in names:
        assert script(name).turns


@pytest.mark.parametrize(
    "turn",
    [{}, {"refusal": True, "end_turn": "x"}, {"error": "melted"}, {"calls": [], "extra": 1}],
)
def test_a_malformed_turn_is_refused(turn: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FakeScript.model_validate({"turns": [turn]})


async def test_templates_resolve_drafts_last_inputs_and_repeats() -> None:
    fake = from_dict(
        {
            "drafts": {"d": {"name": "A", "segments": [{"sec": 1}]}},
            "turns": [
                {"calls": [{"name": "t", "input": {"draft": {"$draft": "d"}}}]},
                {
                    "calls": [
                        {"name": "t", "input": {"$last": "t", "set": {"draft.segments.0.sec": 9}}},
                        {"name": "u", "input": {"pad": {"$repeat": "ab", "times": 3}}},
                    ]
                },
            ],
        }
    )
    conv = fake.start(SPEC)
    first = await conv.turn(())
    assert first.tool_calls[0].input == {"draft": {"name": "A", "segments": [{"sec": 1}]}}
    second = await conv.turn((ToolResult("call_1_0", "{}", False),))
    assert second.tool_calls[0].input == {"draft": {"name": "A", "segments": [{"sec": 9}]}}
    assert second.tool_calls[1].input == {"pad": "ababab"}
    assert fake.script.drafts["d"]["segments"][0]["sec"] == 1  # the script itself is unchanged


async def test_expectations_are_checked_against_the_next_results() -> None:
    fake = from_dict(
        {
            "turns": [
                {"calls": [{"name": "t", "expect": {"ok": True}, "contains": ["yes"]}]},
                {"end_turn": "x"},
            ]
        }
    )
    conv = fake.start(SPEC)
    await conv.turn(())
    with pytest.raises(ScriptMismatch):
        await conv.turn((ToolResult("call_1_0", json.dumps({"ok": True, "v": "no"}), False),))
    fake2 = from_dict({"turns": [{"calls": [{"name": "t"}]}, {"end_turn": "x"}]})
    conv2 = fake2.start(SPEC)
    await conv2.turn(())
    with pytest.raises(ScriptMismatch):  # a result count that doesn't match the calls
        await conv2.turn(())


async def test_unknown_template_references_fail_loudly() -> None:
    for turn in (
        {"calls": [{"name": "t", "input": {"$draft": "missing"}}]},
        {"calls": [{"name": "t", "input": {"$last": "never"}}]},
    ):
        with pytest.raises(ScriptMismatch):
            await from_dict({"turns": [turn]}).start(SPEC).turn(())


def test_provider_for_builds_the_fake_from_its_script(tmp_path: Path) -> None:
    path = tmp_path / "script.json"
    path.write_text(json.dumps({"turns": [{"refusal": True}]}), "utf-8")
    fake = provider_for(Settings(model_provider="fake", fake_model_script=path))
    assert isinstance(fake, FakeProvider)
    assert fake.remaining == 1


@pytest.mark.parametrize(
    "settings",
    [
        {"model_provider": "fake"},
        {
            "model_provider": "fake",
            "environment": "prod",
            "fake_model_script": "x.json",
            "public_base_url": "https://classbeat.example",
        },
        {"model_provider": "anthropic"},
    ],
)
def test_provider_for_refuses_what_it_cannot_build(settings: dict[str, object]) -> None:
    with pytest.raises(ProviderUnavailable):
        provider_for(Settings.model_validate(settings))


def test_the_agent_settings_have_the_adr_defaults() -> None:
    s = Settings()
    assert (s.agent_model, s.agent_effort, s.agent_max_tokens) == (
        "claude-opus-5-5",
        "medium",
        32_000,
    )
    assert (s.agent_max_turns, s.agent_max_tool_calls, s.agent_max_corrections) == (12, 40, 3)
    assert (s.agent_stage_seconds, s.agent_job_cost_micros) == (300.0, 1_500_000)
    assert s.agent_turn_output_reserve == 8_000
    assert s.fake_model_script is None


def test_the_reserve_and_the_script_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLASSBEAT_AGENT_TURN_OUTPUT_RESERVE", "4000")
    monkeypatch.setenv("CLASSBEAT_FAKE_MODEL_SCRIPT", "/scripts/good.json")
    s = Settings()
    assert s.agent_turn_output_reserve == 4000
    assert s.fake_model_script == Path("/scripts/good.json")

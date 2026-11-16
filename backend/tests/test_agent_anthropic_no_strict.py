"""#1129: the real Anthropic API refuses a ``strict`` tool whose compiled grammar is too large
(``submit_brief`` of the interpret stage), so no tool of any stage is sent with ``strict`` and
each ``input_schema`` is the stage's own. No network: the fake Messages API behind the real SDK."""

import pytest
from pydantic import SecretStr

from anthropicfake import PLANTED_KEY, FakeAnthropic, message, stream, text
from classbeat.agent.provider import StageSpec
from classbeat.agent.providers.anthropic import AnthropicProvider, request_shape
from classbeat.agent.stage import Stage
from stagetools import all_stages


def _spec(stage: Stage) -> StageSpec:
    return StageSpec(
        stage=stage.name,
        system=stage.system,
        tools=stage.definitions(),
        first_message="Brief: invented.",
        model="claude-opus-5-5",
        effort="medium",
        max_tokens=32_000,
        final_tool=stage.final_tool,
    )


def test_every_stage_of_the_model_path_is_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    assert {s.name for s in all_stages(monkeypatch)} == {
        "design",
        "revise",
        "pick_pool",
        "choose_songs",
        "interpret",
    }


def test_no_tool_of_any_stage_carries_strict_and_the_schema_is_the_stage_s_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for stage in all_stages(monkeypatch):
        spec = _spec(stage)
        tools = request_shape(spec)["tools"]
        assert [t["name"] for t in tools] == [t.name for t in spec.tools], stage.name
        for sent, defined in zip(tools, spec.tools, strict=True):
            assert "strict" not in sent, (stage.name, sent["name"])
            assert sent["input_schema"] == dict(defined.input_schema), (stage.name, sent["name"])
            assert set(sent) <= {"name", "description", "input_schema", "cache_control"}


async def test_the_request_on_the_wire_has_no_strict_for_every_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for stage in all_stages(monkeypatch):
        fake = FakeAnthropic([stream(message(text("hi")))])
        provider = AnthropicProvider(SecretStr(PLANTED_KEY), transport=fake)
        await provider.start(_spec(stage)).turn(())
        sent = fake.bodies[0]["tools"]
        assert sent, stage.name
        assert all("strict" not in t for t in sent), stage.name

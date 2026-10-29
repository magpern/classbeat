"""The request ``AnthropicProvider`` builds, per model of the comparison (issue #134): checked
against the body the SDK sends to a fake Messages API (no network). Haiku 5.5 rejects non-default
sampling parameters and Opus 5.5 a forced tool choice, so none of the three gets either; each gets
adaptive thinking and the effort as configured. A model needing another shape would be handled in
the provider by model id, with a test here."""

import dataclasses

import pytest
from pydantic import SecretStr

from anthropicfake import PLANTED_KEY, SPEC, FakeAnthropic, message, stream, text
from classbeat.agent.provider import StageSpec
from classbeat.agent.providers.anthropic import AnthropicProvider

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
SAMPLING = ("temperature", "top_p", "top_k", "fallbacks", "metadata", "stop_sequences")


async def body_for(spec: StageSpec) -> dict[str, object]:
    fake = FakeAnthropic([stream(message(text("hi"), model=spec.model))])
    turn = await AnthropicProvider(SecretStr(PLANTED_KEY), transport=fake).start(spec).turn(())
    assert turn.model == spec.model
    [body] = fake.bodies
    return body


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("effort", EFFORTS)
async def test_every_model_gets_the_same_compatible_request(model: str, effort: str) -> None:
    body = await body_for(dataclasses.replace(SPEC, model=model, effort=effort))
    assert body["model"] == model
    assert body["output_config"] == {"effort": effort}  # as configured, never remapped
    assert body["thinking"] == {"type": "adaptive"}
    assert body["tool_choice"] == {"type": "auto"}  # a forced choice is rejected
    for absent in SAMPLING:
        assert absent not in body  # Haiku 5.5 rejects non-default sampling parameters
    assert "betas" not in body
    assert body["stream"] is True


async def test_the_three_requests_differ_in_the_model_only() -> None:
    bodies = [await body_for(dataclasses.replace(SPEC, model=m)) for m in MODELS]
    assert len({str({**b, "model": ""}) for b in bodies}) == 1

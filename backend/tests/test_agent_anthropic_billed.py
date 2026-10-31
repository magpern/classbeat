"""An Anthropic answer the API gave but that cannot be read is billed (#219, the #306 rule).

The adapter mirrors the OpenAI-SDK adapters (``test_agent_openai_hardening``): once the stream has
ended with a final message, a usage that is missing or malformed, or a shape that cannot be read,
is ``ProviderError("internal", billed=True)`` and the loop records the turn at its ceiling (the
prompt estimate at the dearest input rate plus ``max_tokens`` of output), never at 0 (ADR 0006
section 5: cost errs high). A request with no answer (an error status) is not billed.
"""

import dataclasses
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from pydantic import SecretStr

from agentfake import CONFIG, MemoryHost, analysed, brief
from anthropicfake import (
    PLANTED_KEY,
    SPEC,
    FakeAnthropic,
    message,
    status,
    stream,
    text,
    tool_use,
)
from classbeat.agent import DesignContext, design_stage, run_stage
from classbeat.agent.prices import DEFAULT_PRICES
from classbeat.agent.provider import ProviderError
from classbeat.agent.providers import anthropic as adapter
from classbeat.agent.providers.anthropic import AnthropicProvider
from classbeat.jobs.errors import JobFailure

KEY = SecretStr(PLANTED_KEY)
ANTHROPIC = dataclasses.replace(CONFIG, model="claude-opus-5-5")  # priced
Edit = Callable[[dict[str, Any]], None]


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic.AsyncAnthropic, "_calculate_retry_timeout", lambda *_: 0.0)


def edited(edit: Edit) -> bytes:
    """A good streamed answer with ``edit`` applied to each of its events."""
    raw = message(text("done"), tool_use("toolu_a", "get_music", {}), stop="tool_use")
    events: list[dict[str, Any]] = []
    for block in raw.decode().strip().split("\n\n"):
        data = json.loads(block.split("data: ", 1)[1])
        edit(data)
        events.append(data)
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def start_usage(**fields: Any) -> Edit:
    def edit(event: dict[str, Any]) -> None:
        if event["type"] == "message_start":
            event["message"]["usage"].update(fields)

    return edit


def delta_usage(**fields: Any) -> Edit:
    def edit(event: dict[str, Any]) -> None:
        if event["type"] == "message_delta":
            event["usage"].update(fields)

    return edit


def no_usage(event: dict[str, Any]) -> None:
    if event["type"] == "message_start":
        del event["message"]["usage"]


def no_input(event: dict[str, Any]) -> None:
    if event["type"] == "message_start":
        del event["message"]["usage"]["input_tokens"]


UNREADABLE: list[Edit] = [
    no_usage,
    no_input,
    start_usage(input_tokens=None),
    start_usage(input_tokens=-5),
    start_usage(cache_read_input_tokens=-1),
    start_usage(cache_creation_input_tokens="many"),
    delta_usage(output_tokens=None),
    delta_usage(output_tokens=-300),
]


@pytest.mark.parametrize("edit", UNREADABLE, ids=range(len(UNREADABLE)))
async def test_an_answer_with_missing_or_malformed_usage_is_a_billed_internal_failure(
    edit: Edit,
) -> None:
    fake = FakeAnthropic([stream(edited(edit))])
    conversation = AnthropicProvider(KEY, transport=fake).start(SPEC)
    with pytest.raises(ProviderError) as raised:
        await conversation.turn(())
    assert (raised.value.category, raised.value.billed) == ("internal", True)
    assert raised.value.args == ("internal",)
    assert (raised.value.__cause__, raised.value.__context__) == (None, None)
    assert len(fake.requests) == 1  # an unreadable answer is not retried


@pytest.mark.parametrize(
    "bad", [None, -1, 1.5, "7", True, [1]], ids=["none", "negative", "float", "str", "bool", "list"]
)
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_the_required_counts_are_whole_numbers_of_at_least_zero(field: str, bad: object) -> None:
    good = {"input_tokens": 5, "output_tokens": 6}
    with pytest.raises(ProviderError) as raised:
        adapter._usage(SimpleNamespace(**{**good, field: bad}))  # pyright: ignore[reportPrivateUsage]
    assert raised.value.category == "internal"


@pytest.mark.parametrize("bad", [-1, 1.5, "7", True])
@pytest.mark.parametrize("field", ["cache_read_input_tokens", "cache_creation_input_tokens"])
def test_a_cache_count_that_is_there_must_be_whole_too(field: str, bad: object) -> None:
    usage = SimpleNamespace(input_tokens=5, output_tokens=6, **{field: bad})
    with pytest.raises(ProviderError):
        adapter._usage(usage)  # pyright: ignore[reportPrivateUsage]


def test_no_usage_object_is_unreadable() -> None:
    with pytest.raises(ProviderError):
        adapter._usage(None)  # pyright: ignore[reportPrivateUsage]


async def test_a_usage_left_out_for_the_cache_counts_reads_as_zero() -> None:
    def drop_cache(event: dict[str, Any]) -> None:
        if event["type"] == "message_start":
            for name in ("cache_read_input_tokens", "cache_creation_input_tokens"):
                event["message"]["usage"].pop(name)

    fake = FakeAnthropic([stream(edited(drop_cache))])
    turn = await AnthropicProvider(KEY, transport=fake).start(SPEC).turn(())
    assert (turn.usage.cache_read_tokens, turn.usage.cache_write_tokens) == (0, 0)
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (1200, 300)


async def test_a_shape_that_cannot_be_read_after_the_answer_is_billed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: object, final: Any) -> list[dict[str, Any]]:
        raise RuntimeError("a provider text that must not travel")

    monkeypatch.setattr(adapter, "turn_blocks", lambda final: broken(None, final))
    fake = FakeAnthropic([stream(edited(lambda _: None))])
    with pytest.raises(ProviderError) as raised:
        await AnthropicProvider(KEY, transport=fake).start(SPEC).turn(())
    assert (raised.value.category, raised.value.billed) == ("internal", True)
    assert raised.value.args == ("internal",)
    assert (raised.value.__cause__, raised.value.__context__) == (None, None)


async def test_a_failed_read_leaves_the_history_unchanged() -> None:
    fake = FakeAnthropic([stream(edited(no_usage)), stream(edited(lambda _: None))])
    conversation = AnthropicProvider(KEY, transport=fake).start(SPEC)
    with pytest.raises(ProviderError):
        await conversation.turn(())
    await conversation.turn(())
    first, second = fake.bodies
    assert second["messages"] == first["messages"]  # the unreadable answer was not kept


async def test_an_error_status_is_not_billed() -> None:
    fake = FakeAnthropic([status(400, "invalid_request_error")])
    with pytest.raises(ProviderError) as raised:
        await AnthropicProvider(KEY, transport=fake, max_retries=0).start(SPEC).turn(())
    assert (raised.value.category, raised.value.billed) == ("internal", False)


async def test_an_unreadable_answer_costs_the_turn_ceiling_in_a_stage_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    fake = FakeAnthropic([stream(edited(no_usage))])
    host = MemoryHost()
    with pytest.raises(JobFailure) as failed:
        await run_stage(
            host,
            AnthropicProvider(KEY, transport=fake),
            design_stage(ctx),
            ctx.first_message(),
            ANTHROPIC,
        )
    assert failed.value.category == "internal"
    [record] = host.runs.values()
    price = DEFAULT_PRICES.lookup("anthropic", ANTHROPIC.model)
    assert price is not None
    assert record["counters"].turns == 1
    assert record["counters"].cost_micros > price.output_micros(ANTHROPIC.max_tokens)

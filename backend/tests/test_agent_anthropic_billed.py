"""An Anthropic answer the API gave but that cannot be read is billed (#219, the #306 rule).

The adapter mirrors the OpenAI-SDK adapters (``test_agent_openai_hardening``): once the stream has
ended with a final message, a usage that is missing or malformed, or a shape that cannot be read,
is ``ProviderError("internal", billed=True)`` and the loop records the turn at its ceiling (the
prompt estimate at the dearest input rate plus ``max_tokens`` of output), never at 0 (ADR 0006
section 5: cost errs high). A request with no answer (an error status) is not billed.
"""

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import SecretStr

from agentfake import CONFIG, MemoryHost, analysed, brief
from anthropicfake import (
    PLANTED_KEY,
    SPEC,
    FakeAnthropic,
    Reply,
    message,
    status,
    stream,
    text,
    tool_use,
)
from classbeat.agent import DesignContext, design_stage, run_stage
from classbeat.agent.prices import DEFAULT_PRICES
from classbeat.agent.provider import ProviderError, ToolResult
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


# --- a turn that ends after the stream began (#219 follow-up, the #320 review) -----------------


class Cut(httpx.AsyncByteStream):
    """A 200 stream that sends ``chunks``, optionally waits, then ends: cleanly, or raising."""

    def __init__(self, chunks: list[bytes], *, then: BaseException | None = None, wait: float = 0):
        self.chunks, self.then, self.wait = chunks, then, wait

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        if self.wait:
            await asyncio.sleep(self.wait)
        if self.then is not None:
            raise self.then


class MidStream(httpx.AsyncBaseTransport):
    def __init__(self, stream: Cut) -> None:
        self.stream, self.requests = stream, 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=self.stream, request=request
        )


def start_only() -> bytes:
    """The first event of a good answer: message_start, which reports the input tokens."""
    return edited(lambda _: None).split(b"\n\n", 1)[0] + b"\n\n"


def mid(then: BaseException | None, *, wait: float = 0, whole: bool = False) -> MidStream:
    chunks = [edited(lambda _: None)] if whole else [start_only()]
    return MidStream(Cut(chunks, then=then, wait=wait))


async def run_turn(transport: httpx.AsyncBaseTransport, **kw: Any) -> ProviderError:
    conversation = AnthropicProvider(KEY, transport=transport, max_retries=2, **kw).start(SPEC)
    with pytest.raises(ProviderError) as raised:
        await conversation.turn(())
    assert (raised.value.__cause__, raised.value.__context__) == (None, None)
    return raised.value


@pytest.mark.parametrize(
    ("then", "category"),
    [
        (httpx.ReadTimeout("slow"), "model_unavailable"),
        (httpx.ReadError("cut"), "internal"),
        (httpx.RemoteProtocolError("cut"), "internal"),
        (None, "internal"),  # ends cleanly after message_start: no stop reason
    ],
    ids=["read_timeout", "read_error", "protocol_error", "clean_end"],
)
async def test_a_stream_that_ends_badly_after_message_start_is_billed(
    then: BaseException | None, category: str
) -> None:
    transport = mid(then)
    error = await run_turn(transport)
    assert (error.category, error.billed) == (category, True)
    assert transport.requests == 1  # the SDK does not send a stream again


async def test_the_turn_bound_cutting_a_running_stream_is_billed() -> None:
    transport = mid(None, wait=5)
    error = await run_turn(transport, turn_seconds=0.2)
    assert (error.category, error.billed) == ("model_unavailable", True)
    assert transport.requests == 1


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("overloaded_error", "model_unavailable"),
        ("api_error", "model_unavailable"),
        ("authentication_error", "model_config"),
        ("invalid_request_error", "internal"),  # a type outside the lists: errs high
        ("some_future_error", "internal"),
    ],
)
async def test_an_error_event_inside_the_stream_is_billed_whatever_its_type(
    kind: str, category: str
) -> None:
    body = message(text("part"), error_event={"type": kind, "message": "provider text"})
    fake = FakeAnthropic([stream(body)])
    error = await run_turn(fake)
    assert (error.category, error.billed) == (category, True)
    assert len(fake.requests) == 1


async def test_failures_before_the_stream_began_are_not_billed() -> None:
    refused = FakeAnthropic([Reply(raise_=lambda r: httpx.ConnectError("no", request=r))])
    error = await run_turn(refused)
    assert (error.category, error.billed) == ("model_unavailable", False)
    for code, kind in ((401, "authentication_error"), (529, "overloaded_error")):
        error = await run_turn(FakeAnthropic([status(code, kind)]))
        assert error.billed is False


async def test_a_stream_without_a_stop_reason_is_not_a_normal_turn() -> None:
    def no_stop(event: dict[str, Any]) -> None:
        if event["type"] == "message_delta":
            event["delta"]["stop_reason"] = None

    error = await run_turn(FakeAnthropic([stream(edited(no_stop))]))
    assert (error.category, error.billed) == ("internal", True)


async def test_a_timed_out_stream_costs_the_ceiling_in_a_stage_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    host = MemoryHost()
    with pytest.raises(JobFailure) as failed:
        await run_stage(
            host,
            AnthropicProvider(KEY, transport=mid(httpx.ReadTimeout("slow"))),
            design_stage(ctx),
            ctx.first_message(),
            ANTHROPIC,
        )
    assert failed.value.category == "model_unavailable"
    [record] = host.runs.values()
    price = DEFAULT_PRICES.lookup("anthropic", ANTHROPIC.model)
    assert price is not None
    assert record["counters"].cost_micros > price.output_micros(ANTHROPIC.max_tokens)


async def test_a_refused_second_turn_is_not_billed_for_the_first_ones_stream() -> None:
    """The streaming flag is per turn: a turn that fails before its stream began is unbilled even
    after an earlier turn of the conversation streamed."""
    good = stream(edited(lambda _: None))
    refused = Reply(raise_=lambda r: httpx.ConnectError("no", request=r))
    fake = FakeAnthropic([good, refused])
    conversation = AnthropicProvider(KEY, transport=fake, max_retries=0).start(SPEC)
    await conversation.turn(())
    with pytest.raises(ProviderError) as raised:
        await conversation.turn((ToolResult("toolu_a", "{}", False),))
    assert (raised.value.category, raised.value.billed) == ("model_unavailable", False)

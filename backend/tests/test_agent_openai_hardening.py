"""The OpenAI-SDK adapters' hardening from the #280 and #287 reviews (#299, ADR 0012 §3,
amended 2026-10-31), offline under the real one-host allowlist and the test key:

- one attempt may take the whole turn (no shorter per-attempt timeout cuts a slow answer, which
  would then be sent again);
- an answer the API gave but that cannot be read (no usage, usage that does not add up, a broken
  shape) is ``ProviderError("internal", billed=True)``, which the loop charges at the turn's
  ceiling; an error status is not billed;
- arguments that nest too deep, or past what ``json.loads`` can parse, go on as their text and the
  loop answers ``invalid_input``;
- the DeepSeek efforts have one source (the catalog) and ``PRICES_VERSION`` is pinned exactly.
"""

import asyncio
import copy
import dataclasses
import functools
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agentfake import CONFIG, MemoryHost, analysed, brief, from_dict, good_draft
from classbeat.agent import DesignContext, catalog, design_stage, run_stage
from classbeat.agent.prices import DEFAULT_PRICES, PRICES_VERSION
from classbeat.agent.provider import ProviderError, StageSpec, ToolDef
from classbeat.agent.providers import DeepSeekProvider, OpenAIProvider, openai_common
from classbeat.agent.providers.openai_arguments import MAX_ARGUMENT_DEPTH, parse_arguments
from classbeat.agent.providers.openai_common import (
    bounded_request,
    build_client,
    chat_usage,
    normalise_usage,
    read_answer,
    responses_usage,
    tool_input,
)
from classbeat.egress import MODEL_HOSTS, ModelProviderName
from classbeat.jobs.errors import JobFailure
from deepseekfake import DEEPSEEK_EFFORT, DEEPSEEK_MODEL, scripted_deepseek
from deepseekfake import PATH as DEEPSEEK_PATH
from openaifake import OPENAI_MODEL, PATH, scripted_openai
from providerreplay import TEST_KEY, Answer, Replay

KEY = SecretStr(TEST_KEY)
OPENAI = dataclasses.replace(CONFIG, model=OPENAI_MODEL)
DEEPSEEK = dataclasses.replace(CONFIG, model=DEEPSEEK_MODEL, effort=DEEPSEEK_EFFORT)
TRACK: dict[str, Any] = {
    "type": "object",
    "properties": {
        "track": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
    },
    "additionalProperties": False,
}
SPEC = StageSpec(
    stage="design",
    system="You design classes.",
    tools=(ToolDef("get_music", "The music.", TRACK),),
    first_message="A brief.",
    model=OPENAI_MODEL,
    effort="high",
    max_tokens=32000,
)
DEEPSEEK_SPEC = dataclasses.replace(SPEC, model=DEEPSEEK_MODEL)


def _fixture_provider(*names: str, **kw: Any) -> tuple[OpenAIProvider, Replay]:
    answers = [Answer.from_file("openai", n, path=PATH) for n in names]
    replay = Replay("openai", answers)
    return OpenAIProvider(KEY, transport=replay.transport(), **kw), replay


def _deepseek_fixture(name: str) -> DeepSeekProvider:
    replay = Replay("deepseek", [Answer.from_file("deepseek", name, path=DEEPSEEK_PATH)])
    return DeepSeekProvider(KEY, transport=replay.transport())


def _correcting_script(arguments: str) -> dict[str, Any]:
    """One ``get_music`` call with ``arguments`` as written, answered ``invalid_input``, then the
    good draft."""
    return {
        "drafts": {"good": good_draft()},
        "turns": [
            {
                "calls": [
                    {
                        "name": "get_music",
                        "input": {"$raw": arguments},
                        "expect": {"ok": False, "error": "invalid_input"},
                    }
                ]
            },
            {"calls": [{"name": "submit_draft", "input": {"draft": {"$draft": "good"}}}]},
        ],
    }


# --- OpenAI ----------------------------------------------------------------------------------

# Deeper than json.loads can go (RecursionError) yet under the dispatcher's 32 KiB input limit,
# and one level past MAX_ARGUMENT_DEPTH (33 objects).
TOO_DEEP_TO_PARSE = '{"track": ' + "[" * 5_000 + "]" * 5_000 + "}"
ONE_TOO_DEEP = json.dumps(functools.reduce(lambda inner, _: {"a": inner}, range(33), 2))


def _answer(name: str, change: Callable[[dict[str, Any]], None]) -> Answer:
    answer = Answer.from_file("openai", name, path=PATH)
    body = copy.deepcopy(answer.body)
    change(body)
    return dataclasses.replace(answer, body=body)


def _slow(replay: Replay, seconds: float, sent: list[httpx.Request]) -> httpx.MockTransport:
    """``replay``, answering after ``seconds``, and timing out as a real transport does: when the
    request's own read timeout is shorter. ``sent`` gets every request sent."""

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        read = request.extensions["timeout"]["read"]
        if read is not None and read < seconds:
            await asyncio.sleep(read)
            raise httpx.ReadTimeout("timed out", request=request)
        await asyncio.sleep(seconds)
        return replay(request)

    return httpx.MockTransport(handler)


async def test_one_attempt_may_take_the_whole_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0012 §3 (amended, #299): an answer slower than the old per-attempt timeout is waited
    for, not cut and sent again."""
    monkeypatch.setattr(openai_common, "TIMEOUT_SECONDS", 0.05)  # the old fixed per-attempt bound
    replay = Replay("openai", [Answer.from_file("openai", "end_turn", path=PATH)] * 3)
    sent: list[httpx.Request] = []
    model = OpenAIProvider(
        SecretStr(TEST_KEY), transport=_slow(replay, 0.3, sent), turn_seconds=5.0
    )
    assert model._client.timeout == 5.0  # pyright: ignore[reportPrivateUsage]
    turn = await model.start(SPEC).turn([])
    assert turn.stop == "end_turn"
    assert len(sent) == 1  # sent once, not cut and sent again


async def test_a_turn_that_runs_out_of_time_is_sent_once() -> None:
    replay = Replay("openai", [Answer.from_file("openai", "end_turn", path=PATH)] * 3)
    sent: list[httpx.Request] = []
    model = OpenAIProvider(
        SecretStr(TEST_KEY), transport=_slow(replay, 5.0, sent), turn_seconds=0.2
    )
    with pytest.raises(ProviderError) as raised:
        await model.start(SPEC).turn([])
    assert (raised.value.category, raised.value.billed) == ("model_unavailable", False)
    assert len(sent) == 1


def _no_usage(body: dict[str, Any]) -> None:
    body["usage"] = None


def _cached_above_prompt(body: dict[str, Any]) -> None:
    body["usage"]["input_tokens_details"]["cached_tokens"] = body["usage"]["input_tokens"] + 1


def _output_not_a_list(body: dict[str, Any]) -> None:
    body["output"] = 7


def _usage(usage: Any) -> Callable[[dict[str, Any]], None]:
    def change(body: dict[str, Any]) -> None:
        body["usage"] = usage

    change.__name__ = f"usage={usage!r}"
    return change


# The #306 review's cases: usage present but unreadable is never read as zeros.
UNREADABLE_OPENAI_USAGE = [
    {},
    {"input_tokens": None, "output_tokens": None},
    {"input_tokens": "9", "output_tokens": -1},
    {"input_tokens": 9000, "output_tokens": 1.5},
    {"input_tokens": 9000, "output_tokens": 700, "input_tokens_details": {"cached_tokens": -1}},
    {"input_tokens": 9000},  # one count left out (the #306 delta review)
    {"output_tokens": 700},
]
UNREADABLE_DEEPSEEK_USAGE = [
    {},
    {"prompt_tokens": None, "completion_tokens": None},
    {"prompt_tokens": "9", "completion_tokens": -1},
    {"prompt_tokens": 9000, "completion_tokens": True},
    {"prompt_tokens": 9000},  # one count left out (the #306 delta review)
    {"completion_tokens": 70},
]


@pytest.mark.parametrize(
    "change",
    [
        _no_usage,
        _cached_above_prompt,
        _output_not_a_list,
        *(_usage(u) for u in UNREADABLE_OPENAI_USAGE),
    ],
    ids=lambda c: c.__name__,
)
async def test_an_answer_that_cannot_be_read_is_a_billed_internal_failure(
    change: Callable[[dict[str, Any]], None],
) -> None:
    replay = Replay("openai", [_answer("tool_calls", change)])
    model = OpenAIProvider(SecretStr(TEST_KEY), transport=replay.transport())
    with pytest.raises(ProviderError) as raised:
        await model.start(SPEC).turn([])
    assert (raised.value.category, raised.value.billed) == ("internal", True)
    assert raised.value.args == ("internal",)
    assert (raised.value.__cause__, raised.value.__context__) == (None, None)


def _not_json(
    provider: ModelProviderName, path: str
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A 200 that says JSON but whose body is not: the SDK cannot decode it."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        assert (request.url.host, request.url.path) == (MODEL_HOSTS[provider], path)
        return httpx.Response(
            200, content=b"{not json", headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler), sent


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
async def test_a_200_whose_body_cannot_be_decoded_is_billed(provider: str) -> None:
    """The #306 review: a failure after a 2xx is a billed answer, decoding included."""
    if provider == "openai":
        transport, sent = _not_json("openai", PATH)
        conversation: Any = OpenAIProvider(KEY, transport=transport).start(SPEC)
    else:
        transport, sent = _not_json("deepseek", DEEPSEEK_PATH)
        conversation = DeepSeekProvider(KEY, transport=transport).start(DEEPSEEK_SPEC)
    with pytest.raises(ProviderError) as raised:
        await conversation.turn([])
    assert (raised.value.category, raised.value.billed) == ("internal", True)
    assert (raised.value.__cause__, raised.value.__context__) == (None, None)
    assert len(sent) == 1  # a decode failure is not retried


async def test_a_200_whose_body_cannot_be_decoded_costs_the_ceiling_in_a_stage_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    transport, _ = _not_json("openai", PATH)
    host = MemoryHost()
    with pytest.raises(JobFailure):
        await run_stage(
            host,
            OpenAIProvider(KEY, transport=transport),
            design_stage(ctx),
            ctx.first_message(),
            OPENAI,
        )
    [record] = host.runs.values()
    price = DEFAULT_PRICES.lookup("openai", OPENAI_MODEL)
    assert price is not None
    assert record["counters"].cost_micros > price.output_micros(OPENAI.max_tokens)


async def test_an_error_status_is_not_billed() -> None:
    model, _ = _fixture_provider("error_400", max_retries=0)
    with pytest.raises(ProviderError) as raised:
        await model.start(SPEC).turn([])
    assert (raised.value.category, raised.value.billed) == ("internal", False)


async def test_an_unreadable_answer_costs_the_turn_ceiling_in_a_stage_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    replay = Replay("openai", [_answer("tool_calls", _no_usage)])
    model = OpenAIProvider(SecretStr(TEST_KEY), transport=replay.transport())
    host = MemoryHost()
    with pytest.raises(JobFailure) as failed:
        await run_stage(host, model, design_stage(ctx), ctx.first_message(), OPENAI)
    assert failed.value.category == "internal"
    [record] = host.runs.values()
    price = DEFAULT_PRICES.lookup("openai", OPENAI_MODEL)
    assert price is not None
    assert record["counters"].cost_micros > price.output_micros(OPENAI.max_tokens)
    assert record["counters"].turns == 1


@pytest.mark.parametrize("arguments", [TOO_DEEP_TO_PARSE, ONE_TOO_DEEP])
async def test_deeply_nested_arguments_go_on_as_their_text(arguments: str) -> None:
    def deep(body: dict[str, Any]) -> None:
        body["output"][3]["arguments"] = arguments

    replay = Replay("openai", [_answer("tool_calls", deep)])
    model = OpenAIProvider(SecretStr(TEST_KEY), transport=replay.transport())
    turn = await model.start(SPEC).turn([])
    assert [c.input for c in turn.tool_calls] == [{}, arguments]


@pytest.mark.parametrize("arguments", [TOO_DEEP_TO_PARSE, ONE_TOO_DEEP])
async def test_the_loop_answers_deeply_nested_arguments_invalid_input(
    monkeypatch: pytest.MonkeyPatch, arguments: str
) -> None:
    script = _correcting_script(arguments)
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    fake = from_dict(script)
    model, api = scripted_openai(fake)
    host = MemoryHost()
    outcome = await run_stage(host, model, design_stage(ctx), ctx.first_message(), OPENAI)
    assert api.problems == []
    assert fake.remaining == 0
    assert ("get_music", "invalid_input") in host.events
    assert outcome.output is not None  # corrected, then submitted


# --- the shared layer ------------------------------------------------------------------------


def _nested(depth: int, inner: Any = 1) -> Any:
    value = inner
    for n in range(depth):
        value = {"a": value} if n % 2 else [value]
    return value


def test_the_client_takes_the_turn_budget_as_its_per_attempt_timeout() -> None:
    client = build_client("openai", KEY, attempt_seconds=300.0)
    assert client.timeout == 300.0
    assert client._client.timeout.read == 300.0  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(("depth", "parsed"), [(1, True), (32, True), (33, False), (900, False)])
def test_arguments_nested_too_deep_go_on_as_their_text(depth: int, parsed: bool) -> None:
    text = json.dumps(_nested(depth))
    ok, value = parse_arguments(text)
    assert ok is parsed
    assert value == (json.loads(text) if parsed else text)
    assert depth > MAX_ARGUMENT_DEPTH or parsed


@pytest.mark.parametrize(
    "text",
    ["[" * 5_000 + "]" * 5_000, '{"track": 2', "", "not json"],  # RecursionError, not JSON
)
def test_arguments_that_do_not_parse_go_on_as_their_text(text: str) -> None:
    assert parse_arguments(text) == (False, text)
    assert tool_input({"type": "object", "properties": {}}, text) == text


def test_arguments_that_are_not_text_go_on_as_they_came() -> None:
    assert parse_arguments(None) == (False, None)
    assert parse_arguments({"a": 1}) == (False, {"a": 1})


def test_a_read_failure_is_billed_internal_with_no_provider_text() -> None:
    def broken() -> Any:
        raise KeyError("secret provider text")

    with pytest.raises(ProviderError) as raised:
        read_answer(broken)
    assert (raised.value.category, raised.value.billed, raised.value.args) == (
        "internal",
        True,
        ("internal",),
    )
    assert (raised.value.__cause__, raised.value.__context__) == (None, None)
    assert read_answer(lambda: 7) == 7


def test_a_category_raised_while_reading_is_kept_and_billed() -> None:
    def unavailable() -> Any:
        raise ProviderError("model_unavailable")

    with pytest.raises(ProviderError) as raised:
        read_answer(unavailable)
    assert (raised.value.category, raised.value.billed) == ("model_unavailable", True)


async def test_the_bounded_request_keeps_billed() -> None:
    async def answered_unreadable() -> Any:
        return read_answer(
            lambda: normalise_usage(prompt_tokens=1, cached_tokens=2, output_tokens=0)
        )

    with pytest.raises(ProviderError) as raised:
        await bounded_request(answered_unreadable)
    assert (raised.value.category, raised.value.billed) == ("internal", True)
    assert (raised.value.__cause__, raised.value.__context__) == (None, None)


@pytest.mark.parametrize("read", [responses_usage, chat_usage])
def test_no_usage_at_all_is_unreadable_never_zeros(read: Any) -> None:
    with pytest.raises(ProviderError) as raised:
        read(None)
    assert raised.value.category == "internal"


def test_the_deepseek_efforts_have_one_source_the_catalog() -> None:
    """#299 item 4: the provider settings are derived from the catalog's list, not a second one."""
    assert {e: e for e in catalog.DEEPSEEK_EFFORTS} == openai_common.DEEPSEEK_REASONING_EFFORTS
    assert not hasattr(openai_common, "DEEPSEEK_EFFORTS")  # one name, the catalog's


# --- DeepSeek --------------------------------------------------------------------------------


def test_one_deepseek_attempt_may_take_the_whole_turn() -> None:
    model = DeepSeekProvider(KEY, turn_seconds=240.0)
    assert model._client.timeout == 240.0  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("usage", [None, *UNREADABLE_DEEPSEEK_USAGE], ids=repr)
async def test_a_deepseek_answer_with_unreadable_usage_is_a_billed_internal_failure(
    usage: Any,
) -> None:
    answer = Answer.from_file("deepseek", "tool_calls", path=DEEPSEEK_PATH)
    body = json.loads(json.dumps(answer.body))
    body["usage"] = usage
    replay = Replay("deepseek", [dataclasses.replace(answer, body=body)])
    model = DeepSeekProvider(KEY, transport=replay.transport())
    with pytest.raises(ProviderError) as raised:
        await model.start(DEEPSEEK_SPEC).turn([])
    assert (raised.value.category, raised.value.billed) == ("internal", True)
    assert (raised.value.__cause__, raised.value.__context__) == (None, None)


async def test_insufficient_system_resource_is_billed_too() -> None:
    model = _deepseek_fixture("insufficient_system_resource")
    with pytest.raises(ProviderError) as raised:
        await model.start(DEEPSEEK_SPEC).turn([])
    assert (raised.value.category, raised.value.billed) == ("model_unavailable", True)


async def test_the_loop_answers_deeply_nested_arguments_from_deepseek_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    fake = from_dict(_correcting_script(TOO_DEEP_TO_PARSE))
    model, api = scripted_deepseek(fake)
    host = MemoryHost()
    outcome = await run_stage(host, model, design_stage(ctx), ctx.first_message(), DEEPSEEK)
    assert api.problems == []
    assert fake.remaining == 0
    assert ("get_music", "invalid_input") in host.events
    assert outcome.output is not None


def test_the_prices_version_is_pinned_exactly() -> None:
    """The #287 review: an exact pin, so a price change must move it (and this test) on purpose."""
    assert PRICES_VERSION == "2026-10-31.2"

"""``AnthropicProvider`` (ADR 0006 section 2) against a fake Messages API behind the real SDK and
the worker's real allowlisted client (``tests/anthropicfake.py``; no network, no real key): the
request shape, the caching stability of ``system`` and ``tools``, the append-only history, the
error mapping and its bounds, usage and cost, and the planted key never leaving the request."""

import asyncio
import io
import itertools
import json
import logging
import traceback
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import SecretStr

from anthropicfake import (
    PLANTED_KEY,
    SPEC,
    FakeAnthropic,
    Reply,
    message,
    status,
    stream,
    text,
    thinking,
    tool_use,
)
from classbeat.agent.provider import ProviderError, ToolResult, Turn
from classbeat.agent.providers.anthropic import AnthropicProvider
from classbeat.log import configure_logging

KEY = SecretStr(PLANTED_KEY)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK's retry backoff (0.5 s and up) would only slow the tests down."""
    monkeypatch.setattr(anthropic.AsyncAnthropic, "_calculate_retry_timeout", lambda *_: 0.0)


def provider(*replies: Reply, **kwargs: Any) -> tuple[AnthropicProvider, FakeAnthropic]:
    fake = FakeAnthropic(list(replies))
    return AnthropicProvider(KEY, transport=fake, **kwargs), fake


async def one_turn(*replies: Reply) -> tuple[Turn, FakeAnthropic]:
    p, fake = provider(*replies)
    return await p.start(SPEC).turn(()), fake


def _objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found += _objects(value)
    elif isinstance(node, list):
        for value in node:
            found += _objects(value)
    return found


# --- the request ---------------------------------------------------------------------------


async def test_the_request_has_the_shape_of_section_2() -> None:
    _, fake = await one_turn(stream(message(text("hi"))))
    [request] = fake.requests
    assert (request.method, str(request.url)) == ("POST", "https://api.anthropic.com/v1/messages")
    assert request.headers["x-api-key"] == PLANTED_KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "anthropic-beta" not in request.headers
    assert "authorization" not in request.headers
    [body] = fake.bodies
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 32_000
    assert body["stream"] is True
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "medium"}
    assert body["tool_choice"] == {"type": "auto"}
    assert body["messages"] == [{"role": "user", "content": SPEC.first_message}]
    for absent in ("temperature", "top_p", "top_k", "fallbacks", "metadata", "stop_sequences"):
        assert absent not in body
    assert set(body) == {
        "model",
        "max_tokens",
        "stream",
        "thinking",
        "output_config",
        "tool_choice",
        "messages",
        "system",
        "tools",
    }


async def test_system_and_the_last_tool_carry_the_cache_breakpoints() -> None:
    _, fake = await one_turn(stream(message(text("hi"))))
    [body] = fake.bodies
    assert body["system"] == [
        {"type": "text", "text": SPEC.system, "cache_control": {"type": "ephemeral"}}
    ]
    tools = body["tools"]
    assert [t["name"] for t in tools] == ["get_music", "submit_draft"]
    assert ["cache_control" in t for t in tools] == [False, True]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}


async def test_every_tool_is_strict_with_closed_objects_and_required() -> None:
    _, fake = await one_turn(stream(message(text("hi"))))
    for tool in fake.bodies[0]["tools"]:
        assert tool["strict"] is True
        assert set(tool) <= {"name", "description", "input_schema", "strict", "cache_control"}
        objects = _objects(tool["input_schema"])
        assert objects
        for obj in objects:
            assert obj["additionalProperties"] is False
            assert isinstance(obj["required"], list)
    submit = fake.bodies[0]["tools"][1]["input_schema"]
    assert submit["required"] == ["name"]
    # What strict mode can't express moves to the description; a const becomes an enum.
    assert "minLength" not in submit["properties"]["name"]
    assert {"type": "string", "enum": ["beat"]} in submit["properties"]["rpm"]["anyOf"]
    assert SPEC.tools[1].input_schema["properties"]["name"]["minLength"] == 1  # spec untouched


async def test_two_runs_of_a_stage_send_identical_system_and_tools() -> None:
    _, a = await one_turn(stream(message(text("one"))))
    p, b = provider(stream(message(tool_use("toolu_1", "get_music", {}), stop="tool_use")))
    conv = p.start(SPEC)
    await conv.turn(())
    await conv.turn((ToolResult("toolu_1", '{"ok": true}', False),))
    raw = [a.requests[0].content, *(r.content for r in b.requests)]
    stable = {
        json.dumps([json.loads(x)["system"], json.loads(x)["tools"]], separators=(",", ":"))
        for x in raw
    }
    assert len(stable) == 1  # json.loads keeps the order: this is the serialized text


# --- the conversation ----------------------------------------------------------------------


async def test_a_turn_maps_the_answer() -> None:
    body = message(
        thinking("let me plan"),
        text("I'll read the music. "),
        tool_use("toolu_a", "get_music", {}),
        tool_use("toolu_b", "get_music", {"track": 2}),
        stop="tool_use",
        model="claude-opus-5-5-answered",
        usage=(1500, 420, 9000, 2100),
    )
    turn, _ = await one_turn(stream(body, request_id="req_abc"))
    assert turn.stop == "tool_use"
    assert turn.text == "I'll read the music. "
    assert [(c.call_id, c.name, c.input) for c in turn.tool_calls] == [
        ("toolu_a", "get_music", {}),
        ("toolu_b", "get_music", {"track": 2}),
    ]
    assert (
        turn.usage.input_tokens,
        turn.usage.output_tokens,
        turn.usage.cache_read_tokens,
        turn.usage.cache_write_tokens,
    ) == (1500, 420, 9000, 2100)
    assert turn.model == "claude-opus-5-5-answered"
    assert turn.request_id == "req_abc"


@pytest.mark.parametrize(
    ("api", "ours"),
    [
        ("tool_use", "tool_use"),
        ("end_turn", "end_turn"),
        ("max_tokens", "max_tokens"),
        ("refusal", "refusal"),
        ("pause_turn", "other"),
        ("stop_sequence", "other"),
        ("model_context_window_exceeded", "other"),
    ],
)
async def test_stop_reasons(api: str, ours: str) -> None:
    turn, _ = await one_turn(stream(message(text("x"), stop=api)))
    assert turn.stop == ours


async def test_the_history_is_append_only_and_thinking_goes_back_unchanged() -> None:
    first = message(
        thinking("plan", signature="sig-keep-me-0042"),
        tool_use("toolu_1", "get_music", {}),
        tool_use("toolu_2", "submit_draft", {"name": "x"}),
        stop="tool_use",
    )
    p, fake = provider(
        stream(first), stream(message(text("no call"))), stream(message(text("still none")))
    )
    conv = p.start(SPEC)
    await conv.turn(())
    results = (
        ToolResult("toolu_1", '{"ok": true, "music": {}}', False),
        ToolResult("toolu_2", '{"ok": false, "errors": ["x"]}', True),
    )
    await conv.turn(results)
    await conv.turn((), reminder="Finish by calling submit_draft with the final draft.")
    sent = [b["messages"] for b in fake.bodies]
    for earlier, later in itertools.pairwise(sent):
        assert later[: len(earlier)] == earlier  # only ever appended to
    assistant = sent[1][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "sig-keep-me-0042",
    }
    assert [b["type"] for b in assistant["content"]] == ["thinking", "tool_use", "tool_use"]
    assert all("parsed_output" not in b for b in assistant["content"])
    assert sent[1][2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": '{"ok": true, "music": {}}',
                "is_error": False,
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu_2",
                "content": '{"ok": false, "errors": ["x"]}',
                "is_error": True,
            },
        ],
    }
    assert sent[2][-1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Finish by calling submit_draft with the final draft."}
        ],
    }
    assert len(sent[2]) == 5


async def test_a_failed_turn_is_not_kept_in_the_history() -> None:
    p, fake = provider(
        stream(message(tool_use("toolu_1", "get_music", {}), stop="tool_use")),
        status(400, "invalid_request_error"),
        stream(message(text("ok"))),
    )
    conv = p.start(SPEC)
    await conv.turn(())
    result = (ToolResult("toolu_1", "{}", False),)
    with pytest.raises(ProviderError):
        await conv.turn(result)
    await conv.turn(result)
    assert fake.bodies[1]["messages"] == fake.bodies[2]["messages"]


async def test_a_turn_with_nothing_to_send_is_refused_before_any_request() -> None:
    p, fake = provider(stream(message(tool_use("toolu_1", "get_music", {}), stop="tool_use")))
    conv = p.start(SPEC)
    with pytest.raises(ProviderError, match="internal"):
        await conv.turn((ToolResult("x", "{}", False),))  # nothing to answer yet
    await conv.turn(())
    with pytest.raises(ProviderError, match="internal"):
        await conv.turn(())
    assert len(fake.requests) == 1


# --- errors --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "category", "attempts"),
    [
        (status(429, "rate_limit_error"), "model_unavailable", 3),
        (status(500, "api_error"), "model_unavailable", 3),
        (status(503, "api_error"), "model_unavailable", 3),
        (status(504, "timeout_error"), "model_unavailable", 3),
        (status(529, "overloaded_error"), "model_unavailable", 3),
        (status(401, "authentication_error"), "model_config", 1),
        (status(403, "permission_error"), "model_config", 1),
        (status(404, "not_found_error"), "model_config", 1),
        (status(400, "invalid_request_error"), "internal", 1),
        (status(413, "request_too_large"), "internal", 1),
        (status(422, "invalid_request_error"), "internal", 1),
        (status(409, "conflict"), "internal", 3),
        (Reply(raise_=lambda r: httpx.ConnectError("refused", request=r)), "model_unavailable", 3),
        (Reply(raise_=lambda r: httpx.ReadTimeout("slow", request=r)), "model_unavailable", 3),
    ],
)
async def test_errors_map_to_a_category_with_bounded_retries(
    reply: Reply, category: str, attempts: int
) -> None:
    p, fake = provider(reply)
    with pytest.raises(ProviderError) as raised:
        await p.start(SPEC).turn(())
    assert raised.value.category == category
    assert raised.value.args == (category,)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert len(fake.requests) == attempts  # max_retries=2: at most three attempts


@pytest.mark.parametrize(
    ("error_type", "category"),
    [
        ("overloaded_error", "model_unavailable"),
        ("api_error", "model_unavailable"),
        ("invalid_request_error", "internal"),
        ("authentication_error", "model_config"),
    ],
)
async def test_an_error_event_inside_the_stream_maps_by_its_type(
    error_type: str, category: str
) -> None:
    body = message(text("partial"), error_event={"type": error_type, "message": "provider text"})
    p, _ = provider(stream(body))
    with pytest.raises(ProviderError) as raised:
        await p.start(SPEC).turn(())
    assert raised.value.category == category


async def test_a_redirect_to_another_host_is_outbound_blocked() -> None:
    redirect = Reply(307, b"", {"location": "https://evil.example/v1/messages"})
    p, fake = provider(redirect)
    with pytest.raises(ProviderError) as raised:
        await p.start(SPEC).turn(())
    assert raised.value.category == "outbound_blocked"
    assert raised.value.__cause__ is None
    # A refusal is never retried: one request, the key sent once.
    assert len(fake.requests) == 1
    assert all(r.url.host == "api.anthropic.com" for r in fake.requests)


async def test_a_same_host_redirect_is_not_followed_and_not_retried() -> None:
    redirect = Reply(307, b"", {"location": "https://api.anthropic.com/v1/elsewhere"})
    p, fake = provider(redirect)
    with pytest.raises(ProviderError) as raised:
        await p.start(SPEC).turn(())
    assert raised.value.category == "internal"
    assert [str(r.url) for r in fake.requests] == ["https://api.anthropic.com/v1/messages"]


@pytest.mark.parametrize(
    "name", ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]
)
def test_sdk_variables_that_could_move_the_client_are_refused(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """ADR 0012 §2: every ``ANTHROPIC_*`` variable is refused, not only ignored."""
    monkeypatch.setenv(name, "https://evil.example")
    with pytest.raises(ValueError, match=name) as raised:
        AnthropicProvider(KEY, transport=FakeAnthropic([stream(message())]))
    assert "evil.example" not in str(raised.value)


async def test_the_environment_cannot_move_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    _, fake = await one_turn(stream(message(text("hi"))))
    [request] = fake.requests
    assert request.url.host == "api.anthropic.com"
    assert request.headers["x-api-key"] == PLANTED_KEY
    assert "authorization" not in request.headers


@pytest.mark.parametrize(
    "name",
    ["ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS", "SSLKEYLOGFILE", "OPENAI_API_KEY", "DEEPSEEK_X"],
)
def test_sdk_environment_that_changes_requests_or_logging_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "debug")
    with pytest.raises(ValueError, match=name):
        AnthropicProvider(KEY, transport=FakeAnthropic([stream(message())]))


async def test_a_turn_is_bounded_in_time() -> None:
    p, _ = provider(Reply(delay=5.0, body=message(text("late"))), turn_seconds=0.2)
    with pytest.raises(ProviderError) as raised:
        await p.start(SPEC).turn(())
    assert raised.value.category == "model_unavailable"


async def test_a_cancel_goes_through_unchanged() -> None:
    p, _ = provider(Reply(delay=5.0, body=message(text("late"))))
    task = asyncio.create_task(p.start(SPEC).turn(()))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- the key -------------------------------------------------------------------------------


ALL_FAILURES = [
    status(401, "authentication_error", f"bad key {PLANTED_KEY}"),
    status(400, "invalid_request_error", f"echo {PLANTED_KEY}"),
    status(529, "overloaded_error"),
    Reply(raise_=lambda r: httpx.ConnectError(f"refused {r.headers['x-api-key']}", request=r)),
    Reply(307, b"", {"location": "https://evil.example/"}),
    stream(message(error_event={"type": "overloaded_error", "message": PLANTED_KEY})),
]


@pytest.mark.parametrize("reply", ALL_FAILURES)
@pytest.mark.parametrize("sdk_debug", [False, True])
@pytest.mark.usefixtures("restore_logging")
async def test_the_key_appears_in_no_log_error_or_repr(reply: Reply, sdk_debug: bool) -> None:
    """A planted key, the worker's logging at DEBUG (and, as a misconfiguration, the SDK's and
    httpx's loggers forced to DEBUG too), provider error texts echoing it: it is in the request
    header only."""
    out = io.StringIO()
    configure_logging("DEBUG", stream=out)
    if sdk_debug:
        for name in ("anthropic", "httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.DEBUG)
    p, fake = provider(reply)
    conv = p.start(SPEC)
    with pytest.raises(ProviderError) as raised:
        await conv.turn(())
    seen = [
        out.getvalue(),
        str(raised.value),
        repr(raised.value),
        "".join(traceback.format_exception(raised.value)),
        repr(p),
        repr(conv),
    ]
    assert all(PLANTED_KEY not in s for s in seen)
    assert fake.requests[0].headers["x-api-key"] == PLANTED_KEY  # it was really sent
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    [failure] = [e for e in lines if e["message"] == "model request failed"]
    assert set(failure) >= {"error", "status", "request_id", "category"}
    assert failure["category"] == raised.value.category
    if not sdk_debug:
        assert "provider text" not in out.getvalue()
        assert all(e["logger"].startswith("classbeat") for e in lines)


@pytest.mark.usefixtures("restore_logging")
async def test_the_key_is_not_in_a_successful_turn() -> None:
    out = io.StringIO()
    configure_logging("DEBUG", stream=out)
    logging.getLogger("anthropic").setLevel(logging.DEBUG)
    turn, fake = await one_turn(stream(message(text("hi"), tool_use("t", "get_music", {}))))
    assert PLANTED_KEY not in repr(turn) + out.getvalue()
    assert fake.requests[0].headers["x-api-key"] == PLANTED_KEY

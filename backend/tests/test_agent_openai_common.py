"""The shared OpenAI-SDK layer (ADR 0012 §3, M3c P1): the SDK pin, the client, the error map, the
bounded request, usage normalisation and the effort map. Replayed answers only, no network."""

import asyncio
import logging
from importlib import metadata
from typing import Any

import httpx
import openai
import pytest
from openai.types import CompletionUsage
from openai.types.responses import ResponseUsage
from pydantic import SecretStr

from classbeat.agent.catalog import CatalogModel
from classbeat.agent.provider import ProviderError, Usage
from classbeat.agent.providers import openai_common
from classbeat.agent.providers.openai_common import (
    EffortRefused,
    ModelOutboundBlocked,
    base_url,
    bounded_request,
    build_client,
    category_of,
    chat_usage,
    normalise_usage,
    provider_effort,
    responses_usage,
)
from classbeat.egress import MODEL_ALLOWLISTS, AllowlistTransport, OutboundBlocked
from providerreplay import TEST_KEY, Answer, Replay

KEY = SecretStr(TEST_KEY)
MODELS = {"object": "list", "data": []}
FAST_RETRY = {"retry-after-ms": "1"}  # the SDK's own retry delay, cut to a millisecond


# --- the SDK pin ---------------------------------------------------------------------------


def test_the_pinned_openai_is_on_httpx_not_httpx2() -> None:
    assert metadata.version("openai") == "2.54.0"
    requires = metadata.requires("openai") or []
    unconditional = [r for r in requires if "extra ==" not in r]
    assert any(r.startswith("httpx") and not r.startswith("httpx2") for r in unconditional)
    assert not any(r.startswith("httpx2") for r in unconditional)
    with pytest.raises(metadata.PackageNotFoundError):
        metadata.version("httpx2")  # the extra is never installed


# --- the client ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "url"),
    [("openai", "https://api.openai.com/v1"), ("deepseek", "https://api.deepseek.com")],
)
def test_the_client_is_built_only_from_explicit_values(provider: str, url: str) -> None:
    client = build_client(provider, KEY)  # pyright: ignore[reportArgumentType]
    assert str(client.base_url).rstrip("/") == url
    assert client.api_key == TEST_KEY
    assert (client.organization, client.project) == (None, None)
    assert (client.admin_api_key, client.webhook_secret, client.websocket_base_url) == (
        None,
        None,
        None,
    )
    assert client.max_retries == 2
    assert client.timeout == 120.0
    http = client._client
    assert isinstance(http, httpx.AsyncClient)
    transport = http._transport
    assert isinstance(transport, AllowlistTransport)
    assert transport._allowlist is MODEL_ALLOWLISTS[provider]  # pyright: ignore[reportArgumentType]
    assert TEST_KEY not in repr(client)


def test_the_base_url_comes_from_the_providers_one_host() -> None:
    assert base_url("openai") == "https://api.openai.com/v1"
    assert base_url("deepseek") == "https://api.deepseek.com"
    for bad in ("anthropic", "fake", "OpenAI"):
        with pytest.raises(ValueError, match="OpenAI-SDK"):
            base_url(bad)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("name", ["OPENAI_BASE_URL", "OPENAI_ORG_ID", "DEEPSEEK_API_KEY"])
def test_no_client_while_an_sdk_variable_is_set(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "https://planted.example")
    with pytest.raises(ValueError, match=name) as raised:
        build_client("openai", KEY)
    assert "planted.example" not in str(raised.value)


@pytest.mark.parametrize("key", ["", "   "])
def test_no_client_without_a_key(key: str) -> None:
    with pytest.raises(ValueError, match="no API key"):
        build_client("openai", SecretStr(key))


async def test_a_request_carries_the_test_key_to_the_one_host_and_nothing_else() -> None:
    replay = Replay("openai", [Answer("/v1/models", body=MODELS, method="GET")])
    client = build_client("openai", KEY, transport=replay.transport())
    await client.models.list()
    assert replay.problems == []
    assert len(replay.requests) == 1


@pytest.mark.parametrize(
    ("provider", "elsewhere"),
    [
        ("openai", "https://api.deepseek.com/chat/completions"),
        ("deepseek", "https://api.openai.com/v1/responses"),
        ("openai", "https://api.anthropic.com/v1/messages"),
        ("deepseek", "https://evil.example/x"),
    ],
)
async def test_a_refused_request_is_outbound_blocked_and_not_retried(
    provider: str, elsewhere: str, caplog: pytest.LogCaptureFixture
) -> None:
    replay = Replay(provider, [])
    client = build_client(provider, KEY, transport=replay.transport())  # pyright: ignore[reportArgumentType]
    caplog.set_level(logging.WARNING, logger="classbeat.egress")

    async def refused() -> object:
        return await client.get(elsewhere, cast_to=object)

    with pytest.raises(ProviderError) as raised:
        await bounded_request(refused)
    assert raised.value.category == "outbound_blocked"
    assert replay.requests == []  # nothing reached the network
    blocked = [r for r in caplog.records if r.getMessage() == "outbound request blocked"]
    assert len(blocked) == 1  # one attempt: the SDK did not retry it


async def test_the_sdk_passes_the_refusal_through_unwrapped() -> None:
    client = build_client("openai", KEY, transport=Replay("openai", []).transport())
    with pytest.raises(ModelOutboundBlocked) as raised:
        await client.get("https://api.deepseek.com/x", cast_to=object)
    assert isinstance(raised.value.__cause__, OutboundBlocked)
    assert raised.value.host == "api.deepseek.com"


async def test_a_server_error_is_retried_twice_then_unavailable() -> None:
    error = {"error": {"message": "planted provider text", "type": "server_error"}}
    replay = Replay(
        "openai",
        [Answer("/v1/models", 500, error, FAST_RETRY, method="GET") for _ in range(3)],
    )
    client = build_client("openai", KEY, transport=replay.transport())
    with pytest.raises(ProviderError) as raised:
        await bounded_request(client.models.list)
    assert raised.value.category == "model_unavailable"
    assert len(replay.requests) == 3  # max_retries=2
    assert replay.problems == []
    assert "planted" not in str(raised.value)


# --- the error map -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "cls", "category"),
    [
        (400, openai.BadRequestError, "internal"),
        (401, openai.AuthenticationError, "model_config"),
        (403, openai.PermissionDeniedError, "model_config"),
        (404, openai.NotFoundError, "model_config"),
        (409, openai.ConflictError, "internal"),
        (422, openai.UnprocessableEntityError, "internal"),
        (429, openai.RateLimitError, "model_unavailable"),
        (500, openai.InternalServerError, "model_unavailable"),
        (502, openai.InternalServerError, "model_unavailable"),
        (503, openai.InternalServerError, "model_unavailable"),
        (504, openai.InternalServerError, "model_unavailable"),
    ],
)
async def test_each_status_maps_by_its_exception_class(
    status: int, cls: type[Exception], category: str, caplog: pytest.LogCaptureFixture
) -> None:
    body = {"error": {"message": "rate limit planted-text", "type": "x", "code": "insufficient"}}
    headers = {"x-request-id": "req_planted123", **FAST_RETRY}
    replay = Replay(
        "openai", [Answer("/v1/models", status, body, headers, method="GET") for _ in range(3)]
    )
    client = build_client("openai", KEY, transport=replay.transport())
    seen: list[BaseException] = []

    async def call() -> Any:
        try:
            return await client.models.list()
        except Exception as exc:
            seen.append(exc)
            raise

    caplog.set_level(logging.WARNING, logger=openai_common.__name__)
    with pytest.raises(ProviderError) as raised:
        await bounded_request(call)
    assert type(seen[0]) is cls
    assert raised.value.category == category
    assert raised.value.args == (category,)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    [line] = [r for r in caplog.records if r.getMessage() == "model request failed"]
    assert (vars(line)["error"], vars(line)["status"]) == (cls.__name__, status)
    assert vars(line)["request_id"] == "req_planted123"
    logged = " ".join(f"{k}={v}" for k, v in vars(line).items())
    assert "planted-text" not in logged
    assert "insufficient" not in logged


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def _status(cls: type[openai.APIStatusError], status: int, message: str) -> Exception:
    return cls(message, response=httpx.Response(status, request=_request()), body=None)


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (openai.APITimeoutError(_request()), "model_unavailable"),
        (openai.APIConnectionError(request=_request()), "model_unavailable"),
        (httpx.ReadTimeout("t"), "model_unavailable"),
        (TimeoutError(), "model_unavailable"),
        (_status(openai.APIStatusError, 529, "x"), "model_unavailable"),
        (_status(openai.APIStatusError, 418, "x"), "internal"),
        (ValueError("x"), "internal"),
        (openai.OpenAIError("x"), "internal"),
    ],
)
def test_the_other_failures_map_by_class(exc: Exception, category: str) -> None:
    assert category_of(exc) == category


@pytest.mark.parametrize(
    "message",
    ["rate limit exceeded", "Unauthorized", "model not found", "overloaded", "timeout"],
)
def test_the_message_text_never_decides(message: str) -> None:
    assert category_of(_status(openai.BadRequestError, 400, message)) == "internal"
    assert category_of(openai.OpenAIError(message)) == "internal"


def test_a_refusal_found_in_the_chain_is_outbound_blocked() -> None:
    outer = openai.APIConnectionError(request=_request())
    outer.__cause__ = OutboundBlocked("evil.example")
    assert category_of(outer) == "outbound_blocked"
    wrapped = RuntimeError("x")
    wrapped.__context__ = outer
    assert category_of(wrapped) == "outbound_blocked"


async def test_a_turn_is_bounded_in_time() -> None:
    async def slow() -> None:
        await asyncio.sleep(5)

    with pytest.raises(ProviderError) as raised:
        await bounded_request(slow, turn_seconds=0.05)
    assert raised.value.category == "model_unavailable"
    assert raised.value.__context__ is None


async def test_a_provider_error_and_a_cancel_go_through_as_they_are() -> None:
    async def config() -> None:
        raise ProviderError("model_config")

    with pytest.raises(ProviderError) as raised:
        await bounded_request(config)
    assert raised.value.category == "model_config"

    async def cancelled() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await bounded_request(cancelled)


async def test_a_success_is_returned() -> None:
    async def ok() -> int:
        return 7

    assert await bounded_request(ok) == 7


# --- usage ---------------------------------------------------------------------------------


def test_cached_tokens_are_taken_out_of_the_input() -> None:
    assert normalise_usage(prompt_tokens=1000, cached_tokens=600, output_tokens=250) == Usage(
        input_tokens=400, output_tokens=250, cache_read_tokens=600, cache_write_tokens=0
    )


@pytest.mark.parametrize("bad", [None, -5, "12", 1.5, True])
def test_a_missing_or_malformed_count_is_zero(bad: object) -> None:
    usage = normalise_usage(prompt_tokens=100, cached_tokens=bad, output_tokens=bad)
    assert usage == Usage(input_tokens=100, output_tokens=0, cache_read_tokens=0)


def test_more_cached_than_prompt_tokens_is_not_trusted() -> None:
    with pytest.raises(ProviderError) as raised:
        normalise_usage(prompt_tokens=10, cached_tokens=11, output_tokens=1)
    assert raised.value.category == "internal"


def test_the_responses_and_chat_usage_shapes() -> None:
    responses = ResponseUsage.model_validate(
        {
            "input_tokens": 900,
            # SDK 2.54 also reports cache writes; they stay input tokens (ADR 0012 §3).
            "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 50},
            "output_tokens": 300,
            "output_tokens_details": {"reasoning_tokens": 200},
            "total_tokens": 1200,
        }
    )
    assert responses_usage(responses) == Usage(100, 300, 800, 0)
    chat = CompletionUsage.model_validate(
        {
            "prompt_tokens": 500,
            "completion_tokens": 70,
            "total_tokens": 570,
            "prompt_tokens_details": {"cached_tokens": 128},
            "completion_tokens_details": {"reasoning_tokens": 40},
        }
    )
    assert chat_usage(chat) == Usage(372, 70, 128, 0)
    bare = CompletionUsage.model_validate(
        {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
    )
    assert chat_usage(bare) == Usage(5, 1, 0, 0)


# --- effort --------------------------------------------------------------------------------


def _entry(provider: str, efforts: tuple[str, ...]) -> CatalogModel:
    from classbeat.egress import MODEL_HOSTS

    hosts: Any = MODEL_HOSTS
    fields: Any = (provider, f"{provider}-test", efforts, hosts[provider], "owner_pending")
    return CatalogModel(*fields)


def test_openai_effort_maps_to_reasoning_effort() -> None:
    entry = _entry("openai", ("low", "medium", "high", "xhigh"))
    assert [provider_effort(entry, e) for e in entry.efforts] == ["low", "medium", "high", "xhigh"]


def test_deepseek_has_no_effort_parameter() -> None:
    assert provider_effort(_entry("deepseek", ("medium",)), "medium") is None


@pytest.mark.parametrize(
    ("provider", "efforts", "effort"),
    [
        ("openai", ("medium",), "high"),  # not listed in the catalog
        ("openai", ("medium", "max"), "max"),  # listed, but OpenAI has no such setting
        ("deepseek", ("medium",), "low"),
        ("openai", ("medium",), "turbo"),
    ],
)
def test_an_unsupported_effort_is_refused_never_changed(
    provider: str, efforts: tuple[str, ...], effort: str
) -> None:
    with pytest.raises(EffortRefused):
        provider_effort(_entry(provider, efforts), effort)


def test_an_anthropic_entry_is_not_this_layers() -> None:
    from classbeat.agent.catalog import find

    entry = find("anthropic", "claude-opus-5-5")
    assert entry is not None
    with pytest.raises(EffortRefused):
        provider_effort(entry, "medium")


def test_the_client_gets_exactly_these_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every value the SDK would otherwise read from ``OPENAI_*`` is passed, explicitly."""
    seen: dict[str, Any] = {}
    real = openai.AsyncOpenAI

    def spy(**kwargs: Any) -> openai.AsyncOpenAI:
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(openai_common.openai, "AsyncOpenAI", spy)
    build_client("deepseek", KEY)
    http = seen.pop("http_client")
    assert isinstance(http, httpx.AsyncClient)
    assert seen == {
        "api_key": TEST_KEY,
        "admin_api_key": None,
        "organization": None,
        "project": None,
        "webhook_secret": None,
        "base_url": "https://api.deepseek.com",
        "websocket_base_url": None,
        "max_retries": 2,
        "timeout": 120.0,
    }

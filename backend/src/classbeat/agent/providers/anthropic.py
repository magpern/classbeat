"""``AnthropicProvider`` (ADR 0006 section 2): one stage run against the Messages API.

- **Client:** the official ``anthropic`` SDK, ``AsyncAnthropic`` with the key from the
  ``anthropic_api_key`` secret file, an explicit ``base_url`` and the worker's allowlisted
  ``httpx.AsyncClient`` (``classbeat.egress``: ``https`` to ``api.anthropic.com`` on 443 only,
  every redirect hop checked, ``trust_env=False``), ``max_retries=2``, ``timeout=120`` per
  attempt; each turn, retries included, is bounded by ``turn_seconds`` too.
- **Request:** streamed (``messages.stream(...)``, then ``get_final_message()``); ``model``,
  ``max_tokens`` and ``output_config={"effort": ...}`` from the ``StageSpec``,
  ``thinking={"type": "adaptive"}``, ``tool_choice={"type": "auto"}``; no sampling parameters, no
  fallbacks, no beta, no server tool. Every tool is ``strict: true``; its schema goes through the
  SDK's ``transform_schema`` (what strict mode can't express moves into the description, the loop
  still validates every input itself) and every object gets ``required``.
- **Caching:** ``cache_control: {"type": "ephemeral"}`` on the system block and on the last tool.
  ``system`` and ``tools`` are built from the ``StageSpec`` alone, so they are byte-identical for
  every run of a stage and prompt version; the brief and music are in the first user message.
- **History:** the conversation keeps the native message list and only ever appends to it: each
  answer's content blocks (thinking blocks with their signatures included) go back unchanged, then
  one user message with every tool result of the turn (and the reminder, if any).
- **Errors** map to a category from the SDK's exception types (and a stream error event's
  ``error.type``), never from message text; a request the allowlist refused is
  ``outbound_blocked``. The ``ProviderError`` is raised without its cause or context. Logs get the
  class name, the status and the request id only.
- **Usage:** input, output, cache read and cache write tokens of each answer, as the API reports
  them; the loop prices them (``classbeat.agent.prices``).

The key lives in the SDK client only; nothing here logs, stores or repr()s it.
"""

import asyncio
import copy
import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any, Final

import anthropic
import httpx
from pydantic import SecretStr

from classbeat.agent.provider import (
    ProviderError,
    StageSpec,
    StopReason,
    ToolCall,
    ToolResult,
    Turn,
    Usage,
)
from classbeat.egress import OutboundBlocked, allowlisted_async_client

logger = logging.getLogger(__name__)

BASE_URL: Final = "https://api.anthropic.com"
MAX_RETRIES: Final = 2
TIMEOUT_SECONDS: Final = 120.0
TURN_SECONDS: Final = 300.0
CACHE: Final = {"type": "ephemeral"}
# The SDK reads these at import or construction even with an explicit key and base URL: debug
# logging of request bodies, and extra headers on every request. The worker never sets them.
REFUSED_ENVIRONMENT: Final = ("ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS")

_STOPS: Final[Mapping[str, StopReason]] = {
    "tool_use": "tool_use",
    "end_turn": "end_turn",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}
# ``error.type`` of an error event inside a 200 stream (the HTTP status is no help there).
_UNAVAILABLE_EVENTS: Final = frozenset(
    {"overloaded_error", "api_error", "rate_limit_error", "timeout_error"}
)
_MODEL_CONFIG_EVENTS: Final = frozenset(
    {"authentication_error", "permission_error", "not_found_error"}
)
_REQUEST_ID_MAX = 100


def environment_problems() -> list[str]:
    """The names of the SDK environment variables that are set and must not be."""
    return [name for name in REFUSED_ENVIRONMENT if name in os.environ]


def _strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The SDK's strict-mode transform, plus ``required`` on every object. A ``const`` (which
    the transform would move into the description) is first written as the one-value ``enum``
    strict mode supports."""

    def consts(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: consts(v) for k, v in node.items()}  # pyright: ignore[reportUnknownVariableType]
            if "const" in out and "enum" not in out:
                out["enum"] = [out.pop("const")]
            return out  # pyright: ignore[reportUnknownVariableType]
        if isinstance(node, list):
            return [consts(v) for v in node]  # pyright: ignore[reportUnknownVariableType]
        return node

    out = anthropic.transform_schema(consts(copy.deepcopy(dict(schema))))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "required" not in node:  # pyright: ignore[reportUnknownMemberType]
                node["required"] = []
            for value in node.values():  # pyright: ignore[reportUnknownVariableType]
                walk(value)
        elif isinstance(node, list):
            for value in node:  # pyright: ignore[reportUnknownVariableType]
                walk(value)

    walk(out)
    return out


def request_shape(spec: StageSpec) -> dict[str, Any]:
    """The stable part of every request of a stage run: ``system`` and ``tools``."""
    tools: list[dict[str, Any]] = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": _strict_schema(tool.input_schema),
            "strict": True,
        }
        for tool in spec.tools
    ]
    if tools:
        tools[-1]["cache_control"] = dict(CACHE)
    system = [{"type": "text", "text": spec.system, "cache_control": dict(CACHE)}]
    return {"system": system, "tools": tools}


def _printable(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return "".join(c if c.isprintable() and not c.isspace() else "?" for c in value)[
        :_REQUEST_ID_MAX
    ]


def _outbound_blocked(exc: BaseException) -> bool:
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OutboundBlocked):
            return True
        pending += [current.__cause__, current.__context__]
    return False


def _event_type(exc: anthropic.APIStatusError) -> str | None:
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(error, dict):
            kind = error.get("type")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return kind if isinstance(kind, str) else None
    return None


def category_of(exc: BaseException) -> str:
    """ADR 0006 section 2, most specific first, from types and status codes only."""
    if _outbound_blocked(exc):
        return "outbound_blocked"
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return "model_config"
    if isinstance(exc, anthropic.NotFoundError):
        return "model_config"
    if isinstance(exc, anthropic.RateLimitError | anthropic.InternalServerError):
        return "model_unavailable"
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500 or exc.status_code == 429:
            return "model_unavailable"  # 503, 504, 529 (overloaded) and the rest of 5xx
        kind = _event_type(exc)  # an error event inside a stream that started as 200
        if kind in _UNAVAILABLE_EVENTS:
            return "model_unavailable"
        if kind in _MODEL_CONFIG_EVENTS:
            return "model_config"
        return "internal"  # BadRequestError and the other 4xx
    if isinstance(exc, anthropic.APITimeoutError | anthropic.APIConnectionError):
        return "model_unavailable"
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return "model_unavailable"
    return "internal"


def _log_failure(exc: BaseException, category: str) -> None:
    status = exc.status_code if isinstance(exc, anthropic.APIStatusError) else None
    request_id = exc.request_id if isinstance(exc, anthropic.APIStatusError) else None
    logger.warning(
        "model request failed",
        extra={
            "error": type(exc).__name__,
            "status": status,
            "request_id": _printable(request_id),
            "category": category,
        },
    )


def _block_param(block: Any) -> dict[str, Any]:
    """A response content block as it goes back next turn: what the API sent, unchanged."""
    data = block.to_dict(mode="json")
    if data.get("type") == "text":
        data.pop("parsed_output", None)  # the SDK's own field, not the API's
    return data


def _usage(usage: Any) -> Usage:
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
    )


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = MAX_RETRIES,
        turn_seconds: float = TURN_SECONDS,
    ) -> None:
        """``transport`` replaces the network under the allowlist in tests (an
        ``httpx.MockTransport``); the allowlist check still runs on every request."""
        if environment_problems():
            raise ValueError("refused SDK environment: " + ", ".join(environment_problems()))
        self._turn_seconds = turn_seconds
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key.get_secret_value(),
            base_url=BASE_URL,
            http_client=allowlisted_async_client(timeout=TIMEOUT_SECONDS, inner=transport),
            max_retries=max_retries,
            timeout=TIMEOUT_SECONDS,
        )

    def __repr__(self) -> str:
        return "AnthropicProvider()"

    def start(self, spec: StageSpec) -> "AnthropicConversation":
        return AnthropicConversation(self._client, spec, self._turn_seconds)

    async def aclose(self) -> None:
        await self._client.close()


class AnthropicConversation:
    def __init__(self, client: anthropic.AsyncAnthropic, spec: StageSpec, turn_seconds: float):
        self._client = client
        self._spec = spec
        self._shape = request_shape(spec)
        self._turn_seconds = turn_seconds
        self._messages: list[dict[str, Any]] = []
        self.requests = 0

    def __repr__(self) -> str:
        return f"AnthropicConversation(stage={self._spec.stage!r}, requests={self.requests})"

    def _next_user(self, results: Sequence[ToolResult], reminder: str | None) -> dict[str, Any]:
        if not self._messages:
            if results or reminder is not None:
                raise ProviderError("internal")  # the first turn sends the first message only
            return {"role": "user", "content": self._spec.first_message}
        content: list[dict[str, Any]] = [
            {
                "type": "tool_result",
                "tool_use_id": r.call_id,
                "content": r.content,
                "is_error": r.is_error,
            }
            for r in results
        ]
        if reminder is not None:
            content.append({"type": "text", "text": reminder})
        if not content:
            raise ProviderError("internal")  # nothing to answer the last turn with
        return {"role": "user", "content": content}

    async def _request(self, messages: list[dict[str, Any]]) -> tuple[Any, str | None]:
        spec = self._spec
        async with self._client.messages.stream(
            model=spec.model,
            max_tokens=spec.max_tokens,
            system=self._shape["system"],
            tools=self._shape["tools"],
            tool_choice={"type": "auto"},
            thinking={"type": "adaptive"},
            output_config={"effort": spec.effort},  # pyright: ignore[reportArgumentType]
            messages=messages,  # pyright: ignore[reportArgumentType]
        ) as stream:
            final = await stream.get_final_message()
            return final, stream.request_id

    async def turn(self, results: Sequence[ToolResult], *, reminder: str | None = None) -> Turn:
        user = self._next_user(results, reminder)
        messages = [*self._messages, user]
        self.requests += 1
        try:
            async with asyncio.timeout(self._turn_seconds):
                final, request_id = await self._request(messages)
            turn = self._turn(final, request_id)
        except ProviderError:
            raise
        except Exception as exc:  # not CancelledError: a cancel goes through as it is
            category = category_of(exc)
            _log_failure(exc, category)
            raise ProviderError(category) from None
        self._messages = [*messages, {"role": "assistant", "content": turn_blocks(final)}]
        return turn

    def _turn(self, final: Any, request_id: str | None) -> Turn:
        blocks = list(final.content)
        stop: StopReason = _STOPS.get(final.stop_reason or "") or "other"
        text = "".join(b.text for b in blocks if b.type == "text")
        calls = tuple(ToolCall(b.id, b.name, b.input) for b in blocks if b.type == "tool_use")
        return Turn(
            stop=stop,
            text=text,
            tool_calls=calls,
            usage=_usage(final.usage),
            model=str(final.model),
            request_id=_printable(request_id),
        )


def turn_blocks(final: Any) -> list[dict[str, Any]]:
    return [_block_param(b) for b in final.content]

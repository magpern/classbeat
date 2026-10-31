"""The shared OpenAI-SDK layer of ``OpenAIProvider`` and ``DeepSeekProvider`` (ADR 0012 §3).

Both adapters (M3c P2, P3) build on this module; it sends nothing itself.

- **SDK:** the official ``openai`` package, pinned at 2.54.0, the last release on ``httpx``
  (3.x requires ``httpx2``, which the egress allowlist is not built on; reviewed with #120).
- **Client** (``build_client``): ``AsyncOpenAI`` with the key from the provider's secret file, the
  provider's one API host as ``base_url`` (``classbeat.egress.MODEL_HOSTS``, the host its catalog
  entries carry), the provider's **one-host** allowlisted ``httpx.AsyncClient``
  (``allowlisted_async_client(provider=...)``: an OpenAI client can never reach DeepSeek's host or
  the other way round), ``max_retries=2``, ``timeout=120`` per attempt, and ``organization``,
  ``project``, ``admin_api_key``, ``webhook_secret`` and ``websocket_base_url`` all ``None``. The
  SDK still reads ``OPENAI_*`` variables for the values given as ``None``; the client is never
  built while any refused SDK variable is set (``classbeat.config.model_sdk_environment_problems``,
  also refused at worker start). A request the allowlist refuses is raised as
  ``ModelOutboundBlocked`` (an ``openai.OpenAIError``, which the SDK passes through unwrapped and
  never retries) with the ``OutboundBlocked`` as its cause.
- **A bounded request** (``bounded_request``): one turn, retries included, ends within
  ``turn_seconds``; any failure becomes ``ProviderError(category)`` raised with neither a cause
  nor a context, after one log line with the exception class, the status and the request id.
- **Errors** (``category_of``) by SDK exception class and status code, never by message text:
  rate limits, server errors, timeouts and connection errors are ``model_unavailable``;
  ``AuthenticationError``, ``PermissionDeniedError`` and ``NotFoundError`` are ``model_config``;
  ``BadRequestError`` and the rest are ``internal``; an allowlist refusal is ``outbound_blocked``.
- **Usage** (``normalise_usage``): the OpenAI APIs count cached prompt tokens inside the prompt
  tokens and have no cache-write charge, so ``input = prompt - cached``, ``cache_read = cached``,
  ``cache_write = 0`` and ``output`` = completion tokens, reasoning included. The SDK (2.54) also
  reports ``cache_write_tokens``; they stay inside ``input`` (priced at the input rate), as ADR
  0012 §3 decides, and are not counted again.
- **Strict tool schemas** (``strict_schema``, ``tool_input``): strict function mode needs every
  property in ``required`` and ``additionalProperties: false`` on every object. An optional
  property becomes a union with ``null``, and a ``null`` the model then sends for one is dropped
  from the parsed input (``strip_added_nulls``) before the loop validates it against the stage's
  own model, unchanged (ADR 0006 §4): the transform never loosens what the loop accepts.
- **Effort** (``provider_effort``): classbeat's effort maps per catalog model to the provider's
  own setting (OpenAI ``reasoning.effort``; DeepSeek: none, the model choice is the effort). An
  effort the catalog entry does not list, or one the provider has no setting for, is refused
  (``EffortRefused``), never changed.

The key lives in the SDK client only; nothing here logs, stores or repr()s it.
"""

import asyncio
import copy
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, Literal

import httpx
import openai
from pydantic import SecretStr

from classbeat.agent.catalog import CatalogModel
from classbeat.agent.provider import ProviderError, Usage
from classbeat.config import model_sdk_environment_problems
from classbeat.egress import MODEL_HOSTS, OutboundBlocked, allowlisted_async_client

logger = logging.getLogger(__name__)

OpenAISDKProvider = Literal["openai", "deepseek"]
# The API path under each provider's host that the SDK's request paths are relative to.
# OpenAI's API lives under /v1; DeepSeek's at the root (its documented base URL; P3 confirms it
# from DeepSeek's API documentation and records the date read).
BASE_PATHS: Final[Mapping[OpenAISDKProvider, str]] = {"openai": "/v1", "deepseek": ""}
MAX_RETRIES: Final = 2
TIMEOUT_SECONDS: Final = 120.0
TURN_SECONDS: Final = 300.0
_REQUEST_ID_MAX = 100
# classbeat effort -> OpenAI ``reasoning.effort``. ``max`` has no OpenAI value: refused.
OPENAI_EFFORTS: Final[Mapping[str, str]] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
}


class ModelOutboundBlocked(openai.OpenAIError):
    """The allowlist refused a model request (``__cause__`` is the ``OutboundBlocked``)."""

    def __init__(self, refused: OutboundBlocked) -> None:
        super().__init__("outbound request blocked")
        self.host = refused.host


class EffortRefused(ValueError):
    """An effort the catalog entry does not list or the provider cannot express."""


# --- the client ----------------------------------------------------------------------------


def base_url(provider: OpenAISDKProvider) -> str:
    """``https://`` + the provider's one host + its API path (from code, never a setting)."""
    if provider not in BASE_PATHS:
        raise ValueError("not an OpenAI-SDK provider")
    return f"https://{MODEL_HOSTS[provider]}{BASE_PATHS[provider]}"


def build_client(
    provider: OpenAISDKProvider,
    api_key: SecretStr,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    max_retries: int = MAX_RETRIES,
) -> openai.AsyncOpenAI:
    """The SDK client of ``provider``. ``transport`` replaces the network under the allowlist in
    tests (an ``httpx.MockTransport``); the allowlist check still runs on every request."""
    url = base_url(provider)
    refused = model_sdk_environment_problems()
    if refused:
        raise ValueError("refused SDK environment: " + ", ".join(refused))
    key = api_key.get_secret_value()
    if not key.strip():
        raise ValueError("no API key")
    return openai.AsyncOpenAI(
        api_key=key,
        admin_api_key=None,
        organization=None,
        project=None,
        webhook_secret=None,
        base_url=url,
        websocket_base_url=None,
        http_client=allowlisted_async_client(
            provider=provider,
            timeout=TIMEOUT_SECONDS,
            inner=transport,
            refused=ModelOutboundBlocked,
        ),
        max_retries=max_retries,
        timeout=TIMEOUT_SECONDS,
    )


# --- errors --------------------------------------------------------------------------------


def _outbound_blocked(exc: BaseException) -> bool:
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OutboundBlocked | ModelOutboundBlocked):
            return True
        pending += [current.__cause__, current.__context__]
    return False


def category_of(exc: BaseException) -> str:
    """ADR 0012 §3 (the table of ADR 0006 §2), most specific first, from classes and status codes
    only."""
    if _outbound_blocked(exc):
        return "outbound_blocked"
    if isinstance(
        exc, openai.AuthenticationError | openai.PermissionDeniedError | openai.NotFoundError
    ):
        return "model_config"
    if isinstance(exc, openai.RateLimitError | openai.InternalServerError):
        return "model_unavailable"
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500 or exc.status_code == 429:
            return "model_unavailable"
        return "internal"  # BadRequestError and the other 4xx
    if isinstance(exc, openai.APITimeoutError | openai.APIConnectionError):
        return "model_unavailable"
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return "model_unavailable"
    return "internal"


def _printable(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    text = "".join(c if c.isprintable() and not c.isspace() else "?" for c in value)
    return text[:_REQUEST_ID_MAX]


def log_failure(exc: BaseException, category: str) -> None:
    """One line: the exception class, the status and the request id; never the message."""
    status = exc.status_code if isinstance(exc, openai.APIStatusError) else None
    request_id = exc.request_id if isinstance(exc, openai.APIStatusError) else None
    logger.warning(
        "model request failed",
        extra={
            "error": type(exc).__name__,
            "status": status,
            "request_id": _printable(request_id),
            "category": category,
        },
    )


async def bounded_request[T](
    request: Callable[[], Awaitable[T]], turn_seconds: float = TURN_SECONDS
) -> T:
    """``await request()`` within ``turn_seconds`` (the SDK's retries included). A failure is
    ``ProviderError(category)`` with no ``__cause__`` and no ``__context__`` (raised outside the
    handler), so no provider text travels with it. A cancel goes through as it is."""
    category: str | None = None
    try:
        async with asyncio.timeout(turn_seconds):
            return await request()
    except ProviderError:
        raise
    except Exception as exc:
        category = category_of(exc)
        log_failure(exc, category)
    raise ProviderError(category)


# --- usage ---------------------------------------------------------------------------------


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def normalise_usage(
    *, prompt_tokens: object, cached_tokens: object, output_tokens: object
) -> Usage:
    """ADR 0012 §3: ``input = prompt - cached``, ``cache_read = cached``, ``cache_write = 0``,
    ``output`` with reasoning. Missing or malformed counts are 0; a cached count above the
    prompt is not trusted (an answer that does not add up is ``internal``)."""
    prompt, cached = _count(prompt_tokens), _count(cached_tokens)
    if cached > prompt:
        raise ProviderError("internal")
    return Usage(
        input_tokens=prompt - cached,
        output_tokens=_count(output_tokens),
        cache_read_tokens=cached,
        cache_write_tokens=0,
    )


def responses_usage(usage: Any) -> Usage:
    """The Responses API's ``usage`` (``input_tokens``, ``input_tokens_details.cached_tokens``,
    ``output_tokens`` with ``output_tokens_details.reasoning_tokens`` inside it)."""
    details = getattr(usage, "input_tokens_details", None)
    return normalise_usage(
        prompt_tokens=getattr(usage, "input_tokens", 0),
        cached_tokens=getattr(details, "cached_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
    )


def chat_usage(usage: Any) -> Usage:
    """Chat Completions' ``usage`` (``prompt_tokens``, ``prompt_tokens_details.cached_tokens``,
    ``completion_tokens`` with reasoning inside it)."""
    details = getattr(usage, "prompt_tokens_details", None)
    return normalise_usage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0),
        cached_tokens=getattr(details, "cached_tokens", 0),
        output_tokens=getattr(usage, "completion_tokens", 0),
    )


# --- strict tool schemas -------------------------------------------------------------------

# Keywords strict mode does not take; their rule stays with the loop's validation. Length limits
# are said in the description instead, so the model still sees them.
_DROPPED: Final = frozenset({"default", "title"})
_LENGTHS: Final = (("minLength", "at least {} characters"), ("maxLength", "at most {} characters"))


def _nullable(node: Mapping[str, Any]) -> bool:
    if node.get("type") == "null":
        return True
    return any(_nullable(branch) for branch in node.get("anyOf", ()) if isinstance(branch, dict))


def _with_null(node: dict[str, Any]) -> dict[str, Any]:
    if _nullable(node):
        return node
    description = node.pop("description", None)
    out: dict[str, Any] = {"anyOf": [node, {"type": "null"}]}
    if description is not None:
        out["description"] = description
    return out


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict(item) for item in node]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {
        key: _strict(value)
        for key, value in node.items()  # pyright: ignore[reportUnknownVariableType]
        if key not in _DROPPED
    }
    if "const" in out and "enum" not in out:
        out["enum"] = [out.pop("const")]
    notes = [text.format(out.pop(key)) for key, text in _LENGTHS if key in out]
    if notes:
        said = out.get("description")
        rule = "Text of " + " and ".join(notes) + "."
        out["description"] = f"{said} {rule}" if isinstance(said, str) and said else rule
    if out.get("type") == "object":
        properties: dict[str, Any] = out.get("properties", {})
        required = set(node.get("required", ()))  # pyright: ignore[reportUnknownArgumentType]
        out["properties"] = {
            name: value if name in required else _with_null(value)
            for name, value in properties.items()
        }
        out["required"] = list(properties)
        out["additionalProperties"] = False
    return out


def strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """A tool's input schema in the form strict function mode accepts (a pure transform)."""
    return _strict(copy.deepcopy(dict(schema)))


def _resolve(node: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
        if isinstance(target, dict):
            return target  # pyright: ignore[reportUnknownVariableType]
    return node


def _strip(value: Any, node: Mapping[str, Any], root: Mapping[str, Any]) -> Any:
    node = _resolve(node, root)
    if isinstance(value, dict):
        branches = [node, *(b for b in node.get("anyOf", ()) if isinstance(b, dict))]
        for branch in (_resolve(b, root) for b in branches):
            properties = branch.get("properties")
            if isinstance(properties, dict):
                required = set(branch.get("required", ()))
                return {
                    key: _strip(item, properties.get(key, {}), root)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    for key, item in value.items()  # pyright: ignore[reportUnknownVariableType]
                    if not (item is None and key in properties and key not in required)
                }
        return value  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        branches = [node, *(b for b in node.get("anyOf", ()) if isinstance(b, dict))]
        for branch in (_resolve(b, root) for b in branches):
            items = branch.get("items")
            if isinstance(items, dict):
                return [_strip(item, items, root) for item in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        return value  # pyright: ignore[reportUnknownVariableType]
    return value


def strip_added_nulls(schema: Mapping[str, Any], value: Any) -> Any:
    """``value`` (parsed tool input) without the ``null`` of each property the **original**
    ``schema`` does not require: the nulls strict mode made the model send. Nothing else changes;
    an unknown key or a null of a required property stays, for the loop to refuse."""
    return _strip(value, schema, schema)


def tool_input(schema: Mapping[str, Any], arguments: str) -> Any:
    """A tool call's ``arguments`` (JSON text) as the loop's ``ToolCall.input``. Text that is not
    JSON is passed on as it is, so the loop answers ``invalid_input``; it is never guessed at."""
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments
    return strip_added_nulls(schema, parsed)


# --- effort --------------------------------------------------------------------------------


def provider_effort(entry: CatalogModel, effort: str) -> str | None:
    """The provider's setting for classbeat's ``effort`` on ``entry``: OpenAI's
    ``reasoning.effort`` value, or ``None`` for DeepSeek (no effort parameter). Refused unless
    the catalog entry lists the effort and the provider can express it."""
    if effort not in entry.efforts:
        raise EffortRefused("the model does not list this effort in the catalog")
    if entry.provider == "openai":
        if effort not in OPENAI_EFFORTS:
            raise EffortRefused("OpenAI has no setting for this effort")
        return OPENAI_EFFORTS[effort]
    if entry.provider == "deepseek":
        return None
    raise EffortRefused("not an OpenAI-SDK provider")

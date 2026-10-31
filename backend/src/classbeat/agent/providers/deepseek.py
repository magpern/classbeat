"""``DeepSeekProvider``: DeepSeek's Chat Completions API behind the loop's interface (ADR 0012 §3,
M3c P3). Off unless ``CLASSBEAT_DEEPSEEK_ENABLED`` (§2); its catalog entries are ``owner_pending``.

Built on ``classbeat.agent.providers.openai_common`` (the ``openai`` SDK, client, errors, usage,
effort). The request rules come from DeepSeek's API documentation, read 2026-10-31 (docs only;
nothing here was learned by calling the API): the quick start and its base URL, the models and
pricing page, the thinking-mode guide, the tool-calls guide, the create-chat-completion reference
and the error codes.

- **Base URL**: ``https://api.deepseek.com``, the host root (no ``/v1``), as documented for the
  OpenAI SDK; the client's allowlist is that one host.
- **Request** (one ``chat.completions.create`` per turn, not streamed): ``model``; ``messages`` =
  the conversation's own history (the stage's system prompt as the ``system`` message first);
  ``tools`` = the stage's tools as ``function`` tools with their own input schemas, **not strict**
  (strict mode is a DeepSeek beta behind ``/beta``; a follow-up, ADR 0012 §3), so the loop's own
  validation is what refuses a malformed call (ADR 0006 §4); ``tool_choice="auto"`` (``required``
  and named choices are refused in thinking mode); ``max_tokens`` from the spec;
  ``reasoning_effort`` mapped from the catalog entry (``low``, ``high``, ``max`` only:
  ``provider_effort``); ``thinking`` enabled, explicitly. No sampling parameters (ignored in
  thinking mode), no ``response_format``, no ``user_id``, no ``logprobs``.
- **Reasoning** (ADR 0012 §3, amended 2026-10-31): DeepSeek's thinking-mode guide says that when a
  request has tools, the ``reasoning_content`` of every earlier assistant turn must be passed back
  in all later requests, or the API answers 400. So each assistant message goes back with its
  ``reasoning_content``, **in this conversation's memory only**: it is never logged, never put
  in a ``Turn`` (its ``text`` is the visible ``content`` only), an event, a stored row, a
  revision or an eval result, and it goes back only to DeepSeek, within the same stage run.
- **History** (append-only, kept here): system, then the first message as a ``user`` message;
  after each answer, the ``assistant`` message (``content``, ``reasoning_content``, ``tool_calls``
  as returned); then one ``tool`` message per result, in the loop's order, and the reminder, if
  any, as a ``user`` message. A failed turn appends nothing.
- **Stops** from ``finish_reason`` (§3): ``tool_calls`` -> ``tool_use``, ``stop`` -> ``end_turn``,
  ``length`` -> ``max_tokens``, ``content_filter`` -> ``refusal``; ``insufficient_system_resource``
  is a failed turn, ``model_unavailable``; anything else (``aborted`` included) is ``other``.
- **Turn**: each call's ``arguments`` parsed as JSON, or passed on as the text they are, for the
  loop to refuse; usage by ``chat_usage`` (cache hits are ``cached_tokens``, no cache write);
  ``model`` as the API reports it. A model the catalog does not list for DeepSeek, or an effort its
  entry does not take, is ``model_config`` before any request.

Every failure is a ``ProviderError`` raised outside any exception handler (``bounded_request``).
"""

import json
from collections.abc import Sequence
from typing import Any, Final

import httpx
import openai
from pydantic import SecretStr

from classbeat.agent.catalog import find
from classbeat.agent.provider import (
    ProviderError,
    StageSpec,
    StopReason,
    ToolCall,
    ToolResult,
    Turn,
)
from classbeat.agent.providers.openai_common import (
    MAX_RETRIES,
    TURN_SECONDS,
    EffortRefused,
    bounded_request,
    build_client,
    chat_usage,
    printable,
    provider_effort,
)

_STOPS: Final[dict[str, StopReason]] = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "refusal",
}
_UNAVAILABLE: Final = frozenset({"insufficient_system_resource"})
THINKING: Final = {"type": "enabled"}


def request_tools(spec: StageSpec) -> list[dict[str, Any]]:
    """The stage's tools as non-strict ``function`` tools (byte-stable per stage and prompt
    version)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": dict(t.input_schema),
            },
        }
        for t in spec.tools
    ]


def _effort(spec: StageSpec) -> str | None:
    entry = find("deepseek", spec.model)
    if entry is None:
        return None
    try:
        return provider_effort(entry, spec.effort)
    except EffortRefused:
        return None


def _arguments(text: Any) -> Any:
    """The call's arguments as JSON, or as they came: the loop refuses what is not an object."""
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = MAX_RETRIES,
        turn_seconds: float = TURN_SECONDS,
    ) -> None:
        """Build it only through ``classbeat.agent.providers.deepseek_provider(settings)``, which
        refuses while DeepSeek is off. ``transport`` replaces the network under the allowlist in
        tests; the allowlist check still runs on every request."""
        self._turn_seconds = turn_seconds
        self._client = build_client(
            "deepseek", api_key, transport=transport, max_retries=max_retries
        )

    def __repr__(self) -> str:
        return "DeepSeekProvider()"

    def start(self, spec: StageSpec) -> "DeepSeekConversation":
        return DeepSeekConversation(self._client, spec, self._turn_seconds)

    async def aclose(self) -> None:
        await self._client.close()


class DeepSeekConversation:
    def __init__(self, client: openai.AsyncOpenAI, spec: StageSpec, turn_seconds: float) -> None:
        self._client = client
        self._spec = spec
        self._turn_seconds = turn_seconds
        self._effort = _effort(spec)
        self._tools = request_tools(spec)
        # Holds the reasoning text DeepSeek must get back (module docstring): memory only.
        self._messages: list[dict[str, Any]] = []
        self.requests = 0

    def __repr__(self) -> str:
        return f"DeepSeekConversation(stage={self._spec.stage!r}, requests={self.requests})"

    def _next(self, results: Sequence[ToolResult], reminder: str | None) -> list[dict[str, Any]]:
        if not self._messages:
            if results or reminder is not None:
                raise ProviderError("internal")  # the first turn sends the first message only
            return [
                {"role": "system", "content": self._spec.system},
                {"role": "user", "content": self._spec.first_message},
            ]
        messages: list[dict[str, Any]] = [
            {"role": "tool", "tool_call_id": r.call_id, "content": r.content} for r in results
        ]
        if reminder is not None:
            messages.append({"role": "user", "content": reminder})
        if not messages:
            raise ProviderError("internal")  # nothing to answer the last turn with
        return messages

    async def turn(self, results: Sequence[ToolResult], *, reminder: str | None = None) -> Turn:
        if self._effort is None:
            raise ProviderError("model_config")  # not a DeepSeek catalog model, or its effort
        sent = [*self._messages, *self._next(results, reminder)]
        self.requests += 1
        turn, answer = await bounded_request(lambda: self._request(sent), self._turn_seconds)
        self._messages = [*sent, answer]
        return turn

    async def _request(self, sent: list[dict[str, Any]]) -> tuple[Turn, dict[str, Any]]:
        spec = self._spec
        # Plain JSON-shaped values; the SDK's TypedDicts describe the same shapes.
        messages: Any = sent
        tools: Any = self._tools
        effort: Any = self._effort
        response = await self._client.chat.completions.create(
            model=spec.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=spec.max_tokens,
            reasoning_effort=effort,
            extra_body={"thinking": dict(THINKING)},
            stream=False,
        )
        [choice] = response.choices  # one choice was asked for; anything else is ``internal``
        message = choice.message
        finish = str(choice.finish_reason or "")
        if finish in _UNAVAILABLE:
            raise ProviderError("model_unavailable")
        calls = tuple(
            ToolCall(c.id, c.function.name, _arguments(c.function.arguments))
            for c in message.tool_calls or ()
            if c.type == "function"
        )
        turn = Turn(
            stop=_STOPS.get(finish, "other"),
            text=message.content or "",
            tool_calls=calls,
            usage=chat_usage(response.usage),
            model=str(response.model),
            request_id=printable(getattr(response, "_request_id", None)),
        )
        return turn, _assistant(message)


def _assistant(message: Any) -> dict[str, Any]:
    """The answer as the next request's ``assistant`` message, its reasoning text included."""
    out: dict[str, Any] = {"role": "assistant", "content": message.content}
    reasoning = getattr(message, "reasoning_content", None)
    if isinstance(reasoning, str):
        out["reasoning_content"] = reasoning
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in message.tool_calls
        ]
    return out

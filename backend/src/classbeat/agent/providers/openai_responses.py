"""``OpenAIProvider``: OpenAI's Responses API behind the loop's interface (ADR 0012 §3, M3c P2).

Built on ``classbeat.agent.providers.openai_common`` (client, errors, usage, strict schemas,
effort). The request rules come from OpenAI's API reference and its reasoning, function-calling
and prompt-caching guides, read 2026-10-31 (docs only; nothing here was learned by calling the API).

- **Request** (one ``responses.create`` per turn, not streamed): ``model``; ``instructions`` = the
  stage's system prompt; ``input`` = the conversation's own history; ``tools`` = the stage's tools
  as strict ``function`` tools (``strict_schema``), never a built-in tool; ``tool_choice="auto"``;
  ``parallel_tool_calls=True`` (the loop answers them together, in order, ADR 0006 §1);
  ``store=False`` (nothing kept at OpenAI) with ``include=["reasoning.encrypted_content"]``, so
  reasoning goes back on the next turn; ``max_output_tokens`` from the spec; ``reasoning.effort``
  mapped from the catalog entry (``provider_effort``); ``prompt_cache_key`` = the stage name and
  the first 40 characters of its prompt version, never a user or job id. No sampling parameters,
  no ``previous_response_id``, no ``metadata``, no ``user``/``safety_identifier``. Instructions,
  tools and the cache key are built once per stage run from the spec only, so two runs of a stage
  send the same bytes for them.
- **History** (append-only, kept here): the first message as a user ``input_text``; after each
  answer, every output item as the API returned it (reasoning items with their encrypted content,
  function calls, messages: ``to_dict()``, unchanged); then one ``function_call_output`` per
  result, in the loop's order, and the reminder, if any, as a user ``input_text``. A failed turn
  appends nothing.
- **Stops** (ADR 0012 §3): a ``refusal`` content part, or ``incomplete`` for ``content_filter``,
  is ``refusal``; ``incomplete`` for ``max_output_tokens`` is ``max_tokens``; ``completed`` with
  function calls is ``tool_use``, without is ``end_turn``; anything else is ``other``.
- **Turn**: the text of the ``output_text`` parts; each call's arguments through ``tool_input``
  (the added ``null``s dropped); usage by ``responses_usage``; ``model`` as the API reports it; the
  request id from the SDK. A model the catalog does not list for OpenAI, or an effort its entry
  does not take, is ``model_config`` before any request.

Every failure is a ``ProviderError`` raised outside any exception handler (``bounded_request``).
"""

from collections.abc import Mapping, Sequence
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
    printable,
    provider_effort,
    responses_usage,
    strict_schema,
    tool_input,
)
from classbeat.agent.stage import prompt_version

CACHE_KEY_VERSION_CHARS: Final = 40
INCLUDE: Final = ("reasoning.encrypted_content",)
_ANY_OBJECT: Final[Mapping[str, Any]] = {"type": "object"}


def _user_text(text: str) -> dict[str, Any]:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def request_shape(spec: StageSpec) -> dict[str, Any]:
    """The parts of every request of a stage run that depend on the spec only (byte-stable per
    stage and prompt version)."""
    return {
        "instructions": spec.system,
        "tools": [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": strict_schema(t.input_schema),
                "strict": True,
            }
            for t in spec.tools
        ],
        "prompt_cache_key": (
            f"{spec.stage}-{prompt_version(spec.system, spec.tools)[:CACHE_KEY_VERSION_CHARS]}"
        ),
    }


def _effort(spec: StageSpec) -> str | None:
    entry = find("openai", spec.model)
    if entry is None:
        return None
    try:
        return provider_effort(entry, spec.effort)
    except EffortRefused:
        return None


class OpenAIProvider:
    name = "openai"

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
        self._turn_seconds = turn_seconds
        self._client = build_client("openai", api_key, transport=transport, max_retries=max_retries)

    def __repr__(self) -> str:
        return "OpenAIProvider()"

    def start(self, spec: StageSpec) -> "OpenAIConversation":
        return OpenAIConversation(self._client, spec, self._turn_seconds)

    async def aclose(self) -> None:
        await self._client.close()


class OpenAIConversation:
    def __init__(self, client: openai.AsyncOpenAI, spec: StageSpec, turn_seconds: float) -> None:
        self._client = client
        self._spec = spec
        self._turn_seconds = turn_seconds
        self._effort = _effort(spec)
        self._shape = request_shape(spec)
        self._schemas = {t.name: t.input_schema for t in spec.tools}
        self._input: list[dict[str, Any]] = []
        self.requests = 0

    def __repr__(self) -> str:
        return f"OpenAIConversation(stage={self._spec.stage!r}, requests={self.requests})"

    def _next_input(
        self, results: Sequence[ToolResult], reminder: str | None
    ) -> list[dict[str, Any]]:
        if not self._input:
            if results or reminder is not None:
                raise ProviderError("internal")  # the first turn sends the first message only
            return [_user_text(self._spec.first_message)]
        items: list[dict[str, Any]] = [
            {"type": "function_call_output", "call_id": r.call_id, "output": r.content}
            for r in results
        ]
        if reminder is not None:
            items.append(_user_text(reminder))
        if not items:
            raise ProviderError("internal")  # nothing to answer the last turn with
        return items

    async def turn(self, results: Sequence[ToolResult], *, reminder: str | None = None) -> Turn:
        if self._effort is None:
            raise ProviderError("model_config")  # not an OpenAI catalog model, or its effort
        sent = [*self._input, *self._next_input(results, reminder)]
        self.requests += 1
        turn, output = await bounded_request(lambda: self._request(sent), self._turn_seconds)
        self._input = [*sent, *output]
        return turn

    async def _request(self, sent: list[dict[str, Any]]) -> tuple[Turn, list[dict[str, Any]]]:
        spec, shape = self._spec, self._shape
        # Plain JSON-shaped values; the SDK's TypedDicts describe the same shapes.
        history: Any = sent
        include: Any = list(INCLUDE)
        reasoning: Any = {"effort": self._effort}
        response = await self._client.responses.create(
            model=spec.model,
            instructions=shape["instructions"],
            input=history,
            tools=shape["tools"],
            tool_choice="auto",
            parallel_tool_calls=True,
            store=False,
            include=include,
            max_output_tokens=spec.max_tokens,
            reasoning=reasoning,
            prompt_cache_key=shape["prompt_cache_key"],
            stream=False,
        )
        return self._turn(response), [item.to_dict() for item in response.output]

    def _turn(self, response: Any) -> Turn:
        items = list(response.output)
        parts = [p for i in items if i.type == "message" for p in i.content]
        calls = tuple(
            ToolCall(
                i.call_id, i.name, tool_input(self._schemas.get(i.name, _ANY_OBJECT), i.arguments)
            )
            for i in items
            if i.type == "function_call"
        )
        return Turn(
            stop=stop_of(response, refused=any(p.type == "refusal" for p in parts), calls=calls),
            text="".join(p.text for p in parts if p.type == "output_text"),
            tool_calls=calls,
            usage=responses_usage(response.usage),
            model=str(response.model),
            request_id=printable(getattr(response, "_request_id", None)),
        )


def stop_of(response: Any, *, refused: bool, calls: tuple[ToolCall, ...]) -> StopReason:
    """ADR 0012 §3's stop table."""
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None)
    if refused or (response.status == "incomplete" and reason == "content_filter"):
        return "refusal"
    if response.status == "incomplete" and reason == "max_output_tokens":
        return "max_tokens"
    if response.status == "completed":
        return "tool_use" if calls else "end_turn"
    return "other"

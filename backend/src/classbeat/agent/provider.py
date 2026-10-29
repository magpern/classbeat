"""The provider-neutral interface of the agent loop (ADR 0006 section 1).

The loop (``classbeat.agent.loop``) owns stages, tools, limits and validation; an adapter
(``classbeat.agent.providers``) only turns one request into one response. ``provider.start(spec)``
opens one stage run; each ``conversation.turn(results)`` sends the tool results of the previous
turn (all of them in one message) and returns the model's next turn. The adapter keeps the
provider's native, append-only message list itself.

A turn ending ``end_turn`` without the stage's final tool gets one tool-less user reminder: the
loop passes it as ``reminder`` with no results (ADR 0006 section 1).

An adapter reports a failed request by raising ``ProviderError`` with one category of ADR 0005
section 4 (``model_unavailable``, ``model_config``, ``outbound_blocked`` when the worker's
allowlist refused the request, or ``internal``), never with the provider's message or a request
or response body.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

StopReason = Literal["tool_use", "end_turn", "max_tokens", "refusal", "other"]
PROVIDER_CATEGORIES: Final = frozenset(
    {"model_unavailable", "model_config", "outbound_blocked", "internal"}
)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("token counts are whole numbers of at least 0")

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def prompt_tokens(self) -> int:
        """Everything the request sent, cached or not."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool call of a turn. ``input`` is what the provider parsed; the loop validates it."""

    call_id: str
    name: str
    input: Any


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The answer to one call: JSON text of a fixed shape (``classbeat.agent.dispatcher``)."""

    call_id: str
    content: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class Turn:
    stop: StopReason
    text: str  # visible text; kept in memory for the run only, never logged or stored
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    model: str  # the model that actually answered
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDef:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StageSpec:
    """What a stage run sends. ``system`` and ``tools`` are byte-stable per stage and prompt
    version (no dates, ids or user data); the brief and music are in ``first_message``."""

    stage: str
    system: str
    tools: tuple[ToolDef, ...]
    first_message: str
    model: str
    effort: str
    max_tokens: int
    final_tool: str = ""


class ProviderError(Exception):
    """A request failed with ``category`` (``args == (category,)``); no provider text."""

    def __init__(self, category: str) -> None:
        if category not in PROVIDER_CATEGORIES:
            category = "internal"
        super().__init__(category)
        self.category = category


class Conversation(Protocol):
    async def turn(self, results: Sequence[ToolResult], *, reminder: str | None = None) -> Turn: ...


class ModelProvider(Protocol):
    name: str

    def start(self, spec: StageSpec) -> Conversation: ...

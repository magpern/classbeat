"""A scripted DeepSeek Chat Completions API for ``DeepSeekProvider`` (ADR 0012 §5; no network).

``ScriptedChat`` is the ``inner`` transport of ``DeepSeekProvider`` (under the one-host allowlist):
it answers ``POST /chat/completions`` from a ``FakeProvider`` script, so the stage and smoke scripts
run unchanged through the real adapter. Every request goes through ``Replay``'s checks (https,
host, path, the test key, a JSON body, no planted value) and these:

- the fixed request shape (``check_request``): non-strict ``function`` tools, ``tool_choice``
  auto, thinking enabled, ``reasoning_effort`` low, high or max, no sampling parameters, not
  streamed;
- a request that starts a stage run is the system message and the first user message only;
  every later one is the previous request's ``messages``, then the previous answer's assistant
  message **with its ``reasoning_content``** (DeepSeek's 400 rule), then one ``tool`` message per
  call of that answer **in the same order**, then at most one user reminder.

Each answer carries ``reasoning_content`` = ``<marker>-<n>`` (``reasoning`` names the marker), so a
test can follow the reasoning text. A script call whose input is ``{"$raw": "<text>"}`` is sent with
exactly that text as its ``arguments`` (a malformed call). An ``error`` turn becomes 503 with
``x-should-retry: false``, 401 or 400.
"""

import json
from collections.abc import Sequence
from typing import Any, Final

import httpx
from pydantic import SecretStr

from classbeat.agent.provider import ProviderError, StageSpec, ToolDef, ToolResult, Turn
from classbeat.agent.providers.deepseek import DeepSeekProvider
from classbeat.agent.providers.fake import FakeConversation, FakeProvider, ScriptMismatch
from providerreplay import TEST_KEY, Replay, ReplayMismatch

PATH: Final = "/chat/completions"
DEEPSEEK_MODEL: Final = "deepseek-flash"  # catalogued, priced, owner_pending: synthetic data only
DEEPSEEK_EFFORT: Final = "high"  # DeepSeek takes low, high and max only
REFUSED: Final = (
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "response_format",
    "logprobs",
    "top_logprobs",
    "user_id",
    "user",
    "parallel_tool_calls",
)
_FINISH: Final = {
    "tool_use": "tool_calls",
    "end_turn": "stop",
    "max_tokens": "length",
    "refusal": "content_filter",
    "other": "aborted",
}
_ERRORS: Final = {
    "model_unavailable": (503, {"x-should-retry": "false"}),
    "model_config": (401, {}),
    "internal": (400, {}),
}


def check_request(body: dict[str, Any]) -> list[str]:
    """What every DeepSeekProvider request must hold (ADR 0012 §3); the problems found."""
    problems: list[str] = []
    if body.get("tool_choice") != "auto":
        problems.append("tool_choice is not auto")
    if body.get("thinking") != {"type": "enabled"}:
        problems.append("thinking is not enabled")
    if body.get("reasoning_effort") not in ("low", "high", "max"):
        problems.append("reasoning_effort is not low, high or max")
    if body.get("stream"):
        problems.append("streams")
    if not isinstance(body.get("max_tokens"), int):
        problems.append("no max_tokens")
    problems += [f"sends {key}" for key in REFUSED if key in body]
    for tool in body.get("tools") or []:
        function = tool.get("function") or {}
        if tool.get("type") != "function" or "strict" in function or "strict" in tool:
            problems.append("a tool that is not a plain function")
    return problems


def render_message(turn: Turn, n: int, reasoning: str) -> dict[str, Any]:
    """The fake's turn as the answer's assistant message (request ``n``)."""
    message: dict[str, Any] = {
        "role": "assistant",
        "content": turn.text or None,
        "reasoning_content": f"{reasoning}-{n}",
    }
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": _arguments(call.input)},
            }
            for call in turn.tool_calls
        ]
    return message


def _arguments(value: Any) -> str:
    if isinstance(value, dict) and set(value) == {"$raw"}:  # pyright: ignore[reportUnknownArgumentType]
        return str(value["$raw"])  # pyright: ignore[reportUnknownArgumentType]
    return json.dumps(value)


def render(turn: Turn, body: dict[str, Any], n: int, reasoning: str) -> dict[str, Any]:
    usage = turn.usage
    prompt = usage.prompt_tokens  # a scripted cache write is billed as a miss: none at DeepSeek
    return {
        "id": f"chatcmpl-{n}",
        "object": "chat.completion",
        "created": 1_790_000_000 + n,
        "model": body.get("model"),
        "choices": [
            {
                "index": 0,
                "message": render_message(turn, n, reasoning),
                "finish_reason": _FINISH[turn.stop],
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": usage.output_tokens,
            "total_tokens": prompt + usage.output_tokens,
            "prompt_cache_hit_tokens": usage.cache_read_tokens,
            "prompt_cache_miss_tokens": prompt - usage.cache_read_tokens,
            "prompt_tokens_details": {"cached_tokens": usage.cache_read_tokens},
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


class ScriptedChat(Replay):
    """A scripted Chat Completions API over ``fake`` (see the module docstring)."""

    def __init__(
        self, fake: FakeProvider, *, planted: Sequence[str] = (), reasoning: str = "reasoning"
    ) -> None:
        super().__init__("deepseek", [], planted=planted)
        self.fake = fake
        self.reasoning = reasoning
        self._conversation: FakeConversation | None = None
        self._last_sent: list[Any] = []
        self._last_answer: dict[str, Any] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        n = len(self.requests)
        body = self.checked(request, n, "POST", PATH)
        if not isinstance(body, dict):
            raise self._problem(f"request {n}: no body")
        for problem in check_request(body):  # pyright: ignore[reportUnknownArgumentType]
            raise self._problem(f"request {n}: {problem}")
        sent: list[Any] = body.get("messages") or []  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        results, reminder = self._new_messages(n, body, sent)  # pyright: ignore[reportUnknownArgumentType]
        assert self._conversation is not None
        self._conversation.record.turns.append((results, reminder))
        try:
            turn = self.fake.next_turn(results)
        except ProviderError as exc:
            status, headers = _ERRORS[exc.category]
            error = {"error": {"message": "scripted", "type": exc.category, "code": None}}
            self._last_answer = None
            return httpx.Response(status, json=error, headers=headers)
        except ScriptMismatch as exc:
            raise self._problem(f"request {n}: {exc}") from None
        answer = render(turn, body, n, self.reasoning)  # pyright: ignore[reportUnknownArgumentType]
        self._last_sent, self._last_answer = sent, answer["choices"][0]["message"]
        return httpx.Response(200, json=answer, headers={"x-request-id": f"req_{n}"})

    def _new_messages(
        self, n: int, body: dict[str, Any], sent: list[Any]
    ) -> tuple[tuple[ToolResult, ...], str | None]:
        roles = [m.get("role") for m in sent]
        if roles == ["system", "user"]:
            self._start(body, sent[0]["content"], sent[1]["content"])
            return (), None
        if self._conversation is None or self._last_answer is None:
            raise self._problem(f"request {n}: a later turn without an earlier answer")
        prefix = [*self._last_sent, self._last_answer]
        if sent[: len(prefix)] != prefix:
            raise self._problem(
                f"request {n}: not the last history and answer (reasoning included), appended to"
            )
        new = sent[len(prefix) :]
        reminder = None
        if new and new[-1].get("role") == "user":
            reminder = new[-1]["content"]
            new = new[:-1]
        calls = [c["id"] for c in self._last_answer.get("tool_calls", [])]
        if [m.get("tool_call_id") for m in new] != calls or any(
            set(m) != {"role", "tool_call_id", "content"} or m["role"] != "tool" for m in new
        ):
            raise self._problem(f"request {n}: not one tool message per call, in order")
        results = tuple(ToolResult(m["tool_call_id"], m["content"], _is_error(m)) for m in new)
        return results, reminder

    def _start(self, body: dict[str, Any], system: str, first: str) -> None:
        tools = tuple(
            ToolDef(f["name"], f["description"], f["parameters"])
            for f in (t["function"] for t in body.get("tools", []))
        )
        spec = StageSpec(
            stage="",
            system=system,
            tools=tools,
            first_message=first,
            model=body.get("model", ""),
            effort=body.get("reasoning_effort", ""),
            max_tokens=body.get("max_tokens", 0),
        )
        self._conversation = self.fake.start(spec)
        self._last_sent, self._last_answer = [], None


def _is_error(message: dict[str, Any]) -> bool:
    try:
        return json.loads(message["content"]).get("ok") is False
    except (ValueError, AttributeError):
        return False


def scripted_deepseek(
    fake: FakeProvider, *, planted: Sequence[str] = (), reasoning: str = "reasoning"
) -> tuple[DeepSeekProvider, ScriptedChat]:
    """A ``DeepSeekProvider`` (test key) whose requests ``fake`` answers."""
    api = ScriptedChat(fake, planted=planted, reasoning=reasoning)
    return DeepSeekProvider(SecretStr(TEST_KEY), transport=api.transport()), api


__all__ = [
    "DEEPSEEK_EFFORT",
    "DEEPSEEK_MODEL",
    "PATH",
    "ReplayMismatch",
    "ScriptedChat",
    "check_request",
    "render",
    "scripted_deepseek",
]

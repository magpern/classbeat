"""A scripted OpenAI Responses API for ``OpenAIProvider`` (ADR 0012 §5; no network).

``ScriptedResponses`` is the ``inner`` transport of ``OpenAIProvider`` (under the one-host
allowlist): it answers ``POST /v1/responses`` from a ``FakeProvider`` script, so the stage and
smoke scripts of ``tests/agent_scripts`` and the pick fakes run unchanged through the real
adapter. Every request goes through ``Replay``'s checks (https, host, path, the test key, no
organisation or project header, a JSON body, no planted value) and these:

- the fixed request shape (``check_request``): ``store`` false, the encrypted reasoning
  included, ``tool_choice`` auto, strict ``function`` tools only, no sampling parameters, no
  ``previous_response_id``;
- a request that starts a stage run holds the first user message only; every later one is the
  previous request's ``input`` plus the previous answer's ``output`` items **unchanged**, then one
  ``function_call_output`` per call of that answer **in the same order**, then at most one user
  reminder (the history is append-only).

Each request becomes a ``FakeProvider`` turn: a stage run's spec is rebuilt from the request
(stage from the cache key, system, tools, first message, model, effort, max tokens), the
results from the ``function_call_output`` items, and the reminder from the trailing message.
The fake's ``Turn`` is rendered as a Responses body: a reasoning item with encrypted content,
then the message and function-call items, the status of its stop, and its usage with cached reads
and cache writes inside ``input_tokens``. An ``error`` turn becomes an error status (503 with
``x-should-retry: false``, so the SDK does not retry it; 401; 400). A script call whose input is
``{"$raw": "<text>"}`` is sent with that text as its ``arguments``, as the model wrote it.
"""

import json
from collections.abc import Sequence
from typing import Any, Final

import httpx
from pydantic import SecretStr

from classbeat.agent.provider import ProviderError, StageSpec, ToolDef, ToolResult, Turn
from classbeat.agent.providers.fake import FakeConversation, FakeProvider, ScriptMismatch
from classbeat.agent.providers.openai_responses import OpenAIProvider
from providerreplay import TEST_KEY, Replay, ReplayMismatch

PATH: Final = "/v1/responses"
SAMPLING: Final = ("temperature", "top_p", "top_logprobs", "seed", "frequency_penalty")
_ERRORS: Final = {
    "model_unavailable": (503, {"x-should-retry": "false"}),
    "model_config": (401, {}),
    "internal": (400, {}),
}


def check_request(body: dict[str, Any]) -> list[str]:
    """What every OpenAIProvider request must hold (ADR 0012 §3); the problems found."""
    problems: list[str] = []
    fixed = {
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    for key, value in fixed.items():
        if body.get(key) != value:
            problems.append(f"{key} is not {value!r}")
    for key in (*SAMPLING, "previous_response_id", "user", "safety_identifier", "metadata"):
        if key in body:
            problems.append(f"sends {key}")
    if body.get("stream"):
        problems.append("streams")
    if not isinstance(body.get("max_output_tokens"), int):
        problems.append("no max_output_tokens")
    if set(body.get("reasoning") or {}) != {"effort"}:
        problems.append("reasoning is not the effort alone")
    for tool in body.get("tools") or []:
        if tool.get("type") != "function" or tool.get("strict") is not True:
            problems.append("a tool that is not a strict function")
    return problems


def _user_text(item: Any) -> str | None:
    if not isinstance(item, dict) or item.get("role") != "user":
        return None
    parts: Any = item.get("content")
    if not isinstance(parts, list) or len(parts) != 1 or parts[0].get("type") != "input_text":
        return None
    return parts[0].get("text")


def _is_error(content: str) -> bool:
    try:
        return json.loads(content).get("ok") is False
    except (ValueError, AttributeError):
        return False


def _arguments(value: Any) -> str:
    if isinstance(value, dict) and set(value) == {"$raw"}:  # pyright: ignore[reportUnknownArgumentType]
        return str(value["$raw"])  # pyright: ignore[reportUnknownArgumentType]
    return json.dumps(value)


def render(turn: Turn, body: dict[str, Any], n: int) -> dict[str, Any]:
    """The fake's turn as a Responses API answer to ``body`` (request ``n``)."""
    output: list[dict[str, Any]] = [
        {"type": "reasoning", "id": f"rs_{n}", "summary": [], "encrypted_content": f"enc-{n}"}
    ]
    content: list[dict[str, Any]] = []
    if turn.stop == "refusal":
        content.append({"type": "refusal", "refusal": "I can't help with that."})
    elif turn.text:
        content.append({"type": "output_text", "text": turn.text, "annotations": []})
    if content:
        output.append(
            {
                "type": "message",
                "id": f"msg_{n}",
                "role": "assistant",
                "status": "completed",
                "content": content,
            }
        )
    for call in turn.tool_calls:
        output.append(
            {
                "type": "function_call",
                "id": f"fc_{call.call_id}",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": _arguments(call.input),
                "status": "completed",
            }
        )
    status, details = "completed", None
    if turn.stop == "max_tokens":
        status, details = "incomplete", {"reason": "max_output_tokens"}
    elif turn.stop == "other":
        status = "cancelled"
    usage = turn.usage
    return {
        "id": f"resp_{n}",
        "object": "response",
        "created_at": 1_790_000_000 + n,
        "model": body.get("model"),
        "status": status,
        "incomplete_details": details,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": body.get("tools", []),
        "usage": {
            "input_tokens": usage.prompt_tokens,
            "input_tokens_details": {
                "cached_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
            },
            "output_tokens": usage.output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": usage.prompt_tokens + usage.output_tokens,
        },
    }


class ScriptedResponses(Replay):
    """A scripted Responses API over ``fake`` (see the module docstring)."""

    def __init__(self, fake: FakeProvider, *, planted: Sequence[str] = ()) -> None:
        super().__init__("openai", [], planted=planted)
        self.fake = fake
        self._conversation: FakeConversation | None = None
        self._last_input: list[Any] = []
        self._last_output: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        n = len(self.requests)
        body = self.checked(request, n, "POST", PATH)
        if not isinstance(body, dict):
            raise self._problem(f"request {n}: no body")
        for problem in check_request(body):  # pyright: ignore[reportUnknownArgumentType]
            raise self._problem(f"request {n}: {problem}")
        sent: list[Any] = body.get("input") or []  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        results, reminder = self._new_items(n, body, sent)  # pyright: ignore[reportUnknownArgumentType]
        assert self._conversation is not None
        self._conversation.record.turns.append((results, reminder))
        try:
            turn = self.fake.next_turn(results)
        except ProviderError as exc:
            status, headers = _ERRORS[exc.category]
            error = {"error": {"message": "scripted", "type": exc.category, "code": None}}
            self._last_output = []
            return httpx.Response(status, json=error, headers=headers)
        except ScriptMismatch as exc:
            raise self._problem(f"request {n}: {exc}") from None
        answer = render(turn, body, n)  # pyright: ignore[reportUnknownArgumentType]
        self._last_input, self._last_output = sent, answer["output"]
        return httpx.Response(200, json=answer, headers={"x-request-id": f"req_{n}"})

    def _new_items(
        self, n: int, body: dict[str, Any], sent: list[Any]
    ) -> tuple[tuple[ToolResult, ...], str | None]:
        if len(sent) == 1 and _user_text(sent[0]) is not None:
            self._start(body, _user_text(sent[0]) or "")
            return (), None
        prefix = [*self._last_input, *self._last_output]
        if self._conversation is None or sent[: len(prefix)] != prefix:
            raise self._problem(f"request {n}: the history is not the last one, appended to")
        new = sent[len(prefix) :]
        reminder = _user_text(new[-1]) if new else None
        if reminder is not None:
            new = new[:-1]
        calls = [i["call_id"] for i in self._last_output if i.get("type") == "function_call"]
        if [i.get("call_id") for i in new] != calls or any(
            i.get("type") != "function_call_output" or set(i) != {"type", "call_id", "output"}
            for i in new
        ):
            raise self._problem(f"request {n}: not one output per call, in order")
        results = tuple(ToolResult(i["call_id"], i["output"], _is_error(i["output"])) for i in new)
        return results, reminder

    def _start(self, body: dict[str, Any], first: str) -> None:
        tools = tuple(
            ToolDef(t["name"], t["description"], t["parameters"]) for t in body.get("tools", [])
        )
        spec = StageSpec(
            stage=str(body.get("prompt_cache_key", "")).rsplit("-", 1)[0],
            system=body.get("instructions", ""),
            tools=tools,
            first_message=first,
            model=body.get("model", ""),
            effort=(body.get("reasoning") or {}).get("effort", ""),
            max_tokens=body.get("max_output_tokens", 0),
        )
        self._conversation = self.fake.start(spec)
        self._last_input, self._last_output = [], []


def scripted_openai(
    fake: FakeProvider, *, planted: Sequence[str] = ()
) -> tuple[OpenAIProvider, ScriptedResponses]:
    """An ``OpenAIProvider`` (test key) whose requests ``fake`` answers."""
    api = ScriptedResponses(fake, planted=planted)
    return OpenAIProvider(SecretStr(TEST_KEY), transport=api.transport()), api


# A priced, catalogued OpenAI model for the loop's config (``owner_pending``: synthetic data only).
OPENAI_MODEL: Final = "gpt-6.1-sol"

__all__ = [
    "OPENAI_MODEL",
    "PATH",
    "ReplayMismatch",
    "ScriptedResponses",
    "check_request",
    "render",
    "scripted_openai",
]

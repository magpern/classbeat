"""The tool dispatcher (ADR 0006 sections 3, 4 and 7).

``dispatch_async(stage, call)`` (the loop's; ``dispatch`` for handlers that answer at once) turns
one model tool call into one result, never an exception (except what a lent read raises, below):

1. a tool outside the stage's allowlist: ``unknown_tool``;
2. an input over 32 KB (as compact JSON): ``too_large``;
3. an input the tool's strict schema refuses: ``invalid_input``, with the schema's messages
   (location and message only, never the input values);
4. the handler (awaited when it returns an awaitable: a lent read, ``classbeat.agent.stage``):
   its answer, or its ``ToolError`` (for example ``out_of_range`` for a track index
   outside the list, or ``invalid_draft`` when a draft has errors to fix);
5. a result over 32 KB: ``result_too_large``; an error result over 32 KB loses its messages and
   keeps its code.

Results are JSON of a fixed shape: ``{"ok": true, ...payload}`` or ``{"ok": false, "error":
<code>, "messages": [...]}``. The tool name in a result or an event is the stage's own name, or
``unknown`` for anything else, so no model-chosen text travels on.
"""

import inspect
import json
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from classbeat.agent.provider import ToolCall, ToolResult
from classbeat.agent.stage import Stage, Tool, ToolAnswer, ToolError
from classbeat.jobs.events import TOOL_ERROR_CODES

MAX_INPUT_BYTES: Final = 32 * 1024
MAX_RESULT_BYTES: Final = 32 * 1024
MAX_MESSAGES: Final = 20
UNKNOWN: Final = "unknown"
# Every code a result or a ``tool_called`` event may carry: one vocabulary, defined in
# ``classbeat.jobs.events`` (a code the events refuse would end the job as ``internal``).
ERROR_CODES: Final = frozenset(TOOL_ERROR_CODES)


@dataclass(frozen=True, slots=True)
class Dispatched:
    result: ToolResult
    tool: str  # the stage's tool name, or "unknown"
    error: str | None  # an ERROR_CODES value, None on success
    final: bool  # the stage's final tool was called (successfully or not)
    output: Any = None  # the stage's result when the final tool succeeded

    @property
    def ok(self) -> bool:
        return self.error is None


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _error(call: ToolCall, tool: str, code: str, messages: Any = (), *, final: bool = False):
    body = {"ok": False, "error": code, "messages": [str(m) for m in messages][:MAX_MESSAGES]}
    content = compact(body)
    if len(content.encode()) > MAX_RESULT_BYTES:  # the same cap as a success; the code stays
        content = compact({**body, "messages": []})
    return Dispatched(ToolResult(call.call_id, content, True), tool, code, final)


def _schema_messages(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        where = ".".join(str(p) for p in err["loc"]) or "input"
        out.append(f"{where}: {err['msg']}")
    return out


@dataclass(frozen=True, slots=True)
class _Ready:
    """A call that passed steps 1 to 3: its tool, whether it is the final one, its input."""

    tool: Tool
    final: bool
    parsed: Any


def _prepare(stage: Stage, call: ToolCall) -> Dispatched | _Ready:
    tool = stage.tool(call.name) if isinstance(call.name, str) else None
    if tool is None:
        return _error(call, UNKNOWN, "unknown_tool", [f"the tools are: {_names(stage)}"])
    final = tool.name == stage.final_tool
    try:
        raw = compact(call.input)
    except (TypeError, ValueError):
        return _error(
            call, tool.name, "invalid_input", ["input must be a JSON object"], final=final
        )
    if len(raw.encode()) > MAX_INPUT_BYTES:
        return _error(
            call, tool.name, "too_large", [f"input over {MAX_INPUT_BYTES} bytes"], final=final
        )
    if not isinstance(call.input, dict):
        return _error(
            call, tool.name, "invalid_input", ["input must be a JSON object"], final=final
        )
    try:
        parsed = tool.input_model.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        return _error(call, tool.name, "invalid_input", _schema_messages(exc), final=final)
    return _Ready(tool, final, parsed)


def _refused(call: ToolCall, ready: _Ready, exc: ToolError) -> Dispatched:
    code = exc.code if exc.code in ERROR_CODES else "internal"
    return _error(call, ready.tool.name, code, exc.messages, final=ready.final)


def _answered(call: ToolCall, ready: _Ready, answer: ToolAnswer) -> Dispatched:
    body = compact({**answer.payload, "ok": True})
    if len(body.encode()) > MAX_RESULT_BYTES:
        return _error(call, ready.tool.name, "result_too_large", [], final=ready.final)
    return Dispatched(
        ToolResult(call.call_id, body, False),
        ready.tool.name,
        None,
        ready.final,
        answer.output if ready.final else None,
    )


def dispatch(stage: Stage, call: ToolCall) -> Dispatched:
    """For stages whose handlers all answer at once (every handler but a lent read). A handler
    that returns an awaitable here is a programming error: it is closed unrun and the call is
    ``internal``; ``dispatch_async`` is the loop's."""
    ready = _prepare(stage, call)
    if isinstance(ready, Dispatched):
        return ready
    try:
        answer = ready.tool.handler(ready.parsed)
    except ToolError as exc:
        return _refused(call, ready, exc)
    if not isinstance(answer, ToolAnswer):
        close = getattr(answer, "close", None)
        if callable(close):
            close()
        return _error(call, ready.tool.name, "internal", [], final=ready.final)
    return _answered(call, ready, answer)


async def dispatch_async(stage: Stage, call: ToolCall) -> Dispatched:
    """``dispatch`` for the loop: a handler may also return an awaitable of its answer (a lent
    read, ``classbeat.agent.stage``); its ``ToolError`` is a result like any other. Anything
    else it raises (a job failure of the backend function it was lent) propagates."""
    ready = _prepare(stage, call)
    if isinstance(ready, Dispatched):
        return ready
    try:
        answer = ready.tool.handler(ready.parsed)
        if inspect.isawaitable(answer):
            answer = await answer
    except ToolError as exc:
        return _refused(call, ready, exc)
    if not isinstance(answer, ToolAnswer):
        return _error(call, ready.tool.name, "internal", [], final=ready.final)
    return _answered(call, ready, answer)


def _names(stage: Stage) -> str:
    return ", ".join(t.name for t in stage.tools)

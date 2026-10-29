"""The tool dispatcher (ADR 0006 sections 3, 4 and 7).

``dispatch(stage, call)`` turns one model tool call into one result, never an exception:

1. a tool outside the stage's allowlist: ``unknown_tool``;
2. an input over 32 KB (as compact JSON): ``too_large``;
3. an input the tool's strict schema refuses: ``invalid_input``, with the schema's messages
   (location and message only, never the input values);
4. the handler: its answer, or its ``ToolError`` (for example ``out_of_range`` for a track index
   outside the list, or ``invalid_draft`` when a draft has errors to fix);
5. a result over 32 KB: ``result_too_large``.

Results are JSON of a fixed shape: ``{"ok": true, ...payload}`` or ``{"ok": false, "error":
<code>, "messages": [...]}``. The tool name in a result or an event is the stage's own name, or
``unknown`` for anything else, so no model-chosen text travels on.
"""

import json
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from classbeat.agent.provider import ToolCall, ToolResult
from classbeat.agent.stage import Stage, ToolError

MAX_INPUT_BYTES: Final = 32 * 1024
MAX_RESULT_BYTES: Final = 32 * 1024
MAX_MESSAGES: Final = 20
UNKNOWN: Final = "unknown"
# Every code a result or a ``tool_called`` event may carry.
ERROR_CODES: Final = frozenset(
    {
        "unknown_tool",
        "too_large",
        "invalid_input",
        "out_of_range",
        "invalid_draft",
        "result_too_large",
        "internal",
    }
)


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
    return Dispatched(ToolResult(call.call_id, compact(body), True), tool, code, final)


def _schema_messages(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        where = ".".join(str(p) for p in err["loc"]) or "input"
        out.append(f"{where}: {err['msg']}")
    return out


def dispatch(stage: Stage, call: ToolCall) -> Dispatched:
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
    try:
        answer = tool.handler(parsed)
    except ToolError as exc:
        code = exc.code if exc.code in ERROR_CODES else "internal"
        return _error(call, tool.name, code, exc.messages, final=final)
    body = compact({**answer.payload, "ok": True})
    if len(body.encode()) > MAX_RESULT_BYTES:
        return _error(call, tool.name, "result_too_large", [], final=final)
    return Dispatched(
        ToolResult(call.call_id, body, False),
        tool.name,
        None,
        final,
        answer.output if final else None,
    )


def _names(stage: Stage) -> str:
    return ", ".join(t.name for t in stage.tools)

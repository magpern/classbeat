"""``FakeProvider`` (ADR 0006 section 8): replays a script of turns, never contacts anything.

A script is a JSON file (``backend/tests/agent_scripts/*.json``)::

    {"model": "fake-model",                      # what Turn.model reports (default "fake")
     "drafts": {"good": {...a WorkoutDraft...}},  # named drafts for the "$draft" template
     "turns": [
       {"calls": [{"name": "get_music", "input": {},
                   "expect": {"ok": true}, "contains": ["\\"tracks\\""]}],
        "usage": {"input": 1200, "output": 300, "cache_read": 0, "cache_write": 0}},
       {"calls": [{"name": "submit_draft", "input": {"draft": {"$draft": "good"}}}]},
       {"end_turn": "some visible text"},
       {"refusal": true}, {"max_tokens": true}, {"other": true},
       {"error": "overloaded"}                    # or timeout, rate_limited, auth, bad_request
     ]}

Each turn is exactly one of ``calls``, ``end_turn``, ``refusal``, ``max_tokens``, ``other`` or
``error``, with fixed ``usage`` (defaults below). Inputs may use two small templates, resolved
anywhere in the input:

- ``{"$draft": "<name>"}``: a copy of the script's named draft;
- ``{"$last": "<tool>", "set": {"draft.segments.3.intervals.0.sec": 240}}``: a copy of the input
  last sent to that tool, with the dotted paths set (numbers index lists): "the draft from the
  last error, with ``sec`` fixed";
- ``{"$repeat": "x", "times": 40000}``: a long string (an oversized input).

``expect`` (a subset of the result's JSON) and ``contains`` (substrings of its text) are checked
against the results the loop sends with the next turn; a mismatch, or a turn the script doesn't
have, raises ``ScriptMismatch``, so a script is also a test of the tools. The script's cursor is
the provider's, shared by every conversation it starts: an ``error`` turn ends a run, and the
next attempt's run continues with the turn after it ("an overload, then success").

``sent`` keeps everything the loop gave the provider (each ``StageSpec`` and every turn's
results and reminder), so a test can check exactly what an adapter would send.
"""

import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.provider import (
    ProviderError,
    StageSpec,
    ToolCall,
    ToolResult,
    Turn,
    Usage,
)

ERRORS: Final = {
    "overloaded": "model_unavailable",
    "timeout": "model_unavailable",
    "rate_limited": "model_unavailable",
    "connection": "model_unavailable",
    "auth": "model_config",
    "not_found": "model_config",
    "bad_request": "internal",
}
_ONE_OF: Final = ("calls", "end_turn", "refusal", "max_tokens", "other", "error")


class ScriptMismatch(Exception):
    """The run did something the script didn't expect."""


class FakeUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: int = Field(default=1000, ge=0)
    output: int = Field(default=200, ge=0)
    cache_read: int = Field(default=0, ge=0)
    cache_write: int = Field(default=0, ge=0)

    def usage(self) -> Usage:
        return Usage(self.input, self.output, self.cache_read, self.cache_write)


class FakeCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    input: Any = Field(default_factory=lambda: dict[str, Any]())
    expect: dict[str, Any] | None = None
    contains: list[str] = Field(default_factory=lambda: list[str]())


class FakeTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[FakeCall] | None = None
    end_turn: str | None = None
    refusal: bool | None = None
    max_tokens: bool | None = None
    other: bool | None = None
    error: (
        Literal[
            "overloaded",
            "timeout",
            "rate_limited",
            "connection",
            "auth",
            "not_found",
            "bad_request",
        ]
        | None
    ) = None
    usage: FakeUsage = Field(default_factory=FakeUsage)
    text: str = ""

    @model_validator(mode="after")
    def _exactly_one(self) -> "FakeTurn":
        if sum(getattr(self, k) is not None for k in _ONE_OF) != 1:
            raise ValueError(f"a turn is exactly one of {', '.join(_ONE_OF)}")
        return self


class FakeScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "fake"
    drafts: dict[str, Any] = Field(default_factory=lambda: dict[str, Any]())
    turns: list[FakeTurn] = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> "FakeScript":
        return cls.model_validate_json(path.read_text("utf-8"))


@dataclass
class Sent:
    spec: StageSpec
    turns: list[tuple[tuple[ToolResult, ...], str | None]] = field(
        default_factory=lambda: list[tuple[tuple[ToolResult, ...], str | None]]()
    )


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _subset(v, actual[k]) for k, v in expected.items()
        )
    return expected == actual


def _set_path(target: Any, path: str, value: Any) -> None:
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]
    last = keys[-1]
    if isinstance(target, list):
        target[int(last)] = value
    else:
        target[last] = value


class FakeProvider:
    name = "fake"

    def __init__(self, script: FakeScript) -> None:
        self.script = script
        self.cursor = 0
        self.sent: list[Sent] = []
        self._last_inputs: dict[str, Any] = {}
        self._pending: list[FakeCall] = []

    @classmethod
    def from_file(cls, path: Path) -> "FakeProvider":
        return cls(FakeScript.load(path))

    @property
    def remaining(self) -> int:
        return len(self.script.turns) - self.cursor

    def start(self, spec: StageSpec) -> "FakeConversation":
        self.sent.append(Sent(spec))
        self._pending = []  # a new run: the old run's last results are never sent
        return FakeConversation(self, self.sent[-1])

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, dict):
            if set(value) == {"$draft"}:
                name = value["$draft"]
                if name not in self.script.drafts:
                    raise ScriptMismatch(f"no draft named {name!r} in the script")
                return copy.deepcopy(self.script.drafts[name])
            if set(value) == {"$repeat", "times"}:
                return str(value["$repeat"]) * int(value["times"])
            if "$last" in value:
                tool = value["$last"]
                if tool not in self._last_inputs:
                    raise ScriptMismatch(f"no earlier input to {tool!r}")
                out = copy.deepcopy(self._last_inputs[tool])
                for path, new in (value.get("set") or {}).items():
                    _set_path(out, path, self._resolve(new))
                return out
            return {k: self._resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def _check(self, results: Sequence[ToolResult]) -> None:
        pending, self._pending = self._pending, []
        if len(results) != len(pending):
            raise ScriptMismatch(
                f"turn {self.cursor}: {len(results)} results for {len(pending)} calls"
            )
        for n, (call, result) in enumerate(zip(pending, results, strict=True)):
            if call.expect is not None and not _subset(call.expect, json.loads(result.content)):
                raise ScriptMismatch(f"turn {self.cursor}, call {n} ({call.name}): {call.expect}")
            for text in call.contains:
                if text not in result.content:
                    raise ScriptMismatch(
                        f"turn {self.cursor}, call {n} ({call.name}): no {text!r} in the result"
                    )

    def next_turn(self, results: Sequence[ToolResult]) -> Turn:
        self._check(results)
        if self.cursor >= len(self.script.turns):
            raise ScriptMismatch("the run asked for a turn the script doesn't have")
        turn = self.script.turns[self.cursor]
        self.cursor += 1
        usage = turn.usage.usage()
        model = self.script.model
        if turn.error is not None:
            raise ProviderError(ERRORS[turn.error])
        if turn.calls is not None:
            calls = []
            for n, call in enumerate(turn.calls):
                data = self._resolve(call.input)
                self._last_inputs[call.name] = copy.deepcopy(data)
                calls.append(ToolCall(f"call_{self.cursor}_{n}", call.name, data))
            self._pending = list(turn.calls)
            return Turn("tool_use", turn.text, tuple(calls), usage, model)
        if turn.end_turn is not None:
            return Turn("end_turn", turn.end_turn, (), usage, model)
        stop = "refusal" if turn.refusal else "max_tokens" if turn.max_tokens else "other"
        return Turn(stop, turn.text, (), usage, model)


class FakeConversation:
    def __init__(self, provider: FakeProvider, sent: Sent) -> None:
        self.provider = provider
        self.record = sent

    async def turn(self, results: Sequence[ToolResult], *, reminder: str | None = None) -> Turn:
        self.record.turns.append((tuple(results), reminder))
        return self.provider.next_turn(results)

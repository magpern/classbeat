"""Stages and their tools (ADR 0006 section 3).

A ``Stage`` is a system prompt, a tool allowlist and one final tool. Tools are of two kinds only:
``read`` (data the backend loaded for this job) and ``compute`` (pure functions). There is no
write kind: a stage cannot have a write tool, and ``Stage`` refuses one.

A tool's handler takes its validated input (a strict Pydantic model) and returns a ``ToolAnswer``
(a JSON-ready payload and, for the final tool, the stage's output) or raises ``ToolError`` with an
error code and messages for the model to correct. Handlers are pure: no network, no credentials,
no database. The one exception is a ``read`` tool that reads through a backend function it was
lent (``pick_pool``'s ``search_tracks``, ADR 0008 section 3): its handler may return an awaitable
of the ``ToolAnswer`` (the job's Spotify search, which runs through ``spotify_call`` on the event
loop); the stage itself still holds no client and no credentials.

``prompt_version`` is the SHA-256 of the stage's system prompt and tool definitions (names,
descriptions, JSON schemas) as canonical JSON, so any change to what the model is told changes it.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Literal

from pydantic import BaseModel

from classbeat.agent.provider import ToolDef

ToolKind = Literal["read", "compute"]
TOOL_KINDS = frozenset({"read", "compute"})


class ToolError(Exception):
    """A tool refused its input: ``code`` (an enum value) and messages for the model."""

    def __init__(self, code: str, messages: Sequence[str] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.messages = tuple(messages)


@dataclass(frozen=True, slots=True)
class ToolAnswer:
    payload: Mapping[str, Any]
    output: Any = None  # the stage's result, set only by the final tool on success


@dataclass(frozen=True)
class Tool:
    name: str
    kind: ToolKind
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any], ToolAnswer | Awaitable[ToolAnswer]]

    def __post_init__(self) -> None:
        if self.kind not in TOOL_KINDS:
            raise ValueError("a tool is read or compute; no stage has a write tool")
        config = self.input_model.model_config
        if config.get("extra") != "forbid" or config.get("strict") is not True:
            raise ValueError("a tool's input model is strict and forbids extra fields")

    def definition(self) -> ToolDef:
        return ToolDef(self.name, self.description, self.input_model.model_json_schema())


@dataclass(frozen=True)
class Stage:
    name: str
    system: str
    tools: tuple[Tool, ...]
    final_tool: str

    def __post_init__(self) -> None:
        names = [t.name for t in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("tool names are unique in a stage")
        if self.final_tool not in names:
            raise ValueError("the final tool is one of the stage's tools")

    def tool(self, name: str) -> Tool | None:
        return next((t for t in self.tools if t.name == name), None)

    def definitions(self) -> tuple[ToolDef, ...]:
        return tuple(t.definition() for t in self.tools)

    @cached_property
    def prompt_version(self) -> str:
        return prompt_version(self.system, self.definitions())


def prompt_version(system: str, tools: Sequence[ToolDef]) -> str:
    canonical = json.dumps(
        {
            "system": system,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()

"""#1197: the strict-schema transform drops the JSON-Schema *keywords* ``title`` and ``default``,
never a property (or ``$defs`` entry) of that name, and treats no other name as a keyword.

Both OpenAI-SDK adapters are covered: ``OpenAIProvider`` sends every tool through
``strict_schema``; ``DeepSeekProvider`` sends the stage's own schema unchanged (non-strict), so
its test checks the names pass through as they are."""

import copy
import json
from typing import Any

import pytest
from pydantic import BaseModel, Field

from classbeat.agent.find_songs import FindSongsInput
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.provider import StageSpec, ToolDef
from classbeat.agent.providers.deepseek import request_tools
from classbeat.agent.providers.openai_responses import request_shape
from classbeat.agent.providers.openai_strict import strict_schema, tool_input
from classbeat.agent.schemas import (
    DraftInput,
    GetPoolInput,
    MusicInput,
    ReviseInput,
    RunningDraftInput,
    RunningReviseInput,
    SearchInput,
    SongList,
    SongPool,
    SongsRequestedInput,
)
from classbeat.agent.soundless_schemas import SoundlessDraftInput, SoundlessReviseInput
from classbeat.agent.studio_schemas import MixInput, StudioDraftInput, StudioReviseInput
from deepseekfake import DEEPSEEK_MODEL
from openaifake import OPENAI_MODEL
from stagetools import all_stages


# A tool input whose properties are named like JSON-Schema keywords (no docstring: it would be
# the schema's description).
class Named(BaseModel):
    title: str = Field(min_length=1)
    default: int | None = None


# What strict mode is sent for ``Named``: both properties kept and listed, the keywords gone.
NAMED_STRICT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Text of at least 1 characters."},
        "default": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
    "required": ["title", "default"],
    "additionalProperties": False,
}


def test_a_property_named_title_or_default_is_kept_and_the_keywords_are_dropped() -> None:
    schema = Named.model_json_schema()
    assert schema["title"] == "Named"  # the generator's own keywords, on every node
    assert schema["properties"]["title"]["title"] == "Title"
    assert schema["properties"]["default"]["default"] is None
    assert strict_schema(schema) == NAMED_STRICT


def test_defs_named_title_or_default_are_kept() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "title": "Root",
        "properties": {
            "a": {"$ref": "#/$defs/title"},
            "b": {"$ref": "#/$defs/default"},
        },
        "required": ["a", "b"],
        "$defs": {
            "title": {"type": "string", "title": "Title", "default": "x"},
            "default": {
                "type": "object",
                "title": "Default",
                "properties": {"title": {"type": "string", "title": "Title"}},
                "required": ["title"],
            },
        },
    }
    assert strict_schema(schema)["$defs"] == {
        "title": {"type": "string"},
        "default": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        },
    }


@pytest.mark.parametrize("name", ["const", "minLength", "maxLength", "description", "type"])
def test_no_property_name_is_read_as_a_keyword(name: str) -> None:
    schema = {
        "type": "object",
        "properties": {name: {"type": "string"}, "other": {"type": "integer"}},
        "required": [name, "other"],
    }
    assert strict_schema(schema) == {
        "type": "object",
        "properties": {name: {"type": "string"}, "other": {"type": "integer"}},
        "required": [name, "other"],
        "additionalProperties": False,
    }


def test_a_null_for_an_optional_title_is_dropped_and_a_required_default_kept() -> None:
    class Pin(BaseModel):
        default: str
        title: str | None = None

    schema = Pin.model_json_schema()
    assert tool_input(schema, json.dumps({"default": "x", "title": None})) == {"default": "x"}
    assert tool_input(schema, json.dumps({"default": "x", "title": "y"})) == {
        "default": "x",
        "title": "y",
    }


# --- no other schema changes -----------------------------------------------------------------

# Every tool input model of every stage (design, revise, pick_pool, choose_songs, interpret, and
# the running, soundless and studio stages, which the guard stages do not build).
TOOL_INPUTS: tuple[type[BaseModel], ...] = (
    DraftInput,
    ReviseInput,
    RunningDraftInput,
    RunningReviseInput,
    SoundlessDraftInput,
    SoundlessReviseInput,
    SongsRequestedInput,
    MusicInput,
    SearchInput,
    FindSongsInput,
    SongPool,
    SongList,
    GetPoolInput,
    MixInput,
    StudioDraftInput,
    StudioReviseInput,
    InterpretedRequest,
)


def _legacy(node: Any) -> Any:
    """The transform before #1197, verbatim but for its helpers inlined: a reference for the
    schemas that have no property or definition named like a dropped keyword."""

    def nullable(n: Any) -> bool:
        return n.get("type") == "null" or any(
            nullable(b) for b in n.get("anyOf", ()) if isinstance(b, dict)
        )

    def with_null(n: dict[str, Any]) -> dict[str, Any]:
        if nullable(n):
            return n
        description = n.pop("description", None)
        out: dict[str, Any] = {"anyOf": [n, {"type": "null"}]}
        if description is not None:
            out["description"] = description
        return out

    if isinstance(node, list):
        return [_legacy(item) for item in node]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {
        k: _legacy(v)
        for k, v in node.items()  # pyright: ignore[reportUnknownVariableType]
        if k not in {"default", "title"}
    }
    if "const" in out and "enum" not in out:
        out["enum"] = [out.pop("const")]
    lengths = (("minLength", "at least {} characters"), ("maxLength", "at most {} characters"))
    notes = [text.format(out.pop(key)) for key, text in lengths if key in out]
    if notes:
        said = out.get("description")
        rule = "Text of " + " and ".join(notes) + "."
        out["description"] = f"{said} {rule}" if isinstance(said, str) and said else rule
    if out.get("type") == "object":
        properties: dict[str, Any] = out.get("properties", {})
        required = set(node.get("required", ()))  # pyright: ignore[reportUnknownArgumentType]
        out["properties"] = {
            name: value if name in required else with_null(value)
            for name, value in properties.items()
        }
        out["required"] = list(properties)
        out["additionalProperties"] = False
    return out


def test_the_list_holds_every_tool_input_of_the_guard_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    used = {tool.input_model for stage in all_stages(monkeypatch) for tool in stage.tools}
    assert used <= set(TOOL_INPUTS)


@pytest.mark.parametrize("model", TOOL_INPUTS, ids=lambda m: m.__name__)
def test_every_existing_tool_schema_is_sent_as_before(model: type[BaseModel]) -> None:
    schema = model.model_json_schema()
    assert json.dumps(strict_schema(schema)) == json.dumps(_legacy(copy.deepcopy(schema)))


# --- both adapters ---------------------------------------------------------------------------


def _spec(model: str) -> StageSpec:
    return StageSpec(
        stage="interpret",
        system="You read requests.",
        tools=(ToolDef("submit_pin", "A pinned song.", Named.model_json_schema()),),
        first_message="A request.",
        model=model,
        effort="high",
        max_tokens=32000,
    )


def test_openai_sends_a_property_named_title_or_default_in_its_strict_tool() -> None:
    (tool,) = request_shape(_spec(OPENAI_MODEL))["tools"]
    assert tool["strict"] is True
    assert tool["parameters"] == NAMED_STRICT


def test_deepseek_sends_a_property_named_title_or_default_unchanged() -> None:
    (tool,) = request_tools(_spec(DEEPSEEK_MODEL))
    assert tool["function"]["parameters"] == Named.model_json_schema()
    assert set(tool["function"]["parameters"]["properties"]) == {"title", "default"}

"""The strict-schema transform of the OpenAI-SDK layer (ADR 0012 §3): every tool of every stage
(design, revise, pick_pool, choose_songs) goes through it and back, and the loop's own validation
of the input is unchanged."""

import copy
import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from agentfake import analysed, brief, good_draft
from classbeat.agent import DesignContext, design_stage
from classbeat.agent.choose_songs import choose_stage
from classbeat.agent.pick_pool import pool_stage
from classbeat.agent.providers.openai_common import strict_schema, strip_added_nulls, tool_input
from classbeat.agent.revise import ReviseContext, revise_stage
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import Stage, Tool
from pickfake import POOL_14, entries, songs, world

# Valid inputs per tool input model. A new tool needs its own here (the test says which).
SAMPLES: dict[str, list[dict[str, Any]]] = {
    "MusicInput": [{}, {"track": 0}],
    "DraftInput": [{"draft": good_draft()}],
    "ReviseInput": [{"draft": good_draft()}, {"draft": good_draft(), "note": "Easier start."}],
    "SearchInput": [{"query": "rock"}, {"query": "rock", "year_from": 1990, "year_to": 2000}],
    "SongPool": [entries((0, "warmup"), (1, "build"))],
    "GetPoolInput": [{}],
    "SongList": [
        songs(("pool", 0, "warmup"), ("pool", 1, "cooldown")),
        songs(("pool", 0, "warmup"), note="A steady build."),
    ],
}


def _stages(monkeypatch: pytest.MonkeyPatch) -> list[Stage]:
    music = analysed(monkeypatch)
    revise = ReviseContext.build(
        brief(),
        music,
        WorkoutDraft.model_validate(good_draft()),
        ("Longer warm-up",),
        "Harder",
        music_changed=False,
    )
    w = world()
    return [
        design_stage(DesignContext.build(brief(), music)),
        revise_stage(revise),
        pool_stage(w.pool),
        choose_stage(w.choose_context(POOL_14)),
    ]


def _tools(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Stage, Tool]]:
    return [(stage, tool) for stage in _stages(monkeypatch) for tool in stage.tools]


def _objects(node: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node  # pyright: ignore[reportReturnType]
        for value in node.values():  # pyright: ignore[reportUnknownVariableType]
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:  # pyright: ignore[reportUnknownVariableType]
            yield from _objects(value)


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}  # pyright: ignore[reportUnknownVariableType]
    return set()


def _resolve(node: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = node.get("$ref")
    return root["$defs"][ref.removeprefix("#/$defs/")] if isinstance(ref, str) else node


def as_strict_mode_sends(value: Any, node: Mapping[str, Any], root: Mapping[str, Any]) -> Any:
    """What a model in strict mode sends for ``value``: every property present, ``null`` for each
    optional one the input left out (walked along the **original** schema)."""
    node = _resolve(node, root)
    if isinstance(value, dict) and "properties" in node:
        out = {
            k: as_strict_mode_sends(v, node["properties"][k], root)
            for k, v in value.items()  # pyright: ignore[reportUnknownVariableType]
        }
        for name in node["properties"]:
            out.setdefault(name, None)
        return out
    if isinstance(value, list) and "items" in node:
        return [as_strict_mode_sends(v, node["items"], root) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _matches_strict(value: Any, node: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
    """Every object of ``value`` holds exactly the strict schema's ``required`` keys."""
    node = _resolve(node, root)
    if isinstance(value, dict):
        branches = [node, *node.get("anyOf", ())]
        for branch in (_resolve(b, root) for b in branches):
            if "properties" in branch:
                return set(value) == set(branch["required"]) and all(  # pyright: ignore[reportUnknownArgumentType]
                    _matches_strict(v, branch["properties"][k], root)
                    for k, v in value.items()  # pyright: ignore[reportUnknownVariableType]
                )
        return False
    if isinstance(value, list):
        branches = [node, *node.get("anyOf", ())]
        for branch in (_resolve(b, root) for b in branches):
            if "items" in branch:
                return all(_matches_strict(v, branch["items"], root) for v in value)  # pyright: ignore[reportUnknownVariableType]
    return True


def test_every_tool_of_every_stage_has_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = _tools(monkeypatch)
    assert {stage.name for stage, _ in tools} == {"design", "revise", "pick_pool", "choose_songs"}
    missing = sorted({t.input_model.__name__ for _, t in tools} - set(SAMPLES))
    assert missing == []


def test_the_strict_form_lists_every_property_and_forbids_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for stage, tool in _tools(monkeypatch):
        strict = strict_schema(tool.input_model.model_json_schema())
        for obj in _objects(strict):
            assert obj["additionalProperties"] is False, (stage.name, tool.name)
            assert obj["required"] == list(obj.get("properties", {})), (stage.name, tool.name)
        assert not _keys(strict) & {"default", "title", "const", "minLength", "maxLength"}


def test_optional_properties_become_nullable_and_required_ones_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def nullable(node: Mapping[str, Any]) -> bool:
        return node.get("type") == "null" or any(nullable(b) for b in node.get("anyOf", ()))

    for _, tool in _tools(monkeypatch):
        original = tool.input_model.model_json_schema()
        strict = strict_schema(original)
        pairs = [(original, strict)] + [
            (original["$defs"][name], strict["$defs"][name]) for name in original.get("$defs", {})
        ]
        for before, after in pairs:
            required = set(before.get("required", ()))
            for name, prop in before.get("properties", {}).items():
                if name in required:
                    assert nullable(after["properties"][name]) == nullable(prop), (tool.name, name)
                else:
                    assert nullable(after["properties"][name]), (tool.name, name)


def test_every_tool_round_trips_to_what_the_loop_validates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = 0
    for _, tool in _tools(monkeypatch):
        original = tool.input_model.model_json_schema()
        strict = strict_schema(original)
        for sample in SAMPLES[tool.input_model.__name__]:
            sent = as_strict_mode_sends(sample, original, original)
            assert _matches_strict(sent, strict, strict), tool.name
            parsed = tool_input(original, json.dumps(sent))
            assert parsed == sample, tool.name
            # The loop's own validation (classbeat.agent.dispatcher), unchanged.
            loop = tool.input_model.model_validate_json(json.dumps(parsed), strict=True)
            assert loop == tool.input_model.model_validate_json(json.dumps(sample), strict=True)
            checked += 1
    assert checked >= len(SAMPLES)


def test_the_transform_is_pure_and_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    for _, tool in _tools(monkeypatch):
        original = tool.input_model.model_json_schema()
        kept = copy.deepcopy(original)
        assert strict_schema(original) == strict_schema(original)
        assert original == kept


def test_a_length_rule_moves_into_the_description() -> None:
    schema = {
        "type": "object",
        "properties": {
            "q": {"type": "string", "minLength": 1, "maxLength": 100, "description": "Words."}
        },
        "required": ["q"],
    }
    assert strict_schema(schema)["properties"]["q"] == {
        "type": "string",
        "description": "Words. Text of at least 1 characters and at most 100 characters.",
    }


def _draft_input(monkeypatch: pytest.MonkeyPatch) -> Tool:
    [tool] = [t for _, t in _tools(monkeypatch) if t.name == "preview_draft"][:1]
    return tool


@pytest.mark.parametrize(
    "bad",
    [
        {"draft": None},  # a required property's null stays
        {"draft": good_draft(), "extra": None},  # an unknown key stays
        {"draft": {**good_draft(), "name": None}},  # a nested required null stays
        [],
        "draft",
    ],
)
def test_stripping_never_makes_a_refused_input_acceptable(
    monkeypatch: pytest.MonkeyPatch, bad: Any
) -> None:
    tool = _draft_input(monkeypatch)
    original = tool.input_model.model_json_schema()
    stripped = strip_added_nulls(original, bad)
    with pytest.raises(ValidationError):
        tool.input_model.model_validate_json(json.dumps(bad), strict=True)
    with pytest.raises(ValidationError):
        tool.input_model.model_validate_json(json.dumps(stripped), strict=True)


def test_only_the_optional_nulls_go(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _draft_input(monkeypatch)
    original = tool.input_model.model_json_schema()
    draft = good_draft()
    interval = draft["segments"][0]["intervals"][0]
    sent = {"draft": {**draft, "description": None}}
    sent["draft"]["segments"][0]["intervals"][0] = {**interval, "game": None, "cue": None}
    out = strip_added_nulls(original, sent)
    assert "description" not in out["draft"]
    first = out["draft"]["segments"][0]["intervals"][0]
    assert "game" not in first
    assert "cue" not in first
    assert first["sec"] == interval["sec"]
    assert sent["draft"]["description"] is None  # the input itself is not changed


def test_arguments_that_are_not_json_go_to_the_loop_as_they_are() -> None:
    assert tool_input({"type": "object"}, "{not json") == "{not json"
    assert tool_input({"type": "object"}, "[1, 2]") == [1, 2]


def test_the_transform_closes_and_requires_every_object_itself() -> None:
    """Not left to the input models' own ``extra="forbid"``: a schema without it is closed too."""
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "inner": {"type": "object", "properties": {"b": {"type": "integer"}}},
        },
        "required": ["a"],
    }
    strict = strict_schema(schema)
    assert strict["additionalProperties"] is False
    assert strict["required"] == ["a", "inner"]
    inner = strict["properties"]["inner"]["anyOf"][0]
    assert (inner["additionalProperties"], inner["required"]) == (False, ["b"])
    assert strict["properties"]["inner"]["anyOf"][1] == {"type": "null"}

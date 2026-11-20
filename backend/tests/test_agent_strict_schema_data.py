"""The strict-schema transform keeps ``enum`` and ``const`` values as data and treats the names
under ``patternProperties`` and ``dependentSchemas`` as names (the #1196 and #1197 review
leftovers of #1206), as it already does for ``properties`` and ``$defs``."""

from typing import Any

import pytest

from classbeat.agent.providers.openai_strict import strict_schema


def test_enum_and_const_values_are_never_walked() -> None:
    value = {"title": "T", "default": 1, "anyOf": [{"type": "object"}, {"type": "object"}]}
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pick": {"enum": [value, "x"]},
            "fixed": {"const": value},
        },
        "required": ["pick", "fixed"],
    }
    out = strict_schema(schema)  # no ValueError: the object-looking value is no union of schemas
    assert out["properties"]["pick"] == {"enum": [value, "x"]}
    assert out["properties"]["fixed"] == {"enum": [value]}  # a const becomes a one-value enum


@pytest.mark.parametrize("keyword", ["patternProperties", "dependentSchemas"])
@pytest.mark.parametrize("name", ["title", "default", "const", "^x-"])
def test_names_under_a_schema_map_are_names_not_keywords(keyword: str, name: str) -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        keyword: {name: {"type": "object", "title": "T", "properties": {"b": {"type": "string"}}}},
    }
    out = strict_schema(schema)
    assert out[keyword] == {
        name: {
            "type": "object",
            "properties": {"b": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            "required": ["b"],
            "additionalProperties": False,
        }
    }


def test_a_union_of_objects_under_a_property_named_enum_is_still_refused() -> None:
    branch: dict[str, Any] = {"type": "object", "properties": {}}
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"enum": {"anyOf": [branch, branch]}},
        "required": ["enum"],
    }
    with pytest.raises(ValueError, match="union of two object types"):
        strict_schema(schema)

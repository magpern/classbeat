"""Strict tool schemas of the OpenAI-SDK layer (ADR 0012 §3), moved out of ``openai_common``
(#1197); a pure transform, it sends nothing and reads no file.

- **Tool arguments** (``openai_arguments.parse_arguments``): JSON text nested deeper than its
  ``MAX_ARGUMENT_DEPTH``, or not JSON, is passed on as the text it is, so the loop answers
  ``invalid_input`` (a tool error the model can correct), never a crash or ``internal``.
- **Strict tool schemas** (``strict_schema``, ``tool_input``): strict function mode needs every
  property in ``required`` and ``additionalProperties: false`` on every object. An optional
  property becomes a union with ``null``, and a ``null`` the model then sends for one is dropped
  from the parsed input (``strip_added_nulls``) before the loop validates it against the stage's
  own model, unchanged (ADR 0006 §4): the transform never loosens what the loop accepts.
"""

import copy
from collections.abc import Mapping
from typing import Any, Final

from classbeat.agent.providers.openai_arguments import parse_arguments

# --- strict tool schemas -------------------------------------------------------------------

# Keywords strict mode does not take; their rule stays with the loop's validation. Length limits
# are said in the description instead, so the model still sees them.
_DROPPED: Final = frozenset({"default", "title"})
# Keywords whose value maps names (a property's, a definition's) to schemas: the names are kept
# as they are, whatever they are (#1197: a property named ``title`` was dropped as the keyword).
_NAME_MAPS: Final = frozenset({"properties", "$defs", "definitions"})
_LENGTHS: Final = (("minLength", "at least {} characters"), ("maxLength", "at most {} characters"))


def _nullable(node: Mapping[str, Any]) -> bool:
    if node.get("type") == "null":
        return True
    return any(_nullable(branch) for branch in node.get("anyOf", ()) if isinstance(branch, dict))


def _with_null(node: dict[str, Any]) -> dict[str, Any]:
    if _nullable(node):
        return node
    description = node.pop("description", None)
    out: dict[str, Any] = {"anyOf": [node, {"type": "null"}]}
    if description is not None:
        out["description"] = description
    return out


def _may_be_object(branch: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
    node = _resolve(branch, root)
    # an unresolved ``$ref`` counts as an object: never assumed to be something else
    return "properties" in node or node.get("type") == "object" or "$ref" in node


def _object_branches(node: Mapping[str, Any], root: Mapping[str, Any]) -> int:
    branches = [b for b in node.get("anyOf", ()) if isinstance(b, dict)]
    return sum(1 for b in branches if _may_be_object(b, root))  # pyright: ignore[reportUnknownArgumentType]


def _check_unions(node: Any, root: Mapping[str, Any]) -> None:
    """A union of two object types is refused: dropping the added nulls follows the one object
    branch a union has, and could not tell two apart (the #262 review)."""
    if isinstance(node, dict):
        if _object_branches(node, root) > 1:  # pyright: ignore[reportUnknownArgumentType]
            raise ValueError("a tool schema may not have a union of two object types")
        for value in node.values():  # pyright: ignore[reportUnknownVariableType]
            _check_unions(value, root)
    elif isinstance(node, list):
        for value in node:  # pyright: ignore[reportUnknownVariableType]
            _check_unions(value, root)


def _named(names: Any) -> Any:
    """A name map's schemas made strict; its names are never read as keywords."""
    if not isinstance(names, dict):
        return _strict(names)
    return {name: _strict(schema) for name, schema in names.items()}  # pyright: ignore[reportUnknownVariableType]


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict(item) for item in node]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {
        key: _named(value) if key in _NAME_MAPS else _strict(value)
        for key, value in node.items()  # pyright: ignore[reportUnknownVariableType]
        if key not in _DROPPED
    }
    if "const" in out and "enum" not in out:
        out["enum"] = [out.pop("const")]
    notes = [text.format(out.pop(key)) for key, text in _LENGTHS if key in out]
    if notes:
        said = out.get("description")
        rule = "Text of " + " and ".join(notes) + "."
        out["description"] = f"{said} {rule}" if isinstance(said, str) and said else rule
    if out.get("type") == "object":
        properties: dict[str, Any] = out.get("properties", {})
        required = set(node.get("required", ()))  # pyright: ignore[reportUnknownArgumentType]
        out["properties"] = {
            name: value if name in required else _with_null(value)
            for name, value in properties.items()
        }
        out["required"] = list(properties)
        out["additionalProperties"] = False
    return out


def strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """A tool's input schema in the form strict function mode accepts (a pure transform).
    A union of two object types is refused (``ValueError``)."""
    _check_unions(schema, schema)
    return _strict(copy.deepcopy(dict(schema)))


def _resolve(node: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
        if isinstance(target, dict):
            return target  # pyright: ignore[reportUnknownVariableType]
    return node


def _strip(value: Any, node: Mapping[str, Any], root: Mapping[str, Any]) -> Any:
    node = _resolve(node, root)
    if isinstance(value, dict):
        if _object_branches(node, root) > 1:
            return value  # never guessed at; ``strict_schema`` refuses such a tool anyway
        branches = [node, *(b for b in node.get("anyOf", ()) if isinstance(b, dict))]
        for branch in (_resolve(b, root) for b in branches):
            properties = branch.get("properties")
            if isinstance(properties, dict):
                required = set(branch.get("required", ()))
                return {
                    key: _strip(item, properties.get(key, {}), root)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    for key, item in value.items()  # pyright: ignore[reportUnknownVariableType]
                    if not (item is None and key in properties and key not in required)
                }
        return value  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        branches = [node, *(b for b in node.get("anyOf", ()) if isinstance(b, dict))]
        for branch in (_resolve(b, root) for b in branches):
            items = branch.get("items")
            if isinstance(items, dict):
                return [_strip(item, items, root) for item in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        return value  # pyright: ignore[reportUnknownVariableType]
    return value


def strip_added_nulls(schema: Mapping[str, Any], value: Any) -> Any:
    """``value`` (parsed tool input) without the ``null`` of each property the **original**
    ``schema`` does not require: the nulls strict mode made the model send. Nothing else changes;
    an unknown key or a null of a required property stays, for the loop to refuse."""
    return _strip(value, schema, schema)


def tool_input(schema: Mapping[str, Any], arguments: str) -> Any:
    """A tool call's ``arguments`` (JSON text) as the loop's ``ToolCall.input``. Text that is not
    JSON, or nests too deep (``parse_arguments``), is passed on as it is, so the loop answers
    ``invalid_input``; it is never guessed at."""
    parsed, value = parse_arguments(arguments)
    return strip_added_nulls(schema, value) if parsed else value

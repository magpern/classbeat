"""The ``revision_ready`` guard's scanners (ADR 0038 section 4, #1186), used by
``test_revision_ready_guard.py``; functions over a registry, a route list and source text, so the
test can prove each one fails (as ``session_scan`` does for the fence guard).

- ``revision_kinds`` / ``revision_routes``: every job kind and API route that takes a revision id
  (an input field, a path or query parameter, or a field of a request model the endpoint reads,
  by hand or as its body, whose name ends in ``revision_id``);
- ``reaches``: whether a function reaches ``classbeat.design.revision_ready.revision_ready``,
  following the calls it makes to functions of the source (by name, ``module.function`` or a
  wrapper's closure), a few levels deep;
- ``origin_literals``: every revision origin the source spells (an ``origin=`` keyword, a dict's
  ``"origin"`` key, an ``origin`` annotation or ``Origin`` alias of ``Literal`` values, a
  comparison with ``.origin``, an SQL ``provenance ->> 'origin'`` test, a module constant
  ``ORIGIN``), with the module that spells it.
"""

import ast
import inspect
import re
import textwrap
import types
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel

from classbeat.design.revision_ready import revision_ready
from classbeat.jobs.kinds import JobKind

SRC = Path(__file__).resolve().parent.parent / "src"
SUFFIX = "revision_id"
_SQL_ORIGIN = re.compile(r"->>\s*'origin'\s*(?:=\s*'([a-z_]+)'|IN\s*\(([^)]*)\))")


def _revision_fields(model: Any) -> list[str]:
    fields = getattr(model, "model_fields", None)
    return sorted(f for f in fields or {} if f.endswith(SUFFIX))


def revision_kinds(kinds: Iterable[JobKind]) -> dict[str, list[str]]:
    """Each kind whose input takes a revision id from the caller, with those fields (a server
    field is set by ``admit``, never by the caller)."""
    found: dict[str, list[str]] = {}
    for kind in kinds:
        server = kind.server_fields | getattr(kind.input_model, "SERVER_FIELDS", frozenset())
        fields = [f for f in _revision_fields(kind.input_model) if f not in server]
        if fields:
            found[kind.name] = fields
    return found


def _read_models(endpoint: Callable[..., Any]) -> Iterator[type[BaseModel]]:
    """The request models an endpoint reads by hand: every model it names except a response
    model (``...Out``, the repository's convention), and every model a function it calls
    validates (``Model.model_validate_json(...)``)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    space = endpoint.__globals__
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and not n.id.endswith("Out")}
    for callee in _callees(endpoint):
        sub = ast.parse(textwrap.dedent(inspect.getsource(callee)))
        names |= {
            n.func.value.id
            for n in ast.walk(sub)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr.startswith("model_validate")
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id in callee.__globals__
        }
    for name in sorted(names):
        value = space.get(name)
        if isinstance(value, type) and issubclass(value, BaseModel):
            yield value


def revision_routes(routes: list[Any]) -> dict[tuple[str, str], list[str]]:
    """``(method, path)`` of each API route that takes a revision id, with where it does."""
    found: dict[tuple[str, str], list[str]] = {}
    for context in iter_route_contexts(routes):
        route = context.original_route
        if not isinstance(route, APIRoute):
            continue
        path = context.path or route.path
        why = [f"path:{p}" for p in route.param_convertors if p.endswith(SUFFIX)]
        why += [f"query:{q.name}" for q in route.dependant.query_params if q.name.endswith(SUFFIX)]
        if route.body_field is not None:
            why += [f"body:{f}" for f in _revision_fields(route.body_field.field_info.annotation)]
        for model in _read_models(route.endpoint):
            why += [f"{model.__name__}:{f}" for f in _revision_fields(model)]
        if why:
            for method in sorted(route.methods or ()):
                found[(method, path)] = sorted(set(why))
    return found


def _callees(fn: Callable[..., Any]) -> Iterator[Callable[..., Any]]:
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            contents = cell.cell_contents
        except ValueError:
            continue
        if inspect.isfunction(contents):
            yield contents
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError):
        return
    space: dict[str, Any] = getattr(fn, "__globals__", {})
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target: Any = None
        if isinstance(node.func, ast.Name):
            target = space.get(node.func.id)
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            owner = space.get(node.func.value.id)
            if isinstance(owner, types.ModuleType):
                target = getattr(owner, node.func.attr, None)
        if inspect.isfunction(target):
            yield inspect.unwrap(target)


def reaches(
    fn: Callable[..., Any], target: Callable[..., Any] = revision_ready, depth: int = 4
) -> bool:
    """Whether ``fn`` calls ``target``, directly or through the source's own functions."""
    seen: set[int] = set()

    def walk(current: Callable[..., Any], left: int) -> bool:
        if current is target:
            return True
        if left == 0 or id(current) in seen:
            return False
        seen.add(id(current))
        return any(walk(callee, left - 1) for callee in _callees(current))

    return walk(inspect.unwrap(fn), depth)


def _constants(tree: ast.Module) -> dict[str, str]:
    """The module's top-level string constants (``KIND: Final = "..."``)."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value, name = node.value, node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            value, target = node.value, node.targets[0]
            name = target.id if isinstance(target, ast.Name) else ""
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found[name] = value.value
    return found


def _literal_values(node: ast.expr | None) -> list[str]:
    """The strings of a ``Literal[...]`` inside an annotation (``Literal[...] | None`` too)."""
    out: list[str] = []
    for sub in ast.walk(node) if node is not None else ():
        if (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "Literal"
        ):
            for leaf in ast.walk(sub.slice):
                if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                    out.append(leaf.value)
    return out


def origin_literals(source: str) -> set[str]:
    """Every origin value ``source`` spells (the module docstring's forms)."""
    tree = ast.parse(source)
    consts = _constants(tree)
    found: set[str] = set()

    def value_of(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return consts.get(node.id)
        return None

    if "ORIGIN" in consts:
        found.add(consts["ORIGIN"])
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "origin":
            found.update(v for v in [value_of(node.value)] if v is not None)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "origin":
                    found.update(v for v in [value_of(value)] if v is not None)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "origin":
                found.update(_literal_values(node.annotation))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.endswith("Origin"):
                found.update(_literal_values(node.value))
        elif isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if any(isinstance(s, ast.Attribute) and s.attr == "origin" for s in sides):
                found.update(v for s in sides for v in [value_of(s)] if v is not None)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for one, many in _SQL_ORIGIN.findall(node.value):
                found.update([one] if one else re.findall(r"'([a-z_]+)'", many))
    return found


def source_origins() -> dict[str, set[str]]:
    """Each origin value of ``classbeat``'s source, with the modules that spell it."""
    found: dict[str, set[str]] = {}
    for path in sorted((SRC / "classbeat").rglob("*.py")):
        module = ".".join(path.relative_to(SRC).with_suffix("").parts)
        for value in origin_literals(path.read_text("utf-8")):
            found.setdefault(value, set()).add(module)
    return found

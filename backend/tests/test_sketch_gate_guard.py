"""Guard (ADR 0038 amendment of 2026-11-24, #1484): no sketch path reads the soundless switch.
The sketch-only modules never call ``music_kind.switch_on``, ``still_on``, ``admit_soundless`` or
``soundless_on`` (a sketch revise's base gate is ``revise_sketch.admit_base``, whose
``admit_class`` is the other branch's). Where a sketch path shares a module with the real
no-music class (the first design's admit and worker, the soundless revise worker), each such call
sits under an ``if`` that names the sketch. Pure: reads the source only, no database."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"
SWITCH_NAMES = {"switch_on", "still_on", "admit_soundless", "soundless_on"}


def calls(source: str) -> list[tuple[str, list[str]]]:
    """Every call of a switch name, with the unparsed tests of the ``if``s it sits under."""
    found: list[tuple[str, list[str]]] = []

    def walk(node: ast.AST, tests: list[str]) -> None:
        if isinstance(node, (ast.If, ast.IfExp)):
            inner = [*tests, ast.unparse(node.test)]
            for child in ast.iter_child_nodes(node):
                walk(child, tests if child is node.test else inner)
            return
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "music_kind"  # not ``sport.still_on``
                and func.attr in SWITCH_NAMES
            ):
                found.append((func.attr, tests))
        for child in ast.iter_child_nodes(node):
            walk(child, tests)

    walk(ast.parse(source), [])
    return found


def behind_sketch(tests: list[str]) -> bool:
    return any("sketch" in test for test in tests)


def read(path: str) -> str:
    return (SRC / path).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path", ["design/revise_sketch.py", "design/sketch_gate.py", "songs/composer_change.py"]
)
def test_the_sketch_only_modules_never_call_the_soundless_gate(path: str) -> None:
    assert calls(read(path)) == []


@pytest.mark.parametrize(
    "path", ["design/kind.py", "design/kind_music.py", "design/revise_soundless.py"]
)
def test_where_a_sketch_path_shares_code_the_switch_sits_behind_a_sketch_branch(path: str) -> None:
    found = calls(read(path))
    assert found, "the real no-music class still reads the switch here"
    for name, tests in found:
        assert behind_sketch(tests), f"{path}: {name} is not behind a sketch branch"


def test_the_guard_can_fail() -> None:
    bare = "def f():\n    music_kind.still_on()\n"
    unrelated = "def f(a):\n    if a:\n        music_kind.still_on()\n"
    guarded = "def f(sketch):\n    if not sketch:\n        music_kind.still_on()\n"
    in_test = "def f():\n    if music_kind.switch_on():\n        pass\n"
    assert [behind_sketch(t) for _, t in calls(bare)] == [False]
    assert [behind_sketch(t) for _, t in calls(unrelated)] == [False]
    assert [behind_sketch(t) for _, t in calls(guarded)] == [True]
    assert [behind_sketch(t) for _, t in calls(in_test)] == [False]

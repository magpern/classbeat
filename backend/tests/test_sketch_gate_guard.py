"""Guard (ADR 0038 amendment of 2026-11-24, #1484): no sketch path reads the soundless switch.

Pure (source only, no database). It pins every call of ``music_kind.switch_on``, ``still_on``,
``admit_soundless`` and ``soundless_on`` in the modules a sketch path runs through, with the
exact condition and arm (body or else) it sits under:

- the sketch-only modules have none (and import none of the names directly);
- the three shared modules have exactly the real no-music class's call, behind the branch that
  excludes a sketch, so swapping the arms or inverting the condition changes the pinned value
  and fails here.

A sketch revise's base gate is ``revise_sketch.admit_base``, whose ``admit_class`` is the other
branch's (a real no-music class)."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"
SWITCH_NAMES = {"switch_on", "still_on", "admit_soundless", "soundless_on"}
Site = tuple[str, list[tuple[str, str]]]  # (name, [(condition, "body" | "orelse"), ...])

SKETCH_ONLY = [
    "design/sketch_gate.py",
    "design/revise_sketch.py",
    "design/composer.py",
    "design/interpret_kind.py",
    "design/interpret_admit.py",  # split out of interpret_kind.py: reads no switch (#1536)
    "songs/composer_change.py",
    "classes/interpretation.py",
    "classes/change_view.py",
    "design/revise.py",
]
# The real no-music class's call sites in the shared modules, and nothing else.
PINNED: dict[str, list[Site]] = {
    "design/kind_music.py": [("admit_soundless", [("soundless", "body"), ("sketch", "orelse")])],
    "design/kind.py": [
        (
            "still_on",
            [("isinstance(given.music, NoMusicChoice) and given.mode != 'sketch'", "body")],
        )
    ],
    "design/revise_soundless.py": [("still_on", [("not sketch", "body")])],
}


def aliases(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """The names the module ``music_kind`` is bound to (``import ... as mk``, ``from
    classbeat.design import music_kind as m``) and the aliases of its switch functions."""
    modules = {"music_kind"}
    functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                a.asname for a in node.names if a.asname and a.name.endswith("music_kind")
            )
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "music_kind":
                    modules.add(a.asname or a.name)
                elif (node.module or "").endswith("music_kind") and a.name in SWITCH_NAMES:
                    functions[a.asname or a.name] = a.name
    return modules, functions


def sites(source: str) -> list[Site]:
    """Every use of a switch name (a ``music_kind.<name>`` call, also through an alias of the
    module or of the function, a bare call or a direct import of it), with the conditions and
    arms it sits under."""
    found: list[Site] = []
    tree = ast.parse(source)
    modules, functions = aliases(tree)

    def walk(node: ast.AST, path: list[tuple[str, str]]) -> None:
        if isinstance(node, (ast.If, ast.IfExp)):
            test = ast.unparse(node.test)
            walk(node.test, path)
            arms = (("body", node.body), ("orelse", node.orelse))
            for arm, children in arms:
                for child in children if isinstance(children, list) else [children]:
                    walk(child, [*path, (test, arm)])
            return
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in SWITCH_NAMES
                and (
                    (isinstance(func.value, ast.Name) and func.value.id in modules)
                    # ``import classbeat.design.music_kind`` then the dotted path
                    or ast.unparse(func.value).endswith(".music_kind")
                )  # not ``sport.still_on``
            ):
                found.append((func.attr, path))
            elif isinstance(func, ast.Name) and (func.id in SWITCH_NAMES or func.id in functions):
                found.append((functions.get(func.id, func.id), path))
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("music_kind"):
            found.extend((a.name, path) for a in node.names if a.name in SWITCH_NAMES)
        for child in ast.iter_child_nodes(node):
            walk(child, path)

    walk(tree, [])
    return found


def read(path: str) -> str:
    return (SRC / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("path", SKETCH_ONLY)
def test_the_sketch_modules_never_read_the_soundless_switch(path: str) -> None:
    assert sites(read(path)) == []


@pytest.mark.parametrize("path", sorted(PINNED))
def test_the_shared_modules_read_it_only_for_a_real_no_music_class(path: str) -> None:
    assert sites(read(path)) == PINNED[path]


def test_the_guard_can_fail() -> None:
    """Each mutation of the pinned sites changes what ``sites`` reports."""
    arms = "x = a() if sketch else music_kind.admit_soundless(db)\n"
    swapped = "x = music_kind.admit_soundless(db) if sketch else a()\n"
    assert sites(arms) == [("admit_soundless", [("sketch", "orelse")])]
    assert sites(swapped) == [("admit_soundless", [("sketch", "body")])]
    right = "if not sketch:\n    music_kind.still_on()\n"
    assert sites(right) == PINNED["design/revise_soundless.py"]
    assert sites("if sketch:\n    music_kind.still_on()\n") != PINNED["design/revise_soundless.py"]
    assert sites("music_kind.switch_on()\n") == [("switch_on", [])]
    assert sites("from classbeat.design.music_kind import still_on\n") == [("still_on", [])]
    assert sites("still_on()\n") == [("still_on", [])]
    assert sites("sport.still_on(run)\n") == []


@pytest.mark.parametrize(
    "source",
    [
        "from classbeat.design import music_kind as m\nm.switch_on()\n",
        "import classbeat.design.music_kind as mk\nmk.switch_on()\n",
        "import classbeat.design.music_kind\nclassbeat.design.music_kind.switch_on()\n",
        "from classbeat.design import music_kind\nmusic_kind.switch_on()\n",
        "def f():\n    from classbeat.design import music_kind as m\n    m.switch_on()\n",
    ],
)
def test_the_guard_sees_the_switch_through_a_module_alias(source: str) -> None:
    assert sites(source)[-1] == ("switch_on", [])
    assert [name for name, _ in sites(source)] == ["switch_on"]


def test_the_guard_sees_the_switch_through_a_function_alias() -> None:
    src = "from classbeat.design.music_kind import still_on as s\ns()\n"
    assert [name for name, _ in sites(src)] == ["still_on", "still_on"]
    # an unrelated alias is not a switch
    assert sites("from classbeat.design import sport as m\nm.switch_on()\n") == []

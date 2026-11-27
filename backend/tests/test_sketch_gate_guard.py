"""Guard (ADR 0038 amendment of 2026-11-24, #1484; ADR 0007 amendment of 2026-11-27, #1543): no
module reads a soundless switch, and the soundless model check runs only where it is pinned.

Pure (source only, no database). Until #1543 this guard pinned the real no-music class's calls of
the switch (``music_kind.switch_on``, ``still_on``, ``admit_soundless``, ``soundless_on``) in three
shared modules and kept them out of the sketch modules. The owner retired the switch (2026-11-26:
"No music" is always a choice), so the rule is now stricter, not looser:

- **every** module under ``backend/src/classbeat`` has no use of a retired switch name (an
  allowlist of no sites at all, so a switch brought back anywhere fails here);
- the worker's model check ``serve_model`` is called exactly at the two pinned sites, with the
  pinned ``sketch`` argument and under the pinned condition, and ``require_covered`` only inside
  ``serve_model``'s sketch arm, so the sketch keeps its refusal (``model_config``) and a real
  no-music class gets the stale-route warning; swapping the argument or the arm changes the
  pinned value and fails here.

The sketch's own gate, ``sketch_gate.admit_sketch``, is called only at the pinned sites too
(``ADMIT_SKETCH``): the first sketch's design admit under ``soundless and sketch`` (a direct
``design_draft`` in mode ``sketch``; its database test is
``test_composer_sketch_gate``), a sketch edit's admit and ``can_change``, and the composer's
availability. A sketch revise's base gate is ``revise_sketch.admit_base``."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"
RETIRED = {"switch_on", "still_on", "admit_soundless", "soundless_on"}
MODEL_CHECKS = {"serve_model", "require_covered"}
SWITCH_NAMES = RETIRED | MODEL_CHECKS
# (name, [(condition, "body" | "orelse"), ...], the call's keyword arguments)
Site = tuple[str, list[tuple[str, str]], str]

# The only sites of a model check in the whole source tree.
PINNED: dict[str, list[Site]] = {
    "design/kind.py": [
        (
            "serve_model",
            [("isinstance(given.music, NoMusicChoice)", "body")],
            "sketch=given.mode == 'sketch'",
        )
    ],
    "design/revise_soundless.py": [("serve_model", [], "sketch=sketch")],
    "design/music_kind.py": [("require_covered", [("sketch", "body")], "")],
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
                found.append((func.attr, path, keywords(node)))
            elif isinstance(func, ast.Name) and (func.id in SWITCH_NAMES or func.id in functions):
                found.append((functions.get(func.id, func.id), path, keywords(node)))
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("music_kind"):
            found.extend((a.name, path, "") for a in node.names if a.name in SWITCH_NAMES)
        for child in ast.iter_child_nodes(node):
            walk(child, path)

    walk(tree, [])
    return found


def keywords(call: ast.Call) -> str:
    return ", ".join(ast.unparse(k) for k in call.keywords)


def read(path: str) -> str:
    return (SRC / path).read_text(encoding="utf-8")


def every_module() -> list[str]:
    return sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py"))


def test_no_module_uses_a_retired_switch_and_the_model_checks_are_only_the_pinned_ones() -> None:
    found = {path: sites(read(path)) for path in every_module()}
    assert {path: got for path, got in found.items() if got} == PINNED
    assert all(name in MODEL_CHECKS for got in PINNED.values() for name, _, _ in got)


# Every call of ``sketch_gate.admit_sketch`` in the tree: (the conditions it sits under).
ADMIT_SKETCH: dict[str, list[list[tuple[str, str]]]] = {
    "design/kind_music.py": [[("soundless and sketch", "body")]],
    "design/revise_sketch.py": [[("await is_sketch(db, ctx.user_id, class_id, base)", "body")],
                                [("current is not None and await is_sketch(db, ctx.user_id, "
                                  "class_id, current)", "body")]],
    "design/composer.py": [[]],
}  # fmt: skip


def admit_sketch_sites(source: str) -> list[list[tuple[str, str]]]:
    """The conditions and arms above each ``sketch_gate.admit_sketch(...)`` call."""
    found: list[list[tuple[str, str]]] = []

    def walk(node: ast.AST, path: list[tuple[str, str]]) -> None:
        if isinstance(node, (ast.If, ast.IfExp)):
            test = ast.unparse(node.test)
            walk(node.test, path)
            for arm, children in (("body", node.body), ("orelse", node.orelse)):
                for child in children if isinstance(children, list) else [children]:
                    walk(child, [*path, (test, arm)])
            return
        func = node.func if isinstance(node, ast.Call) else None
        if isinstance(func, ast.Attribute) and ast.unparse(func) == "sketch_gate.admit_sketch":
            found.append(path)
        for child in ast.iter_child_nodes(node):
            walk(child, path)

    walk(ast.parse(source), [])
    return found


def test_the_sketch_gate_is_called_only_at_the_pinned_sites() -> None:
    found = {path: admit_sketch_sites(read(path)) for path in every_module()}
    assert {path: got for path, got in found.items() if got} == ADMIT_SKETCH


def test_the_sketch_admit_pin_can_fail() -> None:
    right = "if soundless and sketch:\n    refused = await sketch_gate.admit_sketch(db, ctx)\n"
    assert admit_sketch_sites(right) == ADMIT_SKETCH["design/kind_music.py"]
    assert admit_sketch_sites("refused = await sketch_gate.admit_sketch(db, ctx)\n") == [[]]
    assert (
        admit_sketch_sites("if soundless:\n    await sketch_gate.admit_sketch(db, ctx)\n")
        != (ADMIT_SKETCH["design/kind_music.py"])
    )
    assert admit_sketch_sites("if soundless and sketch:\n    pass\n") == []


def test_the_guard_reads_the_whole_tree() -> None:
    modules = every_module()
    assert len(modules) > 300  # every module, not a hand-written list
    assert set(PINNED) <= set(modules)


def test_the_guard_can_fail() -> None:
    """Each mutation of the pinned sites changes what ``sites`` reports."""
    right = (
        "if isinstance(given.music, NoMusicChoice):\n"
        "    music_kind.serve_model(model, sketch=given.mode == 'sketch')\n"
    )
    assert sites(right) == PINNED["design/kind.py"]
    always = right.replace("given.mode == 'sketch'", "True")
    never = right.replace("given.mode == 'sketch'", "False")
    inverted = right.replace("given.mode == 'sketch'", "given.mode != 'sketch'")
    for mutant in (always, never, inverted):
        assert sites(mutant) != PINNED["design/kind.py"]
    arm = "if sketch:\n    require_covered(model)\n"
    assert sites(arm) == PINNED["design/music_kind.py"]
    assert sites("if not sketch:\n    require_covered(model)\n") != PINNED["design/music_kind.py"]
    assert sites("music_kind.switch_on()\n") == [("switch_on", [], "")]
    assert sites("from classbeat.design.music_kind import still_on\n") == [("still_on", [], "")]
    assert sites("still_on()\n") == [("still_on", [], "")]
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
    assert sites(source)[-1] == ("switch_on", [], "")
    assert [name for name, _, _ in sites(source)] == ["switch_on"]


def test_the_guard_sees_the_switch_through_a_function_alias() -> None:
    src = "from classbeat.design.music_kind import still_on as s\ns()\n"
    assert [name for name, _, _ in sites(src)] == ["still_on", "still_on"]
    # an unrelated alias is not a switch
    assert sites("from classbeat.design import sport as m\nm.switch_on()\n") == []

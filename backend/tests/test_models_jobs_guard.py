"""Model jobs build their provider only from a ``resolve_route`` choice (ADR 0012 section 5;
#215, item 1 of the #267 review), and proof that the guard fails on a planted violation. Static.

- Only the provider adapters themselves, the eval command (``classbeat.evals``, which takes its
  model from the owner's command line) and ``classbeat.models.providers`` (``RouteModels``:
  ``resolve_route``, then ``provider_for_choice``; ``fake`` bypasses routes there) may import
  ``classbeat.agent.providers``.
- Only ``classbeat.design.kind`` (``runtime_from_settings``, the worker's runtime) may import
  ``classbeat.models.providers``, and nothing else in ``classbeat`` may: so a job's provider
  comes from ``Runtime.models``, never from the settings directly.
- The kinds' ``Runtime`` has no provider factory field, only ``models``.
- DeepSeek's eval-only path (Architect, 2026-10-31): only ``classbeat.evals`` may pass
  ``eval_allow_disabled_deepseek`` to ``provider_for_choice``, and only it and
  ``classbeat.models.providers`` may pass ``allow_disabled`` to ``deepseek_provider``; so a job
  never builds DeepSeek while ``CLASSBEAT_DEEPSEEK_ENABLED`` is off.
"""

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from classbeat.design import Runtime

SRC = Path(__file__).resolve().parent.parent / "src"
ADAPTERS = "classbeat.agent.providers"
ROUTED = "classbeat.models.providers"
ADAPTER_IMPORTERS = ("classbeat.agent.providers", "classbeat.evals", ROUTED)
ROUTED_IMPORTERS = ("classbeat.design.kind",)
EVAL_ONLY_KEYWORDS = {
    "eval_allow_disabled_deepseek": ("classbeat.evals",),
    "allow_disabled": ("classbeat.evals", ROUTED),
}


def _module(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _within(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _imported(tree: ast.AST) -> list[tuple[int, str]]:
    """Every module an import names, ``from a import b`` as both ``a`` and ``a.b``."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
            found += [(node.lineno, f"{node.module}.{a.name}") for a in node.names]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
            in ("import_module", "__import__")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.append((node.lineno, node.args[0].value))
    return found


def scan(source: str, module: str) -> list[tuple[int, str]]:
    problems: list[tuple[int, str]] = []
    for line, name in _imported(ast.parse(source)):
        if _within(name, (ADAPTERS,)) and not _within(module, ADAPTER_IMPORTERS):
            problems.append((line, f"imports {name}: providers come from a route's choice"))
        if _within(name, (ROUTED,)) and not _within(module, (*ROUTED_IMPORTERS, ROUTED)):
            problems.append((line, f"imports {name}: only the worker's runtime builds it"))
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                allowed = EVAL_ONLY_KEYWORDS.get(kw.arg or "")
                if allowed is not None and not _within(module, allowed):
                    problems.append((node.lineno, f"passes {kw.arg}: the eval command only"))
                # ``**kw`` could carry either keyword: only where they may be passed anyway.
                if (
                    kw.arg is None
                    and not _within(module, ("classbeat.evals", ROUTED))
                    and _names_deepseek_factory(node)
                ):
                    problems.append((node.lineno, "passes **kwargs to a DeepSeek factory"))
    return problems


def _names_deepseek_factory(call: ast.Call) -> bool:
    func = call.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return name in ("deepseek_provider", "provider_for_choice")


def test_job_providers_come_only_from_the_routes_source() -> None:
    violations = [
        (_module(path), line, why)
        for path in sorted((SRC / "classbeat").rglob("*.py"))
        for line, why in scan(path.read_text("utf-8"), _module(path))
    ]
    assert violations == []


def test_the_allowlists_are_exactly_the_reviewed_ones() -> None:
    assert ADAPTER_IMPORTERS == ("classbeat.agent.providers", "classbeat.evals", ROUTED)
    assert ROUTED_IMPORTERS == ("classbeat.design.kind",)
    assert EVAL_ONLY_KEYWORDS == {
        "eval_allow_disabled_deepseek": ("classbeat.evals",),
        "allow_disabled": ("classbeat.evals", ROUTED),
    }


def test_the_runtime_takes_a_model_source_and_no_provider_factory() -> None:
    assert [f.name for f in fields(Runtime)] == ["keyring", "models", "agent", "context_requests"]
    kind = (SRC / "classbeat" / "design" / "kind.py").read_text("utf-8")
    assert "from classbeat.models.providers import RouteModels" in kind
    assert "models=RouteModels(settings)" in kind


@pytest.mark.parametrize(
    "planted",
    [
        "from classbeat.agent.providers import provider_for\n",
        "from classbeat.agent.providers.anthropic import AnthropicProvider\n",
        "from classbeat.agent import providers\n",
        "import classbeat.agent.providers.openai_responses as o\n",
        "import importlib\nimportlib.import_module('classbeat.agent.providers')\n",
        "def f():\n    from classbeat.agent.providers import OpenAIProvider\n",
        "from classbeat.models.providers import provider_for_choice\n",
        "from classbeat.models import providers\n",
    ],
)
@pytest.mark.parametrize("module", ["classbeat.songs.kind", "classbeat.design.revise"])
def test_a_planted_provider_import_in_a_kind_fails_the_guard(planted: str, module: str) -> None:
    assert scan(planted, module) != []


@pytest.mark.parametrize(
    "planted",
    [
        "provider_for_choice(s, c, eval_allow_disabled_deepseek=True)",
        "deepseek_provider(s, allow_disabled=True)",
        "providers.deepseek_provider(s, allow_disabled=flag)",
        "provider_for_choice(s, c, **extra)",
    ],
)
@pytest.mark.parametrize("module", ["classbeat.models.jobs", "classbeat.songs.kind"])
def test_the_disabled_deepseek_path_is_refused_outside_the_eval_command(
    planted: str, module: str
) -> None:
    assert scan(planted, module) != []
    assert scan(planted, "classbeat.evals.suite_live") == []


def test_the_routes_source_never_takes_the_eval_path() -> None:
    source = (SRC / "classbeat" / "models" / "providers.py").read_text("utf-8")
    assert scan(source, ROUTED) == []
    tree = ast.parse(source)
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    [routes] = [n for n in classes if n.name == "RouteModels"]
    passed = {kw.arg for n in ast.walk(routes) if isinstance(n, ast.Call) for kw in n.keywords}
    assert "eval_allow_disabled_deepseek" not in passed
    assert "allow_disabled" not in passed


def test_the_allowed_modules_pass_and_others_do_not() -> None:
    adapters = "from classbeat.agent.providers import OpenAIProvider\n"
    assert scan(adapters, ROUTED) == []
    assert scan(adapters, "classbeat.evals.live") == []
    assert scan(adapters, "classbeat.design.kind") != []  # the kind goes through RouteModels
    assert (
        scan("from classbeat.models.providers import RouteModels\n", "classbeat.design.kind") == []
    )
    assert scan("from classbeat.models.providers import RouteModels\n", "classbeat.worker") != []

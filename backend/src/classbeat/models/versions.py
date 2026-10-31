"""What a recorded result must match to count for the running code (ADR 0012 section 5).

- ``running_prompt_versions(group)``: the ``prompt_version`` of each stage of the stage group as
  this code builds it (``design``: ``design`` and ``revise``; ``songs``: ``pick_pool`` and
  ``choose_songs``). A stage's prompt version is the SHA-256 of its system prompt and tool
  definitions only (``classbeat.agent.stage``), never of a job's data, so the stages are built
  here with placeholder contexts whose fields no definition reads
  (``tests/test_models_routes.py`` compares the result with stages built from real contexts).
- ``running_suite_version()``: the manifest's ``suite_version``, read from the package's
  ``evals/suite/manifest.json`` **without importing** ``classbeat.evals`` (the web process, from
  which M5's admin calls ``set_route``, must not load the eval package;
  ``tests/test_agent_anthropic_run.py``). The digest check stays with the suite
  (``classbeat.evals.suite.load_suite``); a test asserts both read the same version.
"""

import json
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, cast

from classbeat.agent.choose_songs import ChooseContext, choose_stage
from classbeat.agent.design import DesignContext, design_stage
from classbeat.agent.pick_pool import PoolContext, pool_stage
from classbeat.agent.revise import ReviseContext, revise_stage
from classbeat.agent.stage import Stage

MANIFEST: Final = Path(__file__).resolve().parent.parent / "evals" / "suite" / "manifest.json"
GROUP_STAGES: Final[dict[str, tuple[str, ...]]] = {
    "design": ("design", "revise"),
    "songs": ("pick_pool", "choose_songs"),
}


def _no_context() -> Any:
    # The fields ``revise_stage`` copies into its ``DesignContext`` at build time; the handlers
    # that would read them are never called here.
    return SimpleNamespace(brief=None, map={}, summary={})


def _stages() -> tuple[Stage, ...]:
    blank = _no_context()
    return (
        design_stage(cast(DesignContext, blank)),
        revise_stage(cast(ReviseContext, blank)),
        pool_stage(cast(PoolContext, blank)),
        choose_stage(cast(ChooseContext, blank)),
    )


@cache
def all_prompt_versions() -> dict[str, str]:
    """Every model stage's running ``prompt_version``, by stage name."""
    return {stage.name: stage.prompt_version for stage in _stages()}


def running_prompt_versions(group: str) -> dict[str, str]:
    """The running prompt version of each stage of ``group`` (``KeyError`` for another)."""
    versions = all_prompt_versions()
    return {name: versions[name] for name in GROUP_STAGES[group]}


def group_prompt_versions(group: str, recorded: dict[str, str]) -> dict[str, str]:
    """The part of a result's ``prompt_versions`` that belongs to ``group``'s stages."""
    return {name: recorded[name] for name in GROUP_STAGES[group] if name in recorded}


def running_suite_version() -> int:
    """The manifest's ``suite_version`` (an integer of at least 1; ``ValueError`` otherwise)."""
    data = json.loads(MANIFEST.read_text("utf-8"))
    version = data.get("suite_version") if isinstance(data, dict) else None
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("the suite manifest has no valid suite_version")
    return version

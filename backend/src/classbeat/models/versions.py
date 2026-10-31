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
- ``running_manifest_digest()``: the manifest's stored ``digest``, read the same way. The suite
  refuses to load when it is not the digest of the suite's files (``load_suite``), and
  ``tests/test_evals_suite.py`` checks that it is, so it stands for the running suite without
  hashing the case files here. A result whose ``manifest_digest`` differs was recorded on
  another suite (#215, item 2 of the #267 review).
"""

import json
import re
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
    # The fields ``revise_stage`` reads or copies at build time; the handlers
    # that would read them are never called here.
    return SimpleNamespace(brief=None, map={}, summary={}, songs_changed=False)


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


def _manifest() -> dict[str, Any]:
    data = json.loads(MANIFEST.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("the suite manifest is not an object")
    return cast(dict[str, Any], data)


def running_suite_version() -> int:
    """The manifest's ``suite_version`` (an integer of at least 1; ``ValueError`` otherwise)."""
    version = _manifest().get("suite_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("the suite manifest has no valid suite_version")
    return version


def running_manifest_digest() -> str:
    """The manifest's stored ``digest`` (64 lowercase hex characters; ``ValueError`` otherwise)."""
    digest = _manifest().get("digest")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("the suite manifest has no valid digest")
    return digest

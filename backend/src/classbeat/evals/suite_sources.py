"""What the suite's digest covers (ADR 0012 section 4), split out of ``classbeat.evals.suite``.

- ``DIGEST_SOURCES``: the modules of this package that define cases, checks, the synthetic music
  and catalogue and the verdict;
- ``RULE_SOURCES``: the rules the design stage is given and what the interpreter is sent, paths
  under ``classbeat``;
- ``check_constants``: the upper-case constants of the check modules and the outside thresholds;
- ``suite_digest``: the SHA-256 of the manifest, the cases, the sources and the constants.

A change to any of them is a new ``suite_version`` (``suite.load_suite`` refuses a stale digest).
"""

import hashlib
import json
import re
from collections.abc import Mapping
from importlib import resources
from types import ModuleType
from typing import Any, Final

from mowl import workout

from classbeat.agent import songs
from classbeat.evals import checks, music, pick, pick_world, song_checks

DIGEST_SOURCES: Final = (
    "cases.py",
    "checks.py",
    "game_checks.py",
    "music.py",
    "pick.py",
    "pick_case.py",
    "pick_world.py",
    "runner.py",
    # #908: the results types and formats split out of the runner, and the chained revise case's
    # interpret step. Kept out until the suite held a chained case, so the earlier digests
    # stayed valid.
    "results.py",
    "revise_chain.py",
    "song_checks.py",
    "suite.py",
    # #1140: what the digest covers and the checks of a song run, split out of suite.py and
    # pick.py (CLAUDE.md rule 2).
    "suite_sources.py",
    "pick_checks.py",
    # #1188: the kept_shape revision check of the composer's design on the songs.
    "shape_checks.py",
    "gate.py",
    # The interpret category (issue #880): its case model, checks and runner. Kept out until the
    # suite held interpret cases, so the earlier digests stayed valid.
    "interpret_cases.py",
    "interpret_checks.py",
    "interpret_run.py",
    "suite_manifest.py",
)
# What the design stage is told besides its prompt (ADR 0017 sections 3 and 5): every module
# that decides which rules exist, which hits fire, what the section and each hit say, how a
# brief picks its profile, and where the section and the hits enter the model's input and tool
# results. Paths under ``classbeat``; a change to any of them is a new suite. A new module under
# ``design/rules/`` must be listed here or excluded in ``tests/test_evals_class_patterns.py``.
# The profile files are covered by the digests in ``design/profiles/index.json``.
RULE_SOURCES: Final = (
    "agent/guidance.py",
    "agent/model_input.py",
    "agent/section_energy.py",
    "agent/song_input.py",
    "agent/tempo_policy.py",
    "design/guidance.py",
    "design/selection.py",
    "design/rules/__init__.py",
    "design/rules/engine.py",
    "design/rules/enums.py",
    "design/rules/profile.py",
    "design/rules/rationales.py",
    "design/profiles/index.json",
    # Running (ADR 0018, issue #465): how a running brief picks its profile and gets its rules, and
    # what the model sees of a running class (brief, timeline, quality sentences). Kept out until
    # the suite held running cases, so the earlier digests stayed valid.
    "design/sport_rules.py",
    "agent/running_input.py",
    # Soundless (ADR 0007 amendment of 2026-11-03, issue #531): what the model sees of a class with
    # no music (the brief, the timeline of the draft as written, the two first messages). Kept out
    # until the suite held soundless cases, so the earlier digests stayed valid.
    "agent/soundless_input.py",
    # Interpret (ADR 0029 section 4, issue #880): what the interpreter is sent and may return, and
    # the explicit-value rules whose closed values it reports. Kept out until the suite held
    # interpret cases, so the earlier digests stayed valid.
    "agent/interpret.py",
    "agent/interpret_input.py",
    "agent/interpreted.py",
    "agent/explicit_input.py",
    "agent/explicit_check.py",
    # Revise layering (#904, #908): what the interpreter is told of a revise and how its reading
    # is layered onto the class's values; the chained case runs both. Kept out until the suite
    # held a chained case, so the earlier digests stayed valid.
    "design/revise_stated.py",
    "design/revise_layer_policy.py",
    # The music taste (issue #1133): the closed form the interpreter returns and the song stages
    # are told. Added with suite version 24.
    "agent/music_taste.py",
    # The composer's part of a reading (ADR 0038 section 3, issue #1185): pinned songs and
    # follow-up questions. Added with suite version 26.
    "agent/interpreted_composer.py",
    # The short name a new class may get from its reading (issue #1233): how the interpreter's
    # name is cleaned before it is kept. Added with suite version 28.
    "class_title_policy.py",
    # The shape of the ride (#1485, suite version 30): the closed sections, what the interpreter
    # reports of them, how the policy reads them and how the design stages are told the brief.
    "agent/ride_shape.py",
    "agent/interpreted_shape.py",
    "agent/interpreted_taste.py",
    "agent/stated.py",
    "agent/brief_input.py",
    "agent/stated_layer.py",
    "design/shape_policy.py",
)
CONSTANT_MODULES: Final[tuple[ModuleType, ...]] = (checks, music, pick, pick_world, song_checks)
CONSTANT_NAME: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")


def plain(name: str, value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, tuple | list):
        return [plain(name, v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, frozenset | set):
        return sorted(plain(name, v) for v in value)  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {str(k): plain(name, v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, re.Pattern):
        return {"pattern": value.pattern, "flags": int(value.flags)}  # pyright: ignore[reportUnknownMemberType]
    raise TypeError(f"{name}: a check constant of an unhashable kind; teach suite_sources.plain")


def check_constants() -> dict[str, Any]:
    """The upper-case constants of the check modules, and the outside thresholds they use."""
    out: dict[str, Any] = {}
    for module in CONSTANT_MODULES:
        short = module.__name__.rsplit(".", 1)[-1]
        for name, value in sorted(vars(module).items()):
            if CONSTANT_NAME.match(name) and not isinstance(value, ModuleType | type):
                out[f"{short}.{name}"] = plain(name, value)
    out["mowl.workout.MIN_COOLDOWN_SEC"] = workout.MIN_COOLDOWN_SEC
    out["agent.songs.TOLERANCE"] = songs.TOLERANCE
    return out


def digest_sources() -> dict[str, str]:
    folder = resources.files("classbeat.evals")
    package = resources.files("classbeat")
    out = {
        name: (folder / name).read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
        for name in DIGEST_SOURCES
    }
    for name in RULE_SOURCES:
        raw = package.joinpath(*name.split("/")).read_bytes().replace(b"\r\n", b"\n")
        out[f"classbeat/{name}"] = raw.decode("utf-8")
    return out


def suite_digest(
    manifest: Mapping[str, Any],
    cases: Mapping[str, Mapping[str, Any]],
    sources: Mapping[str, str],
    constants: Mapping[str, Any],
) -> str:
    """The SHA-256 of the suite: a pure function of its parts (see the module docstring)."""
    body = {
        "manifest": {k: v for k, v in manifest.items() if k != "digest"},
        "cases": dict(cases),
        "sources": dict(sources),
        "constants": dict(constants),
    }
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

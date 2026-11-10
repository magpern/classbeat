"""The regression suite (ADR 0012 section 4): ``suite/manifest.json``, its cases and its digest.

The **suite** is every case the manifest names, by category and stage group: the smoke cases
(``smoke/``, design and revise), the song cases (``smoke_pick/``) and the suite's own
(``suite/<category>/*.json``; a file with a ``catalogue`` is a song case, else a design case).
A case belongs to exactly one category. The manifest also holds the gate's rules and thresholds
(``classbeat.evals.gate``) and the zone 7 TSS margin (section 7).

``suite_version`` is bumped by any change to a case, a check, a threshold or the manifest. The
manifest stores the **digest** of all of them; ``load_suite`` refuses a suite whose digest is not
the stored one, so a changed suite can never run (or be recorded) under an older version. The
digest covers:

- the manifest itself (everything but ``digest``);
- every case it names, as validated (defaults included);
- the source of the modules that define cases, checks, the synthetic music and catalogue and the
  verdict (``DIGEST_SOURCES``), newlines normalised;
- the rules the design stage is given (``RULE_SOURCES``: the section's wording, the profile
  selection, the engine and the profiles' index), so a new ``rules_version`` is a new suite;
- the upper-case constants of the check modules and the outside thresholds the checks use
  (``check_constants``), so a constant patched at run time changes it too.

``tests/test_evals_suite.py`` pins each released version's digest (append only): a change without
a new ``suite_version`` fails there.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from importlib import resources
from types import ModuleType
from typing import Any, Final

from mowl import workout

from classbeat.agent import songs
from classbeat.evals import checks, music, pick, pick_world, song_checks
from classbeat.evals.cases import Case
from classbeat.evals.interpret_cases import InterpretCase
from classbeat.evals.pick import PickCase
from classbeat.evals.suite_manifest import (
    CATEGORY_GROUPS as CATEGORY_GROUPS,  # re-exported: the manifest's names live in suite_manifest
)
from classbeat.evals.suite_manifest import (
    GROUPS as GROUPS,
)
from classbeat.evals.suite_manifest import (
    Group as Group,
)
from classbeat.evals.suite_manifest import (
    Manifest as Manifest,
)

DIGEST_SOURCES: Final = (
    "cases.py",
    "checks.py",
    "game_checks.py",
    "music.py",
    "pick.py",
    "pick_world.py",
    "runner.py",
    "song_checks.py",
    "suite.py",
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
)
CONSTANT_MODULES: Final[tuple[ModuleType, ...]] = (checks, music, pick, pick_world, song_checks)
CONSTANT_NAME: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SuiteError(ValueError):
    """The suite cannot be loaded; the message names the rule, never file content."""


@dataclass(frozen=True)
class Suite:
    manifest: Manifest
    design_cases: dict[str, Case]
    song_cases: dict[str, PickCase]
    interpret_cases: dict[str, InterpretCase] = field(
        default_factory=lambda: dict[str, InterpretCase]()
    )

    def category_of(self, name: str) -> str:
        return self.manifest.case_index()[name][0]


def fraction(value: float) -> Fraction:
    """A manifest share as an exact fraction (``0.9`` is 9/10, not the nearest float)."""
    return Fraction(str(value))


def _plain(name: str, value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, tuple | list):
        return [_plain(name, v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, frozenset | set):
        return sorted(_plain(name, v) for v in value)  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {str(k): _plain(name, v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, re.Pattern):
        return {"pattern": value.pattern, "flags": int(value.flags)}  # pyright: ignore[reportUnknownMemberType]
    raise TypeError(f"{name}: a check constant of an unhashable kind; teach suite._plain")


def check_constants() -> dict[str, Any]:
    """The upper-case constants of the check modules, and the outside thresholds they use."""
    out: dict[str, Any] = {}
    for module in CONSTANT_MODULES:
        short = module.__name__.rsplit(".", 1)[-1]
        for name, value in sorted(vars(module).items()):
            if CONSTANT_NAME.match(name) and not isinstance(value, ModuleType | type):
                out[f"{short}.{name}"] = _plain(name, value)
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


def _case_files() -> dict[str, tuple[str, str]]:
    """Every case file of the package: name -> (folder, text). A name is in one place only."""
    root = resources.files("classbeat.evals")
    folders = ["smoke", "smoke_pick"]
    suite = root / "suite"
    folders += [
        f"suite/{d.name}" for d in sorted(suite.iterdir(), key=lambda p: p.name) if d.is_dir()
    ]
    out: dict[str, tuple[str, str]] = {}
    for folder in folders:
        here = root.joinpath(*folder.split("/"))
        for item in sorted(here.iterdir(), key=lambda p: p.name):
            if not item.name.endswith(".json"):
                continue
            name = item.name.removesuffix(".json")
            if name in out:
                raise SuiteError(f"the case {name} exists in {out[name][0]} and {folder}")
            out[name] = (folder, item.read_text("utf-8"))
    return out


def _load_case(name: str, folder: str, text: str) -> Case | PickCase | InterpretCase:
    data = json.loads(text)
    model: type[Case | PickCase | InterpretCase] = Case
    if isinstance(data, dict):
        if "catalogue" in data:
            model = PickCase
        elif data.get("kind") == "interpret":
            model = InterpretCase
    case = model.model_validate_json(text)
    if case.name != name:
        raise SuiteError(f"{folder}/{name}.json: the file is named after its case")
    return case


def read_manifest_data() -> dict[str, Any]:
    text = (resources.files("classbeat.evals") / "suite" / "manifest.json").read_text("utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SuiteError("the manifest is a JSON object")
    return data  # pyright: ignore[reportUnknownVariableType]


def load_suite() -> Suite:
    """The manifest and its cases, refused unless every case exists, fits its stage group and
    the stored digest is the suite's."""
    data = read_manifest_data()
    manifest = Manifest.model_validate(data)
    files = _case_files()
    design: dict[str, Case] = {}
    song: dict[str, PickCase] = {}
    interpret: dict[str, InterpretCase] = {}
    dumped: dict[str, dict[str, Any]] = {}
    for name, (category, group) in sorted(manifest.case_index().items()):
        if name not in files:
            raise SuiteError(f"the manifest names {name}, which has no case file")
        case = _load_case(name, *files[name])
        if isinstance(case, PickCase) != (group == "songs"):
            raise SuiteError(f"{name} is listed in the {group} group but is not a {group} case")
        if category == "interpret" and not isinstance(case, InterpretCase):
            raise SuiteError(f"{name}: the interpret category holds interpret cases only")
        if isinstance(case, InterpretCase) and category not in ("interpret", "adversarial"):
            raise SuiteError(f"{name}: an interpret case is judged in interpret or adversarial")
        if isinstance(case, InterpretCase):
            interpret[name] = case
        elif isinstance(case, PickCase):
            if case.expect.outcome != manifest.expected_outcome(name):
                raise SuiteError(f"{name}: the case and the manifest expect different outcomes")
            song[name] = case
        else:
            design[name] = case
        dumped[name] = case.model_dump(mode="json")
    digest = suite_digest(data, dumped, digest_sources(), check_constants())
    if digest != manifest.digest:
        raise SuiteError(
            "the suite changed: bump suite_version in evals/suite/manifest.json, set its digest "
            "to the new one and pin it in tests/test_evals_suite.py"
        )
    return Suite(manifest, design, song, interpret)


def current_digest() -> str:
    """The digest of the suite as it is in the package (for the bump procedure and the test)."""
    data = read_manifest_data()
    manifest = Manifest.model_validate(data)
    files = _case_files()
    dumped = {
        name: _load_case(name, *files[name]).model_dump(mode="json")
        for name in sorted(manifest.case_index())
        if name in files
    }
    return suite_digest(data, dumped, digest_sources(), check_constants())

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
- the upper-case constants of the check modules and the outside thresholds the checks use
  (``check_constants``), so a constant patched at run time changes it too.

``tests/test_evals_suite.py`` pins each released version's digest (append only): a change without
a new ``suite_version`` fails there.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from importlib import resources
from types import ModuleType
from typing import Any, Final, Literal

from mowl import workout
from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent import songs
from classbeat.evals import checks, music, pick
from classbeat.evals.cases import Case
from classbeat.evals.pick import PickCase

Group = Literal["design", "songs"]
GROUPS: Final[tuple[Group, ...]] = ("design", "songs")
# Roadmap section 11 / ADR 0012 section 4: each category and the stage groups it may hold.
CATEGORY_GROUPS: Final[dict[str, frozenset[str]]] = {
    "standard": frozenset({"design"}),
    "music_sync": frozenset({"design"}),
    "games_ftp": frozenset({"design"}),
    "revisions": frozenset({"design"}),
    "song_choice": frozenset({"songs"}),
    "missing_music": frozenset({"design", "songs"}),
    "adversarial": frozenset({"design", "songs"}),
    "cost": frozenset({"design", "songs"}),
}
DIGEST_SOURCES: Final = (
    "cases.py", "checks.py", "music.py", "pick.py", "runner.py", "suite.py", "gate.py",
)  # fmt: skip
CONSTANT_MODULES: Final[tuple[ModuleType, ...]] = (checks, music, pick)
CONSTANT_NAME: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
CASE_NAME = r"^[a-z0-9_]{1,40}$"


class SuiteError(ValueError):
    """The suite cannot be loaded; the message names the rule, never file content."""


class GroupRules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    median_cost_micros: int = Field(ge=0)  # the median cost per class of the group's runs
    job_ceiling_micros: int = Field(ge=0)  # no run may cost more

    @model_validator(mode="after")
    def _median_below_ceiling(self) -> "GroupRules":
        if self.median_cost_micros > self.job_ceiling_micros:
            raise ValueError("a median cost threshold above its job ceiling says nothing")
        return self


class Rules(BaseModel):
    """ADR 0012 section 4.3, every rule's parameters (the defaults live in the manifest)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    repeat: int = Field(ge=1, le=10)  # every case runs at least this many times
    pass_share: float = Field(gt=0, le=1)  # of the runs of a category not in every_run
    every_run_categories: list[str]  # every run of these categories passes
    every_run_checks: list[str] = Field(min_length=1)  # these checks pass in every run
    # An outcome that is a failed check (a run stopped by a limit failed ``limits``).
    outcome_checks: dict[str, str]
    not_run_outcomes: list[str] = Field(min_length=1)  # an outage: counted as not run
    not_run_share_max: float = Field(ge=0, lt=1)  # above it: ``incomplete``


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    suite_version: int = Field(ge=1)
    digest: str = Field(pattern=HEX64.pattern)
    rules: Rules
    groups: dict[Group, GroupRules]
    zone7_tss_margin: float = Field(ge=0, le=1)  # section 7: widens a zone 7 class's TSS bound
    # category -> stage group -> case names
    categories: dict[str, dict[Group, list[str]]]

    @model_validator(mode="after")
    def _consistent(self) -> "Manifest":
        if set(self.groups) != set(GROUPS):
            raise ValueError("the manifest sets the rules of both stage groups")
        if set(self.categories) != set(CATEGORY_GROUPS):
            raise ValueError("the manifest lists exactly the categories of ADR 0012 section 4")
        seen: set[str] = set()
        for category, by_group in self.categories.items():
            if not set(by_group) <= CATEGORY_GROUPS[category]:
                raise ValueError(f"{category} cannot hold cases of that stage group")
            for names in by_group.values():
                for name in names:
                    if not re.fullmatch(CASE_NAME, name):
                        raise ValueError(f"{category}: a case name is not a case name")
                    if name in seen:
                        raise ValueError(f"{name} is listed twice: a case has one category")
                    seen.add(name)
        rules = self.rules
        if not set(rules.every_run_categories) <= set(CATEGORY_GROUPS):
            raise ValueError("every_run_categories names an unknown category")
        if set(rules.not_run_outcomes) & ({"submitted"} | set(rules.outcome_checks)):
            raise ValueError("a not-run outcome cannot also be a result")
        return self

    def case_index(self) -> dict[str, tuple[str, Group]]:
        """Each case's category and stage group."""
        return {
            name: (category, group)
            for category, by_group in self.categories.items()
            for group, names in by_group.items()
            for name in names
        }

    def cases_of(self, category: str, group: Group) -> list[str]:
        return list(self.categories[category].get(group, []))


@dataclass(frozen=True)
class Suite:
    manifest: Manifest
    design_cases: dict[str, Case]
    song_cases: dict[str, PickCase]

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
        return value.pattern  # pyright: ignore[reportUnknownMemberType]
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
    return {
        name: (folder / name).read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
        for name in DIGEST_SOURCES
    }


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


def _load_case(name: str, folder: str, text: str) -> Case | PickCase:
    data = json.loads(text)
    model = PickCase if isinstance(data, dict) and "catalogue" in data else Case
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
    dumped: dict[str, dict[str, Any]] = {}
    for name, (_, group) in sorted(manifest.case_index().items()):
        if name not in files:
            raise SuiteError(f"the manifest names {name}, which has no case file")
        case = _load_case(name, *files[name])
        if isinstance(case, PickCase) != (group == "songs"):
            raise SuiteError(f"{name} is listed in the {group} group but is not a {group} case")
        if isinstance(case, PickCase):
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
    return Suite(manifest, design, song)


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

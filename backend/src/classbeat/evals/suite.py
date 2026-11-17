"""The regression suite (ADR 0012 section 4): ``suite/manifest.json``, its cases and its digest.

The **suite** is every case the manifest names, by category and stage group: the smoke cases
(``smoke/``, design and revise), the song cases (``smoke_pick/``) and the suite's own
(``suite/<category>/*.json``; a file with a ``catalogue`` is a song case, else a design case).
A case belongs to exactly one category. The manifest also holds the gate's rules and thresholds
(``classbeat.evals.gate``) and the zone 7 TSS margin (section 7).

``suite_version`` is bumped by any change to a case, a check, a threshold or the manifest. The
manifest stores the **digest** of all of them; ``load_suite`` refuses a suite whose digest is not
the stored one, so a changed suite can never run (or be recorded) under an older version. The
digest covers (its parts are ``classbeat.evals.suite_sources``):

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

import json
from dataclasses import dataclass, field
from fractions import Fraction
from importlib import resources
from typing import Any

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
from classbeat.evals.suite_sources import (  # re-exported: the digest's parts live there (#1140)
    DIGEST_SOURCES as DIGEST_SOURCES,
)
from classbeat.evals.suite_sources import (
    RULE_SOURCES as RULE_SOURCES,
)
from classbeat.evals.suite_sources import check_constants, digest_sources, suite_digest


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

"""The suite's verdict (ADR 0012 section 4.3): ``evaluate(results, manifest) -> Verdict``.

A pure function of a results document (format 2, ``runner.suite_results``) and the manifest:
no clock, no settings, no database. The verdict is **per stage group** (``design``, ``songs``),
for each group in the file, with per category the runs, passes, not-run runs and the rules that
failed. Rules, all parameters from the manifest:

- ``repeats``: every case of the category has at least ``rules.repeat`` runs (distinct repeat
  indexes; a not-run run is a run that was attempted);
- ``every_run``: in an ``every_run_categories`` category (``adversarial``) every counted run
  passes;
- each name in ``every_run_checks`` (``limits``, ``forbidden_text``): in every category, no
  counted run has that check failed (an outcome in ``outcome_checks`` counts as that check
  failed: a run stopped by a limit failed ``limits``);
- ``pass_share``: in every other category at least ``rules.pass_share`` of the counted runs
  pass;
- ``each_case_once``: every case with counted runs passes at least once;
- ``median_cost`` (group): the median cost of the group's counted runs at most the group's
  ``median_cost_micros``; ``job_ceiling`` (group): no run costs more than ``job_ceiling_micros``;
- a run whose outcome is in ``not_run_outcomes`` (``model_unavailable``, ``outbound_blocked``)
  is **not run**: neither a pass nor a fail. More than ``not_run_share_max`` of such runs, in the
  file or in the group, or a case none of whose runs counted, makes the group ``incomplete``: an
  outage cannot fail a model and too few runs cannot pass it.

What makes a whole file fail (every group ``failed``, whatever its runs): another
``suite_version`` or manifest digest than the manifest's, a case the manifest does not name, a
run whose category is not its case's, the same run (case and repeat) twice, or a row whose
``passed`` is not what its outcome and checks say. A run passes when it ended ``submitted`` with
at least one check and every check passed; the row's own ``passed`` is never trusted alone.

Precedence: a file failure gives ``failed``; else ``incomplete``; else ``failed`` when any rule
failed; else ``passed``. ``GroupVerdict.as_json()`` is what ``model_eval_results.verdict``
stores (ADR 0012 section 5): a JSON object whose ``passed`` is a JSON boolean, ``false`` for
``incomplete``. The verdict is never read from a file: whoever needs it computes it.
"""

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from classbeat.evals.suite import GROUPS, Group, Manifest, fraction

RESULTS_FORMAT_2 = 2
Status = Literal["passed", "failed", "incomplete"]


class ResultsInvalid(ValueError):
    """Not a format-2 results document; the message names the rule, never file content."""


class _CheckRow(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str
    ok: bool


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    case: str
    repeat: int = Field(ge=0)
    category: str
    outcome: str
    passed: bool
    cost_micros: int = Field(ge=0)
    checks: list[_CheckRow]

    @property
    def ok(self) -> bool:
        return self.outcome == "submitted" and bool(self.checks) and all(c.ok for c in self.checks)


class _Doc(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    results_format: int
    suite_version: int
    manifest_digest: str
    results: list[_Row]


@dataclass(frozen=True)
class CategoryVerdict:
    runs: int
    passes: int
    not_run: int
    rules_failed: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "passes": self.passes,
            "not_run": self.not_run,
            "rules_failed": list(self.rules_failed),
        }


@dataclass(frozen=True)
class GroupVerdict:
    group: Group
    status: Status
    suite_version: int
    manifest_digest: str
    runs: int
    passes: int
    not_run: int
    median_cost_micros: int | None  # rounded up; None: no counted run
    rules_failed: tuple[str, ...]  # the group's own and the file's
    categories: dict[str, CategoryVerdict] = field(
        default_factory=lambda: dict[str, CategoryVerdict]()
    )

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def as_json(self) -> dict[str, Any]:
        """The stored verdict: an object with a JSON boolean ``passed`` (false unless passed)."""
        return {
            "passed": self.passed,
            "status": self.status,
            "group": self.group,
            "suite_version": self.suite_version,
            "manifest_digest": self.manifest_digest,
            "runs": self.runs,
            "passes": self.passes,
            "not_run": self.not_run,
            "median_cost_micros": self.median_cost_micros,
            "rules_failed": list(self.rules_failed),
            "categories": {k: v.as_json() for k, v in self.categories.items()},
        }


@dataclass(frozen=True)
class Verdict:
    suite_version: int
    manifest_digest: str
    groups: dict[Group, GroupVerdict]  # only the groups the file has runs of


def _check_failed(row: _Row, check: str, outcome_checks: Mapping[str, str]) -> bool:
    if outcome_checks.get(row.outcome) == check:
        return True
    return any(c.name == check and not c.ok for c in row.checks)


def _file_rules(doc: _Doc, manifest: Manifest) -> list[str]:
    index = manifest.case_index()
    rules: list[str] = []
    if doc.suite_version != manifest.suite_version:
        rules.append("suite_version")
    if doc.manifest_digest != manifest.digest:
        rules.append("manifest_digest")
    seen: set[tuple[str, int]] = set()
    for row in doc.results:
        if row.case not in index:
            rules.append("unknown_case")
            continue
        if row.category != index[row.case][0]:
            rules.append("category_mismatch")
        if (row.case, row.repeat) in seen:
            rules.append("duplicate_run")
        seen.add((row.case, row.repeat))
        if row.passed != row.ok:
            rules.append("inconsistent_row")
    return list(dict.fromkeys(rules))


def _over(share_max: float, part: int, whole: int) -> bool:
    return whole > 0 and fraction(share_max) * whole < part


def _group(
    group: Group,
    rows: list[_Row],
    manifest: Manifest,
    file_rules: list[str],
    file_over: bool,
) -> GroupVerdict:
    r = manifest.rules
    not_run_outcomes = set(r.not_run_outcomes)
    index = manifest.case_index()
    counted = [row for row in rows if row.outcome not in not_run_outcomes]
    incomplete = file_over or _over(r.not_run_share_max, len(rows) - len(counted), len(rows))
    categories: dict[str, CategoryVerdict] = {}
    for category in manifest.categories:
        names = manifest.cases_of(category, group)
        if not names:
            continue
        mine = [row for row in rows if index[row.case][0] == category]
        live = [row for row in mine if row.outcome not in not_run_outcomes]
        passes = sum(row.ok for row in live)
        failed: list[str] = []
        if any(len({row.repeat for row in mine if row.case == n}) < r.repeat for n in names):
            failed.append("repeats")
        if category in r.every_run_categories:
            if passes < len(live):
                failed.append("every_run")
        elif live and passes < fraction(r.pass_share) * len(live):
            failed.append("pass_share")
        for check in r.every_run_checks:
            if any(_check_failed(row, check, r.outcome_checks) for row in live):
                failed.append(check)
        never_passed = False
        for name in names:
            runs = [row for row in mine if row.case == name]
            ran = [row for row in runs if row.outcome not in not_run_outcomes]
            if runs and not ran:
                incomplete = True  # every run of the case was an outage
            elif ran and not any(row.ok for row in ran):
                never_passed = True
        if never_passed:
            failed.append("each_case_once")
        categories[category] = CategoryVerdict(
            len(mine), passes, len(mine) - len(live), tuple(failed)
        )
    rules = list(file_rules)
    limits = manifest.groups[group]
    median = None
    if counted:
        exact = statistics.median(row.cost_micros for row in counted)
        median = math.ceil(exact)
        if exact > limits.median_cost_micros:
            rules.append("median_cost")
    if any(row.cost_micros > limits.job_ceiling_micros for row in rows):
        rules.append("job_ceiling")
    status: Status
    if file_rules:
        status = "failed"
    elif incomplete:
        status = "incomplete"
    elif rules or any(c.rules_failed for c in categories.values()):
        status = "failed"
    else:
        status = "passed"
    return GroupVerdict(
        group,
        status,
        manifest.suite_version,
        manifest.digest,
        len(rows),
        sum(row.ok for row in counted),
        len(rows) - len(counted),
        median,
        tuple(rules),
        categories,
    )


def evaluate(results: Mapping[str, Any], manifest: Manifest) -> Verdict:
    """The verdict of a format-2 results document against the manifest (see the module)."""
    if results.get("results_format") != RESULTS_FORMAT_2:
        raise ResultsInvalid("not a results file of format 2")
    try:
        doc = _Doc.model_validate(dict(results))
    except ValidationError:
        raise ResultsInvalid("the results file does not have the format-2 fields") from None
    file_rules = _file_rules(doc, manifest)
    index = manifest.case_index()
    known = [row for row in doc.results if row.case in index]
    not_run = sum(row.outcome in manifest.rules.not_run_outcomes for row in doc.results)
    file_over = _over(manifest.rules.not_run_share_max, not_run, len(doc.results))
    groups: dict[Group, GroupVerdict] = {}
    for group in GROUPS:
        rows = [row for row in known if index[row.case][1] == group]
        if rows:
            groups[group] = _group(group, rows, manifest, file_rules, file_over)
    return Verdict(manifest.suite_version, manifest.digest, groups)

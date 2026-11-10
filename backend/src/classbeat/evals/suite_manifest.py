"""The suite's manifest (ADR 0012 section 4): its categories, stage groups, gate rules and
thresholds, validated. Split out of ``classbeat.evals.suite``, which loads the cases and digests
the whole; ``suite`` re-exports these names. Pure: no I/O.
"""

import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.evals.song_checks import Band

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
    "class_patterns": frozenset({"design"}),
    "running": frozenset({"design"}),  # ADR 0012 amendment of 2026-11-03 (ADR 0018, issue #465)
    # ADR 0007 amendment of 2026-11-03 (soundless classes, issue #531); suite version 11
    "soundless": frozenset({"design"}),
    # ADR 0029 section 4 (the interpret stage, issue #880); suite version 17
    "interpret": frozenset({"design"}),
    "adversarial": frozenset({"design", "songs"}),
    "cost": frozenset({"design", "songs"}),
}
HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
CASE_NAME = r"^[a-z0-9_]{1,40}$"


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
    # section 4.1: the BPM band of each role (a role with none is not judged)
    bpm_bands: dict[str, Band] = Field(default_factory=lambda: dict[str, Band]())
    # category -> stage group -> case names
    categories: dict[str, dict[Group, list[str]]]
    # A song case whose right end is a failure category, not a list (``pool_too_small``: too few
    # usable tracks, nothing stored; ADR 0012 section 4 ``missing_music``). The gate counts such a
    # run as passed when it ended there and its checks hold; its case file says the same
    # (``expect.outcome``), and ``load_suite`` refuses a pair that disagrees.
    expected_outcomes: dict[str, Literal["pool_too_small"]] = Field(
        default_factory=lambda: dict[str, Literal["pool_too_small"]]()
    )

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
        index = self.case_index()
        for name in self.expected_outcomes:
            if name not in index or index[name][1] != "songs":
                raise ValueError("expected_outcomes names a song case of the manifest")
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

    def expected_outcome(self, name: str) -> str:
        return self.expected_outcomes.get(name, "submitted")

    def cases_of(self, category: str, group: Group) -> list[str]:
        return list(self.categories[category].get(group, []))

"""The song-picking case model (ADR 0008 section 3; issue #179): what a ``smoke_pick/`` or
``suite/`` song case file holds, and the plan it builds. Split out of ``classbeat.evals.pick``,
which runs a case and re-exports these names. Pure: no I/O besides reading the case files.
"""

from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.brief import Brief
from classbeat.agent.songs import PickPlan
from classbeat.evals.pick_world import CatalogueSpec, ReplaceSpec, kept_songs


class PickExpect(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    forbidden_text: list[str] = Field(default_factory=lambda: list[str]())  # in the note
    years: list[int] | None = Field(default=None, min_length=2, max_length=2)  # the era asked for
    # The expected end of the run: a list (``submitted``) or the failure the case is about
    # (``pool_too_small``: too few usable tracks, nothing stored). The manifest lists the cases
    # that expect the latter (``Manifest.expected_outcomes``); the gate reads it from there.
    outcome: Literal["submitted", "pool_too_small"] = "submitted"
    max_turns: int | None = Field(default=None, ge=1)  # the case's own bounds (cost category)
    max_cost_micros: int | None = Field(default=None, ge=0)
    # #1035: the choose stage's pool carried every track's section energy (``song_checks``).
    sections_shown: bool = False
    # #1040: the submitted list has no tempo jump between two songs of the same role.
    tempo_flow: bool = False

    @model_validator(mode="after")
    def _years_ordered(self) -> "PickExpect":
        if self.years is not None and self.years[0] > self.years[1]:
            raise ValueError("the era runs from the earlier year to the later")
        return self


class PickCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    about: str = Field(min_length=1, max_length=200)
    brief: Brief
    catalogue: CatalogueSpec
    replace: ReplaceSpec | None = None
    expect: PickExpect = Field(default_factory=PickExpect)
    # Run the song-quality checks of ADR 0012 section 4.1 (``song_checks``); they need the
    # manifest's band table, which the caller passes to ``run_pick_case``.
    quality: bool = False


def load_pick_cases() -> list[PickCase]:
    folder = resources.files("classbeat.evals") / "smoke_pick"
    cases = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        if not item.name.endswith(".json"):
            continue
        case = PickCase.model_validate_json(item.read_text("utf-8"))
        if item.name != f"{case.name}.json":
            raise ValueError(f"{item.name}: the file is named after its case, {case.name}")
        cases.append(case)
    return cases


def plan_for(case: PickCase) -> PickPlan:
    if case.replace is None:
        return PickPlan.build(case.brief)
    spec = case.replace
    return PickPlan.build(
        case.brief,
        replace=[spec.seed_roles[s] for s in sorted(spec.replace)],
        kept=kept_songs(spec),
    )

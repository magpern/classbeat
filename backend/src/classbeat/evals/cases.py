"""The smoke cases (ADR 0006 section 8): JSON files in ``smoke/``, one per case.

A case is the brief, the synthetic music and what the submitted draft must satisfy. Everything
the brief asks for is derived from the brief itself (the length, the intensity, the number of
climbs, sprints and races, the FTP test); ``expect`` only holds what a case sets differently from
the defaults of ``classbeat.evals.checks`` or adds (text the draft must not contain).

JSON, not YAML: the project has no YAML dependency and the files are generated and read by code.
"""

from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from classbeat.agent.schemas import Brief, BriefText, WorkoutDraft
from classbeat.evals.music import MusicSpec

Range = tuple[float, float]


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # "asked": within 5 % of the brief's length and not longer than the music; "music": the
    # music is shorter than the ask, so the draft fills 90 % to 100 % of the music instead.
    duration: Literal["asked", "music"] = "asked"
    hard_share: Range | None = None  # share of the time in zone 5 or above, instead of the default
    tss: Range | None = None  # instead of the default per hour of the intensity
    changes_on_music_min: float = Field(default=0.75, ge=0, le=1)
    allow_issues: list[str] = Field(default_factory=lambda: list[str]())  # quality issue codes
    forbidden_text: list[str] = Field(default_factory=lambda: list[str]())  # case-insensitive
    max_turns: int | None = Field(default=None, ge=1)
    max_cost_micros: int | None = Field(default=None, ge=0)


class ReviseSpec(BaseModel):
    """A revise case (ADR 0007 section 3): the class is ``brief`` and ``music`` as for a design,
    ``base`` is the draft it stands at (as stored: fitted), ``earlier_requests`` the user's
    requests that led to it and ``request`` the new one. The checks run on the revised draft
    against the case's brief; it must also differ from ``base``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: BriefText = Field(min_length=1)
    earlier_requests: list[BriefText] = Field(default_factory=lambda: list[str](), max_length=10)
    base: WorkoutDraft


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    about: str = Field(min_length=1, max_length=200)
    brief: Brief
    music: MusicSpec
    expect: Expect = Field(default_factory=Expect)
    revise: ReviseSpec | None = None  # a revise case; none: a design case


def load_cases() -> list[Case]:
    """Every case file of the package, ordered by name; a file's name is its case's name."""
    folder = resources.files("classbeat.evals") / "smoke"
    cases = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        if not item.name.endswith(".json"):
            continue
        case = Case.model_validate_json(item.read_text("utf-8"))
        if item.name != f"{case.name}.json":
            raise ValueError(f"{item.name}: the file is named after its case, {case.name}")
        cases.append(case)
    return cases

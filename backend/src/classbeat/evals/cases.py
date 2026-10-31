"""The smoke cases (ADR 0006 section 8): JSON files in ``smoke/``, one per case.

A case is the brief, the synthetic music and what the submitted draft must satisfy. Everything
the brief asks for is derived from the brief itself (the length, the intensity, the number of
climbs, sprints and races, the FTP test); ``expect`` only holds what a case sets differently from
the defaults of ``classbeat.evals.checks`` or adds (text the draft must not contain).

JSON, not YAML: the project has no YAML dependency and the files are generated and read by code.
"""

from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.schemas import Brief, BriefText, Game, WorkoutDraft
from classbeat.evals.music import MusicSpec

Range = tuple[float, float]


class MusicSyncExpect(BaseModel):
    """The suite's ``music_sync`` checks (ADR 0012 section 4), on the fitted spec and the music
    map: every sprint starts within a bar of a drop (when the music has one), and no zone changes
    mid-phrase where a phrase boundary, drop, breakdown or track change is within 8 s."""

    model_config = ConfigDict(extra="forbid", strict=True)

    sprints_on_drops: bool = True
    phrase_changes: bool = True
    max_off_phrase: int = Field(default=0, ge=0)  # zone changes allowed mid-phrase


class RevisionExpect(BaseModel):
    """What a ``revisions`` case asked for (ADR 0012 section 4), checked against its base:

    - ``more_sprints``: at least ``by`` more sprints than the base;
    - ``easier``: a TSS at most 95 % of the base's and no larger share at zone 5 or above;
    - ``shorter``: within 5 % of ``length_min``;
    - ``climb_moved``: a climb is the first (``climb_at: start``) or last main segment.

    Everything the request did not touch stays: the length within 5 % of the base's (unless
    ``shorter``), the cooldown within 60 s of the base's, and the other kinds of effort: for
    ``more_sprints`` the climbs, races and FTP tests of the base, for ``climb_moved`` those and
    the sprints."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["more_sprints", "easier", "shorter", "climb_moved"]
    by: int = Field(default=1, ge=1, le=10)
    length_min: int | None = Field(default=None, ge=10, le=180)
    climb_at: Literal["start", "end"] | None = None

    @model_validator(mode="after")
    def _params(self) -> "RevisionExpect":
        if (self.kind == "shorter") != (self.length_min is not None):
            raise ValueError("length_min is the asked length of a shorter revision, and only that")
        if (self.kind == "climb_moved") != (self.climb_at is not None):
            raise ValueError("climb_at is where a moved climb goes, and only that")
        return self


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
    # The suite's own (ADR 0012 section 4); unset, the check is not run (the smoke set's).
    games: list[Game] = Field(default_factory=lambda: list[Game]())  # each exactly once
    ftp_warmup_min_sec: int | None = Field(default=None, ge=60, le=1800)  # before the FTP test
    music_sync: MusicSyncExpect | None = None
    revision: RevisionExpect | None = None


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

    @model_validator(mode="after")
    def _revision_needs_a_base(self) -> "Case":
        if self.expect.revision is not None and self.revise is None:
            raise ValueError("expect.revision is for a revise case")
        return self


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

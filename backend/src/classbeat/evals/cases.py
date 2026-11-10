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

from classbeat.agent.brief import Brief
from classbeat.agent.schemas import BriefText, Game, RunningDraft, WorkoutDraft
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
    - ``climb_moved``: a climb is the first (``climb_at: start``) or last main segment;
    - a game edit (ADR 0029 section 2, G11), ``at`` being the base's interval (counted from 0 over
      the whole class): ``game_added`` (a ``game`` inserted before ``at``), ``game_replaced``
      (that interval becomes ``game``, same length and zone), ``game_type`` (its game becomes
      ``game``), ``game_removed``, ``game_length`` (``sec``) and ``game_zone`` (``zone``). Each
      changes only that interval (``classbeat.evals.game_checks``).

    Everything the request did not touch stays: the length within 5 % of the base's (unless
    ``shorter``), the cooldown within 60 s of the base's, and the other kinds of effort: for
    ``more_sprints`` the climbs, races and FTP tests of the base, for ``climb_moved`` those and
    the sprints."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal[
        "more_sprints",
        "easier",
        "shorter",
        "climb_moved",
        "game_added",
        "game_replaced",
        "game_type",
        "game_removed",
        "game_length",
        "game_zone",
    ]
    by: int = Field(default=1, ge=1, le=10)
    length_min: int | None = Field(default=None, ge=10, le=180)
    climb_at: Literal["start", "end"] | None = None
    at: int | None = Field(default=None, ge=0, le=1000)
    game: Game | None = None
    sec: int | None = Field(default=None, ge=10, le=3600)
    zone: int | None = Field(default=None, ge=1, le=7)

    @model_validator(mode="after")
    def _params(self) -> "RevisionExpect":
        if (self.kind == "shorter") != (self.length_min is not None):
            raise ValueError("length_min is the asked length of a shorter revision, and only that")
        if (self.kind == "climb_moved") != (self.climb_at is not None):
            raise ValueError("climb_at is where a moved climb goes, and only that")
        edit = self.kind.startswith("game_")
        wants = {
            "at": edit,
            "game": self.kind in ("game_added", "game_replaced", "game_type"),
            "sec": self.kind == "game_length",
            "zone": self.kind == "game_zone",
        }
        for name, needed in wants.items():
            if (getattr(self, name) is not None) != needed:
                raise ValueError(f"{name} is for the game edits that name it, and only those")
        return self


class PatternExpect(BaseModel):
    """A ``class_patterns`` case (ADR 0017 section 5), judged on the backend's own rule hits for
    the fitted draft (``classbeat.design.rules.evaluate``), never on model text:

    - ``follows``: the brief asks for nothing unusual; the draft must have **no ``warn`` hit**
      (this measures whether the rules section helps);
    - ``deviates``: the brief asks for what a rule discourages and the brief wins; the run must
      end submitted with every asked feature present (``asked_present``), and a hit is allowed
      but must carry ``explained_by_brief`` (``hits_explained``). A model that "corrects" the
      draft away from the brief fails ``asked_present``.

    The asked features (all optional, any number): the first zone 4 or above starts at most
    ``hard_start_by_sec`` into the class; the warm-up segments add up to at most
    ``warmup_max_sec``; at least ``sprints_min`` bursts; no zone above ``max_zone`` anywhere."""

    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["follows", "deviates"]
    hard_start_by_sec: int | None = Field(default=None, ge=0, le=3600)
    warmup_max_sec: int | None = Field(default=None, ge=60, le=3600)
    sprints_min: int | None = Field(default=None, ge=1, le=50)
    max_zone: int | None = Field(default=None, ge=1, le=7)

    @model_validator(mode="after")
    def _asks(self) -> "PatternExpect":
        asked = (self.hard_start_by_sec, self.warmup_max_sec, self.sprints_min, self.max_zone)
        if (self.mode == "deviates") != any(a is not None for a in asked):
            raise ValueError("a deviates case names what the brief asked for, a follows case not")
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
    # A request that names no type (ADR 0029): exactly one game, of any type the catalogue offers.
    game_any: bool = False
    ftp_warmup_min_sec: int | None = Field(default=None, ge=60, le=1800)  # before the FTP test
    music_sync: MusicSyncExpect | None = None
    revision: RevisionExpect | None = None
    patterns: PatternExpect | None = None  # the ``class_patterns`` category's
    # A running case (ADR 0018, issue #465): at least this many seconds at gradient 1 (an incline
    # the brief asked for); unset, the incline is not judged. Running cases only.
    incline_min_sec: int | None = Field(default=None, ge=1, le=10800)


class ReviseSpec(BaseModel):
    """A revise case (ADR 0007 section 3): the class is ``brief`` and ``music`` as for a design,
    ``base`` is the draft it stands at (as stored: fitted), ``earlier_requests`` the user's
    requests that led to it and ``request`` the new one. The checks run on the revised draft
    against the case's brief; it must also differ from ``base``. A running case's ``base`` is a
    ``RunningDraft`` and a cycling case's a ``WorkoutDraft`` (``Case`` checks the pair)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: BriefText = Field(min_length=1)
    earlier_requests: list[BriefText] = Field(default_factory=lambda: list[str](), max_length=10)
    base: WorkoutDraft | RunningDraft
    # "songs_requested" (issue #278): the request only asks for other songs, so the stage ends
    # with songs_requested and no draft; the case passes only if it does.
    outcome: Literal["draft", "songs_requested"] = "draft"


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    about: str = Field(min_length=1, max_length=200)
    brief: Brief
    # A soundless case (ADR 0007 amendment of 2026-11-03, issue #531) is a cycling class with no
    # music: the runner gives it the soundless stages and no music at all. It may not name
    # ``music``; the default fills the field only so its type stays one for every case (nothing
    # builds or reads it for a soundless case), and every other case must name its music.
    soundless: bool = False
    music: MusicSpec = Field(default_factory=lambda: MusicSpec(tracks=1))
    expect: Expect = Field(default_factory=Expect)
    revise: ReviseSpec | None = None  # a revise case; none: a design case

    @model_validator(mode="after")
    def _soundless_is_cycling_and_unjudged_on_music(self) -> "Case":
        if ("music" in self.model_fields_set) == self.soundless:
            raise ValueError("a soundless case names no music, any other case names its music")
        if not self.soundless:
            return self
        e = self.expect
        if self.brief.sport != "cycling":
            raise ValueError("a soundless case is a cycling class")
        if e.duration != "asked" or e.music_sync is not None or e.patterns is not None:
            raise ValueError("a soundless case is judged on the asked length, with no music checks")
        if e.changes_on_music_min != Expect().changes_on_music_min or e.allow_issues:
            raise ValueError("a soundless case has no quality report: no music or issue allowance")
        if self.revise is not None and self.revise.outcome != "draft":
            raise ValueError("a soundless revise case ends with a draft (it has no songs to ask)")
        if self.revise is not None and isinstance(self.revise.base, RunningDraft):
            raise ValueError("a soundless case's base draft is a cycling draft")
        return self

    @model_validator(mode="after")
    def _revision_needs_a_base(self) -> "Case":
        if self.expect.revision is not None and self.revise is None:
            raise ValueError("expect.revision is for a revise case")
        if self.expect.patterns is not None and self.revise is not None:
            raise ValueError("expect.patterns is for a design case")
        b = self.brief
        running = b.sport == "running"
        if self.revise is not None and isinstance(self.revise.base, RunningDraft) != running:
            raise ValueError("a revise case's base draft is of its brief's sport")
        if self.expect.incline_min_sec is not None and not running:
            raise ValueError("expect.incline_min_sec is for a running case")
        if self.expect.game_any and self.expect.games:
            raise ValueError("a case names its games or accepts any one, not both")
        if running and (
            self.expect.games or self.expect.game_any or self.expect.ftp_warmup_min_sec is not None
        ):
            raise ValueError("a running case has no games and no FTP test")
        if self.expect.patterns is not None and self.expect.patterns.mode == "follows":
            unusual = b.sprints or b.races or b.ftp_test or b.style or b.text or b.climbs
            if unusual or b.intensity != "moderate" or b.class_type is None:
                raise ValueError(
                    "a follows case is a moderate brief of an explicit class type that asks "
                    "for nothing else"
                )
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

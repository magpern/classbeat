"""The brief of a class as the model may see it (ADR 0006 section 7, ADR 0029 section 4).

The guided options and the user's free text, nothing else. ``games`` and ``explicit`` (ADR 0029
section 4, MA1b-1) hold **closed enums and numbers only**: a placement is a position or a
relation to a kind of interval and its number, never a phrase of the user's or of a model, so no
model-produced text can come back to a model through the brief.

- ``games``: at most 6 games the instructor stated, each a type (one of the four, or ``any``), an
  optional closed placement and an optional length. Cycling only.
- ``explicit``: at most 20 paths of the values the instructor stated and the policy never
  replaces (``classbeat.design.request_policy``); the others are assumptions.

Both are left out of the serialized brief when empty, so every brief stored before them stays
byte-identical (the evals, the size guard and every reader see what they saw).
"""

from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

from classbeat.agent.schemas import STRICT, BriefText, Game, Style

Sport = Literal["cycling", "running"]
SPORTS: tuple[str, ...] = ("cycling", "running")
# ADR 0018 section 4: the bike-only knobs of the brief. A running brief may not name them (not even
# at their defaults), and its stored and sized form leaves them out. ``games`` joins them
# (ADR 0029).
CYCLING_ONLY_BRIEF: tuple[str, ...] = ("climbs", "sprints", "races", "ftp_test", "games")

MAX_GAMES = 6
MAX_EXPLICIT = 20
# The paths ``explicit`` may name: a brief field the instructor can state, or one of its games.
EXPLICIT_PATH = (
    r"^(sport|length_min|intensity|climbs|sprints|races|ftp_test|class_type|games\.[0-5])$"
)
ExplicitPath = Annotated[str, StringConstraints(pattern=EXPLICIT_PATH)]
_CYCLING_ONLY_PATHS = ("climbs", "sprints", "races", "ftp_test", "games.")

Position = Literal["start", "early", "middle", "late", "before_cooldown"]
Relation = Literal["after", "replaces", "in"]
Anchor = Literal["climb", "sprint", "race", "interval", "warmup", "cooldown"]


class PositionPlacement(BaseModel):
    model_config = STRICT

    position: Position


class RelativePlacement(BaseModel):
    """ "After climb 2": a relation to the ``ordinal``-th ``anchor`` of the class. The class does
    not exist yet when the request is read, so the hint is closed here and mapped onto the draft
    by ``classbeat.agent.explicit_check``."""

    model_config = STRICT

    relation: Relation
    anchor: Anchor
    ordinal: int = Field(ge=1, le=20)


class BriefGame(BaseModel):
    """A game the instructor stated. ``any`` lets the model pick one of the four by its advice;
    the length is a request (the library's 10 s floor is the policy's, not this schema's)."""

    model_config = STRICT

    type: Game | Literal["any"]
    placement: PositionPlacement | RelativePlacement | None = None
    duration_sec: int | None = Field(default=None, ge=1, le=600)


class Brief(BaseModel):
    """The new-class form's brief (ADR 0006 section 7).

    ``sport`` (ADR 0018 section 1): ``cycling`` or ``running``. A brief without it is cycling (every
    brief stored before it), and a cycling brief's ``model_dump`` leaves it out, so what the evals,
    the size guard and every existing reader see of a cycling brief is byte-identical. The class's
    stored brief writes it explicitly (``stored``). A running brief refuses the bike-only knobs
    (``CYCLING_ONLY_BRIEF``) and the ``ftp_test`` class type."""

    model_config = STRICT

    sport: Sport = "cycling"
    length_min: int = Field(ge=10, le=180)
    intensity: Literal["easy", "moderate", "hard"] = "moderate"
    climbs: int = Field(default=0, ge=0, le=10)
    sprints: int = Field(default=0, ge=0, le=20)
    races: int = Field(default=0, ge=0, le=5)
    ftp_test: Literal[3, 5, 20] | None = None
    style: Style = ""
    text: BriefText = ""
    # ADR 0017 section 2: the user's own class type; absent = derived (``design.selection``). A
    # closed set, kept equal to ``design.rules.enums.CLASS_TYPES`` by a test (this module cannot
    # import it: ``classbeat.design`` imports the agent).
    class_type: Literal["plain", "interval", "hiit", "journey", "ftp_test"] | None = None
    games: list[BriefGame] = Field(default_factory=lambda: list[BriefGame](), max_length=MAX_GAMES)
    explicit: list[ExplicitPath] = Field(
        default_factory=lambda: list[ExplicitPath](), max_length=MAX_EXPLICIT
    )

    @model_validator(mode="after")
    def _running_has_no_bike_knobs(self) -> Self:
        if self.sport != "running":
            return self
        named = sorted(set(CYCLING_ONLY_BRIEF) & self.model_fields_set)
        if named:
            raise ValueError(f"a running brief has no {', '.join(named)}")
        if self.class_type == "ftp_test":
            raise ValueError("a running brief has no ftp_test class type")
        return self

    @model_validator(mode="after")
    def _explicit_names_what_the_brief_has(self) -> Self:
        if len(set(self.explicit)) != len(self.explicit):
            raise ValueError("explicit names a path once")
        for path in self.explicit:
            if path.startswith("games.") and int(path[6:]) >= len(self.games):
                raise ValueError(f"explicit names {path}, which the brief does not have")
            if self.sport == "running" and path.startswith(_CYCLING_ONLY_PATHS):
                raise ValueError(f"a running brief has no {path}")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """An absent ``class_type`` is not written, so a stored or sized brief of today is
        byte-identical (the ``classes.brief`` size guard is unchanged). Nor is ``sport`` of a
        cycling brief (ADR 0018: absent is cycling), nor an empty ``games`` or ``explicit``
        (ADR 0029); a running brief is written with its sport and without the bike-only knobs,
        so it validates again as written."""
        out: dict[str, Any] = handler(self)
        if out.get("class_type") is None:
            out.pop("class_type", None)
        for key in ("games", "explicit"):
            if not out.get(key):
                out.pop(key, None)
        if out.get("sport") == "running":
            for key in CYCLING_ONLY_BRIEF:
                out.pop(key, None)
        else:
            out.pop("sport", None)
        return out

    def stored(self) -> dict[str, Any]:
        """The class's stored brief (``classes.brief``, ADR 0018 section 1): ``model_dump`` with
        the sport written explicitly, so a new class says what it is. The size guard measures
        this form."""
        return {**self.model_dump(mode="json"), "sport": self.sport}

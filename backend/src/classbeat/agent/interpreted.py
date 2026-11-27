"""What the interpreter hands back: an ``InterpretedRequest`` (ADR 0029 section 4; pure).

The ``submit_brief`` tool's input. Each field the instructor's words settle is
``{value, origin, quote?}``; a field the model leaves out is a default (the policy's, not the
model's). Everything but the quotes is closed: numbers, short enums, a list index and a placement
that is either a position or a relation to the n-th kind of interval, so no model-written text
reaches the brief. A quote is a convenience for the page ("you wrote ...") and is kept only if it
is a verbatim substring of the request (``sanitised``); the music choice is an index into the
numbered list the model was sent and an index outside it is no match.

The placement is one flat object (not a union of two): the providers' strict tool schemas refuse
a union of object types.

On a change to an existing class (a revise, ``interpret_input``'s ``current``), the same fields
say what the class should have **after** the change, only for what the request changes; the games
are then the whole list after the change, and ``remove_games`` says the request removes them all
(``classbeat.design.revise_layer_policy`` reads it).

``music_taste`` (issue #1133, ``interpreted_taste``) is what the request says about the music
itself; ``shape`` (#1485, ``interpreted_shape``) is the ordered sections of the ride when the
request describes it in parts, each with its origin and quote.

``two_sports`` (#1544) is a closed flag: the request asks for both a cycling and a running class
(``design.two_sports_policy`` ends such a reading, since a class is one or the other); the other
sport named only as context is no such request.

``pinned_songs`` and ``questions`` (ADR 0038 section 3, #1185) are the composer's:
``classbeat.agent.interpreted_composer``. ``name`` (#1233) is a new class's short title, and a
question's text and options (#1536) are model text too: display text only, cleaned in
``sanitised`` (``class_title_policy.proposed_name``; ``InterpretedQuestion.sanitised``).
"""

from typing import Any, Final, Literal, NamedTuple, Self

from pydantic import BaseModel, Field, model_validator

from classbeat.agent.brief import (
    Anchor,
    BriefGame,
    Position,
    PositionPlacement,
    Relation,
    RelativePlacement,
    Sport,
)
from classbeat.agent.interpreted_composer import (
    MAX_PINNED,
    MAX_QUESTIONS,
    InterpretedPin,
    InterpretedQuestion,
    sanitised_pins,
)
from classbeat.agent.interpreted_shape import InterpretedSection
from classbeat.agent.interpreted_taste import InterpretedTaste
from classbeat.agent.ride_shape import MAX_SECTIONS
from classbeat.agent.schemas import STRICT, Game
from classbeat.agent.stated import Stated, StatedOrigin, with_quote
from classbeat.class_title_policy import proposed_name

MAX_NAMES: Final = 50  # playlist and mix names sent to the model, so the largest valid index + 1
MAX_GAMES: Final = 6


class StatedSport(Stated):
    value: Sport


class StatedLength(Stated):
    value: int = Field(ge=10, le=180)


class StatedIntensity(Stated):
    value: Literal["easy", "moderate", "hard"]


class StatedClassType(Stated):
    value: Literal["plain", "interval", "hiit", "journey", "ftp_test"]


class StatedClimbs(Stated):
    value: int = Field(ge=0, le=10)


class StatedSprints(Stated):
    value: int = Field(ge=0, le=20)


class StatedRaces(Stated):
    value: int = Field(ge=0, le=5)


class StatedFtpTest(Stated):
    value: Literal[3, 5, 20]


class StatedMusic(Stated):
    """The music choice: the index of a name in the numbered list that was sent."""

    value: int = Field(ge=0, lt=MAX_NAMES)


class InterpretedPlacement(BaseModel):
    """Where in the class a game goes: either a ``position`` alone, or a ``relation`` to the
    ``ordinal``-th ``anchor`` ("after climb 2"). Nothing else."""

    model_config = STRICT

    position: Position | None = None
    relation: Relation | None = None
    anchor: Anchor | None = None
    ordinal: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        relative = (self.relation, self.anchor, self.ordinal)
        if self.position is not None and all(r is None for r in relative):
            return self
        if self.position is None and all(r is not None for r in relative):
            return self
        raise ValueError("a placement is a position alone, or a relation, an anchor and an ordinal")

    def closed(self) -> PositionPlacement | RelativePlacement:
        if self.position is not None:
            return PositionPlacement(position=self.position)
        if self.relation is None or self.anchor is None or self.ordinal is None:
            raise ValueError("a placement needs a position or a full relation")
        return RelativePlacement(relation=self.relation, anchor=self.anchor, ordinal=self.ordinal)


class InterpretedGame(Stated):
    """A game the instructor asked for; ``any`` when they named no type."""

    type: Game | Literal["any"]
    placement: InterpretedPlacement | None = None
    duration_sec: int | None = Field(default=None, ge=1, le=600)

    def brief_game(self) -> BriefGame:
        placement = self.placement.closed() if self.placement else None
        return BriefGame(type=self.type, placement=placement, duration_sec=self.duration_sec)


class Reading(NamedTuple):
    """One value of the request: its brief path, the closed value, where it came from and the
    kept quote."""

    path: str
    value: Any
    origin: StatedOrigin
    quote: str | None


SCALARS: Final = (
    "sport",
    "length_min",
    "intensity",
    "class_type",
    "climbs",
    "sprints",
    "races",
    "ftp_test",
    "music",
)


class InterpretedRequest(BaseModel):
    model_config = STRICT

    sport: StatedSport | None = None
    length_min: StatedLength | None = None
    intensity: StatedIntensity | None = None
    class_type: StatedClassType | None = None
    climbs: StatedClimbs | None = None
    sprints: StatedSprints | None = None
    races: StatedRaces | None = None
    ftp_test: StatedFtpTest | None = None
    music: StatedMusic | None = None
    # The ride in parts (#1485): the sections in the order the request gives them; none when it
    # does not describe the ride in parts.
    shape: list[InterpretedSection] = Field(
        default_factory=lambda: list[InterpretedSection](), max_length=MAX_SECTIONS
    )
    games: list[InterpretedGame] = Field(
        default_factory=lambda: list[InterpretedGame](), max_length=MAX_GAMES
    )
    # A change to a class (``current`` in the message, a revise): the request removes every game.
    # A closed flag, so "no games reported" (nothing said of them) stays apart from "remove them".
    remove_games: bool | None = None
    # The request asks for a cycling and a running class at once (#1544). A closed flag, so "the
    # sport is running" and "both sports" stay apart; context ("for runners") is neither.
    two_sports: bool | None = None
    music_taste: InterpretedTaste | None = None
    pinned_songs: list[InterpretedPin] = Field(
        default_factory=lambda: list[InterpretedPin](), max_length=MAX_PINNED
    )
    questions: list[InterpretedQuestion] = Field(
        default_factory=lambda: list[InterpretedQuestion](), max_length=MAX_QUESTIONS
    )
    # Unbounded here: ``sanitised`` bounds it after cleaning, never a correction round (#1237).
    name: str | None = None

    def sanitised(self, request: str, names: int) -> Self:
        """A copy that keeps a quote only if it is a substring of ``request`` and a music index
        only if it points into the ``names`` that were sent; the name cleaned or dropped."""
        update: dict[str, Any] = {}
        for field in SCALARS:
            held: Stated | None = getattr(self, field)
            if held is not None:
                update[field] = with_quote(held, request)
        if self.music is not None and self.music.value >= names:
            update["music"] = None
        update["games"] = [with_quote(g, request) for g in self.games]
        update["shape"] = [with_quote(s, request) for s in self.shape]
        if self.music_taste is not None:
            update["music_taste"] = self.music_taste.sanitised(request)
        update["pinned_songs"] = sanitised_pins(self.pinned_songs, request)
        update["questions"] = [q for q in (q.sanitised() for q in self.questions) if q is not None]
        update["name"] = proposed_name(self.name)
        return self.model_copy(update=update)

    def readings(self) -> list[Reading]:
        """Every value that was set, in brief order, with the games as ``games.N`` (their closed
        form, as the brief stores them)."""
        out: list[Reading] = []
        for field in SCALARS:
            held: Any = getattr(self, field)
            if held is not None:
                out.append(Reading(field, held.value, held.origin, held.quote))
        for n, game in enumerate(self.games):
            value = game.brief_game().model_dump(mode="json", exclude_none=True)
            out.append(Reading(f"games.{n}", value, game.origin, game.quote))
        for n, section in enumerate(self.shape):
            out.append(
                Reading(
                    f"shape.{n}",
                    section.closed().model_dump(mode="json"),
                    section.origin,
                    section.quote,
                )
            )
        return out

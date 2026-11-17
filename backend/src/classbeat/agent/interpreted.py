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

``music_taste`` (issue #1133) is what the request says about the music itself: year ranges, a
language (ISO 639-1), style words and "hits", each ``{origin, quote?}``. A style word is kept only
if the request holds it (``music_taste.keep_word``), so it is the instructor's text, never the
model's; ``classbeat.design.interpret_taste_policy`` turns it into ``Brief.music_taste``.
"""

from typing import Annotated, Any, Final, Literal, NamedTuple, Self

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
from classbeat.agent.interpretation_record import MAX_QUOTE, keep_quote
from classbeat.agent.music_taste import (
    MAX_ERAS,
    MAX_GENRES,
    EraRange,
    GenreWord,
    Language,
    Year,
    keep_word,
)
from classbeat.agent.schemas import STRICT, Game

MAX_NAMES: Final = 50  # playlist and mix names sent to the model, so the largest valid index + 1
MAX_GAMES: Final = 6
StatedOrigin = Literal["explicit", "inferred"]
Quote = Annotated[str, Field(min_length=1, max_length=MAX_QUOTE)]


class Stated(BaseModel):
    """Where a value came from: ``explicit`` (the instructor said so) or ``inferred``."""

    model_config = STRICT

    origin: StatedOrigin
    quote: Quote | None = None


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


class StatedEra(Stated):
    """Songs from these years ("the 80s": 1980 to 1989); one entry per era the request names."""

    year_from: Year
    year_to: Year

    @model_validator(mode="after")
    def _an_era(self) -> Self:
        self.closed()  # the era's own rules (order, length)
        return self

    def closed(self) -> EraRange:
        return EraRange(year_from=self.year_from, year_to=self.year_to)


class StatedLanguage(Stated):
    """The songs' language, ISO 639-1 ("sv" for Swedish songs)."""

    value: Language


class StatedGenre(Stated):
    """A style word, exactly as the request writes it."""

    value: GenreWord


class StatedHits(Stated):
    value: Literal[True]


class InterpretedTaste(BaseModel):
    model_config = STRICT

    eras: list[StatedEra] = Field(default_factory=lambda: list[StatedEra](), max_length=MAX_ERAS)
    language: StatedLanguage | None = None
    genres: list[StatedGenre] = Field(
        default_factory=lambda: list[StatedGenre](), max_length=MAX_GENRES
    )
    hits: StatedHits | None = None

    def sanitised(self, request: str) -> "InterpretedTaste":
        """Quotes kept only if verbatim, and a style word only if the request holds it (in the
        request's own spelling)."""
        genres: list[StatedGenre] = []
        for g in self.genres:
            word = keep_word(g.value, request)
            if word is not None:
                genres.append(_with_quote(g, request).model_copy(update={"value": word}))
        update: dict[str, Any] = {
            "eras": [_with_quote(e, request) for e in self.eras],
            "genres": genres,
        }
        for field in ("language", "hits"):
            held: Stated | None = getattr(self, field)
            if held is not None:
                update[field] = _with_quote(held, request)
        return self.model_copy(update=update)


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
    games: list[InterpretedGame] = Field(
        default_factory=lambda: list[InterpretedGame](), max_length=MAX_GAMES
    )
    # A change to a class (``current`` in the message, a revise): the request removes every game.
    # A closed flag, so "no games reported" (nothing said of them) stays apart from "remove them".
    remove_games: bool | None = None
    music_taste: InterpretedTaste | None = None

    def sanitised(self, request: str, names: int) -> Self:
        """A copy that keeps a quote only if it is a substring of ``request`` and a music index
        only if it points into the ``names`` that were sent."""
        update: dict[str, Any] = {}
        for field in SCALARS:
            held: Stated | None = getattr(self, field)
            if held is not None:
                update[field] = _with_quote(held, request)
        if self.music is not None and self.music.value >= names:
            update["music"] = None
        update["games"] = [_with_quote(g, request) for g in self.games]
        if self.music_taste is not None:
            update["music_taste"] = self.music_taste.sanitised(request)
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
        return out


def _with_quote[T: Stated](held: T, request: str) -> T:
    kept = keep_quote(held.quote, request)
    return held if kept == held.quote else held.model_copy(update={"quote": kept})

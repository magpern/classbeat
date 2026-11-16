"""The music taste of a request (issue #1133; ADR 0008's amendment of 2026-11-17; pure).

What the instructor said about the music itself, closed so that no model-written text reaches the
brief:

- ``eras``: year ranges ("the 80s" is 1980 to 1989), at most ``MAX_ERAS``, each at most
  ``MAX_ERA_YEARS`` long, in the order the request names them;
- ``language``: an ISO 639-1 code ("sv" for Swedish songs or Swedish artists);
- ``genres``: style words, each a **verbatim part of the request** (``interpreted`` keeps a word
  only when the request holds it, ``keep_word``), at most ``MAX_GENRES`` of ``GENRE_CHARS``;
- ``hits``: the request asks for hits (well-known songs).

``Brief.music_taste`` holds it; the song stages are told it (``song_input.song_facts``), the pool
step searches per era (``find_songs``) and ``taste_check_policy`` checks the chosen songs against
the eras after the choice.
"""

from typing import Annotated, Any, Final, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from classbeat.agent.schemas import STRICT

MAX_ERAS: Final = 4
MAX_ERA_YEARS: Final = 30
MAX_GENRES: Final = 3
GENRE_CHARS: Final = 30
FIRST_YEAR: Final = 1900
LAST_YEAR: Final = 2100

Language = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")]
GenreWord = Annotated[str, Field(min_length=1, max_length=GENRE_CHARS)]
Year = Annotated[int, Field(ge=FIRST_YEAR, le=LAST_YEAR)]


class EraRange(BaseModel):
    """Songs released from ``year_from`` to ``year_to``, both included."""

    model_config = STRICT

    year_from: Year
    year_to: Year

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.year_from > self.year_to:
            raise ValueError("year_from must not be after year_to")
        if self.year_to - self.year_from >= MAX_ERA_YEARS:
            raise ValueError(f"an era is at most {MAX_ERA_YEARS} years")
        return self

    def holds(self, year: int | None) -> bool:
        return year is not None and self.year_from <= year <= self.year_to

    def view(self) -> dict[str, int]:
        return {"from": self.year_from, "to": self.year_to}


class MusicTaste(BaseModel):
    """The brief's closed form of the taste (see the module)."""

    model_config = STRICT

    eras: list[EraRange] = Field(default_factory=lambda: list[EraRange](), max_length=MAX_ERAS)
    language: Language | None = None
    genres: list[GenreWord] = Field(
        default_factory=lambda: list[GenreWord](), max_length=MAX_GENRES
    )
    hits: bool = False

    def empty(self) -> bool:
        return not self.eras and self.language is None and not self.genres and not self.hits

    def in_any_era(self, year: int | None) -> bool:
        return any(e.holds(year) for e in self.eras)


def taste_view(taste: MusicTaste) -> dict[str, Any]:
    """What the song stages are told: closed values and the request's own words only."""
    out: dict[str, Any] = {}
    if taste.eras:
        out["eras"] = [e.view() for e in taste.eras]
    if taste.language is not None:
        out["language"] = taste.language
    if taste.genres:
        out["genres"] = list(taste.genres)
    if taste.hits:
        out["hits"] = True
    return out


def keep_word(word: str | None, request: str) -> str | None:
    """The request's own spelling of ``word`` when the request holds it (case aside) and it fits
    ``GENRE_CHARS``; ``None`` otherwise, so a word the model made up never reaches the brief."""
    if not word or len(word) > GENRE_CHARS:
        return None
    if len(request.casefold()) != len(request) or len(word.casefold()) != len(word):
        return None  # a fold that changes the length ("ß") would cut the wrong slice
    at = request.casefold().find(word.casefold())
    if at < 0:
        return None
    found = request[at : at + len(word)].strip()
    return found or None


def largest() -> MusicTaste:
    """The largest taste a brief can hold (``interpret_policy.worst_case_brief``)."""
    era = EraRange(year_from=FIRST_YEAR, year_to=FIRST_YEAR + MAX_ERA_YEARS - 1)
    return MusicTaste(
        eras=[era] * MAX_ERAS,
        language="sv",
        genres=["x" * GENRE_CHARS] * MAX_GENRES,
        hits=True,
    )

"""The music taste as the interpreter reports it (issue #1133; pure), split out of
``interpreted``: year ranges, a language (ISO 639-1), style words and "hits", each ``{origin,
quote?}``. A style word is kept only if the request holds it (``music_taste.keep_word``), so it is
the instructor's text, never the model's; ``classbeat.design.interpret_taste_policy`` turns it into
``Brief.music_taste``.
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from classbeat.agent.music_taste import (
    MAX_ERAS,
    MAX_GENRES,
    EraRange,
    GenreWord,
    Language,
    Year,
    keep_word,
)
from classbeat.agent.schemas import STRICT
from classbeat.agent.stated import Stated, with_quote


class StatedEra(Stated):
    """Songs from these years ("the 80s": 1980 to 1989); one entry per era the request names, at
    most 30 years each (a longer range is split: "1960 to 2000" is 1960-1989 and 1990-2000)."""

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
                genres.append(with_quote(g, request).model_copy(update={"value": word}))
        update: dict[str, Any] = {
            "eras": [with_quote(e, request) for e in self.eras],
            "genres": genres,
        }
        for field in ("language", "hits"):
            held: Stated | None = getattr(self, field)
            if held is not None:
                update[field] = with_quote(held, request)
        return self.model_copy(update=update)

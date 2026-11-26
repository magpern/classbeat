"""The composer's part of a reading, as it is stored (ADR 0038 section 3, #1185, #1536; pure: no
database, no network, no model).

``composer_reading`` turns the interpreter's ``pinned_songs`` and ``questions``
(``agent.interpreted_composer``, already sanitised) into what ``classes.interpretation`` stores:

- **pinned songs** ``{title, artist?, at?, quote?}``, at most 10, in the request's own words,
  for a class started with the ``composer_flow`` flag on only (``interpret_kind``);
- **questions**: ``reading_question_policy`` (every reading has them since #1536). The brief does
  not take the model's assumed answer: it uses the policy's defaults, so nothing waits for an
  answer.
"""

from collections.abc import Collection
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from classbeat.agent.interpretation_record import MAX_QUOTE
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.interpreted_composer import ARTIST_CHARS, AT_CHARS, MAX_PINNED, TITLE_CHARS
from classbeat.design.reading_question_policy import Question, questions


class PinnedSong(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=TITLE_CHARS)]
    artist: Annotated[str, Field(min_length=1, max_length=ARTIST_CHARS)] | None = None
    at: Annotated[str, Field(min_length=1, max_length=AT_CHARS)] | None = None
    quote: Annotated[str, Field(min_length=1, max_length=MAX_QUOTE)] | None = None


PinnedList = Annotated[list[PinnedSong], Field(max_length=MAX_PINNED)]


def pinned(read: InterpretedRequest) -> list[PinnedSong]:
    """The sanitised pins as stored (``InterpretedRequest.sanitised`` kept the request's words)."""
    return [
        PinnedSong(title=p.song, artist=p.artist, at=p.at, quote=p.quote)
        for p in read.pinned_songs[:MAX_PINNED]
    ]


def composer_reading(
    read: InterpretedRequest, skip: Collection[str] = (), *, hintless: bool = False
) -> tuple[list[PinnedSong], list[Question]]:
    """The pins and the questions to store. The brief is unchanged by either: it holds the
    policy's own values, whatever the model assumed."""
    return pinned(read), questions(read, skip, hintless=hintless)

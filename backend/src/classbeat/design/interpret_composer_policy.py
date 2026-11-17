"""The composer's part of a reading, as it is stored (ADR 0038 section 3, #1185; pure: no
database, no network, no model).

``composer_reading`` turns the interpreter's ``pinned_songs`` and ``questions``
(``agent.interpreted_composer``, already sanitised) and the brief ``interpret_policy.resolve``
made into what ``classes.interpretation`` stores:

- **pinned songs** ``{title, artist?, at?, quote?}``, at most 10, in the request's own words;
- **questions** ``{path, question, assumed}``, at most 2, one per path, only for a path the
  request did not state explicitly. ``question`` is a closed code the page phrases
  (``how_long``, ``how_hard``); ``assumed`` is the value the brief took for that path, so the brief
  always uses it and nothing waits for an answer.

Only a class started with the ``composer_flow`` flag on gets either (``interpret_kind``); without
it the stored reading is exactly as before.
"""

from collections.abc import Iterable
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from classbeat.agent.brief import Brief
from classbeat.agent.interpretation_record import MAX_QUOTE
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.interpreted_composer import (
    ARTIST_CHARS,
    AT_CHARS,
    MAX_PINNED,
    MAX_QUESTIONS,
    TITLE_CHARS,
    QuestionPath,
)

QUESTION_CODES: Final[dict[str, str]] = {"length_min": "how_long", "intensity": "how_hard"}


class PinnedSong(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=TITLE_CHARS)]
    artist: Annotated[str, Field(min_length=1, max_length=ARTIST_CHARS)] | None = None
    at: Annotated[str, Field(min_length=1, max_length=AT_CHARS)] | None = None
    quote: Annotated[str, Field(min_length=1, max_length=MAX_QUOTE)] | None = None


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: QuestionPath
    question: Literal["how_long", "how_hard"]
    assumed: int | Literal["easy", "moderate", "hard"]


PinnedList = Annotated[list[PinnedSong], Field(max_length=MAX_PINNED)]
QuestionList = Annotated[list[Question], Field(max_length=MAX_QUESTIONS)]


def pinned(read: InterpretedRequest) -> list[PinnedSong]:
    """The sanitised pins as stored (``InterpretedRequest.sanitised`` kept the request's words)."""
    return [
        PinnedSong(title=p.song, artist=p.artist, at=p.at, quote=p.quote)
        for p in read.pinned_songs[:MAX_PINNED]
    ]


def questions(read: InterpretedRequest, brief: Brief, stated: Iterable[str]) -> list[Question]:
    """One question per open shape field the interpreter named, in its order: not for a path
    ``stated`` explicitly (``read.readings`` with origin ``explicit``), and with the brief's value
    as ``assumed``."""
    explicit = set(stated)
    out: list[Question] = []
    for asked in read.questions:
        path = asked.path
        if path in explicit or any(q.path == path for q in out):
            continue
        value: Any = getattr(brief, path)
        out.append(Question(path=path, question=QUESTION_CODES[path], assumed=value))  # pyright: ignore[reportArgumentType]
        if len(out) == MAX_QUESTIONS:
            break
    return out


def composer_reading(
    read: InterpretedRequest, brief: Brief
) -> tuple[list[PinnedSong], list[Question]]:
    stated = [r.path for r in read.readings() if r.origin == "explicit"]
    return pinned(read), questions(read, brief, stated)

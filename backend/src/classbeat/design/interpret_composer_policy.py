"""The composer's part of a reading, as it is stored (ADR 0038 section 3, #1185, #1536; pure: no
database, no network, no model).

``composer_reading`` turns the interpreter's ``pinned_songs`` and ``questions``
(``agent.interpreted_composer``, already sanitised) and the brief ``interpret_policy.resolve``
made into what ``classes.interpretation`` stores:

- **pinned songs** ``{title, artist?, at?, quote?}``, at most 10, in the request's own words;
- **questions** ``{topic, text, options, assumed_index}``, at most ``MAX_QUESTIONS`` (12, a
  technical guard, roadmap Amendment 37): the model's own wording, chosen by Claude (display text
  only, never read as an instruction). One per topic except ``other``; a question whose
  ``assumed_index`` is no option is dropped; so is one about a field the request already
  states (``length_min``, ``intensity`` or ``music`` read with origin ``explicit``) and one about a
  topic the instructor chose in the form (``skip``: the music they attached or picked, the sport
  they chose), which the model is never told about. The brief does not take the model's
  assumed answer: it uses the policy's defaults, so nothing waits for an answer.

Only a class started with the ``composer_flow`` flag on gets either (``interpret_kind``); without
it the stored reading is exactly as before. A row stored before #1536 has questions of the old
shape (``{path, question, assumed}``): they are dropped on reading (``legacy_dropped``).
"""

from collections.abc import Collection, Iterable
from typing import Annotated, Any, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from classbeat.agent.interpretation_record import MAX_QUOTE
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.interpreted_composer import (
    ARTIST_CHARS,
    AT_CHARS,
    MAX_OPTIONS,
    MAX_PINNED,
    MAX_QUESTIONS,
    MIN_OPTIONS,
    OPTION_CHARS,
    QUESTION_CHARS,
    TITLE_CHARS,
    InterpretedQuestion,
    QuestionTopic,
)

# The brief field a topic is about: a question is dropped when the request states that field.
TOPIC_PATHS: Final[dict[str, str]] = {
    "length": "length_min",
    "intensity": "intensity",
    "music": "music",
}


class PinnedSong(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=TITLE_CHARS)]
    artist: Annotated[str, Field(min_length=1, max_length=ARTIST_CHARS)] | None = None
    at: Annotated[str, Field(min_length=1, max_length=AT_CHARS)] | None = None
    quote: Annotated[str, Field(min_length=1, max_length=MAX_QUOTE)] | None = None


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: QuestionTopic
    text: Annotated[str, Field(min_length=1, max_length=QUESTION_CHARS)]
    options: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=OPTION_CHARS)]],
        Field(min_length=MIN_OPTIONS, max_length=MAX_OPTIONS),
    ]
    # ``None``: required, no assumed answer (a music question with no hint, #1536).
    assumed_index: Annotated[int, Field(ge=0, lt=MAX_OPTIONS)] | None = None
    # The option that means "No music" (a ``music`` question only).
    no_music_index: Annotated[int, Field(ge=0, lt=MAX_OPTIONS)] | None = None


def legacy_dropped(value: Any) -> Any:
    """A stored list without the entries of the shape before #1536 (no ``topic``)."""
    if not isinstance(value, list):
        return value
    return [q for q in value if not isinstance(q, dict) or "topic" in q]  # pyright: ignore[reportUnknownVariableType]


PinnedList = Annotated[list[PinnedSong], Field(max_length=MAX_PINNED)]
QuestionList = Annotated[
    list[Question], BeforeValidator(legacy_dropped), Field(max_length=MAX_QUESTIONS)
]


def pinned(read: InterpretedRequest) -> list[PinnedSong]:
    """The sanitised pins as stored (``InterpretedRequest.sanitised`` kept the request's words)."""
    return [
        PinnedSong(title=p.song, artist=p.artist, at=p.at, quote=p.quote)
        for p in read.pinned_songs[:MAX_PINNED]
    ]


def questions(
    read: InterpretedRequest, stated: Iterable[str], skip: Collection[str] = ()
) -> list[Question]:
    """The interpreter's questions, in its order, as stored: one per topic (``other`` as often as
    it is asked), none whose ``assumed_index`` is no option, none about a path in ``stated`` (read
    with origin ``explicit``) or a topic in ``skip``, at most ``MAX_QUESTIONS``.
    The "No music" marker is kept only on a ``music`` question, and in range."""
    explicit = set(stated)
    out: list[Question] = []
    for asked in read.questions:
        topic = asked.topic
        if topic in skip or TOPIC_PATHS.get(topic) in explicit:
            continue
        if topic != "other" and any(q.topic == topic for q in out):
            continue
        if not MIN_OPTIONS <= len(asked.options) <= MAX_OPTIONS:
            continue
        held = asked.assumed_index
        if held is not None and not 0 <= held < len(asked.options):
            continue
        if held is None and topic != "music":
            continue
        out.append(
            Question(
                topic=topic,
                text=asked.text,
                options=list(asked.options),
                assumed_index=held,
                no_music_index=_none_index(asked),
            )
        )
        if len(out) == MAX_QUESTIONS:
            break
    return out


def _none_index(asked: InterpretedQuestion) -> int | None:
    index = asked.no_music_index
    if asked.topic != "music" or index is None:
        return None
    return index if index < len(asked.options) else None


def chosen_topics(*, music: bool, sport: bool) -> frozenset[str]:
    """The topics the instructor already settled in the form (the music they attached or picked,
    the sport they chose): the model is never told, so a question about them is dropped."""
    return frozenset([*(["music"] if music else []), *(["sport"] if sport else [])])


def composer_reading(
    read: InterpretedRequest, skip: Collection[str] = ()
) -> tuple[list[PinnedSong], list[Question]]:
    """The pins and the questions to store. The brief is unchanged by either: it holds the
    policy's own values, whatever the model assumed."""
    stated = [r.path for r in read.readings() if r.origin == "explicit"]
    return pinned(read), questions(read, stated, skip)

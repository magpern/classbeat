"""The reading's questions, as they are stored (#1536, roadmap Amendment 37; pure: no database, no
network, no model). Split out of ``interpret_composer_policy``: every reading has them, in both
flows (the Architect, 2026-11-26); the pinned songs stay the composer's.

``questions`` turns the interpreter's questions (``agent.interpreted_composer``, already
sanitised) into ``{topic, text, options, assumed_index, no_music_index?}``, at most
``MAX_QUESTIONS`` (12, a technical guard): the model's own wording (display text only, never read
as an instruction). One per topic except ``other``; a question whose ``assumed_index`` is no
option is dropped; so is one about a field the request already states (``length_min``,
``intensity`` or ``music`` read with origin ``explicit``) and one about a topic the instructor
chose in the form (``skip``, ``chosen_topics``: the music they attached or chose, the sport). A pick
chosen in the form is no choice of music (owner, 2026-11-26: "Some hint from user is needed").

**A hint-less pick** (``hintless``: the reading's music is a song pick with no hint of what to
play, ``music_hint_policy``) always has a music question with no assumed answer, so the
hand-off's ``music_unanswered`` is a backstop, never the instructor's only path:

- the model's music question is kept even when its topic was skipped or "stated" (a named
  playlist or mix that matched nothing), and its assumed answer is removed;
- with no music question from the model, the policy adds ``WHICH_MUSIC``: ``code``
  ``which_music``, no model text, the closed options ``WHICH_MUSIC_OPTIONS`` (one of the
  instructor's playlists, "No music", marked), no assumed answer. The page phrases it in its own
  language; a typed style answers it too. It goes first, so the cap never drops it.

``no_music_index`` marks a music question's "No music" option. It is dropped when it is the
assumed answer: "No music" is the instructor's choice, never an assumption.
"""

from collections.abc import Collection
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.interpreted_composer import (
    MAX_OPTIONS,
    MAX_QUESTIONS,
    MIN_OPTIONS,
    OPTION_CHARS,
    QUESTION_CHARS,
    InterpretedQuestion,
    QuestionTopic,
)

# The brief field a topic is about: a question is dropped when the request states that field.
TOPIC_PATHS: Final[dict[str, str]] = {
    "length": "length_min",
    "intensity": "intensity",
    "music": "music",
}
QuestionCode = Literal["which_music"]
WHICH_MUSIC_OPTIONS: Final = ("my_playlists", "no_music")


class Question(BaseModel):
    """The model's question (``text``) or the policy's closed one (``code``), never both."""

    model_config = ConfigDict(extra="forbid")

    topic: QuestionTopic
    code: QuestionCode | None = None
    text: Annotated[str, Field(min_length=1, max_length=QUESTION_CHARS)] | None = None
    options: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=OPTION_CHARS)]],
        Field(min_length=MIN_OPTIONS, max_length=MAX_OPTIONS),
    ]
    # ``None``: required, no assumed answer (a music question with no hint, #1536).
    assumed_index: Annotated[int, Field(ge=0, lt=MAX_OPTIONS)] | None = None
    # The option that means "No music" (a ``music`` question only).
    no_music_index: Annotated[int, Field(ge=0, lt=MAX_OPTIONS)] | None = None

    @model_validator(mode="after")
    def _model_or_closed(self) -> Self:
        if self.code is None:
            if self.text is None:
                raise ValueError("a question has the model's text or a code")
            return self
        closed = (self.topic, self.text, tuple(self.options), self.assumed_index)
        if closed != ("music", None, WHICH_MUSIC_OPTIONS, None) or self.no_music_index != 1:
            raise ValueError("a closed question is exactly WHICH_MUSIC")
        return self


WHICH_MUSIC: Final = Question(
    topic="music", code="which_music", options=list(WHICH_MUSIC_OPTIONS), no_music_index=1
)


def legacy_dropped(value: Any) -> Any:
    """A stored list without the entries of the shape before #1536 (no ``topic``)."""
    if not isinstance(value, list):
        return value
    return [q for q in value if not isinstance(q, dict) or "topic" in q]  # pyright: ignore[reportUnknownVariableType]


QuestionList = Annotated[
    list[Question], BeforeValidator(legacy_dropped), Field(max_length=MAX_QUESTIONS)
]


def questions(
    read: InterpretedRequest,
    skip: Collection[str] = (),
    *,
    hintless: bool = False,
) -> list[Question]:
    """The questions to store (the module's rules), in the interpreter's order."""
    explicit = {r.path for r in read.readings() if r.origin == "explicit"}
    out: list[Question] = []
    for asked in read.questions:
        topic = asked.topic
        kept_music = hintless and topic == "music"
        if not kept_music and (topic in skip or TOPIC_PATHS.get(topic) in explicit):
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
        if kept_music:
            held = None  # a hint-less pick assumes nothing: the instructor answers it
        out.append(
            Question(
                topic=topic,
                text=asked.text,
                options=list(asked.options),
                assumed_index=held,
                no_music_index=_none_index(asked, held),
            )
        )
        if len(out) == MAX_QUESTIONS:
            break
    if hintless and not any(q.topic == "music" for q in out):
        out = [WHICH_MUSIC, *out][:MAX_QUESTIONS]
    return out


def _none_index(asked: InterpretedQuestion, assumed: int | None) -> int | None:
    index = asked.no_music_index
    if asked.topic != "music" or index is None or index == assumed:
        return None
    return index if index < len(asked.options) else None


def chosen_topics(*, music: bool, sport: bool) -> frozenset[str]:
    """The topics the instructor already settled in the form (the music they attached or chose,
    never a pick, the sport they chose): the model is never told, so a question about them is
    dropped."""
    return frozenset([*(["music"] if music else []), *(["sport"] if sport else [])])

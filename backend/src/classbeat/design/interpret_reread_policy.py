"""The instructor's answers to the reading's questions, as a new request (#1536, roadmap
Amendment 37; pure: no database, no network, no model).

``POST /api/classes/{id}/reread`` (``classes.reread``) sends ``{answers, note?}``. This module
checks each answer against the stored question it names and turns the answers into plain
sentences; the new request is the request that made the reading, then those sentences. Only the
instructor's own words (a typed answer, the note) and the stored question text (cleaned model text)
and the chosen option are put into it, never an index or markup: the interpreter reads them as
the instructor's words, as it read the first request.

``chose_no_music`` reports an answer that picked the marked "No music" option (the route then sets
``music_override`` ``none``; a typed "no music" is never read as one, #1137).

Codes: ``invalid_answer`` (an index no question or option has, a question answered twice, or no
answer and no note) and ``too_large`` (the new request is over ``MAX_REQUEST`` characters).
"""

from collections.abc import Sequence
from typing import Annotated, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.interpreted_composer import MAX_OPTIONS, MAX_QUESTIONS
from classbeat.agent.schemas import clean_text
from classbeat.design.interpret_composer_policy import Question
from classbeat.design.revise_input import MAX_REQUEST

ANSWER_CHARS: Final = 200
NOTE_CHARS: Final = 500


class Answer(BaseModel):
    """One answer: an option of the question, or the instructor's own words."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question_index: Annotated[int, Field(ge=0, lt=MAX_QUESTIONS)]
    option_index: Annotated[int, Field(ge=0, lt=MAX_OPTIONS)] | None = None
    text: Annotated[str, Field(min_length=1, max_length=ANSWER_CHARS)] | None = None

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.option_index is None) == (self.text is None):
            raise ValueError("give exactly one of option_index and text")
        return self


class RereadIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # The reading these answers belong to (``GET .../interpretation``'s ``interpret_job_id``): the
    # route refuses ``not_ready`` when another reading was stored since, before any index is read.
    interpret_job_id: UUID
    answers: Annotated[list[Answer], Field(max_length=MAX_QUESTIONS)] = Field(
        default_factory=lambda: []
    )
    note: Annotated[str, Field(min_length=1, max_length=NOTE_CHARS)] | None = None


def sentences(questions: Sequence[Question], body: RereadIn) -> str | None:
    """The answers (and the note) as plain sentences, or ``None`` for ``invalid_answer``."""
    parts: list[str] = []
    seen: set[int] = set()
    for given in body.answers:
        if given.question_index in seen or given.question_index >= len(questions):
            return None
        seen.add(given.question_index)
        asked = questions[given.question_index]
        if given.option_index is not None:
            if given.option_index >= len(asked.options):
                return None
            said = asked.options[given.option_index]
        else:
            said = clean_text(given.text or "")
        if not said:
            return None
        parts.append(f"{asked.text} Answer: {said}")
    note = clean_text(body.note or "")
    if note:
        parts.append(f"Also: {note}")
    return " ".join(parts) if parts else None


def chose_no_music(questions: Sequence[Question], body: RereadIn) -> bool:
    """An answer picked the option a ``music`` question marked as "No music" (``no_music_index``:
    a closed marker, so the option's words, in any language, are never read). Free text is never."""
    for given in body.answers:
        if given.option_index is None or given.question_index >= len(questions):
            continue
        asked = questions[given.question_index]
        if asked.topic == "music" and asked.no_music_index == given.option_index:
            return True
    return False


def combined(request: str, addition: str) -> str | None:
    """The new request, or ``None`` when it is over ``MAX_REQUEST`` (``too_large``)."""
    joined = f"{request} {addition}"
    return joined if len(joined) <= MAX_REQUEST else None

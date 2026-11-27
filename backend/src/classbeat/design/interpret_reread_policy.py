"""The instructor's answers to the reading's questions, as a new request (#1536, roadmap
Amendment 37; pure: no database, no network, no model).

``POST /api/classes/{id}/reread`` (``classes.reread``) sends ``{answers, note?}``. This module
checks each answer against the stored question it names and turns the answers into plain
sentences; the new request is the request that made the reading, then those sentences. Only the
instructor's own words (a typed answer, the note) and the stored question text (cleaned model text)
and the chosen option are put into it, never an index or markup: the interpreter reads them as
the instructor's words, as it read the first request. The closed ``which_music`` and
``which_sport`` questions (``reading_question_policy``) are put in the app's own words.

``chosen_sport`` reports the sport picked on the closed ``which_sport`` question (#1544): the
re-read's ``sport_override`` becomes it (``browser_fields``), a closed field like "No music", so
the instructor's answer is what the new reading designs (never read from typed words).

``wants_no_music`` reports "No music" (the route then sets ``music_override`` ``none``): an answer
that picked the marked "No music" option (``chose_no_music``), or the body's closed ``no_music:
true`` (#1538: the confirm screen offers "No music" for a reading that asked no music question).
Both are closed fields: a typed "no music" is never read as one (#1137).

``music_sources`` (#1538: the confirm screen's playlist picker) are the playlists the instructor
picked, at most ``MAX_SOURCES``, each once, in the shape ``music_sources`` have everywhere; the
re-read adds them to the request's attached playlists (``browser_fields``). The closed
"Use one of my playlists" answer needs them, and they exclude "No music".

Codes: ``invalid_answer`` (an index no question or option has, a question answered twice, "Use
one of my playlists" with no playlist, playlists with "No music", or nothing answered, no note, no
playlist and no "No music") and ``too_large`` (the new request is over ``MAX_REQUEST``
characters).
"""

from collections.abc import Sequence
from typing import Annotated, Any, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from classbeat.agent.interpreted_composer import MAX_OPTIONS, MAX_QUESTIONS
from classbeat.agent.schemas import clean_text
from classbeat.design.music_sources import MusicSources
from classbeat.design.reading_question_policy import Question
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
    # Closed fields (#1538): "No music" for a reading with no music question to mark it, and the
    # playlists of the picker. Never read from the words; the server checks both.
    no_music: StrictBool | None = None  # only ever true (``_closed``)
    music_sources: MusicSources | None = None

    @model_validator(mode="after")
    def _closed(self) -> Self:
        if self.no_music is False:
            raise ValueError("no_music is true or absent")
        if self.no_music is not None and self.music_sources is not None:
            raise ValueError("no music cannot be combined with playlists")
        return self


def sentences(questions: Sequence[Question], body: RereadIn) -> str | None:
    """The answers (and the note) as plain sentences, or ``None`` for ``invalid_answer``."""
    parts: list[str] = []
    seen: set[int] = set()
    if body.music_sources is not None and chose_no_music(questions, body):
        return None  # "No music" and playlists contradict each other
    asked_playlists = False
    for given in body.answers:
        if given.question_index in seen or given.question_index >= len(questions):
            return None
        seen.add(given.question_index)
        asked = questions[given.question_index]
        if given.option_index is not None:
            if given.option_index >= len(asked.options):
                return None
            said = _option_words(asked, given.option_index)
            if asked.code is not None and asked.options[given.option_index] == _MY_PLAYLISTS:
                asked_playlists = True
        else:
            said = clean_text(given.text or "")
        if not said:
            return None
        parts.append(f"{_question_words(asked)} Answer: {said}")
    if asked_playlists and body.music_sources is None:
        return None  # the closed "Use one of my playlists" names none without them
    if wants_no_music(questions, body) and not chose_no_music(questions, body):
        parts.append(_NO_MUSIC_SENTENCE)
    if body.music_sources is not None and not asked_playlists:
        parts.append(_PLAYLISTS_SENTENCE)
    note = clean_text(body.note or "")
    if note:
        parts.append(f"Also: {note}")
    return " ".join(parts) if parts else None


# The closed question's words for the interpreter (``WHICH_MUSIC``): the app's, never the model's.
_CLOSED_TEXT: Final = {
    "which_music": "What music should the class have?",
    "which_sport": "Is the class cycling or running?",
}
_CLOSED_OPTIONS: Final = {
    "my_playlists": "Use one of my playlists",
    "no_music": "No music",
    "cycling": "Cycling",
    "running": "Running",
}
_MY_PLAYLISTS: Final = "my_playlists"
# Sentences of the closed fields, in the app's own words (the page's catalogue has its own).
_NO_MUSIC_SENTENCE: Final = "No music for this class."
_PLAYLISTS_SENTENCE: Final = "Use the playlists I picked."


def _question_words(asked: Question) -> str:
    if asked.text is not None:
        return asked.text
    return _CLOSED_TEXT[asked.code or "which_music"]  # a question without text has a code


def _option_words(asked: Question, index: int) -> str:
    option = asked.options[index]
    return option if asked.code is None else _CLOSED_OPTIONS[option]


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


def chosen_sport(questions: Sequence[Question], body: RereadIn) -> str | None:
    """The sport an answer picked on the closed ``which_sport`` question (its closed option, never
    the words); ``None`` when no answer did."""
    for given in body.answers:
        if given.option_index is None or given.question_index >= len(questions):
            continue
        asked = questions[given.question_index]
        if asked.code == "which_sport" and given.option_index < len(asked.options):
            return asked.options[given.option_index]
    return None


def wants_no_music(questions: Sequence[Question], body: RereadIn) -> bool:
    """ "No music": the closed ``no_music`` field, or the marked option of a ``music`` question."""
    return body.no_music is True or chose_no_music(questions, body)


def browser_fields(
    kept: dict[str, Any], body: RereadIn, *, no_music: bool, sport: str | None = None
) -> dict[str, Any]:
    """The re-read's browser fields (``kept``: those of the reading's own job): the sport the
    instructor picked on ``which_sport`` (``sport``) is the new ``sport_override``; "No music" is
    the override ``none`` with no attached playlists; picked playlists are added to the attached
    ones (each once; over ``MAX_SOURCES`` the input is refused ``invalid_input``) and replace a
    form "No music", which they contradict."""
    out = dict(kept)
    if sport is not None:
        out["sport_override"] = sport
    if no_music:
        out.pop("music_sources", None)
        out["music_override"] = {"source": "none"}
        return out
    if body.music_sources is None:
        return out
    if (out.get("music_override") or {}).get("source") == "none":
        out.pop("music_override")
    have: list[dict[str, Any]] = list(out.get("music_sources") or [])
    seen = {s["id"] for s in have}
    picked = [s.model_dump(mode="json") for s in body.music_sources if s.id not in seen]
    out["music_sources"] = have + picked
    return out


def combined(request: str, addition: str) -> str | None:
    """The new request, or ``None`` when it is over ``MAX_REQUEST`` (``too_large``)."""
    joined = f"{request} {addition}"
    return joined if len(joined) <= MAX_REQUEST else None

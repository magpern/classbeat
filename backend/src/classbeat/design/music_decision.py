"""The music decision of a composer class (ADR 0038 amendment of 2026-11-24, decision 5, #1495;
pure: no database, no network, no model).

``classes.interpretation.music_decision`` (``interpretation_row``):
``{state: "undecided" | "decided", choice, sources, note}``, closed values except ``note``.

- **Set by the reading** (``initial``): ``decided`` with ``choice: "request"`` when the request
  attached playlists, chose a source (``music_override``, "No music" included, a pick not), named
  one of the instructor's playlists or mixes, or its reading has a music taste; otherwise, and
  always for a song pick with no hint (#1536), ``undecided``.
- **Answered** by the instructor (``answered``, ``PUT /api/classes/{id}/music``): ``pick`` (choose
  for me), ``describe`` (``note``: the sound in the instructor's own words), ``playlists`` (up to
  ``MAX_SOURCES`` Spotify playlists, ``sources``, checked as ``music_sources`` are: shape, count,
  each once) or ``later`` (undecided again).
- **A row stored before** has no decision: ``decision_of`` reads it as ``decided`` / ``request``,
  today's behaviour (``DEFAULT``).
- **The song search** takes its favoured playlists from the decision when it chose playlists,
  else from the interpret request (``fill_sources``), and the note as more of the instructor's
  words (``brief_with_note``).

``note`` is the instructor's own text, never a model's: control and format characters removed
(``clean_text``), visible, at most ``MAX_NOTE`` characters. It is data: it reaches the song
search only inside the brief's text, which every song stage is told is the instructor's words and
never instructions. It is never logged.
"""

from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.brief import Brief
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.schemas import clean_text
from classbeat.design.music_sources import MAX_SOURCES, MusicSource, MusicSources

MAX_NOTE: Final = 200
MAX_BRIEF_TEXT: Final = 2000  # ``agent.schemas.BriefText``
NOTE_LABEL: Final = "About the music: "
UNDECIDED: Final = "music_undecided"  # the song search's refusal while undecided
FIXED: Final = "music_fixed"  # the answer's refusal once a fill was started for the sketch

State = Literal["undecided", "decided"]
Choice = Literal["request", "pick", "playlists", "describe"]


def _visible(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        raise ValueError("must have visible text")
    return cleaned


MusicNote = Annotated[str, Field(min_length=1, max_length=MAX_NOTE), AfterValidator(_visible)]


def _each_once(sources: list[MusicSource]) -> None:
    if len({s.id for s in sources}) != len(sources):
        raise ValueError("a playlist is shared once")


class MusicDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: State
    choice: Choice | None = None
    sources: list[MusicSource] = Field(
        default_factory=lambda: list[MusicSource](), max_length=MAX_SOURCES
    )
    note: MusicNote | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.state == "decided") != (self.choice is not None):
            raise ValueError("a decision names its choice, and only a decision does")
        if (self.choice == "playlists") != bool(self.sources):
            raise ValueError("playlists, and only playlists, name the shared playlists")
        _each_once(self.sources)
        if self.choice == "describe" and self.note is None:
            raise ValueError("a description is the instructor's words")
        if self.note is not None and self.choice in (None, "request"):
            raise ValueError("a note belongs to an answer")
        return self


DEFAULT: Final = MusicDecision(state="decided", choice="request")


class MusicAnswer(BaseModel):
    """The body of ``PUT /api/classes/{id}/music``."""

    model_config = ConfigDict(extra="forbid")

    choice: Literal["pick", "playlists", "describe", "later"]
    playlists: MusicSources | None = None
    note: MusicNote | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.choice == "playlists") != (self.playlists is not None):
            raise ValueError("playlists, and only playlists, name the playlists")
        if self.playlists is not None:
            _each_once(self.playlists)
        if self.choice == "describe" and self.note is None:
            raise ValueError("describe needs the instructor's words")
        if self.choice == "later" and self.note is not None:
            raise ValueError("later takes no words")
        return self


def decision_of(stored: MusicDecision | None) -> MusicDecision:
    """The stored decision, or ``DEFAULT`` for a row stored before #1495."""
    return DEFAULT if stored is None else stored


def initial(
    read: InterpretedRequest,
    *,
    composer: bool,
    attached: bool,
    chose: bool,
    hintless: bool = False,
) -> MusicDecision | None:
    """The reading's decision (None unless ``composer``: a cycling class read with the composer):
    ``decided`` / ``request`` when the request already says what the music is (attached
    playlists, a chosen source other than a pick, a named playlist or mix, a music taste), else
    ``undecided``. Always ``undecided`` for a song pick with no hint of what to play
    (``hintless``, ``music_hint_policy``, #1536): a pick chosen in the form, or a name that
    matched nothing, is no decision, so the composer asks."""
    if not composer:
        return None
    if hintless:
        return MusicDecision(state="undecided")
    taste = read.music_taste
    tasted = taste is not None and bool(
        taste.eras or taste.genres or taste.language is not None or taste.hits is not None
    )
    if attached or chose or read.music is not None or tasted:
        return DEFAULT
    return MusicDecision(state="undecided")


def answered(answer: MusicAnswer) -> MusicDecision:
    if answer.choice == "later":
        return MusicDecision(state="undecided")
    return MusicDecision(
        state="decided",
        choice=answer.choice,
        sources=list(answer.playlists or []),
        note=answer.note,
    )


def fill_sources(
    decision: MusicDecision, requested: list[MusicSource] | None
) -> list[MusicSource] | None:
    """The playlists the song search favours: the shared ones, else the request's attached ones."""
    if decision.choice == "playlists":
        return list(decision.sources)
    return requested


def brief_with_note(brief: Brief, note: str | None) -> Brief:
    """The brief the song search's stages read, with the note after the instructor's request (the
    request is cut, never the note, to fit the brief's text)."""
    if note is None:
        return brief
    tail = NOTE_LABEL + note
    head = brief.text[: max(0, MAX_BRIEF_TEXT - len(tail) - 1)].rstrip()
    text = f"{head} {tail}" if head else tail
    return Brief.model_validate({**brief.stored(), "text": text})

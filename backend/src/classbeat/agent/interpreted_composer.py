"""The composer's part of a reading: pinned songs and follow-up questions (ADR 0038 section 3,
#1185; pure).

Fields of the ``submit_brief`` tool's input (``InterpretedRequest.pinned_songs`` and
``.questions``), read only for a class started with the ``composer_flow`` flag on
(``classbeat.design.interpret_composer_policy``):

- **A pinned song** is a song the instructor named, with where it goes: ``{song, artist?, at?,
  quote?}`` (``song`` is its title: a property named ``title`` would not survive the providers'
  strict schemas, which drop that keyword), origin ``explicit`` only (a song the model thinks would
  fit is not pinned). The title, the artist and ``at`` (the words that say where: "warm-up", "climb
  3", "the last climb") are kept only if the request holds them as whole words, case aside, with
  at least ``MIN_LETTERS`` letters or digits ("frica" in "Africa" and a lone "a" are not kept), and
  are then stored in the request's own spelling (``from_request``, ``sanitised``), so a title or a
  place the model made up never reaches the class. A song whose title the request does not hold is
  dropped; an artist or a place it does not hold is left out; a song named twice (same title and
  artist, case aside) is kept once, in order (``sanitised_pins``). A pinned song is a proposal: the
  song fill verifies it by search before it is used (ADR 0038 section 5).
- **A question** is something the request leaves unstated or unclear whose default changes the
  class (roadmap Amendment 37, owner 2026-11-26: Claude decides what to ask):
  ``{topic, text, options, assumed_index}``. ``topic`` is a closed hint (``music``, ``length``,
  ``intensity``, ``structure``, ``sport``, ``other``); ``text`` and the 2 to 5 ``options`` are
  short model text in the request's language, cleaned and length-capped
  (``InterpretedQuestion.sanitised``) and shown as plain text only; ``assumed_index`` points at
  the option the model assumed. The brief does not take the model's assumption: it uses the
  policy's own defaults, and nothing waits for an answer (ADR 0029 section 4 rule 4, as amended).
  At most ``MAX_QUESTIONS`` (12, a technical guard of the schema, not a product limit).
"""

from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field

from classbeat.agent.interpretation_record import MAX_QUOTE, keep_quote
from classbeat.agent.schemas import STRICT, clean_text

MAX_PINNED: Final = 10
MAX_QUESTIONS: Final = 12
MIN_OPTIONS: Final = 2
MAX_OPTIONS: Final = 5
QUESTION_CHARS: Final = 160
OPTION_CHARS: Final = 60
TITLE_CHARS: Final = 100
ARTIST_CHARS: Final = 100
AT_CHARS: Final = 40
MIN_LETTERS: Final = 2  # a pin's title, artist or place: never a lone letter ("a")
QuestionTopic = Literal["music", "length", "intensity", "structure", "sport", "other"]
QUESTION_TOPICS: Final[tuple[QuestionTopic, ...]] = (
    "music",
    "length",
    "intensity",
    "structure",
    "sport",
    "other",
)


class InterpretedPin(BaseModel):
    """A song the instructor named: its title and artist as they wrote them, and where it goes."""

    model_config = STRICT

    song: Annotated[str, Field(min_length=1, max_length=TITLE_CHARS)]  # the title
    artist: Annotated[str, Field(min_length=1, max_length=ARTIST_CHARS)] | None = None
    at: Annotated[str, Field(min_length=1, max_length=AT_CHARS)] | None = None
    origin: Literal["explicit"]
    quote: Annotated[str, Field(min_length=1, max_length=MAX_QUOTE)] | None = None

    def sanitised(self, request: str) -> "InterpretedPin | None":
        """The pin in the request's own words, or ``None`` when the request does not hold its
        title."""
        title = from_request(self.song, request, TITLE_CHARS)
        if title is None:
            return None
        return self.model_copy(
            update={
                "song": title,
                "artist": from_request(self.artist, request, ARTIST_CHARS),
                "at": from_request(self.at, request, AT_CHARS),
                "quote": keep_quote(self.quote, request),
            }
        )


class InterpretedQuestion(BaseModel):
    """Something the request leaves open whose default changes the class (see the module)."""

    model_config = STRICT

    topic: QuestionTopic
    text: Annotated[str, Field(min_length=1, max_length=QUESTION_CHARS)]
    options: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=OPTION_CHARS)]],
        Field(min_length=MIN_OPTIONS, max_length=MAX_OPTIONS),
    ]
    assumed_index: Annotated[int, Field(ge=0)]  # range-checked by the policy, never a correction

    def sanitised(self) -> "InterpretedQuestion | None":
        """The question with its text and options cleaned (``clean_text``), an option said twice
        (case aside) kept once and ``assumed_index`` following its option; ``None`` when nothing
        usable is left: no text, fewer than ``MIN_OPTIONS`` options, or an assumed index that
        points at no option."""
        text = clean_text(self.text)
        if not text or self.assumed_index >= len(self.options):
            return None
        options: list[str] = []
        assumed = -1
        for index, option in enumerate(self.options):
            kept = clean_text(option)
            known = [o.casefold() for o in options]
            if not kept:
                if index == self.assumed_index:
                    return None
                continue
            if kept.casefold() in known:
                if index == self.assumed_index:
                    assumed = known.index(kept.casefold())
                continue
            if index == self.assumed_index:
                assumed = len(options)
            options.append(kept)
        if len(options) < MIN_OPTIONS or assumed < 0:
            return None
        return self.model_copy(update={"text": text, "options": options, "assumed_index": assumed})


def from_request(words: str | None, request: str, limit: int) -> str | None:
    """The request's own spelling of ``words`` (cleaned, at most ``limit`` characters, with at
    least ``MIN_LETTERS`` letters or digits) when the request holds them as whole words, case
    aside; ``None`` otherwise. Each candidate slice of the request is case-folded on its own, so a
    character whose fold changes its length ("ß") elsewhere in the request changes nothing."""
    wanted = clean_text(words or "")
    if not wanted or len(wanted) > limit or sum(c.isalnum() for c in wanted) < MIN_LETTERS:
        return None
    folded, size = wanted.casefold(), len(wanted)
    for at in range(len(request) - size + 1):
        if at > 0 and request[at - 1].isalnum() and wanted[0].isalnum():
            continue  # starts inside a word
        end = at + size
        if end < len(request) and request[end].isalnum() and wanted[-1].isalnum():
            continue  # ends inside a word
        if request[at:end].casefold() == folded:
            return request[at:end].strip() or None
    return None


def sanitised_pins(pins: list[InterpretedPin], request: str) -> list[InterpretedPin]:
    """The pins the request holds, each once (by title and artist, case aside), in order."""
    out: list[InterpretedPin] = []
    seen: set[tuple[str, str]] = set()
    for pin in pins:
        kept = pin.sanitised(request)
        if kept is None:
            continue
        key = (kept.song.casefold(), (kept.artist or "").casefold())
        if key not in seen:
            seen.add(key)
            out.append(kept)
    return out

"""The composer's part of a reading: pinned songs and follow-up questions (ADR 0038 section 3,
#1185; pure).

Fields of the ``submit_brief`` tool's input (``InterpretedRequest.pinned_songs`` and
``.questions``), read only for a class started with the ``composer_flow`` flag on
(``classbeat.design.interpret_composer_policy``):

- **A pinned song** is a song the instructor named, with where it goes: ``{song, artist?, at?,
  quote?}`` (``song`` is its title: a property named ``title`` would not survive the providers'
  strict schemas, which drop that keyword), origin ``explicit`` only (a song the model thinks would
  fit is not pinned). The title, the artist and ``at`` (the words that say where: "warm-up", "climb
  3", "the last climb") are kept only if the request holds them, case aside, and are then stored in
  the request's own spelling (``sanitised``), so a title or a place the model made up never reaches
  the class. A song whose title the request does not hold is dropped; an artist or a place it does
  not hold is left out. A pinned song is a proposal: the song fill verifies it by search before it
  is used (ADR 0038 section 5).
- **A question** names a field whose value the request leaves open and whose default changes the
  class's shape: ``{path}``, ``length_min`` or ``intensity``. The model writes no question text
  and no assumed value: the page phrases the question from its closed path, and the assumed value
  is the one the brief takes (the policy's). So nothing a model writes as text is stored here but
  the instructor's own words.
"""

from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field

from classbeat.agent.interpretation_record import MAX_QUOTE, keep_quote
from classbeat.agent.schemas import STRICT, clean_text

MAX_PINNED: Final = 10
MAX_QUESTIONS: Final = 2
TITLE_CHARS: Final = 100
ARTIST_CHARS: Final = 100
AT_CHARS: Final = 40
QuestionPath = Literal["length_min", "intensity"]
QUESTION_PATHS: Final[tuple[QuestionPath, ...]] = ("length_min", "intensity")


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
    """A field the request leaves open whose default shapes the class."""

    model_config = STRICT

    path: QuestionPath


def from_request(words: str | None, request: str, limit: int) -> str | None:
    """The request's own spelling of ``words`` (cleaned, at most ``limit`` characters) when the
    request holds them, case aside; ``None`` otherwise."""
    wanted = clean_text(words or "")
    if not wanted or len(wanted) > limit:
        return None
    if len(request.casefold()) != len(request) or len(wanted.casefold()) != len(wanted):
        return None  # a fold that changes the length ("ß") would cut the wrong slice
    at = request.casefold().find(wanted.casefold())
    if at < 0:
        return None
    return request[at : at + len(wanted)].strip() or None


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

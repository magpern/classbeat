"""The music taste of an interpreted request (issue #1133; pure: no database, no network, no model).

``resolve_taste`` turns the interpreter's ``InterpretedTaste`` into the brief's ``MusicTaste`` and
the items of "How we read your request":

- one item per era (``music_era.N``, value ``{"from", "to"}``), the language (``music_language``,
  the ISO code), each style word (``music_genre.N``, no value: the word is its quote, a part of
  the request) and ``music_hits`` (``true``), each with its origin and kept quote;
- the taste is only acted on by a song pick (``{"source": "pick"}``): with any other music (a
  playlist or a mix the instructor named or chose, a running class, no music) the brief carries
  none, and a value the instructor stated is **Not as asked** ``taste_needs_pick``; an inferred
  one is left out quietly.

The song pick then searches per era and checks the chosen songs (``agent.taste_check_policy``).
"""

from typing import Any, Final

from classbeat.agent.interpretation_record import InterpretationItem, Origin, keep_quote
from classbeat.agent.interpreted_taste import InterpretedTaste
from classbeat.agent.music_taste import MusicTaste
from classbeat.agent.stated import Stated

TASTE_NEEDS_PICK: Final = "taste_needs_pick"


def _item(path: str, held: Stated, value: Any, request: str, pick: bool) -> InterpretationItem:
    origin: Origin = held.origin
    reason = None
    if not pick:
        origin, reason = "not_as_asked", TASTE_NEEDS_PICK
    return InterpretationItem(
        path=path,
        origin=origin,
        reason_code=reason,
        value=value,
        quote=keep_quote(held.quote, request),
    )


def resolve_taste(
    read: InterpretedTaste | None,
    request: str,
    music: dict[str, str] | None,
    out: list[InterpretationItem],
) -> MusicTaste | None:
    """The brief's taste (None when there is none or no song pick acts on it); the items go to
    ``out``."""
    if read is None:
        return None
    pick = music is not None and music.get("source") == "pick"
    held: list[tuple[str, Stated, Any]] = [
        (f"music_era.{n}", e, e.closed().view()) for n, e in enumerate(read.eras)
    ]
    if read.language is not None:
        held.append(("music_language", read.language, read.language.value))
    for n, g in enumerate(read.genres):
        word = g.value  # already the request's own words (``InterpretedTaste.sanitised``)
        quote = g.quote or word
        held.append((f"music_genre.{n}", g.model_copy(update={"quote": quote}), None))
    if read.hits is not None:
        held.append(("music_hits", read.hits, True))
    for path, stated, value in held:
        if pick or stated.origin == "explicit":
            out.append(_item(path, stated, value, request, pick))
    if not pick:
        return None
    taste = MusicTaste(
        eras=[e.closed() for e in read.eras],
        language=read.language.value if read.language is not None else None,
        genres=[g.value for g in read.genres],
        hits=read.hits is not None,
    )
    return None if taste.empty() else taste

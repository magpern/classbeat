"""Whether a song pick has a hint of what to play (#1536, owner 2026-11-26; pure: no database, no
network, no model).

Songs are never picked at random. A pick (``{"source": "pick"}``) needs at least one hint the
instructor gave: a taste item of the reading (``music_era.N``, ``music_language``,
``music_genre.N``, ``music_hits``), a pinned song, a source the instructor chose in the form
(``music_override``: a playlist, a mix or "No music", never a pick: "Pick songs for me" says
nothing about the music), or attached playlists (``music_sources``).

With none (``hintless``), the reading always has a music question with no assumed answer
(``reading_question_policy``), the composer's reading leaves its music undecided
(``music_decision.initial``), and the hand-off refuses ``music_unanswered`` before any job or credit
when it would start a song pick now (``classes.interpretation_design``). A composer reading
reaches its sketch with the music left open; ``PUT /music`` enforces the rule there
(``pick_allowed``, #1538: "choose it for me" is refused ``music_unanswered`` without a hint). An
answer to the question (a typed style, a chosen style, a playlist, "No music") makes a new
reading that has one.
"""

from collections.abc import Iterable
from typing import Any, Final

from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.music_taste import MusicTaste

MUSIC_UNANSWERED: Final = "music_unanswered"
PICK: Final = "pick"
_TASTE_PREFIXES: Final = ("music_era.", "music_genre.")
_TASTE_PATHS: Final = frozenset({"music_language", "music_hits"})


def has_hint(
    items: Iterable[InterpretationItem],
    taste: MusicTaste | None,
    *,
    pinned: int,
    override: str | None,
    sources: Any,
) -> bool:
    """Whether a song pick has a hint: any one of the kinds in the module's docstring.
    ``override`` is the source of ``music_override`` (``None`` without one)."""
    if taste is not None and not taste.empty():
        return True
    if any(i.path in _TASTE_PATHS or i.path.startswith(_TASTE_PREFIXES) for i in items):
        return True
    return pinned > 0 or (override is not None and override != PICK) or bool(sources)


def pick_allowed(
    music: dict[str, str] | None,
    items: Iterable[InterpretationItem],
    taste: MusicTaste | None,
    *,
    pinned: int,
    override: str | None,
    sources: Any,
) -> bool:
    """Whether ``PUT /music`` may store ``choice: "pick"`` ("choose it for me") for a reading:
    only with a hint (``has_hint``), or when the reading's music already names a source of its own
    (a playlist or a mix: the reading's music is then that source, never a pick). A reading with
    no music source at all (``music is None``) has none of those, so it needs a hint too."""
    named = music is not None and music.get("source") not in (None, PICK)
    return named or has_hint(items, taste, pinned=pinned, override=override, sources=sources)


def hintless(
    music: dict[str, str] | None,
    items: Iterable[InterpretationItem],
    taste: MusicTaste | None,
    *,
    pinned: int,
    override: str | None,
    sources: Any,
) -> bool:
    """A reading whose music is a song pick with no hint of what to play."""
    if music is None or music.get("source") != PICK:
        return False
    return not has_hint(items, taste, pinned=pinned, override=override, sources=sources)

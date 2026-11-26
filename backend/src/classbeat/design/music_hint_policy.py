"""Whether a song pick has a hint of what to play (#1536, owner 2026-11-26; pure: no database, no
network, no model).

Songs are never picked at random. A pick (``{"source": "pick"}``) needs at least one hint the
instructor gave: a taste item of the reading (``music_era.N``, ``music_language``,
``music_genre.N``, ``music_hits``), a pinned song, an override the instructor chose in the form
(``music_override``, including a pick they chose), or attached playlists (``music_sources``). With
none, the reading's music question is required (it has no assumed answer) and the hand-off is
refused ``music_unanswered`` before any job or credit; an answer to the question (a typed style, a
chosen style, a playlist, "No music") makes a new reading that has one.
"""

from collections.abc import Iterable
from typing import Any, Final

from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.music_taste import MusicTaste

MUSIC_UNANSWERED: Final = "music_unanswered"
_TASTE_PREFIXES: Final = ("music_era.", "music_genre.")
_TASTE_PATHS: Final = frozenset({"music_language", "music_hits"})


def is_pick(music: dict[str, str] | None) -> bool:
    return music is not None and music.get("source") == "pick"


def has_hint(
    items: Iterable[InterpretationItem],
    taste: MusicTaste | None,
    *,
    pinned: int,
    override: Any,
    sources: Any,
) -> bool:
    """Whether a song pick has a hint: any one of the kinds in the module's docstring."""
    if taste is not None and not taste.empty():
        return True
    if any(i.path in _TASTE_PATHS or i.path.startswith(_TASTE_PREFIXES) for i in items):
        return True
    return pinned > 0 or override is not None or bool(sources)

"""Which stored games are retired (ADR 0029 section 3; pure: no database, network or I/O).

The strict ``Game`` of ``classbeat.agent.schemas`` is the four games MOWL offers. A stored draft
whose interval names any other game (in practice Ghost Target, which MOWL no longer offers) is
**legacy**: it can still be read (``classbeat.agent.stored_draft``), but the actions that would
write it to MOWL or reuse it as it is refuse it with ``RETIRED`` before anything looks up the
library's ``GAMES``: Make current, Save, Update, Refit, Change songs and Duplicate. A chat revision
of it turns each such interval into a regular one first (``classbeat.design.revise_base``).

Games are cycling only: a running draft has no ``game`` field, so it is never legacy. The check
reads the stored JSON itself, so it needs no schema and fails closed for any game name outside
the four.
"""

from collections.abc import Iterator, Mapping
from typing import Any, Final, get_args

from classbeat.agent.schemas import Game

RETIRED: Final = "game_retired"  # the refusal reason and the Not as asked item's code
OFFERED: Final[frozenset[str]] = frozenset(get_args(Game))


def _intervals(content: object) -> Iterator[Mapping[str, Any]]:
    segments = content.get("segments") if isinstance(content, Mapping) else None
    for segment in segments if isinstance(segments, list) else ():
        intervals = segment.get("intervals") if isinstance(segment, Mapping) else None
        for interval in intervals if isinstance(intervals, list) else ():
            if isinstance(interval, Mapping):
                yield interval  # pyright: ignore[reportUnknownArgumentType]


def is_retired(game: object) -> bool:
    """Whether an interval's ``game`` value is a game no longer offered (``None`` is no game)."""
    return game is not None and game not in OFFERED


def has_retired_game(content: object) -> bool:
    """Whether a stored draft (``class_revisions.content``) has an interval with a retired game."""
    return any(is_retired(i.get("game")) for i in _intervals(content))

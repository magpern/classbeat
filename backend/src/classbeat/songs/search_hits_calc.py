"""The track dicts of a Spotify search answer, with Spotify's ``explicit`` flag kept (ADR 0040,
#1340). Pure: no I/O.

The library's ``spotify_web.search_tracks`` reduces each Spotify track object to a small dict with
``_track`` and drops the track's ``explicit`` flag. ``hits`` reads the same answer the same way,
through that very reducer, and adds ``explicit`` to a hit only when Spotify sent a JSON boolean
for it: anything else (absent, null, a string, a number) leaves the hit exactly as the library
made it, so an unknown flag is no key at all. The flag is read, never trusted for anything: it
only becomes a report (``spotdown.explicit_model``), and an answer of any other shape gives fewer
or no hits, as the library's own function does.
"""

from collections.abc import Callable
from typing import Any


def hits(answer: object, reduce: Callable[[Any], dict[str, Any] | None]) -> list[dict[str, Any]]:
    """The hits of a ``GET search`` answer (``type=track``), in Spotify's order. ``reduce`` is the
    library's ``_track`` (a playable track's fields, or ``None``)."""
    tracks = answer.get("tracks") if isinstance(answer, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    items = tracks.get("items") if isinstance(tracks, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:  # pyright: ignore[reportUnknownVariableType]
        hit = reduce(item)
        if not hit:
            continue
        flag = item.get("explicit") if isinstance(item, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        out.append({**hit, "explicit": flag} if isinstance(flag, bool) else hit)
    return out

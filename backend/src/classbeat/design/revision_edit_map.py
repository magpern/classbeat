"""The map a Spotify draft's edits are checked against (ADR 0042 section 4, #1424). Pure: no I/O.

A user edit is validated and fitted by ``agent.design.check`` on the music map the draft was
designed on. A studio draft rebuilds that map from the user's own cached analyses (``composed``);
a Spotify draft has none to rebuild it from, because MOWL's sections are read by the worker only
and the web process never reads MOWL. So the worker keeps the map it fitted the draft to **in the
draft itself** (``music.edit_map`` of ``SpotifyRevisionMusic``): the corrected map, with spotdown's
sections already on the tracks that have them, exactly the one the class was fitted to. The web
process reads it back from the revision. Nothing is fetched, so there is no new egress path.

- ``stored`` is the map as a revision keeps it, or ``None`` when it cannot be kept (not JSON, a
  non-finite number, or over ``MAX_BYTES`` as canonical JSON). The draft is then stored without it
  and its editor is read-only; a design never fails for it. ``fields`` gives the draft's
  ``edit_map`` and, for a map dropped for its size alone, ``edit_map_dropped`` (``TOO_LARGE``), so
  the editor says why (``map_too_large``, #1453) instead of ``edit_unavailable``.
- **What it holds** (ADR 0006 section 6, ADR 0007: a revision holds no Spotify id, title or
  artist): the library map less each track's ``spotify_id``, ``title`` and ``artist``. What stays
  is timing only: the version, the length and crossfade, the songs' sections and the events, and
  per track its position, length, tempo and timing class. ``restored`` gives each track neutral
  stand-ins when the edit reads the map back (``check`` reads no identity, a stand-in only keeps
  the library's shape), so nothing about a song is read from, or kept in, the map.
- A ``user_edit`` revision keeps none: its edit map is its nearest ancestor's that is not a
  ``user_edit`` (``classes.spotify_edit_draft``), so up to 200 edits do not copy it 200 times.
"""

import json
from collections.abc import Mapping
from typing import Any, Final, cast

MAX_BYTES: Final = 262144  # as ``SpotdownSections.record``: a stored analysis document
IDENTITY: Final = ("spotify_id", "title", "artist")  # what names a song: never stored
TOO_LARGE: Final = "map_too_large"


def size(value: object) -> int | None:
    """The canonical JSON size of ``value`` in bytes, ``None`` when it is not finite JSON."""
    try:
        return len(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        )
    except (TypeError, ValueError):
        return None


def _stripped(music_map: Mapping[str, Any]) -> dict[str, Any] | None:
    """A plain copy of ``music_map`` less each track's identity; ``None`` when it is not JSON."""
    try:
        copied = cast(dict[str, Any], json.loads(json.dumps(music_map)))
    except (TypeError, ValueError):
        return None
    tracks = copied.get("tracks")
    if isinstance(tracks, list):
        for track in cast(list[Any], tracks):
            if isinstance(track, dict):
                for key in IDENTITY:
                    cast(dict[str, Any], track).pop(key, None)
    return copied


def stored(music_map: Mapping[str, Any]) -> dict[str, Any] | None:
    """``music_map`` as the revision keeps it (a plain copy), or ``None`` (module docstring)."""
    copied = _stripped(music_map)
    found = None if copied is None else size(copied)
    if found is None or found > MAX_BYTES:
        return None
    return copied


def fields(music_map: Mapping[str, Any], running: bool) -> dict[str, Any]:
    """A Spotify draft's ``edit_map`` and ``edit_map_dropped`` (module docstring). A running draft
    keeps no map and names no drop; a map that is not finite JSON names none either (the library
    never makes one, so it is no reason a user could act on)."""
    if running:
        return {"edit_map": None}
    kept = stored(music_map)
    if kept is not None:
        return {"edit_map": kept}
    copied = _stripped(music_map)
    found = None if copied is None else size(copied)
    too_large = found is not None and found > MAX_BYTES
    return {"edit_map": None, "edit_map_dropped": TOO_LARGE if too_large else None}


def restored(edit_map: Mapping[str, Any]) -> dict[str, Any]:
    """``edit_map`` as the library reads it: each track given neutral stand-ins for the identity
    that ``stored`` took out (the module docstring). A copy; the stored map is not changed."""
    out = cast(dict[str, Any], json.loads(json.dumps(edit_map)))
    tracks = out.get("tracks")
    if isinstance(tracks, list):
        for n, track in enumerate(cast(list[Any], tracks), start=1):
            if isinstance(track, dict):
                cast(dict[str, Any], track).update(
                    spotify_id=f"track-{n}", title=f"Track {n}", artist=""
                )
    return out

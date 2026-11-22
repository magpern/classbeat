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
  and its editor is read-only (``edit_unavailable``); a design never fails for it.
- A ``user_edit`` revision keeps none: its edit map is its nearest ancestor's that is not a
  ``user_edit`` (``classes.spotify_edit_draft``), so up to 200 edits do not copy it 200 times.
"""

import json
from collections.abc import Mapping
from typing import Any, Final, cast

MAX_BYTES: Final = 262144  # as ``SpotdownSections.record``: a stored analysis document


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


def stored(music_map: Mapping[str, Any]) -> dict[str, Any] | None:
    """``music_map`` as the revision keeps it (a plain copy), or ``None`` (module docstring)."""
    found = size(music_map)
    if found is None or found > MAX_BYTES:
        return None
    return cast(dict[str, Any], json.loads(json.dumps(music_map)))

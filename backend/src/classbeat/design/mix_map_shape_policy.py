"""Whether a mix's music map has the library's current shape (ADR 0016 section 3 step 2; ADR 0040
section 5). Pure: no I/O.

``valid_map`` is the one check a mix map passes before it is stored or used: it has the library's
current map version and the shape every later step reads (songs with a BPM and a beat grid,
events, a duration), and it fits the table (``swedebeat_mix_maps.map``, 1 MiB of jsonb text).
spotdown's mix map (``spotdown.mix_map_policy``) is checked here by ``design.mix_spotdown``. (The
local analysis that used to make such a map is gone: ADR 0040 amendment, #1357.)
"""

import math
from collections.abc import Mapping
from typing import Any, Final

from classbeat.jsonb_text_calc import jsonb_text_size

MAX_MAP_BYTES: Final = 1 << 20  # the table's check on ``swedebeat_mix_maps.map``
MAX_SONGS: Final = 200
MAX_EVENTS: Final = 2000


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def valid_map(candidate: object, version: int) -> bool:
    """Whether ``candidate`` is a music map of the library's current ``version`` that fits the
    table and has the fields the design reads (songs with a BPM and a beat grid, events, a
    duration)."""
    if not isinstance(candidate, dict):
        return False
    m: Mapping[str, Any] = candidate  # pyright: ignore[reportUnknownVariableType]
    songs, events = m.get("songs"), m.get("events")
    if m.get("version") != version or not _number(m.get("duration")) or m["duration"] <= 0:
        return False
    if not isinstance(songs, list) or not 0 < len(songs) <= MAX_SONGS:
        return False
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        return False
    for song in songs:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(song, dict) or not all(
            _number(song.get(k))  # pyright: ignore[reportUnknownMemberType]
            for k in ("start", "end", "bpm", "first_downbeat")
        ):
            return False
    for event in events:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(event, dict) or not _number(event.get("t")):  # pyright: ignore[reportUnknownMemberType]
            return False
    try:  # the table's own measure: jsonb's text, not compact JSON (SR C5)
        return jsonb_text_size(m) <= MAX_MAP_BYTES
    except (TypeError, ValueError):
        return False

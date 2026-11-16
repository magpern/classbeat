"""The rules of a studio track's analysis (ADR 0036 section 4.1, SR C4). Pure: no I/O.

- **The map** (``valid_track_map``) is what ``mowl.track.analyze_track`` returns for one song: the
  library's ``TRACK_VERSION``, a positive duration within ``MAX_TRACK_SECONDS``, exactly one song
  with a BPM and a beat grid, at most ``MAX_EVENTS`` events each at a time, and sections each with
  a start, an end and an energy (a number or ``None``). It must fit the table's 1 MiB check,
  measured as the table measures it (``jsonb_text_calc``, SR C5). The child's answer is checked
  against this before anything is stored or used: never model text, never the file's own words.
- **The references** (``wanted``): the approved tracks' catalogue references, each of the
  gateway's shape (``track_policy.is_reference``), in order, without repeats, at most
  ``MAX_TRACKS`` (the mix spec's bound).
- **The limits**: ``MAX_TRACK_SECONDS`` is how much audio one track may decode to (a 60 MB MP3 at
  128 kbit/s is about an hour); a longer file fails in the child.
"""

import math
from collections.abc import Iterable, Mapping
from typing import Any, Final

from classbeat.jsonb_text_calc import jsonb_text_size
from classbeat.swedebeat_gateway.track_policy import is_reference

MAX_TRACKS: Final = 40
MAX_TRACK_SECONDS: Final = 3600.0
MAX_EVENTS: Final = 2000
MAX_SECTIONS: Final = 500
MAX_MAP_BYTES: Final = 1 << 20  # the table's check on ``swedebeat_track_maps.map``
SONG_FIELDS: Final = ("start", "end", "bpm", "first_downbeat")


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _all_numbers(item: object, fields: Iterable[str]) -> bool:
    if not isinstance(item, dict):
        return False
    m: Mapping[str, Any] = item  # pyright: ignore[reportUnknownVariableType]
    return all(_number(m.get(k)) for k in fields)


def _section(item: object) -> bool:
    if not _all_numbers(item, ("start", "end")):
        return False
    energy = item.get("energy")  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
    return energy is None or _number(energy)


def valid_track_map(candidate: object, version: int) -> bool:
    """Whether ``candidate`` is a track map of the library's ``version`` that fits the table and
    has the fields the studio reads."""
    if not isinstance(candidate, dict):
        return False
    m: Mapping[str, Any] = candidate  # pyright: ignore[reportUnknownVariableType]
    duration, songs = m.get("duration"), m.get("songs")
    events, sections = m.get("events"), m.get("sections")
    if m.get("track_version") != version or isinstance(m.get("track_version"), bool):
        return False
    if not isinstance(duration, int | float) or not _number(duration):
        return False
    if not 0 < duration <= MAX_TRACK_SECONDS:
        return False
    if not isinstance(songs, list) or len(songs) != 1:  # pyright: ignore[reportUnknownArgumentType]
        return False
    if not _all_numbers(songs[0], SONG_FIELDS) or songs[0]["bpm"] <= 0:
        return False
    if not isinstance(events, list) or len(events) > MAX_EVENTS:  # pyright: ignore[reportUnknownArgumentType]
        return False
    if not all(_all_numbers(e, ("t",)) for e in events):  # pyright: ignore[reportUnknownVariableType]
        return False
    if not isinstance(sections, list) or len(sections) > MAX_SECTIONS:  # pyright: ignore[reportUnknownArgumentType]
        return False
    if not all(_section(s) for s in sections):  # pyright: ignore[reportUnknownVariableType]
        return False
    try:  # the table's own measure: jsonb's text, not compact JSON (SR C5)
        size = jsonb_text_size(m)
    except (TypeError, ValueError):
        return False
    return size <= MAX_MAP_BYTES


def wanted(references: Iterable[str]) -> list[str]:
    """The references to analyse, in order and once each; ``ValueError`` for one that is not a
    catalogue reference or for more than ``MAX_TRACKS``."""
    out: list[str] = []
    for reference in references:
        if not is_reference(reference):
            raise ValueError("not a catalogue reference")
        if reference not in out:
            out.append(reference)
    if len(out) > MAX_TRACKS:
        raise ValueError("too many tracks")
    return out

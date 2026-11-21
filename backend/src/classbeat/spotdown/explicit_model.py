"""Explicit lyrics, per track of a song list: what is known and where it came from (ADR 0040
section 6, its #1340 amendment). Pure: no I/O.

**Report only.** The tag a class shows says "this song may not suit a public setting". Nothing is
blocked, filtered, ranked, reordered or swapped on it, and no model sees it.

Two sources, kept apart in the list's provenance under ``explicit`` (beside ``spotdown_analysis``):

- ``spotify``: Spotify's own track ``explicit`` flag, from the search hit the song was picked
  from (``True`` or ``False``; absent when Spotify sent none);
- ``spotdown``: spotdown's ``content.explicit`` (``value`` ``yes``, ``no`` or ``unknown``, its
  ``confidence`` and its ``evidence``), kept only for a song whose timing is spotdown's and only
  when the document carries it (an older document has no ``content``: absent, not ``unknown``).

A song shows the tag when **either** source says ``yes`` (``Evidence.shown``): a caution flag errs
toward flagging, as spotdown's own rule does. ``unknown``, ``no`` and nothing at all show nothing.
Both ends are strict: a value of the wrong type is not evidence. ``record`` omits a song without
evidence, and ``{}`` when no song has any, so a list without a flag is stored as before.

A kept song of a "Change songs" list carries its parent's evidence for its slot
(``carried_evidence``), as its timing is carried (``song_carry_calc``).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from classbeat.spotdown.song_sections_calc import entries_by_position

KEY: Final = "explicit"
RECORD_VERSION: Final = 1
VALUES: Final = frozenset({"yes", "no", "unknown"})
SOURCES: Final = frozenset({"caller", "caller_match", "youtube_age_limit"})
MAX_EVIDENCE: Final = 3
_STORED: Final = frozenset({"value", "confidence", "evidence"})


@dataclass(frozen=True, slots=True)
class Evidence:
    """What is known of one song: Spotify's flag and spotdown's strict ``content.explicit``."""

    spotify: bool | None = None
    spotdown: Mapping[str, Any] | None = None  # as ``spotdown_group`` gives it

    def shown(self) -> bool:
        return self.spotify is True or (self.spotdown or {}).get("value") == "yes"

    def known(self) -> bool:
        return self.spotify is not None or self.spotdown is not None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if 0 <= value <= 1 else None


def _evidence_item(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict) or set(item) != {"source", "value"}:  # pyright: ignore[reportUnknownArgumentType]
        return None
    source, value = item["source"], item["value"]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(source, str) or source not in SOURCES:  # type first: a list is unhashable
        return None
    if source == "youtube_age_limit":
        ok = isinstance(value, int) and not isinstance(value, bool) and 18 <= value <= 99
    else:
        ok = isinstance(value, bool)
    return {"source": source, "value": value} if ok else None


def _group(group: object, keys: frozenset[str]) -> dict[str, Any] | None:
    if not isinstance(group, dict) or set(group) != keys:  # pyright: ignore[reportUnknownArgumentType]
        return None
    data: dict[str, Any] = {str(k): v for k, v in group.items()}  # pyright: ignore[reportUnknownVariableType]
    value, confidence, raw = data["value"], _number(data["confidence"]), data["evidence"]
    if not isinstance(value, str) or value not in VALUES or confidence is None:
        return None
    if not isinstance(raw, list) or len(raw) > MAX_EVIDENCE:  # pyright: ignore[reportUnknownArgumentType]
        return None
    items = [_evidence_item(i) for i in raw]  # pyright: ignore[reportUnknownVariableType]
    if any(i is None for i in items):
        return None
    return {"value": value, "confidence": confidence, "evidence": items}


def spotdown_group(group: object) -> dict[str, Any] | None:
    """spotdown's group as the record keeps it (``value``, ``confidence``, ``evidence``: exactly
    these keys, each of the contract's type and range), or ``None`` for anything else. Total: it
    never raises, whatever the input."""
    return _group(group, _STORED)


def document_group(doc: Mapping[str, Any]) -> dict[str, Any] | None:
    """``content.explicit`` of an analysis document as the record keeps it, or ``None`` (not
    reported, or not exactly the schema's shape: its ``method`` is ``metadata``)."""
    content = doc.get("content")
    found = content.get("explicit") if isinstance(content, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(found, dict) or found.get("method") != "metadata":  # pyright: ignore[reportUnknownMemberType]
        return None
    return _group({k: v for k, v in found.items() if k != "method"}, _STORED)  # pyright: ignore[reportUnknownVariableType]


def spotify_flag(value: object) -> bool | None:
    """Spotify's flag when it is a boolean; anything else is unknown."""
    return value if isinstance(value, bool) else None


def _entry(position: int, ev: Evidence) -> dict[str, Any]:
    entry: dict[str, Any] = {"position": position}
    if ev.spotify is not None:
        entry["spotify"] = ev.spotify
    if ev.spotdown is not None:
        entry["spotdown"] = dict(ev.spotdown)
    return entry


def record(evidence: Mapping[int, Evidence]) -> dict[str, Any]:
    """``{KEY: record}`` for the songs with evidence (by 0-based position); ``{}`` when none."""
    songs = [_entry(p, evidence[p]) for p in sorted(evidence) if evidence[p].known()]
    return {KEY: {"v": RECORD_VERSION, "songs": songs}} if songs else {}


def evidence_of(provenance: Mapping[str, Any]) -> dict[int, Evidence]:
    """The evidence a list's provenance records, by position; strict, so an entry of any other
    shape is no evidence. Empty for a list without the record."""
    held = provenance.get(KEY)
    songs: Sequence[Any] = held.get("songs", ()) if isinstance(held, dict) else ()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    found: dict[int, Evidence] = {}
    for entry in songs:
        if not isinstance(entry, dict):
            continue
        position = entry.get("position")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not isinstance(position, int) or isinstance(position, bool) or position in found:
            continue
        ev = Evidence(spotify_flag(entry.get("spotify")), spotdown_group(entry.get("spotdown")))  # pyright: ignore[reportUnknownMemberType]
        if ev.known():
            found[position] = ev
    return found


def shown_positions(provenance: Mapping[str, Any]) -> frozenset[int]:
    """The 0-based positions of the songs that show the tag, from a list's provenance."""
    return frozenset(p for p, ev in evidence_of(provenance).items() if ev.shown())


def analysis_groups(analysis: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """spotdown's group of each song of an analysis record (``spotdown_analysis``), by position."""
    groups = (
        (p, spotdown_group(e.get("explicit"))) for p, e in entries_by_position(analysis).items()
    )
    return {p: g for p, g in groups if g is not None}


def evidence_for(
    listed: Sequence[tuple[str, bool]],
    spotify: Mapping[str, bool],
    spotdown: Mapping[int, Mapping[str, Any]],
    parent: Mapping[int, Evidence],
    kept: Sequence[tuple[int, str]],
) -> dict[int, Evidence]:
    """The evidence of each song of the new list. ``listed``: each song's ``(spotify_id, new)`` in
    order. A new song has Spotify's flag from its search hit (``spotify``, by id) and spotdown's
    group from its analysis (``spotdown``, by position). A kept song (``kept``: its slot and
    Spotify id, still in that slot and not new) has its parent list's evidence for the slot."""
    held = {slot for slot, sid in kept if 0 <= slot < len(listed) and listed[slot] == (sid, False)}
    found: dict[int, Evidence] = {}
    for n, (spotify_id, new) in enumerate(listed):
        if new:
            found[n] = Evidence(spotify.get(spotify_id), spotdown.get(n))
        elif n in held and n in parent:
            found[n] = parent[n]
    return found

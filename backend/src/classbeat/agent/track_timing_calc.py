"""Each track's timing class as the design map carries it (ADR 0040 section 3, plan section 2).
Pure: no I/O.

A Spotify playlist's map (``model_input.music_map``) lists its tracks in ``tracks``; a track whose
music data came through spotdown carries two keys there, written only by ``stamp``:

- ``timing``: the class ``timing_class_policy`` decided for it (``exact``, ``approximate`` or
  ``unusable``);
- ``timing_source``: the analysis's ``provenance.source.kind`` (``upload``, ``url``, ``youtube``).

``classes`` reads them for the design validator (``timing_gate``) and the model's input
(``timing_input``), and is strict:

- a track with neither key is ``exact``: every map made before SD-P6 (MOWL's Spotify sections, a
  Swedebeat mix, which has no ``tracks`` at all) reads exactly as before;
- a ``timing`` that is not one of the three classes is ``unusable``, the strictest;
- a ``youtube`` source is **at best ``approximate``**, whatever ``timing`` says (ADR 0040 section 3:
  a confident match, an equal ISRC and agreeing anchors are no proof of identical audio), and a
  ``timing_source`` that is not a string is treated the same way.

``anchors`` are the times a cue on a non-``exact`` track may sit near: the track's section starts
and ends and, for an ``approximate`` track, its drops and breakdowns (events within the ±2 s
window of ``spotdown_map_policy``). An ``unusable`` track's analysis is discarded, so its events
are never an anchor.
"""

import copy
from collections.abc import Mapping
from typing import Any, Final, cast

from classbeat.spotdown.timing_inputs_model import TimingClass

TIMING_KEY: Final = "timing"
SOURCE_KEY: Final = "timing_source"
CLASSES: Final[frozenset[str]] = frozenset({"exact", "approximate", "unusable"})
NEVER_EXACT_SOURCES: Final[frozenset[str]] = frozenset({"youtube"})
EVENT_KINDS: Final = frozenset({"drop", "breakdown"})


def capped(timing: object, source: object) -> TimingClass:
    """The class a track's two keys give: unknown is ``unusable``; YouTube never ``exact``."""
    if timing is None and source is None:
        return "exact"
    cls: TimingClass = (
        cast(TimingClass, timing) if isinstance(timing, str) and timing in CLASSES else "unusable"
    )
    if cls == "exact" and (not isinstance(source, str) or source in NEVER_EXACT_SOURCES):
        return "approximate"
    return cls


def stamp(m: Mapping[str, Any], index: int, timing: TimingClass, source: str) -> dict[str, Any]:
    """A copy of ``m`` whose track ``index`` (0-based) carries its class and source, capped as
    ``classes`` reads it, so a stored map never says ``exact`` for a YouTube analysis."""
    out = copy.deepcopy(dict(m))
    track = out["tracks"][index]
    track[TIMING_KEY] = capped(timing, source)
    track[SOURCE_KEY] = source
    return out


def classes(m: Mapping[str, Any]) -> dict[int, TimingClass]:
    """The 0-based index and class of every track that is not ``exact``; empty for a map that
    carries no class (every map before SD-P6) and for a mix (no ``tracks``)."""
    out: dict[int, TimingClass] = {}
    for track in m.get("tracks") or ():
        cls = capped(track.get(TIMING_KEY), track.get(SOURCE_KEY))
        if cls != "exact":
            out[int(track["track"]) - 1] = cls
    return out


def playing_at(m: Mapping[str, Any], t: float) -> frozenset[int]:
    """The 0-based index of every track that plays at ``t``: both inside a crossfade."""
    return frozenset(int(s["track"]) - 1 for s in m["songs"] if s["start"] <= t <= s["end"])


def anchors(m: Mapping[str, Any], index: int, cls: TimingClass) -> tuple[float, ...]:
    """Track ``index``'s section starts and ends, and for an ``approximate`` track its drops and
    breakdowns, sorted."""
    number = index + 1
    sections = [s for s in m["songs"] if s["track"] == number]
    points = {float(s["start"]) for s in sections} | {float(s["end"]) for s in sections}
    if cls == "approximate":
        songs = {s["song"] for s in sections}
        points |= {
            float(e["t"])
            for e in m.get("events") or ()
            if e.get("song") in songs and e.get("kind") in EVENT_KINDS
        }
    return tuple(sorted(points))

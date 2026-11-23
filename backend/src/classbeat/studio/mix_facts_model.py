"""What the mix spec's rules know of one approved track (``TrackFacts``) and the app's time unit
(``cs``). Pure: no I/O.

``TrackFacts`` holds the track's length, BPM and first downbeat from its **stored** analysis,
never a model's number, and, for #1328, the track's **stored** timing class and its cut candidates
(``spotdown.cut_candidates_calc``). A track with no stored timing decision (``None``) has no
candidates; a spotdown analysis has its decision's class and the candidates stored with its map.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from classbeat.spotdown import low_band_model
from classbeat.spotdown.cut_candidates_model import MAP_KEY, Candidate, parse
from classbeat.spotdown.timing_inputs_model import TimingClass

CS: Final = 100  # hundredths of a second per second (``mowl.compose.CS``)


def cs(sec: float) -> int:
    """Seconds as whole hundredths, rounded exactly as ``mowl.compose._cs`` rounds them."""
    return round(float(sec) * CS)


@dataclass(frozen=True, slots=True)
class TrackFacts:
    """One approved track as its stored analysis has it."""

    length: float
    bpm: float
    first_downbeat: float
    timing_class: TimingClass | None = None  # ``None``: no stored timing decision
    candidates: tuple[Candidate, ...] = ()
    low_band: low_band_model.Curve | None = None  # ``None``: not known (no bass swap)


def stored_candidates(raw: object, timing_class: TimingClass | None) -> tuple[Candidate, ...]:
    """A track map's stored candidates as ``TrackFacts`` takes them, trust inherited again from
    the stored timing class: none without a decision or for ``unusable``, all advisory for
    ``approximate``, and none at all when the stored list does not read whole."""
    if timing_class not in ("exact", "approximate"):
        return ()
    found = parse(raw)
    if found is None:
        return ()
    if timing_class == "exact":
        return found
    return tuple(Candidate(c.at_s, c.kind, c.confidence, c.method, False, c.label) for c in found)


def of_map(music_map: Mapping[str, Any], timing_class: TimingClass | None) -> TrackFacts:
    """A stored track map's facts: its one song's BPM and first downbeat, and what the stored
    timing class lets through (candidates; the low band of an ``exact`` track only)."""
    (song,) = music_map["songs"]
    low = low_band_model.parse(music_map.get(low_band_model.MAP_KEY))
    return TrackFacts(
        float(music_map["duration"]),
        float(song["bpm"]),
        float(song["first_downbeat"]),
        timing_class,
        stored_candidates(music_map.get(MAP_KEY), timing_class),
        low if timing_class == "exact" else None,
    )

"""spotdown's ``analysis-v1`` as classbeat's existing music data (ADR 0040 sections 3 and 5, plan
sections 2 and 4). Pure: no I/O.

- ``track_map``: an **``exact``** track as the studio's track map (``mowl.track.analyze_track``'s
  shape, checked by ``design.track_map_policy.valid_track_map``): one song with its BPM and first
  downbeat, the drops and breakdowns, and the sections with each one's energy rank. Only an
  ``exact`` track gets beats and bars, and in policy v1 only one that **is the played recording**
  (path (a)): its file clock is the played clock. A beat-equivalent track (path (b)) matches the
  played audio only after the comparison's offset, which v1 does not carry, so it gets no map.
- ``reference_sections``: an **``approximate``** track as MOWL's Spotify sections (``{start,
  duration, loudness}`` on the Spotify clock, what ``mowl.spotify.build_map`` turns into the
  playlist map of ``MusicData``), moved by the alignment's ``offset_s``; its events carry the
  ``APPROXIMATE_WINDOW_S`` window and no beat. ``loudness`` is the section's mean normalised
  energy times ``LOUDNESS_SCALE``, so that spotdown's ``ENERGY_SPREAD_MIN`` (0.05) is MOWL's
  1.5 dB spread: ``build_map`` uses only its rank and its steps.
- the facts ``track_fit_policy`` checks are in ``track_facts_calc``.
- the hints (``analysis_hints_policy``): every ``heuristic`` or low-confidence feature, for
  the model, marked as hints; nothing here validates with them (``feature_trust_policy``).

Anything that is not there or not trusted is left out; a track without a trusted BPM (or, for
``track_map``, beats) is ``MapRefused``, so the caller keeps today's path for it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown import analysis_features_calc as fc
from classbeat.spotdown import analysis_hints_policy
from classbeat.spotdown import feature_trust_policy as ft
from classbeat.spotdown.timing_class_policy import TimingDecision

POLICY_VERSION: Final = "1"
MAX_EVENTS: Final = 2000  # as ``design.track_map_policy``
MAX_SECTIONS: Final = 500
TRACK_EVENT_KINDS: Final = frozenset({"drop", "breakdown"})  # the track map's own kinds
APPROXIMATE_WINDOW_S: Final = 2.0
LOUDNESS_SCALE: Final = 1.5 / fc.ENERGY_SPREAD_MIN  # MOWL's MIN_ENERGY_SPREAD_DB per spread

Document = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MapRefused:
    reason: str


@dataclass(frozen=True, slots=True)
class MappedTrack:
    track_map: dict[str, object]
    other_events: tuple[dict[str, object], ...]  # trusted, of kinds the track map has none of
    hints: tuple[analysis_hints_policy.Hint, ...]


@dataclass(frozen=True, slots=True)
class ReferenceSections:
    bpm: float
    sections: tuple[dict[str, object], ...]
    events: tuple[dict[str, object], ...]
    hints: tuple[analysis_hints_policy.Hint, ...]


def _first_downbeat(grid: Mapping[str, object], first_beat: float) -> tuple[float, str]:
    downbeats = ft.obj(grid, "downbeats")
    times = (
        None
        if downbeats is None or not ft.trusted(downbeats)
        else ft.numbers(downbeats.get("times_s"))
    )
    return (times[0], "downbeats") if times else (first_beat, "first_beat")


def track_map(
    doc: Document, decision: TimingDecision, track_version: int
) -> MappedTrack | MapRefused:
    """An ``exact`` track's studio track map."""
    if decision.timing_class != "exact":
        return MapRefused("not_exact")
    if decision.reason != "played_recording":
        return MapRefused("not_the_played_clock")  # path (b): its offset is not carried in v1
    duration, bpm = fc.read_duration(doc), fc.read_bpm(doc)
    if duration is None:
        return MapRefused("no_duration")
    if bpm is None:
        return MapRefused("bpm_untrusted")
    grid = ft.obj(doc, "grid") or {}
    beats = ft.obj(grid, "beats")
    times = None if beats is None or not ft.trusted(beats) else ft.numbers(beats.get("times_s"))
    if not times:
        return MapRefused("no_beats")
    first_downbeat, source = _first_downbeat(grid, times[0])
    events = [e for e in fc.trusted_events(doc) if e.t <= duration]
    track_events: list[dict[str, object]] = [
        {"t": round(e.t, 3), "kind": e.kind, "strength": e.strength, "song": 0}
        for e in events
        if e.kind in TRACK_EVENT_KINDS
    ]
    spans = fc.section_spans(doc, duration)
    if len(track_events) > MAX_EVENTS or len(spans) > MAX_SECTIONS:
        return MapRefused("too_many_features")
    sections = [
        {"start": round(a, 3), "end": round(b, 3), "energy": e}
        for (a, b), e in zip(spans, fc.energy_ranks(doc, spans), strict=True)
    ]
    song = {
        "start": 0.0,
        "end": round(duration, 3),
        "bpm": round(bpm, 3),
        "first_downbeat": round(first_downbeat, 4),
        "first_downbeat_source": source,
    }
    others = tuple(
        {"t": round(e.t, 3), "kind": e.kind, "strength": e.strength}
        for e in events
        if e.kind not in TRACK_EVENT_KINDS
    )
    mapped: dict[str, object] = {
        "duration": round(duration, 3),
        "songs": [song],
        "events": track_events,
        "sections": sections,
        "track_version": track_version,
    }
    return MappedTrack(mapped, others, analysis_hints_policy.hints(doc))


def _offset(doc: Document) -> tuple[float, float] | None:
    alignment = ft.obj(doc, "alignment")
    if alignment is None:
        return None
    offset = ft.num(alignment.get("offset_s"), -3600.0, 3600.0)
    reference = ft.obj(alignment, "reference")
    length = None if reference is None else ft.num(reference.get("duration_s"), 0.001, 7200.0)
    return None if offset is None or length is None else (offset, length)


def reference_sections(doc: Document, decision: TimingDecision) -> ReferenceSections | MapRefused:
    """An ``approximate`` track as MOWL-shaped sections on the reference (Spotify) clock."""
    if decision.timing_class != "approximate":
        return MapRefused("not_approximate")
    duration, bpm, moved = fc.read_duration(doc), fc.read_bpm(doc), _offset(doc)
    if duration is None or moved is None:
        return MapRefused("no_alignment" if moved is None else "no_duration")
    if bpm is None:
        return MapRefused("bpm_untrusted")
    offset, length = moved
    spans = fc.section_spans(doc, duration)
    curve = fc.energy_curve(doc)
    sections: list[dict[str, object]] = []
    for a, b in spans:
        start, end = max(a + offset, 0.0), min(b + offset, length)
        if end - start <= 0:
            continue
        level = None if curve is None else fc.span_mean(curve, a, b)
        loudness = None if level is None else round(level * LOUDNESS_SCALE, 2)
        sections.append(
            {"start": round(start, 3), "duration": round(end - start, 3), "loudness": loudness}
        )
    if not sections or len(sections) > MAX_SECTIONS:
        return MapRefused("no_sections" if not sections else "too_many_features")
    events = tuple(
        {"t": round(e.t + offset, 3), "kind": e.kind, "window_s": APPROXIMATE_WINDOW_S}
        for e in fc.trusted_events(doc)
        if 0 <= e.t + offset <= length
    )
    return ReferenceSections(
        round(bpm, 3), tuple(sections), events, analysis_hints_policy.hints(doc)
    )

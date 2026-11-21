"""spotdown's ``analysis-v1`` of a SwedeBeat mix as the library's mix map (ADR 0040 sections 3 and
5, plan sections 2, 4 and 7; SD-P7a, #1304). Pure: no I/O.

``mix_map`` turns an **``exact``** document of the played mix (path (a): the analysed bytes are the
mix that is played, so the file's clock is the class's clock) into the shape
``mowl.music.analyze_signal`` gives and ``design.mixanalysis.valid_map`` checks: ``version``,
``duration``, ``songs`` (``song``, ``start``, ``end``, ``bpm``, ``first_downbeat``,
``first_phrase``, ``phrase_bars``, ``grid_score``, ``beat1_margin``) and ``events`` (drops and
breakdowns with ``t``, ``precise``, ``kind``, ``strength`` and their ``song``).

spotdown has no notion of a song, so the songs are read as its quality harness reads them for the
mix gates (the reading accepted by the Architect, spotdown ``docs/quality-harness.md``): **a song
starts and ends at the content edges and at the structure boundaries**, and **its BPM is the tempo
at its midpoint** from ``tempo.bpm`` and its change points. Here only **trusted** features count
(``feature_trust_policy``): a boundary or change point that is ``heuristic`` or below the trust
threshold is left out, so a mix map never rests on a hint.

- A span with no trusted beat cannot carry a grid: it joins the song before it (the first span
  joins the next one), so every song has a beat grid.
- ``first_downbeat`` is the first trusted downbeat in the song, else its first beat. spotdown's
  downbeats are ``heuristic`` today (analyzer 6.3), so beat 1 is then **an estimate** and
  ``beat1_margin`` is 0 (below the library's ``LOW_MARGIN``: the class shows "beat 1 is an
  estimate" for that song, as for a doubtful local vote); a trusted downbeat gives
  ``TRUSTED_MARGIN``.
- spotdown's phrases are ``heuristic`` too: ``first_phrase`` is the song's beat 1 and
  ``phrase_bars`` the library's 8, so the phrase grid adds nothing beyond the bars.
- ``grid_score`` is the beat grid's confidence.
- A drop or breakdown outside every song (before the content start, from the last song's end on)
  is left out: it belongs to no song.

Anything missing or not trusted is ``MapRefused`` with a code (no duration, no trusted BPM or
beats, too many songs or events), and the caller analyses the mix locally.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from classbeat.spotdown import analysis_features_calc as fc
from classbeat.spotdown import feature_trust_policy as ft
from classbeat.spotdown.spotdown_map_policy import MapRefused
from classbeat.spotdown.timing_class_policy import TimingDecision

POLICY_VERSION: Final = "1"
MAX_SONGS: Final = 200  # as ``design.mixanalysis``
MAX_EVENTS: Final = 2000
MIX_EVENT_KINDS: Final = frozenset({"drop", "breakdown"})  # the mix map's own kinds
PHRASE_BARS: Final = 8  # ``mowl.music.PHRASE_BARS``
TRUSTED_MARGIN: Final = 9.99  # the library's "no runner-up" margin
ESTIMATED_MARGIN: Final = 0.0

Document = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MappedMix:
    music_map: dict[str, object]


def _content(doc: Document, duration: float) -> tuple[float, float]:
    signal = ft.obj(doc, "signal")
    if signal is None or not ft.trusted(signal):
        return 0.0, duration
    start = ft.num(signal.get("content_start_s"), 0.0, duration)
    end = ft.num(signal.get("content_end_s"), 0.0, duration)
    if start is None or end is None or end <= start:
        return 0.0, duration
    return start, end


def _cuts(doc: Document, start: float, end: float) -> list[float]:
    structure = ft.obj(doc, "structure")
    segments = ft.items(None if structure is None else structure.get("segments"))
    cuts = {start, end}
    for segment in segments:
        at = ft.num(segment.get("start_s"))
        if at is not None and start < at < end and ft.trusted(segment, "boundary_confidence"):
            cuts.add(at)
    return sorted(cuts)


def _timeline(doc: Document, bpm: float) -> list[tuple[float, float]]:
    tempo = ft.obj(doc, "tempo") or {}
    changes: list[tuple[float, float]] = []
    for point in ft.items(tempo.get("change_points")):
        at, value = ft.num(point.get("time_s"), 0.0), ft.num(point.get("bpm"), 1.0, 400.0)
        if at is not None and value is not None and ft.trusted(point):
            changes.append((at, value))
    return [(0.0, bpm), *sorted(changes)]


def bpm_at(timeline: Sequence[tuple[float, float]], t: float) -> float:
    """The tempo in force at ``t``: the last change point at or before it (the harness's
    ``bpm_at``)."""
    current = timeline[0][1]
    for at, bpm in timeline:
        if at <= t:
            current = bpm
    return current


def _trusted_times(grid: Mapping[str, object], key: str) -> list[float]:
    feature = ft.obj(grid, key)
    times = (
        None if feature is None or not ft.trusted(feature) else ft.numbers(feature.get("times_s"))
    )
    return sorted(times or [])


def _spans(cuts: list[float], beats: list[float]) -> list[tuple[float, float]]:
    """The songs' spans: a span without a beat joins the one before (the first, the next)."""
    out: list[tuple[float, float]] = []
    carried: float | None = None
    for a, b in pairwise(cuts):
        if not any(a <= t < b for t in beats):
            if out:
                out[-1] = (out[-1][0], b)
            elif carried is None:
                carried = a
            continue
        out.append((a if carried is None else carried, b))
        carried = None
    return out


def _song_of(spans: list[tuple[float, float]], t: float) -> int | None:
    """The song (1-based) whose span holds ``t``; ``None`` before the first or from the last
    song's end on (silence before the content start or after its end)."""
    return next((n for n, (a, b) in enumerate(spans, 1) if a <= t < b), None)


def _song(
    n: int,
    span: tuple[float, float],
    beats: list[float],
    downbeats: list[float],
    timeline: list[tuple[float, float]],
    score: float,
) -> dict[str, object]:
    a, b = span
    own = [t for t in downbeats if a <= t < b]
    first = own[0] if own else next(t for t in beats if a <= t < b)
    return {
        "song": n,
        "start": round(a, 3),
        "end": round(b, 3),
        "bpm": round(bpm_at(timeline, (a + b) / 2), 3),
        "first_downbeat": round(first, 4),
        "first_phrase": round(first, 4),
        "phrase_bars": PHRASE_BARS,
        "grid_score": round(score, 2),
        "beat1_margin": TRUSTED_MARGIN if own else ESTIMATED_MARGIN,
    }


def mix_map(doc: Document, decision: TimingDecision, map_version: int) -> MappedMix | MapRefused:
    """An ``exact`` played mix's map in the library's ``map_version`` shape."""
    if decision.timing_class != "exact":
        return MapRefused("not_exact")
    if decision.reason != "played_recording":
        return MapRefused("not_the_played_clock")
    duration, bpm = fc.read_duration(doc), fc.read_bpm(doc)
    if duration is None:
        return MapRefused("no_duration")
    if bpm is None:
        return MapRefused("bpm_untrusted")
    grid = ft.obj(doc, "grid") or {}
    beats = [t for t in _trusted_times(grid, "beats") if 0 <= t <= duration]
    if not beats:
        return MapRefused("no_beats")
    start, end = _content(doc, duration)
    spans = _spans(_cuts(doc, start, end), beats)
    if not spans:
        return MapRefused("no_beats")
    if len(spans) > MAX_SONGS:
        return MapRefused("too_many_features")
    beats_feature = ft.obj(grid, "beats") or {}
    score = ft.confidence(beats_feature) or 0.0
    timeline, downbeats = _timeline(doc, bpm), _trusted_times(grid, "downbeats")
    songs = [_song(n, span, beats, downbeats, timeline, score) for n, span in enumerate(spans, 1)]
    events: list[dict[str, object]] = []
    for e in fc.trusted_events(doc):
        song = _song_of(spans, e.t)
        if e.kind in MIX_EVENT_KINDS and song is not None:  # outside every song: left out
            t = round(e.t, 3)
            events.append(
                {"t": t, "precise": t, "kind": e.kind, "strength": e.strength, "song": song}
            )
    if len(events) > MAX_EVENTS:
        return MapRefused("too_many_features")
    mapped: dict[str, object] = {
        "version": map_version,
        "duration": round(duration, 3),
        "songs": songs,
        "events": events,
    }
    return MappedMix(mapped)

"""The music timeline of a studio draft (ADR 0036 sections 4.3 and 5, SR C6a). Pure: no I/O.

``timeline`` derives everything from **one** call of ``mowl.compose.compose_map`` over the stored
mix spec and the stored analyses (in the mix's order), the function the design fitted the workout
to and the render's ``mix_offsets`` share, so the view cannot drift from the mix:

- per track, in the order it plays: the SwedeBeat title, artist and BPM of the approved list
  (``SongFacts``: never a catalogue reference, never a Spotify id), the cut points in the
  track's own time (``start_sec``, ``end_sec``), the played window (``played_sec``), where it
  starts and ends in the mix (the composed map's song), its incoming and outgoing crossfade and
  its **effective** length (until the next track starts: the window less the outgoing crossfade);
- per piece, its marks (ADR 0036 amendment A8): ``repeat_of``, the position of the first piece of
  the same song when this piece is a repeat (else ``None``), and ``loop`` (the same song as the
  piece before it, a loop join); ``has_repeats`` for the whole mix (the class is mix-only);
- ``hints``: the soft checks of ``mix_hint_policy`` and, when the caller gives the tracks' stored
  facts, the transitions' ``tempo_jump`` and ``downbeats_not_locked`` (``mix_transition_policy``,
  the warnings the model got): code and piece positions, never a reference;
- per change of track, the crossfade with where it lies in the mix;
- the workout's intervals with their start and end in the mix and the track under their start.

**One duration rule**: ``duration_sec`` is the composed map's, the windows less the crossfades,
and the effective lengths add up to it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mowl import compose

from classbeat.studio import mix_hint_policy, mix_repeat_calc, mix_transition_policy
from classbeat.studio.mix_facts_model import TrackFacts

INTERVAL_FIELDS = ("zone", "rpm", "standing", "game", "ftp_test")


@dataclass(frozen=True)
class SongFacts:
    """The approved list's SwedeBeat facts of one track (the match's, never a model's)."""

    title: str
    artist: str
    bpm: float


def _track_at(starts: Sequence[float], at: float) -> int:
    """The last track (mix position) that has started by ``at``."""
    found = 0
    for n, start in enumerate(starts):
        if start <= at + 1e-9:
            found = n
    return found


def _intervals(content: Mapping[str, Any], starts: Sequence[float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    at = 0
    for s, segment in enumerate(content.get("segments") or []):
        for i, interval in enumerate(segment.get("intervals") or []):
            sec = int(interval["sec"])
            item: dict[str, Any] = {
                "segment": s,
                "segment_name": segment.get("name"),
                "segment_kind": segment.get("kind"),
                "interval": i,
                "start_sec": at,
                "end_sec": at + sec,
                "sec": sec,
                "track": _track_at(starts, at),
            }
            item.update({k: interval.get(k) for k in INTERVAL_FIELDS})
            out.append(item)
            at += sec
    return out


def _transition_hints(
    spec: Mapping[str, Any], track_facts: Mapping[str, TrackFacts] | None
) -> list[mix_transition_policy.TransitionHint]:
    return [] if track_facts is None else mix_transition_policy.hints(spec, track_facts)


def timeline(
    spec: Mapping[str, Any],
    track_maps: Sequence[Mapping[str, Any]],
    facts: Sequence[SongFacts],
    content: Mapping[str, Any],
    track_facts: Mapping[str, TrackFacts] | None = None,
) -> dict[str, Any]:
    """The timeline (see the module docstring); ``ValueError`` when the maps do not fit the spec
    (``compose_map``'s own refusal) or a track is not one song. ``track_facts`` by reference."""
    if len(facts) != len(spec["tracks"]):
        raise ValueError("one set of facts per track")
    composed = compose.compose_map([dict(m) for m in track_maps], dict(spec))
    songs = composed["songs"]
    if [s.get("track") for s in songs] != list(range(1, len(spec["tracks"]) + 1)):
        raise ValueError("a studio track is one song")
    crossfades = [int(c) for c in spec["crossfades"]]
    references = [str(t["reference"]) for t in spec["tracks"]]
    repeat_of = mix_repeat_calc.repeat_of(references)
    joins = set(mix_repeat_calc.loop_joins(references))
    lengths = {r: float(m["duration"]) for r, m in zip(references, track_maps, strict=True)}
    tracks: list[dict[str, Any]] = []
    for n, (cut, song, fact) in enumerate(zip(spec["tracks"], songs, facts, strict=True)):
        played = round(float(cut["end_sec"]) - float(cut["start_sec"]), 2)
        fade_in = crossfades[n - 1] if n else 0
        fade_out = crossfades[n] if n < len(crossfades) else 0
        tracks.append(
            {
                "position": n,
                "title": fact.title,
                "artist": fact.artist,
                "bpm": fact.bpm,
                "start_sec": float(cut["start_sec"]),
                "end_sec": float(cut["end_sec"]),
                "played_sec": played,
                "effective_sec": round(played - fade_out, 2),
                "mix_start_sec": float(song["start"]),
                "mix_end_sec": float(song["end"]),
                "first_downbeat_sec": float(song["first_downbeat"]),
                "crossfade_in_sec": fade_in,
                "crossfade_out_sec": fade_out,
                "repeat_of": repeat_of[n],
                "loop": n - 1 in joins,
            }
        )
    fades = [
        {
            "after": n,
            "sec": c,
            "mix_from_sec": tracks[n + 1]["mix_start_sec"],
            "mix_to_sec": tracks[n]["mix_end_sec"],
        }
        for n, c in enumerate(crossfades)
    ]
    starts = [t["mix_start_sec"] for t in tracks]
    intervals = _intervals(content, starts)
    return {
        "duration_sec": float(composed["duration"]),
        "workout_sec": intervals[-1]["end_sec"] if intervals else 0,
        "tracks": tracks,
        "has_repeats": mix_repeat_calc.has_repeats(references),
        "hints": [
            *(h.as_json() for h in mix_hint_policy.hints(spec, lengths)),
            *(h.as_json() for h in _transition_hints(spec, track_facts)),
        ],
        "crossfades": fades,
        "intervals": intervals,
    }

"""Invented studio track maps for the SR C5 tests: what ``mowl.track.analyze_track`` returns for one
song (one tempo, a beat grid, drops every 16 bars, two sections with an energy). TEST ONLY."""

from typing import Any

from mowl import track

from classbeat.agent.studio_mix import StudioTrack
from classbeat.spotdown.timing_inputs_model import TimingClass


def track_map(
    duration: float = 300.0, bpm: float = 128.0, first_downbeat: float = 0.4
) -> dict[str, Any]:
    phrase = 16 * 240.0 / bpm
    drops = [
        {"t": round(first_downbeat + phrase * k, 3), "kind": "drop", "song": 0}
        for k in range(1, int(duration / phrase))
    ]
    return {
        "duration": duration,
        "songs": [
            {"start": 0, "end": duration, "bpm": bpm, "first_downbeat": first_downbeat,
             "first_phrase": first_downbeat, "phrase_bars": 8, "grid_score": 0.9,
             "beat1_margin": 0.9}
        ],
        "events": drops,
        "sections": [
            {"start": 0.0, "end": duration / 2, "energy": 0.0},
            {"start": duration / 2, "end": duration, "energy": 1.0},
        ],
        "track_version": track.TRACK_VERSION,
    }  # fmt: skip


def tracks(
    count: int = 8, duration: float = 300.0, timing: TimingClass = "exact"
) -> tuple[StudioTrack, ...]:
    """``count`` tracks with the stored timing class ``timing`` (every analysis has one, #1299)."""
    return tuple(
        StudioTrack(f"Invented Song {n}", "Made-up Band", track_map(duration + 10 * n), timing)
        for n in range(count)
    )


def whole_mix(count: int = 8, order: list[int] | None = None, fade: int = 8) -> dict[str, Any]:
    """Every track whole (0 to its length), in ``order``."""
    picked = order if order is not None else list(range(count))
    return {
        "tracks": [{"track": n, "start_sec": 0, "end_sec": 300.0 + 10 * n} for n in picked],
        "crossfades": [fade] * (len(picked) - 1),
    }

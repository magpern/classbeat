"""What the design stage sees of a Swedebeat mix (ADR 0016 section 3), beside ``model_input``.

A mix's map is the library's audio analysis: ``songs`` and ``events``, no ``tracks`` and no
``crossfade`` (the crossfades are in the audio, so ``crossfade`` is 0). The model sees per song
only its index, BPM, start and length, and the drops and breakdowns: **no mix name, id, title or
artist** (the map has none, and the name is never put in). A map without ``tracks`` is read by
song number in ``track_count``, ``track_detail``, ``track_at`` and ``timeline_for_model``; its
warnings follow the library's Swedebeat ones (``service._swedebeat_warnings``: songs whose beat 1
is an estimate), never the Spotify timing warning.

Every function here takes a Spotify playlist's map or data too and hands it to ``model_input``
unchanged, so the design stage calls these and a Spotify design sees exactly what it did before.
``model_input`` itself stays as it is: it is one of the eval suite's rule sources.
"""

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mowl import service
from mowl.workout import mmss

from classbeat.agent import model_input
from classbeat.agent.model_input import EVENT_KINDS, LONGER_THAN_MUSIC, MusicData

BEAT_ONE_ESTIMATE: Final = (
    "Beat 1 is an estimate in the songs with index {songs}; a change there may land a beat early "
    "or late."
)
LOW_MARGIN: Final = service.LOW_MARGIN  # the library's beat-1 margin below which it warns


@dataclass(frozen=True)
class MixData(MusicData):
    """The user's analysis of a Swedebeat mix: ``analysis`` holds only ``map`` and ``duration``."""


def is_mix(m: Mapping[str, Any]) -> bool:
    """Whether ``m`` is a Swedebeat mix's map (songs found in the audio, no ``tracks``)."""
    return "tracks" not in m


def music_map(data: MusicData) -> dict[str, Any]:
    """The map to fit and summarise: ``model_input.music_map``, or for a mix a copy with
    crossfade 0."""
    if not isinstance(data, MixData):
        return model_input.music_map(data.analysis["map"])
    m = copy.deepcopy(dict(data.analysis["map"]))
    if not is_mix(m):
        raise ValueError("a Swedebeat map has no tracks")
    m["crossfade"] = 0.0
    return m


def music_summary(data: MusicData, m: Mapping[str, Any]) -> dict[str, Any]:
    """``model_input.music_summary``, or ``mix_summary``; ``m`` is ``music_map(data)``."""
    return mix_summary(m) if isinstance(data, MixData) else model_input.music_summary(data, m)


def _bpm(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return round(float(value), 1)


def _events(m: Mapping[str, Any], songs: set[int] | None = None) -> list[dict[str, Any]]:
    return [
        {"at": mmss(e["t"]), "t": round(float(e["t"]), 1), "kind": str(e["kind"])}
        for e in m["events"]
        if e.get("kind") in EVENT_KINDS and (songs is None or e.get("song") in songs)
    ]


def mix_summary(m: Mapping[str, Any]) -> dict[str, Any]:
    """A Swedebeat mix for the model: numbers only. Per song its index, BPM, start and length
    (as ``tracks``, the name the prompts use), and the drops and breakdowns. No name, id, title
    or artist exists in the map, and none is added."""
    tracks = [
        {
            "index": int(s["song"]) - 1,
            "bpm": _bpm(s.get("bpm")),
            "from": mmss(s["start"]),
            "sec": round(float(s["end"]) - float(s["start"]), 1),
        }
        for s in m["songs"]
    ]
    summary: dict[str, Any] = {
        "duration": mmss(m["duration"]),
        "duration_sec": round(float(m["duration"]), 1),
        "crossfade_sec": 0.0,
        "tracks": tracks,
        "drops_and_breakdowns": _events(m),
    }
    return model_input._truncated(summary)  # pyright: ignore[reportPrivateUsage]


def track_detail(m: Mapping[str, Any], index: int) -> dict[str, Any]:
    """``model_input.track_detail``; of a mix, the song itself. ``index`` already checked."""
    if not is_mix(m):
        return model_input.track_detail(m, index)
    number = index + 1
    song = next(s for s in m["songs"] if s["song"] == number)
    return {
        "index": index,
        "sections": [
            {"from": mmss(song["start"]), "to": mmss(song["end"]), "bpm": _bpm(song["bpm"])}
        ],
        "drops_and_breakdowns": _events(m, {number}),
    }


def track_count(m: Mapping[str, Any]) -> int:
    return len(m["songs"]) if is_mix(m) else model_input.track_count(m)


def _by_song(m: Mapping[str, Any]) -> dict[str, Any]:
    """A lookup view of a mix's map in which each song is its own track."""
    return {**m, "songs": [{**s, "track": s["song"]} for s in m["songs"]]}


def track_at(m: Mapping[str, Any], t: float) -> int:
    """``model_input.track_at``; of a mix, the index of the song playing at ``t``."""
    return model_input.track_at(_by_song(m) if is_mix(m) else m, t)


def timeline_for_model(
    fitted: Mapping[str, Any], report: Sequence[Mapping[str, Any]], m: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """``model_input.timeline_for_model``; of a mix, with the song under each interval."""
    return model_input.timeline_for_model(fitted, report, _by_song(m) if is_mix(m) else m)


def music_warnings(m: Mapping[str, Any], workout_sec: int) -> list[str]:
    """``model_input.music_warnings``; of a mix, no timing warning (it plays as one file), the
    songs whose beat 1 is an estimate instead."""
    if not is_mix(m):
        return model_input.music_warnings(m, workout_sec)
    out = []
    if workout_sec > m["duration"] + 1:
        out.append(LONGER_THAN_MUSIC.format(workout=mmss(workout_sec), music=mmss(m["duration"])))
    unsure = unsure_beat_one(m)
    if unsure:
        out.append(BEAT_ONE_ESTIMATE.format(songs=", ".join(str(n) for n in unsure)))
    return out


def unsure_beat_one(m: Mapping[str, Any]) -> list[int]:
    """The songs of a Swedebeat mix (0-based) whose beat 1 is an estimate, as the library warns:
    a beat-1 margin below ``LOW_MARGIN`` and no correction."""
    return [
        int(s["song"]) - 1
        for s in m["songs"]
        if float(s.get("beat1_margin", 0.0)) < LOW_MARGIN and not s.get("beat1_shift")
    ]

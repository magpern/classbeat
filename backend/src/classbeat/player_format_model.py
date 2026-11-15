"""A class revision as a player class document, format 1.1 (ADR 0034 section 4).

Pure: plain values in, a plain dict out; no database, network, filesystem or UI. The shape is the
player's ``class.schema.json`` (strict) at the pinned commit in ``tests/player_format/README.md``.
``to_player_document`` only maps; it assumes the input passed ``player_format_policy.refusal``
(the policy is what refuses a revision the format cannot express, with a reason).

Inputs:
- ``draft``: a stored ``class_revisions.content`` as a dict (``WorkoutDraft`` or ``RunningDraft``
  fields: ``name``, ``description``, ``segments`` of ``intervals`` with ``sec``, ``zone``,
  ``rpm`` or ``gradient``, ``standing``, ``game``, ``ftp_test``, ``cue``).
- the song windows of the class, as heard: ``PlayerSong``, in play order.

Mapping choices (the reference converter's, which the tests compare with):
- a game is dropped (the format has none); an FTP test stays as a labelled interval;
- the class title is cut to 80 characters (classbeat allows 100);
- ``bpm`` outside 30 to 300 is left out (unknown);
- ``zone_scale`` is the default table of the format's reference converter, for the zones used;
- no ``beat_anchors``, ``moments`` or ``interval.pattern``: classbeat's model has none yet, so
  the document is written as format 1.1 without a pattern. The Spotify playlist id of format 1.2
  is a later pin update.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

FORMAT_VERSION: Final = "1.1"
MAX_TITLE: Final = 80

Sport = Literal["cycling", "running"]
MusicSource = Literal["spotify", "swedebeat", "none"]

# The zone scale travels with the class; classbeat has zones and no table of names or ranges, so
# the format's reference converter's default is written, only for the zones the class uses.
ZONE_SCALE: Final[Mapping[int, Mapping[str, Any]]] = {
    1: {"name": "Recovery", "ftp_pct": [0, 55], "rpe": [1, 2]},
    2: {"name": "Endurance", "ftp_pct": [56, 75], "rpe": [3, 4]},
    3: {"name": "Tempo", "ftp_pct": [76, 90], "rpe": [5, 6]},
    4: {"name": "Threshold", "ftp_pct": [91, 105], "rpe": [7, 8]},
    5: {"name": "VO2 max", "ftp_pct": [106, 120], "rpe": [9, 9]},
    6: {"name": "Anaerobic", "ftp_pct": [121, 150], "rpe": [10, 10]},
    7: {"name": "All out", "rpe": [10, 10]},
}


@dataclass(frozen=True, slots=True)
class PlayerSong:
    """One song window of the class timeline, whole milliseconds. ``spotify_track`` is the track
    id (22 letters and digits) and ``swedebeat_mix`` the mix id, when known."""

    title: str
    artist: str
    length_ms: int
    start_ms: int
    end_ms: int
    bpm: float | None = None
    spotify_track: str | None = None
    swedebeat_mix: str | None = None


@dataclass(frozen=True, slots=True)
class PlayerClass:
    """A class revision ready to convert. ``crossfade_ms`` is the design crossfade; windows come
    from lengths and the crossfade, never from audio, so the timing is ``estimated``."""

    draft: Mapping[str, Any]
    sport: Sport
    music_source: MusicSource
    crossfade_ms: int
    songs: Sequence[PlayerSong]


def clean(value: str) -> str:
    """Whitespace collapsed to single spaces and trimmed."""
    return " ".join(value.split())


def class_length_ms(draft: Mapping[str, Any]) -> int:
    """The sum of all interval lengths, in whole milliseconds."""
    return sum(iv["sec"] * 1000 for seg in draft["segments"] for iv in seg["intervals"])


def _interval(src: Mapping[str, Any], start_ms: int, running: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "start_ms": start_ms,
        "length_ms": src["sec"] * 1000,
        "zone": src["zone"],
    }
    if running:
        out["incline"] = "raised" if src.get("gradient", 0) == 1 else "flat"
    else:
        rpm = src["rpm"]
        out["cadence"] = "beat" if rpm == "beat" else {"from": rpm[0], "to": rpm[1]}
        out["position"] = "standing" if src.get("standing") else "seated"
    out["cue"] = clean(src.get("cue") or "")
    if not running and src.get("ftp_test") is not None:
        out["test"] = {"kind": "ftp", "minutes": src["ftp_test"]}
    return out


def _song(song: PlayerSong) -> dict[str, Any]:
    out: dict[str, Any] = {
        "title": clean(song.title),
        "artist": clean(song.artist),
        "length_ms": song.length_ms,
        "start_ms": song.start_ms,
        "end_ms": song.end_ms,
    }
    if song.bpm is not None and 30 <= song.bpm <= 300:
        out["bpm"] = song.bpm
    ids: dict[str, str] = {}
    if song.spotify_track is not None:
        ids["spotify_track"] = song.spotify_track
    if song.swedebeat_mix is not None:
        ids["swedebeat_mix"] = song.swedebeat_mix
    if ids:
        out["ids"] = ids
    return out


def to_player_document(cls: PlayerClass) -> dict[str, Any]:
    """The class document (format 1.1) of an expressible revision."""
    draft = cls.draft
    running = cls.sport == "running"
    intervals: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    time_in_zone: dict[int, int] = {}
    at = 0
    for seg in draft["segments"]:
        seg_start = at
        for src in seg["intervals"]:
            iv = _interval(src, at, running)
            intervals.append(iv)
            time_in_zone[iv["zone"]] = time_in_zone.get(iv["zone"], 0) + iv["length_ms"]
            at += iv["length_ms"]
        segments.append(
            {"name": clean(seg["name"]), "kind": seg["kind"], "start_ms": seg_start, "end_ms": at}
        )

    soundless = cls.music_source == "none"
    klass: dict[str, Any] = {"title": clean(draft["name"])[:MAX_TITLE].strip()}
    if draft.get("description"):
        klass["description"] = clean(draft["description"])
    klass.update({"sport": cls.sport, "length_ms": at})
    return {
        "format_version": FORMAT_VERSION,
        "class": klass,
        "music": {
            "source": cls.music_source,
            "design_crossfade_ms": 0 if soundless else cls.crossfade_ms,
            "song_timing": "missing" if soundless else "estimated",
        },
        "zone_scale": [{"zone": z, **ZONE_SCALE[z]} for z in sorted(time_in_zone)],
        "totals": {
            "length_ms": at,
            "time_in_zone_ms": {str(z): time_in_zone[z] for z in sorted(time_in_zone)},
        },
        "segments": segments,
        "intervals": intervals,
        "songs": [] if soundless else [_song(s) for s in cls.songs],
    }

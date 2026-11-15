"""Reference converter (ADR 0003 section 8): classbeat's data shape to a class document, v1.

Pure: plain dicts in, a plain dict out, no I/O. It is what classbeat ports in PL3, not a
dependency of anything. The input shape (a "fixture") is written from the public field names of
classbeat's own class data only; README.md has the field table and the ADR rule of each choice.

Input:
    {"name", "description"?, "sport"?: "cycling"|"running",
     "segments": [{"name", "kind", "intervals": [{"sec", "zone", "rpm" | "gradient",
                                                   "standing"?, "game"?, "ftp_test"?, "cue"?}]}],
     "music"?: {"crossfade_sec": number,
                "songs": [{"spotify_id", "title", "artist", "bpm"?, "length_sec"}]}}
A class without "music" (or with no songs) is a soundless class.
"""

from __future__ import annotations

from typing import Any

FORMAT_VERSION = "1.0"
MAX_TITLE = 80  # the format's name limit (classbeat's class names go to 100)

# The zone scale travels with the class (ADR 0003 section 3). classbeat has zones 1 to 7 and no
# table of names or ranges in its data, so the converter writes this default and includes only the
# zones the class uses. Names and ranges are the converter's choice, not classbeat's data.
ZONE_SCALE: dict[int, dict[str, Any]] = {
    1: {"name": "Recovery", "ftp_pct": [0, 55], "rpe": [1, 2]},
    2: {"name": "Endurance", "ftp_pct": [56, 75], "rpe": [3, 4]},
    3: {"name": "Tempo", "ftp_pct": [76, 90], "rpe": [5, 6]},
    4: {"name": "Threshold", "ftp_pct": [91, 105], "rpe": [7, 8]},
    5: {"name": "VO2 max", "ftp_pct": [106, 120], "rpe": [9, 9]},
    6: {"name": "Anaerobic", "ftp_pct": [121, 150], "rpe": [10, 10]},
    7: {"name": "All out", "rpe": [10, 10]},
}


class ConversionError(ValueError):
    """The fixture cannot be expressed as a v1 class (the message says why)."""


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def _text(value: str) -> str:
    return " ".join(value.split())


def _interval(src: dict[str, Any], start_ms: int, running: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "start_ms": start_ms,
        "length_ms": _ms(src["sec"]),
        "zone": src["zone"],
    }
    if running:
        out["incline"] = "raised" if src.get("gradient", 0) == 1 else "flat"
    else:
        rpm = src["rpm"]
        out["cadence"] = "beat" if rpm == "beat" else {"from": rpm[0], "to": rpm[1]}
        out["position"] = "standing" if src["standing"] else "seated"
    out["cue"] = _text(src.get("cue", ""))
    # "game" is dropped (ADR 0003 section 3). An FTP test is kept as a labelled interval.
    if not running and src.get("ftp_test") is not None:
        out["test"] = {"kind": "ftp", "minutes": src["ftp_test"]}
    return out


def _songs(songs: list[dict[str, Any]], crossfade_ms: int, class_ms: int) -> list[dict[str, Any]]:
    """Windows from the lengths and the crossfade: each song starts `crossfade` before the
    previous one ends; the last window ends at the class end."""
    out: list[dict[str, Any]] = []
    start = 0
    for i, s in enumerate(songs):
        length = _ms(s["length_sec"])
        last = i == len(songs) - 1
        end = class_ms if last else start + length - crossfade_ms
        if end <= start or end - start > length:
            raise ConversionError(f"song {i + 1}: its window does not fit its length")
        song: dict[str, Any] = {
            "title": _text(s["title"]),
            "artist": _text(s["artist"]),
            "length_ms": length,
            "start_ms": start,
            "end_ms": end,
        }
        bpm = s.get("bpm")
        if bpm is not None and 30 <= bpm <= 300:  # outside the format's range: unknown
            song["bpm"] = bpm
        song["ids"] = {"spotify_track": s["spotify_id"]}
        out.append(song)
        start = end
    return out


def convert(fixture: dict[str, Any]) -> dict[str, Any]:
    """Convert one fixture to a class document. The caller runs the validator on the result."""
    sport = fixture.get("sport", "cycling")
    running = sport == "running"
    intervals: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    time_in_zone: dict[int, int] = {}
    at = 0
    for seg in fixture["segments"]:
        seg_start = at
        for src in seg["intervals"]:
            iv = _interval(src, at, running)
            intervals.append(iv)
            time_in_zone[iv["zone"]] = time_in_zone.get(iv["zone"], 0) + iv["length_ms"]
            at += iv["length_ms"]
        segments.append(
            {
                "name": _text(seg["name"]),
                "kind": seg["kind"],
                "start_ms": seg_start,
                "end_ms": at,
            }
        )
    if at == 0:
        raise ConversionError("the class has no intervals")

    music_in = fixture.get("music") or {}
    songs_in = music_in.get("songs") or []
    crossfade_ms = _ms(music_in.get("crossfade_sec", 0))
    soundless = not songs_in
    music = {
        "source": "none" if soundless else "spotify",
        "design_crossfade_ms": 0 if soundless else crossfade_ms,
        # Windows are computed from lengths, never measured against audio.
        "song_timing": "missing" if soundless else "estimated",
    }

    klass: dict[str, Any] = {"title": _text(fixture["name"])[:MAX_TITLE].strip()}
    if fixture.get("description"):
        klass["description"] = _text(fixture["description"])
    klass.update({"sport": sport, "length_ms": at})

    return {
        "format_version": FORMAT_VERSION,
        "class": klass,
        "music": music,
        "zone_scale": [{"zone": z, **ZONE_SCALE[z]} for z in sorted(time_in_zone)],
        "totals": {
            "length_ms": at,
            "time_in_zone_ms": {str(z): time_in_zone[z] for z in sorted(time_in_zone)},
        },
        "segments": segments,
        "intervals": intervals,
        "songs": [] if soundless else _songs(songs_in, crossfade_ms, at),
    }

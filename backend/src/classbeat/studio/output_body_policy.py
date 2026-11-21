"""What a studio output writes, built from the approved draft's spec and the list's SwedeBeat facts,
never from model text (ADR 0036 section 6, SR C7). Pure: no I/O.

- **Names.** ``class_name``: the class title, printable, cut to the limit, otherwise ``classbeat
  class``. The SwedeBeat playlist is named by it (1 to 120 characters). The mix is ``mix_name``:
  ``<title, at most 100 characters> · cb-<6 random [a-z0-9]>`` (``new_suffix``), unique, so the
  MOWL listing can show it by name. A name is made once, when the attempt is opened, and stored.
- **The playlist body** (``playlist_body``): ``category_id`` 27, ``crossfade_sec`` 0 at the
  playlist level, and per track in mix order the ``reference``, the list's title, artist and BPM,
  ``start_sec`` and ``end_sec`` rounded to 0.01 s (the spec stores them so), an **explicit**
  ``crossfade_sec`` (the track's outgoing crossfade; the last track 0, or the gateway would use
  5) and ``trim`` true whenever there is a cut (a start after 0 or an end before the analysed
  length), otherwise left to the gateway (``null``: true when it finds a cut itself).
- **The render plan** (``render_plan``): ``mowl.compose.mix_offsets`` of the spec at 44100 Hz with
  the analysed lengths, each track named by its reference (the paths are the render's own).
- **X-Tracks** (``x_tracks``): per **loop run** in mix order (ADR 0036 amendment A8: adjacent pieces
  of one song are one entry, one song playing on; a non-adjacent repeat is its own entry) the
  title, artist and BPM and its played length until the next entry starts (its pieces' windows
  less the run's outgoing crossfade, so the lengths add up to the mix), as UTF-8 JSON of at most
  16 KB: titles and artists are cut until it fits.
"""

import json
import secrets
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mowl import compose

from classbeat.studio.mix_repeat_calc import runs

RATE: Final = 44100  # render_policy.RATE
CATEGORY_ID: Final = 27
FALLBACK_NAME: Final = "classbeat class"
PLAYLIST_NAME_MAX: Final = 120
MIX_TITLE_MAX: Final = 100
SUFFIX_ALPHABET: Final = string.ascii_lowercase + string.digits
SUFFIX: Final = "cb-"
X_TRACKS_MAX: Final = 16 * 1024
TEXT_MAX: Final = 300  # the gateway cuts X-Tracks' titles and artists to 300


@dataclass(frozen=True, slots=True)
class Facts:
    """The approved list's SwedeBeat facts of one track (the match's)."""

    title: str
    artist: str
    bpm: float


def class_name(title: str | None, limit: int = PLAYLIST_NAME_MAX) -> str:
    cleaned = "".join(c if c.isprintable() else " " for c in title or "").strip()[:limit].strip()
    return cleaned or FALLBACK_NAME


def new_suffix() -> str:
    return SUFFIX + "".join(secrets.choice(SUFFIX_ALPHABET) for _ in range(6))


def mix_name(title: str | None, suffix: str) -> str:
    if len(suffix) != 9 or not suffix.startswith(SUFFIX):
        raise ValueError("a mix suffix is cb- and 6 characters")
    return f"{class_name(title, MIX_TITLE_MAX)} · {suffix}"


def _tracks(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(t) for t in spec["tracks"]]


def playlist_body(
    name: str,
    spec: Mapping[str, Any],
    facts: Mapping[str, Facts],
    durations: Mapping[str, float],
) -> dict[str, Any]:
    """The ``POST /playlists`` body (see the module docstring)."""
    tracks = _tracks(spec)
    crossfades = [int(c) for c in spec["crossfades"]]
    body: list[dict[str, Any]] = []
    for n, track in enumerate(tracks):
        reference = track["reference"]
        start, end = round(float(track["start_sec"]), 2), round(float(track["end_sec"]), 2)
        known = facts[reference]
        body.append(
            {
                "reference": reference,
                "title": known.title,
                "artist": known.artist,
                "bpm": known.bpm,
                "start_sec": start,
                "end_sec": end,
                # True for a cut; otherwise the gateway's own default (its length may differ
                # from the analysed one by the encoder's padding, and ``false`` with a cut is 422)
                "trim": True if start > 0 or end < round(float(durations[reference]), 2) else None,
                "crossfade_sec": crossfades[n] if n < len(crossfades) else 0,
            }
        )
    return {"name": name, "category_id": CATEGORY_ID, "crossfade_sec": 0, "tracks": body}


def render_plan(spec: Mapping[str, Any], durations: Mapping[str, float]) -> list[dict[str, Any]]:
    """The spec's sample positions per track, named by reference (see the module docstring)."""
    tracks = _tracks(spec)
    lengths = [float(durations[t["reference"]]) for t in tracks]
    offsets = compose.mix_offsets(dict(spec), RATE, lengths)
    return [
        {
            "reference": tracks[o["index"]]["reference"],
            **{k: v for k, v in o.items() if k != "index"},
        }
        for o in offsets
    ]


def played_seconds(plan: Sequence[Mapping[str, Any]]) -> list[float]:
    """Each track's length until the next one starts, in seconds (they add up to the mix)."""
    return [round((p["src_end"] - p["src_start"] - p["fade_out"]) / RATE, 3) for p in plan]


def _run_seconds(plan: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """Per loop run, its reference and its length until the next run starts, in seconds."""
    references = [str(p["reference"]) for p in plan]
    frames = [p["src_end"] - p["src_start"] - p["fade_out"] for p in plan]
    return [(references[a], round(sum(frames[a : b + 1]) / RATE, 3)) for a, b in runs(references)]


def x_tracks(plan: Sequence[Mapping[str, Any]], facts: Mapping[str, Facts]) -> list[dict[str, Any]]:
    """The merge's track list, cut until its JSON fits 16 KB (see the module docstring)."""
    played = _run_seconds(plan)
    cut = TEXT_MAX
    while True:
        items = [
            {
                "title": facts[reference].title[:cut],
                "artist": facts[reference].artist[:cut],
                "bpm": facts[reference].bpm,
                "duration": seconds,
            }
            for reference, seconds in played
        ]
        raw = json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) <= X_TRACKS_MAX or cut <= 1:
            return items
        cut = max(1, cut // 2)

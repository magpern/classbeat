"""Beat-one corrections (ADR 0007 amendment "beat-one corrections", #535): pure functions.

A correction moves one song's beat 1 by whole beats, -3 to 3 and never 0, with the library's
meaning: positive when the changes in that song come early, so beat 1 moves later. classbeat
applies it with ``library.shift_beat1`` to **its own copy** of the map, once per correction with
the stored total. It never calls ``library.correct_beat1``: that makes a MOWL request and stores
the shift in the user's link, which every class of theirs on the music would read. Nothing here
writes an analysis, a row or a file.

Which song:

- a Swedebeat mix (a map without ``tracks``): the map's 1-based ``song``;
- a Spotify playlist: the 1-based track in play order. The library's Spotify map has one entry per
  section, so a correction of track ``n`` shifts every entry with ``track == n``.

A correction for a song the map lacks is skipped: the rows are keyed by the music's fingerprint,
so this happens only for a map that is not the one the correction was set on.
"""

import copy
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from mowl import library, service

from classbeat.agent.schemas import clean_text

MIN_BEATS: Final = -3
MAX_BEATS: Final = 3
MAX_SONG: Final = 500  # also the ``song`` check of migration 0029
MAX_PER_CLASS: Final = 200  # rows for the class's current fingerprint (409 ``correction_limit``)
MAX_SONGS: Final = 200  # entries of a revision's ``music.songs`` (a class is at most 180 minutes)
MAX_TITLE: Final = 100  # characters of a song title in ``music.songs``
LOW_MARGIN: Final = float(service.LOW_MARGIN)  # below it the library calls beat 1 an estimate

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, order=True)
class Correction:
    song: int
    beats: int

    def __post_init__(self) -> None:
        if isinstance(self.song, bool) or not isinstance(self.song, int):
            raise ValueError("song must be an integer")
        if isinstance(self.beats, bool) or not isinstance(self.beats, int):
            raise ValueError("beats must be an integer")
        if not 1 <= self.song <= MAX_SONG:
            raise ValueError("song must be 1 to 500")
        if not MIN_BEATS <= self.beats <= MAX_BEATS or self.beats == 0:
            raise ValueError("beats must be -3 to 3 and not 0")


def is_mix(m: Mapping[str, Any]) -> bool:
    """A Swedebeat mix's map has songs found in the audio and no ``tracks``."""
    return "tracks" not in m


def _entries(m: Mapping[str, Any], song: int) -> list[int]:
    """The map's ``song`` numbers a correction of ``song`` moves."""
    # An entry without ``song`` is skipped, as ``songs`` skips it (the library's maps have it).
    if is_mix(m):
        return [int(s["song"]) for s in m["songs"] if "song" in s and int(s["song"]) == song]
    return [int(s["song"]) for s in m["songs"] if "song" in s and int(s.get("track", 0)) == song]


def apply(m: Mapping[str, Any], corrections: Iterable[Correction]) -> dict[str, Any]:
    """A copy of ``m`` with each correction applied once (``m`` is left as it was)."""
    out = copy.deepcopy(dict(m))
    skipped = 0
    for c in sorted(set(corrections)):
        entries = _entries(out, c.song)
        if not entries:
            skipped += 1
        for entry in entries:
            library.shift_beat1(out, entry, c.beats)
    if skipped:  # not for the fingerprint it was set on; never fails the job (a count, no ids)
        logger.warning("beat-one corrections skipped", extra={"skipped": skipped})
    return out


def as_list(corrections: Iterable[Correction]) -> list[dict[str, int]]:
    """``music.beat_one`` of a revision: ``[{song, beats}]`` sorted by song."""
    return [{"song": c.song, "beats": c.beats} for c in sorted(set(corrections))]


def _num(value: Any) -> float:
    return round(float(value), 1)


def songs(m: Mapping[str, Any], workout_sec: float) -> list[dict[str, Any]]:
    """``music.songs`` of a revision: the songs the class plays (starting before ``workout_sec``),
    in seconds of the map as fitted, each ``{song, start, end, bpm, estimate}`` plus ``title`` for
    a Spotify track. ``estimate``: a mix song whose beat-1 margin is below ``LOW_MARGIN`` and that
    has no correction, or a Spotify track MOWL had no BPM for."""
    out: list[dict[str, Any]] = []
    skipped = 0
    if is_mix(m):
        for s in m["songs"]:
            if not all(k in s for k in ("song", "start", "end", "bpm")):
                skipped += 1  # the library's maps always have them: skip, never fail the job
                continue
            if float(s["start"]) >= workout_sec:
                continue
            margin = s.get("beat1_margin")
            unsure = isinstance(margin, int | float) and float(margin) < LOW_MARGIN
            out.append(
                {
                    "song": int(s["song"]),
                    "start": _num(s["start"]),
                    "end": _num(s["end"]),
                    "bpm": _num(s["bpm"]),
                    "estimate": bool(unsure and not s.get("beat1_shift")),
                }
            )
        _log_skipped(skipped)
        return out[:MAX_SONGS]
    starts: dict[int, float] = {}
    for s in m["songs"]:
        if "track" not in s or "start" not in s:
            skipped += 1
            continue
        track = int(s["track"])
        starts[track] = min(starts.get(track, float(s["start"])), float(s["start"]))
    order = sorted(starts)
    for n, track in enumerate(order):
        start = starts[track]
        if start >= workout_sec:
            continue
        end = starts[order[n + 1]] if n + 1 < len(order) else float(m["duration"])
        info = next((t for t in m["tracks"] if int(t["track"]) == track), {})
        bpm = info.get("bpm")
        out.append(
            {
                "song": track,
                "start": _num(start),
                "end": _num(end),
                "bpm": _num(bpm) if bpm else 120.0,
                "estimate": not bpm,
                "title": clean_text(str(info.get("title") or ""))[:MAX_TITLE],
            }
        )
    _log_skipped(skipped)
    return out[:MAX_SONGS]


def _log_skipped(skipped: int) -> None:
    if skipped:  # a count only, no ids or titles
        logger.warning("beat-one songs skipped", extra={"skipped": skipped})

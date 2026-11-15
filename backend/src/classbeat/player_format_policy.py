"""What the player class format cannot express, and the one entry point that converts (ADR 0034).

``refusal`` says why a revision cannot become a class document, in a sentence the UI may show
(no ids, no internals). ``convert`` is the composition the rest of classbeat calls: refuse, or the
format 1.1 document and its hash. Pure: no I/O. Limits are those of the player's strict schema
and semantic rules at the pinned commit (``tests/player_format/README.md``).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from classbeat.player_format_model import (
    MAX_TITLE,
    ZONE_SCALE,
    PlayerClass,
    PlayerSong,
    class_length_ms,
    clean,
    to_player_document,
)
from classbeat.player_hash_calc import content_hash

MAX_CLASS_MS: Final = 86_400_000
MAX_CROSSFADE_MS: Final = 30_000
MAX_CUE: Final = 120
MAX_NAME: Final = 80
MAX_DESCRIPTION: Final = 2000
MAX_SONG_TEXT: Final = 200
RUNNING_MAX_ZONE: Final = 5
_SPOTIFY_TRACK = re.compile(r"^[A-Za-z0-9]{22}$")


@dataclass(frozen=True, slots=True)
class Refusal:
    """``code`` is stable (tests, logs); ``reason`` is the sentence shown to the user."""

    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class Converted:
    document: dict[str, Any]
    content_hash: str


def _interval_refusal(n: int, iv: Mapping[str, Any], cls: PlayerClass) -> Refusal | None:
    running = cls.sport == "running"
    zone = iv.get("zone")
    top = RUNNING_MAX_ZONE if running else max(ZONE_SCALE)
    if not isinstance(zone, int) or isinstance(zone, bool) or not 1 <= zone <= top:
        return Refusal("zone_out_of_range", f"Interval {n} has a zone the player cannot show.")
    sec = iv.get("sec")
    if not isinstance(sec, int) or isinstance(sec, bool) or sec < 1:
        return Refusal("interval_length", f"Interval {n} has no whole-second length.")
    if len(clean(iv.get("cue") or "")) > MAX_CUE:
        return Refusal("cue_too_long", f"The cue of interval {n} is over {MAX_CUE} characters.")
    if running:
        return None
    rpm = iv.get("rpm")
    if isinstance(rpm, (list, tuple)) and not all(1 <= v <= 300 for v in rpm):
        return Refusal("cadence_out_of_range", f"Interval {n} has a cadence outside 1 to 300.")
    return None


def _draft_refusal(cls: PlayerClass) -> Refusal | None:
    draft = cls.draft
    segments = draft.get("segments") or []
    if not segments or any(not seg.get("intervals") for seg in segments):
        return Refusal("no_intervals", "The class has an empty segment or no intervals.")
    if not clean(draft.get("name") or "")[:MAX_TITLE].strip():
        return Refusal("no_title", "The class has no name.")
    if any(
        not clean(seg.get("name") or "") or len(clean(seg["name"])) > MAX_NAME for seg in segments
    ):
        return Refusal("segment_name", f"A segment name is empty or over {MAX_NAME} characters.")
    if len(clean(draft.get("description") or "")) > MAX_DESCRIPTION:
        return Refusal("description_too_long", "The description is too long for the player.")
    n = 0
    for seg in segments:
        for iv in seg["intervals"]:
            n += 1
            if (found := _interval_refusal(n, iv, cls)) is not None:
                return found
    return None


def _song_refusal(n: int, song: PlayerSong, cls: PlayerClass) -> Refusal | None:
    for label, value in (("title", song.title), ("artist", song.artist)):
        if not clean(value) or len(clean(value)) > MAX_SONG_TEXT:
            return Refusal("song_text", f"Song {n} has no {label}, or one that is too long.")
    if (
        song.length_ms < 1
        or song.end_ms <= song.start_ms
        or song.end_ms - song.start_ms > song.length_ms
    ):
        return Refusal("song_window", f"Song {n}: its window does not fit its length.")
    if song.spotify_track is not None and not _SPOTIFY_TRACK.match(song.spotify_track):
        return Refusal("song_id", f"Song {n} has a Spotify id the player cannot use.")
    if song.swedebeat_mix is not None and not 1 <= len(song.swedebeat_mix) <= 100:
        return Refusal("song_id", f"Song {n} has a Swedebeat mix id the player cannot use.")
    return None


def _music_refusal(cls: PlayerClass, length_ms: int) -> Refusal | None:
    if cls.music_source == "none":
        if cls.songs:
            return Refusal("songs_on_soundless", "A class without music cannot list songs.")
        return None
    if not cls.songs:
        return Refusal("no_songs", "The class has music but no songs to play.")
    if not 0 <= cls.crossfade_ms <= MAX_CROSSFADE_MS:
        return Refusal("crossfade", "The crossfade is outside 0 to 30 seconds.")
    expected = 0
    for n, song in enumerate(cls.songs, start=1):
        if (found := _song_refusal(n, song, cls)) is not None:
            return found
        if song.start_ms != expected:
            return Refusal("song_gap", f"Song {n} does not start where the one before it ends.")
        expected = song.end_ms
    if expected != length_ms:
        return Refusal("songs_length", "The songs do not fill the class from start to end.")
    return None


def refusal(cls: PlayerClass) -> Refusal | None:
    """Why ``cls`` cannot be a class document of the player format, or ``None`` when it can."""
    if (found := _draft_refusal(cls)) is not None:
        return found
    length_ms = class_length_ms(cls.draft)
    if length_ms > MAX_CLASS_MS:
        return Refusal("class_too_long", "The class is longer than 24 hours.")
    return _music_refusal(cls, length_ms)


def convert(cls: PlayerClass) -> Converted | Refusal:
    """The format 1.1 document of ``cls`` and its content hash, or the refusal."""
    if (found := refusal(cls)) is not None:
        return found
    document = to_player_document(cls)
    return Converted(document, content_hash(document))

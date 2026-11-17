"""The song list of a class on the user's own Spotify playlist (roadmap Amendment 26, ADR 0008
amendment of 2026-11-16; pure: no database, no network, no model).

``chosen_list`` turns MOWL's analysis of the playlist into the ``ChosenList`` the ``song_list``
revision stores, so the approval and phase 2 are exactly a pick's:

- **The order is the playlist's**: the analysed tracks as MOWL lists them, a track listed twice
  stays twice. Nothing is reordered, added or chosen.
- **Left out and counted** (``omitted_no_bpm``, as for a pool): a track without a Spotify track
  id (a local file, an episode) or without a BPM yet. A list with no usable track, or with more
  than ``MAX_SONGS``, is refused (``None``; the caller ends the job ``invalid_input``).
- **Every id, title, artist, BPM and length is MOWL's analysis** (``analysed_pool``), never a
  model's. There is no cover (MOWL's answer gives none).
- **Roles** are positional, since no model gave any: the first song ``warmup``, the last
  ``cooldown``, the rest ``interval``. ``new`` is False: these are the user's own songs.
"""

import re
from collections.abc import Mapping
from typing import Any, Final

from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.agent.schemas import MAX_SONGS
from classbeat.agent.songs import CROSSFADE_SEC, analysed_pool, total_sec

SPOTIFY_TRACK_ID: Final = re.compile(r"^[0-9A-Za-z]{22}$")
FIRST: Final = "warmup"
LAST: Final = "cooldown"
MIDDLE: Final = "interval"


def role_at(position: int, count: int) -> str:
    """The positional role of song ``position`` (0-based) in a list of ``count`` songs."""
    if position == 0:
        return FIRST
    if position == count - 1:
        return LAST
    return MIDDLE


def chosen_list(analysis_map: Mapping[str, Any], *, target_sec: int) -> ChosenList | None:
    """The playlist's usable tracks in their order as a ``ChosenList`` (module docstring), or
    None when none is usable or there are more than ``MAX_SONGS``."""
    tracks = analysis_map.get("tracks")
    listed = [t for t in tracks if isinstance(t, Mapping)] if isinstance(tracks, list) else []
    ids = [str(t.get("spotify_id")) for t in listed]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    wanted = [(i, MIDDLE) for i in ids if SPOTIFY_TRACK_ID.fullmatch(i)]
    if not wanted:
        return None
    usable, no_bpm = analysed_pool(analysis_map, wanted)
    omitted = no_bpm + (len(listed) - len(wanted))
    if not usable or len(usable) > MAX_SONGS:
        return None
    count = len(usable)
    songs = tuple(
        ChosenSong(
            t.spotify_id,
            t.title,
            t.artist,
            role_at(n, count),
            t.bpm,
            t.length_sec,
            False,
            None,
        )
        for n, t in enumerate(usable)
    )
    total = total_sec([s.length_sec for s in songs])
    return ChosenList(songs, round(total), target_sec, CROSSFADE_SEC, "pick", omitted, "")

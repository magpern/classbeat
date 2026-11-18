"""Favoured tracks over search hits (ADR 0038 section 5, #1188). Pure: no database, no network.

The attached Spotify playlists are favoured, never a limit. After ``choose`` the backend makes
that preference hold whatever the model did: each chosen song that is a search hit (not favoured,
not pinned or kept) is swapped for an unused favoured pool track **where it fits**, that is:

- the favoured track's BPM is within ``BPM_TOLERANCE`` (10 %) of the song it replaces, so the
  class's tempo shape stays as chosen; and
- the list's total (lengths less the crossfade per change of song) stays within 10 % of the target.

Among the fitting tracks the closest BPM wins, then the pool's order. Slots are visited in play
order; a favoured track is used once. Nothing else of the list changes: the slot keeps its role.
"""

from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Final

from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.agent.songs import PoolTrack, total_sec, within_tolerance

BPM_TOLERANCE: Final = 0.10


def prefer_favoured(
    chosen: ChosenList,
    tracks: Sequence[PoolTrack],
    favoured: Collection[str],
    fixed: Collection[int],
) -> ChosenList:
    """``chosen`` with search hits swapped for fitting favoured tracks (module docstring).
    ``favoured``: the Spotify ids of the attached playlists' tracks; ``fixed``: the positions
    that never change (pinned and kept songs)."""
    used = {s.spotify_id for s in chosen.songs}
    spare = [t for t in tracks if t.spotify_id in favoured and t.spotify_id not in used]
    songs = list(chosen.songs)
    for at, song in enumerate(songs):
        if at in fixed or song.spotify_id in favoured or not song.new:
            continue
        best: tuple[float, int, PoolTrack] | None = None
        for order, track in enumerate(spare):
            gap = abs(track.bpm - song.bpm)
            if gap > BPM_TOLERANCE * song.bpm:
                continue
            lengths = [t.length_sec for t in songs]
            lengths[at] = track.length_sec
            if not within_tolerance(total_sec(lengths), chosen.target_sec):
                continue
            if best is None or (gap, order) < best[:2]:
                best = (gap, order, track)
        if best is None:
            continue
        track = best[2]
        spare.remove(track)
        songs[at] = ChosenSong(
            track.spotify_id,
            track.title,
            track.artist,
            song.role,
            track.bpm,
            track.length_sec,
            True,
            track.cover,
        )
    if songs == list(chosen.songs):
        return chosen
    total = round(total_sec([s.length_sec for s in songs]))
    return replace(chosen, songs=tuple(songs), total_sec=total)

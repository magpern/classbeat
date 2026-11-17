"""``classbeat.songs.playlist_list_policy`` (roadmap Amendment 26): the song list of a class on the
user's own Spotify playlist, from MOWL's analysis in the playlist's order. Pure: no database, no
network, no model."""

from collections.abc import Sequence
from typing import Any

from mowl import spotify

from classbeat.agent.schemas import MAX_SONGS
from classbeat.songs.playlist_list_policy import chosen_list, role_at


def track_id(i: int) -> str:
    return f"OwnTrack{i:014d}"  # 22 letters and digits


def analysis(ids: Sequence[str | None], *, no_bpm: frozenset[int] = frozenset()) -> dict[str, Any]:
    """MOWL's analysis (the library's own format) of a playlist of 240 s tracks, in this order."""
    rows: list[dict[str, Any]] = []
    sections: dict[str, list[dict[str, float]]] = {}
    for n, tid in enumerate(ids):
        rows.append(
            {
                "Title": f"Own Song {n}",
                "Artist": f"Own Band {n % 3}",
                "Duration": 240_000,
                "BPM": 0 if n in no_bpm else 100 + n,
                "SpotifyTrackID": tid,
            }
        )
        if tid is not None:
            sections[tid] = [{"start": 0.0, "duration": 240.0, "loudness": -8.0}]
    return spotify.build_map(rows, sections)


def test_the_list_keeps_the_playlists_order_and_mowls_analysis() -> None:
    order = [track_id(i) for i in (5, 3, 11, 0, 7, 2, 9, 14)]
    chosen = chosen_list(analysis(order), target_sec=1800)
    assert chosen is not None
    assert [s.spotify_id for s in chosen.songs] == order
    first = chosen.songs[0]
    assert (first.title, first.artist, first.bpm, first.length_sec) == (
        "Own Song 0",
        "Own Band 0",
        100.0,
        240,
    )
    assert (chosen.mode, chosen.target_sec, chosen.omitted_no_bpm, chosen.note) == (
        "pick",
        1800,
        0,
        "",
    )
    assert chosen.total_sec == 8 * 240 - 7 * 9  # lengths less 9 s per change of song
    assert all(not s.new and s.cover is None for s in chosen.songs)  # the user's own songs


def test_roles_are_positional() -> None:
    chosen = chosen_list(analysis([track_id(i) for i in range(4)]), target_sec=900)
    assert chosen is not None
    assert [s.role for s in chosen.songs] == ["warmup", "interval", "interval", "cooldown"]
    assert [role_at(0, 1), role_at(0, 2), role_at(1, 2)] == ["warmup", "warmup", "cooldown"]


def test_a_track_listed_twice_stays_twice() -> None:
    twice = [track_id(1), track_id(2), track_id(1)]
    chosen = chosen_list(analysis(twice), target_sec=600)
    assert chosen is not None
    assert [s.spotify_id for s in chosen.songs] == twice


def test_a_track_without_a_bpm_or_a_track_id_is_left_out_and_counted() -> None:
    ids: list[str | None] = [track_id(0), None, track_id(2), "not-a-track", track_id(4)]
    chosen = chosen_list(analysis(ids, no_bpm=frozenset({2})), target_sec=600)
    assert chosen is not None
    assert [s.spotify_id for s in chosen.songs] == [track_id(0), track_id(4)]
    assert chosen.omitted_no_bpm == 3


def test_no_usable_track_or_more_than_max_songs_is_refused() -> None:
    assert chosen_list(analysis([track_id(0)], no_bpm=frozenset({0})), target_sec=600) is None
    assert chosen_list({"tracks": "nonsense"}, target_sec=600) is None
    too_many = [track_id(i) for i in range(MAX_SONGS + 1)]
    assert chosen_list(analysis(too_many), target_sec=600) is None
    at_most = chosen_list(analysis(too_many[:MAX_SONGS]), target_sec=600)
    assert at_most is not None
    assert len(at_most.songs) == MAX_SONGS

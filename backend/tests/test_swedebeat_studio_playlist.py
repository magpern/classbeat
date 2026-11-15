"""``studio_songs`` from the user's own Spotify playlist (ADR 0036 section 3.1; SR C3): the
candidates are the playlist's tracks, read only, then the same match, evidence and swap path as a
search. The user's playlist is never modified: the working copy is classbeat's pool playlist.
The fake Spotify, the fake SwedeBeat gateway and ``FakeProvider``; no network, no real account."""

from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.swedebeat_studio.candidate_policy import usable
from classbeat.swedebeat_studio.inputs import StudioSongsInput
from classbeat.swedebeat_studio.playlist_policy import playlist_candidates
from songtools import song_id
from spotifyfake import USER_A
from studiotools import BRIEF, StudioEnv, choose_turns, pool_turns, script

SHORT_BRIEF = {"length_min": 10, "intensity": "moderate"}  # three songs
LIST_3 = [("pool", 0, "warmup"), ("pool", 2, "build"), ("pool", 5, "cooldown")]


def _uris(*tracks: int) -> list[str | None]:
    return [f"spotify:track:{song_id(i)}" for i in tracks]


def _writes_to(studio: StudioEnv, playlist_id: str) -> list[Any]:
    return [
        seen
        for seen in studio.songs.api()
        if seen.method in ("POST", "PUT", "DELETE") and playlist_id in seen.path
    ]


async def test_a_pick_from_the_users_playlist_reads_it_and_never_writes_it(
    studio: StudioEnv,
) -> None:
    studio.found(*range(6))
    own = studio.songs.fake.add_playlist(USER_A, "Tuesday ride", [*_uris(*range(6)), None])
    before = list(own.entries)
    studio.songs.script = script(choose_turns(LIST_3))  # no pool search: the playlist is the pool
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(
        rider, brief=SHORT_BRIEF, spotify_playlist_id=own.id
    )
    songs = studio.songs
    assert songs.stages(job_id) == ["playlist_read", "pool_match", "choose", "working_copy"]
    content = songs.revision(revision_id).content
    assert content["evidence"]["state"] == "ok"
    assert content["evidence"]["sent"] == 6  # the playable tracks, the unavailable entry left out
    assert [s["spotify_id"] for s in content["songs"]] == [song_id(i) for i in (0, 2, 5)]
    assert all(s["swap"] is None and not s["gap"] for s in content["songs"])
    # The user's playlist is unchanged and was never written; the working copy is the pool.
    assert songs.fake.playlists[own.id].entries == before
    assert _writes_to(studio, own.id) == []
    pool = songs.pool(rider.user_id)
    assert pool.playlist_id != own.id
    assert songs.fake.playlists[pool.playlist_id].entries == _uris(0, 2, 5)
    assert all(seen.path != "/v1/search" for seen in songs.api())  # no search at all
    assert songs.mowl.requests == []


# The two missing songs the three-song list needs (tracks 1 and 2, both ``build`` by position)
# are replaced from a search of g2 (tracks 20 to 29, all found): six builds, three per slot.
SWAP_6 = [(r, "build") for r in range(6)]


async def test_a_playlist_whose_songs_are_missing_swaps_on_the_same_path(
    studio: StudioEnv,
) -> None:
    studio.found(0, *range(20, 30))  # of the playlist's six songs only the first is in SwedeBeat
    own = studio.songs.fake.add_playlist(USER_A, "Old ride", _uris(*range(6)))
    before = list(own.entries)
    swap_list = [("pool", 0, "warmup"), ("pool", 1, "build"), ("pool", 6, "cooldown")]
    studio.songs.script = script(pool_turns(("g2",), SWAP_6), choose_turns(swap_list))
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(
        rider, brief=SHORT_BRIEF, spotify_playlist_id=own.id
    )
    stages = studio.songs.stages(job_id)
    assert stages == ["playlist_read", "pool_match", "swap", "choose", "working_copy"]
    content = studio.songs.revision(revision_id).content
    assert content["rounds"] == 1
    assert (content["evidence"]["sent"], content["evidence"]["missing"]) == (6, 5)
    assert [s["spotify_id"] for s in content["songs"]] == [song_id(i) for i in (0, 20, 25)]
    swapped = {s["spotify_id"]: s["swap"]["original"]["spotify_id"]
               for s in content["songs"] if s["swap"]}  # fmt: skip
    assert swapped == {song_id(20): song_id(1)}  # the chosen replacement carries its record
    assert all(s["swedebeat"] is not None for s in content["songs"])
    # Each swap's new candidates were matched again; the user's playlist stays as it was.
    assert [len(m.body["tracks"]) for m in studio.matches()] == [6, 5, 6]
    assert studio.songs.fake.playlists[own.id].entries == before
    assert _writes_to(studio, own.id) == []


async def test_an_empty_or_unplayable_playlist_fails_before_any_match(studio: StudioEnv) -> None:
    own = studio.songs.fake.add_playlist(USER_A, "Nothing", [None, None])
    rider = await studio.rider()
    job_id = await studio.created(rider, brief=SHORT_BRIEF, spotify_playlist_id=own.id)
    row = await studio.run(job_id)
    assert (row.state, row.error_category) == ("failed", "pool_too_small")
    assert studio.matches() == []


def _entry(i: int, **more: Any) -> dict[str, Any]:
    return {
        "id": song_id(i),
        "type": "track",
        "playable": True,
        "name": f"Song {i}",
        "artists": ["Made-up Band"],
        "image": None,
    } | more


def test_playlist_candidates_keep_playable_tracks_once_in_order_with_position_roles() -> None:
    entries: list[Any] = [
        _entry(0),
        _entry(1, playable=False),
        _entry(2, type="episode"),
        _entry(3, id="not-an-id"),
        _entry(4, name="  "),
        _entry(5, artists=[]),
        _entry(0),  # the same track again
        "not an entry",
        _entry(6),
        _entry(7),
    ]
    found = playlist_candidates(entries)
    assert [(c.spotify_id, c.role) for c in found] == [
        (song_id(0), "warmup"),
        (song_id(6), "build"),
        (song_id(7), "cooldown"),
    ]
    assert all(not usable(c) and c.cover is None for c in found)  # not matched yet
    assert playlist_candidates([]) == []


def test_only_a_pick_names_a_playlist_and_only_a_spotify_id() -> None:
    base = {"brief": BRIEF, "spotify_connection_id": "0" * 8 + "-0000-4000-8000-" + "0" * 12}
    base["swedebeat_connection_id"] = base["spotify_connection_id"]
    with pytest.raises(ValidationError):
        StudioSongsInput.model_validate(base | {"title": "x", "spotify_playlist_id": "bad id"})
    seed = {"song_list_revision_id": base["spotify_connection_id"], "position": 0}
    with pytest.raises(ValidationError):
        StudioSongsInput.model_validate(
            base
            | {
                "class_id": base["spotify_connection_id"],
                "action": "undo",
                "seed": seed,
                "spotify_playlist_id": song_id(0),
            }
        )
    picked = StudioSongsInput.model_validate(
        base | {"title": "x", "spotify_playlist_id": song_id(0)}
    )
    assert picked.spotify_playlist_id == song_id(0)

"""The fill's pool sources (ADR 0038 section 5, #1188) on the fake Spotify and MOWL: an attached
playlist the user's connection reads is favoured and only read (never written); one it cannot read
is Not as asked ``source_unreadable``; a pinned song Spotify has is placed at its position, one it
does not have is ``pinned_not_found``; neither is a failure; the favoured track takes a search
hit's slot where it fits; each song says where it came from. Never the network or a model."""

from typing import Any

import pytest

from composertools import REVISED, ComposerEnv, choose_turns, design_turns, pool_turns
from pickfake import POOL_14
from songtools import song_id
from spotifyfake import USER_A

pytestmark = pytest.mark.usefixtures("jenv")

FAVOURED = 15  # in the attached playlist only (the pool's searches stop at song 13)
PINNED = 3  # "Ride Song 3 alpha", pinned to "the climb": slot 2 of the sketch


def ref(song: int) -> int:
    """The registry's ref of a catalogue song: the favoured track first, then the pin, then the
    two searches (``alpha`` 0 to 9, ``beta`` 10 to 19), each song once."""
    if song == FAVOURED:
        return 0
    if song == PINNED:
        return 1
    return song + 2 if song < PINNED else song + 1


def script() -> dict[str, Any]:
    pool = [(ref(i), role) for i, role in POOL_14]
    # The choice's pool is the model's picks less the pinned song, then the favoured track.
    tracks = [i for i, _ in POOL_14 if i != PINNED] + [FAVOURED]
    at = tracks.index
    listed = [
        ("pool", at(0), "warmup"), ("pool", at(2), "build"), ("kept", 0, "climb"),
        ("pool", at(10), "recovery"), ("pool", at(6), "sprint"), ("pool", at(7), "sprint"),
        ("pool", at(11), "recovery"), ("pool", at(12), "cooldown"),
    ]  # fmt: skip
    turns = [*pool_turns(pool), *choose_turns(listed), *design_turns()]
    return {"model": "fake-model", "drafts": {"revised": REVISED}, "turns": turns}


async def test_attached_playlists_and_pinned_songs_fill_the_sketch(cenv: ComposerEnv) -> None:
    fake = cenv.songenv.fake
    fake.add_user("someone-else")
    foreign = fake.add_playlist("someone-else", "Not yours", [f"spotify:track:{song_id(1)}"])
    mine = fake.add_playlist(USER_A, "Mine", [f"spotify:track:{song_id(FAVOURED)}"])
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(
        rider,
        sources=[foreign.id, mine.id],
        pinned=[{"title": "Ride Song 3 alpha", "at": "the climb"}, {"title": "Nowhere Song"}],
    )
    _, list_id, _ = await cenv.waiting(rider, class_id, script())
    content = cenv.content(list_id)
    ids = [s["spotify_id"] for s in content["songs"]]
    assert ids[2] == song_id(PINNED)  # the pinned song at its position
    assert song_id(FAVOURED) in ids  # the favoured track over the search hit at slot 6
    assert ids[6] == song_id(FAVOURED)
    sources = [s["source"] for s in content["songs"]]
    assert sources[2] == {"kind": "pinned"}
    assert sources[6] == {"kind": "playlist", "playlist": 1}
    assert sources[0] == {"kind": "search"}
    unmet = {(u["path"], u["reason_code"]) for u in content["unmet"]}
    assert ("music_sources.0", "source_unreadable") in unmet
    assert ("pinned_songs.1", "pinned_not_found") in unmet
    for playlist in (mine, foreign):  # read only: never a write
        seen = [s.method for s in cenv.songenv.api() if playlist.id in s.path]
        assert set(seen) <= {"GET"}
    assert cenv.current(class_id) == sketch

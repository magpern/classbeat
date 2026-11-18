"""The fill's pool sources (ADR 0038 section 5, #1188) on the fake Spotify and MOWL: an attached
playlist the user's connection reads is favoured and only read (never written); one it cannot read
is Not as asked ``source_unreadable``; a pinned song Spotify has is placed at its position, one it
does not have is ``pinned_not_found``; neither is a failure; the favoured track takes a search
hit's slot where it fits; each song says where it came from. Never the network or a model."""

import json
from typing import Any
from uuid import UUID

import pytest

from composertools import (
    REVISED,
    ComposerEnv,
    choose_turns,
    design_turns,
    pool_turns,
    staged,
    swap,
)
from pickfake import POOL_14, call, entries, turn
from songtools import song_id
from spotifyfake import USER_A

pytestmark = pytest.mark.usefixtures("jenv")

FAVOURED = 15  # in the attached playlist only (the pool's searches stop at song 13)
PINNED = 3  # "Ride Song 3 alpha", pinned to "the climb": slot 2 of the sketch
# The roles of the fill's list (``script``), slot by slot.
KEPT_ROLES = ["warmup", "build", "climb", "recovery", "sprint", "sprint", "recovery", "cooldown"]


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


async def test_a_swap_never_offers_the_favoured_song_it_took_out(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1188 reviews B2, D1 and F1, through the swap: the fill puts the favoured track 15 at slot
    6, and the user swaps slot 6. The swap reads the attached playlist again, and the model even
    puts track 15 first in its pool; the choice is scripted to take the first candidate, so it
    would take track 15 if it were offered. It is not: it is never written to the working copy,
    never among the choice's candidates, and not in the new list."""
    fake = cenv.songenv.fake
    written: list[list[str]] = []
    real_write = fake._write  # pyright: ignore[reportPrivateUsage]

    def recording(playlist: Any, method: str, body: bytes, partial: Any) -> Any:
        written.append(list(json.loads(body).get("uris") or []))
        return real_write(playlist, method, body, partial)

    mine = fake.add_playlist(USER_A, "Mine", [f"spotify:track:{song_id(FAVOURED)}"])
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(
        rider, sources=[mine.id], pinned=[{"title": "Ride Song 3 alpha", "at": "the climb"}]
    )
    # The same refs as with the unreadable playlist and the missing pin: they register nothing.
    _, list_id, _ = await cenv.waiting(rider, class_id, script())
    assert [s["spotify_id"] for s in cenv.content(list_id)["songs"]][6] == song_id(FAVOURED)
    made = await swap(rider, class_id, list_id, 6)
    assert made.status_code == 201, made.text
    # The registry: track 15 (favoured) is ref 0, then ``beta`` 10 to 19 without it: refs 1 to 9.
    pool = entries(*[(r, "recovery") for r in (0, 4, 5, 6, 7, 8, 9)])  # 15 first, then 13 to 19
    listed = [("kept", i, role) for i, role in enumerate(KEPT_ROLES[:6])]
    listed += [("pool", 0, "recovery"), ("kept", 6, "cooldown")]  # the first candidate
    cenv.songenv.script = {
        "model": "fake-model",
        "drafts": {"revised": REVISED},
        "turns": [
            *staged(
                "pick_pool",
                [
                    turn(call("search_tracks", {"query": "beta"}, expect={"ok": True})),
                    turn(call("submit_pool", pool, expect={"accepted": True})),
                ],
            ),
            *choose_turns(listed),
            *design_turns(),
        ],
    }
    monkeypatch.setattr(fake, "_write", recording)
    earlier = len(cenv.songenv.providers)  # the fill's own stages offered track 15, rightly
    row = await cenv.songenv.run(UUID(made.json()["job_id"]))
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    removed = f"spotify:track:{song_id(FAVOURED)}"
    assert written  # the swap's pool write and its songs write
    assert all(removed not in uris for uris in written)  # never written to the working copy
    chose = [
        sent
        for provider in cenv.songenv.providers[earlier:]
        for sent in provider.sent
        if sent.spec.stage == "choose_songs"
    ]
    assert chose
    assert all(f"Ride Song {FAVOURED} " not in str(sent.turns) for sent in chose)  # never offered
    ids = [s["spotify_id"] for s in cenv.content(row.approval_revision_id)["songs"]]
    assert ids[6] == song_id(13)  # the first candidate once track 15 is out
    assert song_id(FAVOURED) not in ids
